"""Own one immediate EX and deliver namespace facts after its normal exit.

The single thirty-second token starts before validation and the sole user poll.
Fixed public lower calls receive that token. Existing caller SH/EX is rejected;
these filesystem facts do not install authority or prove source_closed.
"""
from __future__ import annotations

import contextlib
import math
import time

from . import personal_authority_namespace3 as facts
from . import personal_authority_namespace3_owner as ordinary
from . import personal_authority_namespace3_contract as contract
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_namespace3_effect_deadline as locked
from . import storage_lifecycle as lifecycle

MAX_SECONDS = 30.0
Namespace3OwnerError = ordinary.Namespace3OwnerError
Error = Namespace3OwnerError


def _fail(suffix):
    raise Namespace3OwnerError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "NAMESPACE3_OWNER_CLEANUP_FAILED")
    except BaseException:
        pass


class _OwnerState:
    __slots__ = ("token", "first", "first_tb")

    def __init__(self, token):
        self.token, self.first, self.first_tb = token, None, None


def _remember(state, primary, tb=None):
    # First is committed before any potentially failing traceback bookkeeping.
    if state.first is None:
        state.first = primary
        try:
            state.first_tb = (BaseException.__dict__["__traceback__"].__get__(primary)
                              if tb is None else tb)
        except BaseException:
            _note(primary)
    elif primary is not state.first:
        _note(state.first)


def _raise_first(state):
    if state.first is not None:
        raise BaseException.with_traceback(state.first, state.first_tb) from None


def _monotonic():
    return time.monotonic()


def _deadline(absolute):
    return deadline.Namespace3Deadline(absolute)


def _clock(state):
    _raise_first(state)
    try:
        now = _monotonic()
        if type(now) is not float or not math.isfinite(now) or now < 0.0:
            _fail("INVALID_ARGUMENT")
        if now >= state.token.absolute_monotonic:
            _fail("DEADLINE")
    except BaseException as primary:
        _remember(state, primary)
        if state.first is not primary:
            _raise_first(state)
        raise


def _cleanup_clock(state):
    try:
        _clock(state)
    except BaseException as primary:
        _remember(state, primary)


def _call(state, operation, /, *args, legal=None, **kwargs):
    # Private call sites below select only their fixed aliases.
    _clock(state)
    try:
        result = operation(*args, **kwargs)
        if legal is not None and not legal(result):
            _fail("RESULT_INVALID")
    except BaseException as primary:
        _remember(state, primary)
        if state.first is not primary:
            _raise_first(state)
        raise
    _clock(state)
    return result


def _uuid(value):
    try:
        return contract._uuid(value)
    except contract.Namespace3ContractError:
        _fail("INVALID_ARGUMENT")


def _sha(value):
    try:
        return contract._sha(value)
    except contract.Namespace3ContractError:
        _fail("INVALID_ARGUMENT")


def _profile_paths(profile):
    return tuple(profile.identity)


def _paths(value):
    return (type(value) is tuple and len(value) == 4
            and all(type(item) is str for item in value))


def _current_guard():
    return lifecycle.current_guard()


def _require_guard(profile):
    return lifecycle.require_guard(profile, exclusive=True)


def _exclusive(profile, budget):
    return lifecycle.exclusive(profile, timeout=0,
                               check_cancelled=budget.acquisition_clock)


def _exclusive_enter(context):
    return contextlib._GeneratorContextManager.__enter__(context)


def _exclusive_exit(context, kind, primary, tb):
    return contextlib._GeneratorContextManager.__exit__(context, kind, primary, tb)


def _initialize_locked(profile, *, domain_id, facility_id, init_operation_id,
                       expected_origin_reservation_sha256,
                       expected_origin_observation_sha256, deadline_token):
    return locked.initialize_namespace3_locked(profile, domain_id=domain_id,
        facility_id=facility_id, init_operation_id=init_operation_id,
        expected_origin_reservation_sha256=expected_origin_reservation_sha256,
        expected_origin_observation_sha256=expected_origin_observation_sha256,
        deadline_token=deadline_token)


def _resume_locked(profile, *, domain_id, facility_id, init_operation_id,
                   expected_origin_reservation_sha256,
                   expected_origin_observation_sha256,
                   expected_namespace_reservation_sha256, deadline_token):
    return locked.resume_unstarted_namespace3_locked(profile, domain_id=domain_id,
        facility_id=facility_id, init_operation_id=init_operation_id,
        expected_origin_reservation_sha256=expected_origin_reservation_sha256,
        expected_origin_observation_sha256=expected_origin_observation_sha256,
        expected_namespace_reservation_sha256=expected_namespace_reservation_sha256,
        deadline_token=deadline_token)


