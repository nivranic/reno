"""Independent complete successor content oracles over19 full declared raws.

No SQL/native, other test/conftest import, new subject private expected builder,
or claimed COMMIT. Prior controlled materials are authenticated again here.
All future generated raw/receipts stay under the fixed RENO_QA_ROOT child.
"""
import ast
import base64
from copy import deepcopy
import hashlib
import inspect
import json
import os
from pathlib import Path

import pytest

from reno import personal_authority_enrollment_successor as prepare
from reno import personal_authority_enrollment_successor_content as subject
from reno import personal_authority_joint_sql_observation as observation
from reno import personal_authority_joint_sql_schema as schema


FIXTURE_PINS = {'originals/00-first.json': (2806, '2fa551826ebb923917cb04b8ea20a79b84dd316edcb910ea2571841b25058531'), 'originals/01-second.json': (11100, 'e519d7b72dbf39a4ff3e8d74affeb03d196dde34315678c1f9dd21617726ab30'), 'originals/02-namespace_reservation.json': (3655, 'caf0c7a77f1d4c0078f96bcc091d6fc9012491c30681444018073c8d4bc00ed3'), 'originals/03-root_seal.json': (918, 'ce01419572269c9587470c2c8f1842e1ec100fde76a153f07534f4189a7c8627'), 'originals/04-header.json': (750, '9f21e098578013b0edde10c3bc9a54895b3ee46f45723ca348b9f24a981d8d10'), 'originals/05-binding.json': (2033, 'd5c89545f62639086b2950a410a100bae14e625ed7753d6c0e1bf280bc30dd0c'), 'originals/06-prefix.json': (904, '084e57440c5cf0a1f5fe16dfa6fac3b92339143d2c651d8bacd06050d4adcf7f'), 'originals/07-origin_intent.json': (643, 'c9c61fe17ced786f392b393ba82390c961cfac365e126bbca580b8ef61a97004'), 'originals/08-local_contract.json': (467, '8a967587f582102dafce950497e555d48b6fc8dbc26efa6d1b0a81ae353b4fbf'), 'originals/09-init_seal.json': (532, 'a95915db163a43a21bedc19bfeac850de2657768d7b63067e0f0560ac874bd11'), 'originals/10-genesis_grant.json': (639, '90ef9f71f94d4f081c6fb005a90e93fa9f613f7db5b26f7851426103fb82bdaf'), 'prepare.json': (7795, '17ae80d9a041fbdf9f8277a88fc777cdcff77b00b8130305abe7831287d27d18'), 'reservation.json': (11355, '1137cbf855cd0c52502cf6f1bfee7f91def5fc018ce9d2c558528c695aee4b2e'), 'observations/initial-base-pre.json': (223331, '08f76c9a71f4cd3a13aae28d555a7328a90c96b087fa249a453e3629de33f01e'), 'observations/initial-base-post.json': (240843, 'e0a01022f854d594b72f7dfbea84afb2eed024e4bd803b0a26922b92f50b77cf'), 'observations/initial-unbound-pre.json': (240183, '70a41c18f9a2ef1a282ba6033d319c4569540accd234947e823d0953df8848cf'), 'observations/initial-unbound-post.json': (240843, 'e0a01022f854d594b72f7dfbea84afb2eed024e4bd803b0a26922b92f50b77cf'), 'observations/recovery-pre.json': (240848, '86739891ec95a70e67c05f841a23af13b3c5def55e1b1cada29b5040641c4cd8'), 'observations/recovery-post.json': (240849, 'ef40673691e3fb6aa86dde8690a7ad0a9851765ee09a467c745ebde29626827b'), 'provenance.json': (16729, '011a1644d20b246d116544c942b65a5117eae95cd10ce6eeb2817397f9e934af')}
ORIGINAL_ROLES = ("first", "second", "namespace_reservation", "root_seal", "header", "binding", "prefix",
                  "origin_intent", "local_contract", "init_seal", "genesis_grant")
ORIGINAL_MAXIMA = (16384,65536,32768,16384,16384,16384,16384,16384,16384,16384,16384)
ALL_SLOT_ROLES = ORIGINAL_ROLES + ("prepare", "reservation", "precommit", "postcommit", "successor")
ALL_SLOT_MAXIMA = ORIGINAL_MAXIMA + (32768,65536,262144,262144,32768)
REFERENCE_SPECS = (('first', 'original_origin_reservation', 'personal_authority_origin_reservation', 'origin_reservation', 1), ('second', 'original_origin_observation', 'personal_authority_origin_reservation', 'origin_observation', 1), ('namespace_reservation', 'original_namespace3_reservation', 'personal_authority_namespace3', 'namespace_reservation', 1), ('root_seal', 'original_namespace3_root_seal', 'personal_authority_namespace3', 'namespace_root_seal', 1), ('header', 'original_namespace3_header', 'personal_authority_namespace3', 'namespace_header', 1), ('binding', 'original_enrollment_binding', 'personal_authority_enrollment_binding', 'enrollment_binding', 1), ('prefix', 'original_authority_prefix', 'personal_authority_enrollment_binding', 'authority_prefix', 1), ('origin_intent', 'original_sealed_origin_intent', 'authority_format1_no_family_envelope', 'origin_init_intent', 1), ('local_contract', 'original_local_contract', 'authority_format1_no_family_envelope', 'local_contract', 1), ('init_seal', 'original_init_seal', 'authority_format1_no_family_envelope', 'init_seal', 1), ('genesis_grant', 'original_genesis_grant', 'authority_format1_no_family_envelope', 'genesis_grant', 1), ('activation_prepare_payload', 'original_sql_registration_prepare_payload', 'personal_authority_enrollment_sql_prepare', 'sql_registration_prepare', 1), ('activation_prepare_reservation', 'original_sql_registration_prepare_reservation', 'personal_authority_enrollment_sql_prepare', 'sql_registration_prepare_reservation', 1), ('precommit_observation', 'declared_precommit_joint_observation', 'personal_authority_joint_sql_observation', 'precommit_joint_state', 1), ('postcommit_observation', 'declared_postcommit_joint_observation', 'personal_authority_joint_sql_observation', 'postcommit_joint_state', 1))
OTHER_UUID = "22222222-2222-4222-8222-222222222222"


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def wrong_sha(value):
    return "f" * 64 if value != "f" * 64 else "e" * 64


