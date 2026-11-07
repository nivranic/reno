"""Read-only, visibility-scoped reconciliation of immutable review events.

This report never rebinds an atom identifier or changes review projections.
Counts describe records, not unique targets. A record becomes historical only
when a later, currently bound and valid review covers the same exact target.
"""
from __future__ import annotations

from contextlib import contextmanager
import html
import re
from urllib.parse import quote, urlsplit, urlunsplit
import uuid

from . import db, evidence_identity as identity, review, sync_visibility

REASONS = ("active", "stale", "missing", "legacy_unbound", "invalid_time")
SCOPES = ("attention", "history", "all")
_REASON_LABELS = {"active": "绑定有效", "stale": "内容已变化", "missing": "目标已缺失",
                  "legacy_unbound": "旧记录未绑定", "invalid_time": "时间无效"}
_SCOPE_LABELS = {"attention": "当前待核对", "history": "历史记录", "all": "全部记录"}
_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_VIDEO_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]{0,199}\Z")


class ReconciliationError(ValueError):
    code = "INVALID_RECONCILIATION_FILTER"
    status = 400


def validate_filters(reason="all", scope="attention", *, offset=0, limit=30):
    if not isinstance(reason, str) or reason not in ("all", *REASONS):
        raise ReconciliationError("复核原因筛选无效")
    if not isinstance(scope, str) or scope not in SCOPES:
        raise ReconciliationError("复核记录范围无效")
    if type(offset) is not int or not 0 <= offset <= 2**63 - 1:
        raise ReconciliationError("分页起点必须是非负整数")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ReconciliationError("每页记录数必须为 1 至 100")


@contextmanager
def _snapshot(con):
    # Releasing our nested savepoint must neither commit the caller's writes
    # nor replace an already established SQLite read snapshot.
    name = "reconcile_" + uuid.uuid4().hex
    con.execute(f"SAVEPOINT {name}")
    try:
        yield
    finally:
        con.execute(f"RELEASE SAVEPOINT {name}")


def _text(value, maximum=2000):
    if not isinstance(value, str):
        return None
    value = "".join(c for c in value if c in "\n\t" or ord(c) >= 32)

    def public_url(match):
        try:
            parsed = urlsplit(match.group())
            host = parsed.hostname
            if not host:
                return "[链接已隐藏]"
            authority = "[" + host + "]" if ":" in host else host
            if parsed.port is not None:
                authority += ":" + str(parsed.port)
            return urlunsplit((parsed.scheme, authority, parsed.path, "", ""))
        except ValueError:
            return "[链接已隐藏]"

    # Old free-text notes may contain source links. Their credentials, query
    # strings and fragments are unnecessary for a reconciliation summary.
    return _URL.sub(public_url, value)[:maximum]


def _summary(snapshot):
    if not isinstance(snapshot, dict):
        return None
    claim = _text(snapshot.get("claim"))
    if claim:
        return claim
    sides = []
    for key, label in (("side_a", "A"), ("side_b", "B")):
        side = snapshot.get(key)
        claim = _text(side.get("claim")) if isinstance(side, dict) else None
        if claim:
            sides.append(f"{label}：{claim}")
    return "；".join(sides)[:2000] or None


def _videos(snapshot):
    """Only inspect source identifiers in the known fingerprint schema."""
    if not isinstance(snapshot, dict):
        return set()
    found = {snapshot["video_id"]} if isinstance(snapshot.get("video_id"), str) else set()
    for ref in snapshot.get("evidence_refs", []) if isinstance(snapshot.get("evidence_refs", []), list) else []:
        if isinstance(ref, dict) and isinstance(ref.get("video_id"), str):
            found.add(ref["video_id"])
    for key in ("side_a", "side_b"):
        side = snapshot.get(key)
        if not isinstance(side, dict):
            continue
        if isinstance(side.get("video"), str):
            found.add(side["video"])
        for member in side.get("members", []) if isinstance(side.get("members", []), list) else []:
            if isinstance(member, dict):
                # Member content is an atom snapshot, not arbitrary recursion.
                content = member.get("content")
                if isinstance(content, dict) and isinstance(content.get("video_id"), str):
                    found.add(content["video_id"])
    return found


