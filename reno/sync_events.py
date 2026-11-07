"""Append-only knowledge review exchange, independent of transport and personal data.

The receiver allocates decision_id: it is a local arrival cursor, never a wire
identity or an event-ordering timestamp. Callers must commit successful merges.
These helpers neither migrate databases nor replace evidence or personal records.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta, timezone

from . import db, evidence_identity as identity, migrations, review
from .sync_protocol import SyncError

WIRE_FIELDS = (
    "atom_id", "conflict_id", "action", "revised_claim", "note", "created_at",
    "event_id", "client_id", "decided_at", "target_fingerprint",
    "target_snapshot_json", "event_schema_version",
)
MAX_EVENTS = 200
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_EVENT_BYTES = 300 * 1024
MAX_BATCH_BYTES = 4 * 1024 * 1024
MAX_CURSOR = 2**63 - 1
_UTC_MILLISECONDS = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
_FINGERPRINT = re.compile(r"v1:[0-9a-f]{64}\Z")


def _invalid(message, *, code="INVALID_SYNC_EVENT", status=400):
    raise SyncError(message, code, status)


def _bytes(value):
    try:
        return len(value.encode("utf-8"))
    except (AttributeError, UnicodeError):
        _invalid("Event text must contain valid UTF-8 characters")


def _text(value, name, maximum, *, nullable=True, nonempty=False):
    if value is None and nullable:
        return
    if not isinstance(value, str) or len(value) > maximum or (nonempty and not value.strip()):
        _invalid(f"Invalid event field: {name}")
    _bytes(value)


def _object_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("Duplicate snapshot JSON key")
        result[key] = value
    return result


def _snapshot(value):
    if not isinstance(value, str):
        _invalid("Snapshot must be a JSON string")
    if _bytes(value) > MAX_SNAPSHOT_BYTES:
        _invalid("Snapshot exceeds the event size limit", status=413)
    try:
        obj = json.loads(value, object_pairs_hook=_object_pairs,
                         parse_constant=lambda _: _invalid("Non-finite snapshot number"))
    except (ValueError, RecursionError) as exc:
        if isinstance(exc, SyncError):
            raise
        _invalid("Snapshot must be valid JSON")
    if not isinstance(obj, dict):
        _invalid("Snapshot must be a JSON object")
    stack, nodes = [(obj, 0)], 0
    while stack:
        part, depth = stack.pop()
        nodes += 1
        if depth > 16 or nodes > 16384:
            _invalid("Snapshot JSON is too complex", status=413)
        if isinstance(part, dict):
            for key, child in part.items():
                _bytes(key)
                stack.append((child, depth + 1))
        elif isinstance(part, list):
            stack.extend((child, depth + 1) for child in part)
        elif isinstance(part, str):
            _bytes(part)
    return obj


def _utc_time(value):
    if not isinstance(value, str) or not _UTC_MILLISECONDS.fullmatch(value):
        _invalid("Version 1 event times must be UTC millisecond timestamps")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _invalid("Invalid event timestamp")
    if parsed > datetime.now(timezone.utc) + timedelta(minutes=5):
        _invalid("Event timestamp is too far in the future")


def _validate_event(raw):
    if not isinstance(raw, dict) or raw.keys() != set(WIRE_FIELDS):
        _invalid("Review events require the exact wire fields; local and personal fields are forbidden")
    event = dict(raw)
    version = event["event_schema_version"]
    if type(version) is not int or version not in (0, 1):
        _invalid("Unsupported review event schema", code="UNSUPPORTED_EVENT_SCHEMA", status=409)
    try:
        canonical_id = str(uuid.UUID(event["event_id"]))
    except (AttributeError, TypeError, ValueError):
        _invalid("event_id must be a canonical UUID")
    if canonical_id != event["event_id"]:
        _invalid("event_id must be a canonical UUID")
    for name in ("atom_id", "conflict_id"):
        _text(event[name], name, 200, nonempty=version == 1)
    _text(event["client_id"], "client_id", 200, nullable=False)
    _text(event["action"], "action", 200)
    _text(event["note"], "note", 2000)
    _text(event["revised_claim"], "revised_claim", 20000)
    _text(event["created_at"], "created_at", 200)
    _text(event["decided_at"], "decided_at", 200)
    _text(event["target_fingerprint"], "target_fingerprint", 100)
    snapshot = _snapshot(event["target_snapshot_json"])
    if version == 1:
        if bool(event["atom_id"]) == bool(event["conflict_id"]):
            _invalid("Version 1 review events require exactly one target")
        actions = review.ATOM_ACTIONS if event["atom_id"] else review.CONFLICT_ACTIONS
        if event["action"] not in actions or event["revised_claim"] is not None:
            _invalid("Invalid version 1 review action")
        _utc_time(event["created_at"])
        _utc_time(event["decided_at"])
        fingerprint = event["target_fingerprint"]
        if (not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint)
                or type(snapshot.get("fingerprint_version")) is not int
                or snapshot["fingerprint_version"] != 1):
            _invalid("Version 1 events require a versioned snapshot fingerprint")
        try:
            actual = identity.fingerprint(snapshot)
        except (ValueError, TypeError, RecursionError, OverflowError):
            _invalid("Invalid snapshot content")
        if actual != fingerprint:
            _invalid("Event fingerprint does not match its original snapshot")
    else:
        # This is exactly the v0 migration shape. Original created_at can be
        # naive/invalid/NULL; migration leaves decided_at NULL in that case.
        # Do not manufacture a timezone, a current fingerprint or a new target.
        expected = {"binding_status": "legacy_unbound", "atom_id": event["atom_id"],
                    "conflict_id": event["conflict_id"], "created_at": event["created_at"]}
        try:
            legacy_time = migrations._legacy_time(event["created_at"])
        except (ValueError, OverflowError):
            _invalid("Legacy event time cannot be represented")
        if (event["client_id"] != "legacy" or event["target_fingerprint"] is not None
                or snapshot != expected
                or event["decided_at"] != legacy_time):
            _invalid("Legacy event does not match the migration history contract")
    size = _bytes(json.dumps(event, ensure_ascii=False, separators=(",", ":"), allow_nan=False))
    if size > MAX_EVENT_BYTES:
        _invalid("Review event exceeds the size limit", status=413)
    return event, size


def _cursor(value, name):
    if type(value) is not int or not 0 <= value <= MAX_CURSOR:
        _invalid(f"{name} must be a non-negative SQLite sequence", code="INVALID_SYNC_CURSOR")


def export_events(con, *, after=0, limit=200, through=None) -> dict:
    """Export one bounded page using local arrival order and a fixed watermark.

    Pass the first page's high_watermark as through on subsequent pages. Events
    arriving later (even with old decided_at) belong to the next scan. A changed
    stream library_id/epoch requires the future transport client to rescan.
    """
    _cursor(after, "after")
    if type(limit) is not int or not 1 <= limit <= MAX_EVENTS:
        _invalid(f"limit must be between 1 and {MAX_EVENTS}", code="INVALID_SYNC_CURSOR")
    if through is not None:
        _cursor(through, "through")
    savepoint = "sync_export_" + uuid.uuid4().hex
    con.execute(f"SAVEPOINT {savepoint}")
    try:
        version = db.knowledge_version(con)
        current = con.execute("SELECT COALESCE(MAX(decision_id),0) FROM user_decision").fetchone()[0]
        high = current if through is None else through
        if high > current or after > high:
            _invalid("Review cursor is outside this stream; rescan after checking its identity",
                     code="SYNC_CURSOR_OUT_OF_RANGE", status=409)
        rows = con.execute(
            "SELECT * FROM user_decision WHERE decision_id>? AND decision_id<=? "
            "ORDER BY decision_id LIMIT ?", (after, high, limit)).fetchall()
        events, size, cursor = [], 0, after
        for row in rows:
            event, event_size = _validate_event({key: row[key] for key in WIRE_FIELDS})
            if events and size + event_size > MAX_BATCH_BYTES:
                break
            events.append(event)
            size += event_size
            cursor = row["decision_id"]
        more = con.execute("SELECT 1 FROM user_decision WHERE decision_id>? AND decision_id<=? LIMIT 1",
                           (cursor, high)).fetchone() is not None
        return {"events": events, "next_cursor": cursor if more else high,
                "high_watermark": high, "has_more": more,
                "stream": {"library_id": version["library_id"], "library_epoch": version["library_epoch"]}}
    finally:
        con.execute(f"RELEASE SAVEPOINT {savepoint}")


def merge_events(con, rows) -> dict:
    """Validate and merge a whole batch, then replay within the caller transaction.

    Duplicate UUIDs must preserve every wire field, including the original JSON
    text. No timestamp normalization or snapshot reconstruction is performed.
    A bad late row or conflicting UUID rolls back only this batch; prior caller
    writes survive. Success remains uncommitted, even with an initially idle con.
    """
    if not isinstance(rows, list) or len(rows) > MAX_EVENTS:
        _invalid(f"events must be a list of at most {MAX_EVENTS} records", status=413)
    events, total = [], 0
    for row in rows:
        event, size = _validate_event(row)
        total += size
        if total > MAX_BATCH_BYTES:
            _invalid("Review batch exceeds the size limit", status=413)
        events.append(event)
    if not events:
        return {"received": 0, "inserted": 0, "duplicates": 0, "projection_changed": False}
    started = not con.in_transaction
    if started:
        con.execute("BEGIN")  # Releasing an outermost savepoint would commit.
    savepoint = "sync_merge_" + uuid.uuid4().hex
    con.execute(f"SAVEPOINT {savepoint}")
    try:
        inserted, duplicates = 0, 0
        for event in events:
            prior = con.execute("SELECT * FROM user_decision WHERE event_id=?", (event["event_id"],)).fetchone()
            if prior is not None:
                if any(prior[key] != event[key] for key in WIRE_FIELDS):
                    _invalid("Event UUID already identifies a different immutable payload",
                             code="IDEMPOTENCY_KEY_REUSED", status=409)
                duplicates += 1
                continue
            con.execute(f"INSERT INTO user_decision ({','.join(WIRE_FIELDS)}) "
                        f"VALUES ({','.join('?' for _ in WIRE_FIELDS)})",
                        tuple(event[key] for key in WIRE_FIELDS))
            inserted += 1
        projected = review.replay(con)
        if inserted and not projected["changed"]:
            # A new stale/unbound/missing-target event can change review history
            # and needs_recheck without changing the compatibility status column.
            db.bump_kb_revision(con)
        con.execute(f"RELEASE SAVEPOINT {savepoint}")
        return {"received": len(events), "inserted": inserted, "duplicates": duplicates,
                "projection_changed": projected["changed"]}
    except BaseException:
        con.execute(f"ROLLBACK TO SAVEPOINT {savepoint}")
        con.execute(f"RELEASE SAVEPOINT {savepoint}")
        if started:
            con.rollback()
        raise
