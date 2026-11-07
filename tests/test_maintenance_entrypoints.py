"""Maintenance preview, worker fencing, backup ordering and failed-run retention."""
import json
import sqlite3
import uuid

import pytest

from reno import config, db, evidence_identity as identity, personal, review, tasks
from scripts import backfill_atoms_v2 as backfill
from scripts import rerun_atomize_flashx as rerun
from tests.test_review_backfill import reviewed  # noqa: F401


def snapshot_rows(con):
    return {table: [dict(row) for row in con.execute(f"SELECT * FROM {table}")]
            for table in ("meta", "knowledge_atom", "evidence_ref", "user_decision", "knowledge_cluster", "conflict_case", "processing_run",
                          "decision_card", "personal_evidence_ref", "personal_revision", "mutation_receipt")}


def complete_labels(rows):
    return {row["id"]: {"dimension": "施工工艺", "evidence_nature": "author_opinion", "exceptions": []}
            for row in rows}


@pytest.mark.parametrize("script,args", [(backfill, []), (backfill, ["--dry-run"]), (rerun, []), (rerun, ["--dry-run"])])
def test_default_and_dry_run_are_read_only_and_never_call_models(reviewed, monkeypatch, script, args):
    before = snapshot_rows(reviewed)
    monkeypatch.setattr(backfill, "llm_batch", lambda *args: pytest.fail("preview called model"))
    monkeypatch.setattr(rerun.atomize, "generate", lambda *args, **kwargs: pytest.fail("preview atomized video"))
    monkeypatch.setattr(db, "snapshot", lambda *args: pytest.fail("preview wrote backup"))
    assert script.main(args) == 0
    assert snapshot_rows(reviewed) == before
    assert not list(config.DATA.glob("*.bak-*"))
    assert not (config.DATA / "rerun-flashx-summary.json").exists()


@pytest.mark.parametrize("script,args", [(backfill, ["--apply"]), (rerun, ["--apply", "--video-id", "BVbackfill"])])
def test_active_worker_os_lock_refuses_all_maintenance_before_backup(reviewed, monkeypatch, script, args):
    before = snapshot_rows(reviewed)
    monkeypatch.setattr(db, "snapshot", lambda *args: pytest.fail("busy maintenance wrote backup"))
    with tasks.process_lock() as acquired:
        assert acquired
        assert script.main(args) == 2
    assert snapshot_rows(reviewed) == before


def test_backfill_backup_precedes_even_deterministic_write(reviewed, monkeypatch):
    before = snapshot_rows(reviewed)
    observed = []
    real_snapshot = db.snapshot
    def backup(con, path, **kwargs):
        assert snapshot_rows(con) == before
        real_snapshot(con, path, **kwargs)
        observed.append(path)
    monkeypatch.setattr(db, "snapshot", backup)
    monkeypatch.setattr(backfill, "llm_batch", complete_labels)
    assert backfill.main(["--apply"]) == 0
    assert len(observed) == 1
    con = sqlite3.connect(observed[0])
    con.row_factory = sqlite3.Row
    assert snapshot_rows(con) == before
    con.close()
    assert review.annotate_atoms(reviewed, db.all_atoms(reviewed))[0]["review_status"] == "needs_recheck"


def test_backfill_provider_failure_does_not_advance_checkpoint(reviewed, monkeypatch):
    monkeypatch.setattr(backfill, "llm_batch", lambda rows: (_ for _ in ()).throw(RuntimeError("fake provider unavailable")))
    assert backfill.main(["--apply"]) == 1
    assert reviewed.execute("SELECT 1 FROM meta WHERE key=?", (backfill.CURSOR_KEY,)).fetchone() is None
    assert len(db.all_atoms(reviewed)) == 2


