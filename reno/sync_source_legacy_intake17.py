"""Receive original wire1 admissions as history, never as paid admission.

The public factories accept selectors only. A real closed HTTP ticket saves
the original outer record1; a separate, independently pinned capture consumes
only its exact original pending State transition. Neither capability creates a
control, manual authorization, timer, publication or new paid opportunity.

Integration is deliberately explicit: authority publication and the existing
StateCAS/restore consumers must recognize these exact capability types. This
module does not replace their validators or manufacture their private seals.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json

from . import budget_writer_witness as sql, db, home_recovery17 as schema17
from . import source_preservation_witness as witness, storage_lifecycle as lifecycle
from . import sync_access, sync_source_admission_history1 as history
from . import sync_source_authority as permanent, sync_source_claim as engine
from . import sync_source_readmit_contract as wire, sync_source_readmit_local2 as local
from . import sync_source_transport17 as transport, sync_state
from .sync_protocol import SyncError

_SEAL = object()
_PUBLISHER = ContextVar("reno_legacy_http_receipt17_publisher", default=None)
_CURRENT = ContextVar("reno_legacy_baseline17_capture", default=None)
_CONTEXT = ContextVar("reno_legacy_baseline17_context", default=None)


def _fail(code="INVALID"):
    raise SyncError("Original legacy admission intake could not be verified",
        "SYNC_SOURCE_LEGACY_INTAKE17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("FACT_CHANGED")


def _selectors(profile, store):
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity):
        _fail("FACTORY_REQUIRED")
    permanent._Ownership(store).verify()
    return profile


def _owned(stack, store):
    existing = permanent._CURRENT.get()
    if existing is not None and (type(existing) is not permanent.AuthorityGuard
            or existing.lock.store is not store):
        _fail("FACTORY_REQUIRED")
    return stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))


def _origin(entries, intent):
    authority = intent["expected_before"]["source_authority"]
    if authority is None or authority["origin"] is None:
        _fail("ORIGIN_REQUIRED")
    origin = permanent._resolve_record(entries, authority["origin"])
    _same(origin["record_kind"], "origin")
    _same(origin["core_sha256"], intent["core_sha256"])
    _same(authority["core_sha256"], origin["core_sha256"])
    return origin


def _schema(profile, store, origin, stack):
    with sql.connection(profile) as con:
        signature = schema17._actual_schema(con)
        cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
        version, identity = db.knowledge_version(con), sync_access.identity(con)
        _same(identity["handshake"]["migration_version"], 17)
        _same((identity["library_id"], identity["node_id"]),
            (origin["binding"]["home_library_id"], origin["binding"]["home_node_id"]))
        if origin["core"]["local_epoch"] > version["library_epoch"]:
            _fail("ORIGIN_EPOCH_MISMATCH")
        from . import sync_source_binding17 as binding
        binding._root(con, origin["core"])
        pins = witness._IdentityCapture(store, con, stack)
    return signature, cookie, version, pins


class LegacyHttpReceiptPublisher17:
    """Exact closed HTTP result, lexical to the original authority owner."""
    __slots__ = ("owner", "_ticket", "_original_ticket", "_record", "_signature", "_owner_context", "_seal", "_active")

    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    @property
    def record(self):
        self.check()
        return deepcopy(self._record)

    def check(self, record=None):
        if (type(self) is not LegacyHttpReceiptPublisher17 or self._seal is not _SEAL
                or not self._active or _PUBLISHER.get() is not self
                or permanent._CURRENT.get() is not self.owner
                or self._owner_context != engine._owner() or sql.in_write_window()
                or self._ticket is not self._original_ticket
                or type(self._ticket) is not transport.HttpReceiptCapture17):
            _fail("FACTORY_REQUIRED")
        self.owner._check()
        self._ticket.check()
        _same(permanent.digest(self._record), self._signature)
        _same((self._record["record_id"], self._record["request_sha256"], self._record["result"]),
            (self._ticket.request["request_id"], wire.request_sha256(self._ticket.request), self._ticket.result))
        if record is not None:
            _same(record, self._record)


def require_legacy_http_producer17(owner, kind, record, producer, *, entries):
    """Authority's explicit branch for newly received schema17 wire1 facts."""
    if (kind != "receipts" or record["format_version"] != 1
            or type(producer) is not LegacyHttpReceiptPublisher17 or producer.owner is not owner):
        _fail("PRODUCER_REQUIRED")
    producer.check(record)
    intent = history._committed(entries, "requests", record["record_id"])
    _same(intent["request"], producer._ticket.request)
    _same(intent["request_sha256"], record["request_sha256"])


