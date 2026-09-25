# -*- coding: utf-8 -*-
"""Ingest: share URL (bilibili via yt-dlp, DASH split streams) or local file.

DASH lesson from feasibility: video stream has no audio; keep separate files.
SHA-256 content addressing makes re-ingest idempotent.
"""
import hashlib
import json
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

from . import config, db

# single-process guard: concurrent imports of the same URL/content must not
# both pass the sha dedup check (check-then-insert is a TOCTOU otherwise)
_register_lock = threading.Lock()


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
    with _register_lock:  # serialize dedup-check + insert (see lock comment)
        return _register_locked(vid, info, v, a, source_type, url)


def _register_locked(vid, info, v: Path, a: Path, source_type, url) -> dict:
    con = db.connect()
    try:
        sha = _sha256(v)
        dup = db.asset_by_sha(con, sha)
        if dup:
            db.upsert_asset(con, dict(dup))
            con.commit()
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
            "files": {"video": str(v.relative_to(config.ROOT)),
                      "audio": str(a.relative_to(config.ROOT)) if a and a.exists() else None},
            "status": "imported"}
        try:
            db.upsert_asset(con, asset)
            db.enqueue_job(con, vid, "process")
            con.commit()
        except sqlite3.IntegrityError:
            # concurrent import of the same content won the sha race
            # (content_sha256 is UNIQUE) - report it as a duplicate
            con.rollback()
            dup = db.asset_by_sha(con, sha)
            if dup:
                db.upsert_asset(con, dict(dup))
                con.commit()
                return {"video_id": dup["video_id"], "status": "duplicate",
                        "duplicate_of": dup["video_id"]}
            raise
        return {"video_id": vid, "status": "imported"}
    finally:
        con.close()
