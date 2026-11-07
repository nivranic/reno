"""Unrun semantic candidates using complete original historical fixtures.

The fixtures were independently assembled for the original R01/raw2 tests.
These tests exercise declarations, never use a synthetic cache as authority.
"""
from copy import deepcopy
import hashlib
import json

import pytest

from reno import personal_authority_replica_contract as replica
from reno import personal_authority_replica_source_declaration as builder
from reno import personal_authority_replica_rawfact as rawfact
from tests import test_personal_authority_replica_contract as original


def drain(generator):
    progress = []
    while True:
        try:
            progress.append(next(generator))
        except StopIteration as done:
            return done.value, progress


def assemble(bundle, generation, *, expected=None):
    if expected is None:
        expected = original.declaration(bundle, generation)
    cache, cache_progress = drain(rawfact.build_cache_steps(bundle["facts"]))
    current = expected["current"]
    result, build_progress = drain(builder.build_declaration_steps(cache,
        directories=current["directories"], files=current["files"], generation=generation,
        replica_id=expected["replica_id"], observation_scope_id=current["observation_scope_id"],
        profile_sha256=current["profile_sha256"], node_id=current["node_id"]))
    return result, cache, cache_progress, build_progress


@pytest.mark.parametrize("kind", ["g1", "g2", "g1_with_g2", "claim", "unpublished", "reserved", "partial_B", "partial_manifest", "resumed"])
@pytest.mark.parametrize("populated", [False, True])
def test_actual_complete_fixture_differential_preserves_every_present_original_byte(kind, populated):
    if kind == "resumed" and not populated:
        # This original resumed fixture is specifically a populated orphan.
        bundle, generation = original.history(kind)
    else:
        bundle, generation = original.history(kind, populated=populated)
    expected = original.declaration(bundle, generation)
    before = deepcopy(expected)
    raw_before, facts_before = dict(bundle["raw_files"]), dict(bundle["facts"])
    result, cache, cache_progress, build_progress = assemble(bundle, generation, expected=expected)
    assert type(result) is builder.BuildResult
    assert result.declaration == expected == before
    assert replica.normalize(result.declaration, fact_bytes=bundle["facts"]) == expected
    assert replica.compare_file_bytes(result.declaration, fact_bytes=bundle["facts"], file_bytes=bundle["raw_files"]) is None
    assert bundle["raw_files"] == raw_before and bundle["facts"] == facts_before
    assert rawfact.fact_bytes(cache) == facts_before
    assert all(type(step) is rawfact.Progress for step in cache_progress)
    assert all(type(step) is builder.BuildProgress for step in build_progress)
    assert build_progress[-1].phase == "complete"
    assert result.structural_visits > 0 and result.canonical_output_bytes > 0 and result.pure_hash_input_bytes >= result.canonical_output_bytes
    assert result.declaration["conclusions"] == original.CONCLUSIONS


def test_all_package_files_have_continuous_p_index_including_control_marker_retained_payload_and_empty_dirs():
    bundle, generation = original.history("resumed")
    result, *_ = assemble(bundle, generation)
    files, dirs = result.declaration["current"]["files"], result.declaration["current"]["directories"]
    assert [identifier for identifier, _ in result.file_index] == [f"p{index:08d}" for index in range(len(bundle["raw_files"]))]
    assert [identifier for identifier, _ in result.directory_index] == [f"d{index:08d}" for index in range(len(bundle["directories"]))]
    assert [row for _, row in result.file_index] == files
    assert [row for _, row in result.directory_index] == dirs
    entry_set = {"directories": [[identifier, row] for identifier, row in result.directory_index],
        "files": [[identifier, row] for identifier, row in result.file_index]}
    expected_digest = hashlib.sha256(b"reno-authority-replica-runtime-v1\0package-entry-set\0" + original.wire(entry_set)).hexdigest()
    assert result.package_entry_set_sha256 == expected_digest
    assert {row["relative_key"] for row in files} == set(bundle["raw_files"])
    assert {row["relative_key"] for row in dirs} == set(bundle["directories"])
    assert all("entry_id" not in row and "package_entry_id" not in row for row in files + dirs)


def test_selected_copies_keep_strict_mixed_slot_origins_and_do_not_adopt_equal_orphan_bytes():
    mixed = original.two_source_history()
    result, *_ = assemble(mixed, 2)
    assert result.declaration == original.declaration(mixed, 2)
    assert [row["origin_slot"] for row in result.declaration["source_map"]] == [1, 2]
    orphan, generation = original.history("resumed")
    result, *_ = assemble(orphan, generation)
    payloads = [row for row in result.declaration["package_map"]["files"] if row["role"] == "payload"]
    assert len(payloads) == 2 and payloads[0]["sha256"] == payloads[1]["sha256"]
    assert len(result.declaration["source_map"]) == 1
    assert result.declaration["source_map"][0]["origin_slot"] == 2


