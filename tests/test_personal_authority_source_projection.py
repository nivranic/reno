"""Synthetic pure source comparisons, never capture, copy completion or grants.

Expected wire documents are assembled independently from declared source
fixtures.  Matching declarations cannot prove that a producer saw a real tree.
"""
from copy import deepcopy
import builtins
import json
import math
import os
import sqlite3

import pytest

from reno import personal_authority_preservation_contract as capsule
from reno import personal_authority_source_projection as projection
from tests.test_personal_authority_preservation_contract import (
    LIMITS, NODE, OP, PIN, ROOT, directory, full_synthetic_capsule,
    linked_fixture, raw_sha, synthetic_capsule, wire,
)


ERROR = projection.AuthoritySourceProjectionError
OTHER_OP = "33333333-3333-4333-8333-333333333333"


def expected_source(value):
    """The specified source contract, without calling either product codec."""
    return {
        "format_version": 1,
        "kind": "personal_authority_source_projection",
        "scope": "personal-authority-raw-source-comparison-v1",
        "capsule_format_version": 2,
        "capsule_scope": "personal-authority-raw-census-v2",
        "layout_profile": "namespace2-store2-extension3-enrollment1-r729",
        "layout_source_sha256": PIN,
        "source_context": deepcopy(value["source_context"]),
        "source_coverage": {
            "declared_tree": value["coverage"]["declared_tree"],
            "reason": value["coverage"]["reason"],
        },
        "directories": deepcopy(value["directories"]),
        "files": deepcopy(value["files"]),
        "unresolved": deepcopy(value["unresolved"]),
    }


def sample(*, copies=False, empty_dirs=None):
    if empty_dirs is None:
        empty_dirs = (ROOT + "/empty",)
    specs = [(ROOT + "/a.raw", "unclassified_authority_raw", b"alpha"),
             (ROOT + "/b.raw", "unclassified_authority_raw", b"bravo"),
             ("node-identity.json", "node_identity_raw", wire({"instance_id": NODE}))]
    return synthetic_capsule(specs, empty_dirs=empty_dirs, copies=copies, node=True)[0]


def reject(function, *args, code=None, **options):
    with pytest.raises(ERROR) as raised:
        function(*args, **options)
    assert type(raised.value) is ERROR
    assert raised.value.code.startswith("AUTHORITY_SOURCE_PROJECTION1_")
    if code is not None:
        assert raised.value.code == "AUTHORITY_SOURCE_PROJECTION1_" + code
    return raised.value.code


def test_complete_three_journal_source_has_independent_exact_wire():
    value = full_synthetic_capsule()[0]
    expected = expected_source(value)
    assert projection.from_capsule(value) == expected
    assert projection.normalize(expected) == expected
    assert projection.encode(expected) == wire(expected)
    assert projection.decode(wire(expected)) == expected
    assert projection.cross_validate_capsule(expected, value) is None
    assert {row["role"] for row in expected["files"]} >= {
        "store2_journal_raw", "extension3_journal_raw", "enrollment1_journal_raw",
        "namespace_staging_raw", "unclassified_authority_raw",
    }
    assert any(row["relative_key"] == ROOT + "/empty" and not row["children"]
               for row in expected["directories"])
    assert set(expected).isdisjoint({"operation_id", "copies", "artifacts", "links", "ready", "verified"})


@pytest.mark.parametrize("mutation", ["operation", "copy_inode", "copy_destination", "copy_path_ctime",
    "copy_handle_ctime", "copies_partial", "copies_none", "artifacts", "links"])
def test_operation_and_copy_or_optional_evidence_changes_do_not_change_source(mutation):
    original = sample(copies=True)
    changed = deepcopy(original)
    if mutation == "operation":
        changed["operation_id"] = OTHER_OP
    elif mutation == "copy_inode":
        for index, row in enumerate(changed["copies"]):
            for field in ("copy_path_identity", "copy_handle_identity"):
                row[field]["inode"] = str(5000 + index)
    elif mutation == "copy_destination":
        for row in changed["copies"]:
            row["target_relative_key"] = "new-target/" + row["source_entry_id"] + ".raw"
            row["target_parent_identity"] = directory(6000)
    elif mutation in {"copy_path_ctime", "copy_handle_ctime"}:
        field = "copy_path_identity" if mutation == "copy_path_ctime" else "copy_handle_identity"
        changed["copies"][0][field]["ctime_ns"] = "999"
    elif mutation == "copies_partial":
        changed["copies"] = changed["copies"][:1]
        changed["coverage"]["declared_copies"] = "partial"
    elif mutation == "copies_none":
        changed["copies"] = []
        changed["coverage"]["declared_copies"] = "none"
    elif mutation == "artifacts":
        raw = b"opaque previous manifest bytes"
        changed["artifacts"] = [{"artifact_id": "a00000000", "contract": "personal_preservation_manifest",
            "format_version": 1, "sha256": raw_sha(raw), "byte_size": len(raw)}]
    else:
        changed["links"] = [{"from_id": "f00000000", "field_path": ["explicit", 0], "to_id": "f00000001"}]
    assert capsule.normalize(changed) == changed
    assert wire(original) != wire(changed)
    expected = expected_source(original)
    assert projection.from_capsule(original) == projection.from_capsule(changed) == expected
    assert projection.cross_validate_capsule(expected, changed) is None


