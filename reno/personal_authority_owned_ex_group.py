"""Actual opt-in EX owners and registered borrowed-capture lineage.

Native group closure is distinct from source_closed, producer completion,
SQL registration, Store/writer authority, or a serialized caller receipt.
"""
from __future__ import annotations

from contextlib import ExitStack
from contextvars import Token
from dataclasses import dataclass
from types import TracebackType
import os
import weakref

from . import personal_authority_owned_ex_deadline as owned
from . import storage_lifecycle as lifecycle
from . import sync_media as safe

_GROUP_SEAL = object()
_GROUPS = {}
_SOURCE_ROLES = ("source_directory", "image", "held_main", "held_wal", "held_journal", "node")


@dataclass(frozen=True, slots=True)
class CaptureLineageReceiptV1:
    format_version: int
    kind: str
    scope: str
    phase: str
    capture_ordinal: int
    invocation_registered: bool
    context_completed: bool
    failed: bool
    normal_exit: bool
    outer_ex_borrowed: bool
    owned_outer_ex_closed: bool
    lower_fact_reads_attempted: tuple[bool, ...]
    source_close_facts: tuple
    source_closed: bool


@dataclass(frozen=True, slots=True)
class ExclusiveGroupCloseReceiptV1:
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    outer_ex_borrowed: bool
    context_reset_state: str
    guard_retired: bool
    declared_root_count: int
    acquired_root_count: int
    declared_roots_complete: bool
    lock_receipts: tuple
    lock_stream_receipts: tuple
    directory_receipts: tuple
    generated_resources_terminal_complete: bool
    effective_gate_passed: bool
    declared_capture_count: int
    capture_receipts: tuple
    capture_lineage_complete: bool
    owned_outer_ex_closed: bool
    source_closed: bool
    write_authority: bool


def _fail(suffix):
    owned._fail(suffix)


def _capture_first(owner, failure, traceback=None):
    if owner._first is None:
        owner._first = failure
        owner._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                           if traceback is None else traceback)
    elif failure is not owner._first:
        try:
            BaseException.add_note(owner._first, "OWNED_EX_GROUP_ADDITIONAL_FAILURE")
        except BaseException:
            pass


def _raise_first(owner):
    if owner._first is not None:
        raise BaseException.with_traceback(owner._first, owner._first_tb) from None


def _clock(token):
    owned._clock(token)


def _cleanup_clock(owner):
    try:
        _clock(owner._token)
    except BaseException as failure:
        _capture_first(owner, failure)


def _set_current(guard):
    return lifecycle._CURRENT.set(guard)


def _reset_current(token):
    return lifecycle._CURRENT.reset(token)


def _same_owner(left, right):
    return (type(left) is tuple and type(right) is tuple and len(left) == len(right) == 3
            and left[0] == right[0] and left[1] == right[1] and left[2] is right[2])


class _RootOwners:
    __slots__ = ("ordinal", "path", "directory", "directory_journal", "stream", "lock",
        "lock_journal", "directory_started", "stream_started", "lock_started",
        "directory_entered", "stream_entered", "lock_acquired", "fact_reads_attempted",
        "directory_receipt", "stream_receipt", "lock_receipt", "retirements")

    def __init__(self, ordinal, path):
        self.ordinal, self.path = ordinal, path
        self.directory = self.directory_journal = self.stream = self.lock = self.lock_journal = None
        self.directory_started = self.stream_started = self.lock_started = False
        self.directory_entered = self.stream_entered = self.lock_acquired = False
        self.fact_reads_attempted = [False, False, False]
        self.directory_receipt = self.stream_receipt = self.lock_receipt = None
        self.retirements = [None, None, None]


class _RootFactState:
    __slots__ = ("attempted", "receipt")

    def __init__(self):
        self.attempted, self.receipt = False, None


@dataclass(frozen=True, slots=True)
class _RegisteredOwner:
    role: str
    actual_owner: object
    actual_journal: object
    root_ordinal: int
    actual_root: object
    fact_state: object
    actual_callback: object


class _RootRetirement:
    """Fixed callback enrolled before construction/enter, never a caller hook."""
    __slots__ = ("_group", "_root", "_registration", "_attempted")

    def __init__(self, group, root, role, actual_owner, actual_journal=None):
        self._group, self._root, self._attempted = group, root, False
        if group._phase != "opening":
            _fail("CLOSED")
        group = _registered(group)
        self._registration = _RegisteredOwner(role, actual_owner, actual_journal,
                                              root.ordinal, root, _RootFactState(), self)
        # Pin each actual callback/record before its constructor/resource call.
        group._owner_records = group._pins[10] + (self._registration,)
        group._pins = group._pins[:10] + (group._owner_records,)

    def __exit__(self, _kind, _primary, _traceback):
        if self._attempted:
            return False
        self._attempted = True
        group, root = self._group, self._root
        registration = self._registration
        role, actual, journal = registration.role, registration.actual_owner, registration.actual_journal
        # Tamper cannot redirect a registered cleanup obligation to a foreign
        # owner, nor prevent this original owner and other roots from unwinding.
        try:
            index = ("directory", "stream", "lock").index(role)
            if (root.ordinal != registration.root_ordinal or root.retirements[index] is not self
                    or getattr(root, role) is not actual
                    or (role != "stream" and getattr(root, role + "_journal") is not journal)):
                _fail("INVALID_RESULT")
        except BaseException as failure:
            _capture_first(group, failure)
        primary, tb = group._first, group._first_tb
        try:
            if role == "lock":
                if journal is not None and journal._owner is actual:
                    if primary is not None:
                        lifecycle._remember_owned_lock_primary(actual, primary, tb)
                    if lifecycle._FileLock.release(actual) is not None:
                        _fail("INVALID_RESULT")
            elif role == "stream":
                stream = actual
                if stream is not None and hasattr(stream, "_phase"):
                    result = owned.LockStreamDeadlineLease.__exit__(stream,
                        None if primary is None else type(primary), primary, tb)
                    if result is not None:
                        _fail("INVALID_RESULT")
            elif role == "directory":
                if journal is not None and journal._owner is actual:
                    result = safe._Directory.__exit__(actual,
                        None if primary is None else type(primary), primary, tb)
                    if result is not None:
                        _fail("INVALID_RESULT")
            else:
                _fail("INVALID_ARGUMENT")
        except BaseException as failure:
            _capture_first(group, failure)
        # Keep unwinding every independent owner even after a control failure.
        return False


def _registered(lease):
    if (type(lease) is not OwnedExclusiveDeadlineLease
            or lease._seal is not _GROUP_SEAL
            or type(dict.get(_GROUPS, id(lease))) is not weakref.ReferenceType
            or dict.get(_GROUPS, id(lease))() is not lease):
        _fail("INVALID_ARGUMENT")
    binding = owned._binding(lease._binding)
    pins = lease._pins
    if (type(pins) is not tuple or len(pins) != 11
            or binding is not pins[0] or lease._profile is not pins[1]
            or lease._roots is not pins[2] or lease._captures is not pins[3]
            or binding._operation is not pins[4]
            or binding._scope_identity is not pins[5]
            or lease._owner is not pins[6]
            or lease._token is not pins[7] or lease._parent_token is not pins[8]
            or lease._claim is not pins[9]
            or lease._owner_records is not pins[10] or type(pins[10]) is not tuple
            or binding._effective_token is not lease._token
            or binding._parent_token is not lease._parent_token
            or binding._group_claim is not lease._claim
            or not _same_owner(lease._owner, lifecycle._owner())):
        _fail("INVALID_ARGUMENT")
    return lease


