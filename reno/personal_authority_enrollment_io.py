"""New enrollment content publication candidate using owned Windows primitives.

The six closed names live in the existing namespace3 root. These publications
are durable content facts, never live authority, SQL rights, source closure,
or the caller's eventual EX exit. No previous native admission is inherited.
Failed stages and partial finals remain unknown; nothing is deleted/adopted.
"""
from __future__ import annotations

from contextlib import contextmanager
import ctypes
import hashlib
from types import MappingProxyType

from . import personal_authority_contract as authority
from . import personal_authority_enrollment_contract as contract
from . import personal_authority_namespace3_contract as namespace
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_namespace3_io as native
from . import personal_authority_enrollment_custody as custody

MAX_RECORD_BYTES = 16 * 1024
CHUNK_BYTES = native.CHUNK_BYTES
NAMES = MappingProxyType({
    "enrollment_binding": ".reno-personal-authority-enrollment-binding.json",
    "origin_init_intent": ".reno-personal-authority-origin-init-intent.json",
    "local_contract": ".reno-personal-authority-local-contract.json",
    "init_seal": ".reno-personal-authority-init-seal.json",
    "genesis_grant": ".reno-personal-authority-genesis-grant.json",
    "authority_prefix": ".reno-personal-authority-enrollment-prefix.json",
})
_NAMES = frozenset(NAMES.values())
_CONTENT_NAMES = frozenset({NAMES["enrollment_binding"], NAMES["authority_prefix"]})
_AUTHORITY_LIMITS = authority.Limits(max_record_bytes=MAX_RECORD_BYTES)


class EnrollmentIOError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_IO_" + suffix
        super().__init__(self.code)


Error = EnrollmentIOError


def _fail(suffix):
    raise EnrollmentIOError(suffix) from None


def _budget(value):
    if type(value) is deadline._RestrictedBudget:
        return value
    # Fixed lazy import avoids the runtime -> I/O import cycle. A caller cannot
    # supply a provider, arbitrary callback, or arbitrary budget subclass.
    from . import personal_authority_enrollment as enrollment
    if type(value) is not enrollment._Budget:
        _fail("INVALID_ARGUMENT")
    return value


def stage_name(name: str, operation: str) -> str:
    """Return one closed name for the caller's original canonical init UUID."""
    if type(name) is not str or name not in _NAMES:
        _fail("INVALID_ARGUMENT")
    try:
        canonical = authority._uuid(operation)
    except authority.ContractError:
        _fail("INVALID_ARGUMENT")
    return name + "." + canonical + ".stage"


def _retire(owner, budget, *, _custody_journal=None):
    """Clock failures cannot prevent one close of a confirmed owned HANDLE."""
    if _custody_journal is None:
        first = None
        try:
            budget.clock_only()
        except BaseException as error:
            first = error
        try:
            owner.close()
        except BaseException as error:
            if first is None:
                first = error
            elif error is not first:
                native._note(first)
        try:
            budget.clock_only()
        except BaseException as error:
            if first is None:
                first = error
            elif error is not first:
                native._note(first)
        if first is not None:
            raise first
        return
    first = None
    try:
        budget.clock_only()
    except BaseException as error:
        first = error
    if first is not None:
        try:
            custody._remember_owner_primary(owner, _custody_journal, first)
        except BaseException as propagation:
            if propagation is not first:
                native._note(first)
    try:
        owner.close()
    except BaseException as error:
        if first is None:
            first = error
        elif error is not first:
            native._note(first)
    try:
        budget.clock_only()
    except BaseException as error:
        if first is None:
            first = error
        elif error is not first:
            native._note(first)
    if first is not None:
        raise first


