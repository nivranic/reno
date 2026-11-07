"""Expand namespace3 filesystem composition at fixed deadline boundaries.

One original restricted ledger and its effective token flow through every
namespace component. Original immutable bytes, repeated physical checks,
producer normal exits and all held readback lifetimes remain distinct. These
filesystem facts do not establish enrollment, source closure or write rights.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import os
from pathlib import Path

from . import personal_authority_namespace3 as ordinary
from . import personal_authority_namespace3_deadline as deadline
from . import personal_authority_namespace3_io_deadline as publication
from . import personal_authority_namespace3_contract as contract
from . import personal_authority_origin_reservation_contract as origin_contract
from . import personal_authority_origin_source as origin
from . import personal_authority_origin_capture_deadline as capture
from . import personal_authority_directory_deadline as directories
from . import personal_authority_source_deadline as nodes
from . import personal_authority_legacy_fence as fence
from . import storage_lifecycle as lifecycle, sync_media as safe

Namespace3Error = ordinary.Namespace3Error
Namespace3FilesystemFacts = ordinary.Namespace3FilesystemFacts
Namespace3Deadline = deadline.Namespace3Deadline
Error = Namespace3Error
MAX_SECONDS = ordinary.MAX_SECONDS
_PATH_TYPE = type(Path())
_ROOTS = ("data", "media", "reports", "checkpoints")
_ORIGIN_NAMES = frozenset({origin_contract.RESERVATION_NAME, origin_contract.OBSERVATION_NAME})
_RESERVED_NAMES = _ORIGIN_NAMES | {contract.RESERVATION_NAME}
_COMPLETE_NAMES = _RESERVED_NAMES | {contract.ROOT_NAME, contract.HEADER_NAME}


def _ns_before(state):
    publication._budget_clock_only(state)


def _ns_after(state):
    publication._budget_clock_only(state)


def _ns_bad(state):
    _ns_fixed_failure(state, "INVALID_RESULT")


def _ns_exact_state(state):
    if type(state) is not publication._NamespaceState:
        raise Namespace3Error("INVALID_ARGUMENT") from None


def _ns_decimal(value):
    return (type(value) is str and bool(value) and value.isascii()
            and value.isdecimal() and (value == "0" or value[0] != "0"))


def _ns_native_identity_domain(state, value):
    if (type(value) is not dict or set(value) != {"volume_serial", "file_index"}
            or not _ns_decimal(value["volume_serial"]) or not _ns_decimal(value["file_index"])):
        _ns_bad(state)
    return value


def _ns_path_identity_domain(state, value, *, file=False):
    keys = {"device", "inode"}
    if file:
        keys |= {"byte_size", "mtime_ns", "ctime_ns", "mode", "nlink"}
    if type(value) is not dict or set(value) != keys:
        _ns_bad(state)
    for key in ("device", "inode") + (("mtime_ns", "ctime_ns") if file else ()):
        if not _ns_decimal(value[key]):
            _ns_bad(state)
    if file and (type(value["byte_size"]) is not int or value["byte_size"] < 0
            or type(value["mode"]) is not int or value["mode"] < 0
            or type(value["nlink"]) is not int or value["nlink"] != 1):
        _ns_bad(state)
    return value


def _ns_sha_domain(state, value):
    if (type(value) is not str or len(value) != 64
            or any(ch not in "0123456789abcdef" for ch in value)):
        _ns_bad(state)
    return value


def _ns_path_domain(state, value):
    if type(value) is not _PATH_TYPE:
        _ns_bad(state)
    return value


def _ns_copy(state, value):
    _ns_before(state)
    result = deepcopy(value)
    _ns_after(state)
    return result


def _ns_dict_copy(state, value):
    _ns_before(state)
    if type(value) is not dict:
        _ns_bad(state)
    result = dict(value)
    _ns_after(state)
    return result


def _ns_digest(state, raw):
    digest = publication._new_sha256(state, raw)
    return publication._hash_hex(state, digest)


def _ns_guard(state, profile):
    _ns_before(state)
    if type(profile) is not lifecycle.StorageProfile:
        _ns_fixed_failure(state, "INVALID_ARGUMENT")
    result = lifecycle.require_guard(profile, exclusive=True)
    _ns_after(state)
    return result


def _ns_stage(state, name, operation):
    return publication._stage_name(state, name, operation)


def _ns_scope(state):
    stack = publication._new_known_stack(state)
    receipt = publication._claim_stack_receipt(state, stack)
    return publication._typed_scope_guard(state, receipt)


def _ns_fixed_failure(state, fixed_failure_role, /):
    _ns_exact_state(state)
    if fixed_failure_role not in {
            "INVALID_ARGUMENT", "INVALID_RESULT", "PROFILE_CHANGED", "ORIGIN_MISMATCH",
            "UNKNOWN_NAMES", "STATE_INVALID", "PUBLICATION_MISMATCH",
            "ROOT_IDENTITY_MISMATCH", "GUARD_REQUIRED", "UNSUPPORTED", "LIMIT"}:
        fixed_failure_role = "INVALID_ARGUMENT"
    primary = Namespace3Error(fixed_failure_role)
    first = publication._remember_primary(state, primary)
    if first is not primary:
        publication._raise_first(state)
    raise primary from None


def _ns_append_directory(state, exact_directory_list, exact_directory, /):
    _ns_before(state)
    if (type(exact_directory_list) is not list
            or type(exact_directory) is not directories.DeadlineDirectory):
        _ns_bad(state)
    result = list.append(exact_directory_list, exact_directory)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_capture_factory(state, profile, domain_id, facility_id, init_operation_id, /):
    return publication._issue_capture_context(state, profile, domain_id, facility_id, init_operation_id)


def _ns_complete_cross(state, first, second, reservation, seal, header, /):
    _ns_before(state)
    result = contract.cross_validate_complete(first, second, reservation, seal, header)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_deadlinedirectory_verify(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not directories.DeadlineDirectory
            or exact_receiver.deadline_token is not state.token):
        _ns_bad(state)
    result = directories.DeadlineDirectory.verify(exact_receiver)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_deadlineheldfile_bytes(state, exact_receiver, maximum, /):
    _ns_before(state)
    if (type(exact_receiver) is not nodes.DeadlineHeldFile
            or type(maximum) is not int or maximum != origin_contract.MAX_NODE_BYTES):
        _ns_bad(state)
    result = nodes.DeadlineHeldFile.bytes(exact_receiver, maximum)
    if type(result) is not bytes or len(result) > maximum:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_deadlineheldfile_verify(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not nodes.DeadlineHeldFile:
        _ns_bad(state)
    result = nodes.DeadlineHeldFile.verify(exact_receiver)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_evidence_blob(state, exact_second_record, /):
    _ns_before(state)
    if type(exact_second_record) is not dict:
        _ns_bad(state)
    result = origin_contract._blob(exact_second_record["origin_evidence_base64"], origin_contract.MAX_EVIDENCE_BYTES)
    if type(result) is not bytes:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_expand_arguments(state, profile, domain, facility, operation, expected_first, expected_second, expected_reservation, expected_seal, expected_header, mode, /):
    publication._budget_clock_only(state)
    if type(profile) is not lifecycle.StorageProfile:
        _ns_fixed_failure(state, "INVALID_ARGUMENT")
    for value in (domain, facility, operation):
        _ns_uuid(state, value)
    required = (expected_first, expected_second)
    if mode in {"resume", "verify"}:
        required += (expected_reservation,)
    if mode == "verify":
        required += (expected_seal, expected_header)
    for value in required:
        _ns_sha(state, value)
    paths = tuple(_ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, profile))
    try:
        guard = _ns_guard(state, profile)
    except lifecycle.StorageLifecycleError:
        _ns_fixed_failure(state, "GUARD_REQUIRED")
    state.profile_paths, state.guard = paths, guard
    _ns_expand_same_guard(state, profile, paths, guard)
    publication._budget_poll(state)
    _ns_expand_same_guard(state, profile, paths, guard)
    if _ns_get_pinned_imported_os_module_name_original_ordinary_field_read(state, os) != "nt":
        _ns_fixed_failure(state, "UNSUPPORTED")
    return paths, guard


def _ns_expand_common(state, kind, first, /):
    return {"format_version": contract.FORMAT_VERSION, "family": contract.FAMILY,
            "kind": kind, "scope": contract.SCOPE,
            "init_operation_id": first["init_operation_id"], "domain_id": first["domain_id"],
            "facility_id": first["facility_id"]}


def _ns_expand_create(state, profile, parent, first_raw, second_raw, reservation_raw, /):
    first = _ns_origin_decode(state, first_raw)
    operation = first["init_operation_id"]
    with _ns_scope(state) as stack:
        receiver = _ns_expand_create_root_locked(state, profile, parent)
        root = stack.enter_root_create(receiver)
        _ns_expand_need_names(state, root, set(), outer=False)
        creator = {"creation_kind": contract.CREATION_KIND, "private_policy": contract.PRIVATE_POLICY,
            "parent_native_identity": _ns_dict_copy(state, _ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, parent)),
            "parent_path_identity": _ns_dict_copy(state, _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, parent)),
            "root_native_identity": _ns_dict_copy(state, _ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, root)),
            "root_path_identity": _ns_dict_copy(state, _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, root))}
        seal_raw = _ns_record_encode(state, {**_ns_expand_common(state, "namespace_root_seal", first),
            "namespace_reservation_sha256": _ns_digest(state, reservation_raw),
            "root_name": contract.ROOT_NAME, "creator": creator})
        _ns_expand_publish(state, profile, root, contract.ROOT_SEAL_NAME, operation, seal_raw, stack, contract.MAX_ROOT_SEAL_BYTES)
        header_raw = _ns_record_encode(state, {**_ns_expand_common(state, "namespace_header", first),
            "namespace_reservation_sha256": _ns_digest(state, reservation_raw),
            "root_seal_sha256": _ns_digest(state, seal_raw), "root_name": contract.ROOT_NAME,
            "creation_kind": contract.CREATION_KIND,
            "root_native_identity": _ns_dict_copy(state, _ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, root)),
            "root_path_identity": _ns_dict_copy(state, _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, root))})
        _ns_complete_cross(state, first_raw, second_raw, reservation_raw, seal_raw, header_raw)
        _ns_expand_publish(state, profile, parent, contract.HEADER_NAME, operation, header_raw, stack, contract.MAX_HEADER_BYTES)
        _ns_expand_need_names(state, root, {contract.ROOT_SEAL_NAME}, outer=False)
        _ns_expand_need_names(state, parent, _COMPLETE_NAMES, outer=True)
        _ns_iodirectory_verify(state, root)
    publication._budget_clock_only(state)
    return seal_raw, header_raw


def _ns_expand_create_root_locked(state, profile, parent, /):
    return publication._issue_root_create_context(state, profile, parent)


def _ns_expand_expect_sha(state, raw, expected, /):
    if _ns_digest(state, raw) != expected:
        _ns_fixed_failure(state, "ORIGIN_MISMATCH")


def _ns_expand_fresh_origin(state, profile, domain, facility, operation, second, /):
    publication._budget_clock_only(state)
    original_intent = _ns_intent_blob(state, second)
    original_evidence = _ns_evidence_blob(state, second)
    with _ns_scope(state) as capture_stack:
        receiver = _ns_capture_factory(state, profile, domain, facility, operation)
        observed = capture_stack.enter_capture(receiver)
        if (type(observed) is not origin.OriginObservation
                or _ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r(state, observed) != original_intent
                or _ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field(state, observed) != original_evidence):
            _ns_fixed_failure(state, "ORIGIN_MISMATCH")
        publication._budget_clock_only(state)
    # Complete producer normal exit precedes the original after-capture clock.
    publication._budget_clock_only(state)


def _ns_expand_names(state, directory, /, *, outer):
    publication._budget_clock_only(state)
    _ns_iodirectory_verify(state, directory)
    selected, seen = set(), set()
    with _ns_scope(state) as scan_stack:
        receiver = _ns_scan(state, directory)
        entries = scan_stack.enter_scandir(receiver)
        while True:
            publication._budget_before_next(state)
            try:
                entry = _ns_scan_next(state, entries)
            except StopIteration:
                break
            name = _ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read(state, entry)
            publication._budget_name(state, name)
            if name in seen:
                _ns_fixed_failure(state, "UNKNOWN_NAMES")
            _ns_set_add_name(state, seen, name)
            if not outer or _ns_prefix_startswith(state, _ns_prefix_casefold_copy(state, _ns_prefix_rstrip_copy(state, name))):
                _ns_set_add_name(state, selected, name)
    _ns_iodirectory_verify(state, directory)
    publication._budget_clock_only(state)
    return frozenset(selected)


def _ns_expand_need_names(state, directory, expected, /, *, outer):
    if _ns_expand_names(state, directory, outer=outer) != frozenset(expected):
        _ns_fixed_failure(state, "UNKNOWN_NAMES")


def _ns_expand_open_parent_locked(state, profile, /, *, write):
    return publication._issue_parent_context(state, profile, write=write)


def _ns_expand_open_root_locked(state, profile, parent, /):
    return publication._issue_root_open_context(state, profile, parent)


def _ns_expand_physical(state, profile, stack, /):
    roots, held_directories = {}, []
    database = _ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, profile)
    data_path = _ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, database)
    media = _ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read(state, profile)
    reports = _ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read(state, profile)
    checkpoints = _ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read(state, profile)
    for name, path in zip(_ROOTS, (data_path, media, reports, checkpoints)):
        publication._budget_clock_only(state)
        receiver = _ns_physical_directory_factory(state, path)
        directory = stack.enter_physical(receiver)
        _ns_append_directory(state, held_directories, directory)
        roots[name] = {"path": _ns_profile_key(state, path),
                       "identity": _ns_physical_identity(state, directory)}
    node = _ns_node_factory(state, held_directories[0], stack)
    node_raw = _ns_deadlineheldfile_bytes(state, node, origin_contract.MAX_NODE_BYTES)
    physical = {"profile_paths": list(_ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, profile)),
                "roots": roots}
    evidence = {"node_id": _ns_node_id(state, node_raw), **_ns_node_evidence(state, node)}
    return tuple(held_directories), node, physical, evidence


def _ns_expand_publish(state, profile, directory, name, operation, raw, stack, maximum, /):
    published = _ns_expand_publish_new_locked(state, profile, directory, name,
                                            _ns_stage(state, name, operation), raw)
    # All creator/descriptor and both publisher readers have normally exited.
    held, observed = _ns_expand_read_control(state, profile, directory, name, maximum, stack)
    if (type(published) is not publication.PublishedFile or observed != raw
            or _ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read(state, published) != _ns_digest(state, raw)
            or type(_ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read(state, published)) is not int
            or _ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read(state, published) != len(raw)
            or _ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read(state, published) != _ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read(state, held)
            or _ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read(state, published) != _ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read(state, held)):
        _ns_fixed_failure(state, "PUBLICATION_MISMATCH")
    publication._budget_clock_only(state)
    return held, observed


def _ns_expand_publish_new_locked(state, profile, directory, name, stage_name, raw, /):
    return publication.publish_new_locked(profile, directory, name, stage_name, raw, state)


def _ns_expand_read_control(state, profile, parent, name, maximum, stack, /):
    receiver = _ns_expand_read_control_locked(state, profile, parent, name, maximum, creator_live=False)
    held = stack.enter_held(receiver)
    return held, _ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read(state, held)


def _ns_expand_read_control_locked(state, profile, directory, name, maximum, /, *, creator_live):
    return publication._issue_held_context(state, profile, directory, name, maximum,
                                           creator_live=creator_live)


def _ns_expand_reservation(state, first_raw, second_raw, /):
    first, second = _ns_origin_decode(state, first_raw), _ns_origin_decode(state, second_raw)
    value = {**_ns_expand_common(state, "namespace_reservation", first),
        "profile": _ns_copy(state, first["profile"]), "node": _ns_copy(state, first["node"]),
        "layout": _ns_copy(state, contract.LAYOUT), "creation_kind": contract.CREATION_KIND,
        "origin": {"reservation_sha256": _ns_digest(state, first_raw),
            "observation_sha256": _ns_digest(state, second_raw),
            "intent_sha256": second["origin_intent_sha256"],
            "evidence_sha256": second["origin_evidence_sha256"],
            "authority_profile_paths_sha256": first["authority_profile_paths_sha256"],
            "physical_profile_sha256": first["physical_profile_sha256"],
            "authority_profile_paths_role": contract.AUTHORITY_ROLE,
            "physical_profile_role": contract.PHYSICAL_ROLE}}
    raw = _ns_record_encode(state, value)
    _ns_reservation_cross(state, first_raw, second_raw, raw)
    return raw


def _ns_expand_same_guard(state, profile, paths, guard, /):
    publication._budget_clock_only(state)
    identity = _ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, profile)
    if tuple(identity) != paths or _ns_guard(state, profile) is not guard:
        _ns_fixed_failure(state, "PROFILE_CHANGED")
    publication._budget_clock_only(state)


def _ns_expand_verify_complete(state, profile, first_raw, second_raw, reservation_raw, seal_sha, header_sha, /):
    with _ns_scope(state) as stack:
        parent = stack.enter_parent(_ns_expand_open_parent_locked(state, profile, write=False))
        _ns_expand_need_names(state, parent, _COMPLETE_NAMES, outer=True)
        pair_first, pair_first_raw = _ns_expand_read_control(state, profile, parent, origin_contract.RESERVATION_NAME,
                                                          origin_contract.MAX_RESERVATION_BYTES, stack)
        pair_second, pair_second_raw = _ns_expand_read_control(state, profile, parent, origin_contract.OBSERVATION_NAME,
                                                            origin_contract.MAX_OBSERVATION_BYTES, stack)
        if pair_first_raw != first_raw or pair_second_raw != second_raw:
            _ns_fixed_failure(state, "ORIGIN_MISMATCH")
        _, observed_reservation = _ns_expand_read_control(state, profile, parent, contract.RESERVATION_NAME,
                                                        contract.MAX_RESERVATION_BYTES, stack)
        if observed_reservation != reservation_raw:
            _ns_fixed_failure(state, "PUBLICATION_MISMATCH")
        root = stack.enter_root_open(_ns_expand_open_root_locked(state, profile, parent))
        _ns_expand_need_names(state, root, {contract.ROOT_SEAL_NAME}, outer=False)
        _, seal_raw = _ns_expand_read_control(state, profile, root, contract.ROOT_SEAL_NAME, contract.MAX_ROOT_SEAL_BYTES, stack)
        _, header_raw = _ns_expand_read_control(state, profile, parent, contract.HEADER_NAME, contract.MAX_HEADER_BYTES, stack)
        _ns_expand_expect_sha(state, seal_raw, seal_sha)
        _ns_expand_expect_sha(state, header_raw, header_sha)
        _ns_complete_cross(state, first_raw, second_raw, reservation_raw, seal_raw, header_raw)
        creator = publication._record_decode(state, seal_raw)["creator"]
        if (_ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, parent) != creator["parent_native_identity"]
                or _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, parent) != creator["parent_path_identity"]
                or _ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, root) != creator["root_native_identity"]
                or _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, root) != creator["root_path_identity"]):
            _ns_fixed_failure(state, "ROOT_IDENTITY_MISMATCH")
        _ns_ioheldcontrol_bytes(state, pair_first)
        _ns_ioheldcontrol_bytes(state, pair_second)
        _ns_iodirectory_verify(state, root)
        _ns_expand_need_names(state, root, {contract.ROOT_SEAL_NAME}, outer=False)
    publication._budget_clock_only(state)
    return seal_raw, header_raw


def _ns_expand_verify_physical(state, profile, paths, guard, directories, node, physical, evidence, /):
    _ns_expand_same_guard(state, profile, paths, guard)
    for name, directory in zip(_ROOTS, directories):
        path = _ns_physical_path(state, directory)
        if (_ns_profile_key(state, path) != physical["roots"][name]["path"]
                or _ns_physical_identity(state, directory) != physical["roots"][name]["identity"]):
            _ns_fixed_failure(state, "PROFILE_CHANGED")
    _ns_deadlineheldfile_verify(state, node)
    if (_ns_node_id(state, _ns_deadlineheldfile_bytes(state, node, origin_contract.MAX_NODE_BYTES)) != evidence["node_id"]
            or _ns_node_evidence(state, node) != {key: evidence[key] for key in origin_contract._FILE}):
        _ns_fixed_failure(state, "PROFILE_CHANGED")
    _ns_expand_same_guard(state, profile, paths, guard)


def _ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not directories.DeadlineDirectory
            or exact_receiver.deadline_token is not state.token):
        _ns_bad(state)
    result = exact_receiver.path
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not publication._IODirectory
            or exact_receiver.state is not state):
        _ns_bad(state)
    result = exact_receiver.native_identity
    _ns_native_identity_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not publication._IODirectory
            or exact_receiver.state is not state):
        _ns_bad(state)
    result = exact_receiver.path_identity
    _ns_path_identity_domain(state, result, file=False)
    _ns_after(state)
    return result


def _ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not publication._IODirectory
            or exact_receiver.state is not state):
        _ns_bad(state)
    result = exact_receiver.path
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not publication._IOHeldControl
            or exact_receiver.state is not state):
        _ns_bad(state)
    result = exact_receiver.native_identity
    _ns_native_identity_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not publication._IOHeldControl
            or exact_receiver.state is not state):
        _ns_bad(state)
    result = exact_receiver.path_identity
    _ns_path_identity_domain(state, result, file=True)
    _ns_after(state)
    return result


def _ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if (type(exact_receiver) is not publication._IOHeldControl
            or exact_receiver.state is not state):
        _ns_bad(state)
    result = exact_receiver.raw
    if type(result) is not bytes:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not os.DirEntry:
        _ns_bad(state)
    result = exact_receiver.name
    if type(result) is not str:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not origin.OriginObservation:
        _ns_bad(state)
    result = exact_receiver.evidence_bytes
    if type(result) is not bytes:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not origin.OriginObservation:
        _ns_bad(state)
    result = exact_receiver.intent_bytes
    if type(result) is not bytes:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    _ns_path_domain(state, exact_receiver)
    result = exact_receiver.parent
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not publication.PublishedFile:
        _ns_bad(state)
    result = exact_receiver.byte_size
    if type(result) is not int or not 0 <= result <= contract.MAX_RECORD_BYTES:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not publication.PublishedFile:
        _ns_bad(state)
    result = exact_receiver.native_identity
    _ns_native_identity_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not publication.PublishedFile:
        _ns_bad(state)
    result = exact_receiver.path_identity
    _ns_path_identity_domain(state, result, file=True)
    _ns_after(state)
    return result


def _ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not publication.PublishedFile:
        _ns_bad(state)
    result = exact_receiver.raw_sha256
    _ns_sha_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not lifecycle.StorageProfile:
        _ns_bad(state)
    result = exact_receiver.checkpoints
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not lifecycle.StorageProfile:
        _ns_bad(state)
    result = exact_receiver.database
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not lifecycle.StorageProfile:
        _ns_bad(state)
    result = exact_receiver.identity
    if type(result) is not tuple or len(result) != 4 or any(type(x) is not str for x in result):
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not lifecycle.StorageProfile:
        _ns_bad(state)
    result = exact_receiver.media
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if type(exact_receiver) is not lifecycle.StorageProfile:
        _ns_bad(state)
    result = exact_receiver.reports
    _ns_path_domain(state, result)
    _ns_after(state)
    return result


def _ns_get_pinned_imported_os_module_name_original_ordinary_field_read(state, exact_receiver, /):
    _ns_before(state)
    if exact_receiver is not os:
        _ns_bad(state)
    result = exact_receiver.name
    if type(result) is not str:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_intent_blob(state, exact_second_record, /):
    _ns_before(state)
    if type(exact_second_record) is not dict:
        _ns_bad(state)
    result = origin_contract._blob(exact_second_record["origin_intent_base64"], origin_contract.MAX_INTENT_BYTES)
    if type(result) is not bytes:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_iodirectory_verify(state, exact_receiver, /):
    return publication._iodirectory_verify(state, exact_receiver)


def _ns_ioheldcontrol_bytes(state, exact_receiver, /):
    return publication._ioheldcontrol_bytes(state, exact_receiver)


def _ns_new_filesystem_facts(state, scope, init_operation_id, domain_id, facility_id, origin_reservation_sha256, origin_observation_sha256, namespace_reservation_sha256, root_seal_sha256, header_sha256, creation_kind, /):
    _ns_before(state)
    result = Namespace3FilesystemFacts(scope, init_operation_id, domain_id, facility_id,
        origin_reservation_sha256, origin_observation_sha256, namespace_reservation_sha256,
        root_seal_sha256, header_sha256, creation_kind)
    if type(result) is not Namespace3FilesystemFacts:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_node_evidence(state, exact_deadline_node, /):
    return {"sha256": _ns_node_public_sha256(state, exact_deadline_node),
            "byte_size": _ns_node_public_byte_size(state, exact_deadline_node),
            "directory_identity": _ns_copy(state, _ns_node_public_directory_identity(state, exact_deadline_node)),
            "path_identity": _ns_copy(state, _ns_node_public_path_identity(state, exact_deadline_node)),
            "handle_identity": _ns_copy(state, _ns_node_public_handle_identity(state, exact_deadline_node))}


def _ns_node_factory(state, exact_data_directory, exact_stack, /):
    _ns_before(state)
    if type(exact_stack) is not publication._KnownStack or exact_stack.state is not state:
        _ns_bad(state)
    receiver = publication._issue_node_context(state, exact_data_directory)
    return exact_stack.enter_node(receiver)


def _ns_node_id(state, exact_node_raw, /):
    _ns_before(state)
    if type(exact_node_raw) is not bytes:
        _ns_bad(state)
    result = origin._node_id(exact_node_raw)
    if type(result) is not str:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_node_public_byte_size(state, exact_node, /):
    _ns_before(state)
    if type(exact_node) is not nodes.DeadlineHeldFile:
        _ns_bad(state)
    result = exact_node.byte_size
    if type(result) is not int or not 0 <= result <= origin_contract.MAX_NODE_BYTES:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_node_public_directory_identity(state, exact_node, /):
    _ns_before(state)
    if type(exact_node) is not nodes.DeadlineHeldFile:
        _ns_bad(state)
    result = exact_node.directory_identity
    _ns_path_identity_domain(state, result)
    _ns_after(state)
    return result


def _ns_node_public_handle_identity(state, exact_node, /):
    _ns_before(state)
    if type(exact_node) is not nodes.DeadlineHeldFile:
        _ns_bad(state)
    result = exact_node.handle_identity
    _ns_path_identity_domain(state, result, file=True)
    _ns_after(state)
    return result


def _ns_node_public_path_identity(state, exact_node, /):
    _ns_before(state)
    if type(exact_node) is not nodes.DeadlineHeldFile:
        _ns_bad(state)
    result = exact_node.path_identity
    _ns_path_identity_domain(state, result, file=True)
    _ns_after(state)
    return result


def _ns_node_public_sha256(state, exact_node, /):
    _ns_before(state)
    if type(exact_node) is not nodes.DeadlineHeldFile:
        _ns_bad(state)
    result = exact_node.sha256
    _ns_sha_domain(state, result)
    _ns_after(state)
    return result


def _ns_origin_cross(state, exact_first_raw, exact_second_raw, /):
    _ns_before(state)
    result = origin_contract.cross_validate(exact_first_raw, exact_second_raw)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_origin_decode(state, exact_bounded_origin_raw, /):
    _ns_before(state)
    if type(exact_bounded_origin_raw) is not bytes:
        _ns_bad(state)
    result = origin_contract.decode(exact_bounded_origin_raw)
    if type(result) is not dict:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_physical_directory_factory(state, path, /):
    return publication._issue_physical_context(state, path)


def _ns_physical_identity(state, exact_deadline_directory, /):
    _ns_deadlinedirectory_verify(state, exact_deadline_directory)
    path = _ns_physical_path(state, exact_deadline_directory)
    info = publication._path_stat(state, path)
    publication._safe_stat(state, info, True)
    return {"device": str(publication._get_stat_st_dev(state, info)),
            "inode": str(publication._get_stat_st_ino(state, info))}


def _ns_physical_path(state, exact_directory, /):
    return _ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read(state, exact_directory)


def _ns_prefix_casefold_copy(state, exact_prefix_copy, /):
    _ns_before(state)
    if type(exact_prefix_copy) is not str:
        _ns_bad(state)
    result = str.casefold(exact_prefix_copy)
    if type(result) is not str:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_prefix_rstrip_copy(state, exact_original_name, /):
    _ns_before(state)
    if type(exact_original_name) is not str:
        _ns_bad(state)
    result = str.rstrip(exact_original_name, " .")
    if type(result) is not str:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_prefix_startswith(state, exact_prefix_copy, /):
    _ns_before(state)
    if type(exact_prefix_copy) is not str:
        _ns_bad(state)
    result = str.startswith(exact_prefix_copy, fence.PREFIX)
    if type(result) is not bool:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_profile_key(state, exact_profile_path, /):
    _ns_before(state)
    _ns_path_domain(state, exact_profile_path)
    result = lifecycle._key(exact_profile_path)
    if type(result) is not str:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_record_encode(state, exact_fixed_record, /):
    _ns_before(state)
    if type(exact_fixed_record) is not dict:
        _ns_bad(state)
    result = contract.encode(exact_fixed_record)
    if type(result) is not bytes:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_reservation_cross(state, first, second, reservation, /):
    _ns_before(state)
    result = contract.cross_validate_reservation(first, second, reservation)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_scan(state, exact_original_receiver, /):
    _ns_before(state)
    if type(exact_original_receiver) is not publication._IODirectory or exact_original_receiver.state is not state:
        _ns_bad(state)
    path = _ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read(state, exact_original_receiver)
    native = publication._native_absolute(state, os.fspath(path))
    return publication._issue_scandir_context(state, native)


def _ns_scan_next(state, exact_original_receiver, /):
    _ns_before(state)
    publication._known_scandir_receiver(state, exact_original_receiver)
    try:
        result = next(exact_original_receiver)
    except StopIteration:
        _ns_after(state)
        raise
    if type(result) is not os.DirEntry:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_set_add_name(state, exact_set, exact_name, /):
    _ns_before(state)
    if type(exact_set) is not set or type(exact_name) is not str:
        _ns_bad(state)
    result = set.add(exact_set, exact_name)
    if result is not None:
        _ns_bad(state)
    _ns_after(state)
    return result


def _ns_sha(state, canonical_sha, /):
    _ns_before(state)
    result = contract._sha(canonical_sha)
    _ns_after(state)
    return result


def _ns_uuid(state, canonical_uuid, /):
    _ns_before(state)
    result = contract._uuid(canonical_uuid)
    _ns_after(state)
    return result



def _perform(state, profile, domain, facility, operation, expected_first, expected_second,
             expected_reservation, expected_seal, expected_header, mode):
    paths, guard = _ns_expand_arguments(state, profile, domain, facility, operation,
        expected_first, expected_second, expected_reservation, expected_seal, expected_header, mode)
    with _ns_scope(state) as stack:
        held_directories, node, physical, evidence = _ns_expand_physical(state, profile, stack)
        parent = stack.enter_parent(_ns_expand_open_parent_locked(state, profile, write=mode != "verify"))
        if _ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read(state, parent) != physical["roots"]["data"]["identity"]:
            _ns_fixed_failure(state, "PROFILE_CHANGED")
        selected = _ns_expand_names(state, parent, outer=True)
        recognized = _ORIGIN_NAMES if mode == "init" else (_RESERVED_NAMES if mode == "resume" else _COMPLETE_NAMES)
        if selected - _COMPLETE_NAMES:
            _ns_fixed_failure(state, "UNKNOWN_NAMES")
        if selected != recognized:
            _ns_fixed_failure(state, "STATE_INVALID")
        first_held, first_raw = _ns_expand_read_control(state, profile, parent, origin_contract.RESERVATION_NAME,
                                                      origin_contract.MAX_RESERVATION_BYTES, stack)
        second_held, second_raw = _ns_expand_read_control(state, profile, parent, origin_contract.OBSERVATION_NAME,
                                                        origin_contract.MAX_OBSERVATION_BYTES, stack)
        _ns_expand_expect_sha(state, first_raw, expected_first)
        _ns_expand_expect_sha(state, second_raw, expected_second)
        _ns_origin_cross(state, first_raw, second_raw)
        first, second = _ns_origin_decode(state, first_raw), _ns_origin_decode(state, second_raw)
        if (first["init_operation_id"] != operation or first["domain_id"] != domain or first["facility_id"] != facility
                or first["profile"] != physical or first["node"] != evidence):
            _ns_fixed_failure(state, "ORIGIN_MISMATCH")
        _ns_expand_verify_physical(state, profile, paths, guard, held_directories, node, physical, evidence)
        _ns_expand_fresh_origin(state, profile, domain, facility, operation, second)
        reservation_raw = _ns_expand_reservation(state, first_raw, second_raw)
        if mode == "init":
            _ns_expand_need_names(state, parent, _ORIGIN_NAMES, outer=True)
            _, reservation_raw = _ns_expand_publish(state, profile, parent, contract.RESERVATION_NAME, operation,
                                                  reservation_raw, stack, contract.MAX_RESERVATION_BYTES)
        else:
            _, observed = _ns_expand_read_control(state, profile, parent, contract.RESERVATION_NAME,
                                                 contract.MAX_RESERVATION_BYTES, stack)
            _ns_expand_expect_sha(state, observed, expected_reservation)
            if observed != reservation_raw:
                _ns_fixed_failure(state, "ORIGIN_MISMATCH")
            _ns_reservation_cross(state, first_raw, second_raw, observed)
            reservation_raw = observed
        if mode != "verify":
            _ns_expand_need_names(state, parent, _RESERVED_NAMES, outer=True)
            seal_raw, header_raw = _ns_expand_create(state, profile, parent, first_raw, second_raw, reservation_raw)
            expected_seal = _ns_digest(state, seal_raw)
            expected_header = _ns_digest(state, header_raw)
        seal_raw, header_raw = _ns_expand_verify_complete(state, profile, first_raw, second_raw, reservation_raw,
                                                        expected_seal, expected_header)
        if mode != "verify":
            _ns_expand_fresh_origin(state, profile, domain, facility, operation, second)
        if _ns_ioheldcontrol_bytes(state, first_held) != first_raw or _ns_ioheldcontrol_bytes(state, second_held) != second_raw:
            _ns_fixed_failure(state, "ORIGIN_MISMATCH")
        _ns_expand_verify_physical(state, profile, paths, guard, held_directories, node, physical, evidence)
        _ns_expand_need_names(state, parent, _COMPLETE_NAMES, outer=True)
        _ns_expand_need_names(state, parent, _COMPLETE_NAMES, outer=True)
        _ns_expand_same_guard(state, profile, paths, guard)
        result = _ns_new_filesystem_facts(state, contract.SCOPE, operation, domain, facility,
            _ns_digest(state, first_raw), _ns_digest(state, second_raw),
            _ns_digest(state, reservation_raw), _ns_digest(state, seal_raw),
            _ns_digest(state, header_raw), contract.CREATION_KIND)
    _ns_expand_same_guard(state, profile, paths, guard)
    publication._budget_clock_only(state)
    return result


def _entry(profile, domain, facility, operation, first, second,
           reservation, seal, header, mode, deadline_token):
    restricted = deadline._RestrictedBudget(deadline_token)
    state = publication._NamespaceState(restricted, restricted.effective_deadline)
    try:
        return _perform(state, profile, domain, facility, operation, first, second,
                        reservation, seal, header, mode)
    except BaseException as primary:
        first_error = publication._remember_primary(state, primary)
        if first_error is primary:
            raise
        publication._raise_first(state)


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
