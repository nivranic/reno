"""Own ordinary outer EX exit before delivering namespace3 filesystem facts.

The outer 30-second deadline is a delivery gate. The unchanged locked factory
polls the passed clock once, then uses its own deadline; this module does not
claim cooperative outer-deadline propagation or cutoff of late file actions.
No stored facts become enrollment, write rights, or a source_closed witness.
"""
from __future__ import annotations

from contextlib import contextmanager
import time

from . import personal_authority_namespace3 as locked
from . import personal_authority_namespace3_contract as contract
from . import storage_lifecycle as lifecycle

MAX_SECONDS = 30.0


class Namespace3OwnerError(ValueError):
    def __init__(self, suffix):
        self.code = "NAMESPACE3_OWNER_" + suffix
        super().__init__(self.code)


Error = Namespace3OwnerError


def _fail(suffix):
    raise Namespace3OwnerError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "NAMESPACE3_OWNER_CLEANUP_FAILED")
    except BaseException:
        pass


class _Budget:
    def __init__(self, callback):
        # All entry time counts toward delivery, including pure validation.
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
            self.callback()  # Last and only actual user callback.
        self.clock_only()


@contextmanager
def _preserve_primary(context):
    guard = context.__enter__()
    try:
        yield guard
    except BaseException as primary:
        try:
            context.__exit__(type(primary), primary,
                BaseException.__dict__["__traceback__"].__get__(primary))
        except BaseException as cleanup:
            if cleanup is not primary:
                _note(primary)
        raise
    else:
        # A normal EX exit failure keeps its original object and blocks DTO.
        context.__exit__(None, None, None)


def _validate(profile, domain, facility, operation, first, second, reservation, seal, header, mode):
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    try:
        for value in (domain, facility, operation):
            contract._uuid(value)
        required = (first, second)
        if mode in {"resume", "verify"}:
            required += (reservation,)
        if mode == "verify":
            required += (seal, header)
        for value in required:
            contract._sha(value)
    except contract.Namespace3ContractError:
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


def _result(result, domain, facility, operation, first, second, reservation, seal, header, mode):
    if type(result) is not locked.Namespace3FilesystemFacts:
        _fail("RESULT_INVALID")
    try:
        values = (result.scope, result.init_operation_id, result.domain_id, result.facility_id,
            result.origin_reservation_sha256, result.origin_observation_sha256,
            result.namespace_reservation_sha256, result.root_seal_sha256,
            result.header_sha256, result.creation_kind)
    except AttributeError:
        _fail("RESULT_INVALID")
    if (any(type(value) is not str for value in values) or result.scope != contract.SCOPE
            or result.creation_kind != contract.CREATION_KIND
            or result.init_operation_id != operation or result.domain_id != domain or result.facility_id != facility
            or result.origin_reservation_sha256 != first or result.origin_observation_sha256 != second):
        _fail("RESULT_INVALID")
    try:
        for value in (result.origin_reservation_sha256, result.origin_observation_sha256,
                      result.namespace_reservation_sha256, result.root_seal_sha256, result.header_sha256):
            contract._sha(value)
    except contract.Namespace3ContractError:
        _fail("RESULT_INVALID")
    if mode in {"resume", "verify"} and result.namespace_reservation_sha256 != reservation:
        _fail("RESULT_INVALID")
    if mode == "verify" and (result.root_seal_sha256 != seal or result.header_sha256 != header):
        _fail("RESULT_INVALID")


def _run(profile, domain, facility, operation, first, second, reservation, seal, header, mode, callback):
    budget = _Budget(callback)
    paths = _validate(profile, domain, facility, operation, first, second, reservation, seal, header, mode)
    budget.clock_only()
    _no_guard()
    budget.poll()
    _no_guard()
    _same_profile(profile, paths)
    budget.clock_only()
    with _preserve_primary(lifecycle.exclusive(profile, timeout=0,
                                               check_cancelled=budget.clock_only)) as guard:
        _held(profile, paths, guard, budget)
        arguments = {"domain_id": domain, "facility_id": facility, "init_operation_id": operation,
            "expected_origin_reservation_sha256": first, "expected_origin_observation_sha256": second,
            "check_cancelled": budget.clock_only}
        if mode == "init":
            result = locked.initialize_namespace3_locked(profile, **arguments)
        elif mode == "resume":
            result = locked.resume_unstarted_namespace3_locked(profile, **arguments,
                expected_namespace_reservation_sha256=reservation)
        else:
            result = locked.verify_namespace3_locked(profile, **arguments,
                expected_namespace_reservation_sha256=reservation,
                expected_root_seal_sha256=seal, expected_header_sha256=header)
        # The inner callback is polled once; this postcheck rejects late facts
        # but does not assert that inner work stopped at the outer deadline.
        _held(profile, paths, guard, budget)
        _result(result, domain, facility, operation, first, second, reservation, seal, header, mode)
        _held(profile, paths, guard, budget)
    budget.clock_only()
    _no_guard()
    _same_profile(profile, paths)
    budget.clock_only()
    return result  # Same actual inner object, after this owner's normal exit.


def initialize_namespace3(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256, check_cancelled=None):
    return _run(profile, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        None, None, None, "init", check_cancelled)


def resume_unstarted_namespace3(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, check_cancelled=None):
    return _run(profile, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, None, None, "resume", check_cancelled)


def verify_namespace3(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, check_cancelled=None):
    return _run(profile, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, "verify", check_cancelled)
