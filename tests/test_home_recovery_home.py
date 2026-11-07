"""Real isolated SQL/State recovery; transport and providers are never called."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import budget, budget_contract, db, home_recovery as recovery
from reno import home_recovery_schema as schema, home_recovery_source as sources
from reno import config, migrations, model_scopes, review, storage_admin, storage_lifecycle, sync_queue, sync_state, tasks
from tests.schema_fixtures import assert_released16_image, initialize_released16, released16


def build_home16(tmp_path):
    """Cold genuine16 seed; its caller owns the complete released16 scope."""
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                               tmp_path / "reports", tmp_path / "checkpoints")
    initialize_released16(profile.database)
    storage_lifecycle.initialize(profile)
    video, audio, frame = (profile.media / name for name in ("originals/video.mp4", "audio/audio.m4a", "frames/frame.jpg"))
    for path, raw in ((video, b"synthetic-video"), (audio, b"synthetic-audio"), (frame, b"synthetic-frame")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    with storage_admin.connection(profile) as con:
        db.upsert_asset(con, {"video_id": "home_video", "source_type": "local_file", "title": "人工样本",
            "content_sha256": hashlib.sha256(video.read_bytes()).hexdigest(), "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "files": {"video": video.relative_to(profile.media.parent).as_posix(), "audio": audio.relative_to(profile.media.parent).as_posix()}, "status": "processed"}, profile=profile)
        db.replace_frames(con, "home_video", [{"frame_id": "frame_0", "pts_ms": 0, "origin": "scene", "dhash": "0x00", "path": frame.relative_to(profile.media.parent).as_posix()}], profile=profile)
        db.replace_segments(con, "home_video", [{"id": "asr_0", "start_ms": 0, "end_ms": 100, "text": "人工原文"}])
        db.replace_atoms(con, "home_video", [{"id": "home_atom", "claim": "原知识", "polarity": "recommend", "evidence_refs": [{"modality": "asr", "source_item_id": "asr_0", "start_ms": 0, "end_ms": 100, "evidence_text": "人工原文"}]}])
        con.execute("INSERT INTO meta VALUES('step:home_video:atomize','original-step')")
        con.execute("INSERT INTO meta VALUES('private-test-blob',?)", (b"\x00\xffprivate-stays-local",))
        con.commit()
        library, node = sync_queue._local_identity(con)
    identifier = str(uuid.uuid4())
    sources.create_checkpoint(profile=profile, source_id=identifier)
    assert_released16_image(profile.database)
    return profile, identifier, library, node


@contextmanager
def released16_home(tmp_path, monkeypatch):
    """Keep old producers, worker/State owners and teardown in their16 scope."""
    with released16(monkeypatch):
        yield build_home16(tmp_path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    with released16_home(tmp_path, monkeypatch) as value:
        yield value


def state(env):
    profile, _, library, node = env
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    store.initialize({"home_library_id": library, "home_node_id": node, "target_library_id": str(uuid.uuid4()),
                      "target_node_id": str(uuid.uuid4()), "target_url": "https://synthetic.example.test"}, 1)
    return store


def changed(env):
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE knowledge_atom SET claim='后来的知识' WHERE id='home_atom'")
        con.execute("UPDATE meta SET value='changed-step' WHERE key='step:home_video:atomize'")
        con.commit()


def observed(env):
    return recovery.preview(profile=env[0], source_id=env[1])


def apply(env, preview=None, identifier=None):
    view = observed(env) if preview is None else preview
    identifier = identifier or str(uuid.uuid4())
    return identifier, recovery.apply(profile=env[0], source_id=env[1], expected_digest=view["digest"], operation_id=identifier)


def rows(env):
    with storage_admin.connection(env[0]) as con:
        return {name: [tuple(r) for r in con.execute('SELECT * FROM "' + name + '" ORDER BY 1')]
                for name in ("meta", "job", "task_request", "budget_operation", "budget_attempt", "user_decision", "renovation_project", "source_record", "knowledge_atom", schema.TABLE)}


def test_preview_is_pure_and_does_not_create_worker_state_or_receipt(env):
    changed(env)
    profile = env[0]
    files = {p.relative_to(profile.database.parent).as_posix(): p.read_bytes() for p in profile.database.parent.rglob("*") if p.is_file() and p.name not in ("reno.db", "reno.db-wal", "reno.db-shm")}
    before = rows(env)
    view = observed(env)
    assert view["source"]["source_kind"] == "home_checkpoint"
    assert view["semantic_closure"]["job_video_refs"] == 0
    assert rows(env) == before
    assert not (profile.database.parent / "sync-state.json").exists()
    after = {p.relative_to(profile.database.parent).as_posix(): p.read_bytes() for p in profile.database.parent.rglob("*") if p.is_file() and p.name not in ("reno.db", "reno.db-wal", "reno.db-shm")}
    assert after == files
    public = json.dumps(view)
    assert "private-stays-local" not in public and str(profile.database) not in public


def test_restore_knowledge_preserves_waiting_jobs_personal_blob_and_actual_state(env):
    store = state(env)
    job = tasks.submit("report", payload={"mode": "static"}, profile=env[0])["job_id"]
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE job SET manual_paused=1,wait_mode='blocked',blocked_reason='manual_pause' WHERE job_id=?", (job,))
        con.commit()
    changed(env)
    before_rows, before_state = rows(env), store.read()
    identifier, result = apply(env)
    after = rows(env)
    assert result["replayed"] is False
    assert result["after_version"]["library_epoch"] == 2
    assert result["after_version"]["kb_revision"] > result["before_version"]["kb_revision"]
    for table in ("job", "task_request", "budget_operation", "budget_attempt", "user_decision", "renovation_project", "source_record"):
        assert after[table] == before_rows[table]
    assert store.read() == {**before_state, "local_epoch": 2}
    with storage_admin.connection(env[0]) as con:
        assert con.execute("SELECT claim FROM knowledge_atom WHERE id='home_atom'").fetchone()[0] == "原知识"
        assert con.execute("SELECT value FROM meta WHERE key='private-test-blob'").fetchone()[0] == b"\x00\xffprivate-stays-local"
        assert con.execute("SELECT value FROM meta WHERE key='step:home_video:atomize'").fetchone()[0] == "original-step"
        assert con.execute("SELECT atom_id FROM atom_fts WHERE atom_fts MATCH '原 AND 知 AND 识'").fetchone()[0] == "home_atom"
        assert con.execute("PRAGMA foreign_key_check").fetchone() is None
        receipt = dict(con.execute('SELECT * FROM ' + schema.TABLE + ' WHERE operation_id=?', (identifier,)).fetchone())
        schema.validate_record(receipt)


def test_sql_commit_state_crash_gap_original_uuid_resumes_without_source(env, monkeypatch):
    store = state(env)
    changed(env)
    view, identifier = observed(env), str(uuid.uuid4())
    before_state = store.read()
    original = recovery._reconcile
    def crash(*args, **kwargs):
        raise RuntimeError("controlled-state-crash")
    monkeypatch.setattr(recovery, "_reconcile", crash)
    with pytest.raises(RuntimeError, match="controlled-state-crash"):
        recovery.apply(profile=env[0], source_id=env[1], expected_digest=view["digest"], operation_id=identifier)
    assert store.read() == before_state
    with storage_admin.connection(env[0]) as con:
        assert db.knowledge_version(con)["library_epoch"] == 2
        assert con.execute('SELECT COUNT(*) FROM ' + schema.TABLE).fetchone()[0] == 1
    # Retire/remove only the isolated source declaration, proving replay is
    # receipt-first and does not require the original source to remain open.
    path = env[0].database.parent / sources.DIRECTORY / (env[1] + ".json")
    path.unlink()
    monkeypatch.setattr(recovery, "_reconcile", original)
    first = recovery.resume(profile=env[0], operation_id=identifier)
    assert first["replayed"] and store.read() == {**before_state, "local_epoch": 2}
    after = rows(env)
    again = recovery.apply(profile=env[0], source_id=env[1], expected_digest=view["digest"], operation_id=identifier)
    assert again == first and rows(env) == after


def test_cas_and_changed_original_body_do_not_restore_or_create_backup(env):
    changed(env)
    view = observed(env)
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE meta SET value=? WHERE key='private-test-blob'", (b"different-private",))
        con.commit()
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="CAS_MISMATCH"):
        apply(env, view)
    assert rows(env) == before
    identifier, _ = apply(env)
    with pytest.raises(recovery.HomeRecoveryError, match="OPERATION_CONFLICT"):
        recovery.apply(profile=env[0], source_id=env[1], expected_digest="0" * 64, operation_id=identifier)


def test_missing_any_historical_job_video_rejects_before_backup(env):
    with storage_admin.connection(env[0]) as con:
        con.execute("INSERT INTO job(video_id,kind,status) VALUES('missing-original','process','done')")
        con.commit()
    with pytest.raises(recovery.HomeRecoveryError, match="JOB_INPUT_MISSING"):
        observed(env)
    assert not list(env[0].database.parent.glob("*.bak-home-restore-*"))


@pytest.mark.parametrize("mutation", [
    "CREATE TRIGGER sabotage AFTER UPDATE ON video_asset BEGIN UPDATE job SET manual_paused=0; END",
    "DROP TRIGGER kb_atom_insert",
])
def test_actual_unknown_or_missing_trigger_is_rejected_in_preview(env, mutation):
    with storage_admin.connection(env[0]) as con:
        con.execute(mutation)
        con.commit()
    with pytest.raises((recovery.HomeRecoveryError, migrations.SchemaVersionError)):
        observed(env)


def test_worker_busy_refuses_apply_without_stopping_worker(env):
    view = observed(env)
    with tasks.process_lock(profile=env[0]) as acquired:
        assert acquired
        with pytest.raises(recovery.HomeRecoveryError, match="WORKER_BUSY"):
            apply(env, view)
        assert tasks.holds_process_lock(profile=env[0])


def reserve(env, job, *, dispatch=False, settle=False):
    with storage_admin.connection(env[0]) as con:
        row = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job,)).fetchone())
    operation = budget.OperationSpec(row["continuation_id"], row["execution_generation"], "model", "outside-home:opaque-consumer", "1" * 64, job)
    policy = budget_contract.parse_policy({"budget_enabled": True})
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    value = budget.reserve(operation, budget.AttemptSpec("2" * 64, adapter="paas_v4", effective_max_tokens=100), profile=env[0], policy=policy, now=now)
    if dispatch:
        assert budget.mark_dispatching(value.attempt_id, value.reservation_sha256, str(uuid.uuid4()), expected_revision=value.revision, profile=env[0], policy=policy, now=now).won
    if settle:
        budget.settle(value.attempt_id, "2" * 64, profile=env[0], result_text="controlled-paid-response", now=now)
    return value


@pytest.mark.parametrize("unknown", [False, True])
def test_unfinished_paid_outside_home_input_opaque_or_unknown_preserved_and_rejected(env, unknown):
    job = tasks.submit("process", "home_video", profile=env[0])["job_id"]
    reserve(env, job, dispatch=unknown)
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="UNFINISHED_INPUT_OPAQUE"):
        observed(env)
    assert rows(env) == before


def test_historical_applied_paid_output_does_not_lock_current_knowledge(env):
    job = tasks.submit("process", "home_video", profile=env[0])["job_id"]
    reserve(env, job, dispatch=True, settle=True)
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE job SET status='done' WHERE job_id=?", (job,))
        con.commit()
    changed(env)
    before = rows(env)
    apply(env)
    after = rows(env)
    assert after["budget_operation"] == before["budget_operation"]
    assert after["budget_attempt"] == before["budget_attempt"]


def test_resume_missing_operation_is_not_a_new_restore(env):
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="OPERATION_MISSING"):
        recovery.resume(profile=env[0], operation_id=str(uuid.uuid4()))
    assert rows(env) == before


@pytest.mark.parametrize("initial_state", [False, True])
def test_replay_rejects_state_epoch_ahead_of_actual_database(env, initial_state):
    store = state(env) if initial_state else None
    identifier, _ = apply(env)
    store = state(env) if store is None else store
    store.update({"local_epoch": 3})
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="STATE_CHANGED"):
        recovery.resume(profile=env[0], operation_id=identifier)
    assert rows(env) == before and store.read()["local_epoch"] == 3


def test_unrelated_historical_unknown_is_kept_and_does_not_block_restore(env):
    job = tasks.submit("process", "home_video", profile=env[0])["job_id"]
    reserve(env, job, dispatch=True)
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE job SET status='done' WHERE job_id=?", (job,))
        con.commit()
    changed(env)
    before = rows(env)
    apply(env)
    after = rows(env)
    assert after["budget_attempt"] == before["budget_attempt"]
    assert after["budget_operation"] == before["budget_operation"]
    assert after["job"] == before["job"]


def source_reservation(env, operation, *, request_sha="2" * 64, kind="source_metadata", dispatch=True, ordinal=1, now=None):
    policy = budget_contract.parse_policy({"budget_enabled": True})
    now = now or datetime(2026, 10, 2, tzinfo=timezone.utc)
    arguments = ({"resolver_contract_version": 1, "resolver_phase": "dns", "resolver_max_attempts": config.source_resolution_attempt_limit()}
                 if kind == "source_resolution" else {"source_contract_version": 1})
    value = budget.reserve(operation, budget.AttemptSpec(request_sha, ordinal=ordinal, **arguments), profile=env[0], policy=policy, now=now)
    if dispatch:
        assert budget.mark_dispatching(value.attempt_id, value.reservation_sha256, str(uuid.uuid4()), expected_revision=value.revision, profile=env[0], policy=policy, now=now).won
    return value


def test_current_unknown_source_metadata_with_exact_task_input_can_restore(env):
    target = "https://www.bilibili.com/video/BVfake123"
    job = tasks.submit("ingest", payload={"target": target}, profile=env[0])["job_id"]
    with storage_admin.connection(env[0]) as con:
        lineage = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job,)).fetchone())
    logical = recovery._sha({"kind": "source_metadata", "source": target, "runner_contract": "yt_dlp_v1"})
    operation = budget.OperationSpec(lineage["continuation_id"], lineage["execution_generation"], "source_metadata", "ingest:metadata:v1", logical, job)
    from reno import source_contract
    source_reservation(env, operation, request_sha=source_contract.SourcePlan("source_metadata", target).request_sha256)
    changed(env)
    before = rows(env)
    view = observed(env)
    assert len(view["semantic_closure"]["compatible_source_operations"]) == 1
    apply(env, view)
    after = rows(env)
    for name in ("job", "budget_operation", "budget_attempt"):
        assert after[name] == before[name]


def home_dns(env):
    store = state(env)
    job = tasks.submit_sync(profile=env[0])
    with storage_admin.connection(env[0]) as con:
        lineage = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job,)).fetchone())
    binding = store.read()["binding"]
    stream = {"library_id": binding["target_library_id"], "library_epoch": 1}
    entry = {"item_id": 7, "url": "https://www.bilibili.com/video/BVfake123", "attempts": 1,
        "lease_until": "2030-01-01T00:00:00.000Z", "claim": {"item_id": 7, "lease_generation": 1, "lease_token": str(uuid.uuid4())}}
    store.update({"claims": [entry]})
    store.observe_recovery({"generation": 0, "revision": 0, "state": "running", "source_floor": None, "stream": stream})
    store.enable_source_execution()
    core = {"format_version": 1, "binding": binding, "item_id": 7, "submitted_url_sha256": hashlib.sha256(entry["url"].encode()).hexdigest(),
        "root_job_id": job, "continuation_id": lineage["continuation_id"], "execution_generation": lineage["execution_generation"],
        "local_epoch": 1, "recovery_generation": 0, "recovery_stream": stream}
    namespace = recovery._sha({k: core[k] for k in ("binding", "item_id", "submitted_url_sha256")})
    from reno import source_resolution_contract as codec
    core["resolution_input_sha256"] = recovery._sha({"format_version": 1, "kind": "source_resolution", "resolver_contract": codec.RESOLVER_CONTRACT,
        "namespace": namespace, "submitted_url_sha256": core["submitted_url_sha256"]})
    store.install_source_execution(core)
    operation = budget.OperationSpec(core["continuation_id"], core["execution_generation"], "source_resolution", "home:" + namespace + ":resolution:v1", core["resolution_input_sha256"], job)
    plan = codec.ResolutionPlan("dns", entry["url"], entry["url"], operation.input_sha256, config.source_resolution_attempt_limit())
    return store, operation, plan


def test_current_unknown_home_dns_preserves_original_core_and_paid_uuid(env):
    store, operation, plan = home_dns(env)
    source_reservation(env, operation, request_sha=plan.request_sha256, kind="source_resolution")
    changed(env)
    before, saved = rows(env), store.read()
    apply(env)
    assert store.read() == {**saved, "local_epoch": 2}
    assert store.read()["source_execution"] == saved["source_execution"]
    for name in ("job", "budget_operation", "budget_attempt"):
        assert rows(env)[name] == before[name]


@pytest.mark.parametrize("unknown", [False, True])
def test_backfill_paid_or_unknown_fixed_witness_allows_unrelated_knowledge_restore(env, monkeypatch, unknown):
    from scripts import backfill_atoms_v2 as backfill
    monkeypatch.setattr(config, "budget_settings", lambda: {"budget_enabled": True})
    with storage_admin.connection(env[0]) as con:
        # Only an unrelated candidate changes after checkpoint creation.
        db.replace_atoms(con, "home_video", [{"id": "home_atom", "claim": "原知识", "polarity": "recommend", "evidence_refs": [{"modality": "asr", "source_item_id": "asr_0", "start_ms": 0, "end_ms": 100, "evidence_text": "人工原文"}]},
            {"id": "unrelated_atom", "claim": "后来的无关知识", "polarity": "recommend", "evidence_refs": []}])
        # Source row timestamps/evidence IDs are original frozen inputs too.
        with sources.open_source(con, env[1], profile=env[0]) as source:
            original = source.rows["knowledge_atom"][0]
            con.execute("UPDATE knowledge_atom SET created_at=? WHERE id='home_atom'", (original["created_at"],))
            con.execute("UPDATE evidence_ref SET id=1 WHERE atom_id='home_atom'")
        con.commit()
    with model_scopes.execution(env[0], "backfill_v2", {"selector": "dimension-is-null"}, selection=("home_atom",)) as execution:
        with storage_admin.connection(env[0]) as con:
            fixed = backfill._fixed_rows(con, ["home_atom"])
            key, witness = backfill._batch_witness(con, execution, 0, fixed)
        backfill._batch_guard(execution, key, witness, ("home_atom",))
        operation = budget.OperationSpec(execution.continuation_id, execution.execution_generation, "model", "backfill-v2:batch:0", "1" * 64)
        policy = budget_contract.parse_policy({"budget_enabled": True})
        value = budget.reserve(operation, budget.AttemptSpec("2" * 64, adapter="paas_v4", effective_max_tokens=100), profile=env[0], policy=policy)
        assert budget.mark_dispatching(value.attempt_id, value.reservation_sha256, str(uuid.uuid4()), expected_revision=value.revision, profile=env[0], policy=policy).won
        if not unknown:
            budget.settle(value.attempt_id, "2" * 64, profile=env[0], result_text="controlled-paid-response")
    before = rows(env)
    apply(env)
    after = rows(env)
    assert after["budget_operation"] == before["budget_operation"]
    assert after["budget_attempt"] == before["budget_attempt"]
    assert [r for r in after["meta"] if r[0].startswith("budget:")] == [r for r in before["meta"] if r[0].startswith("budget:")]
    with storage_admin.connection(env[0]) as con:
        assert con.execute("SELECT 1 FROM knowledge_atom WHERE id='unrelated_atom'").fetchone() is None


def test_pending_process_completed_output_and_step_checkpoint_cannot_be_rolled_back(env):
    tasks.submit("process", "home_video", profile=env[0])
    changed(env)
    with pytest.raises(recovery.HomeRecoveryError, match="UNFINISHED_CHECKPOINT_MISMATCH"):
        observed(env)


def test_current_bound_reviews_are_replayed_without_rewriting_raw_events(env):
    with storage_admin.connection(env[0]) as con:
        event = review.record_decision(con, atom_id="home_atom", action="reject", note="controlled-review")
        con.commit()
    before = rows(env)
    apply(env)
    after = rows(env)
    assert after["user_decision"] == before["user_decision"]
    with storage_admin.connection(env[0]) as con:
        assert con.execute("SELECT status FROM knowledge_atom WHERE id='home_atom'").fetchone()[0] == "rejected"


def test_pre_sql_crash_binds_original_request_and_exact_retry_reuses_protection(env, monkeypatch):
    changed(env)
    view, identifier = observed(env), str(uuid.uuid4())
    original = recovery._write_knowledge
    monkeypatch.setattr(recovery, "_write_knowledge", lambda *_: (_ for _ in ()).throw(RuntimeError("controlled-pre-sql-crash")))
    with pytest.raises(RuntimeError, match="controlled-pre-sql-crash"):
        recovery.apply(profile=env[0], source_id=env[1], expected_digest=view["digest"], operation_id=identifier)
    intent = env[0].database.parent / recovery.INTENT_DIRECTORY / (identifier + ".intent.json")
    frozen = intent.read_bytes()
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="OPERATION_CONFLICT"):
        recovery.apply(profile=env[0], source_id=env[1], expected_digest="0" * 64, operation_id=identifier)
    assert rows(env) == before and intent.read_bytes() == frozen
    monkeypatch.setattr(recovery, "_write_knowledge", original)
    recovery.apply(profile=env[0], source_id=env[1], expected_digest=view["digest"], operation_id=identifier)
    assert intent.read_bytes() == frozen
    assert len(list(env[0].database.parent.glob("*.bak-home-restore-" + identifier))) == 1


def test_existing_protection_image_cannot_acquire_invented_request_intent(env):
    identifier = str(uuid.uuid4())
    backup_id = str(uuid.uuid5(uuid.UUID(identifier), "reno.home.knowledge.restore.protection.v1"))
    destination = env[0].database.with_name(env[0].database.name + ".bak-home-restore-" + identifier)
    with storage_admin.connection(env[0]) as con:
        db.snapshot(con, destination, profile=env[0], kind="maintenance", backup_id=backup_id)
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="PROTECTION_INTENT_MISSING"):
        apply(env, identifier=identifier)
    assert rows(env) == before
    assert not (env[0].database.parent / recovery.INTENT_DIRECTORY / (identifier + ".intent.json")).exists()


@pytest.mark.parametrize("sidecar", [False, True])
def test_actual_protection_verify_to_pin_substitution_cannot_commit(env, monkeypatch, sidecar):
    from reno import storage_backups as backups
    original = backups._verify_ready
    changed(env)
    before = rows(env)
    def substitute(record, profile):
        result = original(record, profile)
        if ".bak-home-restore-" in record["destination"]:
            path = Path(record["destination"])
            if sidecar:
                path = path.with_name(path.name + backups.SUFFIX)
                body = path.read_bytes()
                body = body.replace(b"maintenance", b"maintenanco", 1)
            else:
                body = path.read_bytes()
                body = b"X" + body[1:]
            path.write_bytes(body)
        return result
    monkeypatch.setattr(backups, "_verify_ready", substitute)
    with pytest.raises((ValueError, recovery.SyncError)):
        apply(env)
    assert rows(env) == before


def test_original_receipt_replay_after_second_restore_does_not_regress_epoch(env):
    store = state(env)
    first, result = apply(env)
    apply(env)
    before = rows(env)
    replay = recovery.resume(profile=env[0], operation_id=first)
    assert replay == {**result, "replayed": True}
    assert store.read()["local_epoch"] == 3 and rows(env) == before


def test_state_changed_after_sql_commit_does_not_get_rewritten_as_recovery(env, monkeypatch):
    store = state(env)
    identifier = str(uuid.uuid4())
    original = recovery._reconcile
    def changed_state(con, actual_store, record):
        status = {**actual_store.read()["status"], "state": "controlled_change"}
        actual_store.update({"status": status})
        return original(con, actual_store, record)
    monkeypatch.setattr(recovery, "_reconcile", changed_state)
    with pytest.raises(recovery.HomeRecoveryError, match="STATE_CHANGED"):
        apply(env, identifier=identifier)
    assert store.read()["local_epoch"] == 1 and store.read()["status"]["state"] == "controlled_change"
    with storage_admin.connection(env[0]) as con:
        assert db.knowledge_version(con)["library_epoch"] == 2
        assert con.execute('SELECT COUNT(*) FROM ' + schema.TABLE).fetchone()[0] == 1


def test_actual_database_writer_busy_refuses_without_leaving_restore_intent(env):
    view = observed(env)
    con = sqlite3.connect(env[0].database)
    try:
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(recovery.HomeRecoveryError, match="DATABASE_BUSY"):
            apply(env, view)
    finally:
        con.rollback()
        con.close()
    assert not (env[0].database.parent / recovery.INTENT_DIRECTORY).exists()


def test_actual_changed_knowledge_fk_contract_is_rejected_before_backup(env):
    with storage_admin.connection(env[0]) as con:
        table = con.execute("SELECT sql FROM sqlite_master WHERE name='knowledge_atom'").fetchone()[0]
        triggers = [r[0] for r in con.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND tbl_name='knowledge_atom'")]
        data = [tuple(r) for r in con.execute("SELECT * FROM knowledge_atom")]
        con.execute("ALTER TABLE knowledge_atom RENAME TO old_atoms")
        ddl = table[:table.rfind(")")] + ", FOREIGN KEY(video_id) REFERENCES video_asset(video_id) ON DELETE CASCADE)"
        con.execute(ddl)
        con.executemany("INSERT INTO knowledge_atom VALUES(" + ",".join("?" for _ in data[0]) + ")", data)
        con.execute("DROP TABLE old_atoms")
        for trigger in triggers:
            con.execute(trigger)
        con.commit()
    with pytest.raises(recovery.HomeRecoveryError, match="FOREIGN_KEY_CONTRACT"):
        observed(env)
    assert not list(env[0].database.parent.glob("*.bak-home-restore-*"))


def test_failed_original_job_completed_retry_does_not_lock_historical_paid_output(env):
    job = tasks.submit("process", "home_video", profile=env[0])["job_id"]
    reserve(env, job, dispatch=True, settle=True)
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE job SET status='failed' WHERE job_id=?", (job,))
        con.commit()
    continued = tasks.retry(job, profile=env[0])["job_id"]
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE job SET status='done' WHERE job_id=?", (continued,))
        con.commit()
    changed(env)
    before = rows(env)
    apply(env)
    after = rows(env)
    for name in ("job", "budget_operation", "budget_attempt"):
        assert after[name] == before[name]


def test_downloaded_work_cannot_lose_committed_child_output_checkpoint(env):
    store = state(env)
    # A phase update may lag the child's SQL commit. The actual step marker,
    # rather than only the work.phase label, protects that consumed output.
    entry = {"item_id": 7, "url": "https://www.bilibili.com/video/BVfake123", "attempts": 1,
        "lease_until": "2030-01-01T00:00:00.000Z", "claim": {"item_id": 7, "lease_generation": 1, "lease_token": str(uuid.uuid4())}}
    store.update({"claims": [entry], "work": {"item_id": 7, "lease_generation": 1, "video_id": "home_video", "phase": "downloaded"}})
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE knowledge_atom SET claim='child output committed before work phase' WHERE id='home_atom'")
        con.commit()
    with pytest.raises(recovery.HomeRecoveryError, match="UNFINISHED_INPUT_MISMATCH"):
        observed(env)


def test_unknown_age_refresh_has_exact_original_plan_and_paid_authority(env):
    from reno import source_resolution_contract as codec
    store, operation, initial = home_dns(env)
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    stamp = now.isoformat(timespec="microseconds").replace("+00:00", "Z")
    dns = source_reservation(env, operation, request_sha=initial.request_sha256, kind="source_resolution")
    dns_result = codec.ResolutionResult(initial.request_sha256, "dns", True, "completed", addresses=("8.8.8.8",), observed_at=stamp)
    dns_receipt = budget.settle(dns.attempt_id, initial.request_sha256, profile=env[0], result_text=codec.encode_envelope(initial, dns_result), outcome="source_step_complete", now=now)
    headers = codec.ResolutionPlan("headers", initial.submitted_url, initial.current_url, operation.input_sha256, initial.max_attempts,
        dns_attempt_id=dns.attempt_id, dns_settlement_sha256=dns_receipt.settlement_sha256, peer_ip="8.8.8.8")
    policy = budget_contract.parse_policy({"budget_enabled": True})
    paid = budget.reserve(operation, budget.AttemptSpec(headers.request_sha256, ordinal=2, resolver_contract_version=1, resolver_phase="headers", resolver_max_attempts=initial.max_attempts), profile=env[0], policy=policy, now=now)
    assert budget.mark_dispatching(paid.attempt_id, paid.reservation_sha256, str(uuid.uuid4()), expected_revision=paid.revision, profile=env[0], policy=policy, now=now).won
    header_result = codec.ResolutionResult(headers.request_sha256, "headers", True, "completed", headers_complete=True, transport_closed=True, status=200, observed_at=stamp)
    complete = budget.settle(paid.attempt_id, headers.request_sha256, profile=env[0], result_text=codec.encode_envelope(headers, header_result), outcome="source_step_complete", now=now)
    refresh = codec.ResolutionPlan("dns", initial.submitted_url, initial.submitted_url, operation.input_sha256, initial.max_attempts,
        generation=1, previous_attempt_id=paid.attempt_id, previous_settlement_sha256=complete.settlement_sha256,
        refresh_attempt_id=paid.attempt_id, refresh_settlement_sha256=complete.settlement_sha256)
    source_reservation(env, operation, request_sha=refresh.request_sha256, kind="source_resolution", ordinal=3, now=now + timedelta(hours=2))
    changed(env)
    before, saved = rows(env), store.read()
    apply(env)
    assert store.read() == {**saved, "local_epoch": 2}
    assert rows(env)["budget_operation"] == before["budget_operation"]
    assert rows(env)["budget_attempt"] == before["budget_attempt"]


def test_unknown_metadata_refresh_reconstructs_retained_failed_video_authority(env):
    from reno import source_contract as codec
    target = "https://www.bilibili.com/video/BVfake123"
    job = tasks.submit("ingest", payload={"target": target}, profile=env[0])["job_id"]
    with storage_admin.connection(env[0]) as con:
        lineage = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job,)).fetchone())
    now = datetime(2026, 10, 2, tzinfo=timezone.utc)
    metadata_input = recovery._sha({"kind": "source_metadata", "source": target, "runner_contract": "yt_dlp_v1"})
    operation = budget.OperationSpec(lineage["continuation_id"], lineage["execution_generation"], "source_metadata", "ingest:metadata:v1", metadata_input, job)
    initial = codec.SourcePlan("source_metadata", target)
    reserved = source_reservation(env, operation, request_sha=initial.request_sha256)
    info = codec.canonical_json({"id": "BVfake123", "title": "controlled-initial"})
    result = codec.SourceProcessResult(initial.request_sha256, True, True, 0, "exited", metadata_json=info)
    budget.settle(reserved.attempt_id, initial.request_sha256, profile=env[0], result_text=codec.encode_envelope(initial, result, input_sha256=metadata_input), outcome="source_step_complete", now=now)
    info_sha = hashlib.sha256(info.encode()).hexdigest()
    video_input = recovery._sha({"kind": "download", "source": target, "runner_contract": "yt_dlp_v1", "genesis_metadata_sha256": info_sha})
    download = budget.OperationSpec(lineage["continuation_id"], lineage["execution_generation"], "download", "ingest:media:v1:BVfake123", video_input, job)
    plan = codec.SourcePlan("download", target, segment="video", info_json=info, artifact_relpath=f"originals/BVfake123/source-{info_sha}/video.mp4")
    policy = budget_contract.parse_policy({"budget_enabled": True})
    video = budget.reserve(download, budget.AttemptSpec(plan.request_sha256, source_contract_version=1, download_segment="video"), profile=env[0], policy=policy, now=now)
    assert budget.mark_dispatching(video.attempt_id, video.reservation_sha256, str(uuid.uuid4()), expected_revision=video.revision, profile=env[0], policy=policy, now=now).won
    failure = codec.SourceProcessResult(plan.request_sha256, True, True, 1, "exited", failure_class="process_failure")
    failed = budget.settle(video.attempt_id, plan.request_sha256, profile=env[0], result_text=codec.encode_envelope(plan, failure, input_sha256=video_input), outcome="source_retry", now=now)
    refresh = codec.SourcePlan("source_metadata", target, phase="refresh", refresh_attempt_id=video.attempt_id, refresh_settlement_sha256=failed.settlement_sha256)
    source_reservation(env, operation, request_sha=refresh.request_sha256, ordinal=2)
    changed(env)
    before = rows(env)
    apply(env)
    for name in ("job", "budget_operation", "budget_attempt"):
        assert rows(env)[name] == before[name]


def test_original_logical_input_does_not_authorize_a_different_request_sha(env):
    target = "https://www.bilibili.com/video/BVfake123"
    job = tasks.submit("ingest", payload={"target": target}, profile=env[0])["job_id"]
    with storage_admin.connection(env[0]) as con:
        lineage = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job,)).fetchone())
    digest = recovery._sha({"kind": "source_metadata", "source": target, "runner_contract": "yt_dlp_v1"})
    operation = budget.OperationSpec(lineage["continuation_id"], lineage["execution_generation"], "source_metadata", "ingest:metadata:v1", digest, job)
    source_reservation(env, operation, request_sha="2" * 64)
    before = rows(env)
    with pytest.raises(recovery.HomeRecoveryError, match="ORIGINAL_REQUEST_MISMATCH"):
        observed(env)
    assert rows(env) == before
