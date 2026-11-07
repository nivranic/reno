"""Owned, metered raw authority census; no copy, SQL or authority grants.

The caller owns lifecycle EX. This factory holds original directories, files
and existing authority SH locks, reads every selected byte, then verifies and
closes its resources. A closed observation is historical raw evidence only.
"""
from __future__ import annotations

from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from functools import wraps
import hashlib
import io
import json
import math
import os
from pathlib import Path
import stat
import sys
import time
import uuid
from types import MappingProxyType
from weakref import WeakKeyDictionary, ref

from . import personal_authority_files as locks
from . import personal_authority_preservation_contract as contract
from . import personal_authority_source_projection as projection
from . import personal_preservation_files as files
from . import storage_lifecycle as lifecycle, sync_media as safe
from . import storage_personal_authority_roots as physical

_TOKEN = object()
_LIVE = {}
_CLOSED = WeakKeyDictionary()
_PREFIX = ".reno-personal-authority"
_NODE = "node-identity.json"
_CHUNK = 128 * 1024
_MAX_READ_BYTES = 384 * 1024**3
_PRESERVATION_CLOSED = WeakKeyDictionary()


def _closed_owner_ref(owner):
    """Historical owner identity without retaining a Task's result/traceback."""
    return owner[0], owner[1], None if owner[2] is None else ref(owner[2])


def _closed_owner_matches(owner):
    current = lifecycle._owner()
    if current[:2] != owner[:2]:
        return False
    if owner[2] is None:
        return current[2] is None
    task = owner[2]()
    return task is not None and task is current[2]


class AuthorityRawCaptureError(ValueError):
    def __init__(self, suffix):
        self.code = "AUTHORITY_RAW_CAPTURE_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise AuthorityRawCaptureError(suffix) from None


class _First(BaseException):
    def __init__(self, original):
        self.original = original


@dataclass(frozen=True)
class RawCaptureLimits:
    capsule_limits: dict | None = None
    max_read_bytes: int = _MAX_READ_BYTES
    deadline_seconds: float = 120.0
    node_json_bytes: int = 4096

    def __post_init__(self):
        copied, _, _, _ = _options(self.capsule_limits, self.max_read_bytes,
                                   self.deadline_seconds, self.node_json_bytes)
        object.__setattr__(self, "capsule_limits", MappingProxyType(copied))


def _options(capsule_limits, max_read_bytes, deadline_seconds, node_json_bytes):
    if capsule_limits is not None and type(capsule_limits) not in (dict, MappingProxyType):
        _fail("LIMIT_INVALID")
    try:
        copied = contract._limits(None if capsule_limits is None else dict(capsule_limits))
    except contract.AuthorityPreservationContractError:
        _fail("LIMIT_INVALID")
    if (type(max_read_bytes) is not int or not 1 <= max_read_bytes <= _MAX_READ_BYTES
            or type(node_json_bytes) is not int or not 1 <= node_json_bytes <= 4096
            or type(deadline_seconds) not in (int, float)
            or not math.isfinite(deadline_seconds) or not 0 < deadline_seconds <= 120):
        _fail("LIMIT_INVALID")
    return copied, max_read_bytes, deadline_seconds, node_json_bytes


def _validated_options(limits):
    if type(limits) is not RawCaptureLimits:
        _fail("LIMIT_INVALID")
    try:
        # Never dispatch caller-shadowable __post_init__ or trust frozen as a
        # substitute for validating the actual values consumed by the budget.
        if type(limits.capsule_limits) is not MappingProxyType:
            _fail("LIMIT_INVALID")
        return _options(limits.capsule_limits, limits.max_read_bytes,
                        limits.deadline_seconds, limits.node_json_bytes)
    except AttributeError:
        _fail("LIMIT_INVALID")


def _read_bytes_original(stream, count):
    return io.BufferedReader.read(stream, count)


def _seek_original(stream, offset):
    return io.BufferedReader.seek(stream, offset)


def _close_stream_original(stream):
    return io.BufferedReader.close(stream)


def _close_directory_handle_original(handle):
    if os.name == "nt":
        return bool(safe._kernel.CloseHandle(handle))
    os.close(handle)
    return True


