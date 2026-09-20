# -*- coding: utf-8 -*-
"""Reno web app: review UI is the product core (feasibility report §8)."""
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from reno import config  # noqa: F401  (ensures dirs exist)

APP_DIR = Path(__file__).parent


def create_app() -> FastAPI:
    app = FastAPI(title="reno", docs_url=None, redoc_url=None)
    app.mount("/static", StaticFiles(directory=APP_DIR / "static"), name="static")

    # bookmarklet submissions arrive from the user's own browser pages
    # (douyin.com / space.bilibili.com) -> local reno API
    from fastapi.middleware.cors import CORSMiddleware
    app.add_middleware(CORSMiddleware,
                       allow_origins=["http://www.douyin.com", "https://www.douyin.com",
                                      "https://space.bilibili.com", "http://space.bilibili.com"],
                       allow_methods=["POST"], allow_headers=["*"])

    from .api import router as api_router
    from .pages import router as pages_router
    app.include_router(api_router)
    app.include_router(pages_router)
    return app


app = create_app()
