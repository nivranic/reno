"""Actual CLI preview, original-operation restore and postcommit State resume."""
import json
import uuid

import pytest

from reno import cli, db, home_recovery_source, storage_admin, sync_state, tasks
from tests.test_home_recovery_home import env, changed, state, rows  # noqa: F401


def profile_arguments(profile):
    return ["--db", str(profile.database), "--media", str(profile.media),
            "--reports", str(profile.reports), "--checkpoints", str(profile.checkpoints)]


def preview_arguments(env):
    return ["home-restore", env[1], *profile_arguments(env[0])]


def test_preview_has_no_write_effect_or_implicit_setup(env, capsys, monkeypatch):
    changed(env)
    original = rows(env)
    def forbidden(*args, **kwargs):
        pytest.fail("Preview cannot set up facilities or initialize an identity")
    monkeypatch.setattr(storage_admin, "setup", forbidden)
    monkeypatch.setattr(storage_admin, "initialize", forbidden)
    assert cli.main(preview_arguments(env)) == 0
    observed = json.loads(capsys.readouterr().out)
    assert observed["source"]["source_id"] == env[1]
    assert len(observed["digest"]) == 64
    assert rows(env) == original
    assert not (env[0].database.parent / "sync-state.json").exists()


def test_explicit_restore_and_exact_retry_keep_the_original_receipt(env, capsys):
    changed(env)
    assert cli.main(preview_arguments(env)) == 0
    view = json.loads(capsys.readouterr().out)
    identifier = str(uuid.uuid4())
    arguments = [*preview_arguments(env), "--apply", "--expected-digest", view["digest"], "--operation-id", identifier]
    assert cli.main(arguments) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["operation_id"] == identifier and not result["replayed"]
    assert result["after_version"]["library_epoch"] == view["before_version"]["library_epoch"] + 1
    committed = rows(env)
    # The original receipt is sufficient for replay even if its selected
    # checkpoint is subsequently unavailable; it must not restore twice.
    declaration = home_recovery_source._source_path(env[0], env[1])
    declaration.rename(declaration.with_suffix(".unavailable"))
    assert cli.main(arguments) == 0
    assert json.loads(capsys.readouterr().out) == {**result, "replayed": True}
    assert rows(env) == committed


def test_original_operation_resume_repairs_the_actual_sql_commit_state_gap(env, capsys, monkeypatch):
    store = state(env)
    changed(env)
    assert cli.main(preview_arguments(env)) == 0
    view = json.loads(capsys.readouterr().out)
    original_state = store.read()
    identifier = str(uuid.uuid4())
    original_update = sync_state.StateStore.update
    def interrupted(self, changes):
        if changes == {"local_epoch": original_state["local_epoch"] + 1}:
            raise OSError("synthetic-state-write-interruption")
        return original_update(self, changes)
    with monkeypatch.context() as patch:
        patch.setattr(sync_state.StateStore, "update", interrupted)
        assert cli.main([*preview_arguments(env), "--apply", "--expected-digest", view["digest"],
                         "--operation-id", identifier]) == 2
        error = capsys.readouterr()
        assert json.loads(error.err)["code"] == "HOME_RECOVERY_FAILED"
        assert "synthetic-state-write-interruption" not in error.err
    committed = rows(env)
    assert store.read() == original_state
    with storage_admin.connection(env[0]) as con:
        assert db.knowledge_version(con)["library_epoch"] == original_state["local_epoch"] + 1
    assert cli.main(["home-restore", "--resume", "--operation-id", identifier, *profile_arguments(env[0])]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["operation_id"] == identifier and result["replayed"]
    assert store.read() == {**original_state, "local_epoch": original_state["local_epoch"] + 1}
    assert rows(env) == committed


@pytest.mark.parametrize("suffix", [
    ["--apply"], ["--apply", "--operation-id", str(uuid.uuid4())],
    ["--apply", "--expected-digest", "a" * 64], ["--operation-id", str(uuid.uuid4())],
    ["--expected-digest", "a" * 64], ["--resume", "--operation-id", str(uuid.uuid4())],
])
def test_missing_or_cross_mode_arguments_never_change_the_library(env, capsys, suffix):
    before = rows(env)
    assert cli.main([*preview_arguments(env), *suffix]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "HOME_RECOVERY_EXPECTATION_REQUIRED"
    assert rows(env) == before


def test_stale_preview_does_not_create_a_protection_backup(env, capsys):
    assert cli.main(preview_arguments(env)) == 0
    view = json.loads(capsys.readouterr().out)
    changed(env)
    before = rows(env)
    assert cli.main([*preview_arguments(env), "--apply", "--expected-digest", view["digest"],
                     "--operation-id", str(uuid.uuid4())]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "HOME_RECOVERY_CAS_MISMATCH"
    assert rows(env) == before
    assert not list(env[0].database.parent.glob("*.bak-home-restore-*"))


def test_busy_actual_worker_keeps_the_original_cli_operation_pending(env, capsys):
    assert cli.main(preview_arguments(env)) == 0
    view = json.loads(capsys.readouterr().out)
    before = rows(env)
    with tasks.process_lock(profile=env[0]) as acquired:
        assert acquired
        assert cli.main([*preview_arguments(env), "--apply", "--expected-digest", view["digest"],
                         "--operation-id", str(uuid.uuid4())]) == 2
        assert json.loads(capsys.readouterr().err)["code"] == "HOME_RECOVERY_WORKER_BUSY"
        assert tasks.holds_process_lock(profile=env[0])
    assert rows(env) == before


@pytest.mark.parametrize("arguments", [
    ["home-restore", "--unknown-private-argument"],
    ["home-restore", str(uuid.uuid4()), "--apply", "--resume"],
])
def test_argparse_errors_do_not_echo_private_values(arguments, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(arguments)
    assert caught.value.code == 2
    error = capsys.readouterr().err
    assert "unknown-private-argument" not in error
    assert json.loads(error)["code"] == "HOME_RECOVERY_ARGUMENT_INVALID"


def test_partial_profile_and_missing_library_fail_without_creating_files(tmp_path, capsys):
    assert cli.main(["home-restore", str(uuid.uuid4()), "--db", str(tmp_path / "missing/reno.db")]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "STORAGE_PROFILE_REQUIRED"
    arguments = ["home-restore", str(uuid.uuid4()), "--db", str(tmp_path / "missing/reno.db"),
        "--media", str(tmp_path / "media"), "--reports", str(tmp_path / "reports"), "--checkpoints", str(tmp_path / "checkpoints")]
    assert cli.main(arguments) == 2
    assert json.loads(capsys.readouterr().err)["code"] in {"HOME_RECOVERY_FAILED", "SCHEMA_UPGRADE_REQUIRED", "STORAGE_NOT_INITIALIZED"}
    assert not list(tmp_path.iterdir())
