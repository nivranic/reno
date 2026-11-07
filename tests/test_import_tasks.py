"""Persistent import entrypoints use artificial stores and never a platform."""
import json
from pathlib import Path
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from reno import config, db, import_tasks, ingest, storage_admin, tasks, worker


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "ORIG", tmp_path / "media/originals")
    config.set_local({"node_role": "all", "budget_enabled": False})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    yield profile
    assert worker.stop_running()


def rows(store, sql="SELECT * FROM job ORDER BY job_id", values=()):
    con = db.connect(store.database)
    try:
        return [dict(row) for row in con.execute(sql, values)]
    finally:
        con.close()


def client(monkeypatch):
    from app.api import router
    app = FastAPI()
    app.include_router(router)
    monkeypatch.setattr(worker, "ensure_running", lambda: True)
    return TestClient(app)


def test_single_api_only_queues_and_replays_after_completion(store, monkeypatch):
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("request thread downloaded"))
    request_id = str(uuid.uuid4())
    http = client(monkeypatch)
    payload = {"url": "https://www.bilibili.com/video/BV1Queue/", "request_id": request_id}
    first = http.post("/api/import", json=payload)
    assert first.status_code == 202
    value = first.json()
    assert value["status"] == "queued" and value["queued"] == 1
    job = rows(store)[0]
    assert job["kind"] == "ingest" and value["task_id"] == job["job_id"]
    with storage_admin.connection(store) as con:
        con.execute("UPDATE job SET status='done'")
        con.commit()
    again = http.post("/api/import", json=payload)
    assert again.status_code == 202 and again.json()["task_id"] == job["job_id"]
    assert len(rows(store)) == 1


def test_batch_api_has_stable_receipt_and_rejects_changed_selection(store, monkeypatch):
    http = client(monkeypatch)
    request_id = str(uuid.uuid4())
    payload = {"urls": ["https://video.test/a", "https://video.test/b"], "request_id": request_id}
    first = http.post("/api/import-batch", json=payload)
    assert first.status_code == 202 and first.json()["queued"] == 2
    before = rows(store)
    assert http.post("/api/import-batch", json=payload).json()["task_ids"] == first.json()["task_ids"]
    changed = http.post("/api/import-batch", json={**payload, "urls": payload["urls"][:1]})
    assert changed.status_code == 409 and changed.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    assert rows(store) == before


@pytest.mark.parametrize("kind,payload", [
    ("ingest", {"target": "C:/private/video.mp4"}),
    ("ingest", {"target": "file:///private/video.mp4"}),
    ("favlist", {"url": "https://video.test/list", "cookies": "C:/private/cookies.txt"}),
])
def test_public_task_entry_does_not_open_local_inputs(store, kind, payload):
    with pytest.raises(tasks.TaskError):
        tasks.submit(kind, payload=payload, profile=store)
    assert rows(store) == []


def test_local_cli_target_is_frozen_and_not_exposed_as_a_private_path(store, tmp_path):
    source = tmp_path / "private recording.mp4"
    source.write_bytes(b"artificial video")
    value = import_tasks.submit_one(str(source), allow_local=True, process=False, profile=store)
    job = rows(store)[0]
    assert json.loads(job["payload_json"])["target"] == str(source.resolve())
    assert str(tmp_path) not in json.dumps(value)
    assert value["payload"]["source"] == "file"


def test_worker_keeps_import_and_processing_in_original_job(store, monkeypatch):
    seen = []
    def fake_ingest(target, **kwargs):
        seen.append(("ingest", target, kwargs))
        assert kwargs["enqueue_process"] is False and kwargs["source_proof"] is False
        assert kwargs["profile"].identity == store.identity
        with storage_admin.connection(store) as con:
            db.upsert_asset(con, {"video_id": "video", "content_sha256": "artificial"})
            con.commit()
        return {"video_id": "video", "status": "imported"}
    monkeypatch.setattr(ingest, "ingest_url", fake_ingest)
    monkeypatch.setattr(worker.pipeline, "run_video", lambda video_id, force=False: seen.append(("pipeline", video_id)) or {})
    value = import_tasks.submit_one("https://video.test/a", profile=store)
    assert worker.process_pending() == 1
    jobs = rows(store)
    assert len(jobs) == 1 and jobs[0]["kind"] == "ingest" and jobs[0]["status"] == "done"
    assert jobs[0]["video_id"] == "video" and jobs[0]["job_id"] == value["task_id"]
    assert [x[0] for x in seen] == ["ingest", "pipeline"]


