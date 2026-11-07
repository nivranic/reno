"""Synthetic metadata and caller bytes only: no capture, disk, SQL, or authority.

These fixtures do not claim that their paths, identities, or bytes were observed
by a producer. Internally consistent omissions or fabricated buffers are outside
the evidence available to a pure codec.
"""
from copy import deepcopy
import builtins
import hashlib
import json
import os

import pytest

from reno import personal_authority_preservation_contract as contract
from reno import personal_preservation_contract as legacy


OP = "11111111-1111-4111-8111-111111111111"
NODE = "22222222-2222-4222-8222-222222222222"
ROOT = ".reno-personal-authority-namespace-v2"
PIN = "7965e1b9d8e4b3a3336e88ec6545f4448ad6cb208f16e653cb81310b01f71aeb"
ERROR = contract.AuthorityPreservationContractError
LIMITS = {
    "max_files": 100000, "max_directories": 1000000, "max_entries": 64000000,
    "max_artifacts": 100000, "max_links": 64000000,
    "max_json_bytes": 134217728, "max_total_bytes": 68719476736,
    "max_raw_json_bytes": 134217728, "max_json_depth": 64,
    "max_json_nodes": 4000000, "max_json_string_bytes": 134217728,
    "max_json_integer_digits": 4300,
}


def raw_sha(raw):
    return hashlib.sha256(raw).hexdigest()


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def directory(inode):
    return {"device": "1", "inode": str(inode)}


def file_identity(inode, size, *, ctime="40"):
    return {**directory(inode), "byte_size": size, "mtime_ns": "30",
            "ctime_ns": ctime, "mode": 0o100600, "nlink": 1}


def synthetic_capsule(specs=(), *, empty_dirs=(), unresolved=(), copies=False,
                      node=False, artifacts=()):
    """Assemble explicitly synthetic declarations, without product helpers."""
    directories = {"", *empty_dirs}
    for path, *_ in [*specs, *((path, reason) for path, reason in unresolved),
                     *((path,) for path in empty_dirs)]:
        parent = path.rpartition("/")[0]
        while parent:
            directories.add(parent)
            parent = parent.rpartition("/")[0]
    directory_rows = [
        {"entry_id": f"d{index:08d}", "relative_key": path,
         "source_identity": directory(10 + index), "listing": "complete", "children": []}
        for index, path in enumerate(sorted(directories, key=lambda key: key.encode("utf-8")))
    ]
    by_directory = {row["relative_key"]: row for row in directory_rows}
    file_rows, source_buffers = [], {}
    for index, (path, role, raw) in enumerate(sorted(specs, key=lambda item: item[0].encode("utf-8"))):
        entry_id = f"f{index:08d}"
        observed = file_identity(1000 + index, len(raw))
        file_rows.append({
            "entry_id": entry_id, "relative_key": path, "role": role,
            "sha256": raw_sha(raw), "byte_size": len(raw),
            "source_directory_identity": deepcopy(by_directory[path.rpartition("/")[0]]["source_identity"]),
            "source_path_identity": observed,
            "source_handle_identity": {**observed, "ctime_ns": "41"},
        })
        source_buffers[entry_id] = raw
    unresolved_rows = [
        {"entry_id": f"u{index:08d}", "relative_key": path, "reason": reason}
        for index, (path, reason) in enumerate(sorted(unresolved, key=lambda item: item[0].encode("utf-8")))
    ]
    children = {}
    for row in [*directory_rows[1:], *file_rows, *unresolved_rows]:
        parent, _, name = row["relative_key"].rpartition("/")
        children.setdefault(parent, []).append((name, row["entry_id"]))
    for row in directory_rows:
        row["children"] = [entry for _, entry in sorted(children.get(row["relative_key"], []),
                                                       key=lambda item: item[0].encode("utf-8"))]
    context = {
        "profile": {
            "profile_paths": ["/synthetic/data/reno.db", "/synthetic/media",
                              "/synthetic/reports", "/synthetic/checkpoints"],
            "roots": {
                "data": {"path": "/synthetic/data", "identity": directory(10)},
                "media": {"path": "/synthetic/media", "identity": directory(20)},
                "reports": {"path": "/synthetic/reports", "identity": directory(21)},
                "checkpoints": {"path": "/synthetic/checkpoints", "identity": directory(22)},
            },
        }, "node": None,
    }
    if node:
        declared = next(row for row in file_rows if row["relative_key"] == "node-identity.json")
        context["node"] = {
            "node_id": NODE, "sha256": declared["sha256"],
            "path_identity": deepcopy(declared["source_path_identity"]),
            "handle_identity": deepcopy(declared["source_handle_identity"]),
        }
    copy_rows, copy_buffers = [], {}
    if copies:
        for index, source in enumerate(file_rows):
            observed = file_identity(3000 + index, source["byte_size"], ctime="50")
            copy_rows.append({
                "source_entry_id": source["entry_id"],
                "target_relative_key": f"payload/{source['entry_id']}.raw",
                "target_parent_identity": directory(2000), "copy_path_identity": observed,
                "copy_handle_identity": {**observed, "ctime_ns": "51"},
                "sha256": source["sha256"], "byte_size": source["byte_size"],
            })
            # Shared immutable objects are permitted; no second-fd fact is claimed.
            copy_buffers[source["entry_id"]] = source_buffers[source["entry_id"]]
    artifact_rows, artifact_buffers = [], {}
    for index, (name, version, raw) in enumerate(artifacts):
        entry_id = f"a{index:08d}"
        artifact_rows.append({"artifact_id": entry_id, "contract": name, "format_version": version,
                              "sha256": raw_sha(raw), "byte_size": len(raw)})
        artifact_buffers[entry_id] = raw
    value = {
        "format_version": 2, "kind": "personal_authority_preservation_capsule",
        "scope": "personal-authority-raw-census-v2", "operation_id": OP,
        "layout_profile": "namespace2-store2-extension3-enrollment1-r729",
        "layout_source_sha256": PIN, "source_context": context,
        "coverage": {"declared_tree": "partial" if unresolved else "complete",
                     "declared_copies": "complete" if copy_rows else "none",
                     "reason": "CONTENT_UNREADABLE" if unresolved else None},
        "directories": directory_rows, "files": file_rows, "unresolved": unresolved_rows,
        "copies": copy_rows, "artifacts": artifact_rows, "links": [],
    }
    return value, source_buffers, copy_buffers, artifact_buffers


