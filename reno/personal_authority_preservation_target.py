"""Owned raw-preservation target I/O; all payloads come from a live S1 fd.

Only the coordinator's original operation token opens this acquisition. Wire
metadata, paths, streams, and public observations are never accepted as owners.
"""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import io
import json
import os
import posixpath
import stat
import uuid
from weakref import WeakKeyDictionary, ref

from . import personal_authority_preservation_source as source
from . import personal_authority_failure_cleanup as _failure_cleanup
from . import personal_authority_preservation_io as _io
from . import personal_authority_raw_preservation_contract as contract
from . import personal_preservation_files as files
from . import storage_lifecycle as lifecycle, sync_media as safe
from . import storage_personal_authority_roots as physical


_Directory, _File = _io._Directory, _io._File

_TOKEN = object()
_LIVE = {}
_CLOSED = WeakKeyDictionary()
_LATE = frozenset({"source-verified.json", "manifest.json", "receipt.json"})
_FIXED_DIRS = ("attempts", "copy-attempts", "payload", "copy-records", "failures")


class AuthorityRawTargetError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_PRESERVATION_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise AuthorityRawTargetError(suffix)


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


def _error(exc, suffix):
    if isinstance(exc, AuthorityRawTargetError) or getattr(exc, "code", "").startswith("AUTHORITY_RAW_PRESERVATION_"):
        return exc
    return AuthorityRawTargetError(suffix)


def _operation(token, *, failure=False):
    from . import personal_authority_raw_preservation as coordinator
    function = coordinator._failure_operation_record if failure else coordinator._operation_record
    return function(token)


class _TargetToken:
    __slots__ = ("__weakref__",)

    def __new__(cls, token):
        if token is not _TOKEN:
            _fail("OWNER_MISMATCH")
        return super().__new__(cls)






def _record(token, *, phases=("idle",)):
    state = _LIVE.get(id(token))
    if type(token) is not _TargetToken or state is None or state.token is not token:
        _fail("OWNER_MISMATCH")
    if state.owner != lifecycle._owner():
        _fail("OWNER_MISMATCH")
    if state.phase not in phases:
        raise state.operation.budget.remember(AuthorityRawTargetError("REENTRANT"))
    state.check(callback=False)
    return state


