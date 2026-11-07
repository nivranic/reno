"""Synthetic declarations and supplied bytes; no physical capture claims."""
from copy import deepcopy
import hashlib
import json

import pytest

from reno import personal_authority_preservation_contract as old
from reno import personal_authority_preservation3_contract as new
from reno import personal_authority_source_projection as p1
from reno import personal_authority_source_projection2 as p2
from tests.test_personal_authority_preservation_contract import synthetic_capsule, wire

ROOT = ".reno-personal-authority-namespace-v3"
SPECS = (
    (".reno-personal-authority-origin-v1-reservation.json", "origin_reservation_raw", b"\xff{not-json\n"),
    (".reno-personal-authority-origin-v1-observation.json", "origin_observation_raw", b" original\x00"),
    (ROOT + "-reservation.json", "namespace3_anchor_raw", b" reservation  \n"),
    (ROOT + "-header.json", "namespace3_anchor_raw", b"header"),
    (ROOT + "/" + ROOT + "-root-seal.json", "namespace3_root_seal_raw", b"{ broken seal"),
    (ROOT + "/facts/head.json", "unclassified_authority_raw", b"opaque modern fact"),
)


def capsule(*, copies=False):
    value, sources, copied, artifacts = synthetic_capsule(SPECS, empty_dirs=(ROOT + "/empty",), copies=copies)
    value.update(format_version=3, scope="personal-authority-raw-census-v3",
                 layout_profile="namespace3-origin1-opaque-authority-r1158",
                 layout_source_sha256="ade18a7270d577142f84b9591bdb48e2c25c72d302a49adc926b971c305c3777")
    return value, sources, copied, artifacts


def test_new_canonical_bytes_and_raw_buffers_keep_invalid_json_exactly():
    value, sources, copies, artifacts = capsule(copies=True)
    encoded = new.encode(value)
    assert encoded == wire(value)
    assert new.decode(encoded) == value
    assert new.cross_validate_originals(value, source_bytes=sources, copy_bytes=copies, artifact_bytes=artifacts) is None
    assert hashlib.sha256(new.LAYOUT_SOURCE_BYTES).hexdigest() == value["layout_source_sha256"]
    assert json.loads(new.LAYOUT_SOURCE_BYTES)["modern_exact_roles"][ROOT + "/" + ROOT + "-root-seal.json"] == "namespace3_root_seal_raw"


def test_old_wire_and_roles_remain_old_and_generations_reject_each_other():
    legacy = synthetic_capsule([(".reno-personal-authority-namespace-v2/facts/head.json", "primitive_fact_raw", b"old")])[0]
    original = wire(legacy)
    assert old.encode(legacy) == original and old.decode(original) == legacy
    modern = new.encode(capsule()[0])
    with pytest.raises(old.AuthorityPreservationContractError):
        old.decode(modern)
    with pytest.raises(new.AuthorityPreservationContractError):
        new.decode(original)
    assert new._role(".reno-personal-authority-namespace-v2/facts/head.json") == "primitive_fact_raw"
    assert old._role(SPECS[0][0]) == "unclassified_authority_raw"
    assert old.encode(legacy) == original


def test_exact_modern_roles_do_not_adopt_case_variants_or_store2_children():
    for path, expected, _raw in SPECS:
        assert new._role(path) == expected
        assert new._role(path.upper()) == "unclassified_authority_raw"
    for path in (ROOT + "/store-header.json", ROOT + "/journal-v1/records/a.json", ROOT + "-root-seal.json"):
        assert new._role(path) == "unclassified_authority_raw"
    variant = SPECS[0][0].upper()
    value = synthetic_capsule([(variant, "unclassified_authority_raw", b"case raw")])[0]
    value.update({key: capsule()[0][key] for key in ("format_version", "scope", "layout_profile", "layout_source_sha256")})
    assert new.decode(new.encode(value))["files"][0]["relative_key"] == variant


