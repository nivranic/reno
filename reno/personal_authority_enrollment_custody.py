"""Private opt-in namespace native facts; never source or writer authority.

Only the fixed namespace acquisition sites supply native outputs. An empty
context permits the legacy path only when this actual budget has no claim.
"""
from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
import ctypes
import weakref

_CURRENT = ContextVar("reno_enrollment_native_custody", default=None)
_CONTEXT_VARIABLE = _CURRENT
_SEAL = object()
_GROUP_JOURNALS = {}
_BUDGET_JOURNALS = {}
_OWNER_SLOTS = {}
_HANDLE, _LOCALALLOC = "HANDLE", "LOCALALLOC"
_UNKNOWN, _CLOSED = "UNKNOWN", "CLOSED"
_POINTER_MAX = (1 << (8 * ctypes.sizeof(ctypes.c_void_p))) - 1
_INVALID_HANDLE = ctypes.c_void_p(-1).value
_POINTER_TYPE = ctypes.c_void_p
_POINTER_VALUE = ctypes.c_void_p.value
_POINTER_VIEW = ctypes.c_void_p.from_buffer
_ROLES = (("token", _HANDLE), ("system_sid", _LOCALALLOC),
          ("sid_text", _LOCALALLOC), ("private_descriptor", _LOCALALLOC),
          ("security_descriptor", _LOCALALLOC), ("parent", _HANDLE), ("relative", _HANDLE))


class EnrollmentCustodyError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_CUSTODY_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise EnrollmentCustodyError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "ENROLLMENT_CUSTODY_ADDITIONAL_FAILURE")
    except BaseException:
        pass


@dataclass(frozen=True, slots=True)
class NativeOwnerCloseFactV1:
    format_version: int
    kind: str
    ordinal: int
    acquisition_attempted: bool
    native_acquired: bool
    close_attempted: bool
    native_state: str
    source_closed: bool
    write_authority: bool


@dataclass(frozen=True, slots=True)
class NativeOwnerJournalSnapshotV1:
    format_version: int
    kind: str
    scope: str
    facts: tuple
    registered_count: int
    unknown_count: int
    first_present: bool
    install_state: str
    reset_state: str
    all_registered_native_closed: bool
    acquisition_coverage_proven: bool
    source_closed: bool
    write_authority: bool


class _NativeOwnerJournal:
    __slots__ = ("_seal", "_group", "_budget", "_operation", "_parent", "_effective",
                 "_scope", "_guard", "_owner", "_claim", "_slots", "_pins", "_first",
                 "_first_tb", "_install_attempted", "_install_state", "_context_token",
                 "_reset_attempted", "_reset_state", "_seal_attempted", "_receipt", "_registry_pins",
                 "__weakref__")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    @property
    def close_receipt(self):
        return _JOURNAL_RECEIPT.__get__(self, _NativeOwnerJournal)


class _NativeSlot:
    __slots__ = ("_seal", "_journal", "_ordinal", "_kind", "_role", "_carrier", "_pins",
                 "_resource", "_return", "_known", "_representation_confirmed",
                 "_acquire_attempted", "_close_attempted", "_owner", "_owner_pins",
                 "_failure", "_receipt", "_unknown_acquire", "_owned_fact",
                 "_unknown_close", "_closed_fact", "_close_return", "_return_seen", "__weakref__")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    @property
    def close_receipt(self):
        return _SLOT_RECEIPT.__get__(self, _NativeSlot)


_JOURNAL_RECEIPT = _NativeOwnerJournal.__dict__["_receipt"]
_SLOT_RECEIPT = _NativeSlot.__dict__["_receipt"]


def _remember(journal, failure):
    if journal._first is None:
        journal._first = failure
        journal._first_tb = BaseException.__dict__["__traceback__"].__get__(failure)
    elif failure is not journal._first:
        _note(journal._first)