class OwnedExclusiveDeadlineLease:
    """Registered actual operation scope; exact legacy Guard and native owners."""
    __slots__ = ("_seal", "_binding", "_token", "_parent_token", "_profile", "_profile_identity", "_owner", "_roots",
        "_captures", "_declared_capture_count", "_callback", "_pins", "_phase", "_context",
        "_guard", "_current_token", "_context_reset_state", "_guard_retired", "_first",
        "_first_tb", "_enter_returned", "_exit_attempted", "_effective_gate_passed",
        "_consume_attempted", "_receipt", "_normal_exit", "_claim", "_owner_records", "_outer_exit_state", "__weakref__")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, profile, *, binding, declared_capture_count=0, check_cancelled=None):
        if type(self) is not OwnedExclusiveDeadlineLease:
            _fail("INVALID_ARGUMENT")
        try:
            object.__getattribute__(self, "_phase")
        except AttributeError:
            pass
        else:
            _fail("CLOSED")
        if os.name != "nt" or type(profile) is not lifecycle.StorageProfile:
            _fail("INVALID_ARGUMENT")
        binding = owned._binding(binding)
        if (type(declared_capture_count) is not int or not 0 <= declared_capture_count <= 2
                or (check_cancelled is not None and not callable(check_cancelled))):
            _fail("INVALID_ARGUMENT")
        if any(value is not None for value in binding._owners):
            _fail("CLOSED")
        if binding._group_claim is not None:
            _fail("CLOSED")
        paths = lifecycle._roots((profile,))
        if not 0 < len(paths) <= 4:
            _fail("INVALID_ARGUMENT")
        self._seal, self._binding, self._profile = _GROUP_SEAL, binding, profile
        self._token, self._parent_token = binding._effective_token, binding._parent_token
        self._profile_identity, self._owner = tuple(profile.identity), lifecycle._owner()
        self._roots = tuple(_RootOwners(i, path) for i, path in enumerate(paths))
        self._captures, self._declared_capture_count = [None] * declared_capture_count, declared_capture_count
        self._callback, self._phase, self._context = check_cancelled, "new", None
        self._guard = self._current_token = self._first = self._first_tb = self._receipt = None
        self._context_reset_state, self._guard_retired = "not_started", False
        self._enter_returned = self._exit_attempted = self._effective_gate_passed = False
        self._consume_attempted = self._normal_exit = False
        self._owner_records = ()
        self._outer_exit_state = "not_started"
        # One claim per actual binding; no process-wide historical scan/tombstone.
        self._claim = object()
        self._pins = (binding, profile, self._roots, self._captures,
                      binding._operation, binding._scope_identity, self._owner,
                      self._token, self._parent_token, self._claim, self._owner_records)
        object.__setattr__(binding, "_group_claim", self._claim)
        key = id(self)
        def collected(reference, *, key=key):
            if dict.get(_GROUPS, key) is reference:
                dict.pop(_GROUPS, key, None)
        # Alive identity only; actual context/callbacks hold owed owners strongly.
        _GROUPS[key] = weakref.ref(self, collected)
        try:
            _clock(binding._effective_token)
            self._context = lifecycle._guard_many((profile,), True, 0, check_cancelled, _owned_ex=self)
        except BaseException as failure:
            _capture_first(self, failure)
            self._phase = "retired"
            _raise_first(self)

    @property
    def deadline_token(self):
        return self._token

    @property
    def close_receipt(self):
        # Entirely this instance's cached immutable result; no lower reads.
        return self._receipt

    def __enter__(self):
        _raise_first(self)
        if self._phase != "new":
            _fail("CLOSED")
        _registered(self)
        if lifecycle._CURRENT.get() is not None:
            _fail("INVALID_ARGUMENT")
        try:
            guard = self._context.__enter__()
            if type(guard) is not lifecycle.StorageGuard or guard is not self._guard:
                _fail("INVALID_RESULT")
            self._enter_returned = True
            return guard
        except BaseException as failure:
            _capture_first(self, failure)
            if self._phase == "active":
                self.__exit__(type(failure), failure, self._first_tb)
            else:
                self._outer_exit_state = "enter_failed"
                _finish_group(self)
            _raise_first(self)

    def __exit__(self, kind, primary, traceback):
        if self._exit_attempted:
            _raise_first(self)
            return False
        self._exit_attempted = True
        if primary is not None:
            _capture_first(self, primary, traceback)
        self._outer_exit_state = "attempted"
        _cleanup_clock(self)
        try:
            returned = self._context.__exit__(None if self._first is None else type(self._first),
                                              self._first, self._first_tb)
            if returned is not False:
                _fail("INVALID_RESULT")
            self._outer_exit_state = "returned_false"
        except BaseException as failure:
            self._outer_exit_state = "failed"
            _capture_first(self, failure)
        finally:
            # Original outer scope/contextmanager has completed before facts/gate/cache.
            _finish_group(self)
        if self._first is not None and self._first is not primary:
            _raise_first(self)
        return False


def _check(group):
    _raise_first(group)
    if group._callback is not None:
        try:
            result = group._callback()
            if result is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            _capture_first(group, failure)
            _raise_first(group)
    _clock(group._token)


def _open_root(group, root, stack):
    _check(group)
    root.directory_journal = safe._new_owned_directory_journal(group._binding, root_ordinal=root.ordinal)
    root.directory = object.__new__(safe._Directory)
    root.retirements[0] = _RootRetirement(group, root, "directory", root.directory, root.directory_journal)
    stack.push(root.retirements[0])
    root.directory_started = True
    safe._Directory.__init__(root.directory, root.path, base=root.path,
                             _owned_close_journal=root.directory_journal)
    with lifecycle._filesystem():
        if safe._Directory.__enter__(root.directory) is not root.directory:
            _fail("INVALID_RESULT")
    root.directory_entered = True
    root.stream = object.__new__(owned.LockStreamDeadlineLease)
    root.retirements[1] = _RootRetirement(group, root, "stream", root.stream)
    stack.push(root.retirements[1])
    root.stream_started = True
    owned.LockStreamDeadlineLease.__init__(root.stream, root.directory,
                                         binding=group._binding, root_ordinal=root.ordinal)
    with lifecycle._filesystem():
        stream = owned.LockStreamDeadlineLease.__enter__(root.stream)
    root.stream_entered = True
    root.lock_journal = lifecycle._new_owned_lock_journal(group._binding, root_ordinal=root.ordinal,
                                            stream_owner=root.stream, directory_owner=root.directory)
    root.lock = object.__new__(lifecycle._FileLock)
    root.retirements[2] = _RootRetirement(group, root, "lock", root.lock, root.lock_journal)
    stack.push(root.retirements[2])
    root.lock_started = True
    lifecycle._FileLock.__init__(root.lock, root.directory, stream, _owned_lock_journal=root.lock_journal)
    root.lock.verify_stat()
    _check(group)
    if root.lock.attempt(True) is not True:
        lifecycle._fail("STORAGE_BUSY")
    root.lock_acquired = True
    root.lock.verify()


def _retire_context(group):
    guard = group._guard
    if guard is not None:
        try:
            guard._active = False
            group._guard_retired = True
        except BaseException as failure:
            _capture_first(group, failure)
    token = group._current_token
    if token is not None:
        # Consume before any cleanup clock/getter/native operation.
        group._context_reset_state = "attempted"
        _cleanup_clock(group)
        try:
            if _reset_current(token) is not None:
                _fail("INVALID_RESULT")
            group._context_reset_state = "reset"
        except BaseException as failure:
            group._context_reset_state = "unknown"
            _capture_first(group, failure)
        _cleanup_clock(group)


def _owner_record_pins(group):
    # Mutable _owner_records is a facade/tamper detector. Only this actual
    # per-registration pin supplies fact authority, including partial entry.
    records = ()
    try:
        pins = group._pins
        if type(pins) is not tuple or len(pins) != 11 or type(pins[10]) is not tuple:
            _fail("INVALID_RESULT")
        records = pins[10]
        if group._owner_records is not records:
            _fail("INVALID_RESULT")
    except BaseException as failure:
        _capture_first(group, failure)
    trusted = []
    for record in records:
        try:
            if type(record) is not _RegisteredOwner:
                _fail("INVALID_RESULT")
            callback = record.actual_callback
            if (type(callback) is not _RootRetirement or callback._registration is not record
                    or callback._group is not group or callback._root is not record.actual_root
                    or not any(record.actual_root is root for root in group._roots)):
                _fail("INVALID_RESULT")
            trusted.append(record)
        except BaseException as failure:
            _capture_first(group, failure)
    return tuple(trusted)


def _read_root_facts(group, root, records):
    selections = (("directory", safe.OwnedDirectoryCloseReceiptV1),
                  ("stream", owned.LockStreamCloseReceiptV1),
                  ("lock", lifecycle.OwnedLockCloseReceiptV1))
    for index, (role, expected) in enumerate(selections):
        # Select solely from the immutable pre-constructor records. Mutable
        # root.retirements is a tamper detector, never cleanup/fact authority.
        try:
            registrations = tuple(record for record in records
                                  if record.actual_root is root and record.role == role)
            if not registrations:
                continue
            if len(registrations) != 1:
                _fail("INVALID_RESULT")
            registration = registrations[0]
            owner = registration.actual_owner
            fact_state = registration.fact_state
            if type(fact_state) is not _RootFactState:
                _fail("INVALID_RESULT")
            if fact_state.attempted:
                continue
            fact_state.attempted = True
            try:
                root.fact_reads_attempted[index] = True
            except BaseException as failure:
                _capture_first(group, failure)
            if role == "directory":
                actual = safe._Directory.owned_close_receipt.__get__(owner, safe._Directory)
            elif role == "stream":
                actual = owned.LockStreamDeadlineLease.close_receipt.__get__(owner, owned.LockStreamDeadlineLease)
            else:
                actual = lifecycle._FileLock.owned_lock_receipt.__get__(owner, lifecycle._FileLock)
            if (type(actual) is not expected
                    or actual.root_ordinal != registration.root_ordinal):
                _fail("INVALID_RESULT")
            fact_state.receipt = actual
            setattr(root, role + "_receipt", actual)
        except BaseException as failure:
            _capture_first(group, failure)


def _root_cached_receipt(root, records, role):
    selected = tuple(record for record in records if record.actual_root is root and record.role == role)
    if not selected:
        return None
    if len(selected) != 1 or type(selected[0].fact_state) is not _RootFactState:
        _fail("INVALID_RESULT")
    return selected[0].fact_state.receipt


def _root_terminal(root, records):
    directory, stream, lock = (_root_cached_receipt(root, records, role)
                               for role in ("directory", "stream", "lock"))
    roles = tuple(record.role for record in records if record.actual_root is root)
    if "directory" in roles:
        if (type(directory) is not safe.OwnedDirectoryCloseReceiptV1
                or directory.handle_states not in (("empty",), ("closed",))):
            return False
    if "stream" in roles:
        if (type(stream) is not owned.LockStreamCloseReceiptV1
                or stream.handle_state not in ("empty", "transferred", "closed")
                or stream.fd_state not in ("empty", "closed")
                or stream.raw_state not in ("empty", "closed")
                or stream.buffer_state not in ("empty", "closed")
                or stream.transfer_state not in ("not_started", "transferred")
                or stream.fd_close_owner != "numeric_owner"):
            return False
    if "lock" in roles:
        if (type(lock) is not lifecycle.OwnedLockCloseReceiptV1
                or lock.acquire_state not in ("not_started", "not_acquired", "acquired")
                or lock.unlock_consumed is not True
                or (lock.unlock_owed is True and lock.unlock_state != "unlocked")
                or (lock.unlock_owed is False and lock.unlock_state != "not_owed")):
            return False
    return True


