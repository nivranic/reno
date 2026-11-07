"""Pure content tests using five preserved files from one isolated r847 case.

Fixtures retain prior factory bytes, not current native identities or owners.
No path contained in a fixture is opened. Relinked variants are deliberately
synthetic content changes and never manufacture an enrollment close witness.
"""
import base64
from copy import deepcopy
from dataclasses import FrozenInstanceError, replace
import hashlib
import json
from pathlib import Path

import pytest

from reno import personal_authority_contract as authority
from reno import personal_authority_enrollment_contract as contract
from reno import personal_authority_namespace3_contract as namespace
from reno import personal_authority_origin_reservation_contract as origin


FIXTURE = Path(__file__).parent / "fixtures" / "personal_authority_enrollment_binding"
NAMES = ("origin-reservation.json", "origin-observation.json", "namespace-reservation.json",
         "namespace-root-seal.json", "namespace-header.json")
OTHER_NODE = "11111111-1111-4111-8111-111111111111"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


@pytest.fixture
def originals():
    return tuple((FIXTURE / name).read_bytes() for name in NAMES)


def assert_rejected(call, suffix=None):
    with pytest.raises(contract.EnrollmentContractError) as raised:
        call()
    if suffix is not None:
        assert raised.value.code == "ENROLLMENT_CONTRACT_" + suffix


def relinked_variant(originals, *, change_first=False):
    """A new, internally linked declaration set; no new physical fact claim."""
    first, second, reservation, seal, header = map(json.loads, originals)
    intent = json.loads(base64.b64decode(second["origin_intent_base64"], validate=True))
    evidence = json.loads(base64.b64decode(second["origin_evidence_base64"], validate=True))
    if change_first:
        first["node"]["sha256"] = "f" * 64 if first["node"]["sha256"] != "f" * 64 else "e" * 64
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


def test_portable_fixture_pins_retain_one_prior_factory_case_without_opening_its_paths(originals):
    provenance = json.loads((FIXTURE / "provenance.json").read_bytes())
    rows = {Path(row["fixture"]["path"]).name: row for row in provenance["input_pairs"]}
    for name, raw in zip(NAMES, originals):
        row = rows[name]
        for key in ("fixture", "original", "preservation_copy"):
            assert len(raw) == row[key]["byte_size"] and sha(raw) == row[key]["sha256"]
        assert "test_r841_pending_owned_falsey0/r839-1-absent/data/" in row["original"]["path"]
    origin.cross_validate(*originals[:2])
    namespace.cross_validate_complete(*originals)


def test_binding_links_all_original_bytes_roles_root_identity_and_second_sql_observation(originals):
    raw = contract.build_binding(*originals)
    binding = contract.decode(raw)
    first, second, _reservation, seal, header = map(json.loads, originals)
    intent_raw = base64.b64decode(second["origin_intent_base64"], validate=True)
    intent = authority.decode(intent_raw)
    assert contract.cross_validate(raw, *originals) is None
    assert contract.encode(binding) == raw and contract.record_sha256(raw) == sha(raw)
    assert binding["init_operation_id"] == intent["operation_id"] == first["init_operation_id"]
    assert binding["domain_id"] == first["domain_id"] == intent["domain_id"]
    assert binding["issuer"] == intent["issuer"]
    assert binding["issuer"]["facility_id"] == first["facility_id"]
    assert binding["issuer"]["node_id"] == first["node"]["node_id"]
    assert binding["profiles"]["authority_profile_paths_sha256"] == first["authority_profile_paths_sha256"]
    assert binding["profiles"]["physical_profile_sha256"] == first["physical_profile_sha256"]
    assert binding["origin"]["reservation_sha256"] == sha(originals[0])
    assert binding["origin"]["observation_sha256"] == sha(originals[1])
    assert binding["origin"]["intent_sha256"] == sha(intent_raw)
    assert binding["origin"]["evidence_sha256"] == second["origin_evidence_sha256"]
    assert binding["origin"]["intent_observation"] == intent["payload"]
    for key, index in (("reservation_sha256", 2), ("root_seal_sha256", 3), ("header_sha256", 4)):
        assert binding["namespace"][key] == sha(originals[index])
    assert binding["namespace"]["root_native_identity"] == header["root_native_identity"]
    assert binding["namespace"]["root_path_identity"] == seal["creator"]["root_path_identity"]
    binding["namespace"]["root_path_identity"]["inode"] = "999"
    assert contract.decode(raw)["namespace"]["root_path_identity"] == header["root_path_identity"]


