"""Publish an immutable authority prefix under the actual origin EX.

This is a versioned filesystem successor to namespace3, not an installed
Store, SQL transaction, source-close receipt, or writer witness. Partial
states are never adopted. Locked results leave the caller EX open; owned
results follow this layer's ordinary exits and its outer EX exit.
"""
from __future__ import annotations

from contextlib import ExitStack
from dataclasses import dataclass
import hashlib
import os
import time

from . import personal_authority_enrollment_contract as contract
from . import personal_authority_enrollment_io as publication
from . import personal_authority_namespace3 as namespace
from . import personal_authority_namespace3_contract as ns_contract
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_namespace3_io as native
from . import personal_authority_origin_capture_deadline as capture
from . import personal_authority_origin_reservation_contract as origin
from . import personal_authority_origin_source as source
from . import storage_lifecycle as lifecycle
from . import personal_authority_owned_ex_deadline as owned_ex
from . import personal_authority_owned_ex_group as owned_group
from . import personal_authority_enrollment_custody as custody
from . import personal_authority_enrollment_physical as physical_source
from . import personal_authority_enrollment_packet as packet
from . import personal_authority_enrollment_store_runtime as store_runtime

MAX_SECONDS = 30.0
SCOPE = "durable_authority_prefix_files_only"
_OUTER_NAMES = frozenset({origin.RESERVATION_NAME, origin.OBSERVATION_NAME,
                         ns_contract.RESERVATION_NAME, ns_contract.ROOT_NAME,
                         ns_contract.HEADER_NAME})
_ORIGINAL_NAMES = (origin.RESERVATION_NAME, origin.OBSERVATION_NAME,
                   ns_contract.RESERVATION_NAME, ns_contract.ROOT_SEAL_NAME,
                   ns_contract.HEADER_NAME)
_ORIGINAL_MAXIMA = (origin.MAX_RESERVATION_BYTES, origin.MAX_OBSERVATION_BYTES,
                    ns_contract.MAX_RESERVATION_BYTES, ns_contract.MAX_ROOT_SEAL_BYTES,
                    ns_contract.MAX_HEADER_BYTES)
_KINDS = ("enrollment_binding", "origin_init_intent", "local_contract",
          "init_seal", "genesis_grant", "authority_prefix")


class EnrollmentError(ValueError):
    def __init__(self, suffix):
        self.code = "ENROLLMENT_" + suffix
        super().__init__(self.code)


Error = EnrollmentError


@dataclass(frozen=True, slots=True)
class AuthorityPrefixFiles:
    """Content references, with no permission or complete close assertion."""
    scope: str
    init_operation_id: str
    domain_id: str
    facility_id: str
    origin_reservation_sha256: str
    origin_observation_sha256: str
    namespace_reservation_sha256: str
    root_seal_sha256: str
    header_sha256: str
    binding_sha256: str
    prefix_sha256: str


def _fail(suffix):
    raise EnrollmentError(suffix) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


class _Budget(deadline._RestrictedBudget):
    """Original bounded ledger and immutable token, plus first-error custody."""
    def remember(self, primary):
        if self.callback_primary is None:
            self.callback_primary = primary
        return self.callback_primary


def _call(budget, operation, /, *args, **kwargs):
    budget.clock_only()
    try:
        result = operation(*args, **kwargs)
        budget.clock_only()
        return result
    except BaseException as primary:
        first = budget.remember(primary)
        if first is not primary:
            raise first from None
        raise


def _enter(stack, context, budget):
    # Register ownership before the post-acquisition clock can fail.
    return _call(budget, stack.enter_context, namespace._preserve_primary(context))


def _same_owner(profile, paths, guard, budget):
    budget.clock_only()
    if (type(profile) is not lifecycle.StorageProfile or tuple(profile.identity) != paths
            or lifecycle.require_guard(profile, exclusive=True) is not guard):
        _fail("PROFILE_CHANGED")
    budget.clock_only()


