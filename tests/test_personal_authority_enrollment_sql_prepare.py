"""Pure prepare/reservation contract candidates; no runtime authority claim.

Intended canonical target: tests/test_personal_authority_enrollment_sql_prepare.py.
Only the five adjacent portable fixtures are read. Paths declared inside those
fixtures are never opened. Relinked variants change content declarations only;
they do not represent fresh native observations, SQL commits, durable writes,
packet ownership, source closure, or a successful enrollment.
"""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from reno import personal_authority_contract as authority
from reno import personal_authority_enrollment_contract as enrollment_contract
from reno import personal_authority_namespace3_contract as namespace
from reno import personal_authority_origin_reservation_contract as origin
from reno import personal_authority_enrollment_successor as subject


FIXTURE = Path(__file__).parent / "fixtures" / "personal_authority_enrollment_binding"
FIXTURE_NAMES = (
    "origin-reservation.json", "origin-observation.json", "namespace-reservation.json",
    "namespace-root-seal.json", "namespace-header.json",
)
FIXTURE_PINS = (
    (2806, "2fa551826ebb923917cb04b8ea20a79b84dd316edcb910ea2571841b25058531"),
    (11100, "6eb52c9a8d69a0148770a76aaaa641731bd5d2bc3594f2e341ff5ae9c4bdf1f9"),
    (3655, "6199a570a8d316d063d6aafc5c9ef2cea6426a20066212a87bab768a791737ac"),
    (918, "6215b60e393af74bb8009646ef505eba586e16cdfecf03fe5430f4c3bc874dde"),
    (750, "6c57ac2203b7fb03a1009f8f3793bed2fdfd27eb6d504c4c3b8178f4fcffb176"),
)
ORIGINAL_FIELDS = (
    "first_bytes", "second_bytes", "namespace_reservation_bytes", "root_seal_bytes",
    "header_bytes", "binding_bytes", "prefix_bytes", "origin_intent_bytes",
    "local_contract_bytes", "init_seal_bytes", "genesis_grant_bytes",
)
REFERENCE_SPECS = (
    ("first", "original_origin_reservation", "personal_authority_origin_reservation", "origin_reservation"),
    ("second", "original_origin_observation", "personal_authority_origin_reservation", "origin_observation"),
    ("namespace_reservation", "original_namespace3_reservation", "personal_authority_namespace3", "namespace_reservation"),
    ("root_seal", "original_namespace3_root_seal", "personal_authority_namespace3", "namespace_root_seal"),
    ("header", "original_namespace3_header", "personal_authority_namespace3", "namespace_header"),
    ("binding", "original_enrollment_binding", "personal_authority_enrollment_binding", "enrollment_binding"),
    ("prefix", "original_authority_prefix", "personal_authority_enrollment_binding", "authority_prefix"),
    ("origin_intent", "original_sealed_origin_intent", "authority_format1_no_family_envelope", "origin_init_intent"),
    ("local_contract", "original_local_contract", "authority_format1_no_family_envelope", "local_contract"),
    ("init_seal", "original_init_seal", "authority_format1_no_family_envelope", "init_seal"),
    ("genesis_grant", "original_genesis_grant", "authority_format1_no_family_envelope", "genesis_grant"),
)
PUBLICATION_ORDER = ["origin_intent", "local_contract", "init_seal", "genesis_grant", "binding", "prefix"]
PREPARE_FIELDS = {
    "format_version", "family", "kind", "scope", "context", "profiles", "namespace",
    "logical_operation_id", "slot_identity", "original_refs", "publication_order",
}
RESERVATION_FIELDS = {
    "format_version", "family", "kind", "scope", "context", "logical_operation_id",
    "slot_identity", "prepare_payload_base64", "prepare_payload_byte_size", "prepare_payload_sha256",
}
OTHER_UUID = "11111111-1111-4111-8111-111111111111"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def different_sha(value):
    return "f" * 64 if value != "f" * 64 else "e" * 64