def test_budget_wait_keeps_original_import_lineage_and_does_not_fail(store, monkeypatch):
    from reno.budget_contract import BudgetDeferred
    def waiting(*args, **kwargs):
        raise BudgetDeferred("daily_limit")
    monkeypatch.setattr(ingest, "ingest_url", waiting)
    value = import_tasks.submit_one("https://video.test/a", profile=store)
    before = rows(store)[0]
    assert worker.process_pending() == 1
    after = rows(store)[0]
    assert after["status"] == "pending" and after["error"] is None and after["wait_mode"] == "blocked"
    assert after["continuation_id"] == before["continuation_id"] and after["job_id"] == value["task_id"]


def test_favlist_recovery_reuses_enumeration_and_child_receipts(store, monkeypatch):
    from reno import favlist
    seen = []
    monkeypatch.setattr(favlist, "enumerate_favlist", lambda url, cookies=None: seen.append(url) or
                        [("BV1ChildA", "A"), ("BV1ChildB", "B")])
    value = import_tasks.submit_favlist("https://www.bilibili.com/list/test", profile=store)
    assert worker.process_pending(max_jobs=1) == 1
    jobs = rows(store)
    assert [job["kind"] for job in jobs] == ["favlist", "ingest", "ingest"]
    # Replay the same worker execution after a missing outer task completion.
    original = jobs[0]
    with storage_admin.connection(store) as con:
        con.execute("UPDATE job SET status='running',owner_id='recovery' WHERE job_id=?", (value["task_id"],))
        con.commit()
    original["owner_id"] = "recovery"
    with worker._execution(original, "recovery", None, profile=store):
        replay = worker.dispatch(original)
    assert replay["task_ids"] == [job["job_id"] for job in jobs[1:]]
    assert len(rows(store)) == 3 and len(seen) == 1


def test_failed_import_retry_keeps_source_and_lineage(store):
    value = import_tasks.submit_one("https://video.test/a", profile=store)
    with storage_admin.connection(store) as con:
        con.execute("UPDATE job SET status='failed' WHERE job_id=?", (value["task_id"],))
        con.commit()
    before = rows(store)[0]
    retried = tasks.retry(value["task_id"], profile=store)
    after = rows(store)[-1]
    assert retried["kind"] == "ingest" and before["payload_json"] == after["payload_json"]
    assert before["continuation_id"] == after["continuation_id"]


def test_cli_import_submits_without_direct_download(store, monkeypatch, capsys):
    from reno.cli import main
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("CLI bypassed task queue"))
    monkeypatch.setattr(storage_admin, "setup", lambda: store)
    request_id = str(uuid.uuid4())
    assert main(["import", "https://video.test/a", "--request-id", request_id]) == 0
    assert main(["import", "https://video.test/a", "--request-id", request_id]) == 0
    assert len(rows(store)) == 1 and rows(store)[0]["status"] == "pending"
    assert '"task_id"' in capsys.readouterr().out


def test_favorites_compatibility_entrypoints_only_submit_and_do_not_sleep(store, monkeypatch):
    from reno import favlist
    import time
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("wrapper started a download"))
    monkeypatch.setattr(favlist, "enumerate_favlist", lambda *a, **k: pytest.fail("wrapper enumerated"))
    monkeypatch.setattr(time, "sleep", lambda *a: pytest.fail("extra batch pacing"))
    queued = favlist.import_urls([("BV1A", "A"), "https://video.test/b"])
    assert queued["queued"] == 2
    favorite = favlist.import_favlist("https://video.test/list")
    assert favorite["kind"] == "favlist" and len(rows(store)) == 3


