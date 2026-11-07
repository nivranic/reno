"""Detached Home receipt/checkpoint validation selected by the actual image.

This module reads no database, State or permanent file. The caller must obtain
``schema_version`` from its actual protected committed/immutable image. A valid
row proves representation and cross-field consistency only; it is never a
restore producer, admission, dispatch or retirement capability.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import uuid

from . import budget_schema, home_preservation_contract17 as contract
from . import home_recovery_schema as home, source_preservation as preservation, sync_state


_OPERATION_FIELDS = frozenset({"operation_id", "continuation_id", "execution_generation", "kind",
    "suboperation_key_sha256", "row_sha256", "attempts"})
_ATTEMPT_FIELDS = frozenset({"attempt_id", "ordinal", "state", "row_sha256", "result_sha256",
                           "result_bytes", "result_storage_state"})
_ATTEMPT_STATES = frozenset({"reserved", "dispatching", "indeterminate", "settled", "released"})
_RESULT_STATES = frozenset({"none", "stored", "oversize", "capacity_blocked", "retired"})


class HomeRecoveryCheckpointError(ValueError):
    def __init__(self, code="HOME_RECOVERY_CHECKPOINT_INVALID"):
        self.code = code
        super().__init__(code)


def _fail(code="HOME_RECOVERY_CHECKPOINT_INVALID"):
    raise HomeRecoveryCheckpointError(code) from None


@dataclass(frozen=True, slots=True, repr=False)
class RowCheckpoint:
    """Detached facts; frozen fields do not turn their dictionaries into seals."""

    checkpoint: dict
    wrapper: dict | None
    certificate_sha256: str | None


def _exact(value, fields):
    if type(value) is not dict or set(value) != fields:
        _fail()


def _integer(value, minimum=0, maximum=home.MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _sha(value):
    if type(value) is not str or not home._SHA.fullmatch(value):
        _fail()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _fail()


def _digest(value):
    return hashlib.sha256(home.canonical_json(value).encode("utf-8")).hexdigest()


def _bare(value):
    """Preserve the released nine-key State1--4 interpretation exactly."""
    _exact(value, contract.CHECKPOINT_FIELDS)
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
        return value
    if value["absence_sha256"] is not None or value["state_bytes"] < 2:
        _fail()
    for name in ("state_sha256", "canonical_state_sha256"):
        _sha(value[name])
    _integer(value["state_format_version"], 1, 4)
    lineage = value["source_execution_lineage"]
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
    for outbox in outboxes:
        _exact(outbox, {"publication_id", "sha256", "byte_size", "semantic_sha256"})
        _uuid(outbox["publication_id"])
        if outbox["publication_id"] in seen:
            _fail()
        seen.add(outbox["publication_id"])
        for name in ("sha256", "semantic_sha256"):
            _sha(outbox[name])
        _integer(outbox["byte_size"], 2, sync_state.MAX_OUTBOX_BYTES)
    return value


def _frontier(value, row):
    _exact(value, {"root_job_id", "root_job_row_sha256", "operations"})
    if value["root_job_id"] != row["root_job_id"]:
        _fail()
    operations = value["operations"]
    if type(operations) is not list or len(operations) > preservation.MAX_OPERATIONS:
        _fail()
    if value["root_job_id"] is None:
        if value["root_job_row_sha256"] is not None or operations:
            _fail()
        return
    _integer(value["root_job_id"], 1)
    _sha(value["root_job_row_sha256"])
    operation_ids, attempt_ids, attempt_count = [], set(), 0
    for operation in operations:
        _exact(operation, _OPERATION_FIELDS)
        for name in ("operation_id", "continuation_id"):
            _uuid(operation[name])
        operation_ids.append(operation["operation_id"])
        _integer(operation["execution_generation"], 1)
        if type(operation["kind"]) is not str or operation["kind"] not in budget_schema.KINDS:
            _fail()
        for name in ("suboperation_key_sha256", "row_sha256"):
            _sha(operation[name])
        attempts = operation["attempts"]
        if type(attempts) is not list:
            _fail()
        attempt_count += len(attempts)
        if attempt_count > preservation.MAX_ATTEMPTS:
            _fail()
        ordinals = set()
        for attempt in attempts:
            _exact(attempt, _ATTEMPT_FIELDS)
            _uuid(attempt["attempt_id"])
            _integer(attempt["ordinal"], 1, 128)
            if attempt["attempt_id"] in attempt_ids or attempt["ordinal"] in ordinals:
                _fail()
            attempt_ids.add(attempt["attempt_id"])
            ordinals.add(attempt["ordinal"])
            if (type(attempt["state"]) is not str or attempt["state"] not in _ATTEMPT_STATES
                    or type(attempt["result_storage_state"]) is not str
                    or attempt["result_storage_state"] not in _RESULT_STATES):
                _fail()
            _sha(attempt["row_sha256"])
            if attempt["result_sha256"] is not None:
                _sha(attempt["result_sha256"])
            _integer(attempt["result_bytes"])
    if operation_ids != sorted(set(operation_ids)):
        _fail()


def _business(row):
    request, result = json.loads(row["request_json"]), json.loads(row["result_json"])
    before, after = json.loads(row["before_version_json"]), json.loads(row["after_version_json"])
    _sha(request.get("expected_digest"))
    if (row["source_kind"] != "home_checkpoint"
            or request != {"action": "knowledge_restore", "source_id": row["source_id"],
                           "expected_digest": request["expected_digest"]}):
        _fail()
    _sha(result.get("knowledge_sha256"))
    if type(result.get("format_version")) is not int or result["format_version"] != 1:
        _fail()
    for version in (result.get("before_version"), result.get("after_version")):
        _exact(version, {"library_id", "library_epoch", "kb_revision"})
        _uuid(version["library_id"])
        _integer(version["library_epoch"], 1)
        _integer(version["kb_revision"])
    if result != {"format_version": 1, "action": "knowledge_restore", "operation_id": row["operation_id"],
            "source_id": row["source_id"], "before_version": before, "after_version": after,
            "knowledge_sha256": result["knowledge_sha256"], "protection_backup_id": row["protection_backup_id"]}:
        _fail()


def validate(row, *, schema_version):
    """Validate all35 original columns and return detached checkpoint facts.

    Only actual schema17 can select wrapper2. Old bare checkpoints retain their
    State1--4 gate even in17; schema<=16 never accepts wrapper2. The integer
    schema argument must come from the caller's real image, not these facts.
    Certificate/file SHA correctness and original producer provenance require
    a separate resolver holding the actual SQL row and original permanent pins.
    """
    try:
        if type(schema_version) is not int or not 1 <= schema_version <= 17:
            _fail("HOME_RECOVERY_CHECKPOINT_SCHEMA_UNSUPPORTED")
        record = home.validate_record(row)
        _business(record)
        value = json.loads(record["checkpoint_json"])
        if set(value) == contract.CHECKPOINT_FIELDS:
            checkpoint, wrapper = _bare(value), None
        elif schema_version == 17:
            wrapper = contract.normalize_checkpoint(record["checkpoint_json"],
                expected_sha256=record["checkpoint_sha256"])
            checkpoint = wrapper["checkpoint"]
        else:
            _fail()
        lineage = checkpoint["source_execution_lineage"]
        expected_lineage = None if record["original_core_sha256"] is None else {
            name: record[name] for name in ("root_job_id", "continuation_id", "execution_generation")}
        if (checkpoint["source_execution_sha256"] != record["original_core_sha256"]
                or lineage != expected_lineage):
            _fail()
        frontier = json.loads(record["frontier_json"])
        _frontier(frontier, record)
        certificate_sha256 = None
        if wrapper is not None:
            certificate = wrapper["certificate"]
            if (certificate["preserved_sha256"] != record["before_operational_sha256"]
                    or certificate["frontier_sha256"] != record["frontier_sha256"]
                    or wrapper["bundle"]["bundle_id"] != str(uuid.uuid5(uuid.UUID(record["operation_id"]),
                        "reno.home.preservation.bundle.v1"))
                    or record["protection_backup_id"] != str(uuid.uuid5(uuid.UUID(record["operation_id"]),
                        "reno.home.preservation.backup.v1"))):
                _fail()
            binding = wrapper["permanent_witnesses"]["home_authority"]["binding"]
            if binding is not None and (binding["home_library_id"], binding["home_node_id"]) != (
                    record["library_id"], record["node_id"]):
                _fail()
            certificate_sha256 = certificate["certificate_sha256"]
        return RowCheckpoint(deepcopy(checkpoint), deepcopy(wrapper), certificate_sha256)
    except HomeRecoveryCheckpointError:
        raise
    except (home.HomeRecoveryRecordError, contract.HomePreservationContractError,
            ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail()
