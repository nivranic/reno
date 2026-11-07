"""Read-only, allowlisted browser projection of the home syncer's private state.

No database, worker, state initialization, pairing or remote request belongs to
this path. A persisted lease is not proof of a running process or current pair.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import uuid

from . import config, sync_client
from .sync_media import _Directory
from .sync_protocol import SyncError
from .sync_state import StateStore

STATES = frozenset(("idle", "pulling", "pushing_reviews", "uploading", "publishing",
                    "published", "processing", "waiting_lease", "retry_wait", "review_backlog",
                    "recovery_held", "recovery_resuming", "recovery_blocked", "budget_wait",
                    "pending_delivery", "source_blocked", "source_failed"))
# Only known codes may leave the private state. Do not accept a prefix: a token
# or arbitrary internal string can itself be a syntactically valid state label.
ERROR_CODES = sync_client._REMOTE_CODES | frozenset("SYNC_CLIENT_" + name for name in sync_client._ERRORS) | frozenset("""
SYNC_CANCELLED SYNC_HEARTBEAT_LOST SYNC_PROCESSING_FAILED SYNC_LOCAL_MEDIA_MISSING
SYNC_LOCAL_MEDIA_UNSAFE SYNC_WORKER_LOCK_REQUIRED SYNC_DISABLED SYNC_HOME_REQUIRED
SYNC_TARGET_NOT_CONFIGURED SYNC_INTERVAL_INVALID SYNC_SOURCE_INVALID SYNC_SOURCE_TIMEOUT
SYNC_SOURCE_NETWORK SYNC_SOURCE_DNS_REJECTED SYNC_SOURCE_UNAVAILABLE SYNC_SOURCE_RATE_LIMITED
SYNC_SOURCE_MEDIA_MISMATCH SYNC_SOURCE_MAP_STORAGE SYNC_SOURCE_MAP_CORRUPT
SYNC_SOURCE_MAPPING_MISSING SYNC_STATE_MISSING SYNC_STATE_BINDING_MISMATCH
SYNC_STATE_EPOCH_MISMATCH SYNC_STATE_STORAGE_ERROR SYNC_STATE_UNSAFE_PATH INVALID_SYNC_STATE
SYNC_OUTBOX_MISSING SYNC_OUTBOX_CORRUPT SYNC_OUTBOX_CONFLICT
SYNC_RECOVERY_SOURCE_EPOCH_BEHIND SYNC_RECOVERY_LOCAL_WORK_INVALID SYNC_STATE_RECOVERY_CONFLICT
SYNC_SOURCE_EXECUTION_BLOCKED SYNC_BUDGET_DELIVERY_PENDING
SYNC_SOURCE_FAILED SYNC_SOURCE_CONTROL_DELIVERY_PENDING
SYNC_REVIEW_WAIT_SYNC_FAILED
""".split())


def _configuration():
    # config.get() can initialize mutable directories on its first load. Mirror
    # the cached options (or a read-only initial snapshot) without causing that.
    with config._config_lock:
        if isinstance(config._cache, dict):
            values = dict(config._cache)
        else:
            values = {**config.DEFAULTS, **config._read_local(config.local_path())}
            for key in values:
                name = "RENO_" + key.upper()
                if name in os.environ:
                    values[key] = os.environ[name]
    # Credentials rotate independently of normal cached runtime options.
    values.update(config.sync_access_settings())
    return values


def _target(settings):
    token = settings.get("sync_token")
    if type(token) is not str or not 0 < len(token) <= 4096 or any(not 33 <= ord(char) <= 126 for char in token):
        return None
    try:
        target = {}
        for key in ("library_id", "node_id"):
            value = settings.get("sync_target_" + key)
            if type(value) is not str or str(uuid.UUID(value)) != value:
                return None
            target["target_" + key] = value
        interval = settings.get("sync_interval_seconds")
        if type(interval) is not int or not 15 <= interval <= 3600:
            return None
        url, http, loopback = sync_client._base_url(settings.get("sync_cloud_url"))
        flag = settings.get("sync_allow_insecure_test_transport")
        enabled = flag is True or type(flag) is str and flag.lower() in {"true", "1"}
        if http and not (loopback and enabled and config.is_isolated_qa()):
            return None
        target["target_url"] = url
        return target
    except (SyncError, ValueError, TypeError, AttributeError):
        return None


def _local_node(data_dir):
    # Do not call node_identity(create=True), db.connect(), or load attachments.
    with _Directory(data_dir) as directory:
        with directory.open_file("node-identity.json") as stream:
            raw = stream.read(4097)
        directory.verify()
    if len(raw) > 4096:
        raise ValueError
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError
            result[key] = value
        return result
    value = json.loads(raw, object_pairs_hook=unique)
    if type(value) is not dict or type(value.get("instance_id")) is not str:
        raise ValueError
    node_id = value["instance_id"]
    if str(uuid.UUID(node_id)) != node_id:
        raise ValueError
    return node_id


def summary(*, data_dir=None, now=None):
    """Return exactly the public home DTO; every failure has a fixed code."""
    result = {"enabled": False, "configured": False, "state": "state_error",
              "updated_at": None, "last_success_at": None, "retry_at": None,
              "error_code": None, "seeded": False, "has_pending_publication": False,
              "work_phase": None, "lease_valid": False}
    try:
        settings = _configuration()
        result["enabled"] = settings.get("sync_enabled") is True
        target = _target(settings)
        result["configured"] = target is not None
        if not result["enabled"]:
            result["state"] = "disabled"
            return result
        if target is None:
            result["state"] = "unconfigured"
            return result
        root = Path(config.DATA if data_dir is None else data_dir).absolute()
        try:
            state = StateStore(root).read()
        except SyncError as exc:
            if exc.code == "SYNC_STATE_MISSING":
                result["state"] = "not_started"
                return result
            raise
        if (any(state["binding"][key] != value for key, value in target.items())
                or state["binding"]["home_node_id"] != _local_node(root)):
            result["error_code"] = "SYNC_STATE_BINDING_MISMATCH"
            return result
        status = state["status"]
        code = status["error_code"]
        lease = state["lease"]
        current = now if now is not None else datetime.now(timezone.utc)
        lease_valid = bool(lease and lease["session_id"] == state["session_id"]
                           and datetime.fromisoformat(lease["lease_until"].replace("Z", "+00:00")) > current)
        # Explicit projection only: no private DTO object is ever returned.
        result.update(state=status["state"] if status["state"] in STATES else "unknown",
                      updated_at=status["updated_at"], last_success_at=status["last_success_at"],
                      retry_at=status["retry_at"], error_code=code if code is None or code in ERROR_CODES else "SYNC_UNKNOWN_ERROR",
                      seeded=state["seeded"], has_pending_publication=state["pending_publication"] is not None,
                      work_phase=state["work"]["phase"] if state["work"] is not None else None,
                      lease_valid=lease_valid)
        recovery = state["recovery"]
        if recovery is not None and recovery["state"] == "held":
            result["state"] = "recovery_held"
        elif (recovery is not None and recovery["state"] == "resume_pending"
              and result["state"] != "recovery_blocked"):
            result["state"] = "recovery_resuming"
        return result
    except Exception:
        # Configuration/file/parser/OS failures must neither initialize state
        # nor disclose exception strings, paths, URLs or credential fragments.
        result.update(state="state_error", error_code="SYNC_STATUS_UNAVAILABLE")
        return result