def test_bad_favlist_checkpoint_cannot_trigger_free_reenumeration(store, monkeypatch):
    from reno import favlist
    value = import_tasks.submit_favlist("https://video.test/list", profile=store)
    job = rows(store)[0]
    key = "import:favlist:v1:" + job["continuation_id"] + ":1"
    with storage_admin.connection(store) as con:
        con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (key, "broken JSON"))
        con.commit()
    monkeypatch.setattr(favlist, "enumerate_favlist", lambda *a: pytest.fail("bad cached result re-enumerated"))
    assert worker.process_pending(max_jobs=1) == 1
    result = tasks.get(value["task_id"])
    assert result["status"] == "failed" and len(rows(store)) == 1


def test_local_copy_uses_fixed_profile_and_does_not_spawn_process_job(store, tmp_path, monkeypatch):
    source = tmp_path / "source.mp4"
    source.write_bytes(b"artificial local recording")
    monkeypatch.setattr(ingest, "_duration_of", lambda path: 1.0)
    value = import_tasks.submit_one(str(source), allow_local=True, process=False, profile=store)
    assert worker.process_pending() == 1
    job = rows(store)[0]
    assert job["status"] == "done" and len(rows(store)) == 1 and job["job_id"] == value["task_id"]
    original = store.media / "originals" / job["video_id"] / "video.mp4"
    assert original.read_bytes() == source.read_bytes()
    asset = rows(store, "SELECT files_json FROM video_asset")[0]
    assert json.loads(asset["files_json"])["video"] == original.relative_to(store.media.parent).as_posix()


def test_supplied_admission_connection_is_read_only_and_stays_open(store):
    value = import_tasks.submit_one("https://video.test/a", profile=store)
    with storage_admin.connection(store) as con:
        con.execute("UPDATE job SET status='running',owner_id='owner' WHERE job_id=?", (value["task_id"],))
        con.commit()
        job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (value["task_id"],)).fetchone())
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE job SET manual_paused=1 WHERE job_id=?", (value["task_id"],))
        from reno.budget_contract import BudgetDeferred
        with pytest.raises(BudgetDeferred):
            tasks.check_admission(job["job_id"], "owner", job["continuation_id"], job["execution_generation"],
                                  profile=store, connection=con)
        assert con.in_transaction and con.execute("SELECT manual_paused FROM job").fetchone()[0] == 1
        con.rollback()
        assert con.execute("SELECT manual_paused FROM job").fetchone()[0] == 0


@pytest.mark.parametrize("batch_first", [True, False])
def test_uuid_cannot_change_between_single_and_batch_intent(store, batch_first):
    request_id = str(uuid.uuid4())
    targets = ["https://video.test/a", "https://video.test/b"]
    if batch_first:
        import_tasks.submit_batch(targets, request_id, profile=store)
    else:
        import_tasks.submit_one(targets[0], request_id, profile=store)
    before = rows(store)
    with pytest.raises(tasks.TaskError, match="请求编号") as error:
        if batch_first:
            import_tasks.submit_one(targets[0], request_id, profile=store)
        else:
            import_tasks.submit_batch(targets, request_id, profile=store)
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED" and rows(store) == before