@pytest.mark.parametrize("first_result", ["empty", "partial", "invalid_dimension"])
def test_incomplete_labels_fail_and_normal_apply_retries_earlier_pending_ids(reviewed, monkeypatch, first_result):
    calls = []

    def model(messages, **kwargs):
        rows = json.loads(messages[-1]["content"])
        calls.append([row["id"] for row in rows])
        labels = [{"id": aid, **label} for aid, label in complete_labels(rows).items()]
        if len(calls) == 1:
            if first_result == "empty":
                labels = []
            elif first_result == "partial":
                labels = labels[1:]  # The missing ID precedes the saved cursor.
            else:
                labels[0]["dimension"] = "不存在的知识维度"
                labels[0]["exceptions"] = ["木基层另行核对"]
        return {"labels": labels}, "isolated-test-model"

    # Use the real batch validation; only the model boundary is replaced.
    monkeypatch.setattr(backfill.llm, "chat_json", model)
    assert backfill.main(["--apply"]) == 1
    assert reviewed.execute("SELECT value FROM meta WHERE key=?", (backfill.CURSOR_KEY,)).fetchone()[0] == "backfill-rejected"
    expected_pending = calls[0] if first_result == "empty" else ["backfill-confirmed"]
    assert [row["id"] for row in backfill.pending(reviewed)] == expected_pending
    if first_result == "invalid_dimension":
        row = reviewed.execute("SELECT dimension,exceptions FROM knowledge_atom WHERE id='backfill-confirmed'").fetchone()
        assert row["dimension"] is None
        assert json.loads(row["exceptions"]) == ["木基层另行核对"]
    assert backfill.main(["--apply"]) == 0  # No --restart needed.
    assert calls[1] == expected_pending
    assert backfill.pending(reviewed) == []
    assert reviewed.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 2


def test_legacy_cursor_never_hides_unclassified_atoms(reviewed, monkeypatch, capsys):
    reviewed.execute("INSERT INTO meta(key,value) VALUES(?,?)", (backfill.CURSOR_KEY, "zzzz-older-run"))
    reviewed.commit()
    assert backfill.main([]) == 0
    assert json.loads(capsys.readouterr().out)["pending"] == 2
    calls = []

    def model(messages, **kwargs):
        rows = json.loads(messages[-1]["content"])
        calls.append([row["id"] for row in rows])
        return {"labels": [{"id": aid, **label} for aid, label in complete_labels(rows).items()]}, "isolated-test-model"

    monkeypatch.setattr(backfill.llm, "chat_json", model)
    assert backfill.main(["--apply"]) == 0
    assert calls == [["backfill-confirmed", "backfill-rejected"]]
    assert backfill.pending(reviewed) == []


@pytest.fixture()
def rerun_env(reviewed):
    reviewed.execute("INSERT INTO transcript_segment(id,video_id,idx,start_ms,end_ms,text) VALUES('asr','BVbackfill',0,0,1000,'防水人工时间轴')")
    reviewed.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json) VALUES('BVuntouched','未选视频','untouched-sha','{}')")
    reviewed.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json,polarity) VALUES('untouched-atom','BVuntouched','不应被删除的原子','{}','[]','recommend')")
    reviewed.execute("INSERT INTO knowledge_cluster(cluster_id,canonical_topic,relation,members_json) VALUES('keep-cluster','保留聚类','related','[\"untouched-atom\"]')")
    reviewed.execute("INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json,analysis_json) VALUES('keep-conflict','numeric_conflict','needs_review','{}','{}','{}')")
    reviewed.execute("INSERT INTO meta(key,value) VALUES('step:BVbackfill:atomize','old-marker')")
    reviewed.execute("UPDATE video_asset SET duration_ms=2000 WHERE video_id='BVbackfill'")
    reviewed.commit()
    return reviewed


def test_rerun_apply_requires_explicit_selection(rerun_env):
    with pytest.raises(SystemExit) as error:
        rerun.main(["--apply"])
    assert error.value.code == 2
    assert len(db.all_atoms(rerun_env)) == 3