def _historical_parent(entries, intent, receipt):
    """Compare known originals without inventing a missing current parent."""
    parent = receipt["parent_defer"]
    if parent is None:
        return
    identifier = parent["request"]["request_id"]
    control = entries["controls"].get(identifier)
    if control is not None:
        _same(control["state"], "committed")
        _same((control["record"]["request"], control["record"]["request_sha256"], control["record"]["core_sha256"]),
            (parent["request"], wire.digest(parent["request"]), intent["core_sha256"]))
    for item in entries["receipts"].values():
        record, result = item["record"], item["record"]["result"]
        if result.get("request_id") == identifier and result.get("status") == "budget_wait":
            _same(result, parent["receipt"])
            _same(record.get("core_sha256", intent["core_sha256"]), intent["core_sha256"])
        if result.get("original_request_id") != identifier:
            continue
        original = entries["requests"].get(record["record_id"])
        if original is None or original["state"] != "committed":
            _fail("PARENT_INTENT_REQUIRED")
        _same((original["record"]["request"]["original_request"], original["record"]["core_sha256"]),
            (parent["request"], intent["core_sha256"]))
        if "disposition" in result:
            _same(result["disposition"], "applied")
            _same(result["original_receipt"], parent["receipt"])
    # Absence of an independently recorded control/outcome stays unknown.
    # The original persisted parent is returned only as a historical fact;
    # this reader never supplies a lineage, refund or baseline capability.


def _stored_receipt_fact(profile, store, owner, observed, stack, request_id,
        request_sha256, expected_record_sha256=None):
    """Read one existing fact; this branch creates no producer or capability."""
    frame = history._frame(observed)
    state = witness._state(store)
    intent = history._committed(observed["entries"], "requests", request_id)
    request = intent["request"]
    if request.get("format_version", 1) != 1 or request["kind"] not in {
            "source_register", "source_dispose", "source_readmit"}:
        _fail("ORIGINAL_WIRE1_REQUIRED")
    intent, receipt = history._pair(observed["entries"], request_id, kind=request["kind"])
    _historical_parent(observed["entries"], intent, receipt)
    _same(intent["request_sha256"], request_sha256)
    if expected_record_sha256 is not None:
        _same(permanent.digest(receipt), expected_record_sha256)
    origin = _origin(observed["entries"], intent)
    if request["kind"] != "source_register":
        history._original(request, origin)
    else:
        _same((request["core_sha256"], request["item_id"]),
            (origin["core_sha256"], origin["core"]["item_id"]))
        permanent._peer(request, origin["binding"])
    signature, cookie, version, identity = _schema(profile, store, origin, stack)
    if request.get("current_home_epoch", origin["core"]["local_epoch"]) > version["library_epoch"]:
        _fail("RECEIPT_EPOCH_MISMATCH")
    # No receipt ticket, HTTP provenance, baseline capture, pending installation
    # or SQL writer is manufactured from an already saved historical fact.
    witness._verify_state_full(store, state)
    _same(history._frame(owner.observe_readonly()), frame)
    with sql.connection(profile) as con:
        _same(schema17._actual_schema(con), signature)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], cookie)
        _same(db.knowledge_version(con), version)
        identity.verify_for_commit(con)
        from . import sync_source_binding17 as binding
        binding._root(con, origin["core"])
    witness._verify_state_full(store, state)
    return {"record": deepcopy(receipt), "ref": permanent.record_ref(receipt)}


