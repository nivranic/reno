"""One operation's original readback bytes; no SQL or writer authority.

Registry rows own content slots, never the carrier or its owner graph. The fixed
enrollment call sites stage, bind, consume under the same EX, then retire them.
"""
from __future__ import annotations

from dataclasses import dataclass
import weakref

from . import personal_authority_enrollment_contract as contract
from . import personal_authority_enrollment_physical as physical
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_owned_ex_group as groups

_SEAL = object()
_PACKETS = {}
_FIELDS = ("first_bytes", "second_bytes", "namespace_reservation_bytes",
           "root_seal_bytes", "header_bytes", "binding_bytes", "prefix_bytes",
           "origin_intent_bytes", "local_contract_bytes", "init_seal_bytes",
           "genesis_grant_bytes")
_MAXIMA = (contract.origin.MAX_RESERVATION_BYTES, contract.origin.MAX_OBSERVATION_BYTES,
           contract.namespace.MAX_RESERVATION_BYTES, contract.namespace.MAX_ROOT_SEAL_BYTES,
           contract.namespace.MAX_HEADER_BYTES) + (contract.MAX_RECORD_BYTES,) * 6
_MAX_TOTAL = contract.MAX_INPUT_BYTES + 6 * contract.MAX_RECORD_BYTES


class EnrollmentPacketError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_PACKET_" + suffix
        super().__init__(self.code)


Error = EnrollmentPacketError


def _fail(suffix):
    raise EnrollmentPacketError(suffix) from None


@dataclass(frozen=True, slots=True, init=False)
class _PacketBytes:
    first_bytes: bytes
    second_bytes: bytes
    namespace_reservation_bytes: bytes
    root_seal_bytes: bytes
    header_bytes: bytes
    binding_bytes: bytes
    prefix_bytes: bytes
    origin_intent_bytes: bytes
    local_contract_bytes: bytes
    init_seal_bytes: bytes
    genesis_grant_bytes: bytes

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


