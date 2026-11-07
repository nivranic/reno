"""Exact personal mirror transfer facts; never an operational capability.

The snapshot remains the original UTF-8 document, including every raw JSON
cell and source attachment observation. Its library_id identifies the source;
it must never replace the receiver's library identity during installation.
Transport, durable preparation, file verification, installation and primary
handoff are deliberately outside these pure validators.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from functools import wraps
import hashlib
import re

from . import personal_mirror_contract as snapshot

FORMAT_VERSION = 1
MAX_ATTACHMENT_COUNT = 1024
MAX_ATTACHMENT_BYTES = 1024 * 1024 * 1024
MAX_ATTACHMENT_TOTAL_BYTES = 4 * 1024 * 1024 * 1024
SOURCE_FIELDS = {"library_id", "node_id", "personal_primary_id", "primary_epoch"}
RECEIVER_FIELDS = {"library_id", "node_id"}
PREPARE_FIELDS = {"format_version", "kind", "operation_id", "snapshot_id", "snapshot_sha256",
    "snapshot_json", "source", "receiver", "previous", "expected_revision"}
SELECTOR_FIELDS = {"format_version", "kind", "operation_id", "prepare_sha256"}
ATTACHMENT_FIELDS = {"attachment_id", "storage_key", "sha256", "byte_size", "file_identity"}
RECEIPT_FIELDS = {"format_version", "kind", "operation_id", "prepare_sha256", "snapshot_id",
    "snapshot_sha256", "source", "receiver", "installed_revision", "counts",
    "attachment_manifest_sha256", "committed_at", "receipt_sha256", "replayed"}
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?Z\Z")


class PersonalMirrorTransferError(ValueError):
    def __init__(self, code):
        self.code = "PERSONAL_MIRROR_TRANSFER_" + code
        super().__init__(self.code)


def _fail(code="INVALID"):
    raise PersonalMirrorTransferError(code) from None


def _exact(value, fields):
    if type(value) is not dict or set(value) != fields:
        _fail("FIELDS_INVALID")


def _checked(function):
    @wraps(function)
    def wrapper(*args, **kwargs):
        try:
            return function(*args, **kwargs)
        except PersonalMirrorTransferError:
            raise
        except snapshot.PersonalSnapshotError as exc:
            raise PersonalMirrorTransferError(exc.code) from None
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            _fail()
    return wrapper


def _version(value):
    if type(value) is not int or value != FORMAT_VERSION:
        _fail("VERSION_INVALID")


def _source(value):
    _exact(value, SOURCE_FIELDS)
    for name in ("library_id", "node_id", "personal_primary_id"):
        snapshot._uuid(value[name])
    snapshot._integer(value["primary_epoch"], 1)
    if value["node_id"] != value["personal_primary_id"]:
        _fail("SOURCE_NOT_PRIMARY")
    return deepcopy(value)


def _receiver(value, source):
    _exact(value, RECEIVER_FIELDS)
    for name in RECEIVER_FIELDS:
        snapshot._uuid(value[name])
        if value[name] == source[name]:
            _fail("INDEPENDENT_RECEIVER_REQUIRED")
    return deepcopy(value)


def _previous(value, revision):
    snapshot._integer(revision)
    if revision == 2**63 - 1:
        _fail("REVISION_EXHAUSTED")
    if value is None:
        if revision != 0:
            _fail("PREVIOUS_REQUIRED")
        return None
    _exact(value, {"snapshot_id", "snapshot_sha256"})
    snapshot._uuid(value["snapshot_id"])
    snapshot._sha(value["snapshot_sha256"])
    if revision == 0:
        _fail("PREVIOUS_UNEXPECTED")
    return deepcopy(value)


def _manifest_limit(manifest):
    if len(manifest) > MAX_ATTACHMENT_COUNT:
        _fail("ATTACHMENT_COUNT_LIMIT")
    total = 0
    for descriptor in manifest:
        size = descriptor["byte_size"]
        if size > MAX_ATTACHMENT_BYTES:
            _fail("ATTACHMENT_SIZE_LIMIT")
        total += size
        if total > MAX_ATTACHMENT_TOTAL_BYTES:
            _fail("ATTACHMENT_TOTAL_LIMIT")


def _normalize_prepare(value, *, prior_snapshot_json=None):
    _exact(value, PREPARE_FIELDS)
    _version(value["format_version"])
    if value["kind"] != "personal_mirror_prepare":
        _fail("KIND_INVALID")
    snapshot._uuid(value["operation_id"])
    snapshot._uuid(value["snapshot_id"])
    snapshot._sha(value["snapshot_sha256"])
    if type(value["snapshot_json"]) is not str:
        _fail("SNAPSHOT_TEXT_REQUIRED")
    source = _source(value["source"])
    receiver = _receiver(value["receiver"], source)
    previous = _previous(value["previous"], value["expected_revision"])
    facts = snapshot.validate_snapshot(value["snapshot_json"],
        expected_sha256=value["snapshot_sha256"], prior_snapshot_json=prior_snapshot_json)
    document = facts.document
    if facts.snapshot_id != value["snapshot_id"] or document["source"] != {
            name: source[name] for name in ("library_id", "personal_primary_id", "primary_epoch")}:
        _fail("SNAPSHOT_BINDING_MISMATCH")
    _manifest_limit(document["attachment_manifest"])
    if prior_snapshot_json is not None:
        prior = snapshot.validate_snapshot(prior_snapshot_json)
        if previous != {"snapshot_id": prior.snapshot_id, "snapshot_sha256": prior.snapshot_sha256}:
            _fail("PREVIOUS_SNAPSHOT_MISMATCH")
    normalized = {**deepcopy(value), "source": source, "receiver": receiver, "previous": previous}
    return normalized, facts


@_checked
def normalize_prepare(value, *, prior_snapshot_json=None):
    """Validate exact binding and original snapshot bytes, without admission.

    A prior snapshot is an explicit fact input, not a receiver CAS proof. The
    runtime must select its actual accepted predecessor and enforce the guard.
    """
    normalized, _ = _normalize_prepare(value, prior_snapshot_json=prior_snapshot_json)
    return normalized


def _plain_dict_copy(value, fields):
    if type(value) is not dict:
        return None
    copied = dict.copy(value)
    if any(type(key) is not str for key in copied) or set(copied) != fields:
        return None
    if any(child is not None and type(child) is not str and type(child) is not int for child in copied.values()):
        return None
    return copied


def _plain_prepare_copy(value):
    """Detach supported JSON shapes without invoking caller copy callbacks.

    Unsupported inputs miss this optimization and retain legacy validation.
    Facts may only be reused after validating this detached primitive input;
    the legacy deepcopy can change snapshot fields after their first check.
    """
    if type(value) is not dict:
        return None
    copied = dict.copy(value)
    if any(type(key) is not str for key in copied) or set(copied) != PREPARE_FIELDS:
        return None
    nested_fields = {"source": SOURCE_FIELDS, "receiver": RECEIVER_FIELDS,
        "previous": {"snapshot_id", "snapshot_sha256"}}
    for key, child in copied.items():
        if key in nested_fields:
            if key == "previous" and child is None:
                continue
            detached = _plain_dict_copy(child, nested_fields[key])
            if detached is None:
                return None
            copied[key] = detached
        elif child is not None and type(child) is not str and type(child) is not int:
            return None
    return copied


def _digest(value):
    return hashlib.sha256(snapshot._json(value).encode("utf-8")).hexdigest()


def prepare_sha256(value):
    """Hash canonical envelope while preserving the original snapshot string."""
    return _digest(normalize_prepare(value))


def attachment_manifest(value):
    """Return all original ready descriptors; do not invent staging bodies."""
    prepared = normalize_prepare(value)
    return snapshot.validate_snapshot(prepared["snapshot_json"]).document["attachment_manifest"]


def attachment_manifest_sha256(value):
    return _digest(attachment_manifest(value))


@_checked
def require_same_prepare(previous, value):
    """Facts-only comparison of one operation's exact original preparation."""
    original, current = normalize_prepare(previous), normalize_prepare(value)
    if original["operation_id"] != current["operation_id"]:
        _fail("OPERATION_MISMATCH")
    if _digest(original) != _digest(current):
        _fail("IDEMPOTENCY_KEY_REUSED")
    return current