def _verify_locked(profile, *, domain_id, facility_id, init_operation_id,
                   expected_origin_reservation_sha256,
                   expected_origin_observation_sha256,
                   expected_namespace_reservation_sha256,
                   expected_root_seal_sha256, expected_header_sha256,
                   deadline_token):
    return locked.verify_namespace3_locked(profile, domain_id=domain_id,
        facility_id=facility_id, init_operation_id=init_operation_id,
        expected_origin_reservation_sha256=expected_origin_reservation_sha256,
        expected_origin_observation_sha256=expected_origin_observation_sha256,
        expected_namespace_reservation_sha256=expected_namespace_reservation_sha256,
        expected_root_seal_sha256=expected_root_seal_sha256,
        expected_header_sha256=expected_header_sha256,
        deadline_token=deadline_token)


class _OwnerBudget:
    __slots__ = ("state", "callback", "initial_pending")

    def __init__(self, token, callback):
        self.state, self.callback = _OwnerState(token), callback
        self.initial_pending = True

    def clock_only(self):
        _clock(self.state)

    def poll(self):
        self.clock_only()
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as primary:
                _remember(self.state, primary)
                if self.state.first is not primary:
                    _raise_first(self.state)
                raise
        self.clock_only()

    def acquisition_clock(self):
        self.clock_only()
        if self.initial_pending:
            _no_guard(self.state)
            # Only a successful no-ambient check clears the first-poll marker.
            self.initial_pending = False


def _make_budget(callback):
    start = _monotonic()
    if type(start) is not float or not math.isfinite(start) or start < 0.0:
        _fail("INVALID_ARGUMENT")
    absolute = start + MAX_SECONDS
    if not math.isfinite(absolute):
        _fail("INVALID_ARGUMENT")
    token = _deadline(absolute)
    if type(token) is not deadline.Namespace3Deadline:
        _fail("RESULT_INVALID")
    deadline._token(token)
    if token.absolute_monotonic > absolute:
        _fail("RESULT_INVALID")
    if callback is not None and not callable(callback):
        _fail("INVALID_ARGUMENT")
    budget = _OwnerBudget(token, callback)
    _clock(budget.state)
    return budget


def _validate(profile, domain, facility, operation, first, second,
              reservation, seal, header, mode, state):
    if type(profile) is not lifecycle.StorageProfile:
        _fail("INVALID_ARGUMENT")
    for value in (domain, facility, operation):
        _call(state, _uuid, value, legal=lambda result: type(result) is str)
    required = (first, second)
    if mode in {"resume", "verify"}:
        required += (reservation,)
    if mode == "verify":
        required += (seal, header)
    for value in required:
        _call(state, _sha, value, legal=lambda result: type(result) is str)
    return _call(state, _profile_paths, profile, legal=_paths)


def _same_profile(profile, paths, state):
    if type(profile) is not lifecycle.StorageProfile:
        _fail("PROFILE_CHANGED")
    if _call(state, _profile_paths, profile, legal=_paths) != paths:
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


def _result_value(result, field):
    # Fixed literal fields are selected by this module, never a public operand.
    if field == "scope":
        return result.scope
    if field == "init_operation_id":
        return result.init_operation_id
    if field == "domain_id":
        return result.domain_id
    if field == "facility_id":
        return result.facility_id
    if field == "origin_reservation_sha256":
        return result.origin_reservation_sha256
    if field == "origin_observation_sha256":
        return result.origin_observation_sha256
    if field == "namespace_reservation_sha256":
        return result.namespace_reservation_sha256
    if field == "root_seal_sha256":
        return result.root_seal_sha256
    if field == "header_sha256":
        return result.header_sha256
    if field == "creation_kind":
        return result.creation_kind
    _fail("RESULT_INVALID")