def assert_rejected(call, suffix=None):
    with pytest.raises(subject.EnrollmentSqlSuccessorError) as raised:
        call()
    assert type(raised.value) is subject.EnrollmentSqlSuccessorError
    code = getattr(raised.value, "code", str(raised.value))
    assert code.startswith("ENROLLMENT_SQL_SUCCESSOR_")
    if suffix is not None:
        assert code == "ENROLLMENT_SQL_SUCCESSOR_" + suffix


def make_originals(five):
    """Construct six existing pure materials, not replacement prepare content."""
    origin.cross_validate(*five[:2])
    namespace.cross_validate_complete(*five)
    binding_raw = enrollment_contract.build_binding(*five)
    content = enrollment_contract.prepare_authority_prefix(binding_raw, *five)
    enrollment_contract.cross_validate_authority_prefix(content, binding_raw, *five)
    return five + (binding_raw, content.prefix_bytes, content.origin_intent_bytes,
                   content.local_contract_bytes, content.init_seal_bytes, content.genesis_grant_bytes)


def relinked_five(five, *, change_first):
    """A fully relinked synthetic declaration set; no physical fact is created."""
    first, second, reservation, seal, header = map(json.loads, five)
    intent = json.loads(base64.b64decode(second["origin_intent_base64"], validate=True))
    evidence = json.loads(base64.b64decode(second["origin_evidence_base64"], validate=True))
    if change_first:
        first["node"]["sha256"] = different_sha(first["node"]["sha256"])
        evidence["node"]["sha256"] = first["node"]["sha256"]
        reservation["node"] = deepcopy(first["node"])
    else:
        previous = intent["payload"]["current_epoch"]
        intent["payload"]["current_epoch"] = 1 if previous == authority.MAX_INTEGER else previous + 1
    first_raw, intent_raw = canonical(first), canonical(intent)
    evidence["intent_sha256"] = sha(intent_raw)
    evidence_raw = canonical(evidence)
    second.update(reservation_sha256=sha(first_raw), origin_intent_sha256=sha(intent_raw),
                  origin_intent_base64=base64.b64encode(intent_raw).decode("ascii"),
                  origin_evidence_sha256=sha(evidence_raw),
                  origin_evidence_base64=base64.b64encode(evidence_raw).decode("ascii"))
    second_raw = canonical(second)
    reservation["origin"].update(reservation_sha256=sha(first_raw), observation_sha256=sha(second_raw),
                                  intent_sha256=sha(intent_raw), evidence_sha256=sha(evidence_raw))
    reservation_raw = canonical(reservation)
    seal["namespace_reservation_sha256"] = sha(reservation_raw)
    seal_raw = canonical(seal)
    header.update(namespace_reservation_sha256=sha(reservation_raw), root_seal_sha256=sha(seal_raw))
    return first_raw, second_raw, reservation_raw, seal_raw, canonical(header)


@pytest.fixture
def originals():
    five = tuple((FIXTURE / name).read_bytes() for name in FIXTURE_NAMES)
    for raw, (size, digest) in zip(five, FIXTURE_PINS):
        assert len(raw) == size and sha(raw) == digest
    return make_originals(five)


@pytest.fixture
def prepare_raw(originals):
    return subject.build_prepare(originals=originals)


@pytest.fixture
def reservation_raw(originals, prepare_raw):
    return subject.build_reservation(prepare_raw, originals=originals)


@pytest.fixture
def relinked_sets(originals):
    five = originals[:5]
    return (make_originals(relinked_five(five, change_first=True)),
            make_originals(relinked_five(five, change_first=False)))


