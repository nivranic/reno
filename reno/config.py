# -*- coding: utf-8 -*-
"""Central config: defaults overridable by config.local.json (gitignored)."""
import json
import os
import threading
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[1]
# RENO_DATA_DIR relocates every mutable path (db / media / frame cache).
# Deployment flexibility + lets isolated instances (tests, multi-process
# checks) run side by side without touching the repo's real data.
_data_dir = os.environ.get("RENO_DATA_DIR")
if _data_dir:
    DATA = Path(_data_dir)
    MEDIA = DATA.parent / "media"
else:
    DATA = ROOT / "data"
    MEDIA = ROOT / "media"
FRAMES = MEDIA / "frames"
AUDIO = MEDIA / "audio"
ORIG = MEDIA / "originals"
FRAME_CACHE = MEDIA.parent / "frames_cache"

# thread safety: get()'s lazy _load and set_local's cache drop race under
# concurrent requests (robustness suite finding #11 - NoneType.get 500s)
_config_lock = threading.Lock()

DEFAULTS = {
    "zhipu_api_key": "",
    "zhipu_base_url": "https://open.bigmodel.cn/api/paas/v4",
    "atomize_model": "GLM-5.3-Flash",
    "judge_models": ["GLM-5.3-Flash"],
    "vlm_model": "glm-4.6v",
    "asr_model": "small",
    "asr_compute": "int8",
    "max_frames_per_video": 22,
    "safety_interval_s": 2.5,
    "hf_endpoint": "https://hf-mirror.com",
    "atomize_prompt_version": "v2",
    "judge_prompt_version": "v2",
    "taxonomy_version": "v2",
    "schema_version": "2.0.0",
    # deployment role: "all" = current single-machine behavior; "cloud" =
    # serving only (no pipeline worker, imports enqueue, glm-5.3* coding-plan
    # models refused - see docs/sync-plan.md); "home" = full pipeline + sync
    "node_role": "all",
    # cloud /ask model: a paas (non glm-5.3*) model, empty falls back to
    # atomize_model - which on cloud must not be glm-5.3* (llm guard)
    "ask_model": "",
    "summary_model": "",
    # separate credential for /api/sync/* (Phase 1 sync agent); empty = sync
    # endpoints stay closed. Header-only (X-Sync-Token), never a query param
    "sync_token": "",
    # /ask per-token rate limit per minute, 0 = off
    "ask_rate_per_min": 10,
}


def is_cloud() -> bool:
    return str(get("node_role") or "all").lower() == "cloud"


def resolve_media(p) -> "Path | None":
    """Resolve a files_json path entry against the media base (MEDIA.parent).

    Canonical stored form is POSIX-relative ("media/originals/x.mp4"), which
    resolves identically for a repo checkout and a relocated RENO_DATA_DIR.
    Legacy rows may hold backslash separators or absolute Windows paths;
    absolute values pass through unchanged, separators are normalized."""
    if not p:
        return None
    s = str(p)
    if s.startswith(("/", "\\\\")) or (len(s) > 2 and s[1] == ":"):
        return Path(s)
    return MEDIA.parent / PurePosixPath(s.replace("\\", "/"))

_cache = None


def get(key: str = "", default=None):
    global _cache
    with _config_lock:
        if _cache is None:
            _load()
        if not key:
            return _cache
        return _cache.get(key, DEFAULTS.get(key, default))


def _load():
    global _cache
    cfg = dict(DEFAULTS)
    local = ROOT / "config.local.json"
    if local.exists():
        cfg.update(json.loads(local.read_text(encoding="utf-8")))
    # env vars win (RENO_<UPPER_KEY>)
    for k in list(cfg):
        env = os.environ.get(f"RENO_{k.upper()}")
        if env is not None:
            cfg[k] = env
    _cache = cfg
    for d in (DATA, MEDIA, FRAMES, AUDIO, ORIG, FRAME_CACHE):
        d.mkdir(parents=True, exist_ok=True)


def db_path() -> Path:
    return DATA / "reno.db"


def set_local(updates: dict):
    """Merge `updates` into config.local.json (created if missing), then
    drop the in-memory cache so subsequent get() calls see the new values
    without a process restart. Never touches unrelated keys. The write is
    lock-serialized and atomic (tmp file + rename)."""
    with _config_lock:
        local = ROOT / "config.local.json"
        data = {}
        if local.exists():
            try:
                data = json.loads(local.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, ValueError):
                data = {}
        data.update(updates)
        tmp = local.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n",
                       encoding="utf-8")
        tmp.replace(local)
    global _cache
    _cache = None