def fixture_raw(relative):
    raw = (Path(__file__).parent / "fixtures/personal_authority_enrollment_successor_content" / relative).read_bytes()
    assert (len(raw), sha(raw)) == FIXTURE_PINS[relative]
    return raw


def rejected(action, suffix=None):
    with pytest.raises(subject.EnrollmentSqlSuccessorContentError) as caught:
        action()
    assert type(caught.value) is subject.EnrollmentSqlSuccessorContentError
    assert caught.value.code.startswith("ENROLLMENT_SQL_SUCCESSOR_")
    assert str(caught.value) == caught.value.code
    if suffix is not None:
        assert caught.value.code == "ENROLLMENT_SQL_SUCCESSOR_" + suffix


class BytesSubclass(bytes): pass
class DictSubclass(dict): pass
class ListSubclass(list): pass
class IntSubclass(int): pass
class StrSubclass(str): pass


@pytest.mark.parametrize("kind,suffix", [("none","RAW_TYPE_INVALID"),("text","RAW_TYPE_INVALID"),
    ("bytearray","RAW_TYPE_INVALID"),("subclass","RAW_TYPE_INVALID"),("empty","LIMIT"),("over-limit","LIMIT")],
    ids=["none","text","bytearray","subclass","empty","over-limit"])
def test_decode_exact_raw_types_and_outer_limit(kind, suffix):
    value = {"none":None,"text":"{}","bytearray":bytearray(b"{}"),"subclass":BytesSubclass(b"{}"),
             "empty":b"","over-limit":b"x" * 32769}[kind]
    rejected(lambda: subject.decode(value), suffix)


@pytest.mark.parametrize("raw,suffix", [(b"\xff","JSON_INVALID"),(b"{","JSON_INVALID"),
    (b'{"x":1,"x":2}',"JSON_DUPLICATE_KEY"),(b'{"x":{"n":1,"n":2}}',"JSON_DUPLICATE_KEY"),
    (b'{"x":0.0}',"JSON_INVALID"),(b'{"x":NaN}',"JSON_INVALID"),(b'{"x":Infinity}',"JSON_INVALID"),
    (b'{"x":true}',"JSON_INVALID"),(b'{"x":false}',"JSON_INVALID"),
    (b'{"x":9223372036854775808}',"INTEGER_INVALID"),(b'{"x":-9223372036854775809}',"INTEGER_INVALID"),
    (b'{"x":-0}',"JSON_NONCANONICAL"),(b'{}\n',"JSON_NONCANONICAL"),(b'{ "x":null}',"JSON_NONCANONICAL"),
    (b'{"x":-9223372036854775808}',"FIELDS_INVALID"),(b'{"x":9223372036854775807}',"FIELDS_INVALID")],
    ids=["UTF8","malformed","duplicate","nested-duplicate","float","NaN","Infinity","true","false",
         "over64","under64","minus-zero","newline","whitespace","signed-min","signed-max"])
def test_parser_canonical_and_signed64_domain_before_family_fields(raw, suffix):
    rejected(lambda: subject.decode(raw), suffix)


@pytest.mark.parametrize("depth,suffix", [(16,"FIELDS_INVALID"),(17,"LIMIT")], ids=["lexical16","lexical17"])
def test_root_container_one_lexical_depth(depth, suffix):
    rejected(lambda: subject.decode(b"[" * depth + b"null" + b"]" * depth), suffix)


@pytest.mark.parametrize("depth,suffix", [(16,"FIELDS_INVALID"),(17,"LIMIT")], ids=["structural16","structural17"])
def test_root_zero_structural_depth(depth, suffix):
    value = None
    for _ in range(depth - 1): value = [value]
    rejected(lambda: subject.encode({"probe":value}), suffix)


@pytest.mark.parametrize("children,suffix", [(4093,"FIELDS_INVALID"),(4094,"LIMIT")], ids=["nodes4096","nodes4097"])
def test_key_value_container_node_ceiling(children, suffix):
    rejected(lambda: subject.encode({"probe":[None] * children}), suffix)


def test_repeated_alias_occurrences_charge_without_mutating_inputs():
    leaf = {"x":None}; value = {"probe":[leaf] * 1365}
    assert len(canonical(value)) < 32768
    rejected(lambda: subject.encode(value), "LIMIT")
    assert value["probe"][0] is value["probe"][-1] is leaf


@pytest.mark.parametrize("size,suffix", [(32768,"FIELDS_INVALID"),(32769,"LIMIT")], ids=["bytes32768","bytes32769"])
def test_exact_32KiB_bytes_boundary_is_parser_not_semantic_fixture(size, suffix):
    raw = canonical({"probe":"x" * (size - 12)})
    assert len(raw) == size
    rejected(lambda: subject.decode(raw), suffix)


