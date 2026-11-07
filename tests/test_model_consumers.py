"""Consumer budget boundaries on artificial data and synthetic model calls."""
from datetime import datetime, timezone
import json
import threading
from types import SimpleNamespace
import uuid

import pytest

from reno import ask, atomize, config, db, judge, llm, model_budget, model_scopes, report, storage_admin, tasks, vlm, worker
from reno.budget import BudgetLedgerError
from reno.budget_contract import BudgetContractError, BudgetDeferred
from scripts import backfill_atoms_v2 as backfill, rerun_atomize_flashx as rerun

_REAL_POST = llm._post


@pytest.fixture()
def consumer_store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"node_role": "all"})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    with storage_admin.connection(profile) as con:
        db.upsert_asset(con, {"video_id": "video", "content_sha256": "synthetic-video", "duration_ms": 2000,
                              "title": "人工防水样本", "author": "人工作者"})
        con.execute("INSERT INTO transcript_segment(id,video_id,start_ms,end_ms,text) VALUES('asr1','video',0,1000,'防水施工前先核实基层情况')")
        db.replace_atoms(con, "video", [{"id": "old", "video_id": "video", "claim": "防水施工前先核实基层情况",
            "category": "防水", "polarity": "recommend", "evidence_refs": [{"modality": "asr",
                "source_item_id": "asr1", "start_ms": 0, "end_ms": 1000, "evidence_text": "人工防水证据"}]}])
        con.commit()
    ask._answer_cache.clear()
    yield profile
    ask._answer_cache.clear()


def controlled_error(kind):
    if kind == "deferred":
        return BudgetDeferred("daily_limit", resume_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    if kind == "contract":
        return BudgetContractError("BUDGET_CAPABILITY_UNSUPPORTED")
    return BudgetLedgerError()


@pytest.mark.parametrize("kind", ["deferred", "contract", "ledger"])
@pytest.mark.parametrize("phase", [1, 2])
def test_vlm_controlled_error_never_becomes_empty_success_or_paid_retry(monkeypatch, kind, phase):
    error = controlled_error(kind)
    calls = []
    def vision(*args, **kwargs):
        calls.append(True)
        if len(calls) == phase:
            raise error
        return "synthetic incomplete JSON"
    monkeypatch.setattr(llm, "vision", vision)
    with pytest.raises(type(error)) as caught:
        vlm._observe_frame("synthetic-frame.jpg")
    assert caught.value is error and len(calls) == phase


def test_vlm_wait_preserves_old_observations_and_does_not_log_success(consumer_store, monkeypatch):
    error = BudgetDeferred("indeterminate")
    con = db.connect(consumer_store.database)
    try:
        con.execute("INSERT INTO visual_observation(id,video_id,frame_id,pts_ms,scene_description) VALUES('oldvis','video','oldframe',0,'原有证据')")
        con.commit()
        monkeypatch.setattr(vlm, "budget_frames", lambda *args: [{"frame_id": "f1", "pts_ms": 1000, "path": "synthetic.jpg"}])
        monkeypatch.setattr(llm, "vision", lambda *args, **kwargs: (_ for _ in ()).throw(error))
        with pytest.raises(BudgetDeferred) as caught:
            vlm.run(con, "video")
        assert caught.value is error
        assert con.execute("SELECT scene_description FROM visual_observation").fetchone()[0] == "原有证据"
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE step='vlm'").fetchone()[0] == 0
    finally:
        con.close()


def test_report_budget_wait_keeps_previous_report_without_failed_status(consumer_store, monkeypatch):
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: ("此前报告正文", "synthetic"))
    report.summary_report(profile=consumer_store)
    previous = report.read_report("summary", profile=consumer_store)
    error = BudgetDeferred("daily_limit", resume_at=datetime(2030, 1, 1, tzinfo=timezone.utc))
    monkeypatch.setattr(llm, "chat", lambda *args, **kwargs: (_ for _ in ()).throw(error))
    with pytest.raises(BudgetDeferred) as caught:
        report.summary_report(profile=consumer_store)
    assert caught.value is error and report.read_report("summary", profile=consumer_store) == previous
    assert report.metadata(profile=consumer_store)["summary"]["status"] != "failed"


@pytest.mark.parametrize("kind", ["deferred", "contract", "ledger"])
def test_ask_budget_failure_keeps_grounded_retrieval_and_never_caches_as_model_answer(consumer_store, monkeypatch, kind):
    error = controlled_error(kind)
    calls = []
    def chat(*args, **kwargs):
        calls.append(True)
        raise error
    monkeypatch.setattr(llm, "chat", chat)
    for _ in range(2):
        result = ask.ask("防水基层怎么处理？")
        assert result["degraded"] is True and result["refs"][0]["atom_id"] == "old"
        assert "防水施工前先核实基层情况" in result["answer"]
    assert len(calls) == 2 and not ask._answer_cache


