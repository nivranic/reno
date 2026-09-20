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
    """Ingest a list of URLs (or (id,title) tuples -> constructed bilibili
    URLs). SHA-256 dedup makes re-runs cheap. Returns per-status counts."""
    stats = {"imported": [], "duplicate": [], "failed": []}
    for item in urls:
        if isinstance(item, (tuple, list)):
            vid, title = item
            url = (platform_url_of or
                   (lambda v: f"https://www.bilibili.com/video/{v}/"))(vid)
        else:
            url = str(item)
        try:
            res = ingest.ingest_url(url)
            stats[res["status"]].append(res.get("video_id") or url)
        except Exception as e:  # noqa: BLE001 - one bad link must not stop the batch
            stats["failed"].append(f"{url}: {str(e)[:120]}")
    return stats


def import_favlist(url: str, cookies: str = None, limit: int = 0) -> dict:
    items = enumerate_favlist(url, cookies)
    if limit:
        items = items[:limit]
    return import_urls(items)