def _journal(value, *, live=False):
    if type(value) is not _NativeOwnerJournal or value._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    pins = value._pins
    row = dict.get(_GROUP_JOURNALS, id(value._group))
    if (type(pins) is not tuple or len(pins) != 11
            or type(row) is not tuple or len(row) != 3
            or type(row[0]) is not weakref.ReferenceType or type(row[1]) is not weakref.ReferenceType
            or row[0]() is not value._group or row[1]() is not value or row[2] is not value._claim
            or value._group is not pins[0] or value._budget is not pins[1]
            or value._operation is not pins[2] or value._parent is not pins[3]
            or value._effective is not pins[4] or value._scope is not pins[5]
            or value._guard is not pins[6] or value._owner is not pins[7]
            or value._slots is not pins[8] or type(value._slots) is not list
            or value._claim is not pins[9] or value._registry_pins is not pins[10]
            or type(value._registry_pins) is not tuple or len(value._registry_pins) != 2
            or row is not value._registry_pins[0]
            or dict.get(_GROUP_JOURNALS, id(value._budget)) is not row):
        _fail("INVALID_ARGUMENT")
    budget_row = dict.get(_BUDGET_JOURNALS, id(value._budget))
    if (type(budget_row) is not tuple or len(budget_row) != 3
            or type(budget_row[0]) is not weakref.ReferenceType
            or type(budget_row[1]) is not weakref.ReferenceType
            or budget_row[0]() is not value._budget or budget_row[1]() is not value
            or budget_row[2] is not value._claim or budget_row is not value._registry_pins[1]):
        _fail("INVALID_ARGUMENT")
    if live:
        from . import personal_authority_owned_ex_group as groups
        from . import personal_authority_enrollment as enrollment
        from . import storage_lifecycle as lifecycle
        budget = value._budget
        # Recheck the exact budget before any of its private getters.
        if type(budget) is not enrollment._Budget:
            _fail("INVALID_ARGUMENT")
        group = groups._registered(value._group)
        binding = group._binding
        if (group._phase != "active" or group._guard is not value._guard
                or lifecycle._CURRENT.get() is not value._guard
                or not groups._same_owner(value._owner, lifecycle._owner())
                or binding._operation is not value._operation
                or binding._parent_token is not value._parent
                or binding._effective_token is not value._effective
                or binding._scope_identity is not value._scope
                or budget._owned_ex is not group
                or budget._requested_deadline is not value._parent
                or budget._effective_deadline is not value._effective):
            _fail("INVALID_ARGUMENT")
    return value