def test_rerun_model_generation_precedes_writer_transaction(consumer_store, monkeypatch):
    observed = []
    con = db.connect(consumer_store.database)
    def generate(*args, **kwargs):
        observed.append(con.in_transaction)
        return {"atoms": [{"claim": "新的防水施工建议需要证据核实", "category": "防水",
                            "evidence": [{"source_item_id": "asr1"}]}]}, "synthetic"
    monkeypatch.setattr(llm, "chat_json", generate)
    try:
        selected = rerun.plan(con, ["video"])
        assert rerun._apply(con, selected) == 0
        assert observed == [False]
    finally:
        con.close()


@pytest.mark.parametrize("script", ["backfill", "rerun"])
def test_maintenance_budget_wait_preserves_cursor_atoms_and_failure_history(consumer_store, monkeypatch, script):
    error = BudgetDeferred("indeterminate")
    con = db.connect(consumer_store.database)
    try:
        con.execute("INSERT INTO meta(key,value) VALUES(?,'previous-progress')", (backfill.CURSOR_KEY,))
        con.commit()
        if script == "backfill":
            monkeypatch.setattr(backfill, "llm_batch", lambda *args, **kwargs: (_ for _ in ()).throw(error))
            action = lambda: backfill._apply(con, SimpleNamespace(restart=False, limit=0))
        else:
            monkeypatch.setattr(llm, "chat_json", lambda *args, **kwargs: (_ for _ in ()).throw(error))
            selected = rerun.plan(con, ["video"])
            action = lambda: rerun._apply(con, selected)
        with pytest.raises(BudgetDeferred) as caught:
            action()
        assert caught.value is error
        assert con.execute("SELECT value FROM meta WHERE key=?", (backfill.CURSOR_KEY,)).fetchone()[0] == "previous-progress"
        assert con.execute("SELECT claim FROM knowledge_atom WHERE id='old'").fetchone()[0] == "防水施工前先核实基层情况"
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE ok=0").fetchone()[0] == 0
    finally:
        con.close()


def manual_records(profile):
    con = db.connect(profile.database)
    try:
        return {row["key"]: row["value"] for row in con.execute("SELECT key,value FROM meta WHERE key LIKE 'budget:manual:%'")}
    finally:
        con.close()


def test_manual_scope_disabled_without_tracking_preserves_legacy_no_meta_writes(consumer_store):
    from reno import model_scopes
    before = manual_records(consumer_store)
    with model_scopes.execution(consumer_store, "backfill_v2", {"selector": "dimension-null", "limit": 0},
                                selection=("old",)) as value:
        assert value.tracked is False and value.record_key is None
        assert model_scopes.current_execution() is value
    assert manual_records(consumer_store) == before and model_scopes.current_execution() is None


