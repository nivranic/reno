"""Original Home17 publication selection and byte-only crash completion.

An unselected content-addressed body is preparation, never publication or paid
authority. The permanent reserved journal first selects its UUID and bytes.
Completion consumes that actual original selection without a current claim,
HTTP observation, SQL snapshot export or report regeneration.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import stat
import time

from . import budget_writer_witness as sql, storage_lifecycle as lifecycle
from . import storage_source as source, sync_media as safe
from . import sync_snapshot as snapshots, sync_state
from . import sync_source_authority as permanent, sync_source_execution as execution
from .sync_protocol import SyncError


PAYLOAD_DIRECTORY = "sync-publication-payloads"
PAYLOAD_VERSION = "v1"
PAYLOAD_FAMILY = PAYLOAD_DIRECTORY + "/" + PAYLOAD_VERSION
MAX_PAYLOAD_BYTES = sync_state.MAX_OUTBOX_BYTES
MAX_PAYLOAD_TOTAL_BYTES = 128 * 1024 * 1024
MAX_PAYLOAD_FILES = permanent.MAX_RECORDS
PAYLOAD_DEADLINE_SECONDS = 120.0
RECORD_FIELDS = frozenset({"format_version", "record_kind", "record_id", "metadata",
    "core_sha256", "claims_sha256", "creation_basis", "expected_before", "payload_ref"})
PAYLOAD_REF_FIELDS = frozenset({"format_version", "family", "sha256", "byte_size"})
_SEAL = object()
_PIN_SEAL = object()
_CURRENT = ContextVar("reno_original_publication2_producer", default=None)
_CONTEXT = ContextVar("reno_original_publication2_context", default=None)


def _fail(code="INVALID"):
    raise SyncError("Original publication evidence is unavailable",
                    "SYNC_PUBLICATION_" + code, 409) from None


def _same(left, right):
    if left != right:
        _fail("CHANGED")


def _owner():
    from .sync_source_lifecycle17 import _owner as owner
    return owner()


def validate_payload_ref2(value):
    permanent._exact(value, PAYLOAD_REF_FIELDS)
    permanent._version(value["format_version"])
    if value["family"] != PAYLOAD_FAMILY:
        _fail("FAMILY_INVALID")
    permanent._sha(value["sha256"])
    permanent._integer(value["byte_size"], minimum=2, maximum=MAX_PAYLOAD_BYTES)
    return deepcopy(value)


def payload_name2(value):
    return validate_payload_ref2(value)["sha256"] + ".json"


def payload_key2(value):
    return PAYLOAD_FAMILY + "/" + payload_name2(value)


def validate_record2(kind, value, activation):
    if kind != "publications":
        _fail("KIND_INVALID")
    permanent._exact(value, RECORD_FIELDS)
    if type(value["format_version"]) is not int or value["format_version"] != 2:
        _fail()
    if value["record_kind"] != "publication":
        _fail()
    permanent._uuid(value["record_id"])
    sync_state._metadata(value["metadata"])
    _same(value["metadata"]["publication_id"], value["record_id"])
    permanent._sha(value["core_sha256"])
    permanent._sha(value["claims_sha256"])
    ref = validate_payload_ref2(value["payload_ref"])
    _same((ref["sha256"], ref["byte_size"]),
          (value["metadata"]["sha256"], value["metadata"]["byte_size"]))
    binding = activation["binding"]
    permanent._basis(value["creation_basis"], value["core_sha256"], binding)
    permanent._before(value["expected_before"], binding, value["core_sha256"])
    basis, before = value["creation_basis"], value["expected_before"]
    if basis["origin_ref"] is None or basis["continuity"] is None:
        _fail("CREATION_REQUIRED")
    _same((before["local_epoch"], before["admission_ref"], before["claim"],
           before["recovery_generation"], before["recovery_stream"], before["control_revision"]),
          (basis["home_epoch"], basis["admission_ref"], basis["claim"],
           basis["recovery_generation"], basis["recovery_stream"], basis["control_revision"]))
    authority = before["source_authority"]
    if (authority is None or authority["origin"] != basis["origin_ref"]
            or authority["publication"] is not None or before["pending_publication"] is not None
            or before["budget_pending_sha256"] is not None
            or before["source_control_pending_sha256"] is not None):
        _fail("CREATION_REQUIRED")
    permanent._encoded(value)
    return deepcopy(value)


def record_relations2(entries, *, check=None):
    check = check or (lambda: None)
    selected = {}
    for item in entries["publications"].values():
        check()
        record = item["record"]
        key = record["core_sha256"]
        if key in selected and selected[key] != record["record_id"]:
            _fail("SELECTION_AMBIGUOUS")
        selected[key] = record["record_id"]
        if record["format_version"] != 2:
            continue
        basis, before = record["creation_basis"], record["expected_before"]
        origin = permanent._resolve_record(entries, basis["origin_ref"])
        _same(origin["core_sha256"], key)
        _same(basis["claim"]["item_id"], origin["core"]["item_id"])
        for ref in before["source_authority"].values():
            if type(ref) is dict and "record_kind" in ref:
                _same(permanent._resolve_record(entries, ref)["core_sha256"], key)
        if before["admission_ref"] is not None:
            ref = before["admission_ref"]
            actual = entries["receipts"].get(ref["admission_id"])
            if actual is None or actual["state"] != "committed":
                _fail("ADMISSION_REQUIRED")
            _same(permanent.digest(actual["record"]), ref["record_sha256"])
            result = actual["record"]["result"]
            _same({name: result[name] for name in ("admission_id", "core_sha256", "request_sha256", "admission_sha256")},
                  {name: ref[name] for name in ("admission_id", "core_sha256", "request_sha256", "admission_sha256")})


def public_refs2(kind, record):
    if kind != "publications" or record["format_version"] != 2:
        _fail("KIND_INVALID")
    refs = []
    basis, before = record["creation_basis"], record["expected_before"]
    for ref in before["source_authority"].values():
        if type(ref) is dict and "record_kind" in ref:
            refs.append({"kind": "authority_record", **deepcopy(ref)})
    if basis["admission_ref"] is not None:
        refs.append({"kind": "admission", **deepcopy(basis["admission_ref"])})
    refs.append({"kind": "continuity", **deepcopy(basis["continuity"])})
    refs.append({"kind": "publication_payload", **deepcopy(record["payload_ref"]),
                 "claims_sha256": record["claims_sha256"]})
    return list({permanent._encoded(ref): ref for ref in refs}.values())


def normalize_payload2(snapshot, claims, *, recovery_generation, recovery_stream):
    checked = snapshots.validate_snapshot(snapshot)
    sync_state._integer(recovery_generation, minimum=0)
    sync_state._cursor({"stream": recovery_stream, "after": 0, "through": None})
    envelope = {"snapshot": checked, "claims": sync_state._bindings(claims,
        {video["video_id"] for video in checked["videos"]}),
        "recovery_generation": recovery_generation, "recovery_stream": deepcopy(recovery_stream)}
    raw = sync_state._bounded(envelope, maximum=MAX_PAYLOAD_BYTES,
                             depth=snapshots.MAX_ENVELOPE_DEPTH + 1)
    sha = hashlib.sha256(raw).hexdigest()
    metadata = {"publication_id": checked["publication_id"], "sha256": sha, "byte_size": len(raw)}
    return raw, metadata, {"format_version": 1, "family": PAYLOAD_FAMILY,
                            "sha256": sha, "byte_size": len(raw)}


def validate_payload_bytes2(raw, payload_ref, *, record=None, source_identity=None, core=None):
    """Pure original-byte parser, with no authority, file lookup or repair."""
    ref = validate_payload_ref2(payload_ref)
    if (type(raw) is not bytes or len(raw) != ref["byte_size"]
            or hashlib.sha256(raw).hexdigest() != ref["sha256"]):
        _fail("BODY_CORRUPT")
    value = sync_state._decode(raw)
    sync_state._exact(value, ("snapshot", "claims", "recovery_generation", "recovery_stream"))
    canonical, metadata, _ = normalize_payload2(value["snapshot"], value["claims"],
        recovery_generation=value["recovery_generation"], recovery_stream=value["recovery_stream"])
    _same(canonical, raw)
    if source_identity is not None:
        _same(value["snapshot"]["version"]["library_id"], source_identity["library_id"])
        for entry in value["claims"]:
            origin = entry["origin"]
            _same((origin["source_library_id"], origin["source_node_id"]),
                  (source_identity["library_id"], source_identity["node_id"]))
    if record is not None:
        _same(metadata, record["metadata"])
        _same(permanent.digest(value["claims"]), record["claims_sha256"])
        basis = record["creation_basis"]
        _same((value["snapshot"]["version"]["library_epoch"],
               value["recovery_generation"], value["recovery_stream"]),
              (basis["home_epoch"], basis["recovery_generation"], basis["recovery_stream"]))
        selected = [entry for entry in value["claims"]
                    if entry["claim"]["item_id"] == basis["claim"]["item_id"]]
        if len(selected) != 1:
            _fail("CLAIM_REQUIRED")
        _same(selected[0]["claim"], basis["claim"])
        if core is not None:
            _same(permanent.digest(core), record["core_sha256"])
            _same(selected[0]["claim"]["item_id"], core["item_id"])
            origin = selected[0]["origin"]
            _same((origin["source_library_id"], origin["source_node_id"],
                   hashlib.sha256(origin["submitted_url"].encode("utf-8")).hexdigest()),
                  (core["binding"]["home_library_id"], core["binding"]["home_node_id"],
                   core["submitted_url_sha256"]))
    return deepcopy(value)


def validate_record_payload2(raw, record, entries):
    """Resolve the full original origin before checking the actual body."""
    origin = permanent._resolve_record(entries, record["creation_basis"]["origin_ref"])
    core = origin["core"]
    return validate_payload_bytes2(raw, record["payload_ref"], record=record, core=core,
        source_identity={"library_id": core["binding"]["home_library_id"],
                         "node_id": core["binding"]["home_node_id"]})


def _verify_v1_body(envelope, record, core):
    """Historical v1 normalization may fill its proved legacy stream only."""
    basis = record["creation_basis"]
    _same(permanent.digest(core), record["core_sha256"])
    _same(permanent.digest(envelope["claims"]), record["claims_sha256"])
    _same((envelope["snapshot"]["publication_id"], envelope["snapshot"]["version"]["library_id"],
           envelope["snapshot"]["version"]["library_epoch"], envelope["recovery_generation"], envelope["recovery_stream"]),
          (record["record_id"], core["binding"]["home_library_id"], basis["home_epoch"],
           basis["recovery_generation"], basis["recovery_stream"]))
    selected = [entry for entry in envelope["claims"] if entry["claim"]["item_id"] == core["item_id"]]
    if len(selected) != 1:
        _fail("CLAIM_REQUIRED")
    _same(selected[0]["claim"], basis["claim"])
    origin = selected[0]["origin"]
    _same((origin["source_library_id"], origin["source_node_id"],
           hashlib.sha256(origin["submitted_url"].encode("utf-8")).hexdigest()),
          (core["binding"]["home_library_id"], core["binding"]["home_node_id"], core["submitted_url_sha256"]))


class PayloadPin2:
    """An actual no-follow file lifetime; full SHA stays outside SQL writers."""
    __slots__ = ("profile", "directory", "name", "held", "before", "opened", "digest",
                 "raw", "_owner", "_active", "_seal", "_callback", "_started")

    def __init__(self, *, profile, directory, name, held, raw, before, opened, callback, seal):
        if seal is not _PIN_SEAL:
            _fail("FACTORY_REQUIRED")
        self.profile, self.directory, self.name, self.held = profile, directory, name, held
        self.raw, self.before, self.opened = raw, before, opened
        self.digest = hashlib.sha256(raw).digest()
        self._owner, self._active, self._seal = _owner(), True, seal
        self._callback, self._started = callback, time.monotonic()

    @property
    def file(self):
        self.short()
        return (self.directory, self.name, self.held, self.before, self.opened, self.digest)

    def short(self):
        if type(self) is not PayloadPin2 or self._seal is not _PIN_SEAL or not self._active or self._owner != _owner():
            _fail("FACTORY_REQUIRED")
        if self._callback is not None:
            self._callback()
        if time.monotonic() - self._started > PAYLOAD_DEADLINE_SECONDS:
            _fail("BODY_LIMIT")
        lifecycle.require_guard(self.profile)
        self.directory.verify()
        _same(source.file_signature(self.directory.entry_stat(self.name)), self.before)
        _same(source.file_signature(os.fstat(self.held.fileno())), self.opened)

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self.short()
        os.lseek(self.held.fileno(), 0, os.SEEK_SET)
        found, remaining = hashlib.sha256(), self.before[2]
        while remaining:
            self.short()
            chunk = os.read(self.held.fileno(), min(128 * 1024, remaining))
            if not chunk:
                _fail("BODY_CHANGED")
            remaining -= len(chunk)
            found.update(chunk)
        if os.read(self.held.fileno(), 1) or found.digest() != self.digest:
            _fail("BODY_CHANGED")
        self.short()


def _pin(profile, directory, name, stack, *, callback=None):
    before_info = directory.entry_stat(name)
    safe._safe_stat(before_info, directory=False)
    if (before_info.st_size > MAX_PAYLOAD_BYTES or before_info.st_nlink != 1
            or os.name != "nt" and (before_info.st_uid != os.geteuid() or stat.S_IMODE(before_info.st_mode) & 0o077)):
        _fail("BODY_UNSAFE")
    before = source.file_signature(before_info)
    held = stack.enter_context(directory.open_file(name))
    opened = source.file_signature(os.fstat(held.fileno()))
    _same(before[:4], opened[:4])
    raw, remaining = bytearray(), before[2]
    started = time.monotonic()
    while remaining:
        if callback is not None:
            callback()
        if time.monotonic() - started > PAYLOAD_DEADLINE_SECONDS:
            _fail("BODY_LIMIT")
        chunk = os.read(held.fileno(), min(128 * 1024, remaining))
        if not chunk:
            _fail("BODY_CHANGED")
        raw.extend(chunk)
        remaining -= len(chunk)
    if os.read(held.fileno(), 1):
        _fail("BODY_CHANGED")
    pin = PayloadPin2(profile=profile, directory=directory, name=name, held=held,
        raw=bytes(raw), before=before, opened=opened, callback=callback, seal=_PIN_SEAL)
    # Final SHA check runs before closure releases the original handles.
    stack.callback(setattr, pin, "_active", False)
    stack.callback(pin.full)
    pin.full()
    return pin


def _namespace(directory, *, addition=None, callback=None):
    """Bound the whole preparation namespace without adopting any candidate."""
    count, parts, total, names = 0, 0, 0, set()
    started = time.monotonic()
    with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as iterator:
        for entry in iterator:
            if callback is not None:
                callback()
            if time.monotonic() - started > PAYLOAD_DEADLINE_SECONDS:
                _fail("BODY_LIMIT")
            info = directory.entry_stat(entry.name)
            safe._safe_stat(info, directory=False)
            if (info.st_nlink != 1 or info.st_size > MAX_PAYLOAD_BYTES
                    or os.name != "nt" and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077)):
                _fail("BODY_UNSAFE")
            total += info.st_size
            names.add(entry.name)
            if permanent._PART.fullmatch(entry.name):
                parts += 1
            else:
                if not entry.name.endswith(".json"):
                    _fail("FAMILY_INVALID")
                permanent._sha(entry.name[:-5])
                count += 1
            if count > MAX_PAYLOAD_FILES or parts > permanent.MAX_INTERNAL_PARTS or total > MAX_PAYLOAD_TOTAL_BYTES:
                _fail("BODY_LIMIT")
    if addition is not None and addition[0] not in names:
        count += 1
        total += addition[1]
        if count > MAX_PAYLOAD_FILES or total > MAX_PAYLOAD_TOTAL_BYTES:
            _fail("BODY_LIMIT")
    directory.verify()


def _version_namespace(outer):
    names = outer.names()
    if set(names) != {PAYLOAD_VERSION}:
        _fail("FAMILY_INVALID")
    safe._safe_stat(outer.entry_stat(PAYLOAD_VERSION), directory=True)
    outer.verify()


def capture_payload2_locked(*, profile, payload_ref, stack, check_cancelled=None):
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    ref = validate_payload_ref2(payload_ref)
    root = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
    outer = stack.enter_context(safe._Directory(root.path / PAYLOAD_DIRECTORY, parent=root))
    directory = stack.enter_context(safe._Directory(outer.path / PAYLOAD_VERSION, parent=outer))
    _version_namespace(outer)
    _namespace(directory, callback=check_cancelled)
    pin = _pin(profile, directory, payload_name2(ref), stack, callback=check_cancelled)
    validate_payload_bytes2(pin.raw, ref)
    return pin


def capture_outbox17_locked(*, profile, store, metadata, stack, proved_stream=None):
    sync_state._metadata(metadata)
    profile = lifecycle._profile(profile)
    if lifecycle._key(store.data_dir) != lifecycle._key(profile.database.parent):
        _fail("OWNER_REQUIRED")
    directory = stack.enter_context(safe._Directory(store.data_dir / sync_state._OUTBOX, base=store.data_dir))
    pin = _pin(profile, directory, metadata["publication_id"] + ".json", stack,
               callback=store.check_cancelled)
    _same((pin.digest.hex(), len(pin.raw)), (metadata["sha256"], metadata["byte_size"]))
    # v1's original canonical outbox is the body; never synthesize a new v2 blob.
    store.read_publication(metadata, proved_stream=proved_stream)
    return pin


def prepare_payload2_locked(*, profile, store, raw, payload_ref):
    """Write an unselected candidate only; this grants no continuation right."""
    from .sync_source_lifecycle17 import _selectors
    profile = _selectors(profile, store)
    if lifecycle._key(store.data_dir) != lifecycle._key(profile.database.parent):
        _fail("OWNER_REQUIRED")
    ref = validate_payload_ref2(payload_ref)
    validate_payload_bytes2(raw, ref)
    with store._mutating() as root, ExitStack() as stack:
        outer = stack.enter_context(safe._Directory(store.data_dir / PAYLOAD_DIRECTORY, parent=root, create=True))
        directory = stack.enter_context(safe._Directory(outer.path / PAYLOAD_VERSION, parent=outer, create=True))
        name = payload_name2(ref)
        _version_namespace(outer)
        _namespace(directory, addition=(name, len(raw)), callback=store.check_cancelled)
        try:
            directory.entry_stat(name)
        except FileNotFoundError:
            sync_state._write(directory, name, raw, immutable=True)
        pin = _pin(profile, directory, name, stack, callback=store.check_cancelled)
        _same(pin.raw, raw)


def _readonly(owner):
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    return owner.observe_readonly()


def _base_successor(owner, record, base_entries, base_head):
    observed = _readonly(owner)
    actual = deepcopy(observed["entries"])
    candidate = actual["publications"].pop(record["record_id"], None)
    if record["record_id"] in base_entries["publications"]:
        _fail("SELECTION_CHANGED")
    _same(actual, base_entries)
    head = observed["journal_head"]
    difference = head["ordinal"] - base_head["ordinal"]
    if candidate is None:
        _same((difference, head), (0, base_head))
    else:
        _same(candidate["record"], record)
        states = ["reserved"] if candidate["state"] == "reserved" else ["reserved", "committed"]
        _same(difference, len(states))
        directory = owner._directory(observed["ledger"], "journal")
        previous = base_head["sha256"]
        for offset, state in enumerate(states, 1):
            ordinal = base_head["ordinal"] + offset
            event = owner._read(directory, f"{ordinal:08d}.json")
            _same((event["facility_id"], event["ordinal"], event["previous_sha256"],
                   event["kind"], event["key"], event["record_sha256"], event["record"], event["state"]),
                  (base_head["facility_id"], ordinal, previous, "publications", record["record_id"],
                   permanent.digest(record), record, state))
            previous = permanent.digest(event)
        _same(head["sha256"], previous)
    if any(item != ("publications", record["record_id"]) for item in observed["incomplete"]):
        _fail("SELECTION_CHANGED")
    return observed


class _Producer2:
    __slots__ = ("owner", "profile", "store", "_record_json", "_owner", "_active", "_seal",
                 "_context_token", "_payload_pin", "_materialized", "_outbox_pin", "_materialization_stack")

    def __init__(self, *, owner, record, pin, seal):
        if (seal is not _SEAL or type(owner) is not permanent.AuthorityGuard
                or type(pin) is not PayloadPin2 or pin._seal is not _PIN_SEAL):
            _fail("FACTORY_REQUIRED")
        self.owner, self.profile, self.store = owner, owner.profile, owner.lock.store
        self._record_json, self._payload_pin = execution._canonical(record), pin
        self._owner, self._active, self._seal = _owner(), True, seal
        self._context_token, self._materialized = None, False
        self._outbox_pin, self._materialization_stack = None, ExitStack()

    def _context(self):
        if (type(self) not in {PublicationCreationGuard2, PublicationCompletionGuard2}
                or self._seal is not _SEAL or not self._active or self._owner != _owner()
                or _CURRENT.get() is not self or _CONTEXT.get() is not self):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        self.owner._check()
        self.owner.lock.verify()
        self._payload_pin.short()

    @property
    def record(self):
        self._context()
        return json.loads(self._record_json)

    def _record(self, record=None):
        self._context()
        if record is not None:
            _same(execution._canonical(record), self._record_json)
        return json.loads(self._record_json)

    def verify_materialization(self):
        """The held actual original outbox, separate from checkpoint slots."""
        original = self._record()
        if not self._materialized or type(self._outbox_pin) is not PayloadPin2:
            _fail("MATERIALIZATION_REQUIRED")
        self._outbox_pin.full()
        _same(self._outbox_pin.raw, self._payload_pin.raw)
        _same((self._outbox_pin.digest.hex(), len(self._outbox_pin.raw)),
              (original["metadata"]["sha256"], original["metadata"]["byte_size"]))

    def _materialization_short(self):
        self._context()
        if self._materialized:
            self._outbox_pin.short()


class PublicationCreationGuard2(_Producer2):
    __slots__ = ("_view", "_base_entries_json", "_base_head_json", "_media_files", "_media_rows_json")

    def __init__(self, *, owner, view, record, pin, observed, media_files, media_rows, seal):
        from .sync_source_admission17 import require_view
        require_view(view)
        super().__init__(owner=owner, record=record, pin=pin, seal=seal)
        _same((view.binding.profile.identity, view.binding.store), (owner.profile.identity, owner.lock.store))
        if observed["head_status"] != "complete" or observed["incomplete"]:
            _fail("SELECTION_CHANGED")
        self._view = view
        self._base_entries_json = execution._canonical(observed["entries"])
        self._base_head_json = execution._canonical(observed["journal_head"])
        self._media_files, self._media_rows_json = tuple(media_files), execution._canonical(media_rows)

    def _media_short(self, connection=None):
        self._context()
        _media_files_short(self._media_files)
        if connection is not None:
            _same(_media_rows(connection), json.loads(self._media_rows_json))

    def _advance_for_view(self, view):
        record = self._record()
        if view is not self._view or sql.in_write_window():
            _fail("FACTORY_REQUIRED")
        _same(view.creation_basis, record["creation_basis"])
        observed = _base_successor(self.owner, record, json.loads(self._base_entries_json),
                                  json.loads(self._base_head_json))
        if observed["head_status"] != "complete":
            _fail("HEAD_INCOMPLETE")
        return observed

    def _advance_for_capture(self, captured):
        self._context()
        if captured is None or captured not in (self._view._admission, self._view._restore):
            _fail("FACTORY_REQUIRED")
        return self._advance_for_view(self._view)

    def check(self, record=None):
        original = self._record(record)
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._view._advance_creation(self)
        self._view.verify_source()
        self._media_short()
        self._payload_pin.full()
        if self._materialized:
            self.verify_materialization()
        validate_payload_bytes2(self._payload_pin.raw, original["payload_ref"], record=original,
            source_identity={"library_id": self._view.binding.core["binding"]["home_library_id"],
                             "node_id": self._view.binding.core["binding"]["home_node_id"]},
            core=self._view.binding.core)
        with sql.connection(self.profile, write=True) as con:
            permit = sql.require_writer(con, profile=self.profile)
            self._payload_pin.short()
            self._materialization_short()
            self._media_short(con)
            self._view.verify_for_commit(con, writer_permit=permit)
            sql.register_commit_check(permit, self._payload_pin.short, connection=con, profile=self.profile)
            sql.register_commit_check(permit, self._materialization_short, connection=con, profile=self.profile)
            sql.register_commit_check(permit, lambda: self._media_short(con), connection=con, profile=self.profile)
        self._view.verify_source()
        self._media_short()
        self._payload_pin.full()
        if self._materialized:
            self.verify_materialization()

    def check_head_repair(self, owner):
        record = self._record()
        if owner is not self.owner:
            _fail("OWNER_REQUIRED")
        self._view._current()
        self._payload_pin.full()
        if self._materialized:
            self.verify_materialization()
        _base_successor(owner, record, json.loads(self._base_entries_json), json.loads(self._base_head_json))

    def adopt_head_repair(self, owner):
        self._context()
        if owner is not self.owner:
            _fail("OWNER_REQUIRED")
        receipt = owner.creation_head_repair_receipt(producer=self)
        if receipt.owner is not owner or receipt.producer is not self:
            _fail("FACTORY_REQUIRED")
        _same(receipt.original, {"record_kind": "publication", "record_id": self.record["record_id"],
                                "record_sha256": permanent.digest(self.record)})
        self._view._advance_creation(self)
        self.check()


class PublicationCompletionGuard2(_Producer2):
    __slots__ = ("_recovery_capture",)

    def __init__(self, *, owner, captured, pin, seal):
        from .home_creation_recovery17 import OriginalCreationRecoveryCapture17
        if type(captured) is not OriginalCreationRecoveryCapture17:
            _fail("FACTORY_REQUIRED")
        super().__init__(owner=owner, record=captured.record, pin=pin, seal=seal)
        self._recovery_capture = captured

    def check(self, record=None):
        original = self._record(record)
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._recovery_capture.verify_source()
        self._payload_pin.full()
        if self._materialized:
            self.verify_materialization()
        if original["format_version"] == 2:
            validate_record_payload2(self._payload_pin.raw, original, _readonly(self.owner)["entries"])
        else:
            _same((self._payload_pin.digest.hex(), len(self._payload_pin.raw)),
                  (original["metadata"]["sha256"], original["metadata"]["byte_size"]))
            envelope = self.store.read_publication(original["metadata"],
                proved_stream=original["creation_basis"]["recovery_stream"])
            _verify_v1_body(envelope, original, self._recovery_capture.core)
        with sql.connection(self.profile, write=True) as con:
            permit = sql.require_writer(con, profile=self.profile)
            self._payload_pin.short()
            self._materialization_short()
            self._recovery_capture.verify_for_creation_commit(con, writer_permit=permit, producer=self)
            sql.register_commit_check(permit, self._payload_pin.short, connection=con, profile=self.profile)
            sql.register_commit_check(permit, self._materialization_short, connection=con, profile=self.profile)
        self._recovery_capture.verify_source()
        self._payload_pin.full()
        if self._materialized:
            self.verify_materialization()

    def check_head_repair(self, owner):
        self._context()
        if owner is not self.owner:
            _fail("OWNER_REQUIRED")
        self._payload_pin.full()
        self._recovery_capture.check_head_repair(owner, self)

    def adopt_head_repair(self, owner):
        self._context()
        if owner is not self.owner:
            _fail("OWNER_REQUIRED")
        self._recovery_capture.adopt_head_repair(owner, self)
        self._payload_pin.full()


@contextmanager
def _producer_context(producer):
    token = _CURRENT.set(producer)
    producer._context_token = _CONTEXT.set(producer)
    try:
        yield producer
    finally:
        try:
            producer._materialization_stack.close()
        finally:
            producer._active = False
            _CONTEXT.reset(producer._context_token)
            _CURRENT.reset(token)


def require_producer2(owner, kind, record, producer, *, entries):
    if kind != "publications" or type(producer) not in {PublicationCreationGuard2, PublicationCompletionGuard2}:
        _fail("PRODUCER_REQUIRED")
    if producer.owner is not owner:
        _fail("OWNER_REQUIRED")
    producer._record(record)
    actual = entries["publications"].get(record["record_id"])
    if type(producer) is PublicationCompletionGuard2:
        if actual is None or actual["record"] != record:
            _fail("ORIGINAL_SELECTION_REQUIRED")
    elif record["format_version"] != 2:
        _fail("PRODUCER_REQUIRED")
    producer.check(record)


def materialize_publication2(owner, record, producer):
    """Authority's closed post-reservation hook, copying only original bytes."""
    if type(producer) not in {PublicationCreationGuard2, PublicationCompletionGuard2} or producer.owner is not owner:
        _fail("PRODUCER_REQUIRED")
    original = producer._record(record)
    observed = _readonly(owner)
    entry = observed["entries"]["publications"].get(original["record_id"])
    if entry is None or entry["record"] != original or entry["state"] not in {"reserved", "committed"}:
        _fail("ORIGINAL_SELECTION_REQUIRED")
    if observed["head_status"] != "complete":
        _fail("HEAD_INCOMPLETE")
    require_producer2(owner, "publications", original, producer, entries=observed["entries"])
    producer._payload_pin.full()
    raw = producer._payload_pin.raw
    metadata = original["metadata"]
    _same((len(raw), hashlib.sha256(raw).hexdigest()), (metadata["byte_size"], metadata["sha256"]))
    with producer.store._mutating() as root:
        producer.store.assert_sendable(metadata["publication_id"])
        with safe._Directory(producer.store.data_dir / sync_state._OUTBOX, parent=root, create=True) as directory:
            name = metadata["publication_id"] + ".json"
            try:
                existing = sync_state._read_bytes(directory, name, MAX_PAYLOAD_BYTES)
            except FileNotFoundError:
                if original["format_version"] != 2:
                    _fail("ORIGINAL_BODY_REQUIRED")
                sync_state._write(directory, name, raw, immutable=True)
            else:
                _same(existing, raw)
    envelope = producer.store.read_publication(metadata,
        proved_stream=original["creation_basis"]["recovery_stream"])
    if original["format_version"] == 2:
        _same(envelope, validate_record_payload2(raw, original, observed["entries"]))
    else:
        _verify_v1_body(envelope, original, producer._recovery_capture.core)
    if producer._outbox_pin is None:
        producer._outbox_pin = capture_outbox17_locked(profile=producer.profile,
            store=producer.store, metadata=metadata, stack=producer._materialization_stack,
            proved_stream=original["creation_basis"]["recovery_stream"])
    _same(producer._outbox_pin.raw, raw)
    producer._materialized = True
    producer.verify_materialization()
    if type(producer) is PublicationCompletionGuard2:
        producer._recovery_capture.adopt_materialization(producer)
    producer.check(original)


