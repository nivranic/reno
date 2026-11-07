"""Own the outer EX through normal exit before delivering two declarations.

This public shim refuses an already-held guard, because lifecycle.exclusive
would otherwise borrow it. It calls the existing locked reservation API and
returns its same three-string result only after this shim's actual EX context
returns normally and the original deadline still holds. No namespace, grant,
live witness, journal or complete native/source-close proof is created here.
"""
from __future__ import annotations

from contextlib import contextmanager
import time

from . import personal_authority_origin_reservation as locked
from . import personal_authority_origin_reservation_contract as contract
from . import storage_lifecycle as lifecycle

MAX_SECONDS = 30.0


class OriginReservationOwnerError(ValueError):
    def __init__(self, suffix):
        self.code = "ORIGIN_RESERVATION_OWNER_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise OriginReservationOwnerError(suffix) from None


class _Budget:
    def __init__(self, callback):
        # Start before validation, current-guard observation or EX acquisition.
        self.deadline = time.monotonic() + MAX_SECONDS
        self.callback = callback
        if callback is not None and not callable(callback):
            _fail("INVALID_ARGUMENT")

    def clock_only(self):
        if time.monotonic() >= self.deadline:
            _fail("DEADLINE")

    def poll(self):
        self.clock_only()
        if self.callback is not None:
            # Poll once before EX. False-valued callables and their original
            # exceptions retain ordinary dispatch; no truthiness fallback.
            self.callback()
        self.clock_only()


@contextmanager
def _preserve_primary(context):
    guard = context.__enter__()
    try:
        yield guard
    except BaseException as primary:
        original_tb = BaseException.__dict__["__traceback__"].__get__(primary)
        try:
            context.__exit__(type(primary), primary, original_tb)
        except BaseException as cleanup:
            if cleanup is not primary:
                try:
                    BaseException.add_note(primary, "ORIGIN_RESERVATION_OWNER_EX_CLEANUP_FAILED")
                except BaseException:
                    pass
        raise
    else:
        # Preserve a normal EX close/verification failure as its raw object.
        context.__exit__(None, None, None)


def _validate(profile, domain_id, facility_id, operation, expected_first, expected_second, resume):
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    try:
        for value in (domain_id, facility_id, operation):
            contract._uuid(value)
        if resume:
            contract._sha(expected_first)
            if expected_second is not None:
                contract._sha(expected_second)
    except contract.OriginReservationContractError:
        _fail("INVALID_ARGUMENT")
    return tuple(profile.identity)


def _same_profile(profile, paths):
    if type(profile) is not lifecycle.StorageProfile or tuple(profile.identity) != paths:
        _fail("PROFILE_CHANGED")


def _no_guard():
    if lifecycle.current_guard() is not None:
        _fail("GUARD_ALREADY_HELD")


def _held(profile, paths, guard, budget):
    budget.clock_only()
    _same_profile(profile, paths)
    if lifecycle.require_guard(profile, exclusive=True) is not guard:
        _fail("PROFILE_CHANGED")
    budget.clock_only()


def _result(result, operation):
    if type(result) is not locked.OriginReservationResult or result.init_operation_id != operation:
        _fail("RESULT_INVALID")
    try:
        contract._sha(result.reservation_sha256)
        contract._sha(result.observation_sha256)
    except contract.OriginReservationContractError:
        _fail("RESULT_INVALID")


def _run(profile, domain_id, facility_id, operation, callback, *, resume=False,
         expected_first=None, expected_second=None):
    budget = _Budget(callback)
    paths = _validate(profile, domain_id, facility_id, operation, expected_first, expected_second, resume)
    budget.clock_only()
    _no_guard()
    budget.poll()  # Last and only user callback; no new EX effect before it.
    _no_guard()
    _same_profile(profile, paths)
    budget.clock_only()
    with _preserve_primary(lifecycle.exclusive(profile, timeout=0,
                                               check_cancelled=budget.clock_only)) as guard:
        _held(profile, paths, guard, budget)
        arguments = {"domain_id": domain_id, "facility_id": facility_id,
                     "init_operation_id": operation, "check_cancelled": budget.clock_only}
        if resume:
            result = locked.resume_origin_locked(profile, **arguments,
                expected_reservation_sha256=expected_first, expected_observation_sha256=expected_second)
        else:
            result = locked.reserve_origin_locked(profile, **arguments)
        _result(result, operation)
        _held(profile, paths, guard, budget)
    # Result stays internal until this owner, rather than a borrowed caller,
    # has exited. Native/source closer completeness remains a separate gate.
    budget.clock_only()
    _no_guard()
    _same_profile(profile, paths)
    budget.clock_only()
    return result


def reserve_origin(profile, *, domain_id, facility_id, init_operation_id, check_cancelled=None):
    """Own a fresh immediate EX; return the existing two-declaration result."""
    return _run(profile, domain_id, facility_id, init_operation_id, check_cancelled)


def resume_origin(profile, *, domain_id, facility_id, init_operation_id,
                  expected_reservation_sha256, expected_observation_sha256=None, check_cancelled=None):
    """Own EX for the existing strict resume; never adopt stages or refresh raw."""
    return _run(profile, domain_id, facility_id, init_operation_id, check_cancelled, resume=True,
                expected_first=expected_reservation_sha256, expected_second=expected_observation_sha256)
