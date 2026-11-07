"""Independent S0 original-raw2 path-role grammar examples.

Only pure lexical classification is exercised.  These expected strings come
from SOURCE-WIRE 2.1 and the original public R01/raw2 contracts, not from the
new classifier.  A role does not establish bytes, completeness or authority.
"""
import pytest

from reno import personal_authority_replica_path_roles as roles


UUID = "12345678-1234-4234-8234-123456789abc"
ZERO_UUID = "00000000-0000-0000-0000-000000000000"
SHA = "0123456789abcdef" * 4
OTHER_SHA = "fedcba9876543210" * 4
ENTRY = "f00000000"
PREFIX = "AUTHORITY_REPLICA_PATH_ROLE1_"
ERROR_SUFFIXES = {"TYPE_INVALID", "PATH_INVALID", "ORIGIN_INVALID"}


def rejected(call, *args, **kwargs):
    with pytest.raises(roles.AuthorityReplicaPathRoleError) as captured:
        call(*args, **kwargs)
    assert type(captured.value.code) is str
    assert captured.value.code.startswith(PREFIX)
    assert captured.value.code[len(PREFIX):] in ERROR_SUFFIXES


@pytest.mark.parametrize("key", [
    "request.json", "source-metadata.json", "source-projection.json", "selection.json",
])
def test_root_formal_control_names(key):
    assert roles.classify_file(key) == "control"


@pytest.mark.parametrize("generation", ["01", "02"])
@pytest.mark.parametrize("name", [
    "run-attempt.json", "capsule.json", "source-verified.json", "manifest.json", "receipt.json",
])
def test_both_original_generations_have_the_five_formal_controls(generation, name):
    assert roles.classify_file(f"runs/{generation}/{name}") == "control"


@pytest.mark.parametrize("generation", ["01", "02"])
@pytest.mark.parametrize("entry", ["f00000000", "f00000001", "f99999999"])
def test_copy_records_use_original_f_ids_not_new_package_p_ids(generation, entry):
    assert roles.classify_file(f"runs/{generation}/copy-records/{entry}.json") == "control"


@pytest.mark.parametrize("generation", ["01", "02"])
def test_copy_attempt_path_uses_its_original_uuid(generation):
    assert roles.classify_file(f"runs/{generation}/copy-attempts/{UUID}.json") == "control"


def test_only_original_root_second_slot_claim_is_a_marker():
    assert roles.classify_file("resume-slot-02.claim") == "marker"


@pytest.mark.parametrize("generation", ["01", "02"])
def test_raw_payload_is_not_a_formal_control(generation):
    assert roles.classify_file(f"runs/{generation}/payload/{UUID}/{ENTRY}.raw") == "payload"


@pytest.mark.parametrize("generation", ["01", "02"])
@pytest.mark.parametrize("relative", [f"failures/{UUID}.json", f".write-{UUID}.tmp"])
def test_failure_json_and_temp_stay_retained(generation, relative):
    assert roles.classify_file(f"runs/{generation}/{relative}") == "retained"


@pytest.mark.parametrize("key,expected", [
    ("", "root"), ("runs", "runs"),
    ("runs/01", "run"), ("runs/02", "run"),
    (f"admission-q1-sha256-{SHA}", "anchor"),
    (f"runs/01/payload/{UUID}", "payload_copy"),
    (f"runs/02/payload/{UUID}", "payload_copy"),
])
def test_directory_roles_are_a_separate_closed_grammar(key, expected):
    assert roles.classify_directory(key, expected_request_sha256=SHA) == expected


@pytest.mark.parametrize("generation", ["01", "02"])
@pytest.mark.parametrize("name", ["copy-attempts", "copy-records", "payload", "failures"])
def test_four_original_fixed_directories(generation, name):
    assert roles.classify_directory(f"runs/{generation}/{name}", expected_request_sha256=SHA) == "fixed"


@pytest.mark.parametrize("key", [
    "", "request.JSON", "Request.json", "other.json", "request.json/child", "runs", "runs/01",
    "runs/00/capsule.json", "runs/1/capsule.json", "runs/03/capsule.json",
    "runs/01/source-selection.json", "runs/01/attempt.json", "runs/01/replica.json",
    "runs/01/source-closed.json", "runs/01/target-closed.json", "runs/01/receipt-started.claim",
    "receipt-started.claim", "resume-attempt-02.claim", "resume-slot-01.claim",
    f".write-{UUID}.tmp", f"failures/{UUID}.json",
    f"attempts/01/copy-attempts/{UUID}.json", "attempts/01/request.json",
    f"runs/01/copy-attempts/{ZERO_UUID}.json", f"runs/02/copy-attempts/{UUID.upper()}.json",
    f"runs/01/payload/{ZERO_UUID}/{ENTRY}.raw", f"runs/01/failures/{ZERO_UUID}.json",
    f"runs/01/.write-{ZERO_UUID}.tmp", f"runs/01/copy-records/p00000000.json",
    "runs/01/copy-records/f0000000.json", "runs/01/copy-records/f000000000.json",
    "runs/01/copy-records/f0000000x.json", "runs/01/copy-records/f00000000.raw",
    f"runs/01/payload/{UUID}/p00000000.raw", f"runs/01/payload/{UUID}/{ENTRY}.json",
    f"runs/01/payload/{UUID}/{ENTRY}.raw/extra", f"runs/01/payload/{UUID}/another/{ENTRY}.raw",
    f"runs/01/copy-attempts/{UUID}.json.tmp", f"runs/01/failures/{UUID}.json/extra",
])
def test_unknown_or_cross_family_file_names_are_rejected(key):
    rejected(roles.classify_file, key)


