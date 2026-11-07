"""Read-only review reconciliation on real, disposable SQLite libraries."""
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

from fastapi.testclient import TestClient
import pytest

from reno import config, db, evidence_identity as identity, llm, reconciliation, review


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "FRAME_CACHE", tmp_path / "frames")
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "web_ui": "classic", "node_role": "all",
                                         "auth_token": "synthetic-reconciliation-token"})
    monkeypatch.setattr(llm, "chat", lambda *a, **k: pytest.fail("Read-only reconciliation must not use a model"))
    monkeypatch.setattr(llm, "chat_json", lambda *a, **k: pytest.fail("Read-only reconciliation must not use a model"))
    db.initialize()
    con = db.connect()
    for video, state, title in (("ready", "ready", "公开来源"), ("hidden", "staging", "隐藏来源哨兵")):
        con.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json,sync_state) VALUES(?,?,?,'{}',?)",
                    (video, title, video + "-synthetic-sha", state))
    for atom, video, claim in (("a", "ready", "核对防水基层"), ("b", "ready", "核对材料型号"),
                               ("hidden_atom", "hidden", "隐藏原子哨兵")):
        con.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json) VALUES(?,?,?,'{}','[]')",
                    (atom, video, claim))
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,start_ms,end_ms,evidence_text) VALUES(?,?,'asr',0,1000,?)",
                    (atom, video, claim))
    con.commit()
    try:
        yield con
    finally:
        con.close()


def event(con, atom="a", when="2024-01-01T00:00:00.000Z", action="confirm", *, note=None, conflict=None):
    result = review.record_decision(con, atom_id=None if conflict else atom, conflict_id=conflict,
                                   action=action, event_id=str(uuid.uuid4()), client_id="synthetic-test", note=note)
    con.execute("UPDATE user_decision SET decided_at=? WHERE decision_id=?", (when, result["decision_id"]))
    return result["event_id"]


def all_items(con):
    return {item["record_id"]: item for item in reconciliation.query(con, scope="all", limit=100)["items"]}


def legacy(con, *, atom="a", when="2023-01-01T00:00:00Z"):
    return con.execute("INSERT INTO user_decision(atom_id,action,created_at,decided_at,event_schema_version) "
                       "VALUES(?,'confirm',?,?,0)", (atom, when, when)).lastrowid


def test_empty_is_honest_read_only_and_has_current_stream(env):
    env.execute("PRAGMA query_only=ON")
    changes = env.total_changes
    data = reconciliation.query(env)
    assert data["items"] == [] and data["total"] == 0 and data["summary"]["total"] == 0
    assert set(data["summary"]["by_reason"]) == set(reconciliation.REASONS)
    assert data["version"] == db.knowledge_version(env)
    assert data["event_watermark"] == {"stream": {key: data["version"][key] for key in ("library_id", "library_epoch")},
                                       "high_watermark": 0}
    assert "没有可见复核记录" in reconciliation.export_markdown(env)
    assert env.total_changes == changes


def test_changed_fingerprint_never_rebinds_and_new_valid_review_covers_old_history(env):
    old = event(env)
    env.execute("UPDATE knowledge_atom SET claim='新内容须独立复核' WHERE id='a'")
    before = reconciliation.query(env)
    assert before["total"] == 1 and before["items"][0]["binding_status"] == "stale"
    assert before["items"][0]["snapshot_summary"] == "核对防水基层"
    assert before["items"][0]["current_summary"] == "新内容须独立复核"
    new = event(env, when="2024-02-01T00:00:00Z", action="clear_review")
    env.commit()
    version, changes = db.knowledge_version(env), env.total_changes
    items = all_items(env)
    assert items[old]["attention_state"] == "historical" and items[old]["superseded_by"] == new
    assert items[new]["attention_state"] == "current" and items[new]["binding_status"] == "active"
    assert reconciliation.query(env)["total"] == 0
    assert reconciliation.query(env, scope="history")["items"][0]["record_id"] == old
    assert env.total_changes == changes and db.knowledge_version(env) == version
    saved = env.execute("SELECT target_fingerprint FROM user_decision WHERE event_id=?", (old,)).fetchone()[0]
    assert saved != identity.load_target(env, "atom", "a")[1]


