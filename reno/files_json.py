# -*- coding: utf-8 -*-
"""Normalize legacy video_asset.files_json rows to the canonical form
consumed by config.resolve_media(): POSIX separators, relative to the media
base (MEDIA.parent), e.g. "media/originals/BVxxx.mp4".

Legacy shapes still resolve at read time (absolute paths pass through,
backslashes are fixed on the fly), but rows are rewritten once so a synced
cloud copy is clean from day one (docs/sync-plan.md section 3.2)."""
import json
from contextlib import nullcontext
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
import uuid

from . import config, db, storage_admin


def _is_absolute(s: str) -> bool:
    return s.startswith(("/", "\\")) or (len(s) > 2 and s[1] == ":")


def normalize_value(value, media_base: Path):
    """Return (canonical_string, file_found, changed).

    Tries the stored value (absolute as-is, relative against the media base
    with backslashes fixed); when the file exists, rewrites it to the
    POSIX-relative canonical form. Missing files are returned unchanged with
    file_found=False so callers can report them without breaking the row."""
    if not value:
        return value, False, False
    s = str(value)
    canonical_sep = s.replace("\\", "/")
    if _is_absolute(s):
        p = Path(s)
        if p.is_file():
            try:
                return p.relative_to(media_base).as_posix(), True, True
            except ValueError:
                return s, True, False  # absolute outside the media tree: keep
        return s, False, False
    p = media_base / PurePosixPath(canonical_sep)
    if p.is_file():
        return canonical_sep, True, canonical_sep != s
    return s, False, False


def run(dry_run: bool = False, *, profile=None) -> dict:
    """Rewrite every video_asset row; idempotent. Returns stats."""
    from . import storage_lifecycle
    if type(dry_run) is not bool:
        raise storage_lifecycle.StorageLifecycleError("STORAGE_INVALID_ARGUMENT", 400)
    profile = storage_admin.profile() if profile is None else storage_lifecycle._profile(profile)
    media_base = profile.media.parent
    stats = {"rows": 0, "rewritten": 0, "missing": []}
    with storage_admin.connection(profile) as con:
        if not dry_run:
            con.execute("BEGIN IMMEDIATE")
        rows = con.execute(
            "SELECT video_id, files_json FROM video_asset "
            "WHERE files_json IS NOT NULL").fetchall()
        for r in rows:
            stats["rows"] += 1
            try:
                files = json.loads(r["files_json"])
            except (ValueError, TypeError):
                continue  # mangled rows have their own repair script
            if not isinstance(files, dict):
                continue
            new_files = dict(files)
            changed = False
            for key in ("video", "audio"):
                val = files.get(key)
                if not val:
                    continue
                canon, found, did = normalize_value(val, media_base)
                if not found:
                    stats["missing"].append(f"{r['video_id']}:{key}")
                if did:
                    new_files[key] = canon
                    changed = True
            if changed:
                stats["rewritten"] += 1
                if not dry_run:
                    con.execute(
                        "UPDATE video_asset SET files_json=? WHERE video_id=?",
                        (json.dumps(new_files, ensure_ascii=False), r["video_id"]))
        if not dry_run:
            con.commit()
    return stats


def repair(dry_run: bool = False, *, profile=None) -> dict:
    """Repair standard-layout pointers; backup first, never invent files.

    Explicit maintenance owns the worker lock before lifecycle/SQL. A dry run
    only reads rows and existing files, and does not reserve a backup location.
    """
    from . import tasks
    from .sync_protocol import validate_video_id
    from . import storage_lifecycle
    if type(dry_run) is not bool:
        raise storage_lifecycle.StorageLifecycleError("STORAGE_INVALID_ARGUMENT", 400)
    profile = storage_admin.profile() if profile is None else storage_lifecycle._profile(profile)
    worker_lock = nullcontext(True) if dry_run else tasks.process_lock(profile=profile)
    result = {"rows": 0, "repaired": 0, "missing": 0, "invalid_video_ids": 0,
              "dry_run": dry_run, "backup_created": False}
    with worker_lock as acquired:
        if not acquired:
            raise tasks.TaskError("路径修复需要本实例写入者已停止", "WORKER_BUSY", 409)
        with storage_admin.connection(profile) as con:
            if not dry_run:
                stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
                destination = profile.database.with_name(profile.database.name + ".bak-files-repair-" + stamp + "-" + uuid.uuid4().hex)
                db.snapshot(con, destination, profile=profile, kind="maintenance")
                result["backup_created"] = True
                con.execute("BEGIN IMMEDIATE")
            for row in con.execute("SELECT video_id,files_json FROM video_asset").fetchall():
                result["rows"] += 1
                try:
                    video_id = validate_video_id(row["video_id"])
                except ValueError:
                    result["invalid_video_ids"] += 1
                    continue
                originals = profile.media / "originals" / video_id
                proposed = {"video": originals / "video.mp4", "audio": originals / "audio.m4a"}
                try:
                    existing = json.loads(row["files_json"] or "null")
                    valid = (type(existing) is dict and all(type(existing.get(key)) is str
                        and (Path(existing[key]) if _is_absolute(existing[key]) else
                             profile.media.parent / existing[key].replace("\\", "/")).is_file()
                        for key in ("video", "audio")))
                except (ValueError, TypeError):
                    valid = False
                if valid:
                    continue
                if not all(path.is_file() for path in proposed.values()):
                    result["missing"] += 1
                    continue
                result["repaired"] += 1
                if not dry_run:
                    files = {key: path.relative_to(profile.media.parent).as_posix() for key, path in proposed.items()}
                    con.execute("UPDATE video_asset SET files_json=? WHERE video_id=?", (json.dumps(files), video_id))
            if not dry_run:
                con.commit()
    return result
