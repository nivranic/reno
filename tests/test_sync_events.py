"""Actual independent SQLite files; review exchange never touches real user data."""
import json
import sqlite3
import uuid
from copy import deepcopy

import pytest

from tests.migration_helpers import migration_profile

from reno import db, derivation, evidence_identity as identity, migrations, personal_schema, review, sync_events
from reno.sync_protocol import SyncError


def populate(con):
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256) VALUES('video','人工同步样本','synthetic')")
    for aid, claim in (("a", "防水施工前核对基层"), ("b", "另一种基层处理")):
        con.execute("INSERT INTO knowledge_atom(id,video_id,claim,polarity,status,conditions_json,parameters_json) "
                    "VALUES(?,'video',?,'recommend','candidate','{}','[]')", (aid, claim))
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
                    "VALUES(?,'video','asr',?,100,200,?)", (aid, aid, claim))
    db.replace_conflicts(con, [{"conflict_id": "conflict", "type": "method_conflict", "members": {
        "side_a": {"atoms": ["a"], "claim": "方法A"}, "side_b": {"atoms": ["b"], "claim": "方法B"}}}])
    review.replay(con)
    project = con.execute("SELECT project_id FROM renovation_project").fetchone()[0]
    card = str(uuid.uuid4())
    con.execute("INSERT INTO decision_card(card_id,project_id,title,state,created_at,updated_at) "
                "VALUES(?,?,'私人选择','adopted','2026-01-01','2026-01-01')", (card, project))
    con.execute("INSERT INTO personal_revision(entity_kind,entity_id,revision,snapshot_json,created_at) "
                "VALUES('card',?,1,'{\"decision\":\"保留\"}','2026-01-01')", (card,))
    con.execute("INSERT INTO mutation_receipt(operation_id,scope,request_hash,response_status,response_json,created_at) "
                "VALUES(?,'private','private-hash',200,'{\"ok\":true}','2026-01-01')", (str(uuid.uuid4()),))
    con.commit()


@pytest.fixture()
def pair(tmp_path):
    connections = []
    for name in ("a", "b"):
        path = tmp_path / name / "reno.db"
        db.initialize(path)
        con = db.connect(path)
        populate(con)
        # These transport rollback tests need an actually current dispute,
        # independent of the intentionally unbound shared populate fixture.
        binding = derivation.bind_inputs({atom["id"]: atom for atom in db.all_atoms(con)}, ["a", "b"])
        con.execute("UPDATE conflict_case SET derivation_json=? WHERE conflict_id='conflict'", (json.dumps(binding),))
        review.replay(con)
        con.commit()
        connections.append(con)
    yield connections
    for con in connections:
        con.close()


def event(con, *, atom="a", conflict=None, action="confirm", when="2025-01-02T12:00:00.000Z", number=None):
    result = review.record_decision(con, atom_id=atom if conflict is None else None,
                                    conflict_id=conflict, action=action, client_id="node-origin",
                                    event_id=str(uuid.UUID(int=number)) if number else None)
    # Controlled fixture time, not transport normalization.
    con.execute("UPDATE user_decision SET created_at=?,decided_at=? WHERE event_id=?",
                (when, when, result["event_id"]))
    review.replay(con)
    con.commit()
    row = con.execute("SELECT * FROM user_decision WHERE event_id=?", (result["event_id"],)).fetchone()
    return {key: row[key] for key in sync_events.WIRE_FIELDS}


def personal_snapshot(con):
    from pathlib import Path
    path = Path(con.execute("PRAGMA database_list").fetchone()[2])
    result = {table: [tuple(row) for row in con.execute(f"SELECT * FROM {table} ORDER BY rowid")]
              for table in personal_schema.TABLES}
    result["meta"] = [tuple(row) for row in con.execute("SELECT * FROM meta WHERE key!='kb_revision' ORDER BY key")]
    result["node_identity"] = (path.parent / "node-identity.json").read_bytes()
    return result


