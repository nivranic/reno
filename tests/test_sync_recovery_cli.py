"""Local recovery CLI previews by default and requires explicit versioned apply."""
import json
import uuid

import pytest

from reno import cli, config, db, sync_recovery, storage_admin, storage_members
from tests.test_sync_transfer import env, next_payload, publish, receiver_guard


def applied_arguments(preview, operation_id):
    version = preview["current_version"]
    return ["--apply", "--operation-id", operation_id,
            "--expected-library-id", version["library_id"], "--expected-epoch", str(version["library_epoch"]),
            "--expected-revision", str(version["kb_revision"]),
            "--expected-control-revision", str(preview["control_revision"])]


@pytest.fixture()
def ready(env, monkeypatch):
    with receiver_guard(env):
        publish(env)
        env["con"].commit()
        changed = next_payload(env)
        changed["videos"][0]["tables"]["knowledge_atom"][0]["claim"] = "新版建议"
        publish(env, changed)
        env["con"].commit()
    checkpoint = env["con"].execute("SELECT checkpoint_id FROM sync_checkpoint WHERE publication_id=?",
                                   (changed["publication_id"],)).fetchone()[0]
    monkeypatch.setattr(config, "db_path", lambda: env["target_path"])
    return env, checkpoint


def test_preview_is_read_only_and_apply_is_idempotent_before_explicit_resume(ready, capsys):
    env, checkpoint = ready
    before = db.knowledge_version(env["con"])
    assert cli.main(["sync-restore", checkpoint]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["recoverable"]
    assert db.knowledge_version(env["con"]) == before
    assert sync_recovery.state(env["con"])["state"] == "running"
    args = ["sync-restore", checkpoint, *applied_arguments(preview, str(uuid.uuid4()))]
    assert cli.main(args) == 0
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["replayed"] is False
    assert env["con"].execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "施工前检查基层"
    after = db.knowledge_version(env["con"])
    assert after["library_epoch"] == before["library_epoch"] and after["kb_revision"] > before["kb_revision"]
    assert sync_recovery.state(env["con"])["state"] == "held"
    assert cli.main(args) == 0
    assert json.loads(capsys.readouterr().out)["replayed"] is True
    assert db.knowledge_version(env["con"]) == after
    assert cli.main(["sync-recovery-checkpoints"]) == 0
    assert len(json.loads(capsys.readouterr().out)) == 1
    assert cli.main(["sync-resume"]) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume["control"]["state"] == "held"
    assert cli.main(["sync-resume", *applied_arguments(resume, str(uuid.uuid4()))]) == 0
    capsys.readouterr()
    assert sync_recovery.state(env["con"])["state"] == "resume_pending"


@pytest.mark.parametrize("suffix", [["--apply"], ["--operation-id", str(uuid.uuid4())]])
def test_missing_apply_or_expectations_never_mutates(ready, suffix, capsys):
    env, checkpoint = ready
    version = db.knowledge_version(env["con"])
    assert cli.main(["sync-restore", checkpoint, *suffix]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SYNC_RECOVERY_EXPECTATION_REQUIRED"
    assert db.knowledge_version(env["con"]) == version
    assert sync_recovery.state(env["con"])["state"] == "running"


def test_stale_cli_preview_rejected_and_user_data_remains_current(ready, capsys):
    env, checkpoint = ready
    assert cli.main(["sync-restore", checkpoint]) == 0
    preview = json.loads(capsys.readouterr().out)
    db.bump_kb_revision(env["con"])
    env["con"].commit()
    version = db.knowledge_version(env["con"])
    assert cli.main(["sync-restore", checkpoint, *applied_arguments(preview, str(uuid.uuid4()))]) == 2
    assert json.loads(capsys.readouterr().err)["code"].startswith("SYNC_RECOVERY_")
    assert db.knowledge_version(env["con"]) == version
    assert env["con"].execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "新版建议"


@pytest.mark.parametrize("command", [["sync-recovery-status"], ["sync-recovery-checkpoints"], ["sync-resume"],
                                     ["sync-restore", str(uuid.uuid4())]])
def test_readonly_recovery_commands_do_not_initialize_library(tmp_path, monkeypatch, capsys, command):
    path = tmp_path / "missing" / "reno.db"
    monkeypatch.setattr(config, "db_path", lambda: path)
    assert cli.main(command) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "SCHEMA_UPGRADE_REQUIRED"
    assert not path.parent.exists()


def test_recovery_previews_never_call_storage_setup(ready, monkeypatch, capsys):
    env, checkpoint = ready
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Read-only preview must not set up membership")
    monkeypatch.setattr(storage_admin, "setup", forbidden)
    for command in (["sync-restore", checkpoint], ["sync-resume"]):
        assert cli.main(command) == 0
        assert json.loads(capsys.readouterr().out)
    assert all(not (getattr(env["profile"], role) / storage_members.REGISTRY).exists() for role in storage_members.ROLES)


@pytest.mark.parametrize("command", ["sync-restore", "sync-resume"])
def test_recovery_apply_rejects_partial_local_witness_before_mutating(ready, command, capsys):
    env, checkpoint = ready
    storage_admin.setup(env["profile"])
    if command == "sync-resume":
        assert cli.main(["sync-restore", checkpoint]) == 0
        first = json.loads(capsys.readouterr().out)
        assert cli.main(["sync-restore", checkpoint, *applied_arguments(first, str(uuid.uuid4()))]) == 0
        capsys.readouterr()
    preview_args = [command, checkpoint] if command == "sync-restore" else [command]
    assert cli.main(preview_args) == 0
    preview = json.loads(capsys.readouterr().out)
    reports = env["profile"].reports
    registry = json.loads((reports / storage_members.REGISTRY).read_text(encoding="utf-8"))
    (reports / (storage_members.COMMIT_PREFIX + registry["operation_id"] + ".json")).unlink()
    before_version, before_control = db.knowledge_version(env["con"]), sync_recovery.state(env["con"])
    assert cli.main([*preview_args, *applied_arguments(preview, str(uuid.uuid4()))]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "MEMBERSHIP_ENROLLMENT_REQUIRED"
    assert db.knowledge_version(env["con"]) == before_version
    assert sync_recovery.state(env["con"]) == before_control
