# -*- coding: utf-8 -*-
"""Ingest: share URL (bilibili via yt-dlp, DASH split streams) or local file.

DASH lesson from feasibility: video stream has no audio; keep separate files.
SHA-256 content addressing makes re-ingest idempotent.
"""
import hashlib
import json
import os
import sqlite3
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

from . import config, db

# cross-process guard: concurrent imports of the same URL/content must not
# race on the download target or the sha dedup check-then-insert. The vid
# file lock serializes the download; DB-level claim arbitration (INSERT DO
# NOTHING + content_sha256 UNIQUE) makes the register correct across
# processes, so an in-process lock alone is not sufficient.
if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _store_path(p: Path) -> str:
    """files_json stores repo-relative paths, but a relocated data dir
    (RENO_DATA_DIR) lives outside ROOT - fall back to an absolute path
    (consumers do config.ROOT / stored, which absolutes pass through)."""
    try:
        return str(p.relative_to(config.ROOT))
    except ValueError:
        return str(p)


@contextmanager
def _vid_lock(vid: str):
    lock_dir = config.ORIG
    lock_dir.mkdir(parents=True, exist_ok=True)
    lf = lock_dir / f".lock-{vid}"
    with open(lf, "w") as f:
        if os.name == "nt":
            msvcrt.locking(f.fileno(), msvcrt.LK_LOCK, 1)
        else:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == "nt":
                f.seek(0)
                msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)


