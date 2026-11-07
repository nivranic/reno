"""Pure private source DTOs and canonical paid-process proofs.

This module deliberately has no configuration, database, or transport imports.
URLs and extracted media addresses remain private; no DTO prints their values.
"""
from dataclasses import dataclass, fields
import hashlib
import json
import math
import re
from urllib.parse import urlsplit

RUNNER_CONTRACT = "yt_dlp_v1"
FORMAT_VERSION = 1
STDOUT_LIMIT = 1024 * 1024
STDERR_LIMIT = 256 * 1024
ENVELOPE_LIMIT = 2 * 1024 * 1024
MAX_ITEMS = 10000
EMPTY_SHA256 = hashlib.sha256(b"").hexdigest()
KINDS = frozenset({"source_metadata", "source_enumeration", "download"})
TERMINATIONS = frozenset({"exited", "timeout", "cancelled", "launch_unknown", "output_limit", "cleanup_unknown"})
FAILURES = frozenset({"process_failure", "invalid_output", "unsafe_metadata", "artifact_invalid"})
_SHA = re.compile(r"^[0-9a-f]{64}$")
_ID = re.compile(r"^[A-Za-z0-9_.-]{1,200}$")
_SAFE_HEADERS = frozenset({"user-agent", "referer", "origin", "accept", "accept-language"})
_SECRET_KEYS = frozenset({"cookie", "cookies", "authorization", "proxy", "password", "username", "token",
    "access_token", "refresh_token", "api_key", "client_secret", "_cookies"})
_META_KEYS = frozenset({"id", "title", "uploader", "uploader_id", "duration", "width", "height", "fps",
    "extractor", "extractor_key", "webpage_url", "original_url", "url", "ext", "protocol", "format_id",
    "format", "format_note", "container", "resolution", "vcodec", "acodec", "tbr", "abr", "vbr",
    "asr", "audio_channels", "filesize", "filesize_approx", "format_index", "preference", "quality",
    "source_preference", "language", "dynamic_range", "has_drm", "is_live", "live_status", "_type",
    "formats", "fragments", "fragment_base_url", "http_headers", "headers", "requested_formats",
    "requested_downloads", "manifest_url", "hls_aes", "hls_media_sequence", "fragment_count"})


class SourceContractError(ValueError):
    def __init__(self, code="SOURCE_CONTRACT_INVALID"):
        self.code = code
        super().__init__(code)

    def public(self):
        return {"code": self.code, "detail": "来源执行证明无效或当前能力不支持，请核对原任务。"}


def _fail(code="SOURCE_CONTRACT_INVALID"):
    raise SourceContractError(code) from None


def canonical_json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        raw.encode("utf-8")
        return raw
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail()


def sha256_json(value):
    try:
        return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()
    except UnicodeError:
        _fail()


def _integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()
    return value


def _digest(value):
    if type(value) is not str or _SHA.fullmatch(value) is None:
        _fail()
    return value


def _kind(value):
    value = {"metadata": "source_metadata", "enumeration": "source_enumeration"}.get(value, value) if type(value) is str else None
    if value not in KINDS:
        _fail()
    return value


def _text(value, maximum=8192):
    try:
        if type(value) is not str or not value or "\0" in value or len(value.encode("utf-8")) > maximum:
            _fail()
    except UnicodeError:
        _fail()
    return value


def _utf8_length(value, code="SOURCE_CONTRACT_INVALID"):
    try:
        if type(value) is not str:
            _fail(code)
        return len(value.encode("utf-8"))
    except UnicodeError:
        _fail(code)


def _source(value):
    value = _text(value).strip()
    try:
        parsed = urlsplit(value)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            _fail()
        parsed.port
    except ValueError:
        _fail()
    return value


def _relpath(value):
    value = _text(value, 1024)
    parts = value.split("/")
    if ("\\" in value or ":" in value or value.startswith("/")
            or any(not part or part in {".", ".."} or part.rstrip(" .") != part for part in parts)):
        _fail()
    return value


def _identifier(value):
    if type(value) is not str or _ID.fullmatch(value) is None:
        _fail()
    return value