def test_manual_scope_reuses_running_uuid_and_fixed_selection_after_own_kb_commit(consumer_store):
    from reno import model_scopes
    config.set_local({"budget_enabled": True})
    intent = {"selector": "dimension-null", "limit": 0, "prompt_version": "backfill-v2"}
    with model_scopes.execution(consumer_store, "backfill_v2", intent, selection=("old", "other")) as first:
        identifier = first.continuation_id
        con = db.connect(consumer_store.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE knowledge_atom SET dimension='施工工艺' WHERE id='old'")
            model_scopes.checkpoint(con, first, completed_items=("old",))
            con.commit()
        finally:
            con.close()
    config.set_local({"budget_enabled": False})
    with model_scopes.execution(consumer_store, "backfill_v2", intent, selection=("other",)) as resumed:
        assert resumed.continuation_id == identifier and resumed.tracked is True
        assert resumed.selection == ("old", "other") and resumed.completed_items == ("old",)
    assert model_scopes.current_execution() is None


def test_manual_checkpoint_commits_atomically_with_business_and_does_not_commit_itself(consumer_store):
    from reno import model_scopes
    config.set_local({"budget_enabled": True})
    with model_scopes.execution(consumer_store, "rerun_atomize", {"video_ids": ["video"]}, selection=("video",)) as value:
        con = db.connect(consumer_store.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE knowledge_atom SET claim='应随事务回滚的知识' WHERE id='old'")
            model_scopes.checkpoint(con, value, completed_items=("video",), completed=True)
            assert con.in_transaction
            con.rollback()
            assert con.execute("SELECT claim FROM knowledge_atom WHERE id='old'").fetchone()[0] == "防水施工前先核实基层情况"
            record = json.loads(con.execute("SELECT value FROM meta WHERE key=?", (value.record_key,)).fetchone()[0])
            assert record["state"] == "running" and record["completed_items"] == []
            con.execute("BEGIN IMMEDIATE")
            con.execute("UPDATE knowledge_atom SET claim='已提交的有效知识' WHERE id='old'")
            model_scopes.checkpoint(con, value, completed_items=("video",), completed=True)
            con.commit()
        finally:
            con.close()
    with model_scopes.execution(consumer_store, "rerun_atomize", {"video_ids": ["video"]}, selection=("video",)) as next_value:
        assert next_value.continuation_id != value.continuation_id
    assert json.loads(manual_records(consumer_store)[value.record_key])["state"] == "completed"


def test_manual_new_execution_preserves_old_running_record_and_progress(consumer_store):
    from reno import model_scopes
    config.set_local({"budget_enabled": True})
    intent = {"video_ids": ["video"]}
    with model_scopes.execution(consumer_store, "rerun_atomize", intent, selection=("video",)) as first:
        original = manual_records(consumer_store)[first.record_key]
    with model_scopes.execution(consumer_store, "rerun_atomize", intent, selection=("video",), new_execution=True) as fresh:
        assert fresh.continuation_id != first.continuation_id
    assert manual_records(consumer_store)[first.record_key] == original


def test_manual_scope_rejects_business_writer_without_committing_or_rolling_back_it(consumer_store):
    from reno import model_scopes
    config.set_local({"budget_enabled": True})
    con = db.connect(consumer_store.database)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO meta(key,value) VALUES('caller-uncommitted','keep')")
        with pytest.raises(BudgetLedgerError) as caught:
            with model_scopes.execution(consumer_store, "atomize", {"video_id": "video"}, business_connection=con):
                pytest.fail("known writer cannot open a model execution")
        assert caught.value.code == "BUDGET_BUSINESS_TRANSACTION_ACTIVE" and con.in_transaction
        assert con.execute("SELECT value FROM meta WHERE key='caller-uncommitted'").fetchone()[0] == "keep"
        con.rollback()
        assert not manual_records(consumer_store)
    finally:
        con.close()


def test_manual_nested_scope_restores_parent_identity_after_exception(consumer_store):
    from reno import model_scopes
    config.set_local({"budget_enabled": True})
    with model_scopes.execution(consumer_store, "rerun_atomize", {"video_ids": ["video"]}, selection=("video",)) as root:
        with pytest.raises(RuntimeError):
            with model_scopes.execution(consumer_store, "atomize", {"video_id": "video"}) as child:
                assert child is root
                raise RuntimeError("synthetic consumer interruption")
        assert model_scopes.current_execution() is root
    assert model_scopes.current_execution() is None


def provider_packets(monkeypatch, *contents):
    """Run the actual model bridge against in-memory provider receipts only."""
    sent, pending = [], list(contents)
    config.set_local({"zhipu_base_url": "https://synthetic.invalid/api", "zhipu_api_key": "",
                      "atomize_model": "synthetic-paas", "vlm_model": "synthetic-paas",
                      "summary_model": "synthetic-paas"})
    monkeypatch.setattr(llm, "_post", _REAL_POST)
    class Response:
        status = 200
        def __init__(self, content):
            self.body = json.dumps({"choices": [{"message": {"content": content}}]}, ensure_ascii=False).encode()
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
        def read(self):
            return self.body
    def transport(req, timeout=None):
        sent.append(req.data)
        assert pending, "unexpected synthetic provider send"
        return Response(pending.pop(0))
    monkeypatch.setattr(llm.urllib.request, "urlopen", transport)
    return sent


def test_legacy_manual_uuid_is_promoted_before_first_tracked_reservation(consumer_store, monkeypatch):
    sent = provider_packets(monkeypatch, "已经付费的合成回答")
    with model_scopes.execution(consumer_store, "summary", {"manual": "promotion"}) as value:
        assert not value.tracked and manual_records(consumer_store) == {}
        config.set_local({"budget_enabled": True})
        with model_scopes.call(value, "summary:promotion"):
            assert llm.chat([{"role": "user", "content": "合成请求"}], model="synthetic-paas", json_mode=False)[0] == "已经付费的合成回答"
        record_key = "budget:manual:execution:v1:" + value.continuation_id
        assert json.loads(manual_records(consumer_store)[record_key])["state"] == "running"
        # Initial DTO stays immutable, but checkpoint must observe its durable
        # promotion instead of silently leaving the record forever running.
        with storage_admin.connection(consumer_store) as con:
            con.execute("BEGIN IMMEDIATE")
            model_scopes.checkpoint(con, value, completed=True)
            assert con.in_transaction
            con.commit()
            assert con.execute("SELECT continuation_id FROM budget_operation").fetchone()[0] == value.continuation_id
    assert len(sent) == 1 and json.loads(manual_records(consumer_store)[record_key])["state"] == "completed"


def test_supplied_writer_guard_requires_witness_without_promoting_or_touching_transaction(consumer_store):
    with model_scopes.execution(consumer_store, "summary", {"manual": "no-witness"}) as value:
        config.set_local({"budget_enabled": True})
        with storage_admin.connection(consumer_store) as con:
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO meta(key,value) VALUES('caller-write','must-remain-pending')")
            with pytest.raises(BudgetDeferred) as caught:
                model_scopes._check_send(value, con)
            assert caught.value.reason == "ledger_unavailable" and con.in_transaction
            assert con.execute("SELECT value FROM meta WHERE key='caller-write'").fetchone()[0] == "must-remain-pending"
            assert con.execute("SELECT COUNT(*) FROM meta WHERE key LIKE 'budget:manual:%'").fetchone()[0] == 0
            con.rollback()
    assert manual_records(consumer_store) == {}


def test_manual_send_guard_rejects_changed_consumer_or_original_selection(consumer_store):
    config.set_local({"budget_enabled": True})
    with model_scopes.execution(consumer_store, "vlm", {"video_id": "video"}, selection=("original-frame",)) as value:
        for changes in ({"consumer": "judge"}, {"selection": ["other-frame"]}):
            with storage_admin.connection(consumer_store) as con:
                con.execute("BEGIN IMMEDIATE")
                record = json.loads(con.execute("SELECT value FROM meta WHERE key=?", (value.record_key,)).fetchone()[0])
                record.update(changes)
                con.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(record), value.record_key))
                with pytest.raises(BudgetDeferred):
                    model_scopes._check_send(value, con)
                assert con.in_transaction
                con.rollback()


