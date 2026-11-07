"""Public local commands keep explicit mutations separate from read-only status."""
import json
import sqlite3
import uuid

import pytest

from reno import cli, config, db, storage_admin
from tests.test_personal_preservation import library


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def commands(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "home"})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    from reno import personal_mirror_admin, personal_preservation_admin
    calls = []

    def bind(name):
        def invoke(selected_profile, **kwargs):
            assert selected_profile.identity == profile.identity
            calls.append((name, kwargs))
            return {"action": name, "phase": "ready"}
        return invoke

    for name in ("initialize", "preview", "apply", "status"):
        monkeypatch.setattr(personal_mirror_admin, name, bind("mirror-" + name))
    for name in ("status", "create", "resume", "verify"):
        monkeypatch.setattr(personal_preservation_admin, name, bind("preservation-" + name))
    return profile, calls


def output(capsys):
    return json.loads(capsys.readouterr().out)


def test_preservation_default_status_and_verify_do_not_create(commands, capsys):
    _profile, calls = commands
    assert cli.main(["personal-preservation"]) == 0
    assert output(capsys)["action"] == "preservation-status"
    operation = uid()
    assert cli.main(["personal-preservation", "--verify", "--operation-id", operation]) == 0
    assert output(capsys)["action"] == "preservation-verify"
    assert calls == [("preservation-status", {"operation_id": None}),
                     ("preservation-verify", {"operation_id": operation, "expected_request_sha256": None})]


def test_preservation_explicit_create_limits_and_original_resume(commands, capsys):
    _profile, calls = commands
    operation = uid()
    assert cli.main(["personal-preservation", "--create", "--apply", "--operation-id", operation,
                     "--max-files", "20", "--max-bytes", "1048576", "--max-seconds", "60"]) == 0
    output(capsys)
    assert calls[0] == ("preservation-create", {"operation_id": operation,
        "limits": {"max_files": 20, "max_bytes": 1048576, "max_seconds": 60}})
    assert cli.main(["personal-preservation", "--resume", "--apply", "--operation-id", operation,
                     "--expected-request-sha256", "a" * 64]) == 0
    output(capsys)
    assert calls[1] == ("preservation-resume", {"operation_id": operation, "expected_request_sha256": "a" * 64})


@pytest.mark.parametrize("arguments", [
    ["personal-preservation", "--create", "--operation-id", uid()],
    ["personal-preservation", "--apply"],
    ["personal-preservation", "--resume", "--apply", "--operation-id", uid()],
    ["personal-preservation", "--verify", "--apply", "--operation-id", uid()],
    ["personal-preservation", "--max-files", "20"],
    ["personal-mirror-init"],
    ["personal-mirror", "--apply", "--operation-id", uid(), "--snapshot-id", uid()],
    ["personal-mirror", "--operation-id", uid(), "--snapshot-id", uid(), "--expected-snapshot-sha256", "a" * 64],
    ["personal-mirror-deliver", "--operation-id", uid(), "--expected-request-sha256", "a" * 64],
])
def test_incomplete_or_mixed_actions_reject_before_an_admin_call(commands, capsys, arguments):
    _profile, calls = commands
    assert cli.main(arguments) == 2
    assert calls == []
    assert "code" in json.loads(capsys.readouterr().err)


def test_mirror_preview_and_exact_cas_apply(commands, capsys):
    _profile, calls = commands
    operation, snapshot, head = uid(), uid(), uid()
    arguments = ["personal-mirror", "--operation-id", operation, "--snapshot-id", snapshot,
                 "--expected-head-operation-id", head]
    assert cli.main(arguments) == 0
    assert output(capsys)["action"] == "mirror-preview"
    assert cli.main([*arguments, "--apply", "--expected-snapshot-sha256", "a" * 64]) == 0
    assert output(capsys)["action"] == "mirror-apply"
    assert calls == [("mirror-preview", {"operation_id": operation, "snapshot_id": snapshot,
        "expected_head_operation_id": head}), ("mirror-apply", {"operation_id": operation,
        "snapshot_id": snapshot, "expected_head_operation_id": head, "expected_snapshot_sha256": "a" * 64})]