def full_synthetic_capsule(*, copies=True):
    specs = [
        ("node-identity.json", "node_identity_raw", (' { "instance_id": "' + NODE + '" }\n').encode()),
        (ROOT + "-reservation.json", "namespace_anchor_raw", b"synthetic reservation"),
        (ROOT + "-header.json", "namespace_anchor_raw", b"synthetic header"),
        (ROOT + "-reserve-" + OP + ".stage", "namespace_staging_raw", b"synthetic reserve stage"),
        (ROOT + "-header-" + OP + ".stage", "namespace_staging_raw", b"synthetic header stage"),
        (ROOT + "/authority.lock", "authority_lock_raw", b"synthetic lock bytes; no OS lock"),
        (ROOT + "/facts/fence_1.json", "primitive_fact_raw", b"synthetic fact"),
        (ROOT + "/unknown.bin", "unclassified_authority_raw", b"\x00\xffsynthetic opaque"),
        (ROOT + "/zero.raw", "unclassified_authority_raw", b""),
        (".reno-personal-authority-namespace-v1/old.raw", "unclassified_authority_raw", b"synthetic legacy tree"),
    ]
    for name in ("init-reservation.json", "declared-binding.json", "files-seal.json", "head.json"):
        specs.append((ROOT + "/" + name, "primitive_metadata_raw", b"synthetic primitive metadata"))
    for role, names in (
        ("store2_metadata_raw", ("store-init-reservation.json", "store-header.json")),
        ("extension3_metadata_raw", ("store-extension-reservation-v3.json", "store-extension-header-v3.json")),
        ("enrollment1_metadata_raw", ("store-enrollment-reservation-v1.json", "store-enrollment-header-v1.json")),
    ):
        specs.extend((ROOT + "/" + name, role, b"synthetic metadata") for name in names)
    for journal, role in (("journal-v1", "store2_journal_raw"),
                          ("journal-v3", "extension3_journal_raw"),
                          ("journal-enrollment-v1", "enrollment1_journal_raw")):
        for name in ("records/envelope.json", "payloads/body.raw", "operations/node",
                     "ordinals/p-000", "successors/publication", "segments/receipt", ".stage-synthetic"):
            specs.append((f"{ROOT}/{journal}/{name}", role, b"synthetic " + name.encode()))
    return synthetic_capsule(specs, empty_dirs=(ROOT + "/empty",), copies=copies, node=True)


def cross(fixture, *, limits=None):
    value, sources, copies, artifacts = fixture
    return contract.cross_validate_originals(value, source_bytes=sources, copy_bytes=copies,
                                             artifact_bytes=artifacts, limits=limits)


def rejected(function, *args, code=None, **kwargs):
    with pytest.raises(ERROR) as caught:
        function(*args, **kwargs)
    assert caught.value.code.startswith("AUTHORITY_PRESERVATION2_")
    if code:
        assert caught.value.code == "AUTHORITY_PRESERVATION2_" + code
    return caught.value.code


def test_complete_synthetic_raw_tree_roundtrips_without_claiming_capture():
    fixture = full_synthetic_capsule()
    value, sources, copies, _ = fixture
    assert contract.normalize(value) == value
    assert contract.encode(value) == wire(value)
    assert contract.decode(wire(value)) == value
    assert cross(fixture) is None
    assert any(row["relative_key"] == ROOT + "/empty" and row["children"] == []
               for row in value["directories"])
    assert {row["role"] for row in value["files"]} >= {
        "store2_journal_raw", "extension3_journal_raw", "enrollment1_journal_raw",
        "namespace_staging_raw", "authority_lock_raw", "unclassified_authority_raw",
    }
    for row in value["files"]:
        assert contract.verify_original_bytes(sources[row["entry_id"]], expected_sha256=row["sha256"],
                                              expected_byte_size=row["byte_size"]) is None
        assert contract.verify_original_bytes(copies[row["entry_id"]], expected_sha256=row["sha256"],
                                              expected_byte_size=row["byte_size"]) is None


def test_empty_selected_tree_and_metadata_only_large_raw_reference_are_representable():
    empty = synthetic_capsule()
    assert cross(empty) is None
    value, *_ = synthetic_capsule([(ROOT + "/large.raw", "unclassified_authority_raw", b"synthetic")])
    source = value["files"][0]
    source["byte_size"] = 257 * 1024 ** 2
    for field in ("source_path_identity", "source_handle_identity"):
        source[field]["byte_size"] = source["byte_size"]
    assert contract.decode(contract.encode(value)) == value


