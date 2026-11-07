"""Source consumers must not bypass tracked admission; no real platforms."""
import json
import hashlib
from pathlib import Path
from types import SimpleNamespace

import pytest

from reno import budget, config, db, favlist, import_tasks, ingest, storage_admin, tasks, worker
from reno.budget_contract import BudgetDeferred

CONTROLLED_SOURCE_REJECTION = (budget.BudgetLedgerError, BudgetDeferred)


@pytest.fixture(autouse=True)
def no_real_source_transport(monkeypatch):
    from reno import source_budget, source_contract
    # These integration checks use production scopes, ledger and consumers.
    # Physical-process behavior is verified separately in runner tests.
    monkeypatch.setattr(source_budget, "_components", lambda: (source_contract, SimpleNamespace()))


def test_enabled_bare_ingest_never_enters_unmetered_runner(monkeypatch):
    config.set_local({"node_role": "all", "budget_enabled": True})
    sends = []

    def unmetered(*args):
        sends.append(args)
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({"id": "BV1Synthetic", "title": "Synthetic"}))

    monkeypatch.setattr(ingest, "_ytdlp", unmetered)
    monkeypatch.setattr(ingest, "_register", lambda *a, **k: {"video_id": "BV1Synthetic", "status": "imported"})
    with pytest.raises(CONTROLLED_SOURCE_REJECTION):
        ingest.ingest_url("https://source.synthetic.test/video", enqueue_process=False, source_proof=False)
    assert sends == []


def test_enabled_bare_enumeration_never_enters_unmetered_runner(monkeypatch):
    config.set_local({"node_role": "all", "budget_enabled": True})
    sends = []

    def unmetered(*args):
        sends.append(args)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ingest, "_ytdlp", unmetered)
    with pytest.raises(CONTROLLED_SOURCE_REJECTION):
        favlist.enumerate_favlist("https://source.synthetic.test/list")
    assert sends == []


def test_enabled_raw_runner_cannot_bypass_typed_adapter(monkeypatch):
    config.set_local({"node_role": "all", "budget_enabled": True})
    sends = []

    def unmetered(*args, **kwargs):
        sends.append(args)
        return SimpleNamespace(returncode=0, stderr="", stdout="")

    monkeypatch.setattr(ingest.subprocess, "run", unmetered)
    monkeypatch.setattr(ingest, "_ytdlp_cmd", lambda: ["synthetic-unmetered"])
    with pytest.raises(CONTROLLED_SOURCE_REJECTION):
        ingest._ytdlp("--skip-download", "--dump-json", "https://source.synthetic.test/video")
    assert sends == []


def test_disabled_enumeration_preserves_legacy_empty_and_tuple_results(monkeypatch):
    config.set_local({"node_role": "all", "budget_enabled": False})
    responses = iter(["", json.dumps({"id": "BV1Synthetic", "title": "Synthetic"})])
    monkeypatch.setattr(ingest, "_ytdlp", lambda *a: SimpleNamespace(returncode=0, stderr="", stdout=next(responses)))
    assert favlist.enumerate_favlist("https://source.synthetic.test/list") == []
    assert favlist.enumerate_favlist("https://source.synthetic.test/list") == [("BV1Synthetic", "Synthetic")]


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


def test_import_worker_binds_original_source_scope(store, monkeypatch):
    from reno import source_budget
    seen = []

    def scoped_import(url, **kwargs):
        scope = source_budget.current_scope()
        original = worker.execution_context()
        assert scope.profile.identity == store.identity == kwargs["profile"].identity
        assert (scope.root_job_id, scope.continuation_id, scope.execution_generation) == (
            original["job_id"], original["continuation_id"], original["execution_generation"])
        seen.append(scope)
        with storage_admin.connection(store) as con:
            db.upsert_asset(con, {"video_id": "BV1Synthetic", "content_sha256": "synthetic-content"})
            con.commit()
        return {"video_id": "BV1Synthetic", "status": "imported"}

    monkeypatch.setattr(ingest, "ingest_url", scoped_import)
    import_tasks.submit_one("https://source.synthetic.test/video", process=False, profile=store)
    assert worker.process_pending() == 1
    assert len(seen) == 1 and source_budget.current_scope() is None