def _sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ytdlp(*args):
    venv_bin = Path(config.ROOT) / ".venv" / "Scripts"
    exe = venv_bin / "yt-dlp.exe"
    if not exe.exists():
        exe = venv_bin / "yt-dlp"
    full = [str(exe), *args]
    # optional passthroughs: cookies file (douyin needs fresh ones; bilibili
    # private favlists need owner cookies) and arbitrary extra args
    cookies = config.get("ytdlp_cookies")
    if cookies and Path(cookies).exists() and "--cookies" not in full:
        full += ["--cookies", str(cookies)]
    extra = config.get("ytdlp_extra_args") or []
    if extra:
        full += list(extra)
    r = subprocess.run(full, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r


def ingest_url(url: str) -> dict:
    """Returns {video_id, status: imported|duplicate}, raises on failure."""
    info = _ytdlp("--skip-download", "--dump-json", "--no-warnings", url)
    if info.returncode != 0:
        raise RuntimeError(f"yt-dlp info failed: {info.stderr[-300:]}")
    d = json.loads(info.stdout.strip().splitlines()[-1])
    vid = d["id"]
    # bili cookies are only for bilibili; other platforms (douyin) must fall
    # through to ytdlp_cookies from config instead of getting foreign cookies
    cookies = config.ROOT.parent / "reno-feas" / "cache" / "bili_cookies.txt"
    cookie_args = (["--cookies", str(cookies)]
                   if cookies.exists() and "bilibili" in url else [])
    vdir = config.ORIG / vid
    vdir.mkdir(parents=True, exist_ok=True)
    v, a = vdir / "video.mp4", vdir / "audio.m4a"
    with _vid_lock(vid):  # cross-process: one download per target
        if not v.exists():
            r = _ytdlp(*cookie_args, "-S", "res:480", "-f", "bv*",
                       "-o", str(v), url)
            if r.returncode != 0:
                raise RuntimeError(f"video download failed: {r.stderr[-300:]}")
        if not a.exists():
            r = _ytdlp(*cookie_args, "-f", "ba/bestaudio", "-o", str(a), url)
            if r.returncode != 0:
                # single-stream platforms (e.g. douyin) have no separate audio;
                # media step falls back to extracting from the video container
                a = None
    return _register(vid, d, v, a, source_type="share_url", url=url)


def ingest_file(path: str) -> dict:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    vid = "file_" + hashlib.sha1(str(p).encode()).hexdigest()[:12]
    vdir = config.ORIG / vid
    vdir.mkdir(parents=True, exist_ok=True)
    v = vdir / "video.mp4"
    with _vid_lock(vid):  # same target as URL downloads - serialize writers
        if not v.exists():
            import shutil
            shutil.copy2(p, v)
    fake = {"id": vid, "title": p.stem, "uploader": None,
            "duration": _duration_of(v), "width": None, "height": None,
            "fps": None, "webpage_url": None}
    return _register(vid, fake, v, None, source_type="local_file", url=None)


def _duration_of(v: Path) -> float:
    from .media import ffmpeg_exe
    r = subprocess.run([ffmpeg_exe(), "-i", str(v)], capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    import re
    m = re.search(r"Duration: (\d+):(\d+):(\d+\.?\d*)", r.stderr)
    if not m:
        return 0.0
    h, mi, s = m.groups()
    return int(h) * 3600 + int(mi) * 60 + float(s)


def _register(vid, info, v: Path, a: Path, source_type, url) -> dict:
    con = db.connect()
    try:
        sha = _sha256(v)
        dup = db.asset_by_sha(con, sha)
        if dup:
            # read-only: the existing row is already complete (an upsert here
            # would null its files_json - robustness suite finding)
            return {"video_id": dup["video_id"], "status": "duplicate",
                    "duplicate_of": dup["video_id"]}
        asset = {
            "video_id": vid, "source_platform": (info.get("extractor") or "").split(":")[0] or None,
            "source_type": source_type, "source_url": url or info.get("webpage_url"),
            "title": info.get("title"), "author": info.get("uploader"),
            "duration_ms": int(round((info.get("duration") or 0) * 1000)),
            "width": info.get("width"), "height": info.get("height"),
            "fps": info.get("fps"), "content_sha256": sha,
            "audio_sha256": _sha256(a) if a and a.exists() else None,
            "imported_at": time.strftime("%Y-%m-%dT%H:%M:%S+08:00"),
            "files": {"video": _store_path(v),
                      "audio": _store_path(a) if a and a.exists() else None},
            "status": "imported"}
        # claim by video_id at the DB level (works across processes): the
        # loser of the claim re-reads by sha and reports a duplicate
        cur = con.execute(
            """INSERT INTO video_asset (video_id, source_platform, source_type,
                   source_url, title, author, duration_ms, width, height, fps,
                   content_sha256, audio_sha256, imported_at, files_json, status)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(video_id) DO NOTHING""",
            (asset["video_id"], asset.get("source_platform"), asset.get("source_type"),
             asset.get("source_url"), asset.get("title"), asset.get("author"),
             asset.get("duration_ms"), asset.get("width"), asset.get("height"),
             asset.get("fps"), asset["content_sha256"], asset.get("audio_sha256"),
             asset["imported_at"], json.dumps(asset["files"]), asset["status"]))
        if cur.rowcount == 0:
            con.rollback()
            row = con.execute("SELECT video_id FROM video_asset WHERE content_sha256=?",
                              (sha,)).fetchone()
            if row:
                return {"video_id": row["video_id"], "status": "duplicate",
                        "duplicate_of": row["video_id"]}
            # same vid, different content (re-download after deletion): update
            db.upsert_asset(con, asset)
            db.enqueue_job(con, vid, "process")
            con.commit()
            return {"video_id": vid, "status": "imported"}
        try:
            db.enqueue_job(con, vid, "process")
            con.commit()
        except sqlite3.IntegrityError:
            # different vid, same content lost the sha UNIQUE race
            con.rollback()
            row = con.execute("SELECT video_id FROM video_asset WHERE content_sha256=?",
                              (sha,)).fetchone()
            if row:
                return {"video_id": row["video_id"], "status": "duplicate",
                        "duplicate_of": row["video_id"]}
            raise
        return {"video_id": vid, "status": "imported"}
    finally:
        con.close()