def sanitize_metadata(value):
    """Keep bounded downloader fields; reject embedded transport credentials.

    Signed media URLs are necessary private source data. Login headers/cookies
    and proxy configuration cannot safely become replayable database contents.
    """
    count = [0]
    def visit(item, depth=0):
        count[0] += 1
        if depth > 16 or count[0] > 30000:
            _fail("SOURCE_METADATA_UNSAFE")
        if item is None or type(item) is bool or type(item) is int:
            return item
        if type(item) is float:
            if not math.isfinite(item):
                _fail("SOURCE_METADATA_UNSAFE")
            return item
        if type(item) is str:
            if _utf8_length(item, "SOURCE_METADATA_UNSAFE") > 8192 or "\0" in item:
                _fail("SOURCE_METADATA_UNSAFE")
            return item
        if type(item) is list:
            if len(item) > MAX_ITEMS:
                _fail("SOURCE_METADATA_UNSAFE")
            return [visit(child, depth + 1) for child in item]
        if type(item) is not dict:
            _fail("SOURCE_METADATA_UNSAFE")
        result = {}
        for key, child in item.items():
            if type(key) is not str:
                _fail("SOURCE_METADATA_UNSAFE")
            normalized = key.lower().replace("-", "_")
            if normalized in _SECRET_KEYS:
                if child:
                    _fail("SOURCE_METADATA_UNSAFE")
                continue
            if key not in _META_KEYS:
                continue
            if key in {"headers", "http_headers"}:
                if type(child) is not dict:
                    _fail("SOURCE_METADATA_UNSAFE")
                headers = {}
                for name, header in child.items():
                    if type(name) is not str or name.lower() not in _SAFE_HEADERS or type(header) is not str:
                        if header:
                            _fail("SOURCE_METADATA_UNSAFE")
                        continue
                    if "\r" in header or "\n" in header or _utf8_length(header, "SOURCE_METADATA_UNSAFE") > 8192:
                        _fail("SOURCE_METADATA_UNSAFE")
                    headers[name] = header
                result["http_headers"] = headers
            else:
                if key in {"url", "webpage_url", "original_url", "manifest_url", "fragment_base_url"} and child:
                    _source(child)
                result[key] = visit(child, depth + 1)
        return result
    if type(value) is not dict:
        _fail("SOURCE_METADATA_UNSAFE")
    cleaned = visit(value)
    _identifier(cleaned.get("id"))
    if cleaned.get("_type", "video") != "video" or "entries" in value:
        _fail("SOURCE_METADATA_UNSAFE")
    if len(canonical_json(cleaned).encode()) > STDOUT_LIMIT:
        _fail("SOURCE_METADATA_UNSAFE")
    return cleaned


@dataclass(frozen=True, slots=True, repr=False)
class SourcePlan:
    kind: str
    source: str
    segment: str | None = None
    info_json: str | None = None
    artifact_relpath: str | None = None
    phase: str = "initial"
    refresh_attempt_id: str | None = None
    refresh_settlement_sha256: str | None = None

    def __post_init__(self):
        object.__setattr__(self, "kind", _kind(self.kind))
        object.__setattr__(self, "source", _source(self.source))
        if type(self.phase) is not str or self.phase not in {"initial", "refresh"}:
            _fail()
        if (self.refresh_attempt_id is None) != (self.refresh_settlement_sha256 is None):
            _fail()
        if (self.phase == "refresh") != (self.refresh_attempt_id is not None):
            _fail()
        if self.refresh_attempt_id is not None:
            import uuid
            try:
                if type(self.refresh_attempt_id) is not str or str(uuid.UUID(self.refresh_attempt_id)) != self.refresh_attempt_id:
                    _fail()
            except ValueError:
                _fail()
            _digest(self.refresh_settlement_sha256)
        if self.kind == "download":
            if type(self.segment) is not str or self.segment not in {"video", "audio"}:
                _fail()
            _relpath(self.artifact_relpath)
            if type(self.info_json) is not str:
                _fail()
            try:
                info = json.loads(self.info_json)
            except (ValueError, RecursionError):
                _fail()
            clean = sanitize_metadata(info)
            if canonical_json(clean) != self.info_json:
                _fail()
        elif self.segment is not None or self.info_json is not None or self.artifact_relpath is not None:
            _fail()
        if self.kind == "source_enumeration" and (self.phase != "initial" or self.refresh_attempt_id is not None):
            _fail()

    @property
    def selection(self):
        return "all_items" if self.kind == "source_enumeration" else "single_video"

    @property
    def input_sha256(self):
        return sha256_json({"format_version": FORMAT_VERSION, "runner_contract": RUNNER_CONTRACT,
            "kind": self.kind, "selection": self.selection, "source": self.source,
            "info_sha256": None if self.info_json is None else hashlib.sha256(self.info_json.encode()).hexdigest()})

    @property
    def request_sha256(self):
        return sha256_json({"format_version": FORMAT_VERSION, "runner_contract": RUNNER_CONTRACT,
            "kind": self.kind, "segment": self.segment, "source": self.source, "selection": self.selection,
            "info_sha256": None if self.info_json is None else hashlib.sha256(self.info_json.encode()).hexdigest(),
            "artifact_relpath": self.artifact_relpath, "phase": self.phase,
            "refresh_attempt_id": self.refresh_attempt_id, "refresh_settlement_sha256": self.refresh_settlement_sha256})


