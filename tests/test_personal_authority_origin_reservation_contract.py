"""Pure declaration validation; synthetic identities are not origin evidence.

Successful observation/cross-record validation uses actual r815 producer bytes
in the separate runtime tests. This file invents no accepted observation DTO.
"""
from copy import deepcopy
import hashlib
import json
import stat

import pytest

from reno import personal_authority_origin_reservation_contract as contract


OPERATION = "11111111-1111-4111-8111-111111111111"
DOMAIN = "22222222-2222-4222-8222-222222222222"
FACILITY = "33333333-3333-4333-8333-333333333333"
NODE = "44444444-4444-4444-8444-444444444444"


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def digest(value):
    return hashlib.sha256(encoded(value)).hexdigest()


def declaration():
    """Explicitly synthetic, non-I/O first-record contract fixture."""
    identity = {"device": "1", "inode": "2"}
    profile = {"profile_paths": ["/synthetic/data/reno.db", "/synthetic/media",
                                 "/synthetic/reports", "/synthetic/checkpoints"],
               "roots": {name: {"path": path, "identity": dict(identity)}
                         for name, path in zip(("data", "media", "reports", "checkpoints"),
                         ("/synthetic/data", "/synthetic/media", "/synthetic/reports",
                          "/synthetic/checkpoints"))}}
    file_identity = {"device": "1", "inode": "3", "byte_size": 53,
                     "mtime_ns": "4", "ctime_ns": "5", "mode": stat.S_IFREG | 0o600,
                     "nlink": 1}
    return {"format_version": 1, "family": "personal_authority_origin_reservation",
            "kind": "origin_reservation", "init_operation_id": OPERATION,
            "domain_id": DOMAIN, "facility_id": FACILITY, "profile": profile,
            "authority_profile_paths_sha256": digest(profile["profile_paths"]),
            "physical_profile_sha256": digest(profile),
            "node": {"node_id": NODE, "sha256": "a" * 64, "byte_size": 53,
                     "directory_identity": dict(identity),
                     "path_identity": dict(file_identity), "handle_identity": dict(file_identity)}}


def assert_rejected(call, suffix):
    with pytest.raises(contract.OriginReservationContractError) as raised:
        call()
    assert raised.value.code == "ORIGIN_RESERVATION_" + suffix


def test_first_record_is_canonical_deep_copied_and_has_two_distinct_digest_domains():
    value = declaration()
    raw = contract.encode(value)
    decoded = contract.decode(raw)
    assert raw == encoded(value)
    assert decoded == value
    assert contract.record_sha256(raw) == hashlib.sha256(raw).hexdigest()
    assert decoded["authority_profile_paths_sha256"] != decoded["physical_profile_sha256"]
    decoded["profile"]["roots"]["media"]["identity"]["inode"] = "99"
    assert contract.decode(raw) == value
    assert set(value) == {"format_version", "family", "kind", "init_operation_id", "domain_id",
                          "facility_id", "profile", "authority_profile_paths_sha256",
                          "physical_profile_sha256", "node"}
    assert not ({"sql_document", "meta_document", "grant", "witness"} & set(value))


@pytest.mark.parametrize("suffix,raw", [
    ("JSON_DUPLICATE_KEY", b'{"kind":"origin_reservation","kind":"origin_reservation"}'),
    ("JSON_NONCANONICAL", b'{ "kind": "origin_reservation" }'),
    ("JSON_INVALID", b'\xff'),
    ("JSON_INVALID", b'{"kind":1.5}'),
    ("JSON_INVALID", b'{"kind":NaN}'),
    ("JSON_INVALID", b'{"kind":true}'),
    ("INTEGER_INVALID", b'{"kind":9223372036854775808}'),
    ("LIMIT", b'[' * 17 + b'0' + b']' * 17),
    ("LIMIT", b'[' + b','.join([b'null'] * 4096) + b']'),
    ("LIMIT", b' ' * (64 * 1024 + 1)),
], ids=["duplicate-key", "noncanonical", "invalid-utf8", "float", "nan", "boolean",
        "integer-overflow", "depth-limit", "node-limit", "byte-limit"])
def test_wire_parser_rejects_ambiguous_unbounded_or_noncanonical_json(suffix, raw):
    assert_rejected(lambda: contract.decode(raw), suffix)


@pytest.mark.parametrize("field", ["init_operation_id", "domain_id", "facility_id"])
@pytest.mark.parametrize("bad", ["00000000-0000-0000-0000-000000000000",
                                  "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", 1])
def test_first_uuid_must_be_canonical_nonzero_string(field, bad):
    value = declaration()
    value[field] = bad
    assert_rejected(lambda: contract.encode(value), "UUID_INVALID")