def _kind(event):
    atom, conflict = event.get("atom_id"), event.get("conflict_id")
    if bool(atom) == bool(conflict):
        return "unknown", None
    return ("atom", atom) if atom else ("conflict", conflict)


def _order(event):
    return review.event_order(event)


def _read(con):
    version = db.knowledge_version(con)
    # Same stream and local arrival-watermark semantics as sync_events export.
    # Reading it directly also supports deliberately unbound v0 records that
    # are unsuitable for wire export. No transport credentials are inspected.
    high = con.execute("SELECT COALESCE(MAX(decision_id),0) FROM user_decision").fetchone()[0]
    visibility = sync_visibility.cache_signature(con)
    events = review.review_history(con, public=True)
    ordered = [(event, _kind(event), _order(event)) for event in events]
    ordered.sort(key=lambda entry: entry[2], reverse=True)
    latest_active = {}
    for event, key, order in ordered:
        if (key[0] != "unknown" and order[0] and event["binding_status"] == "active"
                and review.decision_valid(event)):
            latest_active.setdefault(key, (event, order))
    current_cache, video_cache, items = {}, {}, []

    def video_link(video_id):
        if not isinstance(video_id, str) or not _VIDEO_ID.fullmatch(video_id) or video_id in (".", ".."):
            return None
        if video_id not in video_cache:
            row = con.execute("SELECT title FROM video_asset WHERE video_id=? AND " + sync_visibility.video_sql(),
                              (video_id,)).fetchone()
            video_cache[video_id] = ({"video_id": video_id, "url": "/videos/" + quote(video_id, safe=""),
                                      "title": _text(row["title"], 200)} if row is not None else None)
        return video_cache[video_id]

    def record_id(event):
        return event.get("event_id") or "decision:" + str(event["decision_id"])

    for event, key, order in ordered:
        later = latest_active.get(key)
        covered = bool(order[0] and later is not None and later[1] > order)
        if key not in current_cache:
            current_cache[key] = (identity.load_target(con, *key)
                                  if key[0] != "unknown" and sync_visibility.target_visible(con, *key) else None)
        current = current_cache[key]
        current_snapshot = current[0] if current else None
        saved = event.get("target_snapshot")
        historical_summary, current_summary = _summary(saved), _summary(current_snapshot)
        sources = [link for video_id in sorted(_videos(saved) | _videos(current_snapshot))
                   if (link := video_link(video_id)) is not None]
        target_url = None
        if current_snapshot is not None:
            if key[0] == "conflict":
                target_url = "/conflicts"
            elif (link := video_link(current_snapshot.get("video_id"))) is not None:
                target_url = link["url"]
        state = event["binding_status"]
        decision_valid = review.decision_valid(event)
        items.append({"record_id": record_id(event), "event_id": event.get("event_id"),
            "decision_id": event["decision_id"], "target_kind": key[0], "target_id": key[1],
            "binding_status": state, "attention_state": "historical" if covered else "current",
            "decision_valid": decision_valid,
            "needs_attention": (state != "active" or not decision_valid) and not covered,
            "action": _text(event.get("action"), 200) or "", "decided_at": _text(event.get("decided_at"), 200),
            "created_at": _text(event.get("created_at"), 200), "note": _text(event.get("note")),
            "title": (historical_summary or current_summary or "未绑定的旧复核记录")[:120],
            "snapshot_summary": historical_summary, "current_summary": current_summary,
            "target_url": target_url, "source_links": sources,
            "superseded_by": record_id(later[0]) if covered else None})
    summary = {"total": len(items), "needs_attention": 0, "historical": 0,
               "by_reason": {reason: {"total": 0, "current": 0, "historical": 0} for reason in REASONS}}
    for item in items:
        count = summary["by_reason"][item["binding_status"]]
        count["total"] += 1
        count[item["attention_state"]] += 1
        summary["needs_attention"] += int(item["needs_attention"])
        summary["historical"] += int(item["attention_state"] == "historical")
    return {"version": version, "event_watermark": {
        "stream": {key: version[key] for key in ("library_id", "library_epoch")}, "high_watermark": high},
        "visibility_signature": visibility, "evaluated_at": review.utc_now(), "summary": summary}, items


