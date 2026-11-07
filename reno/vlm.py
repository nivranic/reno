# -*- coding: utf-8 -*-
"""VLM step: cloud vision (glm-4.6v) per budget frame - scene observation +
subtitle cross-check. This is the feasibility-gap filler: local image
injection was broken; the cloud channel was verified live during setup."""
import json
import time
import hashlib
from pathlib import Path

from . import config, db, llm, model_budget, model_scopes, storage_admin, storage_lifecycle
from .budget import BudgetLedgerError
from .budget_contract import BudgetContractError, BudgetDeferred
from .media import budget_frames
from .sync_source_opportunity17 import ReadmissionRequired17

PROMPT = (
    "观察这一视频帧。只输出JSON对象:{\"scene_description\":\"一句话客观描述画面内容"
    "(施工阶段/材料/动作,不做对错判断)\",\"materials\":[\"可见材料\"],"
    "\"tools\":[\"可见工具\"],\"subtitle_text\":\"画面中文字幕原文,无则null\","
    "\"measurement\":\"画面中可见的数字/尺寸/参数,无则null\"}"
)


class VlmError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _usable(value):
    if isinstance(value, str):
        return bool(value.strip())
    if isinstance(value, (list, tuple)):
        return any(_usable(item) for item in value)
    if isinstance(value, dict):
        return any(_usable(item) for item in value.values())
    return type(value) in (int, float)  # A visible zero measurement is usable.


def _observe_frame(path: str) -> dict:
    """Bounded attempts: vision -> parse -> repair prompt; raw text fallback.
    Handles empty content (reasoning-only replies) by retrying the call."""
    raw = ""
    try:
        raw = llm.vision(path, PROMPT, max_tokens=250)
    except (BudgetDeferred, BudgetContractError, BudgetLedgerError, ReadmissionRequired17):
        raise
    except Exception:  # noqa: BLE001
        pass
    obj = llm.extract_json(raw) if raw.strip() else None
    if obj is None:
        try:
            with model_budget.phase("repair"):
                raw = llm.vision(
                    path,
                    "只输出一个JSON对象(不要markdown、不要解释):"
                    '{"scene_description":"...","materials":[],"tools":[],'
                    '"subtitle_text":null,"measurement":null}',
                    max_tokens=250)
        except (BudgetDeferred, BudgetContractError, BudgetLedgerError, ReadmissionRequired17):
            raise
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


def _frame_sha(path):
    # Missing fake transport fixtures carry no fabricated content fingerprint;
    # a real vision call still opens the image before any model dispatch.
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except FileNotFoundError:
        return None


def run(con, video_id: str, *, profile=None) -> dict:
    from .worker import _execution_profile
    if con.in_transaction:
        raise BudgetLedgerError("BUDGET_BUSINESS_TRANSACTION_ACTIVE")
    inherited = model_scopes.current_execution()
    profile = storage_admin.from_connection(con, profile=profile or (
        inherited.profile if inherited is not None else _execution_profile()))
    frames = [dict(fr) for fr in budget_frames(con, video_id)]
    model = config.get("vlm_model")
    with model_scopes.execution(profile, "vlm", {"video_id": video_id,
            "model": model, "prompt_version": "vlm-observation-v1"},
            selection=tuple(fr["frame_id"] for fr in frames), business_connection=con) as execution:
        if execution.manual and execution.consumer == "vlm":
            by_id = {fr["frame_id"]: fr for fr in frames}
            if any(frame_id not in by_id for frame_id in execution.selection):
                raise BudgetDeferred("ledger_unavailable")
            frames = [by_id[frame_id] for frame_id in execution.selection]
        return _run(con, video_id, frames, model, profile, execution)


def _run(con, video_id, frames, model, profile, execution):
    t0 = time.time()
    obs = []
    valid_frames = []
    frame_shas = tuple(_frame_sha(fr["path"]) for fr in frames)
    frozen_sha = hashlib.sha256(db.j([{**fr, "frame_sha256": digest}
        for fr, digest in zip(frames, frame_shas)]).encode()).hexdigest()
    for i, fr in enumerate(frames):
        frame_sha = frame_shas[i]
        tags = (("prompt_version", "vlm-observation-v1"), ("frozen_input_sha256", frozen_sha))
        if frame_sha is not None:
            tags += (("frame_sha256", frame_sha),)
        with model_scopes.call(execution, f"vlm:{video_id}:{fr['frame_id']}",
                business_connection=con, version_tags=tags, expects_json=True):
            obj = _observe_frame(fr["path"])
        if _frame_sha(fr["path"]) != frame_sha:
            raise BudgetDeferred("ledger_unavailable")
        if not isinstance(obj, dict):
            obj = {}
        if any(_usable(obj.get(field)) for field in (
                "scene_description", "subtitle_text", "measurement", "materials", "tools")):
            valid_frames.append(fr["frame_id"])
        obs.append({
            "id": f"vis_{fr['frame_id'][1:]}",  # f0001200 -> vis_0001200
            "frame_id": fr["frame_id"], "pts_ms": fr["pts_ms"],
            "scene_description": _s(obj.get("scene_description")),
            "materials": obj.get("materials") or [],
            "tools": obj.get("tools") or [],
            "subtitle_text": _s(obj.get("subtitle_text")),
            "measurement": _s(obj.get("measurement")),
        })
        if i % 5 == 4:
            time.sleep(1.0)  # gentle pacing under single-key rate limits
    dt = time.time() - t0
    if frames and len(valid_frames) != len(frames):
        # Keep the complete prior observation set and this unfinished execution.
        # Syntactically valid but unusable paid JSON remains a full receipt;
        # restarting reuses it without authorizing another provider attempt.
        raise VlmError("VLM_INCOMPLETE_RESULT" if valid_frames else "VLM_EMPTY_RESULT")
    stats = {"n_frames": len(frames), "n_ok": len(valid_frames),
             "process_s": round(dt, 1), "model": model}
    with storage_lifecycle.shared(profile):
        if tuple(_frame_sha(fr["path"]) for fr in frames) != frame_shas:
            raise BudgetDeferred("ledger_unavailable")
        con.execute("BEGIN IMMEDIATE")
        try:
            current = {fr["frame_id"]: dict(fr) for fr in budget_frames(con, video_id)}
            if any(current.get(fr["frame_id"]) != fr for fr in frames):
                raise BudgetDeferred("ledger_unavailable")
            db.replace_visual(con, video_id, obs)
            db.log_run(con, video_id, "vlm", model=model, ok=True, detail=str(stats))
            if execution.consumer == "vlm":
                model_scopes.checkpoint(con, execution,
                    completed_items=tuple(fr["frame_id"] for fr in frames), completed=True)
            con.commit()
        except BaseException:
            con.rollback()
            raise
    return stats