def _result(result, domain, facility, operation, first, second,
            reservation, seal, header, mode, state):
    if type(result) is not facts.Namespace3FilesystemFacts:
        _fail("RESULT_INVALID")
    fields = ("scope", "init_operation_id", "domain_id", "facility_id",
              "origin_reservation_sha256", "origin_observation_sha256",
              "namespace_reservation_sha256", "root_seal_sha256", "header_sha256",
              "creation_kind")
    values = []
    for field in fields:
        values.append(_call(state, _initial_result_value, result, field))
    # Preserve the original owner's repeated field reads and short-circuit order.
    def read(field):
        return _call(state, _result_value, result, field,
                     legal=lambda value: type(value) is str)

    if (any(type(value) is not str for value in values)
            or read("scope") != contract.SCOPE
            or read("creation_kind") != contract.CREATION_KIND
            or read("init_operation_id") != operation or read("domain_id") != domain
            or read("facility_id") != facility or read("origin_reservation_sha256") != first
            or read("origin_observation_sha256") != second):
        _fail("RESULT_INVALID")
    hash_values = tuple(read(field) for field in fields[4:9])
    for value in hash_values:
        _call(state, _result_sha, value, legal=lambda returned: type(returned) is str)
    if mode in {"resume", "verify"} and read("namespace_reservation_sha256") != reservation:
        _fail("RESULT_INVALID")
    if mode == "verify" and (read("root_seal_sha256") != seal or read("header_sha256") != header):
        _fail("RESULT_INVALID")


def _initial_result_value(result, field):
    # Original initial tuple catches AttributeError before the later type gate.
    try:
        return _result_value(result, field)
    except AttributeError:
        _fail("RESULT_INVALID")


def _result_sha(value):
    # Convert inside the fixed primitive, before _call commits first.
    try:
        return contract._sha(value)
    except contract.Namespace3ContractError:
        _fail("RESULT_INVALID")


class _ExclusiveReceipt:
    __slots__ = ("state", "context", "entered", "attempted", "normal")

    def __init__(self, state):
        self.state, self.context = state, None
        self.entered = self.attempted = self.normal = False

    def __enter__(self):
        _clock(self.state)
        try:
            guard = _exclusive_enter(self.context)
        except BaseException as primary:
            # The actual enter did not normally return; lower owns its partial cleanup.
            _remember(self.state, primary)
            if self.state.first is not primary:
                _raise_first(self.state)
            raise
        self.entered = True
        try:
            if type(guard) is not lifecycle.StorageGuard:
                _fail("RESULT_INVALID")
            _clock(self.state)
        except BaseException as primary:
            _remember(self.state, primary)
            # Normal-enter receiver is known even when its yielded payload is invalid.
            try:
                self.__exit__(type(primary), primary, self.state.first_tb)
            except BaseException as cleanup:
                _remember(self.state, cleanup)
            if self.state.first is not primary:
                _raise_first(self.state)
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
            returned = _exclusive_exit(self.context, None if first is None else type(first),
                                       first, first_tb)
            if returned is not False:
                _fail("RESULT_INVALID")
            self.normal = True
        except BaseException as failure:
            _remember(self.state, failure)
        _cleanup_clock(self.state)
        _raise_first(self.state)
        return False


def _run(profile, domain, facility, operation, first, second,
         reservation, seal, header, mode, callback):
    budget = _make_budget(callback)
    state, token = budget.state, budget.state.token
    paths = _validate(profile, domain, facility, operation, first, second,
                      reservation, seal, header, mode, state)
    budget.clock_only()
    _no_guard(state)
    budget.poll()
    _no_guard(state)
    _same_profile(profile, paths, state)
    budget.clock_only()
    receipt = _ExclusiveReceipt(state)  # Preallocate before the actual factory/enter.
    receipt.context = _call(state, _exclusive, profile, budget,
                           legal=lambda value: type(value) is contextlib._GeneratorContextManager)
    with receipt as guard:
        _held(profile, paths, guard, budget)
        arguments = {"domain_id": domain, "facility_id": facility,
                     "init_operation_id": operation,
                     "expected_origin_reservation_sha256": first,
                     "expected_origin_observation_sha256": second,
                     "deadline_token": token}
        if mode == "init":
            result = _call(state, _initialize_locked, profile, **arguments,
                           legal=lambda value: type(value) is facts.Namespace3FilesystemFacts)
        elif mode == "resume":
            result = _call(state, _resume_locked, profile, **arguments,
                           expected_namespace_reservation_sha256=reservation,
                           legal=lambda value: type(value) is facts.Namespace3FilesystemFacts)
        else:
            result = _call(state, _verify_locked, profile, **arguments,
                           expected_namespace_reservation_sha256=reservation,
                           expected_root_seal_sha256=seal, expected_header_sha256=header,
                           legal=lambda value: type(value) is facts.Namespace3FilesystemFacts)
        _held(profile, paths, guard, budget)
        _result(result, domain, facility, operation, first, second,
                reservation, seal, header, mode, state)
        _held(profile, paths, guard, budget)
    budget.clock_only()
    _no_guard(state)
    _same_profile(profile, paths, state)
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