def test_optional_link_selection_changes_do_not_decode_old_artifact_bytes():
    old = linked_fixture()[0]
    current = deepcopy(old)
    current["links"] = []
    expected = expected_source(old)
    assert projection.from_capsule(old) == projection.from_capsule(current) == expected
    assert projection.cross_validate_capsule(expected, old) is None


@pytest.mark.parametrize("mutation", ["operation", "copy_source", "copy_sha", "copy_parent_alias",
    "copy_coverage", "artifact_contract", "artifact_future_version", "artifact_sha", "link_missing_id", "link_duplicate"])
def test_excluded_capsule_fields_are_still_validated_before_projection(mutation):
    value = sample(copies=True)
    expected = expected_source(value)
    if mutation == "operation":
        value["operation_id"] = "not an operation UUID"
    elif mutation == "copy_source":
        value["copies"][0]["source_entry_id"] = "f99999999"
    elif mutation == "copy_sha":
        value["copies"][0]["sha256"] = "0" * 64
    elif mutation == "copy_parent_alias":
        value["copies"][1]["target_relative_key"] = "other-parent/copy.raw"
    elif mutation == "copy_coverage":
        value["copies"].pop()
    elif mutation.startswith("artifact_"):
        value["artifacts"] = [{"artifact_id": "a00000000", "contract": "source_preservation_certificate17",
            "format_version": 2, "sha256": "a" * 64, "byte_size": 1}]
        field, invalid = {"artifact_contract": ("contract", "arbitrary_current_certificate"),
                          "artifact_future_version": ("format_version", 3),
                          "artifact_sha": ("sha256", "NOT_A_SHA")}[mutation]
        value["artifacts"][0][field] = invalid
    else:
        link = {"from_id": "f00000000", "field_path": ["sha"], "to_id": "f00000001"}
        value["links"] = [link]
        if mutation == "link_missing_id":
            link["to_id"] = "a00000000"
        else:
            value["links"].append(deepcopy(link))
    first = reject(projection.from_capsule, value)
    assert reject(projection.cross_validate_capsule, expected, value) == first


def mutate_source(value, mutation):
    source = value["files"][0]
    profile = value["source_context"]["profile"]
    if mutation == "file_sha":
        source["sha256"] = "c" * 64
    elif mutation == "file_size":
        source["byte_size"] += 1
        for key in ("source_path_identity", "source_handle_identity"):
            source[key]["byte_size"] = source["byte_size"]
    elif mutation in {"file_device", "file_inode", "file_mtime", "file_mode"}:
        field, changed = {"file_device": ("device", "2"), "file_inode": ("inode", "7000"),
            "file_mtime": ("mtime_ns", "300"), "file_mode": ("mode", 0o100640)}[mutation]
        for key in ("source_path_identity", "source_handle_identity"):
            source[key][field] = changed
    elif mutation in {"path_ctime", "handle_ctime"}:
        field = "source_path_identity" if mutation == "path_ctime" else "source_handle_identity"
        source[field]["ctime_ns"] = "4000"
    elif mutation == "path_spelling":
        source["relative_key"] = ROOT + "/A.raw"
    elif mutation == "directory_identity":
        selected = next(row for row in value["directories"] if row["relative_key"] == ROOT)
        selected["source_identity"] = directory(8000)
        for row in value["files"]:
            if row["relative_key"].rpartition("/")[0] == ROOT:
                row["source_directory_identity"] = directory(8000)
    elif mutation == "empty_directory_identity":
        value["directories"][-1]["source_identity"] = directory(8100)
    elif mutation == "database_path":
        profile["profile_paths"][0] = "/synthetic/data/renamed.db"
    elif mutation == "media_path":
        profile["profile_paths"][1] = "/synthetic/new-media"
        profile["roots"]["media"]["path"] = "/synthetic/new-media"
    elif mutation == "media_identity":
        profile["roots"]["media"]["identity"] = directory(8200)
    elif mutation == "data_identity":
        profile["roots"]["data"]["identity"] = directory(8300)
        value["directories"][0]["source_identity"] = directory(8300)
        for row in value["files"]:
            if "/" not in row["relative_key"]:
                row["source_directory_identity"] = directory(8300)
    elif mutation == "node_null":
        value["source_context"]["node"] = None
    elif mutation == "node_id":
        value["source_context"]["node"]["node_id"] = OTHER_OP
    elif mutation == "partial_listing":
        value["coverage"].update(declared_tree="partial", reason="ENUMERATION_INCOMPLETE")
        value["directories"][0]["listing"] = "partial"
    elif mutation == "unknown_declaration":
        value["coverage"].update(declared_tree="unknown", reason="SELECTION_ONLY")
    else:
        raise AssertionError(mutation)