def _arguments(profile, operation, expected, budget):
    budget.clock_only()
    if type(profile) is not lifecycle.StorageProfile or type(expected) is not tuple or len(expected) != 5:
        _fail("INVALID_ARGUMENT")
    try:
        for value in operation:
            ns_contract._uuid(value)
        for value in expected:
            ns_contract._sha(value)
    except ns_contract.ContractError:
        _fail("INVALID_ARGUMENT")
    if os.name != "nt":
        _fail("UNSUPPORTED")
    budget.clock_only()
    return tuple(profile.identity)


def _require_owner(profile, budget):
    budget.clock_only()
    try:
        guard = lifecycle.require_guard(profile, exclusive=True)
    except lifecycle.StorageLifecycleError:
        _fail("GUARD_REQUIRED")
    budget.clock_only()
    return guard


class _ExclusiveScope:
    """Normal protocol exit of this one EX, not a native-close witness."""
    def __init__(self, context, budget):
        self.context, self.budget = context, budget
        self.entered = self.attempted = False

    def __enter__(self):
        self.budget.clock_only()
        guard = self.context.__enter__()
        self.entered = True
        try:
            self.budget.clock_only()
            if type(guard) is not lifecycle.StorageGuard:
                _fail("RESULT_INVALID")
        except BaseException as primary:
            self.budget.remember(primary)
            self.__exit__(type(primary), primary, BaseException.__dict__["__traceback__"].__get__(primary))
            raise
        return guard

    def __exit__(self, kind, primary, tb):
        if not self.entered or self.attempted:
            _fail("RESULT_INVALID")
        self.attempted = True
        if primary is not None:
            self.budget.remember(primary)
        try:
            self.budget.clock_only()
        except BaseException as failure:
            self.budget.remember(failure)
        first = self.budget.callback_primary
        try:
            returned = self.context.__exit__(None if first is None else type(first), first,
                None if first is None else BaseException.__dict__["__traceback__"].__get__(first))
            if returned is not False:
                _fail("RESULT_INVALID")
        except BaseException as failure:
            self.budget.remember(failure)
        self.budget.clock_only()  # Re-raise first only after the actual exit attempt.
        return False


def _result(result, operation, expected, mode, binding, prefix, budget):
    budget.clock_only()
    if type(result) is not AuthorityPrefixFiles:
        _fail("RESULT_INVALID")
    try:
        values = (result.scope, result.init_operation_id, result.domain_id,
                  result.facility_id, result.origin_reservation_sha256,
                  result.origin_observation_sha256, result.namespace_reservation_sha256,
                  result.root_seal_sha256, result.header_sha256, result.binding_sha256,
                  result.prefix_sha256)
        if (any(type(value) is not str for value in values) or values[0] != SCOPE
                or values[1:4] != (operation[2], operation[0], operation[1])
                or values[4:9] != expected):
            _fail("RESULT_INVALID")
        ns_contract._sha(values[9])
        ns_contract._sha(values[10])
    except (AttributeError, ns_contract.ContractError):
        _fail("RESULT_INVALID")
    if mode == "verify" and values[9:11] != (binding, prefix):
        _fail("RESULT_INVALID")
    budget.clock_only()


def _read(profile, directory, name, maximum, stack, budget):
    held = _enter(stack, native.read_control_locked(profile, directory, name, budget, maximum), budget)
    return held, _call(budget, held.bytes)