def selected_publication2_locked(*, profile, store):
    """Return an actual selector only; a returned DTO grants no completion."""
    from .sync_source_lifecycle17 import _selectors, _owned
    profile = _selectors(profile, store)
    if lifecycle._key(store.data_dir) != lifecycle._key(profile.database.parent):
        _fail("OWNER_REQUIRED")
    state = store.read()
    core = state.get("source_execution")
    if state["format_version"] != 5 or core is None:
        _fail("STATE_REQUIRED")
    with _owned(store) as owner:
        observed = _readonly(owner)
        found = [item for item in observed["entries"]["publications"].values()
                 if item["record"]["core_sha256"] == permanent.digest(core)]
        if len(found) > 1:
            _fail("SELECTION_AMBIGUOUS")
        if not found:
            if observed["head_status"] != "complete" or observed["incomplete"]:
                _fail("SELECTION_UNPROVEN")
            _reject_unselected_outboxes(store, core, observed)
            return None
        record = found[0]["record"]
        if any(item != ("publications", record["record_id"]) for item in observed["incomplete"]):
            _fail("SELECTION_UNPROVEN")
        return {"record_id": record["record_id"], "record_sha256": permanent.digest(record),
                "record_state": found[0]["state"], "format_version": record["format_version"]}


def _reject_unselected_outboxes(store, core, observed):
    """Unknown same-source v1 bodies cannot become a new UUID opportunity."""
    known = set(observed["entries"]["publications"])
    started, count, total, opened = time.monotonic(), 0, 0, False
    try:
        with safe._Directory(store.data_dir / sync_state._OUTBOX, base=store.data_dir) as directory:
            opened = True
            with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as iterator:
                for entry in iterator:
                    if store.check_cancelled is not None:
                        store.check_cancelled()
                    if time.monotonic() - started > PAYLOAD_DEADLINE_SECONDS:
                        _fail("BODY_LIMIT")
                    info = directory.entry_stat(entry.name)
                    safe._safe_stat(info, directory=False)
                    if (info.st_nlink != 1 or os.name != "nt"
                            and (info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) & 0o077)):
                        _fail("UNSELECTED_BODY_UNKNOWN")
                    total += info.st_size
                    count += 1
                    if count > MAX_PAYLOAD_FILES + permanent.MAX_INTERNAL_PARTS or total > MAX_PAYLOAD_TOTAL_BYTES:
                        _fail("BODY_LIMIT")
                    if permanent._PART.fullmatch(entry.name):
                        continue
                    if not entry.name.endswith(".json"):
                        _fail("UNSELECTED_BODY_UNKNOWN")
                    identifier = entry.name[:-5]
                    permanent._uuid(identifier)
                    if identifier in known:
                        continue
                    metadata = store.find_publication(identifier, audit=True)
                    if metadata is None:
                        _fail("UNSELECTED_BODY_UNKNOWN")
                    envelope = store.read_publication(metadata)
                    if any(binding["claim"]["item_id"] == core["item_id"]
                           and (binding["origin"]["source_library_id"], binding["origin"]["source_node_id"])
                           == (core["binding"]["home_library_id"], core["binding"]["home_node_id"])
                           for binding in envelope["claims"]):
                        _fail("UNSELECTED_BODY_UNKNOWN")
            directory.verify()
    except FileNotFoundError:
        if opened:
            _fail("UNSELECTED_BODY_UNKNOWN")
        store._directory.verify()
        return