def test_vlm_paid_initial_receipt_resumes_only_repair_and_commits_whole_observation_set(consumer_store, monkeypatch, tmp_path):
    frame = tmp_path / "synthetic.jpg"
    frame.write_bytes(b"synthetic frame bytes for fake transport")
    frames = [{"frame_id": "f1", "pts_ms": 1000, "path": str(frame)}]
    monkeypatch.setattr(vlm, "budget_frames", lambda *args: frames)
    sent = provider_packets(monkeypatch, "incomplete JSON", '{"scene_description":"合成的防水画面","materials":[],"tools":[]}')
    config.set_local({"budget_enabled": True, "model_daily_attempts": 1})
    con = db.connect(consumer_store.database)
    try:
        con.execute("INSERT INTO visual_observation(id,video_id,frame_id,pts_ms,scene_description) VALUES('oldvis','video','oldframe',0,'原有证据')")
        con.commit()
        with pytest.raises(BudgetDeferred, match="daily_limit"):
            vlm.run(con, "video")
        assert con.execute("SELECT scene_description FROM visual_observation").fetchone()[0] == "原有证据"
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE step='vlm'").fetchone()[0] == 0
        config.set_local({"model_daily_attempts": 2})
        assert vlm.run(con, "video")["n_ok"] == 1
        assert con.execute("SELECT scene_description FROM visual_observation").fetchone()[0] == "合成的防水画面"
        assert con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0] == 1
        assert [row[0] for row in con.execute("SELECT outcome FROM budget_attempt ORDER BY ordinal")] == ["repair", "full_result"]
        record = next(json.loads(raw) for key, raw in manual_records(consumer_store).items() if ":execution:" in key)
        assert record["state"] == "completed" and record["completed_items"] == ["f1"]
    finally:
        con.close()
    assert len(sent) == 2 and model_budget.current_scope() is None


@pytest.mark.parametrize("consumer", ["vlm", "summary"])
def test_worker_owner_loss_is_controlled_wait_before_model_send(consumer_store, monkeypatch, tmp_path, consumer):
    sent = provider_packets(monkeypatch)
    config.set_local({"budget_enabled": True})
    frame = tmp_path / "synthetic.jpg"
    frame.write_bytes(b"synthetic bytes")
    monkeypatch.setattr(vlm, "budget_frames", lambda *args: [{"frame_id": "f1", "pts_ms": 0, "path": str(frame)}])
    def lost(**kwargs):
        raise tasks.TaskError("synthetic private owner diagnostic", "TASK_OWNER_LOST", 409)
    monkeypatch.setattr(worker, "check_admission", lost)
    with storage_admin.connection(consumer_store) as con:
        submitted = tasks.enqueue(con, "video" if consumer == "vlm" else None,
            kind="process" if consumer == "vlm" else "report",
            payload={} if consumer == "vlm" else {"mode": "summary"}, profile=consumer_store)
        con.commit()
        job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (submitted,)).fetchone())
    with worker._execution(job, str(uuid.uuid4()), threading.Event(), profile=consumer_store):
        with pytest.raises(BudgetDeferred, match="ledger_unavailable"):
            if consumer == "vlm":
                con = db.connect(consumer_store.database)
                try:
                    vlm.run(con, "video")
                    pytest.fail("owner loss must not become an empty successful observation")
                finally:
                    con.close()
            else:
                report.summary_report(profile=consumer_store)
    if consumer == "summary":
        assert report.metadata(profile=consumer_store)["summary"]["status"] == "waiting"
    assert sent == []