class _PacketSlot:
    __slots__ = ("_seal", "_claim", "_carrier_ref", "_carrier_claim", "_identity_pin",
                 "_operation", "_parent", "_effective", "_scope", "_capture_refs",
                 "_context_pin", "_payload", "_payload_pin", "_result", "_result_pin",
                 "_stage_attempted", "_stage_succeeded", "_bind_attempted",
                 "_consume_attempted", "_retired", "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


def _registered_slot(slot):
    """Only self/claim/registry recognition; no lineage, budget or clock read."""
    try:
        if type(slot) is not _PacketSlot or slot._seal is not _SEAL:
            _fail("INVALID_ARGUMENT")
        pin = slot._identity_pin
        if (type(pin) is not tuple or len(pin) != 3
                or type(slot._carrier_ref) is not weakref.ReferenceType
                or slot._claim is not pin[0] or slot._carrier_ref is not pin[1]
                or slot._carrier_claim is not pin[2]):
            _fail("INVALID_ARGUMENT")
        if slot._retired is True:
            _fail("CLOSED")
        carrier = slot._carrier_ref()
        if (type(carrier) is not physical._EnrollmentSourceCarrier
                or carrier._seal is not physical._SEAL or carrier._claim is not slot._carrier_claim):
            _fail("INVALID_ARGUMENT")
        row = dict.get(_PACKETS, id(carrier))
        if (type(row) is not tuple or len(row) != 3 or row[0] is not slot._carrier_ref
                or row[1] is not slot or row[2] is not slot._claim):
            _fail("INVALID_ARGUMENT")
        return carrier
    except AttributeError:
        _fail("INVALID_ARGUMENT")


def _slot_for_carrier(carrier):
    if type(carrier) is not physical._EnrollmentSourceCarrier:
        _fail("INVALID_ARGUMENT")
    row = dict.get(_PACKETS, id(carrier))
    if type(row) is not tuple or len(row) != 3 or type(row[1]) is not _PacketSlot:
        _fail("INVALID_RESULT")
    slot = row[1]
    if _registered_slot(slot) is not carrier:
        _fail("INVALID_ARGUMENT")
    return slot


def _clear_slot(slot):
    # No live getters or exception retention: this is content retirement only.
    slot._retired = True
    slot._operation = slot._parent = slot._effective = slot._scope = None
    slot._capture_refs = ()
    slot._context_pin = slot._payload = slot._payload_pin = None
    slot._result = slot._result_pin = None


def _check_bytes(values):
    if type(values) is not tuple or len(values) != len(_FIELDS):
        _fail("INVALID_RESULT")
    if any(type(raw) is not bytes or not 0 < len(raw) <= maximum
           for raw, maximum in zip(values, _MAXIMA)) or sum(map(len, values)) > _MAX_TOTAL:
        _fail("LIMIT")


def _payload_values(slot):
    payload, pin = slot._payload, slot._payload_pin
    if type(payload) is not _PacketBytes or type(pin) is not tuple or len(pin) != 12 or pin[0] is not payload:
        _fail("INVALID_RESULT")
    try:
        values = tuple(getattr(payload, name) for name in _FIELDS)
    except AttributeError:
        _fail("INVALID_RESULT")
    if any(current is not prior for current, prior in zip(values, pin[1:])):
        _fail("INVALID_RESULT")
    _check_bytes(values)
    return values


def _check_context(slot, carrier):
    current = (slot._operation, slot._parent, slot._effective, slot._scope, slot._capture_refs)
    pin = slot._context_pin
    if (type(pin) is not tuple or len(pin) != 5 or any(a is not b for a, b in zip(current, pin))
            or slot._operation is not carrier._operation or slot._parent is not carrier._parent
            or slot._effective is not carrier._effective or slot._scope is not carrier._scope
            or slot._stage_attempted is not True or slot._stage_succeeded is not True
            or slot._retired is not False or type(slot._capture_refs) is not tuple
            or len(slot._capture_refs) != 2 or carrier._captures is not carrier._group._captures):
        _fail("INVALID_RESULT")
    deadline._token(slot._parent)
    deadline._token(slot._effective)
    for ordinal, reference in enumerate(slot._capture_refs):
        if type(reference) is not weakref.ReferenceType:
            _fail("INVALID_RESULT")
        capture = reference()
        if (type(capture) is not groups.BorrowedCaptureDeadlineLease
                or carrier._captures[ordinal] is not capture or carrier._capture_returns[ordinal] is not capture
                or capture._ordinal != ordinal or capture._group is not carrier._group):
            _fail("INVALID_RESULT")
        groups._capture_registered(capture)


def _result_values(result):
    from . import personal_authority_enrollment as enrollment
    if type(result) is not enrollment.AuthorityPrefixFiles:
        _fail("INVALID_RESULT")
    values = tuple(getattr(result, name) for name in enrollment.AuthorityPrefixFiles.__slots__)
    if any(type(value) is not str for value in values):
        _fail("INVALID_RESULT")
    return values


def _stage_original_bytes(carrier, *, originals, observed_binding, observed_content):
    if (type(carrier) is not physical._EnrollmentSourceCarrier or carrier._seal is not physical._SEAL
            or type(carrier._claim) is not object):
        _fail("INVALID_ARGUMENT")
    key = id(carrier)
    if dict.__contains__(_PACKETS, key):
        _fail("CLOSED")
    slot = object.__new__(_PacketSlot)
    slot._seal, slot._claim, slot._carrier_claim = _SEAL, object(), carrier._claim
    claim = slot._claim
    def collected(reference, *, key=key, claim=claim):
        row = dict.get(_PACKETS, key)
        if type(row) is tuple and len(row) == 3 and row[0] is reference and row[2] is claim:
            dict.pop(_PACKETS, key, None)
            if type(row[1]) is _PacketSlot:
                _clear_slot(row[1])
    slot._carrier_ref = weakref.ref(carrier, collected)
    slot._identity_pin = (slot._claim, slot._carrier_ref, slot._carrier_claim)
    slot._operation = slot._parent = slot._effective = slot._scope = None
    slot._capture_refs = ()
    slot._context_pin = slot._payload = slot._payload_pin = None
    slot._result = slot._result_pin = None
    slot._stage_attempted, slot._stage_succeeded = True, False
    slot._bind_attempted = slot._consume_attempted = slot._retired = False
    _PACKETS[key] = (slot._carrier_ref, slot, slot._claim)
    # Pending row and sticky stage attempt exist before lineage or content checks.
    carrier = physical._lineage(carrier, installed=True)
    carrier._budget.clock_only()
    deadline._token(carrier._parent)
    deadline._token(carrier._effective)
    if (type(originals) is not tuple or len(originals) != 5
            or type(observed_content) is not contract.AuthorityPrefixContent
            or type(carrier._operation) is not tuple or len(carrier._operation) != 3
            or any(type(value) is not str for value in carrier._operation) or type(carrier._scope) is not object
            or carrier._captures is not carrier._group._captures or len(carrier._captures) != 2):
        _fail("INVALID_RESULT")
    values = originals + (observed_binding, observed_content.prefix_bytes, observed_content.origin_intent_bytes,
                          observed_content.local_contract_bytes, observed_content.init_seal_bytes,
                          observed_content.genesis_grant_bytes)
    _check_bytes(values)
    references = []
    for ordinal, capture in enumerate(carrier._captures):
        groups._capture_registered(capture)
        if (carrier._capture_returns[ordinal] is not capture or capture._ordinal != ordinal
                or capture._phase != "retired" or capture._first is not None
                or capture._completed is not True or capture._finish_attempted is not True):
            _fail("INVALID_RESULT")
        references.append(weakref.ref(capture))
    payload = object.__new__(_PacketBytes)
    for name, raw in zip(_FIELDS, values):
        object.__setattr__(payload, name, raw)
    slot._operation, slot._parent, slot._effective, slot._scope = (
        carrier._operation, carrier._parent, carrier._effective, carrier._scope)
    slot._capture_refs = tuple(references)
    slot._context_pin = (slot._operation, slot._parent, slot._effective, slot._scope, slot._capture_refs)
    slot._payload, slot._payload_pin = payload, (payload, *values)
    carrier._budget.clock_only()
    slot._stage_succeeded = True


def _bind_verified_result(carrier, *, result):
    slot = _slot_for_carrier(carrier)
    if slot._bind_attempted is not False:
        _fail("CLOSED")
    slot._bind_attempted = True
    carrier = physical._lineage(carrier, installed=True)
    _check_context(slot, carrier)
    if (carrier._result is not result or carrier._result_verified is not True
            or carrier._phase != "inner_terminal_cached" or carrier._first is not None):
        _fail("INVALID_RESULT")
    values = _result_values(result)
    slot._result, slot._result_pin = result, (result, *values)


def _consume_locked(slot, *, source_consumer, source_precondition, result):
    carrier = _registered_slot(slot)
    if slot._consume_attempted is not False:
        _fail("CLOSED")
    slot._consume_attempted = True
    # No enrollment._call preclock; every full getter/lineage check follows attempt.
    carrier = physical._lineage(carrier, installed=False)
    _check_context(slot, carrier)
    if (slot._bind_attempted is not True or slot._result is not result or carrier._result is not result
            or carrier._result_verified is not True or carrier._entry_returned is not True
            or carrier._outer_owner_verified is not True or carrier._terminal_attempted is not True
            or carrier._first is not None or carrier._budget.callback_primary is not None
            or carrier._phase != "borrowed_precondition_issued"
            or type(source_consumer) is not physical._BorrowedSourceConsumer
            or carrier._consumer is not source_consumer or source_consumer._attempted is not True
            or source_consumer._result is not result or source_consumer._receipt is not source_precondition
            or carrier._receipt is not source_precondition
            or type(source_precondition) is not physical.BorrowedEnrollmentSourcePreconditionV1):
        _fail("INVALID_RESULT")
    consumer_pin = source_consumer._pins
    if (source_consumer._seal is not physical._SEAL or type(consumer_pin) is not tuple or len(consumer_pin) != 3
            or type(source_consumer._carrier_ref) is not weakref.ReferenceType
            or source_consumer._carrier_ref() is not carrier
            or any(a is not b for a, b in zip((source_consumer._carrier_ref, source_consumer._claim, result), consumer_pin))):
        _fail("INVALID_RESULT")
    receipt = source_precondition
    native_snapshot = physical.custody._JOURNAL_RECEIPT.__get__(carrier._journal, physical.custody._NativeOwnerJournal)
    if (type(receipt.format_version) is not int or receipt.format_version != 1
            or type(receipt.kind) is not str or receipt.kind != "borrowed_enrollment_source_completion_precondition"
            or type(receipt.scope) is not str or receipt.scope != "registered_enrollment_captures_native_physical_node_only"
            or receipt.native_snapshot is not native_snapshot
            or carrier._journal._reset_attempted is not True or carrier._journal._seal_attempted is not True
            or receipt.physical_directory_receipts is not carrier._directory_receipts
            or receipt.node_receipt is not carrier._node_receipt
            or receipt.registered_enrollment_sources_terminal_complete is not True or receipt.outer_ex_borrowed is not True
            or any(flag is not False for flag in (receipt.outer_ex_closed, receipt.acquisition_coverage_proven,
                                                 receipt.source_closed, receipt.write_authority))
            or type(receipt.capture_receipts) is not tuple or len(receipt.capture_receipts) != 2
            or type(carrier._capture_receipts) is not tuple or len(carrier._capture_receipts) != 2):
        _fail("INVALID_RESULT")
    for ordinal, reference in enumerate(slot._capture_refs):
        capture = reference()
        if (capture._receipt is not receipt.capture_receipts[ordinal]
                or capture._receipt is not carrier._capture_receipts[ordinal]):
            _fail("INVALID_RESULT")
        physical._capture_source_facts_complete(carrier, capture, capture._receipt)
    result_values = _result_values(result)
    result_pin = slot._result_pin
    if (type(result_pin) is not tuple or len(result_pin) != 12 or result_pin[0] is not result
            or any(a is not b for a, b in zip(result_values, result_pin[1:]))):
        _fail("INVALID_RESULT")
    values = _payload_values(slot)
    budget = carrier._budget
    budget.clock_only()
    content = contract.AuthorityPrefixContent(values[6], *values[7:])
    contract.cross_validate_authority_prefix(content, values[5], *values[:5])
    from . import personal_authority_enrollment as enrollment
    enrollment._result(result, slot._operation, tuple(contract._hash(raw) for raw in values[:5]),
                       "publish", None, None, budget)
    if result.binding_sha256 != contract._hash(values[5]) or result.prefix_sha256 != contract._hash(values[6]):
        _fail("INVALID_RESULT")
    budget.clock_only()
    return slot._payload


def _retire_for_carrier(carrier, *, primary=None):
    try:
        if carrier is None:
            return
        if type(carrier) is not physical._EnrollmentSourceCarrier:
            _fail("INVALID_ARGUMENT")
        key = id(carrier)
        if not dict.__contains__(_PACKETS, key):
            return
        row = dict.get(_PACKETS, key)
        if (type(row) is not tuple or len(row) != 3 or type(row[0]) is not weakref.ReferenceType
                or row[0]() is not carrier or type(row[1]) is not _PacketSlot
                or row[1]._seal is not _SEAL or row[1]._carrier_ref is not row[0]
                or row[1]._claim is not row[2]):
            _fail("INVALID_ARGUMENT")
        dict.pop(_PACKETS, key, None)
        _clear_slot(row[1])
    except BaseException:
        if primary is None:
            raise
        try:
            BaseException.add_note(primary, "ENROLLMENT_PACKET_ADDITIONAL_FAILURE")
        except BaseException:
            pass