def _finish_group_facts(group):
    group._phase, group._exit_attempted = "retired", True
    records = _owner_record_pins(group)
    for root in group._roots:
        _read_root_facts(group, root, records)
    try:
        _clock(group._token)
        group._effective_gate_passed = True
    except BaseException as failure:
        _capture_first(group, failure)
    declared_complete = all(root.directory_entered and root.stream_entered and root.lock_acquired
                            for root in group._roots)
    terminal = all(_root_terminal(root, records) for root in group._roots)
    captures = tuple(None if value is None else value._receipt for value in group._captures)
    lineage_complete = all(type(value) is CaptureLineageReceiptV1 and value.invocation_registered is True
                           and value.context_completed is True and value.normal_exit is True for value in captures)
    normal = (group._first is None and group._outer_exit_state == "returned_false"
        and group._enter_returned and declared_complete and terminal
        and group._context_reset_state == "reset" and group._guard_retired and group._effective_gate_passed
        and lineage_complete and all(_root_cached_receipt(root, records, role).normal_exit is True
            for root in group._roots for role in ("directory", "stream", "lock")))
    group._normal_exit = normal
    try:
        group._receipt = ExclusiveGroupCloseReceiptV1(1, "owned_ex_group", "native_owner_close_facts_only",
            group._phase, group._enter_returned, group._exit_attempted, group._first is not None, normal, False,
            group._context_reset_state, group._guard_retired, len(group._roots),
            sum(root.lock_acquired for root in group._roots), declared_complete,
            tuple(_root_cached_receipt(root, records, "lock") for root in group._roots),
            tuple(_root_cached_receipt(root, records, "stream") for root in group._roots),
            tuple(_root_cached_receipt(root, records, "directory") for root in group._roots),
            terminal, group._effective_gate_passed,
            group._declared_capture_count, captures, lineage_complete, normal, False, False)
    except BaseException as failure:
        _capture_first(group, failure)
        group._receipt, group._normal_exit = None, False


def _finish_group(group):
    # Corrupted metadata cannot replace an earlier body/control exception.
    try:
        _finish_group_facts(group)
    except BaseException as failure:
        _capture_first(group, failure)
        group._phase, group._exit_attempted = "retired", True
        group._receipt, group._normal_exit = None, False


def _guard_many_owned(profiles, exclusive, timeout, check_cancelled, group):
    group = _registered(group)
    if (group._phase != "new" or type(profiles) is not tuple or len(profiles) != 1
            or profiles[0] is not group._profile or exclusive is not True
            or type(timeout) is not int or timeout != 0
            or check_cancelled is not group._callback or lifecycle._CURRENT.get() is not None
            or tuple(group._profile.identity) != group._profile_identity
            or lifecycle._roots(profiles) != tuple(root.path for root in group._roots)):
        _fail("INVALID_ARGUMENT")
    group._phase = "opening"
    try:
        with ExitStack() as stack:
            try:
                for root in group._roots:
                    _open_root(group, root, stack)
                locks = tuple(root.lock for root in group._roots)
                if not lifecycle._original_lock_dispatch(locks):
                    _fail("INVALID_RESULT")
                group._guard = lifecycle.StorageGuard(profiles, True, locks)
                group._context_reset_state = "setting"
                try:
                    token = _set_current(group._guard)
                except BaseException:
                    group._context_reset_state = "unknown"
                    raise
                group._current_token = token
                if type(token) is not Token:
                    group._current_token = None
                    group._context_reset_state = "unknown"
                    _fail("INVALID_RESULT")
                group._context_reset_state = "owned"
                _check(group)
                lifecycle._require_replica_group_original(group._guard)
                if group._guard.verify() is not group._guard:
                    _fail("INVALID_RESULT")
                records = _owner_record_pins(group)
                if len(records) != 3 * len(group._roots):
                    _fail("INVALID_RESULT")
                _raise_first(group)
                group._phase = "active"
                yield group._guard
                lifecycle._require_replica_group_original(group._guard)
                if group._guard.verify() is not group._guard:
                    _fail("INVALID_RESULT")
                _check(group)
            except BaseException as primary:
                _capture_first(group, primary)
            finally:
                _retire_context(group)
    except BaseException as failure:
        _capture_first(group, failure)
    finally:
        # Only native unwind is complete here. The actual outer context
        # return and final gate/cache belong to OwnedExclusiveDeadlineLease.
        group._phase = "retired"
    _raise_first(group)


class BorrowedCaptureDeadlineLease:
    """An actual origin invocation borrows this EX; close DTO is not authority."""
    __slots__ = ("_seal", "_group", "_operation", "_parent", "_effective", "_identity", "_ordinal",
        "_phase", "_state", "_guard", "_sources", "_source_reads", "_first", "_first_tb",
        "_completed", "_finish_attempted", "_receipt", "_pins", "_invocation_pin", "_source_pins",
        "_budget", "_restricted", "_read_budget", "_node_parent", "_support_pins", "_image_pin", "__weakref__")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    @property
    def close_receipt(self):
        return self._receipt


def _new_owned_capture(group, *, operation, parent_token, effective_token):
    group = _registered(group)
    operation = owned._operation(operation)
    parent, effective = owned._token(parent_token), owned._token(effective_token)
    if (group._phase != "active" or group._guard is not lifecycle._CURRENT.get()
            or operation != group._binding._operation
            or parent is not group._binding._effective_token
            or effective.absolute_monotonic > parent.absolute_monotonic):
        _fail("INVALID_ARGUMENT")
    if any(value is not None and value._phase in ("new", "active") for value in group._captures):
        _fail("CLOSED")
    ordinal = next((i for i, value in enumerate(group._captures) if value is None), None)
    if ordinal is None:
        _fail("CLOSED")
    _clock(effective)
    result = object.__new__(BorrowedCaptureDeadlineLease)
    result._seal, result._group, result._operation = _GROUP_SEAL, group, group._binding._operation
    result._parent, result._effective, result._identity, result._ordinal = parent, effective, object(), ordinal
    result._phase, result._state, result._guard = "new", None, group._guard
    result._sources, result._source_reads = [None] * len(_SOURCE_ROLES), [False] * len(_SOURCE_ROLES)
    result._first = result._first_tb = result._receipt = None
    result._completed = result._finish_attempted = False
    result._pins = (group, result._operation, parent, effective, result._identity, ordinal, result._sources)
    result._invocation_pin = None
    result._source_pins = (None,) * len(_SOURCE_ROLES)
    result._budget = result._restricted = None
    result._read_budget = result._node_parent = None
    result._support_pins = (None, None)
    result._image_pin = None
    group._captures[ordinal] = result
    return result


def _capture_registered(value):
    if (type(value) is not BorrowedCaptureDeadlineLease or value._seal is not _GROUP_SEAL
            or type(value._group) is not OwnedExclusiveDeadlineLease
            or type(value._ordinal) is not int or not 0 <= value._ordinal < len(value._group._captures)
            or value._group._captures[value._ordinal] is not value
            or value._operation is not value._group._binding._operation
            or value._parent is not value._group._binding._effective_token
            or value._identity is value._group._binding._scope_identity):
        _fail("INVALID_ARGUMENT")
    _registered(value._group)
    pins = value._pins
    if (type(pins) is not tuple or len(pins) != 7
            or value._group is not pins[0] or value._operation is not pins[1]
            or value._parent is not pins[2] or value._effective is not pins[3]
            or value._identity is not pins[4] or value._ordinal != pins[5]
            or value._sources is not pins[6]):
        _fail("INVALID_ARGUMENT")
    return value


def _begin_capture(value, budget, guard):
    from . import personal_authority_origin_capture_deadline as capture
    value = _capture_registered(value)
    if type(budget) is not capture._OriginBudget:
        _fail("INVALID_ARGUMENT")
    state = budget.state
    if (value._phase != "new" or type(state) is not capture._FirstState
            or state.token is not value._effective or guard is not value._guard
            or type(budget.restricted) is not owned.deadline._RestrictedBudget
            or budget.effective_deadline is not value._effective
            or budget.restricted.requested_deadline is not value._parent
            or budget.restricted.effective_deadline is not value._effective
            or value._group._phase != "active" or lifecycle._CURRENT.get() is not guard):
        _fail("INVALID_ARGUMENT")
    if any(other is not None and other is not value
           and (other._state is state or other._restricted is budget.restricted)
           for other in value._group._captures):
        _fail("INVALID_ARGUMENT")
    value._budget, value._restricted = budget, budget.restricted
    value._state, value._phase = state, "active"
    value._invocation_pin = (budget, state, guard, budget.restricted)


@dataclass(frozen=True, slots=True)
class _CaptureSourceRegistration:
    role: str
    actual_owner: object
    profile: object
    guard: object
    token: object
    directory: object
    support: object
    restricted: object
    suffix: object


@dataclass(frozen=True, slots=True)
class _CaptureSupportRegistration:
    role: str
    actual_owner: object
    profile: object
    guard: object
    token: object
    directory: object
    restricted: object


