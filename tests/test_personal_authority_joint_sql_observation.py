"""Full pure observation closure with independently fixed declaration oracles.

No SQL/native connection, other test/conftest import, or subject private builder.
The legacy five remain unchanged; a separately named controlled five declares
new meta texts/data. Existing dependency codecs authenticate it at test time.
Only plain QA files are written, always below the fixed RENO_QA_ROOT child.
"""

import base64
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from reno import personal_authority_enrollment_contract as enrollment
from reno import personal_authority_enrollment_successor as prepare
from reno import personal_authority_enrollment_store as store
from reno import personal_authority_joint_sql_schema as schema
from reno import personal_authority_joint_sql_observation as subject
from reno import personal_authority_namespace3_contract as namespace
from reno import personal_authority_origin_reservation_contract as origin


FIXTURE_PINS = {'legacy/origin-reservation.json': (2806, '2fa551826ebb923917cb04b8ea20a79b84dd316edcb910ea2571841b25058531'), 'legacy/origin-observation.json': (11100, '6eb52c9a8d69a0148770a76aaaa641731bd5d2bc3594f2e341ff5ae9c4bdf1f9'), 'legacy/namespace-reservation.json': (3655, '6199a570a8d316d063d6aafc5c9ef2cea6426a20066212a87bab768a791737ac'), 'legacy/namespace-root-seal.json': (918, '6215b60e393af74bb8009646ef505eba586e16cdfecf03fe5430f4c3bc874dde'), 'legacy/namespace-header.json': (750, '6c57ac2203b7fb03a1009f8f3793bed2fdfd27eb6d504c4c3b8178f4fcffb176'), 'legacy/provenance.json': (5092, '44c305ea6ccd589f55264a9b146b95f8291cced4c9782a0e6983fbe56840088b'), 'catalog/reference-a-pre.json': (713346, '0a608a60509090a0642f572c2208dde8d878aa8911343c42f281a55aacadfa72'), 'catalog/reference-a-post.json': (765559, '2c03ab6ae1bc37d653ad0de1b73dbe008a4525d7db9da6dcd1db3ab6dac5daf9'), 'catalog/reference-b-pre.json': (713346, '4e4664237c5d64a8da89efdd468f67b26adb4293ad036b2c989b14a47885f4f3'), 'catalog/reference-b-post.json': (765559, 'fbf3940ca8366bdbd549e8301e368b088bea9018ef48c487f2d5a831aec9e17a'), 'legacy-schema-descriptors.json': (55923, '28ff1f4a409cc021432b79fda5d949265847a1a1c4253cbcf632916b04ac7e1f'), 'controlled-case-01/origin-reservation.json': (2806, '2fa551826ebb923917cb04b8ea20a79b84dd316edcb910ea2571841b25058531'), 'controlled-case-01/origin-observation.json': (11100, 'e519d7b72dbf39a4ff3e8d74affeb03d196dde34315678c1f9dd21617726ab30'), 'controlled-case-01/namespace-reservation.json': (3655, 'caf0c7a77f1d4c0078f96bcc091d6fc9012491c30681444018073c8d4bc00ed3'), 'controlled-case-01/namespace-root-seal.json': (918, 'ce01419572269c9587470c2c8f1842e1ec100fde76a153f07534f4189a7c8627'), 'controlled-case-01/namespace-header.json': (750, '9f21e098578013b0edde10c3bc9a54895b3ee46f45723ca348b9f24a981d8d10'), 'controlled-case-01/constructor.json': (2602, '4a3789662ed51f93f0ee30e9ba4ff0a8982cde5f35aae2fe835e5f64c2d6d98b')}
FIVE_NAMES = ("origin-reservation.json", "origin-observation.json", "namespace-reservation.json",
              "namespace-root-seal.json", "namespace-header.json")
ORIGINAL_ROLES = ("first", "second", "namespace_reservation", "root_seal", "header", "binding", "prefix",
                  "origin_intent", "local_contract", "init_seal", "genesis_grant")
ORIGINAL_MAXIMA = (16384, 65536, 32768, 16384, 16384, 16384, 16384, 16384, 16384, 16384, 16384)
PERSONAL_TABLES = ("checklist_check", "checklist_instance", "checklist_item", "decision_card", "mutation_receipt",
                   "personal_evidence_ref", "personal_revision", "renovation_project", "source_attachment", "source_record")
OTHER_UUID = "22222222-2222-4222-8222-222222222222"
EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def altered_sha(value):
    return "f" * 64 if value != "f" * 64 else "e" * 64


def fixture_raw(relative):
    path = Path(__file__).parent / "fixtures/personal_authority_joint_sql_observation" / relative
    raw = path.read_bytes()
    assert (len(raw), sha(raw)) == FIXTURE_PINS[relative]
    return raw


def fixture_json(relative):
    return json.loads(fixture_raw(relative))


def rejected(call, suffix=None):
    with pytest.raises(subject.JointSqlObservationError) as caught:
        call()
    assert type(caught.value) is subject.JointSqlObservationError
    assert isinstance(caught.value, ValueError)
    assert caught.value.code.startswith("ENROLLMENT_SQL_SUCCESSOR_")
    assert str(caught.value) == caught.value.code
    if suffix is not None:
        assert caught.value.code == "ENROLLMENT_SQL_SUCCESSOR_" + suffix


class BytesSubclass(bytes): pass
class DictSubclass(dict): pass
class ListSubclass(list): pass
class IntSubclass(int): pass
class StrSubclass(str): pass


@pytest.mark.parametrize("kind,suffix", [("none", "RAW_TYPE_INVALID"), ("text", "RAW_TYPE_INVALID"),
    ("bytearray", "RAW_TYPE_INVALID"), ("subclass", "RAW_TYPE_INVALID"), ("empty", "LIMIT"),
    ("over-limit", "LIMIT")], ids=["none", "text", "bytearray", "subclass", "empty", "over-limit"])
def test_decode_raw_type_and_outer_bytes_before_JSON(kind, suffix):
    value = {"none": None, "text": "{}", "bytearray": bytearray(b"{}"),
             "subclass": BytesSubclass(b"{}"), "empty": b"", "over-limit": b"x" * 262145}[kind]
    rejected(lambda: subject.decode(value), suffix)


