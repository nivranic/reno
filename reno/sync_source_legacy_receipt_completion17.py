"""Finish only an original journal-selected wire1 receipt, without another RPC.

This independent capture owns its own seal and context. Shared physical
closure algorithms are reused without constructing a foreign sealed capture.
The original result must already exist in the actual reserved journal; an
intent alone never creates a result, HTTP ticket, paid grant or State effect.

Authority's protected-receipt classification and head-repair exact-type gates
must explicitly recognize this producer before this factory can publish.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import time

from . import budget_writer_witness as sql, db, home_creation_recovery17 as physical
from . import home_recovery17 as restore, home_recovery_runtime17 as runtime
from . import home_recovery_schema as restore_schema, source_preservation_witness as witness
from . import storage_root_inputs as inputs, storage_source_continuity_roots as cloud_roots
from . import sync_source_authority as authority, sync_source_binding17 as binding
from . import sync_source_claim as engine, sync_source_admission_history1 as history
from . import sync_source_legacy_intake17 as intake, sync_source_readmit_contract as wire
from .sync_protocol import SyncError

_SEAL = object()
_CURRENT = ContextVar("reno_original_legacy_receipt17_capture", default=None)
_CONTEXT = ContextVar("reno_original_legacy_receipt17_context", default=None)
_ACQUIRING = ContextVar("reno_original_legacy_receipt17_acquiring", default=None)
_PRODUCER = ContextVar("reno_original_legacy_receipt17_producer", default=None)
_PRODUCER_CONTEXT = ContextVar("reno_original_legacy_receipt17_producer_context", default=None)
_ROOT_COLUMNS = ("kind", "scope_key", "video_id", "payload_json", "continuation_id", "execution_generation")


def _fail(code):
    raise SyncError("Original legacy receipt completion could not be verified",
        "SYNC_SOURCE_LEGACY_RECEIPT17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("ORIGINAL_CHANGED")


def _root(con, core):
    # Closing an already received fact remains possible after manual pause or
    # lease loss. Fresh worker/claim permission belongs to subsequent StateCAS.
    binding._root(con, core)
    row = con.execute("SELECT " + ",".join(_ROOT_COLUMNS)
        + " FROM main.job WHERE job_id=?", (core["root_job_id"],)).fetchone()
    if row is None:
        _fail("ROOT_REQUIRED")
    return tuple(row)


class OriginalReceiptRecoveryCapture17:
    """Actual sole receipt tail; no foreign creation/restore/paid capability."""

    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    def _initialize(self, owner, profile, store, identifier, request_sha256, expected):
        self.owner, self._profile, self._store = owner, profile, store
        self._folder, self._identifier, self._expected = "receipts", identifier, expected
        self._seal, self._active, self._owner_context = _SEAL, True, physical._owner()
        self._context_token, self._creator = None, None
        self._actual, self._record, self._base, self._reserved = physical._frame(
            owner, "receipts", identifier, expected)
        self._initial_entries = deepcopy(self._actual["entries"])
        self._initial_state = store.read()
        self._intent = history._committed(self._actual["entries"], "requests", identifier)
        self._origin = intake._origin(self._actual["entries"], self._intent)
        self._core = deepcopy(self._origin["core"])
        request = self._intent["request"]
        if (self._intent["format_version"] != 1 or self._record["format_version"] != 1
                or self._record["result"]["format_version"] != 1
                or request["kind"] != "source_readmit" or request.get("format_version", 1) != 1):
            _fail("ORIGINAL_WIRE1_REQUIRED")
        _same((self._intent["request_sha256"], self._record["request_sha256"]),
            (request_sha256, request_sha256))
        _same(wire.request_sha256(request), request_sha256)
        _same((self._record["record_id"], self._record["result_sha256"]),
            (identifier, authority.digest(self._record["result"])))
        if self._record["parent_defer"] is not None:
            _fail("READMIT_PARENT_UNEXPECTED")
        _same(wire.normalize_result(request, self._record["result"]), self._record["result"])
        history._original(request, self._origin)
        state = self._initial_state
        if state is None or state["format_version"] != 5:
            _fail("STATE_REQUIRED")
        _same(state["source_execution"], self._core)
        if state["source_authority"] is None:
            _fail("ORIGIN_REQUIRED")
        _same((state["source_authority"]["core_sha256"], state["source_authority"]["origin"]),
            (authority.digest(self._core), authority.record_ref(self._origin)))
        # Current State is held unchanged, not treated as the original request
        # before-image. A later pause/revision is no permission to edit it.
        # _historical_facts independently checks the actual historical before.
        pending = state["source_recovery_pending"]
        if pending is not None:
            _same(pending, {"format_version": 1, "kind": "source_readmit", "request_id": identifier,
                "request_sha256": request_sha256, "core_sha256": authority.digest(self._core),
                "intent_sha256": authority.digest(self._intent)})
        self._signature = tuple(witness._digest(value) for value in
            (self._record, self._intent, self._origin, self._core, self._initial_state))
        self._started = time.monotonic()

    def _context(self):
        if (type(self) is not OriginalReceiptRecoveryCapture17 or self._seal is not _SEAL
                or not self._active or _CURRENT.get() is not self or _CONTEXT.get() is not self
                or self._owner_context != physical._owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        self._read_control()
        _same(self.owner.profile.identity, self._profile.identity)
        _same(self._store.profile.identity, self._profile.identity)
        if self.owner.lock.store is not self._store:
            _fail("FACTORY_REQUIRED")
        _same(self._signature, tuple(witness._digest(value) for value in
            (self._record, self._intent, self._origin, self._core, self._initial_state)))
        if _ACQUIRING.get() is not self:
            if (type(getattr(self, "_facts_signature", None)) is not tuple
                    or type(getattr(self, "_manual_rows", None)) is not tuple
                    or type(getattr(self, "_history_signature", None)) is not str):
                _fail("FACTS_REQUIRED")
            _same(self._facts_signature, (witness._digest(self._manual_rows), self._history_signature))

    @property
    def record(self):
        self._context()
        return deepcopy(self._record)

    @property
    def core(self):
        self._context()
        return deepcopy(self._core)

    @property
    def record_state(self):
        self._context()
        return self._actual["entries"]["receipts"][self._identifier]["state"]

    # These mechanical readers/refresh algorithms do not construct a sealed
    # capability or accept an effect DTO. They invoke this class's independent
    # context, producer, root SQL and declared-restoration validators.
    _read_control = physical.OriginalCreationRecoveryCapture17._read_control
    _home_capture = physical.OriginalCreationRecoveryCapture17._home_capture
    _preserved_prefix = physical.OriginalCreationRecoveryCapture17._preserved_prefix
    _progress = physical.OriginalCreationRecoveryCapture17._progress
    _refresh = physical.OriginalCreationRecoveryCapture17._refresh
    _fixed = physical.OriginalCreationRecoveryCapture17._fixed
    verify_for_creation_commit = physical.OriginalCreationRecoveryCapture17.verify_for_creation_commit
    check_head_repair = physical.OriginalCreationRecoveryCapture17.check_head_repair
    adopt_head_repair = physical.OriginalCreationRecoveryCapture17.adopt_head_repair

    def verify_source(self):
        physical.OriginalCreationRecoveryCapture17.verify_source(self)
        # Actual history/SQL facts are rebuilt in a read window; the short
        # writer below only rechecks its fixed original rows and live pins.
        with sql.connection(self._profile) as con:
            self._historical_facts(con)
        self._verify_declared_restorations()
        self._home.verify_source()
        witness._verify_state_full(self._store, self._state)

    def _acquire(self, stack):
        self._stack = stack
        self._state = witness._state(self._store)
        self._pins = runtime._RuntimePins(stack, self._profile, self._read_control)
        with sql.connection(self._profile) as con:
            con.execute("PRAGMA query_only=ON")
            self._root_projection = _root(con, self._core)
            self._schema_sha = restore._actual_schema(con)
            self._cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            self._version = db.knowledge_version(con)
            _same(self._version["library_epoch"], self._initial_state["local_epoch"])
            self._projection = inputs.read_continuity_projection(con)
            self._identity = witness._IdentityCapture(self._store, con, stack)
            self._checkpoint = witness._CheckpointCache(self._store, self._state, stack)
            self._cloud = stack.enter_context(cloud_roots.capture_locked(self._profile,
                sql_projection=self._projection, limits=cloud_roots.CaptureLimits(deadline_seconds=120),
                check_cancelled=self._read_control))
            if self._cloud.unknown or self._cloud.summary["presence"] not in {"present", "absent"}:
                _fail("CLOUD_INCOMPLETE")
            self._home = self._home_capture(stack, self._actual)
            witness._resolve_state(self._state["value"], self._home)
            self._records, self._documents = [], []
            if self._core["local_epoch"] != self._initial_state["local_epoch"]:
                rows = con.execute("SELECT operation_id,result_sha256 FROM main."
                    + restore_schema.TABLE + " WHERE library_id=? AND after_library_epoch=?",
                    (self._version["library_id"], self._version["library_epoch"])).fetchall()
                if len(rows) != 1:
                    _fail("RESTORE_REQUIRED")
                selected = restore._record(con, rows[0][0])
                self._records = runtime._chain(con, selected, self._version)
                for row, parsed in self._records:
                    if parsed.wrapper is None:
                        runtime._legacy_row(self._pins, row, self._profile)
                        self._documents.append(None)
                    else:
                        document = runtime._completed_row(self._pins, row, self._profile, con)
                        runtime._current_prefix(document, row["operation_id"], self._pins,
                            self._profile, self._cloud, self._home)
                        self._documents.append(document)
            self._row_hashes = tuple(restore._row_sha(row) for row, _ in self._records)
            self._verify_declared_restorations()
            self._historical_facts(con, capture=True)
        self.verify_source()

    def _verify_declared_restorations(self):
        request, provenance = self._intent["request"], self._intent["preservation_basis"]
        if request["current_home_epoch"] == self._core["local_epoch"]:
            _same((request["preservation"], provenance), (None, None))
        else:
            if provenance is None:
                _fail("RESTORE_REQUIRED")
            # A bare legacy16 comparison is retained/read above, but cannot be
            # relabeled a trusted17 certificate. Its separate intake proof is
            # still required before that historical variant can be installed.
            _same(request["preservation"], self._preserved_prefix(provenance,
                to_epoch=request["current_home_epoch"]))

    def _historical_facts(self, con, *, capture=False):
        request, result = self._intent["request"], self._record["result"]
        entries = self._actual["entries"]
        chain, consumed, records, manual_rows = intake._history(entries, self._origin,
            result["previous_admission_id"], con, self._initial_state)
        previous = None if not chain else chain[-1][1]
        history._before(self._intent, self._origin, previous)
        if previous is not None and result["attempt_basis"] == "held_claim":
            _same(result["attempts"], previous["result"]["attempts"])
        used = {fact["control_ref"]["record_id"] for fact in consumed}
        selected_manual = list(manual_rows)
        selected = intake._consumed(entries, self._origin, self._intent, previous,
            list(records), selected_manual, con)
        if used & {fact["control_ref"]["record_id"] for fact in selected}:
            _fail("CONTROL_ALREADY_CONSUMED")
        for intent, receipt in chain:
            if intent["request"]["current_home_epoch"] != self._core["local_epoch"]:
                provenance = intent["preservation_basis"]
                if provenance is None:
                    _fail("RESTORE_REQUIRED")
                _same(intent["request"]["preservation"], self._preserved_prefix(provenance,
                    to_epoch=intent["request"]["current_home_epoch"]))
        facts = (chain, consumed, tuple(selected), tuple(selected_manual))
        if capture:
            self._history_signature = witness._digest(facts)
            self._manual_rows = tuple(deepcopy(selected_manual))
            self._facts_signature = (witness._digest(self._manual_rows), self._history_signature)
        else:
            _same(witness._digest(facts), self._history_signature)

    def _fixed_sql(self, con):
        _same(_root(con, self._core), self._root_projection)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
        _same(db.knowledge_version(con), self._version)
        _same(inputs.read_continuity_projection(con), self._projection)
        self._identity.verify_for_commit(con)
        if self._records:
            selected = restore._record(con, self._records[-1][0]["operation_id"])
            if selected is None:
                _fail("RESTORE_REQUIRED")
            chain = runtime._chain(con, selected, self._version)
            _same(tuple(restore._row_sha(row) for row, _ in chain), self._row_hashes)
        from . import sync_source_resume_basis2 as manual
        for identifier, record in self._manual_rows:
            _same(manual._row(con, self._core, identifier), record)

    def _producer(self, producer):
        if (type(producer) is not ReceiptCompletionGuard17 or producer.owner is not self.owner
                or producer._recovery_capture is not self):
            _fail("COMPLETION_PRODUCER_REQUIRED")
        producer._context()
        if self._creator not in (None, producer):
            _fail("COMPLETION_PRODUCER_REQUIRED")
        self._creator = producer


class ReceiptCompletionGuard17:
    """Only this live capture can finish its original immutable receipt."""

    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    def _context(self):
        if (type(self) is not ReceiptCompletionGuard17 or self._seal is not _SEAL or not self._active
                or _PRODUCER.get() is not self or _PRODUCER_CONTEXT.get() is not self
                or self._owner_context != physical._owner()
                or type(self._recovery_capture) is not OriginalReceiptRecoveryCapture17
                or self._recovery_capture is not _CURRENT.get() or self.owner is not self._recovery_capture.owner):
            _fail("FACTORY_REQUIRED")
        try:
            _PRODUCER_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _PRODUCER_CONTEXT.set(self)
        self.owner._check()
        self.owner.lock.verify()

    @property
    def record(self):
        self._context()
        return self._recovery_capture.record

    def check(self, record=None):
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        if record is not None:
            _same(record, self.record)
        captured = self._recovery_capture
        captured._producer(self)
        captured.verify_source()
        with sql.connection(self.profile, write=True) as con:
            permit = sql.require_writer(con, profile=self.profile)
            captured.verify_for_creation_commit(con, writer_permit=permit, producer=self)
        captured.verify_source()

    def check_head_repair(self, owner):
        self._context()
        self._recovery_capture.check_head_repair(owner, self)

    def adopt_head_repair(self, owner):
        self._context()
        self._recovery_capture.adopt_head_repair(owner, self)


@contextmanager
def capture_original_legacy_receipt17_locked(*, profile, store, request_id, request_sha256,
        expected_record_sha256=None):
    profile = intake._selectors(profile, store)
    if _CURRENT.get() is not None:
        _fail("FACTORY_REQUIRED")
    authority._uuid(request_id)
    authority._sha(request_sha256)
    if expected_record_sha256 is not None:
        authority._sha(expected_record_sha256)
    with ExitStack() as stack:
        stack.enter_context(store.reference_operation())
        owner = intake._owned(stack, store)
        captured = object.__new__(OriginalReceiptRecoveryCapture17)
        try:
            captured._initialize(owner, profile, store, request_id, request_sha256, expected_record_sha256)
        except physical.CreationRecoveryError:
            _fail("ORIGINAL_TAIL_INVALID")
        token = _CURRENT.set(captured)
        captured._context_token = _CONTEXT.set(captured)
        try:
            acquiring = _ACQUIRING.set(captured)
            try:
                captured._acquire(stack)
            finally:
                _ACQUIRING.reset(acquiring)
            captured.verify_source()
            yield captured
            captured.verify_source()
        except physical.CreationRecoveryError:
            _fail("ORIGINAL_TAIL_INVALID")
        finally:
            captured._active = False
            _CONTEXT.reset(captured._context_token)
            _CURRENT.reset(token)


def require_receipt_completion17(owner, kind, record, producer, *, entries):
    if (kind != "receipts" or type(producer) is not ReceiptCompletionGuard17
            or producer.owner is not owner):
        _fail("COMPLETION_PRODUCER_REQUIRED")
    producer.check(record)
    intent = history._committed(entries, "requests", record["record_id"])
    _same(intent, producer._recovery_capture._intent)


def complete_original_legacy_receipt17_locked(*, profile, store, request_id, request_sha256,
        expected_record_sha256=None):
    """Self-read one original tail; return only its saved record/ref facts."""
    profile = intake._selectors(profile, store)
    authority._uuid(request_id)
    authority._sha(request_sha256)
    if expected_record_sha256 is not None:
        authority._sha(expected_record_sha256)
    with store.reference_operation(), ExitStack() as stack:
        owner = intake._owned(stack, store)
        observed = owner.observe_readonly()
        item = observed["entries"]["receipts"].get(request_id)
        if item is None:
            _fail("ORIGINAL_RESULT_REQUIRED")
        if item["state"] == "committed" and observed["head_status"] == "complete" and not observed["incomplete"]:
            return intake.read_legacy_http_receipt17_locked(profile=profile, store=store,
                request_id=request_id, request_sha256=request_sha256,
                expected_record_sha256=expected_record_sha256)
        captured = stack.enter_context(capture_original_legacy_receipt17_locked(profile=profile, store=store,
            request_id=request_id, request_sha256=request_sha256, expected_record_sha256=expected_record_sha256))
        guard = object.__new__(ReceiptCompletionGuard17)
        guard._recovery_capture, guard.owner = captured, captured.owner
        guard.profile, guard.store = profile, store
        guard._seal, guard._active, guard._owner_context = _SEAL, True, physical._owner()
        token = _PRODUCER.set(guard)
        guard._context_token = _PRODUCER_CONTEXT.set(guard)
        try:
            guard.check()
            record = captured.record
            saved = guard.owner.publish("receipts", record, producer=guard)
            _same(saved, record)
            guard.check()
            return {"record": deepcopy(saved), "ref": authority.record_ref(saved)}
        finally:
            guard._active = False
            _PRODUCER_CONTEXT.reset(guard._context_token)
            _PRODUCER.reset(token)
