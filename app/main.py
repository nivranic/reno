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

from reno import config  # noqa: F401  (ensures dirs exist)

APP_DIR = Path(__file__).parent
DIST = APP_DIR.parent / "frontend" / "dist"


def create_app() -> FastAPI:
    app = FastAPI(title="reno", docs_url=None, redoc_url=None)
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

    # Shared-token gate for /api when auth_token is configured (mobile H5 over
    # LAN is the use case; loopback-only setups leave it empty and keep the
    # old no-auth behavior). SPA shell/assets stay open - all data flows
    # through /api. Token travels as X-Reno-Token header or ?token= query
    # (media <video>/<img> tags cannot send headers).
    @app.middleware("http")
    async def token_gate(request, call_next):
        auth_token = config.get("auth_token", "")  # read per request
        if auth_token and request.url.path.startswith("/api"):
            provided = (request.headers.get("x-reno-token")
                        or request.query_params.get("token"))
            if provided != auth_token:
                from fastapi.responses import JSONResponse
                return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)

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
            # root-level build files (favicon.svg etc.) when they exist
            if full_path and ".." not in full_path.split("/"):
                candidate = (DIST / full_path).resolve()
                if candidate.is_file() and candidate.parent == DIST.resolve():
                    return FileResponse(candidate)
            if full_path and "." in full_path.rsplit("/", 1)[-1]:
                return JSONResponse({"detail": "not found"}, status_code=404)
            return FileResponse(DIST / "index.html")
    else:
        from .pages import router as pages_router
        app.include_router(pages_router)
    return app


app = create_app()