@pytest.mark.parametrize("raw,suffix", [(b"\xff", "JSON_INVALID"), (b"{", "JSON_INVALID"),
    (b'{"x":1,"x":2}', "JSON_DUPLICATE_KEY"), (b'{"x":{"n":1,"n":2}}', "JSON_DUPLICATE_KEY"),
    (b'{"x":0.0}', "JSON_INVALID"), (b'{"x":NaN}', "JSON_INVALID"), (b'{"x":Infinity}', "JSON_INVALID"),
    (b'{"x":true}', "JSON_INVALID"), (b'{"x":false}', "JSON_INVALID"),
    (b'{"x":9223372036854775808}', "INTEGER_INVALID"), (b'{"x":-9223372036854775809}', "INTEGER_INVALID"),
    (b'{"x":-0}', "JSON_NONCANONICAL"), (b'{}\n', "JSON_NONCANONICAL"), (b'{ "x":null}', "JSON_NONCANONICAL"),
    (b'{"x":null}', "FIELDS_INVALID"), (b'{"x":-9223372036854775808}', "FIELDS_INVALID"),
    (b'{"x":9223372036854775807}', "FIELDS_INVALID")],
    ids=["UTF8", "malformed", "duplicate", "nested-duplicate", "float", "NaN", "Infinity", "true", "false",
         "over64", "under64", "minus-zero", "newline", "whitespace", "closed-fields", "signed-min", "signed-max"])
def test_parser_errors_and_signed64_domain_are_independent_of_materials(raw, suffix):
    rejected(lambda: subject.decode(raw), suffix)


@pytest.mark.parametrize("depth,suffix", [(16, "FIELDS_INVALID"), (17, "LIMIT")], ids=["lexical16", "lexical17"])
def test_root_container_one_lexical_nesting(depth, suffix):
    raw = b"[" * depth + b"null" + b"]" * depth
    rejected(lambda: subject.decode(raw), suffix)


@pytest.mark.parametrize("depth,suffix", [(16, "FIELDS_INVALID"), (17, "LIMIT")], ids=["structural16", "structural17"])
def test_root_zero_structural_depth(depth, suffix):
    value = None
    for _ in range(depth - 1): value = [value]
    rejected(lambda: subject.encode({"probe": value}), suffix)


@pytest.mark.parametrize("children,suffix", [(32765, "FIELDS_INVALID"), (32766, "LIMIT")],
                         ids=["key-inclusive32768", "key-inclusive32769"])
def test_key_inclusive_nodes_are_checked_before_fields(children, suffix):
    rejected(lambda: subject.encode({"probe": [None] * children}), suffix)


def test_repeated_alias_positions_are_charged_and_not_mutated():
    leaf = {"x": None}
    value = {"probe": [leaf] * 10922}
    assert len(canonical(value)) < 262144
    rejected(lambda: subject.encode(value), "LIMIT")
    assert value["probe"][0] is value["probe"][-1] is leaf


@pytest.mark.parametrize("byte_size,suffix", [(262144, "FIELDS_INVALID"), (262145, "LIMIT")],
                         ids=["bytes262144", "bytes262145"])
def test_exact_raw_bytes_ceiling_is_not_a_claim_of_semantic_validity(byte_size, suffix):
    raw = canonical({"probe": "x" * (byte_size - 12)})
    assert len(raw) == byte_size
    rejected(lambda: subject.decode(raw), suffix)


@pytest.mark.parametrize("kind,suffix", [("dict", "JSON_INVALID"), ("list", "JSON_INVALID"),
    ("int", "JSON_INVALID"), ("str", "JSON_INVALID"), ("bool", "JSON_INVALID"),
    ("float", "JSON_INVALID"), ("cycle", "JSON_INVALID"), ("key", "FIELDS_INVALID"),
    ("surrogate", "JSON_INVALID")], ids=["dict", "list", "int", "str", "bool", "float", "cycle", "key", "surrogate"])
def test_encode_exact_types_cycles_and_UTF8_without_positive_fixture(kind, suffix):
    value = {"probe": None}
    if kind == "dict": value = DictSubclass(value)
    elif kind == "list": value["probe"] = ListSubclass([])
    elif kind == "int": value["probe"] = IntSubclass(1)
    elif kind == "str": value["probe"] = StrSubclass("x")
    elif kind == "bool": value["probe"] = True
    elif kind == "float": value["probe"] = float("nan")
    elif kind == "cycle": value["probe"] = value
    elif kind == "key": value = {1: None}
    else: value["probe"] = "\ud800"
    rejected(lambda: subject.encode(value), suffix)


@pytest.mark.parametrize("slot", [0,1,2,3,4,5,6,7,8,9,10], ids=['first', 'second', 'namespace_reservation', 'root_seal', 'header', 'binding', 'prefix', 'origin_intent', 'local_contract', 'init_seal', 'genesis_grant'])
def test_every_original_slot_limit_precedes_invalid_earlier_JSON(slot):
    originals = [b"!"] * 11
    originals[slot] = b"x" * (ORIGINAL_MAXIMA[slot] + 1)
    rejected(lambda: subject._cross_validate_candidate_content(b"!", originals=tuple(originals),
              prepare_raw=b"!", reservation_raw=b"!"), "LIMIT")


@pytest.mark.parametrize("kind", ["late-original-type", "late-reservation-type", "late-post-type",
                                   "late-prepare-limit", "late-reservation-limit", "late-post-limit"],
    ids=["late-original-type", "late-reservation-type", "late-post-type", "late-prepare-limit", "late-reservation-limit", "late-post-limit"])
def test_all_outer_types_lengths_and_pair_slots_precede_dependency_decoding(kind):
    originals, payload, reservation, post = [b"!"] * 11, b"!", b"!", b"!"
    if kind == "late-original-type": originals[-1] = "late"
    elif kind == "late-reservation-type": reservation = bytearray(b"!")
    elif kind == "late-post-type": post = BytesSubclass(b"!")
    elif kind == "late-prepare-limit": payload = b"x" * 32769
    elif kind == "late-reservation-limit": reservation = b"x" * 65537
    else: post = b"x" * 262145
    rejected(lambda: subject._cross_validate_candidate_pair(originals=tuple(originals), prepare_raw=payload,
              reservation_raw=reservation, pre_raw=b"!", post_raw=post),
              "RAW_TYPE_INVALID" if kind.endswith("type") else "LIMIT")


def test_current_pair_and_future_successor_budget_arithmetic_is_kept_separate():
    assert sum(ORIGINAL_MAXIMA) == 245760
    assert sum(ORIGINAL_MAXIMA) + 32768 + 65536 + 2 * 262144 == 868352
    assert 868352 + 32768 == 901120
    # No fabricated fully legal current pair can exceed901120 under all its caps.


