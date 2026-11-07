"""Own a fresh EX and return the original result only after normal retirement.

The single thirty-second token starts before validation or guard acquisition.
No existing SH/EX is borrowed.  Lower components receive this same effective
token and clock-only callbacks; the user is polled once before the new EX.
Declarations remain candidates, not grants or complete source-close witnesses.
"""
from __future__ import annotations

import math
import time

from . import personal_authority_origin_reservation as ordinary_locked
from . import personal_authority_origin_reservation_owner as ordinary
from . import personal_authority_origin_reservation_deadline as locked
from . import personal_authority_origin_reservation_contract as contract
from . import personal_authority_namespace3_deadline as deadline
from . import storage_lifecycle as lifecycle

OriginReservationOwnerError = ordinary.OriginReservationOwnerError
Error = OriginReservationOwnerError
MAX_SECONDS = 30.0


def _fail(suffix):
    raise OriginReservationOwnerError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "ORIGIN_RESERVATION_OWNER_EX_CLEANUP_FAILED")
    except BaseException:
        pass


# The sixteen fixed aliases retain their locked signatures and return domains.
def _monotonic():
    return time.monotonic()


def _deadline(absolute):
    return deadline.Namespace3Deadline(absolute)


def _restricted_budget(requested):
    return deadline._RestrictedBudget(requested)


def _effective_token(budget):
    return budget.effective_deadline


def _uuid(value):
    return contract._uuid(value)


def _sha(value):
    return contract._sha(value)


def _profile_paths(profile):
    return tuple(profile.identity)


def _current_guard():
    return lifecycle.current_guard()


def _require_guard(profile, *, exclusive=True):
    return lifecycle.require_guard(profile, exclusive=exclusive)


def _exclusive(profile, budget, *, state):
    initial_pending = True

    def initial_poll():
        nonlocal initial_pending
        _raise_first(state)
        _call(state, _acquisition_clock, budget, legal=lambda value: value is None)
        if initial_pending:
            _no_guard(state)
            initial_pending = False

    return lifecycle.exclusive(profile, timeout=0, check_cancelled=initial_poll)


def _acquisition_clock(budget):
    return budget.clock_only()


def _exclusive_enter(context):
    return context.__enter__()


def _exclusive_exit(context, kind, primary, tb):
    return context.__exit__(kind, primary, tb)


def _reserve_locked(profile, *, domain_id, facility_id, init_operation_id,
                    deadline_token, check_cancelled):
    return locked.reserve_origin_locked(profile, domain_id=domain_id,
        facility_id=facility_id, init_operation_id=init_operation_id,
        deadline_token=deadline_token, check_cancelled=check_cancelled)


def _resume_locked(profile, *, domain_id, facility_id, init_operation_id,
                   expected_reservation_sha256, expected_observation_sha256,
                   deadline_token, check_cancelled):
    return locked.resume_origin_locked(profile, domain_id=domain_id,
        facility_id=facility_id, init_operation_id=init_operation_id,
        expected_reservation_sha256=expected_reservation_sha256,
        expected_observation_sha256=expected_observation_sha256,
        deadline_token=deadline_token, check_cancelled=check_cancelled)


def _validate_result(result, operation):
    # Complete original _result conversion, before the enclosing I gate.
    if type(result) is not ordinary_locked.OriginReservationResult or result.init_operation_id != operation:
        _fail("RESULT_INVALID")
    try:
        _sha(result.reservation_sha256)
        _sha(result.observation_sha256)
    except contract.OriginReservationContractError:
        _fail("RESULT_INVALID")


class _FirstState:
    __slots__ = ("token", "first", "first_tb")

    def __init__(self, token):
        self.token, self.first, self.first_tb = token, None, None


def _remember(state, failure, tb=None):
    if state.first is None:
        state.first = failure
        state.first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                          if tb is None else tb)
    elif failure is not state.first:
        _note(state.first)


def _raise_first(state):
    if state.first is not None:
        raise BaseException.with_traceback(state.first, state.first_tb) from None