def read_legacy_http_receipt17_locked(*, profile, store, request_id,
        request_sha256, expected_record_sha256=None):
    """Selector-only committed history read; return no live admission right."""
    profile = _selectors(profile, store)
    permanent._uuid(request_id)
    permanent._sha(request_sha256)
    if expected_record_sha256 is not None:
        permanent._sha(expected_record_sha256)
    with ExitStack() as stack:
        stack.enter_context(store.reference_operation())
        owner = _owned(stack, store)
        observed = owner.observe_readonly()
        return _stored_receipt_fact(profile, store, owner, observed, stack,
            request_id, request_sha256, expected_record_sha256)


def publish_legacy_http_receipt17_locked(*, profile, store, client, request_id, request_sha256):
    """Save a genuine HTTP fact, or read its existing original fact at zero RPC.

    This entry currently requires a complete original facility. Recovery of
    this receipt's reserved/file/lagging-head append needs a separate exact
    completion producer; a selector alone cannot repair or finish that write.
    """
    profile = _selectors(profile, store)
    permanent._uuid(request_id)
    permanent._sha(request_sha256)
    with ExitStack() as stack:
        stack.enter_context(store.reference_operation())
        owner = _owned(stack, store)
        observed = owner.observe_readonly()
        if request_id in observed["entries"]["receipts"]:
            item = observed["entries"]["receipts"][request_id]
            if (item["state"] != "committed" or observed["head_status"] != "complete"
                    or observed["incomplete"]):
                from .sync_source_legacy_receipt_completion17 import complete_original_legacy_receipt17_locked
                return complete_original_legacy_receipt17_locked(profile=profile, store=store,
                    request_id=request_id, request_sha256=request_sha256,
                    expected_record_sha256=permanent.digest(item["record"]))
            # A stored fact does not need a surviving client/broker lifetime.
            return _stored_receipt_fact(profile, store, owner, observed, stack,
                request_id, request_sha256)
        history._frame(observed)
        ticket = stack.enter_context(transport.capture_receipt(client,
            request_id=request_id, request_sha256=request_sha256))
        if type(ticket) is not transport.HttpReceiptCapture17:
            _fail("HTTP_TICKET_REQUIRED")
        intent = history._committed(observed["entries"], "requests", request_id)
        request = intent["request"]
        if request.get("format_version", 1) != 1 or request["kind"] not in {
                "source_register", "source_dispose", "source_readmit"}:
            _fail("ORIGINAL_WIRE1_REQUIRED")
        _same((request, intent["request_sha256"]), (ticket.request, request_sha256))
        origin = _origin(observed["entries"], intent)
        _schema(profile, store, origin, stack)
        _same(ticket.provenance["target_url_sha256"],
            hashlib.sha256(origin["binding"]["target_url"].encode("utf-8")).hexdigest())
        if request["kind"] != "source_register":
            history._original(request, origin)
        else:
            _same((request["core_sha256"], request["item_id"]),
                (origin["core_sha256"], origin["core"]["item_id"]))
            permanent._peer(request, origin["binding"])
        parent = None
        result = ticket.result
        _same(result.get("format_version"), 1)
        if (request["kind"] == "source_dispose" and result["disposition"] == "applied"
                and request["original_request"]["kind"] == "budget_resume"):
            parent = owner._parent_defer(observed["entries"], request["original_request"], intent["core_sha256"])
            if ticket.defer_receipt is None:
                _fail("PARENT_RECEIPT_REQUIRED")
            _same(parent["receipt"], {**ticket.defer_receipt, "replayed": False})
        _same(wire.normalize_result(request, result,
            defer_receipt=None if parent is None else parent["receipt"]), result)
        # Preserve the original record1 shape, original UUID and exact result.
        # HTTP provenance is a live capability, not a new serialized field.
        record = {"format_version": 1, "record_kind": "recovery_receipt", "record_id": request_id,
            "request_sha256": request_sha256, "result": result,
            "result_sha256": permanent.digest(result), "parent_defer": parent}
        permanent.validate_record("receipts", record, observed["contract"])
        producer = object.__new__(LegacyHttpReceiptPublisher17)
        producer.owner, producer._ticket, producer._record = owner, ticket, deepcopy(record)
        producer._original_ticket = ticket
        producer._signature = permanent.digest(record)
        producer._owner_context, producer._seal, producer._active = engine._owner(), _SEAL, True
        token = _PUBLISHER.set(producer)
        try:
            producer.check(record)
            saved = owner.publish("receipts", record, producer=producer)
            producer.check(record)
        finally:
            producer._active = False
            _PUBLISHER.reset(token)
        return {"record": deepcopy(saved), "ref": permanent.record_ref(saved)}


