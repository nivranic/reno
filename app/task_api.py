"""User-visible maintenance tasks; protected by the application's API gate."""
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from reno import config, report, tasks, worker

router = APIRouter(prefix="/api")


async def _body(request):
    try:
        value = await request.json()
    except Exception:
        raise tasks.TaskError("需要 JSON 对象", "INVALID_REQUEST", 400) from None
    if not isinstance(value, dict):
        raise tasks.TaskError("需要 JSON 对象", "INVALID_REQUEST", 400)
    return value


def _error(exc):
    return JSONResponse(status_code=exc.status, content={"detail": str(exc), "code": exc.code})


def _job_id(value):
    try:
        result = int(value)
        if not 1 <= result <= 2**63 - 1:
            raise ValueError
        return result
    except (TypeError, ValueError):
        raise tasks.TaskError("任务编号无效", "INVALID_REQUEST", 400) from None


@router.post("/tasks", status_code=202)
async def create_task(request: Request):
    try:
        body = await _body(request)
        payload = body.get("payload")
        if body.get("kind") == "process" and isinstance(payload, dict) and payload.get("force"):
            raise tasks.TaskError("界面仅支持继续未完成步骤；全量重算需单独维护操作")
        result = tasks.submit(body.get("kind"), body.get("video_id"), payload, body.get("request_id"))
        worker.ensure_running()
        return result
    except tasks.TaskError as exc:
        return _error(exc)


@router.get("/tasks")
def get_tasks(limit: str = "50"):
    try:
        number = int(limit)
        if not 1 <= number <= 100:
            raise ValueError
    except ValueError:
        return _error(tasks.TaskError("limit 必须为 1 至 100", "INVALID_REQUEST", 400))
    return {"tasks": [] if config.is_cloud() else tasks.list_tasks(number)}


@router.get("/tasks/{job_id}")
def get_task(job_id: str):
    if config.is_cloud():
        return _error(tasks.TaskError("任务不存在", "NOT_FOUND", 404))
    try:
        value = tasks.get(_job_id(job_id))
    except tasks.TaskError as exc:
        return _error(exc)
    if value is None:
        return _error(tasks.TaskError("任务不存在", "NOT_FOUND", 404))
    return value


@router.post("/jobs/{job_id}/retry", status_code=202)
async def retry_task(job_id: str, request: Request):
    try:
        body = await _body(request)
        value = tasks.retry(_job_id(job_id), body.get("request_id"))
        worker.ensure_running()
        return value
    except tasks.TaskError as exc:
        return _error(exc)


@router.post("/jobs/{job_id}/pause", status_code=202)
async def pause_task(job_id: str, request: Request):
    try:
        if await _body(request):
            raise tasks.TaskError("暂停任务不接受额外参数", "INVALID_REQUEST", 400)
        return tasks.pause(_job_id(job_id))
    except tasks.TaskError as exc:
        return _error(exc)


@router.post("/jobs/{job_id}/resume", status_code=202)
async def resume_task(job_id: str, request: Request):
    try:
        if await _body(request):
            raise tasks.TaskError("恢复任务不接受额外参数", "INVALID_REQUEST", 400)
        value = tasks.resume(_job_id(job_id))
        worker.ensure_running()
        return value
    except tasks.TaskError as exc:
        return _error(exc)


@router.get("/reports/meta")
def report_metadata():
    return {"reports": report.metadata()}