def _filtered(items, reason, scope):
    return [item for item in items if (reason == "all" or item["binding_status"] == reason)
            and (scope == "all" or scope == "attention" and item["needs_attention"]
                 or scope == "history" and item["attention_state"] == "historical")]


def query(con, *, reason="all", scope="attention", offset=0, limit=30):
    """Return one page and whole-visible-library counts from one read snapshot."""
    validate_filters(reason, scope, offset=offset, limit=limit)
    with _snapshot(con):
        metadata, items = _read(con)
        selected = _filtered(items, reason, scope)
        return {**metadata, "filters": {"reason": reason, "scope": scope}, "offset": offset, "limit": limit,
                "total": len(selected), "items": selected[offset:offset + limit]}


def _markdown_text(value):
    text = html.escape(value or "—", quote=False).replace("\r", "").replace("\n", " / ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", text)


def export_markdown(con, *, reason="all", scope="attention"):
    """Export ALL matching records, never a paginated or persisted report file."""
    validate_filters(reason, scope)
    with _snapshot(con):
        data, items = _read(con)
        items = _filtered(items, reason, scope)
        version, watermark, summary = data["version"], data["event_watermark"], data["summary"]
        lines = ["# 复核对账", "", "只读快照；统计按复核记录计数，不自动重绑旧目标。", "",
            f"- 筛选：{_REASON_LABELS.get(reason, '全部原因')} / {_SCOPE_LABELS[scope]}",
            f"- 知识版本：{version['library_id']} / epoch {version['library_epoch']} / revision {version['kb_revision']}",
            f"- 事件流：{watermark['stream']['library_id']} / epoch {watermark['stream']['library_epoch']} / 水位 {watermark['high_watermark']}",
            f"- 可见范围标识：{data['visibility_signature']}", f"- 评估时间：{data['evaluated_at']}",
            f"- 可见记录：{summary['total']}；当前待核对：{summary['needs_attention']}；历史记录：{summary['historical']}",
            f"- 本次导出：{len(items)} 条", "", "| 原因 | 全部 | 当前 | 历史 |", "| --- | ---: | ---: | ---: |"]
        for key, count in summary["by_reason"].items():
            lines.append(f"| {_REASON_LABELS[key]} | {count['total']} | {count['current']} | {count['historical']} |")
        if not items:
            lines.extend(("", "当前筛选下没有可见复核记录。"))
        for index, item in enumerate(items, 1):
            attention = "历史记录（后续有效复核已覆盖）" if item["attention_state"] == "historical" else (
                "当前待核对" if item["needs_attention"] else "当前有效绑定")
            lines.extend(("", f"## {index}. {_markdown_text(item['title'])}", "",
                f"- 记录：{_markdown_text(item['record_id'])}",
                f"- 目标：{item['target_kind']} / {_markdown_text(item['target_id'])}",
                f"- 状态：{_REASON_LABELS[item['binding_status']]}；{attention}",
                f"- 原决定有效性：{'有效' if item['decision_valid'] else '无效，需重新核对'}",
                f"- 原决定：{_markdown_text(item['action'])}；时间：{_markdown_text(item['decided_at'])}",
                f"- 旧快照摘要：{_markdown_text(item['snapshot_summary'])}",
                f"- 当前内容摘要：{_markdown_text(item['current_summary'])}",
                f"- 备注：{_markdown_text(item['note'])}"))
            if item["superseded_by"]:
                lines.append(f"- 覆盖记录：{_markdown_text(item['superseded_by'])}")
            if item["target_url"]:
                lines.append(f"- [查看当前来源或争议列表]({item['target_url']})")
            for link in item["source_links"]:
                lines.append(f"- [历史或当前来源：{_markdown_text(link['title'] or link['video_id'])}]({link['url']})")
        return "\n".join(lines) + "\n"
