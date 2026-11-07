# -*- coding: utf-8 -*-
"""Ingest: share URL (bilibili via yt-dlp, DASH split streams) or local file.

DASH lesson from feasibility: video stream has no audio; keep separate files.
SHA-256 content addressing makes re-ingest idempotent.
"""
import errno
import hashlib
import json
import os
import re
import sqlite3
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from . import config, db, storage_admin

# cross-process guard: concurrent imports of the same URL/content must not
# race on the download target or the sha dedup check-then-insert. The vid
# file lock serializes the download; DB-level claim arbitration (INSERT DO
# NOTHING + content_sha256 UNIQUE) makes the register correct across
# processes, so an in-process lock alone is not sufficient.
if os.name == "nt":
    import msvcrt
else:
    import fcntl


def _store_path(p: Path, *, media_base=None) -> str:
    """Canonical files_json path: POSIX separators, relative to the media base
    (MEDIA.parent) so both a repo checkout and a relocated RENO_DATA_DIR
    resolve identically via config.resolve_media(). Legacy rows (backslashes
    or absolute paths) still resolve; scripts/normalize_files_json.py rewrites
    them to this canonical form."""
    try:
        rel = p.relative_to(config.MEDIA.parent if media_base is None else media_base)
    except ValueError:
        rel = p  # truly outside the media tree: keep absolute (passes through)
    return rel.as_posix()


def _wait_windows_lock(stream, api):
    """Wait through ordinary byte-lock contention, without the CRT's 10s cap."""
    competing = {errno.EACCES, errno.EAGAIN, errno.EDEADLK}
    while True:
        stream.seek(0)
        try:
            api.locking(stream.fileno(), api.LK_NBLCK, 1)
            return
        except OSError as exc:
            if exc.errno not in competing and getattr(exc, "winerror", None) != 33:
                raise
            time.sleep(0.1)


@contextmanager
def _vid_lock(vid: str, *, originals=None):
    lock_dir = config.ORIG if originals is None else originals
    lock_dir.mkdir(parents=True, exist_ok=True)
    # vid ultimately comes from platform URLs -> keep it out of path-metachar
    # territory when it names a lock file on disk
    safe_vid = re.sub(r"[^A-Za-z0-9_-]", "_", vid)
    lf = lock_dir / f".lock-{safe_vid}"
    with open(lf, "a+b") as f:
        f.seek(0)
        if os.name == "nt":
            _wait_windows_lock(f, msvcrt)
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


def _ytdlp_cmd() -> list[str]:
    """Locate yt-dlp across install layouts: PATH first, then the bin dir of
    the *running* interpreter (Windows venvs: Scripts/, POSIX venvs: bin/),
    finally `python -m yt_dlp`. The old hardcoded .venv/Scripts path broke
    every non-Windows environment (CI runs 1-6)."""
    import os
    import shutil
    import sys
    found = shutil.which("yt-dlp")
    if found:
        return [found]
    local = Path(sys.executable).parent / ("yt-dlp.exe" if os.name == "nt" else "yt-dlp")
    if local.exists():
        return [str(local)]
    return [sys.executable, "-m", "yt_dlp"]


def _ytdlp(*args):
    from . import source_budget
    source_budget.ensure_legacy_dispatch()
    full = [*_ytdlp_cmd(), *args]
    # optional passthroughs: cookies file (douyin needs fresh ones; bilibili
    # private favlists need owner cookies) and arbitrary extra args
    cookies = config.get("ytdlp_cookies")
    if cookies and Path(cookies).exists() and "--cookies" not in full:
        full += ["--cookies", str(cookies)]
    extra = config.get("ytdlp_extra_args") or []
    if extra:
        full += list(extra)
    source_budget.ensure_legacy_dispatch()
    r = subprocess.run(full, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=600)
    return r


@contextmanager
def _download_input(url, info, directory, *, frozen=False):
    if not frozen:
        yield (url,)
        return
    path = None
    try:
        # Use the exact validated extraction for both video and audio. Re-running
        # URL extraction would allow playlist/redirect changes between calls.
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=directory,
                                         prefix=".sync-source-", suffix=".json", delete=False) as stream:
            path = Path(stream.name)
            # yt-dlp's download_with_info_file retries webpage_url after a
            # DownloadError. Remove that escape hatch from the download copy;
            # keep the original verified metadata untouched for registration.
            frozen_info = {key: value for key, value in info.items()
                           if key not in ("webpage_url", "original_url")}
            json.dump(frozen_info, stream, ensure_ascii=False, allow_nan=False)
        yield ("--no-playlist", "--load-info-json", str(path))
    finally:
        if path is not None:
            path.unlink(missing_ok=True)