def _consumed(entries, origin, intent, previous, records, manual_rows, con):
    """Original controls only; each real manual row has its own two hashes."""
    from . import sync_source_resume_basis2 as manual
    request = intent["request"]
    prior_ref = None if previous is None else history._admission_ref(previous)
    selected, controls = {}, {}
    for result in request["dispositions"]:
        identifier = result["original_request_id"]
        control = history._control(entries, identifier, origin, request, prior_ref)
        controls[identifier] = control
        selected[identifier] = history._disposition(entries, result, control, origin, request, records)
    defer, resume = request["defer_receipt"], request["resume_receipt"]
    if defer is not None:
        for fact, kind in ((defer, "budget_defer"), (resume, "budget_resume")):
            control = history._control(entries, fact["request_id"], origin, request, prior_ref)
            _same(control["request"]["kind"], kind)
            controls[control["record_id"]] = control
        deferred, resumed = controls[defer["request_id"]], controls[resume["request_id"]]
        wire._budget_receipt_for(deferred["request"], defer)
        wire._budget_receipt_for(resumed["request"], resume, defer_receipt=defer)
        _same(resumed["request"]["defer_request_id"], deferred["record_id"])
        basis, provenance = request["manual_resume_basis"], intent["manual_resume_basis"]
        if basis is None or provenance is None:
            _fail("MANUAL_PREFIX_REQUIRED")
        row = manual._row(con, origin["core"], defer["request_id"])
        _same(basis, {"authorization_id": row["request_id"],
            "authorization_sha256": row["payload_signature"], "defer_request_id": defer["request_id"]})
        _same(provenance, {"format_version": 1, "producer": "sync_budget_resume",
            "operation_id": row["request_id"], "receipt_sha256": permanent.digest(row)})
        manual_rows.append((defer["request_id"], deepcopy(row)))
    elif intent["manual_resume_basis"] is not None:
        _fail("MANUAL_PREFIX_UNEXPECTED")
    facts = []
    for identifier, control in controls.items():
        positive, negative = history._matching_outcomes(entries, control, origin, request, records)
        supplied = selected.get(identifier)
        fact = defer if defer is not None and identifier == defer["request_id"] else (
            resume if resume is not None and identifier == resume["request_id"] else None)
        if fact is not None:
            if negative:
                _fail("CONTROL_OUTCOME_MISMATCH")
            for receipt in positive:
                _same(receipt["result"]["original_receipt"], fact)
                if control["request"]["kind"] == "budget_resume":
                    _same(receipt["parent_defer"], {"request": deferred["request"], "receipt": defer})
            disposition = "applied"
        else:
            if supplied is None or supplied["result"]["disposition"] != "not_applied" or not negative:
                _fail("UNCONSUMED_CONTROL")
            _same(negative[0], supplied)
            disposition = "not_applied"
        records.append(control)
        facts.append({"control_ref": permanent.record_ref(control), "disposition": disposition,
            "control_request": deepcopy(control["request"]), "original_receipt": deepcopy(fact),
            "disposition_refs": tuple(permanent.record_ref(r) for r in (*positive, *negative))})
    return facts


