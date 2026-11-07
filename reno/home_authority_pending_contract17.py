"""Pure Home witness metadata for the independent legacy pending namespace.

Home1 remains its exact released flat summary and original absence digest.
Home2 adds one explicit component1 summary; its original authority record and
reference hashes still describe only the original five record kinds. Public
projection2 carries the component's safe references separately. Nothing here
opens a file, selects an actual schema, creates a writer, or grants paid work.
The owning schema17 consumer must bind these DTOs to its actual physical pins.
Unknown observations cannot be normalized into a complete Home witness.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
import re
import uuid

from . import home_preservation_contract17 as legacy
from . import storage_legacy_pending_state17 as component
from .sync_protocol import SyncError

KIND = "home_authority"
COMPONENT = "legacy_pending_state"
SUMMARY1_FIELDS = frozenset(legacy.CAPSULE_FIELDS | {"binding"})
SUMMARY2_FIELDS = SUMMARY1_FIELDS | {COMPONENT}
PROJECTION1_FIELDS = frozenset({"summary", "references", "unknown"})
PROJECTION2_FIELDS = PROJECTION1_FIELDS | {COMPONENT}
COMPONENT_PROJECTION_FIELDS = frozenset({"summary", "references", "unknown"})
RECORD_FIELDS = frozenset({"kind", "record_id", "record_sha256", "core_sha256",
    "state", "refs", "publication_metadata"})
REFERENCE_FIELDS = frozenset(component.REFERENCE_FIELDS)
RECORD_KINDS = frozenset({"origins", "controls", "requests", "receipts", "publications"})
REFERENCE_KINDS = frozenset({"origin", "control", "publication", "recovery_intent", "recovery_receipt"})
MAX_REFERENCES = 8192
MAX_COMPONENT_REFERENCES = 16 * component.MAX_FILES
MAX_CAPSULE_BYTES = component.CaptureLimits().max_capsule_bytes
MAX_PUBLIC_BYTES = 16 * 1024 * 1024
MAX_PUBLIC_NODES = 1_000_000
MAX_PUBLIC_DEPTH = 24
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PRIVATE_FIELDS = frozenset({"state_images", "state_json", "before_json", "after_json", "before_raw_base64", "raw"})


class HomeAuthorityPendingContractError(ValueError):
    def __init__(self, code="HOME_AUTHORITY_PENDING_CONTRACT17_INVALID"):
        self.code = code
        super().__init__(code)


def _fail(code="HOME_AUTHORITY_PENDING_CONTRACT17_INVALID"):
    raise HomeAuthorityPendingContractError(code) from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
        allow_nan=False).encode("utf-8")


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _exact(value, fields):
    if type(value) is not dict or set(value) != fields:
        _fail()


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            _fail()
    except (ValueError, AttributeError):
        _fail()


def _plain(value, depth=0, count=None):
    count = [0] if count is None else count
    count[0] += 1
    if depth > MAX_PUBLIC_DEPTH or count[0] > MAX_PUBLIC_NODES:
        _fail("HOME_AUTHORITY_PENDING_CONTRACT17_LIMIT")
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str or key in _PRIVATE_FIELDS:
                _fail()
            _plain(child, depth + 1, count)
    elif type(value) is list:
        for child in value:
            _plain(child, depth + 1, count)
    elif type(value) not in (str, int, bool, type(None)):
        _fail()


def _bounded(value):
    _plain(value)
    if len(_json(value)) > MAX_PUBLIC_BYTES:
        _fail("HOME_AUTHORITY_PENDING_CONTRACT17_LIMIT")


def _absence1():
    return _digest({"format_version": 1, "kind": KIND, "presence": "absent"})


def _absence2(pending):
    # This is a new explicit combination. Historical witness1's digest never
    # implies coverage of this separate namespace or changes its old bytes.
    return _digest({"format_version": 2, "kind": KIND, "presence": "absent", COMPONENT: pending})


def _component_summary(value, owner):
    try:
        normalized = component.normalize_summary(value, owner_profile_sha256=owner)
    except (component.PendingStateRootError, SyncError, ValueError, TypeError, KeyError, AttributeError):
        _fail()
    empty = _digest([])
    if normalized["unknown_count"] != 0 or normalized["unknown_sha256"] != empty:
        _fail("HOME_AUTHORITY_PENDING_CONTRACT17_COMPONENT_UNPROVEN")
    files, capsules, markers = (normalized[name] for name in ("file_count", "capsule_count", "marker_count"))
    if (capsules + markers != files or markers > capsules
            or normalized["applied_count"] != markers
            or normalized["total_bytes"] < 2 * files
            or normalized["total_bytes"] > capsules * MAX_CAPSULE_BYTES + markers * component.MAX_APPLIED_BYTES):
        _fail()
    if files == 0:
        expected_namespace = _digest({"presence": normalized["presence"], "names": []})
        if (normalized["total_bytes"] != 0 or normalized["namespace_sha256"] != expected_namespace
                or any(normalized[name] != empty for name in
                    ("file_set_sha256", "state_set_sha256", "reference_set_sha256"))):
            _fail()
    elif any(normalized[name] == empty for name in
            ("file_set_sha256", "state_set_sha256", "reference_set_sha256")):
        _fail()
    return normalized


def normalize_summary(value, *, owner_profile_sha256=None, expected_version=None):
    """Normalize exact Home1/2 metadata, without inferring physical presence.

    A positive component's nonempty file/State hashes remain opaque historical
    comparisons until its owning reader verifies the original actual files.
    Public reference/unknown hashes are additionally checked with their lists
    by normalize_projection. No unknown summary is a complete certificate.
    """
    try:
        _bounded(value)
        if type(value) is not dict or type(value.get("format_version")) is not int:
            _fail()
        version = value["format_version"]
        if (version not in (1, 2) or expected_version is not None
                and (type(expected_version) is not int or expected_version not in (1, 2) or version != expected_version)):
            _fail()
        _exact(value, SUMMARY1_FIELDS if version == 1 else SUMMARY2_FIELDS)
        if owner_profile_sha256 is not None:
            _sha(owner_profile_sha256)
            if value["owner_profile_sha256"] != owner_profile_sha256:
                _fail()
        if version == 1:
            legacy._capsule(value, KIND)
            return deepcopy(value)
        pending = _component_summary(value[COMPONENT], value["owner_profile_sha256"])
        # Reuse the released codec for its exact flat metadata. This local
        # comparison does not rewrite the supplied2 or infer a fallback1.
        flat = {key: deepcopy(value[key]) for key in SUMMARY1_FIELDS}
        flat["format_version"] = 1
        if value["presence"] == "absent":
            if value["absence_sha256"] != _absence2(pending):
                _fail()
            flat["absence_sha256"] = _absence1()
        legacy._capsule(flat, KIND)
        result = deepcopy(value)
        result[COMPONENT] = pending
        return result
    except HomeAuthorityPendingContractError:
        raise
    except (legacy.HomePreservationContractError, ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, OverflowError, RecursionError):
        _fail()


def build_summary(base1, *, legacy_pending_state):
    """Build2 only from an explicit normalized base1 and component1 observation."""
    base = normalize_summary(base1, expected_version=1)
    pending = _component_summary(legacy_pending_state, base["owner_profile_sha256"])
    result = {**base, "format_version": 2, COMPONENT: pending}
    if base["presence"] == "absent":
        result["absence_sha256"] = _absence2(pending)
    return normalize_summary(result, expected_version=2)


def _component_projection(value, summary):
    _exact(value, COMPONENT_PROJECTION_FIELDS)
    pending = _component_summary(value["summary"], summary["owner_profile_sha256"])
    if pending != summary[COMPONENT] or type(value["references"]) is not list or type(value["unknown"]) is not list:
        _fail()
    if value["unknown"] or pending["unknown_count"] != len(value["unknown"]) or pending["unknown_sha256"] != _digest(value["unknown"]):
        _fail("HOME_AUTHORITY_PENDING_CONTRACT17_COMPONENT_UNPROVEN")
    references = value["references"]
    if len(references) > MAX_COMPONENT_REFERENCES:
        _fail("HOME_AUTHORITY_PENDING_CONTRACT17_LIMIT")
    hashes = []
    for ref in references:
        _exact(ref, REFERENCE_FIELDS)
        if (type(ref["format_version"]) is not int or ref["format_version"] != 1
                or ref["record_kind"] not in REFERENCE_KINDS):
            _fail()
        _uuid(ref["record_id"])
        _sha(ref["record_sha256"])
        _sha(ref["core_sha256"])
        hashes.append(_digest(ref))
    if (hashes != sorted(set(hashes)) or pending["reference_set_sha256"] != _digest(references)
            or pending["capsule_count"] == 0 and references
            or len(references) < 2 * pending["capsule_count"]):
        _fail()
    return {"summary": pending, "references": deepcopy(references), "unknown": []}


def normalize_projection(value, *, expected_version=None):
    """Normalize complete public metadata; independent capsule refs stay separate.

    The exact component three-key projection is its public_summary(), never
    the private State image view or the larger copied-file reconstruction DTO.
    All returned values are detached dictionaries, not executable capabilities.
    """
    try:
        _bounded(value)
        if type(value) is not dict or "summary" not in value:
            _fail()
        summary = normalize_summary(value["summary"], expected_version=expected_version)
        version = summary["format_version"]
        _exact(value, PROJECTION1_FIELDS if version == 1 else PROJECTION2_FIELDS)
        if type(value["references"]) is not list or type(value["unknown"]) is not list:
            _fail()
        if value["unknown"]:
            _fail("HOME_AUTHORITY_PENDING_CONTRACT17_HOME_UNPROVEN")
        rows = value["references"]
        if len(rows) > MAX_REFERENCES:
            _fail("HOME_AUTHORITY_PENDING_CONTRACT17_LIMIT")
        identifiers = set()
        for row in rows:
            _exact(row, RECORD_FIELDS)
            if row["kind"] not in RECORD_KINDS or row["state"] != "committed" or type(row["refs"]) is not list:
                _fail()
            _uuid(row["record_id"])
            _sha(row["record_sha256"])
            if row["core_sha256"] is not None:
                _sha(row["core_sha256"])
            identifier = row["kind"], row["record_id"]
            if identifier in identifiers or row["kind"] != "publications" and row["publication_metadata"] is not None:
                _fail()
            identifiers.add(identifier)
        if (summary["record_count"] != len(rows) or summary["reference_set_sha256"] != _digest(rows)
                or summary["presence"] == "absent" and rows
                or summary["presence"] == "present" and summary["record_set_sha256"] != _digest(rows)):
            _fail()
        result = {"summary": summary, "references": deepcopy(rows), "unknown": []}
        if version == 2:
            result[COMPONENT] = _component_projection(value[COMPONENT], summary)
        return result
    except HomeAuthorityPendingContractError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def build_projection(base1, *, legacy_pending_state):
    """Bind an explicit component public_summary(), retaining original rows/hash."""
    original = normalize_projection(base1, expected_version=1)
    _exact(legacy_pending_state, COMPONENT_PROJECTION_FIELDS)
    summary = build_summary(original["summary"], legacy_pending_state=legacy_pending_state["summary"])
    result = {**original, "summary": summary, COMPONENT: deepcopy(legacy_pending_state)}
    return normalize_projection(result, expected_version=2)
