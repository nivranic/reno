"""Actual pending capsules consumed as Home2 and copied private components.

The completed fixture uses the original worker, Cloud SQLite/ASGI receipt and
sole State CAS. Detached copied maps below come from those captured raw bytes;
their private before/after images never become current State/history records.
These component tests do not claim a completed outer preservation bundle.
"""
from copy import deepcopy
import hashlib
import json
import sqlite3

import pytest

from reno import home_authority_pending_contract17 as contract
from reno import home_recovery_schema as restoration_schema
from reno import source_preservation_bundle as bundles
from reno import storage_admin, sync_access
from reno import storage_home_roots as home_files
from reno import storage_legacy_pending_state17 as pending
from reno import storage_lifecycle as lifecycle
from reno import storage_source_authority_roots as home
from reno import sync_state
from tests.test_storage_legacy_pending_state17 import completed, original_inputs, profile, real_cycle
from tests.test_home_recovery_migrations import root_ids


def capture_home(value, *, version):
    binding = value.store.read()["binding"]
    with lifecycle.shared(value.profile), home.capture_locked(value.profile,
            binding=binding, format_version=version) as observed:
        assert not observed.unknown
        observed.verify_for_commit()
        observed.verify_source()
        return observed.public_summary()


def copied_component(value):
    projected = capture_home(value, version=2)
    with lifecycle.shared(value.profile), pending.capture_locked(value.profile) as observed:
        files = observed.public_projection()["files"]
        records, images = observed.records, observed.state_images
        assert not observed.unknown
        assert projected["legacy_pending_state"] == observed.public_summary()
        observed.verify_source()
    copies = {("DATA", pending.DIRECTORY + "/" + record.name): record.raw for record in records}
    # Only the consumer's actual Home summary field is selected. This is not
    # a manufactured certificate, outer manifest or restoration permission.
    document = {"permanent_witnesses": {"home_authority": projected["summary"]}}
    return projected, document, copies, files, images


def original_absence(profile, *, version):
    with lifecycle.shared(profile), home.capture_absence_locked(profile,
            format_version=version) as observed:
        assert not observed.unknown
        observed.verify_source()
        return observed.public_summary()


def semantic_inputs(value):
    projected, _document, _copies, _files, images = copied_component(value)
    with lifecycle.shared(value.profile), home_files.capture_locked(value.profile) as observed:
        bundle = observed.bundle
        roots = root_ids(value.profile)
        observed.verify_source()
    with storage_admin.connection(value.profile) as connection:
        connection.execute("BEGIN")
        source = sync_access.identity(connection)
        provenance = {restoration_schema.TABLE: [dict(row) for row in connection.execute(
            "SELECT operation_id,result_sha256 FROM " + restoration_schema.TABLE + " ORDER BY operation_id")]}
    arguments = {"profile": value.profile, "root_ids": roots, "member_id": "actual_pending_home",
        "source_identity": {key: source[key] for key in ("library_id", "node_id")},
        "provenance": provenance, "legacy_pending_images": images}
    return bundle, projected, arguments


def semantic_parse(bundle, projected, arguments):
    return home_files.parse_authority_links(bundle, projected, **arguments)


def assert_closed(result):
    assert not result.blockers
    assert any(row.section == "authority" and row.state == "present" for row in result.source_bindings)


def test_actual_home2_retains_home1_ledger_and_separate_complete_pending_component(completed):
    value, intent, receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    old = capture_home(value, version=1)
    current = capture_home(value, version=2)
    assert contract.normalize_projection(old, expected_version=1) == old
    assert contract.normalize_projection(current, expected_version=2) == current
    for key in contract.SUMMARY1_FIELDS - {"format_version"}:
        assert current["summary"][key] == old["summary"][key]
    assert current["references"] == old["references"]
    assert current["unknown"] == old["unknown"] == []
    component = current["legacy_pending_state"]
    assert current["summary"]["legacy_pending_state"] == component["summary"]
    assert component["summary"]["file_count"] == 2
    assert component["summary"]["capsule_count"] == component["summary"]["applied_count"] == 1
    selected = {row["record_kind"]: row for row in component["references"]
        if row["record_id"] == intent["record"]["record_id"]}
    assert selected["recovery_intent"]["record_sha256"] == intent["ref"]["record_sha256"]
    assert selected["recovery_receipt"]["record_sha256"] == receipt["ref"]["record_sha256"]
    public = json.dumps(current, sort_keys=True)
    assert "state_json" not in public and "before_json" not in public and "after_json" not in public
    assert value.core["binding"]["target_url"] not in public
    assert original_inputs(value, path, marker) == before