def _new_journal(group, *, operation, parent_token, effective_token, scope_identity, budget):
    from . import personal_authority_owned_ex_group as groups
    from . import personal_authority_enrollment as enrollment
    from . import storage_lifecycle as lifecycle
    group = groups._registered(group)
    binding = group._binding
    if (type(budget) is not enrollment._Budget or type(scope_identity) is not object
            or group._phase != "active" or group._guard is not lifecycle._CURRENT.get()
            or operation is not binding._operation or parent_token is not binding._parent_token
            or effective_token is not binding._effective_token
            or scope_identity is not binding._scope_identity
            or budget._owned_ex is not group or budget._requested_deadline is not parent_token
            or budget._effective_deadline is not effective_token):
        _fail("INVALID_ARGUMENT")
    if (dict.get(_GROUP_JOURNALS, id(group)) is not None
            or dict.get(_GROUP_JOURNALS, id(budget)) is not None
            or dict.get(_BUDGET_JOURNALS, id(budget)) is not None):
        _fail("CLOSED")
    value = object.__new__(_NativeOwnerJournal)
    value._seal, value._group, value._budget = _SEAL, group, budget
    value._operation, value._parent, value._effective = operation, parent_token, effective_token
    value._scope, value._guard, value._owner = scope_identity, group._guard, lifecycle._owner()
    value._claim, value._slots = object(), []
    value._first = value._first_tb = value._context_token = value._receipt = None
    value._install_attempted = value._reset_attempted = value._seal_attempted = False
    value._install_state = value._reset_state = "not_started"
    value._pins = (group, budget, operation, parent_token, effective_token, scope_identity,
                   value._guard, value._owner, value._slots, value._claim)
    key, budget_key = id(group), id(budget)
    def budget_collected(reference, *, budget_key=budget_key):
        current_budget = dict.get(_BUDGET_JOURNALS, budget_key)
        if (type(current_budget) is not tuple or len(current_budget) != 3
                or current_budget[0] is not reference or current_budget[1] is not journal_ref
                or current_budget[2] is not claim):
            return
        current_index = dict.get(_GROUP_JOURNALS, budget_key)
        if (type(current_index) is tuple and len(current_index) == 3
                and current_index[0] is group_ref and current_index[1] is journal_ref
                and current_index[2] is claim):
            dict.pop(_GROUP_JOURNALS, budget_key, None)
        if dict.get(_BUDGET_JOURNALS, budget_key) is current_budget:
            dict.pop(_BUDGET_JOURNALS, budget_key, None)
    budget_ref = weakref.ref(budget, budget_collected)
    claim = value._claim
    def collected(reference, *, key=key, budget_key=budget_key, budget_ref=budget_ref, claim=claim):
        current = dict.get(_GROUP_JOURNALS, key)
        if (type(current) is tuple and len(current) == 3
                and current[0] is reference and current[2] is claim):
            dict.pop(_GROUP_JOURNALS, key, None)
        current_index = dict.get(_GROUP_JOURNALS, budget_key)
        if (type(current_index) is tuple and len(current_index) == 3
                and current_index[0] is reference and current_index[2] is claim):
            dict.pop(_GROUP_JOURNALS, budget_key, None)
        current_budget = dict.get(_BUDGET_JOURNALS, budget_key)
        if (type(current_budget) is tuple and len(current_budget) == 3
                and current_budget[0] is budget_ref and current_budget[2] is claim):
            dict.pop(_BUDGET_JOURNALS, budget_key, None)
    group_ref, journal_ref = weakref.ref(group, collected), weakref.ref(value)
    group_row = (group_ref, journal_ref, claim)
    budget_row = (budget_ref, journal_ref, claim)
    value._registry_pins = (group_row, budget_row)
    value._pins = value._pins + (value._registry_pins,)
    # Both direct group-table keys name the same minted claim row. Distinct
    # simultaneously alive group/budget identities cannot have the same id.
    try:
        _GROUP_JOURNALS[key] = group_row
        _GROUP_JOURNALS[budget_key] = group_row
        _BUDGET_JOURNALS[budget_key] = budget_row
    except BaseException:
        if dict.get(_BUDGET_JOURNALS, budget_key) is budget_row:
            dict.pop(_BUDGET_JOURNALS, budget_key, None)
        if dict.get(_GROUP_JOURNALS, budget_key) is group_row:
            dict.pop(_GROUP_JOURNALS, budget_key, None)
        if dict.get(_GROUP_JOURNALS, key) is group_row:
            dict.pop(_GROUP_JOURNALS, key, None)
        raise
    return value


def _install(journal):
    if _CURRENT is not _CONTEXT_VARIABLE or type(_CURRENT) is not ContextVar:
        _fail("INVALID_ARGUMENT")
    journal = _journal(journal, live=True)
    if journal._install_attempted or journal._seal_attempted or _CURRENT.get() is not None:
        _fail("CLOSED")
    journal._install_attempted = True
    journal._install_state = "UNKNOWN"
    try:
        journal._context_token = _CURRENT.set(journal)
    except BaseException as failure:
        _remember(journal, failure)
        raise
    journal._install_state = "installed"


def _reset(journal):
    if _CURRENT is not _CONTEXT_VARIABLE or type(_CURRENT) is not ContextVar:
        _fail("INVALID_ARGUMENT")
    journal = _journal(journal)
    if journal._reset_attempted:
        _fail("CLOSED")
    journal._reset_attempted = True
    journal._reset_state = "UNKNOWN"
    try:
        if journal._install_state != "installed" or _CURRENT.get() is not journal:
            _fail("INVALID_ARGUMENT")
        _CURRENT.reset(journal._context_token)
    except BaseException as failure:
        _remember(journal, failure)
        raise
    journal._reset_state = "reset"