def _fresh(profile, operation, second_raw, budget, *, capture_ordinal=None):
    second = _call(budget, origin.decode, second_raw)
    intent = _call(budget, origin._blob, second["origin_intent_base64"], origin.MAX_INTENT_BYTES)
    evidence = _call(budget, origin._blob, second["origin_evidence_base64"], origin.MAX_EVIDENCE_BYTES)
    domain, facility, init = operation
    actual_ex = object.__getattribute__(budget, "__dict__").get("_owned_ex")
    if actual_ex is None:
        context = capture.capture_locked(profile, domain_id=domain, facility_id=facility,
            init_operation_id=init, deadline_token=budget.effective_deadline)
    else:
        context = capture.capture_locked(profile, domain_id=domain, facility_id=facility,
            init_operation_id=init, deadline_token=budget.effective_deadline, _owned_ex=actual_ex)
    with namespace._preserve_primary(context) as observed:
        budget.clock_only()
        if (type(observed) is not source.OriginObservation
                or observed.intent_bytes != intent or observed.evidence_bytes != evidence):
            _fail("ORIGIN_MISMATCH")
        budget.clock_only()
    # The producer yield and its normal context exit remain separate facts.
    budget.clock_only()
    carrier = physical_source._lookup_installed_carrier(budget)
    if carrier is not None:
        if type(capture_ordinal) is not int or not 0 <= capture_ordinal < 2:
            _fail("INVALID_RESULT")
        physical_source._record_capture_context_return(carrier,
            carrier._group._captures[capture_ordinal], ordinal=capture_ordinal)


def _records(binding, content):
    return (binding, content.origin_intent_bytes, content.local_contract_bytes,
            content.init_seal_bytes, content.genesis_grant_bytes, content.prefix_bytes)


def _root_names(complete):
    return ({ns_contract.ROOT_SEAL_NAME} | set(publication.NAMES.values())
            if complete else {ns_contract.ROOT_SEAL_NAME})


def _load_originals(profile, parent, root, expected, stack, budget):
    holders, originals = [], []
    for index, (name, maximum, expected_sha) in enumerate(zip(_ORIGINAL_NAMES, _ORIGINAL_MAXIMA, expected)):
        held, raw = _read(profile, root if index == 3 else parent, name, maximum, stack, budget)
        if _call(budget, _sha, raw) != expected_sha:
            _fail("ORIGINAL_MISMATCH")
        holders.append(held)
        originals.append(raw)
    _call(budget, ns_contract.cross_validate_complete, *originals)
    creator = _call(budget, ns_contract.decode, originals[3])["creator"]
    if (parent.native_identity != creator["parent_native_identity"]
            or parent.path_identity != creator["parent_path_identity"]
            or root.native_identity != creator["root_native_identity"]
            or root.path_identity != creator["root_path_identity"]):
        _fail("ROOT_IDENTITY_MISMATCH")
    return tuple(holders), tuple(originals)


def _read_prefix(profile, root, binding, content, stack, budget):
    expected_records = _records(binding, content)
    holders, observed = [], []
    for kind, expected_raw in zip(_KINDS, expected_records):
        held, raw = _read(profile, root, publication.NAMES[kind], contract.MAX_RECORD_BYTES, stack, budget)
        _call(budget, native._security_check, held.owner.value, budget)
        if raw != expected_raw:
            _fail("PREFIX_MISMATCH")
        holders.append(held)
        observed.append(raw)
    # This validates the bytes independently loaded from the actual files.
    read_content = contract.AuthorityPrefixContent(observed[5], *observed[1:5])
    return tuple(holders), observed[0], read_content