class _OwnedDirectory(safe._Directory):
    """Original no-follow acquisition with checked, exhaustive native close."""
    def __enter__(self):
        self._raw_opening = True
        try:
            return safe._Directory.__enter__(self)
        finally:
            self._raw_opening = False

    def __exit__(self, exc_type, exc, tb):
        # The original __enter__ closes partial acquisitions with None while
        # an exception is active. Keep that primary if a close also fails.
        primary = exc if exc is not None else (sys.exception() if self._raw_opening else None)
        first = self._close_failure
        # Retire inherited path/fd operations before any original closer runs.
        # Detach ownership once: reentry must never close a numeric handle twice.
        self._known_closed = True
        self.fd = None
        handles, self.handles = self.handles, []
        try:
            for handle in reversed(handles):
                try:
                    if not _close_directory_handle_original(handle):
                        raise safe.ctypes.WinError(safe.ctypes.get_last_error())
                except BaseException as failure:
                    if first is None:
                        first = failure
                    retained = primary if primary is not None else first
                    if primary is not None or failure is not first:
                        try:
                            retained.add_note("AUTHORITY_RAW_CAPTURE_DIRECTORY_CLOSE_FAILED")
                        except BaseException:
                            # Optional annotation cannot replace the first or
                            # skip the remaining original resource releases.
                            pass
        finally:
            self._close_failure = first
        if primary is None and first is not None:
            raise first
        return False


def _canonical(value):
    return contract._canonical(value)


def _digest(value):
    return hashlib.sha256(_canonical(value)).hexdigest()


def _name_key(value):
    return value.rstrip(" .").casefold()


def _selected(value):
    key = _name_key(value)
    return key == _NODE or key.startswith(_PREFIX)


def _error(exc, fallback):
    if isinstance(exc, _First):
        return exc.original
    if isinstance(exc, AuthorityRawCaptureError):
        return exc
    return AuthorityRawCaptureError(fallback)


class _Budget:
    def __init__(self, options, callback, operation_budget=None):
        structural, self.max_read_bytes, self.seconds, self.node_cap = options
        self.structural = MappingProxyType(structural)
        self.callback, self.started = callback, time.monotonic()
        self.first, self.terminal = None, 0
        self.read_bytes = self.read_calls = self.seek_calls = 0
        self.operation_budget = operation_budget

    def remember(self, exc):
        original = exc.original if isinstance(exc, _First) else exc
        if self.operation_budget is not None:
            original = self.operation_budget.remember(original)
        if self.first is None:
            self.first = original
        return self.first

    def check(self):
        if self.first is not None:
            raise _First(self.first)
        if self.operation_budget is not None:
            try:
                self.operation_budget.check(callback=self.terminal == 0)
            except BaseException as exc:
                raise _First(self.remember(exc)) from None
        if self.callback is not None and self.terminal == 0:
            try:
                self.callback()
            except BaseException as exc:
                raise _First(self.remember(exc)) from None
        if self.first is not None:
            raise _First(self.first)
        if time.monotonic() - self.started > self.seconds:
            raise _First(self.remember(AuthorityRawCaptureError("DEADLINE_LIMIT")))

    def read(self, stream, count):
        self.check()
        remaining = self.max_read_bytes - self.read_bytes
        if count < 1 or remaining < count:
            raise _First(self.remember(AuthorityRawCaptureError("READ_LIMIT")))
        ticket = self.operation_budget.begin_read(count) if self.operation_budget is not None else None
        self.read_calls += 1
        try:
            raw = _read_bytes_original(stream, count)
        except BaseException as exc:
            if self.operation_budget is not None:
                self.operation_budget.fail_read(ticket, exc)
            raise
        if type(raw) is bytes:
            self.read_bytes += len(raw)
        if self.operation_budget is not None:
            # Both original ledgers record actual bytes before any callback.
            self.operation_budget.end_read(ticket, raw)
        if type(raw) is not bytes or self.read_bytes > self.max_read_bytes:
            _fail("READ_LIMIT")
        self.check()
        return raw

    def seek(self, stream, offset=0):
        self.check()
        if self.operation_budget is not None:
            self.operation_budget.note_seek()
        self.seek_calls += 1
        result = _seek_original(stream, offset)
        self.check()
        return result

    @contextmanager
    def terminal_window(self):
        previous = self.terminal
        self.terminal += 1
        try:
            with self.operation_budget.terminal_window() if self.operation_budget is not None else nullcontext():
                yield
        finally:
            self.terminal = previous


