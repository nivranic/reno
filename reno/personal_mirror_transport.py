"""Durable home personal-mirror requests and bounded HTTP delivery.

Initialization is explicit. Reads/retries never build a facility, recapture a
snapshot, replace its UUID, change primary or grant a cloud reader. Original
requests/receipts are immutable files under an independently locked namespace;
the catalog advances only after a request-bound remote committed receipt.
Capture closes its source handles before returning facts. The explicit freeze
therefore reopens each original ready body, verifies its captured identity/SHA,
and stores independent immutable blobs. A retry uses only those original blobs
once their whole immutable manifest exists, including if source facts advance.
This module claims no deletion/GC, backup completeness or capability activation.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import base64
import errno
from functools import wraps
import hashlib
import os
import stat
import threading
import uuid

from . import config, db, personal_mirror_contract as facts
from . import personal_mirror_snapshot as capture, personal_mirror_transfer_contract as wire
from . import personal_mirror_transfer_files as file_primitives
from . import storage_admin, storage_lifecycle as lifecycle, storage_source as physical
from . import sync_agent as home, sync_media as safe, sync_queue as queue, sync_state as state_files
from .sync_client import SyncClient, _identity
from .sync_protocol import SyncError
from .sync_state import StateStore

ROOT = "personal-mirror-outbox"
MARKER = "facility.json"
CATALOG = "catalog.json"
LOCK = "operation.lock"
MAX_OPERATIONS = 4096
MAX_CATALOG_BYTES = 1024 * 1024
MAX_OUTBOX_BYTES = 8 * 1024 * 1024 * 1024
MAX_OUTBOX_FILES = 65536
MAX_REQUEST_BYTES = 128 * 1024 * 1024 + 64 * 1024
MAX_CONTROL_BYTES = 64 * 1024
ENDPOINT = "personal-mirror/"
_ENTRY_FIELDS = {"operation_id", "request_sha256", "prepare_sha256", "snapshot_id", "snapshot_sha256",
    "previous_operation_id", "blob_manifest_sha256", "receipt_file_sha256"}
_REQUEST_FIELDS = {"format_version", "kind", "binding", "source", "prepare", "previous_operation_id"}


def _fail(code, status=409):
    raise SyncError("Personal mirror transmission cannot continue", "PERSONAL_MIRROR_SENDER_" + code, status) from None


def _same(actual, expected, code="SOURCE_CHANGED"):
    if actual != expected:
        _fail(code)


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail("INVALID_RECORD")


def _checked(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (facts.PersonalSnapshotError, wire.PersonalMirrorTransferError) as exc:
            _fail(exc.code)
        except OSError:
            _fail("STORAGE_UNAVAILABLE", 503)
    return wrapped


def _bytes(value):
    return facts._json(value).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _now():
    return datetime.now(timezone.utc)


def _enabled(value):
    return value is True or type(value) is str and value.lower() in {"true", "1"}


def _store(store):
    if (type(store) is not StateStore or store.profile is None or store._directory is None
            or type(store._directory) is not safe._Directory):
        _fail("STATE_OWNERSHIP_REQUIRED")
    profile = lifecycle._profile(store.profile)
    lifecycle.require_guard(profile)
    if storage_admin.profile().identity != profile.identity:
        _fail("PROFILE_CHANGED")
    _same(lifecycle._key(config.DATA), lifecycle._key(profile.database.parent), "PROFILE_CHANGED")
    _same(lifecycle._key(store.data_dir), lifecycle._key(profile.database.parent), "PROFILE_CHANGED")
    store._directory.verify()
    return profile


def _home(store):
    """Read actual config, existing pair/state and local identity; no producers."""
    profile = _store(store)
    values = home._sender_config_snapshot()
    if (str(values.get("node_role") or "all").lower() not in {"all", "home"}
            or not _enabled(values.get("personal_features_enabled"))
            or not _enabled(values.get("personal_mirror_enabled"))):
        _fail("DISABLED", 403)
    options = home.settings(delivery_only=True, _snapshot=values)
    con = db.connect(profile.database)
    try:
        storage_admin.from_connection(con, profile=profile)
        metadata = dict(con.execute("SELECT key,value FROM main.meta WHERE key IN (?,?,?)",
            ("library_id", "personal_primary_id", "primary_epoch")))
        version = db.knowledge_version(con)
        handshake = home.sync_protocol.schema_handshake(con)
    finally:
        con.close()
    saved = store.read_observation()
    binding = state_files._binding(saved["binding"])
    expected = {"home_library_id": metadata.get("library_id"), "home_node_id": binding["home_node_id"],
        "target_library_id": options["sync_target_library_id"], "target_node_id": options["sync_target_node_id"],
        "target_url": options["url"]}
    _same(binding, expected, "PEER_CHANGED")
    _same(saved["local_epoch"], version["library_epoch"], "KNOWLEDGE_EPOCH_CHANGED")
    if binding["home_library_id"] == binding["target_library_id"] or binding["home_node_id"] == binding["target_node_id"]:
        _fail("INDEPENDENT_PEER_REQUIRED")
    source = {"library_id": binding["home_library_id"], "node_id": binding["home_node_id"], "handshake": handshake}
    _identity(source)
    return profile, binding, source, metadata, saved


def _live_lease(saved):
    lease = deepcopy(saved["lease"])
    if lease is None:
        _fail("LEASE_REQUIRED")
    queue._lease(lease)
    if lease["session_id"] != saved["session_id"] or queue._stored_time(lease["lease_until"]) <= _now():
        _fail("LEASE_EXPIRED")
    return lease


def _primary(metadata, source):
    _same(metadata.get("personal_primary_id"), source["node_id"], "PRIMARY_CHANGED")
    epoch = metadata.get("primary_epoch")
    try:
        parsed = int(epoch)
    except (TypeError, ValueError):
        _fail("PRIMARY_CHANGED")
    facts._integer(parsed, 1)
    _same(epoch, str(parsed), "PRIMARY_CHANGED")
    return parsed


def _open_existing_lock(directory):
    """OPEN_EXISTING on both platforms; a retry cannot recreate a missing lock."""
    initial = directory.entry_stat(LOCK)
    safe._safe_stat(initial, directory=False)
    if initial.st_nlink != 1 or initial.st_size != 1:
        _fail("FACILITY_CHANGED")
    if os.name == "nt":
        import ctypes
        handle = safe._kernel.CreateFileW(safe._native_path(directory.path / LOCK),
            0xC0000000, 3, None, 3, 0x00200000, None)
        if handle == ctypes.c_void_p(-1).value:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            fd = safe.msvcrt.open_osfhandle(handle, os.O_BINARY | os.O_RDWR)
        except BaseException:
            safe._kernel.CloseHandle(handle)
            raise
    else:
        fd = os.open(LOCK, os.O_RDWR | os.O_NOFOLLOW, dir_fd=directory.fd)
    try:
        opened = os.fstat(fd)
        safe._safe_stat(opened, directory=False)
        if opened.st_nlink != 1:
            _fail("FACILITY_CHANGED")
        _same(physical.file_signature(opened)[:4], physical.file_signature(initial)[:4], "FACILITY_CHANGED")
        return os.fdopen(fd, "r+b", buffering=0)
    except BaseException:
        os.close(fd)
        raise


@contextmanager
def _exclusive(directory):
    with _open_existing_lock(directory) as stream:
        path_signature = physical.file_signature(directory.entry_stat(LOCK))
        handle_signature = physical.file_signature(os.fstat(stream.fileno()))
        def verify():
            directory.verify()
            path, opened = directory.entry_stat(LOCK), os.fstat(stream.fileno())
            safe._safe_stat(path, directory=False)
            safe._safe_stat(opened, directory=False)
            if path.st_nlink != 1 or opened.st_nlink != 1:
                _fail("FACILITY_CHANGED")
            _same(physical.file_signature(path), path_signature, "FACILITY_CHANGED")
            _same(physical.file_signature(opened), handle_signature, "FACILITY_CHANGED")
        try:
            if os.name == "nt":
                stream.seek(0)
                safe.msvcrt.locking(stream.fileno(), safe.msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(exc, "winerror", None) == 33:
                _fail("BUSY")
            raise
        try:
            verify()
            yield verify
        finally:
            if os.name == "nt":
                stream.seek(0)
                safe.msvcrt.locking(stream.fileno(), safe.msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def _read(directory, name, maximum):
    raw = state_files._read_bytes(directory, name, maximum)
    return raw, facts._decode(raw)


def _space(directory, additional):
    names = directory.names()
    if len(names) > 2 * MAX_OPERATIONS + 3:
        _fail("OUTBOX_LIMIT")
    total, count = additional, 0
    for name in names:
        info = directory.entry_stat(name)
        is_directory = stat.S_ISDIR(info.st_mode)
        safe._safe_stat(info, directory=is_directory)
        entries = [(None, info)]
        if is_directory:
            # Count unknown files too; never adopt or remove them. Nested
            # unbounded namespaces are rejected rather than recursively read.
            with safe._Directory(directory.path / name, parent=directory) as child:
                entries = [(item, child.entry_stat(item)) for item in child.names()]
        for _, item in entries:
            safe._safe_stat(item, directory=False)
            if item.st_nlink != 1:
                _fail("FACILITY_CHANGED")
            total += item.st_size
            count += 1
            if total > MAX_OUTBOX_BYTES or count > MAX_OUTBOX_FILES:
                _fail("OUTBOX_LIMIT")


def _request_name(operation_id):
    facts._uuid(operation_id)
    return operation_id + ".prepare.json"


def _receipt_name(operation_id):
    facts._uuid(operation_id)
    return operation_id + ".receipt.json"


def _blob_name(operation_id):
    facts._uuid(operation_id)
    return operation_id + ".blobs.json"


def _blob_directory(operation_id):
    facts._uuid(operation_id)
    return operation_id + ".files"


def _normalize_request(value):
    _exact(value, _REQUEST_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != 1 or value["kind"] != "personal_mirror_sender_request":
        _fail("INVALID_RECORD")
    binding = state_files._binding(value["binding"])
    _identity(value["source"])
    prepared = wire.normalize_prepare(value["prepare"])
    _same((value["source"]["library_id"], value["source"]["node_id"]),
        (binding["home_library_id"], binding["home_node_id"]), "PEER_CHANGED")
    _same(prepared["source"], {"library_id": binding["home_library_id"], "node_id": binding["home_node_id"],
        "personal_primary_id": binding["home_node_id"], "primary_epoch": prepared["source"]["primary_epoch"]})
    _same(prepared["receiver"], {"library_id": binding["target_library_id"], "node_id": binding["target_node_id"]})
    previous = value["previous_operation_id"]
    if previous is not None:
        facts._uuid(previous)
        if previous == prepared["operation_id"]:
            _fail("INVALID_RECORD")
    _same(previous is None, prepared["previous"] is None)
    return value


def _entry(request, raw):
    prepared = request["prepare"]
    return {"operation_id": prepared["operation_id"], "request_sha256": _sha(raw),
        "prepare_sha256": wire.prepare_sha256(prepared), "snapshot_id": prepared["snapshot_id"],
        "snapshot_sha256": prepared["snapshot_sha256"], "previous_operation_id": request["previous_operation_id"],
        "blob_manifest_sha256": None, "receipt_file_sha256": None}


def _catalog(raw, value, marker_sha):
    _exact(value, {"format_version", "marker_sha256", "head_operation_id", "entries"})
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail("INVALID_CATALOG")
    _same(value["marker_sha256"], marker_sha, "FACILITY_CHANGED")
    entries = value["entries"]
    if type(entries) is not list or len(entries) > MAX_OPERATIONS:
        _fail("OUTBOX_LIMIT")
    ids, head, pending = set(), None, False
    for entry in entries:
        _exact(entry, _ENTRY_FIELDS)
        for name in ("operation_id", "snapshot_id"):
            facts._uuid(entry[name])
        for name in ("request_sha256", "prepare_sha256", "snapshot_sha256"):
            facts._sha(entry[name])
        if entry["blob_manifest_sha256"] is not None:
            facts._sha(entry["blob_manifest_sha256"])
        if pending or entry["operation_id"] in ids or entry["previous_operation_id"] != head:
            _fail("INVALID_CATALOG")
        ids.add(entry["operation_id"])
        if entry["receipt_file_sha256"] is None:
            pending = True
        else:
            facts._sha(entry["receipt_file_sha256"])
            head = entry["operation_id"]
    _same(value["head_operation_id"], head, "INVALID_CATALOG")
    if len(raw) > MAX_CATALOG_BYTES:
        _fail("OUTBOX_LIMIT")
    return value


class _Context:
    def __init__(self, store, profile, directory, node, node_fact, binding, source, metadata, marker_raw, check, verify_lock, stack):
        self.store, self.profile, self.directory, self.node = store, profile, directory, node
        self.node_fact, self.binding, self.source, self.metadata = node_fact, binding, source, metadata
        self.marker_sha, self.check = _sha(marker_raw), check
        self.verify_lock = verify_lock
        self.stack, self.pins = stack, {}
        self.after_check = None
        self.thread = threading.get_ident()

    def current(self):
        if threading.get_ident() != self.thread:
            _fail("OWNERSHIP_REQUIRED")
        self.check()
        if self.after_check is not None:
            self.after_check()
        profile, binding, source, metadata, saved = _home(self.store)
        _same(profile.identity, self.profile.identity, "PROFILE_CHANGED")
        _same(binding, self.binding, "PEER_CHANGED")
        _same(source, self.source, "SOURCE_CHANGED")
        self.node.verify()
        for held in self.pins.values():
            held.verify()
        self.verify_lock()
        marker_raw, _ = _read(self.directory, MARKER, MAX_CONTROL_BYTES)
        _same(_sha(marker_raw), self.marker_sha, "FACILITY_CHANGED")
        if self.after_check is not None:
            self.after_check()
        return metadata, saved

    def pin(self, name, raw):
        """Keep actual immutable bytes/handles across callbacks and HTTP work."""
        held = self.pins.get(name)
        if held is None:
            held = capture._HeldFile(self.directory, name, self.stack)
            _same(held.digest(len(raw), self.check), _sha(raw), "IMMUTABLE_FILE_CHANGED")
            self.pins[name] = held
        held.verify()

    def read_catalog(self):
        raw, value = _read(self.directory, CATALOG, MAX_CATALOG_BYTES)
        return raw, _catalog(raw, value, self.marker_sha)

    def replace_catalog(self, original_raw, value):
        self.current()
        raw = _bytes(value)
        _catalog(raw, value, self.marker_sha)
        actual, _ = self.read_catalog()
        _same(_sha(actual), _sha(original_raw), "CATALOG_CHANGED")
        # The original physical namespace remains held; no caller callback
        # runs between the exact CAS observation and its atomic replacement.
        state_files._write(self.directory, CATALOG, raw)

    def request(self, operation_id):
        raw, value = _read(self.directory, _request_name(operation_id), MAX_REQUEST_BYTES)
        self.pin(_request_name(operation_id), raw)
        _normalize_request(value)
        _same(value["prepare"]["operation_id"], operation_id, "REQUEST_CHANGED")
        _same(value["binding"], self.binding, "PEER_CHANGED")
        _same(value["source"], self.source, "SOURCE_CHANGED")
        return raw, value


@contextmanager
def _context(store, *, check_cancelled=None):
    if type(store) is not StateStore or store._directory is None:
        _fail("STATE_OWNERSHIP_REQUIRED")
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    check = check_cancelled or (lambda: None)
    with store.reference_lifetime(), ExitStack() as stack:
        profile, binding, source, metadata, _ = _home(store)
        data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        node = capture._HeldFile(data, "node-identity.json", stack)
        node_fact = node.read_identity(check)
        _same(node_fact[0], binding["home_node_id"], "SOURCE_CHANGED")
        try:
            directory = stack.enter_context(safe._Directory(data.path / ROOT, parent=data))
            verify_lock = stack.enter_context(_exclusive(directory))
            marker_raw, marker = _read(directory, MARKER, MAX_CONTROL_BYTES)
        except FileNotFoundError:
            _fail("NOT_INITIALIZED")
        _exact(marker, {"format_version", "kind", "profile", "binding", "node_sha256", "node_file_identity"})
        if type(marker["format_version"]) is not int:
            _fail("FACILITY_CHANGED")
        _same(marker, {"format_version": 1, "kind": "personal_mirror_sender_facility", "profile": list(profile.identity),
            "binding": binding, "node_sha256": node_fact[1], "node_file_identity": node.identity}, "FACILITY_CHANGED")
        context = _Context(store, profile, directory, node, node_fact, binding, source, metadata, marker_raw, check, verify_lock, stack)
        context.current()
        context.read_catalog()
        yield context
        context.current()


@_checked
def initialize_outbox_locked(*, store, check_cancelled=None):
    """Explicit facility producer; does not initialize or alter knowledge State."""
    if type(store) is not StateStore or store._directory is None:
        _fail("STATE_OWNERSHIP_REQUIRED")
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    check = check_cancelled or (lambda: None)
    with store.reference_lifetime(), ExitStack() as stack:
        profile, binding, source, metadata, _ = _home(store)
        _primary(metadata, source)
        data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        node = capture._HeldFile(data, "node-identity.json", stack)
        node_fact = node.read_identity(check)
        _same(node_fact[0], binding["home_node_id"], "SOURCE_CHANGED")
        directory = stack.enter_context(safe._Directory(data.path / ROOT, create=True, parent=data))
        try:
            directory.entry_stat(LOCK)
        except FileNotFoundError:
            # An explicit initialization can resume its own empty namespace.
            # A populated namespace without its lock is unknown, not adopted.
            if directory.names():
                _fail("UNKNOWN_FACILITY")
            try:
                state_files._write(directory, LOCK, b"\0", immutable=True)
            except FileExistsError:
                pass
        verify_lock = stack.enter_context(_exclusive(directory))
        marker = {"format_version": 1, "kind": "personal_mirror_sender_facility", "profile": list(profile.identity),
            "binding": binding, "node_sha256": node_fact[1], "node_file_identity": node.identity}
        expected = _bytes(marker)
        try:
            marker_raw, actual = _read(directory, MARKER, MAX_CONTROL_BYTES)
        except FileNotFoundError:
            if set(directory.names()) != {LOCK}:
                _fail("UNKNOWN_FACILITY")
            check()
            state_files._write(directory, MARKER, expected, immutable=True)
            marker_raw, actual = _read(directory, MARKER, MAX_CONTROL_BYTES)
        _same(marker_raw, expected, "FACILITY_CHANGED")
        _same(actual, marker, "FACILITY_CHANGED")
        context = _Context(store, profile, directory, node, node_fact, binding, source, metadata, marker_raw, check, verify_lock, stack)
        try:
            context.read_catalog()
        except FileNotFoundError:
            if set(directory.names()) != {LOCK, MARKER}:
                _fail("UNKNOWN_FACILITY")
            context.current()
            state_files._write(directory, CATALOG, _bytes({"format_version": 1, "marker_sha256": _sha(marker_raw),
                "head_operation_id": None, "entries": []}), immutable=True)
        context.current()
        return {"format_version": 1, "binding": deepcopy(binding), "relative_key": ROOT}


def _selected_entry(catalog, operation_id):
    selected = [entry for entry in catalog["entries"] if entry["operation_id"] == operation_id]
    return selected[0] if selected else None


def _require_no_unregistered_request(context, catalog):
    """An original published request fences new capture before catalog repair.

    Immutable request publication and catalog replacement cannot be one file
    transaction. Read actual canonical request names, distinguish declared
    history, and require explicit original-operation resumption across that
    gap. Unknown names stay untouched; no UUID or predecessor is adopted.
    """
    declared = {entry["operation_id"] for entry in catalog["entries"]}
    names = context.directory.names()
    if len(names) > 2 * MAX_OPERATIONS + 3:
        _fail("OUTBOX_LIMIT")
    for name in names:
        if not name.endswith(".prepare.json"):
            continue
        operation_id = name[:-len(".prepare.json")]
        try:
            canonical = str(uuid.UUID(operation_id)) == operation_id
        except (ValueError, AttributeError):
            canonical = False
        if not canonical or operation_id in declared:
            continue
        context.request(operation_id)
        _fail("ORIGINAL_OPERATION_REQUIRED")


def _register_request(context, raw, request, catalog_raw, catalog):
    entry = _entry(request, raw)
    selected = _selected_entry(catalog, entry["operation_id"])
    if selected is not None:
        _same({**selected, "blob_manifest_sha256": None, "receipt_file_sha256": None}, entry, "REQUEST_CHANGED")
        return selected
    if len(catalog["entries"]) >= MAX_OPERATIONS:
        _fail("OUTBOX_LIMIT")
    if any(item["receipt_file_sha256"] is None for item in catalog["entries"]):
        _fail("PENDING_OPERATION_REQUIRED")
    _same(request["previous_operation_id"], catalog["head_operation_id"], "PREDECESSOR_CHANGED")
    catalog["entries"].append(entry)
    context.replace_catalog(catalog_raw, catalog)
    return entry


def _blob_manifest(context, request):
    prepared = request["prepare"]
    raw, value = _read(context.directory, _blob_name(prepared["operation_id"]), MAX_CATALOG_BYTES)
    context.pin(_blob_name(prepared["operation_id"]), raw)
    _, catalog = context.read_catalog()
    entry = _selected_entry(catalog, prepared["operation_id"])
    if entry is None:
        _fail("REQUEST_NOT_REGISTERED")
    if entry["blob_manifest_sha256"] is not None:
        _same(_sha(raw), entry["blob_manifest_sha256"], "BLOBS_CHANGED")
    _exact(value, {"format_version", "kind", "operation_id", "prepare_sha256", "entries"})
    if type(value["format_version"]) is not int or value["format_version"] != 1 or value["kind"] != "personal_mirror_sender_blobs":
        _fail("INVALID_RECORD")
    _same((value["operation_id"], value["prepare_sha256"]),
        (prepared["operation_id"], wire.prepare_sha256(prepared)), "REQUEST_CHANGED")
    manifest = wire.attachment_manifest(prepared)
    if type(value["entries"]) is not list or len(value["entries"]) != len(manifest):
        _fail("BLOBS_CHANGED")
    for actual, descriptor in zip(value["entries"], manifest):
        _exact(actual, {"attachment_id", "storage_key", "sha256", "byte_size", "file_identity"})
        _same({name: actual[name] for name in actual if name != "file_identity"},
            {name: descriptor[name] for name in descriptor if name != "file_identity"}, "BLOBS_CHANGED")
        facts.file_identity(actual["file_identity"])
        _same(actual["file_identity"]["byte_size"], descriptor["byte_size"], "BLOBS_CHANGED")
    return raw, value


def _register_blobs(context, operation_id, raw):
    original, catalog = context.read_catalog()
    selected = _selected_entry(catalog, operation_id)
    if selected is None:
        _fail("REQUEST_NOT_REGISTERED")
    digest = _sha(raw)
    if selected["blob_manifest_sha256"] is not None:
        _same(selected["blob_manifest_sha256"], digest, "BLOBS_CHANGED")
        return
    selected["blob_manifest_sha256"] = digest
    context.replace_catalog(original, catalog)


def _freeze_blobs(context, request):
    """Finish original body freezing; an existing full manifest never goes home."""
    try:
        raw, value = _blob_manifest(context, request)
    except FileNotFoundError:
        entry = _selected_entry(context.read_catalog()[1], request["prepare"]["operation_id"])
        if entry is None or entry["blob_manifest_sha256"] is not None:
            _fail("BLOBS_MISSING")
    else:
        with ExitStack() as stack:
            if value["entries"]:
                directory = stack.enter_context(safe._Directory(context.directory.path / _blob_directory(value["operation_id"]),
                    parent=context.directory))
            for descriptor in value["entries"]:
                key = facts.storage_key(descriptor["storage_key"])
                held = capture._HeldFile(directory, key.name, stack)
                _same(held.identity, descriptor["file_identity"], "BLOBS_CHANGED")
                _same(held.digest(descriptor["byte_size"], context.check), descriptor["sha256"], "BLOBS_CHANGED")
            _register_blobs(context, value["operation_id"], raw)
        return value
    prepared = request["prepare"]
    manifest = wire.attachment_manifest(prepared)
    entries = []
    with ExitStack() as stack:
        if manifest:
            data = stack.enter_context(safe._Directory(context.profile.database.parent, base=context.profile.database.parent))
            source_root = stack.enter_context(safe._Directory(data.path / "personal-attachments", parent=data))
            source_directory = stack.enter_context(safe._Directory(source_root.path / "files", parent=source_root))
            destination = stack.enter_context(safe._Directory(context.directory.path / _blob_directory(prepared["operation_id"]),
                create=True, parent=context.directory))
        sources, copies = [], []
        for descriptor in manifest:
            metadata, _ = context.current()
            _same(_primary(metadata, context.source), prepared["source"]["primary_epoch"], "PRIMARY_CHANGED")
            key = facts.storage_key(descriptor["storage_key"])
            original = capture._HeldFile(source_directory, key.name, stack)
            sources.append(original)
            _same(original.identity, descriptor["file_identity"], "ATTACHMENT_CHANGED")
            _same(original.digest(descriptor["byte_size"], context.check), descriptor["sha256"], "ATTACHMENT_CHANGED")
            try:
                destination.entry_stat(key.name)
            except FileNotFoundError:
                _space(context.directory, descriptor["byte_size"])
                temp_name = ".body-" + uuid.uuid4().hex + ".part"
                owned = None
                try:
                    original.stream.seek(0)
                    def chunks():
                        while True:
                            context.check()
                            original.verify()
                            chunk = original.stream.read(128 * 1024)
                            context.check()
                            original.verify()
                            if not chunk:
                                break
                            yield chunk
                    owned = file_primitives._write_temp(destination, temp_name, chunks(), descriptor, context.check)
                    # Incoming stream SHA is insufficient: verify the actual
                    # flushed temporary body after the last caller callback.
                    with ExitStack() as proof:
                        temporary = capture._HeldFile(destination, temp_name, proof)
                        _same((int(temporary.identity["device"]), int(temporary.identity["inode"])), owned, "BLOBS_CHANGED")
                        _same(temporary.digest(descriptor["byte_size"], context.check), descriptor["sha256"], "BLOBS_CHANGED")
                        signature = temporary.path_signature
                    original.verify()
                    current = destination.entry_stat(temp_name)
                    safe._safe_stat(current, directory=False)
                    _same(physical.file_signature(current), signature, "BLOBS_CHANGED")
                    destination.verify()
                    # No callback follows the final physical check before
                    # publishing this exact original temporary inode.
                    safe._rename_new(destination, temp_name, destination, key.name)
                    if os.name != "nt":
                        os.fsync(destination.fd)
                finally:
                    if owned is not None:
                        file_primitives._cleanup_temp(destination, temp_name, owned)
            copied = capture._HeldFile(destination, key.name, stack)
            copies.append(copied)
            _same(copied.digest(descriptor["byte_size"], context.check), descriptor["sha256"], "BLOBS_CHANGED")
            entries.append({**descriptor, "file_identity": copied.identity})
        context.current()
        for held in (*sources, *copies):
            held.verify()
        value = {"format_version": 1, "kind": "personal_mirror_sender_blobs", "operation_id": prepared["operation_id"],
            "prepare_sha256": wire.prepare_sha256(prepared), "entries": entries}
        raw = _bytes(value)
        if len(raw) > MAX_CATALOG_BYTES:
            _fail("OUTBOX_LIMIT")
        _space(context.directory, len(raw))
        state_files._write(context.directory, _blob_name(prepared["operation_id"]), raw, immutable=True)
        context.pin(_blob_name(prepared["operation_id"]), raw)
        _register_blobs(context, prepared["operation_id"], raw)
    return _blob_manifest(context, request)[1]


def _receipt(context, raw_request, request, entry):
    try:
        raw, value = _read(context.directory, _receipt_name(request["prepare"]["operation_id"]), MAX_CONTROL_BYTES)
    except FileNotFoundError:
        if entry["receipt_file_sha256"] is not None:
            _fail("RECEIPT_MISSING")
        return None
    context.pin(_receipt_name(request["prepare"]["operation_id"]), raw)
    _exact(value, {"format_version", "kind", "request_sha256", "receipt"})
    if type(value["format_version"]) is not int or value["format_version"] != 1 or value["kind"] != "personal_mirror_sender_receipt":
        _fail("INVALID_RECORD")
    _same(value["request_sha256"], _sha(raw_request), "REQUEST_CHANGED")
    normalized = wire.normalize_receipt(request["prepare"], value["receipt"])
    if entry["receipt_file_sha256"] is not None:
        _same(entry["receipt_file_sha256"], _sha(raw), "RECEIPT_CHANGED")
    return raw, normalized


def _advance(context, operation_id, receipt_raw):
    original, catalog = context.read_catalog()
    selected = _selected_entry(catalog, operation_id)
    if selected is None:
        _fail("REQUEST_MISSING")
    digest = _sha(receipt_raw)
    if selected["receipt_file_sha256"] is not None:
        _same(selected["receipt_file_sha256"], digest, "RECEIPT_CHANGED")
        return
    _same(catalog["head_operation_id"], selected["previous_operation_id"], "PREDECESSOR_CHANGED")
    selected["receipt_file_sha256"] = digest
    catalog["head_operation_id"] = operation_id
    context.replace_catalog(original, catalog)


@_checked
def freeze_mirror_locked(*, store, operation_id, snapshot_id, expected_head_operation_id=None,
                         check_cancelled=None, expected_snapshot_sha256=None):
    """Persist one actual ten-table/ready-file snapshot before its first RPC."""
    facts._uuid(operation_id)
    facts._uuid(snapshot_id)
    if expected_head_operation_id is not None:
        facts._uuid(expected_head_operation_id)
    if expected_snapshot_sha256 is not None:
        facts._sha(expected_snapshot_sha256)
    with _context(store, check_cancelled=check_cancelled) as context:
        catalog_raw, catalog = context.read_catalog()
        try:
            raw, request = context.request(operation_id)
        except FileNotFoundError:
            if _selected_entry(catalog, operation_id) is not None:
                _fail("REQUEST_MISSING")
            _require_no_unregistered_request(context, catalog)
            _same(catalog["head_operation_id"], expected_head_operation_id, "PREDECESSOR_CHANGED")
            if any(item["receipt_file_sha256"] is None for item in catalog["entries"]):
                _fail("PENDING_OPERATION_REQUIRED")
            previous, revision, prior_json = None, 0, None
            if expected_head_operation_id is not None:
                prior_raw, prior_request = context.request(expected_head_operation_id)
                prior_entry = _selected_entry(catalog, expected_head_operation_id)
                if prior_entry is None or prior_entry["receipt_file_sha256"] is None:
                    _fail("PREDECESSOR_CHANGED")
                selected = _receipt(context, prior_raw, prior_request, prior_entry)
                if selected is None:
                    _fail("PREDECESSOR_CHANGED")
                prior_receipt = selected[1]
                previous = {"snapshot_id": prior_receipt["snapshot_id"], "snapshot_sha256": prior_receipt["snapshot_sha256"]}
                revision, prior_json = prior_receipt["installed_revision"], prior_request["prepare"]["snapshot_json"]
            metadata, saved = context.current()
            _live_lease(saved)
            epoch = _primary(metadata, context.source)
            captured = capture.capture_snapshot(snapshot_id=snapshot_id,
                expected_library_id=context.source["library_id"], expected_primary_id=context.source["node_id"],
                expected_epoch=epoch, prior_snapshot_json=prior_json, check_cancelled=context.check).snapshot
            prepared = {"format_version": 1, "kind": "personal_mirror_prepare", "operation_id": operation_id,
                "snapshot_id": captured.snapshot_id, "snapshot_sha256": captured.snapshot_sha256,
                "snapshot_json": captured.snapshot_json,
                "source": {"library_id": context.source["library_id"], "node_id": context.source["node_id"],
                    "personal_primary_id": context.source["node_id"], "primary_epoch": epoch},
                "receiver": {"library_id": context.binding["target_library_id"], "node_id": context.binding["target_node_id"]},
                "previous": previous, "expected_revision": revision}
            wire.normalize_prepare(prepared, prior_snapshot_json=prior_json)
            request = {"format_version": 1, "kind": "personal_mirror_sender_request", "binding": context.binding,
                "source": context.source, "prepare": prepared, "previous_operation_id": expected_head_operation_id}
            raw = _bytes(_normalize_request(request))
            if len(raw) > MAX_REQUEST_BYTES:
                _fail("REQUEST_TOO_LARGE")
            metadata, _ = context.current()
            _same(_primary(metadata, context.source), epoch, "PRIMARY_CHANGED")
            _require_no_unregistered_request(context, catalog)
            _space(context.directory, len(raw))
            if expected_snapshot_sha256 is not None:
                _same(request["prepare"]["snapshot_sha256"], expected_snapshot_sha256, "STALE_PREVIEW")
            # No cancellation/user callback follows this exact snapshot CAS
            # before immutable publication. Public apply retains profile EX.
            state_files._write(context.directory, _request_name(operation_id), raw, immutable=True)
            context.pin(_request_name(operation_id), raw)
        _same(request["prepare"]["snapshot_id"], snapshot_id, "REQUEST_CHANGED")
        _same(request["previous_operation_id"], expected_head_operation_id, "PREDECESSOR_CHANGED")
        if expected_snapshot_sha256 is not None:
            _same(request["prepare"]["snapshot_sha256"], expected_snapshot_sha256, "STALE_PREVIEW")
        entry = _register_request(context, raw, request, catalog_raw, catalog)
        if _receipt(context, raw, request, entry) is None:
            _freeze_blobs(context, request)
        entry = _selected_entry(context.read_catalog()[1], operation_id)
        return {**deepcopy(entry), "state": "committed" if entry["receipt_file_sha256"] is not None else "pending"}


def _public_selection(request, raw, state):
    prepared = request["prepare"]
    document = facts.validate_snapshot(prepared["snapshot_json"]).document
    return {"operation_id": prepared["operation_id"], "snapshot_id": prepared["snapshot_id"],
        "snapshot_sha256": prepared["snapshot_sha256"], "request_sha256": _sha(raw),
        "expected_head_operation_id": request["previous_operation_id"], "state": state,
        "counts": deepcopy(document["counts"]),
        "attachment_count": len(document["attachment_manifest"]),
        "attachment_bytes": sum(d["byte_size"] for d in document["attachment_manifest"])}


@_checked
def preview_mirror_locked(*, store, operation_id, snapshot_id, expected_head_operation_id=None, check_cancelled=None):
    """Capture the same deterministic snapshot without publishing any request.

    An existing UUID always selects its immutable original request. Preview
    cannot initialize the sender or repair its catalog or any publish gap.
    """
    facts._uuid(operation_id)
    facts._uuid(snapshot_id)
    if expected_head_operation_id is not None:
        facts._uuid(expected_head_operation_id)
    with _context(store, check_cancelled=check_cancelled) as context:
        _, catalog = context.read_catalog()
        try:
            raw, request = context.request(operation_id)
        except FileNotFoundError:
            if _selected_entry(catalog, operation_id) is not None:
                _fail("REQUEST_MISSING")
            _require_no_unregistered_request(context, catalog)
            _same(catalog["head_operation_id"], expected_head_operation_id, "PREDECESSOR_CHANGED")
            if any(entry["receipt_file_sha256"] is None for entry in catalog["entries"]):
                _fail("PENDING_OPERATION_REQUIRED")
            previous, revision, prior_json = None, 0, None
            if expected_head_operation_id is not None:
                prior_raw, prior_request = context.request(expected_head_operation_id)
                prior_entry = _selected_entry(catalog, expected_head_operation_id)
                if prior_entry is None or prior_entry["receipt_file_sha256"] is None:
                    _fail("PREDECESSOR_CHANGED")
                selected = _receipt(context, prior_raw, prior_request, prior_entry)
                if selected is None:
                    _fail("PREDECESSOR_CHANGED")
                prior_receipt = selected[1]
                previous = {"snapshot_id": prior_receipt["snapshot_id"], "snapshot_sha256": prior_receipt["snapshot_sha256"]}
                revision, prior_json = prior_receipt["installed_revision"], prior_request["prepare"]["snapshot_json"]
            metadata, saved = context.current()
            _live_lease(saved)
            epoch = _primary(metadata, context.source)
            captured = capture.capture_snapshot(snapshot_id=snapshot_id,
                expected_library_id=context.source["library_id"], expected_primary_id=context.source["node_id"],
                expected_epoch=epoch, prior_snapshot_json=prior_json, check_cancelled=context.check).snapshot
            prepared = {"format_version": 1, "kind": "personal_mirror_prepare", "operation_id": operation_id,
                "snapshot_id": captured.snapshot_id, "snapshot_sha256": captured.snapshot_sha256,
                "snapshot_json": captured.snapshot_json,
                "source": {"library_id": context.source["library_id"], "node_id": context.source["node_id"],
                    "personal_primary_id": context.source["node_id"], "primary_epoch": epoch},
                "receiver": {"library_id": context.binding["target_library_id"], "node_id": context.binding["target_node_id"]},
                "previous": previous, "expected_revision": revision}
            wire.normalize_prepare(prepared, prior_snapshot_json=prior_json)
            request = {"format_version": 1, "kind": "personal_mirror_sender_request", "binding": context.binding,
                "source": context.source, "prepare": prepared, "previous_operation_id": expected_head_operation_id}
            raw = _bytes(_normalize_request(request))
            if len(raw) > MAX_REQUEST_BYTES:
                _fail("REQUEST_TOO_LARGE")
            metadata, _ = context.current()
            _same(_primary(metadata, context.source), epoch, "PRIMARY_CHANGED")
            context.read_catalog()
            return _public_selection(request, raw, "preview")
        _same(request["prepare"]["snapshot_id"], snapshot_id, "REQUEST_CHANGED")
        _same(request["previous_operation_id"], expected_head_operation_id, "PREDECESSOR_CHANGED")
        return _operation_status(context, operation_id, raw, request, catalog)


def _operation_status(context, operation_id, raw, request, catalog):
    entry = _selected_entry(catalog, operation_id)
    if entry is None:
        if any(item["receipt_file_sha256"] is None for item in catalog["entries"]):
            _fail("PENDING_OPERATION_REQUIRED")
        _same(request["previous_operation_id"], catalog["head_operation_id"], "PREDECESSOR_CHANGED")
        previous = None
        expected_revision = 0
        if catalog["head_operation_id"] is not None:
            prior_raw, prior_request = context.request(catalog["head_operation_id"])
            prior_entry = _selected_entry(catalog, catalog["head_operation_id"])
            selected = _receipt(context, prior_raw, prior_request, prior_entry)
            if selected is None:
                _fail("PREDECESSOR_CHANGED")
            previous = {"snapshot_id": selected[1]["snapshot_id"], "snapshot_sha256": selected[1]["snapshot_sha256"]}
            expected_revision = selected[1]["installed_revision"]
        _same(request["prepare"]["previous"], previous, "PREDECESSOR_CHANGED")
        _same(request["prepare"]["expected_revision"], expected_revision, "PREDECESSOR_CHANGED")
        # Only the single original tail request may bridge request publication
        # before catalog registration; no repair or newest selection occurs.
        declared = {item["operation_id"] for item in catalog["entries"]}
        names = context.directory.names()
        if len(names) > 2 * MAX_OPERATIONS + 3:
            _fail("OUTBOX_LIMIT")
        for name in names:
            if not name.endswith(".prepare.json"):
                continue
            selected = name[:-len(".prepare.json")]
            try:
                canonical = str(uuid.UUID(selected)) == selected
            except (ValueError, AttributeError):
                canonical = False
            if canonical and selected not in declared and selected != operation_id:
                _fail("ORIGINAL_OPERATION_REQUIRED")
        for name in (_receipt_name(operation_id), _blob_name(operation_id)):
            try:
                context.directory.entry_stat(name)
            except FileNotFoundError:
                pass
            else:
                _fail("INVALID_CATALOG")
        return _public_selection(request, raw, "request_published")
    _same({**entry, "blob_manifest_sha256": None, "receipt_file_sha256": None},
          _entry(request, raw), "REQUEST_CHANGED")
    receipt = _receipt(context, raw, request, entry)
    return _public_selection(request, raw, "committed" if receipt is not None else "pending")


@_checked
def operation_status_locked(*, store, operation_id, expected_request_sha256, check_cancelled=None):
    """Read this exact original operation, including its valid tail publish gap."""
    facts._uuid(operation_id)
    facts._sha(expected_request_sha256)
    with _context(store, check_cancelled=check_cancelled) as context:
        raw, request = context.request(operation_id)
        _same(_sha(raw), expected_request_sha256, "REQUEST_CHANGED")
        _, catalog = context.read_catalog()
        return _operation_status(context, operation_id, raw, request, catalog)


class PersonalMirrorClient:
    """Reuse the actual bounded, cancellable SyncClient HTTP implementation."""
    def __init__(self, client):
        if type(client) is not SyncClient:
            _fail("HTTP_CLIENT_REQUIRED")
        self.client = client

    def check_peer(self, binding, source, check):
        result = SyncClient._request(self.client, "handshake", maximum=MAX_CONTROL_BYTES,
            _deadline=self.client._deadline(self.client._timeout, check))
        _exact(result, {"target"})
        _identity(result["target"])
        target = result["target"]
        _same((target["library_id"], target["node_id"]),
            (binding["target_library_id"], binding["target_node_id"]), "PEER_CHANGED")
        home.sync_protocol.validate_handshake(target["handshake"], source["handshake"])

    def control(self, endpoint, source, lease, field, value, check):
        raw = _bytes({"source": source, "lease": lease, field: value})
        maximum = MAX_REQUEST_BYTES if endpoint == "prepare" else MAX_CONTROL_BYTES
        if len(raw) > maximum:
            _fail("REQUEST_TOO_LARGE")
        return SyncClient._request(self.client, ENDPOINT + endpoint, content=raw,
            headers={"Content-Type": "application/json"}, maximum=MAX_CONTROL_BYTES,
            _deadline=self.client._deadline(self.client._timeout, check))

    def attachment(self, source, lease, prepared, descriptor, held, check):
        selector = {"source": source, "lease": lease, "operation_id": prepared["operation_id"],
            "prepare_sha256": wire.prepare_sha256(prepared), "attachment_id": descriptor["attachment_id"]}
        header = base64.urlsafe_b64encode(_bytes(selector)).decode("ascii")
        if len(header) > 8192:
            _fail("HEADER_TOO_LARGE")
        held.verify()
        _same(held.identity, descriptor["file_identity"], "ATTACHMENT_CHANGED")
        _same(held.digest(descriptor["byte_size"], check), descriptor["sha256"], "ATTACHMENT_CHANGED")
        held.stream.seek(0)
        completed = False
        def chunks():
            nonlocal completed
            total, digest = 0, hashlib.sha256()
            while True:
                check()
                held.verify()
                chunk = held.stream.read(128 * 1024)
                check()
                held.verify()
                if not chunk:
                    break
                total += len(chunk)
                if total > descriptor["byte_size"]:
                    _fail("ATTACHMENT_CHANGED")
                digest.update(chunk)
                yield chunk
            _same((total, digest.hexdigest()), (descriptor["byte_size"], descriptor["sha256"]), "ATTACHMENT_CHANGED")
            completed = True
        deadline = self.client._deadline(3600, check)
        result = SyncClient._request(self.client, ENDPOINT + "attachment", content=chunks(),
            headers={"Content-Type": "application/octet-stream", "Content-Length": str(descriptor["byte_size"]),
                "X-Reno-Personal-Mirror": header}, maximum=MAX_CONTROL_BYTES, _deadline=deadline)
        if not completed:
            _fail("BODY_NOT_CONSUMED")
        held.verify()
        held.stream.seek(0)
        _same(held.digest(descriptor["byte_size"], check), descriptor["sha256"], "ATTACHMENT_CHANGED")
        expected = {"operation_id": prepared["operation_id"], "prepare_sha256": selector["prepare_sha256"],
            "attachment_id": descriptor["attachment_id"], "storage_key": descriptor["storage_key"],
            "sha256": descriptor["sha256"], "byte_size": descriptor["byte_size"], "state": result.get("state")}
        if type(result.get("byte_size")) is not int or result.get("state") not in {"stored", "installed"}:
            _fail("INVALID_RESPONSE")
        _same(result, expected, "INVALID_RESPONSE")
        return result


def _persist_receipt(context, request_raw, request, result):
    normalized = wire.normalize_receipt(request["prepare"], result)
    raw = _bytes({"format_version": 1, "kind": "personal_mirror_sender_receipt",
        "request_sha256": _sha(request_raw), "receipt": normalized})
    context.current()
    name = _receipt_name(request["prepare"]["operation_id"])
    try:
        previous, _ = _read(context.directory, name, MAX_CONTROL_BYTES)
    except FileNotFoundError:
        _space(context.directory, len(raw))
        state_files._write(context.directory, name, raw, immutable=True)
    else:
        _same(previous, raw, "RECEIPT_CHANGED")
    context.pin(name, raw)
    _advance(context, request["prepare"]["operation_id"], raw)
    return normalized


@_checked
def deliver_mirror_locked(*, store, operation_id, expected_request_sha256, client=None, check_cancelled=None):
    """Resume this exact durable operation; never select or capture a new batch."""
    facts._uuid(operation_id)
    facts._sha(expected_request_sha256)
    # Pin a supplied actual transport before any supported user callback can
    # replace it. A default client is pinned immediately after construction.
    provided_client = client
    provided_transport = client._transport if type(client) is SyncClient else None
    with _context(store, check_cancelled=check_cancelled) as context:
        raw, request = context.request(operation_id)
        _same(_sha(raw), expected_request_sha256, "REQUEST_CHANGED")
        catalog_raw, catalog = context.read_catalog()
        entry = _register_request(context, raw, request, catalog_raw, catalog)
        saved_receipt = _receipt(context, raw, request, entry)
        if saved_receipt is not None:
            _advance(context, operation_id, saved_receipt[0])
            return {**deepcopy(saved_receipt[1]), "replayed": True}
        prepared = request["prepare"]
        expected_epoch = prepared["source"]["primary_epoch"]
        def admitted():
            metadata, saved = context.current()
            _same(_primary(metadata, context.source), expected_epoch, "PRIMARY_CHANGED")
            return _live_lease(saved)
        admitted()
        if client is None:
            options = home.settings(delivery_only=True)
            client = SyncClient(context.binding["target_url"], token_provider=lambda: home._sender_token(options),
                check_cancelled=lambda: admitted())
        peer = PersonalMirrorClient(client)
        bound_client, bound_url = peer.client, context.binding["target_url"]
        bound_transport = provided_transport if provided_client is not None else bound_client._transport
        def check_client_binding():
            _same(bound_client._url, bound_url, "PEER_CHANGED")
            if bound_client._transport is not bound_transport:
                _fail("HTTP_TRANSPORT_CHANGED")
        # Every admitted/current check runs this after the user's callback.
        # SyncClient._request performs its existing deadline check after token
        # retrieval and before capturing request_base/the actual transport.
        context.after_check = check_client_binding
        check_client_binding()
        peer.check_peer(context.binding, context.source, admitted)
        selector = {"format_version": 1, "kind": "personal_mirror_commit", "operation_id": operation_id,
            "prepare_sha256": wire.prepare_sha256(prepared)}
        response = peer.control("receipt", context.source, admitted(), "commit", selector, admitted)
        _exact(response, {"receipt"})
        if response["receipt"] is not None:
            return _persist_receipt(context, raw, request, response["receipt"])
        # Incomplete original freezing may resume its exact captured bodies.
        # A completed blob manifest instead selects only original local blobs.
        blobs = _freeze_blobs(context, request)
        result = peer.control("prepare", context.source, admitted(), "prepare", prepared, admitted)
        _exact(result, {"operation_id", "prepare_sha256", "snapshot_id", "snapshot_sha256", "state", "attachment_count"})
        manifest = wire.attachment_manifest(prepared)
        if type(result["attachment_count"]) is not int or result["state"] not in {"prepared", "installed"}:
            _fail("INVALID_RESPONSE")
        _same(result, {"operation_id": operation_id, "prepare_sha256": selector["prepare_sha256"],
            "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
            "state": result["state"], "attachment_count": len(manifest)}, "INVALID_RESPONSE")
        if result["state"] != "installed":
            with ExitStack() as stack:
                if manifest:
                    directory = stack.enter_context(safe._Directory(context.directory.path / _blob_directory(operation_id),
                        parent=context.directory))
                for descriptor, blob in zip(manifest, blobs["entries"]):
                    admitted()
                    key = facts.storage_key(descriptor["storage_key"])
                    held = capture._HeldFile(directory, key.name, stack)
                    peer.attachment(context.source, admitted(), prepared, blob, held, lambda: admitted())
        response = peer.control("commit", context.source, admitted(), "commit", selector, admitted)
        return _persist_receipt(context, raw, request, response)


@_checked
def original_operation_locked(*, store, operation_id, expected_request_sha256, check_cancelled=None):
    """Read original bytes/receipt without a client, network or fresh capture."""
    facts._uuid(operation_id)
    facts._sha(expected_request_sha256)
    with _context(store, check_cancelled=check_cancelled) as context:
        raw, request = context.request(operation_id)
        _same(_sha(raw), expected_request_sha256, "REQUEST_CHANGED")
        _, catalog = context.read_catalog()
        entry = _selected_entry(catalog, operation_id)
        if entry is None:
            _fail("REQUEST_NOT_REGISTERED")
        _same({**entry, "blob_manifest_sha256": None, "receipt_file_sha256": None}, _entry(request, raw), "REQUEST_CHANGED")
        receipt = _receipt(context, raw, request, entry)
        return {"request_bytes": raw, "prepare": deepcopy(request["prepare"]),
            "receipt": None if receipt is None else {**deepcopy(receipt[1]), "replayed": True},
            "state": "pending" if receipt is None else "committed"}
