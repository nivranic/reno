"""Fixed census3 source factory; original resources, separate capabilities.

This produces raw observations only. It does not publish a persistent package,
copy source bytes, initialize authority, or enable a writer.
"""
from contextlib import contextmanager
import hashlib
import io
import json
import os
import stat
import uuid
from weakref import WeakKeyDictionary
from . import personal_authority_preservation_source as prior
from . import personal_authority_preservation3_contract as contract
from . import personal_authority_source_projection2 as projection
from . import storage_lifecycle as lifecycle, sync_media as safe

AuthorityRawCaptureError = prior.AuthorityRawCaptureError
RawCaptureLimits = prior.RawCaptureLimits
_First, _Budget, _Pass, _MeteredLock = prior._First, prior._Budget, prior._Pass, prior._MeteredLock
_fail, _public_failure, _validated_options = prior._fail, prior._public_failure, prior._validated_options
_name_key, _NODE, _PREFIX = prior._name_key, prior._NODE, prior._PREFIX
_canonical, _digest, _require_original_guard = prior._canonical, prior._digest, prior._require_original_guard
_HANDOFF_ROOT = prior.physical.HANDOFF_DIRECTORY
_closed_owner_ref, _closed_owner_matches = prior._closed_owner_ref, prior._closed_owner_matches
_TOKEN = object()
_LIVE = {}
_CLOSED = WeakKeyDictionary()


class _Source3(prior._Source):
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
            if not contract._selected_top(comparison):
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
                    modern = not prefix and name == contract._ROOT3
                    if modern:
                        # Identity and complete listing belong to this held directory.
                        child.names = self.listing(child, census)
                    if (not prefix and _name_key(name).startswith(_PREFIX)
                            and (not modern or any(_name_key(item) == "authority.lock" for item in child.names))):
                        lock = (_MeteredLock(child.original, self.budget, lock_name="handoff.lock")
                                if name == _HANDOFF_ROOT else _MeteredLock(child.original, self.budget))
                        lock.__enter__()
                        self.own("lock", lock)
                        self.locks[key] = lock
                    if not modern:
                        child.names = self.listing(child, census)
                    pending.append((key, child))
                else:
                    self.open_file(parent, name, key, info, census)

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
        value = {"format_version": 2, "kind": "personal_authority_raw_source_observation",
            "operation_id": self.operation_id, "capsule": capsule, "node_observation": node_observation,
            "source_projection": projection.from_capsule(capsule, limits=dict(self.budget.structural))}
        self.metadata_raw = _canonical(value)


class RawAuthorityCapture3:
    __slots__ = ("__weakref__",)

    def __new__(cls, token):
        if token is not _TOKEN:
            _fail("CAPTURE_REQUIRED")
        return super().__new__(cls)


def _origin(capture):
    state = _LIVE.get(id(capture))
    if type(capture) is not RawAuthorityCapture3 or state is None or state.token is not capture:
        _fail("CAPTURE_CLOSED" if type(capture) is RawAuthorityCapture3 and capture in _CLOSED else "CAPTURE_REQUIRED")
    if lifecycle._owner() != state.owner:
        _fail("OWNER_MISMATCH")
    return state


def _acquisition(capture, *, idle=True):
    state = _origin(capture)
    if idle and state.phase != "idle":
        raise _First(state.budget.remember(AuthorityRawCaptureError("REENTRANT")))
    _require_original_guard(state)
    return state


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
    if type(capture) is not RawAuthorityCapture3:
        _fail("CLOSED_OBSERVATION_REQUIRED")
    record = _CLOSED.get(capture)
    if record is None:
        _fail("CLOSED_OBSERVATION_REQUIRED")
    if not _closed_owner_matches(record[0]):
        _fail("OWNER_MISMATCH")
    return json.loads(record[1])


def _translate(exc):
    if isinstance(exc, (contract.AuthorityPreservationContractError, projection.AuthoritySourceProjectionError)):
        return AuthorityRawCaptureError("SOURCE_UNREPRESENTABLE")
    return prior._translate(exc)


@contextmanager
def _capture_raw_core_locked(profile, *, operation_id, limits=None, check_cancelled=None):
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
    token = RawAuthorityCapture3(_TOKEN)
    state = _Source3(token, profile, operation_id, guard, _Budget(options, check_cancelled))
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
    metadata = json.loads(state.metadata_raw)
    closed = {"format_version": 2, "kind": "personal_authority_raw_census_closed",
        "operation_id": operation_id,
        "source_projection_sha256": _digest(metadata["source_projection"]),
        "source_metadata_sha256": hashlib.sha256(state.metadata_raw).hexdigest(),
        "read_bytes": state.budget.read_bytes, "read_calls": state.budget.read_calls,
        "seek_calls": state.budget.seek_calls, "source_file_count": len(state.files),
        "source_byte_size": state.source_bytes}
    _CLOSED[token] = _closed_owner_ref(state.owner), _canonical(closed)


@contextmanager
def capture_raw3_locked(profile, *, operation_id, limits=None, check_cancelled=None):
    with _capture_raw_core_locked(profile, operation_id=operation_id, limits=limits,
                                 check_cancelled=check_cancelled) as capture:
        yield capture