def _selector(value, kind, *, extra=frozenset(), prepare=None):
    _exact(value, SELECTOR_FIELDS | set(extra))
    _version(value["format_version"])
    if value["kind"] != kind:
        _fail("KIND_INVALID")
    snapshot._uuid(value["operation_id"])
    snapshot._sha(value["prepare_sha256"])
    if prepare is not None:
        prepared = normalize_prepare(prepare)
        if (value["operation_id"], value["prepare_sha256"]) != (
                prepared["operation_id"], _digest(prepared)):
            _fail("PREPARE_BINDING_MISMATCH")


@_checked
def normalize_attachment(value, *, prepare=None):
    """Validate a descriptor; absent preparation does not prove membership."""
    _selector(value, "personal_mirror_attachment", extra={"attachment"}, prepare=prepare)
    descriptor = value["attachment"]
    _exact(descriptor, ATTACHMENT_FIELDS)
    snapshot._uuid(descriptor["attachment_id"])
    snapshot.storage_key(descriptor["storage_key"])
    snapshot._sha(descriptor["sha256"])
    snapshot._integer(descriptor["byte_size"], 1)
    snapshot.file_identity(descriptor["file_identity"])
    if descriptor["file_identity"]["byte_size"] != descriptor["byte_size"]:
        _fail("ATTACHMENT_SIZE_MISMATCH")
    _manifest_limit([descriptor])
    if prepare is not None:
        descriptors = attachment_manifest(prepare)
        selected = [item for item in descriptors if item["attachment_id"] == descriptor["attachment_id"]]
        if selected != [descriptor]:
            _fail("ATTACHMENT_NOT_PREPARED")
    return deepcopy(value)


