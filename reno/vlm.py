# -*- coding: utf-8 -*-
"""VLM step: cloud vision (glm-4.6v) per budget frame - scene observation +
subtitle cross-check. This is the feasibility-gap filler: local image
injection was broken; the cloud channel was verified live during setup."""
import json
import time

from . import config, db, llm
from .media import budget_frames

PROMPT = (
    "观察这一视频帧。只输出JSON对象:{\"scene_description\":\"一句话客观描述画面内容"
    "(施工阶段/材料/动作,不做对错判断)\",\"materials\":[\"可见材料\"],"
    "\"tools\":[\"可见工具\"],\"subtitle_text\":\"画面中文字幕原文,无则null\","
    "\"measurement\":\"画面中可见的数字/尺寸/参数,无则null\"}"
)


def _observe_frame(path: str) -> dict:
    """Bounded attempts: vision -> parse -> repair prompt; raw text fallback.
    Handles empty content (reasoning-only replies) by retrying the call."""
    raw = ""
    try:
        raw = llm.vision(path, PROMPT, max_tokens=250)
    except Exception:  # noqa: BLE001
        pass
    obj = llm.extract_json(raw) if raw.strip() else None
    if obj is None:
        try:
            raw = llm.vision(
                path,
                "只输出一个JSON对象(不要markdown、不要解释):"
                '{"scene_description":"...","materials":[],"tools":[],'
                '"subtitle_text":null,"measurement":null}',
                max_tokens=250)
        except Exception:  # noqa: BLE001
            return {}
        obj = llm.extract_json(raw) if raw.strip() else None
    if obj is None and raw.strip():
        obj = {"scene_description": raw.strip()[:200], "materials": [],
               "tools": [], "subtitle_text": None, "measurement": None}
    return obj or {}


def _s(x):
    """VLM fields occasionally come back as lists; SQLite needs scalars."""
    if x is None:
        return None
    if isinstance(x, list):
        return ";".join(str(i) for i in x) if x else None
    if isinstance(x, dict):
        import json
        return json.dumps(x, ensure_ascii=False)
    return str(x)


def run(con, video_id: str) -> dict:
    frames = budget_frames(con, video_id)
    t0 = time.time()
    obs = []
    for fr in frames:
        obj = _observe_frame(fr["path"])
        obs.append({
            "id": f"vis_{fr['frame_id'][1:]}",  # f0001200 -> vis_0001200
            "frame_id": fr["frame_id"], "pts_ms": fr["pts_ms"],
            "scene_description": _s(obj.get("scene_description")),
            "materials": obj.get("materials") or [],
            "tools": obj.get("tools") or [],
            "subtitle_text": _s(obj.get("subtitle_text")),
            "measurement": _s(obj.get("measurement")),
        })
    db.replace_visual(con, video_id, obs)
    dt = time.time() - t0
    stats = {"n_frames": len(frames), "n_ok": sum(1 for o in obs if o["scene_description"]),
             "process_s": round(dt, 1), "model": config.get("vlm_model")}
    db.log_run(con, video_id, "vlm", model=config.get("vlm_model"), ok=True,
               detail=str(stats))
    con.commit()
    return stats