def test_domain_rejected_paid_atomize_result_is_reused_without_repair_or_new_send(consumer_store, monkeypatch):
    sent = provider_packets(monkeypatch, '{"atoms":[]}')
    config.set_local({"budget_enabled": True})
    con = db.connect(consumer_store.database)
    try:
        prepared = atomize.prepare(con, "video")
        with pytest.raises(atomize.AtomizeError, match="ATOMIZE_EMPTY_RESULT"):
            atomize.generate(prepared, business_connection=con)
        assert con.execute("SELECT outcome FROM budget_attempt").fetchone()[0] == "full_result"
        config.set_local({"model_daily_attempts": 0})
        monkeypatch.setattr(model_scopes, "_check_send", lambda *args: pytest.fail("paid replay must precede send admission"))
        with pytest.raises(atomize.AtomizeError, match="ATOMIZE_EMPTY_RESULT"):
            atomize.generate(prepared, business_connection=con)
        assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 1
        assert con.execute("SELECT claim FROM knowledge_atom WHERE id='old'").fetchone()[0] == "防水施工前先核实基层情况"
    finally:
        con.close()
    assert len(sent) == 1


def test_judge_batch_restart_reuses_paid_batch_and_only_sends_unfinished_batch(consumer_store, monkeypatch):
    sent = provider_packets(monkeypatch, '{"judgments":[{"pair_id":0,"relation":"different"}]}',
                            '{"judgments":[{"pair_id":1,"relation":"different"}]}')
    config.set_local({"budget_enabled": True, "model_daily_attempts": 1})
    candidates = [{"pair_id": index, "a": f"a{index}", "b": f"b{index}"} for index in range(2)]
    with model_scopes.execution(consumer_store, "judge", {"fixed": "two-batches"}) as first:
        with pytest.raises(BudgetDeferred, match="daily_limit"):
            judge._judge_llm(candidates, batch_size=1)
    config.set_local({"model_daily_attempts": 2})
    with model_scopes.execution(consumer_store, "judge", {"fixed": "two-batches"}) as resumed:
        assert resumed.continuation_id == first.continuation_id
        judgments, model = judge._judge_llm(candidates, batch_size=1)
        assert [value["pair_id"] for value in judgments] == [0, 1] and model == "synthetic-paas"
    assert len(sent) == 2


def test_rerun_resumes_original_selection_after_child_kb_commit_without_repaying_completed_video(consumer_store, monkeypatch):
    with storage_admin.connection(consumer_store) as con:
        db.upsert_asset(con, {"video_id": "video2", "content_sha256": "synthetic-video2", "duration_ms": 2000,
                              "title": "第二个人工防水视频"})
        con.execute("INSERT INTO transcript_segment(id,video_id,start_ms,end_ms,text) VALUES('asr2','video2',0,1000,'第二个防水样本先核实基层情况')")
        con.commit()
    answer = lambda source: json.dumps({"atoms": [{"claim": "防水施工前需要核对基层与材料情况", "category": "防水",
                                                  "evidence": [{"source_item_id": source}]}]}, ensure_ascii=False)
    sent = provider_packets(monkeypatch, answer("asr1"), answer("asr2"))
    config.set_local({"budget_enabled": True, "model_daily_attempts": 1})
    con = db.connect(consumer_store.database)
    try:
        before_revision = db.knowledge_version(con)["kb_revision"]
        with pytest.raises(BudgetDeferred, match="daily_limit"):
            rerun._apply(con, rerun.plan(con, ["video", "video2"]))
        assert db.knowledge_version(con)["kb_revision"] > before_revision
        running = next(json.loads(value) for key, value in manual_records(consumer_store).items() if ":execution:" in key)
        assert running["selection"] == ["video", "video2"] and running["completed_items"] == ["video"]
        first_revision = db.knowledge_version(con)["kb_revision"]
        first_atoms = [dict(row) for row in con.execute("SELECT * FROM knowledge_atom WHERE video_id='video'")]
        config.set_local({"model_daily_attempts": 2})
        assert rerun._apply(con, rerun.plan(con, ["video", "video2"])) == 0
        restored = next(json.loads(value) for key, value in manual_records(consumer_store).items() if ":execution:" in key)
        assert restored["continuation_id"] == running["continuation_id"] and restored["state"] == "completed"
        assert db.knowledge_version(con)["kb_revision"] > first_revision
        assert [dict(row) for row in con.execute("SELECT * FROM knowledge_atom WHERE video_id='video'")] == first_atoms
        assert con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE step='atomize' AND ok=1").fetchone()[0] == 2
    finally:
        con.close()
    assert len(sent) == 2