@_checked
def normalize_commit(value, *, prepare=None):
    """Exact selectors only; this never declares files or a mirror installed."""
    _selector(value, "personal_mirror_commit", prepare=prepare)
    return deepcopy(value)


def _receipt_sha256(value):
    return _digest({key: child for key, child in value.items() if key not in {"receipt_sha256", "replayed"}})


@_checked
def receipt_sha256(value):
    """Stable receipt identity excludes transport replay, with exact keys."""
    _exact(value, RECEIPT_FIELDS)
    return _receipt_sha256(value)


@_checked
def normalize_receipt(prepare, value):
    """Verify a claimed committed result against the exact original request.

    A matching DTO proves shape and binding, never a local COMMIT, remote HTTP
    receipt, complete files, reader capability or authority to change primary.
    """
    detached = _plain_prepare_copy(prepare)
    prepared_facts = None
    if detached is None:
        prepared = normalize_prepare(prepare)
    else:
        prepared, prepared_facts = _normalize_prepare(detached)
    _exact(value, RECEIPT_FIELDS)
    _version(value["format_version"])
    if value["kind"] != "personal_mirror_receipt":
        _fail("KIND_INVALID")
    if type(value["replayed"]) is not bool:
        _fail("REPLAY_INVALID")
    snapshot._integer(value["installed_revision"], 1)
    source = _source(value["source"])
    receiver = _receiver(value["receiver"], source)
    facts = prepared_facts
    if facts is None:
        facts = snapshot.validate_snapshot(prepared["snapshot_json"])
    expected = {"operation_id": prepared["operation_id"], "prepare_sha256": _digest(prepared),
        "snapshot_id": prepared["snapshot_id"], "snapshot_sha256": prepared["snapshot_sha256"],
        "source": prepared["source"], "receiver": prepared["receiver"],
        "installed_revision": prepared["expected_revision"] + 1,
        "counts": dict(facts.counts), "attachment_manifest_sha256": _digest(
            facts.document["attachment_manifest"] if prepared_facts is not None else attachment_manifest(prepared))}
    if any(value[key] != item for key, item in expected.items()):
        _fail("RECEIPT_BINDING_MISMATCH")
    if (type(value["counts"]) is not dict or set(value["counts"]) != set(expected["counts"])
            or any(type(count) is not int or count < 0 for count in value["counts"].values())):
        _fail("COUNTS_INVALID")
    stamp = value["committed_at"]
    if type(stamp) is not str or not _STAMP.fullmatch(stamp):
        _fail("COMMIT_TIME_INVALID")
    try:
        parsed = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if parsed.tzinfo != timezone.utc:
            _fail("COMMIT_TIME_INVALID")
    except ValueError:
        _fail("COMMIT_TIME_INVALID")
    snapshot._sha(value["receipt_sha256"])
    if value["receipt_sha256"] != _receipt_sha256(value):
        _fail("RECEIPT_SHA256_MISMATCH")
    return {**deepcopy(value), "source": source, "receiver": receiver}
