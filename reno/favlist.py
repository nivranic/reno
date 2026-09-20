# -*- coding: utf-8 -*-
"""Favlist / batch import: enumerate a bilibili favorites list via yt-dlp
flat-playlist, then feed each video through the normal ingest (SHA-256
idempotent). Works for any yt-dlp-supported playlist URL; single-stream
platforms (douyin) are tolerated (audio download failure is a warning)."""
import json
from pathlib import Path

from . import config, db, ingest


def enumerate_favlist(url: str, cookies: str = None):
    """[(id, title)] via yt-dlp flat-playlist. cookies: Netscape cookie file
    for PRIVATE lists (owner) - public lists work anonymously."""
    from .ingest import _ytdlp
    args = ["--flat-playlist", "--dump-json", "--no-warnings"]
    cookie_file = cookies or config.get("ytdlp_cookies")
    if cookie_file and Path(cookie_file).exists():
        args += ["--cookies", str(cookie_file)]
    r = _ytdlp(*args, url)
    if r.returncode != 0:
        raise RuntimeError(f"favlist enumeration failed: {r.stderr[-400:]}")
    out = []
    for line in r.stdout.strip().splitlines():
        try:
            d = json.loads(line)
            if d.get("id"):
                out.append((d["id"], d.get("title") or ""))
        except json.JSONDecodeError:
            continue
    return out


def import_urls(urls, platform_url_of=None) -> dict:
    """Ingest a list of items: URLs, local file paths (mixed freely), or
    (id,title) tuples -> constructed bilibili URLs. SHA-256 dedup makes
    re-runs cheap. Paces network downloads; local files are instant.
    Returns per-status counts."""
    import time as _time
    pace_s = float(config.get("batch_pace_seconds", 2.0))
    stats = {"imported": [], "duplicate": [], "failed": []}
    net_since_pace = 0
    for item in urls:
        if isinstance(item, (tuple, list)):
            vid, title = item
            url = (platform_url_of or
                   (lambda v: f"https://www.bilibili.com/video/{v}/"))(vid)
        else:
            url = str(item)
        is_local = not url.startswith("http")
        try:
            if is_local:
                res = ingest.ingest_file(url)
            else:
                if net_since_pace and pace_s > 0:
                    import time as _t
                    _t.sleep(pace_s)
                res = ingest.ingest_url(url)
                net_since_pace += 1
            stats[res["status"]].append(res.get("video_id") or url)
        except Exception as e:  # noqa: BLE001 - one bad item must not stop the batch
            stats["failed"].append(f"{url}: {str(e)[:120]}")
    return stats


def import_favlist(url: str, cookies: str = None, limit: int = 0) -> dict:
    items = enumerate_favlist(url, cookies)
    if limit:
        items = items[:limit]
    return import_urls(items)