@pytest.mark.parametrize("kind,suffix", [("dict","JSON_INVALID"),("list","JSON_INVALID"),("int","JSON_INVALID"),
    ("str","JSON_INVALID"),("bool","JSON_INVALID"),("float","JSON_INVALID"),("cycle","JSON_INVALID"),
    ("key","FIELDS_INVALID"),("key-subclass","FIELDS_INVALID"),("surrogate","JSON_INVALID")],
    ids=["dict","list","int","str","bool","float","cycle","key","key-subclass","surrogate"])
def test_encode_exact_types_UTF8_and_cycles_without_materials(kind, suffix):
    value = {"probe":None}
    if kind == "dict": value = DictSubclass(value)
    elif kind == "list": value["probe"] = ListSubclass([])
    elif kind == "int": value["probe"] = IntSubclass(1)
    elif kind == "str": value["probe"] = StrSubclass("x")
    elif kind == "bool": value["probe"] = True
    elif kind == "float": value["probe"] = float("nan")
    elif kind == "cycle": value["probe"] = value
    elif kind == "key": value = {1:None}
    elif kind == "key-subclass": value = {StrSubclass("x"):None}
    else: value["probe"] = "\ud800"
    rejected(lambda: subject.encode(value), suffix)


def outer_slots(slot, raw):
    all_raws = [b"!"] * 16; all_raws[slot] = raw
    return all_raws[-1], {"originals":tuple(all_raws[:11]),"prepare_payload_raw":all_raws[11],
        "prepare_reservation_raw":all_raws[12],"precommit_raw":all_raws[13],"postcommit_raw":all_raws[14]}


@pytest.mark.parametrize("slot", [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], ids=['first', 'second', 'namespace_reservation', 'root_seal', 'header', 'binding', 'prefix', 'origin_intent', 'local_contract', 'init_seal', 'genesis_grant', 'prepare', 'reservation', 'precommit', 'postcommit', 'successor'])
def test_every_selected_outer_slot_type_precedes_earlier_invalid_JSON(slot):
    successor_raw, args = outer_slots(slot, BytesSubclass(b"!"))
    rejected(lambda: subject._cross_validate_candidate(successor_raw, **args), "RAW_TYPE_INVALID")


@pytest.mark.parametrize("slot", [0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15], ids=['first', 'second', 'namespace_reservation', 'root_seal', 'header', 'binding', 'prefix', 'origin_intent', 'local_contract', 'init_seal', 'genesis_grant', 'prepare', 'reservation', 'precommit', 'postcommit', 'successor'])
def test_every_selected_outer_slot_limit_precedes_earlier_invalid_JSON(slot):
    successor_raw, args = outer_slots(slot, b"x" * (ALL_SLOT_MAXIMA[slot] + 1))
    rejected(lambda: subject._cross_validate_candidate(successor_raw, **args), "LIMIT")


@pytest.mark.parametrize("kind", ["missing","extra","list"], ids=["missing","extra","list"])
def test_original_tuple_is_exact11_before_decode(kind):
    successor_raw, args = outer_slots(15, b"!")
    args["originals"] = args["originals"][:-1] if kind == "missing" else args["originals"] + (b"!",) if kind == "extra" else list(args["originals"])
    rejected(lambda: subject._cross_validate_candidate(successor_raw, **args), "ORIGINAL_BINDING_INVALID")


def test_full16slots_and_future_output_aggregate_arithmetic():
    assert sum(ORIGINAL_MAXIMA) == 245760
    assert sum(ALL_SLOT_MAXIMA[:-1]) == 868352
    assert len(ALL_SLOT_MAXIMA) == 16 and sum(ALL_SLOT_MAXIMA) == 901120
    # Per-slot ceilings make an over901120 legal positive fixture impossible.


def independent_successor(originals, prepare_raw, reservation_raw, pre_raw, post_raw):
    first, second, header, intent, genesis = (json.loads(originals[i]) for i in (0,1,4,7,10))
    assert base64.b64decode(second["origin_intent_base64"], validate=True) == originals[7]
    pre, post, prepared = json.loads(pre_raw), json.loads(post_raw), json.loads(prepare_raw)
    context = {"domain_id":intent["domain_id"],"init_operation_id":intent["operation_id"],"issuer":deepcopy(intent["issuer"])}
    profiles = {"authority_profile_paths_role":"canonical_four_paths_array_v1",
        "authority_profile_paths_sha256":sha(canonical(first["profile"]["profile_paths"])),
        "physical_profile_role":"canonical_physical_profile_dict_v1","physical_profile_sha256":sha(canonical(first["profile"]))}
    namespace = {"family":"personal_authority_namespace3","format_version":1,"root_name":header["root_name"],
                 "root_native_identity":deepcopy(header["root_native_identity"]),"root_path_identity":deepcopy(header["root_path_identity"])}
    assert pre["context"] == post["context"] == prepared["context"] == context
    assert pre["profiles"] == post["profiles"] == prepared["profiles"] == profiles
    assert pre["namespace"] == post["namespace"] == prepared["namespace"] == namespace
    selected = originals + (prepare_raw,reservation_raw,pre_raw,post_raw)
    refs = {key:{"role":role,"codec":codec,"record_kind":kind,"format_version":version,
                 "raw_byte_size":len(raw),"raw_sha256":sha(raw),"identity":deepcopy(context)}
            for raw, (key,role,codec,kind,version) in zip(selected, REFERENCE_SPECS)}
    binding = [1,3,context["domain_id"],context["issuer"]["facility_id"],context["issuer"]["node_id"],
               genesis["payload"]["epoch"],sha(originals[10]),context["init_operation_id"],sha(prepare_raw),1]
    head = {"kind":"derived_initial_head","binding_slot":1,"binding_revision":1,"epoch":binding[5],
            "grant_sha256":binding[6],"init_operation_id":binding[7],"activation_prepare_sha256":binding[8],
            "journal_sequence":0,"previous_commit_sha256":None}
    assert post["authority"]["binding_rows"] == [binding]
    assert post["authority"]["journal_rows"] == [] and post["authority"]["logical_head"] == head
    unchanged = {"personal_meta_sha256":intent["payload"]["observed_personal_meta_sha256"],
                 "personal_sql_sha256":intent["payload"]["observed_personal_sql_sha256"]}
    for value in (pre,post):
        assert sha(canonical(value["personal"]["meta_document"])) == unchanged["personal_meta_sha256"]
        assert sha(canonical(value["personal"]["sql_document"])) == unchanged["personal_sql_sha256"]
    return {"format_version":1,"family":"personal_authority_enrollment_sql_successor","kind":"origin_sql_successor",
        "scope":"committed_sql_content_only","transition":post["transition"],"context":context,"profiles":profiles,
        "namespace":namespace,"refs":refs,"initial_state":{"binding":binding,"journal_rows":0,"logical_head":head},
        "unchanged_personal":unchanged}