def resume_publication2_locked(*, profile, store):
    """Lazy ordinary-Cycle entry before snapshot/report/claim regeneration."""
    from . import sync_source_lifecycle17 as engine
    profile = engine._selectors(profile, store)
    with store.reference_operation():
        selected = selected_publication2_locked(profile=profile, store=store)
        if selected is None:
            return None
        metadata = complete_publication2_locked(profile=profile, store=store,
            record_id=selected["record_id"], expected_record_sha256=selected["record_sha256"])
        engine.install_publication17_locked(profile=profile, store=store,
                                           publication_id=metadata["publication_id"])
        return metadata


def complete_publication2_locked(*, profile, store, record_id, expected_record_sha256=None):
    """Complete the original selected body and journal without fresh admission."""
    from .sync_source_lifecycle17 import _selectors, _owned
    from .home_creation_recovery17 import capture_original_creation_recovery17_locked
    profile = _selectors(profile, store)
    with store.reference_operation(), _owned(store) as owner, ExitStack() as stack:
        selected = selected_publication2_locked(profile=profile, store=store)
        if selected is None or selected["record_id"] != record_id:
            _fail("ORIGINAL_SELECTION_REQUIRED")
        if expected_record_sha256 is not None:
            _same(expected_record_sha256, selected["record_sha256"])
        expected_record_sha256 = selected["record_sha256"]
        captured = stack.enter_context(capture_original_creation_recovery17_locked(
            profile=profile, store=store, folder="publications", record_id=record_id,
            expected_record_sha256=expected_record_sha256))
        record = captured.record
        if record["format_version"] == 2:
            pin = capture_payload2_locked(profile=profile, payload_ref=record["payload_ref"],
                stack=stack, check_cancelled=store.check_cancelled)
            validate_payload_bytes2(pin.raw, record["payload_ref"], record=record)
        else:
            pin = capture_outbox17_locked(profile=profile, store=store, metadata=record["metadata"], stack=stack,
                                         proved_stream=record["creation_basis"]["recovery_stream"])
        guard = PublicationCompletionGuard2(owner=owner, captured=captured, pin=pin, seal=_SEAL)
        with _producer_context(guard):
            # Head publication is a separately sealed operation; the original
            # read-only factory above has not repaired either head.
            if captured.actual_observation["head_status"] != "complete":
                owner.repair_creation_heads(producer=guard)
            guard.check()
            if captured.record_state == "committed":
                materialize_publication2(owner, record, guard)
            else:
                _same(owner.publish("publications", record, producer=guard), record)
            guard.check()
        pin.full()
        return deepcopy(record["metadata"])