class _Target(_io._TargetIO):
    # Fixed internal forwarding preserves the original module fault seams.
    # No caller may provide a resource implementation or select an adapter.
    def _fail(self, suffix):
        return _fail(suffix)

    def _error(self, exc, suffix):
        return _error(exc, suffix)

    def _identity(self, directory):
        return _identity(directory)

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
        self.owner, self.mode = lifecycle._owner(), mode
        self.phase = "opening"
        self.resources = []
        self.resource_indices = {}
        self.directory_records, self.reader_records = {}, {}
        self.roots, self.directories, self.files = {}, {}, {}
        self.namespace = self.directory = None
        self.pending = None
        self.new_effects = []

    @property
    def budget(self):
        return self.operation.budget

    @property
    def limits(self):
        return self.operation.limits

    def check(self, *, callback=True):
        # Budget rejects callback reentry before the lifecycle physical check.
        self.budget.check(callback=callback)
        if _operation(self.operation_token) is not self.operation:
            _fail("OWNER_MISMATCH")






    def open_new(self):
        from . import storage_roots, storage_members as members
        profile = self.operation.profile
        if (physical.RAW_PRESERVATION_DIRECTORY != contract.DIRECTORY
                or physical.RAW_PRESERVATION_PREFIX != ".reno-authority-raw-preservation"
                or not physical._selected(contract.DIRECTORY)
                or members._key(profile.database.parent / contract.DIRECTORY) not in storage_roots._protection([profile])[1]):
            _fail("PROTECTION_UNAVAILABLE")
        for role, path in (("data", profile.database.parent), ("media", profile.media),
                           ("reports", profile.reports), ("checkpoints", profile.checkpoints)):
            self.roots[role] = self.open_directory(path)
        self.namespace = self.mkdir(self.roots["data"], contract.DIRECTORY, allow_existing=True)
        self.directory = self.mkdir(self.namespace, self.operation.operation_id, key="")
        for name in _FIXED_DIRS:
            self.mkdir(self.directory, name, key=name)
        self.operation.failure_target = {
            "data": self.roots["data"].identity, "namespace": self.namespace.identity,
            "operation": self.directory.identity, "failures": self.directories["failures"].identity}









    def publish(self, key, kind, value):
        contract._fact_name(key)
        if key in self.files or key in self.operation.graph.raw:
            _fail("OPERATION_EXISTS")
        parent_key, name = posixpath.split(key)
        parent = self.directories.get(parent_key)
        if parent is None:
            _fail("TARGET_UNSAFE")
        normalized = self.operation.graph.normalize(kind, value)
        raw = contract._canonical(normalized, self.limits["max_fact_bytes"])
        temporary = ".write-" + str(uuid.uuid4()) + ".tmp"
        writer = self.writer(self.directory, temporary, temporary)
        for index in range(0, len(raw), self.limits["chunk_bytes"]):
            self.write_chunk(writer, raw[index:index + self.limits["chunk_bytes"]])
        self.close_writer(writer)
        held = self.open_reader(self.directory, temporary, temporary, collect=True)
        if held.raw != raw:
            _fail("COPY_MISMATCH")
        self.verify_file(held)
        try:
            _close_stream_original(held.stream)
        except BaseException as exc:
            raise _error(exc, "CLOSE_FAILED") from None
        self.released("stream", held.stream)
        del self.files[temporary]
        self.verify_directory(parent)
        self.verify_directory(self.directory)
        safe._rename_new(self.directory.original, temporary, parent.original, name)
        if os.name != "nt":
            os.fsync(parent.original.fd)
            if parent is not self.directory:
                os.fsync(self.directory.original.fd)
        self.verify_directory(parent)
        final = self.open_reader(parent, name, key, collect=True)
        if final.raw != raw or (final.path_identity["device"], final.path_identity["inode"]) != (held.path_identity["device"], held.path_identity["inode"]):
            _fail("COPY_MISMATCH")
        self.operation.graph.add(key, final.raw)
        return final.raw



    def open_existing(self):
        profile = self.operation.profile
        self.roots["data"] = self.open_directory(profile.database.parent)
        self.namespace = self.open_directory(profile.database.parent / contract.DIRECTORY,
                                             parent=self.roots["data"], private=True)
        self.directory = self.open_directory(self.namespace.original.path / self.operation.operation_id,
                                             parent=self.namespace, key="", private=True)
        pending, selected_files, entry_count = [self.directory], [], 0
        while pending:
            parent = pending.pop()
            for name in self.listing(parent):
                entry_count += 1
                if entry_count > self.limits["max_target_entries"]:
                    _fail("TARGET_UNSAFE")
                key = parent.key + "/" + name if parent.key else name
                contract._key(key, depth=self.limits["max_target_depth"])
                info = parent.original.entry_stat(name)
                directory = stat.S_ISDIR(info.st_mode)
                safe._safe_stat(info, directory=directory)
                if directory:
                    pending.append(self.open_directory(parent.original.path / name, parent=parent, key=key, private=True))
                else:
                    selected_files.append((key, parent, name))
        selected_files.sort(key=lambda row: (row[0] != "request.json", row[0].encode("utf-8")))
        for key, parent, name in selected_files:
            control = not key.startswith("payload/")
            if control:
                contract._fact_name(key)
            record = self.open_reader(parent, name, key, collect=control)
            if control:
                self.operation.graph.add(key, record.raw)
            if key == "request.json":
                expected = self.operation.expected_request_sha256
                if expected is not None and record.sha256 != expected:
                    _fail("REQUEST_MISMATCH")
                request = self.operation.graph.request()
                self.operation.run_id = request["run_id"]
                if (request["operation_id"] != self.operation.operation_id
                        or request["profile"]["profile_paths"] != [str(getattr(profile, field)) for field in ("database", "media", "reports", "checkpoints")]
                        or request["profile"]["roots"]["data"]["identity"] != self.roots["data"].identity
                        or request["target"] != self.target_declaration()):
                    _fail("REQUEST_MISMATCH")
        if self.mode == "publisher":
            before = self.operation.target_closed
            self.operation.graph.target_closed(before)
        else:
            contract.cross_validate_package(fact_bytes=self.operation.graph.raw, max_fact_bytes=self.limits["max_fact_bytes"])
            before = self.operation.graph.load("source-verified.json", "source_verified")["target_closed"]
        actual = self.inventory()
        if self.mode == "verify":
            if set(self.files) != {row["relative_key"] for row in before["files"]} | _LATE:
                _fail("INCOMPLETE")
            actual["files"] = [row for row in actual["files"] if row["relative_key"] not in _LATE]
            for row in actual["directories"]:
                if row["relative_key"] == "":
                    row["children"] = [name for name in row["children"] if name not in _LATE]
        contract._same(actual, {"directories": before["directories"], "files": before["files"]}, "INVENTORY_MISMATCH")

    def target_declaration(self):
        return {"namespace": contract.DIRECTORY, "namespace_identity": dict(self.namespace.identity),
                "operation_relative_key": self.operation.operation_id, "operation_identity": dict(self.directory.identity)}


