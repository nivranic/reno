"""Private Store-stage continuation and lifetime, never a prepared Store.

The sole factory owns the actual packet call/return. No caller bytes, callback,
equivalent DTO, second packet consumption, or fresh budget enters this module.
"""
from __future__ import annotations

from dataclasses import dataclass
import weakref

from . import personal_authority_enrollment_packet as packet
from . import personal_authority_enrollment_physical as physical
from . import personal_authority_enrollment_custody as source_custody
from . import personal_authority_owned_ex_group as groups
from . import personal_authority_namespace3_deadline as deadline
from . import storage_lifecycle as lifecycle
from . import personal_authority_enrollment_store_selections as selections
from . import personal_authority_enrollment_store_directories as directories
from . import personal_authority_enrollment_store_io as file_io

_SEAL = object()
_CONTINUATIONS = {}
_SCOPE_ROWS = {}
_CLOSE_RECEIPTS = {}
_STAGES_BY_CONTINUATION = {}
_RETAINED_RETIREMENTS = {}
_INITIAL_ROUTES = {}
_INITIAL_ROUTE_VALUES = {}
_INITIAL_HOOK_ATTEMPTS = {}
_INITIAL_HOOKS = {}
_INITIAL_HOOK_METADATA = {}
_INITIAL_ROUTE_OWNERS = {}
_INITIAL_FACTORY_ROWS = {}
_INITIAL_FACTORY_OWNERS = {}
_INITIAL_FACTORY_ATTEMPTS = {}
_INITIAL_CALLBACK_PINS = {}


class StoreStageError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_STORE_STAGE_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise StoreStageError(suffix) from None


def _note(first):
    try:
        BaseException.add_note(first, "ENROLLMENT_STORE_STAGE_ADDITIONAL_FAILURE")
    except BaseException:
        pass