def _perform(profile, operation, expected, mode, budget, expected_binding, expected_prefix):
    paths = _arguments(profile, operation, expected, budget)
    guard = _require_owner(profile, budget)
    _same_owner(profile, paths, guard, budget)
    with namespace._preserve_primary(ExitStack()) as stack:
        carrier = physical_source._lookup_installed_carrier(budget)
        if carrier is None:
            physical_dirs, node, physical, node_evidence = _call(budget, namespace._physical, profile, stack, budget)
        else:
            physical_dirs, node, physical, node_evidence = _call(budget, physical_source._open_physical, profile, stack, budget)
        parent = _enter(stack, native.open_parent_locked(profile, budget, write=mode == "publish"), budget)
        _call(budget, namespace._need_names, parent, _OUTER_NAMES, budget)
        root_context = (publication.open_root_for_publication_locked(profile, parent, budget)
                        if mode == "publish" else native.open_root_locked(profile, parent, budget))
        root = _enter(stack, root_context, budget)
        _call(budget, namespace._need_names, root, _root_names(mode == "verify"), budget, outer=False)
        holders, originals = _load_originals(profile, parent, root, expected, stack, budget)
        first = _call(budget, origin.decode, originals[0])
        domain, facility, init = operation
        if (first["domain_id"] != domain or first["facility_id"] != facility
                or first["init_operation_id"] != init or first["profile"] != physical
                or first["node"] != node_evidence or parent.path_identity != physical["roots"]["data"]["identity"]):
            _fail("ORIGIN_MISMATCH")
        _call(budget, namespace._verify_physical, profile, paths, guard, physical_dirs,
              node, physical, node_evidence, budget)
        _fresh(profile, operation, originals[1], budget, capture_ordinal=0)
        binding = _call(budget, contract.build_binding, *originals)
        content = _call(budget, contract.prepare_authority_prefix, binding, *originals)
        if mode == "verify" and (_sha(binding) != expected_binding or _sha(content.prefix_bytes) != expected_prefix):
            _fail("PREFIX_MISMATCH")
        if mode == "publish":
            for position, (kind, raw) in enumerate(zip(_KINDS, _records(binding, content))):
                recognized = {ns_contract.ROOT_SEAL_NAME} | {publication.NAMES[k] for k in _KINDS[:position]}
                _call(budget, namespace._need_names, root, recognized, budget, outer=False)
                published = _call(budget, publication.publish_new_locked, profile, root,
                                  publication.NAMES[kind], raw, budget)
                # Keep an independently opened immutable file through the final checks.
                held, observed = _read(profile, root, publication.NAMES[kind], contract.MAX_RECORD_BYTES, stack, budget)
                if (type(published) is not native.PublishedFile or observed != raw
                        or type(published.byte_size) is not int or published.byte_size != len(raw)
                        or published.raw_sha256 != _sha(raw) or published.native_identity != held.native_identity
                        or published.path_identity != held.path_identity):
                    _fail("PUBLICATION_MISMATCH")
        prefix_holders, observed_binding, observed_content = _read_prefix(profile, root, binding, content, stack, budget)
        _call(budget, contract.cross_validate_authority_prefix, observed_content, observed_binding, *originals)
        _fresh(profile, operation, originals[1], budget, capture_ordinal=1)
        for held, raw in zip(holders, originals):
            if _call(budget, held.bytes) != raw:
                _fail("ORIGINAL_MISMATCH")
        for held, raw in zip(prefix_holders, _records(binding, content)):
            if _call(budget, held.bytes) != raw:
                _fail("PREFIX_MISMATCH")
            _call(budget, native._security_check, held.owner.value, budget)
        _call(budget, namespace._verify_physical, profile, paths, guard, physical_dirs,
              node, physical, node_evidence, budget)
        _call(budget, namespace._need_names, root, _root_names(True), budget, outer=False)
        _call(budget, namespace._need_names, parent, _OUTER_NAMES, budget)
        _same_owner(profile, paths, guard, budget)
        if carrier is not None:
            packet._stage_original_bytes(carrier, originals=originals,
                observed_binding=observed_binding, observed_content=observed_content)
        result = AuthorityPrefixFiles(SCOPE, init, domain, facility, *expected,
                                      _sha(binding), _sha(content.prefix_bytes))
    _same_owner(profile, paths, guard, budget)
    return result


def _entry(profile, operation, expected, mode, budget, callback, binding, prefix):
    try:
        _arguments(profile, operation, expected, budget)
        _require_owner(profile, budget)
        if callback is not None:
            if not callable(callback):
                _fail("INVALID_ARGUMENT")
            _call(budget, callback)
        if mode == "verify":
            try:
                ns_contract._sha(binding)
                ns_contract._sha(prefix)
            except ns_contract.ContractError:
                _fail("INVALID_ARGUMENT")
        result = _perform(profile, operation, expected, mode, budget, binding, prefix)
        _result(result, operation, expected, mode, binding, prefix, budget)
        carrier = physical_source._lookup_installed_carrier(budget)
        if carrier is not None:
            physical_source._record_result_success(carrier, result)
            packet._bind_verified_result(carrier, result=result)
        return result
    except BaseException as primary:
        first = budget.remember(primary)
        if first is not primary:
            raise first from None
        raise