def test_prepare_independently_binds_all_original_refs_roles_context_profiles_and_native_path_ids(originals, prepare_raw):
    first, second = (origin.decode(raw) for raw in originals[:2])
    ns_documents = tuple(namespace.decode(raw) for raw in originals[2:5])
    existing_documents = tuple(enrollment_contract.decode(raw) for raw in originals[5:7])
    authority_documents = tuple(authority.decode(raw) for raw in originals[7:])
    authority.cross_validate(originals[7:])
    intent = authority_documents[0]
    sealed_intent = base64.b64decode(second["origin_intent_base64"], validate=True)
    assert originals[7] == sealed_intent
    assert sha(sealed_intent) == second["origin_intent_sha256"]
    context = {"domain_id": first["domain_id"], "init_operation_id": first["init_operation_id"],
               "issuer": {"facility_id": first["facility_id"], "node_id": first["node"]["node_id"],
                          "profile_sha256": sha(canonical(first["profile"]["profile_paths"]))}}
    assert intent["domain_id"] == context["domain_id"]
    assert intent["operation_id"] == context["init_operation_id"]
    assert intent["issuer"] == context["issuer"]
    for document in ns_documents:
        assert document["domain_id"] == context["domain_id"]
        assert document["init_operation_id"] == context["init_operation_id"]
        assert document["facility_id"] == context["issuer"]["facility_id"]
    for document in existing_documents + authority_documents:
        operation = document.get("init_operation_id", document.get("operation_id"))
        assert document["domain_id"] == context["domain_id"]
        assert operation == context["init_operation_id"] and document["issuer"] == context["issuer"]
    profiles = {"authority_profile_paths_role": "canonical_four_paths_array_v1",
                "authority_profile_paths_sha256": sha(canonical(first["profile"]["profile_paths"])),
                "physical_profile_role": "canonical_physical_profile_dict_v1",
                "physical_profile_sha256": sha(canonical(first["profile"]))}
    assert profiles["authority_profile_paths_sha256"] == first["authority_profile_paths_sha256"]
    assert profiles["physical_profile_sha256"] == first["physical_profile_sha256"]
    assert profiles["authority_profile_paths_sha256"] != profiles["physical_profile_sha256"]
    header, seal = ns_documents[2], ns_documents[1]
    expected_namespace = {"family": "personal_authority_namespace3", "format_version": 1,
                          "root_name": header["root_name"],
                          "root_native_identity": header["root_native_identity"],
                          "root_path_identity": header["root_path_identity"]}
    assert header["root_native_identity"] == seal["creator"]["root_native_identity"]
    assert header["root_path_identity"] == seal["creator"]["root_path_identity"]
    documents = (first, second) + ns_documents + existing_documents + authority_documents
    refs = {}
    for raw, document, (key, role, codec, kind) in zip(originals, documents, REFERENCE_SPECS):
        assert document["kind"] == kind and document["format_version"] == 1
        refs[key] = {"role": role, "codec": codec, "record_kind": kind, "format_version": 1,
                     "raw_byte_size": len(raw), "raw_sha256": sha(raw), "identity": deepcopy(context)}
    expected = {"format_version": 1, "family": "personal_authority_enrollment_sql_prepare",
                "kind": "sql_registration_prepare", "scope": "durable_prepare_content_only",
                "context": context, "profiles": profiles, "namespace": expected_namespace,
                "logical_operation_id": context["init_operation_id"],
                "slot_identity": {"slot": 1, "namespace_version": 3, "domain_id": context["domain_id"],
                                  "facility_id": context["issuer"]["facility_id"],
                                  "owner_node_id": context["issuer"]["node_id"],
                                  "init_operation_id": context["init_operation_id"]},
                "original_refs": refs, "publication_order": PUBLICATION_ORDER}
    assert subject.decode(prepare_raw) == expected
    assert set(expected) == PREPARE_FIELDS and subject.encode(expected) == prepare_raw
    assert canonical(expected) == prepare_raw
    assert subject.cross_validate_prepare(prepare_raw, originals=originals) is None


