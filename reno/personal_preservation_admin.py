"""Explicit personal preservation administration with bounded, private output.

Status reports recorded progress only. It neither repairs packages nor checks
their payloads. Creation and continuation exclude the real worker before taking
EX; verification is a separate read-only SH operation.
"""
from __future__ import annotations

from contextlib import ExitStack
from functools import wraps
import re
import stat
import time
import uuid

from . import personal_preservation as producer
from . import personal_preservation_contract as contract
from . import personal_preservation_files as files
from . import storage_lifecycle as lifecycle, sync_media as safe, tasks

MAX_STATUS_OPERATIONS = 256
MAX_CATALOG_ENTRIES = 4096
MAX_STATUS_FACT_BYTES = 64 * 1024
MAX_STATUS_BYTES = 4 * 1024 * 1024
MAX_STATUS_SECONDS = 10
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_SUFFIXES = (".request.json", ".state.json", ".selection.json",
             ".source-verified.json", ".receipt.json", ".copy-index", ".failures")


def _fail(code, *, retriable=False):
    raise producer.PersonalPreservationError("PERSONAL_PRESERVATION_" + code,
                                            retriable=retriable) from None


def _fixed_errors(function):
    @wraps(function)
    def wrapped(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except (producer.PersonalPreservationError, contract.PersonalPreservationError,
                files.PreservationFileError, lifecycle.StorageLifecycleError):
            raise
        except Exception:
            _fail("ADMIN_FAILED")
    return wrapped


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            _fail("OPERATION_INVALID")
    except (ValueError, TypeError, AttributeError):
        _fail("OPERATION_INVALID")
    return value


def _sha(value, *, required=False):
    if value is None:
        if required:
            _fail("REQUEST_SHA_REQUIRED")
    elif type(value) is not str or not _SHA.fullmatch(value):
        _fail("REQUEST_SHA_INVALID")
    return value


class _ReadBudget:
    def __init__(self):
        self.remaining = MAX_STATUS_BYTES
        self.deadline = time.monotonic() + MAX_STATUS_SECONDS

    def check(self):
        if time.monotonic() >= self.deadline:
            _fail("STATUS_LIMIT", retriable=True)

    def reserve(self, size):
        self.check()
        if size > MAX_STATUS_FACT_BYTES or size > self.remaining:
            _fail("STATUS_LIMIT")
        self.remaining -= size


def _directory(stack, parent, name):
    directory = stack.enter_context(safe._Directory(parent.path / name, parent=parent))
    files.require_private(directory.path, directory=True)
    return directory


def _fact(stack, directory, name, validator, budget, **kwargs):
    budget.reserve(files.identity(directory.entry_stat(name))["byte_size"])
    held = stack.enter_context(files.FileHold(directory.path / name,
        maximum=MAX_STATUS_FACT_BYTES, check_cancelled=budget.check))
    files.require_private(held.path, directory=False)
    value = validator(contract.decode(held.raw), **kwargs)
    if held.raw != contract.canonical_bytes(value):
        _fail("FACT_NONCANONICAL")
    return value, held.sha256


def _request(stack, data, catalog, profile, operation_id, budget):
    request, sha = _fact(stack, catalog, operation_id + ".request.json",
                         contract.validate_request, budget)
    expected = {name: lifecycle._key(getattr(profile, name)) for name in
                ("database", "media", "reports", "checkpoints")}
    if (request["operation_id"] != operation_id or request["source_profile"] != expected
            or request["source_root_identity"] != files.directory_identity(data)):
        _fail("OPERATION_CONFLICT")
    return request, sha


def _summary(operation_id, *, request_sha256=None, state=None):
    return {"kind": "personal_preservation_status", "present": request_sha256 is not None,
        "operation_id": operation_id, "request_sha256": request_sha256,
        "phase": state["phase"] if state else "reserved" if request_sha256 else "absent",
        "state_recorded": state is not None,
        "verification": "not_performed", "verified": False}


def _absent(operation_id):
    if operation_id is not None:
        return _summary(operation_id)
    return {"kind": "personal_preservation_status", "present": False,
        "operation_count": 0, "operations": [],
        "verification": "not_performed", "verified": False}


def _catalog_index(catalog, budget):
    """Recognize all top-level evidence; orphan/unknown entries never mean empty."""
    names = tuple(sorted(catalog.names()))
    if len(names) > MAX_CATALOG_ENTRIES:
        _fail("STATUS_LIMIT")
    operations, identities = {}, {}
    for name in names:
        budget.check()
        suffix = next((suffix for suffix in _SUFFIXES if name.endswith(suffix)), None)
        if suffix is None:
            if name.startswith(".preservation-write-") and name.endswith(".tmp"):
                _fail("CATALOG_INCOMPLETE")
            _fail("CATALOG_UNDECLARED")
        operation_id = _uuid(name[:-len(suffix)])
        operations.setdefault(operation_id, set()).add(suffix)
        info = catalog.entry_stat(name)
        if suffix in (".copy-index", ".failures"):
            safe._safe_stat(info, directory=True)
            identities[name] = (info.st_dev, info.st_ino, info.st_mode)
        else:
            identities[name] = files.identity(info)
        files.require_private(catalog.path / name, directory=stat.S_ISDIR(info.st_mode))
    if len(operations) > MAX_STATUS_OPERATIONS:
        _fail("STATUS_LIMIT")
    if any(".request.json" not in suffixes for suffixes in operations.values()):
        _fail("FACT_MISSING")
    return names, operations, identities


@_fixed_errors
def status(profile, operation_id=None):
    """Read recorded summaries; absent stores need no lifecycle initialization."""
    profile = lifecycle._profile(profile)
    if operation_id is not None:
        operation_id = _uuid(operation_id)
    budget = _ReadBudget()
    with ExitStack() as stack:
        try:
            data = stack.enter_context(safe._Directory(profile.database.parent,
                                                       base=profile.database.parent))
        except FileNotFoundError:
            return _absent(operation_id)
        try:
            data.entry_stat(contract.CATALOG)
        except FileNotFoundError:
            try:
                data.entry_stat(contract.DIRECTORY)
            except FileNotFoundError:
                pass
            else:
                _fail("CATALOG_MISSING")
            data.verify()
            for namespace in (contract.CATALOG, contract.DIRECTORY):
                try:
                    data.entry_stat(namespace)
                except FileNotFoundError:
                    continue
                _fail("CATALOG_CHANGED", retriable=True)
            return _absent(operation_id)
        # No process_lock, initialization, SQL or payload reads belong to status.
        guard = stack.enter_context(lifecycle.shared(profile, timeout=0))
        catalog = _directory(stack, data, contract.CATALOG)
        names, operations, identities = _catalog_index(catalog, budget)
        def finish(exc_type, _exc, _tb):
            if exc_type is None:
                budget.check()
                catalog.verify()
                if tuple(sorted(catalog.names())) != names:
                    _fail("CATALOG_CHANGED", retriable=True)
                for name, expected in identities.items():
                    info = catalog.entry_stat(name)
                    actual = ((info.st_dev, info.st_ino, info.st_mode) if stat.S_ISDIR(info.st_mode)
                              else files.identity(info))
                    if actual != expected:
                        _fail("CATALOG_CHANGED", retriable=True)
                    files.require_private(catalog.path / name, directory=stat.S_ISDIR(info.st_mode))
                guard.verify()
            return False
        # Exit-time namespace checks follow all fact readers, while the exact
        # original catalog and SH are still alive. No intermediate result leaks.
        stack.push(finish)
        selected = sorted(operations) if operation_id is None else (
            [operation_id] if operation_id in operations else [])
        summaries = []
        for selected_id in selected:
            request, sha = _request(stack, data, catalog, profile, selected_id, budget)
            state = None
            if ".state.json" in operations[selected_id]:
                state, _state_sha = _fact(stack, catalog, selected_id + ".state.json",
                    contract.validate_state, budget, request=request)
            summaries.append(_summary(selected_id, request_sha256=sha, state=state))
        if operation_id is not None:
            result = summaries[0] if summaries else _absent(operation_id)
        else:
            result = {"kind": "personal_preservation_status", "present": True,
                "operation_count": len(summaries), "operations": summaries,
                "verification": "not_performed", "verified": False}
    return result


def _completed(profile, operation_id, result):
    if (type(result) is not dict or set(result) != {"operation_id", "phase", "file_count", "byte_size"}
            or result["operation_id"] != operation_id or result["phase"] != "ready"
            or type(result["file_count"]) is not int or not 0 <= result["file_count"] <= contract.MAX_FILES
            or type(result["byte_size"]) is not int or not 0 <= result["byte_size"] <= contract.MAX_TOTAL_BYTES):
        _fail("RESULT_INVALID")
    with ExitStack() as stack:
        data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        catalog = _directory(stack, data, contract.CATALOG)
        _original_request, sha = _request(stack, data, catalog, profile, operation_id, _ReadBudget())
    return {**result, "request_sha256": sha, "verified": True}


@_fixed_errors
def verify(profile, operation_id, expected_request_sha256=None):
    """Verify the historical original package without changing any package fact."""
    profile, operation_id = lifecycle._profile(profile), _uuid(operation_id)
    expected_request_sha256 = _sha(expected_request_sha256)
    with lifecycle.shared(profile, timeout=0):
        result = producer.verify_ready_locked(profile, operation_id=operation_id,
            expected_request_sha256=expected_request_sha256)
        result = _completed(profile, operation_id, result)
    return result


def _write(profile, operation_id, *, limits=None, expected_request_sha256=None, resuming=False):
    profile, operation_id = lifecycle._profile(profile), _uuid(operation_id)
    if lifecycle.current_guard() is not None:
        _fail("LOCK_CONTEXT_ACTIVE")
    if resuming:
        expected_request_sha256 = _sha(expected_request_sha256, required=True)
    else:
        limits = contract.validate_limits(dict(contract.DEFAULT_LIMITS) if limits is None else limits)
    # Admission must finish before process_lock can create its worker marker.
    with lifecycle.shared(profile, timeout=0) as admitted:
        admitted.verify()
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY", retriable=True)
        if not tasks.holds_process_lock(profile=profile):
            _fail("WORKER_LOCK_UNPROVEN")
        with lifecycle.exclusive(profile, timeout=0) as guard:
            if resuming:
                result = producer.resume_locked(profile, operation_id=operation_id,
                    expected_request_sha256=expected_request_sha256)
            else:
                result = producer.create_locked(profile, operation_id=operation_id, limits=limits)
            result = _completed(profile, operation_id, result)
            guard.verify()
            if not tasks.holds_process_lock(profile=profile):
                _fail("WORKER_LOCK_UNPROVEN")
    return result


@_fixed_errors
def create(profile, operation_id, limits=None):
    """Explicitly reserve and preserve this complete scope under the original UUID."""
    return _write(profile, operation_id, limits=limits)


@_fixed_errors
def resume(profile, operation_id, expected_request_sha256):
    """Continue only the reserved request, fixed selection and first-copy anchors."""
    return _write(profile, operation_id, expected_request_sha256=expected_request_sha256, resuming=True)