def ingest_url(url: str, *, enqueue_process=True, metadata_validator=None, source_proof=None, profile=None) -> dict:
    """Returns {video_id, status: imported|duplicate}, raises on failure."""
    from . import source_budget
    if type(enqueue_process) is not bool:
        raise ValueError("enqueue_process must be a boolean")
    if metadata_validator is not None and not callable(metadata_validator):
        raise ValueError("metadata_validator must be callable")
    # Home's historical default remains strict. A queued local import can
    # suppress a second process job without claiming to be a Home source proof.
    sync_mode = not enqueue_process if source_proof is None else source_proof
    if type(sync_mode) is not bool:
        raise ValueError("source_proof must be a boolean")
    source_scope = source_budget.current_scope()
    if source_scope is not None:
        if profile is None:
            profile = source_scope.profile
        elif profile.identity != source_scope.profile.identity:
            from .budget import BudgetLedgerError
            raise BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    originals = config.ORIG if profile is None else profile.media / "originals"
    single_video = sync_mode or not enqueue_process
    selection_args = ("--no-playlist",) if single_video else ()
    def legacy_metadata():
        info = _ytdlp(*selection_args, "--skip-download", "--dump-json", "--no-warnings", url)
        if info.returncode != 0:
            raise RuntimeError(f"yt-dlp info failed: {info.stderr[-300:]}")
        lines = info.stdout.strip().splitlines()
        if single_video and len(lines) != 1:
            if not sync_mode:
                from .tasks import TaskError
                raise TaskError("导入任务需要单个视频来源", "IMPORT_SOURCE_INVALID", 409)
            from .sync_protocol import SyncError
            raise SyncError("Sync import requires exactly one extracted video", "SYNC_SOURCE_INVALID", 409)
        return json.loads(lines[-1])

    d = source_budget.metadata(url, legacy=legacy_metadata)
    if single_video and (type(d) is not dict or "entries" in d or d.get("_type", "video") != "video"):
        if not sync_mode:
            from .tasks import TaskError
            raise TaskError("导入任务不能下载整个播放列表", "IMPORT_SOURCE_INVALID", 409)
        from .sync_protocol import SyncError
        raise SyncError("Sync import cannot consume a playlist", "SYNC_SOURCE_INVALID", 409)
    from .sync_protocol import validate_video_id
    vid = validate_video_id(d.get("id") if isinstance(d, dict) else None)
    if metadata_validator is not None:
        metadata_validator(d)
    # bili cookies are only for bilibili; other platforms (douyin) must fall
    # through to ytdlp_cookies from config instead of getting foreign cookies
    cookies = config.ROOT.parent / "reno-feas" / "cache" / "bili_cookies.txt"
    cookie_args = (["--cookies", str(cookies)]
                   if cookies.exists() and "bilibili" in url else [])
    vdir = originals / vid
    vdir.mkdir(parents=True, exist_ok=True)
    v, a = vdir / "video.mp4", vdir / "audio.m4a"
    lock = _vid_lock(vid) if profile is None else _vid_lock(vid, originals=originals)
    with lock, _download_input(url, d, vdir, frozen=sync_mode or profile is not None) as download_input:
        def legacy_video():
            if not v.exists():
                r = _ytdlp(*cookie_args, "-S", "res:480", "-f", "bv*",
                           "-o", str(v), *download_input)
                if r.returncode != 0:
                    raise RuntimeError(f"video download failed: {r.stderr[-300:]}")
            return v

        def legacy_audio():
            if not a.exists():
                r = _ytdlp(*cookie_args, "-f", "ba/bestaudio", "-o", str(a), *download_input)
                if r.returncode != 0:
                    # A normal complete failure preserves container audio fallback.
                    return None
            return a

        v = source_budget.media(url, d, vid, "video", legacy=legacy_video)
        a = source_budget.media(url, d, vid, "audio", legacy=legacy_audio)
        result = _register(vid, d, v, a, source_type="share_url", url=url, enqueue_process=enqueue_process,
                           source_proof=sync_mode, profile=profile)
        if sync_mode:
            result = {**result, "extracted_video_id": vid, "extracted_media_sha256": _sha256(v)}
    return result


def ingest_file(path: str, *, enqueue_process=True, profile=None) -> dict:
    if type(enqueue_process) is not bool:
        raise ValueError("enqueue_process must be a boolean")
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    vid = "file_" + hashlib.sha1(str(p).encode()).hexdigest()[:12]
    originals = config.ORIG if profile is None else profile.media / "originals"
    vdir = originals / vid
    vdir.mkdir(parents=True, exist_ok=True)
    v = vdir / "video.mp4"
    lock = _vid_lock(vid) if profile is None else _vid_lock(vid, originals=originals)
    with lock:  # same target as URL downloads - serialize writers
        if not v.exists():
            import shutil
            shutil.copy2(p, v)
        fake = {"id": vid, "title": p.stem, "uploader": None,
                "duration": _duration_of(v), "width": None, "height": None,
                "fps": None, "webpage_url": None}
        return _register(vid, fake, v, None, source_type="local_file", url=None,
                         enqueue_process=enqueue_process, source_proof=False, profile=profile)


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


def _register(vid, info, v: Path, a: Path, source_type, url, *, enqueue_process=True, profile=None, source_proof=None) -> dict:
    # The import's per-video lock precedes this lifetime lock. Do not hold
    # lifecycle protection across downloads; cover SHA through root commit.
    with storage_admin.connection(profile) as con:
        profile = storage_admin.from_connection(con, profile=profile)
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
            "files": {"video": _store_path(v, media_base=profile.media.parent),
                      "audio": _store_path(a, media_base=profile.media.parent) if a and a.exists() else None},
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
            if (not enqueue_process if source_proof is None else source_proof):
                # Sync must not manufacture a source proof for new bytes while
                # the canonical asset still names an older content hash.
                from .sync_protocol import SyncError
                raise SyncError("Re-downloaded media conflicts with its stored identity",
                                "SYNC_SOURCE_MEDIA_MISMATCH", 409)
            # same vid, different content (re-download after deletion): update
            db.upsert_asset(con, asset)
            if enqueue_process:
                db.enqueue_job(con, vid, "process")
            con.commit()
            return {"video_id": vid, "status": "imported"}
        try:
            if enqueue_process:
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