def test_reservation_seals_the_selected_whole_prepare_raw_without_a_hash_cycle(originals, prepare_raw, reservation_raw):
    prepare = subject.decode(prepare_raw)
    reservation = subject.decode(reservation_raw)
    assert set(reservation) == RESERVATION_FIELDS
    assert reservation == {
        "format_version": 1, "family": "personal_authority_enrollment_sql_prepare",
        "kind": "sql_registration_prepare_reservation", "scope": "durable_prepare_content_only",
        "context": prepare["context"], "logical_operation_id": prepare["logical_operation_id"],
        "slot_identity": prepare["slot_identity"],
        "prepare_payload_base64": base64.b64encode(prepare_raw).decode("ascii"),
        "prepare_payload_byte_size": len(prepare_raw), "prepare_payload_sha256": sha(prepare_raw),
    }
    assert base64.b64decode(reservation["prepare_payload_base64"], validate=True) == prepare_raw
    assert reservation["prepare_payload_sha256"] != sha(originals[5])
    assert reservation["prepare_payload_sha256"] != sha(reservation_raw)
    assert "prepare_payload_sha256" not in prepare and "reservation_sha256" not in prepare
    assert subject.encode(reservation) == reservation_raw == canonical(reservation)
    assert subject.cross_validate_reservation(reservation_raw, prepare_raw, originals=originals) is None


def test_equal_fresh_builtin_bytes_are_valid_pure_content_inputs(originals, prepare_raw, reservation_raw):
    fresh_originals = tuple(bytes(bytearray(raw)) for raw in originals)
    fresh_prepare = bytes(bytearray(prepare_raw))
    fresh_reservation = bytes(bytearray(reservation_raw))
    assert all(left == right and left is not right for left, right in zip(originals, fresh_originals))
    assert fresh_prepare == prepare_raw and fresh_prepare is not prepare_raw
    assert fresh_reservation == reservation_raw and fresh_reservation is not reservation_raw
    assert subject.build_prepare(originals=fresh_originals) == prepare_raw
    assert subject.cross_validate_prepare(fresh_prepare, originals=fresh_originals) is None
    assert subject.build_reservation(fresh_prepare, originals=fresh_originals) == reservation_raw
    assert subject.cross_validate_reservation(fresh_reservation, fresh_prepare, originals=fresh_originals) is None


def test_decode_returns_independent_deep_copies_for_both_supported_kinds(prepare_raw, reservation_raw):
    for raw in (prepare_raw, reservation_raw):
        first, second = subject.decode(raw), subject.decode(raw)
        snapshot = deepcopy(second)
        first["context"]["issuer"]["node_id"] = OTHER_UUID
        first["slot_identity"]["owner_node_id"] = OTHER_UUID
        if first["kind"] == "sql_registration_prepare":
            first["original_refs"]["second"]["identity"]["issuer"]["node_id"] = OTHER_UUID
            first["namespace"]["root_path_identity"]["inode"] = "999"
            first["publication_order"].reverse()
        assert second == snapshot == subject.decode(raw)


def test_internally_linked_new_materials_get_new_prepare_and_cannot_reuse_old_content(originals, prepare_raw, reservation_raw, relinked_sets):
    for changed in relinked_sets:
        new_prepare = subject.build_prepare(originals=changed)
        new_reservation = subject.build_reservation(new_prepare, originals=changed)
        assert new_prepare != prepare_raw and new_reservation != reservation_raw
        assert subject.cross_validate_prepare(new_prepare, originals=changed) is None
        assert subject.cross_validate_reservation(new_reservation, new_prepare, originals=changed) is None
        assert_rejected(lambda: subject.cross_validate_prepare(prepare_raw, originals=changed))
        assert_rejected(lambda: subject.build_reservation(prepare_raw, originals=changed))
        assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, prepare_raw, originals=changed))
        assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, new_prepare, originals=changed))


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], ids=[
    "first_bytes", "second_bytes", "namespace_reservation_bytes", "root_seal_bytes", "header_bytes",
    "binding_bytes", "prefix_bytes", "origin_intent_bytes", "local_contract_bytes", "init_seal_bytes", "genesis_grant_bytes",
])
def test_replacing_each_single_original_with_other_valid_material_breaks_the_complete_chain(originals, prepare_raw, reservation_raw, relinked_sets, index):
    alternative = relinked_sets[0] if index == 0 else relinked_sets[1]
    assert alternative[index] != originals[index]
    changed = list(originals)
    changed[index] = alternative[index]
    changed = tuple(changed)
    assert_rejected(lambda: subject.build_prepare(originals=changed))
    assert_rejected(lambda: subject.cross_validate_prepare(prepare_raw, originals=changed))
    assert_rejected(lambda: subject.build_reservation(prepare_raw, originals=changed))
    assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, prepare_raw, originals=changed))