def test_explicit_original_delivery_queues_without_starting_a_worker(commands, capsys, monkeypatch):
    profile, calls = commands
    from reno import tasks, worker
    operation, request = uid(), uid()
    submitted = []

    def submit(op, sha, request_id, *, profile):
        submitted.append((op, sha, request_id, profile.identity))
        return {"task_id": 7, "status": "pending"}

    def forbidden(*args, **kwargs):
        raise AssertionError("A queue-only CLI call must not dispatch")

    monkeypatch.setattr(tasks, "submit_personal_mirror", submit)
    monkeypatch.setattr(worker, "process_pending", forbidden)
    assert cli.main(["personal-mirror-deliver", "--apply", "--operation-id", operation,
        "--expected-request-sha256", "a" * 64, "--request-id", request]) == 0
    assert output(capsys) == {"task_id": 7, "status": "pending"}
    assert submitted == [(operation, "a" * 64, request, profile.identity)] and calls == []


def test_argument_errors_do_not_echo_private_paths(commands, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["personal-preservation", "--max-files", "PRIVATE_UNKNOWN_VALUE"])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "PRIVATE_UNKNOWN_VALUE" not in captured.out + captured.err
    assert json.loads(captured.err)["code"] == "PERSONAL_ADMIN_ARGUMENT_INVALID"


def test_wait_rejects_another_profile_before_queue_or_default_worker(commands, capsys, tmp_path, monkeypatch):
    _profile, calls = commands
    from reno import tasks, worker

    def forbidden(*args, **kwargs):
        raise AssertionError("A foreign --wait must neither submit nor run the configured worker")

    monkeypatch.setattr(tasks, "submit_personal_mirror", forbidden)
    monkeypatch.setattr(worker, "process_pending", forbidden)
    arguments = ["personal-mirror-deliver", "--apply", "--wait", "--operation-id", uid(),
                 "--expected-request-sha256", "a" * 64]
    for name in ("db", "media", "reports", "checkpoints"):
        arguments.extend(["--" + name, str(tmp_path / ("other-" + name))])
    assert cli.main(arguments) == 2
    assert calls == []
    assert json.loads(capsys.readouterr().err)["code"] == "PERSONAL_MIRROR_PROFILE_CHANGED"


@pytest.mark.parametrize("error_type", [sqlite3.OperationalError, RuntimeError])
def test_database_and_internal_failures_leave_only_fixed_public_error(commands, capsys, monkeypatch, error_type):
    from reno import personal_preservation_admin, tasks

    def private_failure(*args, **kwargs):
        raise error_type("PRIVATE_PATH_OR_CONFIGURATION_SENTINEL")

    monkeypatch.setattr(personal_preservation_admin, "status", private_failure)
    assert cli.main(["personal-preservation"]) == 2
    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.out + captured.err
    assert json.loads(captured.err)["code"] == "PERSONAL_PRESERVATION_FAILED"
    monkeypatch.setattr(tasks, "submit_personal_mirror", private_failure)
    assert cli.main(["personal-mirror-deliver", "--apply", "--operation-id", uid(),
                     "--expected-request-sha256", "a" * 64]) == 2
    captured = capsys.readouterr()
    assert "SENTINEL" not in captured.out + captured.err
    assert json.loads(captured.err)["code"] == "PERSONAL_MIRROR_FAILED"


def test_real_preservation_cli_creates_verifies_and_resumes_same_package(library, capsys):
    profile, _project, local = library
    from reno import personal_preservation_contract as contract
    original = {path: path.read_bytes() for path in (profile.database, local,
                                                   profile.database.parent / "node-identity.json")}
    assert cli.main(["personal-preservation"]) == 0
    initial = output(capsys)
    assert initial["present"] is False and initial["verified"] is False
    assert not (profile.database.parent / contract.CATALOG).exists()
    operation = uid()
    assert cli.main(["personal-preservation", "--create", "--apply", "--operation-id", operation]) == 0
    created = output(capsys)
    assert created["phase"] == "ready" and created["verified"] is True
    sha = created["request_sha256"]
    assert cli.main(["personal-preservation", "--operation-id", operation]) == 0
    recorded = output(capsys)
    assert recorded["phase"] == "ready" and recorded["verified"] is False
    assert cli.main(["personal-preservation", "--verify", "--operation-id", operation,
                     "--expected-request-sha256", sha]) == 0
    assert output(capsys)["verified"] is True
    assert cli.main(["personal-preservation", "--resume", "--apply", "--operation-id", operation,
                     "--expected-request-sha256", sha]) == 0
    assert output(capsys)["operation_id"] == operation
    assert {path: path.read_bytes() for path in original} == original