def _active_journal_for_budget(budget):
    """Borrow this original active claim without reserving another native slot."""
    if _CURRENT is not _CONTEXT_VARIABLE or type(_CURRENT) is not ContextVar:
        _fail("INVALID_ARGUMENT")
    journal = _CURRENT.get()
    claimed = dict.get(_BUDGET_JOURNALS, id(budget))
    group_claim = dict.get(_GROUP_JOURNALS, id(budget))
    if journal is None and claimed is None and group_claim is None:
        return None
    from . import personal_authority_enrollment as enrollment
    if type(budget) is not enrollment._Budget:
        _fail("INVALID_ARGUMENT")
    if (type(journal) is not _NativeOwnerJournal
            or type(claimed) is not tuple or len(claimed) != 3
            or type(group_claim) is not tuple or len(group_claim) != 3
            or type(claimed[0]) is not weakref.ReferenceType
            or type(claimed[1]) is not weakref.ReferenceType
            or type(group_claim[0]) is not weakref.ReferenceType
            or type(group_claim[1]) is not weakref.ReferenceType
            or claimed[0]() is not budget or claimed[1]() is not journal
            or group_claim[1]() is not journal or budget is not journal._budget
            or group_claim[0]() is not journal._group
            or claimed[2] is not journal._claim or group_claim[2] is not journal._claim):
        _fail("CURRENT_JOURNAL_UNKNOWN")
    journal = _journal(journal, live=True)
    if (claimed is not journal._registry_pins[1] or group_claim is not journal._registry_pins[0]
            or journal._install_state != "installed" or journal._reset_attempted
            or journal._seal_attempted):
        _fail("INVALID_ARGUMENT")
    return journal


def _remember_owner_primary(owner, journal, primary):
    """Record an observed first on an existing owner, never close or reacquire."""
    journal = _journal(journal)
    slot = _find_owner_slot(owner)
    if slot is None or slot._journal is not journal:
        _fail("INVALID_ARGUMENT")
    _owner_slot(owner, slot)
    _failed(slot, primary)


def _restore_installation(journal):
    """Restore only this original context; unknown token delivery stays unknown."""
    journal = _journal(journal)
    if _CURRENT is not _CONTEXT_VARIABLE or type(_CURRENT) is not ContextVar:
        _fail("INVALID_ARGUMENT")
    if not journal._install_attempted:
        return
    if journal._reset_attempted:
        _fail("CLOSED")
    if journal._install_state == "installed":
        try:
            _reset(journal)
        except BaseException as failure:
            journal._reset_attempted = True
            journal._reset_state = "UNKNOWN"
            _remember(journal, failure)
            raise
        return
    journal._reset_attempted = True
    journal._reset_state = "UNKNOWN"
    try:
        if journal._install_state != "UNKNOWN":
            _fail("INVALID_ARGUMENT")
        current = _CURRENT.get()
        if current is None:
            return
        if current is not journal:
            _fail("CURRENT_JOURNAL_UNKNOWN")
        # Install required the original current to be None. This replacement
        # token is not the missing original token and cannot prove reset.
        _CURRENT.set(None)
        if _CURRENT.get() is not None:
            _fail("CURRENT_JOURNAL_UNKNOWN")
    except BaseException as failure:
        _remember(journal, failure)
        raise


def _finish_journal(journal, *, primary=None):
    """Try context recovery and seal separately; preserve observed native facts."""
    journal = _journal(journal)
    if journal._seal_attempted:
        _fail("CLOSED")
    first = journal._first
    if first is None and primary is not None:
        first = primary
    try:
        if primary is not None:
            _remember(journal, primary)
    except BaseException as failure:
        if first is None:
            first = failure
        elif failure is not first:
            _note(first)
    try:
        _restore_installation(journal)
    except BaseException as failure:
        # A call-layer exception after actual reset cannot be reported as a
        # normal reset delivery. This never changes an actual native CLOSED.
        if journal._install_attempted:
            journal._reset_attempted = True
            journal._reset_state = "UNKNOWN"
        if first is None:
            first = journal._first
            if first is None:
                first = failure
        elif failure is not first:
            _note(first)
    try:
        snapshot = _seal(journal, primary=first)
        if (type(snapshot) is not NativeOwnerJournalSnapshotV1
                or snapshot is not _JOURNAL_RECEIPT.__get__(journal, _NativeOwnerJournal)):
            _fail("INVALID_RESULT")
    except BaseException as failure:
        journal._seal_attempted = True
        if first is None:
            first = journal._first
            if first is None:
                first = failure
        elif failure is not first:
            _note(first)
    if first is not None:
        raise first
    return snapshot


