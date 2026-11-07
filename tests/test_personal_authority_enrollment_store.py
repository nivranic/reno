"""Pure S5A candidates with an independent declaration oracle.

Only five portable adjacent fixture files are read after canonical application.
Paths declared inside them are never opened. The synthetic capacity rows test
local structure only, not old-codec originals or persistent/SQL/native facts.
No conftest/test module, SQL connection or runtime continuation is imported.
"""
import base64
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from reno import personal_authority_enrollment_contract as enrollment
from reno import personal_authority_enrollment_successor as prepare
from reno import personal_authority_namespace3_contract as namespace
from reno import personal_authority_origin_reservation_contract as origin
from reno import personal_authority_enrollment_store as subject


FIXTURE = Path(__file__).parent / "fixtures" / "personal_authority_enrollment_binding"
FIXTURE_NAMES = ("origin-reservation.json", "origin-observation.json", "namespace-reservation.json",
                 "namespace-root-seal.json", "namespace-header.json")
FIXTURE_PINS = (
    (2806, "2fa551826ebb923917cb04b8ea20a79b84dd316edcb910ea2571841b25058531"),
    (11100, "6eb52c9a8d69a0148770a76aaaa641731bd5d2bc3594f2e341ff5ae9c4bdf1f9"),
    (3655, "6199a570a8d316d063d6aafc5c9ef2cea6426a20066212a87bab768a791737ac"),
    (918, "6215b60e393af74bb8009646ef505eba586e16cdfecf03fe5430f4c3bc874dde"),
    (750, "6c57ac2203b7fb03a1009f8f3793bed2fdfd27eb6d504c4c3b8178f4fcffb176"),
)
FAMILY = "personal_authority_enrollment_store"
SCOPE = "namespace3_initial_registration_filesystem_only"
PREFIX = ".reno-personal-authority-sql-store-v1-"
OTHER_UUID = "11111111-1111-4111-8111-111111111111"
ROLES = ("origin_intent", "local_contract", "init_seal", "genesis_grant", "prepare")
STEMS = ("intent", "contract", "seal", "genesis", "prepare")
SUFFIXES = ("slot.json", "reservation.json", "envelope.json", "published.json", "receipt.json")
ORIGINAL_SPECS = (
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
ORIGINAL_NAMES = (
    ".reno-personal-authority-origin-v1-reservation.json", ".reno-personal-authority-origin-v1-observation.json",
    ".reno-personal-authority-namespace-v3-reservation.json", ".reno-personal-authority-namespace-v3-root-seal.json",
    ".reno-personal-authority-namespace-v3-header.json", ".reno-personal-authority-enrollment-binding.json",
    ".reno-personal-authority-enrollment-prefix.json", ".reno-personal-authority-origin-init-intent.json",
    ".reno-personal-authority-local-contract.json", ".reno-personal-authority-init-seal.json",
    ".reno-personal-authority-genesis-grant.json",
)
ORIGINAL_MAXIMA = (16384, 65536, 32768, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384)


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def other_sha(value):
    return "f" * 64 if value != "f" * 64 else "e" * 64


def rejected(call, suffix=None):
    with pytest.raises(subject.EnrollmentStoreContractError) as caught:
        call()
    assert type(caught.value) is subject.EnrollmentStoreContractError
    assert caught.value.code.startswith("ENROLLMENT_STORE_CONTRACT_")
    if suffix is not None:
        assert caught.value.code == "ENROLLMENT_STORE_CONTRACT_" + suffix


@pytest.fixture(scope="module")
def materials():
    five = tuple((FIXTURE / name).read_bytes() for name in FIXTURE_NAMES)
    assert tuple((len(raw), sha(raw)) for raw in five) == FIXTURE_PINS
    origin.cross_validate(*five[:2])
    namespace.cross_validate_complete(*five)
    binding = enrollment.build_binding(*five)
    content = enrollment.prepare_authority_prefix(binding, *five)
    enrollment.cross_validate_authority_prefix(content, binding, *five)
    originals = five + (binding, content.prefix_bytes, content.origin_intent_bytes,
                        content.local_contract_bytes, content.init_seal_bytes, content.genesis_grant_bytes)
    payload = prepare.build_prepare(originals=originals)
    reservation = prepare.build_reservation(payload, originals=originals)
    return originals, payload, reservation


def options(materials):
    originals, payload, reservation = materials
    return {"originals": originals, "prepare_payload_raw": payload,
            "prepare_content_reservation_raw": reservation}


def independent_oracle(materials):
    """Specify every Store declaration from raw originals, no subject helpers."""
    originals, payload_raw, content_reservation_raw = materials
    first, ns_header, intent = (json.loads(originals[index]) for index in (0, 4, 7))
    context = {"domain_id": intent["domain_id"], "init_operation_id": intent["operation_id"],
               "issuer": deepcopy(intent["issuer"])}
    slot_identity = {"slot": 1, "namespace_version": 3, "domain_id": context["domain_id"],
                     "facility_id": context["issuer"]["facility_id"],
                     "owner_node_id": context["issuer"]["node_id"],
                     "init_operation_id": context["init_operation_id"]}
    refs = {key: {"role": role, "codec": codec, "record_kind": kind, "format_version": 1,
                  "raw_byte_size": len(raw), "raw_sha256": sha(raw), "identity": deepcopy(context)}
            for raw, (key, role, codec, kind) in zip(originals, ORIGINAL_SPECS)}
    profiles = {"authority_profile_paths_role": "canonical_four_paths_array_v1",
                "authority_profile_paths_sha256": sha(canonical(first["profile"]["profile_paths"])),
                "physical_profile_role": "canonical_physical_profile_dict_v1",
                "physical_profile_sha256": sha(canonical(first["profile"]))}
    assert profiles["authority_profile_paths_sha256"] == context["issuer"]["profile_sha256"]
    assert json.loads(payload_raw)["original_refs"] == refs
    wrapper_names = [PREFIX + "header.json", PREFIX + "operation.json"] + [
        PREFIX + stem + "-" + suffix for stem in STEMS for suffix in SUFFIXES]
    locations = {spec[0]: {"directory": "data_parent" if i in (0, 1, 2, 4) else "namespace_root",
                            "name": ORIGINAL_NAMES[i]} for i, spec in enumerate(ORIGINAL_SPECS)}
    layout = {"root_name": ".reno-personal-authority-namespace-v3", "original_locations": locations,
              "wrapper_names": wrapper_names, "prepare_payload_name": PREFIX + "prepare-payload.json",
              "prepare_content_reservation_name": PREFIX + "prepare-content-reservation.json",
              "publication_order": ["origin_intent", "local_contract", "init_seal", "genesis_grant", "binding", "prefix"],
              "history_gc": "forbidden"}

    def common(kind):
        return {"format_version": 1, "family": FAMILY, "kind": kind, "scope": SCOPE,
                "logical_operation_id": context["init_operation_id"], "slot_identity": deepcopy(slot_identity)}

    header = common("header")
    header.update(context=deepcopy(context), profiles=profiles,
                  namespace={"family": "personal_authority_namespace3", "format_version": 1,
                             "root_name": ns_header["root_name"],
                             "root_native_identity": ns_header["root_native_identity"],
                             "root_path_identity": ns_header["root_path_identity"]},
                  namespace_header_sha256=sha(originals[4]), original_refs=refs,
                  prepare_payload_sha256=sha(payload_raw),
                  prepare_content_reservation_sha256=sha(content_reservation_raw),
                  layout=layout, record_order=list(ROLES))
    header_raw = canonical(header)
    header_sha = sha(header_raw)
    operation = common("operation")
    operation.update(header_sha256=header_sha, context=deepcopy(context))
    operation_raw = canonical(operation)
    output = [header_raw, operation_raw]
    previous_facts = []
    previous_envelope_sha = header_sha
    for sequence, role in enumerate(ROLES):
        raw = originals[7 + sequence] if sequence < 4 else payload_raw
        envelope = common("envelope")
        reference_keys = [spec[0] for spec in ORIGINAL_SPECS[7:8 + sequence]] if sequence < 4 else [spec[0] for spec in ORIGINAL_SPECS]
        content_ref = None if sequence < 4 else {
            "role": "selected_sql_registration_prepare_reservation", "codec": "personal_authority_enrollment_sql_prepare",
            "record_kind": "sql_registration_prepare_reservation", "format_version": 1,
            "raw_byte_size": len(content_reservation_raw), "raw_sha256": sha(content_reservation_raw),
            "identity": deepcopy(context)}
        envelope.update(header_sha256=header_sha, context=deepcopy(context), record_role=role,
                        sequence=sequence, parent_record_sha256=previous_envelope_sha,
                        payload_role=ORIGINAL_SPECS[7 + sequence][1] if sequence < 4 else "selected_sql_registration_prepare",
                        payload_location=deepcopy(locations[role]) if sequence < 4 else {
                            "directory": "namespace_root", "name": PREFIX + "prepare-payload.json"},
                        payload_sha256=sha(raw), payload_byte_size=len(raw),
                        refs={"original_refs": [{"original_key": key, "reference": deepcopy(refs[key])} for key in reference_keys],
                              "prepare_content_reservation_ref": content_ref,
                              "predecessor_facts": deepcopy(previous_facts)})
        envelope_raw = canonical(envelope)
        slot = common("slot")
        slot.update(header_sha256=header_sha, record_role=role, sequence=sequence,
                    parent_record_sha256=previous_envelope_sha, envelope_sha256=sha(envelope_raw),
                    payload_sha256=sha(raw), payload_byte_size=len(raw))
        slot_raw = canonical(slot)
        reservation = common("record_reservation")
        reservation.update(header_sha256=header_sha, record_role=role, sequence=sequence,
                           parent_record_sha256=previous_envelope_sha,
                           envelope_raw_base64=base64.b64encode(envelope_raw).decode("ascii"),
                           envelope_byte_size=len(envelope_raw), envelope_sha256=sha(envelope_raw),
                           payload_raw_base64=base64.b64encode(raw).decode("ascii"),
                           payload_byte_size=len(raw), payload_sha256=sha(raw))
        reservation_raw = canonical(reservation)
        published = common("published")
        published.update(header_sha256=header_sha, record_role=role, operation_sha256=sha(operation_raw),
                         slot_sha256=sha(slot_raw), record_reservation_sha256=sha(reservation_raw),
                         envelope_sha256=sha(envelope_raw), payload_sha256=sha(raw),
                         prepare_content_reservation_sha256=sha(content_reservation_raw) if sequence == 4 else None)
        published_raw = canonical(published)
        receipt = common("receipt")
        receipt.update(header_sha256=header_sha, record_role=role, sequence=sequence,
                       parent_record_sha256=previous_envelope_sha, published_sha256=sha(published_raw),
                       record_reservation_sha256=sha(reservation_raw), envelope_sha256=sha(envelope_raw),
                       payload_sha256=sha(raw))
        receipt_raw = canonical(receipt)
        output += [slot_raw, reservation_raw]
        if sequence == 4:
            output += [payload_raw, content_reservation_raw]
        output += [envelope_raw, published_raw, receipt_raw]
        previous_facts.append({"record_role": role, "sequence": sequence, "slot_sha256": sha(slot_raw),
                               "record_reservation_sha256": sha(reservation_raw), "envelope_sha256": sha(envelope_raw),
                               "payload_sha256": sha(raw), "published_sha256": sha(published_raw), "receipt_sha256": sha(receipt_raw)})
        previous_envelope_sha = sha(envelope_raw)
    return tuple(output)


@pytest.fixture(scope="module")
def expected(materials):
    return independent_oracle(materials)


@pytest.fixture(scope="module")
def files(materials, expected):
    produced = subject.build_prepare_files(**options(materials))
    assert type(produced) is tuple and produced == expected
    assert len(produced) == 29 and all(type(raw) is bytes for raw in produced)
    return produced


def test_build_independent_full_oracle_and_original_raw_retention(materials, files, expected):
    assert files == expected
    assert files[24] == materials[1] and files[25] == materials[2]
    assert subject.cross_validate_prepare_files(files, **options(materials)) is None
    header = json.loads(files[0])
    assert header["prepare_payload_sha256"] == sha(materials[1])
    assert header["prepare_content_reservation_sha256"] == sha(materials[2])
    assert header["prepare_payload_sha256"] != header["prepare_content_reservation_sha256"]
    assert tuple(header["layout"]["wrapper_names"]) == subject.WRAPPER_NAMES
    expected_names = subject.WRAPPER_NAMES[:24] + (PREFIX + "prepare-payload.json", PREFIX + "prepare-content-reservation.json") + subject.WRAPPER_NAMES[24:]
    assert subject.FILE_NAMES == expected_names and len(set(subject.FILE_NAMES)) == 29


@pytest.mark.parametrize("index", [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,26,27,28],
                         ids=["header","operation","intent-slot","intent-reservation","intent-envelope","intent-published","intent-receipt","contract-slot","contract-reservation","contract-envelope","contract-published","contract-receipt","seal-slot","seal-reservation","seal-envelope","seal-published","seal-receipt","genesis-slot","genesis-reservation","genesis-envelope","genesis-published","genesis-receipt","prepare-slot","prepare-reservation","prepare-envelope","prepare-published","prepare-receipt"])
def test_wrapper_roundtrip_and_detached_return(index, files):
    decoded = subject.decode(files[index])
    assert decoded == json.loads(files[index]) and subject.encode(decoded) == files[index]
    decoded["family"] = "changed"
    assert subject.decode(files[index])["family"] == FAMILY


@pytest.mark.parametrize("count", [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23,24,25,26,27,28,29],
                         ids=["k00","k01","k02","k03","k04","k05","k06","k07","k08","k09","k10","k11","k12","k13","k14","k15","k16","k17","k18","k19","k20","k21","k22","k23","k24","k25","k26","k27","k28","k29"])
def test_all_thirty_prefix_states(count, files, materials):
    partial = files[:count] + (None,) * (29 - count)
    state = "unstarted" if count == 0 else "prepare_content_complete" if count == 29 else "prepare_partial"
    assert subject.cross_validate_prepare_prefix(partial, **options(materials)) == state
    if count < 29:
        rejected(lambda: subject.cross_validate_prepare_files(partial, **options(materials)))


@pytest.mark.parametrize("index", [0,1,2,3,4,5,6,7,8,9,10],
                         ids=["first","second","namespace-reservation","root-seal","namespace-header","binding","prefix","intent","contract","seal","genesis"])
def test_each_original_replacement_rejected(index, materials, files):
    original = list(materials[0])
    original[index] += b"\n"
    changed = dict(options(materials), originals=tuple(original))
    rejected(lambda: subject.build_prepare_files(**changed))
    rejected(lambda: subject.cross_validate_prepare_files(files, **changed))


@pytest.mark.parametrize("index", [0,1,2,4,3,5,6], ids=["header","operation","slot","envelope","reservation","published","receipt"])
def test_every_kind_rejects_extra_field(index, files):
    value = json.loads(files[index])
    value["runtime_grant"] = "forbidden"
    rejected(lambda: subject.encode(value))
    rejected(lambda: subject.decode(canonical(value)))


@pytest.mark.parametrize("case", ["missing-original","reordered-original","extra-original","wrong-reference-role","same-sha-reference-swap","foreign-identity","reordered-facts","extra-fact","wrong-fact-payload","wrong-payload-location","null-content-ref"],
                         ids=["missing-original","reordered-original","extra-original","wrong-reference-role","same-sha-reference-swap","foreign-identity","reordered-facts","extra-fact","wrong-fact-payload","wrong-payload-location","null-content-ref"])
def test_local_prepare_envelope_fixed_role_domain(case, files):
    value = json.loads(files[26])
    refs = value["refs"]
    if case == "missing-original":
        refs["original_refs"].pop()
    elif case == "reordered-original":
        refs["original_refs"][0], refs["original_refs"][1] = refs["original_refs"][1], refs["original_refs"][0]
    elif case == "extra-original":
        refs["original_refs"].append(deepcopy(refs["original_refs"][0]))
    elif case == "wrong-reference-role":
        refs["original_refs"][0]["reference"]["role"] = "original_origin_observation"
    elif case == "same-sha-reference-swap":
        first, second = refs["original_refs"][:2]
        for entry in (first, second):
            entry["reference"]["raw_sha256"] = "a" * 64
            entry["reference"]["raw_byte_size"] = 1
        first["reference"], second["reference"] = second["reference"], first["reference"]
    elif case == "foreign-identity":
        refs["original_refs"][0]["reference"]["identity"]["domain_id"] = OTHER_UUID
    elif case == "reordered-facts":
        refs["predecessor_facts"].reverse()
    elif case == "extra-fact":
        refs["predecessor_facts"].append(deepcopy(refs["predecessor_facts"][0]))
    elif case == "wrong-fact-payload":
        refs["predecessor_facts"][0]["payload_sha256"] = other_sha(refs["predecessor_facts"][0]["payload_sha256"])
    elif case == "wrong-payload-location":
        value["payload_location"]["name"] += ".stage"
    else:
        refs["prepare_content_reservation_ref"] = None
    rejected(lambda: subject.decode(canonical(value)))
    rejected(lambda: subject.encode(value))


@pytest.mark.parametrize("index", [4,9,14,19,26], ids=["intent","contract","seal","genesis","prepare"])
def test_wrong_sequence_and_parent(index, files, materials):
    changed = list(files)
    value = json.loads(files[index])
    value["sequence"] = (value["sequence"] + 1) % 5
    changed[index] = canonical(value)
    rejected(lambda: subject.decode(changed[index]))
    rejected(lambda: subject.cross_validate_prepare_files(tuple(changed), **options(materials)))
    value = json.loads(files[index])
    value["parent_record_sha256"] = other_sha(value["parent_record_sha256"])
    changed[index] = canonical(value)
    rejected(lambda: subject.decode(changed[index]))
    rejected(lambda: subject.cross_validate_prepare_files(tuple(changed), **options(materials)))


@pytest.mark.parametrize("case", ["alphabet","extra-padding","envelope-sha","payload-sha","envelope-size","payload-size","noncanonical-inner","wrong-inner-kind","changed-payload"],
                         ids=["alphabet","extra-padding","envelope-sha","payload-sha","envelope-size","payload-size","noncanonical-inner","wrong-inner-kind","changed-payload"])
def test_reservation_raw_base64_and_inner_closure(case, files):
    value = json.loads(files[23])
    if case == "alphabet":
        value["payload_raw_base64"] = "***"
    elif case == "extra-padding":
        value["payload_raw_base64"] += "="
    elif case == "envelope-sha":
        value["envelope_sha256"] = other_sha(value["envelope_sha256"])
    elif case == "payload-sha":
        value["payload_sha256"] = other_sha(value["payload_sha256"])
    elif case == "envelope-size":
        value["envelope_byte_size"] += 1
    elif case == "payload-size":
        value["payload_byte_size"] += 1
    elif case == "noncanonical-inner":
        raw = base64.b64decode(value["envelope_raw_base64"], validate=True) + b" "
        value.update(envelope_raw_base64=base64.b64encode(raw).decode("ascii"), envelope_sha256=sha(raw), envelope_byte_size=len(raw))
    elif case == "wrong-inner-kind":
        inner = json.loads(base64.b64decode(value["envelope_raw_base64"], validate=True))
        inner["kind"] = "record_reservation"
        raw = canonical(inner)
        value.update(envelope_raw_base64=base64.b64encode(raw).decode("ascii"), envelope_sha256=sha(raw), envelope_byte_size=len(raw))
    else:
        raw = base64.b64decode(value["payload_raw_base64"], validate=True) + b" "
        value["payload_raw_base64"] = base64.b64encode(raw).decode("ascii")
    rejected(lambda: subject.decode(canonical(value)))
    rejected(lambda: subject.encode(value))


@pytest.mark.parametrize("value", [True, False, -1, 1.0, None, 9223372036854775808],
                         ids=["true","false","negative","float","null","too-large-int"])
def test_illegal_numeric_domains(value, files):
    row = json.loads(files[2])
    row["sequence"] = value
    rejected(lambda: subject.encode(row))
    rejected(lambda: subject.decode(canonical(row)))


@pytest.mark.parametrize("case", ["family","scope","version","future-kind","foreign-operation","namespace2","profile-role"],
                         ids=["family","scope","version","future-kind","foreign-operation","namespace2","profile-role"])
def test_wrong_family_version_identity_or_future_kind(case, files):
    row = json.loads(files[0])
    if case == "family":
        row["family"] = "personal_enrollment_v1"
    elif case == "scope":
        row["scope"] = "runtime_write"
    elif case == "version":
        row["format_version"] = 2
    elif case == "future-kind":
        row["kind"] = "complete"
    elif case == "foreign-operation":
        row["logical_operation_id"] = OTHER_UUID
    elif case == "namespace2":
        row["slot_identity"]["namespace_version"] = 2
    else:
        row["profiles"]["physical_profile_role"] = "canonical_four_paths_array_v1"
    rejected(lambda: subject.encode(row))
    rejected(lambda: subject.decode(canonical(row)))


def test_json_canonical_duplicate_nested_key_depth_cycle_and_subclass(files):
    rejected(lambda: subject.decode(files[0] + b" "), "JSON_NONCANONICAL")
    duplicate = files[0].replace(b'"format_version":1', b'"format_version":1,"format_version":1', 1)
    rejected(lambda: subject.decode(duplicate), "JSON_DUPLICATE_KEY")
    nested = files[0].replace(b'"domain_id":', b'"domain_id":"11111111-1111-4111-8111-111111111111","domain_id":', 1)
    rejected(lambda: subject.decode(nested), "JSON_DUPLICATE_KEY")
    rejected(lambda: subject.decode(b"[" * 17 + b"0" + b"]" * 17), "LIMIT")
    cyclic = json.loads(files[0])
    cyclic["cycle"] = cyclic
    rejected(lambda: subject.encode(cyclic))
    class ForeignDict(dict):
        pass
    rejected(lambda: subject.encode(ForeignDict(json.loads(files[0]))))
    rejected(lambda: subject.decode(b'\xff'))
    rejected(lambda: subject.decode(b'NaN'))


@pytest.mark.parametrize("hole", [0,1,6,21,23,24,25,27], ids=["header","operation","intent-receipt","genesis-receipt","prepare-reservation","prepare-payload","content-reservation","prepare-published"])
def test_prefix_holes_cannot_qualify(hole, files, materials):
    changed = list(files)
    changed[hole] = None
    rejected(lambda: subject.cross_validate_prepare_prefix(tuple(changed), **options(materials)), "PREFIX_INVALID")


@pytest.mark.parametrize("index", [0,2,3,5,6,22,23,24,25,27,28],
                         ids=["header-prepare-sha","slot-payload-sha","reservation-payload","published-slot","receipt-published","prepare-slot-envelope","prepare-reservation","prepare-payload","content-reservation","prepare-published","prepare-receipt"])
def test_file_raw_join_cannot_be_replaced(index, files, materials):
    changed = list(files)
    if index in (24, 25):
        changed[index] += b" "
    else:
        row = json.loads(files[index])
        target = {0:"prepare_payload_sha256",2:"payload_sha256",3:"payload_sha256",5:"slot_sha256",6:"published_sha256",22:"envelope_sha256",23:"envelope_sha256",27:"prepare_content_reservation_sha256",28:"published_sha256"}[index]
        row[target] = other_sha(row[target])
        changed[index] = canonical(row)
    rejected(lambda: subject.cross_validate_prepare_files(tuple(changed), **options(materials)))


def test_exact_argument_types_and_no_future_runtime_api(files, materials):
    args = options(materials)
    rejected(lambda: subject.cross_validate_prepare_files(None, **args))
    rejected(lambda: subject.cross_validate_prepare_files(list(files), **args))
    rejected(lambda: subject.cross_validate_prepare_files(files[:-1], **args))
    rejected(lambda: subject.build_prepare_files(**dict(args, originals=list(materials[0]))))
    class ForeignBytes(bytes):
        pass
    selected = list(materials[0])
    selected[0] = ForeignBytes(selected[0])
    rejected(lambda: subject.build_prepare_files(**dict(args, originals=tuple(selected))))
    rejected(lambda: subject.build_prepare_files(**dict(args, prepare_payload_raw=materials[2], prepare_content_reservation_raw=materials[1])))
    for name in ("_prepare_locked", "_consume_prepared_locked", "complete", "build_successor", "activate"):
        assert not hasattr(subject, name)


@pytest.mark.parametrize("index", [0,1,2,3,4,5,6,7,8,9,10],
                         ids=["first","second","namespace-reservation","root-seal","namespace-header","binding","prefix","intent","contract","seal","genesis"])
def test_original_individual_byte_limit_before_decode(index, materials):
    selected = list(materials[0])
    selected[index] = b"x" * (ORIGINAL_MAXIMA[index] + 1)
    rejected(lambda: subject.build_prepare_files(**dict(options(materials), originals=tuple(selected))), "LIMIT")


def test_new_wrapper_and_pure_blob_hard_limits(files, materials):
    rejected(lambda: subject.decode(b"x" * 65537), "LIMIT")
    rejected(lambda: subject.build_prepare_files(**dict(options(materials), prepare_payload_raw=b"x" * 32769)), "LIMIT")
    rejected(lambda: subject.build_prepare_files(**dict(options(materials), prepare_content_reservation_raw=b"x" * 65537)), "LIMIT")
    selected = list(files)
    selected[0] = b"x" * 16385
    rejected(lambda: subject.cross_validate_prepare_files(tuple(selected), **options(materials)), "LIMIT")


def test_all_outer_bounds_precede_any_earlier_JSON_decode(files, materials):
    selected = list(files)
    selected[0] = b"invalid JSON"
    selected[28] = "late invalid raw type"
    rejected(lambda: subject.cross_validate_prepare_files(tuple(selected), **options(materials)), "RAW_TYPE_INVALID")
    selected[28] = b"x" * 4097
    rejected(lambda: subject.cross_validate_prepare_files(tuple(selected), **options(materials)), "LIMIT")


def maximal_structural_prepare_envelope(files):
    row = json.loads(files[26])
    for entry, maximum in zip(row["refs"]["original_refs"], ORIGINAL_MAXIMA):
        entry["reference"]["raw_byte_size"] = maximum
    row["refs"]["prepare_content_reservation_ref"]["raw_byte_size"] = 65536
    row["payload_byte_size"] = 32768
    return row


def test_capacity_correction_local_structure_and_reservation64K(files, materials):
    envelope = maximal_structural_prepare_envelope(files)
    synthetic_payload = b"x" * 32768
    envelope["payload_sha256"] = sha(synthetic_payload)
    raw = canonical(envelope)
    assert len(raw) == 10798 > 8192
    assert subject.encode(envelope) == raw and subject.decode(raw) == envelope
    reservation = json.loads(files[23])
    reservation.update(envelope_raw_base64=base64.b64encode(raw).decode("ascii"),
                       envelope_byte_size=len(raw), envelope_sha256=sha(raw),
                       payload_raw_base64=base64.b64encode(synthetic_payload).decode("ascii"),
                       payload_byte_size=len(synthetic_payload), payload_sha256=sha(synthetic_payload))
    reservation_raw = canonical(reservation)
    assert len(reservation_raw) == 59055 <= 65536
    assert subject.encode(reservation) == reservation_raw and subject.decode(reservation_raw) == reservation
    replaced = list(files)
    replaced[23] = reservation_raw
    replaced[26] = raw
    rejected(lambda: subject.cross_validate_prepare_files(tuple(replaced), **options(materials)))
    # The nominal16K envelope cap is not permission for arbitrary appended
    # strings or extra refs, and the unchanged outer64K ceiling is checked first.
    padded = deepcopy(envelope)
    padded["refs"]["original_refs"].append(deepcopy(padded["refs"]["original_refs"][0]))
    assert len(canonical(padded)) < 16384
    rejected(lambda: subject.encode(padded))
    rejected(lambda: subject.decode(canonical(padded)))
    oversized = deepcopy(reservation)
    fake_envelope = b"x" * 16384
    oversized.update(envelope_raw_base64=base64.b64encode(fake_envelope).decode("ascii"),
                     envelope_byte_size=16384, envelope_sha256=sha(fake_envelope))
    assert len(canonical(oversized)) == 66503 > 65536
    rejected(lambda: subject.encode(oversized), "LIMIT")
    rejected(lambda: subject.decode(canonical(oversized)), "LIMIT")


def test_input_output_immutability_and_prepare_SHA_distinction(files, materials):
    before = tuple(sha(raw) for raw in materials[0]) + (sha(materials[1]), sha(materials[2]))
    for call in (subject.cross_validate_prepare_files, subject.cross_validate_prepare_prefix):
        call(files, **options(materials))
    assert tuple(sha(raw) for raw in materials[0]) + (sha(materials[1]), sha(materials[2])) == before
    header = json.loads(files[0])
    header["prepare_payload_sha256"] = sha(materials[2])
    poisoned = list(files)
    poisoned[0] = canonical(header)
    rejected(lambda: subject.cross_validate_prepare_files(tuple(poisoned), **options(materials)))
