"""Complete schema17 local comparison, with actual live permanent witnesses.

Version2 is separate from the released15/16 certificate and SQL-only capture.
It preserves the complete private SQL representation, actual State checkpoint
and both permanent facilities. It grants no admission or dispatch authority.
The supported restore producer must still bind its original certificate and
private bundle to a permanent operation and committed restore receipt.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json

from . import source_preservation as base, source_preservation_v17 as sql
from . import source_preservation_witness as permanent


FORMAT_VERSION = 2
SCHEMA_VERSION = "17"
_PRESERVED_FIELDS = ("profile_sha256", "library_id", "node_id", "schema_sha256", "tables", "frontier",
                     "checkpoint", "state_authority", "permanent_witnesses")
_DOCUMENT_FIELDS = frozenset({"format_version", "schema_version", "scope", "continuity",
                              "knowledge_version", "preserved_sha256", *_PRESERVED_FIELDS})
_STATE_AUTHORITY_FIELDS = frozenset({"format_version", "state_present", "source_core_sha256",
    "source_authority", "source_admission", "source_recovery_pending", "authority_ref_set_sha256"})


@dataclass(frozen=True, slots=True, repr=False)
class PreservationCapture17:
    document_json: str
    sha256: str

    def public(self):
        return json.loads(self.document_json)


def _state_authority(checkpoint, observation):
    refs = {"core_sha256": checkpoint["source_execution_sha256"],
            "source_authority": None, "source_admission": None, "source_recovery_pending": None}
    projected = observation["authority_projection"]
    if projected is not None:
        refs.update({key: projected[key] for key in refs})
    if refs["core_sha256"] != checkpoint["source_execution_sha256"]:
        base._fail("CHECKPOINT_INVALID")
    return {"format_version": 1, "state_present": checkpoint["state_present"],
        "source_core_sha256": refs["core_sha256"],
        **{key: refs[key] for key in ("source_authority", "source_admission", "source_recovery_pending")},
        "authority_ref_set_sha256": base._sha(refs)}


def capture(con, *, profile, store, root_job_id, witness):
    """Consume this factory's actual connection/State/file lifetime only.

    Full State/outbox/file reads precede the SQL write reservation. All method
    checks here use that exact private window and its short physical proofs.
    This function neither commits nor creates a facility, receipt or bundle.
    """
    actual = permanent.require_capture(witness, connection=con, profile=profile, store=store)
    captured_sql = sql.capture_sql(con, profile=profile, root_job_id=root_job_id)
    current = captured_sql.public()
    if {key: current[key] for key in ("library_id", "node_id")} != actual.identity():
        permanent._fail("IDENTITY_CHANGED")
    checkpoint = actual.checkpoint(root_job_id=root_job_id,
                                   library_id=current["library_id"], node_id=current["node_id"])
    preserved = {key: current[key] for key in _PRESERVED_FIELDS if key in current}
    preserved.update(checkpoint=checkpoint,
        state_authority=_state_authority(checkpoint, actual.state_observation()),
        permanent_witnesses=actual.capsules())
    document = {"format_version": FORMAT_VERSION, "schema_version": SCHEMA_VERSION,
        "scope": "local_capture_comparison", "continuity": "not_established",
        "knowledge_version": current["knowledge_version"], "preserved_sha256": base._sha(preserved), **preserved}
    encoded = base._encoded(document)
    if len(encoded) > base.MAX_CERTIFICATE_BYTES:
        base._fail("LIMIT")
    actual.verify_source()
    return PreservationCapture17(encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest())


def _certificate(before):
    try:
        if (type(before) is not PreservationCapture17 or type(before.document_json) is not str
                or len(before.document_json.encode("utf-8")) > base.MAX_CERTIFICATE_BYTES):
            base._fail("CERTIFICATE_INVALID")
        document = before.public()
        encoded = base._encoded(document)
        if (type(document) is not dict or set(document) != _DOCUMENT_FIELDS
                or encoded.decode("utf-8") != before.document_json
                or hashlib.sha256(encoded).hexdigest() != before.sha256
                or type(document["format_version"]) is not int or document["format_version"] != FORMAT_VERSION
                or document["schema_version"] != SCHEMA_VERSION
                or document["scope"] != "local_capture_comparison" or document["continuity"] != "not_established"
                or type(document["state_authority"]) is not dict
                or set(document["state_authority"]) != _STATE_AUTHORITY_FIELDS
                or type(document["permanent_witnesses"]) is not dict
                or set(document["permanent_witnesses"]) != {"cloud_continuity", "home_authority"}
                or any(value.get("kind") != kind or value.get("presence") not in {"absent", "present"}
                       for kind, value in document["permanent_witnesses"].items())):
            base._fail("CERTIFICATE_INVALID")
        if base._sha({key: document[key] for key in _PRESERVED_FIELDS}) != document["preserved_sha256"]:
            base._fail("CERTIFICATE_INVALID")
        return document
    except base.PreservationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        base._fail("CERTIFICATE_INVALID")


def verify_unchanged(con, before, *, profile, store, witness):
    """Compare actual facts; copied documents cannot grant send authority."""
    document = _certificate(before)
    after = capture(con, profile=profile, store=store,
        root_job_id=document["frontier"]["root_job_id"], witness=witness)
    if after.public()["preserved_sha256"] != document["preserved_sha256"]:
        base._fail("CHANGED")
    return base.PreservationComparison(before.sha256, after.sha256, document["preserved_sha256"])
