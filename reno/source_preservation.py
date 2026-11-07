"""Bounded, private-preserving comparison of one local maintenance transition.

This module reads a caller-consistent SQL transaction and an actually owned
StateStore. It does not start/commit transactions, install facilities, mutate
business data, grant admission or establish continuity after physical rollback.
``in_transaction`` does not independently prove a caller's IMMEDIATE writer;
the future supported restore workflow must acquire that writer itself.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
import sqlite3

from . import budget, budget_schema, home_recovery_schema, storage_admin, storage_lifecycle, sync_queue, sync_state
from . import sync_source_readmit_schema as readmit_schema
from .sync_protocol import SyncError


FORMAT_VERSION = 1
SUPPORTED_SCHEMA_VERSIONS = frozenset({"15", "16"})
MAX_ROWS = 100_000
MAX_CELL_BYTES = 8 * 1024 * 1024
MAX_SCAN_BYTES = 256 * 1024 * 1024
MAX_OPERATIONS = 4096
MAX_ATTEMPTS = 32_768
MAX_CERTIFICATE_BYTES = 8 * 1024 * 1024
MAX_OUTBOX_TOTAL_BYTES = 2 * sync_state.MAX_OUTBOX_BYTES
MAX_SCHEMA_OBJECTS = 1024
MAX_SCHEMA_BYTES = 1024 * 1024

# Closed current-v15 columns, not an introspection-derived SELECT * contract.
# All operational/personal rows are preserved, including other task lineages.
_DECLARATIONS = {
    "budget_operation": "operation_id library_id identity_sha256 continuation_id execution_generation kind suboperation_key input_sha256 root_job_id max_attempts state wait_reason resume_at wait_details_json revision created_at updated_at",
    "budget_attempt": "attempt_id operation_id ordinal request_sha256 download_segment state initial_reservation_json initial_reservation_sha256 current_reservation_json current_reservation_sha256 reservation_revision reservation_held current_policy_sha256 current_period_key current_window_start_utc current_window_end_utc first_dispatch_json first_dispatch_sha256 dispatch_token_sha256 dispatched_at last_admitted_at response_complete outcome observed_usage_json usage_quality provider_request_id result_text result_sha256 result_bytes result_storage_state settlement_sha256 settled_at indeterminate_at created_at updated_at",
    "checklist_check": "check_id item_id item_revision criteria_snapshot_hash operation_id commit_seq record_kind result measurements_json note attachment_refs_json checked_at created_at supersedes_check_id",
    "checklist_instance": "instance_id project_id title space stage basis_kb_revision lifecycle_state revision created_at updated_at archived_at",
    "checklist_item": "item_id instance_id title conditions_json method_text basis_kind criteria_json criteria_state position revision created_at updated_at",
    "decision_card": "card_id project_id title question context_json space stage alternatives_json selected_option_id decision_text decision_reason open_questions_json content_snapshot_json state revision created_at updated_at archived_at",
    "job": "job_id video_id kind status attempts error created_at updated_at request_id scope_key payload_json owner_id lease_until result_json started_at completed_at continuation_id execution_generation not_before blocked_reason wait_mode manual_paused budget_operation_id",
    "meta": "key value",
    "mutation_receipt": "operation_id scope request_hash response_status response_json created_at",
    "personal_evidence_ref": "ref_id project_id owner_kind owner_id target_kind target_id fingerprint_version target_fingerprint snapshot_json created_at",
    "personal_revision": "entity_kind entity_id revision snapshot_json operation_id created_at",
    "processing_run": "run_id video_id step model prompt_version schema_version taxonomy_version started_at completed_at ok detail",
    "renovation_project": "project_id name profile_json revision created_at updated_at archived_at",
    "source_attachment": "attachment_id source_id project_id sha256 byte_size media_type original_name storage_key state created_at operation_id error",
    "source_record": "source_id project_id source_type title publisher document_no edition published_on valid_from valid_to source_url locator excerpt scope_json verification_state validity checked_at check_note revision created_at updated_at",
    "storage_artifact": "artifact_id namespace root_id root_device root_inode storage_key sha256 byte_size file_device file_inode file_mtime_ns file_ctime_ns created_at updated_at",
    "storage_gc_item": "operation_id artifact_id namespace root_id storage_key sha256 byte_size file_device file_inode file_mtime_ns file_ctime_ns state deleted_bytes reason_code updated_at",
    "storage_gc_operation": "operation_id request_sha256 preview_sha256 roots_sha256 policy_json request_json result_json state created_at updated_at",
    "sync_checkpoint": "checkpoint_id publication_id receiver_version_json format_version schema_version storage_key sha256 byte_size created_at",
    "sync_checkpoint_retirement": "origin_kind checkpoint_id operation_id storage_key sha256 byte_size retired_at",
    "sync_pair": "slot source_library_id source_node_id target_library_id target_node_id protocol_version schema_fingerprint paired_at",
    "sync_publication": "publication_id source_library_id source_node_id source_epoch source_revision payload_sha256 knowledge_sha256 committed_at local_version_json",
    "sync_publication_fence": "publication_id generation",
    "sync_published_video": "publication_id video_id source_library_id source_node_id source_epoch source_revision media_sha256 committed_at",
    "sync_queue": "item_id url status attempts lease_until last_error_class created_at updated_at owner_id owner_generation lease_token lease_generation retry_at video_id publication_id result_hash budget_wait_reason budget_operation_id",
    "sync_queue_budget_receipt": "request_id request_sha256 request_json action source_library_id source_node_id target_library_id target_node_id receiver_library_epoch recovery_generation item_id lease_generation result_json result_sha256 committed_at",
    "sync_queue_publication": "item_id lease_generation lease_token owner_id owner_generation publication_id video_id created_at",
    "sync_queue_source_receipt": "request_id request_sha256 request_json action source_library_id source_node_id target_library_id target_node_id receiver_library_epoch recovery_generation item_id lease_generation core_sha256 submitted_url_sha256 failure_sha256 result_json result_sha256 committed_at",
    "sync_recovery_checkpoint": "checkpoint_id operation_id origin_kind receiver_version_json format_version schema_version storage_key sha256 byte_size created_at",
    "sync_recovery_control": "slot state generation revision source_floor_json restore_operation_id updated_at",
    "sync_recovery_operation": "operation_id action request_sha256 request_json result_json created_at",
    "sync_session": "slot session_id lease_token generation lease_until updated_at metrics_json",
    "sync_upload_pin": "pin_id kind publication_id parent_pin_id source_library_id source_node_id source_epoch source_revision target_library_id target_library_epoch recovery_generation descriptor_json intent_sha256 state artifact_id abandon_operation_id abandon_request_sha256 created_at updated_at",
    "task_request": "request_id job_id payload_signature created_at",
    "user_decision": "decision_id atom_id conflict_id action revised_claim note created_at event_id client_id decided_at target_fingerprint target_snapshot_json event_schema_version",
    "worker_lease": "scope owner_id lease_until updated_at",
    "sqlite_sequence": "name seq",
}
PROJECTIONS = {name: tuple(columns.split()) for name, columns in _DECLARATIONS.items()}
PROJECTIONS_V16 = {**PROJECTIONS, home_recovery_schema.TABLE: home_recovery_schema.COLUMNS}
# This complete SQL projection is prospective.  The released capture below
# still accepts only 15/16: SQL alone cannot produce a certificate17 without
# actual State5 and both permanent external witness captures.
PROJECTIONS_V17 = {**PROJECTIONS_V16, **readmit_schema.COLUMNS}
_KNOWLEDGE = {
    "conflict_case": "conflict_id ctype status side_a_json side_b_json analysis_json recommended_action created_at derivation_json",
    "evidence_ref": "id atom_id video_id modality source_item_id start_ms end_ms evidence_text weight",
    "frame": "frame_id video_id pts_ms origin dhash path",
    "knowledge_atom": "id video_id category stage space subject claim reason risk_if_ignored polarity conditions_json parameters_json confidence status cluster_id model prompt_version created_at dimension evidence_nature exceptions prices_json",
    "knowledge_cluster": "cluster_id canonical_topic relation judge_reason members_json linked_conflict created_at derivation_json",
    "ocr_span": "id video_id start_ms end_ms text conf frame_ids_json",
    "sync_report": "name publication_id storage_key sha256 byte_size policy_version source_version_json local_version_json generated_at review_events_sha256",
    "transcript_segment": "id video_id idx start_ms end_ms text",
    "video_asset": "video_id source_platform source_type source_url title author source_published_at duration_ms width height fps content_sha256 audio_sha256 imported_at files_json status sync_state",
    "visual_observation": "id video_id frame_id pts_ms scene_description materials_json tools_json subtitle_text measurement",
    "atom_fts": "atom_id claim reason category space",
    "atom_fts_config": "k v", "atom_fts_content": "id c0 c1 c2 c3 c4",
    "atom_fts_data": "id block", "atom_fts_docsize": "id sz", "atom_fts_idx": "segid term pgno",
}
_STATE_FIELDS_V1 = frozenset("format_version binding local_epoch session_id lease inbound outbound seeded claims work pending_publication published_version published_content_sha256 published_reports_sha256 uploaded_media status recovery applied_recovery_generation recovery_attempt legacy_pending_stream".split())
_STATE_FIELDS = {1: _STATE_FIELDS_V1,
    2: _STATE_FIELDS_V1 | {"sender_contract", "publication_media"},
    3: _STATE_FIELDS_V1 | {"sender_contract", "publication_media", "source_execution", "budget_pending"},
    4: _STATE_FIELDS_V1 | {"sender_contract", "publication_media", "source_execution", "budget_pending", "source_admission", "source_control_pending"}}
_DOCUMENT_FIELDS = frozenset("format_version schema_version scope continuity knowledge_version preserved_sha256 profile_sha256 library_id node_id schema_sha256 tables frontier checkpoint".split())


class PreservationError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _fail(code):
    raise PreservationError("SOURCE_PRESERVATION_" + code) from None


def _encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


def _sha(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def _cell(value):
    # SQLite's dynamic storage classes matter: BLOB private metadata must not
    # be decoded, coerced to strings or handed to JSON serialization.
    if value is None:
        tag, raw = b"N", b""
    elif type(value) is bytes:
        tag, raw = b"B", value
    elif type(value) is str:
        tag, raw = b"T", value.encode("utf-8")
    elif type(value) is int:
        tag, raw = b"I", str(value).encode("ascii")
    elif type(value) is float and math.isfinite(value):
        tag, raw = b"F", value.hex().encode("ascii")
    else:
        _fail("LEDGER_INVALID")
    if len(raw) > MAX_CELL_BYTES:
        _fail("LIMIT")
    return tag + len(raw).to_bytes(8, "big") + raw


def _row_sha(table, columns, row, scanned):
    result = hashlib.sha256(_encoded([FORMAT_VERSION, table, list(columns)]))
    for name in columns:
        chunk = _cell(row[name])
        scanned[1] += len(chunk)
        if scanned[1] > MAX_SCAN_BYTES:
            _fail("LIMIT")
        result.update(chunk)
    return result.hexdigest()


def _schema(con, *, prospective_schema17=False):
    if type(prospective_schema17) is not bool:
        _fail("SCHEMA_UNSUPPORTED")
    row = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
    if row is None or (row[0] not in SUPPORTED_SCHEMA_VERSIONS
                       and not (prospective_schema17 and row[0] == "17")):
        _fail("SCHEMA_UNSUPPORTED")
    version = row[0]
    projections = {"15": PROJECTIONS, "16": PROJECTIONS_V16, "17": PROJECTIONS_V17}[version]
    for row in con.execute("SELECT length(CAST(name AS BLOB)),length(CAST(tbl_name AS BLOB)),"
                           "length(CAST(sql AS BLOB)) FROM main.sqlite_master LIMIT ?",
                           (MAX_SCHEMA_OBJECTS + 1,)):
        if any(size is not None and size > MAX_SCHEMA_BYTES for size in row):
            _fail("LIMIT")
    schema_rows = []
    schema_size = 0
    for row in con.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name LIMIT ?",
                           (MAX_SCHEMA_OBJECTS + 1,)):
        schema_rows.append(list(row))
        schema_size += len(_encoded(list(row)))
        if len(schema_rows) > MAX_SCHEMA_OBJECTS or schema_size > MAX_SCHEMA_BYTES:
            _fail("LIMIT")
    actual = {row[1] for row in schema_rows if row[0] == "table"}
    if actual != set(projections) | set(_KNOWLEDGE):
        _fail("SCHEMA_UNSUPPORTED")
    if con.execute("SELECT 1 FROM main.sqlite_master WHERE type='view' LIMIT 1").fetchone():
        _fail("SCHEMA_UNSUPPORTED")
    expected = {**projections, **{name: tuple(value.split()) for name, value in _KNOWLEDGE.items()}}
    order = {}
    for name, columns in expected.items():
        infos = [tuple(row) for row in con.execute(f'PRAGMA main.table_xinfo("{name}")')]
        if name == "atom_fts":
            if {row[1] for row in infos if row[6]} != {"atom_fts", "rank"}:
                _fail("SCHEMA_UNSUPPORTED")
            infos = [row for row in infos if not row[6]]
        if len(infos) != len(columns) or {row[1] for row in infos} != set(columns) or any(row[6] for row in infos):
            _fail("SCHEMA_UNSUPPORTED")
        primary = sorted((row[5], row[1]) for row in infos if row[5])
        order[name] = tuple(column for _, column in primary) or columns
    if any(row[0].lower() in projections for row in con.execute("SELECT name FROM sqlite_temp_master")):
        _fail("SCHEMA_UNSUPPORTED")
    budget_schema.validate(con)
    if version in {"16", "17"}:
        home_recovery_schema.validate(con)
    if version == "17":
        from . import sync_source_readmit_schema2
        sync_source_readmit_schema2.validate_historical(con)
    if con.execute("PRAGMA main.foreign_key_check").fetchone():
        _fail("LEDGER_INVALID")
    return order, _sha(schema_rows), version, projections


def _rows(con, table, order, scanned, projections):
    columns = projections[table]
    prefix = "a." if table == "budget_attempt" else ""
    fields = ",".join(prefix + '"' + name + '"' for name in columns)
    source = f'main."{table}"' + (" a" if prefix else "")
    extras = ()
    if table == "budget_attempt":
        source += " LEFT JOIN main.budget_operation o ON o.operation_id=a.operation_id"
        fields += ",o.kind,o.input_sha256,o.max_attempts AS operation_max_attempts"
        extras = ("kind", "input_sha256", "operation_max_attempts")
    where, parameters = "", ()
    if table == "meta":
        where = " WHERE substr(key,1,5)!='step:' AND key NOT IN ('library_epoch','kb_revision')"
    elif table == "sqlite_sequence":
        where = " WHERE name NOT IN (" + ",".join("?" for _ in _KNOWLEDGE) + ")"
        parameters = tuple(_KNOWLEDGE)
    # Check lengths before materializing any private oversized BLOB/TEXT.
    lengths = ",".join("length(CAST(" + prefix + '"' + c + '" AS BLOB))' for c in columns)
    for raw in con.execute("SELECT " + lengths + " FROM " + source + where + " LIMIT ?",
                           (*parameters, MAX_ROWS + 1)):
        scanned[0] += 1
        if scanned[0] > MAX_ROWS or any(value is not None and value > MAX_CELL_BYTES for value in raw):
            _fail("LIMIT")
    ordering = ",".join(prefix + '"' + column + '"' for column in order[table])
    for row in con.execute("SELECT " + fields + " FROM " + source + where + " ORDER BY " + ordering,
                           parameters):
        yield dict(zip((*columns, *extras), row))


def _sql_capture(con, *, profile, root_job_id, prospective_schema17=False):
    """Shared static SQL representation; never a complete State/witness seal.

    Its row and table hash format stays at the original version1 even when a
    future enclosing certificate has a different document version.  Callers
    separately establish the actual transaction/profile/lifecycle ownership.
    """
    order, schema_sha, schema_version, projections = _schema(
        con, prospective_schema17=prospective_schema17)
    library, node = sync_queue._local_identity(con)
    version = dict(zip(("library_id", "library_epoch", "kb_revision"),
        (library, int(con.execute("SELECT value FROM main.meta WHERE key='library_epoch'").fetchone()[0]),
         int(con.execute("SELECT value FROM main.meta WHERE key='kb_revision'").fetchone()[0]))))
    job = None
    if root_job_id is not None:
        jobs = con.execute("SELECT " + ",".join(projections["job"]) +
                           " FROM main.job WHERE job_id=?", (root_job_id,)).fetchone()
        if jobs is None:
            _fail("ROOT_JOB_MISSING")
        job = dict(zip(projections["job"], jobs))
        budget._uuid(job["continuation_id"])
        budget._integer(job["execution_generation"], minimum=1)
    tables, operations, scanned = [], {}, [0, 0]
    attempts_count = 0
    for table in ("budget_operation", "budget_attempt", *sorted(set(projections) - {"budget_operation", "budget_attempt"})):
        digest = hashlib.sha256(_encoded([FORMAT_VERSION, table, list(projections[table])]))
        count, initial_bytes = 0, scanned[1]
        for row in _rows(con, table, order, scanned, projections):
            row_sha = _row_sha(table, projections[table], row, scanned)
            digest.update(bytes.fromhex(row_sha))
            count += 1
            if table == "budget_operation":
                budget._operation_facts(row)
                if row["library_id"] != library:
                    _fail("LEDGER_INVALID")
                if root_job_id is not None and row["root_job_id"] == root_job_id:
                    if len(operations) >= MAX_OPERATIONS:
                        _fail("LIMIT")
                    operations[row["operation_id"]] = {
                        "operation_id": row["operation_id"], "continuation_id": row["continuation_id"],
                        "execution_generation": row["execution_generation"], "kind": row["kind"],
                        "suboperation_key_sha256": hashlib.sha256(row["suboperation_key"].encode()).hexdigest(),
                        "row_sha256": row_sha, "attempts": []}
            elif table == "budget_attempt":
                budget._facts(row, with_body=True)
                if row["operation_id"] in operations:
                    attempts_count += 1
                    if attempts_count > MAX_ATTEMPTS:
                        _fail("LIMIT")
                    budget._uuid(row["attempt_id"])
                    budget._integer(row["ordinal"], minimum=1, maximum=128)
                    budget._integer(row["result_bytes"])
                    if row["result_sha256"] is not None:
                        budget._sha(row["result_sha256"])
                    if (row["state"] not in {"reserved", "dispatching", "indeterminate", "settled", "released"}
                            or row["result_storage_state"] not in {"none", "stored", "oversize", "capacity_blocked", "retired"}):
                        _fail("LEDGER_INVALID")
                    operations[row["operation_id"]]["attempts"].append({
                        "attempt_id": row["attempt_id"], "ordinal": row["ordinal"],
                        "state": row["state"], "row_sha256": row_sha,
                        "result_sha256": row["result_sha256"], "result_bytes": row["result_bytes"],
                        "result_storage_state": row["result_storage_state"]})
            elif table == home_recovery_schema.TABLE:
                home_recovery_schema.validate_record(row)
            elif table in readmit_schema.COLUMNS:
                from . import sync_source_readmit_schema2
                sync_source_readmit_schema2.validate_record(table, row)
        tables.append({"name": table, "rows": count, "hashed_bytes": scanned[1] - initial_bytes,
                       "sha256": digest.hexdigest()})
    frontier = {"root_job_id": root_job_id,
        "root_job_row_sha256": None if job is None else _row_sha("job", projections["job"], job, scanned),
        "operations": [operations[key] for key in sorted(operations)]}
    return schema_version, version, {"profile_sha256": _sha(list(profile.identity)),
        "library_id": library, "node_id": node, "schema_sha256": schema_sha,
        "tables": sorted(tables, key=lambda item: item["name"]), "frontier": frontier}


def _checkpoint(store, root_job_id, library_id, node_id):
    directory = store._directory
    if directory is None:
        _fail("STATE_OWNERSHIP_REQUIRED")
    directory.verify()
    try:
        raw = sync_state._read_bytes(directory, sync_state._STATE, sync_state.MAX_STATE_BYTES)
    except FileNotFoundError:
        if root_job_id is not None:
            _fail("CHECKPOINT_INVALID")
        try:
            directory.entry_stat(sync_state._STATE)
        except FileNotFoundError:
            directory.verify()
            return {"state_present": False, "absence_sha256": _sha({"format_version": 1, "state": "absent"}),
                "state_sha256": None, "state_bytes": 0, "canonical_state_sha256": None,
                "state_format_version": None, "source_execution_sha256": None,
                "source_execution_lineage": None, "outboxes": []}
        _fail("CHECKPOINT_CHANGED")
    state = store._read(directory)
    if set(state) != _STATE_FIELDS.get(state["format_version"]):
        _fail("CHECKPOINT_INVALID")
    if (state["binding"]["home_library_id"], state["binding"]["home_node_id"]) != (library_id, node_id):
        _fail("PROFILE_MISMATCH")
    core = state.get("source_execution")
    if core is not None and core["root_job_id"] != root_job_id:
        _fail("CHECKPOINT_INVALID")
    # Only current pending/recovery publications are file references. Media
    # cache descriptor history is preserved in state; no historical media SHA
    # scan or cache miss can create an additional source/model opportunity.
    references = {}
    if state["pending_publication"] is not None:
        item = state["pending_publication"]
        references[item["publication_id"]] = (item, state["legacy_pending_stream"])
    attempt = state["recovery_attempt"]
    if attempt is not None:
        publication_id = attempt["publication_id"]
        if publication_id not in references:
            item = store.find_publication(publication_id, audit=True)
            if item is None:
                _fail("CHECKPOINT_INVALID")
            references[publication_id] = (item, attempt["stream"])
    outboxes, size = [], 0
    for publication_id, (metadata, stream) in sorted(references.items()):
        size += metadata["byte_size"]
        if size > MAX_OUTBOX_TOTAL_BYTES:
            _fail("LIMIT")
        value = store.read_publication(metadata, proved_stream=stream)
        outboxes.append({"publication_id": publication_id, "sha256": metadata["sha256"],
            "byte_size": metadata["byte_size"], "semantic_sha256": _sha(value)})
    if raw != sync_state._read_bytes(directory, sync_state._STATE, sync_state.MAX_STATE_BYTES):
        _fail("CHECKPOINT_CHANGED")
    directory.verify()
    return {"state_present": True, "absence_sha256": None,
        "state_sha256": hashlib.sha256(raw).hexdigest(), "state_bytes": len(raw),
        "canonical_state_sha256": _sha(state), "state_format_version": state["format_version"],
        "source_execution_sha256": None if core is None else _sha(core),
        "source_execution_lineage": None if core is None else {key: core[key] for key in (
            "root_job_id", "continuation_id", "execution_generation")},
        "outboxes": outboxes}


@dataclass(frozen=True, slots=True, repr=False)
class PreservationCapture:
    document_json: str
    sha256: str

    def public(self):
        return json.loads(self.document_json)


@dataclass(frozen=True, slots=True)
class PreservationComparison:
    before_sha256: str
    after_sha256: str
    preserved_sha256: str
    unchanged: bool = True


@dataclass(frozen=True, slots=True)
class RestorePreservationComparison:
    operation_id: str
    before_sha256: str
    after_sha256: str
    preserved_sha256: str
    receipt_row_sha256: str
    unchanged: bool = True


def capture(con, *, profile, store, root_job_id):
    """Read actual guarded SQL/state; return only bounded IDs and digests.

    Caller must enter StateStore.reference_operation() before the SQL
    transaction and keep that actual thread ownership through both captures.
    A standalone exclusive()/reference_lifetime() is not sufficient because
    its thread lock is released and a late SQL -> state wait can deadlock. This
    function does not itself prove worker maintenance ownership, an IMMEDIATE
    writer, a protected backup, or continuity with an older physical DB image.
    Unknown and settled attempts are validated and hashed without conversion.
    """
    if type(con) is not sqlite3.Connection or not con.in_transaction:
        _fail("TRANSACTION_REQUIRED")
    if type(store) is not sync_state.StateStore:
        _fail("CHECKPOINT_INVALID")
    if (type(profile) is not storage_lifecycle.StorageProfile
            or root_job_id is not None and (type(root_job_id) is not int or root_job_id < 1)):
        _fail("PROFILE_MISMATCH")
    if store.profile is None or store.profile.identity != profile.identity:
        _fail("PROFILE_MISMATCH")
    # Never wait for another state's thread owner after the caller has entered
    # SQL. EX's directory marker alone belongs to the process and does not
    # prove this caller holds reference_operation's earlier RLock/SH lifetime.
    owns_thread = getattr(store._guard, "_is_owned", None)
    if (not callable(owns_thread) or not owns_thread()
            or type(store._reference_depth) is not int or store._reference_depth < 1):
        _fail("STATE_OWNERSHIP_REQUIRED")
    try:
        guard = storage_lifecycle.require_guard(profile)
        storage_admin.from_connection(con, profile=profile)
        with store._guard:
            if store._directory is None:
                _fail("STATE_OWNERSHIP_REQUIRED")
            schema_version, version, preserved = _sql_capture(
                con, profile=profile, root_job_id=root_job_id)
            preserved["checkpoint"] = _checkpoint(
                store, root_job_id, preserved["library_id"], preserved["node_id"])
            document = {"format_version": FORMAT_VERSION, "schema_version": schema_version,
                "scope": "local_capture_comparison", "continuity": "not_established",
                "knowledge_version": version, "preserved_sha256": _sha(preserved), **preserved}
            encoded = _encoded(document)
            if len(encoded) > MAX_CERTIFICATE_BYTES:
                _fail("LIMIT")
            guard.verify()
            return PreservationCapture(encoded.decode("utf-8"), hashlib.sha256(encoded).hexdigest())
    except PreservationError:
        raise
    except storage_lifecycle.StorageLifecycleError:
        _fail("PROFILE_MISMATCH")
    except budget.BudgetLedgerError:
        _fail("LEDGER_INVALID")
    except home_recovery_schema.HomeRecoverySchemaError:
        _fail("SCHEMA_UNSUPPORTED")
    except SyncError:
        _fail("CHECKPOINT_INVALID")
    except (sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail("LEDGER_INVALID")


def _certificate(before):
    try:
        if type(before) is not PreservationCapture or len(before.document_json.encode()) > MAX_CERTIFICATE_BYTES:
            _fail("CERTIFICATE_INVALID")
        document = before.public()
        encoded = _encoded(document)
        if (type(document) is not dict or set(document) != _DOCUMENT_FIELDS
                or encoded.decode() != before.document_json or hashlib.sha256(encoded).hexdigest() != before.sha256
                or type(document.get("format_version")) is not int or document["format_version"] != FORMAT_VERSION
                or document.get("schema_version") not in SUPPORTED_SCHEMA_VERSIONS
                or document.get("scope") != "local_capture_comparison" or document.get("continuity") != "not_established"):
            _fail("CERTIFICATE_INVALID")
        preserved = {key: document[key] for key in (
            "profile_sha256", "library_id", "node_id", "schema_sha256", "tables", "frontier", "checkpoint")}
        if _sha(preserved) != document["preserved_sha256"]:
            _fail("CERTIFICATE_INVALID")
        return document
    except PreservationError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail("CERTIFICATE_INVALID")


def verify_unchanged(con, before, *, profile, store):
    """Exact preservation comparison; a local capture never authorizes send.

    SQL step metadata, knowledge rows, library_epoch and kb_revision may
    change. The complete actual state checkpoint stays unchanged until the
    future workflow's postcommit epoch reconciliation step.
    """
    document = _certificate(before)
    after = capture(con, profile=profile, store=store, root_job_id=document["frontier"]["root_job_id"])
    if after.public()["preserved_sha256"] != document["preserved_sha256"]:
        _fail("CHANGED")
    return PreservationComparison(before.sha256, after.sha256, document["preserved_sha256"])


def verify_restore_committed_transition(con, before, expected_record, *, profile, store):
    """Verify the prospective one-receipt append in the caller's transaction.

    No commit occurs here. This is not a trusted historical seal or admission
    grant: the supported writer owns ``before`` and validates actual source
    provenance/business JSON/pins separately. Only the exact typed canonical
    Home record is subtracted from the after projection; all old records and
    every other operational table/state/outbox remain part of the comparison.
    """
    try:
        document = _certificate(before)
        record = home_recovery_schema.validate_record(expected_record)
        if document["schema_version"] != "16":
            _fail("TRANSITION_INVALID")
        after = capture(con, profile=profile, store=store, root_job_id=document["frontier"]["root_job_id"])
        current = after.public()
        checkpoint = document["checkpoint"]
        lineage = checkpoint["source_execution_lineage"]
        if (record["library_id"] != document["library_id"] or record["node_id"] != document["node_id"]
                or current["schema_version"] != "16"
                or record["before_version_json"] != _encoded(document["knowledge_version"]).decode()
                or record["after_version_json"] != _encoded(current["knowledge_version"]).decode()
                or record["frontier_json"] != _encoded(document["frontier"]).decode()
                or record["checkpoint_json"] != _encoded(checkpoint).decode()
                or record["before_operational_sha256"] != document["preserved_sha256"]
                or record["after_operational_sha256"] != document["preserved_sha256"]
                or record["original_core_sha256"] != checkpoint["source_execution_sha256"]
                or record["root_job_id"] != document["frontier"]["root_job_id"]
                or record["continuation_id"] != (None if lineage is None else lineage["continuation_id"])
                or record["execution_generation"] != (None if lineage is None else lineage["execution_generation"])):
            _fail("TRANSITION_INVALID")
        order, _, _, projections = _schema(con)
        table = home_recovery_schema.TABLE
        digest = hashlib.sha256(_encoded([FORMAT_VERSION, table, list(projections[table])]))
        scanned, count, matched, receipt_sha = [0, 0], 0, 0, None
        for row in _rows(con, table, order, scanned, projections):
            home_recovery_schema.validate_record(row)
            if row["operation_id"] == record["operation_id"]:
                if row != record:
                    _fail("TRANSITION_INVALID")
                matched += 1
                receipt_sha = _row_sha(table, projections[table], row, [0, 0])
            else:
                digest.update(bytes.fromhex(_row_sha(table, projections[table], row, scanned)))
                count += 1
        if matched != 1:
            _fail("TRANSITION_INVALID")
        normalized_table = {"name": table, "rows": count, "hashed_bytes": scanned[1], "sha256": digest.hexdigest()}
        normalized = {key: current[key] for key in (
            "profile_sha256", "library_id", "node_id", "schema_sha256", "tables", "frontier", "checkpoint")}
        normalized["tables"] = [normalized_table if row["name"] == table else row for row in normalized["tables"]]
        if _sha(normalized) != document["preserved_sha256"]:
            _fail("CHANGED")
        storage_lifecycle.require_guard(profile).verify()
        return RestorePreservationComparison(record["operation_id"], before.sha256, after.sha256,
                                             document["preserved_sha256"], receipt_sha)
    except PreservationError:
        raise
    except (home_recovery_schema.HomeRecoveryRecordError, home_recovery_schema.HomeRecoverySchemaError,
            sqlite3.Error, OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail("TRANSITION_INVALID")