@dataclass(frozen=True, slots=True)
class _CaptureImageRegistration:
    actual_image: object
    actual_lease: object
    returned_fingerprint: object
    encoded_fingerprint: object


def _register_capture_image(value, image, fingerprint):
    from . import personal_authority_committed_image_deadline as images
    value = _capture_registered(value)
    lease = value._sources[1]
    if (value._phase != "active" or value._image_pin is not None
            or type(image) is not images.DeadlineCommittedImage or lease is None
            or image._lease is not lease or lease._image is not image
            or image._active is not True or lease._phase != "active"
            or type(fingerprint) is not dict or type(image._fingerprint) is not bytes):
        _fail("INVALID_ARGUMENT")
    value._image_pin = _CaptureImageRegistration(image, lease, fingerprint, image._fingerprint)


def _register_capture_support(value, role, owner):
    from . import personal_authority_live_sql_held_deadline as held
    from . import personal_authority_source_deadline as nodes
    value = _capture_registered(value)
    if value._phase != "active" or type(role) is not str or role not in ("read_budget", "node_parent"):
        _fail("INVALID_ARGUMENT")
    directory = value._sources[0]
    if directory is None:
        _fail("INVALID_ARGUMENT")
    if role == "read_budget":
        index = 0
        if (type(owner) is not held.LiveSqlHeldBudget or value._read_budget is not None
                or owner._profile is not value._group._profile or owner._guard is not value._guard
                or owner._token is not value._effective or owner._directory is not None
                or owner._retired is not False or owner._first is not None
                or type(value._image_pin) is not _CaptureImageRegistration
                or owner._expected != value._image_pin.returned_fingerprint["files"]
                or any(flag is not False for flag in owner._claims.values())):
            _fail("INVALID_ARGUMENT")
        value._read_budget = owner
    else:
        index = 1
        if (type(owner) is not nodes.DeadlineNodeDirectory or value._node_parent is not None
                or owner._directory is not directory or owner._budget is not value._restricted):
            _fail("INVALID_ARGUMENT")
        value._node_parent = owner
    registration = _CaptureSupportRegistration(role, owner, value._group._profile,
        value._guard, value._effective, directory, value._restricted)
    value._support_pins = value._support_pins[:index] + (registration,) + value._support_pins[index + 1:]


def _register_capture_source(value, role, owner):
    from . import personal_authority_directory_deadline as directories
    from . import personal_authority_committed_image_deadline as images
    from . import personal_authority_live_sql_held_deadline as held
    from . import personal_authority_source_deadline as nodes
    value = _capture_registered(value)
    if type(role) is not str or role not in _SOURCE_ROLES or value._phase != "active":
        _fail("INVALID_ARGUMENT")
    index = _SOURCE_ROLES.index(role)
    expected = (directories.DeadlineDirectory, images.CommittedImageDeadlineLease,
                held.DeadlineLiveSqlHeldFile, held.DeadlineLiveSqlHeldFile,
                held.DeadlineLiveSqlHeldFile, nodes.DeadlineHeldFile)[index]
    if type(owner) is not expected or value._sources[index] is not None:
        _fail("INVALID_ARGUMENT")
    directory, support, suffix = value._sources[0], None, None
    if index == 0:
        directory = owner
        if (owner._deadline_token is not value._effective
                or owner._path != value._group._profile.database.parent
                or owner._base != value._group._profile.database.parent
                or owner._parent is not None or owner._phase != "new"
                or owner._enter_returned is not False or owner._exit_attempted is not False
                or owner._close_failure is not None):
            _fail("INVALID_ARGUMENT")
    elif index == 1:
        if (directory is None or owner._token is not value._effective
                or owner._directory is not directory or owner._profile is not value._group._profile
                or owner._guard is not value._guard or owner._phase != "new"
                or owner._enter_started is not False or owner._first is not None
                or any(actual is not None for actual in
                    (owner._temp, owner._pin, owner._owner, owner._budget, owner._sql, owner._con, owner._image))
                or (owner._temp_state, owner._pin_state, owner._sql_state) != ("empty",) * 3):
            _fail("INVALID_ARGUMENT")
    elif index in (2, 3, 4):
        support, suffix = value._read_budget, ("", "-wal", "-journal")[index - 2]
        if (directory is None or support is None or owner._token is not value._effective
                or owner._directory is not directory or owner._budget is not support
                or owner._suffix != suffix or owner._phase != "new"
                or owner._name != value._group._profile.database.name + suffix
                or support._expected[suffix] is None or owner._lease_state != "empty"
                or any(actual is not None for actual in (owner._lease, owner._raw, owner._fd))
                or owner._entered is not False
                or owner._enter_returned is not False or owner._exit_attempted is not False):
            _fail("INVALID_ARGUMENT")
    else:
        support = value._node_parent
        if (directory is None or support is None or owner._node is not support
                or support._directory is not directory or support._budget is not value._restricted
                or type(owner._budget) is not nodes._NodeFileBudget
                or owner._budget.parent is not value._restricted or owner._phase != "new"
                or owner._budget.count != 0 or owner._entered is not False
                or any(actual is not None for actual in (owner._stream, owner._raw, owner._byte_size,
                    owner._sha256, owner._path_identity, owner._handle_identity, owner._directory_identity))
                or owner._enter_returned is not False or owner._exit_attempted is not False):
            _fail("INVALID_ARGUMENT")
    value._sources[index] = owner
    registration = _CaptureSourceRegistration(role, owner, value._group._profile, value._guard,
        value._effective, directory, support, value._restricted, suffix)
    value._source_pins = value._source_pins[:index] + (registration,) + value._source_pins[index + 1:]


def _capture_associations(value):
    """Recheck actual references against registration-time immutable records."""
    from . import personal_authority_live_sql_held_deadline as held
    from . import personal_authority_source_deadline as nodes
    if (type(value._source_pins) is not tuple or len(value._source_pins) != len(_SOURCE_ROLES)
            or type(value._support_pins) is not tuple or len(value._support_pins) != 2):
        _fail("INVALID_RESULT")
    image_pin = value._image_pin
    if image_pin is not None:
        if (type(image_pin) is not _CaptureImageRegistration
                or image_pin.actual_lease is not value._sources[1]
                or image_pin.actual_image._lease is not image_pin.actual_lease
                or image_pin.actual_lease._image is not image_pin.actual_image
                or image_pin.actual_image._fingerprint is not image_pin.encoded_fingerprint):
            _fail("INVALID_RESULT")
    for index, registration in enumerate(value._support_pins):
        actual = (value._read_budget, value._node_parent)[index]
        if registration is None:
            if actual is not None:
                _fail("INVALID_RESULT")
            continue
        if (type(registration) is not _CaptureSupportRegistration
                or actual is not registration.actual_owner or registration.profile is not value._group._profile
                or registration.guard is not value._guard or registration.token is not value._effective
                or registration.directory is not value._sources[0]
                or registration.restricted is not value._restricted):
            _fail("INVALID_RESULT")
        if index == 0:
            if (type(actual) is not held.LiveSqlHeldBudget or actual._profile is not registration.profile
                    or actual._guard is not registration.guard or actual._token is not registration.token
                    or type(image_pin) is not _CaptureImageRegistration
                    or actual._expected != image_pin.returned_fingerprint["files"]
                    or (actual._directory is not None and actual._directory is not registration.directory)):
                _fail("INVALID_RESULT")
        elif (type(actual) is not nodes.DeadlineNodeDirectory
                or actual._directory is not registration.directory or actual._budget is not registration.restricted):
            _fail("INVALID_RESULT")
    for index, registration in enumerate(value._source_pins):
        actual = value._sources[index]
        if registration is None:
            if actual is not None:
                _fail("INVALID_RESULT")
            continue
        if (type(registration) is not _CaptureSourceRegistration or registration.role != _SOURCE_ROLES[index]
                or actual is not registration.actual_owner or registration.profile is not value._group._profile
                or registration.guard is not value._guard or registration.token is not value._effective
                or registration.directory is not value._sources[0]
                or registration.restricted is not value._restricted):
            _fail("INVALID_RESULT")
        if index == 0:
            if (actual._deadline_token is not registration.token or actual._parent is not None
                    or actual._path != registration.profile.database.parent
                    or actual._base != registration.profile.database.parent):
                _fail("INVALID_RESULT")
        elif index == 1:
            if (actual._token is not registration.token or actual._directory is not registration.directory
                    or actual._profile is not registration.profile or actual._guard is not registration.guard):
                _fail("INVALID_RESULT")
        elif index in (2, 3, 4):
            if (actual._token is not registration.token or actual._directory is not registration.directory
                    or actual._budget is not registration.support or actual._budget is not value._read_budget
                    or actual._suffix != registration.suffix):
                _fail("INVALID_RESULT")
        elif (actual._node is not registration.support or actual._node is not value._node_parent
                or actual._node._directory is not registration.directory
                or actual._node._budget is not registration.restricted
                or type(actual._budget) is not nodes._NodeFileBudget
                or actual._budget.parent is not registration.restricted):
            _fail("INVALID_RESULT")
    return True


def _source_directory_terminal(receipt):
    from . import personal_authority_directory_deadline as directories
    return (type(receipt) is directories.DirectoryCloseReceiptV1
        and receipt.phase == "retired" and receipt.enter_returned is True
        and receipt.exit_attempted is True and receipt.failed is False and receipt.normal_exit is True
        and type(receipt.handle_states) is tuple and len(receipt.handle_states) > 0
        and all(type(state) is str and state == "closed" for state in receipt.handle_states))


