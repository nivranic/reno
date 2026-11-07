"""Actual projections and reconciliation must agree for old offset timestamps."""
import pytest

from reno import review, reconciliation
from tests.test_review_replay import con, conflict


def event(con, action, when, identifier, *, is_conflict=False):
    result = review.record_decision(con, action=action, event_id=identifier,
                                   **({"conflict_id": "conflict"} if is_conflict else {"atom_id": "a"}))
    con.execute("UPDATE user_decision SET decided_at=? WHERE decision_id=?", (when, result["decision_id"]))
    return result


def test_projection_sorts_actual_instants_instead_of_iso_text(con):
    earlier = event(con, "confirm", "2024-01-01T12:00:00+08:00", "ffffffff-ffff-4fff-8fff-ffffffffffff")
    later = event(con, "reject", "2024-01-01T05:00:00Z", "00000000-0000-4000-8000-000000000001")
    state = review.replay(con)["atoms"]["a"]
    assert state["review_status"] == "user_rejected"
    assert state["event_id"] == later["event_id"]
    rows = reconciliation.query(con, scope="all")["items"]
    assert [row["event_id"] for row in rows] == [later["event_id"], earlier["event_id"]]
    assert rows[1]["superseded_by"] == later["event_id"]


def test_equal_instants_with_different_offsets_use_stable_event_key(con):
    smaller = event(con, "confirm", "2024-01-01T12:00:00+08:00", "00000000-0000-4000-8000-000000000001")
    larger = event(con, "reject", "2024-01-01T04:00:00Z", "ffffffff-ffff-4fff-8fff-ffffffffffff")
    state = review.projection(con)["atoms"]["a"]
    assert state["event_id"] == larger["event_id"]
    assert state["review_status"] == "user_rejected"
    rows = reconciliation.query(con, scope="all")["items"]
    assert rows[0]["event_id"] == larger["event_id"]
    assert rows[1]["event_id"] == smaller["event_id"]


@pytest.mark.parametrize("invalid", ["action", "revised_claim"])
@pytest.mark.parametrize("is_conflict", [False, True])
def test_content_bound_but_invalid_old_decision_never_becomes_effective(con, invalid, is_conflict):
    if is_conflict:
        conflict(con)
    record = event(con, "accept_a" if is_conflict else "confirm", "2024-01-01T00:00:00Z",
                   "00000000-0000-4000-8000-000000000001", is_conflict=is_conflict)
    column, value = ("action", "invented-action") if invalid == "action" else ("revised_claim", "旧版任意改写")
    con.execute(f"UPDATE user_decision SET {column}=? WHERE decision_id=?", (value, record["decision_id"]))
    state = review.replay(con)
    if is_conflict:
        assert not state["conflicts"]["conflict"].startswith("decided:")
    else:
        assert state["atoms"]["a"]["review_status"] == "needs_recheck"
        assert con.execute("SELECT status FROM knowledge_atom WHERE id='a'").fetchone()[0] == "candidate"
    assert state["attention"][0]["event_id"] == record["event_id"]
    assert state["attention"][0]["binding_status"] == "active"
