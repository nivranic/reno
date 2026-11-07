"""Original local source failure proofs; no provider, URL or path diagnostics.

Cloud stores an authenticated Home assertion, not an independently verified
provider bill. Only this local reader can prove the original paid failure.
"""
from dataclasses import dataclass
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import os
import threading

from . import budget, db, source_contract, source_resolution_contract as resolver, storage_admin, storage_lifecycle
from .budget_contract import BudgetDeferred
from .sync_protocol import SyncError


_SEAL = object()
_CAPTURE17_SEAL = object()
_CAPTURE17 = ContextVar("reno_source_failure_capture17", default=None)
_CAPTURE17_CONTEXT = ContextVar("reno_source_failure_capture17_context", default=None)
_FIELDS = frozenset(("format_version", "operation_id", "attempt_id", "ordinal", "input_sha256",
    "request_sha256", "settlement_sha256", "result_sha256", "operation_kind", "segment",
    "source_contract_version", "resolver_contract_version", "resolver_phase", "error_class"))
_ERRORS = frozenset(("invalid_url", "source_unavailable", "rate_limited", "network", "media_invalid"))


def _fail():
    raise SyncError("Original source failure is unproved", "SYNC_SOURCE_FAILURE_UNPROVED", 409) from None


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def normalize_failure(value):
    """Pure bounded wire shape; these fields alone do not grant local clear."""
    try:
        if type(value) is not dict or set(value) != _FIELDS or type(value["format_version"]) is not int or value["format_version"] != 1:
            _fail()
        if len(_json(value).encode("utf-8")) > 4096:
            _fail()
        for name in ("operation_id", "attempt_id"):
            budget._uuid(value[name], "BUDGET_REQUEST_INVALID")
        budget._integer(value["ordinal"], minimum=1, maximum=128)
        for name in ("input_sha256", "request_sha256", "settlement_sha256", "result_sha256"):
            budget._sha(value[name])
        kind, segment = value["operation_kind"], value["segment"]
        if type(kind) is not str or kind not in {"source_resolution", "source_metadata", "download"}:
            _fail()
        if type(value["error_class"]) is not str or value["error_class"] not in _ERRORS:
            _fail()
        if kind == "source_resolution":
            if (segment is not None or value["source_contract_version"] is not None
                    or type(value["resolver_contract_version"]) is not int or value["resolver_contract_version"] != 1
                    or type(value["resolver_phase"]) is not str or value["resolver_phase"] not in {"dns", "headers"}
                    or value["error_class"] == "media_invalid"):
                _fail()
        else:
            if (type(value["source_contract_version"]) is not int or value["source_contract_version"] != 1
                    or value["resolver_contract_version"] is not None or value["resolver_phase"] is not None
                    or kind == "source_metadata" and segment is not None
                    or kind == "download" and (type(segment) is not str or segment not in {"video", "audio"})
                    or segment == "audio" and value["error_class"] != "media_invalid"):
                _fail()
        return json.loads(_json(value))
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail()


@dataclass(frozen=True, slots=True, repr=False)
class SourceFailureProof:
    core_sha256: str
    payload_json: str
    _seal: object

    def __post_init__(self):
        if self._seal is not _SEAL:
            _fail()

    def public(self):
        return json.loads(self.payload_json)


def _binding(value):
    from .source_resolution_budget import _binding as validate
    return validate(value)


def _operations(binding):
    core, prefix = binding.core, "home:" + binding.namespace + ":"
    from .sync_source_binding17 import HomeBindingFacts17
    home17 = type(binding) is HomeBindingFacts17
    with storage_lifecycle.shared(binding.profile):
        con = db.connect(binding.profile.database)
        try:
            rows = con.execute("SELECT kind,suboperation_key,input_sha256 FROM budget_operation "
                "WHERE library_id=? AND root_job_id=? AND continuation_id=? AND execution_generation=? "
                + ("" if home17 else "AND substr(suboperation_key,1,?)=?"),
                (core["binding"]["home_library_id"], core["root_job_id"], core["continuation_id"],
                 core["execution_generation"]) + (() if home17 else (len(prefix), prefix))).fetchall()
            # Keep the exact stored key; calling binding.operation here would
            # prefix an already original namespaced key a second time.
            return tuple(budget.OperationSpec(core["continuation_id"], core["execution_generation"],
                row["kind"], row["suboperation_key"], row["input_sha256"], core["root_job_id"]) for row in rows)
        finally:
            con.close()