def test_actual_timestamp_order_not_text_order_and_uuid_breaks_equal_instant_ties(env):
    earlier = event(env, when="2024-01-01T12:00:00+08:00")  # 04:00 UTC; text sorts later.
    later = event(env, when="2024-01-01T05:00:00Z", action="reject")
    data = reconciliation.query(env, scope="all")
    assert [item["record_id"] for item in data["items"]] == [later, earlier]
    assert data["items"][1]["superseded_by"] == later
    assert review.projection(env)["atoms"]["a"]["review_status"] == "user_rejected"
    env.execute("UPDATE user_decision SET decided_at='2024-01-01T05:00:00+00:00'")
    data = reconciliation.query(env, scope="all")
    assert [item["record_id"] for item in data["items"]] == sorted((earlier, later), reverse=True)
    assert data["items"][1]["superseded_by"] == max(earlier, later)
    expected = "user_confirmed" if max(earlier, later) == earlier else "user_rejected"
    assert review.projection(env)["atoms"]["a"]["review_status"] == expected


def test_invalid_time_and_unordered_legacy_cannot_suppress_valid_later_event(env):
    valid = event(env)
    invalid = event(env, when="2999-01-01T00:00:00Z", action="reject")
    unknown_legacy = legacy(env, when="not a time")
    ordered_legacy = legacy(env)
    items = all_items(env)
    assert items[valid]["attention_state"] == "current"
    assert items[invalid]["binding_status"] == "invalid_time" and items[invalid]["needs_attention"]
    assert items[f"decision:{unknown_legacy}"]["needs_attention"]
    assert items[f"decision:{ordered_legacy}"]["attention_state"] == "historical"
    assert items[f"decision:{ordered_legacy}"]["event_id"] is None
    assert reconciliation.query(env)["summary"]["needs_attention"] == 2


def test_newer_stale_or_invalid_action_does_not_claim_to_resolve_old_record(env):
    old = event(env)
    later = event(env, when="2024-02-01T00:00:00Z")
    env.execute("UPDATE knowledge_atom SET claim='已换内容' WHERE id='a'")
    items = all_items(env)
    assert all(items[key]["needs_attention"] for key in (old, later))
    bad = event(env, when="2024-03-01T00:00:00Z")
    env.execute("UPDATE user_decision SET action='not-a-valid-action' WHERE event_id=?", (bad,))
    assert all(all_items(env)[key]["needs_attention"] for key in (old, later))
    assert all_items(env)[bad]["needs_attention"] and not all_items(env)[bad]["decision_valid"]


@pytest.mark.parametrize("action,revised_claim", [("unsupported", None), ("confirm", "未授权改写原文")])
def test_bound_but_invalid_decision_remains_attention_and_cannot_supersede_valid_review(env, action, revised_claim):
    earlier = event(env)
    invalid = event(env, when="2024-02-01T00:00:00Z")
    env.execute("UPDATE user_decision SET action=?,revised_claim=? WHERE event_id=?", (action, revised_claim, invalid))
    items = all_items(env)
    assert items[earlier]["attention_state"] == "current" and items[earlier]["decision_valid"]
    assert items[invalid]["binding_status"] == "active" and not items[invalid]["decision_valid"]
    assert reconciliation.query(env)["items"][0]["record_id"] == invalid
    assert reconciliation.query(env)["summary"]["needs_attention"] == 1
    assert review.projection(env)["atoms"]["a"]["review_status"] == "needs_recheck"
    assert "无效，需重新核对" in reconciliation.export_markdown(env)


def test_unknown_target_kind_is_never_a_valid_decision(env):
    row = env.execute("INSERT INTO user_decision(atom_id,conflict_id,action,decided_at) "
                      "VALUES('a','also-conflict','confirm','2024-01-01T00:00:00Z')").lastrowid
    item = all_items(env)[f"decision:{row}"]
    assert item["target_kind"] == "unknown" and not item["decision_valid"] and item["needs_attention"]
    assert item["target_url"] is None


