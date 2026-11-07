"""Cooperatively clip existing namespace3 checkpoints to one absolute deadline.

The immutable token is process-local clock data, not authority or stored facts.
Old opaque helpers can still cross expiry before their next existing checkpoint;
this adapter does not promise a per-native-call gate or a hard interruption.
The caller retains its actual EX and all original namespace3 ordinary exits.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import time

from . import personal_authority_namespace3 as locked

Namespace3Error = locked.Namespace3Error
Error = Namespace3Error


def _absolute(value):
    if type(value) is not float or not math.isfinite(value) or value < 0.0:
        locked._fail("INVALID_ARGUMENT")
    return value


def _now():
    return _absolute(time.monotonic())


@dataclass(frozen=True, slots=True)
class Namespace3Deadline:
    absolute_monotonic: float

    def __post_init__(self):
        _absolute(self.absolute_monotonic)


def _token(value):
    if type(value) is not Namespace3Deadline:
        locked._fail("INVALID_ARGUMENT")
    try:
        absolute = value.absolute_monotonic
    except AttributeError:
        locked._fail("INVALID_ARGUMENT")
    _absolute(absolute)
    return value


class _RestrictedBudget(locked._Budget):
    """Keep original counters/methods; own a deadline that only tightens 30s."""
    def __init__(self, deadline_token):
        # Do not call the old initializer: it would create a mutable deadline
        # using that module's clock. No caller budget or clock callback enters.
        local_start = _now()
        local_deadline = _absolute(local_start + locked.MAX_SECONDS)
        requested = _token(deadline_token)
        effective = (requested if requested.absolute_monotonic <= local_deadline
                     else Namespace3Deadline(local_deadline))
        self._requested_deadline, self._effective_deadline = requested, effective
        self.callback, self.callback_primary = None, None
        self.names = self.name_bytes = self.control_bytes = 0
        self.clock_only()

    @property
    def requested_deadline(self):
        return self._requested_deadline

    @property
    def effective_deadline(self):
        return self._effective_deadline

    @property
    def deadline(self):
        return self._effective_deadline.absolute_monotonic

    def clock_only(self):
        if self.callback_primary is not None:
            raise self.callback_primary
        if _now() >= self.deadline:
            locked._fail("DEADLINE")

    # poll/before_next/name/request/charge are the complete original methods.
    # In particular, charge counts successful actual bytes before its clock.


def _entry(profile, domain, facility, operation, first, second,
           reservation, seal, header, mode, deadline_token):
    budget = _RestrictedBudget(deadline_token)
    try:
        # Old _entry/locked public functions would replace this budget.
        return locked._perform(profile, domain, facility, operation, first, second,
                               reservation, seal, header, mode, budget)
    except BaseException as error:
        if budget.callback_primary is not None and error is not budget.callback_primary:
            locked._note(budget.callback_primary)
            raise budget.callback_primary from None
        raise


def initialize_namespace3_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        deadline_token: Namespace3Deadline):
    return _entry(profile, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        None, None, None, "init", deadline_token)


def resume_unstarted_namespace3_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, deadline_token: Namespace3Deadline):
    return _entry(profile, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, None, None, "resume", deadline_token)


def verify_namespace3_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, deadline_token: Namespace3Deadline):
    return _entry(profile, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, "verify", deadline_token)
