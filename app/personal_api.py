"""Authenticated personal-domain HTTP API; registered before the SPA fallback."""
import json

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool

from reno import config, personal
from reno import personal_attachments as attachments
from reno import personal_share_preview, personal_checklist_share_preview
from .personal_attachment_response import attachment_response
from .personal_read_api import ReadResult, error_response, json_response, private_headers, read, read_result


class PersonalRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                preview_post = (self.name == "share_preview" and request.method == "POST"
                    and self.path == "/api/projects/{project_id}/decision-cards/share-preview")
                checklist_preview_post = (self.name == "checklist_share_preview" and request.method == "POST"
                    and self.path == "/api/projects/{project_id}/checklists/share-preview")
                # Only the selected export and exact preview POST read data.
                # Actual cloud writers/receipts reject before body parsing.
                if config.is_cloud() and (self.name == "operation" or
                        request.method not in {"GET", "HEAD", "OPTIONS"}
                        and self.name != "export_selected" and not preview_post and not checklist_preview_post):
                    personal.require_local_write()
                if preview_post:
                    personal_share_preview.require_enabled()
                if checklist_preview_post:
                    personal_checklist_share_preview.require_enabled()
                return await original(request)
            except personal.PersonalError as exc:
                return error_response(exc)
            except RequestValidationError:
                return error_response(personal.PersonalError("请求参数无效", "INVALID_REQUEST", 422))
        return handler


router = APIRouter(prefix="/api", route_class=PersonalRoute)


async def body(request: Request, *, share_preview=False):
    content = bytearray()
    async for chunk in request.stream():
        content.extend(chunk)
        if len(content) > 256 * 1024:
            raise personal.PersonalError("请求超过 256 KiB", "INVALID_REQUEST", 413)
    try:
        value = personal_share_preview.decode_body(content) if share_preview else json.loads(content)
        if not isinstance(value, dict):
            raise ValueError()
        return value
    except (ValueError, UnicodeError):
        raise personal.PersonalError("请求必须是 JSON 对象") from None


def mutation(result):
    data, status = result
    return JSONResponse({**data, "personal_read_version": None}, status_code=status, headers=private_headers())


@router.get("/projects/default")
def default_project(request: Request):
    return read(personal.default_project, request=request, gate=False, allow_disabled=True)


@router.patch("/projects/{project_id}")
async def patch_project(project_id: str, request: Request):
    return mutation(await run_in_threadpool(personal.update_project, project_id, await body(request)))


@router.get("/projects/{project_id}/decision-cards")
def cards(project_id: str, request: Request, offset: int = 0, limit: int = 30, state: str | None = None,
          space: str | None = None, stage: str | None = None):
    return read(lambda con: personal.list_entities(con, project_id, "card", offset=offset, limit=limit, state=state, space=space, stage=stage), request=request)


@router.post("/projects/{project_id}/decision-cards")
async def create_card(project_id: str, request: Request):
    return mutation(await run_in_threadpool(personal.create_card, project_id, await body(request)))


@router.get("/decision-cards/{card_id}")
def card(card_id: str, request: Request):
    return read(lambda con: {"card": personal.card_view(con, card_id)}, request=request)


@router.patch("/decision-cards/{card_id}")
async def patch_card(card_id: str, request: Request):
    return mutation(await run_in_threadpool(personal.update_card, card_id, await body(request)))


@router.get("/decision-cards/{card_id}/revisions")
def card_revisions(card_id: str, request: Request):
    return read(lambda con: {"revisions": personal.revisions(con, "card", card_id)}, request=request)


@router.post("/decision-cards/{card_id}/evidence")
async def refresh_evidence(card_id: str, request: Request):
    return mutation(await run_in_threadpool(personal.update_card_evidence, card_id, await body(request)))


@router.get("/decision-cards/{card_id}/export")
@router.get("/decision-cards/{card_id}/export.md")
def export_card(card_id: str, request: Request):
    result = read_result(lambda con: personal.export_cards(con, personal.require_entity(con, "card", card_id)["project_id"], [card_id]), request=request)
    return Response(result.data, media_type="text/markdown", headers={**private_headers(result.version), "Content-Disposition": 'attachment; filename="decision.md"'})


@router.post("/projects/{project_id}/decision-cards/export")
async def export_selected(project_id: str, request: Request):
    data = await body(request)
    if set(data) != {"card_ids"}:
        raise personal.PersonalError("导出只接受选定的 card_ids")
    result = await run_in_threadpool(read_result, lambda con: personal.export_cards(con, project_id, data["card_ids"]), request=request)
    return Response(result.data, media_type="text/markdown", headers={**private_headers(result.version), "Content-Disposition": 'attachment; filename="decisions.md"'})


@router.post("/projects/{project_id}/decision-cards/share-preview")
async def share_preview(project_id: str, request: Request):
    data = await body(request, share_preview=True)

    def produce():
        result = read_result(lambda con: personal_share_preview.snapshot_locked(con, project_id, data),
            request=request)
        # Every original SQL/source/factory exit succeeded before byte freezing.
        return ReadResult(personal_share_preview.finalize_snapshot(result.data), result.version)

    return json_response(await run_in_threadpool(produce))


@router.get("/projects/{project_id}/sources")
def sources(project_id: str, request: Request, offset: int = 0, limit: int = 30):
    return read(lambda con: personal.list_entities(con, project_id, "source", offset=offset, limit=limit), request=request)


@router.post("/projects/{project_id}/sources")
async def create_source(project_id: str, request: Request):
    return mutation(await run_in_threadpool(personal.create_source, project_id, await body(request)))


@router.get("/sources/{source_id}")
def source(source_id: str, request: Request):
    return read(lambda con: {"source": personal.source_view(con, source_id)}, request=request)


@router.patch("/sources/{source_id}")
async def patch_source(source_id: str, request: Request):
    return mutation(await run_in_threadpool(personal.update_source, source_id, await body(request)))


@router.get("/sources/{source_id}/revisions")
def source_revisions(source_id: str, request: Request):
    return read(lambda con: {"revisions": personal.revisions(con, "source", source_id)}, request=request)


@router.post("/sources/{source_id}/attachments")
async def upload(source_id: str, request: Request):
    return mutation(await attachments.receive_upload(request, source_id,
        request.headers.get("X-Operation-Id"), request.headers.get("X-Primary-Epoch")))


@router.get("/attachments/{attachment_id}/file")
async def attachment_file(attachment_id: str, request: Request):
    return await attachment_response(request, attachment_id)


@router.get("/operations/{operation_id}")
def operation(operation_id: str, request: Request):
    personal.require_local_write()
    operation_id = personal.valid_uuid(operation_id, "operation_id")

    def lookup(con):
        cap = personal.require_read(con)
        if not cap["instance_id"] or not cap["personal_primary_id"]:
            raise personal.PersonalError("个人数据主实例不可用", "PERSONAL_PRIMARY_UNAVAILABLE", 503)
        if cap["instance_id"] != cap["personal_primary_id"]:
            raise personal.PersonalError("回执仅可在当前主实例查询", "READ_ONLY_REPLICA", 403)
        result = personal.receipt(con, operation_id)
        if result is None:
            raise personal.PersonalError("尚未查到已提交回执；请保留原操作编号", "NOT_FOUND", 404)
        return result
    return read(lookup, request=request)
