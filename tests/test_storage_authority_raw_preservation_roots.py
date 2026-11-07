"""Permanent raw-package exclusions using real isolated files and GC paths.

Package controls are deliberately opaque/corrupt here. Physical protection is
independent of package validity, and does not grant external retention claims.
"""
import json
import os
import uuid

import pytest

from reno import personal_authority_files as locks
from reno import personal_authority_preservation_source as source
from reno import storage_gc as gc, storage_lifecycle as lifecycle, storage_roots as roots
from reno import storage_personal_authority_roots as authority
from tests.test_storage_personal_authority_roots import (
    authority_env, _apply, _capture, _data, _identity, _key, _op,
    _plan_without_execute, _private_tree_at, _tree, _write,
)
from tests.test_storage_roots_group import libraries, enroll, registered, scan, statuses


DIRECTORY = ".reno-authority-raw-preservation-v1"
FAMILY = ".reno-authority-raw-preservation"


@pytest.mark.parametrize("name", [DIRECTORY, FAMILY + "-v2", ".RENO-AUTHORITY-RAW-PRESERVATION-v99", FAMILY + "-failed"])
def test_whole_raw_family_is_protected_without_parsing_receipt_or_requiring_authority_lock(authority_env, name):
    tree = _private_tree_at(_data(authority_env), name, lock=False)
    (tree / "empty-operation").mkdir()
    payload = _write(tree / "op/payload/partial.raw", b"partial original bytes")
    receipt = _write(tree / "op/receipt.json", b"not JSON or a completed receipt")
    marker = _write(tree / "authority.lock", b"ordinary bytes, not an authority marker")
    stage = _write(tree / ".write-abandoned.tmp", b"retained control fragment")
    with _capture(authority_env) as held:
        assert held.complete and not held.inputs.blockers
        assert not held.inputs.references and not held.inputs.selectors
        assert {_key(item) for item in (payload, receipt, marker, stage)} <= {
            _key(item) for item in held.protected_paths}
        assert {_identity(item) for item in (tree, payload, receipt, marker, stage)} <= held.protected_inodes
        assert _key(tree / "empty-operation") in {_key(item) for item in held.protected_directories}
        binding = held.source_binding["tree"]
        assert binding["algorithm_version"] == 2
        assert binding["selection"]["raw_preservation_prefix"] == FAMILY
        assert "not JSON" not in json.dumps(binding)
        held.verify_source()
    assert receipt.read_bytes() == b"not JSON or a completed receipt"


def test_both_fixed_versions_are_statically_protected_before_package_creation(authority_env):
    profile = authority_env["profile"]
    assert authority.RAW_PRESERVATION_DIRECTORY == DIRECTORY
    assert authority.RAW_PRESERVATION2_DIRECTORY == FAMILY + "-v2"
    exact, directories = roots._protection([profile])
    for name in (DIRECTORY, FAMILY + "-v2"):
        path = _data(authority_env) / name
        assert not path.exists()
        assert _key(path) in directories
        assert roots._protected_path(path, exact, directories)
        assert roots._protected_path(path / "op/runs/02/.write-partial.tmp", exact, directories)


def test_raw_target_does_not_acquire_a_personal_authority_lock(authority_env, monkeypatch):
    tree = _private_tree_at(_data(authority_env), DIRECTORY, lock=False)
    _write(tree / "authority.lock", b"invalid authority marker but ordinary target bytes")
    def forbidden(*_args, **_options):
        pytest.fail("Raw target was incorrectly treated as a live authority facility")
    monkeypatch.setattr(locks._ExistingLock, "__enter__", forbidden)
    with _capture(authority_env) as held:
        assert held.complete


def test_raw_target_does_not_satisfy_a_missing_original_authority_lock(authority_env):
    _tree(authority_env, lock=False)
    target = _private_tree_at(_data(authority_env), DIRECTORY, lock=False)
    _write(target / "authority.lock", b"\x00")
    with _capture(authority_env) as held:
        assert not held.complete
        assert any(item.code.endswith("LOCK_MISSING") for item in held.inputs.blockers)


@pytest.mark.parametrize("directory", [False, True])
def test_absence_capture_rejects_late_raw_target_appearance(authority_env, directory):
    with pytest.raises(authority.PersonalAuthorityRootError, match="SOURCE_CHANGED"):
        with _capture(authority_env):
            path = _data(authority_env) / (DIRECTORY if directory else FAMILY + "-failed.json")
            if directory:
                path.mkdir()
            else:
                _write(path, b"new partial evidence")


def test_source_census_does_not_recursively_capture_raw_backup_targets(authority_env):
    profile = authority_env["profile"]
    tree = _private_tree_at(_data(authority_env), DIRECTORY, lock=False)
    _write(tree / "payload/copy.raw", b"a backup is not a new original source")
    _write(_data(authority_env) / "node-identity.json", b"bad node preserved as raw")
    with lifecycle.exclusive(profile):
        with source.capture_raw_locked(profile, operation_id=str(uuid.uuid4())) as capture:
            metadata = source.source_metadata(capture)
            assert [row["relative_key"] for row in metadata["capsule"]["files"]] == ["node-identity.json"]
            assert metadata["node_observation"]["state"] == "invalid"
    assert source.closed_observation(capture)["source_file_count"] == 1


