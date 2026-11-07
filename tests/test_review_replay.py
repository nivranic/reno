"""Content-bound review invariants on explicitly initialized disposable DBs."""
import json
import uuid

import pytest

from reno import db, derivation, review
from reno.evidence_identity import atom_fingerprint, load_atom, load_target


@pytest.fixture()
def con(tmp_path):
    path = tmp_path / "review.db"
    db.initialize(path)
    connection = db.connect(path)
    for aid, claim in (("a", "防水施工须核对基层"), ("b", "另一种基层处理")):
        connection.execute(
            "INSERT INTO knowledge_atom(id,video_id,claim,polarity,status,conditions_json,parameters_json) "
            "VALUES (?,?,?,'recommend','candidate','{}','[]')", (aid, "video", claim))
        connection.execute(
            "INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
            "VALUES (?, 'video','asr',?,100,200,?)", (aid, aid, claim))
    connection.commit()
    yield connection
    connection.close()


def conflict(con, reverse=False):
    a, b = ("b", "a") if reverse else ("a", "b")
    binding = derivation.bind_inputs({atom["id"]: atom for atom in db.all_atoms(con)}, [a, b])
    db.replace_clusters(con, [{"cluster_id": "cluster", "canonical_topic": "基层", "relation": "conflicting",
                               "members": [a, b], "linked_conflict": "conflict", "derivation_json": binding}])
    db.replace_conflicts(con, [{"conflict_id": "conflict", "type": "method_conflict", "members": {
        "side_a": {"atoms": [a], "claim": a, "video": "video"},
        "side_b": {"atoms": [b], "claim": b, "video": "video"}}, "derivation_json": binding}])


def test_fingerprint_ignores_extraction_metadata_but_binds_conditions(con):
    atom = load_atom(con, "a")
    before = atom_fingerprint(atom)
    atom.update(confidence=0.99, status="verified", created_at="different", model="other")
    assert atom_fingerprint(atom) == before
    atom["conditions_json"] = '{"基层":"旧基层"}'
    assert atom_fingerprint(atom) != before


def test_independent_atom_review_survives_same_content_rejudge(con):
    review.record_decision(con, atom_id="a", action="confirm")
    conflict(con)
    projected = review.replay(con)
    assert projected["atoms"]["a"]["review_status"] == "user_confirmed"
    assert projected["atoms"]["a"]["has_conflict"] is True
    assert con.execute("SELECT status FROM knowledge_atom WHERE id='a'").fetchone()[0] == "verified"


def test_same_id_different_content_never_inherits_confirmation(con):
    review.record_decision(con, atom_id="a", action="confirm")
    con.execute("UPDATE knowledge_atom SET claim='完全不同的建议' WHERE id='a'")
    state = review.replay(con)
    assert state["atoms"]["a"]["review_status"] == "needs_recheck"
    assert state["attention"][0]["binding_status"] == "stale"
    assert state["attention"][0]["target_snapshot"]["claim"] == "防水施工须核对基层"
    assert con.execute("SELECT status FROM knowledge_atom WHERE id='a'").fetchone()[0] == "candidate"


def test_idempotent_retry_before_target_or_state_validation(con):
    operation = str(uuid.uuid4())
    first = review.record_decision(con, atom_id="a", action="reject", event_id=operation)
    version = db.knowledge_version(con)
    second = review.record_decision(con, atom_id="a", action="reject", event_id=operation)
    assert second["decision_id"] == first["decision_id"] and second["replayed"]
    assert db.knowledge_version(con) == version
    con.execute("DELETE FROM knowledge_atom WHERE id='a'")
    assert review.record_decision(con, atom_id="a", action="reject", event_id=operation)["replayed"]
    with pytest.raises(review.ReviewError) as error:
        review.record_decision(con, atom_id="a", action="confirm", event_id=operation)
    assert error.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_conflict_side_swap_invalidates_accept_a_without_rejecting_atoms(con):
    conflict(con)
    review.record_decision(con, conflict_id="conflict", action="accept_a")
    assert all(a["review_status"] == "unreviewed" for a in review.projection(con)["atoms"].values())
    conflict(con, reverse=True)
    state = review.replay(con)
    assert not state["conflicts"]["conflict"].startswith("decided:")
    assert state["attention"][0]["binding_status"] == "stale"


def test_clear_review_and_target_type_validation(con):
    review.record_decision(con, atom_id="a", action="reject")
    review.record_decision(con, atom_id="a", action="clear_review")
    assert review.projection(con)["atoms"]["a"]["review_status"] == "unreviewed"
    with pytest.raises(review.ReviewError):
        review.record_decision(con, atom_id="a", conflict_id="conflict", action="confirm")
    with pytest.raises(review.ReviewError):
        review.record_decision(con, atom_id="a", action="accept_a")


def test_legacy_unbound_is_history_not_fresh_confirmation(con):
    con.execute("INSERT INTO user_decision(atom_id,action,created_at,event_id,decided_at,event_schema_version) "
                "VALUES ('a','confirm',?,?,?,0)", (db.now(), str(uuid.uuid4()), review.utc_now()))
    state = review.replay(con)
    assert state["atoms"]["a"]["review_status"] == "needs_recheck"
    assert state["attention"][0]["binding_status"] == "legacy_unbound"


def test_future_event_does_not_win_replay(con):
    event = review.record_decision(con, atom_id="a", action="confirm")
    con.execute("UPDATE user_decision SET decided_at='2999-01-01T00:00:00.000Z' WHERE event_id=?",
                (event["event_id"],))
    state = review.replay(con)
    assert state["atoms"]["a"]["review_status"] == "unreviewed"
    assert state["attention"][0]["binding_status"] == "invalid_time"


def test_stale_client_fingerprint_rejected(con):
    saved = load_target(con, "atom", "a")[1]
    con.execute("UPDATE knowledge_atom SET claim='新内容' WHERE id='a'")
    with pytest.raises(review.ReviewError) as error:
        review.record_decision(con, atom_id="a", action="confirm", target_fingerprint=saved)
    assert error.value.code == "EVIDENCE_CHANGED"
