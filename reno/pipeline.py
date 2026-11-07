# -*- coding: utf-8 -*-
"""Pipeline orchestration: per-video step sequence with resume-aware
completion tracking (meta table). A failed step never marks the video done;
rerun resumes at the first incomplete step."""
from . import asr, atomize, config, db, judge, media, ocr, vlm
from . import storage_admin, storage_lifecycle
from .budget_contract import BudgetDeferred
from .sync_source_opportunity17 import ReadmissionRequired17

STEPS = ["media", "asr", "ocr", "vlm", "atomize"]


def _active_steps():
    steps = list(STEPS)
    if not config.get("vlm_enabled", True):
        steps.remove("vlm")
    return steps


def _step_done(con, video_id, step) -> bool:
    row = con.execute(
        "SELECT ok FROM processing_run WHERE video_id=? AND step=? AND ok=1 "
        "ORDER BY run_id DESC LIMIT 1", (video_id, step)).fetchone()
    return row is not None


def _mark_step(con, video_id, step):
    con.execute(
        "INSERT INTO meta(key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (f"step:{video_id}:{step}", db.now()))


def _meta_done(con, video_id, step) -> bool:
    r = con.execute("SELECT value FROM meta WHERE key=?",
                    (f"step:{video_id}:{step}",)).fetchone()
    return r is not None


def run_video(video_id: str, force: bool = False, *, check_cancelled=None) -> dict:
    from .worker import _execution_profile, check_admission
    profile = _execution_profile() or storage_admin.profile()
    con = db.connect(profile.database)
    results = {}
    try:
        asset = db.get_asset(con, video_id)
        if asset is None:
            raise KeyError(f"unknown video {video_id}")
        for step in _active_steps():
            # Runtime import avoids a worker↔pipeline import cycle. This check
            # belongs before work only: a pause arriving with a paid response
            # must not roll back that response or its completion marker.
            check_admission()
            if check_cancelled is not None:
                check_cancelled()
            if not force and (_meta_done(con, video_id, step) or _step_done(con, video_id, step)):
                results[step] = "skipped(done)"
                continue
            mod = {"media": media, "asr": asr, "ocr": ocr,
                   "vlm": vlm, "atomize": atomize}[step]
            try:
                if storage_admin.profile().identity != profile.identity:
                    raise BudgetDeferred("ledger_unavailable")
                if step == "media":
                    # Reference producer protection includes file extraction,
                    # frame replacement and media.run's existing commit. Other
                    # model steps do not publish file roots; do not hold this
                    # guard across the whole pipeline or model HTTP retries.
                    with storage_lifecycle.shared(profile):
                        results[step] = media.run(con, video_id)
                else:
                    results[step] = mod.run(con, video_id)
                if check_cancelled is not None:
                    check_cancelled()
                _mark_step(con, video_id, step)
                con.execute("UPDATE video_asset SET status=? WHERE video_id=?",
                            (f"done_{step}", video_id))
                con.commit()
            except (BudgetDeferred, ReadmissionRequired17):
                con.rollback()
                raise
            except Exception as e:  # noqa: BLE001
                con.rollback()
                db.log_run(con, video_id, step, ok=False, detail=str(e)[:500])
                con.execute("UPDATE video_asset SET status=? WHERE video_id=?",
                            ("error", video_id))
                con.commit()
                results[step] = f"ERROR: {e}"
                return results
        con.execute("UPDATE video_asset SET status='processed' WHERE video_id=?",
                    (video_id,))
        con.commit()
        return results
    finally:
        con.close()


def judge_all(force: bool = False) -> dict:
    con = db.connect()
    try:
        stats = judge.run(con)
        con.execute("INSERT INTO meta(key,value) VALUES('last_judge',?) "
                    "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                    (db.now(),))
        con.commit()
        return stats
    finally:
        con.close()
