"""Authenticated cloud sync transport over the S01–S03 domain contracts.

Each operation opens its own SQLite connection in a worker thread. A response
is produced only after commit; no await separates final authorization and
commit. Pairing is a local administrative CLI operation, never a token side
effect. The browser status route uses the ordinary Reno authentication gate.
"""
from __future__ import annotations

import base64
import binascii
from contextlib import contextmanager
import json
import re
import sqlite3
import threading
import time

import anyio
from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute
from starlette.concurrency import run_in_threadpool
from starlette.requests import ClientDisconnect

from reno import config, db, sync_events, sync_protocol, sync_queue, sync_transfer
from reno import storage_admin, storage_lifecycle, sync_upload_pins
from reno import sync_budget_wait, sync_source_control, sync_source_control_contract
from reno import migrations, sync_source_continuity_service, sync_source_readmission, sync_source_readmit_contract
from reno import sync_source_continuity_admin, sync_source_continuity_status_contract
from reno.sync_protocol import SyncError
from reno.sync_snapshot import MAX_SNAPSHOT_BYTES

MAX_JSON_BYTES = MAX_SNAPSHOT_BYTES + 64 * 1024
MAX_CONTROL_BYTES = 64 * 1024
MAX_MEDIA_HEADER = 8192
MAX_INTENT_BYTES = 4 * 1024 * 1024
VERIFY_TOTAL_SECONDS = 300
MEDIA_IDLE_SECONDS = 30
MEDIA_TOTAL_SECONDS = 3600
_uploads = threading.BoundedSemaphore(2)


def _invalid(message="Invalid sync request", status=400):
    raise SyncError(message, "INVALID_SYNC_REQUEST", status)


class SyncRoute(APIRoute):
    def get_route_handler(self):
        original = super().get_route_handler()

        async def handler(request):
            try:
                return await original(request)
            except (SyncError, storage_lifecycle.StorageLifecycleError) as exc:
                return JSONResponse({"detail": str(exc), "code": exc.code}, status_code=exc.status)
            except (TimeoutError, ClientDisconnect):
                return JSONResponse({"detail": "Sync request body was interrupted", "code": "SYNC_BODY_INTERRUPTED"},
                                    status_code=408)
            except (sqlite3.Error, OSError):
                return JSONResponse({"detail": "Sync storage is temporarily unavailable", "code": "SYNC_STORAGE_FAILED"},
                                    status_code=503)
        return handler


router = APIRouter(prefix="/api/sync", route_class=SyncRoute)
status_router = APIRouter(prefix="/api", route_class=SyncRoute)


def _fields(value, required, optional=()):
    if type(value) is not dict or not set(required) <= value.keys() or set(value) - set(required) - set(optional):
        _invalid("Sync request fields do not match the endpoint contract")
    return value


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _invalid("Duplicate sync JSON key")
        result[key] = value
    return result


def _decode(raw):
    try:
        value = json.loads(raw, object_pairs_hook=_pairs,
                           parse_constant=lambda _: _invalid("Non-finite sync JSON number"))
    except (UnicodeError, ValueError, RecursionError) as exc:
        if isinstance(exc, SyncError):
            raise
        _invalid("Invalid sync JSON")
    if type(value) is not dict:
        _invalid("Sync request must be a JSON object")
    return value


def _header(request, name, *, required=False):
    values = request.headers.getlist(name)
    if len(values) > 1 or (required and len(values) != 1):
        _invalid("Repeated or missing sync header")
    return values[0] if values else None


def _content_length(request, maximum):
    value = _header(request, "content-length")
    if value is None:
        return None
    if not value.isascii() or not value.isdigit() or len(value) > 20:
        _invalid("Invalid sync content length")
    size = int(value)
    if size > maximum:
        _invalid("Sync request body exceeds its limit", 413)
    return size