def test_missing_target_retains_snapshot_without_target_link_or_automatic_match(env):
    operation = event(env)
    env.execute("DELETE FROM evidence_ref WHERE atom_id='a'")
    env.execute("DELETE FROM knowledge_atom WHERE id='a'")
    # An unrelated current atom with identical words is never a replacement.
    env.execute("UPDATE knowledge_atom SET claim='核对防水基层' WHERE id='b'")
    item = all_items(env)[operation]
    assert item["binding_status"] == "missing" and item["needs_attention"]
    assert item["target_id"] == "a" and item["target_url"] is None and item["current_summary"] is None
    assert item["source_links"] == [{"video_id": "ready", "url": "/videos/ready", "title": "公开来源"}]


@pytest.mark.parametrize("role,expected", [("all", 2), ("home", 2), ("cloud", 1)])
def test_role_visibility_applies_before_counts_pagination_and_markdown(env, role, expected):
    visible = event(env)
    hidden = event(env, atom="hidden_atom", when="2024-02-01T00:00:00Z", note="隐藏备注哨兵")
    env.commit()
    config._cache["node_role"] = role
    data = reconciliation.query(env, scope="all", limit=1)
    assert data["summary"]["total"] == data["total"] == expected
    assert data["event_watermark"]["high_watermark"] == 2
    if role == "cloud":
        assert data["items"][0]["record_id"] == visible
        export = reconciliation.export_markdown(env, scope="all")
        raw = json.dumps(data, ensure_ascii=False) + export
        for secret in (hidden, "hidden_atom", "隐藏备注哨兵", "隐藏原子哨兵", "隐藏来源哨兵"):
            assert secret not in raw


def test_hidden_historical_conflict_snapshot_does_not_leak_even_when_current_target_is_visible(env):
    env.execute("INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json) VALUES('c','contrast','needs_review',?,?)",
                (json.dumps({"atoms": ["a"], "video": "ready", "claim": "公开甲"}),
                 json.dumps({"atoms": ["hidden_atom"], "video": "hidden", "claim": "旧隐藏快照哨兵"})))
    hidden_history = event(env, conflict="c", action="both", note="隐藏冲突备注哨兵")
    env.execute("UPDATE conflict_case SET side_b_json=? WHERE conflict_id='c'",
                (json.dumps({"atoms": ["b"], "video": "ready", "claim": "公开乙"}),))
    visible = event(env, conflict="c", action="accept_a", when="2024-03-01T00:00:00Z")
    config._cache["node_role"] = "cloud"
    data = reconciliation.query(env, scope="all")
    assert data["total"] == data["summary"]["total"] == 1
    assert data["items"][0]["record_id"] == visible and data["items"][0]["target_url"] == "/conflicts"
    raw = json.dumps(data, ensure_ascii=False) + reconciliation.export_markdown(env, scope="all")
    assert hidden_history not in raw and "旧隐藏快照哨兵" not in raw and "隐藏冲突备注哨兵" not in raw


def test_missing_cloud_target_needs_visible_historical_proof(env):
    operation = event(env)
    env.execute("DELETE FROM evidence_ref WHERE atom_id='a'")
    env.execute("DELETE FROM knowledge_atom WHERE id='a'")
    config._cache["node_role"] = "cloud"
    assert all_items(env)[operation]["binding_status"] == "missing"
    env.execute("UPDATE video_asset SET sync_state='error' WHERE video_id='ready'")
    assert reconciliation.query(env, scope="all")["total"] == 0
    assert operation not in reconciliation.export_markdown(env, scope="all")


def test_ready_change_invalidates_visibility_even_without_kb_revision_and_old_reader_stays_consistent(env):
    operation = event(env)
    env.commit()
    config._cache["node_role"] = "cloud"
    reader = db.connect()
    try:
        reader.execute("BEGIN")
        old = reconciliation.query(reader, scope="all")
        env.execute("UPDATE video_asset SET sync_state='staging' WHERE video_id='ready'")
        env.commit()
        same_snapshot = reconciliation.query(reader, scope="all")
        fresh = reconciliation.query(env, scope="all")
        assert old["items"] == same_snapshot["items"] and old["items"][0]["record_id"] == operation
        assert same_snapshot["version"] == fresh["version"]
        assert same_snapshot["event_watermark"] == fresh["event_watermark"]
        assert same_snapshot["visibility_signature"] != fresh["visibility_signature"]
        assert fresh["items"] == [] and fresh["summary"]["total"] == 0
        reader.rollback()
        assert reconciliation.query(reader, scope="all")["items"] == []
    finally:
        reader.close()