@pytest.mark.parametrize("change_first", [False, True], ids=["second-new-epoch", "first-new-node-bytes"])
def test_an_internally_relinked_new_origin_set_cannot_reuse_the_old_binding(originals, change_first):
    old_binding = contract.build_binding(*originals)
    new_originals = relinked_variant(originals, change_first=change_first)
    origin.cross_validate(*new_originals[:2])
    namespace.cross_validate_complete(*new_originals)
    new_binding = contract.build_binding(*new_originals)
    assert new_binding != old_binding
    assert contract.cross_validate(new_binding, *new_originals) is None
    assert_rejected(lambda: contract.cross_validate(old_binding, *new_originals), "BINDING_MISMATCH")
    assert_rejected(lambda: contract.prepare_authority_prefix(old_binding, *new_originals), "BINDING_MISMATCH")


@pytest.mark.parametrize("index", range(5), ids=["first", "second", "reservation", "root-seal", "header"])
def test_changing_any_single_original_uuid_breaks_the_source_chain(originals, index):
    changed = list(originals)
    document = json.loads(changed[index])
    document["init_operation_id"] = OTHER_NODE
    assert document["init_operation_id"] != json.loads(originals[index])["init_operation_id"]
    changed[index] = canonical(document)
    assert_rejected(lambda: contract.build_binding(*changed))


def test_second_with_primary_other_than_the_retained_node_is_refused(originals):
    first, second, *_rest = map(json.loads, originals)
    intent = json.loads(base64.b64decode(second["origin_intent_base64"], validate=True))
    assert first["node"]["node_id"] != OTHER_NODE
    intent["payload"]["observed_primary_node_id"] = OTHER_NODE
    raw = canonical(intent)
    second.update(origin_intent_base64=base64.b64encode(raw).decode("ascii"), origin_intent_sha256=sha(raw))
    assert_rejected(lambda: contract.build_binding(originals[0], canonical(second), *originals[2:]),
                    "ORIGIN_BINDING_INVALID")


def test_second_cannot_replace_its_sql_observation_hash_with_a_caller_hash(originals):
    second = json.loads(originals[1])
    intent = json.loads(base64.b64decode(second["origin_intent_base64"], validate=True))
    intent["payload"]["observed_personal_sql_sha256"] = "a" * 64
    assert intent["payload"]["observed_personal_sql_sha256"] != authority.decode(
        base64.b64decode(second["origin_intent_base64"], validate=True))["payload"]["observed_personal_sql_sha256"]
    raw = canonical(intent)
    second.update(origin_intent_base64=base64.b64encode(raw).decode("ascii"), origin_intent_sha256=sha(raw))
    assert_rejected(lambda: contract.build_binding(originals[0], canonical(second), *originals[2:]),
                    "ORIGIN_BINDING_INVALID")


def test_equal_hash_text_still_retains_explicit_distinct_profile_roles(originals):
    declaration = contract.decode(contract.build_binding(*originals))
    profiles = declaration["profiles"]
    profiles["physical_profile_sha256"] = profiles["authority_profile_paths_sha256"]
    encoded = contract.encode(declaration)
    assert contract.decode(encoded)["profiles"]["authority_profile_paths_role"] == namespace.AUTHORITY_ROLE
    assert contract.decode(encoded)["profiles"]["physical_profile_role"] == namespace.PHYSICAL_ROLE
    profiles["authority_profile_paths_role"], profiles["physical_profile_role"] = (
        profiles["physical_profile_role"], profiles["authority_profile_paths_role"])
    assert_rejected(lambda: contract.encode(declaration), "ROLE_INVALID")