def _encoding(request, media_type):
    content_type = _header(request, "content-type", required=True)
    if content_type.split(";", 1)[0].strip().lower() != media_type:
        _invalid("Unsupported sync content type", 415)
    if _header(request, "content-encoding") not in (None, "identity"):
        _invalid("Encoded sync bodies are not supported", 415)


async def _body(request, *, maximum=MAX_CONTROL_BYTES):
    _encoding(request, "application/json")
    expected = _content_length(request, maximum)
    content = bytearray()
    with anyio.fail_after(60):
        async for chunk in request.stream():
            if len(content) + len(chunk) > maximum:
                _invalid("Sync request body exceeds its limit", 413)
            content.extend(chunk)
    if expected is not None and len(content) != expected:
        _invalid("Sync body does not match its declared length")
    return _decode(content)


def _authorized_commit(con, source, lease, claims=()):
    sync_queue.require_session(con, source, lease)
    current, _ = sync_queue._clock(None)
    session, _ = sync_queue._session(con, source, lease, current)
    for credential in claims:
        sync_queue._claim_record(con, session, credential, current)
    # All I/O/validation is finished. The database write reservation is still
    # held; commit immediately, then let the async caller serialize the result.
    con.commit()


def _transaction(body, function):
    con = db.connect()
    try:
        sync_queue.require_session(con, body["source"], body["lease"])
        result = function(con)
        claims = [entry["claim"] for entry in result.get("claims", [])]
        if body.get("kind") == "fail":
            claims.append(body["claim"])
        _authorized_commit(con, body["source"], body["lease"], claims)
        return result
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _budget_transaction(body):
    if migrations.SCHEMA_VERSION >= 17:
        return _registered_control_transaction(body, sync_budget_wait.apply_request)
    con = db.connect()
    try:
        result = sync_budget_wait.apply_request(con, body)
        if not result["replayed"]:
            # The original claim has been released; recheck only the live
            # session/fence before the immediate commit. Historical receipts
            # intentionally do not renew or require an expired session.
            sync_queue.require_session(con, body["source"], body["lease"])
            from reno import sync_recovery
            sync_recovery.assert_recovery_fence(con, body["recovery_generation"], body["recovery_stream"])
        con.commit()
        return result
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _source_transaction(body):
    if migrations.SCHEMA_VERSION >= 17:
        return _registered_control_transaction(body, sync_source_control.apply_request)
    con = db.connect()
    try:
        result = sync_source_control.apply_request(con, body)
        if not result["replayed"]:
            sync_queue.require_session(con, body["source"], body["lease"])
            from reno import sync_recovery
            sync_recovery.assert_recovery_fence(con, body["recovery_generation"], body["recovery_stream"])
        con.commit()
        return result
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


def _registered_control_transaction(body, function):
    profile = storage_admin.profile()
    historical = sync_source_continuity_service.legacy_receipt(profile=profile, original_request=body)
    if historical is not None:
        return historical
    with sync_source_continuity_service.writer(profile=profile,
            resume_request=("effects", body["request_id"], sync_source_continuity_service._sha(body))) as con:
        with sync_source_continuity_service.before_effect(con, profile=profile, original_request=body) as permit:
            result = permit.receipt if permit.receipt is not None else function(con, body)
            if not result["replayed"]:
                sync_queue.require_session(con, body["source"], body["lease"])
                from reno import sync_recovery
                sync_recovery.assert_recovery_fence(con, body["recovery_generation"], body["recovery_stream"])
            return result


def _source_control_body(body, kind):
    if body.get("kind") != kind:
        _invalid("Source control kind does not match its endpoint")
    try:
        return sync_source_readmit_contract.normalize_request(body)
    except sync_source_readmit_contract.SourceReadmitContractError:
        _invalid("Invalid source recovery control")


@router.post("/source/continuity")
async def source_continuity_status(request: Request):
    body = await _body(request, maximum=sync_source_continuity_status_contract.MAX_BYTES)
    try:
        body = sync_source_continuity_status_contract.normalize_request(body)
    except (ValueError, TypeError, KeyError):
        _invalid("Invalid source continuity query")
    return await run_in_threadpool(sync_source_continuity_admin.status,
                                  profile=storage_admin.profile(), request=body)