class _Pass:
    def __init__(self, budget):
        self.budget = budget
        self.entries = self.directories = self.metadata = 0

    def record(self, value):
        self.budget.check()
        self.metadata += len(_canonical(value))
        if self.metadata > self.budget.structural["max_json_bytes"]:
            _fail("METADATA_LIMIT")

    def entry(self, name):
        self.budget.check()
        self.entries += 1
        if self.entries > self.budget.structural["max_entries"]:
            _fail("ENTRY_LIMIT")
        self.record(name)

    def directory(self):
        self.budget.check()
        self.directories += 1
        if self.directories > self.budget.structural["max_directories"]:
            _fail("DIRECTORY_LIMIT")


class _MeteredLock(locks._ExistingLock):
    def __init__(self, directory, budget, *, lock_name="authority.lock"):
        self.raw_budget = budget
        if type(lock_name) is str and lock_name == "authority.lock":
            super().__init__(directory, exclusive=False, timeout=0, check=budget.check)
        else:
            super().__init__(directory, exclusive=False, timeout=0, check=budget.check, lock_name=lock_name)

    def verify(self, *, cancel=True, content=True):
        # Before acquisition the original caller selects content=False. Never
        # read a byte locked by another owner in that pre-acquisition branch.
        locks._ExistingLock.verify(self, cancel=cancel, content=False)
        if content:
            with nullcontext() if cancel else self.raw_budget.terminal_window():
                self.raw_budget.seek(self.stream, 0)
                if self.raw_budget.read(self.stream, 2) != b"\x00":
                    _fail("LOCK_UNPROVEN")
            locks._ExistingLock.verify(self, cancel=False, content=False)


@dataclass
class _DirectoryRecord:
    original: object
    identity: tuple
    handles: tuple
    names: tuple = ()


@dataclass
class _FileRecord:
    parent: _DirectoryRecord
    name: str
    key: str
    stream: object
    descriptor: int
    path_identity: dict
    handle_identity: dict
    borrowed: bool
    sha256: str | None = None
    node_raw: bytes | None = None