def test_summary_independent_of_filters_pagination_and_export_includes_every_matching_record(env):
    ids = [event(env, when=f"2024-01-{day:02d}T00:00:00Z") for day in range(1, 8)]
    env.execute("UPDATE knowledge_atom SET claim='需要重新核验' WHERE id='a'")
    first = reconciliation.query(env, reason="stale", limit=2)
    second = reconciliation.query(env, reason="stale", offset=2, limit=2)
    beyond = reconciliation.query(env, reason="active", offset=99, limit=2)
    assert first["total"] == 7 and len(first["items"]) == 2 and second["total"] == 7
    assert first["summary"] == second["summary"] == beyond["summary"]
    assert beyond["items"] == [] and beyond["total"] == 0
    assert not ({x["record_id"] for x in first["items"]} & {x["record_id"] for x in second["items"]})
    exported = reconciliation.export_markdown(env, reason="stale")
    assert "本次导出：7 条" in exported
    # Markdown escapes UUID hyphens; normalize only for identity membership.
    assert all(operation in exported.replace("\\-", "-") for operation in ids)


def test_one_response_keeps_version_watermark_visibility_and_rows_on_same_read_snapshot(env, monkeypatch):
    operation = event(env)
    env.commit()
    config._cache["node_role"] = "cloud"
    original_history = review.review_history
    reader = db.connect()
    wrote = False

    def during_history(connection, *args, **kwargs):
        nonlocal wrote
        if connection is reader and not wrote:
            wrote = True
            # This runs after the report has read its version and watermark.
            env.execute("UPDATE video_asset SET sync_state='staging' WHERE video_id='ready'")
            legacy(env)
            env.commit()
        return original_history(connection, *args, **kwargs)

    monkeypatch.setattr(review, "review_history", during_history)
    try:
        old = reconciliation.query(reader, scope="all")
        assert old["event_watermark"]["high_watermark"] == old["summary"]["total"] == old["total"] == 1
        assert old["items"][0]["record_id"] == operation
        assert not reader.in_transaction
        fresh = reconciliation.query(reader, scope="all")
        assert fresh["event_watermark"]["high_watermark"] == 2
        assert fresh["summary"]["total"] == fresh["total"] == 0
        assert old["visibility_signature"] != fresh["visibility_signature"]
    finally:
        reader.close()


def test_read_never_commits_callers_pending_changes(env):
    event(env)
    assert env.in_transaction
    reconciliation.query(env, scope="all")
    reconciliation.export_markdown(env, scope="all")
    assert env.in_transaction
    env.rollback()
    assert reconciliation.query(env, scope="all")["total"] == 0


def test_markdown_and_dto_project_safe_text_without_raw_snapshot_urls_or_storage_fields(env):
    operation = event(env, note='说明 [外链](https://user:password@example.test/doc?token=QUERY_SECRET#FRAGMENT_SECRET) <script>alert(1)</script>')
    raw = json.loads(env.execute("SELECT target_snapshot_json FROM user_decision WHERE event_id=?", (operation,)).fetchone()[0])
    raw.update(source_url="https://example.test/?token=SNAPSHOT_SECRET", storage_key="C:/private/PRIVATE_PATH")
    env.execute("UPDATE user_decision SET target_snapshot_json=? WHERE event_id=?", (json.dumps(raw), operation))
    data = reconciliation.query(env, scope="all")
    exported = reconciliation.export_markdown(env, scope="all")
    combined = json.dumps(data) + exported
    for forbidden in ("QUERY_SECRET", "FRAGMENT_SECRET", "SNAPSHOT_SECRET", "PRIVATE_PATH", "password", "target_snapshot_json"):
        assert forbidden not in combined
    assert "<script>" not in exported and "&lt;script&gt;" in exported
    assert data["items"][0]["source_links"][0]["url"] == "/videos/ready"