def test_metadata_is_detached_in_both_directions_and_has_only_plain_values():
    original = full_synthetic_capsule()[0]
    normalized = contract.normalize(original)
    expected = deepcopy(original)
    original["directories"][0]["children"].clear()
    assert normalized == expected
    normalized["files"][0]["source_path_identity"]["ctime_ns"] = "12345"
    assert original["files"][0]["source_path_identity"]["ctime_ns"] == "40"

    def plain(value):
        assert type(value) in (dict, list, str, int, type(None))
        if type(value) is dict:
            for key, item in value.items():
                assert type(key) is str
                plain(item)
        elif type(value) is list:
            for item in value:
                plain(item)
    plain(normalized)


@pytest.mark.parametrize("field,value", [
    ("format_version", 1), ("format_version", True), ("format_version", 2.0),
    ("kind", "personal_preservation_manifest"),
    ("scope", "personal-facts-and-mirror-raw-v1"), ("operation_id", "{" + OP + "}"),
    ("layout_profile", "namespace2"), ("layout_source_sha256", "0" * 64),
    ("write_enabled", True),
])
def test_new_wire_identity_is_closed_and_encode_decode_errors_agree(field, value):
    document = synthetic_capsule()[0]
    document[field] = value
    first = rejected(contract.encode, document)
    assert rejected(contract.decode, wire(document)) == first


@pytest.mark.parametrize("location", ["coverage", "directory", "file", "identity", "profile", "node", "copy"])
def test_nested_objects_reject_invented_authority_fields(location):
    value = full_synthetic_capsule()[0]
    target = {"coverage": value["coverage"], "directory": value["directories"][0],
              "file": value["files"][0], "identity": value["files"][0]["source_path_identity"],
              "profile": value["source_context"]["profile"], "node": value["source_context"]["node"],
              "copy": value["copies"][0]}[location]
    target["live_authority"] = True
    rejected(contract.normalize, value)


@pytest.mark.parametrize("mutation", ["remove_empty", "unindexed", "ghost", "duplicate_child", "wrong_parent",
                                      "orphan", "file_ancestor", "type_collision", "file_order", "directory_id"])
def test_only_declared_tree_inconsistencies_are_rejected(mutation):
    value = full_synthetic_capsule()[0]
    if mutation == "remove_empty":
        value = synthetic_capsule(empty_dirs=(ROOT + "/empty",))[0]
        removed = value["directories"].pop()
        assert removed["relative_key"] == ROOT + "/empty"
        assert removed["entry_id"] in value["directories"][1]["children"]
    elif mutation == "unindexed":
        source = value["files"][0]
        parent = next(row for row in value["directories"] if source["entry_id"] in row["children"])
        parent["children"].remove(source["entry_id"])
    elif mutation == "ghost":
        value["directories"][0]["children"].append("f99999999")
    elif mutation == "duplicate_child":
        value["directories"][0]["children"].append(value["directories"][0]["children"][0])
    elif mutation == "wrong_parent":
        value["files"][0]["source_directory_identity"] = directory(9999)
    elif mutation == "orphan":
        value["files"][0]["relative_key"] = ROOT + "/missing-parent/raw.bin"
    elif mutation == "file_ancestor":
        value["files"][1]["relative_key"] = value["files"][0]["relative_key"] + "/child.raw"
    elif mutation == "type_collision":
        value["files"][0]["relative_key"] = value["directories"][1]["relative_key"]
    elif mutation == "file_order":
        value["files"][0], value["files"][1] = value["files"][1], value["files"][0]
    else:
        value["directories"][0]["entry_id"] = "d00000001"
    rejected(contract.normalize, value)


def test_internally_consistent_caller_omission_is_not_a_capture_detection_test():
    # These separately declared sets are indistinguishable from genuine captures
    # to this API. Accepting both makes the evidence boundary explicit.
    full = synthetic_capsule([(ROOT + "/unknown.raw", "unclassified_authority_raw", b"synthetic")])
    caller_omitted_everything = synthetic_capsule()
    assert cross(full) is None
    assert cross(caller_omitted_everything) is None


@pytest.mark.parametrize("mutation", ["file_file", "directory_directory", "file_directory", "copy_source",
                                      "copy_directory", "copy_copy", "same_parent_changed_identity",
                                      "different_parent_same_identity", "parent_is_source_file", "parent_is_copy_file"])
def test_physical_identity_claims_cannot_alias_within_the_declared_sets(mutation):
    value = full_synthetic_capsule()[0]
    files, copies, directories = value["files"], value["copies"], value["directories"]

    def file_inode(row, prefix, inode):
        for suffix in ("path_identity", "handle_identity"):
            row[prefix + suffix]["inode"] = inode

    if mutation == "file_file":
        file_inode(files[1], "source_", files[0]["source_path_identity"]["inode"])
    elif mutation == "directory_directory":
        directories[2]["source_identity"] = deepcopy(directories[1]["source_identity"])
        for source in files:
            if source["relative_key"].rpartition("/")[0] == directories[2]["relative_key"]:
                source["source_directory_identity"] = deepcopy(directories[2]["source_identity"])
    elif mutation == "file_directory":
        file_inode(files[0], "source_", directories[1]["source_identity"]["inode"])
    elif mutation == "copy_source":
        file_inode(copies[0], "copy_", files[-1]["source_path_identity"]["inode"])
    elif mutation == "copy_directory":
        file_inode(copies[0], "copy_", directories[1]["source_identity"]["inode"])
    elif mutation == "copy_copy":
        file_inode(copies[1], "copy_", copies[0]["copy_path_identity"]["inode"])
    elif mutation == "same_parent_changed_identity":
        copies[1]["target_parent_identity"] = directory(9999)
    elif mutation == "different_parent_same_identity":
        copies[1]["target_relative_key"] = "different-parent/copy.raw"
    elif mutation == "parent_is_source_file":
        for copy in copies:
            copy["target_parent_identity"] = directory(files[0]["source_path_identity"]["inode"])
    else:
        for copy in copies:
            copy["target_parent_identity"] = directory(copies[0]["copy_path_identity"]["inode"])
    rejected(contract.normalize, value)