@pytest.mark.parametrize("mutation", ["file_sha", "file_size", "file_device", "file_inode", "file_mtime", "file_mode",
    "path_ctime", "handle_ctime", "path_spelling", "directory_identity", "empty_directory_identity",
    "database_path", "media_path", "media_identity", "data_identity", "node_null", "node_id",
    "partial_listing", "unknown_declaration"])
def test_each_valid_source_change_changes_bytes_and_cross_binding(mutation):
    original = sample()
    changed = deepcopy(original)
    mutate_source(changed, mutation)
    assert capsule.normalize(changed) == changed
    before, after = expected_source(original), expected_source(changed)
    assert projection.from_capsule(changed) == after
    assert projection.encode(before) != projection.encode(after)
    assert raw_sha(projection.encode(before)) != raw_sha(projection.encode(after))
    reject(projection.cross_validate_capsule, before, changed, code="CAPSULE_MISMATCH")


def test_empty_directory_addition_or_removal_changes_source_without_payload_changes():
    variants = [sample(empty_dirs=dirs) for dirs in ((), (ROOT + "/empty",), (ROOT + "/empty", ROOT + "/empty-two"))]
    source_files = [value["files"] for value in variants]
    assert source_files[0] == source_files[1] == source_files[2]
    encoded = [projection.encode(expected_source(value)) for value in variants]
    assert len(set(encoded)) == 3
    for value in variants:
        assert projection.from_capsule(value) == expected_source(value)


def test_unresolved_position_and_reason_are_source_information():
    variants = [synthetic_capsule(unresolved=[(ROOT + suffix, reason)])[0]
                for suffix, reason in (("/missing", "UNREADABLE"), ("/missing", "UNSAFE_TYPE"),
                                       ("/other", "UNREADABLE"))]
    assert len({projection.encode(expected_source(value)) for value in variants}) == 3
    for value in variants:
        projected = projection.from_capsule(value)
        assert projected["source_coverage"] == {"declared_tree": "partial", "reason": "CONTENT_UNREADABLE"}
        assert projected["unresolved"] == value["unresolved"]


@pytest.mark.parametrize("state", ["partial", "unknown"])
def test_equal_incomplete_projections_remain_declarations_without_success_fields(state):
    value = sample()
    value["coverage"].update(declared_tree=state, reason="SELECTION_ONLY")
    expected = expected_source(value)
    assert projection.from_capsule(value) == expected
    assert projection.cross_validate_capsule(expected, value) is None
    assert projection.decode(projection.encode(expected))["source_coverage"]["declared_tree"] == state
    assert set(expected) == {"format_version", "kind", "scope", "capsule_format_version", "capsule_scope",
        "layout_profile", "layout_source_sha256", "source_context", "source_coverage", "directories", "files", "unresolved"}


def test_null_unknown_context_is_preserved_and_cannot_be_promoted_to_complete():
    value = synthetic_capsule()[0]
    value.update(source_context=None, directories=[])
    value["coverage"].update(declared_tree="unknown", reason="CAPTURE_UNAVAILABLE")
    expected = expected_source(value)
    assert projection.from_capsule(value) == expected
    assert projection.decode(wire(expected)) == expected
    expected["source_coverage"] = {"declared_tree": "complete", "reason": None}
    reject(projection.normalize, expected)