def test_copied_component_recovers_exact_full_before_after_without_current_state_alias(completed, monkeypatch):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    projected, document, copies, files, expected_images = copied_component(value)
    original = json.loads(path.read_bytes())
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Copied component opened SQL"))
        scoped.setattr(pending.safe, "_Directory", lambda *a, **kw: pytest.fail("Copied component opened live files"))
        scoped.setattr(sync_state, "_write", lambda *a, **kw: pytest.fail("Copied component rewrote State/history"))
        component, images = bundles._copied_pending_state(value.profile, document, copies, entries=files)
    assert component == projected["legacy_pending_state"]
    assert images == expected_images
    states = {image["phase"]: json.loads(image["state_json"]) for image in images}
    assert states == {"before": json.loads(original["before_json"]), "after": json.loads(original["after_json"])}
    assert all(set(state) == set(value.store.read()) for state in states.values())
    assert states["before"]["source_authority"] is None
    assert states["before"]["source_recovery_pending"] is not None
    assert states["after"]["source_authority"] is not None
    assert states["after"]["source_recovery_pending"] is None
    assert original_inputs(value, path, marker) == before


@pytest.mark.parametrize("damage", ["missing_capsule", "missing_marker", "changed_capsule", "unknown_name", "nested_name"])
def test_copied_component_requires_the_original_exact_raw_file_selection(completed, damage):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    _projected, document, copies, files, _images = copied_component(value)
    capsule_key, marker_key = ("DATA", pending.DIRECTORY + "/" + path.name), (
        "DATA", pending.DIRECTORY + "/" + marker.name)
    if damage == "missing_capsule":
        copies.pop(capsule_key)
    elif damage == "missing_marker":
        copies.pop(marker_key)
    elif damage == "changed_capsule":
        copies[capsule_key] += b" "
    elif damage == "unknown_name":
        copies["DATA", pending.DIRECTORY + "/unknown.json"] = b"{}"
    else:
        copies["DATA", pending.DIRECTORY + "/nested/" + path.name] = copies[capsule_key]
    with pytest.raises(bundles.PreservationBundleError) as raised:
        bundles._copied_pending_state(value.profile, document, copies, entries=files)
    assert raised.value.code == "SOURCE_BUNDLE_PENDING_FILE_SET_CHANGED"
    assert original_inputs(value, path, marker) == before


def test_original_home1_cannot_adopt_actual_new_pending_files_by_omitting_the_component(completed):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    _projected, _document, copies, files, _images = copied_component(value)
    old = capture_home(value, version=1)
    original_summary = deepcopy(old["summary"])
    document = {"permanent_witnesses": {"home_authority": old["summary"]}}
    with pytest.raises(bundles.PreservationBundleError) as raised:
        bundles._copied_pending_state(value.profile, document, copies, entries=files)
    assert raised.value.code == "SOURCE_BUNDLE_PENDING_UNCOVERED_BY_HOME1"
    assert old["summary"] == original_summary and "legacy_pending_state" not in old
    assert bundles._copied_pending_state(value.profile, document, {}, entries=[]) == (None, ())
    assert original_inputs(value, path, marker) == before


@pytest.mark.parametrize("namespace_present", [False, True])
def test_actual_home2_absent_and_empty_component_reconstruct_from_original_copies(profile, namespace_present, monkeypatch):
    directory = profile.database.parent / pending.DIRECTORY
    if namespace_present:
        directory.mkdir(mode=0o700)
    old = original_absence(profile, version=1)
    current = original_absence(profile, version=2)
    assert old["summary"]["absence_sha256"] == hashlib.sha256(pending._json({
        "format_version": 1, "kind": "home_authority", "presence": "absent"})).hexdigest()
    assert current["summary"]["absence_sha256"] != old["summary"]["absence_sha256"]
    assert current["legacy_pending_state"]["summary"]["presence"] == (
        "present" if namespace_present else "absent")
    document = {"permanent_witnesses": {"home_authority": current["summary"]}}
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Copied absence opened SQL"))
        scoped.setattr(pending.safe, "_Directory", lambda *a, **kw: pytest.fail("Copied absence used current namespace"))
        component, images = bundles._copied_pending_state(profile, document, {}, entries=[])
    assert component == current["legacy_pending_state"] and images == ()
    assert directory.exists() is namespace_present
    assert original_absence(profile, version=1) == old


