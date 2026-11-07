"""Optional namespaced review examples inside the existing isolated QA fixture.

Events are deliberately seeded examples, not proof of a sync/model outcome.
Do not call review.replay: baseline question-fixture projections stay untouched.
"""
import hashlib
import uuid


def add_reconciliation_fixture(con, instance):
    from reno import config, db, evidence_identity as identity, reconciliation

    if not config.is_isolated_qa():
        raise RuntimeError("Reconciliation examples require the complete isolated QA environment")
    marker = con.execute("SELECT value FROM meta WHERE key='qa_instance_id'").fetchone()
    if marker is None or marker[0] != instance:
        raise RuntimeError("Synthetic reconciliation fixture identity does not match this database")
    if con.execute("SELECT 1 FROM knowledge_atom WHERE id LIKE 'qa_reconcile_%' LIMIT 1").fetchone():
        raise RuntimeError("Synthetic reconciliation namespace already exists")
    namespace = uuid.UUID(hex=instance)
    video = "BVQARECON0001"
    db.upsert_asset(con, {"video_id": video, "source_platform": "synthetic", "source_type": "qa_synthetic",
        "title": "复核对账人工样本", "author": "QA合成样本", "duration_ms": 20000, "width": 320, "height": 240,
        "fps": 10, "content_sha256": hashlib.sha256(b"synthetic-reconciliation-fixture-v1").hexdigest(),
        "files": {"video": "media/originals/qa-test-pattern.mp4", "audio": None}, "status": "processed"})
    record_ids, retained_atoms = {}, []

    def atom(label, claim, source=video):
        atom_id = "qa_reconcile_" + label
        segment = "qa_reconcile_segment_" + label
        con.execute("INSERT INTO knowledge_atom(id,video_id,claim,conditions_json,parameters_json,model,status) "
                    "VALUES(?,?,?,'{}','[]','qa-fixture','candidate')", (atom_id, source, claim))
        con.execute("INSERT INTO transcript_segment(id,video_id,start_ms,end_ms,text) VALUES(?,?,0,1000,?)",
                    (segment, source, claim))
        con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
                    "VALUES(?,?,'asr',?,0,1000,?)", (atom_id, source, segment, claim))
        retained_atoms.append(atom_id)
        return atom_id

    def event(label, atom_id, when, action="confirm", note="人工对账样本，不代表真实施工复核"):
        snapshot, fingerprint = identity.load_target(con, "atom", atom_id)
        operation = str(uuid.uuid5(namespace, "reconciliation:" + label))
        con.execute("INSERT INTO user_decision(atom_id,action,note,created_at,event_id,client_id,decided_at,"
                    "target_fingerprint,target_snapshot_json,event_schema_version) VALUES(?,?,?,?,?,'synthetic-reconciliation',?,?,?,1)",
                    (atom_id, action, note, "2024-01-01T00:00:00.000Z", operation, when, fingerprint, identity.canonical_json(snapshot)))
        record_ids[label] = operation

    def remove_atom(atom_id):
        con.execute("DELETE FROM evidence_ref WHERE atom_id=?", (atom_id,))
        con.execute("DELETE FROM knowledge_atom WHERE id=?", (atom_id,))
        retained_atoms.remove(atom_id)

    for number in range(1, 33):
        label = f"stale_{number:02d}"
        atom_id = atom(label, f"对账样本旧内容 {number:02d}")
        event(label, atom_id, f"2024-01-01T00:00:{number:02d}.000Z")
        con.execute("UPDATE knowledge_atom SET claim=? WHERE id=?", (f"对账样本新内容 {number:02d}", atom_id))
    missing = atom("missing", "对账目标已缺失，历史来源仍可读")
    event("missing", missing, "2024-02-01T00:00:00.000Z")
    remove_atom(missing)
    unavailable = atom("unavailable", "对账来源已不可用", source="BVQARECONGONE")
    event("unavailable", unavailable, "2024-02-04T00:00:00.000Z")
    remove_atom(unavailable)
    legacy = atom("legacy", "对账旧记录未绑定")
    inserted = con.execute("INSERT INTO user_decision(atom_id,action,note,created_at,decided_at,event_schema_version) "
                           "VALUES(?,'confirm','人工旧记录样本','2024-02-03T00:00:00Z','2024-02-03T00:00:00Z',0)", (legacy,))
    record_ids["legacy"] = "decision:" + str(inserted.lastrowid)
    invalid = atom("invalid", "对账记录时间无效")
    event("invalid", invalid, "2999-01-01T00:00:00.000Z")
    history = atom("history", "对账旧快照已由新复核覆盖")
    event("history", history, "2024-01-01T00:00:00.000Z")
    con.execute("UPDATE knowledge_atom SET claim='对账已独立核对的新内容' WHERE id=?", (history,))
    event("history_active", history, "2024-03-01T00:00:00.000Z", action="clear_review")
    active = atom("active", "对账当前绑定有效")
    event("active", active, "2024-03-02T00:00:00.000Z",
          note="人工备注 https://example.test/document?token=RECONCILIATION_QUERY_SENTINEL#PRIVATE_FRAGMENT")
    db.fts_sync_atoms(con, retained_atoms)
    data = reconciliation.query(con, scope="all", limit=100)
    if (data["summary"]["total"] != 39 or data["summary"]["needs_attention"] != 36
            or data["summary"]["historical"] != 1):
        raise RuntimeError("Synthetic reconciliation examples do not match their declared contract")
    absent = next(item for item in data["items"] if item["record_id"] == record_ids["unavailable"])
    if absent["source_links"] or absent["target_url"] is not None:
        raise RuntimeError("Unavailable synthetic source must expose no navigation links")
    return {"version": "reconciliation-v1", "synthetic": True, "summary": data["summary"],
            "record_ids": record_ids, "video_id": video, "unavailable_record_id": record_ids["unavailable"],
            "atom_namespace": "qa_reconcile_", "added_atom_count": len(retained_atoms),
            "evidence_scope": "人工复核状态样本，只用于独立对账页面与接口验证，不代表真实复核结论"}