def test_coincident_profile_roots_and_distinct_windows_ctimes_are_preserved():
    fixture = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"synthetic")], copies=True)
    value = fixture[0]
    profile = value["source_context"]["profile"]
    profile["profile_paths"] = ["E:/Synthetic/reno.db", "E:/Synthetic", "E:/Synthetic", "E:/Synthetic"]
    for root in profile["roots"].values():
        root.update(path="E:/Synthetic", identity=directory(10))
    result = contract.normalize(value)
    assert result == value
    assert result["files"][0]["source_path_identity"]["ctime_ns"] == "40"
    assert result["files"][0]["source_handle_identity"]["ctime_ns"] == "41"
    assert result["copies"][0]["copy_handle_identity"]["ctime_ns"] == "51"
    assert cross(fixture) is None


@pytest.mark.parametrize("mutation", ["same_path_different_identity", "different_path_same_identity", "database_parent"])
def test_profile_roots_cannot_contradict_their_path_identity_mapping(mutation):
    value = synthetic_capsule()[0]
    profile = value["source_context"]["profile"]
    if mutation == "same_path_different_identity":
        profile["profile_paths"][1] = "/synthetic/data"
        profile["roots"]["media"]["path"] = "/synthetic/data"
    elif mutation == "different_path_same_identity":
        profile["roots"]["media"]["identity"] = directory(10)
    else:
        profile["profile_paths"][0] = "/synthetic/other/reno.db"
    rejected(contract.normalize, value)


@pytest.mark.parametrize("mutation", ["duplicate_target", "target_alias", "target_file_ancestor", "source_sha", "source_size",
                                      "path_handle_inode", "path_handle_mtime"])
def test_copy_declarations_preserve_separate_targets_and_exact_source_relationships(mutation):
    value = full_synthetic_capsule()[0]
    first, second = value["copies"][:2]
    if mutation == "duplicate_target":
        second["target_relative_key"] = first["target_relative_key"]
    elif mutation == "target_alias":
        second["target_relative_key"] = first["target_relative_key"].upper()
    elif mutation == "target_file_ancestor":
        second["target_relative_key"] = first["target_relative_key"] + "/child.raw"
        second["target_parent_identity"] = directory(8000)
    elif mutation == "source_sha":
        first["sha256"] = "0" * 64
    elif mutation == "source_size":
        first["byte_size"] += 1
        first["copy_path_identity"]["byte_size"] += 1
        first["copy_handle_identity"]["byte_size"] += 1
    elif mutation == "path_handle_inode":
        first["copy_handle_identity"]["inode"] = "9000"
    else:
        first["copy_handle_identity"]["mtime_ns"] = "123"
    rejected(contract.normalize, value)


@pytest.mark.parametrize("field,bad", [("device", "01"), ("inode", "0"), ("inode", "+1"),
                                      ("mtime_ns", "-1"), ("ctime_ns", "1" * 41),
                                      ("mode", 0o040700), ("mode", True), ("nlink", 2),
                                      ("byte_size", True)])
def test_file_identity_uses_strict_regular_single_link_facts(field, bad):
    value = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"x")])[0]
    for side in ("source_path_identity", "source_handle_identity"):
        value["files"][0][side][field] = bad
    rejected(contract.normalize, value)


@pytest.mark.parametrize("mutation", ["unresolved", "listing", "copy_subset", "empty_copy_complete",
                                      "context_null", "reason", "partial_no_entry"])
def test_coverage_is_a_closed_declaration_with_internal_requirements(mutation):
    value = full_synthetic_capsule()[0]
    if mutation == "unresolved":
        value = synthetic_capsule(unresolved=[(ROOT + "/missing", "UNREADABLE")])[0]
        value["coverage"].update(declared_tree="complete", reason=None)
    elif mutation == "listing":
        value["directories"][0]["listing"] = "partial"
    elif mutation == "copy_subset":
        value["copies"].pop()
    elif mutation == "empty_copy_complete":
        value = synthetic_capsule()[0]
        value["coverage"]["declared_copies"] = "complete"
    elif mutation == "context_null":
        value = synthetic_capsule()[0]
        value.update(source_context=None, directories=[])
    elif mutation == "reason":
        value["coverage"]["reason"] = "CAPTURE_UNAVAILABLE"
    else:
        value = synthetic_capsule()[0]
        value["coverage"].update(declared_tree="partial", reason="ENUMERATION_INCOMPLETE")
    rejected(contract.normalize, value)


