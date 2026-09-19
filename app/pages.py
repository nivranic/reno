# -*- coding: utf-8 -*-
"""HTMX server-rendered pages: inbox/videos/detail/conflicts/search/reports."""
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from reno import db

router = APIRouter()
TPL = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ctx(request: Request, **kw):
    kw.setdefault("request", request)
    return kw


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    con = db.connect()
    try:
        videos = [dict(r) for r in con.execute(
            """SELECT video_id, title, author, status, duration_ms, imported_at,
                      (SELECT COUNT(*) FROM knowledge_atom ka WHERE ka.video_id=va.video_id) atoms
               FROM video_asset va ORDER BY imported_at DESC LIMIT 100""")]
        jobs = [dict(r) for r in con.execute(
            "SELECT status, COUNT(*) c FROM job GROUP BY status")]
        conflicts = con.execute("SELECT COUNT(*) c FROM conflict_case").fetchone()["c"]
        clusters = con.execute("SELECT COUNT(*) c FROM knowledge_cluster").fetchone()["c"]
        atoms_total = con.execute("SELECT COUNT(*) c FROM knowledge_atom").fetchone()["c"]
        return TPL.TemplateResponse(request, "index.html", ctx(
            request, videos=videos, jobs={j["status"]: j["c"] for j in jobs},
            conflicts=conflicts, clusters=clusters, atoms_total=atoms_total))
    finally:
        con.close()


@router.get("/videos/{video_id}", response_class=HTMLResponse)
def video_detail(request: Request, video_id: str):
    con = db.connect()
    try:
        asset = dict(db.get_asset(con, video_id) or {})
        if not asset:
            return HTMLResponse("not found", status_code=404)
        return TPL.TemplateResponse(request, "detail.html", ctx(
            request, v=asset, files=json.loads(asset["files_json"] or "{}")))
    finally:
        con.close()


@router.get("/conflicts", response_class=HTMLResponse)
def conflicts_page(request: Request):
    con = db.connect()
    try:
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM conflict_case ORDER BY conflict_id")]
        for r in rows:
            r["side_a"] = json.loads(r.pop("side_a_json") or "{}")
            r["side_b"] = json.loads(r.pop("side_b_json") or "{}")
            r["analysis"] = json.loads(r.pop("analysis_json") or "{}")
        return TPL.TemplateResponse(request, "conflicts.html",
                                    ctx(request, conflicts=rows))
    finally:
        con.close()


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = ""):
    con = db.connect()
    try:
        results = []
        if q:
            from reno.db import fts_prep
            try:
                ids = [r["atom_id"] for r in con.execute(
                    "SELECT atom_id FROM atom_fts WHERE atom_fts MATCH ? LIMIT 200",
                    ('"' + fts_prep(q).strip().replace('"', '""') + '"',))]
            except Exception:
                ids = []
            for a in db.all_atoms(con):
                if a["id"] in ids:
                    results.append(a)
        cats = [r["category"] for r in con.execute(
            "SELECT DISTINCT category FROM knowledge_atom ORDER BY category")]
        return TPL.TemplateResponse(request, "search.html",
                                    ctx(request, q=q, results=results, cats=cats))
    finally:
        con.close()


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    gen = Path(__file__).parent.parent / "docs" / "generated"
    out = {}
    for name in ("incremental_diff.md", "checklist.md", "conflicts.md"):
        p = gen / name
        out[name.replace(".md", "")] = p.read_text(encoding="utf-8") if p.exists() else "(尚未生成,运行 `reno report`)"
    return TPL.TemplateResponse(request, "reports.html", ctx(request, reports=out))