def _close_scope_original(state, primary):
    return _io._close_scope_original(state, primary)


@contextmanager
def _scope(operation_token, mode):
    token = _TargetToken(_TOKEN)
    state = _Target(token, operation_token, mode)
    _LIVE[id(token)] = state
    primary = None
    try:
        state.open_new() if mode == "target" else state.open_existing()
        state.phase = "idle"
        yield token
        state.phase = "verifying"
        if state.pending is not None:
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
    if mode == "target":
        closed = {"format_version": 1, "kind": "authority_raw_preservation_target_closed",
            "operation_id": operation.operation_id, "run_id": operation.run_id,
            "request_sha256": operation.graph.sha("request.json"),
            "selection_sha256": operation.graph.sha("selection.json"),
            "operation_identity": dict(state.directory.identity), **state.inventory(), "counters": state.budget.snapshot()}
        operation.graph.target_closed(closed)
    else:
        closed = {"mode": mode, "operation_id": operation.operation_id, "run_id": operation.run_id,
                  "counters": state.budget.snapshot()}
    _CLOSED[token] = source._closed_owner_ref(state.owner), ref(operation), closed


def new_target_locked(operation_token):
    return _scope(operation_token, "target")


def existing_target_locked(operation_token, *, mode):
    if mode not in {"publisher", "verify"}:
        _fail("OWNER_MISMATCH")
    return _scope(operation_token, mode)


def profile_declaration(token):
    state = _record(token)
    if state.mode != "target":
        _fail("OWNER_MISMATCH")
    return {"profile_paths": [str(getattr(state.operation.profile, field)) for field in ("database", "media", "reports", "checkpoints")],
            "roots": {role: {"path": str(record.original.path), "identity": dict(record.identity)}
                      for role, record in state.roots.items()}}


def target_declaration(token):
    return _record(token).target_declaration()


def publish_control(token, relative_key, kind, value):
    state = _record(token)
    if state.mode == "verify" or state.mode == "publisher" and relative_key not in _LATE:
        _fail("OWNER_MISMATCH")
    state.phase = "publishing"
    try:
        return state.publish(relative_key, kind, value)
    except BaseException as exc:
        raise state.budget.remember(exc)
    finally:
        state.phase = "idle"


def copy_entry(token, capture, entry_id):
    state = _record(token)
    if state.mode != "target":
        _fail("OWNER_MISMATCH")
    state.phase = "copying"
    try:
        operation = state.operation
        entry = operation.graph.source_entry(entry_id)
        copy_id = str(uuid.uuid4())
        parent = state.mkdir(state.directories["payload"], copy_id, key="payload/" + copy_id)
        key = f"payload/{copy_id}/{entry_id}.raw"
        attempt = {"format_version": 1, "kind": "authority_raw_preservation_copy_attempt",
            "operation_id": operation.operation_id, "run_id": operation.run_id,
            "request_sha256": operation.graph.sha("request.json"), "selection_sha256": operation.graph.sha("selection.json"),
            "copy_id": copy_id, "source_entry_id": entry_id, "target_relative_key": key,
            "target_parent_identity": dict(parent.identity), "sha256": entry["sha256"], "byte_size": entry["byte_size"]}
        attempt_path = "copy-attempts/" + copy_id + ".json"
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
        row = {"source_entry_id": entry_id, "target_relative_key": key, "target_parent_identity": dict(parent.identity),
               "copy_path_identity": dict(copied.path_identity), "copy_handle_identity": dict(copied.handle_identity),
               "sha256": copied.sha256, "byte_size": copied.path_identity["byte_size"]}
        record = {"format_version": 1, "kind": "authority_raw_preservation_copy_record",
            "operation_id": operation.operation_id, "run_id": operation.run_id,
            "request_sha256": operation.graph.sha("request.json"), "selection_sha256": operation.graph.sha("selection.json"),
            "copy_attempt_sha256": operation.graph.sha(attempt_path), "copy": row}
        state.publish(f"copy-records/{entry_id}.json", "copy_record", record)
        state.pending = None
        return record
    except BaseException as exc:
        raise state.budget.remember(exc)
    finally:
        state.phase = "idle"