def test_failed_rerun_rolls_back_step_internal_commit_and_keeps_old_knowledge(rerun_env, monkeypatch):
    before = snapshot_rows(rerun_env)
    def failing_step(con, prepared, generated, *, commit):
        assert commit is False and con.in_transaction
        vid = prepared.video_id
        assert con.execute("SELECT COUNT(*) FROM knowledge_atom WHERE video_id=?", (vid,)).fetchone()[0] == 2
        con.execute("DELETE FROM knowledge_atom WHERE video_id=?", (vid,))
        con.commit()  # The maintenance transaction intentionally defers this.
        raise RuntimeError("artificial provider failure")
    monkeypatch.setattr(rerun.atomize, "generate", lambda *args, **kwargs: object())
    monkeypatch.setattr(rerun.atomize, "apply", failing_step)
    assert rerun.main(["--apply", "--video-id", "BVbackfill"]) == 1
    after = snapshot_rows(rerun_env)
    for table in ("knowledge_atom", "evidence_ref", "user_decision", "knowledge_cluster", "conflict_case"):
        assert after[table] == before[table]
    assert rerun_env.execute("SELECT value FROM meta WHERE key='step:BVbackfill:atomize'").fetchone()[0] == "old-marker"
    backups = [p for p in config.DATA.glob("reno.db.bak-rerun-*") if not p.name.endswith(".json")]
    assert len(backups) == 1
    descriptor = backups[0].with_name(backups[0].name + ".reno-backup.json")
    assert json.loads(descriptor.read_text(encoding="utf-8"))["kind"] == "rerun"
    summary = json.loads((config.DATA / "rerun-flashx-summary.json").read_text(encoding="utf-8"))
    assert summary["failed"] == ["BVbackfill"] and summary["judge"] is None


def test_success_only_replaces_selected_atoms_and_replays_reviews(rerun_env, monkeypatch):
    old_model = config.get("atomize_model")
    def replace(con, prepared, generated, *, commit):
        assert commit is False and con.in_transaction
        vid = prepared.video_id
        atoms = [a for a in db.all_atoms(con) if a["video_id"] == vid]
        assert len(atoms) == 2
        for atom in atoms:
            atom["claim"] += "，新的适用条件"
        db.replace_atoms(con, vid, atoms)
        con.commit()
        return {"n_atoms": 2, "model": old_model}
    monkeypatch.setattr(rerun.atomize, "generate", lambda *args, **kwargs: object())
    monkeypatch.setattr(rerun.atomize, "apply", replace)
    monkeypatch.setattr(rerun.pipeline, "run_video", lambda *args: pytest.fail("must not rerun media/ASR/OCR"))
    monkeypatch.setattr(rerun.pipeline, "judge_all", lambda *args: pytest.fail("judge was not explicitly selected"))
    assert rerun.main(["--apply", "--video-id", "BVbackfill"]) == 0
    assert rerun_env.execute("SELECT claim FROM knowledge_atom WHERE id='untouched-atom'").fetchone()[0] == "不应被删除的原子"
    assert rerun_env.execute("SELECT COUNT(*) FROM knowledge_cluster").fetchone()[0] == 1
    assert rerun_env.execute("SELECT COUNT(*) FROM conflict_case").fetchone()[0] == 1
    assert rerun_env.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 2
    assert {a["review_status"] for a in review.annotate_atoms(rerun_env, db.all_atoms(rerun_env)) if a["video_id"] == "BVbackfill"} == {"needs_recheck"}
    assert config.get("atomize_model") == old_model
    assert (config.DATA / "rerun-flashx-summary.json").is_file()


def test_zero_atom_result_is_not_authorization_to_wipe_old_outputs(rerun_env, monkeypatch):
    def empty(con, prepared, generated, *, commit):
        assert commit is False and con.in_transaction
        vid = prepared.video_id
        db.replace_atoms(con, vid, [])
        con.commit()
        return {"n_atoms": 0, "model": config.get("atomize_model")}
    monkeypatch.setattr(rerun.atomize, "generate", lambda *args, **kwargs: object())
    monkeypatch.setattr(rerun.atomize, "apply", empty)
    assert rerun.main(["--apply", "--video-id", "BVbackfill"]) == 1
    assert rerun_env.execute("SELECT COUNT(*) FROM knowledge_atom WHERE video_id='BVbackfill'").fetchone()[0] == 2


@pytest.fixture()
def rerun_personal_env(rerun_env):
    config.set_local({"personal_features_enabled": True})
    project = personal.default_project(rerun_env)["project"]
    fingerprint = identity.load_target(rerun_env, "atom", "backfill-confirmed")[1]
    result, status = personal.create_card(project["project_id"], {
        "operation_id": str(uuid.uuid4()), "primary_epoch": 1,
        "changes": {"title": "保留旧防水依据", "state": "adopted", "decision_text": "本次先核实基层",
                    "evidence_refs": [{"target_kind": "atom", "target_id": "backfill-confirmed",
                                       "target_fingerprint": fingerprint}]},
    })
    assert status == 201
    return rerun_env, result["card"]


