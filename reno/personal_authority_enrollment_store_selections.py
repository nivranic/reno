"""Registered content plans from one real live packet continuation.

This module provides neither a native selection nor durable materials nor an
I/O permit. Its fresh budget check cannot be carried across an EX or reused as
authorization. Default source-only enrollment does not create a content plan.
"""
from __future__ import annotations

import weakref

from . import personal_authority_enrollment_store_runtime as runtime
from . import personal_authority_enrollment_successor as prepare
from . import personal_authority_enrollment_store as store
from . import personal_authority_namespace3 as namespace

_SEAL = object()
_CONTENT_ATTEMPTS = {}
_CONTENT_PLANS = {}
_PIN_NAMES = ("_originals", "_prepare_raw", "_reservation_raw", "_file_raws",
              "_file_names", "_file_maxima", "_original_locations", "_continuation_pins",
              "_group", "_guard", "_operation", "_budget", "_parent", "_effective",
              "_source_scope", "_stage_scope", "_original_bytes", "_store_bytes",
              "_authority_bytes", "_empty_io_bytes")


class StoreContentPlanError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_STORE_CONTENT_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise StoreContentPlanError(suffix) from None


def _note(first):
    try:
        BaseException.add_note(first, "ENROLLMENT_STORE_CONTENT_ADDITIONAL_FAILURE")
    except BaseException:
        pass


class _StoreContentPlan:
    __slots__ = ("_seal", "_claim", "_continuation", "_pins", "_phase", "_first",
                 "_first_tb", *_PIN_NAMES, "__weakref__")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")


def _registered_plan(plan):
    """Use independent identity rows, never damaged business fields."""
    if type(plan) is not _StoreContentPlan:
        _fail("INVALID_ARGUMENT")
    row = dict.get(_CONTENT_PLANS, id(plan))
    if (type(row) is not tuple or len(row) != 8
            or type(row[0]) is not weakref.ReferenceType
            or type(row[2]) is not weakref.ReferenceType or row[0]() is not plan):
        _fail("INVALID_ARGUMENT")
    value = row[2]()
    attempt = dict.get(_CONTENT_ATTEMPTS, id(value))
    if (value is None or type(attempt) is not tuple or len(attempt) != 4
            or attempt[0] is not row[2] or attempt[1] is not row[1]
            or attempt[2] is not row[0]):
        _fail("INVALID_ARGUMENT")
    return value, row


def _replace_row(plan, before, after):
    if dict.get(_CONTENT_PLANS, id(plan)) is not before:
        _fail("REGISTRY_CHANGED")
    _CONTENT_PLANS[id(plan)] = after


def _record_first(plan, primary):
    value, row = _registered_plan(plan)
    if row[6] is None:
        first, traceback = value._first, value._first_tb
        if first is None:
            first = primary
            traceback = BaseException.__dict__["__traceback__"].__get__(primary)
        elif primary is not first:
            _note(first)
        _replace_row(plan, row, row[:5] + ("failed", first, traceback))
        plan._first, plan._first_tb, plan._phase = first, traceback, "failed"
        runtime._remember(value, first)
    elif row[6] is not primary:
        _note(row[6])


def _raise_first(row):
    if row[6] is not None:
        raise BaseException.with_traceback(row[6], row[7])


def _checkpoint(value):
    runtime._continuation(value, live=True)
    if value._first is not None or value._phase == "retained_failed":
        if value._first is not None:
            raise BaseException.with_traceback(value._first, value._first_tb)
        _fail("CLOSED")
    value._budget.clock_only()


def _business_pins(plan, value, row):
    pins = row[4]
    if (plan._seal is not _SEAL or plan._claim is not row[1]
            or plan._phase != row[5] or plan._first is not row[6] or plan._first_tb is not row[7]
            or plan._continuation is not value or plan._pins is not pins
            or type(pins) is not tuple or len(pins) != len(_PIN_NAMES)
            or any(getattr(plan, name) is not prior for name, prior in zip(_PIN_NAMES, pins))
            or value._claim is not row[3] or value._pins is not plan._continuation_pins
            or value._raws is not plan._originals
            or any(getattr(value, name) is not getattr(plan, name) for name in
                ("_group", "_guard", "_operation", "_budget", "_parent", "_effective",
                 "_source_scope", "_stage_scope"))
            or store.FILE_NAMES is not plan._file_names
            or store._FILE_MAXIMA is not plan._file_maxima
            or store._ORIGINAL_LOCATIONS is not plan._original_locations):
        _fail("BINDING_INVALID")
    if (type(plan._originals) is not tuple or len(plan._originals) != 11
            or any(type(raw) is not bytes for raw in plan._originals)
            or type(plan._prepare_raw) is not bytes or type(plan._reservation_raw) is not bytes
            or type(plan._file_raws) is not tuple or len(plan._file_raws) != 29
            or any(type(raw) is not bytes for raw in plan._file_raws)
            or plan._file_raws[24] != plan._prepare_raw
            or plan._file_raws[25] != plan._reservation_raw
            or type(plan._file_names) is not tuple or len(plan._file_names) != 29
            or type(plan._file_maxima) is not tuple or len(plan._file_maxima) != 29
            or type(plan._original_locations) is not tuple or len(plan._original_locations) != 11):
        _fail("BINDING_INVALID")
    original_bytes = sum(map(len, plan._originals))
    store_bytes = sum(map(len, plan._file_raws))
    authority_bytes = sum(map(len, plan._originals[7:11]))
    if (any(type(getattr(plan, name)) is not int for name in
            ("_original_bytes", "_store_bytes", "_authority_bytes", "_empty_io_bytes"))
            or plan._original_bytes != original_bytes or plan._store_bytes != store_bytes
            or plan._authority_bytes != authority_bytes
            or plan._empty_io_bytes != 6 * store_bytes + 2 * original_bytes + authority_bytes):
        _fail("BINDING_INVALID")