class _StoreContinuation:
    __slots__ = ("_seal", "_claim", "_row", "_slot", "_carrier", "_payload", "_raws",
                 "_consumer", "_precondition", "_result", "_source_journal", "_source_snapshot",
                 "_group", "_guard", "_operation", "_budget", "_parent", "_effective",
                 "_source_scope", "_stage_scope", "_profile", "_paths", "_owner", "_captures",
                 "_capture_receipts", "_pins", "_phase", "_attempted", "_journal", "_stage",
                 "_receipt", "_first", "_first_tb", "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


_PIN_NAMES = ("_slot", "_carrier", "_payload", "_raws", "_consumer", "_precondition", "_result",
              "_source_journal", "_source_snapshot", "_group", "_guard", "_operation", "_budget",
              "_parent", "_effective", "_source_scope", "_stage_scope", "_profile", "_paths",
              "_owner", "_captures", "_capture_receipts")


def _minimal(value):
    # This recognition does not inspect source lineage, call a clock, or consume.
    if type(value) is not _StoreContinuation or value._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    row = value._row
    if (type(row) is not tuple or len(row) != 4
            or type(row[0]) is not weakref.ReferenceType or type(row[2]) is not weakref.ReferenceType
            or row[1] is not value._claim or row[2]() is not value
            or dict.get(_CONTINUATIONS, row[3]) is not row):
        _fail("INVALID_ARGUMENT")
    return value


def _remember(value, primary):
    if value._first is None:
        value._first = primary
        value._first_tb = BaseException.__dict__["__traceback__"].__get__(primary)
    elif primary is not value._first:
        _note(value._first)


def _continuation(value, *, live=False):
    value = _minimal(value)
    # A recorded first precedes fallible business pins, never trusted cleanup.
    if live and value._first is not None:
        raise BaseException.with_traceback(value._first, value._first_tb)
    if value._phase in ("retired_closed", "retired_uncompleted"):
        _fail("CLOSED")
    pins = value._pins
    if (type(pins) is not tuple or len(pins) != len(_PIN_NAMES)
            or any(getattr(value, name) is not prior for name, prior in zip(_PIN_NAMES, pins))
            or value._row[0]() is not value._slot):
        _fail("INVALID_ARGUMENT")
    if not live:
        return value
    from . import personal_authority_enrollment as enrollment
    if type(value._budget) is not enrollment._Budget:
        _fail("INVALID_ARGUMENT")
    carrier = physical._carrier(value._carrier)
    source = source_custody._journal(value._source_journal, live=True)
    group = groups._registered(value._group)
    receipt = value._precondition
    snapshot = value._source_snapshot
    if (type(value._consumer) is not physical._BorrowedSourceConsumer
            or type(value._profile) is not lifecycle.StorageProfile
            or type(snapshot) is not source_custody.NativeOwnerJournalSnapshotV1
            or snapshot.all_registered_native_closed is not True or snapshot.unknown_count != 0
            or snapshot.first_present is not False or snapshot.install_state != "installed"
            or snapshot.reset_state != "reset" or snapshot.source_closed is not False
            or snapshot.write_authority is not False or snapshot.acquisition_coverage_proven is not False
            or len(snapshot.facts) != len(source._slots)
            or any(fact is not slot._receipt for fact, slot in zip(snapshot.facts, source._slots))
            or packet._registered_slot(value._slot) is not carrier
            or value._slot._consume_attempted is not True or value._slot._payload is not value._payload
            or carrier._journal is not source or carrier._group is not group
            or carrier._guard is not value._guard or carrier._consumer is not value._consumer
            or carrier._receipt is not receipt or carrier._result is not value._result
            or carrier._phase != "borrowed_precondition_issued" or carrier._first is not None
            or value._consumer._result is not value._result or value._consumer._receipt is not receipt
            or value._consumer._attempted is not True
            or type(receipt) is not physical.BorrowedEnrollmentSourcePreconditionV1
            or source_custody._JOURNAL_RECEIPT.__get__(source, source_custody._NativeOwnerJournal) is not value._source_snapshot
            or receipt.native_snapshot is not value._source_snapshot
            or source._reset_attempted is not True or source._reset_state != "reset"
            or source._seal_attempted is not True or source._first is not None
            or source_custody._CURRENT is not source_custody._CONTEXT_VARIABLE
            or source_custody._CURRENT.get() is not None
            or group._phase != "active" or group._guard is not value._guard
            or lifecycle._CURRENT.get() is not value._guard
            or not groups._same_owner(value._owner, lifecycle._owner())
            or group._binding._operation is not value._operation
            or group._binding._parent_token is not value._parent
            or group._binding._effective_token is not value._effective
            or group._binding._scope_identity is not value._source_scope
            or value._budget._owned_ex is not group
            or value._budget._requested_deadline is not value._parent
            or value._budget._effective_deadline is not value._effective
            or tuple(value._profile.identity) != value._paths
            or receipt.outer_ex_borrowed is not True
            or receipt.registered_enrollment_sources_terminal_complete is not True
            or any(flag is not False for flag in (receipt.outer_ex_closed, receipt.acquisition_coverage_proven,
                                                 receipt.source_closed, receipt.write_authority))):
        _fail("SOURCE_BINDING_INVALID")
    if (type(value._raws) is not tuple or len(value._raws) != 11
            or any(a is not b for a, b in zip(packet._payload_values(value._slot), value._raws))
            or type(value._captures) is not tuple or len(value._captures) != 2
            or type(value._capture_receipts) is not tuple or len(value._capture_receipts) != 2
            or len(carrier._captures) != 2 or len(receipt.capture_receipts) != 2):
        _fail("SOURCE_BINDING_INVALID")
    for ordinal, capture in enumerate(value._captures):
        if (carrier._captures[ordinal] is not capture or carrier._capture_returns[ordinal] is not capture
                or capture._receipt is not value._capture_receipts[ordinal]
                or receipt.capture_receipts[ordinal] is not value._capture_receipts[ordinal]):
            _fail("SOURCE_BINDING_INVALID")
        groups._capture_registered(capture)
        physical._capture_source_facts_complete(carrier, capture, capture._receipt)
    deadline._token(value._parent)
    deadline._token(value._effective)
    return value


def _new_prepare_continuation(slot, *, source_consumer, source_precondition, result):
    """Fixed enrollment hook: call the original packet consumer exactly once."""
    carrier = packet._registered_slot(slot)
    key = id(slot)
    if dict.get(_CONTINUATIONS, key) is not None:
        _fail("CLOSED")
    claim = object()
    def collected(reference, *, key=key, claim=claim):
        row = dict.get(_CONTINUATIONS, key)
        if type(row) is tuple and len(row) == 4 and row[0] is reference and row[1] is claim:
            dict.pop(_CONTINUATIONS, key, None)
    reference = weakref.ref(slot, collected)
    # A failed attempt remains claimed while the actual slot lives.
    attempted = (reference, claim, None, key)
    _CONTINUATIONS[key] = attempted
    payload = packet._consume_locked(slot, source_consumer=source_consumer,
        source_precondition=source_precondition, result=result)
    if type(payload) is not packet._PacketBytes or payload is not slot._payload:
        _fail("PACKET_RETURN_INVALID")
    physical._lineage(carrier, installed=False)
    value = object.__new__(_StoreContinuation)
    value._seal, value._claim, value._slot, value._carrier = _SEAL, claim, slot, carrier
    value._payload, value._raws = payload, packet._payload_values(slot)
    value._consumer, value._precondition, value._result = source_consumer, source_precondition, result
    value._source_journal, value._source_snapshot = carrier._journal, source_precondition.native_snapshot
    value._group, value._guard, value._operation, value._budget = carrier._group, carrier._guard, carrier._operation, carrier._budget
    value._parent, value._effective, value._source_scope = carrier._parent, carrier._effective, carrier._scope
    value._stage_scope = object()
    value._profile, value._paths, value._owner = carrier._profile, carrier._paths, carrier._owner
    value._captures, value._capture_receipts = tuple(carrier._captures), tuple(carrier._capture_receipts)
    value._pins = tuple(getattr(value, name) for name in _PIN_NAMES)
    value._phase, value._attempted = "available", False
    value._journal = value._stage = value._receipt = value._first = value._first_tb = None
    value._row = (reference, claim, weakref.ref(value), key)
    if dict.get(_CONTINUATIONS, key) is not attempted:
        _fail("REGISTRY_CHANGED")
    _CONTINUATIONS[key] = value._row
    try:
        _continuation(value, live=True)
        value._budget.clock_only()
    except BaseException as primary:
        _remember(value, primary)
        _retire_continuation(value, primary=primary)
        raise
    return value


@dataclass(frozen=True, slots=True)
class _StorePrepareCloseReceipt:
    format_version: int
    kind: str
    native_snapshot: object
    registered_native_count: int
    all_registered_native_closed: bool
    context_reset: bool
    journal_sealed: bool
    source_snapshot_unchanged: bool
    outer_ex_borrowed: bool
    source_closed: bool
    write_authority: bool


class _StorePrepareStage:
    __slots__ = ("_seal", "_continuation", "_claim", "_pins", "_enter_attempted", "_exit_attempted",
                 "_entered", "_receipt", "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __enter__(self):
        from . import personal_authority_enrollment_store_custody as custody
        value = _stage(self)
        if self._enter_attempted:
            _fail("CLOSED")
        self._enter_attempted = True
        try:
            _continuation(value, live=True)
            value._budget.clock_only()
            journal = custody._new_store_prepare_journal(value)
            # Keep the actual return independently of mutable business fields.
            _bind_cleanup_journal(self, journal)
            value._journal = journal
            custody._install(journal)
            value._phase = "installed"
            value._budget.clock_only()
            self._entered = True
            return journal
        except BaseException as primary:
            _remember(value, primary)
            _finish_stage(self, primary=primary)
            raise

    def __exit__(self, kind, primary, traceback):
        _finish_stage(self, primary=primary)
        return False


def _cleanup_scope(scope):
    """Authenticate registered cleanup identity without any business getter."""
    if type(scope) is not _StorePrepareStage or scope._seal is not _SEAL:
        _fail("INVALID_ARGUMENT")
    row = dict.get(_SCOPE_ROWS, id(scope))
    if (type(row) is not tuple or len(row) != 8 or type(row[0]) is not weakref.ReferenceType
            or type(row[2]) is not weakref.ReferenceType
            or row[0]() is not scope or row[1] is not scope._claim):
        _fail("INVALID_ARGUMENT")
    value = _minimal(row[2]())
    binding = dict.get(_STAGES_BY_CONTINUATION, id(value))
    if (type(binding) is not tuple or len(binding) != 3
            or type(binding[0]) is not weakref.ReferenceType
            or binding[0]() is not value or binding[1] is not value._claim
            or binding[2] is not row[0]):
        _fail("INVALID_ARGUMENT")
    return value, row


def _replace_cleanup_row(scope, before, after):
    if dict.get(_SCOPE_ROWS, id(scope)) is not before:
        _fail("REGISTRY_CHANGED")
    _SCOPE_ROWS[id(scope)] = after


def _bind_cleanup_journal(scope, journal):
    from . import personal_authority_enrollment_store_custody as custody
    value, row = _cleanup_scope(scope)
    if (type(journal) is not custody._StorePrepareJournal or row[4] is not None
            or row[5] is not False or journal._continuation is not value):
        _fail("INVALID_ARGUMENT")
    _replace_cleanup_row(scope, row, row[:4] + (journal,) + row[5:])


def _cleanup_binding(continuation, journal):
    """Return original factory pins only for this registered actual journal."""
    value = _minimal(continuation)
    binding = dict.get(_STAGES_BY_CONTINUATION, id(value))
    if type(binding) is not tuple or len(binding) != 3 or type(binding[2]) is not weakref.ReferenceType:
        _fail("INVALID_ARGUMENT")
    actual, row = _cleanup_scope(binding[2]())
    if (actual is not value or row[4] is not journal
            or type(row[3]) is not tuple or len(row[3]) != len(_PIN_NAMES)):
        _fail("INVALID_ARGUMENT")
    return row[3]


def _record_stage_first(scope, primary):
    value, row = _cleanup_scope(scope)
    if row[6] is None:
        first, traceback = value._first, value._first_tb
        if first is None:
            first = primary
            traceback = BaseException.__dict__["__traceback__"].__get__(primary)
        elif primary is not first:
            _note(first)
        _replace_cleanup_row(scope, row, row[:6] + (first, traceback))
        value._first, value._first_tb = first, traceback
    elif row[6] is not primary:
        _note(row[6])


def _stage(scope):
    value, row = _cleanup_scope(scope)
    if (scope._continuation is not value or value._pins is not row[3]
            or type(scope._pins) is not tuple or len(scope._pins) != 2
            or value is not scope._pins[0] or scope._claim is not scope._pins[1]):
        _fail("INVALID_ARGUMENT")
    _continuation(value)
    if value._stage is not scope or value._attempted is not True:
        _fail("INVALID_ARGUMENT")
    return value


def _store_prepare_scope(continuation):
    """Consume the real continuation's sole stage attempt, before full getters."""
    value = _minimal(continuation)
    if (value._attempted is not False or value._phase != "available"
            or value._stage is not None or value._journal is not None):
        _fail("CLOSED")
    value._attempted = True
    try:
        _continuation(value, live=True)
        value._budget.clock_only()
        scope = object.__new__(_StorePrepareStage)
        scope._seal, scope._continuation, scope._claim = _SEAL, value, object()
        scope._pins = (value, scope._claim)
        scope._enter_attempted = scope._exit_attempted = scope._entered = False
        scope._receipt = None
        key, claim, value_key, value_claim = id(scope), scope._claim, id(value), value._claim
        def collected(reference, *, key=key, claim=claim):
            row = dict.get(_SCOPE_ROWS, key)
            if type(row) is tuple and len(row) == 8 and row[0] is reference and row[1] is claim:
                dict.pop(_SCOPE_ROWS, key, None)
            binding = dict.get(_STAGES_BY_CONTINUATION, value_key)
            if (type(binding) is tuple and len(binding) == 3
                    and binding[1] is value_claim and binding[2] is reference):
                dict.pop(_STAGES_BY_CONTINUATION, value_key, None)
            closed = dict.get(_CLOSE_RECEIPTS, key)
            if type(closed) is tuple and len(closed) == 4 and closed[0] is reference and closed[1] is claim:
                dict.pop(_CLOSE_RECEIPTS, key, None)
        scope_ref, value_ref = weakref.ref(scope, collected), weakref.ref(value)
        if dict.get(_STAGES_BY_CONTINUATION, value_key) is not None:
            _fail("CLOSED")
        # row: scope, claim, continuation, original pins, actual journal,
        #      exit attempted, first exception, first traceback.
        _SCOPE_ROWS[key] = (scope_ref, claim, value_ref, value._pins, None, False, None, None)
        _STAGES_BY_CONTINUATION[value_key] = (value_ref, value_claim, scope_ref)
        value._stage, value._phase = scope, "stage_reserved"
        return scope
    except BaseException as primary:
        _remember(value, primary)
        value._phase = "failed"
        raise


def _finish_stage(scope, *, primary=None):
    from . import personal_authority_enrollment_store_custody as custody
    value, row = _cleanup_scope(scope)
    if row[5] is not False:
        _fail("CLOSED")
    # An incoming body exception precedes every optional business validation.
    if primary is not None:
        _record_stage_first(scope, primary)
        value, row = _cleanup_scope(scope)
    _replace_cleanup_row(scope, row, row[:5] + (True,) + row[6:])
    scope._exit_attempted = True
    value._phase = "closing"
    try:
        _stage(scope)
    except BaseException as failure:
        _record_stage_first(scope, failure)
    value, row = _cleanup_scope(scope)
    journal = row[4]
    snapshot = None
    if journal is not None:
        try:
            snapshot = custody._finish_journal(journal, primary=row[6])
        except BaseException as failure:
            _record_stage_first(scope, failure)
    else:
        if row[6] is None:
            try:
                _fail("JOURNAL_NOT_CREATED")
            except BaseException as failure:
                _record_stage_first(scope, failure)
    value, row = _cleanup_scope(scope)
    if row[6] is None:
        try:
            _stage(scope)
            _continuation(value, live=True)
            value._budget.clock_only()
            custody._require_presentations(journal)
            history = custody._history(journal)
            if (scope._entered is not True or type(snapshot) is not custody.StoreNativeOwnerJournalSnapshotV1
                    or journal is not value._journal
                    or snapshot is not custody._JOURNAL_RECEIPT.__get__(journal, custody._StorePrepareJournal)
                    or snapshot.all_registered_native_closed is not True or snapshot.first_present is not False
                    or snapshot.unknown_count != 0 or snapshot.registered_count <= 0
                    or snapshot.registered_count != len(history)
                    or len(snapshot.facts) != len(history)
                    or any(fact is not slot._receipt for fact, slot in zip(snapshot.facts, history))
                    or snapshot.install_state != "installed" or snapshot.reset_state != "reset"
                    or snapshot.source_closed is not False or snapshot.write_authority is not False):
                _fail("NATIVE_STAGE_INCOMPLETE")
            receipt = _StorePrepareCloseReceipt(1, "registered_store_prepare_native_stage_only", snapshot,
                snapshot.registered_count, True, True, True, True, True, False, False)
            value._budget.clock_only()
            scope_row = dict.get(_SCOPE_ROWS, id(scope))
            if dict.get(_CLOSE_RECEIPTS, id(scope)) is not None:
                _fail("CLOSED")
            _CLOSE_RECEIPTS[id(scope)] = (scope_row[0], scope._claim, receipt, snapshot)
            scope._receipt = value._receipt = receipt
            value._phase = "closed"
        except BaseException as failure:
            _record_stage_first(scope, failure)
    value, row = _cleanup_scope(scope)
    if row[6] is not None:
        value._phase = "failed"
        raise BaseException.with_traceback(row[6], row[7])
    return value._receipt


def _stage_close_receipt(scope):
    """A registered getter, never acceptance of an equal caller receipt."""
    from . import personal_authority_enrollment_store_custody as custody
    value = _stage(scope)
    _continuation(value, live=True)
    row = dict.get(_CLOSE_RECEIPTS, id(scope))
    if (scope._exit_attempted is not True or value._phase != "closed" or value._first is not None
            or type(scope._receipt) is not _StorePrepareCloseReceipt or value._receipt is not scope._receipt
            or type(row) is not tuple or len(row) != 4 or row[0]() is not scope
            or row[1] is not scope._claim or row[2] is not scope._receipt
            or row[3] is not custody._JOURNAL_RECEIPT.__get__(value._journal, custody._StorePrepareJournal)):
        _fail("STAGE_NOT_CLOSED")
    receipt, snapshot = row[2], row[3]
    journal = custody._journal(value._journal)
    custody._require_presentations(journal)
    history = custody._history(journal)
    if (type(receipt.format_version) is not int or receipt.format_version != 1
            or receipt.kind != "registered_store_prepare_native_stage_only"
            or receipt.native_snapshot is not snapshot
            or type(receipt.registered_native_count) is not int
            or receipt.registered_native_count != len(value._journal._slots)
            or receipt.registered_native_count != len(journal._registrations)
            or receipt.registered_native_count != len(history)
            or receipt.registered_native_count != snapshot.registered_count
            or any(a is not b for a, b in zip(journal._slots, journal._registrations))
            or any(a is not b for a, b in zip(journal._slots, history))
            or len(snapshot.facts) != len(history)
            or any(fact is not slot._receipt for fact, slot in zip(snapshot.facts, history))
            or any(flag is not True for flag in (receipt.all_registered_native_closed,
                    receipt.context_reset, receipt.journal_sealed, receipt.source_snapshot_unchanged,
                    receipt.outer_ex_borrowed))
            or receipt.source_closed is not False or receipt.write_authority is not False):
        _fail("STAGE_NOT_CLOSED")
    return receipt


class _RetainedRetirement:
    """Original strong evidence for unconfirmed metadata retirement only."""
    __slots__ = ("_value", "_scope", "_row", "_journal", "_history", "_history_row",
                 "_cleared_row", "_first", "_first_tb", "_content_returned",
                 "_history_release_returned", "_row_clear_returned",
                 "_history_release_observed", "_row_clear_observed", "_observation_returned", "_phase",
                 "_directory_graph", "_directory_retire_returned", "_directory_release_returned",
                 "_file_io_graph", "_file_io_retire_returned", "_file_io_effects_confirmed",
                 "_file_io_release_returned", "_file_io_release_effects_confirmed")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


def _new_retained_retirement(value, scope, row, journal, history):
    from . import personal_authority_enrollment_store_custody as custody
    if dict.get(_RETAINED_RETIREMENTS, id(value)) is not None:
        _fail("CLOSED")
    record = object.__new__(_RetainedRetirement)
    record._value, record._scope, record._row = value, scope, row
    record._journal, record._history = journal, history
    record._history_row = None if journal is None else dict.get(custody._REGISTRATION_HISTORY, id(journal))
    record._cleared_row = row[:3] + ((), None, True, None, None)
    record._first, record._first_tb = (value._first, value._first_tb) if row[6] is None else (row[6], row[7])
    record._content_returned = record._history_release_returned = record._row_clear_returned = False
    record._history_release_observed = record._row_clear_observed = record._observation_returned = False
    record._phase = "retiring"
    initial_hook = _initial_hook(value)
    record._directory_graph = None if initial_hook is None else directories._original_for_continuation(value)
    record._directory_retire_returned = record._directory_graph is None
    record._directory_release_returned = record._directory_graph is None
    record._file_io_graph = file_io._original_graph_for_continuation(value)
    record._file_io_retire_returned = record._file_io_effects_confirmed = record._file_io_graph is None
    record._file_io_release_returned = record._file_io_release_effects_confirmed = record._file_io_graph is None
    # Install this original strong root before any metadata side effect.
    _RETAINED_RETIREMENTS[id(value)] = record
    initial_hook = _initial_hook(value)
    if initial_hook is not None:
        initial_hook._retirement_record = record
    return record


def _retirement_first(record, failure):
    if record._first is None:
        value = record._value
        first, traceback = value._first, value._first_tb
        if first is None:
            first = failure
            traceback = BaseException.__dict__["__traceback__"].__get__(failure)
        elif failure is not first:
            _note(first)
        record._first, record._first_tb = first, traceback
    elif failure is not record._first:
        _note(record._first)
    # Do not rewrite a cleanup row which may already have actually cleared.
    record._value._first, record._value._first_tb = record._first, record._first_tb


def _retirement_observed(record):
    from . import personal_authority_enrollment_store_custody as custody
    record._row_clear_observed = dict.get(_SCOPE_ROWS, id(record._scope)) is record._cleared_row
    record._history_release_observed = record._journal is None
    if record._file_io_graph is not None:
        record._file_io_effects_confirmed = file_io._retirement_observation_for_continuation(record._value) is True
    if record._journal is not None:
        actual = dict.get(custody._REGISTRATION_HISTORY, id(record._journal))
        original = record._history_row
        record._history_release_observed = (type(actual) is tuple and len(actual) == 4
            and type(original) is tuple and len(original) == 4
            and actual[0] is original[0] and actual[1] is original[1]
            and actual[2] is None and actual[3] == original[3])


def _retire_continuation(continuation, *, primary=None):
    """Close original debts once and preserve uncertain retirement evidence."""
    from . import personal_authority_enrollment_store_custody as custody
    value = _minimal(continuation)
    retained = dict.get(_RETAINED_RETIREMENTS, id(value))
    if retained is not None:
        if type(retained) is not _RetainedRetirement or retained._value is not value:
            _fail("INVALID_ARGUMENT")
        if retained._first is not None:
            raise BaseException.with_traceback(retained._first, retained._first_tb)
        _fail("CLOSED")
    if value._phase == "retained_failed":
        if value._first is not None:
            raise BaseException.with_traceback(value._first, value._first_tb)
        _fail("CLOSED")
    if value._phase in ("retired_closed", "retired_uncompleted"):
        _fail("CLOSED")
    if primary is not None:
        _remember(value, primary)
    initial_hook = _initial_hook(value)
    if initial_hook is not None:
        if initial_hook._retire_attempted is not True:
            _fail("CLOSED")
        try:
            directories._require_retirable_for_continuation(value)
        except BaseException as failure:
            _remember(value, failure)
            value._phase = "retained_failed"
            if primary is None:
                raise BaseException.with_traceback(value._first, value._first_tb)
            return None
    binding = dict.get(_STAGES_BY_CONTINUATION, id(value))
    scope, row = None, None
    content_attempted = False
    if binding is not None:
        if type(binding) is not tuple or len(binding) != 3 or type(binding[2]) is not weakref.ReferenceType:
            _fail("INVALID_ARGUMENT")
        scope = binding[2]()
        actual, row = _cleanup_scope(scope)
        if actual is not value:
            _fail("INVALID_ARGUMENT")
        if primary is not None:
            _record_stage_first(scope, primary)
        if row[5] is False:
            if initial_hook is not None:
                try:
                    _fail("CLEANUP_DEBT_REMAINS")
                except BaseException as failure:
                    _record_stage_first(scope, failure)
                value._phase = "retained_failed"
                if primary is None:
                    raise BaseException.with_traceback(value._first, value._first_tb)
                return None
            try:
                _finish_stage(scope, primary=primary)
            except BaseException as failure:
                _record_stage_first(scope, failure)
        value, row = _cleanup_scope(scope)
        journal = row[4]
        history = None
        settled = journal is None
        if journal is not None:
            try:
                history = custody._require_retirable_history(journal)
                settled = True
            except BaseException as failure:
                _record_stage_first(scope, failure)
        value, row = _cleanup_scope(scope)
        if not settled:
            if initial_hook is not None:
                value._phase = "retained_failed"
                if primary is None and value._first is not None:
                    raise BaseException.with_traceback(value._first, value._first_tb)
                return None
            # Native UNKNOWN debt stays in the original complete cleanup graph.
            # No future retirement call may retry metadata or numeric close.
            value._phase = "retained_failed"
            try:
                selections._retire_for_continuation(value, primary=primary)
            except BaseException as failure:
                _record_stage_first(scope, failure)
            value, row = _cleanup_scope(scope)
            if primary is None and row[6] is not None:
                raise BaseException.with_traceback(row[6], row[7])
            return
        try:
            file_io._require_retirable_for_continuation(value)
        except BaseException as failure:
            _record_stage_first(scope, failure)
            value._phase = "retained_failed"
            if primary is None:
                raise BaseException.with_traceback(value._first, value._first_tb)
            return None
        record = _new_retained_retirement(value, scope, row, journal, history)
        first, traceback = record._first, record._first_tb
        completed = value._phase == "closed" and first is None
        if record._file_io_graph is not None:
            try:
                file_io._retire_for_continuation(value, primary=record._first)
                record._file_io_retire_returned = True
            except BaseException as failure:
                _retirement_first(record, failure)
            try:
                record._file_io_effects_confirmed = file_io._retirement_observation_for_continuation(value) is True
            except BaseException as failure:
                _retirement_first(record, failure)
        if record._directory_graph is not None and record._file_io_retire_returned and record._file_io_effects_confirmed:
            try:
                directories._retire_for_continuation(value, primary=record._first)
                record._directory_retire_returned = True
            except BaseException as failure:
                _retirement_first(record, failure)
        # Directory metadata precedes content on the opted Store route.
        content_attempted = True
        if record._directory_retire_returned and record._file_io_retire_returned and record._file_io_effects_confirmed:
            try:
                selections._retire_for_continuation(value, primary=record._first)
                record._content_returned = True
            except BaseException as failure:
                _retirement_first(record, failure)
        # An earlier content fault cannot skip this debt-settled release.
        if record._directory_retire_returned and record._file_io_retire_returned and record._file_io_effects_confirmed:
            if journal is None:
                record._history_release_returned = True
            else:
                try:
                    custody._release_retired_history(journal)
                    record._history_release_returned = True
                except BaseException as failure:
                    _retirement_first(record, failure)
        if record._history_release_returned:
            try:
                _replace_cleanup_row(scope, row, record._cleared_row)
                record._row_clear_returned = True
            except BaseException as failure:
                _retirement_first(record, failure)
        try:
            _retirement_observed(record)
            record._observation_returned = True
        except BaseException as failure:
            _retirement_first(record, failure)
        first, traceback = record._first, record._first_tb
        if not (record._directory_retire_returned and record._content_returned and record._history_release_returned and record._row_clear_returned
                and record._observation_returned and record._file_io_retire_returned and record._file_io_effects_confirmed
                and record._history_release_observed and record._row_clear_observed):
            record._phase = value._phase = "retained_failed"
            # Keep the original strong pins, journal, history and any receipt.
            # An actually cleared old row is never restored or modified here.
            if first is not None and primary is None:
                raise BaseException.with_traceback(first, traceback)
            return
        if record._file_io_graph is not None:
            try:
                file_io._release_retired_graph(record._file_io_graph)
                record._file_io_release_returned = True
            except BaseException as failure:
                _retirement_first(record, failure)
            try:
                record._file_io_release_effects_confirmed = file_io._release_observation_for_graph(record._file_io_graph) is True
            except BaseException as failure:
                _retirement_first(record, failure)
            if not (record._file_io_release_returned and record._file_io_release_effects_confirmed):
                record._phase = value._phase = "retained_failed"
                if primary is None and record._first is not None:
                    raise BaseException.with_traceback(record._first, record._first_tb)
                return None
        if record._directory_graph is not None:
            try:
                directories._release_retired_graph(record._directory_graph)
                record._directory_release_returned = True
            except BaseException as failure:
                _retirement_first(record, failure)
                record._phase = value._phase = "retained_failed"
                if primary is None and record._first is not None:
                    raise BaseException.with_traceback(record._first, record._first_tb)
                return None
        record._phase = "retired"
        dict.pop(_RETAINED_RETIREMENTS, id(value))
        completed = completed and first is None
    else:
        first, traceback = value._first, value._first_tb
        completed = False
    if not content_attempted:
        # Default source-only continuation has no plan and produces nothing.
        try:
            selections._retire_for_continuation(value, primary=primary)
        except BaseException as failure:
            _remember(value, failure)
    if first is None and value._first is not None:
        first, traceback = value._first, value._first_tb
        completed = False
    value._phase = "retired_closed" if completed else "retired_uncompleted"
    value._pins = ()
    for name in _PIN_NAMES:
        setattr(value, name, None)
    value._journal = value._stage = value._receipt = None
    if first is not None and primary is None:
        raise BaseException.with_traceback(first, traceback)


class _StoreInitialRoute:
    __slots__ = ('_seal', '_claim', '_pins', '__weakref__')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _StoreInitialHook:
    """Preallocated route/factory/Store owner; the wrapper alone retires."""
    __slots__ = ('_value', '_route', '_claim', '_plan', '_stage', '_directory', '_first', '_first_tb',
                 '_retire_attempted', '_retire_returned', '_phase', '_entry_returned',
                 '_stage_enter_returned', '_directory_enter_returned', '_directory_exit_returned',
                 '_stage_exit_returned', '_retirement_record', '_factory_attempted', '_factory_returned',
                 '_route_returned', '_slot', '_call_pins', '_metadata_attempted', '_metadata_returned',
                 '_metadata_record')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


class _StoreInitialIndexCallback:
    __slots__ = ('_kind', '_key', '_claim', '_related_claim')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __call__(self, reference):
        pin = dict.get(_INITIAL_CALLBACK_PINS, id(self))
        if (type(pin) is not tuple or len(pin) != 6 or pin[0] is not self or pin[1] is not reference
                or type(reference) is not weakref.ReferenceType
                or reference() is not None or self._kind != pin[2] or self._key != pin[3]
                or self._claim is not pin[4] or self._related_claim is not pin[5]):
            return
        tables = {'group': (_INITIAL_ROUTES, 6), 'owner': (_INITIAL_ROUTE_OWNERS, 6),
                  'binding': (_INITIAL_ROUTE_VALUES, 4), 'hook': (_INITIAL_HOOK_ATTEMPTS, 3),
                  'factory': (_INITIAL_FACTORY_ROWS, 9), 'attempt': (_INITIAL_FACTORY_ATTEMPTS, 3)}
        selected = dict.get(tables, pin[2])
        if selected is None:
            return
        table, length = selected
        row = dict.get(table, pin[3])
        if (type(row) is tuple and len(row) == length and row[0] is reference and row[1] is pin[4]
                and (pin[2] not in ('binding', 'hook', 'attempt') or row[-1] is pin[5])):
            dict.pop(table, pin[3], None)
        if dict.get(_INITIAL_CALLBACK_PINS, id(self)) is pin:
            dict.pop(_INITIAL_CALLBACK_PINS, id(self), None)


class _StoreInitialHookMetadata:
    """Original graph before route/factory/hook metadata, never a resource grant."""
    __slots__ = ('_original', '_hook', '_value', '_route', '_group', '_route_row', '_binding',
                 '_factory_row', '_factory_owner_row', '_hook_row', '_owner_fields', '_retirement_record',
                 '_first', '_first_tb', '_binding_returned', '_factory_returned', '_factory_owner_returned',
                 '_hook_returned', '_route_returned', '_pins_returned', '_owner_returned',
                 '_fields_returned', '_observation_returned', '_effects', '_phase')

    def __init__(self, *args, **kwargs):
        _fail('INVALID_ARGUMENT')

    def __init_subclass__(cls, **kwargs):
        _fail('INVALID_ARGUMENT')


def _initial_reference(value, kind, claim, related_claim=None):
    callback = object.__new__(_StoreInitialIndexCallback)
    callback._kind, callback._key = kind, id(value)
    callback._claim, callback._related_claim = claim, related_claim
    reference = weakref.ref(value, callback)
    _INITIAL_CALLBACK_PINS[id(callback)] = (callback, reference, kind, id(value), claim, related_claim)
    return reference


def _initial_value_reference(value, kind, related_claim):
    return _initial_reference(value, kind, value._claim, related_claim)


def _new_initial_record(route):
    record = object.__new__(_StoreInitialHook)
    record._route, record._claim = route, object()
    record._value = record._plan = record._stage = record._directory = None
    record._first = record._first_tb = record._retirement_record = record._slot = record._call_pins = None
    record._metadata_record = None
    for name in ('_retire_attempted', '_retire_returned', '_entry_returned', '_stage_enter_returned',
                 '_directory_enter_returned', '_directory_exit_returned', '_stage_exit_returned',
                 '_factory_attempted', '_factory_returned', '_route_returned',
                 '_metadata_attempted', '_metadata_returned'):
        setattr(record, name, False)
    record._phase = 'route_registering'
    return record


def _route_original(route):
    if type(route) is not _StoreInitialRoute:
        _fail('INVALID_ARGUMENT')
    original = dict.get(_INITIAL_ROUTE_OWNERS, id(route))
    if (type(original) is not tuple or len(original) != 6
            or type(original[0]) is not weakref.ReferenceType or original[0]() is not route):
        _fail('INVALID_ARGUMENT')
    record = original[5]
    if record is None:
        _fail('CLOSED')
    if (type(record) is not _StoreInitialHook
            or type(original[2]) is not tuple or len(original[2]) != 9
            or type(original[3]) is not weakref.ReferenceType or original[3]() is not original[2][0]
            or type(original[4]) is not tuple or len(original[4]) != 6
            or original[4][0] is not original[3] or original[4][1] is not original[1]
            or original[4][2] is not original[0] or original[4][3] is not original[2]):
        _fail('INVALID_ARGUMENT')
    return original


def _owner_first(original, primary, value=None):
    record, budget = original[5], original[2][2]
    if record._first is None:
        first = None if value is None else value._first
        traceback = None if value is None else value._first_tb
        if first is None:
            first = budget.remember(primary)
            traceback = BaseException.__dict__['__traceback__'].__get__(first)
        elif primary is not first:
            _note(first)
        record._first, record._first_tb = first, traceback
    elif primary is not record._first:
        _note(record._first)
    if value is not None:
        _remember(value, record._first)
    return record._first, record._first_tb


def _new_initial_route(group, guard, budget):
    from . import personal_authority_enrollment as enrollment
    group = groups._registered(group)
    binding = group._binding
    if (type(budget) is not enrollment._Budget or budget._owned_ex is not group
            or group._guard is not guard or group._phase != 'active'
            or lifecycle._CURRENT.get() is not guard or not groups._same_owner(group._owner, lifecycle._owner())
            or budget._requested_deadline is not binding._parent_token
            or budget._effective_deadline is not binding._effective_token
            or dict.get(_INITIAL_ROUTES, id(group)) is not None):
        _fail('INVALID_ARGUMENT')
    deadline._token(binding._parent_token)
    deadline._token(binding._effective_token)
    route, claim = object.__new__(_StoreInitialRoute), object()
    route._seal, route._claim = _SEAL, claim
    route._pins = (group, guard, budget, binding, binding._operation, binding._parent_token,
                   binding._effective_token, binding._scope_identity, group._owner)
    record = _new_initial_record(route)
    group_ref = _initial_reference(group, 'group', claim)
    route_ref = _initial_reference(route, 'owner', claim)
    row = (group_ref, claim, route_ref, route._pins, None, False)
    original = (route_ref, claim, route._pins, group_ref, row, record)
    # Original ownership precedes the first route-index effect and tail clock.
    _INITIAL_ROUTE_OWNERS[id(route)] = original
    try:
        _INITIAL_ROUTES[id(group)] = row
        budget.clock_only()
        record._route_returned, record._phase = True, 'route_available'
    except BaseException as primary:
        first, traceback = _owner_first(original, primary)
        try:
            _retire_initial_hook_metadata(original)
        except BaseException as secondary:
            if secondary is not first:
                _note(first)
        raise BaseException.with_traceback(first, traceback)
    return route


def _route(route):
    original = _route_original(route)
    record = original[5]
    if record._first is not None:
        raise BaseException.with_traceback(record._first, record._first_tb)
    pins = original[2]
    row = dict.get(_INITIAL_ROUTES, id(pins[0]))
    if (record._route is not route or route._seal is not _SEAL
            or route._claim is not original[1] or route._pins is not pins
            or type(row) is not tuple or len(row) != 6 or row[0] is not original[3]
            or row[1] is not original[1] or row[2] is not original[0] or row[3] is not pins):
        _fail('INVALID_ARGUMENT')
    return row


def _bind_initial_route(route, continuation):
    row = _route(route)
    if row[5] is not False:
        _fail('CLOSED')
    group = route._pins[0]
    _INITIAL_ROUTES[id(group)] = row[:5] + (True,)
    value = _continuation(continuation, live=True)
    pins = route._pins
    if (value._group is not group or value._guard is not pins[1] or value._budget is not pins[2]
            or group._binding is not pins[3] or value._operation is not pins[4]
            or value._parent is not pins[5] or value._effective is not pins[6]
            or value._source_scope is not pins[7] or not groups._same_owner(value._owner, pins[8])
            or dict.get(_INITIAL_ROUTE_VALUES, id(value)) is not None):
        _fail('SOURCE_BINDING_INVALID')
    _INITIAL_ROUTES[id(group)] = row[:4] + (weakref.ref(value), True)
    _INITIAL_ROUTE_VALUES[id(value)] = (_initial_value_reference(value, 'binding', row[1]), value._claim, route, row[1])
    return None


def _initial_hook(value):
    record = dict.get(_INITIAL_HOOKS, id(value))
    if record is not None and (type(record) is not _StoreInitialHook or record._value is not value):
        _fail('INVALID_ARGUMENT')
    factory = dict.get(_INITIAL_FACTORY_ROWS, value._row[3])
    if factory is not None and type(factory) is tuple and len(factory) == 9 and factory[4] is value:
        trusted = factory[3]
        if (type(trusted) is not _StoreInitialHook or trusted._value is not value
                or factory[5] is not value._row or (record is not None and record is not trusted)):
            _fail('INVALID_ARGUMENT')
        record = trusted
    return record


def _original_initial_hook_first(value):
    value = _minimal(value)
    record = _initial_hook(value)
    if record is not None and record._first is not None:
        return record._first, record._first_tb
    return value._first, value._first_tb


def _hook_first(record, primary):
    original = _route_original(record._route)
    return _owner_first(original, primary, record._value)


def _factory_binding(original):
    row = dict.get(_INITIAL_FACTORY_OWNERS, id(original[0]()))
    if (type(row) is not tuple or len(row) != 9 or row[2] is not original or row[3] is not original[5]
            or type(row[0]) is not weakref.ReferenceType or row[0]() is None
            or dict.get(_INITIAL_FACTORY_ROWS, id(row[0]())) is not row
            or type(row[8]) is not tuple or len(row[8]) != 5 or row[8][0] is not row[0]()):
        _fail('SOURCE_BINDING_INVALID')
    return row


def _consume_initial_store_owned(original):
    """Store business only; the fixed factory wrapper owns the sole finally."""
    if (type(original) is not tuple or len(original) != 6
            or type(original[0]) is not weakref.ReferenceType or original[0]() is None
            or dict.get(_INITIAL_ROUTE_OWNERS, id(original[0]())) is not original):
        _fail('INVALID_ARGUMENT')
    record = original[5]
    if type(record) is not _StoreInitialHook:
        _fail('INVALID_ARGUMENT')
    # First and all route/minimal business checks occur within wrapper ownership.
    if record._first is not None:
        raise BaseException.with_traceback(record._first, record._first_tb)
    factory = _factory_binding(original)
    value = factory[4]
    if value is None or record._value is not value or factory[5] is not value._row:
        _fail('SOURCE_BINDING_INVALID')
    value = _minimal(value)
    if value._first is not None:
        raise BaseException.with_traceback(value._first, value._first_tb)
    if factory[6] is not False or dict.get(_INITIAL_HOOK_ATTEMPTS, id(value)) is not None:
        _fail('CLOSED')
    claimed = factory[:6] + (True,) + factory[7:]
    _INITIAL_FACTORY_ROWS[id(factory[0]())] = claimed
    _INITIAL_FACTORY_OWNERS[id(original[0]())] = claimed
    _INITIAL_HOOK_ATTEMPTS[id(value)] = (_initial_value_reference(value, 'hook', record._claim), value._claim, record._claim)
    _INITIAL_HOOKS[id(value)] = record
    _bind_initial_route(original[0](), value)
    _continuation(value, live=True)
    record._plan = selections._new_content_plan(value)
    record._stage = _store_prepare_scope(value)
    with record._stage:
        record._stage_enter_returned = True
        record._directory = directories._open_initial_directory_scope(record._plan, record._stage, continuation=value)
        with record._directory:
            record._directory_enter_returned = True
            directories._initial_census(record._directory)
            _prepare_file_collection_owned(record._directory)
        record._directory_exit_returned = True
    record._stage_exit_returned = record._entry_returned = True
    return None


def _observe_initial_hook_metadata(root):
    original, value = root._original, root._value
    binding = None if value is None else dict.get(_INITIAL_ROUTE_VALUES, id(value))
    old = root._binding
    binding_effect = binding is None if old is None else (
        type(binding) is tuple and len(binding) == 4 and binding[0] is old[0]
        and binding[1] is old[1] and binding[2] is None and binding[3] is old[3])
    factory = root._factory_row
    actual_factory = None if factory is None else dict.get(_INITIAL_FACTORY_ROWS, id(factory[0]()))
    factory_effect = factory is None or (type(actual_factory) is tuple and len(actual_factory) == 9
        and actual_factory[0] is factory[0] and actual_factory[1] is factory[1]
        and actual_factory[2:6] == (None, None, None, None) and actual_factory[6] is True
        and actual_factory[7:] == (None, None))
    factory_owner_effect = dict.get(_INITIAL_FACTORY_OWNERS, id(root._route)) is None
    hook_effect = value is None or dict.get(_INITIAL_HOOKS, id(value)) is None
    row = dict.get(_INITIAL_ROUTES, id(root._group))
    route_effect = (type(row) is tuple and len(row) == 6 and row[0] is original[3]
        and row[1] is original[1] and row[2] is original[0] and row[3] == () and row[5] is True)
    owner = dict.get(_INITIAL_ROUTE_OWNERS, id(root._route))
    owner_effect = (type(owner) is tuple and len(owner) == 6 and owner[0] is original[0]
        and owner[1] is original[1] and owner[2] == () and owner[3] is original[3]
        and owner[4] is None and owner[5] is None)
    fields_effect = all(getattr(root._hook, name) is None for name in
        ('_slot', '_call_pins', '_plan', '_stage', '_directory', '_retirement_record'))
    root._effects = (binding_effect, factory_effect, factory_owner_effect, hook_effect,
                     route_effect, root._route._pins == (), owner_effect, fields_effect)
    return None


def _retire_initial_hook_metadata(original):
    """One independent metadata attempt, for both pre-return and Store paths."""
    route, record = original[0](), original[5]
    if (type(record) is not _StoreInitialHook or record._metadata_attempted is not False
            or dict.get(_INITIAL_HOOK_METADATA, id(route)) is not None):
        if record._first is not None:
            raise BaseException.with_traceback(record._first, record._first_tb)
        _fail('CLOSED')
    record._metadata_attempted = True
    root = object.__new__(_StoreInitialHookMetadata)
    root._original, root._hook, root._route, root._group = original, record, route, original[2][0]
    root._factory_row = dict.get(_INITIAL_FACTORY_OWNERS, id(route))
    root._factory_owner_row = root._factory_row
    root._value = None if root._factory_row is None else root._factory_row[4]
    if root._value is None:
        root._value = record._value
    root._binding = None if root._value is None else dict.get(_INITIAL_ROUTE_VALUES, id(root._value))
    root._hook_row = None if root._value is None else dict.get(_INITIAL_HOOKS, id(root._value))
    root._route_row = dict.get(_INITIAL_ROUTES, id(root._group))
    root._owner_fields = (record._slot, record._call_pins, record._plan, record._stage, record._directory)
    root._retirement_record = record._retirement_record
    root._first, root._first_tb = record._first, record._first_tb
    for name in ('_binding_returned', '_factory_returned', '_factory_owner_returned', '_hook_returned',
                 '_route_returned', '_pins_returned', '_owner_returned', '_fields_returned', '_observation_returned'):
        setattr(root, name, False)
    root._effects, root._phase = (False,) * 8, 'retiring'
    record._metadata_record = root
    # Original tuple/actual call-return/metadata rows are held before any effect.
    _INITIAL_HOOK_METADATA[id(route)] = root
    try:
        binding = root._binding
        if binding is not None:
            if (type(binding) is not tuple or len(binding) != 4 or binding[0]() is not root._value
                    or binding[2] is not route or binding[3] is not original[1]
                    or (root._factory_row is not None and root._factory_row[5] is not None
                        and binding[1] is not root._factory_row[5][1])):
                _fail('INVALID_ARGUMENT')
            _INITIAL_ROUTE_VALUES[id(root._value)] = binding[:2] + (None, binding[3])
        root._binding_returned = True
        factory = root._factory_row
        if factory is not None:
            if (type(factory) is not tuple or len(factory) != 9 or factory[2] is not original
                    or factory[3] is not record or dict.get(_INITIAL_FACTORY_ROWS, id(factory[0]())) is not factory):
                _fail('INVALID_ARGUMENT')
            _INITIAL_FACTORY_ROWS[id(factory[0]())] = factory[:2] + (None, None, None, None, True, None, None)
        root._factory_returned = True
        if factory is not None:
            if dict.get(_INITIAL_FACTORY_OWNERS, id(route)) is not factory:
                _fail('INVALID_ARGUMENT')
            dict.__delitem__(_INITIAL_FACTORY_OWNERS, id(route))
        root._factory_owner_returned = True
        if root._hook_row is not None:
            if root._hook_row is not record:
                _fail('INVALID_ARGUMENT')
            dict.__delitem__(_INITIAL_HOOKS, id(root._value))
        root._hook_returned = True
        row = root._route_row
        if (type(row) is not tuple or len(row) != 6 or row[0] is not original[3]
                or row[1] is not original[1] or row[2] is not original[0] or row[3] is not original[2]):
            _fail('INVALID_ARGUMENT')
        _INITIAL_ROUTES[id(root._group)] = row[:3] + ((), row[4], True)
        root._route_returned = True
        route._pins = ()
        root._pins_returned = True
        if dict.get(_INITIAL_ROUTE_OWNERS, id(route)) is not original:
            _fail('INVALID_ARGUMENT')
        _INITIAL_ROUTE_OWNERS[id(route)] = original[:2] + ((), original[3], None, None)
        root._owner_returned = True
        for name in ('_slot', '_call_pins', '_plan', '_stage', '_directory', '_retirement_record'):
            setattr(record, name, None)
        root._fields_returned = True
        _observe_initial_hook_metadata(root)
        root._observation_returned = True
        if not all(root._effects):
            _fail('RETIREMENT_UNCONFIRMED')
        root._phase = 'retired'
        if dict.get(_INITIAL_HOOK_METADATA, id(route)) is not root:
            _fail('INVALID_ARGUMENT')
        dict.__delitem__(_INITIAL_HOOK_METADATA, id(route))
        record._metadata_record = None
        record._metadata_returned = True
    except BaseException as failure:
        first, traceback = _owner_first(original, failure, root._value)
        root._first, root._first_tb = first, traceback
        root._phase = record._phase = 'retained_failed'
        try:
            _observe_initial_hook_metadata(root)
        except BaseException as secondary:
            if secondary is not first:
                _note(first)
        raise BaseException.with_traceback(first, traceback)
    return None


def _initial_prepare_factory_owned(route, slot, *, source_consumer, source_precondition, result):
    """Fixed route/packet factory, Store business and sole aggregate finally."""
    original = _route_original(route)
    record = original[5]
    if record._first is not None:
        raise BaseException.with_traceback(record._first, record._first_tb)
    if record._metadata_attempted or dict.get(_INITIAL_FACTORY_ATTEMPTS, id(route)) is not None:
        _fail('CLOSED')
    returned_value = None
    try:
        _INITIAL_FACTORY_ATTEMPTS[id(route)] = (
            _initial_reference(route, 'attempt', original[1], record._claim), original[1], record._claim)
        record._factory_attempted, record._phase = True, 'factory_running'
        carrier = packet._registered_slot(slot)
        if carrier._group is not original[2][0] or carrier._budget is not original[2][2]:
            _fail('SOURCE_BINDING_INVALID')
        record._slot = slot
        record._call_pins = (slot, carrier, source_consumer, source_precondition, result)
        if dict.get(_INITIAL_FACTORY_ROWS, id(slot)) is not None:
            _fail('CLOSED')
        factory = (_initial_reference(slot, 'factory', slot._claim), slot._claim, original, record,
                   None, None, False, slot._identity_pin, record._call_pins)
        _INITIAL_FACTORY_OWNERS[id(route)] = factory
        _INITIAL_FACTORY_ROWS[id(slot)] = factory
        _route(route)
        # Direct actual return goes to the already strongly registered owner.
        record._value = _new_prepare_continuation(slot, source_consumer=source_consumer,
            source_precondition=source_precondition, result=result)
        returned_value = record._value
        record._factory_returned = True
        after = factory[:4] + (returned_value, returned_value._row, False) + factory[7:]
        if (dict.get(_INITIAL_FACTORY_OWNERS, id(route)) is not factory
                or dict.get(_INITIAL_FACTORY_ROWS, id(slot)) is not factory):
            _fail('SOURCE_BINDING_INVALID')
        _INITIAL_FACTORY_OWNERS[id(route)] = after
        _INITIAL_FACTORY_ROWS[id(slot)] = after
        _consume_initial_store_owned(original)
    except BaseException as primary:
        _owner_first(original, primary, returned_value)
    finally:
        if returned_value is not None:
            record._retire_attempted = True
            try:
                _retire_continuation(returned_value, primary=record._first)
                record._retire_returned = True
            except BaseException as failure:
                _owner_first(original, failure, returned_value)
        # No actual factory return means no new continuation/hook retirement.
        if (returned_value is None or (record._retire_returned
                and returned_value._phase in ('retired_closed', 'retired_uncompleted'))):
            try:
                _retire_initial_hook_metadata(original)
            except BaseException as failure:
                _owner_first(original, failure, returned_value)
                record._phase = 'retained_failed'
            else:
                record._phase = 'retired'
        else:
            record._phase = 'retained_failed'
    if record._first is not None:
        raise BaseException.with_traceback(record._first, record._first_tb)
    if record._phase != 'retired':
        _fail('CLEANUP_DEBT_REMAINS')
    return None


def _initial_hook_catalog_extra_original():
    return tuple((kind, ('__slots__', '__init__', '__init_subclass__',
                         *(name for name in kind.__slots__ if name != '__weakref__')))
                 for kind in (_StoreInitialRoute, _StoreInitialHook, _StoreInitialIndexCallback,
                              _StoreInitialHookMetadata)) + (
        (_StoreInitialIndexCallback, ('__call__',)), (weakref.ReferenceType, ('__callback__', '__call__')))


def _prepare_file_collection_owned(scope):
    """One fixed k0 route, all readers live through the independent census."""
    graph = None
    try:
        graph = file_io._new_file_io_graph(scope)
        cycles = ()
        for ordinal in range(11):
            selected = directories._original_selection(scope, ordinal)
            cycle = file_io._begin_file_cycle(selected, scope=scope)
            reader = file_io._open_held_reader(cycle)
            file_io._initial_reader_bytes(reader)
            cycles = cycles + (cycle,)
        for ordinal in range(29):
            selected = directories._store_selection(scope, ordinal)
            cycle = file_io._begin_file_cycle(selected, scope=scope)
            file_io._publish_missing_store_file(cycle)
            reader = file_io._open_held_reader(cycle)
            file_io._initial_reader_bytes(reader)
        # These are separate owned readers, not a third read of a held owner.
        for ordinal in range(7, 11):
            file_io._authority_checkpoint_cycle(cycles[ordinal])
        generation = directories._begin_final_census_generation(scope)
        directories._run_final_census(generation)
        directories._final_census(scope)
        for reader in file_io._held_reader_collection(graph, scope=scope):
            file_io._finalize_held_reader(reader)
        file_io._require_file_collection_closed_owned(graph, scope=scope)
    except BaseException as primary:
        if graph is not None:
            first, traceback = file_io._record_and_abort(graph, primary)
            if first is not primary:
                raise BaseException.with_traceback(first, traceback)
        raise
    return None
