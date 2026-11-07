"""Original Home17 lifecycle facts and selector-only local reconciliation.

Creation consumes a lexical current admission. Reconciliation reads committed
facts again and owns its own SQLite writer; neither caller DTOs nor detached
binding facts authorize a State effect. No network crosses State/SH/SQL scopes.
The ordinary16 producer and history3/4 validators remain separate.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from datetime import timedelta
import hashlib
import json
import os
import threading
import uuid

from . import budget_writer_witness as sql, db, home_recovery17 as schema17
from . import source_preservation_witness as witness, storage_lifecycle as lifecycle
from . import storage_home_recovery_dependency17 as files
from . import sync_access, sync_source_authority as permanent
from . import sync_source_execution as execution, sync_source_readmit_contract as wire
from . import sync_state, tasks
from .sync_protocol import SyncError


_SEAL = object()
_CURRENT = ContextVar("reno_source_lifecycle17_capture", default=None)
_CONTEXT = ContextVar("reno_source_lifecycle17_context", default=None)
_CREATION = ContextVar("reno_source_publication_creation17", default=None)
_CREATION_CONTEXT = ContextVar("reno_source_publication_creation17_context", default=None)
_COMPLETION = ContextVar("reno_source_control_completion17", default=None)
_COMPLETION_CONTEXT = ContextVar("reno_source_control_completion17_context", default=None)
_FOLDERS = {"origin": "origins", "control": "controls", "recovery_intent": "requests",
            "recovery_receipt": "receipts", "publication": "publications"}


class _ControlContinuity:
    """Authenticated continuity observation without fresh paid/claim admission."""
    def __init__(self, client, source, state):
        from .sync_client import SyncClient
        if type(client) is not SyncClient:
            _fail("TRANSPORT_REQUIRED")
        self.client, self.broker = client, client._source_claim_broker
        self.source, self.binding, self.recovery = deepcopy(source), deepcopy(state["binding"]), deepcopy(state["recovery"])
        self.transport, self.thread = client._transport, _owner()
        with self.broker.lock:
            self.observed = self.broker.latest.get("source/continuity")
        self.check()

    @property
    def value(self):
        self.check()
        return deepcopy(self.observed.response["continuity"])

    def check(self):
        if (self.thread != _owner() or self.client._source_claim_broker is not self.broker
                or self.broker.process != os.getpid() or self.client._transport is not self.transport):
            _fail("CONTINUITY_REQUIRED")
        self.client._check_transport()
        if self.client._check_cancelled is not None:
            self.client._check_cancelled()
        with self.broker.lock:
            value = self.observed
            if value is None or self.broker.latest.get("source/continuity") is not value:
                _fail("CONTINUITY_REQUIRED")
            from . import sync_source_claim as claims
            if type(value) is not claims._Observation or type(value.call) is not claims._Call:
                _fail("CONTINUITY_REQUIRED")
            frame = value.call.frame
            if (type(frame) is not claims._RequestFrame or frame._seal is not claims._FRAME_SEAL
                    or frame.call is not value.call or frame.active or not frame.completed
                    or frame.phase != "closed" or value.call.client is not self.client
                    or value.call.endpoint != "source/continuity"):
                _fail("CONTINUITY_REQUIRED")
            request, result = value.request, value.response
            key = ((execution._canonical(self.source), result["receiver_version"]["library_id"]),
                   result["receiver_version"]["library_epoch"])
            if (value.call.owner != self.thread or value.call.transport is not self.transport
                    or value.call.target_url != self.client._url or self.client._url != self.binding["target_url"]
                    or request["source"] != self.source or result["state"] != "current"
                    or result["source_identity"] != {key: self.source[key] for key in ("library_id", "node_id")}
                    or request["target"] != {key: self.binding["target_" + key] for key in ("library_id", "node_id")}
                    or result["target_identity"] != request["target"]
                    or result["receiver_version"]["library_id"] != self.recovery["stream"]["library_id"]
                    or result["receiver_version"]["library_epoch"] != self.recovery["stream"]["library_epoch"]
                    or key in self.broker.continuity_conflicts):
                _fail("CONTINUITY_REQUIRED")


class _WaitingOperation:
    """Actual one-operation wait, captured before every short control writer."""
    def __init__(self, profile, core, operation_id):
        from . import budget, budget_contract
        sync_state._uuid(operation_id)
        self.profile, self.core, self.identifier = profile, deepcopy(core), operation_id
        with sql.connection(profile) as con:
            row = con.execute("SELECT * FROM main.budget_operation WHERE operation_id=?", (operation_id,)).fetchone()
            if row is None:
                _fail("WAIT_REQUIRED")
            self.row = dict(row)
        namespace = execution._sha({key: core[key] for key in ("binding", "item_id", "submitted_url_sha256")})
        if ((self.row["library_id"], self.row["root_job_id"], self.row["continuation_id"], self.row["execution_generation"])
                != (core["binding"]["home_library_id"], core["root_job_id"], core["continuation_id"], core["execution_generation"])
                or (self.row["kind"] != "model"
                    and not self.row["suboperation_key"].startswith("home:" + namespace + ":"))
                or self.row["state"] != "waiting" or self.row["wait_reason"] not in budget_contract.DEFER_REASONS):
            _fail("WAIT_REQUIRED")
        operation = budget.OperationSpec(core["continuation_id"], core["execution_generation"], self.row["kind"],
                                        self.row["suboperation_key"], self.row["input_sha256"], core["root_job_id"])
        actual = budget.get_operation(operation, profile=profile)
        if actual is None or actual.operation_id != operation_id or actual.state != "waiting":
            _fail("WAIT_REQUIRED")

    def short(self, con):
        row = con.execute("SELECT * FROM main.budget_operation WHERE operation_id=?", (self.identifier,)).fetchone()
        _same(None if row is None else dict(row), self.row)

    def full(self):
        with sql.connection(self.profile) as con:
            self.short(con)


def _capture_restoration17(profile, store, core, state, stack):
    """Self-select the completed same-core suffix; never accept a certificate."""
    if state["local_epoch"] == core["local_epoch"]:
        return None
    from . import home_recovery_runtime17 as runtime, home_recovery_schema as restore_schema
    with sql.connection(profile) as con:
        rows = con.execute("SELECT operation_id,result_sha256 FROM " + restore_schema.TABLE
            + " WHERE after_library_epoch=? AND library_id=?",
            (state["local_epoch"], core["binding"]["home_library_id"])).fetchall()
    if len(rows) != 1:
        _fail("RESTORE_REQUIRED")
    captured = stack.enter_context(runtime.capture_completed_locked(profile=profile, store=store,
        operation_id=rows[0][0], expected_result_sha256=rows[0][1]))
    _same(captured.preservation_observation(from_epoch=core["local_epoch"])["source_core_sha256"],
          permanent.digest(core))
    return captured


def _creation_basis(state, core, observed, continuity, *, restoration=None):
    authority = state["source_authority"]
    admission = state["source_admission"]
    origin = _origin(observed, state, core)
    control = state["recovery"]
    if control is None or state["lease"] is None:
        _fail("CREATION_CONTEXT_REQUIRED")
    if restoration is not None:
        from .home_recovery_runtime17 import CompletedRestoreCapture17
        if type(restoration) is not CompletedRestoreCapture17:
            _fail("RESTORE_REQUIRED")
        restoration.verify_source()
        observation = restoration.preservation_observation(from_epoch=core["local_epoch"])
        _same((observation["source_core_sha256"], observation["preservation"]["to_epoch"],
               restoration._state["value"]), (permanent.digest(core), state["local_epoch"], state))
    if admission is None:
        _same(_entry(state, core)["claim"], origin["claim"])
        if restoration is None:
            _same((state["local_epoch"], control["generation"], control["stream"]),
                  (core["local_epoch"], core["recovery_generation"], core["recovery_stream"]))
        session = deepcopy(state["lease"])
    else:
        receipt = _record(observed, "receipts", admission["admission_id"])
        _same(permanent.digest(receipt), admission["record_sha256"])
        result = receipt["result"]
        _same((result["core_sha256"], result["claim"], result["current_home_epoch"],
               result["recovery_generation"], result["recovery_stream"], result["control_revision"]),
              (permanent.digest(core), _entry(state, core)["claim"], state["local_epoch"],
               control["generation"], control["stream"], control["revision"]))
        _same(_lease_key(state["lease"]), _lease_key(result["session"]))
        if result["session"]["lease_until"] > state["lease"]["lease_until"]:
            _fail("SESSION_CHANGED")
        # The immutable admission binds its original signed session snapshot.
        # A later real heartbeat may extend only the current State expiry.
        session = deepcopy(result["session"])
    return {"format_version": 1, "kind": "original_core" if admission is None else "admission",
        "core_sha256": permanent.digest(core), "home_epoch": state["local_epoch"], "session": session,
        "claim": deepcopy(_entry(state, core)["claim"]), "recovery_generation": control["generation"],
        "recovery_stream": deepcopy(control["stream"]), "control_revision": control["revision"],
        "origin_ref": deepcopy(authority["origin"]), "admission_ref": deepcopy(admission), "continuity": continuity.value}


def _wait_deadline(value):
    """Wire milliseconds never shorten the actual SQL budget wait deadline."""
    if value is None:
        return None
    from . import budget
    instant = budget._datetime(value)
    instant += timedelta(microseconds=(-instant.microsecond) % 1000)
    return instant.isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _successor(owner, base_head, base_entries, folder, record):
    """Full observer plus precisely the original two allowed journal events."""
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    readonly = owner.observe_readonly()
    if readonly["head_status"] != "complete":
        _fail("ORIGINAL_COMPLETION_REQUIRED")
    observed = {**readonly, "head": readonly["journal_head"]}
    actual = deepcopy(observed["entries"])
    candidate = actual[folder].pop(record["record_id"], None)
    if record["record_id"] in base_entries[folder]:
        _fail("CREATION_CONTEXT_REQUIRED")
    _same(actual, base_entries)
    difference = observed["head"]["ordinal"] - base_head["ordinal"]
    if candidate is None:
        _same((difference, observed["head"]), (0, base_head))
    else:
        _same(candidate["record"], record)
        states = ["reserved"] if candidate["state"] == "reserved" else ["reserved", "committed"]
        _same(difference, len(states))
        directory = owner._directory(observed["ledger"], "journal")
        previous = base_head["sha256"]
        for offset, state in enumerate(states, 1):
            ordinal = base_head["ordinal"] + offset
            event = owner._read(directory, f"{ordinal:08d}.json")
            _same((event["facility_id"], event["ordinal"], event["previous_sha256"], event["kind"],
                   event["key"], event["record_sha256"], event["record"], event["state"]),
                  (base_head["facility_id"], ordinal, previous, folder, record["record_id"],
                   permanent.digest(record), record, state))
            previous = permanent.digest(event)
        _same(observed["head"]["sha256"], previous)
    if any(item != (folder, record["record_id"]) for item in observed["incomplete"]):
        _fail("COMMITTED_RECORD_REQUIRED")
    return observed


def _creation_start(owner, observed, folder, record):
    """Continue only this exact original reservation and its immediate head."""
    entry = observed["entries"][folder].get(record["record_id"])
    if entry is None:
        return observed
    _same((entry["record"], entry["state"]), (record, "reserved"))
    head = observed["head"]
    event = owner._read(owner._directory(observed["ledger"], "journal"), f"{head['ordinal']:08d}.json")
    _same((event["kind"], event["key"], event["record"], event["state"], permanent.digest(event)),
          (folder, record["record_id"], record, "reserved", head["sha256"]))
    entries = deepcopy(observed["entries"])
    del entries[folder][record["record_id"]]
    return {**observed, "entries": entries,
        "head": owner._head(observed["contract"], head["ordinal"] - 1, event["previous_sha256"])}


@dataclass(frozen=True, slots=True, init=False, repr=False)
class RegistrationCreationGuard17:
    """One actual committed control and authenticated continuity registration."""
    owner: object
    profile: object
    store: object
    _schema: object
    _continuity: object
    _record_json: str
    _control_json: str
    _state_json: str
    _base_head_json: str
    _base_entries_json: str
    _owner: tuple
    _seal: object
    _active: bool
    _context_token: object

    def __init__(self, *, owner, schema, continuity, record, control, state, observed, seal):
        if seal is not _SEAL or type(owner) is not permanent.AuthorityGuard:
            _fail("FACTORY_REQUIRED")
        for key, value in {"owner": owner, "profile": owner.profile, "store": owner.lock.store,
                "_schema": schema, "_continuity": continuity,
                "_record_json": execution._canonical(record), "_control_json": execution._canonical(control),
                "_state_json": execution._canonical(state), "_base_head_json": execution._canonical(observed["head"]),
                "_base_entries_json": execution._canonical(observed["entries"]), "_owner": _owner(),
                "_seal": seal, "_active": True, "_context_token": None}.items():
            object.__setattr__(self, key, value)

    def _context(self):
        if (type(self) is not RegistrationCreationGuard17 or self._seal is not _SEAL or not self._active
                or _CREATION.get() is not self or _CREATION_CONTEXT.get() is not self or self._owner != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CREATION_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        object.__setattr__(self, "_context_token", _CREATION_CONTEXT.set(self))
        self.owner.lock.verify()
        self._continuity.check()
        _same(execution._canonical(self.store.read()), self._state_json)

    @property
    def record(self):
        self._context()
        return json.loads(self._record_json)

    def check(self, record=None):
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        actual = self.record
        if record is not None:
            _same(actual, record)
        if actual["record_kind"] != "recovery_intent" or actual["request"]["kind"] != "source_register":
            _fail("CREATION_CONTEXT_REQUIRED")
        observed = _successor(self.owner, json.loads(self._base_head_json),
            json.loads(self._base_entries_json), "requests", actual)
        state, control = json.loads(self._state_json), json.loads(self._control_json)
        _same(_record(observed, "controls", control["record_id"]), control)
        _same((actual["expected_before"], actual["request"]["original_request"], actual["request"]["continuity"]),
              (_before(state, self._schema.core), control["request"], self._continuity.value))
        origin = _origin(observed, state, self._schema.core)
        selected = [("origins", origin), ("controls", control)]
        found = observed["entries"]["requests"].get(actual["record_id"])
        if found is not None and found["state"] == "committed":
            selected.append(("requests", actual))
        with ExitStack() as checking:
            pins = _SelectedPins(checking, self.profile, self.store, observed, selected)
            self._schema.full()
            with sql.connection(self.profile, write=True) as con:
                permit = sql.require_writer(con, profile=self.profile)
                def fixed():
                    self._context()
                    self._schema.short(con)
                    pins.short()
                    sql.check(permit, connection=con, profile=self.profile)
                fixed()
                sql.register_commit_check(permit, fixed, connection=con, profile=self.profile)
            self._schema.full()
            pins.full()


def create_registration17_locked(*, profile, store, client, control_request_id, request_id=None):
    """Select one original permanent control; never accept a registration DTO."""
    profile = _selectors(profile, store)
    sync_state._uuid(control_request_id)
    if request_id is not None:
        sync_state._uuid(request_id)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        core = state.get("source_execution")
        if state["format_version"] != 5 or core is None:
            _fail("STATE_REQUIRED")
        schema = _Schema(profile, store, core, state, stack)
        with _owned(store) as owner:
            observed = owner.observe()
            control = _record(observed, "controls", control_request_id)
            _same(control["core_sha256"], permanent.digest(core))
            from . import sync_source_legacy_ancestry2 as legacy
            consumed = legacy.legacy_consumption2(observed["entries"], state)["control_refs"]
            if control_request_id in {ref["record_id"] for ref in consumed}:
                _fail("CONTROL_ALREADY_CONSUMED")
            matches = [entry for entry in observed["entries"]["requests"].values()
                if entry["record"]["request"]["kind"] == "source_register"
                and entry["record"]["core_sha256"] == permanent.digest(core)
                and entry["record"]["request"]["original_request"]["request_id"] == control_request_id]
            if len(matches) > 1:
                _fail("REGISTRATION_FORK")
            if matches and matches[0]["state"] == "committed":
                record = matches[0]["record"]
                if request_id is not None:
                    _same(record["record_id"], request_id)
                return {"record": deepcopy(record), "ref": permanent.record_ref(record)}
            slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[control["request"]["kind"]]
            _same(state["source_authority"][slot], permanent.record_ref(control))
            continuity = _ControlContinuity(client, schema.source, state)
            if matches:
                record = matches[0]["record"]
                _same((record["expected_before"], record["request"]["continuity"], record["request"]["original_request"]),
                      (_before(state, core), continuity.value, control["request"]))
                if request_id is not None:
                    _same(record["record_id"], request_id)
            else:
                request = {"kind": "source_register", "request_id": request_id or str(uuid.uuid4()),
                    "source": schema.source, "target": {key: core["binding"]["target_" + key] for key in ("library_id", "node_id")},
                    "lease": deepcopy(control["request"]["lease"]), "recovery_generation": state["recovery"]["generation"],
                    "recovery_stream": deepcopy(state["recovery"]["stream"]), "control_revision": state["recovery"]["revision"],
                    "continuity": continuity.value, "core_sha256": permanent.digest(core), "item_id": core["item_id"],
                    "original_request": deepcopy(control["request"])}
                request = wire.normalize_register_request(request)
                record = {"format_version": 1, "record_kind": "recovery_intent", "record_id": request["request_id"],
                    "core_sha256": permanent.digest(core), "request_sha256": permanent.digest(request), "request": request,
                    "expected_before": _before(state, core), "preservation_basis": None, "manual_resume_basis": None}
            base = _creation_start(owner, observed, "requests", record)
            guard = RegistrationCreationGuard17(owner=owner, schema=schema, continuity=continuity,
                record=record, control=control, state=state, observed=base, seal=_SEAL)
            token = _CREATION.set(guard)
            object.__setattr__(guard, "_context_token", _CREATION_CONTEXT.set(guard))
            try:
                guard.check()
                committed = owner.publish("requests", record, producer=guard)
                guard.check()
            finally:
                object.__setattr__(guard, "_active", False)
                _CREATION_CONTEXT.reset(guard._context_token)
                _CREATION.reset(token)
            return {"record": deepcopy(committed), "ref": permanent.record_ref(committed)}


class ControlCompletionGuard17:
    """Complete one actual original control reservation, never fresh paid work."""
    def __init__(self, intent, *, seal, paid=None, waiting=None):
        from .sync_source_readmit_local2 import ControlIntentCapture2
        if seal is not _SEAL or type(intent) is not ControlIntentCapture2:
            _fail("FACTORY_REQUIRED")
        self._intent = intent
        self._paid, self._waiting = paid, waiting
        self._recovery_capture = intent.recovery_capture
        self.owner = self._recovery_capture.owner
        self.profile, self.store = self.owner.profile, self.owner.lock.store
        self._seal, self._active, self._owner_context = seal, True, _owner()
        self._context_token = None

    def _context(self):
        if (type(self) is not ControlCompletionGuard17 or self._seal is not _SEAL or not self._active
                or _COMPLETION.get() is not self or _COMPLETION_CONTEXT.get() is not self
                or self._owner_context != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _COMPLETION_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _COMPLETION_CONTEXT.set(self)
        self.owner._check()
        self.owner.lock.verify()

    @property
    def record(self):
        self._context()
        return self._intent.record

    def check(self, record=None):
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        if record is not None:
            _same(record, self.record)
        self._intent.verify_source()
        if self._paid is not None:
            _same(self._paid.proof.public(), self.record["request"]["failure"])
            self._paid.verify_source()
        if self._waiting is not None:
            self._waiting.full()
        with sql.connection(self.profile, write=True) as con:
            permit = sql.require_writer(con, profile=self.profile)
            self._intent.verify_for_control_creation(con, writer_permit=permit, producer=self)
            if self._paid is not None:
                self._paid.verify_for_commit(con, writer_permit=permit)
            if self._waiting is not None:
                self._waiting.short(con)
                sql.register_commit_check(permit, lambda: self._waiting.short(con),
                    connection=con, profile=self.profile)
        self._intent.verify_source()

    def check_head_repair(self, owner):
        self._context()
        self._recovery_capture.check_head_repair(owner, self)

    def adopt_head_repair(self, owner):
        self._context()
        self._recovery_capture.adopt_head_repair(owner, self)


@contextmanager
def _control_completion(intent, *, paid=None, waiting=None):
    guard = ControlCompletionGuard17(intent, seal=_SEAL, paid=paid, waiting=waiting)
    token = _COMPLETION.set(guard)
    guard._context_token = _COMPLETION_CONTEXT.set(guard)
    try:
        guard.check()
        yield guard
        guard.check()
    finally:
        guard._active = False
        _COMPLETION_CONTEXT.reset(guard._context_token)
        _COMPLETION.reset(token)


def complete_control_creation17_locked(*, profile, store, request_id, expected_record_sha256=None):
    """Self-select and complete original bytes before any install or RPC."""
    from .sync_source_readmit_local2 import capture_control_intent_locked
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    with store.reference_operation(), ExitStack() as stack:
        with _owned(store) as selecting:
            selected = selecting.observe_readonly()["entries"]["controls"].get(request_id)
            if selected is None:
                _fail("ORIGINAL_REQUIRED")
            selected = selected["record"]
        paid, waiting = None, None
        # ControlIntent's independent original-failure SQL reader retains the
        # actual operation/attempt/envelope proof without borrowing a normal
        # binding reader's mutable heads across a legitimate head repair.
        if selected["request"]["kind"] == "budget_defer":
            waiting = _WaitingOperation(profile, store.read()["source_execution"],
                selected["request"]["budget_operation_id"])
        intent = stack.enter_context(capture_control_intent_locked(profile=profile, store=store,
            request_id=request_id, expected_record_sha256=expected_record_sha256))
        _same(intent.record, selected)
        with _control_completion(intent, paid=paid, waiting=waiting) as guard:
            record = intent.record
            saved = guard.owner.publish("controls", record, producer=guard)
            _same(saved, record)
            return permanent.record_ref(saved)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class ControlCreationGuard17:
    """Historical original wait/failure control, with no fresh paid permission."""
    owner: object
    profile: object
    store: object
    _schema: object
    _continuity: object
    _waiting: object
    _failure_capture: object
    _resume_capture: object
    _parent_capture: object
    _restore_capture: object
    _history_capture: object
    _retired_json: str
    _stack: object
    _record_json: str
    _state_json: str
    _base_head_json: str
    _base_entries_json: str
    _owner: tuple
    _seal: object
    _active: bool
    _context_token: object

    def __init__(self, *, owner, schema, continuity, waiting, failure_capture, resume_capture, parent_capture, restore_capture, history_capture, retired_records, stack, record, state, observed, seal):
        if seal is not _SEAL or type(owner) is not permanent.AuthorityGuard:
            _fail("FACTORY_REQUIRED")
        values = {"owner": owner, "profile": owner.profile, "store": owner.lock.store, "_schema": schema,
            "_continuity": continuity, "_waiting": waiting, "_failure_capture": failure_capture,
            "_resume_capture": resume_capture, "_parent_capture": parent_capture, "_stack": stack,
            "_restore_capture": restore_capture,
            "_history_capture": history_capture,
            "_retired_json": execution._canonical(retired_records),
            "_record_json": execution._canonical(record), "_state_json": execution._canonical(state),
            "_base_head_json": execution._canonical(observed["head"]),
            "_base_entries_json": execution._canonical(observed["entries"]), "_owner": _owner(),
            "_seal": seal, "_active": True, "_context_token": None}
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def record(self):
        self._context()
        return json.loads(self._record_json)

    def _context(self):
        if (type(self) is not ControlCreationGuard17 or self._seal is not _SEAL or not self._active
                or _CREATION.get() is not self or _CREATION_CONTEXT.get() is not self or self._owner != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CREATION_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        object.__setattr__(self, "_context_token", _CREATION_CONTEXT.set(self))
        self.owner.lock.verify()
        self._continuity.check()
        _same(execution._canonical(self.store.read()), self._state_json)

    def check(self, record=None):
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        actual = self.record
        if record is not None:
            _same(record, actual)
        observed = _successor(self.owner, json.loads(self._base_head_json),
                              json.loads(self._base_entries_json), "controls", actual)
        if self._restore_capture is not None:
            self._restore_capture._advance_creation(self)
        if self._history_capture is not None:
            self._history_capture._advance_creation(self)
        if self._resume_capture is not None:
            self._resume_capture._advance_creation(self)
        if self._parent_capture is not None:
            self._parent_capture._advance_creation(self)
        origin = _origin(observed, json.loads(self._state_json), self._schema.core)
        selected = [("origins", origin)]
        entry = observed["entries"]["controls"].get(actual["record_id"])
        if entry is not None and entry["state"] == "committed":
            selected.append(("controls", actual))
        for folder, record in json.loads(self._retired_json):
            _same(_record(observed, folder, record["record_id"]), record)
            selected.append((folder, record))
        # Each observation owns these head handles only until its comparison
        # ends. Holding a no-delete-share old head across our next legitimate
        # append would prevent Windows' actual atomic replacement. The next
        # call independently proves exactly the allowed original successor.
        with ExitStack() as checking:
            pins = _SelectedPins(checking, self.profile, self.store, observed, selected)
            self._schema.full()
            if self._waiting is not None:
                self._waiting.full()
            if self._failure_capture is not None:
                self._failure_capture.verify_source()
            if self._resume_capture is not None:
                from .sync_source_resume_basis2 import ManualResumeCapture2
                if type(self._resume_capture) is not ManualResumeCapture2:
                    self._resume_capture.verify_source()
            # The original restore was fully verified by _advance_creation
            # above; its live pins, writer checks and postwriter full check
            # cover the remaining interval without another identical read.
            # Each original parent/history advance above completed its live
            # successor, pinned identity and original SQL proof in this check.
            # Manual resume also completed its original parent and row proof;
            # timers retain their public full verification here. Writer,
            # commit and postwriter checks below remain independent.
            with sql.connection(self.profile, write=True) as con:
                permit = sql.require_writer(con, profile=self.profile)
                if self._failure_capture is not None:
                    self._failure_capture.verify_for_commit(con, writer_permit=permit)
                if self._resume_capture is not None:
                    self._resume_capture.verify_for_commit(con, writer_permit=permit)
                if self._parent_capture is not None:
                    self._parent_capture.verify_for_commit(con, writer_permit=permit)
                if self._restore_capture is not None:
                    self._restore_capture.verify_for_commit(con, writer_permit=permit)
                if self._history_capture is not None:
                    self._history_capture.verify_for_control_creation(con, writer_permit=permit, producer=self)
                def fixed():
                    self._context()
                    self._schema.short(con)
                    pins.short()
                    if self._waiting is not None:
                        self._waiting.short(con)
                    if self._failure_capture is not None:
                        self._failure_capture._fixed(con, permit)
                    if self._resume_capture is not None:
                        self._resume_capture._fixed(con, permit)
                    if self._parent_capture is not None:
                        self._parent_capture._fixed(con, permit)
                    if self._restore_capture is not None:
                        self._restore_capture._verify_budget_writer(con, permit)
                    if self._history_capture is not None:
                        self._history_capture._fixed(con, permit, self)
                    sql.check(permit, connection=con, profile=self.profile)
                fixed()
                sql.register_commit_check(permit, fixed, connection=con, profile=self.profile)
            self._schema.full()
            pins.full()
            if self._failure_capture is not None:
                self._failure_capture.verify_source()
            if self._resume_capture is not None:
                self._resume_capture.verify_source()
            if self._parent_capture is not None:
                self._parent_capture.verify_source()
            if self._restore_capture is not None:
                self._restore_capture.verify_source()
            if self._history_capture is not None:
                self._history_capture.verify_source()

    def _advance_for_capture(self, captured):
        self._context()
        if (captured is None or captured is not self._resume_capture and captured is not self._parent_capture and captured is not self._restore_capture and captured is not self._history_capture
                or sql.in_write_window()):
            _fail("CREATION_CONTEXT_REQUIRED")
        return deepcopy(_successor(self.owner, json.loads(self._base_head_json),
            json.loads(self._base_entries_json), "controls", self.record))


@contextmanager
def _control_creator(owner, schema, continuity, waiting, failure_capture, stack, record, state, observed, *, resume_capture=None, parent_capture=None, restore_capture=None, history_capture=None, retired_records=()):
    guard = ControlCreationGuard17(owner=owner, schema=schema, continuity=continuity, waiting=waiting,
        failure_capture=failure_capture, resume_capture=resume_capture, parent_capture=parent_capture,
        restore_capture=restore_capture, history_capture=history_capture, retired_records=retired_records, stack=stack, record=record,
        state=state, observed=observed, seal=_SEAL)
    token = _CREATION.set(guard)
    object.__setattr__(guard, "_context_token", _CREATION_CONTEXT.set(guard))
    try:
        guard.check()
        yield guard
        guard.check()
    finally:
        object.__setattr__(guard, "_active", False)
        _CREATION_CONTEXT.reset(guard._context_token)
        _CREATION.reset(token)


def _control_scope17(state, core, continuity):
    control = state["recovery"]
    if control is None or state["lease"] is None:
        _fail("CREATION_CONTEXT_REQUIRED")
    return {"format_version": 1, "core_sha256": permanent.digest(core),
        "home_epoch": state["local_epoch"], "session": deepcopy(state["lease"]),
        "claim": deepcopy(_entry(state, core)["claim"]),
        "recovery_generation": control["generation"], "recovery_stream": deepcopy(control["stream"]),
        "control_revision": control["revision"], "continuity": continuity.value}


def _historical_control_basis17(profile, store, state, stack):
    from .sync_source_readmit_local2 import capture_historical_control_basis_locked
    prior = state["source_admission"]
    if prior is None:
        return None
    return stack.enter_context(capture_historical_control_basis_locked(profile=profile, store=store,
        admission_id=prior["admission_id"], expected_record_sha256=prior["record_sha256"]))


def _control_identity17(request):
    if request["kind"] == "budget_defer":
        return request["budget_operation_id"]
    if request["kind"] == "budget_resume":
        return request["defer_request_id"]
    return request["failure"]["attempt_id"]


def _recover_original_control17(profile, store, state, kind, identity):
    """Select a real reservation before any fresh permission or current scope."""
    core = state["source_execution"]
    with _owned(store) as owner:
        observed = owner.observe_readonly()
        matches = [item for item in observed["entries"]["controls"].values()
            if item["record"]["core_sha256"] == permanent.digest(core)
            and item["record"]["request"]["kind"] == kind
            and _control_identity17(item["record"]["request"]) == identity
            and item["record"]["creation_basis"]["admission_ref"] == state["source_admission"]]
        reserved = [item["record"] for item in matches if item["state"] == "reserved"]
        if len(reserved) > 1:
            _fail("CONTROL_FORK")
        if not reserved:
            candidates = [item["record"] for item in matches if item["state"] == "committed"
                and item["record"]["format_version"] == 2]
            if not candidates:
                return None
            from .sync_source_readmit_local2 import control_retirement_graph2
            graph = control_retirement_graph2(observed["entries"], candidates[0]["record_id"])
            if graph["closed_head"] is not None:
                return None
            record = _resolve({**observed, "head": observed["journal_head"]}, graph["leaf_ref"])
            slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[kind]
            if (state["source_authority"][slot] != graph["leaf_ref"]
                    and record["expected_before"] != _before(state, core)):
                return None
            if observed["head_status"] == "complete":
                return graph["leaf_ref"]
        else:
            record = reserved[0]
        if record["format_version"] not in {1, 2}:
            _fail("OLD_CONTROL_UNPROVED")
    return complete_control_creation17_locked(profile=profile, store=store,
        request_id=record["record_id"], expected_record_sha256=permanent.digest(record))


def _control_graph17(observed, core, basis, kind, identity):
    from .sync_source_readmit_local2 import control_retirement_graph2
    selected = [item["record"] for item in observed["entries"]["controls"].values()
        if item["record"]["core_sha256"] == permanent.digest(core)
        and item["record"]["request"]["kind"] == kind
        and _control_identity17(item["record"]["request"]) == identity
        and all(item["record"]["creation_basis"][key] == basis[key]
            for key in ("origin_ref", "admission_ref", "claim"))]
    if not selected:
        return None, None
    graph = control_retirement_graph2(observed["entries"], selected[0]["record_id"])
    return graph, _resolve(observed, graph["leaf_ref"])


def _control_record17(core, request, basis, scope, state, restored, *, permission=None,
                      parent=None, retirement_head=None):
    """Original wire; immutable historical and current creation facts differ."""
    _same(scope["claim"], basis["claim"])
    return {"format_version": 2, "record_kind": "control", "record_id": request["request_id"],
        "core_sha256": permanent.digest(core), "request_sha256": permanent.digest(request),
        "request": request, "creation_basis": deepcopy(basis), "control_scope": deepcopy(scope),
        "expected_before": _before(state, core),
        "preservation_provenance": None if restored is None else restored.provenance_ref(),
        "resume_provenance": None if permission is None else permission.provenance_ref(),
        "parent_defer_refs": None if parent is None else {
            "control_ref": deepcopy(state["source_authority"]["defer"]), "receipt_ref": parent.receipt_ref},
        "retirement_head": deepcopy(retirement_head)}


def _freeze_control17(*, profile, store, client, operation_id=None, attempt_id=None):
    from . import source_terminal
    from .sync_source_readmit_local2 import logical_control_scope2
    profile = _selectors(profile, store)
    if (operation_id is None) == (attempt_id is None):
        _fail("SELECTOR_REQUIRED")
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        kind = "budget_defer" if operation_id is not None else "source_fail"
        identity = operation_id if operation_id is not None else attempt_id
        recovered = _recover_original_control17(profile, store, state, kind, identity)
        if recovered is not None:
            return recovered
        schema = _Schema(profile, store, core, state, stack)
        continuity = _ControlContinuity(client, schema.source, state)
        waiting = _WaitingOperation(profile, core, operation_id) if operation_id is not None else None
        failure = stack.enter_context(source_terminal.capture_failure17_locked(profile=profile, store=store,
            attempt_id=attempt_id)) if attempt_id is not None else None
        slot = "defer" if waiting is not None else "failure"
        with _owned(store) as owner, ExitStack() as children:
            observed = owner.observe()
            existing = state["source_authority"][slot]
            if existing is not None:
                record = _resolve(observed, existing)
                request = record["request"]
                _same(request.get("budget_operation_id", request.get("failure", {}).get("attempt_id")),
                      operation_id if waiting is not None else attempt_id)
                result = deepcopy(existing)
            else:
                if waiting is not None and state["budget_pending"] is not None or failure is not None and state["source_control_pending"] is not None:
                    _fail("OLD_CONTROL_UNPROVED")
                history = _historical_control_basis17(profile, store, state, children)
                restored = _capture_restoration17(profile, store, core, state, children)
                basis = (history.creation_basis if history is not None else
                    _creation_basis(state, core, observed, continuity, restoration=restored))
                scope = _control_scope17(state, core, continuity)
                graph, leaf = _control_graph17(observed, core, basis, kind, identity)
                continue_creation = leaf is None or graph["closed_head"] is not None
                if not continue_creation:
                    if logical_control_scope2(leaf) != logical_control_scope2({"format_version": 2, "control_scope": scope}):
                        _fail("OLD_CONTROL_UNPROVED")
                    result = permanent.record_ref(leaf)
                else:
                    if graph is not None and any(logical_control_scope2(_resolve(observed, ref)) ==
                            logical_control_scope2({"format_version": 2, "control_scope": scope})
                            for ref in [graph["leaf_ref"], *graph["ancestor_refs"]]):
                        _fail("RESUME_FORBIDDEN_SCOPE")
                    identifier = str(uuid.uuid5(uuid.UUID(leaf["record_id"]), "source-control17:v2:" + permanent.digest({
                        "scope": logical_control_scope2({"format_version": 2, "control_scope": scope}),
                        "retirement_head": graph["closed_head"]}))) if leaf is not None else str(uuid.uuid4())
                    original = {"kind": kind,
                        "request_id": identifier, "source": schema.source,
                        "target": {key: core["binding"]["target_" + key] for key in ("library_id", "node_id")},
                        "lease": scope["session"], "claim": scope["claim"], "recovery_generation": scope["recovery_generation"],
                        "recovery_stream": scope["recovery_stream"]}
                    if waiting is not None:
                        original.update(reason=waiting.row["wait_reason"], retry_at=_wait_deadline(waiting.row["resume_at"]),
                                        budget_operation_id=waiting.identifier)
                    else:
                        original.update(core_sha256=permanent.digest(core), submitted_url_sha256=core["submitted_url_sha256"],
                                        failure=failure.proof.public())
                    record = _control_record17(core, original, basis, scope, state, restored,
                        retirement_head=None if graph is None else graph["closed_head"])
                if continue_creation:
                    base = _creation_start(owner, observed, "controls", record)
                    with _control_creator(owner, schema, continuity, waiting, failure, children, record, state, base,
                            restore_capture=restored, history_capture=history) as guard:
                        committed = owner.publish("controls", record, producer=guard)
                        _same(committed, record)
                    result = permanent.record_ref(committed)
    return result


def freeze_budget_defer17_locked(*, profile, store, client, operation_id):
    return _freeze_control17(profile=profile, store=store, client=client, operation_id=operation_id)


def freeze_source_failure17_locked(*, profile, store, client, attempt_id):
    return _freeze_control17(profile=profile, store=store, client=client, attempt_id=attempt_id)


def _resume_scope17(basis):
    """A heartbeat extension never creates another logical resume scope."""
    scope = deepcopy(basis)
    scope["session"].pop("lease_until")
    return scope


def _resume_records17(observed, core, defer_id):
    """Classify actual committed negative facts without guessing an outcome."""
    active, retired = [], []
    for entry in observed["entries"]["controls"].values():
        record = entry["record"]
        if (record["core_sha256"] != permanent.digest(core)
                or record["request"]["kind"] != "budget_resume"
                or record["request"]["defer_request_id"] != defer_id):
            continue
        negatives = [item["record"] for item in observed["entries"]["receipts"].values()
            if item["state"] == "committed" and item["record"]["result"].get("disposition") == "not_applied"
            and item["record"]["result"].get("original_request_id") == record["record_id"]]
        if not negatives:
            active.append(entry)
            continue
        if entry["state"] != "committed":
            _fail("DISPOSITION_REQUIRED")
        retired.append(("controls", record))
        for negative in negatives:
            intent = _record(observed, "requests", negative["record_id"])
            body = intent["request"]
            _same((body["kind"], body["original_request"], intent["core_sha256"]),
                  ("source_dispose", record["request"], record["core_sha256"]))
            _same(wire.normalize_dispose_result(body, negative["result"]), negative["result"])
            retired.extend((("requests", intent), ("receipts", negative)))
    return active, sorted(retired, key=lambda value: (value[0], value[1]["record_id"]))


def freeze_budget_resume17_locked(*, profile, store, client):
    """Freeze normal resume from its actual permanent parent and permission."""
    from .sync_source_readmit_local2 import capture_control_receipt_locked
    from . import sync_source_resume_basis2 as manual, sync_source_timer2 as timer
    from .sync_source_readmit_local2 import logical_control_scope2
    profile = _selectors(profile, store)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None or state["budget_pending"] is None:
            _fail("PARENT_REQUIRED")
        core, pending = state["source_execution"], state["budget_pending"]
        defer_id = pending["defer_request"]["request_id"]
        recovered = _recover_original_control17(profile, store, state, "budget_resume", defer_id)
        if recovered is not None:
            return recovered
        schema = _Schema(profile, store, core, state, stack)
        # A committed original resume is a historical fact. Its recovery must
        # not manufacture another timer or ask for a new manual permission.
        with _owned(store) as owner:
            observed = owner.observe()
            matches, retired = _resume_records17(observed, core, defer_id)
            if any(folder == "controls" and state["source_authority"]["resume"] == permanent.record_ref(record)
                    for folder, record in retired):
                _fail("DISPOSITION_RECONCILIATION_REQUIRED")
            if len(matches) > 1:
                _fail("CONTROL_FORK")
            if matches and matches[0]["state"] == "committed":
                ref = permanent.record_ref(matches[0]["record"])
                if state["source_authority"]["resume"] not in (None, ref):
                    _fail("CONTROL_CHANGED")
                return ref
        continuity = _ControlContinuity(client, schema.source, state)
        # A task_request flag selects its real signed capture, never supplies
        # permission itself. Timer creation uses its actual committed parent.
        if tasks.sync_resume_authorized(core["root_job_id"], defer_id, profile=profile):
            permission_factory = manual.capture_manual_locked
        else:
            timer.create_due_locked(profile=profile, store=store, defer_request_id=defer_id)
            permission_factory = timer.capture_timer_locked
        with _owned(store) as owner, ExitStack() as children:
            observed = owner.observe()
            if state["source_authority"]["resume"] is not None:
                record = _resolve(observed, state["source_authority"]["resume"])
                _same(record["request"]["defer_request_id"], defer_id)
                result = permanent.record_ref(record)
            else:
                # The temporary historical getter closes before a positive
                # parent can own today's completed-restore capture.
                history = _historical_control_basis17(profile, store, state, children)
                parent, parent_request, parent_result, parent_ref = children.enter_context(
                    _positive_control(profile, store, defer_id))
                _same((parent.core, parent_ref, parent_request, parent_result),
                      (core, state["source_authority"]["defer"], pending["defer_request"], pending["defer_receipt"]))
                permission = children.enter_context(permission_factory(profile=profile, store=store,
                    defer_request_id=defer_id, origin_ref=state["source_authority"]["origin"]))
                _same(permission.parent_defer, {"request": parent_request, "receipt": parent_result})
                restored = parent.preservation_capture
                if restored is None:
                    restored = _capture_restoration17(profile, store, core, state, children)
                basis = (history.creation_basis if history is not None else
                    _creation_basis(state, core, observed, continuity, restoration=restored))
                scope = _control_scope17(state, core, continuity)
                graph, leaf = _control_graph17(observed, core, basis, "budget_resume", defer_id)
                if leaf is not None and graph["closed_head"] is None:
                    _same(logical_control_scope2(leaf),
                        logical_control_scope2({"format_version": 2, "control_scope": scope}))
                    return permanent.record_ref(leaf)
                if graph is not None:
                    if any(logical_control_scope2(_resolve(observed, ref)) ==
                            logical_control_scope2({"format_version": 2, "control_scope": scope})
                            for ref in [graph["leaf_ref"], *graph["ancestor_refs"]]):
                        _fail("RESUME_FORBIDDEN_SCOPE")
                identifier = (str(uuid.uuid5(uuid.UUID(defer_id), "source-resume17:v2:" + permanent.digest({
                    "scope": logical_control_scope2({"format_version": 2, "control_scope": scope}),
                    "permission": permission.provenance_ref(), "retirement_head": graph["closed_head"]})))
                    if graph is not None else str(uuid.uuid4()))
                original = {"kind": "budget_resume", "request_id": identifier, "source": schema.source,
                    "target": {key: core["binding"]["target_" + key] for key in ("library_id", "node_id")},
                    "lease": scope["session"], "recovery_generation": scope["recovery_generation"],
                    "recovery_stream": scope["recovery_stream"], "defer_request_id": defer_id}
                record = _control_record17(core, original, basis, scope, state, restored,
                    permission=permission, parent=parent,
                    retirement_head=None if graph is None else graph["closed_head"])
                base = _creation_start(owner, observed, "controls", record)
                with _control_creator(owner, schema, continuity, None, None, stack, record, state, base,
                        resume_capture=permission, parent_capture=parent, restore_capture=restored,
                        history_capture=history) as guard:
                    committed = owner.publish("controls", record, producer=guard)
                    _same(committed, record)
                result = permanent.record_ref(committed)
    return result


def install_control17_locked(*, profile, store, request_id):
    """Install one original committed control plus ref before its first RPC."""
    from . import source_terminal
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        schema = _Schema(profile, store, core, state, stack)
        # The paid reader owns its own selector-only binding capture. Complete
        # that independent authority observation before entering this writer's
        # permanent owner; State + SH remain held across both observations.
        with _owned(store) as selecting:
            selected_record = _record(selecting.observe(), "controls", request_id)
        paid = None
        if selected_record["request"]["kind"] == "source_fail":
            paid = stack.enter_context(source_terminal.capture_failure17_locked(profile=profile, store=store,
                attempt_id=selected_record["request"]["failure"]["attempt_id"]))
        with _owned(store) as owner:
            observed = owner.observe()
            origin = _origin(observed, state, core)
            record = _record(observed, "controls", request_id)
            _same(record, selected_record)
            _same(record["core_sha256"], permanent.digest(core))
            original, basis = record["request"], record.get("control_scope", record["creation_basis"])
            _same((state["local_epoch"], state["recovery"]["generation"], state["recovery"]["stream"],
                   state["recovery"]["revision"], _lease_key(state["lease"]), _entry(state, core)["claim"],
                   state["source_admission"]),
                  (basis["home_epoch"], basis["recovery_generation"], basis["recovery_stream"],
                   basis["control_revision"], _lease_key(basis["session"]), basis["claim"],
                   record["creation_basis"]["admission_ref"]))
            slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[original["kind"]]
            ref = permanent.record_ref(record)
            if state["source_authority"][slot] not in (None, ref):
                _fail("CONTROL_CHANGED")
            after = deepcopy(state)
            after["source_authority"][slot] = ref
            waiting = None
            if slot == "defer":
                waiting = _WaitingOperation(profile, core, original["budget_operation_id"])
                _same((waiting.row["wait_reason"], _wait_deadline(waiting.row["resume_at"])),
                      (original["reason"], original["retry_at"]))
                if after["budget_pending"] is None:
                    after["budget_pending"] = {"defer_request": deepcopy(original), "defer_receipt": None,
                                              "resume_request": None, "resume_receipt": None}
                else:
                    _same(after["budget_pending"]["defer_request"], original)
            elif slot == "resume":
                if after["budget_pending"] is None:
                    _fail("PARENT_REQUIRED")
                _same(after["budget_pending"]["defer_request"]["request_id"], original["defer_request_id"])
                if after["budget_pending"]["resume_request"] not in (None, original):
                    _fail("CONTROL_CHANGED")
                after["budget_pending"]["resume_request"] = deepcopy(original)
            else:
                _same(paid.proof.public(), original["failure"])
                if after["source_control_pending"] is None:
                    after["source_control_pending"] = {"core_sha256": permanent.digest(core),
                                                      "request": deepcopy(original), "receipt": None}
                else:
                    _same(after["source_control_pending"]["request"], original)
            sync_state._validate(after)
            pins = _SelectedPins(stack, profile, store, observed, [("origins", origin), ("controls", record)])
            with _cas(owner, schema, pins, state, after, control=paid, waiting=waiting) as guard, guard.writer():
                guard.replace()
            result = deepcopy(after)
    return result


def install_recovery_pending17_locked(*, profile, store, request_id):
    """Attach an actual committed recovery intent using its original before."""
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        with _owned(store) as owner:
            original = _original_pending17_intent(profile, owner.observe_readonly(), request_id)
            if original is not None:
                from .sync_source_legacy_pending_intake17 import install_original_pending17_locked
                return install_original_pending17_locked(profile=profile, store=store,
                    request_id=request_id, request_sha256=original["request_sha256"],
                    expected_intent_sha256=permanent.digest(original))
            schema = _Schema(profile, store, core, state, stack)
            observed = owner.observe()
            origin = _origin(observed, state, core)
            intent = _record(observed, "requests", request_id)
            _same((intent["core_sha256"], intent["expected_before"]), (permanent.digest(core), _before(state, core)))
            pending = {"format_version": 1, "kind": intent["request"]["kind"],
                "request_id": request_id, "request_sha256": intent["request_sha256"],
                "core_sha256": intent["core_sha256"], "intent_sha256": permanent.digest(intent)}
            if state["source_recovery_pending"] not in (None, pending):
                _fail("CONTROL_CHANGED")
            after = deepcopy(state)
            after["source_recovery_pending"] = pending
            sync_state._validate(after)
            pins = _SelectedPins(stack, profile, store, observed, [("origins", origin), ("requests", intent)])
            with _cas(owner, schema, pins, state, after) as guard, guard.writer():
                guard.replace()
            result = deepcopy(after)
    return result


def _fail(code):
    raise SyncError("Original Home source lifecycle facts could not be verified",
                    "SYNC_SOURCE_LIFECYCLE17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("FACTS_CHANGED")


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _selectors(profile, store):
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity):
        _fail("FACTORY_REQUIRED")
    return profile


@contextmanager
def _owned(store):
    """Reuse only this same actual private owner; never accept a caller owner."""
    existing = permanent._CURRENT.get()
    if existing is None:
        with permanent.owned(store) as owner:
            yield owner
        return
    if type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store:
        _fail("FACTORY_REQUIRED")
    existing._check()
    existing.lock.verify()
    _same(existing.profile.identity, store.profile.identity)
    yield existing
    existing._check()
    existing.lock.verify()


def _lease_key(value):
    return None if value is None else tuple(value[key] for key in ("session_id", "lease_token", "generation"))


def _root_owner(profile, con, core):
    # Completion/reconciliation needs the original actual SQL owner, but no
    # new cloud claim, manual resume action or fresh paid admission.
    current = execution._job(profile, connection=con, fresh_admission=False)
    _same((current["job_id"], current["continuation_id"], current["execution_generation"]),
          (core["root_job_id"], core["continuation_id"], core["execution_generation"]))
    row = con.execute("SELECT lease_until FROM main.job WHERE job_id=?", (current["job_id"],)).fetchone()
    worker = con.execute("SELECT owner_id,lease_until FROM main.worker_lease WHERE scope=?",
                         (tasks.WORKER_SCOPE,)).fetchone()
    now = tasks.clock_ms()
    if (row is None or type(row[0]) is not int or row[0] <= now or worker is None
            or worker[0] != current["owner_id"] or type(worker[1]) is not int or worker[1] <= now):
        raise tasks.TaskError("任务执行租约已失效", "TASK_OWNER_LOST", 409)
    return current


def _entry(state, core):
    selected = [entry for entry in state["claims"] if entry["item_id"] == core["item_id"]]
    if (len(selected) != 1
            or hashlib.sha256(selected[0]["url"].encode("utf-8")).hexdigest() != core["submitted_url_sha256"]):
        _fail("SOURCE_REQUIRED")
    return selected[0]


def _before(state, core):
    control = state["recovery"]
    if control is None:
        _fail("RECOVERY_REQUIRED")
    return {"binding": deepcopy(state["binding"]), "local_epoch": state["local_epoch"],
        "core_sha256": permanent.digest(core), "admission_ref": deepcopy(state["source_admission"]),
        "recovery_generation": control["generation"], "recovery_stream": deepcopy(control["stream"]),
        "control_revision": control["revision"], "claim": deepcopy(_entry(state, core)["claim"]),
        "budget_pending_sha256": None if state["budget_pending"] is None else sync_state._source_digest(state["budget_pending"]),
        "source_control_pending_sha256": None if state["source_control_pending"] is None else sync_state._source_digest(state["source_control_pending"]),
        "pending_publication": deepcopy(state["pending_publication"]),
        "source_authority": deepcopy(state["source_authority"])}


def _record(observed, folder, identifier):
    sync_state._uuid(identifier)
    entry = observed["entries"][folder].get(identifier)
    if entry is None or entry["state"] != "committed":
        _fail("COMMITTED_RECORD_REQUIRED")
    return deepcopy(entry["record"])


def _original_pending17_intent(profile, observed, request_id):
    """Select an actual old intent independently of its current State slot."""
    item = observed["entries"]["requests"].get(request_id)
    if item is None:
        return None
    intent = _record(observed, "requests", request_id)
    request, before = intent["request"], intent["expected_before"]
    authority = before["source_authority"]
    if (intent["format_version"] != 1 or request["kind"] not in {"source_dispose", "source_readmit"}
            or request.get("format_version", 1) != 1
            or authority is not None and authority["origin"] is not None):
        return None
    # The frozen source handshake is historical. The actual destination
    # database and its complete reference determine this local17 route.
    with sql.connection(profile) as con:
        schema17._actual_schema(con)
    return intent


def _resolve(observed, ref):
    permanent._ref(ref)
    value = _record(observed, _FOLDERS[ref["record_kind"]], ref["record_id"])
    _same(permanent.record_ref(value), ref)
    return value


def _row(observed, folder, record):
    return permanent.public_record_projection(folder, record, state="committed",
                                               activation=observed["contract"])


def _origin(observed, state, core):
    authority = state["source_authority"]
    if authority is None or authority["origin"] is None:
        _fail("ORIGIN_REQUIRED")
    _same(authority["core_sha256"], permanent.digest(core))
    record = _resolve(observed, authority["origin"])
    _same(record["core"], core)
    return record


@contextmanager
def _positive_control(profile, store, request_id):
    """Resolve original-control positive facts from direct or applied records."""
    from .sync_source_readmit_local2 import capture_control_receipt_locked, capture_disposition_locked
    with _owned(store) as owner:
        observed = owner.observe()
        original = _record(observed, "controls", request_id)
        direct = observed["entries"]["receipts"].get(request_id)
        factory = (capture_control_receipt_locked(profile=profile, store=store, request_id=request_id)
            if direct is not None else capture_disposition_locked(profile=profile, store=store,
                control_request_id=request_id))
        with factory as captured:
            if direct is not None:
                request, positive = captured.request, captured.result
                _same(captured.request_ref, permanent.record_ref(original))
            else:
                if captured.disposition != "applied":
                    _fail("CONTROL_NOT_APPLIED")
                request, positive = captured.original_control_request, captured.original_control_receipt
                if positive is None:
                    _fail("POSITIVE_RECEIPT_REQUIRED")
            _same(request, original["request"])
            _same(permanent.digest(captured.core), original["core_sha256"])
            yield captured, deepcopy(request), deepcopy(positive), permanent.record_ref(original)


class _SelectedPins:
    """Only the selected immutable records and fixed-size facility anchors."""
    def __init__(self, stack, profile, store, observed, records):
        def cancelled():
            if store.check_cancelled is not None:
                store.check_cancelled()
        self.pins = files._ProofPins(stack, profile, cancelled)
        ledger = profile.database.parent / permanent.DIRECTORY / permanent.VERSION_DIRECTORY
        selected = [(profile.database.parent / permanent.INTENT_NAME, observed["contract"]),
                    (profile.checkpoints / permanent.SEAL_NAME, observed["contract"]),
                    (ledger / "contract.json", observed["contract"]),
                    (profile.database.parent / permanent.DB_HEAD_NAME, observed["head"]),
                    (profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME, observed["head"])]
        for folder, record in records:
            selected.append((ledger / folder / (record["record_id"] + ".json"), record))
        for path, expected in selected:
            value, _ = self.pins.document(path, permanent.MAX_RECORD_BYTES,
                                         expected=permanent.digest(expected))
            _same(value, expected)

    def short(self):
        self.pins.check()
        for directory in self.pins.directories:
            directory.verify()
        for item in self.pins.files:
            self.pins._identity(item)
            _, _, stream, digest, size, _ = item
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            raw = os.read(stream.fileno(), permanent.MAX_RECORD_BYTES + 1)
            if len(raw) != size or len(raw) > permanent.MAX_RECORD_BYTES or hashlib.sha256(raw).hexdigest() != digest:
                _fail("RECORD_CHANGED")
            self.pins._identity(item)

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self.pins.verify()


class _Schema:
    def __init__(self, profile, store, core, state, stack):
        self.profile, self.store, self.core = profile, store, deepcopy(core)
        self.epoch = state["local_epoch"]
        with sql.connection(profile) as con:
            self.schema_sha = schema17._actual_schema(con)
            self.cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            self.source = sync_access.identity(con)
            if self.source["handshake"]["migration_version"] != 17:
                _fail("SCHEMA_REQUIRED")
            self.identity = witness._IdentityCapture(store, con, stack)
            self.job = _root_owner(profile, con, core)
            self.short(con)

    def short(self, con):
        self.identity.verify_for_commit(con)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self.cookie)
        _same(con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0], "17")
        version = db.knowledge_version(con)
        _same((version["library_id"], version["library_epoch"]),
              (self.core["binding"]["home_library_id"], self.epoch))
        _same((self.source["library_id"], self.source["node_id"]),
              (self.core["binding"]["home_library_id"], self.core["binding"]["home_node_id"]))
        _same(_root_owner(self.profile, con, self.core), self.job)

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        with sql.connection(self.profile) as con:
            _same(schema17._actual_schema(con), self.schema_sha)
            _same(sync_access.identity(con), self.source)
            self.short(con)


@dataclass(frozen=True, slots=True, init=False, repr=False)
class StateCASGuard17:
    """One self-derived before/after State transition, never fresh admission."""
    owner: object
    profile: object
    store: object
    _schema: object
    _pins: object
    _outbox: object
    _control: object
    _waiting: object
    _before_json: str
    _after_json: str
    _owner: tuple
    _seal: object
    _active: bool
    _context_token: object
    _writer: object
    _failure: object
    _written: bool

    def __init__(self, *, owner, schema, pins, before, after, outbox, control, waiting, seal):
        if seal is not _SEAL or type(owner) is not permanent.AuthorityGuard:
            _fail("FACTORY_REQUIRED")
        for key, value in {"owner": owner, "profile": owner.profile, "store": owner.lock.store,
            "_schema": schema, "_pins": pins, "_outbox": outbox, "_control": control, "_waiting": waiting,
            "_before_json": execution._canonical(before),
            "_after_json": execution._canonical(after), "_owner": _owner(), "_seal": seal,
            "_active": True, "_context_token": None, "_writer": None, "_failure": None, "_written": False}.items():
            object.__setattr__(self, key, value)

    def _context(self):
        if (type(self) is not StateCASGuard17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self or self._owner != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        object.__setattr__(self, "_context_token", _CONTEXT.set(self))
        self.owner.lock.verify()
        execution._peer(self._schema.core["binding"])

    def _state(self):
        current = execution._canonical(self.store.read())
        if current not in (self._before_json, self._after_json):
            _fail("STATE_CHANGED")

    def _fixed(self, con, permit):
        self._context()
        if self._writer != (con, permit) or sql.require_writer(con, profile=self.profile) is not permit:
            _fail("WRITER_REQUIRED")
        if permit.schema_version != 17:
            _fail("SCHEMA_REQUIRED")
        self._schema.short(con)
        self._pins.short()
        if self._outbox is not None:
            self._outbox.verify_for_commit()
        if self._control is not None:
            self._control._fixed(con, permit)
        if self._waiting is not None:
            self._waiting.short(con)
        self._state()
        sql.check(permit, connection=con, profile=self.profile)

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._schema.full()
        self._pins.full()
        if self._outbox is not None:
            self._outbox.verify_full()
        if self._control is not None:
            child = getattr(self._control, "preservation_capture", None)
            if self._written and child is not None:
                child._advance_state(self)
            self._control.verify_source()
        if self._waiting is not None:
            self._waiting.full()
        self.owner.verify()
        self._state()

    def _advance_for_capture(self, captured):
        """Only this held restore child can observe our actual sole State CAS."""
        from .sync_source_readmit_local2 import AdmissionReceiptCapture2, ControlReceiptCapture2
        from .sync_source_legacy_intake17 import LegacyBaselineAdmissionCapture17
        from .sync_source_legacy_origin_intake17 import LegacyOriginIntakeCapture17
        from .sync_source_legacy_pending_intake17 import LegacyPendingIntakeCapture17
        self._context()
        if (sql.in_write_window() or self._writer is not None or not self._written
                or type(self._control) not in {AdmissionReceiptCapture2, ControlReceiptCapture2,
                    LegacyBaselineAdmissionCapture17, LegacyOriginIntakeCapture17,
                    LegacyPendingIntakeCapture17}
                or captured is not self._control.preservation_capture
                or captured is None):
            _fail("STATE_EFFECT_REQUIRED")
        before, after = json.loads(self._before_json), json.loads(self._after_json)
        _same(self.store.read(), after)
        from .home_recovery_runtime17 import _state_effect17
        _same(_state_effect17(captured, self), (before, after))
        _same(before["source_execution"], after["source_execution"])
        self._schema.full()
        self._pins.full()
        self.owner._check()
        return {"before": deepcopy(before), "after": deepcopy(after)}

    @contextmanager
    def writer(self):
        self.verify_source()
        if self._writer is not None:
            _fail("WRITER_REQUIRED")
        try:
            with sql.connection(self.profile, write=True) as con:
                permit = sql.require_writer(con, profile=self.profile)
                object.__setattr__(self, "_writer", (con, permit))
                if self._control is not None:
                    self._control.verify_for_commit(con, writer_permit=permit)
                self._fixed(con, permit)
                sql.register_commit_check(permit, lambda: self._fixed(con, permit),
                                          connection=con, profile=self.profile)
                yield self
                self._fixed(con, permit)
        except BaseException as exc:
            object.__setattr__(self, "_failure", exc)
            raise
        finally:
            object.__setattr__(self, "_writer", None)
        self.verify_source()

    def replace(self):
        self._context()
        if self._writer is None:
            _fail("WRITER_REQUIRED")
        self._fixed(*self._writer)
        directory = self.store._directory
        _same(execution._canonical(self.store._read(directory)), self._before_json)
        # No caller callback/DTO chooses the replacement. The factory already
        # resolved the actual permanent receipt and built this exact after.
        sync_state._write(directory, sync_state._STATE,
                          sync_state._validate(json.loads(self._after_json)))
        object.__setattr__(self, "_written", True)
        self._fixed(*self._writer)


@contextmanager
def _cas(owner, schema, pins, before, after, *, outbox=None, control=None, waiting=None):
    guard = StateCASGuard17(owner=owner, schema=schema, pins=pins, before=before, after=after,
                           outbox=outbox, control=control, waiting=waiting, seal=_SEAL)
    token = _CURRENT.set(guard)
    object.__setattr__(guard, "_context_token", _CONTEXT.set(guard))
    try:
        guard.verify_source()
        yield guard
        guard.verify_source()
    finally:
        object.__setattr__(guard, "_active", False)
        _CONTEXT.reset(guard._context_token)
        _CURRENT.reset(token)


def _recovery_after(observed, state, intent, receipt):
    """Resolve one actual receipt into the sole allowed current State effect."""
    core = state["source_execution"]
    request = intent["request"]
    _same(intent["core_sha256"], permanent.digest(core))
    _same(intent["expected_before"], _before(state, core))
    pending = {"format_version": 1, "kind": request["kind"], "request_id": request["request_id"],
        "request_sha256": intent["request_sha256"], "core_sha256": intent["core_sha256"],
        "intent_sha256": permanent.digest(intent)}
    _same(state["source_recovery_pending"], pending)
    _same(receipt["request_sha256"], intent["request_sha256"])
    parent = receipt["parent_defer"]
    result = wire.normalize_result(request, receipt["result"],
        defer_receipt=None if parent is None else parent["receipt"])
    _same(result, receipt["result"])
    _same(_lease_key(state["lease"]), _lease_key(request["lease"]))
    after = deepcopy(state)
    if request["kind"] == "source_dispose" and result["disposition"] == "applied":
        original = request["original_request"]
        if original["kind"] in {"budget_defer", "budget_resume"}:
            wait = after["budget_pending"]
            field = "defer" if original["kind"] == "budget_defer" else "resume"
            if wait is None or wait[field + "_request"] != original:
                _fail("CONTROL_CHANGED")
            wait[field + "_receipt"] = deepcopy(result["original_receipt"])
        else:
            control = after["source_control_pending"]
            if control is None or control["request"] != original:
                _fail("CONTROL_CHANGED")
            control["receipt"] = deepcopy(result["original_receipt"])
    elif request["kind"] == "source_dispose" and result["disposition"] == "not_applied":
        original = request["original_request"]
        slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[original["kind"]]
        reference = state["source_authority"][slot]
        if reference is None or reference["record_id"] != original["request_id"]:
            _fail("CONTROL_CHANGED")
        authority = _resolve(observed, reference)
        _same(authority["request"], original)
        if slot == "failure":
            control = after["source_control_pending"]
            if control is None or control["request"] != original or control["receipt"] is not None:
                _fail("CONTROL_CHANGED")
            after["source_control_pending"] = None
        else:
            wait = after["budget_pending"]
            if wait is None or wait[slot + "_request"] != original or wait[slot + "_receipt"] is not None:
                _fail("CONTROL_CHANGED")
            if slot == "defer":
                if wait["resume_request"] is not None:
                    _fail("CONTROL_CHANGED")
                after["budget_pending"] = None
            else:
                # The actual positive parent remains waiting. Only this
                # forbidden resume UUID is retired; no refund is invented.
                wait.update(resume_request=None, resume_receipt=None)
        after["source_authority"][slot] = None
    elif request["kind"] == "source_readmit":
        current = _entry(state, core)
        _same(current["claim"], request["expected_claim"])
        _same((state["local_epoch"], state["recovery"]["generation"], state["recovery"]["stream"],
               state["recovery"]["revision"]),
              (result["current_home_epoch"], result["recovery_generation"], result["recovery_stream"],
               result["control_revision"]))
        after["source_admission"] = {"format_version": 1, "admission_id": result["admission_id"],
            "core_sha256": result["core_sha256"], "request_sha256": result["request_sha256"],
            "admission_sha256": result["admission_sha256"], "record_sha256": permanent.digest(receipt)}
        replacement = {**deepcopy(current), "attempts": result["attempts"],
            "claim": deepcopy(result["claim"]), "lease_until": result["session"]["lease_until"]}
        after["claims"] = [replacement if entry["item_id"] == core["item_id"] else deepcopy(entry)
                           for entry in state["claims"]]
        if request["defer_receipt"] is not None:
            if state["budget_pending"] is not None or request["resume_receipt"] is None:
                _fail("WAIT_NOT_CLOSED")
            _same((state["source_authority"]["defer"]["record_id"], state["source_authority"]["resume"]["record_id"]),
                  (request["defer_receipt"]["request_id"], request["resume_receipt"]["request_id"]))
            # The real admission request and immutable history retain these
            # exact ancestors. Only the active slots are ready for a later wait.
            after["source_authority"]["defer"] = None
            after["source_authority"]["resume"] = None
    # A not_applied result forbids the original UUID permanently; no positive
    # receipt, fresh claim, paid refund or terminal is manufactured here.
    after["source_recovery_pending"] = None
    sync_state._validate(after)
    return after


def reconcile_recovery17_locked(*, profile, store, request_id):
    """Consume an existing committed register/dispose/readmit receipt only."""
    from .sync_source_readmit_local2 import capture_admission_locked, capture_control_receipt_locked
    from .sync_source_legacy_intake17 import capture_legacy_baseline_admission17_locked
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        execution._peer(core["binding"])
        with _owned(store) as owner, ExitStack() as children:
            original = _original_pending17_intent(profile, owner.observe_readonly(), request_id)
            if original is not None:
                from .sync_source_legacy_pending_intake17 import reconcile_original_pending17_locked
                return reconcile_original_pending17_locked(profile=profile, store=store,
                    request_id=request_id, request_sha256=original["request_sha256"],
                    expected_intent_sha256=permanent.digest(original))
            schema = _Schema(profile, store, core, state, stack)
            observed = owner.observe()
            origin = _origin(observed, state, core)
            intent = _record(observed, "requests", request_id)
            receipt = _record(observed, "receipts", request_id)
            if (intent["request"]["kind"] == "source_readmit" and intent["format_version"] == 1
                    and receipt["format_version"] == 1 and intent["request"].get("format_version", 1) == 1):
                capture = capture_legacy_baseline_admission17_locked(profile=profile, store=store,
                    request_id=request_id, expected_record_sha256=permanent.digest(receipt))
            else:
                capture = (capture_admission_locked(profile=profile, store=store, admission_id=request_id)
                    if intent["request"]["kind"] == "source_readmit" else
                    capture_control_receipt_locked(profile=profile, store=store, request_id=request_id))
            captured = children.enter_context(capture)
            _same((captured.core, captured.request, captured.result), (core, intent["request"], receipt["result"]))
            after = _recovery_after(observed, state, intent, receipt)
            pins = _SelectedPins(stack, profile, store, observed,
                                 [("origins", origin), ("requests", intent), ("receipts", receipt)])
            with _cas(owner, schema, pins, state, after, control=captured) as guard, guard.writer():
                guard.replace()
            result = deepcopy(after)
    return result


def record_control_receipt17_locked(*, profile, store, request_id):
    """Cache one actual permanent direct/disposed receipt after its commit."""
    from .sync_source_readmit_local2 import capture_control_receipt_locked
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        schema = _Schema(profile, store, core, state, stack)
        # The getter pins immutable source records, not mutable State. The
        # independent local CAS therefore remains valid across this one change.
        with _positive_control(profile, store, request_id) as positive_facts, _owned(store) as owner:
            control, original, positive, original_ref = positive_facts
            observed = owner.observe()
            origin = _origin(observed, state, core)
            _same(control.core, core)
            _same(original["request_id"], request_id)
            slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[original["kind"]]
            _same(state["source_authority"][slot], original_ref)
            after = deepcopy(state)
            if slot in {"defer", "resume"}:
                pending = after["budget_pending"]
                if pending is None or pending[slot + "_request"] != original:
                    _fail("CONTROL_CHANGED")
                previous = pending[slot + "_receipt"]
                if previous is not None and not sync_state._same_receipt(previous, positive):
                    _fail("RECEIPT_CHANGED")
                pending[slot + "_receipt"] = deepcopy(positive)
            else:
                pending = after["source_control_pending"]
                if pending is None or pending["request"] != original:
                    _fail("CONTROL_CHANGED")
                previous = pending["receipt"]
                if previous is not None and not sync_state._same_receipt(previous, positive):
                    _fail("RECEIPT_CHANGED")
                pending["receipt"] = deepcopy(positive)
            sync_state._validate(after)
            if after == state:
                # The held getter still performs its own terminal full proof.
                # A receipt already cached from this exact permanent fact has
                # no State effect and must not replace the original State path.
                return deepcopy(after)
            pins = _SelectedPins(stack, profile, store, observed, [("origins", origin)])
            with _cas(owner, schema, pins, state, after, control=control) as guard, guard.writer():
                guard.replace()
            result = deepcopy(after)
    return result


def _require_http_resources_closed(store):
    if (sql.in_write_window() or permanent._CURRENT.get() is not None
            or store._directory is not None or store._reference_frame is not None
            or lifecycle.current_guard() is not None):
        _fail("HTTP_RESOURCES_MUST_BE_CLOSED")


def _deliver_original_pending17(*, profile, store, client, intent):
    """Deliver only the selected original; saved facts need neither HTTP nor CAS."""
    from . import sync_source_legacy_pending_intake17 as pending
    from .sync_source_legacy_pending_receipt17 import complete_original_pending_receipt17_locked
    request_id = intent["record_id"]
    selector = {"profile": profile, "store": store, "request_id": request_id,
        "request_sha256": intent["request_sha256"], "expected_intent_sha256": permanent.digest(intent)}
    with store.reference_operation(), _owned(store) as owner:
        observed = owner.observe_readonly()
        _same(_record(observed, "requests", request_id), intent)
        state = store.read()
        selected = state["source_recovery_pending"]
        stored = observed["entries"]["receipts"].get(request_id)
        if selected != pending._pending(intent):
            if stored is None or stored["state"] != "committed":
                _fail("REQUEST_REQUIRED")
        if stored is not None:
            return complete_original_pending_receipt17_locked(profile=profile, store=store,
                request_id=request_id, request_sha256=intent["request_sha256"],
                expected_record_sha256=permanent.digest(stored["record"]))
        original, core = deepcopy(intent["request"]), deepcopy(state["source_execution"])
        execution._peer(core["binding"])
        parent = None
        if original["kind"] == "source_dispose" and original["original_request"]["kind"] == "budget_resume":
            parent = owner._parent_defer(observed["entries"], original["original_request"], permanent.digest(core))
    _require_http_resources_closed(store)
    with ExitStack() as call:
        if original["source"]["handshake"]["migration_version"] == 16:
            call.enter_context(pending.original_legacy_http_call17(**selector, client=client))
        if original["kind"] == "source_dispose":
            client.source_dispose(original, defer_receipt=None if parent is None else parent["receipt"])
        else:
            client.source_readmit(original)
    execution._peer(core["binding"])
    with store.reference_operation():
        return pending.publish_original_pending_receipt17_locked(**selector, client=client)


def deliver_recovery17(*, profile, store, client, request_id):
    """Copy a committed intent, exit all resources, then send its original body."""
    from . import sync_client
    from .sync_source_readmit_local2 import publish_http_receipt_locked
    from .sync_source_legacy_intake17 import publish_legacy_http_receipt17_locked
    from .sync_source_legacy_receipt_completion17 import complete_original_legacy_receipt17_locked
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    if type(client) is not sync_client.SyncClient:
        _fail("TRANSPORT_REQUIRED")
    with store.reference_operation(), _owned(store) as owner:
        original_pending = _original_pending17_intent(profile, owner.observe_readonly(), request_id)
    if original_pending is not None:
        return _deliver_original_pending17(profile=profile, store=store, client=client, intent=original_pending)
    with store.reference_operation():
        state = store.read()
        pending = state.get("source_recovery_pending")
        if pending is None or pending["request_id"] != request_id:
            _fail("REQUEST_REQUIRED")
        core = state["source_execution"]
        execution._peer(core["binding"])
        with _owned(store) as owner:
            observed = owner.observe_readonly()
            intent = _record(observed, "requests", request_id)
            _same(permanent.digest(intent), pending["intent_sha256"])
            _same(intent["request_sha256"], pending["request_sha256"])
            original = deepcopy(intent["request"])
            legacy = (intent["format_version"] == 1 and original["kind"] == "source_readmit"
                and original.get("format_version", 1) == 1)
            stored = observed["entries"]["receipts"].get(request_id)
            complete_original = bool(legacy and stored is not None and stored["record"]["format_version"] == 1
                and (stored["state"] != "committed" or observed["head_status"] != "complete" or observed["incomplete"]))
            if stored is not None:
                if stored["state"] != "committed" and not complete_original:
                    _fail("COMMITTED_RECORD_REQUIRED")
                _same(stored["record"]["request_sha256"], pending["request_sha256"])
                result = {"record": deepcopy(stored["record"]), "ref": permanent.record_ref(stored["record"])}
            else:
                result = None
            parent = None
            if original["kind"] == "source_dispose" and original["original_request"]["kind"] == "budget_resume":
                parent = owner._parent_defer(observed["entries"], original["original_request"], permanent.digest(core))
            if not complete_original:
                owner.observe()
    if complete_original:
        with store.reference_operation():
            return complete_original_legacy_receipt17_locked(profile=profile, store=store,
                request_id=request_id, request_sha256=intent["request_sha256"],
                expected_record_sha256=permanent.digest(stored["record"]))
    if result is None:
        _require_http_resources_closed(store)
        execution._peer(core["binding"])
        if original["kind"] == "source_register":
            client.source_register(original)
        elif original["kind"] == "source_dispose":
            client.source_dispose(original, defer_receipt=None if parent is None else parent["receipt"])
        elif original["kind"] == "source_readmit":
            client.source_readmit(original)
        else:
            _fail("REQUEST_REQUIRED")
        execution._peer(core["binding"])
        # Only the actual raw/typed HTTP ticket for these same selectors can
        # produce receipt2. A replaced public method cannot mint this fact.
        with store.reference_operation():
            publisher = publish_legacy_http_receipt17_locked if legacy else publish_http_receipt_locked
            result = publisher(profile=profile, store=store, client=client,
                request_id=request_id, request_sha256=intent["request_sha256"])
    return deepcopy(result)


def deliver_control17(*, profile, store, client, request_id):
    """Deliver only the actual original control, with receipt-first local2."""
    from . import sync_client
    from .sync_source_readmit_local2 import capture_control_receipt_locked, publish_http_receipt_locked
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    if type(client) is not sync_client.SyncClient:
        _fail("TRANSPORT_REQUIRED")
    with store.reference_operation():
        state = store.read()
        core = state.get("source_execution")
        if state["format_version"] != 5 or core is None:
            _fail("STATE_REQUIRED")
        execution._peer(core["binding"])
        with _owned(store) as owner:
            observed = owner.observe()
            original = _record(observed, "controls", request_id)
            _same(original["core_sha256"], permanent.digest(core))
            body = original["request"]
            slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[body["kind"]]
            _same(state["source_authority"][slot], permanent.record_ref(original))
            pending = state["source_control_pending"] if slot == "failure" else state["budget_pending"]
            if pending is None:
                _fail("REQUEST_REQUIRED")
            _same(pending["request"] if slot == "failure" else pending[slot + "_request"], body)
            stored = observed["entries"]["receipts"].get(request_id)
            dispositions = [entry for entry in observed["entries"]["receipts"].values()
                if entry["state"] == "committed" and "disposition" in entry["record"]["result"]
                and entry["record"]["result"]["original_request_id"] == request_id]
            if stored is not None or dispositions:
                with _positive_control(profile, store, request_id) as actual:
                    captured = actual[0]
                    selected = _resolve(observed, captured.receipt_ref)
                    result = {"record": selected, "ref": captured.receipt_ref}
            else:
                result = None
            parent = None
            if slot == "resume":
                with _positive_control(profile, store, body["defer_request_id"]) as actual:
                    parent = actual[2]
    if result is None:
        execution._peer(core["binding"])
        if body["kind"] == "source_fail":
            client.source_control(body)
        else:
            client.budget_request(body, defer_receipt=parent)
        execution._peer(core["binding"])
        with store.reference_operation():
            result = publish_http_receipt_locked(profile=profile, store=store, client=client,
                request_id=request_id, request_sha256=original["request_sha256"])
    return deepcopy(result)


def deliver_registered_control17(*, profile, store, client, request_id):
    """Register the permanent original before first delivery; replay no RPC."""
    profile = _selectors(profile, store)
    sync_state._uuid(request_id)
    with store.reference_operation(), _owned(store) as owner:
        observed = owner.observe()
        original = _record(observed, "controls", request_id)
        existing = observed["entries"]["receipts"].get(request_id)
        disposed = any(entry["state"] == "committed" and "disposition" in entry["record"]["result"]
            and entry["record"]["result"]["original_request_id"] == request_id
            for entry in observed["entries"]["receipts"].values())
        direct = existing is not None and existing["state"] == "committed" or disposed
        registered = [entry["record"] for entry in observed["entries"]["receipts"].values()
            if entry["state"] == "committed" and entry["record"]["core_sha256"] == original["core_sha256"]
            and entry["record"]["result"].get("original_request_id") == request_id
            and "registration_sha256" in entry["record"]["result"]
            and "disposition" not in entry["record"]["result"]]
        if len(registered) > 1:
            _fail("REGISTRATION_FORK")
    if not direct and not registered:
        registration = create_registration17_locked(profile=profile, store=store, client=client,
            control_request_id=request_id)
        identifier = registration["record"]["record_id"]
        install_recovery_pending17_locked(profile=profile, store=store, request_id=identifier)
        deliver_recovery17(profile=profile, store=store, client=client, request_id=identifier)
        reconcile_recovery17_locked(profile=profile, store=store, request_id=identifier)
    return deliver_control17(profile=profile, store=store, client=client, request_id=request_id)


def create_current_readmit17_locked(*, profile, store, client):
    """Recover an original intent-State gap before choosing a new UUID."""
    from .sync_source_readmit_local2 import create_readmit_intent_locked
    profile = _selectors(profile, store)
    with store.reference_operation():
        state = store.read()
        core = state.get("source_execution")
        if state["format_version"] != 5 or core is None:
            _fail("STATE_REQUIRED")
        with _owned(store) as owner:
            observed = owner.observe()
            before = _before(state, core)
            matching = [entry for entry in observed["entries"]["requests"].values()
                if entry["record"]["request"]["kind"] == "source_readmit"
                and entry["record"]["format_version"] == 2
                and entry["record"]["core_sha256"] == permanent.digest(core)
                and entry["record"]["expected_before"] == before]
            if len(matching) > 1:
                _fail("ADMISSION_FORK")
            if matching and matching[0]["state"] == "committed":
                record = matching[0]["record"]
                return {"record": deepcopy(record), "ref": permanent.record_ref(record)}
            request_id = matching[0]["record"]["record_id"] if matching else str(uuid.uuid4())
        return create_readmit_intent_locked(profile=profile, store=store, client=client, request_id=request_id)


def create_current_dispose17_locked(*, profile, store, client, control_request_id):
    """Select the original disposal intent before choosing another UUID."""
    from .sync_source_readmit_local2 import create_dispose_intent_locked
    profile = _selectors(profile, store)
    sync_state._uuid(control_request_id)
    with store.reference_operation(), _owned(store) as owner:
        observed = owner.observe()
        control = _record(observed, "controls", control_request_id)
        from . import sync_source_legacy_ancestry2 as legacy
        consumed = legacy.legacy_consumption2(observed["entries"], store.read())["control_refs"]
        if control_request_id in {ref["record_id"] for ref in consumed}:
            _fail("CONTROL_ALREADY_CONSUMED")
        matching = [entry for entry in observed["entries"]["requests"].values()
            if entry["record"]["request"]["kind"] == "source_dispose"
            and entry["record"]["request"]["original_request"]["request_id"] == control_request_id
            and entry["record"]["core_sha256"] == control["core_sha256"]]
        if len(matching) > 1:
            _fail("DISPOSITION_FORK")
        if matching and matching[0]["state"] == "committed":
            record = matching[0]["record"]
            return {"record": deepcopy(record), "ref": permanent.record_ref(record)}
        request_id = matching[0]["record"]["record_id"] if matching else str(uuid.uuid4())
    with store.reference_operation():
        return create_dispose_intent_locked(profile=profile, store=store, client=client,
            control_request_id=control_request_id, request_id=request_id)


def dispose_control17(*, profile, store, client, control_request_id):
    """Actual original intent -> typed disposition fact -> its precise State."""
    selected = create_current_dispose17_locked(profile=profile, store=store, client=client,
        control_request_id=control_request_id)
    request_id = selected["record"]["record_id"]
    install_recovery_pending17_locked(profile=profile, store=store, request_id=request_id)
    deliver_recovery17(profile=profile, store=store, client=client, request_id=request_id)
    return reconcile_recovery17_locked(profile=profile, store=store, request_id=request_id)


def terminal_authority_locked(owner, observed, history):
    """Resolve an actual captured history's terminal to its committed public row.

    Internal readers call this only after selector-owned physical history reads.
    The helper grants no capability, State write or fresh execution permission.
    """
    owner._check()
    history = sync_state.validate_source_history5(history)
    terminal = history["source_terminal"]
    if terminal is None:
        _fail("TERMINAL_REQUIRED")
    authority = _resolve(observed, terminal["authority_ref"])
    _same(authority["core_sha256"], permanent.digest(history["source_execution"]))
    folder = "publications" if terminal["kind"] == "source_published" else "controls"
    row = _row(observed, folder, authority)
    _same(permanent.digest(row), terminal["authority_sha256"])
    proof = terminal["proof"]
    store = owner.lock.store
    if terminal["kind"] == "source_published":
        from .sync_transfer import publication_payload_sha256
        request = store.publication_no_resend_request(authority["record_id"])
        if request is None or request["reason"] != "published_receipt":
            _fail("PUBLISHED_RECEIPT_REQUIRED")
        response = store.no_resend_receipt(request["operation_id"])
        _same((request, response), (proof["request"], proof["response"]))
        envelope = store.read_publication(authority["metadata"], proved_stream=request["binding"]["recovery_stream"])
        _same(envelope["claims"], proof["claims"])
        _same(permanent.digest(envelope["claims"]), authority["claims_sha256"])
        _same(publication_payload_sha256(envelope["snapshot"], envelope["claims"],
            recovery_generation=envelope["recovery_generation"], recovery_stream=envelope["recovery_stream"]),
            request["payload_sha256"])
    else:
        from .sync_source_readmit_local2 import capture_control_receipt_locked
        with _positive_control(owner.profile, store, authority["record_id"]) as actual:
            receipt, actual_request, actual_result, original_ref = actual
            positive = proof["resume_receipt"] if terminal["kind"] == "budget_resumed" else proof["receipt"]
            original = proof["resume_request"] if terminal["kind"] == "budget_resumed" else proof["request"]
            _same((actual_request, actual_result, original_ref), (original, positive, permanent.record_ref(authority)))
            if terminal["kind"] == "budget_resumed":
                _same(receipt.parent_defer, {"request": proof["defer_request"], "receipt": proof["defer_receipt"]})
    return deepcopy(authority)


def complete_terminal17_locked(*, profile, store, kind):
    """Actual permanent proof -> immutable history5 -> one precise local CAS."""
    from . import source_terminal
    from .sync_source_readmit_local2 import capture_control_receipt_locked
    from .sync_transfer import publication_payload_sha256
    profile = _selectors(profile, store)
    if kind not in {"budget_resumed", "source_failed", "source_published"}:
        _fail("TERMINAL_REQUIRED")
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        schema = _Schema(profile, store, core, state, stack)
        paid, selected_failure = None, None
        if kind == "source_failed":
            reference = state["source_authority"]["failure"]
            if reference is None:
                _fail("TERMINAL_REQUIRED")
            with _owned(store) as selecting:
                selected_failure = _resolve(selecting.observe(), reference)
            paid = stack.enter_context(source_terminal.capture_failure17_locked(profile=profile, store=store,
                attempt_id=selected_failure["request"]["failure"]["attempt_id"]))
        with _owned(store) as owner, ExitStack() as children:
            observed = owner.observe()
            origin = _origin(observed, state, core)
            slot = {"budget_resumed": "resume", "source_failed": "failure", "source_published": "publication"}[kind]
            ref = state["source_authority"][slot]
            if ref is None:
                _fail("TERMINAL_REQUIRED")
            authority = _resolve(observed, ref)
            selected = [("origins", origin), ("publications" if slot == "publication" else "controls", authority)]
            control, outbox = None, None
            if slot == "publication":
                metadata = authority["metadata"]
                request = store.publication_no_resend_request(metadata["publication_id"])
                if request is None or request["reason"] != "published_receipt":
                    _fail("PUBLISHED_RECEIPT_REQUIRED")
                response = store.no_resend_receipt(request["operation_id"])
                if response is None:
                    _fail("PUBLISHED_RECEIPT_REQUIRED")
                envelope = store.read_publication(metadata, proved_stream=request["binding"]["recovery_stream"])
                _same(request["metadata"], metadata)
                _same(permanent.digest(envelope["claims"]), authority["claims_sha256"])
                _same(publication_payload_sha256(envelope["snapshot"], envelope["claims"],
                    recovery_generation=envelope["recovery_generation"], recovery_stream=envelope["recovery_stream"]),
                    request["payload_sha256"])
                basis = authority["creation_basis"]
                _same((envelope["snapshot"]["version"]["library_epoch"], envelope["recovery_generation"],
                       envelope["recovery_stream"]),
                      (basis["home_epoch"], basis["recovery_generation"], basis["recovery_stream"]))
                bound = next((entry for entry in envelope["claims"] if entry["claim"]["item_id"] == core["item_id"]), None)
                if bound is None:
                    _fail("PUBLICATION_CLAIM_REQUIRED")
                _same(bound["claim"], basis["claim"])
                proof = {"request": request, "response": response, "claims": envelope["claims"]}
                freeze = {**state, "pending_publication": metadata, "legacy_pending_stream": request["binding"]["recovery_stream"]}
                outbox = witness._CheckpointCache(store, {"value": freeze}, stack)
            else:
                control, actual_request, actual_result, original_ref = children.enter_context(
                    _positive_control(profile, store, authority["record_id"]))
                _same((control.core, original_ref, actual_request), (core, ref, authority["request"]))
                if slot == "resume":
                    proof = sync_state._stable_pending(state["budget_pending"])
                    if proof is None:
                        _fail("PARENT_REQUIRED")
                    _same(proof["resume_receipt"], actual_result)
                else:
                    proof = sync_state._stable_source_control(state["source_control_pending"])
                    if proof is None:
                        _fail("TERMINAL_REQUIRED")
                    _same(proof["receipt"], actual_result)
                    _same(authority, selected_failure)
                    _same(paid.proof.public(), proof["request"]["failure"])
            terminal = {**sync_state._source_terminal5_shape(kind, proof, state), "format_version": 2,
                "authority_ref": ref, "authority_sha256": permanent.digest(
                    _row(observed, "publications" if slot == "publication" else "controls", authority))}
            history = store._source_history(state, kind, terminal)
            after = deepcopy(state)
            if slot == "resume":
                after["budget_pending"] = None
            elif state["budget_pending"] is None and state["source_recovery_pending"] is None and (
                    slot == "publication" and state["source_control_pending"] is None
                    or slot == "failure" and state["pending_publication"] is None):
                after.update(source_execution=None, source_authority=None, source_admission=None,
                             source_recovery_pending=None, source_control_pending=None)
            pins = _SelectedPins(stack, profile, store, observed, selected)
            evidence = paid if paid is not None else control
            with _cas(owner, schema, pins, state, after, outbox=outbox, control=evidence) as guard, guard.writer():
                if control is not None and paid is not None:
                    control.verify_for_commit(guard._writer[0], writer_permit=guard._writer[1])
                store._write_source_history(store._directory, history)
                guard.replace()
            result = deepcopy(after)
    return result


@dataclass(frozen=True, slots=True, init=False, repr=False)
class PublicationCreationGuard17:
    """One exact original-head to reserved/committed publication successor."""
    owner: object
    profile: object
    store: object
    _view: object
    _record_json: str
    _base_entries_json: str
    _base_head_json: str
    _owner: tuple
    _seal: object
    _active: bool
    _context_token: object

    def __init__(self, *, owner, view, record, observed, seal):
        from .sync_source_admission17 import CurrentAdmissionView17
        if (seal is not _SEAL or type(owner) is not permanent.AuthorityGuard
                or type(view) is not CurrentAdmissionView17):
            _fail("FACTORY_REQUIRED")
        for key, value in {"owner": owner, "profile": owner.profile, "store": owner.lock.store,
            "_view": view, "_record_json": execution._canonical(record),
            "_base_entries_json": execution._canonical(observed["entries"]),
            "_base_head_json": execution._canonical(observed["head"]), "_owner": _owner(),
            "_seal": seal, "_active": True, "_context_token": None}.items():
            object.__setattr__(self, key, value)

    @property
    def record(self):
        self._context()
        return json.loads(self._record_json)

    def _context(self):
        if (type(self) is not PublicationCreationGuard17 or self._seal is not _SEAL
                or not self._active or _CREATION.get() is not self
                or _CREATION_CONTEXT.get() is not self or self._owner != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CREATION_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        object.__setattr__(self, "_context_token", _CREATION_CONTEXT.set(self))
        self.owner.lock.verify()

    def _advance_for_view(self, view):
        """Private exact-type handoff to the original live admission view.

        The observer validates the full journal before the bounded two-event
        successor check. This method is never called inside a SQL writer and
        cannot advance State, origin, admission or unrelated permanent records.
        """
        self._context()
        if sql.in_write_window() or view is not self._view:
            _fail("CREATION_CONTEXT_REQUIRED")
        record = self.record
        _same(view.creation_basis, record["creation_basis"])
        observed = self.owner.observe(resume=("publications", record["record_id"], permanent.digest(record)))
        base, head = json.loads(self._base_entries_json), json.loads(self._base_head_json)
        identifier = record["record_id"]
        actual = deepcopy(observed["entries"])
        candidate = actual["publications"].pop(identifier, None)
        original = base["publications"].get(identifier)
        if original is not None:
            _fail("CREATION_CONTEXT_REQUIRED")
        _same(actual, base)
        difference = observed["head"]["ordinal"] - head["ordinal"]
        if candidate is None:
            _same(difference, 0)
            _same(observed["head"], head)
        else:
            _same(candidate["record"], record)
            states = ["reserved"] if candidate["state"] == "reserved" else ["reserved", "committed"]
            _same(difference, len(states))
            directory = self.owner._directory(observed["ledger"], "journal")
            previous = head["sha256"]
            for offset, state in enumerate(states, 1):
                ordinal = head["ordinal"] + offset
                event = self.owner._read(directory, f"{ordinal:08d}.json")
                _same((event["facility_id"], event["ordinal"], event["previous_sha256"],
                       event["kind"], event["key"], event["record_sha256"], event["record"], event["state"]),
                      (head["facility_id"], ordinal, previous, "publications", identifier,
                       permanent.digest(record), record, state))
                previous = permanent.digest(event)
            _same(observed["head"]["sha256"], previous)
        if any(item != ("publications", identifier) for item in observed["incomplete"]):
            _fail("COMMITTED_RECORD_REQUIRED")
        return deepcopy(observed)

    def _advance_for_capture(self, captured):
        self._context()
        if captured is None or captured not in (self._view._admission, self._view._restore):
            _fail("CREATION_CONTEXT_REQUIRED")
        return self._advance_for_view(self._view)

    def check(self, record=None):
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        if record is not None:
            _same(execution._canonical(record), self._record_json)
        # This exact sealed guard alone may renew the view's two mutable head
        # pins after its own proven append. Generic DTOs cannot do so.
        self._view._advance_creation(self)
        self._view.verify_source()
        with sql.connection(self.profile, write=True) as con:
            permit = sql.require_writer(con, profile=self.profile)
            self._view.verify_for_commit(con, writer_permit=permit)
        self._view.verify_source()


@contextmanager
def _publication_creator(owner, view, record, observed):
    guard = PublicationCreationGuard17(owner=owner, view=view, record=record, observed=observed, seal=_SEAL)
    token = _CREATION.set(guard)
    object.__setattr__(guard, "_context_token", _CREATION_CONTEXT.set(guard))
    try:
        guard.check()
        yield guard
        guard.check()
    finally:
        object.__setattr__(guard, "_active", False)
        _CREATION_CONTEXT.reset(guard._context_token)
        _CREATION.reset(token)


def _publication_record17(view, metadata, envelope):
    """Bind one normalized frozen envelope to the same live creation view."""
    from .sync_source_admission17 import require_view
    require_view(view)
    core, basis = view.binding.core, view.creation_basis
    _same((envelope["snapshot"]["version"]["library_id"],
           envelope["snapshot"]["version"]["library_epoch"],
           envelope["recovery_generation"], envelope["recovery_stream"]),
          (core["binding"]["home_library_id"], basis["home_epoch"],
           basis["recovery_generation"], basis["recovery_stream"]))
    selected = [entry for entry in envelope["claims"] if entry["claim"]["item_id"] == core["item_id"]]
    if len(selected) != 1:
        _fail("PUBLICATION_CLAIM_REQUIRED")
    _same(selected[0]["claim"], basis["claim"])
    origin = selected[0]["origin"]
    _same((origin["source_library_id"], origin["source_node_id"],
           hashlib.sha256(origin["submitted_url"].encode("utf-8")).hexdigest()),
          (core["binding"]["home_library_id"], core["binding"]["home_node_id"],
           core["submitted_url_sha256"]))
    return {"format_version": 1, "record_kind": "publication", "record_id": metadata["publication_id"],
        "metadata": deepcopy(metadata), "core_sha256": permanent.digest(core),
        "claims_sha256": permanent.digest(envelope["claims"]), "creation_basis": basis}


def _selected_publication17_locked(store, core):
    """Recover a uniquely committed original descriptor without fresh rights."""
    with _owned(store) as owner:
        observed = owner.observe()
        selected = [item for item in observed["entries"]["publications"].values()
            if item["record"]["core_sha256"] == permanent.digest(core)]
        if not selected:
            return None
        if observed["incomplete"] or len(selected) != 1 or selected[0]["state"] != "committed":
            _fail("PUBLICATION_INCOMPLETE")
        record = selected[0]["record"]
        metadata = store.find_publication(record["record_id"], audit=True)
        _same(metadata, record["metadata"])
        envelope = store.read_publication(metadata)
        _same(permanent.digest(envelope["claims"]), record["claims_sha256"])
        return deepcopy(metadata)


def freeze_publication17_locked(*, profile, store, client, snapshot, claims,
        recovery_generation, recovery_stream):
    """Select original publication bytes before materializing its outbox."""
    from .sync_source_publication_local2 import freeze_publication2_locked
    profile = _selectors(profile, store)
    with store.reference_operation():
        metadata = freeze_publication2_locked(profile=profile, store=store, client=client,
            snapshot=snapshot, claims=claims, recovery_generation=recovery_generation,
            recovery_stream=recovery_stream)
        # The creation view has ended. This independent actual reader/CAS owns
        # original descriptor installation and does not buy current admission.
        install_publication17_locked(profile=profile, store=store, publication_id=metadata["publication_id"])
        return deepcopy(metadata)


def create_publication_descriptor17_locked(*, profile, store, client, publication_id):
    """Complete an actual original selector; loose v1 files remain unknown."""
    from .sync_source_publication_local2 import selected_publication2_locked, complete_publication2_locked
    profile = _selectors(profile, store)
    sync_state._uuid(publication_id)
    with store.reference_operation():
        selected = selected_publication2_locked(profile=profile, store=store)
        if selected is None:
            _fail("PUBLICATION_SELECTION_REQUIRED")
        _same(selected["record_id"], publication_id)
        complete_publication2_locked(profile=profile, store=store, record_id=publication_id,
                                    expected_record_sha256=selected["record_sha256"])
        with _owned(store) as owner:
            observed = owner.observe()
            return permanent.record_ref(_record(observed, "publications", publication_id))


def install_publication17_locked(*, profile, store, publication_id):
    """Install only an already committed publication descriptor and its bytes."""
    profile = _selectors(profile, store)
    sync_state._uuid(publication_id)
    with store.reference_operation(), ExitStack() as stack:
        state = store.read()
        if state["format_version"] != 5 or state["source_execution"] is None:
            _fail("STATE_REQUIRED")
        core = state["source_execution"]
        schema = _Schema(profile, store, core, state, stack)
        with _owned(store) as owner:
            observed = owner.observe()
            origin = _origin(observed, state, core)
            record = _record(observed, "publications", publication_id)
            _same(record["core_sha256"], permanent.digest(core))
            envelope = store.read_publication(record["metadata"])
            _same(permanent.digest(envelope["claims"]), record["claims_sha256"])
            payload_pin = None
            if record["format_version"] == 2:
                from .sync_source_publication_local2 import capture_payload2_locked, validate_payload_bytes2
                payload_pin = capture_payload2_locked(profile=profile, payload_ref=record["payload_ref"],
                    stack=stack, check_cancelled=store.check_cancelled)
                _same(validate_payload_bytes2(payload_pin.raw, record["payload_ref"], record=record), envelope)
            basis = record["creation_basis"]
            _same((state["local_epoch"], state["recovery"]["generation"], state["recovery"]["stream"],
                   state["recovery"]["revision"], _lease_key(state["lease"]), _entry(state, core)["claim"],
                   state["source_admission"]),
                  (basis["home_epoch"], basis["recovery_generation"], basis["recovery_stream"],
                   basis["control_revision"], _lease_key(basis["session"]), basis["claim"], basis["admission_ref"]))
            ref = permanent.record_ref(record)
            if state["source_authority"]["publication"] not in (None, ref):
                _fail("PUBLICATION_CHANGED")
            if state["pending_publication"] not in (None, record["metadata"]):
                _fail("PUBLICATION_CHANGED")
            store.assert_sendable(publication_id)
            after = deepcopy(state)
            after["source_authority"]["publication"] = ref
            after["pending_publication"] = deepcopy(record["metadata"])
            after["legacy_pending_stream"] = None
            pins = _SelectedPins(stack, profile, store, observed,
                                 [("origins", origin), ("publications", record)])
            if payload_pin is not None:
                from .sync_source_publication_local2 import PublicationSelectionPins2
                pins = PublicationSelectionPins2(pins, payload_pin)
            # Freeze all original bytes before taking the independent writer.
            outbox = witness._CheckpointCache(store, {"value": after}, stack)
            with _cas(owner, schema, pins, state, after, outbox=outbox) as guard, guard.writer():
                guard.replace()
            outbox.verify_full()
            result = deepcopy(after)
    return result
