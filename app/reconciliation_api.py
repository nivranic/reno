"""Read-only reconciliation endpoints, under the ordinary /api token gate."""
from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.routing import APIRoute

from reno import db, reconciliation


class ReconciliationRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except RequestValidationError:
                # FastAPI's default validation body repeats the rejected input;
                # a malformed filter may contain a credential-bearing URL.
                return JSONResponse({"detail": "对账查询参数无效",
                    "code": reconciliation.ReconciliationError.code}, status_code=400)
            except reconciliation.ReconciliationError as exc:
                return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
        return handler


router = APIRouter(prefix="/api", route_class=ReconciliationRoute)


def _parameters(request, *, export=False):
    allowed = {"reason", "scope", "token"} | (set() if export else {"offset", "limit"})
    if any(key not in allowed or len(request.query_params.getlist(key)) != 1 for key in request.query_params):
        raise reconciliation.ReconciliationError("对账查询参数无效")


@router.get("/reconciliation")
def reconciliation_page(request: Request, reason: str = "all", scope: str = "attention", offset: int = 0, limit: int = 30):
    _parameters(request)
    con = db.connect()
    try:
        data = reconciliation.query(con, reason=reason, scope=scope, offset=offset, limit=limit)
        return JSONResponse(data, headers={"Cache-Control": "no-store"})
    finally:
        con.close()


@router.get("/reconciliation/export")
def reconciliation_export(request: Request, reason: str = "all", scope: str = "attention"):
    _parameters(request, export=True)
    con = db.connect()
    try:
        content = reconciliation.export_markdown(con, reason=reason, scope=scope)
        return Response(content, media_type="text/markdown", headers={"Cache-Control": "no-store",
            "Content-Disposition": 'attachment; filename="reconciliation.md"', "X-Content-Type-Options": "nosniff"})
    finally:
        con.close()
