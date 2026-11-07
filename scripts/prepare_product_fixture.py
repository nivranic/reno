"""Create synthetic, credential-free product QA data in a NEW isolated root.

Usage: python scripts/prepare_product_fixture.py --root <new-dir> --port 18765
Never reads repository config.local.json, real data or media. Requires the
existing imageio-ffmpeg dependency to generate a small real test-pattern video.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

sys.dont_write_bytecode = True
REPO = Path(__file__).resolve().parents[1]
QUESTIONS = REPO / "tests" / "fixtures" / "product_questions.json"


def prepare(root: Path, port: int = 18765, *, include_reconciliation: bool = False,
            include_derivation: bool = False) -> Path:
    root = root.expanduser().resolve()
    if root.exists():
        raise ValueError("QA root must be new; refusing to overwrite an existing directory")
    if root.is_relative_to(REPO) or REPO.is_relative_to(root) or port == 8765 or not 1024 <= port <= 65535:
        raise ValueError("Use an isolated QA root and a non-production port (default 18765)")
    root.mkdir(parents=True)
    for name in ("data", "media/originals", "media/audio", "media/frames", "frames_cache"):
        (root / name).mkdir(parents=True, exist_ok=True)
    instance = uuid.uuid4().hex
    # This is a disposable QA-only token, never copied from any real config.
    token = "reno-qa-" + instance
    local = root / "config.test.json"
    local.write_text(json.dumps({
        "zhipu_api_key": "", "zhipu_base_url": "http://127.0.0.1:1/qa-no-network",
        "auth_token": token, "sync_token": "", "node_role": "all",
        "atomize_model": "qa-fixture", "judge_models": ["qa-fixture"],
        "ask_model": "qa-fixture", "summary_model": "qa-fixture",
        "vlm_enabled": False, "web_ui": "react", "ask_rate_per_min": 0,
        "personal_features_enabled": True, "field_checks_enabled": True,
        "ytdlp_cookies": "", "safety_interval_s": 0,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    for key in list(os.environ):
        if key.startswith("RENO_"):
            os.environ.pop(key)
    os.environ.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(local),
                      RENO_DATA_DIR=str(root / "data"), RENO_LLM_TESTS="0")
    sys.path.insert(0, str(REPO))
    from reno import config, db, storage_admin, storage_lifecycle
    config.get()  # validates every mutable path before creating the database
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    fixtures = json.loads(QUESTIONS.read_text(encoding="utf-8"))
    (root / "product_questions.json").write_text(
        json.dumps(fixtures, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    import imageio_ffmpeg
    video_file = root / "media" / "originals" / "qa-test-pattern.mp4"
    subprocess.run([
        imageio_ffmpeg.get_ffmpeg_exe(), "-nostdin", "-loglevel", "error",
        "-f", "lavfi", "-i", "testsrc=duration=20:size=320x240:rate=10",
        "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(video_file),
    ], check=True, capture_output=True)
    with storage_lifecycle.shared(storage_admin.profile()):
        con = db.connect()
        reconciliation_fixture = None
        derivation_fixture = None
        try:
            for index in range(1, 7):
                vid = f"BVQA{index:08d}"
                db.upsert_asset(con, {
                    "video_id": vid, "source_platform": "bilibili", "source_type": "qa_synthetic",
                    "source_url": None, "title": f"人工验收样本 {index} · 装修知识",
                    "author": "QA 合成样本", "duration_ms": 20000,
                    "width": 320, "height": 240, "fps": 10,
                    "content_sha256": hashlib.sha256(f"product-v1:{vid}".encode()).hexdigest(),
                    "files": {"video": "media/originals/qa-test-pattern.mp4", "audio": None},
                    "status": "processed", "imported_at": "2026-01-01T00:00:00+08:00",
                })
            for index, atom in enumerate(fixtures["atoms"]):
                vid = f"BVQA{(1 if index < 6 else 2 + (index - 6) % 5):08d}"
                ms = 1000 + (index % 6) * 2000
                sid = f"asr_{index:04d}"
                con.execute(
                    "INSERT INTO transcript_segment(id,video_id,idx,start_ms,end_ms,text) VALUES(?,?,?,?,?,?)",
                    (sid, vid, index, ms, ms + 1500, atom["claim"]))
                from reno.dict import stage_for
                con.execute(
                    "INSERT INTO knowledge_atom(id,video_id,category,stage,space,subject,claim,"
                    "polarity,conditions_json,parameters_json,confidence,status,cluster_id,model,"
                    "prompt_version,created_at,dimension,evidence_nature,exceptions,prices_json) "
                    "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (atom["id"], vid, atom["category"], stage_for(atom["category"]), atom["space"],
                     atom["subject"], atom["claim"], "recommend", db.j(atom["conditions"]), "[]",
                     0.8, "candidate", "clu_qa_height" if atom["id"].startswith("qa_waterproof_height_") else None,
                     "qa-fixture", "qa-v1", "2026-01-01T00:00:00+08:00", atom["dimension"],
                     atom["evidence_nature"], db.j(atom["exceptions"]), db.j(atom.get("prices", []))))
                con.execute(
                    "INSERT INTO evidence_ref(atom_id,video_id,modality,source_item_id,start_ms,end_ms,evidence_text,weight) "
                    "VALUES(?,?,?,?,?,?,?,?)", (atom["id"], vid, "asr", sid, ms, ms + 1500, atom["claim"], 1.0))
            # Keep an OCR subset for the existing workbench modality filter test.
            con.execute("INSERT INTO ocr_span(id,video_id,start_ms,end_ms,text,conf,frame_ids_json) "
                        "VALUES(?,?,?,?,?,?,?)", ("qa_ocr", "BVQA00000001", 2000, 2500, "人工验收样本", 1.0, "[]"))
            con.execute("INSERT INTO knowledge_cluster(cluster_id,canonical_topic,relation,members_json,linked_conflict,created_at) "
                        "VALUES(?,?,?,?,?,?)", ("clu_qa_height", "防水高度建议分歧", "conflicting",
                        db.j(["qa_waterproof_height_a", "qa_waterproof_height_b"]), "cfl_qa_height", db.now()))
            con.execute("INSERT INTO conflict_case(conflict_id,ctype,status,side_a_json,side_b_json,analysis_json,recommended_action,created_at) "
                        "VALUES(?,?,?,?,?,?,?,?)", ("cfl_qa_height", "numeric_conflict", "needs_review",
                        db.j({"atoms": ["qa_waterproof_height_a"], "claim": fixtures["atoms"][1]["claim"], "video": "BVQA00000001", "conditions": {}}),
                        db.j({"atoms": ["qa_waterproof_height_b"], "claim": fixtures["atoms"][2]["claim"], "video": "BVQA00000001", "conditions": {}}),
                        db.j({"judge_note": "人工测试分歧，未核实强制标准"}), "核实设计和材料依据", db.now()))
            from reno import derivation
            binding = db.j(derivation.bind_inputs({a["id"]: a for a in db.all_atoms(con)},
                                                  ["qa_waterproof_height_a", "qa_waterproof_height_b"]))
            con.execute("UPDATE knowledge_cluster SET derivation_json=? WHERE cluster_id='clu_qa_height'", (binding,))
            con.execute("UPDATE conflict_case SET derivation_json=? WHERE conflict_id='cfl_qa_height'", (binding,))
            db.fts_sync_atoms(con, [atom["id"] for atom in fixtures["atoms"]])
            con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES(?,?)", ("qa_instance_id", instance))
            if include_reconciliation:
                from scripts.reconciliation_fixture import add_reconciliation_fixture
                reconciliation_fixture = add_reconciliation_fixture(con, instance)
            if include_derivation:
                from scripts.derivation_fixture import add_derivation_fixture
                derivation_fixture = add_derivation_fixture(con)
            atom_count = con.execute("SELECT COUNT(*) FROM knowledge_atom").fetchone()[0]
            con.commit()
        finally:
            con.close()
    manifest = {
        "qa_instance_id": instance, "fixture_version": fixtures["fixture_version"],
        "root": str(root), "config_file": str(local), "data_dir": str(root / "data"),
        "media_dir": str(root / "media"), "frame_cache": str(root / "frames_cache"),
        "questions_file": str(root / "product_questions.json"),
        "questions_sha256": hashlib.sha256((root / "product_questions.json").read_bytes()).hexdigest(),
        "port": port, "base_url": f"http://127.0.0.1:{port}",
        "auth_token": token, "video_ids": [f"BVQA{i:08d}" for i in range(1, 7)],
        "atom_count": len(fixtures["atoms"]), "total_atom_count": atom_count,
        "question_count": len(fixtures["questions"]),
    }
    if reconciliation_fixture is not None:
        manifest["reconciliation_fixture"] = reconciliation_fixture
    if derivation_fixture is not None:
        manifest["derivation_fixture"] = derivation_fixture
        from reno import report
        report.run_all()
    manifest_file = root / "qa-manifest.json"
    manifest_file.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return manifest_file


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--port", type=int, default=18765)
    parser.add_argument("--reconciliation", action="store_true", help="Add namespaced synthetic review reconciliation records")
    parser.add_argument("--derivation", action="store_true", help="Add synthetic P04 relation states and static reports")
    args = parser.parse_args()
    manifest = prepare(args.root, args.port, include_reconciliation=args.reconciliation, include_derivation=args.derivation)
    print(json.dumps({"manifest": str(manifest), "port": args.port}, ensure_ascii=False))


if __name__ == "__main__":
    main()