def _slot(value):
    if type(value) is not _NativeSlot or value._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    journal = _journal(value._journal)
    pins = value._pins
    if (type(pins) is not tuple or len(pins) != 5
            or value._journal is not pins[0] or type(value._ordinal) is not int
            or value._ordinal != pins[1] or not 0 <= value._ordinal < len(journal._slots)
            or journal._slots[value._ordinal] is not value
            or value._kind is not pins[2] or value._role is not pins[3]
            or value._carrier is not pins[4]):
        _fail("INVALID_ARGUMENT")
    return value


def _reserve(budget, role, carrier):
    if _CURRENT is not _CONTEXT_VARIABLE or type(_CURRENT) is not ContextVar:
        _fail("INVALID_ARGUMENT")
    journal = _CURRENT.get()
    claimed = dict.get(_BUDGET_JOURNALS, id(budget))
    group_claim = dict.get(_GROUP_JOURNALS, id(budget))
    if group_claim is not None:
        from . import personal_authority_enrollment as enrollment
        # This independent direct claim prevents a removed budget-table row
        # plus cleared context from becoming the unclaimed default path.
        if type(budget) is not enrollment._Budget:
            _fail("INVALID_ARGUMENT")
        if (type(group_claim) is not tuple or len(group_claim) != 3
                or type(group_claim[0]) is not weakref.ReferenceType
                or type(group_claim[1]) is not weakref.ReferenceType
                or journal is None or type(journal) is not _NativeOwnerJournal
                or group_claim[1]() is not journal or budget is not journal._budget
                or group_claim[0]() is not journal._group
                or group_claim[2] is not journal._claim
                or dict.get(_GROUP_JOURNALS, id(journal._group)) is not group_claim
                or claimed is None):
            _fail("CURRENT_JOURNAL_UNKNOWN")
    if claimed is not None:
        if (type(claimed) is not tuple or len(claimed) != 3
                or type(claimed[0]) is not weakref.ReferenceType
                or type(claimed[1]) is not weakref.ReferenceType
                or claimed[0]() is not budget or claimed[1]() is not journal
                or journal is None or type(journal) is not _NativeOwnerJournal
                or claimed[2] is not journal._claim):
            _fail("CURRENT_JOURNAL_UNKNOWN")
    elif journal is None:
        return None
    journal = _journal(journal, live=True)
    if (budget is not journal._budget or journal._install_state != "installed"
            or journal._reset_attempted or journal._seal_attempted
            or type(role) is not str or not any(role == name for name, _kind in _ROLES)):
        _fail("INVALID_ARGUMENT")
    from . import personal_authority_namespace3_io as native
    if role == "sid_text":
        if type(carrier) is not native.w.LPWSTR:
            _fail("INVALID_ARGUMENT")
        # Prepare the scalar pointer view before the API writes this storage.
        carrier = _POINTER_VIEW(carrier)
    elif role == "parent":
        if carrier is not None:
            _fail("INVALID_ARGUMENT")
    elif type(carrier) is not native.w.LPVOID:
        _fail("INVALID_ARGUMENT")
    if carrier is not None and _POINTER_VALUE.__get__(carrier, _POINTER_TYPE) is not None:
        _fail("OUTPUT_ALREADY_POPULATED")
    value = object.__new__(_NativeSlot)
    value._seal, value._journal = _SEAL, journal
    value._ordinal, value._kind, value._role = len(journal._slots), next(kind for name, kind in _ROLES if name == role), role
    value._carrier, value._resource = carrier, None
    value._return = value._owner = value._owner_pins = value._failure = value._close_return = None
    value._known = value._representation_confirmed = value._close_attempted = value._return_seen = False
    value._acquire_attempted = True
    value._unknown_acquire = NativeOwnerCloseFactV1(1, value._kind, value._ordinal, True, False, False, _UNKNOWN, False, False)
    value._owned_fact = NativeOwnerCloseFactV1(1, value._kind, value._ordinal, True, True, False, _UNKNOWN, False, False)
    value._unknown_close = NativeOwnerCloseFactV1(1, value._kind, value._ordinal, True, True, True, _UNKNOWN, False, False)
    value._closed_fact = NativeOwnerCloseFactV1(1, value._kind, value._ordinal, True, True, True, _CLOSED, False, False)
    value._receipt = value._unknown_acquire
    value._pins = (journal, value._ordinal, value._kind, role, carrier)
    # Allocation/append must complete before any native acquisition.
    journal._slots.append(value)
    return value