def _logical_sha(binding, kind, metadata=None):
    value = {"kind": kind, "source": binding.submitted_url, "runner_contract": "yt_dlp_v1"}
    if metadata is not None:
        value["genesis_metadata_sha256"] = hashlib.sha256(metadata.encode()).hexdigest()
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _original_operation(binding, operation):
    """A matching namespace is insufficient without the original logical input."""
    if operation.kind == "source_resolution":
        expected = binding.resolution_operation
    elif operation.kind == "source_metadata":
        expected = binding.operation("source_metadata", "ingest:metadata:v1", _logical_sha(binding, "source_metadata"))
    else:
        metadata_op = binding.operation("source_metadata", "ingest:metadata:v1", _logical_sha(binding, "source_metadata"))
        view = budget.get_operation(metadata_op, profile=binding.profile)
        original = None
        for item in () if view is None else view.attempts:
            if item.state == "settled" and item.response_complete and item.outcome == "source_step_complete":
                receipt = budget.get_result(item.attempt_id, item.request_sha256, profile=binding.profile)
                if receipt.storage_state != "stored" or receipt.text is None:
                    raise BudgetDeferred("result_capacity", operation_id=view.operation_id)
                original = source_contract.decode_envelope(receipt.text, request_sha256=item.request_sha256,
                    input_sha256=metadata_op.input_sha256, kind="source_metadata", segment=None)
                if item.source_contract_version != 1 or not original.successful:
                    _fail()
                break
        if original is None:
            _fail()
        video_id = json.loads(original.metadata_json)["id"]
        expected = binding.operation("download", "ingest:media:v1:" + video_id,
                                     _logical_sha(binding, "download", original.metadata_json))
    if operation != expected:
        _fail()


def _candidate(binding, operation, item):
    _original_operation(binding, operation)
    receipt = budget.get_result(item.attempt_id, item.request_sha256, profile=binding.profile)
    if receipt.storage_state != "stored" or receipt.text is None:
        raise BudgetDeferred("result_capacity", operation_id=budget.get_operation(operation, profile=binding.profile).operation_id)
    values = {"format_version": 1, "operation_id": budget.get_operation(operation, profile=binding.profile).operation_id,
        "attempt_id": item.attempt_id, "ordinal": item.ordinal, "input_sha256": operation.input_sha256,
        "request_sha256": item.request_sha256, "settlement_sha256": receipt.settlement_sha256,
        "result_sha256": receipt.result_sha256, "operation_kind": operation.kind, "segment": item.download_segment,
        "source_contract_version": item.source_contract_version, "resolver_contract_version": item.resolver_contract_version,
        "resolver_phase": item.resolver_phase}
    if operation.kind == "source_resolution":
        if item.resolver_contract_version != 1:
            raise BudgetDeferred("unsupported_capability", operation_id=values["operation_id"])
        view = budget.get_operation(operation, profile=binding.profile)
        keys = dict(request_sha256=item.request_sha256, input_sha256=operation.input_sha256,
                    phase=item.resolver_phase, max_attempts=view.max_attempts)
        plan = resolver.decode_plan(receipt.text, **keys)
        result = resolver.decode_envelope(receipt.text, **keys)
        if plan.submitted_url != binding.submitted_url:
            _fail()
        error = ("invalid_url" if result.failure_class in {"dns_rejected", "redirect_invalid", "resource_invalid", "header_invalid"}
                 else "rate_limited" if result.status == 429 else "network" if result.retryable else "source_unavailable")
    else:
        if item.source_contract_version != 1:
            raise BudgetDeferred("unsupported_capability", operation_id=values["operation_id"])
        result = source_contract.decode_envelope(receipt.text, request_sha256=item.request_sha256,
            input_sha256=operation.input_sha256, kind=operation.kind, segment=item.download_segment)
        # A normal separate-audio failure permits the known container fallback;
        # it does not by itself prove the entire source could not be imported.
        if item.download_segment == "audio" and result.returncode > 0 and result.failure_class == "process_failure":
            return None
        error = "media_invalid" if result.failure_class == "artifact_invalid" else (
            "invalid_url" if result.failure_class in {"invalid_output", "unsafe_metadata"} else "source_unavailable")
    if receipt.outcome != "terminal_error" or not result.response_complete or result.successful:
        _fail()
    values["error_class"] = error
    return SourceFailureProof(hashlib.sha256(binding.core_json.encode("utf-8")).hexdigest(),
                              _json(normalize_failure(values)), _SEAL)