@pytest.mark.parametrize("kwargs", [{"reason": "stale' OR 1=1--"}, {"scope": "admin"}, {"offset": -1},
    {"limit": 0}, {"limit": 101}, {"offset": True}, {"limit": False}, {"reason": []}, {"scope": None}])
def test_invalid_domain_filters_fail_without_sql_or_sensitive_echo(env, kwargs):
    with pytest.raises(reconciliation.ReconciliationError) as error:
        reconciliation.query(env, **kwargs)
    assert "OR 1=1" not in str(error.value)
    assert env.execute("SELECT COUNT(*) FROM knowledge_atom").fetchone()[0] == 3


def test_authenticated_api_json_export_and_strict_parameters(env):
    operation = event(env)
    env.execute("UPDATE knowledge_atom SET claim='新待核验内容' WHERE id='a'")
    env.commit()
    from app.main import create_app
    from app.reconciliation_api import router
    application = create_app()
    if not any(getattr(route, "path", None) == "/api/reconciliation" for route in application.routes):
        application.include_router(router)
    with TestClient(application) as client:
        for path in ("/api/reconciliation", "/api/reconciliation/export"):
            assert client.get(path).status_code == 401
            assert client.get(path, headers={"X-Sync-Token": "synthetic-reconciliation-token"}).status_code == 401
        headers = {"X-Reno-Token": "synthetic-reconciliation-token"}
        response = client.get("/api/reconciliation", headers=headers)
        assert response.status_code == 200 and response.json()["items"][0]["record_id"] == operation
        assert response.headers["cache-control"] == "no-store"
        exported = client.get("/api/reconciliation/export", headers=headers)
        assert exported.status_code == 200 and exported.headers["content-type"].startswith("text/markdown")
        assert exported.headers["content-disposition"] == 'attachment; filename="reconciliation.md"'
        assert "本次导出：1 条" in exported.text
        for query in ("reason=bad", "scope=bad", "offset=-1", "limit=101", "reason=all&reason=stale", "secret=DO_NOT_ECHO",
                      "offset=https://example.test/private/DO_NOT_ECHO?token=QUERY_SECRET", "limit=DO_NOT_ECHO"):
            bad = client.get("/api/reconciliation?" + query, headers=headers)
            assert bad.status_code == 400
            assert "DO_NOT_ECHO" not in bad.text and "QUERY_SECRET" not in bad.text
        assert client.get("/api/reconciliation/export?limit=1", headers=headers).status_code == 400


def test_optional_product_fixture_adds_namespaced_pagination_without_changing_original_atoms(tmp_path):
    pytest.importorskip("imageio_ffmpeg")
    repository = Path(__file__).resolve().parents[1]
    root = tmp_path / "browser-fixture"
    result = subprocess.run([sys.executable, str(repository / "scripts/prepare_product_fixture.py"),
        "--root", str(root), "--port", "18771", "--reconciliation"], cwd=repository,
        capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert result.returncode == 0, result.stderr
    manifest = json.loads((root / "qa-manifest.json").read_text(encoding="utf-8"))
    fixture = manifest["reconciliation_fixture"]
    assert manifest["atom_count"] == manifest["question_count"] == 20
    assert fixture["summary"]["total"] == 39 and fixture["summary"]["needs_attention"] == 36
    assert fixture["summary"]["historical"] == 1
    assert len(fixture["record_ids"]) == 39 and fixture["version"] == "reconciliation-v1"
    with sqlite3.connect((root / "data/reno.db").as_uri() + "?mode=ro", uri=True) as con:
        rows = con.execute("SELECT id,status FROM knowledge_atom WHERE id NOT LIKE 'qa_reconcile_%'").fetchall()
        assert len(rows) == 20 and all(status == "candidate" for _, status in rows)
        assert con.execute("SELECT COUNT(*) FROM user_decision WHERE atom_id NOT LIKE 'qa_reconcile_%'").fetchone()[0] == 0
