# -*- coding: utf-8 -*-
"""Central config: defaults overridable by config.local.json (gitignored)."""
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
MEDIA = ROOT / "media"
FRAMES = MEDIA / "frames"
AUDIO = MEDIA / "audio"
ORIG = MEDIA / "originals"
FRAME_CACHE = ROOT / "frames_cache"

DEFAULTS = {
    "zhipu_api_key": "",
    "zhipu_base_url": "https://open.bigmodel.cn/api/paas/v4",
    "atomize_model": "glm-4.6",
    "judge_models": ["glm-4.7-flash", "glm-4.5-flash", "glm-4.6"],
    "vlm_model": "glm-4.6v",
    "asr_model": "small",
    "asr_compute": "int8",
    "max_frames_per_video": 22,
    "safety_interval_s": 2.5,
    "hf_endpoint": "https://hf-mirror.com",
    "atomize_prompt_version": "v1",
    "judge_prompt_version": "v1",
    "taxonomy_version": "v1",
    "schema_version": "1.0.0",
}

_cache = None


def get(key: str = ""):
    global _cache
    if _cache is None:
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
    return _cache if not key else _cache.get(key, DEFAULTS.get(key))


def db_path() -> Path:
    return DATA / "reno.db"