@pytest.mark.parametrize("index", [0, 1, 2, 3, 4, 5, 6, 7, 8, 9, 10], ids=[
    "first", "second", "namespace_reservation", "root_seal", "header", "binding", "prefix",
    "origin_intent", "local_contract", "init_seal", "genesis_grant",
])
def test_each_original_reference_requires_exact_digest_size_context_and_fixed_roles(originals, prepare_raw, index):
    key = REFERENCE_SPECS[index][0]
    for field in ("raw_sha256", "raw_byte_size", "identity", "role", "codec", "record_kind", "format_version"):
        changed = subject.decode(prepare_raw)
        ref = changed["original_refs"][key]
        if field == "raw_sha256":
            ref[field] = different_sha(ref[field])
        elif field == "raw_byte_size":
            ref[field] += 1
        elif field == "identity":
            ref[field]["init_operation_id"] = OTHER_UUID
        elif field == "format_version":
            ref[field] = 2
        else:
            ref[field] = "unrecognized_" + field
        assert_rejected(lambda: subject.cross_validate_prepare(canonical(changed), originals=originals))


@pytest.mark.parametrize("case", ["context", "logical-operation", "slot-owner", "profile-hashes", "root-identities", "namespace2"],
                         ids=["context", "logical-operation", "slot-owner", "profile-hashes", "root-identities", "namespace2"])