@pytest.mark.parametrize("damage", ["missing_component", "wrong_owner", "changed_file_set"])
def test_copied_actual_component_cannot_use_downgraded_or_foreign_original_metadata(completed, damage):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    _projected, document, copies, files, _images = copied_component(value)
    document = deepcopy(document)
    summary = document["permanent_witnesses"]["home_authority"]
    if damage == "missing_component":
        summary.pop("legacy_pending_state")
    elif damage == "wrong_owner":
        summary["legacy_pending_state"]["owner_profile_sha256"] = "f" * 64
    else:
        summary["legacy_pending_state"]["file_set_sha256"] = "f" * 64
    with pytest.raises((bundles.PreservationBundleError, contract.HomeAuthorityPendingContractError)):
        bundles._copied_pending_state(value.profile, document, copies, entries=files)
    assert original_inputs(value, path, marker) == before


def test_actual_pending_semantics_use_two_independent_private_bindings_without_io_or_retirement(completed, monkeypatch):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    bundle, projected, arguments = semantic_inputs(value)
    assert all(row.kind != "legacy_pending_state" for row in bundle.records)
    with monkeypatch.context() as scoped:
        scoped.setattr(sqlite3, "connect", lambda *a, **kw: pytest.fail("Pure semantic parser opened SQL"))
        scoped.setattr(pending.safe, "_Directory", lambda *a, **kw: pytest.fail("Pure semantic parser opened files"))
        scoped.setattr(home_files, "_retire_frozen", lambda *a, **kw: pytest.fail("Permanent capsule entered pointer retirement"))
        result = semantic_parse(bundle, projected, arguments)
    assert_closed(result)
    captured = [row for row in result.source_bindings if row.section == "legacy_pending_state" and row.state == "captured"]
    assert len(captured) == 2 and len({row.source_id for row in captured}) == 2
    assert len([row for row in result.source_bindings if row.section == "legacy_pending_state" and row.state == "valid"]) == 2
    assert value.core["binding"]["target_url"] not in json.dumps(result.public_summary())
    assert original_inputs(value, path, marker) == before


@pytest.mark.parametrize("damage", ["missing_all", "missing_after", "list_alias"])
def test_actual_home2_rejects_missing_complete_private_images_and_wrong_container(completed, damage):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    bundle, projected, arguments = semantic_inputs(value)
    assert_closed(semantic_parse(bundle, projected, arguments))
    images = arguments["legacy_pending_images"]
    arguments = {**arguments, "legacy_pending_images": (
        () if damage == "missing_all" else
        tuple(image for image in images if image["phase"] == "before") if damage == "missing_after" else list(images))}
    result = semantic_parse(bundle, projected, arguments)
    assert "HOME_LEGACY_PENDING_STATE_UNPROVEN" in {row.code for row in result.blockers}
    assert any(row.section == "authority" and row.state == "unknown" for row in result.source_bindings)
    assert original_inputs(value, path, marker) == before


@pytest.mark.parametrize("kind", ["origins", "requests", "receipts"])
def test_actual_capsule_references_require_each_original_committed_ledger_row(completed, kind):
    value, intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    bundle, projected, arguments = semantic_inputs(value)
    assert_closed(semantic_parse(bundle, projected, arguments))
    projected = deepcopy(projected)
    identifier = value.origin["record_id"] if kind == "origins" else intent["record"]["record_id"]
    selected = [row for row in projected["references"] if (row["kind"], row["record_id"]) == (kind, identifier)]
    assert len(selected) == 1 and selected[0]["state"] == "committed"
    projected["references"].remove(selected[0])
    result = semantic_parse(bundle, projected, arguments)
    assert "HOME_LEGACY_PENDING_STATE_UNPROVEN" in {row.code for row in result.blockers}
    assert any(row.section == "authority" and row.state == "unknown" for row in result.source_bindings)
    assert original_inputs(value, path, marker) == before


def test_actual_home1_semantics_keep_original_scope_and_refuse_new_component_images(completed):
    value, _intent, _receipt, path, marker = completed
    before = original_inputs(value, path, marker)
    bundle, _projected, arguments = semantic_inputs(value)
    old = capture_home(value, version=1)
    assert_closed(semantic_parse(bundle, old, {**arguments, "legacy_pending_images": ()}))
    result = semantic_parse(bundle, old, arguments)
    assert "HOME_LEGACY_PENDING_STATE_UNPROVEN" in {row.code for row in result.blockers}
    assert not any(row.section == "legacy_pending_state" and row.state == "captured" for row in result.source_bindings)
    assert original_inputs(value, path, marker) == before