def publish_prefix_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, deadline_token):
    """Publish six immutable files; leave the exact caller EX held."""
    return _entry(profile, (domain_id, facility_id, init_operation_id),
        (expected_origin_reservation_sha256, expected_origin_observation_sha256,
         expected_namespace_reservation_sha256, expected_root_seal_sha256, expected_header_sha256),
        "publish", _Budget(deadline_token), None, None, None)


def verify_prefix_locked(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, expected_binding_sha256, expected_prefix_sha256, deadline_token):
    """Read a complete successor state; partial states have no resume route."""
    return _entry(profile, (domain_id, facility_id, init_operation_id),
        (expected_origin_reservation_sha256, expected_origin_observation_sha256,
         expected_namespace_reservation_sha256, expected_root_seal_sha256, expected_header_sha256),
        "verify", _Budget(deadline_token), None, expected_binding_sha256, expected_prefix_sha256)


def _owned(profile, operation, expected, mode, callback, binding, prefix):
    # This token starts before arguments, the sole user callback, and EX acquisition.
    token = deadline.Namespace3Deadline(time.monotonic() + MAX_SECONDS)
    budget = _Budget(token)
    try:
        paths = _arguments(profile, operation, expected, budget)
        if lifecycle.current_guard() is not None:
            _fail("GUARD_ALREADY_HELD")
        if callback is not None:
            if not callable(callback):
                _fail("INVALID_ARGUMENT")
            _call(budget, callback)
        if lifecycle.current_guard() is not None or tuple(profile.identity) != paths:
            _fail("PROFILE_CHANGED")
        with _ExclusiveScope(lifecycle.exclusive(profile, timeout=0,
                check_cancelled=budget.clock_only), budget) as guard:
            _same_owner(profile, paths, guard, budget)
            result = _entry(profile, operation, expected, mode, budget, None, binding, prefix)
            _same_owner(profile, paths, guard, budget)
        budget.clock_only()
        if lifecycle.current_guard() is not None or tuple(profile.identity) != paths:
            _fail("PROFILE_CHANGED")
        budget.clock_only()
        return result
    except BaseException as primary:
        first = budget.remember(primary)
        if first is not primary:
            raise first from None
        raise


def publish_prefix(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, check_cancelled=None):
    """Own a new EX; deliver content facts only after its ordinary normal exit."""
    return _owned(profile, (domain_id, facility_id, init_operation_id),
        (expected_origin_reservation_sha256, expected_origin_observation_sha256,
         expected_namespace_reservation_sha256, expected_root_seal_sha256, expected_header_sha256),
        "publish", check_cancelled, None, None)


def verify_prefix(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, expected_binding_sha256, expected_prefix_sha256, check_cancelled=None):
    """Cold-read and recheck a complete prefix without mutating source or SQL."""
    return _owned(profile, (domain_id, facility_id, init_operation_id),
        (expected_origin_reservation_sha256, expected_origin_observation_sha256,
         expected_namespace_reservation_sha256, expected_root_seal_sha256, expected_header_sha256),
        "verify", check_cancelled, expected_binding_sha256, expected_prefix_sha256)