def _source_readonly_terminal(receipt):
    from . import personal_authority_directory_deadline as directories
    return (type(receipt) is directories.ReadonlyCloseReceiptV1
        and receipt.phase == "retired" and receipt.enter_returned is True
        and receipt.exit_attempted is True and receipt.failed is False and receipt.normal_exit is True
        and receipt.handle_state == "transferred" and receipt.transfer_state == "transferred"
        and receipt.fd_state == receipt.raw_state == receipt.buffer_state == "closed")


def _source_held_terminal(receipt):
    from . import personal_authority_live_sql_held_deadline as held
    from . import personal_authority_live_sql_deadline as live
    if (type(receipt) is not held.LiveSqlHeldCloseReceiptV1
            or receipt.phase != "retired" or receipt.enter_returned is not True
            or receipt.exit_attempted is not True or receipt.failed is not False
            or receipt.normal_exit is not True or receipt.terminal_verified is not True):
        return False
    lower = receipt.lower_close_receipt
    return (type(lower) is live.LiveSqlCloseReceiptV1 and lower.phase == "retired"
        and lower.enter_returned is True and lower.exit_attempted is True
        and lower.failed is False and lower.normal_exit is True
        and lower.handle_state == lower.transfer_state == "transferred"
        and lower.fd_state == lower.raw_state == "closed")


def _source_node_terminal(receipt):
    from . import personal_authority_source_deadline as nodes
    if (type(receipt) is not nodes.HeldFileCloseReceiptV1 or receipt.phase != "retired"
            or receipt.enter_returned is not True or receipt.exit_attempted is not True
            or receipt.failed is not False or receipt.normal_exit is not True):
        return False
    stream = receipt.stream_close_receipt
    return (type(stream) is nodes.NodeStreamCloseReceiptV1 and stream.phase == "retired"
        and stream.enter_returned is True and stream.exit_attempted is True
        and stream.failed is False and stream.normal_exit is True
        and _source_readonly_terminal(stream.readonly_close_receipt))


def _source_image_terminal(facts):
    from . import personal_authority_sql_bootstrap_deadline as bootstrap
    from . import personal_preservation_source as preservation
    if type(facts) is not tuple or len(facts) != 4:
        return False
    scalars, private_pin, sql, temp = facts
    if (type(scalars) is not tuple or len(scalars) != 5 or scalars[0] != "retired"
            or any(type(state) is not str or state != "closed" for state in scalars[1:4])
            or scalars[4] is not True or not _source_directory_terminal(private_pin)
            or type(sql) is not bootstrap.SqlBootstrapCloseReceiptV1
            or sql.phase != "retired" or sql.enter_returned is not True or sql.exit_attempted is not True
            or sql.failed is not False or sql.normal_exit is not True or sql.connection_state != "closed"
            or type(sql.cursor_states) is not tuple or len(sql.cursor_states) != 4
            or any(type(state) is not str or state != "closed" for state in sql.cursor_states)
            or sql.bootstrap_complete is not True or sql.limits_restored is not True
            # This exact bool is recorded before Connection.close; a live
            # readonly BEGIN can legitimately still be in transaction.
            # Native closure is the separate strict CLOSED connection fact.
            or type(sql.exit_in_transaction) is not bool):
        return False
    return (type(temp) is preservation.TempCleanupReceiptV1 and temp.phase == "retired"
        and temp.created_returned is True and temp.cleanup_attempted is True
        and temp.cleanup_returned is True and temp.cleanup_return_legal is True
        and temp.failed is False and temp.cleanup_state == "closed")


def _finish_capture_facts(value, state, *, primary=None, traceback=None, completed=False):
    value = _capture_registered(value)
    value._phase = "retired"
    if primary is not None:
        _capture_first(value, primary, traceback)
    if state is not value._state or state.token is not value._effective:
        _capture_first(value, owned.OwnedExDeadlineError("INVALID_RESULT"))
    value._completed = completed is True and value._state is state
    # Reject damaged/foreign registration before any lower getter.
    _capture_associations(value)
    facts = []
    for index, registration in enumerate(value._source_pins):
        try:
            if registration is not None and type(registration) is not _CaptureSourceRegistration:
                _fail("INVALID_RESULT")
            actual = None if registration is None else registration.actual_owner
            if actual is None:
                facts.append(None)
                continue
            value._source_reads[index] = True
            if _SOURCE_ROLES[index] == "image":
                # These getters return this actual image owner's own caches.
                evidence = actual.evidence
                if type(evidence) is not dict:
                    _fail("INVALID_RESULT")
                image_scalars = tuple(evidence[name] for name in
                    ("phase", "temp_state", "private_pin_state", "sql_state", "normal_exit"))
                facts.append((image_scalars, actual.private_pin_close_receipt,
                              actual.sql_close_receipt, actual.temp_cleanup_receipt))
            else:
                facts.append(actual.close_receipt)
        except BaseException as failure:
            _capture_first(value, failure)
            facts.append(None)
    try:
        _clock(value._effective)
    except BaseException as failure:
        _capture_first(value, failure)
    invocation = value._state is state and state.token is value._effective
    # A carrier's ordinary return is insufficient. Require actual mandatory
    # source owners and their cached lower facts from this invocation.
    mandatory = (_source_directory_terminal(facts[0]) and _source_image_terminal(facts[1])
        and _source_held_terminal(facts[2]) and _source_node_terminal(facts[5])
        and all(actual is None or _source_held_terminal(fact)
                for actual, fact in zip(value._sources[3:5], facts[3:5])))
    normal = value._first is None and invocation and value._completed and mandatory
    value._receipt = CaptureLineageReceiptV1(1, "borrowed_capture", "actual_capture_lineage_and_cached_lower_facts",
        value._phase, value._ordinal, invocation, value._completed, value._first is not None, normal,
        True, False, tuple(value._source_reads), tuple(facts), False)
    if value._first is not None and value._first is not primary:
        _raise_first(value)


def _finish_capture(value, state, *, primary=None, traceback=None, completed=False):
    if type(value) is not BorrowedCaptureDeadlineLease:
        _fail("INVALID_ARGUMENT")
    if value._finish_attempted:
        _fail("CLOSED")
    # Consume before metadata/lower getters, and remember the body/control
    # before any registration, predicate, scalar or receipt-construction fault.
    value._finish_attempted = True
    if primary is not None:
        _capture_first(value, primary, traceback)
    try:
        _finish_capture_facts(value, state, primary=primary, traceback=traceback, completed=completed)
    except BaseException as failure:
        _capture_first(value, failure)
        value._phase, value._receipt = "retired", None
    if value._first is not None and value._first is not primary:
        _raise_first(value)


def _capture_terminal_lineage_row(group, capture, ordinal, *, prior_rows):
    identities, states, restrictions = prior_rows
    capture = _capture_registered(capture)
    if (capture._ordinal != ordinal or capture._phase != "retired"
            or capture._guard is not group._guard
            or capture._parent is not group._binding._effective_token
            or capture._effective.absolute_monotonic > capture._parent.absolute_monotonic
            or type(capture._invocation_pin) is not tuple or len(capture._invocation_pin) != 4
            or capture._budget is not capture._invocation_pin[0]
            or capture._state is not capture._invocation_pin[1]
            or capture._guard is not capture._invocation_pin[2]
            or capture._restricted is not capture._invocation_pin[3]
            or capture._budget.state is not capture._state
            or capture._budget.restricted is not capture._restricted
            or capture._budget.effective_deadline is not capture._effective
            or capture._restricted.requested_deadline is not capture._parent
            or capture._restricted.effective_deadline is not capture._effective
            or capture._state.token is not capture._effective
            or any(capture._identity is value for value in identities)
            or any(capture._state is value for value in states)
            or any(capture._restricted is value for value in restrictions)):
        _fail("INVALID_RESULT")
    _capture_associations(capture)
    identities.append(capture._identity)
    states.append(capture._state)
    restrictions.append(capture._restricted)
    receipt = BorrowedCaptureDeadlineLease.close_receipt.__get__(capture, BorrowedCaptureDeadlineLease)
    return capture, receipt


def _consume_owned_ex_group(group):
    if type(group) is not OwnedExclusiveDeadlineLease:
        _fail("INVALID_ARGUMENT")
    if group._consume_attempted:
        _fail("CLOSED")
    # Failure consumes this actual registered operation's one attempt too.
    group._consume_attempted = True
    group = _registered(group)
    _raise_first(group)
    if (group._phase != "retired" or lifecycle._CURRENT.get() is not None
            or group._guard is None or group._guard._active is not False):
        _fail("INVALID_RESULT")
    _clock(group._token)
    actual = OwnedExclusiveDeadlineLease.close_receipt.__get__(group, OwnedExclusiveDeadlineLease)
    if (actual is not group._receipt or group._normal_exit is not True
            or type(actual) is not ExclusiveGroupCloseReceiptV1 or actual.normal_exit is not True
            or actual.owned_outer_ex_closed is not True or actual.outer_ex_borrowed is not False
            or actual.capture_lineage_complete is not True or actual.source_closed is not False
            or actual.write_authority is not False):
        _fail("INVALID_RESULT")
    identities, states, restrictions = [], [], []
    for ordinal, capture in enumerate(group._captures):
        capture, receipt = _capture_terminal_lineage_row(group, capture, ordinal,
            prior_rows=(identities, states, restrictions))
        if receipt is not actual.capture_receipts[ordinal] or receipt.normal_exit is not True:
            _fail("INVALID_RESULT")
    _clock(group._token)
    return actual