@pytest.mark.parametrize("field", ["authority_profile_paths_sha256", "physical_profile_sha256"])
def test_array_and_physical_dictionary_digests_cannot_be_swapped(field):
    value = declaration()
    other = ("physical_profile_sha256" if field == "authority_profile_paths_sha256"
             else "authority_profile_paths_sha256")
    value[field] = value[other]
    assert_rejected(lambda: contract.encode(value), "BINDING_MISMATCH")


@pytest.mark.parametrize("change,suffix", [
    (lambda v: v.update(extra="unknown"), "FIELDS_INVALID"),
    (lambda v: v.update(format_version=2), "VERSION_INVALID"),
    (lambda v: v.update(kind="origin_grant"), "KIND_INVALID"),
    (lambda v: v["node"].update(node_id="00000000-0000-0000-0000-000000000000"), "UUID_INVALID"),
    (lambda v: v["node"].update(sha256="A" * 64), "SHA_INVALID"),
    (lambda v: v["node"].update(byte_size=54), "BINDING_MISMATCH"),
    (lambda v: v["node"]["handle_identity"].update(inode="999"), "BINDING_MISMATCH"),
    (lambda v: v["node"]["path_identity"].update(nlink=2), "IDENTITY_INVALID"),
    (lambda v: v["node"]["path_identity"].update(mode=stat.S_IFDIR | 0o700), "IDENTITY_INVALID"),
    (lambda v: v["node"]["path_identity"].update(inode="03"), "IDENTITY_INVALID"),
    (lambda v: v["node"]["directory_identity"].update(inode="999"), "BINDING_MISMATCH"),
    (lambda v: v["profile"]["roots"]["data"].update(path="/synthetic/other"), "PROFILE_INVALID"),
])
def test_first_record_rejects_unknown_fields_and_unbound_metadata(change, suffix):
    value = declaration()
    change(value)
    assert_rejected(lambda: contract.encode(value), suffix)


@pytest.mark.parametrize("path", ["relative/reno.db", "/synthetic/../data/reno.db",
                                    "/synthetic//data/reno.db", "C:\\Synthetic\\data\\reno.db"])
def test_profile_paths_must_already_be_absolute_canonical_keys(path):
    value = declaration()
    value["profile"]["profile_paths"][0] = path
    assert_rejected(lambda: contract.encode(value), "PROFILE_INVALID")


@pytest.mark.parametrize("field", ["format_version", "byte_size", "mode", "nlink"])
def test_boolean_is_not_an_integer_even_when_python_compares_it_equal(field):
    value = declaration()
    if field == "format_version":
        value[field] = True
    else:
        value["node"]["path_identity"][field] = True
    assert_rejected(lambda: contract.encode(value), "JSON_INVALID")


def test_path_and_handle_ctime_may_differ_without_weakening_other_identity_binding():
    value = declaration()
    value["node"]["handle_identity"]["ctime_ns"] = "6"
    assert contract.decode(contract.encode(value)) == value
    value["node"]["handle_identity"]["mtime_ns"] = "6"
    assert_rejected(lambda: contract.encode(value), "BINDING_MISMATCH")


def test_first_kind_has_its_own_byte_limit_even_through_shared_decoder():
    value = declaration()
    # Each key is absolute/canonical and below its individual 4096-byte cap;
    # repeating the four physical paths in their roots exceeds only first's
    # 16 KiB record limit while remaining below the shared 64 KiB parser cap.
    prefix = "/" + "x" * 2500
    paths = [prefix + "/data/reno.db", prefix + "/media", prefix + "/reports", prefix + "/checkpoints"]
    value["profile"]["profile_paths"] = paths
    for name, path in zip(("data", "media", "reports", "checkpoints"),
                          [prefix + "/data", *paths[1:]]):
        value["profile"]["roots"][name]["path"] = path
    value["authority_profile_paths_sha256"] = digest(paths)
    value["physical_profile_sha256"] = digest(value["profile"])
    raw = encoded(value)
    assert 16 * 1024 < len(raw) < 64 * 1024
    assert_rejected(lambda: contract.encode(value), "LIMIT")
    assert_rejected(lambda: contract.decode(raw), "LIMIT")


def test_stage_name_is_exact_and_never_a_claim_to_existing_file():
    for name in (contract.RESERVATION_NAME, contract.OBSERVATION_NAME):
        assert contract.stage_name(name, OPERATION) == name + "." + OPERATION + ".stage"
    assert_rejected(lambda: contract.stage_name(".reno-personal-authority-unknown", OPERATION), "KIND_INVALID")
    assert_rejected(lambda: contract.stage_name(contract.RESERVATION_NAME, "0"), "UUID_INVALID")


def test_encoder_does_not_mutate_input_on_failure():
    value = declaration()
    value["node"]["handle_identity"]["inode"] = "100"
    before = deepcopy(value)
    assert_rejected(lambda: contract.encode(value), "BINDING_MISMATCH")
    assert value == before