def test_favlist_worker_binds_scope_and_keeps_empty_selection(store, monkeypatch):
    from reno import source_budget
    seen = []

    def scoped_enumeration(url, cookies=None):
        scope = source_budget.current_scope()
        original = worker.execution_context()
        assert scope.profile.identity == store.identity
        assert scope.root_job_id == original["job_id"]
        assert scope.continuation_id == original["continuation_id"]
        seen.append(scope)
        return []

    monkeypatch.setattr(favlist, "enumerate_favlist", scoped_enumeration)
    import_tasks.submit_favlist("https://www.bilibili.com/list/synthetic", profile=store)
    assert worker.process_pending() == 1
    assert len(seen) == 1 and source_budget.current_scope() is None
    con = db.connect(store.database)
    try:
        jobs = con.execute("SELECT kind,status FROM job").fetchall()
        assert [(row["kind"], row["status"]) for row in jobs] == [("favlist", "done")]
    finally:
        con.close()


def test_register_failure_reuses_paid_media_when_new_daily_admission_is_zero(store, monkeypatch):
    from reno import source_budget, source_contract as codec
    config.set_local({"node_role": "all", "budget_enabled": True})
    physical = []

    def verify(artifact, *, profile):
        path = profile.media / artifact.relpath
        stat = path.stat()
        assert (stat.st_dev, stat.st_ino, stat.st_mtime_ns, stat.st_size) == (
            artifact.device, artifact.inode, artifact.mtime_ns, artifact.size_bytes)
        assert hashlib.sha256(path.read_bytes()).hexdigest() == artifact.sha256
        return path

    def run(plan, **kwargs):
        physical.append((plan.kind, plan.segment))
        if plan.kind == "source_metadata":
            body = codec.canonical_json({"id": "BV1Synthetic", "title": "Synthetic", "duration": 1})
            return codec.SourceProcessResult(plan.request_sha256, True, True, 0, "exited",
                stdout_bytes=len(body.encode()), stdout_sha256=hashlib.sha256(body.encode()).hexdigest(), metadata_json=body)
        assert plan.kind == "download"
        path = store.media / plan.artifact_relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(("synthetic-" + plan.segment).encode())
        stat = path.stat()
        artifact = codec.SourceArtifact(plan.segment, plan.artifact_relpath, stat.st_size,
            hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_dev, stat.st_ino, stat.st_mtime_ns)
        return codec.SourceProcessResult(plan.request_sha256, True, True, 0, "exited", artifact=artifact)

    runner = SimpleNamespace(prepare=lambda plan, **kwargs: plan, run=run,
        verify_artifact=verify, verify_prepared_files=lambda prepared: ())
    monkeypatch.setattr(source_budget, "_components", lambda: (codec, runner))
    original_register = ingest._register
    commits = []

    def register(*args, **kwargs):
        commits.append(True)
        if len(commits) == 1:
            raise RuntimeError("Synthetic business commit failure")
        return original_register(*args, **kwargs)

    monkeypatch.setattr(ingest, "_register", register)
    receipt = import_tasks.submit_one("https://source.synthetic.test/video", process=False, profile=store)
    assert worker.process_pending(max_jobs=1) == 1
    assert tasks.get(receipt["task_id"], profile=store)["status"] == "failed"
    assert physical == [("source_metadata", None), ("download", "video"), ("download", "audio")]
    config.set_local({"download_daily_videos": 0})
    retry = tasks.retry(receipt["task_id"], profile=store)
    assert worker.process_pending(max_jobs=1) == 1
    assert tasks.get(retry["task_id"], profile=store)["status"] == "done"
    assert len(physical) == 3 and len(commits) == 2
    con = db.connect(store.database)
    try:
        jobs = con.execute("SELECT continuation_id,execution_generation FROM job ORDER BY job_id").fetchall()
        assert tuple(jobs[0]) == tuple(jobs[1])
        asset = db.get_asset(con, "BV1Synthetic")
        assert asset is not None
        assert len(con.execute("SELECT * FROM budget_attempt").fetchall()) == 3
        files = json.loads(asset["files_json"])
        assert Path(store.media.parent / files["video"]).read_bytes() == b"synthetic-video"
    finally:
        con.close()