def _signed32(value):
    return type(value) is int and -(1 << 31) <= value < (1 << 31)


def _uint32(value):
    return type(value) is int and 0 <= value < (1 << 32)


def _pointer(value, kind):
    return (type(value) is int and 0 < value <= _POINTER_MAX
            and (kind != _HANDLE or value != _INVALID_HANDLE))


def _adopt_output(slot):
    # Successful fixed native acquisition owns its actual output storage first.
    slot._resource, slot._known = slot._carrier, True
    slot._receipt = slot._owned_fact
    try:
        resource = _POINTER_VALUE.__get__(slot._carrier, _POINTER_TYPE)
    except BaseException:
        # The native success was observed; cleanup uses this same output storage.
        # Its representation remains unknown and cannot produce a CLOSED fact.
        raise
    if not _pointer(resource, slot._kind):
        slot._known = False
        slot._resource = None
        slot._receipt = slot._unknown_acquire
        _fail("NATIVE_OUTPUT_UNKNOWN")
    slot._resource, slot._representation_confirmed = resource, True


def _mark_return(slot, raw):
    if type(slot) is not _NativeSlot or slot._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    if slot._return_seen or slot._failure is not None or slot._close_attempted:
        _fail("CLOSED")
    slot._return_seen = True
    slot._return = raw
    return slot


def _returned_bool(slot, raw):
    if slot is None:
        return
    slot = _mark_return(slot, raw)
    if slot._known or slot._close_attempted or not _signed32(raw):
        _fail("NATIVE_RESULT_UNKNOWN")
    if raw != 0:
        _adopt_output(slot)


def _returned_status(slot, raw):
    if slot is None:
        return
    slot = _mark_return(slot, raw)
    if slot._known or slot._close_attempted or not _uint32(raw):
        _fail("NATIVE_RESULT_UNKNOWN")
    if raw == 0:
        _adopt_output(slot)


def _returned_handle(slot, raw):
    if slot is None:
        return
    slot = _mark_return(slot, raw)
    if slot._known or slot._close_attempted:
        _fail("CLOSED")
    if _pointer(raw, _HANDLE):
        slot._resource, slot._known, slot._representation_confirmed = raw, True, True
        slot._receipt = slot._owned_fact
    elif raw is not None and raw != _INVALID_HANDLE:
        _fail("NATIVE_OUTPUT_UNKNOWN")


def _returned_relative(slot, raw, status):
    if slot is None:
        return
    slot = _mark_return(slot, raw)
    if slot._known or slot._close_attempted or not _signed32(raw):
        _fail("NATIVE_RESULT_UNKNOWN")
    if raw != 0:
        return
    # Pending/nonzero/unreadable IO status cannot confirm a residual HANDLE.
    from . import personal_authority_namespace3_io as native
    if type(status) is not native._IoStatus:
        _fail("INVALID_ARGUMENT")
    completion = native._IO_STATUS_STATUS_NATIVE.__get__(status, native._IoStatus)
    if not _signed32(completion):
        _fail("NATIVE_RESULT_UNKNOWN")
    if completion == 0:
        _adopt_output(slot)


def _bind_owner(slot, owner, value, closer):
    if slot is None:
        return
    slot = _slot(slot)
    from . import personal_authority_namespace3_io as native
    expected = native._native_close if slot._kind == _HANDLE else native._native_free
    if (type(owner) is not native._Owner or closer is not expected or not slot._known
            or not slot._representation_confirmed or slot._close_attempted
            or slot._owner is not None or type(value) is not int or value != slot._resource):
        _fail("INVALID_ARGUMENT")
    key = id(owner)
    if dict.get(_OWNER_SLOTS, key) is not None:
        _fail("CLOSED")
    def collected(reference, *, key=key):
        row = dict.get(_OWNER_SLOTS, key)
        if type(row) is tuple and row[0] is reference:
            dict.pop(_OWNER_SLOTS, key, None)
    owner_ref, slot_ref = weakref.ref(owner, collected), weakref.ref(slot)
    row = (owner_ref, slot_ref)
    slot._owner_pins = (owner, value, closer, row)
    slot._owner = owner
    _OWNER_SLOTS[key] = row