def _content_codec_modules_original():
    """Only seven fixed pure codecs, excluding every mutable runtime registry."""
    from . import personal_authority_enrollment as enrollment
    selected = enrollment.store_runtime.selections
    return (selected.prepare, selected.store, selected.prepare.authority,
        selected.prepare.enrollment, selected.prepare.namespace,
        selected.prepare.origin, selected.prepare.origin.preservation)


def _catalog_modules_original():
    """Fixed actual lanes; no caller module, runtime discovery or graph truncation."""
    from . import personal_authority_origin_capture_deadline as capture
    from . import personal_authority_enrollment as enrollment
    from . import personal_authority_enrollment_physical as physical
    from . import personal_authority_enrollment_packet as packet
    import sys
    return (owned, sys.modules[__name__], capture, enrollment, physical, packet,
        capture.directories, capture.nodes, capture.images, capture.live_sql, capture.projection,
        capture.images.images, capture.images.bootstrap, capture.projection._sql,
        capture.live_sql.live_sql, owned.deadline, owned.contract,
        enrollment.contract, enrollment.publication, enrollment.namespace,
        enrollment.native, enrollment.origin, enrollment.custody, enrollment.store_runtime,
        enrollment.native._custody.store, enrollment.native._custody,
        enrollment.store_runtime.selections,
        enrollment.store_runtime.selections.prepare,
        enrollment.store_runtime.selections.store,
        enrollment.store_runtime.selections.prepare.authority,
        enrollment.store_runtime.selections.prepare.enrollment,
        enrollment.store_runtime.selections.prepare.namespace,
        enrollment.store_runtime.selections.prepare.origin,
        enrollment.store_runtime.selections.prepare.origin.preservation,
        enrollment.store_runtime.directories, enrollment.store_runtime.directories.census_native)


