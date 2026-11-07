"""Single-peer sync leases and durable URL queue; all writes are caller-owned.

An idle writer starts BEGIN IMMEDIATE, then a savepoint isolates each operation.
Successful calls never commit. A caller already in a transaction keeps earlier
writes even if a later operation fails. This module never registers publications:
only the server-side publication transaction may create that evidence.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
from pathlib import Path
import re
import sqlite3
import uuid
from urllib.parse import unquote, urlsplit

from . import db, personal_schema, sync_protocol as protocol
from .sync_protocol import SyncError

MAX_ATTEMPTS = 5
MAX_QUEUE_BATCH = 200
MAX_CLAIM_BATCH = 50
MAX_LEASE_SECONDS = 3600
RETRY_BASE_SECONDS = 30
RETRY_MAX_SECONDS = 1800
MAX_INTEGER = 2**63 - 1
RETRYABLE_ERRORS = frozenset({"network", "rate_limited", "quota", "processing_failed"})
PERMANENT_ERRORS = frozenset({"invalid_url", "source_unavailable", "media_invalid", "content_invalid", "auth_failed"})
ERROR_CLASSES = RETRYABLE_ERRORS | PERMANENT_ERRORS
QUEUE_STATES = ("queued", "claimed", "retry_wait", "budget_wait", "dead", "done")
COOKIE_STATES = frozenset({"unknown", "valid", "expired", "missing"})
_TIME_PATTERN = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def _error(message, code="INVALID_SYNC_REQUEST", status=400):
    raise SyncError(message, code, status)


def _integer(value, name, *, minimum=1, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        _error(f"Invalid {name}")
    return value


def _uuid(value, name):
    try:
        canonical = str(uuid.UUID(value))
    except (AttributeError, TypeError, ValueError):
        _error(f"{name} must be a canonical UUID")
    if value != canonical:
        _error(f"{name} must be a canonical UUID")
    return value


def _format_time(value):
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _clock(now):
    if now is None:
        now = datetime.now(timezone.utc)
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() != timedelta(0):
        _error("now must be an aware UTC datetime")
    now = now.astimezone(timezone.utc).replace(microsecond=(now.microsecond // 1000) * 1000)
    return now, _format_time(now)


def _stored_time(value):
    if not isinstance(value, str) or not _TIME_PATTERN.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _lease_end(now, seconds):
    _integer(seconds, "lease_seconds", maximum=MAX_LEASE_SECONDS)
    try:
        return _format_time(now + timedelta(seconds=seconds))
    except OverflowError:
        _error("Lease expiry cannot be represented")


def _metrics(value):
    if type(value) is not dict or set(value) - {"cookie_state", "disk_free_bytes", "queue_depth", "active_jobs"}:
        _error("Unsupported heartbeat metrics")
    for key, part in value.items():
        if key == "cookie_state":
            if not isinstance(part, str) or part not in COOKIE_STATES:
                _error("Invalid cookie_state metric")
        else:
            _integer(part, key, minimum=0,
                     maximum={"disk_free_bytes": MAX_INTEGER, "queue_depth": 1_000_000, "active_jobs": 1024}[key])
    return dict(value)


@contextmanager
def _operation(con):
    started = not con.in_transaction
    name = "sync_queue_" + uuid.uuid4().hex
    saved = False
    try:
        if started:
            con.execute("BEGIN IMMEDIATE")
        con.execute(f"SAVEPOINT {name}")
        saved = True
        if not started:
            # A caller's BEGIN may be deferred. Reserve write ownership before
            # sampling the authorization clock; this changes no rows or revision.
            con.execute("UPDATE sync_session SET slot=slot WHERE 0")
        yield
        con.execute(f"RELEASE SAVEPOINT {name}")
    except BaseException as exc:
        if saved:
            con.execute(f"ROLLBACK TO SAVEPOINT {name}")
            con.execute(f"RELEASE SAVEPOINT {name}")
        if started and con.in_transaction:
            con.rollback()
        if isinstance(exc, sqlite3.OperationalError) and (
                getattr(exc, "sqlite_errorcode", 0) & 0xFF) in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
            raise SyncError("Sync state is busy; retry the transaction", "SYNC_BUSY", 409) from None
        raise


def _local_identity(con):
    try:
        library_id = db.knowledge_version(con)["library_id"]
        path = con.execute("PRAGMA database_list").fetchone()[2]
        if not path:
            _error("A persistent node identity is required", "SYNC_IDENTITY_UNAVAILABLE", 409)
        node_id = personal_schema.node_identity(Path(path).parent)
        if node_id is None:
            _error("A local node identity is required", "SYNC_IDENTITY_UNAVAILABLE", 409)
        _uuid(library_id, "local library_id")
        _uuid(node_id, "local node_id")
        return library_id, node_id
    except (sqlite3.DatabaseError, OSError, KeyError, ValueError):
        _error("Local node identity is unavailable", "SYNC_IDENTITY_UNAVAILABLE", 409)


def _source(con, source):
    if type(source) is not dict or set(source) != {"library_id", "node_id", "handshake"}:
        _error("Source requires exactly library_id, node_id and handshake")
    _uuid(source["library_id"], "source library_id")
    _uuid(source["node_id"], "source node_id")
    protocol.validate_handshake(source["handshake"], protocol.schema_handshake(con))
    target_library, target_node = _local_identity(con)
    if source["library_id"] == target_library or source["node_id"] == target_node:
        _error("Source and target must be independent instances", "SYNC_SELF_PAIRING", 409)
    return target_library, target_node


def _pair(con, source):
    target_library, target_node = _source(con, source)
    row = con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone()
    if row is None:
        _error("Explicit peer binding is required", "SYNC_NOT_PAIRED", 409)
    if (row["source_library_id"] != source["library_id"] or row["source_node_id"] != source["node_id"]
            or row["target_library_id"] != target_library or row["target_node_id"] != target_node):
        _error("Peer does not match the stored binding", "SYNC_PEER_MISMATCH", 409)
    if (row["protocol_version"] != source["handshake"]["protocol_version"]
            or row["schema_fingerprint"] != source["handshake"]["core_schema_sha256"]):
        _error("Stored peer binding needs explicit maintenance", "INCOMPATIBLE_SYNC_SCHEMA", 409)
    return dict(row)


def bind_peer(con, source, *, now=None):
    """Explicitly bind once; equal retries are safe and different peers never win."""
    with _operation(con):
        _, stamp = _clock(now)
        target_library, target_node = _source(con, source)
        if con.execute("SELECT 1 FROM sync_pair WHERE slot=1").fetchone() is None:
            con.execute("INSERT INTO sync_pair(slot,source_library_id,source_node_id,target_library_id,"
                        "target_node_id,protocol_version,schema_fingerprint,paired_at) VALUES(1,?,?,?,?,?,?,?)",
                        (source["library_id"], source["node_id"], target_library, target_node,
                         source["handshake"]["protocol_version"], source["handshake"]["core_schema_sha256"], stamp))
        return _pair(con, source)


def refresh_peer_schema(con, source, *, now=None):
    """Explicit maintenance for the same peer only; never promotes another node."""
    with _operation(con):
        current, _ = _clock(now)
        target_library, target_node = _source(con, source)
        row = con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone()
        if row is None:
            _error("Existing peer binding is required", "SYNC_NOT_PAIRED", 409)
        if (row["source_library_id"] != source["library_id"] or row["source_node_id"] != source["node_id"]
                or row["target_library_id"] != target_library or row["target_node_id"] != target_node):
            _error("Schema refresh cannot change peer identities", "SYNC_PEER_MISMATCH", 409)
        session = con.execute("SELECT lease_until FROM sync_session WHERE slot=1").fetchone()
        expiry = _stored_time(session["lease_until"]) if session else None
        if expiry is not None and expiry > current:
            _error("Stop the active sync session before schema refresh", "SYNC_MAINTENANCE_BUSY", 409)
        for item in con.execute("SELECT lease_until FROM sync_queue WHERE status='claimed'"):
            expiry = _stored_time(item["lease_until"])
            if expiry is not None and expiry > current:
                _error("Wait for active queue claims before schema refresh", "SYNC_MAINTENANCE_BUSY", 409)
        con.execute("UPDATE sync_pair SET protocol_version=?,schema_fingerprint=? WHERE slot=1",
                    (source["handshake"]["protocol_version"], source["handshake"]["core_schema_sha256"]))
        return _pair(con, source)


def _lease(value):
    if type(value) is not dict or set(value) != {"session_id", "lease_token", "generation", "lease_until"}:
        _error("Session lease fields are invalid")
    _uuid(value["session_id"], "session_id")
    _uuid(value["lease_token"], "lease_token")
    _integer(value["generation"], "generation")
    if _stored_time(value["lease_until"]) is None:
        _error("Session lease expiry is invalid")
    return value


def _same_lease(row, lease):
    return (row["session_id"] == lease["session_id"] and row["generation"] == lease["generation"]
            and isinstance(row["lease_token"], str) and hmac.compare_digest(row["lease_token"], lease["lease_token"]))


def _session(con, source, lease, now):
    _lease(lease)
    pair = _pair(con, source)
    row = con.execute("SELECT * FROM sync_session WHERE slot=1").fetchone()
    if row is None or not _same_lease(row, lease):
        _error("Session lease does not match", "SYNC_LEASE_MISMATCH", 409)
    expiry = _stored_time(row["lease_until"])
    if expiry is None or expiry <= now:
        _error("Session lease has expired", "SYNC_LEASE_EXPIRED", 409)
    return dict(row), pair


def require_session(con, source, lease, *, now=None):
    """Hold transaction ownership while validating a later publication/write."""
    with _operation(con):
        current, _ = _clock(now)
        session, pair = _session(con, source, lease, current)
        return {**pair, "session_id": session["session_id"], "generation": session["generation"],
                "lease_until": session["lease_until"]}


def heartbeat(con, source, session_id, *, lease=None, metrics=None, now=None, lease_seconds=600):
    _integer(lease_seconds, "lease_seconds", maximum=MAX_LEASE_SECONDS)
    _uuid(session_id, "session_id")
    if lease is not None:
        _lease(lease)
        if lease["session_id"] != session_id:
            _error("Heartbeat session and lease disagree", "SYNC_LEASE_MISMATCH", 409)
    checked_metrics = _metrics(metrics) if metrics is not None else None
    with _operation(con):
        current, stamp = _clock(now)
        until = _lease_end(current, lease_seconds)
        _pair(con, source)
        row = con.execute("SELECT * FROM sync_session WHERE slot=1").fetchone()
        expiry = _stored_time(row["lease_until"]) if row else None
        active = expiry is not None and expiry > current
        if active:
            if row["session_id"] != session_id:
                _error("Another sync session owns the active lease", "SYNC_SESSION_BUSY", 409)
            if lease is None or not _same_lease(row, lease):
                _error("Renewal requires the original lease", "SYNC_LEASE_MISMATCH", 409)
            generation, token = row["generation"], row["lease_token"]
            until = max(until, row["lease_until"])
        else:
            if lease is not None:
                _error("Expired leases cannot be renewed; acquire a new session", "SYNC_LEASE_EXPIRED", 409)
            generation = (row["generation"] if row else 0) + 1
            _integer(generation, "generation")
            token = str(uuid.uuid4())
        if checked_metrics is None:
            metrics_json = row["metrics_json"] if row else "{}"
        else:
            metrics_json = json.dumps(checked_metrics, sort_keys=True, separators=(",", ":"))
        con.execute("INSERT INTO sync_session(slot,session_id,lease_token,generation,lease_until,updated_at,metrics_json) "
                    "VALUES(1,?,?,?,?,?,?) ON CONFLICT(slot) DO UPDATE SET session_id=excluded.session_id,"
                    "lease_token=excluded.lease_token,generation=excluded.generation,lease_until=excluded.lease_until,"
                    "updated_at=excluded.updated_at,metrics_json=excluded.metrics_json",
                    (session_id, token, generation, until, stamp, metrics_json))
        renewed = con.execute("UPDATE sync_queue SET lease_until=?,updated_at=? WHERE status='claimed' "
                              "AND owner_id=? AND owner_generation=? AND lease_until>?",
                              (until, stamp, session_id, generation, stamp)).rowcount if active else 0
        from . import sync_recovery
        return {"lease": {"session_id": session_id, "lease_token": token,
                          "generation": generation, "lease_until": until}, "renewed_claims": renewed,
                "recovery": sync_recovery.state(con)}


def enqueue(con, urls, *, now=None):
    if type(urls) is not list or len(urls) > MAX_QUEUE_BATCH:
        _error(f"URLs must be a list of at most {MAX_QUEUE_BATCH} entries")
    checked = [protocol.validate_ingest_url(url) for url in urls]
    if not checked:
        return 0
    with _operation(con):
        _, stamp = _clock(now)
        con.executemany("INSERT INTO sync_queue(url,status,attempts,created_at,updated_at) "
                        "VALUES(?,'queued',0,?,?)", ((url, stamp, stamp) for url in checked))
    return len(checked)


def _discard(con, item_id, error_class, stamp):
    con.execute("UPDATE sync_queue SET status='dead',last_error_class=?,updated_at=?,retry_at=NULL,"
                "owner_id=NULL,owner_generation=0,lease_token=NULL,lease_until=NULL WHERE item_id=?",
                (error_class, stamp, item_id))


def claim(con, source, lease, *, limit=1, now=None):
    _integer(limit, "limit", maximum=MAX_CLAIM_BATCH)
    with _operation(con):
        current, stamp = _clock(now)
        session, _ = _session(con, source, lease, current)
        from . import sync_recovery
        if sync_recovery.state(con)["state"] != "running":
            # Recovery does not consume attempts or turn expired suspended
            # claims into failures. The existing leases/history stay intact.
            return []
        con.execute("UPDATE sync_queue SET status=CASE WHEN attempts>=? THEN 'dead' ELSE 'retry_wait' END,"
                    "last_error_class='lease_expired',retry_at=CASE WHEN attempts>=? THEN NULL ELSE ? END,"
                    "owner_id=NULL,owner_generation=0,lease_token=NULL,lease_until=NULL,updated_at=? "
                    "WHERE status='claimed' AND (owner_id IS NOT ? OR owner_generation IS NOT ? "
                    "OR lease_until IS NULL OR lease_until<=?)",
                    (MAX_ATTEMPTS, MAX_ATTEMPTS, stamp, stamp, session["session_id"], session["generation"], stamp))
        claimed = []
        while len(claimed) < limit:
            row = con.execute("SELECT * FROM sync_queue WHERE status='queued' OR "
                              "(status='retry_wait' AND (retry_at IS NULL OR retry_at<=?)) "
                              "OR (status='budget_wait' AND retry_at IS NOT NULL AND retry_at<=?) "
                              "ORDER BY item_id LIMIT 1", (stamp, stamp)).fetchone()
            if row is None:
                break
            try:
                protocol.validate_ingest_url(row["url"])
            except SyncError:
                _discard(con, row["item_id"], "invalid_url", stamp)
                continue
            attempts = row["attempts"] if row["attempts"] is not None else 0
            generation = row["lease_generation"] if row["lease_generation"] is not None else 0
            if (type(attempts) is not int or not 0 <= attempts < MAX_ATTEMPTS
                    or type(generation) is not int or not 0 <= generation < MAX_INTEGER):
                _discard(con, row["item_id"], "processing_failed", stamp)
                continue
            token = str(uuid.uuid4())
            con.execute("UPDATE sync_queue SET status='claimed',attempts=?,owner_id=?,owner_generation=?,"
                        "lease_token=?,lease_generation=?,lease_until=?,retry_at=NULL,last_error_class=NULL,"
                        "video_id=NULL,publication_id=NULL,result_hash=NULL,updated_at=? WHERE item_id=?",
                        (attempts + 1, session["session_id"], session["generation"], token, generation + 1,
                         session["lease_until"], stamp, row["item_id"]))
            claimed.append({"item_id": row["item_id"], "url": row["url"], "attempts": attempts + 1,
                            "lease_until": session["lease_until"], "claim": {
                                "item_id": row["item_id"], "lease_token": token, "lease_generation": generation + 1}})
            con.execute("UPDATE sync_queue SET budget_wait_reason=NULL,budget_operation_id=NULL WHERE item_id=?",
                        (row["item_id"],))
        return claimed


def _claim_record(con, session, credential, now):
    if type(credential) is not dict or set(credential) != {"item_id", "lease_token", "lease_generation"}:
        _error("Task claim fields are invalid")
    _integer(credential["item_id"], "item_id")
    _integer(credential["lease_generation"], "lease_generation")
    _uuid(credential["lease_token"], "claim lease_token")
    row = con.execute("SELECT * FROM sync_queue WHERE item_id=?", (credential["item_id"],)).fetchone()
    if row is None:
        _error("Queue item does not exist", "SYNC_ITEM_NOT_FOUND", 404)
    if (row["owner_id"] != session["session_id"] or row["owner_generation"] != session["generation"]
            or row["lease_generation"] != credential["lease_generation"]
            or not isinstance(row["lease_token"], str)
            or not hmac.compare_digest(row["lease_token"], credential["lease_token"])):
        _error("Task claim no longer belongs to this session", "SYNC_CLAIM_MISMATCH", 409)
    expiry = _stored_time(row["lease_until"])
    if expiry is None or expiry <= now:
        _error("Task claim has expired", "SYNC_CLAIM_EXPIRED", 409)
    return dict(row)


def fail(con, source, lease, claim, error_class, *, now=None, recovery_generation=0, recovery_stream=None):
    if not isinstance(error_class, str) or error_class not in ERROR_CLASSES:
        _error("Failure must use a supported error class")
    with _operation(con):
        current, stamp = _clock(now)
        session, _ = _session(con, source, lease, current)
        from . import sync_recovery
        # The claim's frozen recovery fence is checked under the same write
        # reservation as its outcome. A delayed failure cannot consume work
        # paused by restore, even after a later resume has reached running.
        sync_recovery.assert_recovery_fence(con, recovery_generation, recovery_stream)
        row = _claim_record(con, session, claim, current)
        replayed = row["status"] in ("retry_wait", "dead") and row["last_error_class"] == error_class
        if not replayed and row["status"] != "claimed":
            _error("Task already has a different outcome", "SYNC_OUTCOME_MISMATCH", 409)
        if replayed:
            status, retry_at = row["status"], row["retry_at"]
        else:
            status = "retry_wait" if error_class in RETRYABLE_ERRORS and row["attempts"] < MAX_ATTEMPTS else "dead"
            retry_at = None
            if status == "retry_wait":
                seconds = min(RETRY_MAX_SECONDS, RETRY_BASE_SECONDS * 2 ** (row["attempts"] - 1))
                try:
                    retry_at = _format_time(current + timedelta(seconds=seconds))
                except OverflowError:
                    _error("Retry time cannot be represented")
            con.execute("UPDATE sync_queue SET status=?,last_error_class=?,retry_at=?,updated_at=? WHERE item_id=?",
                        (status, error_class, retry_at, stamp, row["item_id"]))
        return {"item_id": row["item_id"], "status": status, "attempts": row["attempts"],
                "retry_at": retry_at, "error_class": error_class, "replayed": replayed}


def publication_generation(con, publication_id):
    """Absent rows belong only to the pre-recovery generation-zero contract."""
    row = con.execute("SELECT generation FROM sync_publication_fence WHERE publication_id=?", (publication_id,)).fetchone()
    return row["generation"] if row is not None else 0


def require_publication_generation(con, publication_id, generation):
    _integer(generation, "recovery_generation", minimum=0)
    if publication_generation(con, publication_id) != generation:
        _error("Publication belongs to another recovery generation", "SYNC_RECOVERY_GENERATION_MISMATCH", 409)
    row = con.execute("SELECT local_version_json FROM sync_publication WHERE publication_id=?", (publication_id,)).fetchone()
    if row is None:
        _error("Matching server publication evidence is required", "SYNC_PUBLICATION_REQUIRED", 409)
    from . import sync_checkpoints
    try:
        published = sync_checkpoints._version(json.loads(row["local_version_json"]))
    except (SyncError, ValueError, TypeError, KeyError):
        _error("Server publication receiver version is invalid", "SYNC_PUBLICATION_REQUIRED", 409)
    current = db.knowledge_version(con)
    if any(published[key] != current[key] for key in ("library_id", "library_epoch")):
        _error("Publication belongs to another receiver stream", "SYNC_RECOVERY_STREAM_MISMATCH", 409)


def complete(con, source, lease, claim, *, publication_id, video_id, now=None, recovery_generation=0,
             recovery_stream=None):
    _uuid(publication_id, "publication_id")
    protocol.validate_video_id(video_id)
    with _operation(con):
        current, stamp = _clock(now)
        session, pair = _session(con, source, lease, current)
        row = _claim_record(con, session, claim, current)
        payload = {"publication_id": publication_id, "video_id": video_id,
                   "source_library_id": pair["source_library_id"], "source_node_id": pair["source_node_id"]}
        result_hash = hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if row["status"] == "done":
            if (row["publication_id"] != publication_id or row["video_id"] != video_id
                    or row["result_hash"] != result_hash):
                _error("Task already completed with another publication", "SYNC_OUTCOME_MISMATCH", 409)
        elif row["status"] != "claimed":
            _error("Task is not claimable for completion", "SYNC_OUTCOME_MISMATCH", 409)
        publication = con.execute("SELECT * FROM sync_published_video WHERE publication_id=? AND video_id=?",
                                  (publication_id, video_id)).fetchone()
        if (publication is None or publication["source_library_id"] != pair["source_library_id"]
                or publication["source_node_id"] != pair["source_node_id"]):
            _error("Matching server publication evidence is required", "SYNC_PUBLICATION_REQUIRED", 409)
        published_at = _stored_time(publication["committed_at"])
        if (type(publication["source_epoch"]) is not int or publication["source_epoch"] < 1
                or type(publication["source_revision"]) is not int or publication["source_revision"] < 0
                or not isinstance(publication["media_sha256"], str)
                or not _SHA256.fullmatch(publication["media_sha256"])
                or published_at is None or published_at > current):
            _error("Server publication evidence is incomplete", "SYNC_PUBLICATION_REQUIRED", 409)
        from . import sync_recovery
        sync_recovery.assert_publish_allowed(con, recovery_generation, {
            "library_id": publication["source_library_id"], "library_epoch": publication["source_epoch"],
            "kb_revision": publication["source_revision"]}, recovery_stream)
        require_publication_generation(con, publication_id, recovery_generation)
        binding = con.execute("SELECT * FROM sync_queue_publication WHERE item_id=? AND lease_generation=?",
                              (row["item_id"], row["lease_generation"])).fetchone()
        bound_at = _stored_time(binding["created_at"]) if binding else None
        if (binding is None or binding["lease_token"] != row["lease_token"]
                or binding["owner_id"] != session["session_id"] or binding["owner_generation"] != session["generation"]
                or binding["publication_id"] != publication_id or binding["video_id"] != video_id
                or bound_at is None or bound_at > current):
            _error("Publication must belong to this exact queue claim", "SYNC_PUBLICATION_REQUIRED", 409)
        if row["status"] == "done":
            return {"item_id": row["item_id"], "status": "done", **payload, "replayed": True}
        con.execute("UPDATE sync_queue SET status='done',video_id=?,publication_id=?,result_hash=?,"
                    "retry_at=NULL,last_error_class=NULL,updated_at=? WHERE item_id=?",
                    (video_id, publication_id, result_hash, stamp, row["item_id"]))
        return {"item_id": row["item_id"], "status": "done", **payload, "replayed": False}


def summary(con, *, now=None):
    """Read-only public status: no URLs, lease tokens, paths or raw errors."""
    current, stamp = _clock(now)
    name = "sync_summary_" + uuid.uuid4().hex
    con.execute(f"SAVEPOINT {name}")
    try:
        pair = con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone()
        session = con.execute("SELECT * FROM sync_session WHERE slot=1").fetchone()
        paired = False
        if pair:
            try:
                local = _local_identity(con)
                paired = (pair["target_library_id"], pair["target_node_id"]) == local
            except SyncError:
                pass
        expiry = _stored_time(session["lease_until"]) if session else None
        metrics = {}
        if session:
            try:
                metrics = _metrics(json.loads(session["metrics_json"]))
            except (ValueError, TypeError, RecursionError):
                pass
        counts = {state: 0 for state in QUEUE_STATES}
        counts["unknown"] = 0
        for row in con.execute("SELECT status,COUNT(*) AS count FROM sync_queue GROUP BY status"):
            counts[row["status"] if row["status"] in counts else "unknown"] += row["count"]
        expired = con.execute("SELECT COUNT(*) FROM sync_queue WHERE status='claimed' "
                              "AND (lease_until IS NULL OR lease_until<=? OR owner_id IS NOT ? OR owner_generation IS NOT ?)",
                              (stamp, session["session_id"] if session else None,
                               session["generation"] if session else None)).fetchone()[0]
        due = con.execute("SELECT COUNT(*) FROM sync_queue WHERE status='retry_wait' "
                          "AND (retry_at IS NULL OR retry_at<=?)", (stamp,)).fetchone()[0]
        budget_due = con.execute("SELECT COUNT(*) FROM sync_queue WHERE status='budget_wait' "
                                "AND retry_at IS NOT NULL AND retry_at<=?", (stamp,)).fetchone()[0]
        from . import sync_recovery
        recovery = sync_recovery.state(con)
        return {"paired": paired, "online": bool(paired and expiry is not None and expiry > current),
                "last_seen": session["updated_at"] if session and _stored_time(session["updated_at"]) else None,
                "lease_until": session["lease_until"] if expiry else None, "metrics": metrics,
                "counts": counts, "expired_claims": expired, "retry_due": due, "budget_due": budget_due,
                "recovery": {key: recovery[key] for key in ("state", "generation", "revision")}}
    finally:
        con.execute(f"RELEASE SAVEPOINT {name}")


def _display_time(value):
    # Pre-migration queue timestamps may have offsets or lack a timezone.
    if not isinstance(value, str) or len(value) > 40:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return value
    except ValueError:
        return None


def _source_parts(url):
    try:
        protocol.validate_ingest_url(url)
        parsed = urlsplit(url)
    except SyncError:
        return {"source_host": None, "source_path": None}
    path = parsed.path
    for _ in range(16):
        decoded = unquote(path)
        if decoded == path:
            break
        path = decoded
    path = path.split("?", 1)[0].split("#", 1)[0]
    return {"source_host": parsed.hostname, "source_path": path[:512]}


def list_items(con, *, offset=0, limit=30, now=None):
    """Paginated display DTO; raw URLs, ownership and receipt hashes stay private."""
    _integer(offset, "offset", minimum=0)
    _integer(limit, "limit", maximum=100)
    name = "sync_list_" + uuid.uuid4().hex
    con.execute(f"SAVEPOINT {name}")
    try:
        current, _ = _clock(now)
        session = con.execute("SELECT * FROM sync_session WHERE slot=1").fetchone()
        active_session = False
        if session is not None and con.execute("SELECT 1 FROM sync_pair WHERE slot=1").fetchone():
            try:
                _uuid(session["session_id"], "session_id")
                _uuid(session["lease_token"], "lease_token")
                _integer(session["generation"], "generation")
                expiry = _stored_time(session["lease_until"])
                active_session = expiry is not None and expiry > current
            except SyncError:
                pass
        total = con.execute("SELECT COUNT(*) FROM sync_queue").fetchone()[0]
        rows = con.execute("SELECT q.*,EXISTS(SELECT 1 FROM video_asset v WHERE v.video_id=q.video_id "
                           "AND v.sync_state='ready') AS video_ready "
                           "FROM sync_queue q ORDER BY q.item_id DESC LIMIT ? OFFSET ?", (limit, offset))
        items = []
        for row in rows:
            try:
                video_id = protocol.validate_video_id(row["video_id"]) if row["video_id"] is not None else None
            except SyncError:
                video_id = None
            claim_expiry = _stored_time(row["lease_until"])
            valid_claim = True
            try:
                _uuid(row["lease_token"], "claim lease_token")
                _integer(row["lease_generation"], "claim lease_generation")
            except SyncError:
                valid_claim = False
            claim_active = bool(row["status"] == "claimed" and active_session
                                and valid_claim
                                and claim_expiry is not None and claim_expiry > current
                                and row["owner_id"] == session["session_id"]
                                and row["owner_generation"] == session["generation"])
            items.append({"item_id": row["item_id"],
                          "status": row["status"] if row["status"] in QUEUE_STATES else "unknown",
                          "claim_active": claim_active,
                          "can_view_video": bool(row["status"] == "done" and video_id is not None and row["video_ready"]),
                          "attempts": row["attempts"] if type(row["attempts"]) is int and 0 <= row["attempts"] <= MAX_INTEGER else 0,
                          "last_error_class": row["last_error_class"] if row["last_error_class"] in ERROR_CLASSES | {"lease_expired"}
                          else ("unknown" if row["last_error_class"] else None),
                          "created_at": _display_time(row["created_at"]), "updated_at": _display_time(row["updated_at"]),
                          "retry_at": _display_time(row["retry_at"]), "video_id": video_id,
                          **_source_parts(row["url"])})
        return {"total": total, "items": items, "offset": offset, "limit": limit}
    finally:
        con.execute(f"RELEASE SAVEPOINT {name}")