@dataclass(frozen=True, slots=True, repr=False)
class SourceArtifact:
    segment: str
    relpath: str
    size_bytes: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int

    def __post_init__(self):
        _file_fields(self, minimum_size=1)


@dataclass(frozen=True, slots=True, repr=False)
class SourcePartial:
    segment: str
    relpath: str
    size_bytes: int
    sha256: str
    device: int
    inode: int
    mtime_ns: int
    request_sha256: str

    def __post_init__(self):
        _file_fields(self, minimum_size=0)
        _digest(self.request_sha256)


def _file_fields(value, *, minimum_size):
    if type(value.segment) is not str or value.segment not in {"video", "audio"}:
        _fail()
    _relpath(value.relpath)
    _integer(value.size_bytes, minimum_size)
    _digest(value.sha256)
    _integer(value.device, maximum=2**128 - 1)
    _integer(value.inode, 1, 2**128 - 1)
    _integer(value.mtime_ns, maximum=2**128 - 1)


@dataclass(frozen=True, slots=True, repr=False)
class SourceProcessResult:
    request_sha256: str
    process_exited: bool
    drain_complete: bool
    returncode: int | None
    termination: str
    stdout_bytes: int = 0
    stdout_sha256: str = EMPTY_SHA256
    stderr_bytes: int = 0
    stderr_sha256: str = EMPTY_SHA256
    metadata_json: str | None = None
    items: tuple[tuple[str, str], ...] | None = None
    artifact: SourceArtifact | None = None
    partials: tuple[SourcePartial, ...] = ()
    failure_class: str | None = None

    def __post_init__(self):
        _digest(self.request_sha256)
        if type(self.process_exited) is not bool or type(self.drain_complete) is not bool:
            _fail()
        if self.returncode is not None:
            _integer(self.returncode, -(2**31), 2**31 - 1)
        if (type(self.termination) is not str or self.termination not in TERMINATIONS
                or self.failure_class is not None and (type(self.failure_class) is not str or self.failure_class not in FAILURES)):
            _fail()
        for value in (self.stdout_bytes, self.stderr_bytes):
            _integer(value)
        for value in (self.stdout_sha256, self.stderr_sha256):
            _digest(value)
        if self.stdout_bytes == 0 and self.stdout_sha256 != EMPTY_SHA256 or self.stderr_bytes == 0 and self.stderr_sha256 != EMPTY_SHA256:
            _fail()
        if self.metadata_json is not None:
            try:
                clean = sanitize_metadata(json.loads(self.metadata_json))
            except (ValueError, TypeError, RecursionError):
                _fail()
            if canonical_json(clean) != self.metadata_json:
                _fail()
        if self.items is not None:
            if type(self.items) is not tuple or len(self.items) > MAX_ITEMS:
                _fail()
            for item in self.items:
                if type(item) is not tuple or len(item) != 2 or type(item[1]) is not str or _utf8_length(item[1]) > 1000:
                    _fail()
                _identifier(item[0])
        if self.artifact is not None and type(self.artifact) is not SourceArtifact:
            _fail()
        if type(self.partials) is not tuple or len(self.partials) > 8 or any(type(item) is not SourcePartial for item in self.partials):
            _fail()
        if len({item.relpath for item in self.partials}) != len(self.partials):
            _fail()

    @property
    def response_complete(self):
        return (self.process_exited and self.drain_complete and self.termination == "exited"
            and type(self.returncode) is int and self.returncode >= 0
            and self.stdout_bytes <= STDOUT_LIMIT and self.stderr_bytes <= STDERR_LIMIT)

    @property
    def successful(self):
        return self.response_complete and self.returncode == 0 and self.failure_class is None


def _file_json(value):
    return None if value is None else {field.name: getattr(value, field.name) for field in fields(value)}


