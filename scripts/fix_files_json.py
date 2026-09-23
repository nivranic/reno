# -*- coding: utf-8 -*-
"""Repair video_asset.files_json rows that are NULL or mangled, from the
standard on-disk layout media/originals/<vid>/{video.mp4,audio.m4a}."""
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
con = sqlite3.connect(ROOT / "data" / "reno.db")

refs = [r[0] for r in con.execute(
    "SELECT files_json FROM video_asset WHERE files_json IS NOT NULL LIMIT 2")]
print("reference healthy rows:")
for r in refs:
    print("  ", r)

fixed = 0
for vid, fj in con.execute("SELECT video_id, files_json FROM video_asset").fetchall():
    files = {"video": f"media/originals/{vid}/video.mp4",
             "audio": f"media/originals/{vid}/audio.m4a"}
    ok = all((ROOT / p).is_file() for p in files.values())
    try:
        parsed = json.loads(fj) if fj else None
        valid = parsed and all((ROOT / parsed.get(k, "X")).is_file()
                               for k in ("video", "audio"))
    except Exception:
        valid = False
    if not valid and ok:
        con.execute("UPDATE video_asset SET files_json=? WHERE video_id=?",
                    (json.dumps(files), vid))
        fixed += 1
        print(f"fixed {vid}")
    elif not valid:
        print(f"WARNING {vid}: files missing on disk, left as-is")
con.commit()
print("fixed:", fixed)

# verify round-trip
for vid, fj in con.execute("SELECT video_id, files_json FROM video_asset").fetchall():
    p = json.loads(fj)
    assert (ROOT / p["video"]).is_file(), vid
print("all files_json valid, paths exist")
