"""Maintenance safety on artificial databases, deterministic model stubs only."""
import concurrent.futures
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from tests.migration_helpers import initialize_migration_storage, migration_profile

from reno import config, db, llm, migrations, report, storage_admin, tasks, worker


@pytest.fixture()
def task_env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "all"})
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    with storage_admin.connection() as con:
        db.upsert_asset(con, {"video_id": "video", "content_sha256": "test-asset", "title": "人工样本"})
        con.commit()
    yield tmp_path
    worker.stop_running()


def test_scope_dedup_including_null_video_and_durable_request_receipts(task_env):
    request_ids = [str(uuid.uuid4()) for _ in range(6)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        result = list(pool.map(lambda request_id: tasks.submit("judge", request_id=request_id), request_ids))
    assert len({row["task_id"] for row in result}) == 1
    job_id = result[0]["task_id"]
    con = db.connect()
    con.execute("UPDATE job SET status='done' WHERE job_id=?", (job_id,))
    con.commit()
    con.close()
    # A deduplicated request retains its receipt even after the task completed.
    assert tasks.submit("judge", request_id=request_ids[-1])["task_id"] == job_id
    with pytest.raises(tasks.TaskError) as mismatch:
        tasks.submit("report", request_id=request_ids[-1])
    assert mismatch.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert tasks.submit("judge")["task_id"] != job_id


def test_worker_dispatch_and_summary_is_explicit(task_env, monkeypatch):
    seen = []
    monkeypatch.setattr(worker.pipeline, "run_video", lambda video_id, force=False: seen.append(("process", force)) or {})
    from reno import judge
    monkeypatch.setattr(judge, "run", lambda con, threshold: seen.append(("judge", threshold)) or {})
    monkeypatch.setattr(report, "run_all", lambda: seen.append(("report", "static")) or {})
    monkeypatch.setattr(report, "summary_report", lambda: seen.append(("report", "summary")) or Path("summary.md"))
    for kind, video, payload in (("process", "video", None), ("judge", None, None),
                                  ("report", None, None), ("report", None, {"mode": "summary"})):
        tasks.submit(kind, video, payload)
    assert worker.process_pending() == 4
    assert seen == [("process", False), ("judge", 0.22), ("report", "static"), ("report", "summary")]
    assert all(item["status"] == "done" for item in tasks.list_tasks())


def test_live_worker_is_not_reset_and_expired_job_recovers(task_env, monkeypatch):
    queued = tasks.submit("process", "video")
    assert tasks.acquire_worker("live-owner")
    con = db.connect()
    claimed = tasks.claim(con, "live-owner")
    con.close()
    assert claimed["job_id"] == queued["task_id"]
    monkeypatch.setattr(worker, "dispatch", lambda job: {})
    assert worker.process_pending() == 0
    assert tasks.get(queued["task_id"])["status"] == "running"
    con = db.connect()
    con.execute("UPDATE worker_lease SET lease_until=0")
    con.execute("UPDATE job SET lease_until=0")
    con.commit()
    con.close()
    assert worker.process_pending() == 1
    assert tasks.get(queued["task_id"])["attempts"] == 2


def test_daemon_waits_out_an_abandoned_lease_without_another_user_request(task_env, monkeypatch):
    import threading
    job = tasks.submit("process", "video")
    monkeypatch.setattr(tasks, "LEASE_MS", 150)
    assert tasks.acquire_worker("stopped-before-restart")
    dispatched = threading.Event()
    monkeypatch.setattr(worker, "dispatch", lambda job: dispatched.set() or {})
    worker.ensure_running()
    assert dispatched.wait(5), "startup daemon must retry the expired lease without another submission"
    assert worker.stop_running(timeout=5)
    assert tasks.get(job["task_id"])["status"] == "done"


def test_failed_retry_preserves_completed_steps_and_never_forces(task_env, monkeypatch):
    con = db.connect()
    con.execute("INSERT INTO meta(key,value) VALUES ('step:video:media','completed')")
    con.execute("INSERT INTO knowledge_atom(id,video_id,claim) VALUES ('keep','video','保留知识')")
    con.commit()
    con.close()
    first = tasks.submit("process", "video", {"force": True})
    monkeypatch.setattr(worker, "dispatch", lambda job: {"asr": "ERROR: secret-private-token"})
    assert worker.process_pending() == 1
    assert "secret-private-token" not in json.dumps(tasks.get(first["task_id"]))
    next_job = tasks.retry(first["task_id"], str(uuid.uuid4()))
    assert next_job["payload"] == {"force": False}
    assert tasks.get(first["task_id"])["status"] == "failed"
    observed = []
    monkeypatch.setattr(worker.pipeline, "_active_steps", lambda: ["media", "asr"])
    monkeypatch.setattr(worker.pipeline.asr, "run", lambda con, video_id: observed.append(video_id) or {})
    monkeypatch.setattr(worker, "dispatch", lambda job: worker.pipeline.run_video(job["video_id"], force=False))
    assert worker.process_pending() == 1
    assert observed == ["video"]
    con = db.connect()
    assert con.execute("SELECT value FROM meta WHERE key='step:video:media'").fetchone()[0] == "completed"
    assert con.execute("SELECT claim FROM knowledge_atom WHERE id='keep'").fetchone()[0] == "保留知识"
    con.close()


def _seed_report_atoms():
    con = db.connect()
    def atom(aid, claim, amount):
        return {"id": aid, "claim": claim, "polarity": "recommend", "confidence": 0.9,
                "stage": "防水", "prices": [{"object": claim, "amount": amount, "unit": "元"}],
                "evidence_refs": [{"modality": "asr", "start_ms": 0, "end_ms": 100, "evidence_text": claim}]}
    db.replace_atoms(con, "video", [atom("accepted", "保留建议", 10), atom("rejected", "驳回观点秘密标记", 20)])
    db.add_decision(con, atom_id="rejected", action="reject")
    con.commit()
    con.close()


def test_reports_filter_rejected_and_static_never_calls_model(task_env, monkeypatch):
    _seed_report_atoms()
    def forbidden(*args, **kwargs):
        pytest.fail("static report must not call the model")
    monkeypatch.setattr(llm, "chat", forbidden)
    assert report.run_all()["mode"] == "static"
    for name in ("checklist", "prices"):
        assert "保留建议" in report.read_report(name)
        assert "驳回观点秘密标记" not in report.read_report(name)
        assert report.metadata()[name]["status"] == "latest"
    assert report.metadata()["summary"]["status"] == "missing"
    material = []
    monkeypatch.setattr(llm, "chat", lambda messages, **kwargs: material.append(messages[-1]["content"]) or ("总结", "fake"))
    report.summary_report()
    assert "驳回观点秘密标记" not in material[0]
    assert report.metadata()["summary"]["status"] == "latest"


def test_report_changes_during_generation_are_stale_and_failure_keeps_previous(task_env, monkeypatch):
    _seed_report_atoms()
    def changed_while_reading(*args, **kwargs):
        con = db.connect()
        con.execute("UPDATE knowledge_atom SET claim='更新的建议' WHERE id='accepted'")
        con.commit()
        con.close()
        return "旧快照总结", "fake"
    monkeypatch.setattr(llm, "chat", changed_while_reading)
    report.summary_report()
    assert report.metadata()["summary"]["status"] == "stale"
    old = report.read_report("summary")
    def fail(*args, **kwargs):
        raise RuntimeError("secret-upstream-detail")
    monkeypatch.setattr(llm, "chat", fail)
    with pytest.raises(RuntimeError):
        report.summary_report()
    meta = report.metadata()["summary"]
    assert meta["status"] == "failed" and meta["has_content"]
    assert report.read_report("summary") == old
    assert "secret-upstream-detail" not in json.dumps(meta)


def test_report_manifest_switch_failure_serves_the_previous_immutable_file(task_env, monkeypatch):
    _seed_report_atoms()
    report.checklist_report()
    old = report.read_report("checklist")
    con = db.connect()
    con.execute("UPDATE knowledge_atom SET claim='第二版建议' WHERE id='accepted'")
    con.commit()
    con.close()
    write = report._atomic_write
    def fail_publish(path, content):
        if path.name == "checklist.manifest.json" and json.loads(content).get("status") == "latest":
            raise OSError("simulated power-loss window")
        write(path, content)
    monkeypatch.setattr(report, "_atomic_write", fail_publish)
    with pytest.raises(OSError):
        report.checklist_report()
    assert report.read_report("checklist") == old
    assert report.metadata()["checklist"]["status"] == "failed"


@pytest.mark.parametrize("legacy_content", [None, "此前的无 manifest 报告"])
def test_first_manifest_failure_never_exposes_or_overwrites_legacy_content(task_env, monkeypatch, legacy_content):
    _seed_report_atoms()
    if legacy_content is not None:
        report.out_dir().mkdir(parents=True, exist_ok=True)
        (report.out_dir() / "checklist.md").write_text(legacy_content, encoding="utf-8")
    write = report._atomic_write

    def fail_publish(path, content):
        if path.name == "checklist.manifest.json" and json.loads(content).get("status") == "latest":
            raise OSError("simulated first manifest failure")
        write(path, content)

    monkeypatch.setattr(report, "_atomic_write", fail_publish)
    with pytest.raises(OSError):
        report.checklist_report()
    assert report.read_report("checklist") == legacy_content
    meta = report.metadata()["checklist"]
    assert meta["status"] == "failed"
    assert meta["has_content"] is (legacy_content is not None)
    assert meta["generated_at"] is None


def test_compatibility_mirror_failure_does_not_undo_manifest_publication(task_env, monkeypatch):
    _seed_report_atoms()
    write = report._atomic_write

    def fail_mirror(path, content):
        if path == report.out_dir() / "checklist.md":
            raise OSError("simulated compatibility mirror failure")
        write(path, content)

    monkeypatch.setattr(report, "_atomic_write", fail_mirror)
    report.checklist_report()
    assert "保留建议" in report.read_report("checklist")
    assert not (report.out_dir() / "checklist.md").exists()
    meta = report.metadata()["checklist"]
    assert meta["status"] == "latest" and meta["generated_at"]


def test_task_api_local_gate_and_validation(task_env, monkeypatch):
    from app.task_api import router
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(worker, "ensure_running", lambda: True)
    client = TestClient(app)
    result = client.post("/api/tasks", json={"kind": "report"})
    assert result.status_code == 202
    assert client.get(f"/api/tasks/{result.json()['task_id']}").status_code == 200
    assert client.get("/api/reports/meta").json()["reports"]["checklist"]["status"] == "generating"
    assert client.post("/api/tasks", json={"kind": []}).status_code == 400
    invalid = client.get("/api/tasks/not-an-id")
    assert invalid.status_code == 400 and isinstance(invalid.json()["detail"], str)
    assert invalid.json()["code"] == "INVALID_REQUEST"
    assert client.get("/api/tasks/99999999999999999999999").status_code == 400
    assert client.post("/api/tasks", json={"kind": "process", "video_id": "video", "payload": {"force": True}}).status_code == 400
    config.set_local({"node_role": "cloud"})
    assert client.post("/api/tasks", json={"kind": "judge"}).status_code == 403
    assert client.post("/api/jobs/1/retry", json={}).status_code == 403


def test_cross_process_lock_fences_a_live_worker_even_after_lease_expiry(task_env, monkeypatch):
    for kind, video in (("process", "video"), ("judge", None)):
        tasks.submit(kind, video)
    ready, release = task_env / "ready", task_env / "release"
    script = """
import pathlib, sys, time
from reno import worker
ready, release = map(pathlib.Path, sys.argv[1:])
def dispatch(job):
    if job['kind'] == 'process':
        ready.write_text('running')
        deadline = time.time() + 20
        while not release.exists():
            if time.time() > deadline: raise RuntimeError('test release timed out')
            time.sleep(.02)
    return {}
worker.dispatch = dispatch
worker.process_pending()
"""
    child_env = dict(os.environ, RENO_DATA_DIR=str(config.DATA), RENO_CONFIG_FILE=str(config.local_path()))
    process = subprocess.Popen([sys.executable, "-c", script, str(ready), str(release)],
                               env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 15
        while not ready.exists():
            assert process.poll() is None, process.communicate()[1].decode()
            assert time.time() < deadline
            time.sleep(.02)
        con = db.connect()
        con.execute("UPDATE worker_lease SET lease_until=0")
        con.execute("UPDATE job SET lease_until=0 WHERE status='running'")
        con.commit()
        con.close()
        monkeypatch.setattr(worker, "dispatch", lambda job: pytest.fail("second process must not write"))
        assert worker.process_pending() == 0
        assert {r["status"] for r in tasks.list_tasks()} == {"running", "pending"}
        release.write_text("continue")
        _, err = process.communicate(timeout=15)
        assert process.returncode == 0, err.decode()
        # Expired ownership is recovered on the next isolated worker pass.
        monkeypatch.setattr(worker, "dispatch", lambda job: {})
        worker.process_pending()
        assert all(r["status"] == "done" for r in tasks.list_tasks())
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_following_daemon_retries_busy_process_lock_without_another_request(task_env, monkeypatch):
    from contextlib import contextmanager
    import threading

    ready, release = task_env / "lock-ready", task_env / "lock-release"
    script = """
import pathlib, sys, time
from reno import tasks
ready, release = map(pathlib.Path, sys.argv[1:])
with tasks.process_lock() as acquired:
    assert acquired
    ready.write_text('locked')
    deadline = time.time() + 20
    while not release.exists():
        if time.time() > deadline: raise RuntimeError('test release timed out')
        time.sleep(.02)
"""
    child_env = dict(os.environ, RENO_DATA_DIR=str(config.DATA), RENO_CONFIG_FILE=str(config.local_path()))
    process = subprocess.Popen([sys.executable, "-c", script, str(ready), str(release)],
                               env=child_env, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 15
        while not ready.exists():
            assert process.poll() is None, process.communicate()[1].decode()
            assert time.time() < deadline
            time.sleep(.02)
        busy_seen, dispatched = threading.Event(), threading.Event()
        process_lock = tasks.process_lock

        @contextmanager
        def observed_lock(*, profile=None):
            assert profile is not None and profile.identity == storage_admin.profile().identity
            with process_lock(profile=profile) as acquired:
                if not acquired:
                    busy_seen.set()
                yield acquired

        monkeypatch.setattr(tasks, "process_lock", observed_lock)
        monkeypatch.setattr(worker, "dispatch", lambda job: dispatched.set() or {})
        queued = tasks.submit("judge")
        worker.ensure_running()
        assert busy_seen.wait(5)
        release.write_text("continue")
        _, err = process.communicate(timeout=5)
        assert process.returncode == 0, err.decode()
        assert dispatched.wait(5), "daemon must retry the OS lock without another submission"
        assert worker.stop_running(timeout=5)
        assert tasks.get(queued["task_id"])["status"] == "done"
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)


def test_schema_one_upgrade_preserves_jobs_and_merges_duplicate_active_scopes(task_env):
    import sqlite3
    path = task_env / "schema-one.db"
    schema = migrations.SCHEMA_PATH.read_text(encoding="utf-8").split("-- Task dispatch", 1)[0]
    start = schema.index("CREATE TABLE IF NOT EXISTS job (")
    end = schema.index("\n);", start)
    job_schema = schema[start:end]
    for name, declaration in migrations.ADDITIONS["job"].items():
        job_schema = job_schema.replace(",\n  " + name + " " + declaration, "")
    schema = schema[:start] + job_schema + schema[end:]
    con = sqlite3.connect(path)
    con.executescript(schema)
    con.executemany("INSERT INTO meta(key,value) VALUES (?,?)", [
        ("migration_version", "1"), ("library_id", str(uuid.uuid4())),
        ("library_epoch", "1"), ("kb_revision", "6")])
    con.execute("INSERT INTO video_asset(video_id,content_sha256) VALUES ('video','v1-sha')")
    con.executemany("INSERT INTO job(video_id,kind,status,created_at) VALUES (?,?,?,'2026-01-01T00:00:00Z')", [
        ("video", "process", "pending"), ("video", "process", "running"), (None, "judge", "pending")])
    con.commit()
    con.close()
    initialize_migration_storage(path)
    result = db.migrate(path, profile=migration_profile(path))
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    con = db.connect(path)
    rows = list(con.execute("SELECT * FROM job ORDER BY job_id"))
    assert len(rows) == 3
    assert [r["status"] for r in rows] == ["pending", "superseded", "pending"]
    assert rows[0]["scope_key"] == "process:video"
    assert rows[2]["scope_key"] == "judge:all" and rows[2]["video_id"] is None
    assert con.execute("SELECT COUNT(*) FROM task_request").fetchone()[0] == 3
    assert db.knowledge_version(con)["kb_revision"] == 6
    con.close()
    assert not db.migrate(path, profile=migration_profile(path)).changed


def test_cli_commands_use_the_same_queue_and_worker(task_env, monkeypatch, capsys):
    from reno import cli
    observed = []
    monkeypatch.setattr(worker, "dispatch", lambda job: observed.append((job["kind"], db.uj(job["payload_json"]))) or {})
    assert cli.main(["run", "video"]) == 0
    assert cli.main(["judge"]) == 0
    assert cli.main(["report"]) == 0
    assert cli.main(["report", "--summary"]) == 0
    assert observed == [("process", {"force": False}), ("judge", {"threshold": 0.22}),
                        ("report", {"mode": "static"}), ("report", {"mode": "summary"})]
    assert '"status": "done"' in capsys.readouterr().out


def test_killed_worker_is_recovered_after_lease_expiration(task_env, monkeypatch):
    queued = tasks.submit("process", "video")
    ready = task_env / "crash-ready"
    script = """
import pathlib, sys, time
from reno import worker
def dispatch(job):
    pathlib.Path(sys.argv[1]).write_text('running')
    time.sleep(30)
    return {}
worker.dispatch = dispatch
worker.process_pending()
"""
    child_env = dict(os.environ, RENO_DATA_DIR=str(config.DATA), RENO_CONFIG_FILE=str(config.local_path()))
    process = subprocess.Popen([sys.executable, "-c", script, str(ready)], env=child_env,
                               stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.time() + 15
        while not ready.exists():
            assert process.poll() is None, process.communicate()[1].decode()
            assert time.time() < deadline
            time.sleep(.02)
        process.terminate()
        process.wait(timeout=5)
        con = db.connect()
        con.execute("UPDATE worker_lease SET lease_until=0")
        con.execute("UPDATE job SET lease_until=0")
        con.commit()
        con.close()
        monkeypatch.setattr(worker, "dispatch", lambda job: {})
        assert worker.process_pending() == 1
        resumed = tasks.get(queued["task_id"])
        assert resumed["status"] == "done" and resumed["attempts"] == 2
    finally:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=5)