def test_backfill_resumes_original_batches_when_pending_selection_shrinks(consumer_store, monkeypatch):
    with storage_admin.connection(consumer_store) as con:
        con.execute("INSERT INTO knowledge_atom(id,video_id,claim,category,polarity) VALUES('second','video','第二个防水知识需要核对基层','防水','recommend')")
        con.commit()
    monkeypatch.setattr(backfill, "BATCH", 1)
    label = lambda identifier: json.dumps({"labels": [{"id": identifier, "dimension": "施工工艺",
                            "evidence_nature": "author_opinion", "exceptions": []}]}, ensure_ascii=False)
    sent = provider_packets(monkeypatch, label("old"), label("second"))
    config.set_local({"budget_enabled": True, "model_daily_attempts": 1})
    args = SimpleNamespace(restart=False, limit=0)
    con = db.connect(consumer_store.database)
    try:
        with pytest.raises(BudgetDeferred, match="daily_limit"):
            backfill._apply(con, args)
        assert [row["id"] for row in backfill.pending(con)] == ["second"]
        running = next(json.loads(value) for key, value in manual_records(consumer_store).items() if ":execution:" in key)
        assert running["selection"] == ["old", "second"] and running["completed_items"] == ["old"]
        config.set_local({"model_daily_attempts": 2})
        assert backfill._apply(con, args) == 0 and backfill.pending(con) == []
        restored = next(json.loads(value) for key, value in manual_records(consumer_store).items() if ":execution:" in key)
        assert restored["continuation_id"] == running["continuation_id"] and restored["state"] == "completed"
        assert [row[0] for row in con.execute("SELECT suboperation_key FROM budget_operation ORDER BY suboperation_key")] == [
            "backfill-v2:batch:0", "backfill-v2:batch:1"]
    finally:
        con.close()
    assert len(sent) == 2


def test_legacy_promotion_can_replace_verified_completed_pointer_and_keeps_both_histories(consumer_store):
    intent = {"manual": "completed-then-disabled"}
    config.set_local({"budget_enabled": True})
    with model_scopes.execution(consumer_store, "summary", intent, selection=("old",)) as original:
        with storage_admin.connection(consumer_store) as con:
            con.execute("BEGIN IMMEDIATE")
            model_scopes.checkpoint(con, original, completed_items=("old",), completed=True)
            con.commit()
    historical = manual_records(consumer_store)[original.record_key]
    config.set_local({"budget_enabled": False})
    with model_scopes.execution(consumer_store, "summary", intent, selection=("new-selection",)) as legacy:
        assert not legacy.tracked and legacy.continuation_id != original.continuation_id
        config.set_local({"budget_enabled": True})
        model_scopes._check_send(legacy)
        records = manual_records(consumer_store)
        witness = json.loads(records["budget:manual:execution:v1:" + legacy.continuation_id])
        assert witness["selection"] == ["new-selection"] and witness["state"] == "running"
        pointer_key = "budget:manual:active:v1:summary:" + legacy.intent_sha256
        assert records[pointer_key] == legacy.continuation_id
        assert records[original.record_key] == historical


@pytest.mark.parametrize("field,new_value", [("dimension", "安全合规"), ("evidence_nature", "author_test"),
                                             ("exceptions", '["人工新增例外"]')])
