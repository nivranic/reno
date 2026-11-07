"""Read exact, already consumed admission1 facts from their original facility.

This independent selector-only capability is historical evidence. It cannot
create a control, authorize a writer, renew a claim, refund an attempt, retire
anything or provide current paid admission. Wire format1 does not imply a
schema16 database: the live reader requires the actual prospective17 contract.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import uuid

from . import budget_writer_witness as sql, db, home_recovery17 as schema17
from . import source_preservation_witness as witness, storage_lifecycle as lifecycle
from . import sync_source_authority as permanent, sync_source_binding17 as binding17
from . import sync_source_claim as execution_owner, sync_source_readmit_contract as wire
from . import sync_state, tasks
from .sync_protocol import SyncError

_SEAL = object()
_CURRENT = ContextVar("reno_legacy_admission1_history", default=None)
_CONTEXT = ContextVar("reno_legacy_admission1_history_context", default=None)


def _fail(code="INVALID"):
    raise SyncError("Historical source admission1 could not be verified",
        "SYNC_SOURCE_HISTORY1_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("FACT_CHANGED")


def _committed(entries, folder, identifier):
    item = entries[folder].get(identifier)
    if item is None or item["state"] != "committed":
        _fail("REFERENCE_REQUIRED")
    record = item["record"]
    if record["record_id"] != identifier or record["format_version"] != 1:
        _fail("LEGACY_RECORD_REQUIRED")
    return record


def _admission_ref(record):
    result = record["result"]
    return {"format_version": 1, "admission_id": result["admission_id"],
        **{name: result[name] for name in ("core_sha256", "request_sha256", "admission_sha256")},
        "record_sha256": permanent.digest(record)}


def _pair(entries, identifier, *, kind):
    intent = _committed(entries, "requests", identifier)
    receipt = _committed(entries, "receipts", identifier)
    request, result = intent["request"], receipt["result"]
    if request["kind"] != kind or request.get("format_version", 1) != 1:
        _fail("REQUEST_MISMATCH")
    _same(receipt["request_sha256"], intent["request_sha256"])
    _same(intent["request_sha256"], wire.request_sha256(request))
    _same(receipt["result_sha256"], permanent.digest(result))
    parent = receipt["parent_defer"]
    _same(wire.normalize_result(request, result,
        defer_receipt=None if parent is None else parent["receipt"]), result)
    return intent, receipt


def _original(request, origin):
    core = origin["core"]
    _same(request["original"], {"core_sha256": origin["core_sha256"],
        "home_epoch": core["local_epoch"], "item_id": core["item_id"],
        "submitted_url_sha256": core["submitted_url_sha256"], "claim": origin["claim"],
        "recovery_generation": core["recovery_generation"], "recovery_stream": core["recovery_stream"]})
    permanent._peer(request, origin["binding"])


def _before(intent, origin, previous):
    request, before = intent["request"], intent["expected_before"]
    permanent._before(before, origin["binding"], origin["core_sha256"])
    expected_claim = origin["claim"] if previous is None else previous["result"]["claim"]
    prior_ref = None if previous is None else _admission_ref(previous)
    _same((before["local_epoch"], before["claim"], before["admission_ref"]),
        (request["current_home_epoch"], expected_claim, prior_ref))
    _same(request["expected_claim"], expected_claim)
    for key in ("recovery_generation", "recovery_stream", "control_revision"):
        _same(before[key], request[key])
    if before["source_authority"] is not None:
        _same(before["source_authority"]["origin"], permanent.record_ref(origin))


def _control(entries, identifier, origin, admission_request, prior_ref):
    record = _committed(entries, "controls", identifier)
    _same(record["core_sha256"], origin["core_sha256"])
    _same(record["request_sha256"], wire.digest(record["request"]))
    permanent._peer(record["request"], origin["binding"])
    basis = record["creation_basis"]
    _same((basis["origin_ref"], basis["admission_ref"], basis["claim"]),
        (permanent.record_ref(origin), prior_ref, admission_request["expected_claim"]))
    _same(basis["kind"], "original_core" if prior_ref is None else "admission")
    if (basis["home_epoch"] > admission_request["current_home_epoch"]
            or basis["home_epoch"] < origin["core"]["local_epoch"]
            or basis["recovery_generation"] > admission_request["recovery_generation"]):
        _fail("CONTROL_SCOPE_MISMATCH")
    _same(basis["recovery_stream"], admission_request["recovery_stream"])
    return record


def _registration(entries, disposition_intent, control, records):
    supplied = disposition_intent["request"]["registration"]
    if supplied is None:
        return
    intent, receipt = _pair(entries, supplied["request_id"], kind="source_register")
    _same(receipt["result"], supplied)
    _same((intent["core_sha256"], intent["request"]["core_sha256"], intent["request"]["item_id"]),
        (control["core_sha256"], control["core_sha256"], supplied["item_id"]))
    _same(intent["request"]["original_request"], control["request"])
    _same(receipt["parent_defer"], None)
    records.extend((intent, receipt))


def _disposition(entries, supplied, control, origin, admission_request, records):
    intent, receipt = _pair(entries, supplied["request_id"], kind="source_dispose")
    _same(receipt["result"], supplied)
    _same(intent["core_sha256"], origin["core_sha256"])
    _same(intent["request"]["original_request"], control["request"])
    _original(intent["request"], origin)
    before = intent["expected_before"]
    _same((before["local_epoch"], before["admission_ref"], before["claim"]),
        (intent["request"]["current_home_epoch"], control["creation_basis"]["admission_ref"],
        admission_request["expected_claim"]))
    for name in ("recovery_generation", "recovery_stream", "control_revision"):
        _same(before[name], intent["request"][name])
    if before["source_authority"] is not None:
        _same(before["source_authority"]["origin"], permanent.record_ref(origin))
    _same((supplied["core_sha256"], supplied["item_id"], supplied["original_request_id"],
        supplied["original_request_sha256"]), (origin["core_sha256"], origin["core"]["item_id"],
        control["record_id"], control["request_sha256"]))
    if intent["request"]["current_home_epoch"] > admission_request["current_home_epoch"]:
        _fail("HISTORY_EPOCH_MISMATCH")
    if supplied["disposition"] == "not_applied" and intent["request"]["registration"] is None:
        _fail("REGISTRATION_REQUIRED")
    _registration(entries, intent, control, records)
    records.extend((intent, receipt))
    return receipt


def _matching_outcomes(entries, control, origin, admission_request, records):
    """Only this selected control is resolved; unrelated controls stay unknown."""
    positive, negative = [], []
    for item in entries["receipts"].values():
        record, result = item["record"], item["record"].get("result", {})
        if result.get("original_request_id") != control["record_id"] or "disposition" not in result:
            continue
        if item["state"] != "committed" or record["format_version"] != 1:
            _fail("OUTCOME_MISMATCH")
        verified = _disposition(entries, result, control, origin, admission_request, records)
        (positive if result["disposition"] == "applied" else negative).append(verified)
    if len(negative) > 1 or negative and positive:
        _fail("OUTCOME_MISMATCH")
    if positive:
        for record in positive[1:]:
            _same(record["result"]["original_receipt"], positive[0]["result"]["original_receipt"])
    return positive, negative


def _consumed(entries, origin, intent, previous, records):
    request = intent["request"]
    prior_ref = None if previous is None else _admission_ref(previous)
    selected, controls = {}, {}
    for result in request["dispositions"]:
        identifier = result["original_request_id"]
        control = _control(entries, identifier, origin, request, prior_ref)
        controls[identifier] = control
        selected[identifier] = _disposition(entries, result, control, origin, request, records)
    defer, resume = request["defer_receipt"], request["resume_receipt"]
    if defer is not None:
        for fact, kind in ((defer, "budget_defer"), (resume, "budget_resume")):
            control = _control(entries, fact["request_id"], origin, request, prior_ref)
            _same(control["request"]["kind"], kind)
            controls[control["record_id"]] = control
        defer_control, resume_control = controls[defer["request_id"]], controls[resume["request_id"]]
        wire._budget_receipt_for(defer_control["request"], defer)
        wire._budget_receipt_for(resume_control["request"], resume, defer_receipt=defer)
        _same(resume_control["request"]["defer_request_id"], defer_control["record_id"])
        provenance = intent["manual_resume_basis"]
        basis = request["manual_resume_basis"]
        if provenance is None:
            _fail("RESUME_PARENT_REQUIRED")
        # The wire hash names the authorization payload; local provenance
        # names all four persisted task_request columns, including its real
        # timestamp. They identify the same operation, not the same bytes.
        # This pure history projection supplies no SQL authorization: the
        # independent capture resolves both original hashes below.
        _same(provenance["operation_id"], basis["authorization_id"])
    elif intent["manual_resume_basis"] is not None:
        _fail("RESUME_PARENT_MISMATCH")
    facts = []
    for identifier, control in controls.items():
        positive, negative = _matching_outcomes(entries, control, origin, request, records)
        supplied = selected.get(identifier)
        fact = defer if defer is not None and identifier == defer["request_id"] else (
            resume if resume is not None and identifier == resume["request_id"] else None)
        if fact is not None:
            if negative:
                _fail("OUTCOME_MISMATCH")
            for record in positive:
                _same(record["result"]["original_receipt"], fact)
                if control["request"]["kind"] == "budget_resume":
                    parent = record["parent_defer"]
                    _same(parent, {"request": controls[defer["request_id"]]["request"], "receipt": defer})
            disposition = "applied"
        else:
            if supplied is None or supplied["result"]["disposition"] != "not_applied" or not negative:
                _fail("UNCONSUMED_POSITIVE")
            _same(negative[0], supplied)
            disposition = "not_applied"
        records.append(control)
        facts.append({"control_ref": permanent.record_ref(control), "disposition": disposition,
            "control_request": deepcopy(control["request"]), "original_receipt": deepcopy(fact),
            "disposition_refs": tuple(permanent.record_ref(r) for r in (*positive, *negative))})
    return facts


def _facts(entries, origin, admission_id):
    chain, seen, identifier = [], set(), admission_id
    while identifier is not None:
        if identifier in seen or len(seen) >= permanent.MAX_RECORDS:
            _fail("ADMISSION_CYCLE")
        seen.add(identifier)
        intent, receipt = _pair(entries, identifier, kind="source_readmit")
        _same(intent["core_sha256"], origin["core_sha256"])
        _original(intent["request"], origin)
        chain.append((intent, receipt))
        identifier = receipt["result"]["previous_admission_id"]
    chain.reverse()
    records, consumed, used, previous = [origin], [], set(), None
    for intent, receipt in chain:
        _before(intent, origin, previous)
        if previous is not None:
            if receipt["result"]["current_home_epoch"] < previous["result"]["current_home_epoch"]:
                _fail("HISTORY_EPOCH_MISMATCH")
            if receipt["result"]["attempt_basis"] == "held_claim":
                _same(receipt["result"]["attempts"], previous["result"]["attempts"])
        facts = _consumed(entries, origin, intent, previous, records)
        for fact in facts:
            identifier = fact["control_ref"]["record_id"]
            if identifier in used:
                _fail("CONTROL_ALREADY_CONSUMED")
            used.add(identifier)
            fact["admission_ref"] = _admission_ref(receipt)
            consumed.append(fact)
        records.extend((intent, receipt))
        previous = receipt
    return chain, tuple(consumed), records


def _manual_rows(connection, origin, chain):
    """Resolve both hashes from actual fixed SQL projections, never a DTO."""
    core, result = origin["core"], []
    for intent, _receipt in chain:
        request = intent["request"]
        basis, provenance = request["manual_resume_basis"], intent["manual_resume_basis"]
        if basis is None:
            if provenance is not None:
                _fail("RESUME_PARENT_MISMATCH")
            continue
        if provenance is None or request["defer_receipt"] is None:
            _fail("RESUME_PARENT_REQUIRED")
        defer_id = request["defer_receipt"]["request_id"]
        fields = ("job_id", "kind", "scope_key", "video_id", "payload_json", "continuation_id", "execution_generation")
        job = connection.execute("SELECT " + ",".join(fields) + " FROM main.job WHERE job_id=?",
            (core["root_job_id"],)).fetchone()
        tasks._require_sync_job(job)
        _same((job["job_id"], job["continuation_id"], job["execution_generation"]),
            (core["root_job_id"], core["continuation_id"], core["execution_generation"]))
        library = connection.execute("SELECT value FROM main.meta WHERE key='library_id'").fetchone()
        if library is None:
            _fail("MANUAL_IDENTITY_REQUIRED")
        _same(library[0], core["binding"]["home_library_id"])
        body = {"format_version": 1, "action": "sync_budget_resume", "library_id": library[0],
            "job_id": core["root_job_id"], "continuation_id": core["continuation_id"],
            "execution_generation": core["execution_generation"], "defer_request_id": defer_id}
        identifier = str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "sync-budget-resume:v1:" + defer_id))
        signature = hashlib.sha256(tasks._canonical(body).encode("utf-8")).hexdigest()
        columns = ("request_id", "job_id", "payload_signature", "created_at")
        actual = connection.execute("SELECT " + ",".join(columns) + " FROM main.task_request WHERE request_id=?",
            (identifier,)).fetchone()
        if actual is None:
            _fail("MANUAL_RECEIPT_REQUIRED")
        row = dict(zip(columns, actual))
        permanent._time(row["created_at"])
        _same((row["job_id"], row["payload_signature"]), (core["root_job_id"], signature))
        _same(basis, {"authorization_id": identifier, "authorization_sha256": signature,
            "defer_request_id": defer_id})
        _same(provenance, {"format_version": 1, "producer": "sync_budget_resume",
            "operation_id": identifier, "receipt_sha256": permanent.digest(row)})
        result.append((defer_id, row))
    return result


def _frame(observed):
    if observed["contract"] is None or observed.get("head_status") != "complete" or observed["incomplete"]:
        _fail("COMPLETE_FACILITY_REQUIRED")
    if any(item["state"] != "committed" for values in observed["entries"].values() for item in values.values()):
        _fail("RECORD_INCOMPLETE")
    return {name: deepcopy(observed[name]) for name in
        ("contract", "entries", "head", "journal_head", "actual_heads", "head_status")}


class LegacyAdmissionHistoryCapture1:
    """Live immutable history only; deliberately independent of current views."""
    __slots__ = ("_seal", "_active", "_owner_context", "_context_token", "_owner", "_profile",
        "_store", "_state", "_frame", "_chain", "_consumed", "_origin", "_schema", "_cookie",
        "_version", "_identity", "_files", "_selector", "_facts_signature", "_manual_rows")

    def __init__(self, *, owner, profile, store, state, frame, chain, consumed, origin,
            records, observed, connection, stack, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._owner_context = seal, True, execution_owner._owner()
        self._context_token = None
        self._owner, self._profile, self._store = owner, profile, store
        self._state, self._frame = deepcopy(state), frame
        self._chain, self._consumed, self._origin = deepcopy(chain), deepcopy(consumed), deepcopy(origin)
        # Close the actual table/trigger contract before resolving any task
        # receipt. Fixed columns alone do not turn an unknown view into an
        # original immutable SQL producer record.
        self._schema = schema17._actual_schema(connection)
        self._manual_rows = _manual_rows(connection, origin, chain)
        # Fix the original selector independently of mutable derived copies.
        # These immutable digests catch an accidental private mutation before
        # any property can expose it as a historical fact.
        selected = chain[-1][1]
        self._selector = (selected["record_id"], permanent.digest(selected))
        self._facts_signature = tuple(witness._digest(value) for value in
            (self._chain, self._consumed, self._origin, self._manual_rows))
        self._version = db.knowledge_version(connection)
        self._cookie = connection.execute("PRAGMA main.schema_version").fetchone()[0]
        self._identity = witness._IdentityCapture(store, connection, stack)
        _same((self._identity.library_id, self._identity.node_id),
            (origin["binding"]["home_library_id"], origin["binding"]["home_node_id"]))
        binding17._root(connection, origin["core"])
        if self._chain[-1][1]["result"]["current_home_epoch"] > self._version["library_epoch"]:
            _fail("HISTORY_EPOCH_MISMATCH")
        self._files = []
        folders = {"origin": "origins", "control": "controls", "recovery_intent": "requests",
            "recovery_receipt": "receipts"}
        for record in records:
            directory = owner._directory(observed["ledger"], folders[record["record_kind"]])
            pin = owner.pin_index.get((directory.path, record["record_id"] + ".json"))
            if pin is None or pin[-1] != bytes.fromhex(permanent.digest(record)):
                _fail("ORIGINAL_PIN_REQUIRED")
            self._files.append(pin)

    def _context(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        if (type(self) is not LegacyAdmissionHistoryCapture1 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self
                or self._owner_context != execution_owner._owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        self._owner.lock.verify()
        _same(tuple(witness._digest(value) for value in (self._chain, self._consumed, self._origin, self._manual_rows)),
            self._facts_signature)
        _same((self._chain[-1][1]["record_id"], permanent.digest(self._chain[-1][1])), self._selector)

    @property
    def request(self):
        self._context()
        return deepcopy(self._chain[-1][0]["request"])

    @property
    def result(self):
        self._context()
        return deepcopy(self._chain[-1][1]["result"])

    @property
    def core(self):
        self._context()
        return deepcopy(self._origin["core"])

    @property
    def origin_ref(self):
        self._context()
        return permanent.record_ref(self._origin)

    def admission_ref(self):
        self._context()
        return _admission_ref(self._chain[-1][1])

    @property
    def ancestor_admission_refs(self):
        self._context()
        return tuple(_admission_ref(receipt) for _, receipt in self._chain[:-1])

    @property
    def consumed_controls(self):
        self._context()
        return deepcopy(self._consumed)

    @property
    def consumed_control_refs(self):
        return tuple(fact["control_ref"] for fact in self.consumed_controls)

    def verify_source(self):
        """Full original-file/State/SQL comparison outside every SQL writer."""
        self._context()
        _same(witness._state_signature(self._store), self._state["signature"])
        witness._verify_state_full(self._store, self._state)
        observed = self._owner.observe_readonly()
        _same(_frame(observed), self._frame)
        # Rebuild from the actual read-only entries and the original fixed
        # selector, rather than treating the captured derived dicts as input.
        origin = permanent._resolve_record(observed["entries"],
            self._state["value"]["source_authority"]["origin"])
        selected = _committed(observed["entries"], "receipts", self._selector[0])
        _same(permanent.digest(selected), self._selector[1])
        chain, consumed, _ = _facts(observed["entries"], origin, self._selector[0])
        _same((chain, consumed, origin), (self._chain, self._consumed, self._origin))
        with sql.connection(self._profile) as con:
            _same(schema17._actual_schema(con), self._schema)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
            _same(db.knowledge_version(con), self._version)
            self._identity.verify_for_commit(con)
            binding17._root(con, self._origin["core"])
            _same(_manual_rows(con, origin, chain), self._manual_rows)
        witness._verify_state_full(self._store, self._state)
        _same(witness._state_signature(self._store), self._state["signature"])


@contextmanager
def capture_legacy_admission_history_locked(*, profile, store, admission_id, expected_record_sha256=None):
    """Select actual legacy history; callers supply no owner, core or facts."""
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity):
        _fail("FACTORY_REQUIRED")
    permanent._uuid(admission_id)
    if expected_record_sha256 is not None:
        permanent._sha(expected_record_sha256)
    with ExitStack() as stack:
        stack.enter_context(store.reference_operation())
        existing = permanent._CURRENT.get()
        if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed = owner.observe_readonly()
        frame, entries = _frame(observed), observed["entries"]
        state = witness._state(store)
        current = state["value"]
        if current is None or current.get("source_execution") is None or current.get("source_authority") is None:
            _fail("ACTIVE_ORIGINAL_ROOT_REQUIRED")
        origin = permanent._resolve_record(entries, current["source_authority"]["origin"])
        _same(origin["core"], current["source_execution"])
        _same(origin["core_sha256"], current["source_authority"]["core_sha256"])
        _same(origin["binding"], observed["contract"]["binding"])
        selected = _committed(entries, "receipts", admission_id)
        if expected_record_sha256 is not None:
            _same(permanent.digest(selected), expected_record_sha256)
        chain, consumed, records = _facts(entries, origin, admission_id)
        with sql.connection(profile) as con:
            value = LegacyAdmissionHistoryCapture1(owner=owner, profile=profile, store=store,
                state=state, frame=frame, chain=chain, consumed=consumed, origin=origin,
                records=records, observed=observed, connection=con, stack=stack, seal=_SEAL)
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