def wire_history(con):
    return sorted(sync_events.export_events(con)["events"], key=lambda row: row["event_id"])


def test_two_nodes_converge_regardless_of_receipt_order_and_keep_personal_records(pair):
    a, b = pair
    snapshots = [personal_snapshot(con) for con in pair]
    a1 = event(a, action="confirm", number=1)
    a2 = event(a, conflict="conflict", action="accept_a", number=2)
    b1 = event(b, action="reject", number=3)  # Same time: event_id wins deterministically.
    b2 = event(b, atom="b", action="confirm", number=4)
    assert sync_events.merge_events(a, [b2, b1])["inserted"] == 2
    assert sync_events.merge_events(b, [a1, a2])["inserted"] == 2
    for con in pair:
        con.commit()
    assert wire_history(a) == wire_history(b)
    projected = review.projection(a)
    assert projected == review.projection(b)
    assert projected["atoms"]["a"]["review_status"] == "user_rejected"
    assert projected["atoms"]["b"]["review_status"] == "user_confirmed"
    assert projected["conflicts"]["conflict"] == "decided:accept_a"
    assert [personal_snapshot(con) for con in pair] == snapshots
    assert db.knowledge_version(a)["library_id"] != db.knowledge_version(b)["library_id"]


def test_retransmission_preserves_origin_payload_and_does_not_bump_projection(pair):
    a, b = pair
    incoming = event(a)
    before = db.knowledge_version(b)["kb_revision"]
    result = sync_events.merge_events(b, [incoming, incoming])
    assert result == {"received": 2, "inserted": 1, "duplicates": 1, "projection_changed": True}
    b.commit()
    changed = db.knowledge_version(b)["kb_revision"]
    assert changed > before
    assert sync_events.merge_events(b, [incoming]) == {
        "received": 1, "inserted": 0, "duplicates": 1, "projection_changed": False}
    assert wire_history(b) == [incoming]
    assert db.knowledge_version(b)["kb_revision"] == changed


def test_stale_event_invalidates_knowledge_version_even_without_status_change(pair):
    a, b = pair
    incoming = event(a)
    b.execute("DELETE FROM conflict_case")
    b.execute("UPDATE knowledge_atom SET claim='已经改变的内容' WHERE id='a'")
    review.replay(b)
    b.commit()
    before = db.knowledge_version(b)["kb_revision"]
    assert b.execute("SELECT status FROM knowledge_atom WHERE id='a'").fetchone()[0] == "candidate"
    result = sync_events.merge_events(b, [incoming])
    assert result["inserted"] == 1 and result["projection_changed"] is False
    assert review.projection(b)["atoms"]["a"]["review_stale"] is True
    assert b.execute("SELECT status FROM knowledge_atom WHERE id='a'").fetchone()[0] == "candidate"
    assert db.knowledge_version(b)["kb_revision"] > before
    b.rollback()
    assert db.knowledge_version(b)["kb_revision"] == before
    assert review.projection(b)["atoms"]["a"]["review_stale"] is False


@pytest.mark.parametrize("field", ["note", "client_id", "created_at", "decided_at", "target_snapshot_json"])
def test_uuid_reuse_rolls_back_earlier_batch_rows_and_preserves_caller_writes(pair, field):
    a, b = pair
    original = event(a)
    sync_events.merge_events(b, [original])
    b.commit()
    second = event(a, atom="b")
    altered = deepcopy(original)
    if field.endswith("at"):
        altered[field] = "2025-01-03T12:00:00.000Z"
    elif field == "target_snapshot_json":
        altered[field] = json.dumps(json.loads(altered[field]), ensure_ascii=False, indent=2)
    else:
        altered[field] = "different"
    b.execute("UPDATE renovation_project SET name='调用方未提交修改'")
    before_version = db.knowledge_version(b)
    with pytest.raises(SyncError) as caught:
        sync_events.merge_events(b, [second, altered])
    assert caught.value.status == 409 and caught.value.code == "IDEMPOTENCY_KEY_REUSED"
    assert wire_history(b) == [original]
    assert db.knowledge_version(b) == before_version
    assert b.execute("SELECT name FROM renovation_project").fetchone()[0] == "调用方未提交修改"
    assert b.in_transaction