def _history(entries, origin, identifier, con, state):
    chain, seen = [], set()
    while identifier is not None:
        if identifier in seen or len(seen) >= permanent.MAX_RECORDS:
            _fail("ADMISSION_CYCLE")
        seen.add(identifier)
        intent, receipt = history._pair(entries, identifier, kind="source_readmit")
        _same(intent["core_sha256"], origin["core_sha256"])
        history._original(intent["request"], origin)
        chain.append((intent, receipt))
        identifier = receipt["result"]["previous_admission_id"]
    chain.reverse()
    records, consumed, used, manual_rows, previous = [origin], [], set(), [], None
    for intent, receipt in chain:
        history._before(intent, origin, previous)
        epoch = receipt["result"]["current_home_epoch"]
        if not origin["core"]["local_epoch"] <= epoch <= state["local_epoch"]:
            _fail("ADMISSION_EPOCH_MISMATCH")
        if previous is not None:
            if epoch < previous["result"]["current_home_epoch"]:
                _fail("ADMISSION_EPOCH_MISMATCH")
            if receipt["result"]["attempt_basis"] == "held_claim":
                _same(receipt["result"]["attempts"], previous["result"]["attempts"])
        for fact in _consumed(entries, origin, intent, previous, records, manual_rows, con):
            control_id = fact["control_ref"]["record_id"]
            if control_id in used:
                _fail("CONTROL_ALREADY_CONSUMED")
            used.add(control_id)
            fact["admission_ref"] = history._admission_ref(receipt)
            consumed.append(fact)
        records.extend((intent, receipt))
        previous = receipt
    return tuple(chain), tuple(consumed), tuple(records), tuple(manual_rows)


def _restore_prefix(current, intent, core):
    """Select the original prefix from one real pinned current restore chain."""
    from . import home_recovery_runtime17 as runtime
    request, provenance = intent["request"], intent["preservation_basis"]
    if request["current_home_epoch"] == core["local_epoch"]:
        if request["preservation"] is not None or provenance is not None:
            _fail("RESTORE_UNEXPECTED")
        return
    if current is None or provenance is None:
        _fail("RESTORE_PREFIX_REQUIRED")
    matches = [(i, row) for i, (row, parsed) in enumerate(current._records)
        if row["operation_id"] == provenance["operation_id"]]
    if len(matches) != 1:
        _fail("RESTORE_PREFIX_REQUIRED")
    end, selected = matches[0]
    _same((selected["result_sha256"], selected["after_library_epoch"]),
        (provenance["receipt_sha256"], request["current_home_epoch"]))
    epoch, last_document, certificate = core["local_epoch"], None, None
    for (row, parsed), document in zip(current._records[:end + 1], current._documents[:end + 1]):
        if row["after_library_epoch"] <= core["local_epoch"]:
            continue
        if (row["before_library_epoch"] != epoch or parsed.wrapper is None or document is None
                or row["after_library_epoch"] <= epoch):
            _fail("RESTORE_CHAIN_INCOMPLETE")
        _same(document["state_authority"]["source_core_sha256"], permanent.digest(core))
        epoch, last_document, certificate = row["after_library_epoch"], document, parsed.certificate_sha256
    if last_document is None or epoch != request["current_home_epoch"]:
        _fail("RESTORE_CHAIN_INCOMPLETE")
    _same(request["preservation"], {"format_version": 1,
        "frontier_sha256": runtime.base._sha(last_document["frontier"]),
        "certificate_sha256": certificate, "from_epoch": core["local_epoch"], "to_epoch": epoch})