def _owned_with_actual_ex(profile, operation, expected, mode, callback, binding, prefix):
    # Same original product30 lineage; no caller deadline or writer grant.
    token = deadline.Namespace3Deadline(time.monotonic() + MAX_SECONDS)
    budget = _Budget(token)
    carrier = None
    try:
        paths = _arguments(profile, operation, expected, budget)
        if lifecycle.current_guard() is not None:
            _fail("GUARD_ALREADY_HELD")
        if callback is not None:
            if not callable(callback):
                _fail("INVALID_ARGUMENT")
            _call(budget, callback)
        if lifecycle.current_guard() is not None or tuple(profile.identity) != paths:
            _fail("PROFILE_CHANGED")
        ex_binding = owned_ex._new_owned_ex_binding(operation, parent_token=token,
            effective_token=budget.effective_deadline, scope_identity=object())
        context = owned_group.OwnedExclusiveDeadlineLease(profile, binding=ex_binding,
            declared_capture_count=2, check_cancelled=budget.clock_only)
        budget._owned_ex = context
        with _ExclusiveScope(context, budget) as guard:
            _same_owner(profile, paths, guard, budget)
            journal = custody._new_journal(context, operation=ex_binding._operation,
                parent_token=ex_binding._parent_token,
                effective_token=ex_binding._effective_token,
                scope_identity=ex_binding._scope_identity, budget=budget)
            finish_attempted = False
            try:
                custody._install(journal)
                carrier = physical_source._new_source_carrier(profile, budget, journal,
                    group=context, guard=guard)
                result = _entry(profile, operation, expected, mode, budget, None, binding, prefix)
                physical_source._require_entry_return(carrier, result)
                _same_owner(profile, paths, guard, budget)
                physical_source._record_outer_owner_success(carrier)
            except BaseException as primary:
                first = budget.remember(primary)
                if not finish_attempted:
                    finish_attempted = True
                    try:
                        custody._finish_journal(journal, primary=first)
                    except BaseException as cleanup:
                        if cleanup is not first:
                            native._note(first)
                if first is not primary:
                    raise first from None
                raise
            else:
                # Aggregation is after this sole native finish, outside its
                # cleanup handler, so a failed consumer cannot reseal it.
                finish_attempted = True
                native_snapshot = custody._finish_journal(journal)
                consumer = physical_source._register_borrowed_source_consumer(carrier, result=result)
                source_precondition = physical_source._issue_borrowed_source_precondition(consumer, native_snapshot=native_snapshot)
                continuation = store_runtime._new_prepare_continuation(packet._slot_for_carrier(carrier),
                    source_consumer=consumer, source_precondition=source_precondition, result=result)
                # This source-only API has no Store consumer yet. Retire its
                # unused continuation inside the original EX; issue no stage receipt.
                store_runtime._retire_continuation(continuation)
        budget.clock_only()
        if lifecycle.current_guard() is not None or tuple(profile.identity) != paths:
            _fail("PROFILE_CHANGED")
        budget.clock_only()
        owned_group._consume_owned_ex_group(context)
        return result
    except BaseException as primary:
        first = budget.remember(primary)
        if first is not primary:
            raise first from None
        raise
    finally:
        if carrier is not None:
            try:
                packet._retire_for_carrier(carrier, primary=budget.callback_primary)
            except BaseException as cleanup:
                first = budget.remember(cleanup)
                if first is not cleanup:
                    native._note(first)
                    raise first from None
                raise