def _clock(state):
    _raise_first(state)
    try:
        now = _monotonic()
        if type(now) is not float or not math.isfinite(now) or now < 0.0:
            _fail("INVALID_ARGUMENT")
        if now >= state.token.absolute_monotonic:
            _fail("DEADLINE")
    except BaseException as failure:
        _remember(state, failure)
        _raise_first(state)


def _cleanup_clock(state):
    try:
        _clock(state)
    except BaseException as failure:
        _remember(state, failure)


def _call(first_state, operation, /, *args, legal=None, **kwargs):
    _clock(first_state)
    try:
        result = operation(*args, **kwargs)
        if legal is not None and not legal(result):
            _fail("RESULT_INVALID")
    except BaseException as failure:
        _remember(first_state, failure)
        _raise_first(first_state)
    _clock(first_state)
    return result


def _cpu(first_state, operation, /, *args, legal=None, **kwargs):
    return _call(first_state, operation, *args, legal=legal, **kwargs)


def _paths(value):
    return (type(value) is tuple and len(value) == 4
            and all(type(item) is str for item in value))


class _Budget:
    __slots__ = ("restricted", "effective_deadline", "state", "callback")

    def __init__(self, restricted, effective, callback):
        self.restricted, self.effective_deadline = restricted, effective
        self.state, self.callback = _FirstState(effective), callback

    def clock_only(self):
        _clock(self.state)

    def poll(self):
        self.clock_only()
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                _remember(self.state, primary)
                raise
        self.clock_only()


def _make_budget(callback):
    # The first raw clock is the only start.  Invalid input cannot reach token,
    # restricted factory, profile/UUID, guard, or any resource operation.
    start = _monotonic()
    if type(start) is not float or not math.isfinite(start) or start < 0.0:
        _fail("INVALID_ARGUMENT")
    absolute = start + MAX_SECONDS
    if type(absolute) is not float or not math.isfinite(absolute) or absolute < 0.0:
        _fail("INVALID_ARGUMENT")
    requested = _deadline(absolute)
    try:
        deadline._token(requested)
    except deadline.Namespace3Error:
        _fail("RESULT_INVALID")
    if callback is not None and not callable(callback):
        _fail("INVALID_ARGUMENT")
    try:
        restricted = _restricted_budget(requested)
    except deadline.Namespace3Error as error:
        if error.code == "NAMESPACE3_DEADLINE":
            _fail("DEADLINE")
        raise
    if type(restricted) is not deadline._RestrictedBudget:
        _fail("RESULT_INVALID")
    effective = _effective_token(restricted)
    try:
        deadline._token(effective)
    except deadline.Namespace3Error:
        _fail("RESULT_INVALID")
    if (effective is not restricted.effective_deadline
            or effective.absolute_monotonic > requested.absolute_monotonic):
        _fail("RESULT_INVALID")
    result = _Budget(restricted, effective, callback)
    _clock(result.state)
    return result


def _validated_uuid(value):
    # Preserve the original _validate contract conversion before first is set.
    try:
        return _uuid(value)
    except contract.OriginReservationContractError:
        _fail("INVALID_ARGUMENT")


def _validated_sha(value):
    try:
        return _sha(value)
    except contract.OriginReservationContractError:
        _fail("INVALID_ARGUMENT")


def _validate(profile, domain_id, facility_id, operation, expected_first,
              expected_second, resume, state):
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    for value in (domain_id, facility_id, operation):
        _cpu(state, _validated_uuid, value, legal=lambda result: type(result) is str)
    if resume:
        _cpu(state, _validated_sha, expected_first, legal=lambda result: type(result) is str)
        if expected_second is not None:
            _cpu(state, _validated_sha, expected_second, legal=lambda result: type(result) is str)
    return _cpu(state, _profile_paths, profile, legal=_paths)


def _same_profile(profile, paths, state):
    if (type(profile) is not lifecycle.StorageProfile
            or _cpu(state, _profile_paths, profile, legal=_paths) != paths):
        _fail("PROFILE_CHANGED")


def _no_guard(state):
    current = _call(state, _current_guard,
        legal=lambda value: value is None or type(value) is lifecycle.StorageGuard)
    if current is not None:
        _fail("GUARD_ALREADY_HELD")


