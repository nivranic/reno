"""Own EX and pass one immutable deadline to namespace3's existing checkpoints.

This new owner explicitly dispatches the restricted adapter. It never replaces
old module globals, borrows a caller guard, or injects a caller clock/budget.
Late opaque calls and incomplete native-close witnesses remain outside scope.
Only the original actual filesystem facts may leave after this ordinary EX exit.
"""
from __future__ import annotations

import math
import time

from . import personal_authority_namespace3_deadline as adapter
from . import personal_authority_namespace3_owner as ordinary
from . import storage_lifecycle as lifecycle

MAX_SECONDS = 30.0
Namespace3OwnerError = ordinary.Namespace3OwnerError
Error = Namespace3OwnerError


def _now():
    value = time.monotonic()
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        ordinary._fail("INVALID_ARGUMENT")
    return value


class _OwnerBudget(ordinary._Budget):
    def __init__(self, callback):
        # Start before argument validation, the sole user poll, and actual EX.
        absolute = _now() + MAX_SECONDS
        try:
            self._deadline_token = adapter.Namespace3Deadline(absolute)
        except adapter.Namespace3Error:
            ordinary._fail("INVALID_ARGUMENT")
        self.callback = callback
        if callback is not None and not callable(callback):
            ordinary._fail("INVALID_ARGUMENT")

    @property
    def deadline_token(self):
        return self._deadline_token

    @property
    def deadline(self):
        return self._deadline_token.absolute_monotonic

    def clock_only(self):
        if _now() >= self.deadline:
            ordinary._fail("DEADLINE")

    # The unchanged ordinary poll performs the only actual user callback.


def _run(profile, domain, facility, operation, first, second,
         reservation, seal, header, mode, callback):
    budget = _OwnerBudget(callback)
    paths = ordinary._validate(profile, domain, facility, operation,
                               first, second, reservation, seal, header, mode)
    budget.clock_only()
    ordinary._no_guard()
    budget.poll()
    ordinary._no_guard()
    ordinary._same_profile(profile, paths)
    budget.clock_only()
    with ordinary._preserve_primary(lifecycle.exclusive(
            profile, timeout=0, check_cancelled=budget.clock_only)) as guard:
        ordinary._held(profile, paths, guard, budget)
        arguments = {"domain_id": domain, "facility_id": facility,
            "init_operation_id": operation,
            "expected_origin_reservation_sha256": first,
            "expected_origin_observation_sha256": second,
            "deadline_token": budget.deadline_token}
        if mode == "init":
            result = adapter.initialize_namespace3_locked(profile, **arguments)
        elif mode == "resume":
            result = adapter.resume_unstarted_namespace3_locked(profile, **arguments,
                expected_namespace_reservation_sha256=reservation)
        else:
            result = adapter.verify_namespace3_locked(profile, **arguments,
                expected_namespace_reservation_sha256=reservation,
                expected_root_seal_sha256=seal, expected_header_sha256=header)
        ordinary._held(profile, paths, guard, budget)
        ordinary._result(result, domain, facility, operation,
                         first, second, reservation, seal, header, mode)
        ordinary._held(profile, paths, guard, budget)
    # Cleanup is never skipped at expiry. A normal EX exit must return first.
    budget.clock_only()
    ordinary._no_guard()
    ordinary._same_profile(profile, paths)
    budget.clock_only()
    return result


def initialize_namespace3(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        check_cancelled=None):
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
