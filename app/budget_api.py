"""Real local budget settings/observations, protected by the normal /api gate."""
from __future__ import annotations

import json
import hashlib

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from reno import budget_settings, budget_views, storage_admin, storage_lifecycle, tasks


router = APIRouter(prefix="/api")
MAX_SETTINGS_BODY = 16 * 1024
_HOME_CODES = {"BUDGET_HOME_CONTROL_INVALID": 400, "BUDGET_HOME_CONTROL_CHANGED": 409,
    "BUDGET_HOME_CONTROL_UNAVAILABLE": 409, "BUDGET_HOME_CONTROL_ROLE_FORBIDDEN": 403}


class _HomeControlError(ValueError):
    def __init__(self, code="BUDGET_HOME_CONTROL_UNAVAILABLE"):
        self.code = code if code in _HOME_CODES else "BUDGET_HOME_CONTROL_UNAVAILABLE"
        self.status = _HOME_CODES[self.code]
        super().__init__(self.code)


def _home_error(exc):
    return JSONResponse(status_code=exc.status, content={"code": exc.code,
        "detail": "家庭任务状态已变化或暂不可操作，请刷新后核对。"})


def _sha(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
        allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()


def _home_facts(profile, con, *, row=None):
    """Read an original core and its current real SQL task; no worker claim/init."""
    from reno import sync_agent, sync_access
    from reno.sync_state import StateStore
    from reno.sync_protocol import SyncError
    try:
        _, role = budget_settings.read_with_role()
        if role == "cloud":
            raise _HomeControlError("BUDGET_HOME_CONTROL_ROLE_FORBIDDEN")
        if profile.identity != storage_admin.profile().identity:
            raise _HomeControlError()
        # Sync can be disabled while an original paid result awaits delivery.
        # These controls never authorize a new remote request on their own.
        peer = sync_agent.settings(delivery_only=True)
        state = StateStore(profile.database.parent, profile=profile).read_observation()
        core = state.get("source_execution")
        if core is None or state["binding"] != core["binding"]:
            raise _HomeControlError()
        identity = sync_access.identity(con)
        binding = core["binding"]
        if (binding["home_library_id"] != identity["library_id"] or binding["home_node_id"] != identity["node_id"]
                or binding["target_url"] != peer["url"] or any(
                    binding["target_" + key] != peer["sync_target_" + key] for key in ("library_id", "node_id"))):
            raise _HomeControlError()
        selected = con.execute("SELECT * FROM job WHERE job_id=?", (core["root_job_id"],)).fetchone() if row is None else row
        tasks._require_sync_job(selected)
        if (selected["status"] not in {"pending", "running"}
                or (selected["job_id"], selected["continuation_id"], selected["execution_generation"]) != (
                    core["root_job_id"], core["continuation_id"], core["execution_generation"])):
            raise _HomeControlError()
        paused = bool(selected["manual_paused"])
        public = {key: selected[key] for key in ("job_id", "status", "updated_at", "not_before", "blocked_reason", "wait_mode")}
        public.update(manual_paused=paused, can_pause=not paused,
            can_resume=paused or selected["wait_mode"] != "none")
        pending = state.get("budget_pending")
        revision = _sha({"format_version": 1, "core_sha256": _sha(core),
            "continuation_id": selected["continuation_id"], "execution_generation": selected["execution_generation"],
            "task": public, "budget_wait_id": None if pending is None else pending["defer_request"]["request_id"],
            "source_control_pending_sha256": None if state.get("source_control_pending") is None else _sha(state["source_control_pending"])})
        return {"available": True, "task": public, "control_revision": revision}
    except _HomeControlError:
        raise
    except (SyncError, tasks.TaskError, budget_settings.BudgetSettingsError,
            OSError, ValueError, TypeError, KeyError, UnicodeError):
        raise _HomeControlError() from None


def _home_view(profile, role):
    empty = {"available": False, "task": None, "control_revision": None}
    if role == "cloud":
        return empty
    from reno import migrations
    try:
        with storage_lifecycle.shared(profile):
            con = migrations._open(profile.database, readonly=True)
            try:
                con.execute("PRAGMA query_only=ON")
                con.execute("BEGIN")
                migrations.validate(con)
                return _home_facts(profile, con)
            finally:
                con.close()
    except (_HomeControlError, migrations.SchemaVersionError, storage_lifecycle.StorageLifecycleError, OSError):
        return empty


def _control_home(action, expected):
    _, role = budget_settings.read_with_role()
    if role == "cloud":
        raise _HomeControlError("BUDGET_HOME_CONTROL_ROLE_FORBIDDEN")
    profile = storage_admin.profile()
    # Local restore needs EX; keep this exact profile alive across selector and
    # SQL effect. No long state EX or process cancellation belongs to a button.
    with storage_lifecycle.shared(profile):
        with storage_admin.connection(profile) as con:
            current = _home_facts(profile, con)
        if current["control_revision"] != expected:
            raise _HomeControlError("BUDGET_HOME_CONTROL_CHANGED")
        def effect_guard(con, row):
            latest = _home_facts(profile, con, row=row)
            if latest["control_revision"] != expected:
                raise _HomeControlError("BUDGET_HOME_CONTROL_CHANGED")
        function = tasks.pause_sync if action == "pause" else tasks.resume_sync
        function(current["task"]["job_id"], profile=profile, effect_guard=effect_guard)
        with storage_admin.connection(profile) as con:
            return _home_facts(profile, con)


def _settings_error(exc):
    return JSONResponse(status_code=exc.status, content={"detail": "预算设置未保存，请核对有效设置后重试。",
        **exc.public()})


@router.get("/budget")
def inspect_budget():
    try:
        settings, role = budget_settings.read_with_role()
        profile = storage_admin.profile()
        usage = budget_views.inspect(profile=profile, policy=settings["effective_policy"])
        home = _home_view(profile, role)
        return {"settings": settings, "usage": usage, "home": home,
            "capabilities": {"read": True, "edit_policy": any(field["editable"] for field in settings["fields"].values()),
                "task_control": role != "cloud", "home_control": home["available"], "node_role": role}}
    except budget_settings.BudgetSettingsError as exc:
        return _settings_error(exc)
    except budget_views.BudgetViewError as exc:
        return JSONResponse(status_code=503, content={"detail": "本机预算账本暂不可用，未将缺失用量记为零。", "code": exc.code})


async def _home_request(request, action):
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > 512:
            return _home_error(_HomeControlError("BUDGET_HOME_CONTROL_INVALID"))
        content.extend(part)
    try:
        body = json.loads(content)
        if type(body) is not dict or set(body) != {"expected_control_revision"}:
            raise _HomeControlError("BUDGET_HOME_CONTROL_INVALID")
        revision = body["expected_control_revision"]
        if type(revision) is not str or len(revision) != 64 or any(char not in "0123456789abcdef" for char in revision):
            raise _HomeControlError("BUDGET_HOME_CONTROL_INVALID")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _home_error(_HomeControlError("BUDGET_HOME_CONTROL_INVALID"))
    try:
        from starlette.concurrency import run_in_threadpool
        result = await run_in_threadpool(_control_home, action, revision)
        if action == "resume":
            from reno import worker
            worker.ensure_running()
        return JSONResponse(status_code=202, content=result)
    except _HomeControlError as exc:
        return _home_error(exc)
    except (tasks.TaskError, budget_settings.BudgetSettingsError, storage_lifecycle.StorageLifecycleError):
        return _home_error(_HomeControlError())


@router.post("/budget/home/pause", status_code=202)
async def pause_home(request: Request):
    return await _home_request(request, "pause")


@router.post("/budget/home/resume", status_code=202)
async def resume_home(request: Request):
    return await _home_request(request, "resume")


@router.post("/config/budget")
async def save_budget(request: Request):
    content = bytearray()
    async for part in request.stream():
        if len(content) + len(part) > MAX_SETTINGS_BODY:
            return JSONResponse(status_code=413, content={"detail": "预算设置请求过大。", "code": "BUDGET_SETTINGS_REQUEST_TOO_LARGE"})
        content.extend(part)
    try:
        body = json.loads(content)
        if type(body) is not dict or set(body) != {"expected_settings_revision", "updates"}:
            raise budget_settings.BudgetSettingsError("BUDGET_SETTINGS_INVALID")
    except (ValueError, TypeError, UnicodeError, RecursionError):
        return _settings_error(budget_settings.BudgetSettingsError("BUDGET_SETTINGS_INVALID"))
    try:
        # Blocking OS locks and file I/O stay outside the ASGI event loop.
        from starlette.concurrency import run_in_threadpool
        return await run_in_threadpool(budget_settings.save, body["updates"], body["expected_settings_revision"])
    except budget_settings.BudgetSettingsError as exc:
        return _settings_error(exc)
