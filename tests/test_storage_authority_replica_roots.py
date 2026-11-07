"""Replica-family evidence is permanently excluded from real isolated GC.

All packages are synthetic opaque bytes. No replica producer or restoration
is invoked; physical protection does not depend on a valid replica receipt.
"""
import json
import os
import uuid

import pytest

from reno import personal_authority_files as locks
from reno import personal_authority_preservation_source as source
from reno import storage_gc as gc, storage_lifecycle as lifecycle, storage_roots as roots
from reno import storage_personal_authority_roots as authority
from tests.test_storage_authority_raw_preservation_roots import (
    authority_env, libraries, enroll, registered, scan, statuses,
    _apply, _capture, _data, _identity, _key, _op,
    _plan_without_execute, _private_tree_at, _write,
)


DIRECTORY = ".reno-authority-raw-preservation-replicas-v1"
FAMILY = ".reno-authority-raw-preservation"


def test_replica_destination_is_protected_before_it_exists_and_before_any_payload_is_installed(authority_env):
    profile = authority_env["profile"]
    target = _data(authority_env) / DIRECTORY
    original = target / "operation/payload/retained.raw"
    assert authority.REPLICA_DIRECTORY == DIRECTORY
    assert not target.exists()
    exact, directories = roots._protection([profile])
    assert _key(target) in directories
    assert roots._protected_path(target, exact, directories)
    assert roots._protected_path(original, exact, directories)
    staged = _write(profile.media / "actual-staged-payload", b"original reserved destination bytes")
    with lifecycle.exclusive(profile), authority.capture_group_locked([profile]) as group:
        with pytest.raises(authority.PersonalAuthorityRootError, match="PROTECTED"):
            authority.assert_unprotected(group, profile=profile, path=original, identity=_identity(staged))
    assert not target.exists() and staged.read_bytes() == b"original reserved destination bytes"


def test_whole_replica_tree_retains_empty_directories_bad_receipt_and_failed_bytes_without_authority_lock(authority_env, monkeypatch):
    tree = _private_tree_at(_data(authority_env), DIRECTORY, lock=False)
    empty = tree / "empty-operation"
    empty.mkdir()
    raw = {
        "operation/receipt.json": b"\xff not a replica receipt",
        "operation/payload/partial.raw": b"retained incomplete copy\x00",
        "operation/.write-failed.tmp": b"unfinished publication",
        "operation/failures/failed.json": b'{"partial":',
        "authority.lock": b"historical payload, not a live lock marker",
    }
    paths = {key: _write(tree / key, value) for key, value in raw.items()}
    def forbidden(*_args, **_kwargs):
        pytest.fail("a replica evidence tree was treated as a live authority lock")
    monkeypatch.setattr(locks._ExistingLock, "__enter__", forbidden)
    with _capture(authority_env) as capture:
        assert capture.complete and not capture.inputs.blockers
        assert not capture.inputs.references and not capture.inputs.selectors
        assert {_key(path) for path in paths.values()} <= {_key(path) for path in capture.protected_paths}
        assert {_identity(path) for path in paths.values()} | {_identity(tree), _identity(empty)} <= capture.protected_inodes
        expected_dirs = {tree, empty, tree / "operation", tree / "operation/payload", tree / "operation/failures"}
        assert {_key(path) for path in expected_dirs} <= {_key(path) for path in capture.protected_directories}
        binding = capture.source_binding["tree"]
        assert binding["algorithm_version"] == 2
        assert binding["selection"]["raw_preservation_prefix"] == FAMILY
        assert "not a replica receipt" not in json.dumps(binding)
        capture.verify_source()
    assert empty.is_dir() and list(empty.iterdir()) == []
    assert {key: path.read_bytes() for key, path in paths.items()} == raw


def test_registered_artifact_physically_inside_replica_family_is_never_selected_or_deleted(libraries, tmp_path):
    tree = _private_tree_at(tmp_path / "a/data", DIRECTORY, lock=False)
    checkpoints = tree / "copied-checkpoints"
    checkpoints.mkdir()
    env = libraries(checkpoints=checkpoints)
    enroll(env)
    candidate, item = registered(env, "checkpoint")
    before = candidate.read_bytes()
    observed = scan(env)
    assert statuses(observed)[item["artifact_id"]] == "protected"
    assert not observed.eligible_artifacts
    outcome = _apply(env)
    assert outcome["selected_count"] == 0 and outcome["deleted_bytes"] == 0
    assert candidate.read_bytes() == before