def test_prepare_does_not_accept_role_swaps_or_a_different_slot_operation_context(originals, prepare_raw, case):
    changed = subject.decode(prepare_raw)
    if case == "context":
        changed["context"]["domain_id"] = OTHER_UUID
    elif case == "logical-operation":
        changed["logical_operation_id"] = OTHER_UUID
    elif case == "slot-owner":
        changed["slot_identity"]["owner_node_id"] = OTHER_UUID
    elif case == "profile-hashes":
        profiles = changed["profiles"]
        profiles["authority_profile_paths_sha256"], profiles["physical_profile_sha256"] = (
            profiles["physical_profile_sha256"], profiles["authority_profile_paths_sha256"])
    elif case == "root-identities":
        ns = changed["namespace"]
        ns["root_native_identity"], ns["root_path_identity"] = ns["root_path_identity"], ns["root_native_identity"]
    else:
        changed["namespace"]["family"] = "personal_authority_namespace2"
        changed["slot_identity"]["namespace_version"] = 2
    assert_rejected(lambda: subject.cross_validate_prepare(canonical(changed), originals=originals))
    if case == "context":
        for invalid_uuid in ("00000000-0000-0000-0000-000000000000", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA"):
            changed = subject.decode(prepare_raw)
            changed["context"]["domain_id"] = invalid_uuid
            assert_rejected(lambda: subject.decode(canonical(changed)))
    elif case == "slot-owner":
        for key, value in (("slot", 2), ("namespace_version", True)):
            changed = subject.decode(prepare_raw)
            changed["slot_identity"][key] = value
            assert_rejected(lambda: subject.decode(canonical(changed)))
    elif case == "profile-hashes":
        changed = subject.decode(prepare_raw)
        profiles = changed["profiles"]
        profiles["authority_profile_paths_role"], profiles["physical_profile_role"] = (
            profiles["physical_profile_role"], profiles["authority_profile_paths_role"])
        assert_rejected(lambda: subject.decode(canonical(changed)))


@pytest.mark.parametrize("case", ["missing", "extra"], ids=["missing", "extra"])
def test_both_new_kinds_have_closed_top_level_and_nested_field_sets(prepare_raw, reservation_raw, case):
    for raw in (prepare_raw, reservation_raw):
        for path in ((), ("context",), ("context", "issuer"), ("slot_identity",)):
            changed = subject.decode(raw)
            target = changed
            for key in path:
                target = target[key]
            if case == "missing":
                del target[next(iter(target))]
            else:
                target["postcommit_successor"] = "not_yet_available"
            assert_rejected(lambda: subject.decode(canonical(changed)))
    changed = subject.decode(prepare_raw)
    if case == "missing":
        del changed["original_refs"]["second"]
    else:
        changed["original_refs"]["future_observation"] = deepcopy(changed["original_refs"]["second"])
    assert_rejected(lambda: subject.decode(canonical(changed)))


@pytest.mark.parametrize("field,value", [
    ("family", "personal_authority_enrollment_sql_successor"),
    ("kind", "origin_sql_successor"), ("scope", "committed_sql_content_only"), ("format_version", 2),
], ids=["successor-family", "future-successor-kind", "committed-scope", "version2"])
def test_prepare_codec_cannot_silently_accept_a_future_successor_or_wrong_envelope(prepare_raw, reservation_raw, field, value):
    for raw in (prepare_raw, reservation_raw):
        changed = subject.decode(raw)
        changed[field] = value
        assert_rejected(lambda: subject.decode(canonical(changed)))
        assert_rejected(lambda: subject.encode(changed))


def test_publication_order_is_exact_and_never_drops_duplicates_or_reorders_materials(originals, prepare_raw):
    for order in (list(reversed(PUBLICATION_ORDER)), PUBLICATION_ORDER[:-1], PUBLICATION_ORDER + ["binding"]):
        changed = subject.decode(prepare_raw)
        changed["publication_order"] = order
        assert_rejected(lambda: subject.cross_validate_prepare(canonical(changed), originals=originals))


@pytest.mark.parametrize("case", ["bytearray", "memoryview", "text", "bytes-subclass"],
                         ids=["bytearray", "memoryview", "text", "bytes-subclass"])
def test_decode_and_prepare_build_consumers_require_exact_builtin_bytes(originals, prepare_raw, reservation_raw, case):
    class BytesSubclass(bytes):
        pass

    converters = {"bytearray": bytearray, "memoryview": memoryview,
                  "text": lambda raw: raw.decode("utf-8"), "bytes-subclass": BytesSubclass}
    convert = converters[case]
    assert_rejected(lambda: subject.decode(convert(prepare_raw)), "RAW_TYPE_INVALID")
    assert_rejected(lambda: subject.decode(convert(reservation_raw)), "RAW_TYPE_INVALID")
    assert_rejected(lambda: subject.cross_validate_prepare(convert(prepare_raw), originals=originals))
    assert_rejected(lambda: subject.build_reservation(convert(prepare_raw), originals=originals))
    assert_rejected(lambda: subject.cross_validate_reservation(convert(reservation_raw), prepare_raw, originals=originals))
    assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, convert(prepare_raw), originals=originals))


@pytest.mark.parametrize("case", ["list", "tuple-subclass", "ten", "twelve"],
                         ids=["list", "tuple-subclass", "ten", "twelve"])