@pytest.fixture(scope="module")
def materials():
    root = Path.cwd().resolve(); qa = Path(os.environ["RENO_QA_ROOT"]).resolve()
    assert qa.is_relative_to(root)
    out = qa / "controlled-successor-content-01"; out.mkdir(exist_ok=False)
    saved = []; authentication = []

    def save(relative, raw):
        target = out / relative; assert target.resolve().is_relative_to(out.resolve())
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("xb") as stream: assert stream.write(raw) == len(raw)
        assert target.read_bytes() == raw
        pin = {"path":target.relative_to(root).as_posix(),"byte_size":len(raw),"sha256":sha(raw)}
        saved.append(pin); return pin

    def stage(name, validator, inputs, action):
        input_pins = [{"role":role,"byte_size":len(raw),"sha256":sha(raw)} for role,raw in inputs]
        try:
            result = action()
        except BaseException as error:
            record = {"stage":name,"validator":validator,"input_pins":input_pins,"returned":False,
                      "exception_type":type(error).__module__ + "." + type(error).__qualname__,"code":getattr(error,"code",None)}
            save("authentication/" + name + ".json", canonical(record)); raise
        record = {"stage":name,"validator":validator,"input_pins":input_pins,"returned":True,
                  "result_type":type(result).__name__,"exception_type":None,"code":None}
        receipt = save("authentication/" + name + ".json",canonical(record))
        authentication.append(dict(record,record=receipt)); return result

    provenance_raw = fixture_raw("provenance.json"); save("provenance.json",provenance_raw)
    originals = tuple(fixture_raw("originals/" + str(i).zfill(2) + "-" + role + ".json") for i,role in enumerate(ORIGINAL_ROLES))
    original_inputs = list(zip(ORIGINAL_ROLES, originals))
    for i,(role,raw) in enumerate(original_inputs): save("originals/" + str(i).zfill(2) + "-" + role + ".json",raw)
    prepare_raw, reservation_raw = fixture_raw("prepare.json"), fixture_raw("reservation.json")
    save("prepare.json",prepare_raw);save("reservation.json",reservation_raw)
    pair_raws = {}
    for name in ("initial-base","initial-unbound","recovery"):
        pre = fixture_raw("observations/" + name + "-pre.json"); post = fixture_raw("observations/" + name + "-post.json")
        save("observations/" + name + "-pre.json",pre);save("observations/" + name + "-post.json",post)
        pair_raws[name] = (pre,post)
    stage("01-prepare-authentication","prepare.cross_validate_prepare",original_inputs + [("prepare",prepare_raw)],
          lambda: prepare.cross_validate_prepare(prepare_raw, originals=originals))
    stage("02-reservation-authentication","prepare.cross_validate_reservation",original_inputs + [("prepare",prepare_raw),("reservation",reservation_raw)],
          lambda: prepare.cross_validate_reservation(reservation_raw,prepare_raw,originals=originals))
    cases = {}
    for index,(name,(pre,post)) in enumerate(pair_raws.items(),3):
        args = {"originals":originals,"prepare_payload_raw":prepare_raw,"prepare_reservation_raw":reservation_raw,
                "precommit_raw":pre,"postcommit_raw":post}
        stage(str(index).zfill(2) + "-" + name + "-pair","observation._cross_validate_candidate_pair",
              original_inputs + [("prepare",prepare_raw),("reservation",reservation_raw),("precommit",pre),("postcommit",post)],
              lambda pre=pre,post=post: observation._cross_validate_candidate_pair(originals=originals,prepare_raw=prepare_raw,
                        reservation_raw=reservation_raw,pre_raw=pre,post_raw=post))
        value = independent_successor(originals,prepare_raw,reservation_raw,pre,post);raw = canonical(value)
        saved_raw = save("expected/" + name + "-successor.json",raw)
        assert len(raw) <= 32768 and sum(map(len,originals)) + len(prepare_raw) + len(reservation_raw) + len(pre) + len(post) + len(raw) <= 901120
        cases[name] = {"args":args,"value":value,"raw":raw,"expected_pin":saved_raw}
    manifest = {"schema":"reno.r905.actual-independent-successor-test-materials.v1","full_selected_raw_count":19,
        "authentication":authentication,"saved_full_raw_pins":saved.copy(),
        "expected_successor_pins":[row["expected_pin"] for row in cases.values()],
        "scope":"Same19 controlled declared raws authenticated again; independent expected values. No SQL COMMIT/native/producer or runtime qualification."}
    save("MATERIALS-01.json",canonical(manifest))
    return {"cases":cases,"originals":originals,"prepare_raw":prepare_raw,"reservation_raw":reservation_raw,
            "manifest":manifest,"save":save,"provenance":json.loads(provenance_raw)}