def test_windows_path_and_handle_ctimes_are_separate_source_fields():
    value = sample()
    row = value["files"][0]
    assert row["source_path_identity"]["ctime_ns"] != row["source_handle_identity"]["ctime_ns"]
    result = projection.from_capsule(value)
    assert result["files"][0]["source_path_identity"] == row["source_path_identity"]
    assert result["files"][0]["source_handle_identity"] == row["source_handle_identity"]


@pytest.mark.parametrize("field,invalid", [("format_version", 2), ("format_version", True),
    ("format_version", 1.0), ("capsule_format_version", 1), ("capsule_format_version", True),
    ("kind", "personal_authority_preservation_capsule"), ("scope", "personal-authority-raw-census-v2"),
    ("capsule_scope", "legacy"), ("layout_profile", "namespace2"), ("layout_source_sha256", "0" * 64),
    ("operation_id", OP), ("write_enabled", True)])
def test_projection_wire_identity_and_top_fields_are_closed(field, invalid):
    value = expected_source(sample())
    value[field] = invalid
    code = reject(projection.encode, value)
    assert reject(projection.decode, wire(value)) == code


@pytest.mark.parametrize("field", ["format_version", "kind", "scope", "capsule_format_version", "capsule_scope",
    "layout_profile", "layout_source_sha256", "source_context", "source_coverage", "directories", "files", "unresolved"])
def test_missing_required_projection_field_is_not_defaulted(field):
    value = expected_source(sample())
    del value[field]
    reject(projection.normalize, value)


@pytest.mark.parametrize("location", ["coverage", "context", "profile", "root", "node", "directory", "file", "identity"])
def test_nested_fields_cannot_smuggle_authority_or_copy_facts(location):
    value = expected_source(sample())
    target = {"coverage": value["source_coverage"], "context": value["source_context"],
        "profile": value["source_context"]["profile"], "root": value["source_context"]["profile"]["roots"]["data"],
        "node": value["source_context"]["node"], "directory": value["directories"][0],
        "file": value["files"][0], "identity": value["files"][0]["source_handle_identity"]}[location]
    target["authority_verified"] = True
    reject(projection.normalize, value)


@pytest.mark.parametrize("mutation", ["missing_child", "ghost_child", "duplicate_child", "wrong_parent",
    "file_order", "entry_id", "role", "directory_alias", "file_alias", "file_directory_alias",
    "node_binding", "nlink", "size_bool", "mode_bool", "negative_mtime", "leading_zero_inode"])
def test_source_tree_alias_identity_order_and_numeric_constraints_are_inherited(mutation):
    value = expected_source(sample())
    source = value["files"][0]
    if mutation == "missing_child":
        value["directories"][0]["children"].pop()
    elif mutation == "ghost_child":
        value["directories"][0]["children"].append("f99999999")
    elif mutation == "duplicate_child":
        value["directories"][0]["children"].append(value["directories"][0]["children"][0])
    elif mutation == "wrong_parent":
        source["source_directory_identity"] = directory(9000)
    elif mutation == "file_order":
        value["files"][0], value["files"][1] = value["files"][1], value["files"][0]
    elif mutation == "entry_id":
        source["entry_id"] = "f00000009"
    elif mutation == "role":
        source["role"] = "authority_lock_raw"
    elif mutation == "directory_alias":
        value["directories"][-1]["source_identity"] = deepcopy(value["directories"][0]["source_identity"])
    elif mutation in {"file_alias", "file_directory_alias"}:
        inode = (value["files"][1]["source_path_identity"]["inode"] if mutation == "file_alias"
                 else value["directories"][1]["source_identity"]["inode"])
        for field in ("source_path_identity", "source_handle_identity"):
            source[field]["inode"] = inode
    elif mutation == "node_binding":
        value["source_context"]["node"]["sha256"] = "e" * 64
    elif mutation == "size_bool":
        source["byte_size"] = True
    else:
        field, invalid = {"nlink": ("nlink", 2), "mode_bool": ("mode", True),
            "negative_mtime": ("mtime_ns", "-1"), "leading_zero_inode": ("inode", "01000")}[mutation]
        for key in ("source_path_identity", "source_handle_identity"):
            source[key][field] = invalid
    reject(projection.normalize, value)


@pytest.mark.parametrize("path", ["ordinary/raw", ROOT + "/../escape", ROOT + "/trailing. ",
    ROOT + "/CON", ROOT + "/bad:name", ROOT + "/a\\b", ".reno-personal-authority-future/raw"])