def _require_source_writer_original(token, operation, selection_sha256, entry_id):
    state = _record(token, phases=("copying",))
    pending = state.pending
    if (state.operation is not operation or pending is None or pending["entry_id"] != entry_id
            or selection_sha256 != state.operation.graph.sha("selection.json")):
        _fail("OWNER_MISMATCH")
    captured = source._origin(pending["capture"])
    if captured.phase != "reading" or captured.preservation_operation is not operation:
        _fail("OWNER_MISMATCH")


def _write_source_chunk_original(token, raw):
    state = _record(token, phases=("copying",))
    if state.pending is None:
        _fail("OWNER_MISMATCH")
    captured = source._origin(state.pending["capture"])
    if captured.phase != "reading" or captured.preservation_operation is not state.operation:
        _fail("OWNER_MISMATCH")
    state.write_chunk(state.pending["writer"], raw)


def consume_closed(token, operation_token):
    operation = _operation(operation_token)
    if type(token) is not _TargetToken:
        _fail("OWNER_MISMATCH")
    record = _CLOSED.get(token)
    if record is None or not source._closed_owner_matches(record[0]) or record[1]() is not operation:
        _fail("OWNER_MISMATCH")
    del _CLOSED[token]
    return json.loads(contract._canonical(record[2], operation.limits["max_fact_bytes"]))


def write_failure_once(operation_token, *, phase, exc):
    """Bounded diagnostic attempt; never replace first or rescue completion."""
    operation = _operation(operation_token, failure=True)
    budget = operation.budget
    roots = operation.failure_target
    if roots is None:
        return
    budget.begin_failure()
    code = getattr(exc, "code", None)
    if code not in {"AUTHORITY_RAW_PRESERVATION_" + suffix for suffix in contract._FAILURE_SUFFIXES}:
        code = "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED" if getattr(operation, "callback_exception", None) is exc else "AUTHORITY_RAW_PRESERVATION_PUBLICATION_FAILED"
    graph = operation.graph
    value = {"format_version": 1, "kind": "authority_raw_preservation_failure",
        "operation_id": operation.operation_id, "run_id": operation.run_id,
        "request_sha256": graph.sha("request.json") if "request.json" in graph.raw else None,
        "selection_sha256": graph.sha("selection.json") if "selection.json" in graph.raw else None,
        "phase": phase if phase in contract._PHASES else "publication", "code": code,
        "counters": budget.snapshot(), "effects": [], "effects_complete": False}
    normalized = contract.normalize_failure(value, fact_bytes=graph.raw, max_fact_bytes=operation.limits["max_fact_bytes"])
    raw = contract._canonical(normalized, operation.limits["max_failure_bytes"])
    owned, stream = [], None
    primary = budget.first

    def guard():
        _operation(operation_token, failure=True)
        if budget.remaining_seconds <= 0:
            raise primary

    try:
        parent = None
        paths = [(operation.profile.database.parent, "data"),
                 (operation.profile.database.parent / contract.DIRECTORY, "namespace"),
                 (operation.profile.database.parent / contract.DIRECTORY / operation.operation_id, "operation"),
                 (operation.profile.database.parent / contract.DIRECTORY / operation.operation_id / "failures", "failures")]
        for path, role in paths:
            guard()
            directory = source._OwnedDirectory(path, base=path if parent is None else operation.profile.database.parent, parent=parent)
            directory.__enter__()
            owned.append(directory)
            if _identity(directory) != roots[role]:
                _fail("TARGET_CHANGED")
            parent = directory
        guard()
        stream = parent.open_file(operation.run_id + ".json", write=True, create=True)
        # Parent is already private. No new directory or ACL mutation occurs
        # in this diagnostic-only channel; inherited private ACLs are checked.
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