def test_canonical_40_digit_identity_domain_and_large_temp_preserve_original_public_contract():
    bundle, generation = original.history("unpublished")
    bundle["raw_files"][original.TEMP2] = b"retained-temp\x00" * 8192
    expected = original.declaration(bundle, generation)
    dirs = {row["relative_key"]: row for row in expected["current"]["directories"]}
    for index, row in enumerate(dirs.values()):
        row["identity"] = {"device": "9" * 40, "inode": str(10**39 + index + 1)}
    for index, row in enumerate(expected["current"]["files"]):
        row["parent_identity"] = deepcopy(dirs[row["relative_key"].rpartition("/")[0]]["identity"])
        for domain in ("path_identity", "handle_identity"):
            row[domain].update(device="9" * 40, inode=str(2 * 10**39 + index + 1), mtime_ns="8" * 40,
                ctime_ns="7" * 40, mode=0x80000000 | 0o100600)
    original.sync_declaration(expected, children=True)
    assert original.file_row(expected, original.TEMP2)["byte_size"] > 16384
    # The old public codec is the independent acceptance oracle for these
    # declaration domains, not the builder's own constants.
    assert replica.normalize(expected, fact_bytes=bundle["facts"]) == expected
    result, *_ = assemble(bundle, generation, expected=expected)
    assert result.declaration == expected


@pytest.mark.parametrize("alter", ["missing_child", "extra_empty_dir", "duplicate_file_id", "bad_parent", "casefold_collision", "inode_zero", "bool_size"])
def test_complete_current_tree_cannot_hide_missing_or_invalid_metadata(alter):
    bundle, generation = original.history("g1", populated=True)
    expected = original.declaration(bundle, generation)
    current = expected["current"]
    if alter == "missing_child":
        current["directories"][0]["children"].pop()
    elif alter == "extra_empty_dir":
        current["directories"].append({"relative_key": "runs/01/payload/12345678-1234-4234-8234-123456789abc",
            "identity": {"device": "901", "inode": "9999999"}, "children": []})
    elif alter == "duplicate_file_id":
        current["files"][1]["path_identity"] = deepcopy(current["files"][0]["path_identity"])
        current["files"][1]["handle_identity"] = deepcopy(current["files"][0]["handle_identity"])
    elif alter == "bad_parent":
        current["files"][0]["parent_identity"]["inode"] = "99999"
    elif alter == "casefold_collision":
        current["directories"][0]["children"].append(current["directories"][0]["children"][0].upper())
    elif alter == "inode_zero":
        current["directories"][0]["identity"]["inode"] = "0"
    else:
        current["files"][0]["path_identity"]["byte_size"] = True
    with pytest.raises((builder.DeclarationError, replica.AuthorityReplicaContractError)):
        result, *_ = assemble(bundle, generation, expected=expected)
        replica.normalize(result.declaration, fact_bytes=bundle["facts"])


def test_requested_incomplete_generation_has_no_fallback_or_historical_byte_repair():
    bundle, _ = original.history("partial_B")
    original_facts = dict(bundle["facts"])
    with pytest.raises(builder.DeclarationError) as caught:
        assemble(bundle, 2)
    assert caught.value.code.endswith("PACKAGE_INCOMPLETE")
    assert bundle["facts"] == original_facts


def test_hostile_list_or_scalar_hooks_are_rejected_without_dispatch():
    calls = []
    class HostileList(list):
        def __iter__(self):
            calls.append("iter")
            raise AssertionError("caller iteration")
    bundle, generation = original.history("g1")
    expected = original.declaration(bundle, generation)
    cache, _ = drain(rawfact.build_cache_steps(bundle["facts"]))
    with pytest.raises(builder.DeclarationError):
        drain(builder.build_declaration_steps(cache, directories=HostileList(expected["current"]["directories"]),
            files=expected["current"]["files"], generation=generation, replica_id=expected["replica_id"],
            observation_scope_id=expected["current"]["observation_scope_id"], profile_sha256="a" * 64, node_id=original.NODE_ID))
    assert calls == []


def test_cache_and_builder_output_are_only_ordinary_declarations():
    bundle, generation = original.history("g1")
    result, cache, *_ = assemble(bundle, generation)
    ordinary = rawfact._RawFactCache(cache._keys, cache._facts)
    assert rawfact.fact_bytes(ordinary) == bundle["facts"]
    assert result.declaration["conclusions"]["native_observation"] is False
    assert result.declaration["conclusions"]["runtime_write"] is False
    assert result.declaration["conclusions"]["sender_authority"] is False