class _OwnedScope:
    """Allocate the scope before acquisition; protect its enter failure too."""
    def __init__(self, budget, *, root=None, parent=None, name=None,
                 create=False, descriptor=None, creator_live=False):
        self.budget, self.root, self.parent, self.name = budget, root, parent, name
        self.create, self.descriptor, self.creator_live = create, descriptor, creator_live
        self.owner = None
        self._custody_journal = custody._active_journal_for_budget(budget)

    def __enter__(self):
        owner = None
        try:
            if custody._active_journal_for_budget(self.budget) is not self._custody_journal:
                custody._fail("INVALID_ARGUMENT")
            self.budget.clock_only()
            if self.root is not None:
                owner = native._open_parent_raw(self.root, write=True, budget=self.budget)
            else:
                owner = native._relative_open(self.parent, self.name, self.budget,
                    directory=False, create=self.create, descriptor=self.descriptor,
                    reader_share=7 if self.creator_live else 1)
            self.owner = owner
            if type(owner) is not native._Owner:
                _fail("OWNER_UNCONFIRMED")
            slot = custody._find_owner_slot(owner)
            if self._custody_journal is None:
                if slot is not None:
                    custody._fail("INVALID_ARGUMENT")
            elif slot is None or slot._journal is not self._custody_journal:
                custody._fail("INVALID_ARGUMENT")
            else:
                custody._owner_slot(owner, slot)
            self.budget.clock_only()
            owner.__enter__()
            self.budget.clock_only()
            return owner
        except BaseException as primary:
            if type(owner) is native._Owner and not owner.retired:
                if self._custody_journal is not None:
                    try:
                        custody._remember_owner_primary(owner, self._custody_journal, primary)
                    except BaseException as propagation:
                        if propagation is not primary:
                            native._note(primary)
                try:
                    _retire(owner, self.budget, _custody_journal=self._custody_journal)
                except BaseException as cleanup:
                    if cleanup is not primary:
                        native._note(primary)
            raise

    def __exit__(self, _kind, _primary, _tb):
        if self._custody_journal is None:
            _retire(self.owner, self.budget)
            return None
        if _primary is not None:
            try:
                custody._remember_owner_primary(self.owner, self._custody_journal, _primary)
            except BaseException as propagation:
                if propagation is not _primary:
                    native._note(_primary)
            try:
                _retire(self.owner, self.budget, _custody_journal=self._custody_journal)
            except BaseException as cleanup:
                if cleanup is not _primary:
                    native._note(_primary)
            return None
        _retire(self.owner, self.budget, _custody_journal=self._custody_journal)
        return None


def _check(profile, directory, budget, *, parent=False):
    _budget(budget)
    budget.clock_only()
    if type(directory) is not native._Directory or directory.budget is not budget:
        _fail("INVALID_ARGUMENT")
    native._check(profile, directory, budget)
    budget.clock_only()
    expected = profile.database.parent if parent else profile.database.parent / namespace.ROOT_NAME
    if directory.path != expected:
        _fail("PROFILE_CHANGED")
    if parent:
        if directory.parent is not None or directory.name is not None:
            _fail("PROFILE_CHANGED")
    elif (type(directory.parent) is not native._Directory
          or directory.parent.budget is not budget
          or directory.parent.path != profile.database.parent
          or directory.name != namespace.ROOT_NAME):
        _fail("PROFILE_CHANGED")
    budget.clock_only()


def _verify(directory, budget):
    budget.clock_only()
    directory.verify()
    budget.clock_only()


def _security(handle, budget):
    budget.clock_only()
    native._security_check(handle, budget)
    budget.clock_only()


def _info(handle, budget):
    budget.clock_only()
    result = native._info(handle, directory=False, budget=budget)
    budget.clock_only()
    return result