def candidate(materials, value, name="initial-base"):
    return subject._cross_validate_candidate(canonical(value), **materials["cases"][name]["args"])


@pytest.mark.parametrize("name", ["initial-base","initial-unbound","recovery"], ids=["initial-base","initial-unbound","recovery"])
def test_complete_three_independent_expected_successors_and_codec_detachment(name, materials):
    case = materials["cases"][name]; before = canonical(case["value"])
    actual = subject._build_candidate(**case["args"])
    materials["save"]("actual/" + name + "-successor.json",actual)
    assert actual == case["raw"] == subject.encode(case["value"])
    decoded = subject.decode(actual)
    assert decoded == case["value"] and decoded is not case["value"]
    assert subject._cross_validate_candidate(actual, **case["args"]) is None
    decoded["refs"]["first"]["identity"]["issuer"]["node_id"] = OTHER_UUID
    assert canonical(case["value"]) == before
    assert all(row["returned"] is True for row in materials["manifest"]["authentication"])


@pytest.mark.parametrize("key,suffix", [('first', 'ORIGINAL_BINDING_INVALID'), ('second', 'ORIGINAL_BINDING_INVALID'), ('namespace_reservation', 'ORIGINAL_BINDING_INVALID'), ('root_seal', 'ORIGINAL_BINDING_INVALID'), ('header', 'ORIGINAL_BINDING_INVALID'), ('binding', 'ORIGINAL_BINDING_INVALID'), ('prefix', 'ORIGINAL_BINDING_INVALID'), ('origin_intent', 'ORIGINAL_BINDING_INVALID'), ('local_contract', 'ORIGINAL_BINDING_INVALID'), ('init_seal', 'ORIGINAL_BINDING_INVALID'), ('genesis_grant', 'ORIGINAL_BINDING_INVALID'), ('activation_prepare_payload', 'PREPARE_BINDING_INVALID'), ('activation_prepare_reservation', 'PREPARE_BINDING_INVALID'), ('precommit_observation', 'OBSERVATION_BINDING_INVALID'), ('postcommit_observation', 'OBSERVATION_BINDING_INVALID')], ids=['first', 'second', 'namespace_reservation', 'root_seal', 'header', 'binding', 'prefix', 'origin_intent', 'local_contract', 'init_seal', 'genesis_grant', 'activation_prepare_payload', 'activation_prepare_reservation', 'precommit_observation', 'postcommit_observation'])
def test_each_of15_selected_reference_hashes_is_recomputed(key, suffix, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"])
    value["refs"][key]["raw_sha256"] = wrong_sha(value["refs"][key]["raw_sha256"])
    if key in ("genesis_grant","activation_prepare_payload"):
        slot,field = (6,"grant_sha256") if key == "genesis_grant" else (8,"activation_prepare_sha256")
        value["initial_state"]["binding"][slot] = value["initial_state"]["logical_head"][field] = value["refs"][key]["raw_sha256"]
    rejected(lambda: candidate(materials,value), suffix)


@pytest.mark.parametrize("index", [0,1,2,3,4,5,6,7,8,9,10], ids=['first', 'second', 'namespace_reservation', 'root_seal', 'header', 'binding', 'prefix', 'origin_intent', 'local_contract', 'init_seal', 'genesis_grant'])
def test_each_supplied_original_raw_is_authenticated_verbatim(index, materials):
    case = materials["cases"]["initial-base"]; originals = list(case["args"]["originals"]); originals[index] += b"\n"
    rejected(lambda: subject._cross_validate_candidate(case["raw"],**dict(case["args"],originals=tuple(originals))),"ORIGINAL_BINDING_INVALID")


@pytest.mark.parametrize("key,suffix", [("prepare_payload_raw","PREPARE_BINDING_INVALID"),("prepare_reservation_raw","PREPARE_BINDING_INVALID"),
    ("precommit_raw","JSON_NONCANONICAL"),("postcommit_raw","JSON_NONCANONICAL")],
    ids=["prepare","reservation","precommit","postcommit"])
def test_each_pure2_and_observation_selected_raw_is_authenticated(key, suffix, materials):
    case = materials["cases"]["initial-base"]
    rejected(lambda: subject._cross_validate_candidate(case["raw"],**dict(case["args"],**{key:case["args"][key]+b"\n"})),suffix)


@pytest.mark.parametrize("location,mode", [('top', 'extra'), ('top', 'missing'), ('context', 'extra'), ('context', 'missing'), ('issuer', 'extra'), ('issuer', 'missing'), ('profiles', 'extra'), ('profiles', 'missing'), ('namespace', 'extra'), ('namespace', 'missing'), ('native-root', 'extra'), ('native-root', 'missing'), ('path-root', 'extra'), ('path-root', 'missing'), ('refs', 'extra'), ('refs', 'missing'), ('first-ref', 'extra'), ('first-ref', 'missing'), ('ref-identity', 'extra'), ('ref-identity', 'missing'), ('ref-issuer', 'extra'), ('ref-issuer', 'missing'), ('initial-state', 'extra'), ('initial-state', 'missing'), ('head', 'extra'), ('head', 'missing'), ('unchanged-personal', 'extra'), ('unchanged-personal', 'missing')], ids=['top-extra', 'top-missing', 'context-extra', 'context-missing', 'issuer-extra', 'issuer-missing', 'profiles-extra', 'profiles-missing', 'namespace-extra', 'namespace-missing', 'native-root-extra', 'native-root-missing', 'path-root-extra', 'path-root-missing', 'refs-extra', 'refs-missing', 'first-ref-extra', 'first-ref-missing', 'ref-identity-extra', 'ref-identity-missing', 'ref-issuer-extra', 'ref-issuer-missing', 'initial-state-extra', 'initial-state-missing', 'head-extra', 'head-missing', 'unchanged-personal-extra', 'unchanged-personal-missing'])
def test_complete_successor_and_nested_closed_fields(location, mode, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"]); first = value["refs"]["first"]
    paths = {"top":value,"context":value["context"],"issuer":value["context"]["issuer"],"profiles":value["profiles"],
        "namespace":value["namespace"],"native-root":value["namespace"]["root_native_identity"],"path-root":value["namespace"]["root_path_identity"],
        "refs":value["refs"],"first-ref":first,"ref-identity":first["identity"],"ref-issuer":first["identity"]["issuer"],
        "initial-state":value["initial_state"],"head":value["initial_state"]["logical_head"],"unchanged-personal":value["unchanged_personal"]}
    target = paths[location]
    if mode == "extra": target["unapproved"] = None
    else: del target[next(iter(target))]
    rejected(lambda: subject.decode(canonical(value)),"OBSERVATION_BINDING_INVALID" if location in ("native-root","path-root") else "FIELDS_INVALID")


@pytest.mark.parametrize("field", ["role","codec","record_kind","format_version","raw_byte_size","raw_sha256","identity"],
    ids=["role","codec","kind","version","size","SHA","identity"])
def test_each_reference_field_is_required_without_default(field, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"])
    del value["refs"]["activation_prepare_reservation"][field]
    rejected(lambda: subject.decode(canonical(value)),"FIELDS_INVALID")


@pytest.mark.parametrize("case", ["family","kind","scope","format","namespace2","old-prepare","profile-role","physical-role","raw-size-zero","ref-format","ref-role","ref-kind","ref-codec"],
    ids=["family","kind","scope","format","namespace2","old-prepare","profile-role","physical-role","raw-size-zero","ref-format","ref-role","ref-kind","ref-codec"])
def test_roles_versions_and_old_prepare_domain_are_not_normalized(case, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"])
    if case == "old-prepare": raw = materials["prepare_raw"]
    else:
        if case == "family": value["family"] = "personal_authority_enrollment_sql_prepare"
        elif case == "kind": value["kind"] = "sql_registration_prepare"
        elif case == "scope": value["scope"] = "durable_prepare_content_only"
        elif case == "format": value["format_version"] = 2
        elif case == "namespace2": value["namespace"]["family"] = "personal_authority_namespace2"
        elif case == "profile-role": value["profiles"]["authority_profile_paths_role"] = "canonical_physical_profile_dict_v1"
        elif case == "physical-role": value["profiles"]["physical_profile_role"] = "canonical_four_paths_array_v1"
        elif case == "raw-size-zero": value["refs"]["first"]["raw_byte_size"] = 0
        elif case == "ref-format": value["refs"]["first"]["format_version"] = 2
        elif case == "ref-role": value["refs"]["first"]["role"] = value["refs"]["second"]["role"]
        elif case == "ref-kind": value["refs"]["first"]["record_kind"] = "origin_observation"
        else: value["refs"]["first"]["codec"] = "personal_authority_enrollment_binding"
        raw = canonical(value)
    expected = "INTEGER_INVALID" if case == "raw-size-zero" else "FIELDS_INVALID" if case == "old-prepare" else "ROLE_INVALID" if case in ("profile-role","physical-role","ref-role","ref-kind","ref-codec") else "VERSION_INVALID"
    rejected(lambda: subject.decode(raw),expected)


@pytest.mark.parametrize("case", ["domain","operation","issuer","native-root","path-root","physical-SHA","first-size","prepare-size","post-size","reference-identity"],
    ids=["domain","operation","issuer","native-root","path-root","physical-SHA","first-size","prepare-size","post-size","reference-identity"])
def test_full_expected_context_profiles_namespace_and_each_ref_group_sizes(case, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"])
    if case in ("domain","operation","issuer"):
        if case == "issuer": value["context"]["issuer"]["facility_id"] = OTHER_UUID
        else: value["context"]["domain_id" if case == "domain" else "init_operation_id"] = OTHER_UUID
        for ref in value["refs"].values(): ref["identity"] = deepcopy(value["context"])
        row = value["initial_state"]["binding"]
        row[2],row[3],row[4],row[7] = value["context"]["domain_id"],value["context"]["issuer"]["facility_id"],value["context"]["issuer"]["node_id"],value["context"]["init_operation_id"]
        value["initial_state"]["logical_head"]["init_operation_id"] = row[7]
    elif case == "native-root": value["namespace"]["root_native_identity"]["file_index"] = "999"
    elif case == "path-root": value["namespace"]["root_path_identity"]["inode"] = "999"
    elif case == "physical-SHA": value["profiles"]["physical_profile_sha256"] = wrong_sha(value["profiles"]["physical_profile_sha256"])
    elif case.endswith("size"):
        key = {"first-size":"first","prepare-size":"activation_prepare_payload","post-size":"postcommit_observation"}[case]
        value["refs"][key]["raw_byte_size"] += 1
    else: value["refs"]["first"]["identity"]["issuer"]["facility_id"] = OTHER_UUID
    expected = "ORIGINAL_BINDING_INVALID" if case in ("first-size","reference-identity") else "PREPARE_BINDING_INVALID" if case == "prepare-size" else "OBSERVATION_BINDING_INVALID"
    rejected(lambda: candidate(materials,value),expected)


@pytest.mark.parametrize("index", [0,1,2,3,4,5,6,7,8,9],
    ids=["slot","namespace","domain","facility","node","epoch","genesis-raw","init","prepare-raw","revision"])
def test_each_initial_binding_cell_and_derived_head_join(index, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"]); row = value["initial_state"]["binding"]
    row[index] = row[index] + 1 if index in (0,1,5,9) else wrong_sha(row[index]) if index in (6,8) else OTHER_UUID
    head = value["initial_state"]["logical_head"]
    for key,slot in (("epoch",5),("grant_sha256",6),("init_operation_id",7),("activation_prepare_sha256",8)):
        head[key] = row[slot]
    rejected(lambda: candidate(materials,value),"INTEGER_INVALID" if index in (0,1,9) else "INITIAL_BINDING_INVALID")


@pytest.mark.parametrize("field", ["kind","binding_slot","binding_revision","epoch","grant_sha256","init_operation_id","activation_prepare_sha256","journal_sequence","previous_commit_sha256"],
    ids=["kind","slot","revision","epoch","grant","init","prepare","sequence","previous"])
def test_each_initial_head_field_is_exact(field, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"]); head = value["initial_state"]["logical_head"]
    current = head[field];head[field] = current + 1 if type(current) is int else "f" * 64 if current is None else "changed"
    rejected(lambda: candidate(materials,value),"INITIAL_HEAD_INVALID")


@pytest.mark.parametrize("case", ["binding-rows-list","binding-short","binding-bool","journal-list","journal-one","journal-null","journal-bool"],
    ids=["binding-rows-list","binding-short","binding-bool","journal-list","journal-one","journal-null","journal-bool"])
def test_row_and_journal_shapes_preserve_row10_and_exact_integer_zero(case, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"])
    if case == "binding-rows-list": value["initial_state"]["binding"] = [value["initial_state"]["binding"]]
    elif case == "binding-short": value["initial_state"]["binding"].pop()
    elif case == "binding-bool": value["initial_state"]["binding"][0] = True
    else: value["initial_state"]["journal_rows"] = {"journal-list":[],"journal-one":1,"journal-null":None,"journal-bool":False}[case]
    expected = "JSON_INVALID" if case.endswith("bool") else "INITIAL_BINDING_INVALID" if case.startswith("binding") else "INITIAL_JOURNAL_INVALID" if case == "journal-one" else "INTEGER_INVALID"
    rejected(lambda: subject.decode(canonical(value)),expected)


@pytest.mark.parametrize("field", ["personal_meta_sha256","personal_sql_sha256"], ids=["meta","SQL"])
def test_both_personal_document_SHA_fields_are_bound_to_full_pair_and_intent(field, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"]);value["unchanged_personal"][field] = wrong_sha(value["unchanged_personal"][field])
    rejected(lambda: candidate(materials,value),"OBSERVATION_BINDING_INVALID")


@pytest.mark.parametrize("case", ["unknown","other-transition","other-valid-pair","phase-swap"],
    ids=["unknown","other-transition","other-valid-pair","phase-swap"])
def test_successor_and_refs_are_bound_to_this_complete_transition_pair(case, materials):
    value = deepcopy(materials["cases"]["initial-base"]["value"]);args = materials["cases"]["initial-base"]["args"]
    if case == "unknown": value["transition"] = "already_committed"
    elif case == "other-transition": value["transition"] = "same_binding_recovery"
    elif case == "other-valid-pair": value = deepcopy(materials["cases"]["initial-unbound"]["value"])
    else: args = dict(args,precommit_raw=args["postcommit_raw"],postcommit_raw=args["precommit_raw"])
    rejected(lambda: subject._cross_validate_candidate(canonical(value),**args),"OBSERVATION_BINDING_INVALID" if case == "other-valid-pair" else "TRANSITION_INVALID")


@pytest.mark.parametrize("case", ["single-phase-transition","personal","base-SQL","user-version","DBidentity","binding","journal","head"],
    ids=["single-phase-transition","personal","base-SQL","user-version","DBidentity","binding","journal","head"])
def test_original_complete_observation_pair_checks_remain_real_dependencies(case, materials):
    row = materials["cases"]["recovery"];post = json.loads(row["args"]["postcommit_raw"])
    if case == "single-phase-transition": post["transition"] = "initial_install"
    elif case == "personal": post["personal"]["meta_values"]["library_id"] = OTHER_UUID
    elif case == "base-SQL": post["catalog"]["base_objects"][0]["sql"] += "\n"
    elif case == "user-version": post["catalog"]["sqlite_user_version"] = 17
    elif case == "DBidentity":
        file = post["source"]["raw_sql_files"][""]
        file["path_identity"]["inode"] = file["handle_identity"]["inode"] = "999"
        post["source"]["source_fingerprint"]["files"][""]["inode"] = "999"
    elif case == "binding":
        post["authority"]["binding_rows"][0][6] = wrong_sha(post["authority"]["binding_rows"][0][6])
        post["authority"]["logical_head"]["grant_sha256"] = post["authority"]["binding_rows"][0][6]
    elif case == "journal": post["authority"]["journal_rows"] = [[1]]
    else: post["authority"]["logical_head"]["journal_sequence"] = 1
    expected = {"single-phase-transition":"TRANSITION_INVALID","personal":"OBSERVATION_BINDING_INVALID", "base-SQL":"CATALOG_INVALID",
                "user-version":"TRANSITION_INVALID","DBidentity":"OBSERVATION_BINDING_INVALID","binding":"INITIAL_BINDING_INVALID",
                "journal":"INITIAL_JOURNAL_INVALID","head":"INITIAL_HEAD_INVALID"}[case]
    rejected(lambda: subject._cross_validate_candidate(row["raw"],**dict(row["args"],postcommit_raw=canonical(post))),expected)


@pytest.mark.parametrize("name", ["build","cross_validate"], ids=["build","cross"])
def test_public_APIs_check_real_full_content_then_actual_unavailable_publication(name, materials):
    case = materials["cases"]["initial-base"]
    assert subject._cross_validate_candidate(case["raw"],**case["args"]) is None
    with pytest.raises(schema.JointSqlSchemaError) as caught:
        subject.build(**case["args"]) if name == "build" else subject.cross_validate(case["raw"],**case["args"])
    assert type(caught.value) is schema.JointSqlSchemaError
    assert caught.value.code == "JOINT_SQL_SCHEMA_REGISTRY_UNAVAILABLE"
    if name == "build":
        post = json.loads(case["args"]["postcommit_raw"]);post["personal"]["meta_values"]["library_id"] = OTHER_UUID
        rejected(lambda: subject.build(**dict(case["args"],postcommit_raw=canonical(post))),"OBSERVATION_BINDING_INVALID")
    else:
        bad = deepcopy(case["value"]);bad["refs"]["first"]["raw_sha256"] = wrong_sha(bad["refs"]["first"]["raw_sha256"])
        rejected(lambda: subject.cross_validate(canonical(bad),**case["args"]),"ORIGINAL_BINDING_INVALID")


def test_old_prepare_six_APIs_wire_domain_and_module_import_direction_preserved(materials):
    names = ["encode","decode","build_prepare","cross_validate_prepare","build_reservation","cross_validate_reservation"]
    assert all(callable(getattr(prepare,name)) for name in names)
    payload = prepare.build_prepare(originals=materials["originals"])
    assert payload == materials["prepare_raw"] and prepare.encode(prepare.decode(payload)) == payload
    assert prepare.cross_validate_prepare(payload, originals=materials["originals"]) is None
    reservation = prepare.build_reservation(payload, originals=materials["originals"])
    assert reservation == materials["reservation_raw"]
    assert prepare.cross_validate_reservation(reservation,payload,originals=materials["originals"]) is None
    with pytest.raises(prepare.EnrollmentSqlSuccessorError): prepare.decode(materials["cases"]["initial-base"]["raw"])
    for module in (prepare,observation,schema):
        tree = ast.parse(inspect.getsource(module))
        imports = [alias.name for node in ast.walk(tree) if isinstance(node,(ast.Import,ast.ImportFrom)) for alias in node.names]
        assert "personal_authority_enrollment_successor_content" not in imports
    assert not hasattr(prepare,"_build_candidate") and not hasattr(prepare,"_cross_validate_candidate")


def test_complete_API_signatures_immutability_and_no_runtime_grant_fields(materials):
    kwargs = ["originals","prepare_payload_raw","prepare_reservation_raw","precommit_raw","postcommit_raw"]
    expected = {"encode":["value"],"decode":["successor_raw"],"_build_candidate":kwargs,"build":kwargs,
        "_cross_validate_candidate":["successor_raw"] + kwargs,"cross_validate":["successor_raw"] + kwargs}
    assert subject.ContractError is subject.EnrollmentSqlSuccessorContentError
    for name,args in expected.items():
        params = inspect.signature(getattr(subject,name)).parameters
        assert list(params) == args
        assert not any(p.kind in (inspect.Parameter.VAR_POSITIONAL,inspect.Parameter.VAR_KEYWORD) for p in params.values())
        assert all(params[key].kind is inspect.Parameter.KEYWORD_ONLY for key in kwargs if key in params)
    for name in ("_observe_locked","_install_empty_locked","_classify_locked","register","set_ready"):
        assert not hasattr(subject,name)
    case = materials["cases"]["initial-base"];raws = case["args"]["originals"] + tuple(case["args"][key] for key in kwargs[1:]) + (case["raw"],)
    before = tuple((len(raw),sha(raw)) for raw in raws)
    assert subject._build_candidate(**case["args"]) == case["raw"]
    assert subject._cross_validate_candidate(case["raw"],**case["args"]) is None
    assert tuple((len(raw),sha(raw)) for raw in raws) == before
    for field in ("active","writable","commit_verified","source_closed"):
        bad = deepcopy(case["value"]);bad[field] = 1
        rejected(lambda bad=bad: subject.decode(canonical(bad)),"FIELDS_INVALID")
    assert materials["provenance"]["new_dependency_subject_or_pytest_executed"] is False