def _catalog_extra_original():
    """Additional fixed descriptors/OS seams and noncallable owner anchors."""
    from . import personal_authority_origin_capture_deadline as capture
    from . import personal_authority_enrollment as enrollment
    from . import personal_authority_enrollment_physical as physical
    from . import personal_authority_enrollment_packet as packet
    import builtins
    directory_module = enrollment.store_runtime.directories
    census_module = directory_module.census_native
    runtime_module = enrollment.store_runtime
    io_module = runtime_module.file_io
    # The original module capture accepts a real dict. Fixed class values
    # enter its existing complete-MRO class pin through their module export.
    # Keep all rows/order/duplicates and leave every nonclass seam unchanged.
    class_exports = (
        (directory_module._StoreFinalCensusGeneration, directory_module, "_StoreFinalCensusGeneration"),
        (directory_module._StoreFinalCensus, directory_module, "_StoreFinalCensus"),
        (io_module.StoreFileIOError, io_module, "StoreFileIOError"),
        (io_module._FileIOCleanupGraph, io_module, "_FileIOCleanupGraph"),
        (io_module._StoreFileCycle, io_module, "_StoreFileCycle"),
        (io_module._FileResource, io_module, "_FileResource"),
        (io_module._StoreHeldReader, io_module, "_StoreHeldReader"),
        (io_module._StorePublicationReceipt, io_module, "_StorePublicationReceipt"),
        (io_module._StoreReaderCloseReceipt, io_module, "_StoreReaderCloseReceipt"),
        (io_module._GraphIndexCallback, io_module, "_GraphIndexCallback"),
        (io_module._AuthorityCheckpointReceipt, io_module, "_AuthorityCheckpointReceipt"),
        (io_module.contextlib._GeneratorContextManager, io_module.contextlib, "_GeneratorContextManager"),
        (directory_module._StoreDirectoryScope, directory_module, "_StoreDirectoryScope"),
        (directory_module._DirectoryCleanup, directory_module, "_DirectoryCleanup"),
        (directory_module._StoreFileSelection, directory_module, "_StoreFileSelection"),
        (directory_module._StoreInitialCensus, directory_module, "_StoreInitialCensus"),
        (directory_module._StoreDirectoryCloseReceipt, directory_module, "_StoreDirectoryCloseReceipt"),
        (directory_module._IndexCallback, directory_module, "_IndexCallback"),
        (runtime_module._StoreInitialRoute, runtime_module, "_StoreInitialRoute"),
        (runtime_module._StoreInitialHook, runtime_module, "_StoreInitialHook"),
        (runtime_module._StoreInitialIndexCallback, runtime_module, "_StoreInitialIndexCallback"),
        (runtime_module._StoreInitialHookMetadata, runtime_module, "_StoreInitialHookMetadata"),
        (census_module._StatusUnion, census_module, "_StatusUnion"),
        (census_module._IoStatus, census_module, "_IoStatus"),
        (census_module._UnicodeString, census_module, "_UnicodeString"),
        (census_module._FileIdBothDirectoryInformation, census_module, "_FileIdBothDirectoryInformation"),
        (census_module._QueryBuffer, census_module, "_QueryBuffer"),
        (census_module._CensusScan, census_module, "_CensusScan"),
        (census_module._QueryStorage, census_module, "_QueryStorage"),
        (census_module._RetiredIndexCallback, census_module, "_RetiredIndexCallback"),
        (census_module.native._Directory, census_module.native, "_Directory"),
        (enrollment._Budget, enrollment, "_Budget"),
        (weakref.ReferenceType, weakref, "ReferenceType"),
        (census_module.ctypes.Array, census_module.ctypes, "Array"),
        (builtins.int, builtins, "int"),
        (builtins.bytes, builtins, "bytes"),
        (builtins.str, builtins, "str"),
        (builtins.dict, builtins, "dict"),
        (builtins.object, builtins, "object"),
        (builtins.BaseException, builtins, "BaseException"),
    )

    def module_rows(original_rows):
        result = []
        for subject, names in original_rows:
            for kind, module, name in class_exports:
                if subject is kind:
                    result.append((module, (name,)))
                    break
            else:
                result.append((subject, names))
        return tuple(result)

    return (
        (packet, ("_SEAL", "_PACKETS", "_FIELDS", "_MAXIMA", "_MAX_TOTAL",
            "weakref", "dataclass", "contract", "physical", "groups", "deadline", "_PacketBytes", "_PacketSlot")),
        (physical, ("_SEAL", "_ROLES", "_MISSING", "_BUDGET_CARRIERS", "_FACTORY_OWNERS",
            "directories", "nodes", "custody", "groups", "owned", "namespace", "origin", "files", "lifecycle", "safe",
            "ContextVar", "weakref", "_EnrollmentSourceCarrier", "_CarrierFactPin", "_NodeParentBinding", "_FactoryOwnerPin",
            "_BorrowedSourceConsumer", "_BorrowedNodeFacade", "_PhysicalDirectoryScope", "_NodeHeldScope",
            "_InnerTerminalScope", "BorrowedEnrollmentSourcePreconditionV1", "_begin_factory_initialization",
            "_reject_factory_reinit", "_register_factory_owner", "_require_factory_owner",
            "_require_factory_owner_live", "_factory_cleanup_pin", "_mark_enter_owed", "_begin_owner_cleanup",
            "_reserve_owner_exit", "_reserve_lower_exit", "_begin_claimed_exit", "_validate_node_binding")),
        (capture.directories, ("_stat", "_fstat", "_close_fd", "_file_io", "_buffered_reader",
            "_create_file", "_get_info", "_close_handle", "_open_osfhandle", "_WINDOWS", "_INVALID_HANDLE",
            "_NEW", "_ACTIVE", "_RETIRED", "_EMPTY", "_OWNED", "_ATTEMPTED", "_CLOSED", "_UNKNOWN",
            "_TRANSFERRED", "_TRANSFER_PENDING", "_initialize_readonly_lease", "_new_enrollment_readonly_lease",
            "_exit_enrollment_readonly", "_enrollment_factory_module", "_enrollment_cleanup_failure",
            "_enrollment_cleanup_identity", "_retire_buffer", "_retire_raw")),
        (capture.nodes, ("_NODE", "_MAX_NODE", "_CHUNK", "_NEW", "_OPENING", "_ACTIVE", "_RETIRED",
            "_monotonic", "_fstat", "_stat", "_initialize_node_directory", "_initialize_node_file_budget",
            "_initialize_node_stream", "_initialize_held_file", "_new_enrollment_node_directory",
            "_new_enrollment_node_file_budget", "_new_enrollment_node_stream", "_new_enrollment_held_file",
            "_cleanup_enrollment_stream", "_cleanup_enrollment_held", "_exit_enrollment_node_stream",
            "_exit_enrollment_held_file", "_enrollment_factory_module", "_enrollment_cleanup_failure",
            "_enrollment_cleanup_identity", "_raw_read", "_raw_seek", "_raw_fileno")),
        (capture.directories.io, ("FileIO", "BufferedReader")),
        (enrollment, ("physical_source", "packet", "store_runtime")),
        (enrollment.store_runtime, ("_SEAL", "_CONTINUATIONS", "_SCOPE_ROWS", "_CLOSE_RECEIPTS", "_STAGES_BY_CONTINUATION", "_RETAINED_RETIREMENTS", "_RetainedRetirement",
            "_PIN_NAMES", "weakref", "dataclass", "packet", "physical", "source_custody",
            "groups", "deadline", "lifecycle", "selections", "directories", "file_io", "_INITIAL_ROUTES",
            "_INITIAL_ROUTE_VALUES", "_INITIAL_HOOK_ATTEMPTS", "_INITIAL_HOOKS", "_INITIAL_HOOK_METADATA",
            "_INITIAL_ROUTE_OWNERS", "_INITIAL_FACTORY_ROWS", "_INITIAL_FACTORY_OWNERS",
            "_INITIAL_FACTORY_ATTEMPTS", "_INITIAL_CALLBACK_PINS",
            "_StoreInitialRoute", "_StoreInitialHook", "_StoreInitialIndexCallback", "_StoreInitialHookMetadata")),
        (enrollment.store_runtime.selections, ('weakref', 'runtime', 'prepare', 'store', 'namespace', '_SEAL', '_CONTENT_ATTEMPTS', '_CONTENT_PLANS', '_PIN_NAMES', 'StoreContentPlanError', '_StoreContentPlan')),
        (enrollment.store_runtime.selections.prepare, ('base64', 'binascii', 'hashlib', 'json', 'authority', 'enrollment', 'namespace', 'origin', 'FAMILY', 'FORMAT_VERSION', 'SCOPE', 'PREPARE_KIND', 'RESERVATION_KIND', 'MAX_PREPARE_BYTES', 'MAX_RESERVATION_BYTES', 'MAX_DEPTH', 'MAX_NODES', 'MAX_INTEGER', 'MAX_ORIGINAL_BYTES', 'MAX_INPUT_BYTES', 'PUBLICATION_ORDER', 'ORIGINAL_FIELDS', '_ORIGINAL_MAXIMA', '_REFERENCE_SPECS', '_COMMON', '_PREPARE', '_RESERVATION', '_CONTEXT', '_PROFILES', '_NAMESPACE', '_SLOT', '_REFERENCE', '_REFERENCE_KEYS', 'EnrollmentSqlSuccessorError', 'ContractError', 'Error')),
        (enrollment.store_runtime.selections.store, ('base64', 'binascii', 'hashlib', 'authority', 'enrollment', 'prepare', 'namespace', 'origin', 'FAMILY', 'FORMAT_VERSION', 'SCOPE', 'KINDS', 'RECORD_ROLES', 'MAX_DEPTH', 'MAX_NODES', 'MAX_INTEGER', 'MAX_HEADER_BYTES', 'MAX_OPERATION_BYTES', 'MAX_SLOT_BYTES', 'MAX_PUBLISHED_BYTES', 'MAX_RECEIPT_BYTES', 'MAX_ENVELOPE_BYTES', 'MAX_RECORD_RESERVATION_BYTES', 'MAX_ORIGINAL_BYTES', 'MAX_BUILD_INPUT_BYTES', 'MAX_WRAPPER_BYTES', 'MAX_FILE_BYTES', 'MAX_CROSS_INPUT_BYTES', '_PREFIX', '_STEMS', '_SUFFIXES', '_LAYER_KINDS', 'WRAPPER_NAMES', 'PREPARE_PAYLOAD_NAME', 'PREPARE_CONTENT_RESERVATION_NAME', 'FILE_NAMES', '_FILE_KINDS', '_KIND_MAXIMA', '_FILE_MAXIMA', '_BASE', '_FIELDS', '_SLOT_FIELDS', '_REFERENCE_FIELDS', '_REFERENCE_SPECS', '_ORIGINAL_KEYS', '_ORIGINAL_MAXIMA', '_ORIGINAL_LOCATIONS', '_PREDECESSOR_FIELDS', '_FACT_SHA_FIELDS', '_ENVELOPE_STRUCTURAL_MAXIMA', 'EnrollmentStoreContractError', 'ContractError', 'Error')),
        (enrollment.store_runtime.selections.prepare.authority, ('dataclass', 'hashlib', 'json', 're', 'uuid', 'FORMAT_VERSION', 'MAX_INTEGER', 'HARD_LIMITS', 'ENVELOPE_FIELDS', 'ENDPOINT_FIELDS', 'PAYLOAD_FIELDS', 'KINDS', '_SHA', '_UUID', '_INIT_KINDS', '_GRANT_KINDS', 'ContractError', 'Limits', 'DEFAULT_LIMITS')),
        (enrollment.store_runtime.selections.prepare.enrollment, ('deepcopy', 'dataclass', 'hashlib', 'json', 'authority', 'namespace', 'origin', 'FAMILY', 'FORMAT_VERSION', 'SCOPE', 'MAX_RECORD_BYTES', 'MAX_DEPTH', 'MAX_NODES', 'MAX_INPUT_BYTES', '_COMMON', '_EXTRA', '_PROFILES', '_ORIGIN', '_NAMESPACE', '_AUTHORITY', '_JSON_ERRORS', 'EnrollmentContractError', 'ContractError', 'Error', 'AuthorityPrefixContent')),
        (enrollment.store_runtime.selections.prepare.namespace, ('deepcopy', 'hashlib', 'json', 're', 'origin', 'FAMILY', 'FORMAT_VERSION', 'SCOPE', 'CREATION_KIND', 'PRIVATE_POLICY', 'AUTHORITY_ROLE', 'PHYSICAL_ROLE', 'RESERVATION_NAME', 'ROOT_NAME', 'ROOT_SEAL_NAME', 'HEADER_NAME', 'MAX_RESERVATION_BYTES', 'MAX_ROOT_SEAL_BYTES', 'MAX_HEADER_BYTES', 'MAX_RECORD_BYTES', 'MAX_DEPTH', 'MAX_NODES', '_COMMON', '_EXTRA', '_ORIGIN', '_CREATOR', '_NAMES', '_DECIMAL', 'LAYOUT', '_JSON_ERRORS', 'Namespace3ContractError', 'ContractError', 'Error')),
        (enrollment.store_runtime.selections.prepare.origin, ('base64', 'binascii', 'deepcopy', 'hashlib', 'json', 'ntpath', 'posixpath', 're', 'stat', 'authority', 'preservation', 'FAMILY', 'FORMAT_VERSION', 'RESERVATION_NAME', 'OBSERVATION_NAME', 'MAX_RESERVATION_BYTES', 'MAX_OBSERVATION_BYTES', 'MAX_INTENT_BYTES', 'MAX_EVIDENCE_BYTES', 'MAX_DEPTH', 'MAX_NODES', 'MAX_INTEGER', 'MAX_NODE_BYTES', '_SHA', '_DECIMAL', '_ROOTS', '_IDENTITY', '_FILE_IDENTITY', '_WIRE_IDENTITY', '_FILE', '_RESERVATION', '_OBSERVATION', '_EVIDENCE', 'OriginReservationContractError', 'ContractError', 'Error')),
        (enrollment.store_runtime.selections.prepare.origin.preservation, ('deepcopy', 'datetime', 'wraps', 'hashlib', 'json', 'math', 'ntpath', 'posixpath', 're', 'uuid', 'FORMAT_VERSION', 'CATALOG', 'DIRECTORY', 'SCOPE', 'MAX_FILES', 'MAX_JSON_BYTES', 'MAX_TOTAL_BYTES', 'MAX_SQL_BYTES', 'MAX_ROWS', 'MAX_CELL_BYTES', 'DEFAULT_LIMITS', 'ROOT_KINDS', 'ROLES', 'PERSONAL_COLUMNS', 'RECEIVER_COLUMNS', 'PHASES', '_SHA', '_DECIMAL', '_ENTRY', '_CODE', '_STAMP', '_PROFILE_FIELDS', '_IDENTITY_FIELDS', '_WIRE_IDENTITY_FIELDS', '_FILE_FIELDS', '_COPY_FIELDS', '_BODY_ROLES', '_HASH_FIELDS', 'PersonalPreservationError', 'Error', 'PersonalPreservationContractError')),
        (enrollment.store_runtime.selections.namespace, ("MAX_CONTROL_BYTES", "_Budget")),
        (enrollment.store_runtime.selections.prepare.json, ("dumps", "loads", "JSONEncoder", "JSONDecoder")),
        (enrollment.store_runtime.selections.prepare.base64, ("b64encode", "b64decode", "_bytes_from_decode_data", "bytes_types", "binascii")),
        (enrollment.store_runtime.selections.prepare.binascii, ("Error", "a2b_base64", "b2a_base64")),
        (enrollment.store_runtime.selections.prepare.hashlib, ("sha256",)),
        (enrollment.store_runtime.selections.prepare.origin.stat, ("S_ISREG",)),
        (enrollment.store_runtime.selections.prepare.origin.preservation.ntpath, ("normcase", "normpath", "splitdrive")),
        (enrollment.store_runtime.selections.prepare.origin.preservation.posixpath, ("normpath",)),
        (enrollment.native._custody, ("source", "store", "_HANDLE", "_LOCALALLOC", "_POINTER_MAX", "_signed32")),
        (enrollment.native._custody.store, ("_CURRENT", "_CONTEXT_VARIABLE", "ContextVar", "_SEAL", "_GROUP_JOURNALS",
            "_BUDGET_JOURNALS", "_OWNER_SLOTS", "_REGISTRATION_HISTORY", "_ROLES", "_HANDLE", "_LOCALALLOC",
            "_UNKNOWN", "_CLOSED", "_POINTER_MAX", "_INVALID_HANDLE", "_POINTER_TYPE",
            "_POINTER_VALUE", "_POINTER_VIEW", "_JOURNAL_RECEIPT", "_SLOT_RECEIPT", "ctypes", "weakref", "dataclass")),
        (safe, ("OwnedDirectoryCloseReceiptV1", "_OwnedDirectorySlot", "_OwnedDirectoryJournal",
            "_OWNED_DIRECTORY_SEAL", "_owned_directory_module", "_owned_directory_fail",
            "_owned_directory_clock", "_owned_directory_create_file", "_owned_directory_get_info",
            "_owned_directory_close_handle", "_owned_directory_bool", "_owned_directory_capture",
            "_owned_directory_raise", "_new_owned_directory_journal", "_owned_directory_journal",
            "_owned_directory_claim", "_owned_directory_begin", "_owned_directory_cleanup_clock",
            "_retire_owned_directory_slot", "_win_open_owned_directory", "_owned_directory_activated",
            "_owned_directory_exit", "_owned_directory_receipt", "_win_open", "_HandleInfo",
            "_kernel", "ctypes", "msvcrt", "_DIRECTORY_VERIFY_ORIGINAL")),
        (lifecycle, ("_OWNED_LOCK_SEAL", "_FILE_LOCK_VERIFY_ORIGINAL", "_FILE_LOCK_STAT_ORIGINAL",
            "_FILE_LOCK_ENTRY_ORIGINAL", "_DIRECTORY_ENTRY_ORIGINAL", "_LOCK_STREAM_TYPE",
            "_LOCK_STREAM_FILENO_ORIGINAL", "_LOCK_RAW_TYPE", "_LOCK_RAW_FILENO_ORIGINAL")),
        (owned, ("_WINDOWS", "_INVALID_HANDLE", "_NEW", "_OPENING", "_ACTIVE", "_RETIRED",
            "_EMPTY", "_OWNED", "_ATTEMPTED", "_CLOSED", "_UNKNOWN", "_TRANSFERRED",
            "_TRANSFER_PENDING", "_create_file", "_get_info", "_close_handle", "_open_osfhandle",
            "_monotonic", "_file_io", "_buffered_reader", "_close_fd")),
        (owned.io, ("FileIO", "BufferedReader")),
        (weakref, ("ref", "ReferenceType")),
        (safe.ctypes, ("byref", "c_void_p", "get_last_error", "WinError", "Structure",
            "sizeof", "Union", "Array", "c_long")),
        (owned.deadline.locked, ("_Budget", "MAX_SECONDS", "_fail")),
        (capture.images.preservation, ("_PersonalPrivateDirectory", "TempCleanupReceiptV1")),
        (capture.nodes.files, ("identity",)),
        (capture.nodes.original, ("_HeldFile", "_Budget", "_SourceStack", "_fail", "_same")),
        (capture.readonly, ("_PrivateDirectory", "MAX_BYTES_LIMIT", "DEFAULT_MAX_BYTES")),
        (capture.projection._contract, ("canonical_bytes", "PersonalPreservationError")),
        (enrollment.namespace, ("_preserve_primary",)),
        *module_rows(enrollment.store_runtime.file_io._catalog_extra_original()),
        # Fixed consumer exports also capture honest external wrappers through
        # their original closure; the graph keeps its original identity rules.
        (io_module, ("_new_file_io_graph", "_read_selected_exact", "_close_resource",
            "_retire_for_continuation", "_release_retired_graph", "_begin_file_cycle",
            "_open_held_reader", "_finalize_held_reader", "_file_snapshot",
            "_retirement_observation_for_continuation", "_release_observation_for_graph",
            "_flush_creator", "_creator_info")),
        (census_module, ("_scan", "_scan_final_generation", "_dispatch", "_signed32", "_retire_for_scope")),
        (directory_module, ("_begin_final_census_generation", "_run_final_census",
            "_retire_for_continuation", "_release_retired_graph", "_consume_selection",
            "_retirement_observation_for_continuation")),
        (runtime_module, ("_prepare_file_collection_owned", "_replace_cleanup_row", "_retirement_observed")),
        (io_module.custody, ("_release_retired_history", "_closed", "_returned_relative")),
        (io_module.native, ("_read_exact", "_read_chunk", "_native_close", "_native_free",
            "_relative_open", "_open_parent_raw", "_registered", "_publish_owned")),
        (io_module.chunks, ("_write_chunk",)),
        (owned.deadline, ("_RestrictedBudget",)),
        *module_rows(enrollment.store_runtime.directories._catalog_extra_original()),
        *module_rows(enrollment.store_runtime.directories.census_native._catalog_extra_original()),
        *module_rows(enrollment.store_runtime._initial_hook_catalog_extra_original()),
        (enrollment.store_runtime.directories.census_native, ("_SEAL", "_BUFFER_BYTES", "_INFORMATION_CLASS", "_NO_MORE_FILES", "_PENDING", "_STATUS_SENTINEL", "_INFORMATION_SENTINEL", "_ROLES", "_SCOPE_CLEANUP", "_SCAN_ROWS", "_QUERY_ROWS", "_QUERY_DEBTS", "_RETIRED_SCANS", "_RETIRED_SCOPES", "_RETIREMENT_ROOTS", "_QueryBuffer", "_DIR_OFFSETS", "_NAME_OFFSET", "_ARGTYPES", "_ntdll", "_query_directory")),
        (enrollment, ("custody",)),
        (enrollment.publication, ("custody",)),
        (enrollment.custody, ("_CURRENT", "_CONTEXT_VARIABLE", "ContextVar", "_SEAL", "_GROUP_JOURNALS",
            "_BUDGET_JOURNALS", "_OWNER_SLOTS", "_ROLES", "_HANDLE", "_LOCALALLOC",
            "_UNKNOWN", "_CLOSED", "_POINTER_MAX", "_INVALID_HANDLE", "_POINTER_TYPE",
            "_POINTER_VALUE", "_POINTER_VIEW", "_JOURNAL_RECEIPT", "_SLOT_RECEIPT", "ctypes", "weakref")),
        (enrollment.native, ("_custody", "_OWNER_DICT")),
        *((
            (safe.msvcrt, ("open_osfhandle", "get_osfhandle")),
            (safe._kernel, ("CreateFileW", "GetFileInformationByHandle", "CloseHandle",
                "SetFileInformationByHandle", "LockFileEx", "UnlockFileEx")),
            (safe, ("_WinRenameReplaceFlags", "_WinRenameName", "_WinRenameInfo",
                "_IoStatus", "_nt", "_rename_new", "_entry_name", "_win_open")),
            (safe._nt, ("NtSetInformationFile", "RtlNtStatusToDosError")),
            (lifecycle._kernel, ("LockFileEx", "UnlockFileEx")),
            (lifecycle, ("_Overlapped",)),
            (safe.wintypes, ("HANDLE", "BOOL", "DWORD", "FILETIME", "BOOLEAN", "WCHAR", "LPVOID", "LONG", "ULONG")),
            (enrollment.native, ("_kernel", "_advapi", "_ntdll", "w", "_IO_STATUS_STATUS_NATIVE")),
            (enrollment.native._kernel, ('CreateFileW', 'CloseHandle', 'LocalFree', 'GetCurrentProcess', 'GetFileInformationByHandle', 'ReadFile', 'WriteFile', 'FlushFileBuffers', 'SetFilePointerEx')),
            (enrollment.native._advapi, ('OpenProcessToken', 'GetTokenInformation', 'ConvertSidToStringSidW', 'ConvertStringSidToSidW', 'ConvertStringSecurityDescriptorToSecurityDescriptorW', 'GetSecurityInfo', 'GetSecurityDescriptorControl', 'IsValidSecurityDescriptor', 'IsValidSid', 'GetLengthSid', 'EqualSid', 'GetAclInformation', 'GetAce')),
            (enrollment.native._ntdll, ('NtCreateFile', 'NtSetInformationFile')),
            (enrollment.native.w, ("HANDLE", "LPVOID", "LPWSTR", "HLOCAL", "BOOL", "DWORD", "LONG")),
            (enrollment.native.ctypes, ("c_void_p", "sizeof", "cast", "byref", "POINTER")),
        ) if os.name == "nt" else ()),
    )