def test_partial_unknown_and_null_context_prefixes_remain_plain_declarations():
    partial = synthetic_capsule(unresolved=[(ROOT + "/unreadable", "UNREADABLE")])
    assert cross(partial) is None
    value = full_synthetic_capsule()[0]
    value["copies"].pop()
    value["coverage"].update(declared_tree="partial", declared_copies="partial", reason="SELECTION_ONLY")
    value["directories"][0]["listing"] = "partial"
    assert contract.normalize(value) == value
    value = synthetic_capsule()[0]
    value.update(source_context=None, directories=[])
    value["coverage"].update(declared_tree="unknown", reason="CAPTURE_UNAVAILABLE")
    assert contract.normalize(value) == value


@pytest.mark.parametrize("path", ["NODE-IDENTITY.JSON", ".RENO-PERSONAL-AUTHORITY-NAMESPACE-v2/unknown",
                                  ".reno-personal-authority-namespace-extra/raw"])
def test_selected_case_variants_and_unknown_names_are_preserved_as_opaque(path):
    fixture = synthetic_capsule([(path, "unclassified_authority_raw", b"synthetic unknown")])
    assert cross(fixture) is None
    assert contract.decode(contract.encode(fixture[0]))["files"][0]["relative_key"] == path


@pytest.mark.parametrize("path,role", [("reno.db", "unclassified_authority_raw"),
                                       ("NODE-IDENTITY.JSON", "node_identity_raw"),
                                       (ROOT + "/unknown", "primitive_fact_raw")])
def test_unselected_paths_or_invented_known_roles_are_rejected(path, role):
    rejected(contract.normalize, synthetic_capsule([(path, role, b"synthetic")])[0])


def test_case_aliases_are_rejected_but_unicode_normalization_is_not_performed():
    aliases = synthetic_capsule([(ROOT + "/A.raw", "unclassified_authority_raw", b"a"),
                                  (ROOT + "/a.raw", "unclassified_authority_raw", b"b")])
    rejected(contract.normalize, aliases[0], code="PATH_ALIAS")
    distinct = synthetic_capsule([(ROOT + "/é.raw", "unclassified_authority_raw", b"composed"),
                                 (ROOT + "/e\u0301.raw", "unclassified_authority_raw", b"decomposed")])
    assert cross(distinct) is None
    assert b"\\u" not in contract.encode(distinct[0])


@pytest.mark.parametrize("path", ["../escape", ROOT + "//raw", ROOT + "/../raw", ROOT + "/./raw",
                                  ROOT + "/raw:ads", "C:/raw", "//host/share/raw", ROOT + "\\raw",
                                  ROOT + "/CON", ROOT + "/lpt1.txt", ROOT + "/tail.", ROOT + "/tail ",
                                  ROOT + "/bad\x00raw", ROOT + "/bad\nraw", ROOT + "/" + "界" * 86,
                                  ROOT + "/" + "/".join("a" for _ in range(130))])
def test_relative_paths_refuse_unsafe_or_unrepresentable_names(path):
    value = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"synthetic")])[0]
    value["files"][0]["relative_key"] = path
    rejected(contract.normalize, value)


@pytest.mark.parametrize("character", list('<>"|?*'))
@pytest.mark.parametrize("kind", ["relative", "absolute"])
def test_windows_illegal_component_characters_are_rejected_on_every_platform(character, kind):
    value = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"synthetic")])[0]
    if kind == "relative":
        value["files"][0]["relative_key"] = ROOT + "/bad" + character + "name"
    else:
        path = "/synthetic/bad" + character + "name"
        value["source_context"]["profile"]["profile_paths"][1] = path
        value["source_context"]["profile"]["roots"]["media"]["path"] = path
    rejected(contract.normalize, value)


@pytest.mark.parametrize("base", ["/synthetic", "E:/Synthetic", "\\\\synthetic-host\\share"])
def test_absolute_profile_forms_are_lexical_and_keep_original_spelling(base):
    value = synthetic_capsule()[0]
    profile = value["source_context"]["profile"]
    profile["profile_paths"] = [base + "/data/reno.db", base + "/media", base + "/reports", base + "/checkpoints"]
    for kind, root in profile["roots"].items():
        root["path"] = base + "/" + kind
    assert contract.normalize(value) == value


@pytest.mark.parametrize("path", ["E:relative", "\\\\?\\E:\\synthetic", "\\\\.\\E:\\synthetic",
                                  "/synthetic/../escape", "//synthetic/data", "E:/data:ads"])
def test_unsafe_absolute_profile_path_is_rejected_without_resolution(path):
    value = synthetic_capsule()[0]
    value["source_context"]["profile"]["roots"]["data"]["path"] = path
    rejected(contract.normalize, value)


def test_deep_radix_directory_tree_is_representable_without_runtime_limits():
    path = ROOT + "/journal-enrollment-v1/operations/" + "/".join("a" for _ in range(64)) + "/node"
    fixture = synthetic_capsule([(path, "enrollment1_journal_raw", b"synthetic deep node")])
    assert cross(fixture) is None


@pytest.mark.parametrize("raw", [b"{broken", b"", b"\xffnot UTF-8", b'{"instance_id":false}'])
def test_null_node_preserves_bad_or_uninterpreted_node_raw(raw):
    fixture = synthetic_capsule([("node-identity.json", "node_identity_raw", raw)])
    assert cross(fixture) is None
    assert contract.encode(fixture[0]) == wire(fixture[0])


@pytest.mark.parametrize("raw", [b"{broken", b'{"instance_id":"wrong"}',
                                 ('{"instance_id":"' + NODE + '","extra":1}').encode()])