def _owned_store_initial_with_actual_ex(profile, operation, expected, binding, prefix):
    # Fixed registered private verify route; no callback/bool/mode selector.
    mode, callback = "verify", None
    token = deadline.Namespace3Deadline(time.monotonic() + MAX_SECONDS)
    budget = _Budget(token)
    carrier = None
    try:
        paths = _arguments(profile, operation, expected, budget)
        if lifecycle.current_guard() is not None:
            _fail("GUARD_ALREADY_HELD")
        if callback is not None:
            if not callable(callback):
                _fail("INVALID_ARGUMENT")
            _call(budget, callback)
        if lifecycle.current_guard() is not None or tuple(profile.identity) != paths:
            _fail("PROFILE_CHANGED")
        ex_binding = owned_ex._new_owned_ex_binding(operation, parent_token=token,
            effective_token=budget.effective_deadline, scope_identity=object())
        context = owned_group.OwnedExclusiveDeadlineLease(profile, binding=ex_binding,
            declared_capture_count=2, check_cancelled=budget.clock_only)
        budget._owned_ex = context
        with _ExclusiveScope(context, budget) as guard:
            _same_owner(profile, paths, guard, budget)
            journal = custody._new_journal(context, operation=ex_binding._operation,
                parent_token=ex_binding._parent_token,
                effective_token=ex_binding._effective_token,
                scope_identity=ex_binding._scope_identity, budget=budget)
            finish_attempted = False
            try:
                custody._install(journal)
                carrier = physical_source._new_source_carrier(profile, budget, journal,
                    group=context, guard=guard)
                result = _entry(profile, operation, expected, mode, budget, None, binding, prefix)
                physical_source._require_entry_return(carrier, result)
                _same_owner(profile, paths, guard, budget)
                physical_source._record_outer_owner_success(carrier)
            except BaseException as primary:
                first = budget.remember(primary)
                if not finish_attempted:
                    finish_attempted = True
                    try:
                        custody._finish_journal(journal, primary=first)
                    except BaseException as cleanup:
                        if cleanup is not first:
                            native._note(first)
                if first is not primary:
                    raise first from None
                raise
            else:
                # Aggregation is after this sole native finish, outside its
                # cleanup handler, so a failed consumer cannot reseal it.
                finish_attempted = True
                native_snapshot = custody._finish_journal(journal)
                consumer = physical_source._register_borrowed_source_consumer(carrier, result=result)
                source_precondition = physical_source._issue_borrowed_source_precondition(consumer, native_snapshot=native_snapshot)
                # Resolve the actual slot before any route ownership exists.
                slot = packet._slot_for_carrier(carrier)
                route = store_runtime._new_initial_route(context, guard, budget)
                # Fixed wrapper owns factory pre-return metadata and the sole
                # actual-return aggregate finally; there is no caller consumer.
                store_runtime._initial_prepare_factory_owned(route, slot,
                    source_consumer=consumer, source_precondition=source_precondition, result=result)
        budget.clock_only()
        if lifecycle.current_guard() is not None or tuple(profile.identity) != paths:
            _fail("PROFILE_CHANGED")
        budget.clock_only()
        owned_group._consume_owned_ex_group(context)
        return result
    except BaseException as primary:
        first = budget.remember(primary)
        if first is not primary:
            raise first from None
        raise
    finally:
        if carrier is not None:
            try:
                packet._retire_for_carrier(carrier, primary=budget.callback_primary)
            except BaseException as cleanup:
                first = budget.remember(cleanup)
                if first is not cleanup:
                    native._note(first)
                    raise first from None
                raise


def publish_prefix_owned_ex(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, check_cancelled=None):
    """Publish existing content contract; consume this operation's owned EX."""
    return _owned_with_actual_ex(profile, (domain_id, facility_id, init_operation_id),
        (expected_origin_reservation_sha256, expected_origin_observation_sha256,
         expected_namespace_reservation_sha256, expected_root_seal_sha256, expected_header_sha256),
        "publish", check_cancelled, None, None)


def verify_prefix_owned_ex(profile, *, domain_id, facility_id, init_operation_id,
        expected_origin_reservation_sha256, expected_origin_observation_sha256,
        expected_namespace_reservation_sha256, expected_root_seal_sha256,
        expected_header_sha256, expected_binding_sha256, expected_prefix_sha256, check_cancelled=None):
    """Cold-read existing prefix contract; consume this operation's owned EX."""
    return _owned_with_actual_ex(profile, (domain_id, facility_id, init_operation_id),
        (expected_origin_reservation_sha256, expected_origin_observation_sha256,
         expected_namespace_reservation_sha256, expected_root_seal_sha256, expected_header_sha256),
        "verify", check_cancelled, expected_binding_sha256, expected_prefix_sha256)