def test_originals_are_an_exact_eleven_element_builtin_tuple(originals, prepare_raw, reservation_raw, case):
    class TupleSubclass(tuple):
        pass

    invalid = {"list": list(originals), "tuple-subclass": TupleSubclass(originals),
               "ten": originals[:-1], "twelve": originals + (originals[-1],)}[case]
    assert_rejected(lambda: subject.build_prepare(originals=invalid))
    assert_rejected(lambda: subject.cross_validate_prepare(prepare_raw, originals=invalid))
    assert_rejected(lambda: subject.build_reservation(prepare_raw, originals=invalid))
    assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, prepare_raw, originals=invalid))


@pytest.mark.parametrize("case", ["bytes-subclass", "bytearray", "digest-only"], ids=["bytes-subclass", "bytearray", "digest-only"])
def test_original_slots_do_not_accept_mutable_subclass_or_digest_only_substitutes(originals, prepare_raw, reservation_raw, case):
    class BytesSubclass(bytes):
        pass

    changed = list(originals)
    changed[7] = {"bytes-subclass": BytesSubclass(originals[7]), "bytearray": bytearray(originals[7]),
                  "digest-only": sha(originals[7]).encode("ascii")}[case]
    changed = tuple(changed)
    assert_rejected(lambda: subject.build_prepare(originals=changed))
    assert_rejected(lambda: subject.cross_validate_prepare(prepare_raw, originals=changed))
    assert_rejected(lambda: subject.build_reservation(prepare_raw, originals=changed))
    assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, prepare_raw, originals=changed))


@pytest.mark.parametrize("case", ["top", "nested"], ids=["top", "nested"])
def test_duplicate_json_keys_are_rejected_before_any_normalization(prepare_raw, case):
    if case == "top":
        invalid = prepare_raw[:-1] + b',"format_version":1}'
    else:
        context_raw = canonical(subject.decode(prepare_raw)["context"])
        replacement = context_raw[:-1] + b',"domain_id":' + canonical(OTHER_UUID) + b'}'
        needle = b'"context":' + context_raw
        assert prepare_raw.count(needle) == 1
        invalid = prepare_raw.replace(needle, b'"context":' + replacement, 1)
    assert_rejected(lambda: subject.decode(invalid), "JSON_DUPLICATE_KEY")


@pytest.mark.parametrize("case", ["whitespace", "escaped-ascii"], ids=["whitespace", "escaped-ascii"])
def test_semantically_equal_noncanonical_json_is_not_an_alternative_raw_identity(prepare_raw, case):
    if case == "whitespace":
        invalid = json.dumps(subject.decode(prepare_raw), ensure_ascii=False, sort_keys=True, indent=1).encode("utf-8")
    else:
        needle = b'"sql_registration_prepare"'
        assert prepare_raw.count(needle) == 1
        invalid = prepare_raw.replace(needle, b'"sql\\u005fregistration_prepare"', 1)
    assert json.loads(invalid) == json.loads(prepare_raw) and invalid != prepare_raw
    assert_rejected(lambda: subject.decode(invalid), "JSON_NONCANONICAL")


@pytest.mark.parametrize("literal", [b"true", b"1.0", b"NaN", b"Infinity", b"-1", b"9223372036854775808"],
                         ids=["bool", "float", "nan", "infinity", "negative", "overflow"])
def test_wire_numbers_do_not_coerce_boolean_float_nonfinite_negative_or_overflow_slots(prepare_raw, literal):
    needle = b'"slot":1'
    assert prepare_raw.count(needle) == 1
    invalid = prepare_raw.replace(needle, b'"slot":' + literal, 1)
    assert_rejected(lambda: subject.decode(invalid))
    assert_rejected(lambda: subject.encode(json.loads(invalid)))


@pytest.mark.parametrize("case", ["empty", "prepare-over-limit", "reservation-over-limit"],
                         ids=["empty", "prepare-over-limit", "reservation-over-limit"])