@pytest.fixture(scope="module")
def materials():
    root = Path.cwd().resolve()
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve()
    assert qa.is_relative_to(root)
    out = qa / "controlled-observation-materials-01"
    out.mkdir(exist_ok=False)
    authentication = []

    def save(relative, raw):
        target = out / relative
        assert target.resolve().is_relative_to(out.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream:
            assert stream.write(raw) == len(raw)
        assert target.read_bytes() == raw
        return {"path": target.relative_to(root).as_posix(), "byte_size": len(raw), "sha256": sha(raw)}

    def raw_pin(role, raw):
        return {"role": role, "byte_size": len(raw), "sha256": sha(raw)}

    def stage(name, validator, inputs, action, output_roles=()):
        input_pins = [raw_pin(role, raw) for role, raw in inputs]
        try:
            result = action()
        except BaseException as error:
            record = {"stage": name, "validator": validator, "input_pins": input_pins,
                      "returned": False, "passed": False, "output_pins": [],
                      "exception_type": type(error).__module__ + "." + type(error).__qualname__,
                      "code": getattr(error, "code", None)}
            save("authentication/" + name + ".json", canonical(record))
            raise
        if not output_roles: outputs = ()
        elif type(result) is bytes: outputs = (result,)
        elif type(result) is tuple: outputs = result
        else: outputs = tuple(getattr(result, field) for field in
                              ("prefix_bytes", "origin_intent_bytes", "local_contract_bytes", "init_seal_bytes", "genesis_grant_bytes"))
        assert len(outputs) == len(output_roles)
        record = {"stage": name, "validator": validator, "input_pins": input_pins,
                  "returned": True, "passed": True, "result_type": type(result).__name__,
                  "output_pins": [raw_pin(role, raw) for role, raw in zip(output_roles, outputs)],
                  "exception_type": None, "code": None}
        record_pin = save("authentication/" + name + ".json", canonical(record))
        authentication.append(dict(record, record=record_pin))
        return result

    constructor = fixture_json("controlled-case-01/constructor.json")
    save("CONSTRUCTOR-01.json", canonical(constructor))
    five = tuple(fixture_raw("controlled-case-01/" + name) for name in FIVE_NAMES)
    original_pins = [dict(save("originals/" + str(index).zfill(2) + "-" + role + ".json", raw), index=index, role=role)
                     for index, (role, raw) in enumerate(zip(ORIGINAL_ROLES, five))]
    five_inputs = list(zip(ORIGINAL_ROLES[:5], five))
    stage("01-origin-authentication", "origin.cross_validate", five_inputs[:2], lambda: origin.cross_validate(*five[:2]))
    stage("02-namespace-authentication", "namespace.cross_validate_complete", five_inputs, lambda: namespace.cross_validate_complete(*five))
    binding = stage("03-binding-build", "enrollment.build_binding", five_inputs, lambda: enrollment.build_binding(*five), ("binding",))
    original_pins.append(dict(save("originals/05-binding.json", binding), index=5, role="binding"))
    content = stage("04-prefix-build", "enrollment.prepare_authority_prefix", five_inputs + [("binding", binding)],
                    lambda: enrollment.prepare_authority_prefix(binding, *five), ORIGINAL_ROLES[6:])
    originals = five + (binding, content.prefix_bytes, content.origin_intent_bytes,
                        content.local_contract_bytes, content.init_seal_bytes, content.genesis_grant_bytes)
    assert len(originals) == 11 and all(type(raw) is bytes for raw in originals)
    for index in range(6,11):
        original_pins.append(dict(save("originals/" + str(index).zfill(2) + "-" + ORIGINAL_ROLES[index] + ".json", originals[index]),
                                  index=index, role=ORIGINAL_ROLES[index]))
    original_inputs = list(zip(ORIGINAL_ROLES, originals))
    stage("05-full-prefix-authentication", "enrollment.cross_validate_authority_prefix", original_inputs,
          lambda: enrollment.cross_validate_authority_prefix(content, binding, *five))
    payload = stage("06-prepare-build", "prepare.build_prepare", original_inputs, lambda: prepare.build_prepare(originals=originals), ("prepare",))
    payload_pin = save("prepare.json", payload)
    prepare_inputs = original_inputs + [("prepare", payload)]
    reservation = stage("07-reservation-build", "prepare.build_reservation", prepare_inputs,
                        lambda: prepare.build_reservation(payload, originals=originals), ("reservation",))
    reservation_pin = save("reservation.json", reservation)
    reservation_inputs = prepare_inputs + [("reservation", reservation)]
    stage("08-prepare-authentication", "prepare.cross_validate_prepare", prepare_inputs, lambda: prepare.cross_validate_prepare(payload, originals=originals))
    stage("09-reservation-authentication", "prepare.cross_validate_reservation", reservation_inputs,
          lambda: prepare.cross_validate_reservation(reservation, payload, originals=originals))
    options = {"originals": originals, "prepare_payload_raw": payload,
               "prepare_content_reservation_raw": reservation}
    files = stage("10-pure-store-build", "store.build_prepare_files", reservation_inputs,
                  lambda: store.build_prepare_files(**options), store.FILE_NAMES)
    assert type(files) is tuple and len(files) == 29 and all(type(raw) is bytes for raw in files)
    store_pins = [dict(save("store-files/" + str(index).zfill(2) + ".json", raw), index=index, name=store.FILE_NAMES[index])
                  for index, raw in enumerate(files)]
    stage("11-pure-store-authentication", "store.cross_validate_prepare_files",
          reservation_inputs + list(zip(store.FILE_NAMES, files)), lambda: store.cross_validate_prepare_files(files, **options))
    O, T, W, R = sum(map(len, originals)), sum(map(len, originals[7:11])), sum(map(len, files)), sum(map(len, originals[5:11]))
    family_pins = {"originals": original_pins, "prepare": payload_pin, "reservation": reservation_pin, "store_files": store_pins}
    codec_authentication = {"input_pins_sha256": sha(canonical(family_pins)),
        "origin_pair": True, "namespace_complete": True, "authority_prefix": True,
        "prepare": True, "reservation": True, "store_full": True,
        "store_prefix_29": None, "store_prefix_29_executed": False,
        "store_prefix_29_scope": "No cross_validate_prepare_prefix call; only full29 cross validation is reported.",
        "scope": "Reported returned codec calls with complete raw input pins; not an independent codec execution."}
    manifest = {"schema": "reno.r901.actual-controlled-material-content-lengths.v1",
                "case_id": "controlled-observation-materials-01", "originals": original_pins,
                "prepare": payload_pin, "reservation": reservation_pin, "store_files": store_pins,
                "authentication": authentication, "codec_authentication": codec_authentication, "constructor": constructor,
                "lengths": {"O": O, "T": T, "W": W, "R": R,
                            "prepare_bytes": len(payload), "reservation_bytes": len(reservation),
                            "Lverify": 7 * W + 6 * O + T, "Lpublish": 7 * W + 6 * O + T + 8 * R},
                "store_deadline_budget_decided_by_this_test": False,
                "scope": "Authenticated controlled pure content only; no SQL/native/Store persistence/producer receipt."}
    save("MATERIALS-01.json", canonical(manifest))
    return {"originals": originals, "prepare_raw": payload, "reservation_raw": reservation,
            "store_files": files, "constructor": constructor, "save": save, "manifest": manifest}


def options(materials):
    return {key: materials[key] for key in ("originals", "prepare_raw", "reservation_raw")}


def declared_catalog(state):
    pre, post = fixture_json("catalog/reference-a-pre.json"), fixture_json("catalog/reference-a-post.json")
    base_names = {row["name"] for row in pre["main_objects"]}
    base_tables = {row["table_name"] for row in pre["table_contracts"]}
    objects = [row for row in post["main_objects"] if row["name"] not in base_names]
    contracts = [row for row in post["table_contracts"] if row["table_name"] not in base_tables]
    assert len(objects) == 10 and len(contracts) == 2
    return {"family": "personal_authority_joint_sql_catalog", "format_version": 1, "namespace_version": 3,
            "state": state, "base_objects": pre["main_objects"], "base_table_contracts": pre["table_contracts"],
            "authority_objects": [] if state == "base_only" else objects,
            "authority_table_contracts": [] if state == "base_only" else contracts,
            "temp_collisions": [], "sqlite_schema_cookie": pre["sqlite_schema_cookie"] if state == "base_only" else post["sqlite_schema_cookie"],
            "sqlite_user_version": pre["sqlite_user_version"]}


def independent_document(materials, kind, state, transition):
    originals = materials["originals"]
    first, second, header, intent, genesis = (json.loads(originals[index]) for index in (0,1,4,7,10))
    evidence = json.loads(base64.b64decode(second["origin_evidence_base64"], validate=True))
    context = {"domain_id": intent["domain_id"], "init_operation_id": intent["operation_id"], "issuer": deepcopy(intent["issuer"])}
    row = [1,3,context["domain_id"],context["issuer"]["facility_id"],context["issuer"]["node_id"],
           genesis["payload"]["epoch"],sha(originals[10]),context["init_operation_id"],sha(materials["prepare_raw"]),1]
    head = {"kind": "derived_initial_head", "binding_slot": 1, "binding_revision": 1,
            "epoch": row[5], "grant_sha256": row[6], "init_operation_id": row[7],
            "activation_prepare_sha256": row[8], "journal_sequence": 0, "previous_commit_sha256": None}
    return {"format_version": 1, "family": "personal_authority_joint_sql_observation",
        "kind": kind, "scope": "joint_sql_declared_observation_content_only", "transition": transition,
        "context": context,
        "profiles": {"authority_profile_paths_role": "canonical_four_paths_array_v1",
                     "authority_profile_paths_sha256": sha(canonical(first["profile"]["profile_paths"])),
                     "physical_profile_role": "canonical_physical_profile_dict_v1",
                     "physical_profile_sha256": sha(canonical(first["profile"]))},
        "namespace": {"family": "personal_authority_namespace3", "format_version": 1,
                      "root_name": header["root_name"], "root_native_identity": deepcopy(header["root_native_identity"]),
                      "root_path_identity": deepcopy(header["root_path_identity"])},
        "original_intent_sha256": sha(originals[7]), "activation_prepare_sha256": sha(materials["prepare_raw"]),
        "personal": {"meta_document": deepcopy(evidence["meta_document"]), "sql_document": deepcopy(evidence["sql_document"]),
                     "meta_values": deepcopy(materials["constructor"]["explicit_new_meta_texts"])},
        "source": {field: deepcopy(evidence[field]) for field in ("profile_paths", "source_fingerprint", "raw_sql_files", "node")},
        "authority": {"schema_family": "personal_authority_joint_sql", "facility_version": 2, "namespace_version": 3,
                      "journal_version": 2, "mutation_codec_version": 3, "binding_rows": [row] if state == "joint_bound" else [],
                      "journal_rows": [], "logical_head": head if state == "joint_bound" else None},
        "catalog": declared_catalog(state)}


@pytest.fixture(scope="module")
def pairs(materials):
    result = {}
    for name, state, transition in (("initial-base", "base_only", "initial_install"),
                                    ("initial-unbound", "joint_unbound", "initial_install"),
                                    ("recovery", "joint_bound", "same_binding_recovery")):
        pre = independent_document(materials, "precommit_joint_state", state, transition)
        post = independent_document(materials, "postcommit_joint_state", "joint_bound", transition)
        pre_raw, post_raw = canonical(pre), canonical(post)
        materials["save"]("observations/" + name + "-pre.json", pre_raw)
        materials["save"]("observations/" + name + "-post.json", post_raw)
        # Every field and complete SQL stays present, including a failed size gate.
        assert len(pre_raw) <= 262144 and len(post_raw) <= 262144
        assert sum(map(len, materials["originals"])) + len(materials["prepare_raw"]) + len(materials["reservation_raw"]) + len(pre_raw) + len(post_raw) <= 901120
        result[name] = (pre, post)
    return result


def one(materials, value):
    return subject._cross_validate_candidate_content(canonical(value), **options(materials))


def pair(materials, pre, post):
    return subject._cross_validate_candidate_pair(pre_raw=canonical(pre), post_raw=canonical(post), **options(materials))


def refresh_fingerprint(value, suffix):
    file = value["source"]["raw_sql_files"][suffix]
    value["source"]["source_fingerprint"]["files"][suffix] = None if file is None else {
        **{key: file["path_identity"][key] for key in ("device", "inode", "byte_size", "mtime_ns", "ctime_ns")},
        "sha256": file["sha256"]}


def change_file(value, suffix="", *, inode=None, size=None, digest=None):
    file = value["source"]["raw_sql_files"][suffix]
    if inode is not None:
        file["path_identity"]["inode"] = file["handle_identity"]["inode"] = inode
    if size is not None:
        file["byte_size"] = file["path_identity"]["byte_size"] = file["handle_identity"]["byte_size"] = size
    if digest is not None: file["sha256"] = digest
    refresh_fingerprint(value, suffix)


def suffix_file(value, suffix, size):
    file = deepcopy(value["source"]["raw_sql_files"][""])
    file["path_identity"]["inode"] = file["handle_identity"]["inode"] = "999123"
    file["byte_size"] = file["path_identity"]["byte_size"] = file["handle_identity"]["byte_size"] = size
    file["sha256"] = EMPTY_SHA if size == 0 else "a" * 64
    value["source"]["raw_sql_files"][suffix] = file
    refresh_fingerprint(value, suffix)


def test_controlled_material_dependency_authentication_saved_without_legacy_overwrite(materials):
    assert len(materials["manifest"]["originals"]) == 11
    assert len(materials["manifest"]["store_files"]) == 29
    assert len(materials["manifest"]["authentication"]) == 11
    assert all(row["returned"] is True for row in materials["manifest"]["authentication"])
    for name in FIVE_NAMES:
        assert fixture_raw("legacy/" + name) != fixture_raw("controlled-case-01/" + name) or name == "origin-reservation.json"
    selected_intent = base64.b64decode(json.loads(materials["originals"][1])["origin_intent_base64"], validate=True)
    assert selected_intent == materials["originals"][7]


@pytest.mark.parametrize("name", ['meta', 'checklist_check', 'checklist_instance', 'checklist_item', 'decision_card', 'mutation_receipt', 'personal_evidence_ref', 'personal_revision', 'renovation_project', 'source_attachment', 'source_record'], ids=['meta', 'checklist_check', 'checklist_instance', 'checklist_item', 'decision_card', 'mutation_receipt', 'personal_evidence_ref', 'personal_revision', 'renovation_project', 'source_attachment', 'source_record'])
def test_independent_fixed_legacy_descriptor_hash_and_complete_AB_named_rows(name):
    fixed = fixture_json("legacy-schema-descriptors.json")
    row = next(item for item in fixed["descriptors"] if item["table_name"] == name)
    assert sha(canonical(row["legacy_descriptor"])) == row["canonical_descriptor_sha256"]
    for label in ("a", "b"):
        catalog = fixture_json("catalog/reference-" + label + "-pre.json")
        assert row["actual_AB_table_contract"] == next(x for x in catalog["table_contracts"] if x["table_name"] == name)
        assert row["actual_AB_table_object"] == next(x for x in catalog["main_objects"] if x["type"] == "table" and x["name"] == name)
    assert fixed["older_fixture_has_proven_SQL_producer_descriptor_provenance"] is False


@pytest.mark.parametrize("name", ["initial-base", "initial-unbound", "recovery"], ids=["initial-base", "initial-unbound", "recovery"])
def test_complete_independent_three_pairs_and_codec_detachment(name, pairs, materials):
    pre, post = deepcopy(pairs[name]); before = canonical([pre, post])
    for value in (pre, post):
        expected = canonical(value)
        assert subject.encode(value) == expected
        decoded = subject.decode(expected)
        assert decoded == value and decoded is not value
        assert one(materials, value) is None
        decoded["personal"]["meta_values"]["library_id"] = OTHER_UUID
        assert canonical([pre, post]) == before
    assert pair(materials, pre, post) is None
    materials["save"]("authentication/12-" + name + "-observation-pair.json",
                      canonical({"returned": True, "scope": "candidate complete pair content only"}))


@pytest.mark.parametrize("which", ["first", "second", "namespace_reservation", "root_seal", "header", "binding", "prefix",
                                     "origin_intent", "local_contract", "init_seal", "genesis_grant"], ids=['first', 'second', 'namespace_reservation', 'root_seal', 'header', 'binding', 'prefix', 'origin_intent', 'local_contract', 'init_seal', 'genesis_grant'])
def test_each_selected_original_raw_replacement_is_rejected(which, pairs, materials):
    index = ORIGINAL_ROLES.index(which)
    changed = list(materials["originals"]); changed[index] += b"\n"
    rejected(lambda: subject._cross_validate_candidate_content(canonical(pairs["initial-base"][0]),
              **dict(options(materials), originals=tuple(changed))), "ORIGINAL_BINDING_INVALID")


@pytest.mark.parametrize("kind", ["missing", "extra", "list", "bytes-subclass", "intent-swapped", "prepare-replaced", "reservation-replaced"],
                         ids=["missing", "extra", "list", "bytes-subclass", "intent-swapped", "prepare-replaced", "reservation-replaced"])
def test_original_tuple_and_pure2_bindings(kind, pairs, materials):
    args = options(materials)
    if kind == "missing": args["originals"] = args["originals"][:-1]
    elif kind == "extra": args["originals"] += (args["originals"][0],)
    elif kind == "list": args["originals"] = list(args["originals"])
    elif kind == "bytes-subclass":
        originals = list(args["originals"]); originals[-1] = BytesSubclass(originals[-1]); args["originals"] = tuple(originals)
    elif kind == "intent-swapped":
        originals = list(args["originals"]); originals[7] = canonical(dict(json.loads(originals[7]), operation_id=OTHER_UUID)); args["originals"] = tuple(originals)
    elif kind == "prepare-replaced": args["prepare_raw"] += b"\n"
    else: args["reservation_raw"] += b"\n"
    expected = "RAW_TYPE_INVALID" if kind == "bytes-subclass" else "PREPARE_BINDING_INVALID" if kind in ("prepare-replaced", "reservation-replaced") else "ORIGINAL_BINDING_INVALID"
    rejected(lambda: subject._cross_validate_candidate_content(canonical(pairs["initial-base"][0]), **args), expected)


@pytest.mark.parametrize("location,mode", [('top', 'extra'), ('top', 'missing'), ('context', 'extra'), ('context', 'missing'), ('issuer', 'extra'), ('issuer', 'missing'), ('profiles', 'extra'), ('profiles', 'missing'), ('namespace', 'extra'), ('namespace', 'missing'), ('personal', 'extra'), ('personal', 'missing'), ('meta-document', 'extra'), ('meta-document', 'missing'), ('sql-document', 'extra'), ('sql-document', 'missing'), ('meta-summary', 'extra'), ('meta-summary', 'missing'), ('meta-values', 'extra'), ('meta-values', 'missing'), ('source', 'extra'), ('source', 'missing'), ('fingerprint', 'extra'), ('fingerprint', 'missing'), ('raw-files', 'extra'), ('raw-files', 'missing'), ('node', 'extra'), ('node', 'missing'), ('file', 'extra'), ('file', 'missing'), ('file-identity', 'extra'), ('file-identity', 'missing'), ('authority', 'extra'), ('authority', 'missing'), ('head', 'extra'), ('head', 'missing'), ('catalog', 'extra'), ('catalog', 'missing')], ids=['top-extra', 'top-missing', 'context-extra', 'context-missing', 'issuer-extra', 'issuer-missing', 'profiles-extra', 'profiles-missing', 'namespace-extra', 'namespace-missing', 'personal-extra', 'personal-missing', 'meta-document-extra', 'meta-document-missing', 'sql-document-extra', 'sql-document-missing', 'meta-summary-extra', 'meta-summary-missing', 'meta-values-extra', 'meta-values-missing', 'source-extra', 'source-missing', 'fingerprint-extra', 'fingerprint-missing', 'raw-files-extra', 'raw-files-missing', 'node-extra', 'node-missing', 'file-extra', 'file-missing', 'file-identity-extra', 'file-identity-missing', 'authority-extra', 'authority-missing', 'head-extra', 'head-missing', 'catalog-extra', 'catalog-missing'])
def test_full_observation_closed_fields(location, mode, pairs):
    value = deepcopy(pairs["recovery"][1])
    paths = {
        "top": value, "context": value["context"], "issuer": value["context"]["issuer"],
        "profiles": value["profiles"], "namespace": value["namespace"], "personal": value["personal"],
        "meta-document": value["personal"]["meta_document"], "sql-document": value["personal"]["sql_document"],
        "meta-summary": value["personal"]["meta_document"]["meta"], "meta-values": value["personal"]["meta_values"],
        "source": value["source"], "fingerprint": value["source"]["source_fingerprint"],
        "raw-files": value["source"]["raw_sql_files"], "node": value["source"]["node"],
        "file": value["source"]["raw_sql_files"][""], "file-identity": value["source"]["raw_sql_files"][""]["path_identity"],
        "authority": value["authority"], "head": value["authority"]["logical_head"], "catalog": value["catalog"],
    }
    target = paths[location]
    if mode == "extra": target["unapproved"] = None
    else: del target[next(iter(target))]
    rejected(lambda: subject.decode(canonical(value)), "FIELDS_INVALID")


@pytest.mark.parametrize("case", ["intent-sha", "prepare-reservation-sha", "domain", "native-root", "path-root", "profile-role", "physical-role", "physical-sha"],
    ids=["intent-sha", "prepare-reservation-sha", "domain", "native-root", "path-root", "profile-role", "physical-role", "physical-sha"])
def test_complete_original_context_namespace_profile_and_raw_SHA_joins(case, pairs, materials):
    value = deepcopy(pairs["initial-base"][0])
    if case == "intent-sha": value["original_intent_sha256"] = altered_sha(value["original_intent_sha256"])
    elif case == "prepare-reservation-sha": value["activation_prepare_sha256"] = sha(materials["reservation_raw"])
    elif case == "domain": value["context"]["domain_id"] = OTHER_UUID
    elif case == "native-root": value["namespace"]["root_native_identity"]["file_index"] = "999"
    elif case == "path-root": value["namespace"]["root_path_identity"]["inode"] = "999"
    elif case == "profile-role": value["profiles"]["authority_profile_paths_role"] = "canonical_physical_profile_dict_v1"
    elif case == "physical-role": value["profiles"]["physical_profile_role"] = "canonical_four_paths_array_v1"
    else: value["profiles"]["physical_profile_sha256"] = altered_sha(value["profiles"]["physical_profile_sha256"])
    rejected(lambda: one(materials, value), "ROLE_INVALID" if case.endswith("role") else "OBSERVATION_BINDING_INVALID")


@pytest.mark.parametrize("key", ["default_project_id", "library_id", "personal_primary_id", "primary_epoch"],
                         ids=["default-project", "library", "primary", "epoch"])
def test_each_typed_meta_text_is_bound_to_original_documents(key, pairs, materials):
    value = deepcopy(pairs["initial-base"][0]); value["personal"]["meta_values"][key] = "2" if key == "primary_epoch" else OTHER_UUID
    rejected(lambda: one(materials, value), "OBSERVATION_BINDING_INVALID")


@pytest.mark.parametrize("name,field", [('checklist_check', 'count'), ('checklist_check', 'sha256'), ('checklist_check', 'schema_sha256'), ('checklist_instance', 'count'), ('checklist_instance', 'sha256'), ('checklist_instance', 'schema_sha256'), ('checklist_item', 'count'), ('checklist_item', 'sha256'), ('checklist_item', 'schema_sha256'), ('decision_card', 'count'), ('decision_card', 'sha256'), ('decision_card', 'schema_sha256'), ('mutation_receipt', 'count'), ('mutation_receipt', 'sha256'), ('mutation_receipt', 'schema_sha256'), ('personal_evidence_ref', 'count'), ('personal_evidence_ref', 'sha256'), ('personal_evidence_ref', 'schema_sha256'), ('personal_revision', 'count'), ('personal_revision', 'sha256'), ('personal_revision', 'schema_sha256'), ('renovation_project', 'count'), ('renovation_project', 'sha256'), ('renovation_project', 'schema_sha256'), ('source_attachment', 'count'), ('source_attachment', 'sha256'), ('source_attachment', 'schema_sha256'), ('source_record', 'count'), ('source_record', 'sha256'), ('source_record', 'schema_sha256')], ids=['checklist_check-count', 'checklist_check-sha256', 'checklist_check-schema_sha256', 'checklist_instance-count', 'checklist_instance-sha256', 'checklist_instance-schema_sha256', 'checklist_item-count', 'checklist_item-sha256', 'checklist_item-schema_sha256', 'decision_card-count', 'decision_card-sha256', 'decision_card-schema_sha256', 'mutation_receipt-count', 'mutation_receipt-sha256', 'mutation_receipt-schema_sha256', 'personal_evidence_ref-count', 'personal_evidence_ref-sha256', 'personal_evidence_ref-schema_sha256', 'personal_revision-count', 'personal_revision-sha256', 'personal_revision-schema_sha256', 'renovation_project-count', 'renovation_project-sha256', 'renovation_project-schema_sha256', 'source_attachment-count', 'source_attachment-sha256', 'source_attachment-schema_sha256', 'source_record-count', 'source_record-sha256', 'source_record-schema_sha256'])
def test_every_personal_summary_and_legacy_schema_hash_is_retained(name, field, pairs, materials):
    value = deepcopy(pairs["initial-base"][0])
    row = value["personal"]["sql_document"]["personal_tables"][name]
    row[field] = row[field] + 1 if field == "count" else altered_sha(row[field])
    rejected(lambda: one(materials, value), "OBSERVATION_BINDING_INVALID")


@pytest.mark.parametrize("case", ["wrong-meta-sha", "wrong-meta-schema", "numeric-epoch", "zero-epoch", "padded-epoch", "over64-epoch", "count-bool"],
    ids=["wrong-meta-sha", "wrong-meta-schema", "numeric-epoch", "zero-epoch", "padded-epoch", "over64-epoch", "count-bool"])
def test_meta_digest_schema_and_exact_positive_decimal_epoch(case, pairs, materials):
    value = deepcopy(pairs["initial-base"][0])
    if case.startswith("wrong-meta"):
        field = "sha256" if case == "wrong-meta-sha" else "schema_sha256"
        for key in ("meta_document", "sql_document"):
            row = value["personal"][key]["meta"]; row[field] = altered_sha(row[field])
        suffix = "OBSERVATION_BINDING_INVALID"
    elif case == "count-bool":
        value["personal"]["sql_document"]["personal_tables"]["renovation_project"]["count"] = True
        suffix = "JSON_INVALID"
    else:
        value["personal"]["meta_values"]["primary_epoch"] = {"numeric-epoch": 1, "zero-epoch": "0", "padded-epoch": "01", "over64-epoch": "9223372036854775808"}[case]
        suffix = "INTEGER_INVALID"
    rejected(lambda: one(materials, value), suffix)


@pytest.mark.parametrize("index", [0,1,2,3,4,5,6,7,8,9], ids=["slot", "namespace", "domain", "facility", "node", "epoch", "genesis-raw", "init", "prepare-raw", "revision"])
def test_every_binding_cell_is_exact_and_not_a_subset(index, pairs, materials):
    value = deepcopy(pairs["recovery"][1]); row = value["authority"]["binding_rows"][0]
    row[index] = row[index] + 1 if index in (0,1,5,9) else altered_sha(row[index]) if index in (6,8) else OTHER_UUID
    # Keep head coherent to the changed row to test the full desired binding gate.
    head = value["authority"]["logical_head"]
    for key, slot in (("epoch",5),("grant_sha256",6),("init_operation_id",7),("activation_prepare_sha256",8)):
        head[key] = row[slot]
    rejected(lambda: one(materials, value), "INTEGER_INVALID" if index in (0,1,9) else "INITIAL_BINDING_INVALID")


@pytest.mark.parametrize("field", ["kind", "binding_slot", "binding_revision", "epoch", "grant_sha256", "init_operation_id", "activation_prepare_sha256", "journal_sequence", "previous_commit_sha256"],
    ids=["kind", "slot", "revision", "epoch", "grant", "init", "prepare", "sequence", "previous"])
def test_every_derived_head_field_is_exact(field, pairs, materials):
    value = deepcopy(pairs["recovery"][1]); head = value["authority"]["logical_head"]
    current = head[field]; head[field] = current + 1 if type(current) is int else "f" * 64 if current is None else "changed"
    rejected(lambda: one(materials, value), "INITIAL_HEAD_INVALID")


@pytest.mark.parametrize("case", ["journal-row", "journal-null", "missing-binding", "unbound-with-binding", "unbound-head", "binding-bool"],
                         ids=["journal-row", "journal-null", "missing-binding", "unbound-with-binding", "unbound-head", "binding-bool"])
def test_initial_rows_and_unbound_are_not_missing_tables(case, pairs, materials):
    value = deepcopy(pairs["initial-unbound"][0] if case in ("unbound-with-binding", "unbound-head") else pairs["recovery"][1])
    if case == "journal-row": value["authority"]["journal_rows"] = [[1]]
    elif case == "journal-null": value["authority"]["journal_rows"] = None
    elif case == "missing-binding": value["authority"]["binding_rows"] = []
    elif case == "unbound-with-binding": value["authority"]["binding_rows"] = deepcopy(pairs["recovery"][1]["authority"]["binding_rows"])
    elif case == "unbound-head": value["authority"]["logical_head"] = deepcopy(pairs["recovery"][1]["authority"]["logical_head"])
    else: value["authority"]["binding_rows"][0][0] = True
    expected = "INITIAL_JOURNAL_INVALID" if case.startswith("journal") else "INITIAL_HEAD_INVALID" if case == "unbound-head" else "JSON_INVALID" if case == "binding-bool" else "INITIAL_BINDING_INVALID"
    rejected(lambda: one(materials, value), expected)


@pytest.mark.parametrize("case", ["DB-absent", "suffix-absent-mismatch", "fingerprint-SHA", "fingerprint-path", "node-SHA", "directory", "DB-inode", "DB-device", "hot-journal", "journal-SHA", "file-size", "handle-inode"],
    ids=["DB-absent", "suffix-absent-mismatch", "fingerprint-SHA", "fingerprint-path", "node-SHA", "directory", "DB-inode", "DB-device", "hot-journal", "journal-SHA", "file-size", "handle-inode"])
def test_full_source_joins_and_historical_DB_identity_are_required(case, pairs, materials):
    value = deepcopy(pairs["recovery"][1]); source = value["source"]; file = source["raw_sql_files"][""]
    if case == "DB-absent": source["raw_sql_files"][""] = source["source_fingerprint"]["files"][""] = None
    elif case == "suffix-absent-mismatch": source["source_fingerprint"]["files"]["-wal"] = deepcopy(source["source_fingerprint"]["files"][""])
    elif case == "fingerprint-SHA": source["source_fingerprint"]["files"][""]["sha256"] = altered_sha(file["sha256"])
    elif case == "fingerprint-path": source["source_fingerprint"]["profile_identity"].reverse()
    elif case == "node-SHA": source["node"]["sha256"] = altered_sha(source["node"]["sha256"])
    elif case == "directory":
        source["node"]["directory_identity"]["inode"] = file["directory_identity"]["inode"] = "999"
    elif case == "DB-inode": change_file(value, inode="999")
    elif case == "DB-device":
        file["path_identity"]["device"] = file["handle_identity"]["device"] = "999"; refresh_fingerprint(value, "")
    elif case == "hot-journal": suffix_file(value, "-journal", 1)
    elif case == "journal-SHA": suffix_file(value, "-journal", 0); source["raw_sql_files"]["-journal"]["sha256"] = "f" * 64; refresh_fingerprint(value, "-journal")
    elif case == "file-size": file["byte_size"] += 1
    else: file["handle_identity"]["inode"] = "999"
    rejected(lambda: one(materials, value), "OBSERVATION_BINDING_INVALID")


@pytest.mark.parametrize("case", ["unchanged", "DB-size-SHA", "ctime-exemption", "wal-appearance", "wal-disappearance", "wal-recreated", "empty-journal"],
    ids=["unchanged", "DB-size-SHA", "ctime-exemption", "wal-appearance", "wal-disappearance", "wal-recreated", "empty-journal"])
def test_recovery_permits_new_physical_content_without_relaxing_old_fresh(case, pairs, materials):
    pre, post = deepcopy(pairs["recovery"])
    if case == "DB-size-SHA": change_file(pre, size=409600, digest="a" * 64); change_file(post, size=512000, digest="b" * 64)
    elif case == "ctime-exemption": post["source"]["raw_sql_files"][""]["handle_identity"]["ctime_ns"] = "123"
    elif case == "wal-appearance": suffix_file(post, "-wal", 4096)
    elif case == "wal-disappearance": suffix_file(pre, "-wal", 4096)
    elif case == "wal-recreated":
        suffix_file(pre, "-wal", 4096); suffix_file(post, "-wal", 8192); change_file(post, "-wal", inode="999456", digest="b" * 64)
    elif case == "empty-journal": suffix_file(post, "-journal", 0)
    assert pair(materials, pre, post) is None


@pytest.mark.parametrize("case", ["phase-swap", "same-pre-kind", "different-transition", "user-version", "personal", "base-SQL", "DB-replaced-both-phases", "initial-pre-changed"],
    ids=["phase-swap", "same-pre-kind", "different-transition", "user-version", "personal", "base-SQL", "DB-replaced-both-phases", "initial-pre-changed"])
def test_full_pair_transition_and_original_anchors(case, pairs, materials):
    pre, post = deepcopy(pairs["recovery"] if case not in ("different-transition", "initial-pre-changed") else pairs["initial-base"])
    if case == "phase-swap": pre, post = post, pre
    elif case == "same-pre-kind": post["kind"] = "precommit_joint_state"
    elif case == "different-transition": post["transition"] = "same_binding_recovery"
    elif case == "user-version": post["catalog"]["sqlite_user_version"] = 17
    elif case == "personal": post["personal"]["meta_values"]["library_id"] = OTHER_UUID
    elif case == "base-SQL": post["catalog"]["base_objects"][0]["sql"] += "\n"
    elif case == "DB-replaced-both-phases": change_file(pre, inode="999"); change_file(post, inode="999")
    else: change_file(pre, size=409600, digest="a" * 64)
    suffix = "CATALOG_INVALID" if case == "base-SQL" else "OBSERVATION_BINDING_INVALID" if case in ("personal", "DB-replaced-both-phases", "initial-pre-changed") else "TRANSITION_INVALID"
    rejected(lambda: pair(materials, pre, post), suffix)


def test_cookie_can_differ_without_being_schema_version_or_publication(pairs, materials):
    pre, post = deepcopy(pairs["initial-base"])
    pre["catalog"]["sqlite_schema_cookie"] = 0; post["catalog"]["sqlite_schema_cookie"] = 2147483647
    assert pair(materials, pre, post) is None


@pytest.mark.parametrize("cid,suffix", [(-2, "CATALOG_INVALID"), (-3, "INTEGER_INVALID")], ids=["expression-minus2", "minus3"])
def test_signed_metadata_domain_is_kept_without_candidate_normalization(cid, suffix, pairs, materials):
    value = deepcopy(pairs["recovery"][1])
    table = next(row for row in value["catalog"]["authority_table_contracts"] if row["table_name"] == "personal_authority_mutation_commit")
    table["indexes"][0]["parts"][-1]["cid"] = cid
    if cid == -2: assert subject.decode(canonical(value)) == value
    rejected(lambda: one(materials, value), suffix)


def test_complete_valid_public_content_reaches_real_empty_publication_gate(pairs, materials):
    value = pairs["recovery"][1]
    assert one(materials, value) is None
    with pytest.raises(schema.JointSqlSchemaError) as caught:
        subject.cross_validate(canonical(value), **options(materials))
    assert caught.value.code == "JOINT_SQL_SCHEMA_REGISTRY_UNAVAILABLE"
    changed = deepcopy(value); changed["personal"]["meta_values"]["library_id"] = OTHER_UUID
    rejected(lambda: subject.cross_validate(canonical(changed), **options(materials)), "OBSERVATION_BINDING_INVALID")


def test_API_scope_runtime_absence_and_input_immutability(pairs, materials):
    for name in ("_observe_locked", "_classify_locked", "_install_empty_locked"):
        assert not hasattr(subject, name) and name not in subject.__all__
    expected = {"encode": ["value"], "decode": ["raw"],
        "_cross_validate_candidate_content": ["raw", "originals", "prepare_raw", "reservation_raw"],
        "_cross_validate_candidate_pair": ["originals", "prepare_raw", "reservation_raw", "pre_raw", "post_raw"],
        "cross_validate": ["raw", "originals", "prepare_raw", "reservation_raw"]}
    for name, arguments in expected.items():
        params = inspect.signature(getattr(subject, name)).parameters
        assert list(params) == arguments
        assert not any(p.kind in (inspect.Parameter.VAR_POSITIONAL, inspect.Parameter.VAR_KEYWORD) for p in params.values())
    before = tuple((len(raw),sha(raw)) for raw in materials["originals"]) + ((len(materials["prepare_raw"]),sha(materials["prepare_raw"])), (len(materials["reservation_raw"]),sha(materials["reservation_raw"])))
    assert pair(materials, *pairs["initial-base"]) is None
    after = tuple((len(raw),sha(raw)) for raw in materials["originals"]) + ((len(materials["prepare_raw"]),sha(materials["prepare_raw"])), (len(materials["reservation_raw"]),sha(materials["reservation_raw"])))
    assert before == after