def test_opaque_replica_receipt_does_not_invent_references_to_unrelated_registered_artifacts(libraries):
    env = libraries()
    enroll(env)
    tree = _private_tree_at(_data(env), DIRECTORY, lock=False)
    evidence = _write(tree / "operation/receipt.json", b"corrupt opaque replica receipt")
    empty = tree / "failed-empty-operation"
    empty.mkdir()
    candidate, item = registered(env)
    outcome = _apply(env)
    assert outcome["state"] == "completed" and outcome["deleted_bytes"] == item["byte_size"]
    assert not candidate.exists()
    assert evidence.read_bytes() == b"corrupt opaque replica receipt" and empty.is_dir()


def test_foreign_replica_hardlink_alias_is_unknown_and_blocks_the_entire_gc_group(libraries):
    first = libraries("a")
    second = libraries("b", media=first["profile"].media)
    enroll(first, second)
    candidate, item = registered(first, "local_report", content=b"original aliased candidate")
    unrelated, unrelated_item = registered(first, "local_report", content=b"independent but same GC group")
    tree = _private_tree_at(_data(second), DIRECTORY, lock=False)
    alias = tree / "failed-copy.raw"
    os.link(candidate, alias)
    with _capture(second) as capture:
        assert not capture.complete and capture.inputs.blockers
        assert _identity(alias) in capture.protected_inodes
        assert _key(alias) in {_key(path) for path in capture.protected_paths}
    observed = scan(first)
    assert statuses(observed)[item["artifact_id"]] in {"blocked", "protected"}
    assert statuses(observed)[unrelated_item["artifact_id"]] == "blocked"
    assert not observed.eligible_artifacts
    outcome = _apply(first)
    assert outcome["deleted_bytes"] == 0
    assert candidate.read_bytes() == alias.read_bytes() == b"original aliased candidate"
    assert unrelated.read_bytes() == b"independent but same GC group"


def test_replica_appearance_before_native_unlink_invalidates_the_held_absence_observation(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _item = registered(env)
    original_unlink, effects = gc._unlink, []
    def introduced(*args, **kwargs):
        effects.append(_write(_data(env) / DIRECTORY / "operation/.write-failed.tmp", b"new retained replica evidence"))
        return original_unlink(*args, **kwargs)
    monkeypatch.setattr(gc, "_unlink", introduced)
    outcome = _apply(env)
    assert effects and outcome["state"] == "blocked" and outcome["deleted_bytes"] == 0
    assert candidate.exists() and effects[0].read_bytes() == b"new retained replica evidence"


def test_gc_resume_keeps_original_request_and_selection_when_replica_alias_appears(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    candidate, _item = registered(env)
    operation, request = _plan_without_execute(env, monkeypatch)
    tree = _private_tree_at(_data(env), DIRECTORY, lock=False)
    alias = tree / "failed-replica.raw"
    os.link(candidate, alias)
    outcome = gc.resume([env["profile"]], operation_id=operation)
    assert outcome["state"] == "blocked" and outcome["deleted_bytes"] == 0
    assert outcome["selected_count"] == 1
    assert candidate.exists() and alias.read_bytes() == candidate.read_bytes()
    assert _op(env, operation)["request_json"] == request


def test_original_authority_source_census_does_not_recurse_into_replica_evidence(authority_env):
    profile = authority_env["profile"]
    tree = _private_tree_at(_data(authority_env), DIRECTORY, lock=False)
    retained = _write(tree / "operation/payload/source.raw", b"a replica is not a new original authority source")
    _write(_data(authority_env) / "node-identity.json", b"synthetic invalid node raw")
    with lifecycle.exclusive(profile):
        with source.capture_raw_locked(profile, operation_id=str(uuid.uuid4())) as held:
            metadata = source.source_metadata(held)
            assert [row["relative_key"] for row in metadata["capsule"]["files"]] == ["node-identity.json"]
            assert metadata["node_observation"]["state"] == "invalid"
    assert source.closed_observation(held)["source_file_count"] == 1
    assert retained.read_bytes() == b"a replica is not a new original authority source"