def test_nonnull_node_interpretation_requires_its_actual_supplied_json(raw):
    fixture = synthetic_capsule([("node-identity.json", "node_identity_raw", raw)], node=True)
    assert contract.normalize(fixture[0]) == fixture[0]
    rejected(cross, fixture)


@pytest.mark.parametrize("mutation", ["sha", "inode", "ctime", "missing_node"])
def test_nonnull_node_metadata_must_match_the_exact_source_file(mutation):
    value = synthetic_capsule([("node-identity.json", "node_identity_raw", wire({"instance_id": NODE}))], node=True)[0]
    node = value["source_context"]["node"]
    if mutation == "sha":
        node["sha256"] = "0" * 64
    elif mutation == "inode":
        node["handle_identity"]["inode"] = "9999"
    elif mutation == "ctime":
        node["path_identity"]["ctime_ns"] = "123"
    else:
        row = next(row for row in value["files"] if row["relative_key"] == "node-identity.json")
        row["relative_key"] = "NODE-IDENTITY.JSON"
        row["role"] = "unclassified_authority_raw"
    rejected(contract.normalize, value)


@pytest.mark.parametrize("group", [1, 2, 3], ids=["sources", "copies", "artifacts"])
@pytest.mark.parametrize("mutation", ["missing", "extra", "digest", "nonbytes"])
def test_complete_map_requires_exact_buffers_for_each_declared_entry(group, mutation):
    fixture = synthetic_capsule([(ROOT + "/published", "unclassified_authority_raw", b"synthetic published")],
                                copies=True, artifacts=[("personal_preservation_manifest", 1, b"synthetic old bytes")])
    buffers = fixture[group]
    key = next(iter(buffers))
    if mutation == "missing":
        buffers.pop(key)
    elif mutation == "extra":
        buffers["f99999999"] = b"extra"
    elif mutation == "digest":
        buffers[key] = b"X" + buffers[key][1:]
    else:
        buffers[key] = bytearray(buffers[key])
    rejected(cross, fixture)


@pytest.mark.parametrize("raw,size,sha", [(b"changed", 7, raw_sha(b"original")),
                                        (b"x", 2, raw_sha(b"x")),
                                        (b"x", True, raw_sha(b"x")),
                                        (bytearray(b"x"), 1, raw_sha(b"x")),
                                        ("x", 1, raw_sha(b"x")),
                                        (b"x", 1, "A" * 64)])
def test_single_raw_entry_verification_is_strict(raw, size, sha):
    rejected(contract.verify_original_bytes, raw, expected_byte_size=size, expected_sha256=sha)


def test_raw_maps_and_buffers_must_be_exact_builtins_without_callbacks():
    fixture = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"synthetic")])
    mapping = type("SyntheticMapping", (dict,), {})(fixture[1])
    rejected(contract.cross_validate_originals, fixture[0], source_bytes=mapping, copy_bytes={}, artifact_bytes={})
    raw = type("SyntheticBytes", (bytes,), {})(b"synthetic")
    rejected(contract.verify_original_bytes, raw, expected_byte_size=len(raw), expected_sha256=raw_sha(raw))
    invoked = []

    def callback():
        invoked.append(True)
        return fixture[1]

    rejected(contract.cross_validate_originals, fixture[0], source_bytes=callback, copy_bytes={}, artifact_bytes={})
    assert invoked == []


def linked_fixture(raw=None, *, path=None):
    target = b"synthetic target original bytes"
    if raw is None:
        raw = (' {"hashes":["' + raw_sha(target) + '"],"number":1.25,"flag":true,"nil":null} \n').encode()
    fixture = synthetic_capsule(artifacts=[
        ("home_preservation_checkpoint17", 2, raw),
        ("source_preservation_certificate17", 2, target),
    ])
    fixture[0]["links"] = [{"from_id": "a00000000", "field_path": ["hashes", 0] if path is None else path,
                            "to_id": "a00000001"}]
    return fixture


def test_links_read_original_noncanonical_json_without_reencoding_old_artifacts():
    fixture = linked_fixture()
    assert cross(fixture) is None
    original = fixture[3]["a00000000"]
    fixture[3]["a00000000"] = wire(json.loads(original))
    assert fixture[3]["a00000000"] != original
    rejected(cross, fixture, code="RAW_DIGEST_CHANGED")


def test_multiple_links_from_one_original_and_all_declared_artifact_types():
    target = b"synthetic target original bytes"
    raw = wire({"first": raw_sha(target), "second": [raw_sha(target)]})
    fixture = linked_fixture(raw, path=["first"])
    fixture[0]["links"].append({"from_id": "a00000000", "field_path": ["second", 0], "to_id": "a00000001"})
    assert cross(fixture) is None
    for name, version in (("personal_preservation_manifest", 1), ("home_private_bundle_manifest", 1)):
        assert cross(synthetic_capsule(artifacts=[(name, version, b"synthetic opaque original")])) is None


def test_old_raw_json_with_escaped_multiline_text_keeps_its_original_semantics():
    target = b"synthetic target original bytes"
    raw = b"  " + wire({"hashes": [raw_sha(target)], "history": "synthetic first line\nsecond line\tindented"}) + b"\n"
    fixture = linked_fixture(raw)
    before = deepcopy(fixture[0])
    assert cross(fixture) is None
    assert fixture[0] == before
    assert fixture[3]["a00000000"] == raw
    assert json.loads(raw)["history"] == "synthetic first line\nsecond line\tindented"