def test_conflicting_uuid_inside_one_batch_rolls_back_and_does_not_leave_transaction(pair):
    a, b = pair
    original = event(a)
    conflict = {**original, "note": "另一条载荷"}
    before = db.knowledge_version(b)
    with pytest.raises(SyncError, match="UUID"):
        sync_events.merge_events(b, [original, conflict])
    assert b.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
    assert db.knowledge_version(b) == before
    assert not b.in_transaction


def test_success_never_commits_and_caller_rollback_reverts_events_and_projection(pair):
    a, b = pair
    incoming = event(a)
    original_projection = review.projection(b)
    original_version = db.knowledge_version(b)
    assert not b.in_transaction
    sync_events.merge_events(b, [incoming])
    assert b.in_transaction
    path = b.execute("PRAGMA database_list").fetchone()[2]
    with sqlite3.connect(path) as reader:
        assert reader.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
        assert reader.execute("SELECT status FROM knowledge_atom WHERE id='a'").fetchone()[0] == "disputed"
    b.rollback()
    assert b.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
    assert review.projection(b) == original_projection
    assert db.knowledge_version(b) == original_version


def test_late_old_event_survives_watermark_pagination_and_stream_identity(pair):
    a, b = pair
    first = event(a, number=1)
    second = event(a, atom="b", number=2)
    page = sync_events.export_events(a, limit=1)
    assert page["events"] == [first] and page["has_more"]
    assert page["high_watermark"] == 2
    old = event(b, when="2001-01-01T00:00:00.000Z", number=3)
    sync_events.merge_events(a, [old])
    a.commit()
    tail = sync_events.export_events(a, after=page["next_cursor"], through=page["high_watermark"], limit=1)
    assert tail["events"] == [second] and not tail["has_more"]
    assert tail["high_watermark"] == 2 and tail["next_cursor"] == 2
    later = sync_events.export_events(a, after=tail["next_cursor"])
    assert later["events"] == [old] and later["next_cursor"] == 3
    assert later["stream"] == {k: db.knowledge_version(a)[k] for k in ("library_id", "library_epoch")}
    assert all("decision_id" not in row for row in later["events"])
    a.execute("UPDATE meta SET value='2' WHERE key='library_epoch'")
    assert sync_events.export_events(a)["stream"]["library_epoch"] == 2


def test_changed_content_and_missing_target_keep_history_without_applying_origin_choice(pair):
    a, b = pair
    bound = event(a)
    missing = event(a, atom="b")
    conflict = event(a, conflict="conflict", action="accept_a")
    b.execute("UPDATE knowledge_atom SET claim='相同 ID 的另一条主张' WHERE id='a'")
    b.execute("DELETE FROM knowledge_atom WHERE id='b'")
    sync_events.merge_events(b, [bound, missing, conflict])
    projected = review.projection(b)
    assert projected["atoms"]["a"]["review_status"] == "needs_recheck"
    assert projected["conflicts"]["conflict"] == "needs_review"
    histories = {item["event_id"]: item for item in review.review_history(b)}
    assert histories[bound["event_id"]]["binding_status"] == "stale"
    assert histories[missing["event_id"]]["binding_status"] == "missing"
    assert histories[conflict["event_id"]]["binding_status"] == "stale"
    assert histories[bound["event_id"]]["target_snapshot"]["claim"] == "防水施工前核对基层"
    assert wire_history(b) == wire_history(a)


