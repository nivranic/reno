"""Pure malformed codec/stage tests; no invented accepted namespace records.

Successful record/hash/cross-validation uses actual factory files in the
runtime companion. Every large raw parameter has an explicit short test ID.
"""
import pytest

from reno import personal_authority_namespace3_contract as contract


OPERATION = "11111111-1111-4111-8111-111111111111"
RESERVATION = ".reno-personal-authority-namespace-v3-reservation.json"
SEAL = ".reno-personal-authority-namespace-v3-root-seal.json"
HEADER = ".reno-personal-authority-namespace-v3-header.json"


@pytest.mark.parametrize("raw,suffix", [
    (b'{"x":0,"x":1}', "JSON_DUPLICATE_KEY"),
    (b'{ "x":0}', "JSON_NONCANONICAL"),
    (b'\xff', "JSON_INVALID"),
    (b'{"x":1.5}', "JSON_INVALID"),
    (b'{"x":NaN}', "JSON_INVALID"),
    (b'{"x":Infinity}', "JSON_INVALID"),
    (b'{"x":true}', "JSON_INVALID"),
    (b'{"x":9223372036854775808}', "INTEGER_INVALID"),
    (b'[' * 17 + b'0' + b']' * 17, "LIMIT"),
    (b'[' + b','.join([b'null'] * 4096) + b']', "LIMIT"),
    (b' ' * (32 * 1024 + 1), "LIMIT"),
], ids=["duplicate-key", "noncanonical", "invalid-utf8", "float", "nan", "infinity", "boolean",
        "integer-overflow", "depth-limit", "node-limit", "byte-limit"])
def test_wire_codec_rejects_ambiguous_or_unbounded_raw_before_claiming_a_record(raw, suffix):
    with pytest.raises(contract.Namespace3ContractError) as raised:
        contract.decode(raw)
    assert raised.value.code == "NAMESPACE3_CONTRACT_" + suffix


@pytest.mark.parametrize("name", [RESERVATION, SEAL, HEADER], ids=["reservation", "root-seal", "header"])
def test_deterministic_stage_name_contains_original_uuid_and_only_fixed_file_name(name):
    assert contract.stage_name(name, OPERATION) == name + "." + OPERATION + ".stage"


@pytest.mark.parametrize("name", [".reno-personal-authority-namespace-v3", "../" + HEADER,
                                  ".reno-personal-authority-unknown"],
                         ids=["directory", "traversal", "unknown"])
def test_stage_name_refuses_nonfixed_targets(name):
    with pytest.raises(contract.Namespace3ContractError):
        contract.stage_name(name, OPERATION)


@pytest.mark.parametrize("operation", ["00000000-0000-0000-0000-000000000000",
                                       "AAAAAAAA-AAAA-4AAA-8AAA-AAAAAAAAAAAA", 1],
                         ids=["nil", "uppercase", "integer"])
def test_stage_name_requires_canonical_nonzero_uuid_string(operation):
    with pytest.raises(contract.Namespace3ContractError):
        contract.stage_name(RESERVATION, operation)


@pytest.mark.parametrize("raw", [None, bytearray(b"{}"), "{}", b""], ids=["none", "mutable", "text", "empty"])
def test_only_nonempty_immutable_bytes_are_wire_input(raw):
    with pytest.raises(contract.Namespace3ContractError):
        contract.decode(raw)


def test_record_sha256_cannot_hash_invalid_bytes_into_an_accepted_record_identity():
    with pytest.raises(contract.Namespace3ContractError):
        contract.record_sha256(b'{"x":0}')


def test_reservation_has_room_for_original_first_metadata_without_expanding_other_record_caps():
    assert contract.MAX_RESERVATION_BYTES == contract.MAX_RECORD_BYTES == 32 * 1024
    assert contract.MAX_ROOT_SEAL_BYTES == contract.MAX_HEADER_BYTES == 16 * 1024