@pytest.mark.parametrize("mutation", ["wrong_version", "bool_version", "unknown_contract", "duplicate", "missing_field"])
def test_opaque_artifact_reference_types_are_versioned_and_closed(mutation):
    value = linked_fixture()[0]
    if mutation == "wrong_version":
        value["artifacts"][0]["format_version"] = 1
    elif mutation == "bool_version":
        value["artifacts"][0]["format_version"] = True
    elif mutation == "unknown_contract":
        value["artifacts"][0]["contract"] = "future_authority_certificate"
    elif mutation == "duplicate":
        duplicate = deepcopy(value["artifacts"][0])
        duplicate["artifact_id"] = "a00000002"
        value["artifacts"].append(duplicate)
    else:
        value["artifacts"][0].pop("sha256")
    rejected(contract.normalize, value)


@pytest.mark.parametrize("mutation", ["missing_target", "self", "duplicate", "bool", "negative", "out_of_range",
                                      "wrong_container", "empty", "too_large_index", "wrong_leaf"])
def test_links_validate_only_explicit_reference_relationships(mutation):
    fixture = linked_fixture()
    link = fixture[0]["links"][0]
    if mutation == "missing_target":
        link["to_id"] = "a99999999"
    elif mutation == "self":
        link["to_id"] = link["from_id"]
    elif mutation == "duplicate":
        fixture[0]["links"].append(deepcopy(link))
    elif mutation == "bool":
        link["field_path"] = ["hashes", True]
    elif mutation == "negative":
        link["field_path"] = ["hashes", -1]
    elif mutation == "out_of_range":
        link["field_path"] = ["hashes", 9]
    elif mutation == "wrong_container":
        link["field_path"] = ["hashes", "0"]
    elif mutation == "empty":
        link["field_path"] = []
    elif mutation == "too_large_index":
        link["field_path"] = ["hashes", 2 ** 31]
    else:
        link["field_path"] = ["number"]
    rejected(cross, fixture)


@pytest.mark.parametrize("raw", [b'{"x":1,"x":2}', b'{"x":{"a":1,"a":2}}', b'{"x":NaN}',
                                 b'{"x":Infinity}', b'{"x":1e999}', b'\xef\xbb\xbf{}', b'\xff',
                                 b'{"x":"\\ud800"}', b'{} trailing', b'[]'])
def test_capsule_decode_refuses_ambiguous_invalid_or_nonobject_json(raw):
    rejected(contract.decode, raw)


@pytest.mark.parametrize("alter", [lambda raw: b" " + raw, lambda raw: raw + b"\n",
                                   lambda raw: json.dumps(json.loads(raw), indent=2).encode()])
def test_new_capsule_wire_must_already_be_canonical(alter):
    raw = wire(synthetic_capsule()[0])
    rejected(contract.decode, alter(raw), code="JSON_NONCANONICAL")


@pytest.mark.parametrize("raw", [b'{"hashes":[],"x":NaN}', b'{"hashes":[],"x":Infinity}',
                                 b'{"hashes":[],"x":1e999}', b'{"hashes":[],"x":1,"x":2}',
                                 b'{"hashes":[],"x":"\\ud800"}', b'\xef\xbb\xbf{}', b'\xff', b'{} trailing'])
def test_link_json_has_strict_raw_parsing_separate_from_metadata(raw):
    fixture = linked_fixture(raw)
    assert contract.normalize(fixture[0]) == fixture[0]
    rejected(cross, fixture)
    fixture[0]["links"] = []
    assert cross(fixture) is None  # Still preserved as uninterpreted original bytes.


@pytest.mark.parametrize("kind", ["dict_subclass", "list_subclass", "str_subclass", "int_subclass", "cycle", "float", "generator"])
def test_metadata_rejects_nonexact_or_cyclic_values(kind):
    value = synthetic_capsule()[0]
    if kind == "dict_subclass":
        value = type("SyntheticDict", (dict,), {})(value)
    elif kind == "list_subclass":
        value["files"] = type("SyntheticList", (list,), {})([])
    elif kind == "str_subclass":
        value["kind"] = type("SyntheticStr", (str,), {})(value["kind"])
    elif kind == "int_subclass":
        value["format_version"] = type("SyntheticInt", (int,), {})(2)
    elif kind == "cycle":
        value["files"].append(value)
    elif kind == "float":
        value["format_version"] = 2.0
    else:
        value["files"] = (item for item in [])
    rejected(contract.normalize, value)


@pytest.mark.parametrize("mutation", ["missing", "extra", "bool", "zero", "increase"])
def test_limits_are_a_complete_strict_shrink_only_contract(mutation):
    limits = dict(LIMITS)
    if mutation == "missing":
        limits.pop("max_files")
    elif mutation == "extra":
        limits["max_seconds"] = 30
    elif mutation == "bool":
        limits["max_files"] = True
    elif mutation == "zero":
        limits["max_files"] = 0
    else:
        limits["max_files"] += 1
    rejected(contract.normalize, synthetic_capsule()[0], limits=limits)


@pytest.mark.parametrize("key,maximum", [("max_files", 1), ("max_directories", 1), ("max_entries", 1),
                                        ("max_json_bytes", 100), ("max_total_bytes", 1),
                                        ("max_json_depth", 2), ("max_json_nodes", 10),
                                        ("max_json_string_bytes", 8)])
