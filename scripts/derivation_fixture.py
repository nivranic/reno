"""Explicit synthetic provenance states for P04, never used by production."""
from reno import db, derivation


def add_derivation_fixture(con):
    descriptors = []
    for state, label in (("active", "依据未变化"), ("stale", "依据已变化"),
                         ("missing", "依据成员缺失"), ("unknown", "判定依据未知")):
        members = []
        for side, name, video in (("a", "甲", "BVQA00000003"), ("b", "乙", "BVQA00000004")):
            atom_id = f"p04_{state}_{side}"
            members.append(atom_id)
            claim = f"P04{name} 防水方案 {label}：施工前检查基层"
            con.execute("INSERT INTO knowledge_atom(id,video_id,category,space,subject,claim,polarity,"
                        "conditions_json,parameters_json,status,dimension) "
                        "VALUES(?,?,'防水','卫生间',?,?,'recommend','{}','[]','candidate','施工工艺')",
                        (atom_id, video, f"P04{name}", claim))
            con.execute("INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text) "
                        "VALUES(?,?,'asr',?,1000,2000,?)", (atom_id, video, atom_id, claim))
        atoms = {a["id"]: a for a in db.all_atoms(con)}
        binding = db.j(derivation.bind_inputs(atoms, members)) if state != "unknown" else None
        cid, fid = f"clu_p04_{state}", f"cfl_p04_{state}"
        con.execute("INSERT INTO knowledge_cluster(cluster_id,canonical_topic,relation,members_json,linked_conflict,derivation_json) "
                    "VALUES(?,?,'conflicting',?,?,?)", (cid, f"P04 {label}", db.j(members), fid, binding))
        con.execute("UPDATE knowledge_atom SET cluster_id=? WHERE id IN (?,?)", (cid, *members))
        con.execute("INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json,analysis_json,"
                    "recommended_action,derivation_json) VALUES(?,'method_conflict','needs_review',?,?,?,?,?)",
                    (fid, db.j({"atoms": [members[0]], "claim": f"P04甲 {label}", "video": "BVQA00000003"}),
                     db.j({"atoms": [members[1]], "claim": f"P04乙 {label}", "video": "BVQA00000004"}),
                     db.j({"judge_note": "人工历史判定，仅供验证关联有效性"}), "人工核对施工条件", binding))
        if state == "stale":
            con.execute("UPDATE knowledge_atom SET category='水电' WHERE id=?", (members[0],))
        if state == "missing":
            con.execute("DELETE FROM evidence_ref WHERE atom_id=?", (members[1],))
            con.execute("DELETE FROM knowledge_atom WHERE id=?", (members[1],))
        descriptors.append({"state": state, "cluster_id": cid, "conflict_id": fid, "atom_id": members[0]})
    db.fts_sync_atoms(con, [a[0] for a in con.execute("SELECT id FROM knowledge_atom")])
    return {"synthetic": True, "version": 1, "relations": descriptors}