class LegacyBaselineAdmissionCapture17:
    """Historical pending installation only, with no paid/creation interface."""
    __slots__ = ("_seal", "_active", "_context_token", "_owner_context", "_owner", "_profile", "_store",
        "_closure", "_record", "_intent", "_origin", "_chain", "_consumed", "_manual_rows", "_signature", "_history_signature",
        "_schema_sha", "_cookie", "_version", "_identity", "_state", "_before_json", "_after_json",
        "_cas_guard", "_adopted", "_original_restore", "preservation_capture", "resume_capture")

    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    def _context(self):
        if (type(self) is not LegacyBaselineAdmissionCapture17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self
                or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        self._owner._check()
        from . import home_recovery_runtime17 as runtime
        if (self.preservation_capture is not self._original_restore or self.resume_capture is not None
                or self._original_restore is not None and (
                    type(self._original_restore) is not runtime.CompletedRestoreCapture17
                    or self._original_restore._store is not self._store
                    or self._original_restore._profile.identity != self._profile.identity
                    or type(self._original_restore._owner) is not permanent._Ownership
                    or self._original_restore._owner.store is not self._store
                    or self._original_restore._owner.reference_frame is not self._owner.lock.reference_frame)):
            _fail("ORIGINAL_RESTORE_REQUIRED")
        _same(self._signature, tuple(witness._digest(value) for value in
            (self._record, self._intent, self._origin, self._manual_rows,
             self._before_json, self._after_json)))
        if not sql.in_write_window():
            _same(self._history_signature, tuple(witness._digest(value) for value in
                (self._chain, self._consumed)))

    @property
    def core(self):
        self._context()
        return deepcopy(self._origin["core"])

    @property
    def request(self):
        self._context()
        return deepcopy(self._intent["request"])

    @property
    def result(self):
        self._context()
        return deepcopy(self._record["result"])

    @property
    def origin_ref(self):
        self._context()
        return permanent.record_ref(self._origin)

    @property
    def consumed_control_refs(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        return tuple(deepcopy(fact["control_ref"]) for fact in self._consumed)

    def admission_ref(self):
        self._context()
        return history._admission_ref(self._record)

    def _effect(self, guard, *, written=False):
        from . import sync_source_lifecycle17 as effects
        self._context()
        if (type(guard) is not effects.StateCASGuard17 or effects._CURRENT.get() is not guard
                or guard._control is not self or guard.owner is not self._owner or guard.store is not self._store
                or guard.profile.identity != self._profile.identity or not guard._active
                or written and not guard._written):
            _fail("BASELINE_STATE_EFFECT_REQUIRED")
        guard._context()
        _same((guard._before_json, guard._after_json), (self._before_json, self._after_json))
        return json.loads(self._before_json), json.loads(self._after_json)

    def baseline_state_effect(self, guard):
        """For exact restore adoption after this original sole State CAS."""
        return self._effect(guard, written=True)

    def _short_sql(self, con):
        from . import sync_source_binding17 as binding, sync_source_resume_basis2 as manual
        self._identity.verify_for_commit(con)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
        _same(con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0], "17")
        _same(db.knowledge_version(con), self._version)
        binding._root(con, self._origin["core"])
        for identifier, expected in self._manual_rows:
            _same(manual._row(con, self._origin["core"], identifier), expected)

    def _fixed(self, con, token):
        from . import sync_source_lifecycle17 as effects
        self._context()
        guard = effects._CURRENT.get()
        self._effect(guard)
        if (sql.require_writer(con, profile=self._profile) is not token or token.schema_version != 17
                or guard._writer != (con, token)):
            _fail("BASELINE_WRITER_REQUIRED")
        self._short_sql(con)
        self._closure.short()
        state_json = engine._encoded(self._store.read())
        if state_json not in (self._before_json, self._after_json):
            _fail("STATE_CHANGED")
        if state_json == self._after_json:
            self._effect(guard, written=True)
            self._cas_guard = guard
        sql.check(token, connection=con, profile=self._profile)

    def verify_for_commit(self, connection, *, writer_permit=None):
        self._fixed(connection, writer_permit)
        if self.preservation_capture is not None:
            self.preservation_capture.verify_for_commit(connection, writer_permit=writer_permit)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
            connection=connection, profile=self._profile)

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._closure.full()
        observed = self._owner.observe_readonly()
        with sql.connection(self._profile) as con:
            _same(schema17._actual_schema(con), self._schema_sha)
            self._short_sql(con)
            selected = _history(observed["entries"], self._origin, self._record["record_id"], con,
                json.loads(self._before_json))
        _same((selected[0], selected[1], selected[3]), (self._chain, self._consumed, self._manual_rows))
        for intent, receipt in self._chain:
            _restore_prefix(self.preservation_capture, intent, self._origin["core"])
        if self.preservation_capture is not None:
            self.preservation_capture.verify_source()
        actual = engine._encoded(self._store.read())
        if actual == self._after_json and not self._adopted:
            self._effect(self._cas_guard, written=True)
            if self._cas_guard._writer is not None or self._cas_guard._failure is not None:
                _fail("BASELINE_STATE_EFFECT_REQUIRED")
            self._state = witness._state(self._store)
            _same(engine._encoded(self._state["value"]), self._after_json)
            self._adopted = True
        _same(actual, self._after_json if self._adopted else self._before_json)
        witness._verify_state_full(self._store, self._state)


