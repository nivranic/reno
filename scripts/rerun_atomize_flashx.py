# -*- coding: utf-8 -*-
"""One-shot quality re-run: re-atomize every video with the configured model
(currently GLM-5.3-FlashX), then rebuild clusters/conflicts via judge.

For each video, wipes the previous atomize outcome so the pipeline re-runs
ONLY that step (media/asr/ocr markers stay intact — no re-download, no
re-transcription). Old atoms/evidence are removed per-video (replace_atoms
alone would leave high-index stragglers when the new run yields fewer atoms).
Old clusters/conflicts are cleared upfront so the UI never shows dead refs.
user_decision rows are kept untouched (append-only audit trail)."""
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from reno import config, db, pipeline  # noqa: E402

MODEL = config.get("atomize_model")
assert MODEL and "5.3" in MODEL, f"unexpected atomize_model={MODEL}"


def main():
    con = db.connect()
    vids = [r["video_id"] for r in con.execute(
        "SELECT video_id FROM video_asset ORDER BY video_id")]
    print(f"[prep] {len(vids)} videos, model={MODEL}, vlm_enabled={config.get('vlm_enabled')}")

    # one-time wipe: atomize markers + ok-run history + old atoms/evidence,
    # and stale clusters/conflicts (judge rebuilds them at the end)
    for vid in vids:
        con.execute("DELETE FROM meta WHERE key=?", (f"step:{vid}:atomize",))
        con.execute("DELETE FROM processing_run WHERE video_id=? AND step='atomize' AND ok=1", (vid,))
        con.execute("DELETE FROM evidence_ref WHERE video_id=?", (vid,))
        con.execute("DELETE FROM knowledge_atom WHERE video_id=?", (vid,))
    con.execute("DELETE FROM knowledge_cluster")
    con.execute("DELETE FROM conflict_case")
    con.commit()
    print("[prep] wiped old atoms/evidence/markers/clusters/conflicts")

    all_stats, failed = {}, []
    t0 = time.time()
    for i, vid in enumerate(vids, 1):
        t1 = time.time()
        try:
            res = pipeline.run_video(vid)
        except Exception as e:  # noqa: BLE001
            res = {"fatal": str(e)}
        all_stats[vid] = res
        az = res.get("atomize", "?")
        ok = isinstance(az, dict) and az.get("model")
        if not ok:
            failed.append(vid)
        print(f"[{i:>2}/{len(vids)}] {vid} {az if not ok else 'atoms=%s dropped=%s model=%s %ss' % (az['n_atoms'], az.get('n_dropped'), az['model'], az.get('process_s'))} total={time.time()-t0:.0f}s", flush=True)

    print("[judge] rebuilding clusters/conflicts ...", flush=True)
    jstats = pipeline.judge_all()
    print("[judge]", jstats, flush=True)

    total_atoms = sum(
        s.get("n_atoms", 0) for s in all_stats.values() if isinstance(s, dict))
    summary = {
        "model": MODEL, "videos": len(vids), "failed": failed,
        "total_atoms": total_atoms, "judge": jstats,
        "wall_s": round(time.time() - t0, 1),
    }
    Path("data/rerun-flashx-summary.json").write_text(
        json.dumps({"summary": summary, "per_video": all_stats},
                   ensure_ascii=False, indent=1), encoding="utf-8")
    print("[done]", json.dumps(summary, ensure_ascii=False))
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