def _content_plan(plan, *, continuation):
    """Return this actual plan only while its original EX remains live."""
    value, row = _registered_plan(plan)
    if row[5] == "retired":
        _fail("CLOSED")
    if continuation is not value:
        _fail("INVALID_ARGUMENT")
    _raise_first(row)
    try:
        value = runtime._minimal(value)
        if value._first is not None:
            _record_first(plan, value._first)
            _value, current = _registered_plan(plan)
            _raise_first(current)
        _business_pins(plan, value, row)
        _checkpoint(value)
        return plan
    except BaseException as primary:
        _record_first(plan, primary)
        _value, current = _registered_plan(plan)
        _raise_first(current)
        raise


def _require_empty_io_budget(plan, *, continuation):
    """A fresh same-budget k0 precheck, never an I/O permit or saved balance."""
    plan = _content_plan(plan, continuation=continuation)
    try:
        budget = plan._budget
        budget.clock_only()
        current = budget.control_bytes
        if type(current) is not int or not 0 <= current <= namespace.MAX_CONTROL_BYTES:
            _fail("BUDGET_INVALID")
        if current + plan._empty_io_bytes >= namespace.MAX_CONTROL_BYTES:
            _fail("LIMIT")
        _checkpoint(continuation)
    except BaseException as primary:
        _record_first(plan, primary)
        raise
    return None


