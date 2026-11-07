"""Field records use the same gate and mutation envelopes as personal choices."""
from fastapi import APIRouter, Request
from starlette.concurrency import run_in_threadpool

from reno import checklists, personal, personal_checklist_share_preview
from .personal_api import PersonalRoute, body, mutation
from .personal_read_api import ReadResult, json_response, read as personal_read, read_result

router = APIRouter(prefix="/api", route_class=PersonalRoute)


def read(callback, *, request=None):
    return personal_read(callback, request=request, field=True)


@router.get("/projects/{project_id}/checklists")
def lists(project_id: str, request: Request, offset: int = 0, limit: int = 30):
    return read(lambda con: checklists.list_instances(con, project_id, offset, limit), request=request)


@router.post("/projects/{project_id}/checklists")
async def create(project_id: str, request: Request):
    return mutation(await run_in_threadpool(checklists.create, project_id, await body(request)))


@router.post("/projects/{project_id}/checklists/share-preview")
async def checklist_share_preview(project_id: str, request: Request):
    data = await body(request, share_preview=True)

    def produce():
        result = read_result(lambda con: personal_checklist_share_preview.snapshot_locked(con, project_id, data),
                             request=request, field=True)
        return ReadResult(personal_checklist_share_preview.finalize_snapshot(result.data), result.version)

    return json_response(await run_in_threadpool(produce))


@router.get("/checklists/{instance_id}")
def detail(instance_id: str, request: Request):
    return read(lambda con: {"checklist": checklists.detail(con, instance_id)}, request=request)


@router.patch("/checklists/{instance_id}")
async def update(instance_id: str, request: Request):
    return mutation(await run_in_threadpool(checklists.update, instance_id, await body(request)))


@router.patch("/checklist-items/{item_id}")
async def update_item(item_id: str, request: Request):
    return mutation(await run_in_threadpool(checklists.update_item, item_id, await body(request)))


@router.get("/checklist-items/{item_id}/revisions")
def revisions(item_id: str, request: Request):
    return read(lambda con: {"revisions": personal.revisions(con, "checklist_item", item_id)}, request=request)


@router.post("/checklist-items/{item_id}/checks")
async def append(item_id: str, request: Request):
    return mutation(await run_in_threadpool(checklists.append_check, item_id, await body(request)))