def test_reserved_original_path_is_protected_even_when_only_staged_bytes_exist(authority_env):
    profile = authority_env["profile"]
    staged = _write(profile.media / "staged-payload", b"actual separately held payload")
    original = _data(authority_env) / DIRECTORY / "operation/payload/file.raw"
    assert not original.exists()
    with lifecycle.exclusive(profile), authority.capture_group_locked([profile]) as group:
        with pytest.raises(authority.PersonalAuthorityRootError, match="PROTECTED"):
            authority.assert_unprotected(group, profile=profile, path=original, identity=_identity(staged))


def test_actual_registered_artifact_under_raw_target_is_not_deleted(libraries, tmp_path):
    tree = _private_tree_at(tmp_path / "a/data", DIRECTORY, lock=False)
    checkpoints = tree / "payload"
    checkpoints.mkdir()
    env = libraries(checkpoints=checkpoints)
    enroll(env)
    candidate, item = registered(env, "checkpoint")
    before = candidate.read_bytes()
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] == "protected"
    receipt = _apply(env)
    assert receipt["selected_count"] == 0 and receipt["deleted_bytes"] == 0
    assert candidate.read_bytes() == before


def test_corrupt_receipt_is_preserved_without_claiming_unrelated_artifacts(libraries):
    env = libraries()
    enroll(env)
    tree = _private_tree_at(_data(env), DIRECTORY, lock=False)
    corrupt = _write(tree / "operation/receipt.json", b"corrupt, opaque raw-only receipt")
    candidate, item = registered(env)
    result = _apply(env)
    assert result["state"] == "completed" and result["deleted_bytes"] == item["byte_size"]
    assert not candidate.exists()
    assert corrupt.read_bytes() == b"corrupt, opaque raw-only receipt"


def test_raw_target_alias_in_another_member_blocks_the_whole_gc_group(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    candidate, item = registered(a, "local_report")
    target = _private_tree_at(_data(b), DIRECTORY, lock=False)
    alias = target / "incomplete-payload.raw"
    os.link(candidate, alias)
    result = scan(a)
    assert statuses(result)[item["artifact_id"]] in {"blocked", "protected"}
    assert not result.eligible_artifacts
    outcome = _apply(a)
    assert outcome["deleted_bytes"] == 0
    assert candidate.exists() and alias.read_bytes() == candidate.read_bytes()


def test_late_target_before_native_delete_invalidates_the_held_group(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _item = registered(env)
    original = gc._unlink
    effects = []
    def introduce_target(*args, **options):
        effects.append(_write(_data(env) / DIRECTORY / "op/.write-failed.tmp", b"new target evidence"))
        return original(*args, **options)
    monkeypatch.setattr(gc, "_unlink", introduce_target)
    result = _apply(env)
    assert effects and result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert candidate.exists() and effects[0].read_bytes() == b"new target evidence"


def test_resume_preserves_original_request_when_a_target_alias_appears(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _item = registered(env)
    operation_id, request = _plan_without_execute(env, monkeypatch)
    target = _private_tree_at(_data(env), DIRECTORY, lock=False)
    alias = target / "failed-copy.raw"
    os.link(candidate, alias)
    outcome = gc.resume([env["profile"]], operation_id=operation_id)
    assert outcome["state"] == "blocked" and outcome["deleted_bytes"] == 0
    assert outcome["selected_count"] == 1 and candidate.exists() and alias.exists()
    assert _op(env, operation_id)["request_json"] == request


@pytest.mark.skipif(os.name == "nt", reason="POSIX raw-target alias of an already staged GC payload")
def test_staged_recovery_retains_payload_with_a_new_raw_target_alias(libraries):
    from tests.test_storage_gc import _crash_child
    env = libraries()
    enroll(env)
    candidate, item = registered(env)
    operation_id = str(uuid.uuid4())
    _crash_child(env, operation_id, "staged")
    request = _op(env, operation_id)["request_json"]
    payload = (getattr(env["profile"], roots._ROOT_ROLE[item["namespace"]]) /
               gc.STAGE_DIRECTORY / operation_id / item["artifact_id"] / "payload")
    assert not candidate.exists() and payload.read_bytes() == b"unused media"
    target = _private_tree_at(_data(env), DIRECTORY, lock=False)
    alias = target / "partial-copy.raw"
    os.link(payload, alias)
    outcome = gc.resume([env["profile"]], operation_id=operation_id)
    assert outcome["state"] == "blocked" and outcome["deleted_bytes"] == 0
    assert not candidate.exists() and payload.read_bytes() == alias.read_bytes() == b"unused media"
    assert _op(env, operation_id)["request_json"] == request