def test_input_byte_limits_are_enforced_on_each_selected_new_raw_before_json_use(originals, prepare_raw, case):
    if case == "empty":
        assert_rejected(lambda: subject.decode(b""), "LIMIT")
    elif case == "prepare-over-limit":
        assert_rejected(lambda: subject.cross_validate_prepare(b" " * 32769, originals=originals), "LIMIT")
        assert_rejected(lambda: subject.build_reservation(b" " * 32769, originals=originals), "LIMIT")
    else:
        assert_rejected(lambda: subject.decode(b" " * 65537), "LIMIT")
        assert_rejected(lambda: subject.cross_validate_reservation(b" " * 65537, prepare_raw, originals=originals), "LIMIT")


@pytest.mark.parametrize("case", ["depth", "nodes"], ids=["depth", "nodes"])
def test_parser_depth_and_node_caps_reject_bounded_small_raw_before_closed_field_checks(case):
    if case == "depth":
        value = 0
        for _ in range(20):
            value = [value]
    else:
        value = [0] * 8200
    raw = canonical({"extra": value})
    assert len(raw) < 32768
    assert_rejected(lambda: subject.decode(raw), "LIMIT")


def test_reservation_rejects_inexact_base64_size_sha_context_slot_and_digest_only_payload(originals, prepare_raw, reservation_raw):
    original = subject.decode(reservation_raw)
    for case in ("padding", "newline", "size", "sha", "context", "operation", "slot", "digest-only"):
        changed = deepcopy(original)
        if case == "padding":
            changed["prepare_payload_base64"] += "="
        elif case == "newline":
            changed["prepare_payload_base64"] += "\n"
        elif case == "size":
            changed["prepare_payload_byte_size"] += 1
        elif case == "sha":
            changed["prepare_payload_sha256"] = different_sha(changed["prepare_payload_sha256"])
        elif case == "context":
            changed["context"]["domain_id"] = OTHER_UUID
        elif case == "operation":
            changed["logical_operation_id"] = OTHER_UUID
        elif case == "slot":
            changed["slot_identity"]["owner_node_id"] = OTHER_UUID
        else:
            digest_raw = sha(prepare_raw).encode("ascii")
            changed.update(prepare_payload_base64=base64.b64encode(digest_raw).decode("ascii"),
                           prepare_payload_byte_size=len(digest_raw), prepare_payload_sha256=sha(digest_raw))
        assert_rejected(lambda: subject.cross_validate_reservation(canonical(changed), prepare_raw, originals=originals))


def test_prepare_and_reservation_roles_are_not_interchangeable_at_fixed_api_boundaries(originals, prepare_raw, reservation_raw):
    assert_rejected(lambda: subject.cross_validate_prepare(reservation_raw, originals=originals))
    assert_rejected(lambda: subject.build_reservation(reservation_raw, originals=originals))
    assert_rejected(lambda: subject.cross_validate_reservation(prepare_raw, reservation_raw, originals=originals))
    assert_rejected(lambda: subject.cross_validate_reservation(reservation_raw, reservation_raw, originals=originals))
    for raw, wrong_kind in ((prepare_raw, "sql_registration_prepare_reservation"),
                            (reservation_raw, "sql_registration_prepare")):
        changed = subject.decode(raw)
        changed["kind"] = wrong_kind
        assert_rejected(lambda: subject.decode(canonical(changed)))


def test_invalid_utf8_is_rejected_as_json_without_latin1_or_replacement_decoding(prepare_raw):
    invalid = prepare_raw.replace(b'durable_prepare_content_only', b'durable_prepare_content_\xffnly', 1)
    assert invalid != prepare_raw
    assert_rejected(lambda: subject.decode(invalid), "JSON_INVALID")


def test_encode_does_not_accept_a_non_dictionary_or_dictionary_subclass(prepare_raw):
    class DictSubclass(dict):
        pass

    value = subject.decode(prepare_raw)
    for invalid in (list(value.items()), tuple(value.items()), DictSubclass(value)):
        assert_rejected(lambda: subject.encode(invalid))
