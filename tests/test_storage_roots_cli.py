"""Read-only command against isolated, explicitly enrolled schema10 libraries."""
from contextlib import closing
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import cli, config, db, storage_artifacts as artifacts, storage_gc_contract as contract
from reno import storage_lifecycle as lifecycle, storage_members as members, storage_roots as roots
from reno import storage_readonly, storage_home_roots, storage_checkpoint_roots, storage_backup_roots, storage_source


def arguments(profile):
    return ["--db", str(profile.database), "--media", str(profile.media), "--reports", str(profile.reports),
            "--checkpoints", str(profile.checkpoints)]


def contents(root):
    return {str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in root.rglob("*") if path.is_file()}


def create_library(root):
    profile = lifecycle.StorageProfile(root / "data/reno.db", root / "media",
                                       root / "reports", root / "checkpoints")
    db.initialize(profile.database)
    lifecycle.initialize(profile)
    (profile.database.parent / "node-identity.json").write_text(
        json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    declarations = [{"root": str(root), "databases": [str(profile.database)]}
                    for root in (profile.media, profile.reports, profile.checkpoints)]
    preview = members.membership_preview([profile], declarations=declarations)
    members.enroll([profile], expected_digest=preview["digest"], declarations=declarations,
                   operation_id=str(uuid.uuid4()))
    payload = b"PRIVATE_UNUSED_FILE_CONTENT"
    sha = hashlib.sha256(payload).hexdigest()
    key = f"sync/video/{sha}.mp4"
    path = profile.media / key
    path.parent.mkdir(parents=True)
    path.write_bytes(payload)
    with lifecycle.shared(profile), closing(sqlite3.connect(profile.database)) as con:
        con.row_factory = sqlite3.Row
        con.execute("BEGIN IMMEDIATE")
        item = artifacts.register(con, profile=profile, namespace="sync_media", root=profile.media,
                                  storage_key=key, sha256=sha, byte_size=len(payload))
        con.commit()
    return profile, item, path


@pytest.fixture
def library(tmp_path):
    return create_library(tmp_path)


def test_real_preview_has_candidate_and_preserves_all_library_bytes(library, tmp_path, capsys, monkeypatch):
    profile, item, path = library
    before = contents(tmp_path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preview must not initialize or read configuration")
    monkeypatch.setattr(db, "initialize", forbidden)
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    monkeypatch.setattr(config, "get", forbidden)
    monkeypatch.setattr(config, "db_path", forbidden)
    monkeypatch.setattr(config, "local_path", forbidden)
    assert cli.main(["storage-roots", *arguments(profile)]) == 0
    output = capsys.readouterr()
    value = json.loads(output.out)
    assert not output.err
    assert value["candidate_bytes"] == len(path.read_bytes())
    assert next(f for f in value["files"] if f["artifact_id"] == item["artifact_id"])["status"] == "candidate"
    assert value["policy"] == contract.default_policy()
    assert "PRIVATE_UNUSED_FILE_CONTENT" not in output.out and str(tmp_path) not in output.out
    assert contents(tmp_path) == before


@pytest.mark.parametrize("existing_database", [False, True])
def test_missing_facilities_are_not_created_and_errors_do_not_echo_paths(tmp_path, capsys, existing_database):
    private = tmp_path / "PRIVATE_DIRECTORY"
    profile = lifecycle.StorageProfile(private / "data/reno.db", private / "media", private / "reports", private / "checkpoints")
    if existing_database:
        db.initialize(profile.database)
        (profile.database.parent / "node-identity.json").unlink(missing_ok=True)
    before = contents(tmp_path)
    assert cli.main(["storage-roots", *arguments(profile)]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["code"] == "ROOTS_GROUP_UNAVAILABLE"
    assert "PRIVATE_DIRECTORY" not in output.err and str(tmp_path) not in output.err
    assert contents(tmp_path) == before
    assert not profile.media.exists() and not profile.reports.exists() and not profile.checkpoints.exists()
    assert not (private / "data/node-identity.json").exists()


@pytest.mark.parametrize("flags", [[], ["--db", "PRIVATE_PATH"]])
def test_explicit_complete_profile_is_required_without_config_fallback(flags, capsys, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("No default profile")
    monkeypatch.setattr(config, "db_path", forbidden)
    assert cli.main(["storage-roots", *flags]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "STORAGE_PROFILE_REQUIRED"


def test_members_file_is_read_only_and_preserves_first_profile_as_owner(library, tmp_path, capsys):
    previous, previous_item, _path = library
    profile, item, _path = create_library(tmp_path / "second")
    document = tmp_path / "members.json"
    document.write_text(json.dumps({"profiles": [members._profile_json(profile), members._profile_json(previous)],
                                    "declarations": []}), encoding="utf-8")
    before = contents(tmp_path)
    assert cli.main(["storage-roots", "--members-file", str(document)]) == 0
    value = json.loads(capsys.readouterr().out)
    with lifecycle.shared(profile):
        assert value["owner_member_id"] == members.require_enrolled(profile)["member_id"]
    assert value["files"][0]["artifact_id"] == item["artifact_id"]
    assert previous_item["artifact_id"] not in {f["artifact_id"] for f in value["files"]}
    assert contents(tmp_path) == before


def test_nonempty_completeness_declarations_cannot_mutate_membership(library, tmp_path, capsys, monkeypatch):
    profile, _item, _path = library
    document = tmp_path / "members.json"
    document.write_text(json.dumps({"profiles": [members._profile_json(profile)], "declarations": [
        {"root": "PRIVATE_ROOT", "databases": []}]}), encoding="utf-8")
    before = contents(tmp_path)
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Declarations must be rejected before scanning")
    monkeypatch.setattr(roots, "preview", forbidden)
    assert cli.main(["storage-roots", "--members-file", str(document)]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["code"] == "ROOTS_DECLARATIONS_NOT_ALLOWED"
    assert "PRIVATE_ROOT" not in output.err and contents(tmp_path) == before


def test_keep_and_repeated_namespace_options_change_policy_digest(library, capsys):
    profile, _item, _path = library
    assert cli.main(["storage-roots", *arguments(profile)]) == 0
    first = json.loads(capsys.readouterr().out)
    assert cli.main(["storage-roots", *arguments(profile), "--keep-publication", "4", "--keep-recovery", "2",
                     "--namespace", "local_report", "--namespace", "checkpoint", "--namespace", "local_report"]) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["policy"] == contract.default_policy(keep_publication=4, keep_recovery=2,
                                                      namespaces=["checkpoint", "local_report"])
    assert second["preview_sha256"] != first["preview_sha256"] and second["roots_sha256"] != first["roots_sha256"]
    assert second["candidate_bytes"] == 0


@pytest.mark.parametrize("flags", [["--keep-publication", "0"], ["--keep-recovery", "-1"]])
def test_retention_must_keep_at_least_one_checkpoint(library, capsys, flags):
    profile, _item, _path = library
    assert cli.main(["storage-roots", *arguments(profile), *flags]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "ROOTS_CONFIG_INVALID"


@pytest.mark.parametrize("flags", [["--unknown", "PRIVATE_ARGUMENT"],
    ["--keep-recovery", "PRIVATE_NUMBER"], ["--namespace", "PRIVATE_NAMESPACE"], ["--db"]])
def test_invalid_arguments_are_reported_as_sanitized_json(capsys, flags):
    with pytest.raises(SystemExit) as caught:
        cli.main(["storage-roots", *flags])
    assert caught.value.code == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["code"] == "ROOTS_ARGUMENT_INVALID"
    assert "PRIVATE" not in output.err


@pytest.mark.parametrize("error", [roots.RootError("ROOTS_TEST_ERROR"),
    storage_readonly.ReadonlyImageError("SOURCE_CHANGED"), storage_home_roots.HomeRootError("HOME_SOURCE_CHANGED"),
    storage_checkpoint_roots.CheckpointRootError("CHECKPOINT_SOURCE_CHANGED"),
    storage_backup_roots.BackupRootError("SOURCE_CHANGED"), storage_source.SourceReadError("SOURCE_CHANGED"),
    roots.RootError("PRIVATE_PATH / confidential"), OSError("PRIVATE_PATH / confidential"),
    sqlite3.DatabaseError("PRIVATE_PATH / confidential"), RuntimeError("PRIVATE_PATH / confidential")])
def test_all_preview_exception_families_are_controlled_and_sanitized(library, capsys, monkeypatch, error):
    profile, _item, _path = library
    def failed(*_args, **_kwargs):
        raise error
    monkeypatch.setattr(roots, "preview", failed)
    assert cli.main(["storage-roots", *arguments(profile)]) == 2
    output = capsys.readouterr()
    result = json.loads(output.err)
    assert not output.out and set(result) == {"code", "detail"}
    assert "PRIVATE" not in output.err and "confidential" not in output.err and "Traceback" not in output.err
