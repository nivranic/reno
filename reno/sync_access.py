"""Sync transport/authentication and explicit local identity administration.

HTTPS is determined only by the ASGI server's scheme. Proxy headers are never
parsed here; reno serve gives uvicorn an explicit, bounded trusted-proxy list.
An externally launched ASGI server must enforce the same proxy trust boundary.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hmac
import ipaddress
import json
from pathlib import Path
import re

from . import config, sync_protocol, sync_queue
from .sync_protocol import SyncError

MAX_TOKEN_BYTES = 4096
MAX_PREVIOUS_TOKEN_HOURS = 24
MAX_SOURCE_BYTES = 8192
_UTC_UNTIL = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|\+00:00)\Z")


def _token(value):
    if not isinstance(value, str) or not value or len(value) > MAX_TOKEN_BYTES:
        return None
    try:
        data = value.encode("utf-8")
    except UnicodeError:
        return None
    return data if len(data) <= MAX_TOKEN_BYTES else None


def _previous_active(value, now):
    if not isinstance(value, str) or not _UTC_UNTIL.fullmatch(value):
        return False
    try:
        until = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return now < until <= now + timedelta(hours=MAX_PREVIOUS_TOKEN_HOURS)
    except (ValueError, OverflowError):
        return False


def _enabled(value):
    return value is True or (isinstance(value, str) and value.lower() in {"true", "1"})


def _local_test_client(request):
    client = request.scope.get("client")
    if not isinstance(client, (list, tuple)) or len(client) != 2 or not isinstance(client[0], str):
        return False
    # This is the ASGI client address, never the user-controlled Host header.
    if client[0] == "testclient":
        return True
    try:
        address = ipaddress.ip_address(client[0])
        return address.is_loopback or bool(getattr(address, "ipv4_mapped", None) and address.ipv4_mapped.is_loopback)
    except ValueError:
        return False


def access_settings():
    """One fresh credential snapshot for gate/status; role keeps normal caching.

    Tokens and their grace deadline may rotate through an external atomic
    config-file replacement. A role switch requires the ordinary app restart.
    The returned mapping is internal and must never be serialized to clients.
    """
    try:
        settings = config.sync_access_settings()
        settings["node_role"] = str(config.get("node_role") or "all").lower()
        for key in ("sync_token", "sync_previous_token"):
            if _token(settings[key]) is None:
                settings[key] = ""
        settings["sync_allow_insecure_test_transport"] = _enabled(settings["sync_allow_insecure_test_transport"])
        return settings
    except (OSError, ValueError, TypeError, AttributeError, KeyError):
        raise SyncError("Sync access configuration is unavailable", "SYNC_ACCESS_UNAVAILABLE", 503) from None


def require_sync_access(request):
    """Header-only, rotating credentials, cloud role, and verified TLS boundary."""
    try:
        settings = access_settings()
        current = _token(settings["sync_token"])
        previous = _token(settings["sync_previous_token"])
        previous_until = settings["sync_previous_token_until"]
        provided_headers = request.headers.getlist("x-sync-token")
        provided = _token(provided_headers[0]) if len(provided_headers) == 1 else None
        now = datetime.now(timezone.utc)
        # Evaluate both fixed-time comparisons, including malformed/missing input.
        current_match = hmac.compare_digest(provided or b"", current or b"\0")
        previous_match = hmac.compare_digest(provided or b"", previous or b"\0")
        authenticated = bool(current and provided and (
            current_match or (previous and previous_match and _previous_active(previous_until, now))))
        if not authenticated:
            raise SyncError("Unauthorized sync request", "SYNC_UNAUTHORIZED", 401)
        if str(settings["node_role"] or "all").lower() != "cloud":
            raise SyncError("Sync endpoints require the cloud role", "SYNC_CLOUD_REQUIRED", 403)
        if request.scope.get("scheme") == "https":
            return
        if (request.scope.get("scheme") == "http"
                and _enabled(settings["sync_allow_insecure_test_transport"])
                and config.is_isolated_qa() and _local_test_client(request)):
            return
        raise SyncError("Sync requires HTTPS", "SYNC_HTTPS_REQUIRED", 403)
    except SyncError:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise SyncError("Sync access configuration is unavailable", "SYNC_ACCESS_UNAVAILABLE", 503) from None


def trusted_proxy_ips():
    """Return uvicorn's explicit IP allowlist; broad networks/wildcards fail shut."""
    try:
        value = config.get("trusted_proxy_ips")
        if isinstance(value, str):
            value = [part.strip() for part in value.split(",") if part.strip()]
        if type(value) is not list or len(value) > 32:
            raise ValueError
        result = []
        for item in value:
            if not isinstance(item, str):
                raise ValueError
            address = ipaddress.ip_address(item)
            if address.is_unspecified or address.is_multicast:
                raise ValueError
            normalized = str(address)
            if normalized not in result:
                result.append(normalized)
        return ",".join(result)
    except (OSError, ValueError, TypeError):
        raise SyncError("Trusted proxies must be explicit IP addresses", "INVALID_SYNC_PROXY_CONFIGURATION", 400) from None


def identity(con):
    """Read the existing node/library identity without initialization or pairing."""
    library_id, node_id = sync_queue._local_identity(con)
    return {"library_id": library_id, "node_id": node_id, "handshake": sync_protocol.schema_handshake(con)}


def _unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError
        result[key] = value
    return result


def read_source_file(path: Path):
    """Bounded local administrative input; never includes its contents in errors."""
    try:
        if not path.is_file():
            raise ValueError
        with path.open("rb") as stream:
            content = stream.read(MAX_SOURCE_BYTES + 1)
        if len(content) > MAX_SOURCE_BYTES:
            raise ValueError
        value = json.loads(content.decode("utf-8"), object_pairs_hook=_unique_keys)
        if type(value) is not dict or set(value) != {"library_id", "node_id", "handshake"}:
            raise ValueError
        return value
    except (OSError, ValueError, TypeError, RecursionError):
        raise SyncError("Source identity file is invalid", "INVALID_SYNC_SOURCE_FILE", 400) from None
