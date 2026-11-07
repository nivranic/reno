"""Bounded server-selected filesystem observation target, never enrollment.

This codec performs lexical validation only. Existing-only owned runtime opens
must reject links/reparse points; configuration does not prove a live grant.
"""
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import uuid

ENVIRONMENT_VARIABLE = "RENO_PERSONAL_AUTHORITY_READ_TARGET"
MAX_TARGET_BYTES = 32768
_FIELDS = frozenset({"format_version", "facility_root", "profile_paths",
                     "expected_node_id", "expected_declared_binding_sha256"})
_HEX_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PREFIX = ".reno-personal-authority"
_ERROR_CODES = frozenset({
    "TARGET_NOT_CONFIGURED", "TARGET_INVALID", "TARGET_PROFILE_MISMATCH",
    "NODE_INVALID", "NODE_MISMATCH", "STORAGE_UNAVAILABLE",
    "NODE_FILE_UNAVAILABLE", "FILESYSTEM_UNAVAILABLE", "READ_CANCEL_INVALID",
    "STORAGE_BUSY", "STORAGE_NOT_INITIALIZED", "NODE_CHANGED", "OBSERVATION_CANCELLED",
})


class AuthorityReadError(ValueError):
    """Finite public reason; never include target or source material."""
    def __init__(self, code, status=503):
        self.code = code if type(code) is str and code in _ERROR_CODES else "TARGET_INVALID"
        self.status = status if type(status) is int and 400 <= status <= 599 else 503
        super().__init__(self.code)


@dataclass(frozen=True, slots=True)
class ReadTarget:
    facility_root: Path
    profile_paths: tuple[str, str, str, str]
    profile_sha256: str
    expected_node_id: str
    expected_declared_binding_sha256: str


def _fail(code="TARGET_INVALID"):
    raise AuthorityReadError(code) from None


def _pairs(items):
    value = {}
    for key, item in items:
        if type(key) is not str or key in value:
            _fail()
        value[key] = item
    return value


def _constant(_value):
    _fail()


def canonical_uuid(value, *, code="TARGET_INVALID"):
    if type(value) is not str or len(value) != 36:
        _fail(code)
    try:
        identifier = uuid.UUID(value)
    except (ValueError, AttributeError):
        _fail(code)
    if identifier.int == 0 or str(identifier) != value:
        _fail(code)
    return value


def _facility(value, profile):
    if type(value) is not str or not value or any(
            ord(character) < 32 or 127 <= ord(character) <= 159 for character in value):
        _fail()
    try:
        value.encode("utf-8")
    except UnicodeError:
        _fail()
    # Test the input spelling before Path can discard lexical dot components.
    if any(part in (".", "..") for part in re.split(r"[/\\]", value)):
        _fail()
    path = Path(value)
    if not path.is_absolute() or path.parent == path or not path.name.startswith(_PREFIX):
        _fail()
    # The exact native spelling must survive Path and normcase/abspath. This
    # rejects separator/case aliases without resolving or selecting a root;
    # a canonical UNC path remains eligible for an actual UNC profile.
    if value != os.path.normcase(os.path.abspath(os.fspath(path))):
        _fail()
    name = path.name
    if (name.endswith((".", " ")) or any(character in '/\\<>:"|?*' for character in name)
            or len(name.encode("utf-8")) > 255):
        _fail()
    parent = os.path.normcase(os.path.abspath(os.fspath(path.parent)))
    expected_parent = os.path.normcase(os.path.abspath(os.fspath(profile.database.parent)))
    if parent != expected_parent:
        _fail()
    return path  # Do not resolve, scan, create or select a discovered directory.


def parse_target(raw, *, profile):
    """Parse the sole server env selection against the current exact profile."""
    if raw is None or (type(raw) is str and raw == ""):
        _fail("TARGET_NOT_CONFIGURED")
    if type(raw) is not str:
        _fail()
    try:
        encoded = raw.encode("utf-8")
    except UnicodeError:
        _fail()
    if len(encoded) > MAX_TARGET_BYTES:
        _fail()
    try:
        value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=_constant)
    except AuthorityReadError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        _fail()
    if type(value) is not dict or set(value) != _FIELDS:
        _fail()
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail()
    paths = value["profile_paths"]
    if type(paths) is not list or len(paths) != 4 or any(type(path) is not str for path in paths):
        _fail()
    actual_paths = profile.identity
    if (type(actual_paths) is not tuple or len(actual_paths) != 4
            or any(type(path) is not str for path in actual_paths)
            or tuple(paths) != actual_paths):
        _fail("TARGET_PROFILE_MISMATCH")
    node_id = canonical_uuid(value["expected_node_id"])
    binding_sha = value["expected_declared_binding_sha256"]
    if type(binding_sha) is not str or _HEX_SHA.fullmatch(binding_sha) is None:
        _fail()
    facility = _facility(value["facility_root"], profile)
    # Match the existing RP1 profile ARRAY byte convention; no path aliases or
    # dictionary-of-paths digest can stand in for this exact current profile.
    profile_raw = json.dumps(list(actual_paths), ensure_ascii=False, sort_keys=True,
                             separators=(",", ":"), allow_nan=False).encode("utf-8")
    return ReadTarget(facility, actual_paths, hashlib.sha256(profile_raw).hexdigest(),
                      node_id, binding_sha)