@contextmanager
def open_root_for_publication_locked(profile, parent, budget):
    """Borrow EX to reopen one pre-existing pinned root with ADD_FILE access."""
    _check(profile, parent, budget, parent=True)
    budget.clock_only()
    paths = tuple(profile.identity)
    guard = native.lifecycle.require_guard(profile, exclusive=True)
    path = profile.database.parent / namespace.ROOT_NAME
    budget.clock_only()
    with native._preserve_primary(_OwnedScope(budget, root=path)) as owner:
        budget.clock_only()
        root = native._Directory(path, owner, budget, parent=parent, name=namespace.ROOT_NAME)
        budget.clock_only()
        _check(profile, root, budget)
        _security(root.handle, budget)
        yield root
        _check(profile, root, budget)
        _security(root.handle, budget)
        if tuple(profile.identity) != paths or native.lifecycle.require_guard(profile, exclusive=True) is not guard:
            _fail("PROFILE_CHANGED")
        budget.clock_only()
    _check(profile, parent, budget, parent=True)
    budget.clock_only()


def _record(name, raw, budget):
    budget.clock_only()
    if (type(name) is not str or name not in _NAMES or type(raw) is not bytes
            or not 0 < len(raw) <= MAX_RECORD_BYTES):
        _fail("INVALID_ARGUMENT")
    if name in _CONTENT_NAMES:
        decoded = contract.decode(raw)
        operation = decoded["init_operation_id"]
    else:
        decoded = authority.decode(raw, limits=_AUTHORITY_LIMITS)
        operation = decoded["operation_id"]
        if decoded["kind"] == "local_contract" and decoded["payload"]["init_kind"] != "origin":
            _fail("KIND_INVALID")
    budget.clock_only()
    kind = decoded["kind"]
    if type(kind) is not str or kind not in NAMES or name != NAMES[kind]:
        _fail("KIND_INVALID")
    stage = stage_name(name, operation)
    budget.clock_only()
    return stage


def _native_failure(number, operation, budget):
    primary = native._native_error(number, operation)
    try:
        budget.clock_only()
    except BaseException as secondary:
        if secondary is not primary:
            native._note(primary)
    raise primary


def _write_chunk(handle, raw, budget):
    budget.clock_only()
    requested = min(len(raw), CHUNK_BYTES)
    amount = budget.request(requested)
    if type(amount) is not int or not 0 < amount <= requested:
        _fail("WRITE_INVALID")
    budget.clock_only()
    buffer = ctypes.create_string_buffer(raw[:amount], amount)
    count = native.w.DWORD()
    budget.clock_only()
    if not native._kernel.WriteFile(handle, buffer, amount, ctypes.byref(count), None):
        _native_failure(ctypes.get_last_error(), "write", budget)
    actual = count.value
    if not 0 < actual <= amount:
        _fail("WRITE_INVALID")
    # charge records the successful actual bytes before its post-call clock.
    budget.charge(actual)
    budget.clock_only()
    return actual


def _read_chunk(handle, requested, budget):
    budget.clock_only()
    requested = min(requested, CHUNK_BYTES)
    amount = budget.request(requested)
    if type(amount) is not int or not 0 < amount <= requested:
        _fail("READ_INVALID")
    budget.clock_only()
    buffer, count = ctypes.create_string_buffer(amount), native.w.DWORD()
    budget.clock_only()
    if not native._kernel.ReadFile(handle, buffer, amount, ctypes.byref(count), None):
        _native_failure(ctypes.get_last_error(), "read", budget)
    actual = count.value
    if not 0 <= actual <= amount:
        _fail("READ_INVALID")
    budget.charge(actual)
    budget.clock_only()
    result = buffer.raw[:actual]
    budget.clock_only()
    return result


def _read_exact(handle, byte_size, budget):
    budget.clock_only()
    if type(byte_size) is not int or not 0 < byte_size <= MAX_RECORD_BYTES:
        _fail("LIMIT")
    native._rewind(handle, budget)
    budget.clock_only()
    parts, total = [], 0
    budget.clock_only()
    while total < byte_size:
        budget.clock_only()
        chunk = _read_chunk(handle, byte_size - total, budget)
        if not chunk:
            _fail("READ_INCOMPLETE")
        parts.append(chunk)
        total += len(chunk)
        budget.clock_only()
    if _read_chunk(handle, 1, budget) != b"":
        _fail("READBACK_CHANGED")
    budget.clock_only()
    result = b"".join(parts)
    budget.clock_only()
    return result