def _native_custody_functions_original():
    """Fixed existing namespace DLL functions, never caller/backend discovery."""
    if os.name != "nt":
        return ()
    from . import personal_authority_namespace3_io as namespace_native
    from . import personal_authority_enrollment_store_census_native as census_native
    return (
        namespace_native._kernel.CreateFileW,
        namespace_native._kernel.CloseHandle,
        namespace_native._kernel.LocalFree,
        namespace_native._kernel.GetCurrentProcess,
        namespace_native._kernel.GetFileInformationByHandle,
        namespace_native._kernel.ReadFile,
        namespace_native._kernel.WriteFile,
        namespace_native._kernel.FlushFileBuffers,
        namespace_native._kernel.SetFilePointerEx,
        namespace_native._advapi.OpenProcessToken,
        namespace_native._advapi.GetTokenInformation,
        namespace_native._advapi.ConvertSidToStringSidW,
        namespace_native._advapi.ConvertStringSidToSidW,
        namespace_native._advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW,
        namespace_native._advapi.GetSecurityInfo,
        namespace_native._advapi.GetSecurityDescriptorControl,
        namespace_native._advapi.IsValidSecurityDescriptor,
        namespace_native._advapi.IsValidSid,
        namespace_native._advapi.GetLengthSid,
        namespace_native._advapi.EqualSid,
        namespace_native._advapi.GetAclInformation,
        namespace_native._advapi.GetAce,
        namespace_native._ntdll.NtCreateFile,
        namespace_native._ntdll.NtSetInformationFile,
        census_native._query_directory,
    )
