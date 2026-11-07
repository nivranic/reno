"""Original-defer local timer intent and genuine due receipt producers.

Timers never create task_request, send an RPC, change a budget operation or
clear the old wait. The permanent due fact is saved before a resume control.
An existing committed receipt is historical replay with its original clock.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
from datetime import datetime, timedelta, timezone

from . import budget_writer_witness as sql, storage_lifecycle as lifecycle
from . import sync_source_authority as permanent, sync_source_claim as engine
from . import sync_source_genesis17 as genesis, sync_source_readmit_contract as wire
from . import sync_source_readmit_local2 as local, sync_state, sync_queue as queue, tasks

_SEAL = object()
_CURRENT = ContextVar("reno_actual_timer2_capture", default=None)
_PRODUCER = ContextVar("reno_actual_timer2_producer", default=None)


def _fail(code):
    local._fail("TIMER_" + code)


def _before(state, core, claim):
    recovery = state["recovery"]
    return {"binding": deepcopy(state["binding"]), "local_epoch": state["local_epoch"],
        "core_sha256": permanent.digest(core), "admission_ref": deepcopy(state["source_admission"]),
        "recovery_generation": recovery["generation"], "recovery_stream": deepcopy(recovery["stream"]),
        "control_revision": recovery["revision"], "claim": deepcopy(claim),
        "budget_pending_sha256": None if state["budget_pending"] is None else permanent.digest(state["budget_pending"]),
        "source_control_pending_sha256": None if state["source_control_pending"] is None else permanent.digest(state["source_control_pending"]),
        "pending_publication": deepcopy(state["pending_publication"]), "source_authority": deepcopy(state["source_authority"])}


def _selected(owner, defer_request_id, origin_ref=None, *, resume=False):
    key = wire.timer_operation_id(defer_request_id)
    try:
        observed = owner.observe()
    except Exception as exc:
        if not resume or getattr(exc, "code", None) != "SYNC_SOURCE_AUTHORITY_HEAD_INCOMPLETE":
            raise
        # Only this original timer's exact immutable final journal event may
        # finish interrupted head publication. The ordinary reader never does.
        ledger = owner._directory(owner.root, permanent.DIRECTORY)
        journal = owner._directory(ledger, "journal")
        permanent._names(journal, lambda name: bool(permanent._ORDINAL.fullmatch(name)),
            permanent.MAX_RECORDS * 2, owner._check)
        names = sorted(name for name in journal.names() if not permanent._PART.fullmatch(name))
        if not names:
            raise
        tail = owner._read(journal, names[-1])
        permanent._exact(tail, permanent._JOURNAL_FIELDS)
        record = tail["record"]
        if (tail["key"] != key or tail["kind"] not in {"requests", "receipts"}
                or record.get("format_version") != 2 or record.get("record_id") != key
                or (record.get("request") if tail["kind"] == "requests" else record.get("result", {})).get("kind") != "budget_timer"):
            raise
        local._same(tail["record_sha256"], permanent.digest(record))
        observed = owner.observe(resume=(tail["kind"], key, tail["record_sha256"]))
    state = owner.lock.store.read()
    if origin_ref is None:
        if state.get("source_authority") is None:
            _fail("ORIGIN_REQUIRED")
        origin_ref = state["source_authority"]["origin"]
    permanent._ref(origin_ref, kind="origin")
    origin = local._resolve(observed["entries"], origin_ref)
    parent = local._actual_parent(observed["entries"], defer_request_id, origin["core_sha256"])
    return observed, state, origin, parent


def _operation(con, core, parent):
    from . import budget
    identifier = parent["request"]["budget_operation_id"]
    row = con.execute("SELECT * FROM main.budget_operation WHERE operation_id=?", (identifier,)).fetchone()
    if row is None:
        _fail("OPERATION_REQUIRED")
    row = dict(row)
    namespace = permanent.digest({key: core[key] for key in ("binding", "item_id", "submitted_url_sha256")})
    if ((row["library_id"], row["root_job_id"], row["continuation_id"], row["execution_generation"])
            != (core["binding"]["home_library_id"], core["root_job_id"], core["continuation_id"], core["execution_generation"])
            or row["kind"] != "model" and not row["suboperation_key"].startswith("home:" + namespace + ":")
            or row["state"] != "waiting" or row["wait_reason"] != parent["request"]["reason"] or row["resume_at"] is None):
        _fail("OPERATION_REQUIRED")
    deadline = tasks._deadline_ms(budget._datetime(row["resume_at"]))
    instant = datetime(1970, 1, 1, tzinfo=timezone.utc) + timedelta(milliseconds=deadline)
    local._same(instant.isoformat(timespec="milliseconds").replace("+00:00", "Z"), parent["receipt"]["retry_at"])
    return row


class TimerCapture2:
    __slots__ = ("_seal", "_active", "_owner_context", "_owner", "_profile", "_closure", "_intent", "_receipt", "_parent")
    def __init__(self, owner, closure, intent, receipt, parent, *, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._owner_context = seal, True, engine._owner()
        self._owner, self._profile, self._closure = owner, owner.profile, closure
        self._intent, self._receipt, self._parent = deepcopy(intent), deepcopy(receipt), deepcopy(parent)

    def _context(self):
        if (type(self) is not TimerCapture2 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()

    @property
    def parent_defer(self):
        self._context()
        return deepcopy(self._parent)

    @property
    def wire_basis(self):
        self._context()
        body = self._intent["request"]
        return {"kind": "timer", "timer_operation_id": body["timer_operation_id"],
            "timer_intent_sha256": permanent.digest(self._intent), "timer_receipt_sha256": permanent.digest(self._receipt),
            "defer_request_id": body["defer_request"]["request_id"],
            "defer_request_sha256": body["defer_request_sha256"],
            "defer_receipt_sha256": body["defer_receipt_sha256"], "retry_at": body["retry_at"]}

    def provenance_ref(self):
        self._context()
        return {"format_version": 1, "producer": "sync_budget_timer", "operation_id": self._intent["record_id"],
            "receipt_sha256": permanent.digest(self._receipt)}

    @property
    def resume_provenance(self):
        return self.provenance_ref()

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._closure.full()

    def _advance_creation(self, guard, *, parent_capture=None):
        self._context()
        if parent_capture is not None:
            if type(parent_capture) is not local.AdmissionReceiptCapture2 or parent_capture.resume_capture is not self:
                _fail("CREATION_PRODUCER_REQUIRED")
            parent_capture._context()
        self._closure.advance(guard, self if parent_capture is None else parent_capture)

    def _fixed(self, con, token):
        self._context()
        if sql.require_writer(con, profile=self._profile) is not token or token.schema_version != 17:
            _fail("WRITER_REQUIRED")
        self._closure.short()
        sql.check(token, connection=con, profile=self._profile)

    def verify_for_commit(self, connection, *, writer_permit=None):
        self._fixed(connection, writer_permit)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
            connection=connection, profile=self._profile)


@contextmanager
def capture_timer_locked(*, profile, store, defer_request_id, origin_ref=None, owner=None):
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._uuid(defer_request_id)
    permanent._Ownership(store).verify()
    existing = permanent._CURRENT.get()
    if owner is None:
        owner = existing
    if owner is not None and (type(owner) is not permanent.AuthorityGuard or owner is not existing or owner.lock.store is not store):
        _fail("FACTORY_REQUIRED")
    with nullcontext(owner) if owner is not None else permanent.owned(store) as actual:
        observed, _, origin, parent = _selected(actual, defer_request_id, origin_ref)
        key = wire.timer_operation_id(defer_request_id)
        selected = [observed["entries"][folder].get(key) for folder in ("requests", "receipts")]
        if any(item is None or item["state"] != "committed" or item["record"]["format_version"] != 2 for item in selected):
            _fail("RECEIPT_REQUIRED")
        intent, receipt = (item["record"] for item in selected)
        local._same(intent["request"]["core_sha256"], origin["core_sha256"])
        local._same(intent["request"]["origin_ref"], permanent.record_ref(origin))
        local._same((intent["request"]["defer_request"], intent["request"]["defer_receipt"]), (parent["request"], parent["receipt"]))
        local.record_relations2(observed["entries"])
        closure = local._PinnedClosure(actual, observed, receipt)
        value = TimerCapture2(actual, closure, intent, receipt, parent, seal=_SEAL)
        token = _CURRENT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CURRENT.reset(token)


class TimerProducer2:
    __slots__ = ("owner", "_schema", "_parent", "_core", "_state", "_intent", "_receipt", "_operation", "_seal", "_active", "_owner_context")
    def __init__(self, owner, schema, parent, core, state, intent, operation, *, seal):
        if seal is not _SEAL:
            _fail("PRODUCER_REQUIRED")
        self.owner, self._schema, self._parent = owner, schema, deepcopy(parent)
        self._core, self._state, self._intent, self._receipt = deepcopy(core), deepcopy(state), deepcopy(intent), None
        self._operation = deepcopy(operation)
        self._seal, self._active, self._owner_context = seal, True, engine._owner()

    def _context(self):
        if (type(self) is not TimerProducer2 or self._seal is not _SEAL or not self._active
                or _PRODUCER.get() is not self or self._owner_context != engine._owner()
                or permanent._CURRENT.get() is not self.owner):
            _fail("PRODUCER_REQUIRED")
        self.owner._check()

    def _fixed(self, con, token):
        self._context()
        if sql.require_writer(con, profile=self.owner.profile) is not token or token.schema_version != 17:
            _fail("WRITER_REQUIRED")
        self._schema.short(con)
        local._same(_operation(con, self._core, self._parent), self._operation)
        local._same(self.owner.lock.store.read(), self._state)
        local._same(self._state["source_execution"], self._core)
        local._same((self._schema.job["job_id"], self._schema.job["continuation_id"], self._schema.job["execution_generation"]),
            (self._core["root_job_id"], self._core["continuation_id"], self._core["execution_generation"]))
        retry = queue._stored_time(self._parent["receipt"]["retry_at"])
        if retry is None or tasks.clock_ms() < tasks._deadline_ms(retry):
            _fail("NOT_DUE")
        sql.check(token, connection=con, profile=self.owner.profile)

    def check(self, record=None):
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        with sql.connection(self.owner.profile, write=True) as con:
            token = sql.require_writer(con, profile=self.owner.profile)
            self._fixed(con, token)
            sql.register_commit_check(token, lambda: self._fixed(con, token), connection=con, profile=self.owner.profile)
        if record is not None and record != self._intent and record != self._receipt:
            _fail("CREATION_MISMATCH")

    def make_receipt(self):
        self._context()
        with sql.connection(self.owner.profile, write=True) as con:
            token = sql.require_writer(con, profile=self.owner.profile)
            self._fixed(con, token)
            stamp = tasks.clock_ms()  # actual clock read only in the original owned writer
            result = {"format_version": 2, "kind": "budget_timer", "timer_operation_id": self._intent["record_id"],
                "request_sha256": self._intent["request_sha256"], "retry_at": self._parent["receipt"]["retry_at"],
                "defer_request_sha256": permanent.digest(self._parent["request"]),
                "defer_receipt_sha256": permanent.digest(self._parent["receipt"]), "observed_at_ms": stamp, "replayed": False}
            self._receipt = {"format_version": 2, "record_kind": "recovery_receipt", "record_id": self._intent["record_id"],
                "core_sha256": self._intent["core_sha256"], "request_ref": permanent.record_ref(self._intent),
                "request_sha256": self._intent["request_sha256"], "result": result,
                "result_sha256": permanent.digest(result), "parent_defer": None,
                "transport_provenance": {"format_version": 1, "producer": "sync_budget_timer",
                    "operation_id": self._intent["record_id"], "receipt_sha256": permanent.digest(result)}}
            sql.register_commit_check(token, lambda: self._fixed(con, token), connection=con, profile=self.owner.profile)
        return deepcopy(self._receipt)


def require_producer(owner, kind, record, producer):
    if type(producer) is not TimerProducer2 or producer.owner is not owner or kind not in {"requests", "receipts"}:
        _fail("PRODUCER_REQUIRED")
    producer.check(record)


def create_due_locked(*, profile, store, defer_request_id):
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._uuid(defer_request_id)
    permanent._Ownership(store).verify()
    with ExitStack() as stack:
        owner = stack.enter_context(permanent.owned(store))
        observed, state, origin, parent = _selected(owner, defer_request_id, resume=True)
        key = wire.timer_operation_id(defer_request_id)
        old_receipt = observed["entries"]["receipts"].get(key)
        if old_receipt is not None and old_receipt["state"] == "committed":
            local.record_relations2(observed["entries"])
            return {"record": deepcopy(old_receipt["record"]), "ref": permanent.record_ref(old_receipt["record"])}
        retry = queue._stored_time(parent["receipt"]["retry_at"])
        if retry is None or tasks.clock_ms() < tasks._deadline_ms(retry):
            _fail("NOT_DUE")
        schema = genesis._SchemaCapture(profile, store, stack)
        with sql.connection(profile) as con:
            operation = _operation(con, origin["core"], parent)
        from . import budget
        actual_operation = budget.get_operation(budget.OperationSpec(origin["core"]["continuation_id"],
            origin["core"]["execution_generation"], operation["kind"], operation["suboperation_key"],
            operation["input_sha256"], origin["core"]["root_job_id"]), profile=profile)
        if actual_operation is None or actual_operation.operation_id != operation["operation_id"] or actual_operation.state != "waiting":
            _fail("OPERATION_REQUIRED")
        local._same(state["source_execution"], origin["core"])
        original = observed["entries"]["controls"].get(defer_request_id)
        if original is None or original["state"] != "committed" or original["record"]["request"] != parent["request"]:
            _fail("CONTROL_REQUIRED")
        old_intent = observed["entries"]["requests"].get(key)
        if old_intent is None:
            body = {"format_version": 2, "kind": "budget_timer", "timer_operation_id": key,
                "origin_ref": permanent.record_ref(origin), "core_sha256": origin["core_sha256"],
                "defer_request": parent["request"], "defer_receipt": parent["receipt"],
                "defer_request_sha256": permanent.digest(parent["request"]),
                "defer_receipt_sha256": permanent.digest(parent["receipt"]),
                "retry_at": parent["receipt"]["retry_at"], "created_at_ms": tasks.clock_ms()}
            intent = {"format_version": 2, "record_kind": "recovery_intent", "record_id": key,
                "core_sha256": origin["core_sha256"], "request_sha256": permanent.digest(body), "request": body,
                "expected_before": _before(state, origin["core"], parent["request"]["claim"]),
                "creation_basis": deepcopy(original["record"]["creation_basis"]),
                "preservation_provenance": None, "resume_provenance": None}
        else:
            intent = deepcopy(old_intent["record"])
            local._same(intent["request"]["defer_request"], parent["request"])
            local._same(intent["request"]["defer_receipt"], parent["receipt"])
            local._same(intent["request"]["origin_ref"], permanent.record_ref(origin))
        local.validate_record2("requests", intent, observed["contract"])
        producer = TimerProducer2(owner, schema, parent, origin["core"], state, intent, operation, seal=_SEAL)
        token = _PRODUCER.set(producer)
        try:
            producer.check(intent)
            owner.publish("requests", intent, producer=producer)
            if old_receipt is None:
                receipt = producer.make_receipt()
            else:
                # Resume the original reserved clock fact; re-prove its due
                # condition in the actual current writer without rewriting it.
                receipt = deepcopy(old_receipt["record"])
                producer._receipt = deepcopy(receipt)
                producer.check(receipt)
            local.validate_record2("receipts", receipt, observed["contract"])
            owner.publish("receipts", receipt, producer=producer)
            producer.check(receipt)
            schema.full()
        finally:
            producer._active = False
            _PRODUCER.reset(token)
        return {"record": deepcopy(receipt), "ref": permanent.record_ref(receipt)}


def create_due(*, profile, store, defer_request_id):
    with store.reference_operation():
        return create_due_locked(profile=profile, store=store, defer_request_id=defer_request_id)
