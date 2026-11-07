"""Original format-2 target ownership, partial history and current observations.

The fixed shared IO base owns byte/resource mechanics. This module owns Q1
layout, prefix policy and generation closure; a graph or old receipt never
substitutes for current original fds and normal closes.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
import posixpath
import stat
import uuid
from weakref import WeakKeyDictionary, ref

from . import personal_authority_preservation_io as _io
from . import personal_authority_preservation_source as source
from . import personal_authority_failure_cleanup as _failure_cleanup
from . import personal_authority_preservation_target as first
from . import personal_authority_preservation_routing as routing
from . import personal_authority_raw_preservation2_contract as contract
from . import personal_authority_resume_policy as policy
from . import personal_preservation_files as files
from . import storage_lifecycle as lifecycle, sync_media as safe
from . import storage_personal_authority_roots as physical


AuthorityRawTargetError = first.AuthorityRawTargetError
_TOKEN = object()
_LIVE = {}
_CLOSED = WeakKeyDictionary()
_FIXED_DIRS = ("copy-attempts", "payload", "copy-records", "failures")


def _fail(suffix):
    raise AuthorityRawTargetError(suffix) from None


def _error(exc, suffix):
    if isinstance(exc, AuthorityRawTargetError) or getattr(exc, "code", "").startswith("AUTHORITY_RAW_PRESERVATION_"):
        return exc
    return AuthorityRawTargetError(suffix)


def _read_bytes_original(stream, count):
    return io.BufferedReader.read(stream, count)


def _seek_original(stream, offset):
    return io.BufferedReader.seek(stream, offset)


def _write_bytes_original(stream, raw):
    return os.write(stream.fileno(), raw)


def _flush_original(stream):
    return io.BufferedRandom.flush(stream)


def _fsync_original(stream):
    return os.fsync(stream.fileno())


def _close_stream_original(stream):
    return stream.close()


def _identity(directory):
    return dict(zip(("device", "inode"), physical._directory_identity(directory)))


def _operation(token, *, failure=False):
    from . import personal_authority_raw_preservation2 as coordinator
    if type(token) is not object:
        _fail("OWNER_MISMATCH")
    original = coordinator._LIVE.get(token)
    if original is None or original.token is not token:
        _fail("OWNER_MISMATCH")
    operation = routing.operation_record(token, failure=failure)
    if operation is not original:
        _fail("OWNER_MISMATCH")
    return operation


def _checkpoint(phase):
    """Fixed internal fault seam; never an external publication callback."""


class _TargetToken:
    __slots__ = ("__weakref__",)

    def __new__(cls, token):
        if token is not _TOKEN:
            _fail("OWNER_MISMATCH")
        return super().__new__(cls)


def _record(token, *, phases=("idle",)):
    state = _LIVE.get(id(token))
    if type(token) is not _TargetToken or state is None or state.token is not token or state.owner != lifecycle._owner():
        _fail("OWNER_MISMATCH")
    if state.phase not in phases:
        raise state.operation.budget.remember(AuthorityRawTargetError("REENTRANT"))
    state.check(callback=False)
    return state


class _Target(_io._TargetIO):
    _fail = staticmethod(_fail)
    _error = staticmethod(_error)
    _identity = staticmethod(_identity)

    def _read_bytes_original(self, stream, count):
        return _read_bytes_original(stream, count)

    def _seek_original(self, stream, offset):
        return _seek_original(stream, offset)

    def _write_bytes_original(self, stream, raw):
        return _write_bytes_original(stream, raw)

    def _flush_original(self, stream):
        return _flush_original(stream)

    def _fsync_original(self, stream):
        return _fsync_original(stream)

    def _close_stream_original(self, stream):
        return _close_stream_original(stream)

    def __init__(self, token, operation_token, mode):
        self.token, self.operation_token = token, operation_token
        self.operation = _operation(operation_token)
        self.owner, self.mode, self.phase = lifecycle._owner(), mode, "opening"
        self.resources, self.resource_indices = [], {}
        self.directory_records, self.reader_records = {}, {}
        self.roots, self.directories, self.files = {}, {}, {}
        self.namespace = self.directory = None
        self.pending = None
        self.new_effects = []
        self.bootstrap_complete = self.prefix_loaded = False
        self.observed_copies = {}

    @property
    def budget(self):
        return self.operation.budget

    @property
    def limits(self):
        return self.operation.limits

    def check(self, *, callback=True):
        self.budget.check(callback=callback)
        if _operation(self.operation_token) is not self.operation:
            _fail("OWNER_MISMATCH")

    def read_file(self, record, *, collect=False):
        # The earlier path stat is not a cap on the actual opened object: a
        # replacement may have occurred before open_reader captured its F.
        if record.key in {"request.json", policy.CLAIM_NAME}:
            self.verify_file(record)
            maximum = policy.MAX_REQUEST_BYTES if record.key == "request.json" else 0
            if record.path_identity["byte_size"] > maximum:
                _fail("TARGET_UNSAFE")
        return super().read_file(record, collect=collect)

    def _detached(self, value):
        return json.loads(contract._canonical(value, self.limits["max_fact_bytes"]))

    def _root_files(self):
        return tuple(contract._ROOT)

    def _empty_sha(self):
        return hashlib.sha256(b"").hexdigest()

    def _open_base_original(self, *, create, all_roots):
        from . import storage_roots, storage_members as members
        profile = self.operation.profile
        if self.mode != "verify":
            if (getattr(physical, "RAW_PRESERVATION2_DIRECTORY", None) != policy.DIRECTORY
                    or physical.RAW_PRESERVATION_PREFIX != ".reno-authority-raw-preservation"
                    or not physical._selected(policy.DIRECTORY)
                    or members._key(profile.database.parent / policy.DIRECTORY) not in storage_roots._protection([profile])[1]):
                _fail("PROTECTION_UNAVAILABLE")
        roots = (("data", profile.database.parent), ("media", profile.media),
                 ("reports", profile.reports), ("checkpoints", profile.checkpoints))
        for role, path in roots if all_roots else roots[:1]:
            self.roots[role] = self.open_directory(path)
        self.namespace = (self.mkdir(self.roots["data"], policy.DIRECTORY, allow_existing=True) if create
                          else self.open_directory(profile.database.parent / policy.DIRECTORY,
                                                   parent=self.roots["data"], private=True))

    def _make_run_directories_original(self, slot):
        if "runs" not in self.directories:
            self.mkdir(self.directory, "runs", key="runs")
        run = contract._run(slot)
        root = self.mkdir(self.directories["runs"], f"{slot:02d}", key=run)
        for name in _FIXED_DIRS:
            self.mkdir(root, name, key=run + "/" + name)

    def _held_parent_original(self, key):
        parent = self.directory
        parts = posixpath.dirname(key).split("/") if posixpath.dirname(key) else []
        current = ""
        for name in parts:
            current = current + "/" + name if current else name
            if current not in self.directories:
                self.open_directory(parent.original.path / name, parent=parent, key=current, private=True)
            parent = self.directories[current]
        return parent

    def _read_control_original(self, key, *, request=False):
        kind, _, _ = contract._fact_path(key)
        parent = self._held_parent_original(key)
        name = posixpath.basename(key)
        if request:
            size = parent.original.entry_stat(name).st_size
            if size > policy.MAX_REQUEST_BYTES:
                _fail("TARGET_UNSAFE")
        if key in self.files:
            record = self.files[key]
            actual, raw = self.read_file(record, collect=True)
            if actual != record.sha256 or record.raw != raw:
                _fail("TARGET_CHANGED")
        else:
            record = self.open_reader(parent, name, key, collect=True)
        self.operation.graph.add(key, record.raw)
        self.operation.graph.load(key, kind)
        return record

    def _check_request_original(self):
        operation = self.operation
        request = operation.graph.request()
        if (operation.expected_request_sha256 is not None and operation.graph.sha("request.json") != operation.expected_request_sha256
                or request["operation_id"] != operation.operation_id
                or request["profile"]["profile_paths"] != [str(getattr(operation.profile, field)) for field in ("database", "media", "reports", "checkpoints")]
                or request["profile"]["roots"]["data"]["identity"] != self.roots["data"].identity
                or request["target"] != self.target_declaration()):
            _fail("REQUEST_MISMATCH")
        for role, record in self.roots.items():
            if request["profile"]["roots"][role]["identity"] != record.identity:
                _fail("REQUEST_MISMATCH")

    def target_declaration(self):
        return {"namespace": policy.DIRECTORY, "namespace_identity": dict(self.namespace.identity),
                "operation_relative_key": self.operation.operation_id, "operation_identity": dict(self.directory.identity)}

    def publish(self, key, kind, value):
        contract._fact_path(key)
        if key in self.files or key in self.operation.graph.raw:
            _fail("OPERATION_EXISTS")
        parent_key, name = posixpath.split(key)
        parent = self.directories.get(parent_key)
        stage = self.directories.get(contract._run(self.operation.slot))
        if parent is None or stage is None:
            _fail("TARGET_UNSAFE")
        normalized = self.operation.graph.normalize_at(key, kind, value)
        raw = contract._canonical(normalized, self.operation.graph.cap(kind))
        temporary = ".write-" + str(uuid.uuid4()) + ".tmp"
        temporary_key = stage.key + "/" + temporary
        writer = self.writer(stage, temporary, temporary_key)
        for index in range(0, len(raw), self.limits["chunk_bytes"]):
            self.write_chunk(writer, raw[index:index + self.limits["chunk_bytes"]])
        self.close_writer(writer)
        held = self.open_reader(stage, temporary, temporary_key, collect=True)
        if held.raw != raw:
            _fail("COPY_MISMATCH")
        self.verify_file(held)
        try:
            _close_stream_original(held.stream)
        except BaseException as exc:
            raise _error(exc, "CLOSE_FAILED") from None
        self.released("stream", held.stream)
        del self.files[temporary_key]
        self.verify_directory(stage)
        self.verify_directory(parent)
        safe._rename_new(stage.original, temporary, parent.original, name)
        if os.name != "nt":
            os.fsync(parent.original.fd)
            if parent is not stage:
                os.fsync(stage.original.fd)
        self.verify_directory(stage)
        self.verify_directory(parent)
        final = self.open_reader(parent, name, key, collect=True)
        if (final.raw != raw or (final.path_identity["device"], final.path_identity["inode"])
                != (held.path_identity["device"], held.path_identity["inode"])):
            _fail("COPY_MISMATCH")
        self.operation.graph.add(key, final.raw)
        if key == contract._run(self.operation.slot) + "/run-attempt.json":
            self._register_failure_target_original()
        return final.raw

    def _register_failure_target_original(self):
        base = contract._run(self.operation.slot)
        self.operation.failure_target = {"data": self.roots["data"].identity, "namespace": self.namespace.identity,
            "operation": self.directory.identity, "runs": self.directories["runs"].identity,
            "run": self.directories[base].identity, "failures": self.directories[base + "/failures"].identity}

    def scan_prefix(self):
        pending, selected, count = [self.directory], [], 0
        observed_keys = {""}
        while pending:
            parent = pending.pop()
            for name in self.listing(parent):
                count += 1
                if count > self.limits["max_target_entries"]:
                    _fail("TARGET_UNSAFE")
                key = parent.key + "/" + name if parent.key else name
                if key in observed_keys:
                    _fail("TARGET_CHANGED")
                observed_keys.add(key)
                contract._key(key, depth=self.limits["max_target_depth"])
                info = parent.original.entry_stat(name)
                directory = stat.S_ISDIR(info.st_mode)
                safe._safe_stat(info, directory=directory)
                if directory:
                    self.operation.graph._directory_key(key)
                    if key not in self.directories:
                        self.open_directory(parent.original.path / name, parent=parent, key=key, private=True)
                    pending.append(self.directories[key])
                else:
                    category = self.operation.graph._file_class(key)
                    selected.append((key, parent, name, category))
        selected.sort(key=lambda row: (row[0] != "request.json", row[0].encode("utf-8")))
        for key, parent, name, category in selected:
            if category == "fact":
                # Graph additions precede cross-validation: lexical filenames
                # need not be in dependency order, but every fact stays raw.
                if key in self.files:
                    record = self.files[key]
                    actual, raw = self.read_file(record, collect=True)
                    if actual != record.sha256 or raw != record.raw:
                        _fail("TARGET_CHANGED")
                else:
                    if key == "request.json" and parent.original.entry_stat(name).st_size > policy.MAX_REQUEST_BYTES:
                        _fail("TARGET_UNSAFE")
                    record = self.open_reader(parent, name, key, collect=True)
                self.operation.graph.add(key, record.raw)
                if key == "request.json":
                    self._check_request_original()
            elif key in self.files:
                self.read_file(self.files[key])
            else:
                self.open_reader(parent, name, key)
        if observed_keys != set(self.directories) | set(self.files):
            _fail("TARGET_CHANGED")
        contract.cross_validate_prefix(fact_bytes=self.operation.graph.raw, inventory=self.inventory(),
                                       max_fact_bytes=self.limits["max_fact_bytes"])
        self.prefix_loaded = True
        _checkpoint("prefix_loaded")

    def open_existing(self):
        self._open_base_original(create=False, all_roots=False)
        self.directory = self.open_directory(self.namespace.original.path / self.operation.operation_id,
                                             parent=self.namespace, key="", private=True)
        self._read_control_original("request.json", request=True)
        self._check_request_original()
        self.scan_prefix()
        if self.mode == "publisher":
            before = self.operation.target_closed
            if before is None or self.operation.source_closed is None:
                _fail("OWNER_MISMATCH")
            self.operation.graph.target_closed(before)
            contract._same(self.inventory(), {"directories": before["directories"], "files": before["files"]}, "HISTORY_CHANGED")
        else:
            contract.cross_validate_package(fact_bytes=self.operation.graph.raw, generation=self.operation.slot,
                inventory=self.inventory(), max_fact_bytes=self.limits["max_fact_bytes"])


def _close_scope_original(state, primary):
    return _io._close_scope_original(state, primary)


@contextmanager
def _scope(operation_token, mode):
    from . import personal_authority_preservation_admission as admission
    token = _TargetToken(_TOKEN)
    state = _Target(token, operation_token, mode)
    _LIVE[id(token)] = state
    primary = None
    try:
        if mode == "create":
            admission.create_original(token)
        elif mode == "resume":
            admission.resume_original(token)
        else:
            state.open_existing()
        state.phase = "idle"
        yield token
        state.phase = "verifying"
        if state.pending is not None:
            _fail("INCOMPLETE")
        if mode in {"create", "resume"}:
            expected = {row["source_entry_id"]: row["copy_record_sha256"]
                        for row in state.operation.graph.expected_selected(state.operation.slot)}
            contract._same(state.observed_copies, expected, "INCOMPLETE")
            if mode == "resume" and not state.prefix_loaded:
                _fail("INCOMPLETE")
        state.verify_pass()
        with state.budget.terminal_window():
            state.verify_pass()
            state.phase = "closing"
            _LIVE.pop(id(token), None)
            try:
                primary = _close_scope_original(state, None)
            except BaseException as exc:
                primary = _error(exc, "CLOSE_FAILED")
            if primary is not None:
                raise primary
            state.budget.assert_settled()
    except BaseException as exc:
        primary = state.budget.remember(exc)
    finally:
        _LIVE.pop(id(token), None)
        if state.resources:
            try:
                with state.budget.terminal_window():
                    retained = _close_scope_original(state, primary)
                    if primary is None:
                        primary = retained
            except BaseException as exc:
                if primary is None:
                    primary = _error(exc, "CLOSE_FAILED")
                elif exc is not primary:
                    primary.add_note("AUTHORITY_RAW_PRESERVATION_CLOSE_FAILED")
        state.phase = "closed"
    if primary is not None:
        raise primary
    state.budget.assert_settled()
    operation = state.operation
    if mode in {"create", "resume"}:
        closed = {"format_version": 2, "kind": "authority_raw_preservation_target_closed",
            "operation_id": operation.operation_id, "slot": operation.slot, "run_id": operation.run_id,
            "request_sha256": operation.graph.sha("request.json"), "selection_sha256": operation.graph.sha("selection.json"),
            "run_attempt_sha256": operation.graph.sha(contract._run(operation.slot) + "/run-attempt.json"),
            "operation_identity": dict(state.directory.identity), "reserved_slots": list(operation.occupied_slots),
            **state.inventory(), "selected_copies": operation.graph.expected_selected(operation.slot),
            "counters": state.budget.snapshot()}
        operation.graph.target_closed(closed)
    else:
        closed = {"mode": mode, "operation_id": operation.operation_id, "slot": operation.slot,
                  "run_id": operation.run_id, "counters": state.budget.snapshot()}
    state.budget.assert_settled()
    _CLOSED[token] = source._closed_owner_ref(state.owner), ref(operation), ref(operation.guard), closed


def new_target_locked(operation_token):
    operation = _operation(operation_token)
    if operation.mode != "create" or operation.slot != 1:
        _fail("OWNER_MISMATCH")
    return _scope(operation_token, "create")


def resume_target_locked(operation_token):
    operation = _operation(operation_token)
    if operation.mode != "resume" or operation.slot != 2:
        _fail("OWNER_MISMATCH")
    return _scope(operation_token, "resume")


def existing_target_locked(operation_token, *, mode):
    operation = _operation(operation_token)
    if mode not in {"publisher", "verify"} or (mode == "verify") != operation.readonly:
        _fail("OWNER_MISMATCH")
    return _scope(operation_token, mode)


def profile_declaration(token):
    state = _record(token)
    if state.mode != "create":
        _fail("OWNER_MISMATCH")
    return {"profile_paths": [str(getattr(state.operation.profile, field))
            for field in ("database", "media", "reports", "checkpoints")],
        "roots": {role: {"path": str(record.original.path), "identity": dict(record.identity)}
                  for role, record in state.roots.items()}}


def target_declaration(token):
    return _record(token).target_declaration()


def inventory_declaration(token):
    state = _record(token)
    return state._detached(state.inventory())


def establish_request_anchor(token):
    from . import personal_authority_preservation_admission as admission
    state = _record(token)
    try:
        admission.establish_anchor_original(token)
    except BaseException as exc:
        raise state.budget.remember(exc)


def admission_declaration(token):
    from . import personal_authority_preservation_admission as admission
    return admission.declaration_original(token)


def publish_control(token, relative_key, kind, value):
    state = _record(token)
    if (state.mode == "verify" or state.mode == "publisher" and relative_key not in contract._late(state.operation.slot)
            or state.mode == "resume" and not relative_key.startswith(contract._run(2) + "/")):
        _fail("OWNER_MISMATCH")
    state.phase = "publishing"
    try:
        return state.publish(relative_key, kind, value)
    except BaseException as exc:
        raise state.budget.remember(exc)
    finally:
        state.phase = "idle"


def load_prefix(token):
    state = _record(token)
    if (state.mode != "resume" or not state.bootstrap_complete or state.prefix_loaded
            or contract._run(2) + "/run-attempt.json" not in state.operation.graph.raw):
        _fail("OWNER_MISMATCH")
    state.phase = "prefix"
    try:
        state.scan_prefix()
    except BaseException as exc:
        raise state.budget.remember(exc)
    finally:
        state.phase = "idle"


def _header(operation, kind, **values):
    return {"format_version": 2, "kind": "authority_raw_preservation_" + kind,
        "operation_id": operation.operation_id, "slot": operation.slot, "run_id": operation.run_id,
        "request_sha256": operation.graph.sha("request.json"), **values}


def copy_entry(token, capture, entry_id):
    state = _record(token)
    if state.mode not in {"create", "resume"} or state.mode == "resume" and not state.prefix_loaded:
        _fail("OWNER_MISMATCH")
    operation = state.operation
    entry = operation.graph.source_entry(entry_id)
    if entry_id in state.observed_copies:
        raise state.budget.remember(AuthorityRawTargetError("OPERATION_EXISTS"))
    state.phase = "copying"
    try:
        captured = source._acquisition(capture)
        selected_sha = operation.graph.sha("selection.json")
        if (captured.preservation_operation is not operation or captured.guard is not operation.guard
                or captured.preservation_selection_sha256 != selected_sha or entry_id not in captured.entry_files):
            _fail("OWNER_MISMATCH")
        source._require_original_guard(captured)
        captured.verify_file(captured.entry_files[entry_id])
        old_path = "runs/01/copy-records/" + entry_id + ".json"
        if operation.slot == 2 and old_path in operation.graph.raw:
            copy = operation.graph.load(old_path)["copy"]
            record = state.files.get(copy["target_relative_key"])
            if record is None:
                _fail("COPY_MISMATCH")
            expected = {"relative_key": copy["target_relative_key"], "parent_identity": copy["target_parent_identity"],
                "path_identity": copy["copy_path_identity"], "handle_identity": copy["copy_handle_identity"],
                "sha256": copy["sha256"], "byte_size": copy["byte_size"]}
            actual = {"relative_key": record.key, "parent_identity": record.parent.identity,
                "path_identity": record.path_identity, "handle_identity": record.handle_identity,
                "sha256": record.sha256, "byte_size": record.path_identity["byte_size"]}
            contract._same(actual, expected, "COPY_MISMATCH")
            digest, _ = state.read_file(record)
            if digest != copy["sha256"]:
                _fail("COPY_MISMATCH")
            source._require_original_guard(captured)
            captured.verify_file(captured.entry_files[entry_id])
            state.observed_copies[entry_id] = operation.graph.sha(old_path)
            _checkpoint("copy_reused")
            return
        base = contract._run(operation.slot)
        copy_id = str(uuid.uuid4())
        parent_key = base + "/payload/" + copy_id
        parent = state.mkdir(state.directories[base + "/payload"], copy_id, key=parent_key)
        key = parent_key + "/" + entry_id + ".raw"
        attempt_path = base + "/copy-attempts/" + copy_id + ".json"
        attempt = _header(operation, "copy_attempt", selection_sha256=selected_sha,
            run_attempt_sha256=operation.graph.sha(base + "/run-attempt.json"), copy_id=copy_id,
            source_entry_id=entry_id, target_relative_key=key, target_parent_identity=dict(parent.identity),
            sha256=entry["sha256"], byte_size=entry["byte_size"])
        state.publish(attempt_path, "copy_attempt", attempt)
        writer = state.writer(parent, entry_id + ".raw", key)
        state.pending = {"entry_id": entry_id, "capture": capture, "writer": writer}
        source._copy_entry_original(capture, entry_id, token)
        if writer["written"] != entry["byte_size"]:
            _fail("COPY_MISMATCH")
        state.close_writer(writer)
        copied = state.open_reader(parent, entry_id + ".raw", key)
        if copied.sha256 != entry["sha256"]:
            _fail("COPY_MISMATCH")
        copy = {"source_entry_id": entry_id, "target_relative_key": key, "target_parent_identity": dict(parent.identity),
            "copy_path_identity": dict(copied.path_identity), "copy_handle_identity": dict(copied.handle_identity),
            "sha256": copied.sha256, "byte_size": copied.path_identity["byte_size"]}
        record_path = base + "/copy-records/" + entry_id + ".json"
        state.publish(record_path, "copy_record", _header(operation, "copy_record", selection_sha256=selected_sha,
            copy_attempt_sha256=operation.graph.sha(attempt_path), copy=copy))
        state.pending = None
        state.observed_copies[entry_id] = operation.graph.sha(record_path)
        _checkpoint("copy_record_published")
    except BaseException as exc:
        raise state.budget.remember(exc)
    finally:
        state.phase = "idle"


def _require_source_writer_original(token, operation, selection_sha256, entry_id):
    state = _record(token, phases=("copying",))
    pending = state.pending
    if (state.operation is not operation or pending is None or pending["entry_id"] != entry_id
            or selection_sha256 != operation.graph.sha("selection.json")):
        _fail("OWNER_MISMATCH")
    captured = source._origin(pending["capture"])
    if captured.phase != "reading" or captured.preservation_operation is not operation or captured.guard is not operation.guard:
        _fail("OWNER_MISMATCH")


def _write_source_chunk_original(token, raw):
    state = _record(token, phases=("copying",))
    if state.pending is None:
        _fail("OWNER_MISMATCH")
    captured = source._origin(state.pending["capture"])
    if (captured.phase != "reading" or captured.preservation_operation is not state.operation
            or captured.guard is not state.operation.guard):
        _fail("OWNER_MISMATCH")
    state.write_chunk(state.pending["writer"], raw)


def consume_closed(token, operation_token):
    operation = _operation(operation_token)
    if type(token) is not _TargetToken:
        _fail("OWNER_MISMATCH")
    record = _CLOSED.get(token)
    if (record is None or not source._closed_owner_matches(record[0]) or record[1]() is not operation
            or record[2]() is not operation.guard):
        _fail("OWNER_MISMATCH")
    del _CLOSED[token]
    return json.loads(contract._canonical(record[3], operation.limits["max_fact_bytes"]))


def write_failure_once(operation_token, *, phase, exc):
    operation = _operation(operation_token, failure=True)
    budget, roots, graph = operation.budget, operation.failure_target, operation.graph
    run = contract._run(operation.slot)
    if roots is None or run + "/run-attempt.json" not in graph.raw:
        return
    budget.begin_failure()
    code = getattr(exc, "code", None)
    if code not in {"AUTHORITY_RAW_PRESERVATION_" + suffix for suffix in contract._FAILURE_SUFFIXES}:
        code = ("AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED" if operation.callback_exception is exc
                else "AUTHORITY_RAW_PRESERVATION_PUBLICATION_FAILED")
    value = _header(operation, "failure", selection_sha256=graph.sha("selection.json") if "selection.json" in graph.raw else None,
        phase=phase if phase in contract._PHASES else "publication", code=code,
        counters=budget.snapshot(), effects=[], effects_complete=False)
    normalized = graph.normalize_at(run + "/failures/" + operation.run_id + ".json", "failure", value)
    raw = contract._canonical(normalized, operation.limits["max_failure_bytes"])
    owned, stream, primary = [], None, budget.first
    def guard():
        _operation(operation_token, failure=True)
        if budget.remaining_seconds <= 0:
            raise primary
    try:
        parent = None
        data = operation.profile.database.parent
        op_path = data / policy.DIRECTORY / operation.operation_id
        paths = [(data, "data"), (data / policy.DIRECTORY, "namespace"), (op_path, "operation"),
                 (op_path / "runs", "runs"), (op_path / run, "run"), (op_path / run / "failures", "failures")]
        for path, role in paths:
            guard()
            directory = source._OwnedDirectory(path, base=path if parent is None else data, parent=parent)
            directory.__enter__()
            owned.append(directory)
            if _identity(directory) != roots[role]:
                _fail("TARGET_CHANGED")
            parent = directory
        guard()
        stream = parent.open_file(operation.run_id + ".json", write=True, create=True)
        files.require_private(parent.path / (operation.run_id + ".json"), directory=False)
        guard()
        ticket = budget.begin_write(len(raw), failure=True)
        try:
            count = _write_bytes_original(stream, raw)
        except BaseException as error:
            budget.fail_write(ticket, error)
        budget.end_write(ticket, count)
        if count == len(raw):
            guard()
            _flush_original(stream)
            guard()
            _fsync_original(stream)
    finally:
        _failure_cleanup._close_failure_original(stream, owned, primary, _close_stream_original)