class _Source:
    def __init__(self, token, profile, operation_id, guard, budget):
        self.token, self.profile, self.operation_id = token, profile, operation_id
        self.guard, self.budget, self.owner = guard, budget, lifecycle._owner()
        self.phase, self.resources = "opening", []
        self.roots, self.directories, self.files, self.locks = {}, {}, {}, {}
        self.directory_records = {}
        self.positions, self.source_bytes = {}, 0
        self.metadata_raw = None
        self.entry_files = {}
        self.preservation_operation = None
        self.preservation_selection_sha256 = None

    def own(self, kind, original):
        self.resources.append((kind, original))
        return original

    def directory(self, path, *, parent=None):
        self.budget.check()
        directory = _OwnedDirectory(path, base=path if parent is None else self.profile.database.parent,
                                    parent=None if parent is None else parent.original)
        directory.__enter__()
        self.own("directory", directory)
        record = _DirectoryRecord(directory, physical._directory_identity(directory), tuple(directory.handles))
        self.directory_records[id(directory)] = record
        return record

    def verify_directory(self, record):
        self.budget.check()
        if tuple(record.original.handles) != record.handles:
            _fail("SOURCE_CHANGED")
        if physical._directory_identity(record.original) != record.identity:
            _fail("SOURCE_CHANGED")

    def listing(self, record, census, *, selected=False):
        self.verify_directory(record)
        names = []
        directory = record.original
        with os.scandir(safe._native_path(directory.path) if os.name == "nt" else directory.fd) as entries:
            for entry in entries:
                census.entry(entry.name)
                if not selected or _selected(entry.name):
                    names.append(entry.name)
        self.verify_directory(record)
        return tuple(sorted(names, key=lambda value: value.encode("utf-8")))

    def remember_position(self, key, identity):
        position = tuple(identity)
        if position in self.positions:
            _fail("SOURCE_UNREPRESENTABLE")
        self.positions[position] = key

    def verify_file(self, record):
        self.verify_directory(record.parent)
        if (type(record.stream) is not io.BufferedReader or record.stream.closed
                or record.stream.fileno() != record.descriptor):
            _fail("SOURCE_CHANGED")
        if (files.identity(record.parent.original.entry_stat(record.name)) != record.path_identity
                or files.identity(os.fstat(record.descriptor)) != record.handle_identity):
            _fail("SOURCE_CHANGED")

    def read_file(self, record, *, collect=False, target_token=None):
        self.verify_file(record)
        self.budget.seek(record.stream)
        digest, size, chunks = hashlib.sha256(), 0, []
        expected = record.path_identity["byte_size"]
        while True:
            self.verify_file(record)
            raw = self.budget.read(record.stream, min(_CHUNK, expected - size + 1))
            self.verify_file(record)
            if not raw:
                break
            size += len(raw)
            if size > expected:
                _fail("SOURCE_CHANGED")
            digest.update(raw)
            if target_token is not None:
                from . import personal_authority_preservation_routing as routing
                target = routing.target_module(target_token)
                target._write_source_chunk_original(target_token, raw)
            if collect:
                chunks.append(raw)
        if size != expected:
            _fail("SOURCE_CHANGED")
        self.verify_file(record)
        result = digest.hexdigest()
        if record.sha256 is not None and record.sha256 != result:
            _fail("SOURCE_CHANGED")
        return result, b"".join(chunks) if collect else None

    def open_file(self, parent, name, key, info, census):
        if len(self.files) >= self.budget.structural["max_files"]:
            _fail("FILE_LIMIT")
        path_identity = files.identity(info)
        self.source_bytes += path_identity["byte_size"]
        if self.source_bytes > self.budget.structural["max_total_bytes"]:
            _fail("SOURCE_BYTES_LIMIT")
        self.remember_position(key, (path_identity["device"], path_identity["inode"]))
        parent_key = key.rsplit("/", 1)[0] if "/" in key else ""
        lock = self.locks.get(parent_key)
        if lock is not None and _name_key(name) != lock.lock_name:
            lock = None
        if lock is not None:
            stream = lock.stream
        else:
            stream = parent.original.open_file(name)
            self.own("stream", stream)
        if type(stream) is not io.BufferedReader:
            _fail("SOURCE_UNSAFE")
        handle_identity = files.identity(os.fstat(stream.fileno()))
        if any(path_identity[field] != handle_identity[field] for field in path_identity if field != "ctime_ns"):
            _fail("SOURCE_CHANGED")
        record = _FileRecord(parent, name, key, stream, stream.fileno(), path_identity,
                             handle_identity, lock is not None)
        census.record([key, path_identity, handle_identity])
        self.files[key] = record
        record.sha256, record.node_raw = self.read_file(record,
            collect=key == _NODE and path_identity["byte_size"] <= self.budget.node_cap)

    def open(self):
        self.budget.check()
        for role, path in (("data", self.profile.database.parent), ("media", self.profile.media),
                           ("reports", self.profile.reports), ("checkpoints", self.profile.checkpoints)):
            self.roots[role] = self.directory(path)
        data = self.roots["data"]
        census = _Pass(self.budget)
        census.directory()
        self.directories[""] = data
        self.remember_position("", data.identity)
        data.names = self.listing(data, census, selected=True)
        # Validate the whole broad root selection before narrowing its wire
        # representation. Unsupported roots cannot disappear from success.
        for name in data.names:
            contract._component(name)
            comparison = _name_key(name)
            if comparison != _NODE and not comparison.startswith(contract._PREFIX):
                _fail("SOURCE_UNREPRESENTABLE")
        pending = [("", data)]
        while pending:
            prefix, parent = pending.pop()
            for name in parent.names:
                self.budget.check()
                contract._component(name)
                safe._entry_name(name)
                key = prefix + "/" + name if prefix else name
                info = parent.original.entry_stat(name)
                is_directory = stat.S_ISDIR(info.st_mode)
                safe._safe_stat(info, directory=is_directory)
                if not prefix and _name_key(name) == _NODE and is_directory:
                    _fail("SOURCE_UNSAFE")
                if is_directory:
                    census.directory()
                    child = self.directory(parent.original.path / name, parent=parent)
                    if child.identity != (str(info.st_dev), str(info.st_ino)):
                        _fail("SOURCE_CHANGED")
                    self.remember_position(key, child.identity)
                    self.directories[key] = child
                    if not prefix and _name_key(name).startswith(_PREFIX):
                        lock = _MeteredLock(child.original, self.budget)
                        lock.__enter__()
                        self.own("lock", lock)
                        self.locks[key] = lock
                    child.names = self.listing(child, census)
                    pending.append((key, child))
                else:
                    self.open_file(parent, name, key, info, census)

    def verify_pass(self):
        self.budget.check()
        _require_original_guard(self)
        for root in self.roots.values():
            self.verify_directory(root)
        for lock in self.locks.values():
            _MeteredLock.verify(lock)
        census = _Pass(self.budget)
        for key, directory in self.directories.items():
            census.directory()
            if self.listing(directory, census, selected=not key) != directory.names:
                _fail("SOURCE_CHANGED")
            census.record([key, directory.identity, directory.names])
        for record in self.files.values():
            self.read_file(record)
            census.record([record.key, record.path_identity, record.handle_identity])
        for lock in self.locks.values():
            _MeteredLock.verify(lock)
        _require_original_guard(self)

    def verify(self):
        self.verify_pass()
        with self.budget.terminal_window():
            self.verify_pass()

    def make_metadata(self):
        ordered = lambda values: sorted(values, key=lambda value: value.encode("utf-8"))
        directory_ids = {key: f"d{index:08d}" for index, key in enumerate(ordered(self.directories))}
        file_ids = {key: f"f{index:08d}" for index, key in enumerate(ordered(self.files))}
        self.entry_files = {entry_id: self.files[key] for key, entry_id in file_ids.items()}
        entry_ids = {**directory_ids, **file_ids}
        directories = []
        for key in ordered(self.directories):
            record = self.directories[key]
            children = [(key + "/" + name if key else name) for name in record.names]
            directories.append({"entry_id": directory_ids[key], "relative_key": key,
                "source_identity": dict(zip(("device", "inode"), record.identity)),
                "listing": "complete", "children": [entry_ids[child] for child in children]})
        listed_files = []
        for key in ordered(self.files):
            record = self.files[key]
            listed_files.append({"entry_id": file_ids[key], "relative_key": key,
                "role": contract._role(key), "sha256": record.sha256,
                "byte_size": record.path_identity["byte_size"],
                "source_directory_identity": dict(zip(("device", "inode"), record.parent.identity)),
                "source_path_identity": record.path_identity, "source_handle_identity": record.handle_identity})
        node, node_observation = self.node(file_ids)
        roots = {role: {"path": str(record.original.path),
                        "identity": dict(zip(("device", "inode"), record.identity))}
                 for role, record in self.roots.items()}
        capsule = {"format_version": contract.FORMAT_VERSION, "kind": contract.KIND, "scope": contract.SCOPE,
            "operation_id": self.operation_id, "layout_profile": contract.LAYOUT_PROFILE,
            "layout_source_sha256": contract.LAYOUT_SOURCE_SHA256,
            "source_context": {"profile": {"profile_paths": [str(getattr(self.profile, field))
                for field in ("database", "media", "reports", "checkpoints")], "roots": roots}, "node": node},
            "coverage": {"declared_tree": "complete", "declared_copies": "none", "reason": None},
            "directories": directories, "files": listed_files, "unresolved": [],
            "copies": [], "artifacts": [], "links": []}
        capsule = contract.normalize(capsule, limits=dict(self.budget.structural))
        value = {"format_version": 1, "kind": "personal_authority_raw_source_observation",
            "operation_id": self.operation_id, "capsule": capsule, "node_observation": node_observation,
            "source_projection": projection.from_capsule(capsule, limits=dict(self.budget.structural))}
        self.metadata_raw = _canonical(value)

    def node(self, file_ids):
        record = self.files.get(_NODE)
        if record is None:
            variant = next((key for key in self.files if "/" not in key and _name_key(key) == _NODE), None)
            return None, {"state": "missing" if variant is None else "noncanonical_name",
                          "entry_id": None if variant is None else file_ids[variant]}
        observation = {"state": "json_limit" if record.node_raw is None else "invalid", "entry_id": file_ids[_NODE]}
        if record.node_raw is None:
            return None, observation
        def pairs(values):
            result = {}
            for key, value in values:
                if key in result:
                    raise ValueError
                result[key] = value
            return result
        def constant(_value):
            raise ValueError
        try:
            value = json.loads(record.node_raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
            if type(value) is not dict or set(value) != {"instance_id"} or type(value["instance_id"]) is not str:
                raise ValueError
            parsed = uuid.UUID(value["instance_id"])
            if str(parsed) != value["instance_id"] or not parsed.int:
                raise ValueError
        except (ValueError, UnicodeError, RecursionError, OverflowError):
            return None, observation
        return {"node_id": value["instance_id"], "sha256": record.sha256,
                "path_identity": record.path_identity, "handle_identity": record.handle_identity}, {
                    "state": "parsed", "entry_id": file_ids[_NODE]}

    def close(self, primary):
        self.phase = "closing"
        first = primary

        def note():
            try:
                BaseException.add_note(first, "AUTHORITY_RAW_CAPTURE_CLOSE_FAILED")
            except BaseException:
                pass

        def remember(exc):
            nonlocal first
            if first is None:
                # Hold the actual first before mapping can allocate or fail.
                first = exc
                try:
                    first = _error(exc, "CLOSE_FAILED")
                except BaseException:
                    note()
            elif exc is not first:
                note()

        # Retire this source's ownership before any original closer can reenter.
        resources, self.resources = self.resources, []
        try:
            with self.budget.terminal_window():
                for kind, original in reversed(resources):
                    if kind == "stream":
                        if first is None:
                            try:
                                self.budget.check()
                                record = next(item for item in self.files.values() if item.stream is original)
                                self.verify_file(record)
                            except BaseException as exc:
                                remember(exc)
                        try:
                            _close_stream_original(original)
                        except BaseException as exc:
                            remember(exc)
                    else:
                        problem = first
                        if kind == "directory" and first is None:
                            try:
                                self.verify_directory(self.directory_records[id(original)])
                            except BaseException as exc:
                                # This exit still receives the original validation
                                # problem; later resources receive the selected first.
                                problem = exc
                                remember(exc)
                        try:
                            original.__exit__(None if problem is None else type(problem), problem,
                                None if problem is None else BaseException.__dict__["__traceback__"].__get__(problem))
                        except BaseException as exc:
                            remember(exc)
        finally:
            resources.clear()
            self.phase = "closed"
        return first


class RawAuthorityCapture:
    __slots__ = ("__weakref__",)

    def __new__(cls, token):
        if token is not _TOKEN:
            _fail("CAPTURE_REQUIRED")
        return super().__new__(cls)


def _origin(capture):
    state = _LIVE.get(id(capture))
    if type(capture) is not RawAuthorityCapture or state is None or state.token is not capture:
        _fail("CAPTURE_CLOSED" if type(capture) is RawAuthorityCapture and capture in _CLOSED else "CAPTURE_REQUIRED")
    if lifecycle._owner() != state.owner:
        _fail("OWNER_MISMATCH")
    return state


def _require_original_guard(state):
    if state.budget.operation_budget is not None:
        try:
            # Reject operation-callback reentry before lifecycle physical I/O.
            state.budget.operation_budget.check(callback=False)
        except BaseException as exc:
            raise _First(state.budget.remember(exc)) from None
    if state.budget.first is not None:
        raise _First(state.budget.first)
    try:
        if lifecycle.require_guard(state.profile, exclusive=True) is not state.guard:
            _fail("GUARD_CHANGED")
    except BaseException as exc:
        failure = _error(exc, "GUARD_CHANGED")
        raise _First(state.budget.remember(failure)) from None


def _acquisition(capture, *, idle=True):
    state = _origin(capture)
    if idle and state.phase != "idle":
        raise _First(state.budget.remember(AuthorityRawCaptureError("REENTRANT")))
    _require_original_guard(state)
    return state


def _public_failure(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except _First as exc:
            raise exc.original from None
    return wrapped


@_public_failure
def require_capture(capture, *, profile, operation_id):
    state = _origin(capture)
    if type(profile) is not lifecycle.StorageProfile or profile.identity != state.profile.identity:
        _fail("PROFILE_MISMATCH")
    if type(operation_id) is not str or operation_id != state.operation_id:
        _fail("OPERATION_MISMATCH")
    _acquisition(capture)


@_public_failure
def source_metadata(capture):
    state = _acquisition(capture)
    if state.budget.first is not None:
        raise _First(state.budget.first)
    return json.loads(state.metadata_raw)


@_public_failure
def verify_source(capture):
    state = _acquisition(capture)
    state.phase = "verifying"
    try:
        state.verify()
    except BaseException as exc:
        error = _translate(exc)
        state.budget.remember(error)
        raise error from None
    finally:
        state.phase = "idle"


def closed_observation(capture):
    if type(capture) is not RawAuthorityCapture:
        _fail("CLOSED_OBSERVATION_REQUIRED")
    record = _CLOSED.get(capture)
    if record is None:
        _fail("CLOSED_OBSERVATION_REQUIRED")
    if not _closed_owner_matches(record[0]):
        _fail("OWNER_MISMATCH")
    return json.loads(record[1])


def _translate(exc):
    if isinstance(exc, (_First, AuthorityRawCaptureError)):
        return _error(exc, "SOURCE_UNREADABLE")
    if isinstance(exc, (contract.AuthorityPreservationContractError, projection.AuthoritySourceProjectionError)):
        return AuthorityRawCaptureError("SOURCE_UNREPRESENTABLE")
    if isinstance(exc, (safe.SyncError, files.PreservationFileError)):
        return AuthorityRawCaptureError("SOURCE_UNSAFE")
    if isinstance(exc, locks.FilePrimitiveError):
        return AuthorityRawCaptureError("LOCK_UNPROVEN")
    if isinstance(exc, OSError):
        return AuthorityRawCaptureError("SOURCE_UNREADABLE")
    if isinstance(exc, UnicodeError):
        return AuthorityRawCaptureError("SOURCE_UNREPRESENTABLE")
    if isinstance(exc, RecursionError):
        return AuthorityRawCaptureError("SOURCE_UNREADABLE")
    return exc


@contextmanager
def _capture_raw_core_locked(profile, *, operation_id, limits=None, check_cancelled=None, operation=None):
    if type(profile) is not lifecycle.StorageProfile or check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    try:
        parsed = uuid.UUID(operation_id) if type(operation_id) is str else None
        if parsed is None or str(parsed) != operation_id or not parsed.int:
            raise ValueError
    except (ValueError, AttributeError):
        _fail("INVALID_ARGUMENT")
    limits = RawCaptureLimits() if limits is None else limits
    options = _validated_options(limits)
    guard = lifecycle.require_guard(profile, exclusive=True)
    token = RawAuthorityCapture(_TOKEN)
    external_budget = None if operation is None else operation.budget
    state = _Source(token, profile, operation_id, guard, _Budget(options, check_cancelled, external_budget))
    state.preservation_operation = operation
    _LIVE[id(token)] = state
    primary, caller_body = None, False
    try:
        state.open()
        state.verify()
        state.make_metadata()
        state.phase = "idle"
        caller_body = True
        yield token
        caller_body = False
        state.phase = "verifying"
        state.verify()
    except BaseException as exc:
        # An arbitrary caller exception remains the caller's original object.
        primary = exc if caller_body and not isinstance(exc, _First) else _translate(exc)
        state.budget.remember(primary)
    finally:
        _LIVE.pop(id(token), None)
        primary = state.close(primary)
    if primary is not None:
        raise primary from None
    if external_budget is not None:
        external_budget.assert_settled()
    metadata = json.loads(state.metadata_raw)
    closed = {"format_version": 1, "kind": "personal_authority_raw_census_closed",
        "operation_id": operation_id,
        "source_projection_sha256": _digest(metadata["source_projection"]),
        "source_metadata_sha256": hashlib.sha256(state.metadata_raw).hexdigest(),
        "read_bytes": state.budget.read_bytes, "read_calls": state.budget.read_calls,
        "seek_calls": state.budget.seek_calls, "source_file_count": len(state.files),
        "source_byte_size": state.source_bytes}
    _CLOSED[token] = _closed_owner_ref(state.owner), _canonical(closed)
    if operation is not None:
        _PRESERVATION_CLOSED[token] = (_closed_owner_ref(state.owner), ref(operation), ref(guard), state.profile.identity,
            state.preservation_selection_sha256, _canonical(closed))


@contextmanager
def capture_raw_locked(profile, *, operation_id, limits=None, check_cancelled=None):
    with _capture_raw_core_locked(profile, operation_id=operation_id, limits=limits,
                                 check_cancelled=check_cancelled) as capture:
        yield capture


@contextmanager
def _capture_for_preservation_locked(operation_token):
    """Fixed original-operation seam; it does not accept a caller ledger."""
    from . import personal_authority_preservation_routing as preservation
    operation = preservation.operation_record(operation_token)
    limits = operation.limits
    options = RawCaptureLimits(capsule_limits=dict(limits["capsule_limits"]),
        max_read_bytes=limits["source_max_read_bytes"],
        deadline_seconds=min(120.0, operation.budget.remaining_seconds),
        node_json_bytes=limits["node_json_bytes"])
    with _capture_raw_core_locked(operation.profile, operation_id=operation.operation_id,
                                 limits=options, operation=operation) as capture:
        yield capture


@_public_failure
def _bind_preservation_selection_original(capture, operation_token):
    from . import personal_authority_preservation_routing as preservation
    operation = preservation.operation_record(operation_token)
    state = _acquisition(capture)
    if state.preservation_operation is not operation or state.guard is not operation.guard:
        _fail("OPERATION_MISMATCH")
    if operation.graph.bytes("source-metadata.json") != state.metadata_raw:
        raise _First(state.budget.remember(AuthorityRawCaptureError("SOURCE_CHANGED")))
    selected = operation.graph.sha("selection.json")
    if state.preservation_selection_sha256 not in (None, selected):
        raise _First(state.budget.remember(AuthorityRawCaptureError("SOURCE_CHANGED")))
    operation.graph.selection()
    state.preservation_selection_sha256 = selected


@_public_failure
def _copy_entry_original(capture, entry_id, target_token):
    from . import personal_authority_preservation_routing as routing
    target = routing.target_module(target_token)
    state = _acquisition(capture)
    if type(entry_id) is not str or entry_id not in state.entry_files:
        _fail("INVALID_ARGUMENT")
    record = state.entry_files[entry_id]
    state.phase = "reading"
    try:
        target._require_source_writer_original(target_token, state.preservation_operation,
                                               state.preservation_selection_sha256, entry_id)
        lock = state.locks.get(record.key.split("/", 1)[0])
        if lock is not None:
            _MeteredLock.verify(lock)
        state.read_file(record, target_token=target_token)
        if lock is not None:
            _MeteredLock.verify(lock)
        _require_original_guard(state)
    except BaseException as exc:
        error = _translate(exc)
        state.budget.remember(error)
        raise error from None
    finally:
        state.phase = "idle"


def _consume_closed_for_preservation(capture, operation_token):
    from . import personal_authority_preservation_routing as preservation
    operation = preservation.operation_record(operation_token)
    if type(capture) is not RawAuthorityCapture:
        _fail("CLOSED_OBSERVATION_REQUIRED")
    record = _PRESERVATION_CLOSED.get(capture)
    if (record is None or not _closed_owner_matches(record[0]) or record[1]() is not operation
            or record[2]() is not operation.guard or record[3] != operation.profile.identity
            or record[4] != operation.graph.sha("selection.json")):
        _fail("CLOSED_OBSERVATION_REQUIRED")
    del _PRESERVATION_CLOSED[capture]
    return json.loads(record[5])