def _held(profile, paths, guard, budget):
    state = budget.state
    budget.clock_only()
    _same_profile(profile, paths, state)
    if _call(state, _require_guard, profile,
             legal=lambda value: type(value) is lifecycle.StorageGuard) is not guard:
        _fail("PROFILE_CHANGED")
    budget.clock_only()


class _ExclusiveScope:
    __slots__ = ("state", "context", "entered", "attempted", "normal")

    def __init__(self, state, context):
        self.state, self.context = state, context
        self.entered = self.attempted = self.normal = False

    def __enter__(self):
        _clock(self.state)
        try:
            guard = _exclusive_enter(self.context)
            if type(guard) is not lifecycle.StorageGuard:
                _fail("RESULT_INVALID")
        except BaseException as primary:
            # No legal actual guard return means no outer-owned exit attempt.
            _remember(self.state, primary)
            _raise_first(self.state)
        self.entered = True
        try:
            _clock(self.state)
        except BaseException as primary:
            self.__exit__(type(primary), primary,
                          BaseException.__dict__["__traceback__"].__get__(primary))
            raise
        return guard

    def __exit__(self, kind, primary, tb):
        if primary is not None:
            _remember(self.state, primary, tb)
        if not self.entered or self.attempted:
            _fail("RESULT_INVALID")
        self.attempted = True
        _cleanup_clock(self.state)
        first, first_tb = self.state.first, self.state.first_tb
        try:
            result = _exclusive_exit(self.context, None if first is None else type(first),
                                     first, first_tb)
            if result is not False:
                _fail("RESULT_INVALID")
            self.normal = True
        except BaseException as failure:
            _remember(self.state, failure)
        _cleanup_clock(self.state)
        _raise_first(self.state)
        return None


def _run(profile, domain_id, facility_id, operation, callback, *, resume=False,
         expected_first=None, expected_second=None):
    budget = _make_budget(callback)
    state, token = budget.state, budget.effective_deadline
    paths = _validate(profile, domain_id, facility_id, operation,
                      expected_first, expected_second, resume, state)
    budget.clock_only()
    _no_guard(state)
    budget.poll()  # The only user callback, before any new EX owner exists.
    _no_guard(state)
    _same_profile(profile, paths, state)
    budget.clock_only()
    context = _call(state, _exclusive, profile, budget, state=state)
    with _ExclusiveScope(state, context) as guard:
        _held(profile, paths, guard, budget)
        arguments = {"domain_id": domain_id, "facility_id": facility_id,
                     "init_operation_id": operation, "deadline_token": token,
                     "check_cancelled": budget.clock_only}
        if resume:
            result = _call(state, _resume_locked, profile, **arguments,
                expected_reservation_sha256=expected_first,
                expected_observation_sha256=expected_second,
                legal=lambda value: type(value) is ordinary_locked.OriginReservationResult)
        else:
            result = _call(state, _reserve_locked, profile, **arguments,
                legal=lambda value: type(value) is ordinary_locked.OriginReservationResult)
        _cpu(state, _validate_result, result, operation, legal=lambda value: value is None)
        _held(profile, paths, guard, budget)
    # Keep the result internal until our actual EX, rather than a borrowed
    # caller's guard, returned normally.  Then retain the original final gates.
    budget.clock_only()
    _no_guard(state)
    _same_profile(profile, paths, state)
    budget.clock_only()
    return result


def reserve_origin(profile, *, domain_id, facility_id, init_operation_id,
                   check_cancelled=None):
    """Own a fresh immediate EX and return the original three-string result."""
    return _run(profile, domain_id, facility_id, init_operation_id, check_cancelled)


def resume_origin(profile, *, domain_id, facility_id, init_operation_id,
                  expected_reservation_sha256, expected_observation_sha256=None,
                  check_cancelled=None):
    """Own EX for the same strict complete-byte resume; never adopt stages."""
    return _run(profile, domain_id, facility_id, init_operation_id,
        check_cancelled, resume=True, expected_first=expected_reservation_sha256,
        expected_second=expected_observation_sha256)