@pytest.mark.parametrize("created_at", [None, "not-a-date", "2025-01-01T12:00:00", "2025-01-01T12:00:00+08:00"])
@pytest.mark.parametrize("targets", [("a", None), ("a", "conflict"), (None, None)])
def test_actual_v0_migration_events_preserve_old_time_and_unbound_history(pair, tmp_path, created_at, targets):
    from tests.test_personal_migrations import legacy_database
    a, b = pair
    path = tmp_path / "legacy" / "reno.db"
    path.parent.mkdir()
    legacy_database(path)
    with sqlite3.connect(path) as old:
        old.execute("UPDATE user_decision SET atom_id=?,conflict_id=?,created_at=?,revised_claim='旧改写文本'",
                    (*targets, created_at))
    db.migrate(path, profile=migration_profile(path))
    migrated = db.connect(path)
    try:
        rows = sync_events.export_events(migrated)["events"]
        assert len(rows) == 1
        original = rows[0]
        assert original["created_at"] == created_at
        assert original["decided_at"] == migrations._legacy_time(created_at)
        assert original["target_fingerprint"] is None
        assert original["revised_claim"] == "旧改写文本"
        sync_events.merge_events(b, rows)
        assert wire_history(b) == rows
        assert review.review_history(b)[0]["binding_status"] == "legacy_unbound"
        assert all(state["review_status"] != "user_confirmed" for state in review.projection(b)["atoms"].values())
        assert review.projection(b)["conflicts"]["conflict"] == "needs_review"
    finally:
        migrated.close()


@pytest.mark.parametrize("changes,expected", [
    ({"decision_id": 42}, 400), ({"status": "verified"}, 400), ({"project_id": "private"}, 400),
    ({"event_id": "not-a-uuid"}, 400), ({"event_id": "F0000000-0000-0000-0000-000000000001"}, 400),
    ({"event_schema_version": 2}, 409), ({"event_schema_version": True}, 409),
    ({"atom_id": None}, 400), ({"conflict_id": "conflict"}, 400), ({"action": "adopted"}, 400),
    ({"revised_claim": "悄悄改写"}, 400), ({"note": 123}, 400),
    ({"decided_at": "2025-01-01T12:00:00"}, 400), ({"decided_at": "2999-01-01T00:00:00.000Z"}, 400),
    ({"decided_at": "2025-02-30T00:00:00.000Z"}, 400),
    ({"decided_at": "2025-01-01T12:00:00.000+08:00"}, 400),
    ({"created_at": None}, 400), ({"target_snapshot_json": "[]"}, 400),
    ({"target_snapshot_json": {"fingerprint_version": 1}}, 400),
    ({"target_snapshot_json": '{"fingerprint_version":1,"fingerprint_version":2}'}, 400),
    ({"target_snapshot_json": '{"fingerprint_version":1,"value":NaN}'}, 400),
    ({"target_snapshot_json": '{"fingerprint_version":1,"value":1e999}'}, 400),
    ({"target_snapshot_json": '{"fingerprint_version":1,"claim":"tampered"}'}, 400),
    ({"target_fingerprint": "v1:" + "0" * 64}, 400), ({"target_fingerprint": None}, 400),
    ({"note": "x" * 2001}, 400), ({"client_id": "\ud800"}, 400),
    ({"target_snapshot_json": "x" * (sync_events.MAX_SNAPSHOT_BYTES + 1)}, 413),
    ({"target_snapshot_json": '{"nested":' + "[" * 20 + "0" + "]" * 20 + "}"}, 413),
])
def test_bad_late_event_is_controlled_and_batch_atomic(pair, changes, expected):
    a, b = pair
    valid = event(a)
    bad = {**valid, **changes}
    before = db.knowledge_version(b)
    with pytest.raises(SyncError) as caught:
        sync_events.merge_events(b, [valid, bad])
    assert caught.value.status == expected
    assert b.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
    assert db.knowledge_version(b) == before
    assert not b.in_transaction