def test_metadata_limits_fail_explicitly_without_truncating(key, maximum):
    value = full_synthetic_capsule()[0]
    before = deepcopy(value)
    rejected(contract.normalize, value, limits={**LIMITS, key: maximum}, code="LIMIT")
    assert value == before


def test_source_and_copy_byte_totals_are_separate_budgets():
    fixture = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"12345678")], copies=True)
    assert cross(fixture, limits={**LIMITS, "max_total_bytes": 8}) is None
    rejected(cross, fixture, limits={**LIMITS, "max_total_bytes": 7}, code="LIMIT")


def test_canonical_wire_byte_budget_counts_unicode_and_escaped_windows_paths_exactly():
    value = synthetic_capsule([(ROOT + "/绍兴/防水.raw", "unclassified_authority_raw", b"synthetic")])[0]
    profile = value["source_context"]["profile"]
    base = r"E:\Synthetic\资料"
    profile["profile_paths"] = [base + r"\data\reno.db", base + r"\media",
                                base + r"\reports", base + r"\checkpoints"]
    for kind, root in profile["roots"].items():
        root["path"] = base + "\\" + kind
    expected_wire = wire(value)
    exact = {**LIMITS, "max_json_bytes": len(expected_wire)}
    assert contract.normalize(value, limits=exact) == value
    assert contract.encode(value, limits=exact) == expected_wire
    assert contract.decode(expected_wire, limits=exact) == value
    short = {**LIMITS, "max_json_bytes": len(expected_wire) - 1}
    for function, argument in ((contract.normalize, value), (contract.encode, value),
                               (contract.decode, expected_wire)):
        rejected(function, argument, limits=short, code="LIMIT")


def test_artifacts_count_toward_the_source_total_and_collection_limits():
    fixture = synthetic_capsule([(ROOT + "/raw", "unclassified_authority_raw", b"12345678")],
                                artifacts=[("personal_preservation_manifest", 1, b"x")])
    rejected(contract.normalize, fixture[0], limits={**LIMITS, "max_total_bytes": 8}, code="LIMIT")
    links = linked_fixture()
    rejected(contract.normalize, links[0], limits={**LIMITS, "max_artifacts": 1}, code="LIMIT")
    links[0]["links"].append({"from_id": "a00000000", "field_path": ["other"], "to_id": "a00000001"})
    rejected(contract.normalize, links[0], limits={**LIMITS, "max_links": 1}, code="LIMIT")


@pytest.mark.parametrize("key,maximum,extra", [
    ("max_raw_json_bytes", 100, '"padding":"' + "x" * 200 + '"'),
    ("max_json_integer_digits", 8, '"large":123456789'),
    ("max_json_string_bytes", 8192, '"large":"' + "x" * 16384 + '"'),
    ("max_json_depth", 16, '"deep":' + "[" * 20 + "0" + "]" * 20),
    ("max_json_nodes", 4096, '"many":[' + ",".join("0" for _ in range(5000)) + ']'),
])
def test_raw_link_parser_has_bounded_resource_use(key, maximum, extra):
    target_sha = raw_sha(b"synthetic target original bytes")
    raw = ('{"hashes":["' + target_sha + '"],' + extra + '}').encode()
    fixture = linked_fixture(raw)
    limits = {**LIMITS, key: maximum}
    assert contract.normalize(fixture[0], limits=limits) == fixture[0]
    rejected(cross, fixture, limits=limits, code="LIMIT")


def test_raw_json_integer_token_limit_is_controlled_before_python_conversion():
    raw = b'{"hashes":[],"large":' + b"1" * 4301 + b"}"
    fixture = linked_fixture(raw)
    assert contract.normalize(fixture[0]) == fixture[0]
    rejected(cross, fixture, code="LIMIT")


def test_pure_entry_points_do_not_open_files_or_stat_declared_paths(monkeypatch):
    fixture = full_synthetic_capsule()
    value = fixture[0]

    def forbidden(*args, **kwargs):
        pytest.fail("Pure codec attempted filesystem I/O")

    with monkeypatch.context() as scoped:
        scoped.setattr(builtins, "open", forbidden)
        scoped.setattr(os, "stat", forbidden)
        scoped.setattr(os, "lstat", forbidden)
        assert contract.normalize(value) == value
        assert contract.decode(contract.encode(value)) == value
        assert cross(fixture) is None


def test_released_personal_format1_rejects_new_capsule_fields():
    # Independently synthetic format1 request, using only its released pure API.
    request = {
        "format_version": 1, "kind": "personal_preservation_request", "operation_id": OP,
        "bundle_id": legacy.bundle_id(OP),
        "source_profile": {"database": "/synthetic/data/reno.db", "media": "/synthetic/media",
                           "reports": "/synthetic/reports", "checkpoints": "/synthetic/checkpoints"},
        "source_root_identity": directory(10), "target_parent_identity": directory(20),
        "target": "/synthetic/data/" + legacy.DIRECTORY + "/" + legacy.bundle_id(OP),
        "scope": "personal-facts-and-mirror-raw-v1", "limits": dict(legacy.DEFAULT_LIMITS),
    }
    assert legacy.validate_request(request) == request
    request["authority_capsule"] = synthetic_capsule()[0]
    with pytest.raises(legacy.Error) as caught:
        legacy.validate_request(request)
    assert caught.value.code == "PERSONAL_PRESERVATION_FIELDS_INVALID"