def freeze_publication2_locked(*, profile, store, client, snapshot, claims,
        recovery_generation, recovery_stream):
    from . import sync_reports
    from .sync_source_admission17 import capture_current_admission_locked
    from . import sync_source_lifecycle17 as engine
    profile = engine._selectors(profile, store)
    with store.reference_operation():
        selected = selected_publication2_locked(profile=profile, store=store)
        if selected is not None:
            return complete_publication2_locked(profile=profile, store=store,
                record_id=selected["record_id"], expected_record_sha256=selected["record_sha256"])
        # Unselected candidate preparation precedes the sole current creation
        # view, so its full namespace pins need no relaxation or extra capture.
        raw, metadata, payload_ref = normalize_payload2(snapshot, claims,
            recovery_generation=recovery_generation, recovery_stream=recovery_stream)
        prepare_payload2_locked(profile=profile, store=store, raw=raw, payload_ref=payload_ref)
        state = store.read()
        core = state["source_execution"]
        with capture_current_admission_locked(profile=profile, store=store, client=client,
                item_id=core["item_id"]) as view, ExitStack() as stack:
            pin = capture_payload2_locked(profile=profile, payload_ref=payload_ref, stack=stack,
                                         check_cancelled=store.check_cancelled)
            envelope = validate_payload_bytes2(pin.raw, payload_ref)
            record = engine._publication_record17(view, metadata, envelope)
            record.update(format_version=2, expected_before=_before(state, core), payload_ref=payload_ref)
            with engine._owned(store) as owner:
                observed = _readonly(owner)
                validate_record2("publications", record, observed["contract"])
                # Candidate data is not proof. Re-export actual committed SQL
                # and report rows under this sole creation view before reserve.
                with sql.connection(profile) as con:
                    media_files, media_rows = _actual_media2(con, profile, envelope, stack)
                    actual = snapshots.export_snapshot(con, publication_id=metadata["publication_id"],
                        media={video["video_id"]: video["media"] for video in envelope["snapshot"]["videos"]},
                        reports=sync_reports.export_reports(con, profile=profile))
                    _same(actual, envelope["snapshot"])
                    _same(actual["version"], view._prepared.version)
                view.verify_source()
                guard = PublicationCreationGuard2(owner=owner, view=view, record=record, pin=pin,
                    observed=observed, media_files=media_files, media_rows=media_rows, seal=_SEAL)
                with _producer_context(guard):
                    guard.check()
                    _same(owner.publish("publications", record, producer=guard), record)
                    guard.check()
        return metadata