def test_backfill_paid_result_cas_preserves_concurrent_human_derived_edit(consumer_store, monkeypatch, field, new_value):
    response = json.dumps({"labels": [{"id": "old", "dimension": "施工工艺",
                                      "evidence_nature": "author_opinion", "exceptions": ["模型例外"]}]}, ensure_ascii=False)
    sent = provider_packets(monkeypatch, response)
    config.set_local({"budget_enabled": True})
    transport = llm.urllib.request.urlopen
    def concurrent_edit(req, timeout=None):
        with storage_admin.connection(consumer_store) as other:
            other.execute(f"UPDATE knowledge_atom SET {field}=? WHERE id='old'", (new_value,))
            other.commit()
        return transport(req, timeout=timeout)
    monkeypatch.setattr(llm.urllib.request, "urlopen", concurrent_edit)
    con = db.connect(consumer_store.database)
    try:
        for _ in range(2):
            with pytest.raises(BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
                backfill._apply(con, SimpleNamespace(restart=False, limit=0))
            assert con.execute(f"SELECT {field} FROM knowledge_atom WHERE id='old'").fetchone()[0] == new_value
            assert con.execute("SELECT value FROM meta WHERE key=?", (backfill.CURSOR_KEY,)).fetchone() is None
        assert con.execute("SELECT outcome FROM budget_attempt").fetchone()[0] == "full_result"
        assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 1
        record = next(json.loads(raw) for key, raw in manual_records(consumer_store).items() if ":execution:" in key)
        assert record["state"] == "running" and record["completed_items"] == []
    finally:
        con.close()
    assert len(sent) == 1


def test_empty_paid_vlm_domain_result_preserves_old_observations_and_same_execution(consumer_store, monkeypatch, tmp_path):
    frame = tmp_path / "synthetic.jpg"
    frame.write_bytes(b"synthetic frame bytes")
    monkeypatch.setattr(vlm, "budget_frames", lambda *args: [{"frame_id": "f1", "pts_ms": 0, "path": str(frame)}])
    sent = provider_packets(monkeypatch, "{}")
    config.set_local({"budget_enabled": True})
    con = db.connect(consumer_store.database)
    try:
        con.execute("INSERT INTO visual_observation(id,video_id,frame_id,pts_ms,scene_description) VALUES('oldvis','video','oldframe',0,'原有证据')")
        con.commit()
        for _ in range(2):
            with pytest.raises(ValueError, match="VLM_EMPTY_RESULT"):
                vlm.run(con, "video")
            assert con.execute("SELECT scene_description FROM visual_observation").fetchone()[0] == "原有证据"
        assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM processing_run WHERE step='vlm'").fetchone()[0] == 0
        record = next(json.loads(raw) for key, raw in manual_records(consumer_store).items() if ":execution:" in key)
        assert record["state"] == "running" and record["completed_items"] == []
    finally:
        con.close()
    assert len(sent) == 1


@pytest.mark.parametrize("old_state", ["running", "missing", "foreign"])
def test_legacy_promotion_never_steals_running_unknown_or_foreign_active_pointer(consumer_store, old_state):
    intent = {"manual": "conservative-promotion"}
    config.set_local({"budget_enabled": True})
    with model_scopes.execution(consumer_store, "summary", intent) as original:
        with storage_admin.connection(consumer_store) as con:
            con.execute("BEGIN IMMEDIATE")
            model_scopes.checkpoint(con, original, completed=True)
            con.commit()
    config.set_local({"budget_enabled": False})
    with model_scopes.execution(consumer_store, "summary", intent) as legacy:
        with storage_admin.connection(consumer_store) as con:
            if old_state == "missing":
                con.execute("DELETE FROM meta WHERE key=?", (original.record_key,))
            else:
                record = json.loads(con.execute("SELECT value FROM meta WHERE key=?", (original.record_key,)).fetchone()[0])
                record["state" if old_state == "running" else "consumer"] = "running" if old_state == "running" else "judge"
                con.execute("UPDATE meta SET value=? WHERE key=?", (json.dumps(record), original.record_key))
            con.commit()
        before = manual_records(consumer_store)
        config.set_local({"budget_enabled": True})
        with pytest.raises(BudgetDeferred, match="ledger_unavailable"):
            model_scopes._check_send(legacy)
        after = manual_records(consumer_store)
        assert all(after[key] == value for key, value in before.items())
        witness_key = "budget:manual:execution:v1:" + legacy.continuation_id
        assert json.loads(after[witness_key])["continuation_id"] == legacy.continuation_id


def test_partial_paid_vlm_group_stays_unfinished_and_preserves_complete_old_group(consumer_store, monkeypatch, tmp_path):
    frames = []
    for index in range(2):
        path = tmp_path / f"synthetic-{index}.jpg"
        path.write_bytes(f"synthetic frame {index}".encode())
        frames.append({"frame_id": f"f{index}", "pts_ms": index * 1000, "path": str(path)})
    monkeypatch.setattr(vlm, "budget_frames", lambda *args: frames)
    sent = provider_packets(monkeypatch, '{"scene_description":"可用的人工画面"}', "{}")
    config.set_local({"budget_enabled": True})
    con = db.connect(consumer_store.database)
    try:
        con.execute("INSERT INTO visual_observation(id,video_id,frame_id,pts_ms,scene_description,subtitle_text) VALUES('oldvis','video','oldframe',0,'原有证据','原字幕')")
        con.commit()
        before = [dict(row) for row in con.execute("SELECT * FROM visual_observation")]
        for _ in range(2):
            with pytest.raises(vlm.VlmError, match="VLM_INCOMPLETE_RESULT"):
                vlm.run(con, "video")
            assert [dict(row) for row in con.execute("SELECT * FROM visual_observation")] == before
        assert con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0] == 2
        assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 2
        record = next(json.loads(raw) for key, raw in manual_records(consumer_store).items() if ":execution:" in key)
        assert record["selection"] == ["f0", "f1"] and record["state"] == "running"
        assert record["completed_items"] == []
        assert {row[0] for row in con.execute("SELECT continuation_id FROM budget_operation")} == {record["continuation_id"]}
    finally:
        con.close()
    assert len(sent) == 2


@pytest.mark.parametrize("content", ['{"subtitle_text":"可见的人工字幕"}', '{"measurement":0}',
                                     '{"materials":["可见水泥"]}', '{"tools":["可见滚筒"]}'])