def _begin_close(slot, kind):
    slot = _slot(slot)
    if slot._close_attempted:
        if slot._failure is not None:
            raise slot._failure
        return None
    if not slot._known:
        return None
    # Mark once before the native call or any further validation/allocation.
    slot._close_attempted = True
    slot._receipt = slot._unknown_close
    if slot._kind != kind or slot._resource is None:
        _fail("INVALID_ARGUMENT")
    return slot._resource


def _closed(slot, raw):
    slot = _slot(slot)
    slot._close_return = raw
    if not slot._close_attempted or not slot._known:
        _fail("INVALID_ARGUMENT")
    legal_success = (_signed32(raw) and raw != 0) if slot._kind == _HANDLE else raw is None
    if legal_success and slot._representation_confirmed:
        slot._receipt = slot._closed_fact
    return legal_success


def _failed(slot, failure):
    if slot is None:
        return
    slot = _slot(slot)
    if slot._failure is None:
        slot._failure = failure
    _remember(slot._journal, failure)


def _find_owner_slot(owner):
    from . import personal_authority_namespace3_io as native
    fields = native._OWNER_DICT.__get__(owner, native._Owner)
    row = dict.get(_OWNER_SLOTS, id(owner))
    if row is None:
        # A bound marker with missing registry custody is corrupt, not legacy.
        # Reject without consulting mutable value/closer or calling native.
        if dict.__contains__(fields, "_custody_slot"):
            _fail("INVALID_ARGUMENT")
        return None
    if (type(row) is not tuple or len(row) != 2
            or type(row[0]) is not weakref.ReferenceType or type(row[1]) is not weakref.ReferenceType
            or row[0]() is not owner or row[1]() is None):
        _fail("INVALID_ARGUMENT")
    slot = _slot(row[1]())
    if (type(slot._owner_pins) is not tuple or len(slot._owner_pins) != 4
            or slot._owner_pins[3] is not row):
        _fail("INVALID_ARGUMENT")
    return slot


def _owner_slot(owner, slot):
    slot = _slot(slot)
    from . import personal_authority_namespace3_io as native
    pins = slot._owner_pins
    if type(owner) is not native._Owner or type(pins) is not tuple or len(pins) != 4:
        _fail("INVALID_ARGUMENT")
    fields = native._OWNER_DICT.__get__(owner, native._Owner)
    if (pins[0] is not owner or slot._owner is not owner
            or dict.get(fields, "_custody_slot") is not slot
            or type(dict.get(fields, "value")) is not int or dict.get(fields, "value") != pins[1]
            or dict.get(fields, "closer") is not pins[2]
            or dict.get(_OWNER_SLOTS, id(owner)) is not pins[3]):
        _fail("INVALID_ARGUMENT")
    return slot


def _seal(journal, *, primary=None):
    journal = _journal(journal)
    if journal._seal_attempted:
        _fail("CLOSED")
    journal._seal_attempted = True
    try:
        if primary is not None:
            _remember(journal, primary)
        facts = []
        for slot in journal._slots:
            slot = _slot(slot)
            receipt = slot._receipt
            if (type(receipt) is not NativeOwnerCloseFactV1
                    or not any(receipt is fact for fact in (slot._unknown_acquire, slot._owned_fact,
                                       slot._unknown_close, slot._closed_fact))):
                _fail("INVALID_RESULT")
            if receipt is slot._closed_fact:
                legal = (_signed32(slot._close_return) and slot._close_return != 0) if slot._kind == _HANDLE else slot._close_return is None
                if (slot._close_attempted is not True or slot._known is not True
                        or slot._representation_confirmed is not True or slot._return_seen is not True
                        or not legal):
                    _fail("INVALID_RESULT")
            facts.append(receipt)
        facts = tuple(facts)
        unknown = sum(f.native_state != _CLOSED for f in facts)
        journal._receipt = NativeOwnerJournalSnapshotV1(1, "namespace_native_owner_journal",
            "namespace3_native_obligations_only", facts, len(facts), unknown,
            journal._first is not None, journal._install_state, journal._reset_state,
            bool(facts) and unknown == 0 and journal._first is None
            and journal._install_state == "installed" and journal._reset_state == "reset",
            False, False, False)
    except BaseException as failure:
        _remember(journal, failure)
        raise
    return journal._receipt