def test_paths_outside_capsule2_selection_or_lexical_contract_are_not_widened(path):
    value = synthetic_capsule([(path, "unclassified_authority_raw", b"raw")])[0]
    reject(projection.from_capsule, value)
    reject(projection.normalize, expected_source(value))


def test_unicode_and_case_spelling_is_preserved_without_case_aliases():
    value = synthetic_capsule([(ROOT + "/绍兴/防水.raw", "unclassified_authority_raw", b"raw")])[0]
    expected = expected_source(value)
    assert projection.encode(expected) == wire(expected)
    assert "绍兴".encode() in projection.encode(expected)
    aliases = synthetic_capsule([(ROOT + "/name", "unclassified_authority_raw", b"a"),
        (ROOT + "/NAME", "unclassified_authority_raw", b"b")])[0]
    reject(projection.normalize, expected_source(aliases))


def test_both_directions_are_detached_plain_metadata():
    original = sample(copies=True)
    expected = expected_source(original)
    actual = projection.from_capsule(original)
    original["directories"][0]["children"].clear()
    assert actual == expected
    normalized = projection.normalize(actual)
    actual["files"][0]["source_path_identity"]["ctime_ns"] = "987"
    assert normalized == expected

    def plain(value):
        assert type(value) in {dict, list, str, int, type(None)}
        if type(value) is dict:
            assert all(type(key) is str for key in value)
            for item in value.values():
                plain(item)
        elif type(value) is list:
            for item in value:
                plain(item)
    plain(normalized)


@pytest.mark.parametrize("kind", ["dict_subclass", "list_subclass", "str_subclass", "int_subclass", "cycle", "generator"])
def test_nonbuiltin_or_cyclic_metadata_is_rejected_without_running_callbacks(kind):
    value = expected_source(sample())
    if kind == "dict_subclass":
        value = type("SyntheticDict", (dict,), {})(value)
    elif kind == "list_subclass":
        value["files"] = type("SyntheticList", (list,), {})(value["files"])
    elif kind == "str_subclass":
        value["kind"] = type("SyntheticStr", (str,), {})(value["kind"])
    elif kind == "int_subclass":
        value["format_version"] = type("SyntheticInt", (int,), {})(1)
    elif kind == "cycle":
        value["files"].append(value)
    else:
        value["files"] = (item for item in value["files"])
    reject(projection.normalize, value)


@pytest.mark.parametrize("invalid", [1.0, math.inf, -math.inf, math.nan, True, None, "1"])
def test_version_does_not_coerce_numbers_or_boolean(invalid):
    value = expected_source(sample())
    value["format_version"] = invalid
    reject(projection.normalize, value)


@pytest.mark.parametrize("raw", [b'{"format_version":1,"format_version":1}', b'{"x":NaN}',
    b'{"x":Infinity}', b'{"x":1e999}', b'{"x":"\\ud800"}', b'\xef\xbb\xbf{}', b'\xff', b'{} trailing', b'[]', b'null'])
def test_decoder_rejects_duplicate_keys_ambiguous_or_nonobject_json(raw):
    reject(projection.decode, raw)


def test_duplicate_key_in_an_otherwise_complete_document_is_rejected():
    expected = expected_source(sample())
    raw = wire(expected)
    repeated = raw.replace(b'"format_version":1', b'"format_version":1,"format_version":1', 1)
    reject(projection.decode, repeated, code="JSON_INVALID")


@pytest.mark.parametrize("alter", [lambda raw: b" " + raw, lambda raw: raw + b"\n",
    lambda raw: json.dumps(json.loads(raw), indent=2).encode()])
def test_projection_wire_must_already_be_canonical(alter):
    reject(projection.decode, alter(wire(expected_source(sample()))), code="JSON_NONCANONICAL")


@pytest.mark.parametrize("kind", ["str", "bytearray", "memoryview", "bytes_subclass"])
def test_decoder_requires_original_exact_bytes(kind):
    raw = wire(expected_source(sample()))
    value = {"str": lambda: raw.decode(), "bytearray": lambda: bytearray(raw),
             "memoryview": lambda: memoryview(raw), "bytes_subclass": lambda: type("SyntheticBytes", (bytes,), {})(raw)}[kind]()
    reject(projection.decode, value)