@router.post("/source/register")
async def source_register(request: Request):
    body = _source_control_body(await _body(request, maximum=sync_source_readmit_contract.MAX_CONTROL_BYTES), "source_register")
    return await run_in_threadpool(sync_source_continuity_service.register, profile=storage_admin.profile(), request=body)


@router.post("/source/dispose")
async def source_dispose(request: Request):
    body = _source_control_body(await _body(request, maximum=sync_source_readmit_contract.MAX_CONTROL_BYTES), "source_dispose")
    if body.get("format_version") == 2:
        from reno import sync_source_readmit_sql2
        consumer = sync_source_readmit_sql2.dispose
    else:
        consumer = sync_source_continuity_service.dispose
    return await run_in_threadpool(consumer, profile=storage_admin.profile(), request=body)


@router.post("/source/readmit")
async def source_readmit(request: Request):
    body = _source_control_body(await _body(request, maximum=sync_source_readmit_contract.MAX_READMIT_BYTES), "source_readmit")
    if body.get("format_version") == 2:
        from reno import sync_source_readmit_sql2
        consumer = sync_source_readmit_sql2.readmit
    else:
        consumer = sync_source_readmission.readmit
    return await run_in_threadpool(consumer, profile=storage_admin.profile(), request=body)


@router.get("/handshake")
def handshake():
    from reno.sync_access import identity
    con = db.connect()
    try:
        con.execute("BEGIN")
        return {"target": identity(con)}
    finally:
        con.close()


def _heartbeat(body):
    con = db.connect()
    try:
        result = sync_queue.heartbeat(con, body["source"], body["session_id"],
                                      lease=body.get("lease"), metrics=body.get("metrics"))
        _authorized_commit(con, body["source"], result["lease"])
        return result
    except BaseException:
        con.rollback()
        raise
    finally:
        con.close()


@router.post("/heartbeat")
async def heartbeat(request: Request):
    body = _fields(await _body(request), ("source", "session_id"), ("lease", "metrics"))
    return await run_in_threadpool(_heartbeat, body)


def _owned_claims(con, source, lease, limit):
    sync_queue._integer(limit, "claim_limit", minimum=0, maximum=sync_queue.MAX_CLAIM_BATCH)
    if limit == 0:
        return []
    current, stamp = sync_queue._clock(None)
    session, _ = sync_queue._session(con, source, lease, current)
    from reno import sync_recovery
    if sync_recovery.state(con)["state"] != "running":
        # Do not return already claimed work during a hold either: returning
        # it would tell a restarted home node to keep processing that queue.
        return []
    rows = con.execute("SELECT * FROM sync_queue WHERE status='claimed' AND owner_id=? "
                       "AND owner_generation=? AND lease_until>? ORDER BY item_id LIMIT ?",
                       (session["session_id"], session["generation"], stamp, limit)).fetchall()
    result = [{"item_id": row["item_id"], "url": row["url"], "attempts": row["attempts"],
               "lease_until": row["lease_until"], "claim": {"item_id": row["item_id"],
               "lease_token": row["lease_token"], "lease_generation": row["lease_generation"]}}
              for row in rows]
    if len(result) < limit:
        result.extend(sync_queue.claim(con, source, lease, limit=limit - len(result)))
    return result


def _pull(con, body):
    paging = _fields(body["events"], ("after", "limit", "through", "stream"))
    version = db.knowledge_version(con)
    stream = {key: version[key] for key in ("library_id", "library_epoch")}
    expected = paging["stream"]
    if expected is None:
        if paging["after"] != 0 or paging["through"] is not None:
            _invalid("A continued event scan requires its source stream identity")
    else:
        _fields(expected, ("library_id", "library_epoch"))
        sync_queue._uuid(expected["library_id"], "stream library_id")
        sync_queue._integer(expected["library_epoch"], "stream library_epoch")
        if expected != stream:
            raise SyncError("Event stream changed; restart the scan from zero", "SYNC_EVENT_STREAM_CHANGED", 409)
    page = sync_events.export_events(con, after=paging["after"], limit=paging["limit"], through=paging["through"])
    claims = _owned_claims(con, body["source"], body["lease"], body["claim_limit"])
    from reno import sync_recovery
    return {"events": page, "claims": claims, "recovery": sync_recovery.state(con)}