@pytest.mark.parametrize("damage", ["version", "role", "unknown_origin", "identity_alias", "extra_authority"])
def test_new_declarations_reject_corruption_with_new_error_prefix(damage):
    value = capsule()[0]
    if damage == "version":
        value["format_version"] = True
    elif damage == "role":
        value["files"][0]["role"] = "primitive_fact_raw"
    elif damage == "unknown_origin":
        value = synthetic_capsule([(".reno-personal-authority-origin-v2-observation.json", "unclassified_authority_raw", b"unknown")])[0]
        value.update({key: capsule()[0][key] for key in ("format_version", "scope", "layout_profile", "layout_source_sha256")})
    elif damage == "identity_alias":
        value["files"][1]["source_path_identity"] = deepcopy(value["files"][0]["source_path_identity"])
        value["files"][1]["source_handle_identity"] = deepcopy(value["files"][0]["source_handle_identity"])
    else:
        value["writer_permission"] = True
    with pytest.raises(new.AuthorityPreservationContractError) as error:
        new.encode(value)
    assert error.value.code.startswith("AUTHORITY_PRESERVATION3_")


def test_buffer_digest_and_complete_copy_set_are_checked_without_reencoding():
    value, sources, copies, artifacts = capsule(copies=True)
    changed = dict(sources)
    changed[next(iter(changed))] += b"x"
    with pytest.raises(new.AuthorityPreservationContractError):
        new.cross_validate_originals(value, source_bytes=changed, copy_bytes=copies, artifact_bytes=artifacts)
    with pytest.raises(new.AuthorityPreservationContractError):
        new.cross_validate_originals(value, source_bytes=sources, copy_bytes={}, artifact_bytes=artifacts)


def test_projection2_validates_whole_capsule_before_excluding_operation_and_copies():
    value = capsule(copies=True)[0]
    projected = p2.from_capsule(value)
    assert projected["format_version"] == 2 and projected["capsule_format_version"] == 3
    assert p2.decode(p2.encode(projected)) == projected
    other = deepcopy(value)
    other["operation_id"] = "33333333-3333-4333-8333-333333333333"
    assert p2.from_capsule(other) == projected
    other["copies"][0]["byte_size"] += 1
    with pytest.raises(p2.AuthoritySourceProjectionError):
        p2.from_capsule(other)
    with pytest.raises(p1.AuthoritySourceProjectionError):
        p1.normalize(projected)
    legacy_projection = p1.from_capsule(synthetic_capsule()[0])
    with pytest.raises(p2.AuthoritySourceProjectionError):
        p2.normalize(legacy_projection)


def test_projection_mismatch_alias_and_noncanonical_wire_are_rejected():
    value = capsule()[0]
    projected = p2.from_capsule(value)
    changed = deepcopy(projected)
    changed["files"][0]["sha256"] = "0" * 64
    with pytest.raises(p2.AuthoritySourceProjectionError):
        p2.cross_validate_capsule(changed, value)
    with pytest.raises(new.AuthorityPreservationContractError):
        new.decode(b" " + new.encode(value))
    with pytest.raises(p2.AuthoritySourceProjectionError):
        p2.decode(b" " + p2.encode(projected))
    aliased = deepcopy(value)
    row = deepcopy(aliased["files"][0])
    row["relative_key"] = row["relative_key"].upper()
    aliased["files"].append(row)
    with pytest.raises(new.AuthorityPreservationContractError):
        new.encode(aliased)


def test_inherited_bounds_and_duplicate_json_keep_new_public_error_types():
    value = capsule()[0]
    with pytest.raises(new.AuthorityPreservationContractError) as error:
        new.encode(value, limits=dict(new.DEFAULT_LIMITS, max_files=1))
    assert error.value.code == "AUTHORITY_PRESERVATION3_LIMIT"
    with pytest.raises(new.AuthorityPreservationContractError):
        new.decode(b'{"format_version":3,"format_version":3}')
    with pytest.raises(p2.AuthoritySourceProjectionError):
        p2.normalize({})