def test_array_and_dictionary_hashes_cannot_be_substituted_for_the_issuer_role(originals):
    value = contract.decode(contract.build_binding(*originals))
    profiles = value["profiles"]
    assert profiles["authority_profile_paths_sha256"] != profiles["physical_profile_sha256"]
    profiles["authority_profile_paths_sha256"], profiles["physical_profile_sha256"] = (
        profiles["physical_profile_sha256"], profiles["authority_profile_paths_sha256"])
    assert_rejected(lambda: contract.encode(value), "BINDING_MISMATCH")
    value = contract.decode(contract.build_binding(*originals))
    value["issuer"]["profile_sha256"] = value["profiles"]["physical_profile_sha256"]
    assert_rejected(lambda: contract.encode(value), "BINDING_MISMATCH")


@pytest.mark.parametrize("section,field", [
    ("origin", "reservation_sha256"), ("origin", "observation_sha256"),
    ("origin", "intent_sha256"), ("origin", "evidence_sha256"),
    ("namespace", "reservation_sha256"), ("namespace", "root_seal_sha256"),
    ("namespace", "header_sha256"),
], ids=["first-sha", "second-sha", "intent-sha", "evidence-sha", "reservation-sha", "seal-sha", "header-sha"])
def test_a_valid_hash_alone_cannot_substitute_for_any_original_reference(originals, section, field):
    value = contract.decode(contract.build_binding(*originals))
    value[section][field] = "f" * 64 if value[section][field] != "f" * 64 else "e" * 64
    altered = contract.encode(value)
    assert_rejected(lambda: contract.cross_validate(altered, *originals), "BINDING_MISMATCH")


@pytest.mark.parametrize("section", ["origin", "namespace"])
@pytest.mark.parametrize("bad", [2, True], ids=["unknown-version", "boolean-version"])
def test_nested_dependency_versions_cannot_expand_existing_formats(originals, section, bad):
    value = contract.decode(contract.build_binding(*originals))
    value[section]["format_version"] = bad
    assert_rejected(lambda: contract.encode(value))


@pytest.mark.parametrize("path", [(), ("profiles",), ("origin",), ("origin", "intent_observation"),
                                  ("namespace",), ("issuer",)],
                         ids=["record", "profiles", "origin", "observation", "namespace", "issuer"])
def test_unknown_fields_cannot_add_closure_or_writable_claims(originals, path):
    value = contract.decode(contract.build_binding(*originals))
    selected = value
    for key in path:
        selected = selected[key]
    selected["source_closed"] = 1
    assert_rejected(lambda: contract.encode(value), "FIELDS_INVALID")


@pytest.mark.parametrize("field,bad,suffix", [
    ("format_version", 2, "VERSION_INVALID"),
    ("format_version", True, "JSON_INVALID"),
    ("family", "personal_authority_namespace2", "VERSION_INVALID"),
    ("kind", "enrollment_completion", "KIND_INVALID"),
    ("scope", "active", "VERSION_INVALID"),
    ("init_operation_id", "00000000-0000-0000-0000-000000000000", "UUID_INVALID"),
    ("domain_id", "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", "UUID_INVALID"),
], ids=["version", "boolean-version", "family", "completion-kind", "active-scope", "nil-uuid", "uuid-case"])
def test_record_family_version_and_identity_are_closed(originals, field, bad, suffix):
    value = contract.decode(contract.build_binding(*originals))
    value[field] = bad
    assert_rejected(lambda: contract.encode(value), suffix)


@pytest.mark.parametrize("bad", [0, -1, True, authority.MAX_INTEGER + 1],
                         ids=["zero", "negative", "boolean", "overflow"])
