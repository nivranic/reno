"""Maintenance enrichment changes must invalidate old content-bound reviews."""
import json
import sys

import pytest

from reno import ask, config, db, review
from scripts import backfill_atoms_v2 as backfill


@pytest.fixture()
def reviewed(tmp_path, monkeypatch):
    for name, path in {"DATA": tmp_path / "data", "MEDIA": tmp_path / "media",
                       "ORIG": tmp_path / "media/originals", "AUDIO": tmp_path / "media/audio",
                       "FRAMES": tmp_path / "media/frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    db.initialize()
    from reno import storage_admin
    storage_admin.setup()
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,title,content_sha256,files_json) VALUES('BVbackfill','人工回填样本','backfill-sha','{}')")
    for aid, action in (("backfill-confirmed", "confirm"), ("backfill-rejected", "reject")):
        con.execute("INSERT INTO knowledge_atom(id,video_id,category,claim,conditions_json,parameters_json,exceptions,polarity) VALUES(?, 'BVbackfill','防水','防水施工前核对基层',?,'[]','[]','recommend')",
                    (aid, json.dumps({"authority_level": "cited_standard", "基层": "水泥"})))
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) VALUES(?,'BVbackfill','asr',?,0,1000,'人工证据')", (aid, aid))
        review.record_decision(con, atom_id=aid, action=action)
    db.fts_sync_atoms(con, ["backfill-confirmed", "backfill-rejected"])
    con.commit()
    yield con
    con.close()


def assert_rechecks_visible(con):
    atoms = review.annotate_atoms(con, db.all_atoms(con))
    assert len(atoms) == 2
    assert {atom["review_status"] for atom in atoms} == {"needs_recheck"}
    assert {atom["status"] for atom in atoms} == {"candidate"}
    assert {atom["id"] for atom in ask.retrieve(con, "防水")} == {"backfill-confirmed", "backfill-rejected"}
    assert con.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 2


def test_deterministic_backfill_replays_confirm_and_reject_in_same_commit(reviewed):
    before = db.knowledge_version(reviewed)["kb_revision"]
    result = backfill.deterministic_pass(reviewed)
    assert result["authority_migrated"] == 2
    assert_rechecks_visible(reviewed)
    assert db.knowledge_version(reviewed)["kb_revision"] > before
    # A new connection sees both enriched content and its corrected projection.
    reader = db.connect()
    assert_rechecks_visible(reader)
    reader.close()
    unchanged = db.knowledge_version(reviewed)["kb_revision"]
    backfill.deterministic_pass(reviewed)
    assert db.knowledge_version(reviewed)["kb_revision"] == unchanged


def test_llm_enrichment_batch_replays_before_checkpoint_commit(reviewed, monkeypatch):
    backfill.deterministic_pass(reviewed)
    # Bind fresh decisions to this intermediate version before enriching again.
    review.record_decision(reviewed, atom_id="backfill-confirmed", action="confirm")
    review.record_decision(reviewed, atom_id="backfill-rejected", action="reject")
    reviewed.commit()
    monkeypatch.setattr(sys, "argv", ["backfill_atoms_v2.py", "--restart", "--apply"])
    monkeypatch.setattr(backfill, "llm_batch", lambda rows: {
        row["id"]: {"dimension": "施工工艺", "evidence_nature": "cited_standard",
                    "exceptions": ["木基层另行核对"]} for row in rows})
    backfill.main()
    atoms = review.annotate_atoms(reviewed, db.all_atoms(reviewed))
    assert {atom["review_status"] for atom in atoms} == {"needs_recheck"}
    assert {atom["status"] for atom in atoms} == {"candidate"}
    assert len(ask.retrieve(reviewed, "防水")) == 2
    assert reviewed.execute("SELECT value FROM meta WHERE key=?", (backfill.CURSOR_KEY,)).fetchone()[0] == "backfill-rejected"
    assert reviewed.execute("SELECT COUNT(*) FROM user_decision").fetchone()[0] == 4
