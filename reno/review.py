"""Append-only knowledge review and deterministic, content-bound projections.

Personal project choices belong to decision_card, never to this module.
All mutation helpers participate in the caller's transaction and never commit.
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

from . import evidence_identity as identity

ATOM_ACTIONS = {"confirm", "reject", "clear_review"}
CONFLICT_ACTIONS = {"accept_a", "accept_b", "both", "reject"}


class ReviewError(ValueError):
    def __init__(self, message: str, code: str = "INVALID_REQUEST", status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _next_local_time(con, client_id: str) -> str:
    """Preserve sequential local edits even when the clock has millisecond ties."""
    current = datetime.now(timezone.utc)
    prior = con.execute("SELECT decided_at FROM user_decision WHERE client_id=? "
                        "ORDER BY decided_at DESC LIMIT 1", (client_id,)).fetchone()
    previous = _event_time(dict(prior)) if prior else None
    if previous is not None and current <= previous + timedelta(milliseconds=1):
        current = previous + timedelta(milliseconds=1)
    return current.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _event_time(event: dict) -> datetime | None:
    try:
        value = datetime.fromisoformat(str(event.get("decided_at") or "").replace("Z", "+00:00"))
        if value.tzinfo is None or value > datetime.now(timezone.utc) + timedelta(minutes=5):
            return None
        return value.astimezone(timezone.utc)
    except (TypeError, ValueError, OverflowError):
        return None


def event_order(event: dict) -> tuple:
    """Shared history/projection order; ISO strings with offsets are not sortable."""
    when = _event_time(event)
    return (when is not None, when or datetime.min.replace(tzinfo=timezone.utc),
            event.get("event_id") or "", event.get("decision_id") or 0)


def decision_valid(event: dict) -> bool:
    """Binding and timestamps do not make an unsupported old action effective."""
    atom, conflict = event.get("atom_id"), event.get("conflict_id")
    return (bool(atom) != bool(conflict) and not event.get("revised_claim")
            and isinstance(event.get("action"), str)
            and event["action"] in (ATOM_ACTIONS if atom else CONFLICT_ACTIONS))


def record_decision(con, atom_id=None, conflict_id=None, action="", revised_claim=None,
                    note=None, event_id=None, client_id=None, target_fingerprint=None) -> dict:
    if bool(atom_id) == bool(conflict_id):
        raise ReviewError("exactly one atom_id or conflict_id is required")
    for value in (atom_id, conflict_id):
        if value is not None and (not isinstance(value, str) or not value.strip() or len(value) > 200):
            raise ReviewError("invalid review target")
    allowed = ATOM_ACTIONS if atom_id else CONFLICT_ACTIONS
    if not isinstance(action, str) or action not in allowed:
        raise ReviewError(f"action must be one of {sorted(allowed)}")
    if revised_claim:
        raise ReviewError("直接改写来源内容尚未开放，请使用复核备注")
    if note is not None and (not isinstance(note, str) or len(note) > 2000):
        raise ReviewError("note must be a string of at most 2000 chars")
    if event_id is not None:
        try:
            event_id = str(uuid.UUID(event_id))
        except (TypeError, ValueError, AttributeError):
            raise ReviewError("event_id must be a UUID") from None
    else:
        event_id = str(uuid.uuid4())
    if client_id is not None and (not isinstance(client_id, str) or len(client_id) > 200):
        raise ReviewError("invalid client_id")
    note = note or None
    prior = con.execute("SELECT * FROM user_decision WHERE event_id=?", (event_id,)).fetchone()
    if prior:
        p = dict(prior)
        same = (p["atom_id"] == atom_id and p["conflict_id"] == conflict_id
                and p["action"] == action and (p["note"] or None) == note
                and (not target_fingerprint or target_fingerprint == p["target_fingerprint"]))
        if not same:
            raise ReviewError("此操作编号已用于另一条决定", "IDEMPOTENCY_KEY_REUSED", 409)
        return {"ok": True, "event_id": event_id, "decision_id": p["decision_id"], "replayed": True}
    kind, target_id = ("atom", atom_id) if atom_id else ("conflict", conflict_id)
    target = identity.load_target(con, kind, target_id)
    if target is None:
        raise ReviewError(f"unknown {kind} {target_id}", "NOT_FOUND", 404)
    snap, content_hash = target
    if target_fingerprint is not None and target_fingerprint != content_hash:
        raise ReviewError("来源内容已经变化，请重新核对", "EVIDENCE_CHANGED", 409)
    when = _next_local_time(con, client_id or "local")
    cursor = con.execute(
        """INSERT INTO user_decision
           (atom_id,conflict_id,action,revised_claim,note,created_at,event_id,client_id,
            decided_at,target_fingerprint,target_snapshot_json,event_schema_version)
           VALUES (?,?,?,NULL,?,?,?,?,?,?,?,1)""",
        (atom_id, conflict_id, action, note, when, event_id, client_id or "local",
         when, content_hash, identity.canonical_json(snap)))
    replay(con)
    return {"ok": True, "event_id": event_id, "decision_id": cursor.lastrowid, "replayed": False}


def review_history(con, kind: str | None = None, target_id: str | None = None, *, public=False) -> list[dict]:
    where, params = [], []
    if kind:
        if kind not in ("atom", "conflict"):
            raise ReviewError("kind must be atom or conflict")
        column = "atom_id" if kind == "atom" else "conflict_id"
        where.append(f"{column} IS NOT NULL")
        if target_id:
            where.append(f"{column}=?")
            params.append(target_id)
    if public:
        from . import sync_visibility
        where.append(sync_visibility.history_sql())
    sql = "SELECT * FROM user_decision"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY decided_at DESC,event_id DESC"
    records, cache = [], {}
    for row in con.execute(sql, params):
        e = dict(row)
        k = "atom" if e.get("atom_id") else "conflict"
        tid = e.get("atom_id") or e.get("conflict_id")
        invalid_target = bool(e.get("atom_id")) == bool(e.get("conflict_id"))
        if invalid_target or not e.get("target_fingerprint"):
            state = "legacy_unbound"
        elif _event_time(e) is None:
            state = "invalid_time"
        else:
            cache_key = (k, tid, e["target_fingerprint"])
            if cache_key not in cache:
                cache[cache_key] = identity.reference_state(con, k, tid, e["target_fingerprint"])
            state = cache[cache_key]
        e["binding_status"] = state
        e["target_snapshot"] = identity.decoded(e.pop("target_snapshot_json", None), {})
        if public and not sync_visibility.snapshot_visible(
                con, e["target_snapshot"], require_proof=(state == "legacy_unbound"
                                                         or not sync_visibility.target_visible(con, k, tid))):
            continue
        records.append(e)
    records.sort(key=event_order, reverse=True)
    return records


def projection(con, *, public=False) -> dict:
    """Compute review and conflict axes without changing the database."""
    owned = not con.in_transaction
    if owned:
        con.execute("BEGIN")
    try:
        return _projection(con, public=public)
    finally:
        if owned:
            con.rollback()


def _projection(con, *, public=False):
    from . import db, derivation, sync_visibility
    atoms = {a["id"]: a for a in db.all_atoms(con, public=public)}
    conflict_where = " WHERE " + sync_visibility.conflict_sql() if public else ""
    conflicts = {c["conflict_id"]: dict(c) for c in con.execute("SELECT * FROM conflict_case" + conflict_where)}
    validities = derivation.evaluate_relations(con, public=public)
    conflicting, outdated = set(), set()
    cluster_where = " AND " + sync_visibility.cluster_sql() if public else ""
    for row in con.execute("SELECT cluster_id,members_json FROM knowledge_cluster WHERE relation='conflicting'" + cluster_where):
        members = identity.decoded(row["members_json"], [])
        if isinstance(members, list):
            target = conflicting if validities["clusters"][row["cluster_id"]]["state"] == "active" else outdated
            target.update(member for member in members if isinstance(member, str))
    # Conflict cases can also exist without a linked cluster after partial repair.
    for conflict in conflicts.values():
        target = conflicting if validities["conflicts"][conflict["conflict_id"]]["state"] == "active" else outdated
        for key in ("side_a_json", "side_b_json"):
            side = identity.decoded(conflict.get(key), {})
            if isinstance(side, dict) and isinstance(side.get("atoms"), list):
                target.update(member for member in side["atoms"] if isinstance(member, str))
    atom_states = {aid: {"review_status": "unreviewed", "has_conflict": aid in conflicting,
                         "has_outdated_conflict": aid in outdated, "review_stale": False} for aid in atoms}
    conflict_states = {}
    for cid, c in conflicts.items():
        analysis = identity.decoded(c.get("analysis_json"), {})
        conclusion = analysis.get("conditional_conclusion") if isinstance(analysis, dict) else {}
        needs_user = bool(isinstance(conclusion, dict) and conclusion.get("authority_gap"))
        conflict_states[cid] = "needs_user_decision" if needs_user else "needs_review"
    latest, attention = {}, []
    for event in review_history(con, public=public):
        kind = "atom" if event.get("atom_id") else "conflict"
        target_id = event.get("atom_id") or event.get("conflict_id")
        if event["binding_status"] != "active" or not decision_valid(event):
            attention.append(event)
        if event["binding_status"] == "invalid_time":
            continue
        key = (kind, target_id)
        if key not in latest:
            latest[key] = event
    for (kind, target_id), event in latest.items():
        if kind == "atom" and target_id in atom_states:
            state = atom_states[target_id]
            if event["binding_status"] != "active":
                state.update(review_status="needs_recheck", review_stale=True)
            elif not decision_valid(event):
                state.update(review_status="needs_recheck")
            else:
                state["review_status"] = {"confirm": "user_confirmed", "reject": "user_rejected",
                                          "clear_review": "unreviewed"}.get(event["action"], "needs_recheck")
            state["event_id"] = event.get("event_id")
        elif kind == "conflict" and target_id in conflict_states and event["binding_status"] == "active":
            if decision_valid(event):
                conflict_states[target_id] = "decided:" + event["action"]
    for state in atom_states.values():
        state["status"] = {"user_rejected": "rejected", "user_confirmed": "verified"}.get(
            state["review_status"], "disputed" if state["has_conflict"] else "candidate")
    return {"atoms": atom_states, "conflicts": conflict_states, "attention": attention}


def replay(con) -> dict:
    from . import db
    projected = projection(con)
    changed = False
    for aid, state in projected["atoms"].items():
        status = state["status"]
        cur = con.execute("UPDATE knowledge_atom SET status=? WHERE id=? AND status IS NOT ?",
                          (status, aid, status))
        changed = changed or bool(cur.rowcount)
    for cid, status in projected["conflicts"].items():
        cur = con.execute("UPDATE conflict_case SET status=? WHERE conflict_id=? AND status IS NOT ?",
                          (status, cid, status))
        changed = changed or bool(cur.rowcount)
    if changed:
        db.bump_kb_revision(con)
    return {**projected, "changed": changed}


def annotate_atoms(con, atoms: list[dict], *, public=False) -> list[dict]:
    states = projection(con, public=public)["atoms"]
    for atom in atoms:
        atom.update(states.get(atom["id"], {}))
        atom["target_fingerprint"] = identity.atom_fingerprint(atom)
    return atoms