def test_missing_wire_fields_and_unbounded_batches_are_rejected(pair):
    a, b = pair
    incoming = event(a)
    del incoming["note"]
    with pytest.raises(SyncError):
        sync_events.merge_events(b, [incoming])
    for rows in ({}, [None] * (sync_events.MAX_EVENTS + 1)):
        with pytest.raises(SyncError):
            sync_events.merge_events(b, rows)
    assert sync_events.merge_events(b, []) == {
        "received": 0, "inserted": 0, "duplicates": 0, "projection_changed": False}
    assert not b.in_transaction


@pytest.mark.parametrize("change", [
    {"target_fingerprint": "v1:" + "a" * 64},
    {"decided_at": "2025-01-01T00:00:00.000Z"},
    {"client_id": "new-client"},
    {"target_snapshot_json": '{"binding_status":"active"}'},
    {"created_at": "0001-01-01T00:00:00+23:59"},
])
def test_forged_legacy_binding_and_overflowing_historical_time_are_rejected(pair, change):
    a, b = pair
    origin = event(a)
    legacy = {**origin, "client_id": "legacy", "event_schema_version": 0,
              "created_at": None, "decided_at": None, "target_fingerprint": None,
              "target_snapshot_json": json.dumps({"binding_status": "legacy_unbound", "atom_id": "a",
                                                  "conflict_id": None, "created_at": None})}
    with pytest.raises(SyncError):
        sync_events.merge_events(b, [{**legacy, **change}])
    assert b.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0


def test_replay_failure_rolls_back_received_events_and_projection(pair, monkeypatch):
    a, b = pair
    incoming = event(a)
    before = db.knowledge_version(b)

    def broken_replay(con):
        con.execute("UPDATE knowledge_atom SET status='rejected' WHERE id='b'")
        raise RuntimeError("injected replay failure")

    monkeypatch.setattr(review, "replay", broken_replay)
    with pytest.raises(RuntimeError, match="injected replay"):
        sync_events.merge_events(b, [incoming])
    assert b.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
    assert b.execute("SELECT status FROM knowledge_atom WHERE id='b'").fetchone()[0] == "disputed"
    assert db.knowledge_version(b) == before
    assert not b.in_transaction


def test_payload_byte_budget_controls_merge_and_export_pagination(pair):
    a, b = pair
    incoming = event(a)
    snapshot = json.loads(incoming["target_snapshot_json"])
    snapshot["claim"] = "大" * 70000
    incoming["target_snapshot_json"] = identity.canonical_json(snapshot)
    incoming["target_fingerprint"] = identity.fingerprint(snapshot)
    many = [{**incoming, "event_id": str(uuid.uuid4())} for _ in range(24)]
    with pytest.raises(SyncError) as caught:
        sync_events.merge_events(b, many)
    assert caught.value.status == 413
    assert b.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 0
    sync_events.merge_events(b, many[:12])
    sync_events.merge_events(b, many[12:])
    b.commit()
    page = sync_events.export_events(b)
    assert 0 < len(page["events"]) < len(many) and page["has_more"]
    tail = sync_events.export_events(b, after=page["next_cursor"], through=page["high_watermark"])
    assert page["events"] + tail["events"] == many
    assert not tail["has_more"]


@pytest.mark.parametrize("options,status", [
    ({"after": -1}, 400), ({"after": True}, 400), ({"after": 2**63}, 400),
    ({"limit": 0}, 400), ({"limit": 201}, 400), ({"limit": False}, 400),
    ({"through": -1}, 400), ({"through": 1}, 409), ({"after": 1}, 409),
])
def test_cursor_validation_is_bounded_and_read_only(pair, options, status):
    _, b = pair
    with pytest.raises(SyncError) as caught:
        sync_events.export_events(b, **options)
    assert caught.value.status == status
    assert not b.in_transaction


def test_export_does_not_commit_existing_caller_changes(pair):
    a, _ = pair
    event(a)
    a.execute("UPDATE renovation_project SET name='未提交'")
    assert sync_events.export_events(a)["events"]
    assert a.in_transaction
    a.rollback()
    assert a.execute("SELECT name FROM renovation_project").fetchone()[0] == "我的装修"