def test_vlm_usable_non_scene_fields_are_valid_observations(consumer_store, monkeypatch, tmp_path, content):
    frame = tmp_path / "synthetic.jpg"
    frame.write_bytes(b"synthetic bytes")
    monkeypatch.setattr(vlm, "budget_frames", lambda *args: [{"frame_id": "f1", "pts_ms": 0, "path": str(frame)}])
    sent = provider_packets(monkeypatch, content)
    config.set_local({"budget_enabled": True})
    con = db.connect(consumer_store.database)
    try:
        assert vlm.run(con, "video")["n_ok"] == 1
        record = next(json.loads(raw) for key, raw in manual_records(consumer_store).items() if ":execution:" in key)
        assert record["state"] == "completed" and record["completed_items"] == ["f1"]
    finally:
        con.close()
    assert len(sent) == 1


def test_backfill_partial_owned_enrichment_preserves_original_input_and_reuses_paid_receipt(consumer_store, monkeypatch):
    response = '{"labels":[{"id":"old","evidence_nature":"author_opinion","exceptions":["模型例外"]}]}'
    sent = provider_packets(monkeypatch, response)
    config.set_local({"budget_enabled": True})
    args = SimpleNamespace(restart=False, limit=0)
    con = db.connect(consumer_store.database)
    try:
        assert backfill._apply(con, args) == 1
        record = next(json.loads(raw) for key, raw in manual_records(consumer_store).items() if ":execution:" in key)
        snapshot_row = con.execute("SELECT value FROM meta WHERE key LIKE 'budget:consumer:backfill-input:%'").fetchone()
        before = json.loads(snapshot_row[0])
        assert before["input_sha256"] != before["current_sha256"]
        config.set_local({"model_daily_attempts": 0})
        assert backfill._apply(con, args) == 1
        after = json.loads(con.execute("SELECT value FROM meta WHERE key LIKE 'budget:consumer:backfill-input:%'").fetchone()[0])
        assert after == before
        assert con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 1
        assert {row[0] for row in con.execute("SELECT continuation_id FROM budget_operation")} == {record["continuation_id"]}
        assert con.execute("SELECT dimension,evidence_nature FROM knowledge_atom WHERE id='old'").fetchone()[:] == (None, "author_opinion")
    finally:
        con.close()
    assert len(sent) == 1


def test_backfill_enable_during_legacy_call_persists_same_uuid_and_original_batch_before_send(consumer_store, monkeypatch):
    sent = provider_packets(monkeypatch, '{"labels":[{"id":"old","dimension":"施工工艺","exceptions":[]}]}')
    original_batch = backfill.llm_batch
    observed = []
    transport = llm.urllib.request.urlopen
    def enabled_batch(rows):
        execution = model_scopes.current_execution()
        assert execution is not None and not execution.tracked
        observed.append(execution.continuation_id)
        config.set_local({"budget_enabled": True})
        return original_batch(rows)
    def witnessed_send(req, timeout=None):
        with storage_admin.connection(consumer_store) as reader:
            snapshot = json.loads(reader.execute("SELECT value FROM meta WHERE key LIKE 'budget:consumer:backfill-input:%'").fetchone()[0])
            assert snapshot["continuation_id"] == observed[0]
            assert snapshot["input_sha256"] == snapshot["current_sha256"]
            manual = json.loads(reader.execute("SELECT value FROM meta WHERE key=?", (
                "budget:manual:execution:v1:" + observed[0],)).fetchone()[0])
            assert manual["selection"] == ["old"] and manual["state"] == "running"
        return transport(req, timeout=timeout)
    monkeypatch.setattr(backfill, "llm_batch", enabled_batch)
    monkeypatch.setattr(llm.urllib.request, "urlopen", witnessed_send)
    con = db.connect(consumer_store.database)
    try:
        assert backfill._apply(con, SimpleNamespace(restart=False, limit=0)) == 0
        assert con.execute("SELECT continuation_id FROM budget_operation").fetchone()[0] == observed[0]
    finally:
        con.close()
    assert len(sent) == 1


def test_backfill_missing_original_batch_witness_cannot_rebind_existing_paid_result(consumer_store, monkeypatch):
    sent = provider_packets(monkeypatch, '{"labels":[{"id":"old","evidence_nature":"author_opinion"}]}')
    config.set_local({"budget_enabled": True})
    con = db.connect(consumer_store.database)
    try:
        args = SimpleNamespace(restart=False, limit=0)
        assert backfill._apply(con, args) == 1
        con.execute("DELETE FROM meta WHERE key LIKE 'budget:consumer:backfill-input:%'")
        con.commit()
        with pytest.raises(BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
            backfill._apply(con, args)
        assert con.execute("SELECT COUNT(*) FROM budget_operation").fetchone()[0] == 1
        assert con.execute("SELECT COUNT(*) FROM budget_attempt").fetchone()[0] == 1
    finally:
        con.close()
    assert len(sent) == 1
