# -*- coding: utf-8 -*-
"""Favorites enumeration and compatibility helpers for durable import jobs."""
import json
from pathlib import Path

from . import config, db, ingest


def enumerate_favlist(url: str, cookies: str = None):
    """[(id, title)] via yt-dlp flat-playlist. cookies: Netscape cookie file
    for PRIVATE lists (owner) - public lists work anonymously."""
    from . import source_budget

    def legacy_enumeration():
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

    return source_budget.enumeration(url, legacy=legacy_enumeration, cookies=cookies)


def import_urls(urls, platform_url_of=None, *, request_id=None) -> dict:
    """Submit URLs/files or Bilibili (id, title) pairs; never download here."""
    from . import import_tasks
    targets = [(platform_url_of or (lambda v: f"https://www.bilibili.com/video/{v}/"))(item[0])
               if isinstance(item, (tuple, list)) else str(item) for item in urls]
    return import_tasks.submit_batch(targets, request_id, allow_local=True)


def import_favlist(url: str, cookies: str = None, limit: int = 0, *, request_id=None) -> dict:
    """Only the original favlist worker job enumerates and submits its children."""
    from . import import_tasks
    return import_tasks.submit_favlist(url, request_id, cookies=cookies, limit=limit)
