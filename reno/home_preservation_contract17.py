"""Pure schema17 Home checkpoint metadata; never a producer or admission seal.

Consumers must select this contract from the actual committed/immutable schema17
image. This module reads no database, State, bundle or permanent facility. Its
detached dictionaries prove only representation and public digest consistency;
actual owner/window, file identity, producer receipts and complete historical
references must still be verified by the restore/readmit/root consumers.
Released schema15/16 checkpoints and their hashes do not pass through here.
"""
from __future__ import annotations

import hashlib
import re
import uuid

from . import home_recovery_schema as home, source_preservation as preservation
from . import sync_state


SCHEMA_VERSION = 17
FORMAT_VERSION = 2
MAX_CHECKPOINT_BYTES = home.MAX_CHECKPOINT_BYTES
MAX_INTEGER = home.MAX_INTEGER
WRAPPER_FIELDS = frozenset({"format_version", "checkpoint", "certificate", "bundle",
                            "state_authority", "permanent_witnesses"})
CHECKPOINT_FIELDS = frozenset("state_present absence_sha256 state_sha256 state_bytes canonical_state_sha256 "
    "state_format_version source_execution_sha256 source_execution_lineage outboxes".split())
CERTIFICATE_FIELDS = frozenset({"format_version", "certificate_sha256", "byte_size",
                              "preserved_sha256", "frontier_sha256"})
BUNDLE_FIELDS = frozenset({"format_version", "bundle_id", "manifest_sha256", "file_set_sha256"})
STATE_AUTHORITY_FIELDS = frozenset({"format_version", "state_present", "source_core_sha256",
    "source_authority", "source_admission", "source_recovery_pending", "authority_ref_set_sha256"})
CAPSULE_FIELDS = frozenset({"format_version", "kind", "presence", "absence_sha256", "owner_profile_sha256",
    "identity", "contract_sha256", "head_ordinal", "head_sha256", "journal_sha256", "record_set_sha256",
    "reference_set_sha256", "record_count", "journal_count"})
_REF_FIELDS = frozenset({"format_version", "record_kind", "record_id", "record_sha256"})
_AUTHORITY_FIELDS = frozenset({"format_version", "core_sha256", "origin", "defer", "resume", "failure", "publication"})
_ADMISSION_FIELDS = frozenset({"format_version", "admission_id", "core_sha256", "request_sha256",
                             "admission_sha256", "record_sha256"})
_PENDING_FIELDS = frozenset({"format_version", "kind", "request_id", "request_sha256", "core_sha256", "intent_sha256"})
_BINDING_FIELDS = frozenset({"home_library_id", "home_node_id", "target_library_id", "target_node_id"})
_SHA = re.compile(r"[0-9a-f]{64}\Z")


class HomePreservationContractError(ValueError):
    def __init__(self, code="HOME_PRESERVATION_CONTRACT17_INVALID"):
        self.code = code
        super().__init__(code)


def _fail(code="HOME_PRESERVATION_CONTRACT17_INVALID"):
    raise HomePreservationContractError(code) from None


def _exact(value, fields):
    if type(value) is not dict or set(value) != fields:
        _fail()


