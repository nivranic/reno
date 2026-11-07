# -*- coding: utf-8 -*-
"""HTMX server-rendered pages: inbox/videos/detail/conflicts/search/reports."""
import json
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from reno import db, config, derivation, evidence_identity, review, sync_visibility

router = APIRouter()
TPL = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))


def ctx(request: Request, **kw):
    kw.setdefault("request", request)
    return kw


@router.get("/", response_class=HTMLResponse)
def index(request: Request):
    con = db.connect()
    try:
        con.execute("BEGIN")
        videos = [dict(r) for r in con.execute(
            f"""SELECT video_id, title, author, status, duration_ms, imported_at,
                      (SELECT COUNT(*) FROM knowledge_atom ka WHERE ka.video_id=va.video_id AND {sync_visibility.atom_sql('ka')}) atoms
               FROM video_asset va WHERE {sync_visibility.video_sql('va')} ORDER BY imported_at DESC LIMIT 100""")]
        job_where = (" WHERE video_id IS NULL OR EXISTS (SELECT 1 FROM video_asset va "
                     "WHERE va.video_id=job.video_id AND va.sync_state='ready')") if config.is_cloud() else ""
        jobs = [dict(r) for r in con.execute(
            "SELECT status, COUNT(*) c FROM job" + job_where + " GROUP BY status")]
        conflicts = con.execute("SELECT COUNT(*) c FROM conflict_case WHERE " + sync_visibility.conflict_sql()).fetchone()["c"]
        clusters = con.execute("SELECT COUNT(*) c FROM knowledge_cluster WHERE " + sync_visibility.cluster_sql()).fetchone()["c"]
        atoms_total = con.execute("SELECT COUNT(*) c FROM knowledge_atom WHERE " + sync_visibility.atom_sql()).fetchone()["c"]
        return TPL.TemplateResponse(request, "index.html", ctx(
            request, videos=videos, jobs={j["status"]: j["c"] for j in jobs},
            conflicts=conflicts, clusters=clusters, atoms_total=atoms_total))
    finally:
        con.close()


@router.get("/videos/{video_id}", response_class=HTMLResponse)
def video_detail(request: Request, video_id: str):
    con = db.connect()
    try:
        con.execute("BEGIN")
        asset = dict(db.get_asset(con, video_id, public=True) or {})
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
        con.execute("BEGIN")
        rows = [dict(r) for r in con.execute(
            "SELECT * FROM conflict_case WHERE " + sync_visibility.conflict_sql() + " ORDER BY conflict_id")]
        states = derivation.evaluate_relations(con, public=True)["conflicts"]
        projected = review.projection(con, public=True)
        for r in rows:
            r["derivation"] = states[r["conflict_id"]]
            target = evidence_identity.load_target(con, "conflict", r["conflict_id"])
            r["target_fingerprint"] = target[1] if target else None
            r["status"] = projected["conflicts"].get(r["conflict_id"], r["status"])
            r["side_a"] = json.loads(r.pop("side_a_json") or "{}")
            r["side_b"] = json.loads(r.pop("side_b_json") or "{}")
            r["analysis"] = json.loads(r.pop("analysis_json") or "{}")
        return TPL.TemplateResponse(request, "conflicts.html",
                                    ctx(request, conflicts=rows, cloud=config.is_cloud()))
    finally:
        con.close()


@router.get("/search", response_class=HTMLResponse)
def search_page(request: Request, q: str = ""):
    con = db.connect()
    try:
        con.execute("BEGIN")
        results = []
        if q:
            from reno.db import fts_prep
            try:
                ids = [r["atom_id"] for r in con.execute(
                    "SELECT atom_id FROM atom_fts JOIN knowledge_atom ka ON ka.id=atom_fts.atom_id "
                    "WHERE atom_fts MATCH ? AND " + sync_visibility.atom_sql("ka") + " LIMIT 200",
                    ('"' + fts_prep(q).strip().replace('"', '""') + '"',))]
            except Exception:
                ids = []
            for a in db.all_atoms(con, public=True):
                if a["id"] in ids:
                    results.append(a)
        cats = [r["category"] for r in con.execute(
            "SELECT DISTINCT category FROM knowledge_atom WHERE " + sync_visibility.atom_sql() + " ORDER BY category")]
        return TPL.TemplateResponse(request, "search.html",
                                    ctx(request, q=q, results=results, cats=cats))
    finally:
        con.close()


@router.get("/reports", response_class=HTMLResponse)
def reports_page(request: Request):
    from reno import report, storage_admin
    out = {}
    with storage_admin.connection() as con:
        con.execute("BEGIN")
        for name in ("summary", "incremental_diff", "checklist", "conflicts"):
            out[name] = report.present_report(name, con=con) or "(尚未生成,运行 `reno report`)"
        return TPL.TemplateResponse(request, "reports.html", ctx(request, reports=out))


@router.get("/collect", response_class=HTMLResponse)
def collect_page(request: Request):
    return TPL.TemplateResponse(request, "collect.html", ctx(request))