def _new_content_plan(continuation):
    """Claim before full lineage or producers; retain their exact returns."""
    value = runtime._minimal(continuation)
    key = id(value)
    if dict.get(_CONTENT_ATTEMPTS, key) is not None:
        _fail("CLOSED")
    claim = object()
    def continuation_collected(reference, *, key=key, claim=claim):
        row = dict.get(_CONTENT_ATTEMPTS, key)
        if type(row) is tuple and len(row) == 4 and row[0] is reference and row[1] is claim:
            dict.pop(_CONTENT_ATTEMPTS, key, None)
    value_ref = weakref.ref(value, continuation_collected)
    attempted = (value_ref, claim, None, "attempted")
    _CONTENT_ATTEMPTS[key] = attempted
    plan = None
    try:
        if (value._phase != "available" or value._attempted is not False
                or value._stage is not None or value._journal is not None):
            _fail("CLOSED")
        _checkpoint(value)
        originals = value._raws
        if type(originals) is not tuple or len(originals) != 11 or any(type(raw) is not bytes for raw in originals):
            _fail("BINDING_INVALID")
        prepare_raw = prepare.build_prepare(originals=originals)
        _checkpoint(value)
        if type(prepare_raw) is not bytes or not 0 < len(prepare_raw) <= prepare.MAX_PREPARE_BYTES:
            _fail("CONTENT_RETURN_INVALID")
        _checkpoint(value)
        prepare_cross = prepare.cross_validate_prepare(prepare_raw, originals=originals)
        _checkpoint(value)
        if prepare_cross is not None:
            _fail("CONTENT_RETURN_INVALID")
        _checkpoint(value)
        reservation_raw = prepare.build_reservation(prepare_raw, originals=originals)
        _checkpoint(value)
        if type(reservation_raw) is not bytes or not 0 < len(reservation_raw) <= prepare.MAX_RESERVATION_BYTES:
            _fail("CONTENT_RETURN_INVALID")
        _checkpoint(value)
        reservation_cross = prepare.cross_validate_reservation(reservation_raw, prepare_raw, originals=originals)
        _checkpoint(value)
        if reservation_cross is not None:
            _fail("CONTENT_RETURN_INVALID")
        _checkpoint(value)
        file_raws = store.build_prepare_files(originals=originals,
            prepare_payload_raw=prepare_raw, prepare_content_reservation_raw=reservation_raw)
        _checkpoint(value)
        if (type(file_raws) is not tuple or len(file_raws) != 29
                or any(type(raw) is not bytes or not 0 < len(raw) <= maximum
                    for raw, maximum in zip(file_raws, store._FILE_MAXIMA))
                or file_raws[24] != prepare_raw or file_raws[25] != reservation_raw):
            _fail("CONTENT_RETURN_INVALID")
        _checkpoint(value)
        file_cross = store.cross_validate_prepare_files(file_raws, originals=originals,
            prepare_payload_raw=prepare_raw, prepare_content_reservation_raw=reservation_raw)
        _checkpoint(value)
        if file_cross is not None:
            _fail("CONTENT_RETURN_INVALID")
        plan = object.__new__(_StoreContentPlan)
        plan._seal, plan._claim, plan._continuation = _SEAL, claim, value
        plan._originals, plan._prepare_raw, plan._reservation_raw = originals, prepare_raw, reservation_raw
        plan._file_raws, plan._file_names = file_raws, store.FILE_NAMES
        plan._file_maxima, plan._original_locations = store._FILE_MAXIMA, store._ORIGINAL_LOCATIONS
        plan._continuation_pins = value._pins
        for name in ("_group", "_guard", "_operation", "_budget", "_parent", "_effective", "_source_scope", "_stage_scope"):
            setattr(plan, name, getattr(value, name))
        plan._original_bytes = sum(map(len, originals))
        plan._store_bytes = sum(map(len, file_raws))
        plan._authority_bytes = sum(map(len, originals[7:11]))
        plan._empty_io_bytes = 6 * plan._store_bytes + 2 * plan._original_bytes + plan._authority_bytes
        plan._pins = tuple(getattr(plan, name) for name in _PIN_NAMES)
        plan._phase, plan._first, plan._first_tb = "live", None, None
        plan_key = id(plan)
        def plan_collected(reference, *, key=plan_key, claim=claim):
            row = dict.get(_CONTENT_PLANS, key)
            if type(row) is tuple and len(row) == 8 and row[0] is reference and row[1] is claim:
                dict.pop(_CONTENT_PLANS, key, None)
        plan_ref = weakref.ref(plan, plan_collected)
        if dict.get(_CONTENT_ATTEMPTS, key) is not attempted or dict.get(_CONTENT_PLANS, plan_key) is not None:
            _fail("REGISTRY_CHANGED")
        _CONTENT_PLANS[plan_key] = (plan_ref, claim, value_ref, value._claim, plan._pins, "live", None, None)
        _CONTENT_ATTEMPTS[key] = (value_ref, claim, plan_ref, "planned")
        _require_empty_io_budget(plan, continuation=value)
        return plan
    except BaseException as primary:
        runtime._remember(value, primary)
        if plan is not None and dict.get(_CONTENT_PLANS, id(plan)) is not None:
            _retire_content_plan(plan, primary=primary)
        raise


def _retire_content_plan(plan, *, primary=None):
    """Release by original rows even when business pins or claim are damaged."""
    value, row = _registered_plan(plan)
    if row[5] == "retired":
        _fail("CLOSED")
    if primary is not None:
        _record_first(plan, primary)
        value, row = _registered_plan(plan)
    try:
        _business_pins(plan, value, row)
    except BaseException as failure:
        _record_first(plan, failure)
    value, row = _registered_plan(plan)
    first, traceback = row[6], row[7]
    attempt = dict.get(_CONTENT_ATTEMPTS, id(value))
    _replace_row(plan, row, row[:4] + ((), "retired", None, None))
    _CONTENT_ATTEMPTS[id(value)] = attempt[:3] + ("retired",)
    plan._phase, plan._pins = "retired", ()
    plan._continuation = plan._first = plan._first_tb = None
    for name in _PIN_NAMES:
        setattr(plan, name, None)
    if first is not None and primary is None:
        raise BaseException.with_traceback(first, traceback)
    return None


def _retire_for_continuation(continuation, *, primary=None):
    """Fixed runtime lifetime seam; never produce content on source-only use."""
    row = dict.get(_CONTENT_ATTEMPTS, id(continuation))
    if row is None:
        return None
    if (type(row) is not tuple or len(row) != 4
            or type(row[0]) is not weakref.ReferenceType or row[0]() is not continuation):
        _fail("REGISTRY_CHANGED")
    if row[3] == "retired" or row[2] is None:
        return None
    if type(row[2]) is not weakref.ReferenceType:
        _fail("REGISTRY_CHANGED")
    plan = row[2]()
    if plan is None:
        _CONTENT_ATTEMPTS[id(continuation)] = row[:3] + ("retired",)
        return None
    return _retire_content_plan(plan, primary=primary)