@router.post("/pull")
async def pull(request: Request):
    body = _fields(await _body(request), ("source", "lease", "events", "claim_limit"))
    return await run_in_threadpool(_transaction, body, lambda con: _pull(con, body))


def _push_snapshot(body):
    # File preparation must not reserve a database write lock. The domain
    # publisher reserves it only once files and the full closure are checked.
    with _protected_connection() as (con, profile):
        result = sync_transfer.publish_snapshot(con, body["source"], body["lease"], body["snapshot"],
                                                claims=body["claims"], recovery_generation=body["recovery_generation"],
                                                recovery_stream=body["recovery_stream"], profile=profile)
        from reno import sync_recovery
        sync_recovery.assert_publish_allowed(con, body["recovery_generation"], body["snapshot"]["version"], body["recovery_stream"])
        sync_queue.require_publication_generation(con, body["snapshot"]["publication_id"], body["recovery_generation"])
        _authorized_commit(con, body["source"], body["lease"], [entry["claim"] for entry in body["claims"]])
        return result


@contextmanager
def _protected_connection():
    profile = storage_admin.profile()
    with storage_lifecycle.shared(profile):
        con = db.connect()
        try:
            yield con, profile
        except BaseException:
            # A storage cleanup error must not replace the caller's failed
            # proof, cancellation or authorization error.
            try:
                con.rollback()
            except BaseException:
                pass
            try:
                con.close()
            except BaseException:
                pass
            raise
        else:
            con.close()


def _prepare_uploads(body):
    with _protected_connection() as (con, profile):
        result = sync_transfer.prepare_uploads(con, body["source"], body["lease"], binding=body["binding"],
                                               descriptors=body["descriptors"], profile=profile)
        # Freeze only: a large library must not trigger a single unbounded SHA
        # scan that restarts from zero after the client's control timeout.
        _authorized_commit(con, body["source"], body["lease"])
        for item in result["media"]:
            item["state"] = "reserved"  # No file was verified by preparation.
        return result


@router.post("/uploads/prepare")
async def prepare_uploads(request: Request):
    body = _fields(await _body(request, maximum=MAX_INTENT_BYTES), ("source", "lease", "binding", "descriptors"))
    return await run_in_threadpool(_prepare_uploads, body)


def _verify_upload(body):
    started = time.monotonic()

    def check_cancelled():
        anyio.from_thread.check_cancelled()
        if time.monotonic() - started >= VERIFY_TOTAL_SECONDS:
            raise SyncError("Media verification timed out", "SYNC_UPLOAD_TIMEOUT", 408)

    with _protected_connection() as (con, profile):
        stored = sync_transfer.confirm_cached_upload(con, body["source"], body["lease"], body["intent"],
            profile=profile, check_cancelled=check_cancelled)
        check_cancelled()
        _authorized_commit(con, body["source"], body["lease"])
        return {"pin_id": sync_upload_pins.intent_id(body["intent"]), "stored": stored}


@router.post("/uploads/verify")
async def verify_upload(request: Request):
    body = _fields(await _body(request), ("source", "lease", "intent"))
    return await run_in_threadpool(_verify_upload, body)


def _abandon_uploads(body):
    with _protected_connection() as (con, profile):
        result = sync_transfer.abandon_uploads(con, body["source"], body["lease"], binding=body["binding"],
                                               operation_id=body["operation_id"], profile=profile)
        _authorized_commit(con, body["source"], body["lease"])
        return result