@pytest.mark.parametrize("response", [{"atoms": []}, {"atoms": [{"claim": "没有可追溯证据的输出", "evidence": []}]}])
def test_real_atomize_empty_replacement_rolls_back_reviews_and_personal_refs(rerun_personal_env, monkeypatch, response):
    con, card = rerun_personal_env
    before = snapshot_rows(con)
    old_version = db.knowledge_version(con)
    calls = []

    def model(messages, **kwargs):
        calls.append(messages)
        return response, "isolated-test-model"

    # Real generation rejects zero validated atoms before the business writer;
    # old knowledge, reviews and personal references must remain untouched.
    monkeypatch.setattr(rerun.atomize.llm, "chat_json", model)
    assert rerun.main(["--apply", "--video-id", "BVbackfill"]) == 1
    assert len(calls) == 1 and "<asr>" in calls[0][-1]["content"]
    after = snapshot_rows(con)
    for table in before.keys() - {"processing_run"}:
        assert after[table] == before[table], table
    assert db.knowledge_version(con) == old_version
    assert [row["ok"] for row in after["processing_run"]] == [0]
    assert personal.card_view(con, card["card_id"]) == card
    assert {atom["review_status"] for atom in review.annotate_atoms(con, db.all_atoms(con))
            if atom["video_id"] == "BVbackfill"} == {"user_confirmed", "user_rejected"}


def test_real_atomize_success_preserves_old_reviews_and_reference_snapshots(rerun_personal_env, monkeypatch):
    con, card = rerun_personal_env
    before = snapshot_rows(con)
    old_model = config.get("atomize_model")
    calls = []

    def model(messages, **kwargs):
        calls.append(kwargs)
        return {"atoms": [{"claim": "防水施工前应核对基层并清理浮灰", "category": "防水", "space": "卫生间",
                           "conditions": {"基层": "水泥"}, "dimension": "施工工艺", "polarity": "recommend",
                           "evidence_nature": "author_opinion", "exceptions": ["木基层另行核对"], "parameters": [],
                           "evidence": [{"source_item_id": "asr", "weight": 0.8}]}]}, "isolated-test-model"

    monkeypatch.setattr(rerun.atomize.llm, "chat_json", model)
    assert rerun.main(["--apply", "--video-id", "BVbackfill"]) == 0
    assert len(calls) == 1 and calls[0]["model"] == old_model
    after = snapshot_rows(con)
    for table in ("user_decision", "knowledge_cluster", "conflict_case", "decision_card",
                  "personal_evidence_ref", "personal_revision", "mutation_receipt"):
        assert after[table] == before[table], table
    untouched = lambda rows: [row for row in rows if row["video_id"] == "BVuntouched"]
    assert untouched(after["knowledge_atom"]) == untouched(before["knowledge_atom"])
    selected = [row for row in after["knowledge_atom"] if row["video_id"] == "BVbackfill"]
    assert len(selected) == 1 and selected[0]["id"] == "atom_ckfill_000"
    assert selected[0]["claim"] == "防水施工前应核对基层并清理浮灰"
    assert con.execute("SELECT start_ms,end_ms,source_item_id FROM evidence_ref WHERE atom_id='atom_ckfill_000'").fetchone()[:] == (0, 1000, "asr")
    assert con.execute("SELECT value FROM meta WHERE key='step:BVbackfill:atomize'").fetchone()[0] != "old-marker"
    assert [row["ok"] for row in after["processing_run"]] == [1]
    current_card = personal.card_view(con, card["card_id"])
    assert current_card["state"] == "adopted" and current_card["needs_recheck"]
    assert current_card["evidence_refs"][0]["status"] == "missing"
    assert current_card["evidence_refs"][0]["snapshot"] == card["evidence_refs"][0]["snapshot"]
    assert personal.revisions(con, "card", card["card_id"])[0]["snapshot"] == card
    assert config.get("atomize_model") == old_model
    backups = [p for p in config.DATA.glob("reno.db.bak-rerun-*") if not p.name.endswith(".json")]
    assert len(backups) == 1
    backup = sqlite3.connect(backups[0])
    backup.row_factory = sqlite3.Row
    try:
        assert snapshot_rows(backup) == before
    finally:
        backup.close()
