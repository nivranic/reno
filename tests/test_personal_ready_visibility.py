"""Current cloud knowledge visibility does not rewrite personal snapshots."""
import json
import uuid

import pytest

from reno import config, db, evidence_identity as identity, personal, sync_visibility
from tests.test_personal_projects import personal_env, add_atom, create  # noqa: F401


def _target(con, kind):
    if kind == "atom":
        return "personal-atom"
    con.execute(
        "INSERT INTO video_asset(video_id,title,content_sha256,files_json,sync_state) "
        "VALUES('ready-peer','另一视频','ready-peer-sha','{}','ready')")
    con.execute(
        "INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json) "
        "VALUES('peer-atom','ready-peer','另一观点','{}','[]')")
    con.execute(
        "INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json) "
        "VALUES('personal-conflict','contrast','needs_review',?,?)",
        (json.dumps({"atoms": ["personal-atom"], "video": "BVpersonal", "claim": "甲"}),
         json.dumps({"atoms": ["peer-atom"], "video": "ready-peer", "claim": "乙"})))
    return "personal-conflict"


def _save_ref(con, env, card, kind, target_id):
    # Raw row installation represents a future already-installed mirror.
    # It does not bypass or enable the current cloud personal API.
    saved, fingerprint = identity.load_target(con, kind, target_id)
    raw = identity.canonical_json(saved)
    ref_id = str(uuid.uuid4())
    con.execute(
        "INSERT INTO personal_evidence_ref("
        "ref_id,project_id,owner_kind,owner_id,target_kind,target_id,"
        "fingerprint_version,target_fingerprint,snapshot_json,created_at) "
        "VALUES(?,?,'card',?,?,?,?,?,?,?)",
        (ref_id, env["project"]["project_id"], card["card_id"], kind, target_id,
         identity.FINGERPRINT_VERSION, fingerprint, raw, "2026-10-03T00:00:00.000Z"))
    con.commit()
    return ref_id, saved, fingerprint, raw


@pytest.mark.parametrize("kind", ("atom", "conflict"))
@pytest.mark.parametrize("hidden_state", ("staging", "error"))
def test_cloud_current_ref_requires_ready_and_preserves_saved_bytes(personal_env, kind, hidden_state):
    add_atom()
    card = create(personal_env)
    project_id = personal_env["project"]["project_id"]
    con = db.connect()
    try:
        target_id = _target(con, kind)
        ref_id, saved, fingerprint, raw = _save_ref(con, personal_env, card, kind, target_id)
        con.execute("UPDATE video_asset SET sync_state=? WHERE video_id='BVpersonal'", (hidden_state,))
        con.commit()
        config.set_local({"node_role": "cloud"})
        assert config.is_cloud()
        assert not personal.capabilities(con)["personal_read"]
        assert not sync_visibility.target_visible(con, kind, target_id)
        assert personal.resolve_target(con, project_id, kind, target_id) is None
        hidden = personal.list_refs(con, "card", card["card_id"])[0]
        assert hidden["snapshot"] == saved
        assert hidden["current_snapshot"] is None
        assert hidden["current_fingerprint"] is None
        assert hidden["binding_status"] == "missing"

        con.execute("UPDATE video_asset SET sync_state='ready' WHERE video_id='BVpersonal'")
        con.commit()
        assert sync_visibility.target_visible(con, kind, target_id)
        current = personal.resolve_target(con, project_id, kind, target_id)
        assert current is not None and current[1] == fingerprint
        visible = personal.list_refs(con, "card", card["card_id"])[0]
        assert visible["snapshot"] == saved
        assert visible["current_snapshot"] is not None
        assert visible["current_fingerprint"] == fingerprint
        assert visible["binding_status"] == "active"
        assert not personal.capabilities(con)["personal_read"]
        assert con.execute("SELECT snapshot_json FROM personal_evidence_ref WHERE ref_id=?", (ref_id,)).fetchone()[0] == raw
    finally:
        con.close()


def test_cloud_existing_conflict_with_missing_member_keeps_saved_snapshot(personal_env):
    add_atom()
    card = create(personal_env)
    con = db.connect()
    try:
        target_id = _target(con, "conflict")
        con.execute("UPDATE video_asset SET sync_state='ready' WHERE video_id='BVpersonal'")
        ref_id, saved, _, raw = _save_ref(con, personal_env, card, "conflict", target_id)
        config.set_local({"node_role": "cloud"})
        assert sync_visibility.target_visible(con, "conflict", target_id)
        con.execute("UPDATE conflict_case SET side_b_json=? WHERE conflict_id=?",
            (json.dumps({"atoms": ["missing-peer"], "video": "ready-peer", "claim": "乙"}), target_id))
        con.commit()
        assert not sync_visibility.target_visible(con, "conflict", target_id)
        assert personal.resolve_target(con, personal_env["project"]["project_id"], "conflict", target_id) is None
        ref = personal.list_refs(con, "card", card["card_id"])[0]
        assert ref["snapshot"] == saved and ref["current_snapshot"] is None
        assert ref["binding_status"] == "missing"
        assert con.execute("SELECT snapshot_json FROM personal_evidence_ref WHERE ref_id=?", (ref_id,)).fetchone()[0] == raw
    finally:
        con.close()


@pytest.mark.parametrize("role", ("all", "home"))
def test_local_current_ref_remains_readable_for_staging_knowledge(personal_env, role):
    add_atom()
    con = db.connect()
    try:
        con.execute("UPDATE video_asset SET sync_state='staging' WHERE video_id='BVpersonal'")
        con.commit()
        config.set_local({"node_role": role})
        assert sync_visibility.target_visible(con, "atom", "personal-atom")
        assert personal.resolve_target(con, personal_env["project"]["project_id"], "atom", "personal-atom") is not None
    finally:
        con.close()