@router.post("/uploads/abandon")
async def abandon_uploads(request: Request):
    body = _fields(await _body(request), ("source", "lease", "binding", "operation_id"))
    return await run_in_threadpool(_abandon_uploads, body)


def _publication_receipt(body):
    with _protected_connection() as (con, profile):
        # The domain takes a consistent read snapshot and authenticates its
        # session without BEGIN IMMEDIATE, claim checks or any SQL mutation.
        return sync_transfer.publication_receipt(con, body["source"], body["lease"],
            binding=body["binding"], payload_sha256=body["payload_sha256"], profile=profile)


@router.post("/uploads/receipt")
async def publication_receipt(request: Request):
    body = _fields(await _body(request), ("source", "lease", "binding", "payload_sha256"))
    return await run_in_threadpool(_publication_receipt, body)


@router.post("/push")
async def push(request: Request):
    body = await _body(request, maximum=MAX_JSON_BYTES)
    kind = body.get("kind")
    if kind in ("budget_defer", "budget_resume"):
        try:
            budget_size = len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            _invalid("Invalid queue budget control")
        if budget_size > sync_budget_wait.MAX_REQUEST_BYTES:
            _invalid("Queue budget control exceeds its limit", 413)
        return await run_in_threadpool(_budget_transaction, body)
    if kind == "source_fail":
        try:
            source_size = len(json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8"))
        except (UnicodeError, ValueError, TypeError, RecursionError):
            _invalid("Invalid source failure control")
        if source_size > sync_source_control_contract.MAX_CONTROL_BYTES:
            _invalid("Source failure control exceeds its limit", 413)
        return await run_in_threadpool(_source_transaction, body)
    if kind == "snapshot":
        _fields(body, ("source", "lease", "kind", "snapshot", "claims", "recovery_generation", "recovery_stream"))
        _fields(body["recovery_stream"], ("library_id", "library_epoch"))
        return await run_in_threadpool(_push_snapshot, body)
    if kind == "events":
        _fields(body, ("source", "lease", "kind", "events"))
        return await run_in_threadpool(_transaction, body, lambda con: sync_events.merge_events(con, body["events"]))
    if kind == "fail":
        _fields(body, ("source", "lease", "kind", "claim", "error_class", "recovery_generation", "recovery_stream"))
        _fields(body["recovery_stream"], ("library_id", "library_epoch"))
        return await run_in_threadpool(_transaction, body, lambda con: sync_queue.fail(
            con, body["source"], body["lease"], body["claim"], body["error_class"],
            recovery_generation=body["recovery_generation"], recovery_stream=body["recovery_stream"]))
    _invalid("Unsupported sync push kind")


def _media_metadata(request):
    value = _header(request, "x-sync-media", required=True)
    if not value.isascii() or len(value) > MAX_MEDIA_HEADER:
        _invalid("Invalid media descriptor header")
    if not re.fullmatch(r"[A-Za-z0-9_-]+={0,2}", value):
        _invalid("Invalid media descriptor header")
    try:
        raw = base64.b64decode(value.encode(), altchars=b"-_", validate=True)
    except (ValueError, binascii.Error):
        _invalid("Invalid media descriptor header")
    if base64.urlsafe_b64encode(raw).decode() != value:
        _invalid("Media descriptor header must use canonical base64url")
    body = _fields(_decode(raw), ("source", "lease", "intent"))
    body["intent"] = sync_upload_pins.normalize_intent(body["intent"])
    body["descriptor"] = body["intent"]["descriptor"]
    return body


@router.post("/media")
async def media(request: Request):
    _encoding(request, "application/octet-stream")
    body = _media_metadata(request)
    expected = _content_length(request, body["descriptor"]["byte_size"])
    if expected is not None and expected != body["descriptor"]["byte_size"]:
        _invalid("Media length does not match its descriptor")
    if not _uploads.acquire(blocking=False):
        raise SyncError("Media receiver is busy; retry later", "SYNC_UPLOAD_BUSY", 429)
    iterator = request.stream().__aiter__()
    started, received = time.monotonic(), 0

    async def next_chunk():
        nonlocal received
        if time.monotonic() - started >= MEDIA_TOTAL_SECONDS:
            raise SyncError("Media upload timed out", "SYNC_UPLOAD_TIMEOUT", 408)
        try:
            with anyio.fail_after(MEDIA_IDLE_SECONDS):
                try:
                    chunk = await anext(iterator)
                except StopAsyncIteration:
                    return None
        except TimeoutError:
            raise SyncError("Media upload timed out", "SYNC_UPLOAD_TIMEOUT", 408) from None
        except ClientDisconnect:
            raise SyncError("Media body was interrupted", "SYNC_BODY_INTERRUPTED", 408) from None
        received += len(chunk)
        if received > body["descriptor"]["byte_size"]:
            _invalid("Media body exceeds its descriptor", 413)
        return chunk

    def receive():
        def check_cancelled():
            anyio.from_thread.check_cancelled()
            if time.monotonic() - started >= MEDIA_TOTAL_SECONDS:
                raise SyncError("Media upload timed out", "SYNC_UPLOAD_TIMEOUT", 408)

        def chunks():
            while True:
                check_cancelled()
                chunk = anyio.from_thread.run(next_chunk)
                if chunk is None:
                    return
                if chunk:
                    # Keep the storage writer's individual writes bounded even
                    # if a server/test transport supplies an unusually big chunk.
                    for offset in range(0, len(chunk), 128 * 1024):
                        yield chunk[offset:offset + 128 * 1024]

        with _protected_connection() as (con, profile):
            sync_transfer.reserve_upload(con, body["source"], body["lease"], body["intent"], profile=profile)
            _authorized_commit(con, body["source"], body["lease"])
            result = sync_transfer.receive_media(con, body["source"], body["lease"], body["descriptor"], chunks(),
                                                 intent=body["intent"], profile=profile, check_cancelled=check_cancelled)
            _authorized_commit(con, body["source"], body["lease"])
            return result

    try:
        return await run_in_threadpool(receive)
    finally:
        _uploads.release()


@status_router.get("/sync-status")
def status(offset: int = 0, limit: int = 30):
    sync_queue._integer(offset, "offset", minimum=0)
    sync_queue._integer(limit, "limit", maximum=100)
    if not config.is_cloud():
        from reno.sync_home_status import summary as home_summary
        home = home_summary()
        return {"node_role": config.get("node_role", "all"), "mode": "home", "home": home,
                "configured": home["configured"], "compatible": None, "paired": None,
                "online": False, "transport": "https", "last_seen": None, "lease_until": None,
                "metrics": {}, "counts": {**dict.fromkeys(sync_queue.QUEUE_STATES, 0), "unknown": 0},
                "expired_claims": 0, "retry_due": 0,
                "queue": {"total": 0, "items": [], "offset": offset, "limit": limit}}
    con = db.connect()
    try:
        con.execute("BEGIN")
        from reno.sync_access import access_settings
        settings = access_settings()
        configured = bool(settings["node_role"] == "cloud" and settings["sync_token"])
        current, _ = sync_queue._clock(None)
        summary = sync_queue.summary(con, now=current)
        pair = con.execute("SELECT protocol_version,schema_fingerprint FROM sync_pair WHERE slot=1").fetchone()
        handshake = sync_protocol.schema_handshake(con)
        compatible = bool(pair and pair["protocol_version"] == handshake["protocol_version"]
                          and pair["schema_fingerprint"] == handshake["core_schema_sha256"])
        summary["online"] = bool(configured and compatible and summary["online"])
        return {"node_role": config.get("node_role", "all"), "mode": "cloud", "home": None, "configured": configured,
                "compatible": compatible, "transport": "https", **summary,
                "queue": sync_queue.list_items(con, offset=offset, limit=limit, now=current)}
    finally:
        con.close()