def test_current_epoch_is_a_positive_strict_integer(originals, bad):
    value = contract.decode(contract.build_binding(*originals))
    value["origin"]["intent_observation"]["current_epoch"] = bad
    assert_rejected(lambda: contract.encode(value))


@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "a" * 65, 1],
                         ids=["uppercase", "short", "long", "integer"])
def test_content_sha256_requires_exact_lowercase_hex(originals, bad):
    value = contract.decode(contract.build_binding(*originals))
    value["origin"]["evidence_sha256"] = bad
    assert_rejected(lambda: contract.encode(value), "SHA_INVALID")


@pytest.mark.parametrize("raw,suffix", [
    (b'{"x":0,"x":1}', "JSON_DUPLICATE_KEY"), (b'{ "x":0}', "JSON_NONCANONICAL"),
    (b'\xff', "JSON_INVALID"), (b'{"x":1.5}', "JSON_INVALID"), (b'{"x":NaN}', "JSON_INVALID"),
    (b'{"x":true}', "JSON_INVALID"), (b'{"x":9223372036854775808}', "INTEGER_INVALID"),
    (b'[' * 17 + b'0' + b']' * 17, "LIMIT"),
    (b'[' + b','.join([b'0'] * 4096) + b']', "LIMIT"),
    (b' ' * (16 * 1024 + 1), "LIMIT"),
    (None, "LIMIT"), (bytearray(b'{}'), "LIMIT"), (b'', "LIMIT"),
], ids=["duplicate", "noncanonical", "utf8", "float", "nan", "boolean", "overflow", "depth",
        "nodes", "bytes", "none", "mutable", "empty"])
def test_decoder_refuses_ambiguous_or_unbounded_input(raw, suffix):
    assert_rejected(lambda: contract.decode(raw), suffix)


def test_encode_bounds_nested_cycles_wide_objects_and_large_strings():
    cycle = []
    cycle.append(cycle)
    for value in (cycle, [0] * 4096, {"x": "a" * (origin.MAX_OBSERVATION_BYTES + 1)}):
        assert_rejected(lambda: contract.encode(value), "LIMIT")


@pytest.mark.parametrize("index,maximum", [
    (0, origin.MAX_RESERVATION_BYTES), (1, origin.MAX_OBSERVATION_BYTES),
    (2, namespace.MAX_RESERVATION_BYTES), (3, namespace.MAX_ROOT_SEAL_BYTES),
    (4, namespace.MAX_HEADER_BYTES),
], ids=["first-cap", "second-cap", "reservation-cap", "seal-cap", "header-cap"])
def test_all_five_original_inputs_are_bounded_before_dependency_validation(originals, monkeypatch, index, maximum):
    changed = list(originals)
    changed[index] = b' ' * (maximum + 1)
    def forbidden(*_args):
        pytest.fail("unbounded input reached dependency")
    monkeypatch.setattr(origin, "cross_validate", forbidden)
    assert_rejected(lambda: contract.build_binding(*changed), "LIMIT")