@contextmanager
def capture_legacy_baseline_admission17_locked(*, profile, store, request_id, expected_record_sha256=None):
    """Select an original pending receipt; do not accept owner/core/effect DTOs."""
    from . import home_recovery_runtime17 as runtime, sync_source_lifecycle17 as effects
    profile = _selectors(profile, store)
    if _CURRENT.get() is not None:
        _fail("FACTORY_REQUIRED")
    permanent._uuid(request_id)
    if expected_record_sha256 is not None:
        permanent._sha(expected_record_sha256)
    with ExitStack() as stack:
        stack.enter_context(store.reference_operation())
        owner = _owned(stack, store)
        observed = owner.observe_readonly()
        history._frame(observed)
        state = witness._state(store)
        before = state["value"]
        if before is None or before["format_version"] != 5 or before["source_execution"] is None:
            _fail("ACTIVE_STATE_REQUIRED")
        intent, record = history._pair(observed["entries"], request_id, kind="source_readmit")
        if expected_record_sha256 is not None:
            _same(permanent.digest(record), expected_record_sha256)
        origin = _origin(observed["entries"], intent)
        _same(before["source_execution"], origin["core"])
        _same(before["source_authority"]["origin"], permanent.record_ref(origin))
        # Original pending, expected claim and fences determine every byte of
        # the baseline effect. This never makes wire1 a paid current capture.
        after = effects._recovery_after(observed, before, intent, record)
        signature, cookie, version, identity = _schema(profile, store, origin, stack)
        _same(before["local_epoch"], version["library_epoch"])
        with sql.connection(profile) as con:
            chain, consumed, records, manual_rows = _history(observed["entries"], origin, request_id, con, before)
        closure = local._PinnedClosure(owner, observed, record)
        restore = None
        request, core = intent["request"], origin["core"]
        if request["current_home_epoch"] != core["local_epoch"]:
            provenance = intent["preservation_basis"]
            if provenance is None:
                _fail("RESTORE_PREFIX_REQUIRED")
            restore = stack.enter_context(runtime.capture_completed_locked(profile=profile, store=store,
                operation_id=provenance["operation_id"], expected_result_sha256=provenance["receipt_sha256"]))
            if type(restore) is not runtime.CompletedRestoreCapture17:
                _fail("RESTORE_PREFIX_REQUIRED")
            _same(restore.provenance_ref(), provenance)
        for original_intent, original_receipt in chain:
            _restore_prefix(restore, original_intent, core)
        value = object.__new__(LegacyBaselineAdmissionCapture17)
        for key, item in {"_seal": _SEAL, "_active": True, "_context_token": None,
            "_owner_context": engine._owner(), "_owner": owner, "_profile": profile, "_store": store,
            "_closure": closure, "_record": deepcopy(record), "_intent": deepcopy(intent),
            "_origin": deepcopy(origin), "_chain": deepcopy(chain), "_consumed": deepcopy(consumed),
            "_manual_rows": deepcopy(manual_rows), "_schema_sha": signature, "_cookie": cookie,
            "_version": version, "_identity": identity, "_state": state,
            "_before_json": engine._encoded(before), "_after_json": engine._encoded(after),
            "_cas_guard": None, "_adopted": False, "_original_restore": restore,
            "preservation_capture": restore, "resume_capture": None}.items():
            setattr(value, key, item)
        # These are validated aggregate facts, including tuple histories and
        # whole State images, rather than a single bounded authority record.
        value._signature = tuple(witness._digest(item) for item in
            (value._record, value._intent, value._origin, value._manual_rows,
             value._before_json, value._after_json))
        value._history_signature = tuple(witness._digest(item) for item in (value._chain, value._consumed))
        token = _CURRENT.set(value)
        value._context_token = _CONTEXT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CONTEXT.reset(value._context_token)
            _CURRENT.reset(token)