def _integer(value, minimum=0, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _version(value, expected):
    if type(value) is not int or value != expected:
        _fail()


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _fail()


def _digest(value):
    return hashlib.sha256(home.canonical_json(value).encode("utf-8")).hexdigest()


def _plain(value, depth=0):
    # Validate caller object types before serialization can coerce tuples or
    # numeric/key subclasses into superficially valid JSON representations.
    if depth > home.MAX_JSON_DEPTH:
        _fail()
    if type(value) is dict:
        for key, child in value.items():
            if type(key) is not str:
                _fail()
            _plain(child, depth + 1)
    elif type(value) is list:
        for child in value:
            _plain(child, depth + 1)
    elif type(value) not in (str, int, bool, type(None)):
        _fail()


def _checkpoint(value):
    _exact(value, CHECKPOINT_FIELDS)
    if type(value["state_present"]) is not bool:
        _fail()
    _integer(value["state_bytes"], maximum=sync_state.MAX_STATE_BYTES)
    if not value["state_present"]:
        if value != {"state_present": False,
                "absence_sha256": _digest({"format_version": 1, "state": "absent"}),
                "state_sha256": None, "state_bytes": 0, "canonical_state_sha256": None,
                "state_format_version": None, "source_execution_sha256": None,
                "source_execution_lineage": None, "outboxes": []}:
            _fail()
        return
    if value["absence_sha256"] is not None or value["state_bytes"] < 2:
        _fail()
    for name in ("state_sha256", "canonical_state_sha256"):
        _sha(value[name])
    _integer(value["state_format_version"], 1, 5)
    lineage = value["source_execution_lineage"]
    # State1/2 predate source_execution; this new wrapper cannot manufacture a
    # core for either closed State shape. Released parsers remain unchanged.
    if value["state_format_version"] < 3 and (lineage is not None or value["source_execution_sha256"] is not None):
        _fail()
    if lineage is None:
        if value["source_execution_sha256"] is not None:
            _fail()
    else:
        _sha(value["source_execution_sha256"])
        _exact(lineage, {"root_job_id", "continuation_id", "execution_generation"})
        _uuid(lineage["continuation_id"])
        for name in ("root_job_id", "execution_generation"):
            _integer(lineage[name], 1)
    outboxes = value["outboxes"]
    if type(outboxes) is not list or len(outboxes) > 2:
        _fail()
    seen = set()
    for item in outboxes:
        _exact(item, {"publication_id", "sha256", "byte_size", "semantic_sha256"})
        _uuid(item["publication_id"])
        if item["publication_id"] in seen:
            _fail()
        seen.add(item["publication_id"])
        for name in ("sha256", "semantic_sha256"):
            _sha(item[name])
        _integer(item["byte_size"], 2, sync_state.MAX_OUTBOX_BYTES)


def _ref(value, kind):
    _exact(value, _REF_FIELDS)
    _version(value["format_version"], 1)
    if value["record_kind"] != kind:
        _fail()
    _uuid(value["record_id"])
    _sha(value["record_sha256"])


def _state_authority(value, checkpoint):
    _exact(value, STATE_AUTHORITY_FIELDS)
    _version(value["format_version"], 1)
    if (type(value["state_present"]) is not bool or value["state_present"] != checkpoint["state_present"]
            or value["source_core_sha256"] != checkpoint["source_execution_sha256"]):
        _fail()
    core = value["source_core_sha256"]
    names = ("source_authority", "source_admission", "source_recovery_pending")
    if core is not None:
        _sha(core)
    if core is None or checkpoint["state_format_version"] != 5:
        if any(value[name] is not None for name in names):
            _fail()
    authority = value["source_authority"]
    if authority is not None:
        _exact(authority, _AUTHORITY_FIELDS)
        _version(authority["format_version"], 1)
        if authority["core_sha256"] != core:
            _fail()
        for name in ("origin", "defer", "resume", "failure", "publication"):
            if authority[name] is not None:
                _ref(authority[name], "origin" if name == "origin" else "publication" if name == "publication" else "control")
    admission = value["source_admission"]
    if admission is not None:
        _exact(admission, _ADMISSION_FIELDS)
        _version(admission["format_version"], 1)
        _uuid(admission["admission_id"])
        for name in _ADMISSION_FIELDS - {"format_version", "admission_id"}:
            _sha(admission[name])
        if admission["core_sha256"] != core:
            _fail()
    pending = value["source_recovery_pending"]
    if pending is not None:
        _exact(pending, _PENDING_FIELDS)
        _version(pending["format_version"], 1)
        if pending["kind"] not in {"source_register", "source_dispose", "source_readmit"}:
            _fail()
        _uuid(pending["request_id"])
        for name in ("core_sha256", "request_sha256", "intent_sha256"):
            _sha(pending[name])
        if pending["core_sha256"] != core:
            _fail()
    _sha(value["authority_ref_set_sha256"])
    refs = {"core_sha256": core, **{name: value[name] for name in names}}
    if value["authority_ref_set_sha256"] != _digest(refs):
        _fail()


def _capsule(value, kind):
    if (kind == "home_authority" and type(value) is dict
            and type(value.get("format_version")) is int and value["format_version"] == 2):
        # Lazy dispatch avoids an import cycle. The separate2 codec delegates
        # only its local flat1 comparison back here, preserving old hashes.
        from . import home_authority_pending_contract17 as pending_contract
        try:
            pending_contract.normalize_summary(value, expected_version=2)
        except (pending_contract.HomeAuthorityPendingContractError, ValueError, TypeError,
                KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
            _fail()
        return
    _exact(value, CAPSULE_FIELDS | ({"binding"} if kind == "home_authority" else set()))
    _version(value["format_version"], 1)
    if value["kind"] != kind or value["presence"] not in {"absent", "present"}:
        _fail()
    _sha(value["owner_profile_sha256"])
    _sha(value["reference_set_sha256"])
    for name in ("head_ordinal", "record_count", "journal_count"):
        _integer(value[name])
    if value["head_ordinal"] != value["journal_count"]:
        _fail()
    if kind == "home_authority":
        if value["binding"] is None:
            if value["presence"] != "absent":
                _fail()
        else:
            _exact(value["binding"], _BINDING_FIELDS)
            for identifier in value["binding"].values():
                _uuid(identifier)
        if value["presence"] == "present" and value["record_set_sha256"] != value["reference_set_sha256"]:
            _fail()
    digests = ("contract_sha256", "head_sha256", "journal_sha256", "record_set_sha256")
    if value["presence"] == "absent":
        absent = {"format_version": 1, "kind": kind, "presence": "absent"}
        if (value["absence_sha256"] != _digest(absent) or value["identity"] is not None
                or any(value[name] is not None for name in digests)
                or any(value[name] != 0 for name in ("head_ordinal", "record_count", "journal_count"))
                or kind == "home_authority" and value["reference_set_sha256"] != _digest([])):
            _fail()
    else:
        if value["absence_sha256"] is not None:
            _fail()
        identity_key = "facility_id" if kind == "home_authority" else "continuity_id"
        _exact(value["identity"], {identity_key})
        _uuid(value["identity"][identity_key])
        for name in digests:
            _sha(value[name])


def normalize_checkpoint(value, *, expected_sha256=None):
    """Return a detached exact wrapper2 DTO, with no actual provenance grant.

    Accept an exact plain dictionary or canonical JSON text. ``expected_sha256``
    binds the whole wrapper representation only. The consumer must separately
    prove the actual schema17 image, pinned certificate/bundle, original SQL
    operation receipt and permanent witnesses before any effect or retirement.
    """
    try:
        if type(value) is dict:
            _plain(value)
            raw = home.canonical_json(value)
        elif type(value) is str:
            raw = value
        else:
            _fail()
        if len(raw.encode("utf-8")) > MAX_CHECKPOINT_BYTES:
            _fail("HOME_PRESERVATION_CONTRACT17_LIMIT")
        copied = home._decode(raw, MAX_CHECKPOINT_BYTES)
        _plain(copied)
        if expected_sha256 is not None:
            _sha(expected_sha256)
            if hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected_sha256:
                _fail()
        _exact(copied, WRAPPER_FIELDS)
        _version(copied["format_version"], FORMAT_VERSION)
        _checkpoint(copied["checkpoint"])
        certificate = copied["certificate"]
        _exact(certificate, CERTIFICATE_FIELDS)
        _version(certificate["format_version"], 2)
        _integer(certificate["byte_size"], 2, preservation.MAX_CERTIFICATE_BYTES)
        for name in ("certificate_sha256", "preserved_sha256", "frontier_sha256"):
            _sha(certificate[name])
        bundle = copied["bundle"]
        _exact(bundle, BUNDLE_FIELDS)
        _version(bundle["format_version"], 1)
        _uuid(bundle["bundle_id"])
        for name in ("manifest_sha256", "file_set_sha256"):
            _sha(bundle[name])
        _state_authority(copied["state_authority"], copied["checkpoint"])
        witnesses = copied["permanent_witnesses"]
        _exact(witnesses, {"cloud_continuity", "home_authority"})
        for kind, capsule in witnesses.items():
            _capsule(capsule, kind)
        if witnesses["cloud_continuity"]["owner_profile_sha256"] != witnesses["home_authority"]["owner_profile_sha256"]:
            _fail()
        authority = copied["state_authority"]
        refs = authority["source_authority"]
        needs_home = (authority["source_admission"] is not None or authority["source_recovery_pending"] is not None
            or refs is not None and any(refs[name] is not None for name in ("origin", "defer", "resume", "failure", "publication")))
        if needs_home and (witnesses["home_authority"]["presence"] != "present"
                           or witnesses["home_authority"]["record_count"] < 1):
            _fail()
        return copied
    except HomePreservationContractError:
        raise
    except (home.HomeRecoveryRecordError, ValueError, TypeError, KeyError, AttributeError,
            UnicodeError, OverflowError, RecursionError):
        _fail()