def test_prepared_prefix_retains_original_intent_bytes_and_the_complete_unchanged_format1_chain(originals):
    binding_raw = contract.build_binding(*originals)
    content = contract.prepare_authority_prefix(binding_raw, *originals)
    second = json.loads(originals[1])
    assert content.origin_intent_bytes == base64.b64decode(second["origin_intent_base64"], validate=True)
    records = (content.origin_intent_bytes, content.local_contract_bytes,
               content.init_seal_bytes, content.genesis_grant_bytes)
    authority.cross_validate(records)
    assert contract.cross_validate_authority_prefix(content, binding_raw, *originals) is None
    prefix = contract.decode(content.prefix_bytes)
    assert prefix["scope"] == "content_only" and prefix["binding_sha256"] == sha(binding_raw)
    assert set(prefix) == {"format_version", "family", "kind", "scope", "init_operation_id",
                           "domain_id", "issuer", "binding_sha256", "authority"}
    for key, raw, kind in zip(("origin_init_intent_sha256", "local_contract_sha256", "init_seal_sha256",
                              "genesis_grant_sha256"), records,
                             ("origin_init_intent", "local_contract", "init_seal", "genesis_grant")):
        decoded = authority.decode(raw)
        assert prefix["authority"][key] == sha(raw)
        assert decoded["kind"] == kind and decoded["format_version"] == 1
        assert set(decoded) == set(authority.ENVELOPE_FIELDS)
        assert set(decoded["payload"]) == set(authority.PAYLOAD_FIELDS[kind])
        assert decoded["issuer"] == prefix["issuer"]
        assert decoded["operation_id"] == prefix["init_operation_id"]
    assert authority.decode(content.local_contract_bytes)["payload"]["init_kind"] == "origin"
    assert authority.decode(content.genesis_grant_bytes)["payload"]["epoch"] == authority.decode(
        content.origin_intent_bytes)["payload"]["current_epoch"]
    with pytest.raises(FrozenInstanceError):
        content.prefix_bytes = b'{}'


def test_new_second_epoch_is_preserved_in_genesis_without_a_caller_epoch_parameter(originals):
    changed = relinked_variant(originals)
    binding = contract.build_binding(*changed)
    content = contract.prepare_authority_prefix(binding, *changed)
    intent = authority.decode(content.origin_intent_bytes)
    assert intent["payload"] == contract.decode(binding)["origin"]["intent_observation"]
    assert authority.decode(content.genesis_grant_bytes)["payload"]["epoch"] == intent["payload"]["current_epoch"]


@pytest.mark.parametrize("field", ["prefix_bytes", "origin_intent_bytes", "local_contract_bytes",
                                   "init_seal_bytes", "genesis_grant_bytes"])
def test_every_forged_frozen_dto_byte_field_is_revalidated(originals, field):
    binding = contract.build_binding(*originals)
    content = contract.prepare_authority_prefix(binding, *originals)
    object.__setattr__(content, field, b'{}')
    assert_rejected(lambda: contract.cross_validate_authority_prefix(content, binding, *originals))


def test_prefix_hash_and_chain_links_cannot_be_changed_independently(originals):
    binding = contract.build_binding(*originals)
    content = contract.prepare_authority_prefix(binding, *originals)
    prefix = contract.decode(content.prefix_bytes)
    prefix["authority"]["genesis_grant_sha256"] = "f" * 64
    altered = replace(content, prefix_bytes=contract.encode(prefix))
    assert_rejected(lambda: contract.cross_validate_authority_prefix(altered, binding, *originals),
                    "BINDING_MISMATCH")
    genesis = authority.decode(content.genesis_grant_bytes)
    genesis["payload"]["epoch"] += 1
    altered = replace(content, genesis_grant_bytes=authority.encode(genesis))
    assert_rejected(lambda: contract.cross_validate_authority_prefix(altered, binding, *originals),
                    "AUTHORITY_BINDING_INVALID")


@pytest.mark.parametrize("error", [MemoryError(), KeyboardInterrupt(), SystemExit(3)],
                         ids=["memory", "keyboard", "system-exit"])
def test_origin_dependency_control_and_memory_exceptions_keep_the_original_object(originals, monkeypatch, error):
    def fail(*_args):
        raise error
    monkeypatch.setattr(origin, "cross_validate", fail)
    with pytest.raises(type(error)) as raised:
        contract.build_binding(*originals)
    assert raised.value is error


def test_encode_memory_error_is_not_converted_to_a_contract_refusal(originals, monkeypatch):
    value = contract.decode(contract.build_binding(*originals))
    error = MemoryError()
    def fail(_value):
        raise error
    monkeypatch.setattr(contract, "_canonical", fail)
    with pytest.raises(MemoryError) as raised:
        contract.encode(value)
    assert raised.value is error