@pytest.mark.parametrize("damage", ["ghost", "missing_child_receipt", "bool_count", "child_payload"])
def test_favlist_cache_revalidates_exact_batch_and_child_receipts(store, monkeypatch, damage):
    from reno import favlist
    seen = []
    monkeypatch.setattr(favlist, "enumerate_favlist", lambda *a: seen.append("enum") or [("BV1Child", "A")])
    value = import_tasks.submit_favlist("https://video.test/list", profile=store)
    assert worker.process_pending(max_jobs=1) == 1
    job = rows(store)[0]
    key = "import:favlist:v1:" + job["continuation_id"] + ":1"
    with storage_admin.connection(store) as con:
        cached = json.loads(con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0])
        if damage == "ghost":
            cached["result"]["task_ids"] = [999999]
        elif damage == "bool_count":
            cached["result"]["queued"] = True
        elif damage == "missing_child_receipt":
            con.execute("DELETE FROM task_request WHERE job_id=?", (cached["result"]["task_ids"][0],))
        else:
            con.execute("UPDATE job SET payload_json=? WHERE job_id=?",
                        (json.dumps({"target": "https://video.test/tampered", "source": "url", "process": True}),
                         cached["result"]["task_ids"][0]))
        con.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(cached), key))
        con.execute("UPDATE job SET status='running',owner_id='recovery' WHERE job_id=?", (value["task_id"],))
        con.commit()
    job["owner_id"] = "recovery"
    with worker._execution(job, "recovery", None, profile=store), pytest.raises(tasks.TaskError) as error:
        worker.dispatch(job)
    assert error.value.code == "IMPORT_RECOVERY_INVALID" and seen == ["enum"]


def test_empty_favorites_have_a_verifiable_zero_child_receipt(store, monkeypatch):
    from reno import favlist
    seen = []
    monkeypatch.setattr(favlist, "enumerate_favlist", lambda *a: seen.append("enum") or [])
    import_tasks.submit_favlist("https://video.test/list", profile=store)
    assert worker.process_pending(max_jobs=1) == 1
    job = rows(store)[0]
    with storage_admin.connection(store) as con:
        con.execute("UPDATE job SET status='running',owner_id='recovery' WHERE job_id=?", (job["job_id"],))
        con.commit()
    job["owner_id"] = "recovery"
    with worker._execution(job, "recovery", None, profile=store):
        result = worker.dispatch(job)
    assert type(result["queued"]) is int and result["queued"] == 0 and result["task_ids"] == []
    assert len(rows(store)) == 1 and seen == ["enum"]


def test_single_receipt_cannot_be_redirected_to_another_existing_job(store):
    request_id = str(uuid.uuid4())
    original = import_tasks.submit_one("https://video.test/a", request_id, profile=store)
    other = import_tasks.submit_one("https://video.test/b", profile=store)
    with storage_admin.connection(store) as con:
        con.execute("UPDATE task_request SET job_id=? WHERE request_id=?", (other["task_id"], request_id))
        con.commit()
    with pytest.raises(tasks.TaskError) as error:
        import_tasks.submit_one("https://video.test/a", request_id, profile=store)
    assert error.value.code == "IMPORT_RECOVERY_INVALID" and len(rows(store)) == 2
    assert original["task_id"] != other["task_id"]


@pytest.mark.parametrize("metadata", [
    '{"id":"BV1A"}\n{"id":"BV1B"}',
    '{"id":"playlist","_type":"playlist","entries":[{"id":"BV1A"}]}',
])
def test_queued_local_import_cannot_turn_one_video_into_a_playlist(store, monkeypatch, metadata):
    from subprocess import CompletedProcess
    calls = []
    monkeypatch.setattr(ingest, "_ytdlp", lambda *args: calls.append(args) or CompletedProcess([], 0, metadata, ""))
    with pytest.raises(tasks.TaskError) as error:
        ingest.ingest_url("https://video.test/a", enqueue_process=False, source_proof=False, profile=store)
    assert error.value.code == "IMPORT_SOURCE_INVALID" and len(calls) == 1
    assert "--no-playlist" in calls[0] and not (store.media / "originals").exists()
    assert rows(store, "SELECT * FROM video_asset") == []


def test_cloud_queue_receipt_identifies_its_role_without_health_race(store, monkeypatch):
    config.set_local({"node_role": "cloud"})
    http = client(monkeypatch)
    single = http.post("/api/import", json={"url": "https://www.bilibili.com/video/BV1Role/"})
    batch = http.post("/api/import-batch", json={"urls": ["https://www.bilibili.com/video/BV1Batch/"]})
    assert single.json()["node_role"] == batch.json()["node_role"] == "cloud"
    assert rows(store) == []