def _media_rows(connection):
    rows = [dict(row) for row in connection.execute(
        "SELECT video_id,content_sha256,files_json,status FROM main.video_asset "
        "WHERE status='processed' ORDER BY video_id LIMIT ?", (snapshots.MAX_VIDEOS + 1,))]
    if len(rows) > snapshots.MAX_VIDEOS:
        _fail("MEDIA_LIMIT")
    return rows


def _media_files_short(files):
    for directory, name, held, before, opened in files:
        directory.verify()
        _same(source.file_signature(directory.entry_stat(name)), before)
        _same(source.file_signature(os.fstat(held.fileno())), opened)


def _actual_media2(connection, profile, envelope, stack):
    """Bind caller descriptors to real SQL assets and held local file identity.

    The ordinary Cycle already hashes the actual bytes before entering this
    factory. This adds SQL/path/size/extension provenance and writer-time pins;
    it does not claim a second media SHA or accept a caller verification DTO.
    """
    from . import db
    rows = _media_rows(connection)
    descriptors = {video["video_id"]: video["media"] for video in envelope["snapshot"]["videos"]}
    _same(set(descriptors), {row["video_id"] for row in rows})
    held = []
    for asset in rows:
        descriptor = descriptors[asset["video_id"]]
        _same(descriptor["sha256"], asset["content_sha256"])
        submitted = db.uj(asset["files_json"], {}).get("video")
        if type(submitted) is not str or not submitted:
            _fail("MEDIA_PATH_INVALID")
        try:
            path = Path(submitted.replace("\\", "/"))
            if not path.is_absolute():
                path = profile.media.parent / path
            if ".." in path.parts or any(":" in part for part in path.parts[1:]):
                raise ValueError
            path = Path(os.path.abspath(path))
            path.relative_to(profile.media)
        except ValueError:
            _fail("MEDIA_PATH_INVALID")
        _same(descriptor["extension"], path.suffix[1:].lower())
        directory = stack.enter_context(safe._Directory(path.parent, base=profile.media))
        before_info = directory.entry_stat(path.name)
        before = source.file_signature(before_info)
        stream = stack.enter_context(directory.open_file(path.name))
        opened_info = os.fstat(stream.fileno())
        opened = source.file_signature(opened_info)
        # stat/fstat expose different ctime semantics on Windows. Bind the
        # common file identity, then retain and freshly recheck each full
        # five-field signature against its own original observation.
        _same((before_info.st_dev, before_info.st_ino, before_info.st_size, before_info.st_mtime_ns),
              (opened_info.st_dev, opened_info.st_ino, opened_info.st_size, opened_info.st_mtime_ns))
        _same(before, source.file_signature(directory.entry_stat(path.name)))
        _same(opened, source.file_signature(os.fstat(stream.fileno())))
        _same(opened[2], descriptor["byte_size"])
        held.append((directory, path.name, stream, before, opened))
    for binding in envelope["claims"]:
        if "origin" not in binding:
            _fail("CLAIM_ORIGIN_REQUIRED")
        _same(binding["origin"]["media_sha256"], descriptors[binding["video_id"]]["sha256"])
    stack.callback(_media_files_short, tuple(held))
    return held, rows