@pytest.mark.parametrize("key", [
    "request.json", "resume-slot-02.claim", "attempts", "attempts/01", "runs/03",
    "runs/01/unknown", "runs/01/payload/unknown", f"runs/01/payload/{ZERO_UUID}",
    f"runs/01/payload/{UUID}/child", f"runs/01/copy-records/{UUID}",
    f"admission-q1-sha256-{OTHER_SHA}", f"admission-rp1-sha256-{SHA}",
    "admission-q1-sha256-", f"admission-q1-sha256-{SHA.upper()}",
])
def test_unknown_directory_or_different_anchor_is_rejected(key):
    rejected(roles.classify_directory, key, expected_request_sha256=SHA)


@pytest.mark.parametrize("key", [
    "/request.json", "//request.json", "./request.json", "../request.json", "runs/../request.json",
    "runs//01/capsule.json", "runs/./01/capsule.json", "request.json/", " request.json",
    "request.json ", "request.json.", "C:/request.json", "request.json:stream",
    "runs\\01\\capsule.json", "request.json\x00", "request.json\n", "request.json\x7f",
    "runs/01/CON", "runs/01/nul.txt", "runs/01/LPT1.json", "runs/01/COM9.json",
    "\ud800", "a" * 256, "/".join(["x"] * 129), "a" * 32769,
])
def test_common_key_violations_are_rejected_by_both_apis(key):
    rejected(roles.classify_file, key)
    rejected(roles.classify_directory, key, expected_request_sha256=SHA)


@pytest.mark.parametrize("value", [None, True, 1, 1.0, b"request.json", [], {}, ("request.json",)])
def test_keys_require_exact_strings(value):
    rejected(roles.classify_file, value)
    rejected(roles.classify_directory, value, expected_request_sha256=SHA)


@pytest.mark.parametrize("value", [None, True, 1, b"0" * 64, "", "0" * 63, "0" * 65, "g" * 64, SHA.upper()])
def test_directory_anchor_context_is_validated_even_for_root(value):
    rejected(roles.classify_directory, "", expected_request_sha256=value)


def test_hostile_str_subclasses_are_rejected_without_hooks():
    calls = []

    class Hostile(str):
        def __str__(self):
            calls.append("str")
            raise AssertionError("input hook executed")

        def __hash__(self):
            calls.append("hash")
            raise AssertionError("input hook executed")

        def __eq__(self, other):
            calls.append("eq")
            raise AssertionError("input hook executed")

        def encode(self, *args, **kwargs):
            calls.append("encode")
            raise AssertionError("input hook executed")

    rejected(roles.classify_file, Hostile("request.json"))
    rejected(roles.classify_directory, Hostile("runs"), expected_request_sha256=SHA)
    rejected(roles.classify_directory, "runs", expected_request_sha256=Hostile(SHA))
    assert calls == []


def test_classifiers_never_infer_fact_validity_from_filename():
    # No bytes are accepted by these signatures.  Neither a formal-looking
    # name nor a retained .json file can produce a parsed/verified result.
    assert type(roles.classify_file("request.json")) is str
    assert roles.classify_file(f"runs/02/failures/{UUID}.json") == "retained"
    with pytest.raises(TypeError):
        roles.classify_file("request.json", raw=b"{}")
    with pytest.raises(TypeError):
        roles.classify_directory("runs", expected_request_sha256=SHA, inventory={})


@pytest.mark.parametrize("codepoint", [31, 127, 128, 159])
@pytest.mark.parametrize("shape", ["file_component", "directory_component", "absolute_windows", "absolute_posix"])
def test_control_characters_are_rejected_in_relative_and_absolute_lexical_inputs(codepoint, shape):
    component = "normal" + chr(codepoint) + "component"
    choices = {
        "file_component": "runs/01/" + component + ".json",
        "directory_component": "runs/01/" + component,
        "absolute_windows": "C:/" + component + "/request.json",
        "absolute_posix": "/" + component + "/request.json",
    }
    key = choices[shape]
    rejected(roles.classify_file, key)
    rejected(roles.classify_directory, key, expected_request_sha256=SHA)


@pytest.mark.parametrize("codepoint", [126, 160, 161])
def test_noncontrol_characters_do_not_expand_the_original_closed_grammar(codepoint):
    # Such characters can occur in a selector's local absolute directory,
    # but cannot create a new original-raw2 formal file or directory role.
    key = "runs/01/normal" + chr(codepoint) + "component"
    rejected(roles.classify_file, key + ".json")
    rejected(roles.classify_directory, key, expected_request_sha256=SHA)
