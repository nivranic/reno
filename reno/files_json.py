# -*- coding: utf-8 -*-
"""Normalize legacy video_asset.files_json rows to the canonical form
consumed by config.resolve_media(): POSIX separators, relative to the media
base (MEDIA.parent), e.g. "media/originals/BVxxx.mp4".

Legacy shapes still resolve at read time (absolute paths pass through,
backslashes are fixed on the fly), but rows are rewritten once so a synced
cloud copy is clean from day one (docs/sync-plan.md section 3.2)."""
import json
from pathlib import Path, PurePosixPath

from . import config, db


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


def run(dry_run: bool = False) -> dict:
    """Rewrite every video_asset row; idempotent. Returns stats."""
    media_base = config.MEDIA.parent
    con = db.connect()
    stats = {"rows": 0, "rewritten": 0, "missing": []}
    try:
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
    finally:
        con.close()
    return stats