def _result_json(result):
    return {"format_version": FORMAT_VERSION, "runner_contract": RUNNER_CONTRACT,
        **{field.name: getattr(result, field.name) for field in fields(result)
           if field.name not in {"artifact", "partials", "items"}},
        "items": None if result.items is None else [list(item) for item in result.items],
        "artifact": _file_json(result.artifact), "partials": [_file_json(item) for item in result.partials]}


def _typed_result(value):
    expected = {field.name for field in fields(SourceProcessResult)} | {"format_version", "runner_contract"}
    if type(value) is not dict or value.keys() != expected or type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION or value["runner_contract"] != RUNNER_CONTRACT:
        _fail()
    arguments = {key: item for key, item in value.items() if key not in {"format_version", "runner_contract"}}
    try:
        if arguments["artifact"] is not None:
            if type(arguments["artifact"]) is not dict or arguments["artifact"].keys() != {field.name for field in fields(SourceArtifact)}:
                _fail()
            arguments["artifact"] = SourceArtifact(**arguments["artifact"])
        if type(arguments["partials"]) is not list:
            _fail()
        arguments["partials"] = tuple(SourcePartial(**item) for item in arguments["partials"]
            if type(item) is dict and item.keys() == {field.name for field in fields(SourcePartial)})
        if len(arguments["partials"]) != len(value["partials"]):
            _fail()
        if arguments["items"] is not None:
            if type(arguments["items"]) is not list or any(type(item) is not list for item in arguments["items"]):
                _fail()
            arguments["items"] = tuple(tuple(item) for item in arguments["items"])
        return SourceProcessResult(**arguments)
    except (TypeError, ValueError, KeyError, UnicodeError, RecursionError):
        _fail()


def _check_output(result, kind, segment):
    if result.artifact is not None and (kind != "download" or result.artifact.segment != segment):
        _fail()
    if any(part.segment != segment or part.request_sha256 != result.request_sha256 for part in result.partials):
        _fail()
    if kind != "download" and result.partials:
        _fail()
    if not result.successful:
        if result.metadata_json is not None or result.items is not None or result.artifact is not None:
            _fail()
    elif kind == "source_metadata":
        if result.metadata_json is None or result.items is not None or result.artifact is not None:
            _fail()
    elif kind == "source_enumeration":
        if result.items is None or result.metadata_json is not None or result.artifact is not None:
            _fail()
    elif result.artifact is None or result.metadata_json is not None or result.items is not None:
        _fail()


def encode_envelope(plan, result, *, input_sha256=None):
    if type(plan) is not SourcePlan or type(result) is not SourceProcessResult or result.request_sha256 != plan.request_sha256:
        _fail()
    _check_output(result, plan.kind, plan.segment)
    value = {"format_version": FORMAT_VERSION, "runner_contract": RUNNER_CONTRACT, "kind": plan.kind,
        "segment": plan.segment, "input_sha256": plan.input_sha256 if input_sha256 is None else _digest(input_sha256),
        "request_sha256": plan.request_sha256, "result": _result_json(result)}
    raw = canonical_json(value)
    if len(raw.encode()) > ENVELOPE_LIMIT:
        _fail("SOURCE_RESULT_CAPACITY")
    return raw


def decode_envelope(text, *, request_sha256, input_sha256, kind, segment):
    _digest(request_sha256)
    _digest(input_sha256)
    kind = _kind(kind)
    if (kind == "download" and (type(segment) is not str or segment not in {"video", "audio"})) or (kind != "download" and segment is not None):
        _fail()
    try:
        if type(text) is not str or len(text.encode()) > ENVELOPE_LIMIT:
            _fail()
        value = json.loads(text)
        if (type(value) is not dict or value.keys() != {"format_version", "runner_contract", "kind", "segment", "input_sha256", "request_sha256", "result"}
                or type(value["format_version"]) is not int or value["format_version"] != FORMAT_VERSION
                or value["runner_contract"] != RUNNER_CONTRACT or value["kind"] != kind or value["segment"] != segment
                or value["input_sha256"] != input_sha256 or value["request_sha256"] != request_sha256
                or canonical_json(value) != text):
            _fail()
        result = _typed_result(value["result"])
        if result.request_sha256 != request_sha256:
            _fail()
        _check_output(result, kind, segment)
        return result
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail()


def validate_retry_envelope(text, *, request_sha256, input_sha256, kind, segment):
    result = decode_envelope(text, request_sha256=request_sha256, input_sha256=input_sha256, kind=kind, segment=segment)
    if not result.response_complete or result.returncode <= 0 or result.failure_class != "process_failure":
        _fail("SOURCE_RETRY_PROOF_INVALID")
    return result
