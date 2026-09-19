# -*- coding: utf-8 -*-
"""Worker: claim jobs from the SQLite queue and run the pipeline.
Single-process by design; resumable because every step is idempotent
(replace-by-video) and completion-tracked."""
import sys
import time
import traceback

from . import db, pipeline


def process_pending(max_jobs: int = 0, follow: bool = False) -> int:
    """Run pending jobs. follow=True keeps polling (daemon mode).
    Orphan recovery: single-process design means any 'running' job at
    startup belongs to a dead worker - reset it to pending (resume)."""
    con = db.connect()
    cur = con.execute("UPDATE job SET status='pending', updated_at=? "
                      "WHERE status='running'", (db.now(),))
    if cur.rowcount:
        print(f"[worker] recovered {cur.rowcount} orphan job(s) -> pending", flush=True)
    con.commit()
    con.close()
    done = 0
    while True:
        con = db.connect()
        job = db.claim_next_job(con)
        con.close()
        if job is None:
            if not follow or (max_jobs and done >= max_jobs):
                return done
            time.sleep(3)
            continue
        vid = job["video_id"]
        print(f"[worker] job#{job['job_id']} {vid} ({job['kind']})", flush=True)
        try:
            results = pipeline.run_video(vid)
            failed = any(isinstance(v, str) and v.startswith("ERROR")
                         for v in results.values())
            con = db.connect()
            con.execute("UPDATE job SET status=?, error=?, updated_at=? WHERE job_id=?",
                        ("failed" if failed else "done",
                         str(results)[:500] if failed else None,
                         db.now(), job["job_id"]))
            con.commit()
            con.close()
            print(f"[worker] {vid} -> {'FAILED' if failed else 'done'}: "
                  f"{ {k: (v if isinstance(v, str) else v) for k, v in results.items()} }",
                  flush=True)
        except Exception as e:  # noqa: BLE001
            con = db.connect()
            con.execute("UPDATE job SET status='failed', error=?, updated_at=? WHERE job_id=?",
                        (f"{e}\n{traceback.format_exc()[-400:]}", db.now(), job["job_id"]))
            con.commit()
            con.close()
            print(f"[worker] {vid} CRASH: {e}", flush=True)
        done += 1
        if max_jobs and done >= max_jobs:
            return done


if __name__ == "__main__":
    follow = "--follow" in sys.argv
    n = 0
    for a in sys.argv[1:]:
        if a.isdigit():
            n = int(a)
    process_pending(max_jobs=n, follow=follow)