def _path_identity(directory, name, budget):
    budget.clock_only()
    path = directory.path / name
    budget.clock_only()
    info = path.stat(follow_symlinks=False)
    budget.clock_only()
    result = native.facts.identity(info)
    budget.clock_only()
    return result


def _readback(profile, directory, name, expected_raw, expected_native, budget, *, creator_live):
    _check(profile, directory, budget)
    with native._preserve_primary(_OwnedScope(budget, parent=directory.handle,
            name=name, creator_live=creator_live)) as owner:
        _security(owner.value, budget)
        before = _info(owner.value, budget)
        path_identity = _path_identity(directory, name, budget)
        if (before[0] != expected_native or before[1] != len(expected_raw)
                or path_identity["byte_size"] != before[1]):
            _fail("READBACK_CHANGED")
        raw = _read_exact(owner.value, before[1], budget)
        if raw != expected_raw:
            _fail("READBACK_CHANGED")
        budget.clock_only()
        sha = hashlib.sha256(raw).hexdigest()
        budget.clock_only()
        if (_info(owner.value, budget) != before
                or _path_identity(directory, name, budget) != path_identity):
            _fail("READBACK_CHANGED")
        _verify(directory, budget)
        budget.clock_only()
        result = native.PublishedFile(sha, len(raw), dict(before[0]), dict(path_identity))
        budget.clock_only()
    _check(profile, directory, budget)
    budget.clock_only()
    return result


def publish_new_locked(profile, directory, name, raw, budget):
    """Create and publish one new closed record; never overwrite or adopt it."""
    _check(profile, directory, budget)
    stage = _record(name, raw, budget)
    budget.clock_only()
    paths = tuple(profile.identity)
    guard = native.lifecycle.require_guard(profile, exclusive=True)
    budget.clock_only()
    with native._preserve_primary(native._private_descriptor(budget)) as descriptor:
        budget.clock_only()
        with native._preserve_primary(_OwnedScope(budget, parent=directory.handle,
                name=stage, create=True, descriptor=descriptor)) as owner:
            original = _info(owner.value, budget)
            if original[1] != 0:
                _fail("STAGE_CREATE_FAILED")
            _security(owner.value, budget)
            budget.clock_only()
            offset = 0
            while offset < len(raw):
                budget.clock_only()
                piece = raw[offset:offset + CHUNK_BYTES]
                budget.clock_only()
                offset += _write_chunk(owner.value, piece, budget)
                budget.clock_only()
            budget.clock_only()
            if not native._kernel.FlushFileBuffers(owner.value):
                _native_failure(ctypes.get_last_error(), "flush", budget)
            budget.clock_only()
            if _read_exact(owner.value, len(raw), budget) != raw:
                _fail("READBACK_CHANGED")
            written = _info(owner.value, budget)
            if written[0] != original[0] or written[1] != len(raw):
                _fail("READBACK_CHANGED")
            _verify(directory, budget)
            budget.clock_only()
            native._publish_owned(directory, owner, name, budget)
            budget.clock_only()
            if _read_exact(owner.value, len(raw), budget) != raw:
                _fail("READBACK_CHANGED")
            _readback(profile, directory, name, raw, original[0], budget, creator_live=True)
            published_native = _info(owner.value, budget)[0]
            if published_native != original[0]:
                _fail("READBACK_CHANGED")
            _security(owner.value, budget)
        # The creator's ordinary close has now succeeded. The descriptor's
        # ordinary exit is required too before the post-close observation.
        budget.clock_only()
    budget.clock_only()
    result = _readback(profile, directory, name, raw, published_native, budget, creator_live=False)
    _check(profile, directory, budget)
    if tuple(profile.identity) != paths or native.lifecycle.require_guard(profile, exclusive=True) is not guard:
        _fail("PROFILE_CHANGED")
    budget.clock_only()
    return result