def known_failure(binding, *, attempt_id=None):
    """Read paid facts before fresh gates; unknown still prohibits terminal."""
    binding = _binding(binding)
    if attempt_id is not None:
        budget._uuid(attempt_id, "BUDGET_REQUEST_INVALID")
    candidates, observed = [], []
    for operation in _operations(binding):
        view = budget.get_operation(operation, profile=binding.profile)
        if view is None:
            _fail()
        if any(item.state in {"dispatching", "indeterminate"} for item in view.attempts):
            raise BudgetDeferred("indeterminate", operation_id=view.operation_id)
        observed.append((operation, view))
    for operation, view in observed:
        # Every original namespaced dispatch is a blocker, including kinds
        # which cannot independently supply a source terminal witness.
        if operation.kind not in {"source_resolution", "source_metadata", "download"}:
            continue
        # A later reservation/retry cannot be erased by an older normal failure.
        latest = view.attempts[-1] if view.attempts else None
        if (latest is not None and (attempt_id is None or latest.attempt_id == attempt_id)
                and latest.state == "settled" and latest.response_complete and latest.outcome == "terminal_error"):
            candidate = _candidate(binding, operation, latest)
            if candidate is not None:
                candidates.append(candidate)
    if attempt_id is not None and not candidates:
        _fail()
    if len(candidates) > 1:
        _fail()  # Ambiguous local evidence needs an exact original selector.
    return candidates[0] if candidates else None


def revalidate_failure(binding, proof):
    """Neither a mutable DTO nor a stale opaque local witness grants clear."""
    if type(proof) is SourceFailureProof:
        if proof._seal is not _SEAL:
            _fail()
        value = normalize_failure(proof.public())
    else:
        value = normalize_failure(proof)
    current = known_failure(binding, attempt_id=value["attempt_id"])
    if current is None or current.public() != value or type(proof) is SourceFailureProof and current.core_sha256 != proof.core_sha256:
        _fail()
    return current


def _failure_projection17(connection, core, attempt_columns, selected_attempt):
    """Bounded original lineage scalars and one retained source result.

    Model response bodies are irrelevant to source failure. Their attempt
    states still prevent clearing a lineage with an unknown physical effect.
    """
    parameters = (core["binding"]["home_library_id"], core["root_job_id"],
                  core["continuation_id"], core["execution_generation"])
    operations = connection.execute("SELECT * FROM main.budget_operation WHERE library_id=? AND root_job_id=? "
        "AND continuation_id=? AND execution_generation=? ORDER BY operation_id LIMIT 4097", parameters).fetchall()
    if len(operations) > 4096:
        _fail()
    attempts = []
    quoted = ",".join('a."' + name.replace('"', '""') + '"' for name in attempt_columns)
    rows = connection.execute("SELECT " + quoted + " FROM main.budget_attempt a JOIN main.budget_operation o "
        "USING(operation_id) WHERE o.library_id=? AND o.root_job_id=? AND o.continuation_id=? "
        "AND o.execution_generation=? ORDER BY a.operation_id,a.ordinal LIMIT 4097", parameters).fetchall()
    if len(rows) > 4096:
        _fail()
    for row in rows:
        attempts.append(tuple(row))
    selected = connection.execute("SELECT result_text FROM main.budget_attempt WHERE attempt_id=?",
                                  (selected_attempt,)).fetchone()
    if selected is None or type(selected[0]) is not str or len(selected[0].encode()) > source_contract.ENVELOPE_LIMIT:
        _fail()
    return tuple(tuple(row) for row in operations), tuple(attempts), selected[0]


