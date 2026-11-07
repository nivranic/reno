# -*- coding: utf-8 -*-
"""Reno web app.

Two UIs share this app and the same /api:
- classic: the original Jinja2 server-rendered pages (rollback path)
- react:   the SPA built into frontend/dist (default when the build exists)

`web_ui` config key selects: "auto" (default, react if dist exists), "react",
"classic". See docs/frontend-migration.md for the rollback procedure.
"""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette._utils import get_route_path

from reno import config, db

APP_DIR = Path(__file__).parent
DIST = APP_DIR.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="reno", docs_url=None, redoc_url=None)

    # Schema changes belong to explicit setup, never to an arbitrary request.
    # Startup may create a new empty library, but upgrading an existing one is
    # an explicit, backed-up CLI operation (reno migrate-db).
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def lifespan(_app):
        try:
            if not config.db_path().exists():
                db.initialize()
            # Member setup validates schema, then creates/enrolls new roots.
            # Ordinary configuration reads and requests never perform setup.
            from reno import storage_admin
            from starlette.concurrency import run_in_threadpool
            await run_in_threadpool(storage_admin.setup)
            yield
        finally:
            from reno import worker
            from starlette.concurrency import run_in_threadpool
            # Keep the event loop responsive while in-flight work observes its
            # cancellation boundary. Never report shutdown while it can write.
            while not await run_in_threadpool(worker.stop_running, timeout=5):
                pass

    app.router.lifespan_context = lifespan

    from reno.migrations import SchemaVersionError
    from fastapi.responses import JSONResponse

    @app.exception_handler(SchemaVersionError)
    async def schema_error(_request, _exc):
        return JSONResponse({"detail": "知识库需要显式初始化或升级，请运行 reno init-db / migrate-db",
                             "code": "SCHEMA_UPGRADE_REQUIRED"}, status_code=503)
    from reno.storage_lifecycle import StorageLifecycleError

    @app.exception_handler(StorageLifecycleError)
    async def storage_error(_request, exc):
        return JSONResponse({"detail": "文件存储暂时不可用，请稍后重试或检查本机保护设施。",
                             "code": exc.code}, status_code=exc.status)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    # bookmarklet submissions arrive from the user's own browser pages
    # (douyin.com / space.bilibili.com) -> local reno API.
    # CORS origins are NOT authentication: this stays a loopback/LAN tool.
    from fastapi.middleware.cors import CORSMiddleware
    cors_origins = config.get("cors_origins", [
        "http://www.douyin.com", "https://www.douyin.com",
        "https://space.bilibili.com", "http://space.bilibili.com"])
    app.add_middleware(CORSMiddleware,
                       allow_origins=cors_origins,
                       allow_methods=["POST"], allow_headers=["*"])

    from .api import router as api_router
    app.include_router(api_router)
    from .task_api import router as task_router
    app.include_router(task_router)
    from .budget_api import router as budget_router
    app.include_router(budget_router)
    from .personal_api import router as personal_router
    app.include_router(personal_router)
    from .checklist_api import router as checklist_router
    app.include_router(checklist_router)
    from .sync_api import router as sync_router, status_router as sync_status_router
    app.include_router(sync_router)
    app.include_router(sync_status_router)
    from .personal_mirror_api import router as personal_mirror_router
    app.include_router(personal_mirror_router)
    from .personal_authority_api import router as personal_authority_router
    app.include_router(personal_authority_router)
    from .reconciliation_api import router as reconciliation_router
    app.include_router(reconciliation_router)

    # Shared-token gate for /api when auth_token is configured (mobile H5 over
    # LAN is the use case; loopback-only setups leave it empty and keep the
    # old no-auth behavior). SPA shell/assets stay open - all data flows
    # through /api. Token travels as X-Reno-Token header or ?token= query
    # (media <video>/<img> tags cannot send headers).
    # /api/sync/* is a separate credential (X-Sync-Token header only, never a
    # query param - it must not land in proxy logs); empty sync_token keeps
    # the sync surface closed (Phase 1 sync agent, docs/sync-plan.md §7).
    @app.middleware("http")
    async def token_gate(request, call_next):
        import hmac as _hmac
        from fastapi.responses import JSONResponse
        # Use the router's path after mount / ASGI root_path normalization.
        # Checking the public URL would let prefixed API routes skip both gates.
        path = get_route_path(request.scope)
        authority_status = path.rstrip("/") == "/api/personal/authority/status"
        if path == "/api/sync" or path.startswith("/api/sync/"):
            from reno.sync_access import require_sync_access
            from reno.sync_protocol import SyncError
            try:
                require_sync_access(request)
            except SyncError as exc:
                return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
            try:
                return await call_next(request)
            except Exception:
                return JSONResponse({"detail": "Sync request failed", "code": "SYNC_INTERNAL_ERROR"}, status_code=500)
        auth_token = config.get("auth_token", "")  # read per request
        if auth_token and path.startswith("/api"):
            provided = (request.headers.get("x-reno-token")
                        or request.query_params.get("token") or "")
            try:
                matches = _hmac.compare_digest(provided.encode("utf-8"), auth_token.encode("utf-8"))
            except (UnicodeError, AttributeError):
                matches = False
            if not matches:
                return JSONResponse({"detail": "unauthorized"}, status_code=401,
                    headers={"Cache-Control": "private, no-store"} if authority_status else None)
        try:
            response = await call_next(request)
        except Exception:
            if not authority_status:
                raise
            return JSONResponse({"detail": "个人状态暂不可用，请稍后重试", "code": "OBSERVATION_FAILED"},
                status_code=503, headers={"Cache-Control": "private, no-store"})
        if authority_status:
            response.headers["Cache-Control"] = "private, no-store"
        return response

    ui = config.get("web_ui", "auto")
    use_react = (ui == "react") or (ui == "auto" and DIST.exists())

    if use_react:
        from fastapi.responses import FileResponse, JSONResponse
        app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

        @app.get("/{full_path:path}", include_in_schema=False)
        def spa(full_path: str):
            # API misses must stay JSON errors, missing assets must 404 -
            # only app routes fall through to index.html.
            if full_path.startswith(("api/", "assets/", "static/")):
                return JSONResponse({"detail": "not found"}, status_code=404)
            # root-level build files (favicon.svg etc.) when they exist;
            # index.html must always revalidate or a rebuild ships a stale
            # shell pointing at old hashed assets (observed in testing)
            no_cache = {"Cache-Control": "no-cache"}
            if full_path and ".." not in full_path.split("/"):
                candidate = (DIST / full_path).resolve()
                if candidate.is_file() and candidate.parent == DIST.resolve():
                    return FileResponse(candidate, headers=no_cache)
            if full_path and "." in full_path.rsplit("/", 1)[-1]:
                return JSONResponse({"detail": "not found"}, status_code=404)
            return FileResponse(DIST / "index.html", headers=no_cache)
    else:
        from .pages import router as pages_router
        app.include_router(pages_router)
    return app


app = create_app()