def _before(state, core):
    from .sync_source_lifecycle17 import _entry
    recovery = state["recovery"]
    if recovery is None:
        _fail("STATE_REQUIRED")
    return {"binding": deepcopy(state["binding"]), "local_epoch": state["local_epoch"],
        "core_sha256": permanent.digest(core), "admission_ref": deepcopy(state["source_admission"]),
        "recovery_generation": recovery["generation"], "recovery_stream": deepcopy(recovery["stream"]),
        "control_revision": recovery["revision"], "claim": deepcopy(_entry(state, core)["claim"]),
        "budget_pending_sha256": None if state["budget_pending"] is None else permanent.digest(sync_state._stable_pending(state["budget_pending"])),
        "source_control_pending_sha256": None if state["source_control_pending"] is None else permanent.digest(sync_state._stable_source_control(state["source_control_pending"])),
        "pending_publication": deepcopy(state["pending_publication"]), "source_authority": deepcopy(state["source_authority"])}


class PublicationSelectionPins2:
    """Add original payload identities to the existing descriptor/State CAS."""
    __slots__ = ("_original", "_payload")

    def __init__(self, original, payload):
        if type(payload) is not PayloadPin2 or payload._seal is not _PIN_SEAL:
            _fail("FACTORY_REQUIRED")
        self._original, self._payload = original, payload

    def short(self):
        self._original.short()
        self._payload.short()

    def full(self):
        self._original.full()
        self._payload.full()
