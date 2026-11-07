"""Conservative exact source matching and authenticated paired-node origins.

An optional persisted home-node mapping binds aliases and content deduplication.
This module verifies its closed contract and bindings, not DNS independently.
No network or filesystem is consulted on the cloud publication path.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import re
import uuid
from urllib.parse import parse_qsl, urlsplit

from .sync_protocol import SyncError, _json_bytes, validate_ingest_url, validate_video_id

ORIGIN_VERSION = 1
MAX_ORIGIN_BYTES = 16 * 1024
_ORIGIN_FIELDS = frozenset(("origin_version", "mapping_id", "source_library_id", "source_node_id",
    "submitted_url", "resolved_url", "resolution_sha256", "extracted_video_id", "video_id",
    "media_sha256", "resolved_at", "recorded_at"))
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z\Z")

_CANONICAL_HOSTS = {
    "bilibili": frozenset(("bilibili.com", "www.bilibili.com")),
    "douyin": frozenset(("douyin.com", "www.douyin.com")),
}
_BILIBILI_VIDEO = re.compile(r"/video/([bB][vV][A-Za-z0-9]+)/?\Z")
_DOUYIN_VIDEO = re.compile(r"/video/([0-9]+)/?\Z")


def _mismatch():
    raise SyncError("The queued source cannot be verified against this published video",
                    "SYNC_CLAIM_SOURCE_MISMATCH", 409)


def _platform(host):
    if host == "b23.tv" or host == "bilibili.com" or host.endswith(".bilibili.com"):
        return "bilibili"
    if host == "douyin.com" or host.endswith(".douyin.com"):
        return "douyin"
    _mismatch()


def _origin(parsed):
    # S01 has already rejected credentials, fragments and nondefault ports.
    # Preserve scheme, exact path and the WHOLE query, including ordering and
    # repeated keys. In particular, BV pagination is not a tracking parameter.
    return parsed.scheme, parsed.hostname, parsed.path or "/", parsed.query


def _video_reference(parsed, platform):
    if parsed.hostname not in _CANONICAL_HOSTS[platform]:
        return None
    match = (_BILIBILI_VIDEO if platform == "bilibili" else _DOUYIN_VIDEO).fullmatch(parsed.path)
    if match is None:
        return None
    video_id = match[1]
    if platform == "douyin":
        return video_id, None
    video_id = "BV" + video_id[2:]
    try:
        pages = [value for key, value in parse_qsl(parsed.query, keep_blank_values=True,
                                                   max_num_fields=200) if key == "p"]
    except ValueError:
        _mismatch()
    if len(pages) > 1 or (pages and not re.fullmatch(r"[1-9][0-9]{0,8}", pages[0])):
        _mismatch()
    return video_id, pages[0] if pages else None


def _matches_extracted_id(reference, extracted_id, platform):
    video_id, part = reference
    if platform == "douyin":
        return extracted_id == video_id
    # yt-dlp returns BV..._pN for explicit/anthology pages and can append a
    # numeric part/cid for segmented or interactive entries. A query without
    # p does not identify one anthology page; preserve its original URL proof.
    match = re.fullmatch(re.escape(video_id) + r"(?:_p([1-9][0-9]*))?(?:_([0-9]+))?", extracted_id)
    if match is None:
        return False
    return part is None or match[1] == part


def _origin_digest(origin):
    return hashlib.sha256(_json_bytes({key: value for key, value in origin.items()
                                      if key != "mapping_id"})).hexdigest()


def _stamp(value):
    if not isinstance(value, str) or not _STAMP.fullmatch(value):
        _mismatch()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        _mismatch()


def validate_resource_identity(url, extracted_id):
    """A supported canonical final resource must actually name the extracted ID."""
    try:
        validate_ingest_url(url)
        validate_video_id(extracted_id)
        parsed = urlsplit(url)
        platform = _platform(parsed.hostname)
        reference = _video_reference(parsed, platform)
        if reference is None or not _matches_extracted_id(reference, extracted_id, platform):
            _mismatch()
        return platform
    except SyncError:
        _mismatch()


def validate_origin(origin):
    """Validate a bounded paired-node attestation, not independent DNS evidence."""
    if type(origin) is not dict or set(origin) != _ORIGIN_FIELDS:
        _mismatch()
    # Reject non-string/unbounded values before JSON serialization.
    if type(origin["origin_version"]) is not int or origin["origin_version"] != ORIGIN_VERSION:
        _mismatch()
    if any(not isinstance(value, str) or len(value) > 4096
           for key, value in origin.items() if key != "origin_version"):
        _mismatch()
    if len(_json_bytes(origin)) > MAX_ORIGIN_BYTES:
        _mismatch()
    for key in ("mapping_id", "resolution_sha256", "media_sha256"):
        if not _SHA.fullmatch(origin[key]):
            _mismatch()
    for key in ("source_library_id", "source_node_id"):
        try:
            if str(uuid.UUID(origin[key])) != origin[key]:
                _mismatch()
        except ValueError:
            _mismatch()
    try:
        validate_ingest_url(origin["submitted_url"])
        validate_video_id(origin["video_id"])
        platform = validate_resource_identity(origin["resolved_url"], origin["extracted_video_id"])
        if _platform(urlsplit(origin["submitted_url"]).hostname) != platform:
            _mismatch()
    except SyncError:
        _mismatch()
    if _stamp(origin["recorded_at"]) < _stamp(origin["resolved_at"]):
        _mismatch()
    if origin["mapping_id"] != _origin_digest(origin):
        _mismatch()
    return dict(origin)


def validate_claim_origin(queue_url, asset, *, origin=None, source=None):
    """Raise a sanitized mismatch unless submitted source identity is proven.

    Accept the same validated origin (host case/default port normalized), or
    recognized public /video/ routes with the same platform, extracted video
    identity and complete query. Distinct tracking queries are deliberately
    not discarded; future source mappings can bind such aliases after ingest.
    Legacy ``url`` and current ``share_url`` types represent remote imports.
    """
    if origin is not None:
        proof = validate_origin(origin)
        if (not isinstance(asset, Mapping) or not isinstance(source, Mapping)
                or proof["submitted_url"] != queue_url
                or proof["source_library_id"] != source.get("library_id")
                or proof["source_node_id"] != source.get("node_id")
                or proof["video_id"] != asset.get("video_id")
                or proof["media_sha256"] != asset.get("content_sha256")):
            _mismatch()
        return
    if not isinstance(asset, Mapping) or asset.get("source_type") not in ("share_url", "url"):
        _mismatch()
    try:
        validate_ingest_url(queue_url)
        source_url = validate_ingest_url(asset.get("source_url"))
        extracted_id = validate_video_id(asset.get("video_id"))
    except SyncError:
        _mismatch()
    declared = asset.get("source_platform")
    if not isinstance(declared, str) or declared.lower() not in _CANONICAL_HOSTS:
        _mismatch()
    platform = declared.lower()
    queued, source = urlsplit(queue_url), urlsplit(source_url)
    if _platform(queued.hostname) != platform or _platform(source.hostname) != platform:
        _mismatch()
    if platform == "douyin" and not re.fullmatch(r"[0-9]+", extracted_id):
        _mismatch()
    queue_ref, source_ref = _video_reference(queued, platform), _video_reference(source, platform)
    # An exact URL match cannot bless an explicitly contradictory video ID.
    for reference in (queue_ref, source_ref):
        if reference is not None and not _matches_extracted_id(reference, extracted_id, platform):
            _mismatch()
    if _origin(queued) == _origin(source):
        return
    if (queue_ref is not None and source_ref is not None and queue_ref == source_ref
            and queued.query == source.query):
        return
    _mismatch()