@pytest.mark.parametrize("mutation", ["missing", "extra", "bool", "zero", "increase"])
def test_limits_remain_the_closed_shrink_only_capsule_budget(mutation):
    limits = dict(LIMITS)
    if mutation == "missing":
        del limits["max_files"]
    elif mutation == "extra":
        limits["max_seconds"] = 30
    elif mutation == "bool":
        limits["max_files"] = True
    elif mutation == "zero":
        limits["max_files"] = 0
    else:
        limits["max_files"] += 1
    expected = expected_source(sample())
    for function, argument in ((projection.normalize, expected), (projection.encode, expected),
                               (projection.decode, wire(expected)), (projection.from_capsule, sample())):
        reject(function, argument, limits=limits)


@pytest.mark.parametrize("field,maximum", [("max_files", 1), ("max_directories", 1), ("max_entries", 1),
    ("max_json_bytes", 100), ("max_total_bytes", 1), ("max_json_depth", 2),
    ("max_json_nodes", 10), ("max_json_string_bytes", 8), ("max_json_integer_digits", 2)])
def test_source_limits_fail_without_truncating_or_changing_the_input(field, maximum):
    value = expected_source(sample())
    before = deepcopy(value)
    reject(projection.normalize, value, limits={**LIMITS, field: maximum}, code="LIMIT")
    assert value == before


def test_exact_projection_wire_byte_budget_has_no_invented_capsule_overhead():
    value = expected_source(synthetic_capsule([(ROOT + "/绍兴/防水.raw", "unclassified_authority_raw", b"raw")])[0])
    raw = wire(value)
    exact = {**LIMITS, "max_json_bytes": len(raw)}
    assert projection.normalize(value, limits=exact) == value
    assert projection.encode(value, limits=exact) == raw
    assert projection.decode(raw, limits=exact) == value
    for function, argument in ((projection.normalize, value), (projection.encode, value), (projection.decode, raw)):
        reject(function, argument, limits={**exact, "max_json_bytes": len(raw) - 1}, code="LIMIT")


def test_from_capsule_applies_budgets_to_optional_artifacts_even_when_projection_is_small():
    value = synthetic_capsule(artifacts=[("personal_preservation_manifest", 1, b"large ignored evidence")])[0]
    expected = expected_source(value)
    assert projection.normalize(expected, limits={**LIMITS, "max_total_bytes": 1}) == expected
    reject(projection.from_capsule, value, limits={**LIMITS, "max_total_bytes": 1}, code="LIMIT")
    reject(projection.cross_validate_capsule, expected, value, limits={**LIMITS, "max_total_bytes": 1}, code="LIMIT")


def test_public_default_limit_dict_is_not_a_mutable_validation_ceiling(monkeypatch):
    expected = expected_source(sample())
    with monkeypatch.context() as patch:
        patch.setitem(projection.DEFAULT_LIMITS, "max_files", 1)
        assert projection.normalize(expected) == expected
        assert capsule.DEFAULT_LIMITS["max_files"] == LIMITS["max_files"]
        reject(projection.normalize, expected, limits={**LIMITS, "max_files": LIMITS["max_files"] + 1})


def test_invalid_projection_does_not_get_replaced_by_a_valid_capsule():
    value = sample()
    invalid = expected_source(value)
    invalid["files"] = []
    reject(projection.cross_validate_capsule, invalid, value)


def test_capsule2_and_projection1_wires_are_not_interchangeable():
    value = sample()
    expected = expected_source(value)
    reject(projection.decode, wire(value))
    reject(projection.from_capsule, expected)
    with pytest.raises(capsule.AuthorityPreservationContractError):
        capsule.decode(wire(expected))


def test_consistently_omitted_source_is_not_a_pure_codec_capture_proof():
    full = sample()
    empty = synthetic_capsule()[0]
    assert projection.from_capsule(full) != projection.from_capsule(empty)
    # Both are internally consistent declarations; neither API claims disk truth.
    assert projection.cross_validate_capsule(expected_source(full), full) is None
    assert projection.cross_validate_capsule(expected_source(empty), empty) is None


def test_pure_entry_points_do_not_read_files_stat_paths_or_open_sql(monkeypatch):
    value = sample(copies=True)
    expected = expected_source(value)
    raw = wire(expected)
    def forbidden(*_args, **_options):
        pytest.fail("Pure source projection attempted I/O")
    with monkeypatch.context() as patch:
        patch.setattr(builtins, "open", forbidden)
        patch.setattr(os, "stat", forbidden)
        patch.setattr(os, "lstat", forbidden)
        patch.setattr(sqlite3, "connect", forbidden)
        assert projection.from_capsule(value) == expected
        assert projection.normalize(expected) == expected
        assert projection.encode(expected) == raw
        assert projection.decode(raw) == expected
        assert projection.cross_validate_capsule(expected, value) is None