class SourceFailureCapture17:
    """Actual paid failure facts; no new admission or mutable State/head pin."""
    __slots__ = ("_seal", "_binding", "_proof", "_projection", "_columns", "_owner", "_active", "_token")

    def __init__(self, *, seal, binding, proof, projection, columns):
        if seal is not _CAPTURE17_SEAL:
            _fail()
        self._seal, self._binding, self._proof = seal, binding, proof
        self._projection, self._columns = projection, columns
        self._owner, self._active, self._token = (os.getpid(), threading.get_ident()), True, None

    def _check(self):
        if (type(self) is not SourceFailureCapture17 or self._seal is not _CAPTURE17_SEAL or not self._active
                or _CAPTURE17.get() is not self or _CAPTURE17_CONTEXT.get() is not self
                or self._owner != (os.getpid(), threading.get_ident())):
            _fail()
        try:
            _CAPTURE17_CONTEXT.reset(self._token)
        except (ValueError, RuntimeError):
            _fail()
        self._token = _CAPTURE17_CONTEXT.set(self)

    @property
    def proof(self):
        self._check()
        return self._proof

    def _fixed(self, connection, writer):
        from . import budget_writer_witness as sql
        self._check()
        if sql.require_writer(connection, profile=self._binding.profile) is not writer or writer.schema_version != 17:
            _fail()
        self._binding.check_stable(connection=connection)
        if _failure_projection17(connection, self._binding.core, self._columns,
                self._proof.public()["attempt_id"]) != self._projection:
            _fail()
        sql.check(writer, connection=connection, profile=self._binding.profile)

    def verify_for_commit(self, connection, *, writer_permit):
        from . import budget_writer_witness as sql
        self._fixed(connection, writer_permit)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
                                  connection=connection, profile=self._binding.profile)

    def verify_source(self):
        from . import budget_writer_witness as sql
        if sql.in_write_window():
            _fail()
        self._check()
        if revalidate_failure(self._binding, self._proof).public() != self._proof.public():
            _fail()
        with sql.connection(self._binding.profile) as connection:
            self._binding.check_stable(connection=connection)
            if _failure_projection17(connection, self._binding.core, self._columns,
                    self._proof.public()["attempt_id"]) != self._projection:
                _fail()


@contextmanager
def capture_failure17_locked(*, profile, store, attempt_id=None, root_job_id=None,
        continuation_id=None, execution_generation=None):
    """Self-read active or exact completed original lineage; never accept DTOs."""
    from . import budget_writer_witness as sql, sync_source_binding17 as bindings, sync_state
    if sql.in_write_window() or _CAPTURE17.get() is not None:
        _fail()
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail()
    history = (root_job_id, continuation_id, execution_generation)
    with store.reference_operation():
        if all(value is None for value in history):
            core = store.read().get("source_execution")
            if core is None:
                _fail()
            binding = bindings.capture_home_binding_locked(profile=profile, store=store, item_id=core["item_id"])
        elif all(value is not None for value in history):
            binding = bindings.capture_completed_home_binding_locked(profile=profile, store=store,
                root_job_id=root_job_id, continuation_id=continuation_id,
                execution_generation=execution_generation, terminal_kind="source_failed")
        else:
            _fail()
        proof = known_failure(binding, attempt_id=attempt_id)
        if type(proof) is not SourceFailureProof:
            _fail()
        with sql.connection(profile) as connection:
            columns = tuple(row[1] for row in connection.execute("PRAGMA main.table_info(budget_attempt)")
                            if row[1] != "result_text")
            projection = _failure_projection17(connection, binding.core, columns, proof.public()["attempt_id"])
        captured = SourceFailureCapture17(seal=_CAPTURE17_SEAL, binding=binding, proof=proof,
                                         projection=projection, columns=columns)
        token = _CAPTURE17.set(captured)
        captured._token = _CAPTURE17_CONTEXT.set(captured)
        try:
            captured.verify_source()
            yield captured
            captured.verify_source()
        finally:
            captured._active = False
            _CAPTURE17_CONTEXT.reset(captured._token)
            _CAPTURE17.reset(token)


@contextmanager
def failure_clear_guard(binding, proof):
    """Pin original paid facts and the actual DB epoch across local state CAS.

    The StateStore caller enters this after its own state lock. This guard then
    owns a short SQL writer until the state write finishes, without performing
    any database mutation or network request. Original receipt delivery remains
    possible outside this guard after a restore; clearing an old core does not.
    """
    from . import sync_access, sync_source_execution as authority
    binding = _binding(binding)
    if binding.store.profile is None or binding.store.profile.identity != binding.profile.identity:
        _fail()
    with storage_admin.connection(binding.profile) as con:
        con.execute("BEGIN IMMEDIATE")
        try:
            current = authority._job(binding.profile, connection=con, fresh_admission=False)
            core = binding.core
            if (current["job_id"], current["continuation_id"], current["execution_generation"]) != (
                    core["root_job_id"], core["continuation_id"], core["execution_generation"]):
                _fail()
            authority._peer(core["binding"])
            local, version = sync_access.identity(con), db.knowledge_version(con)
            if (local["library_id"], local["node_id"], version["library_epoch"]) != (
                    core["binding"]["home_library_id"], core["binding"]["home_node_id"], core["local_epoch"]):
                _fail()
            binding.store.assert_source_execution(core)
            revalidate_failure(binding, proof)
            yield
        finally:
            con.rollback()
