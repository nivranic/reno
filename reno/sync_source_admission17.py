"""Lexical Home17 admission for one physical opportunity and two writers.

Paid replay uses detached original binding facts. Only after replay and long
input verification does this factory capture current transport/worker/State
permission. Reserve and dispatch consume their own actual SQL writer tokens;
the proof and every local resource must end before an external effect starts.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from importlib import import_module
import json
import os
import threading

from . import budget_writer_witness as sql, home_recovery_runtime17 as runtime
from . import home_recovery_schema as restore_schema
from . import sync_client, sync_source_authority as permanent, sync_source_claim as claims
from . import sync_source_binding17 as bindings, sync_source_genesis17 as genesis
from . import sync_source_readmit_contract as wire
from .sync_protocol import SyncError


_SEAL = object()
_CURRENT = ContextVar("reno_home_current_admission17", default=None)
_CONTEXT = ContextVar("reno_home_current_admission17_context", default=None)


def _fail(code):
    raise SyncError("Current Home source admission could not be verified",
        "SYNC_SOURCE_ADMISSION17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("CURRENT_MISMATCH")


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


class _CurrentTransport:
    """Own exact real heartbeat/continuity observations, without ordinary pull."""
    def __init__(self, client, source):
        if type(client) is not sync_client.SyncClient:
            _fail("TRANSPORT_REQUIRED")
        self.client, self.broker, self.source_json = client, client._source_claim_broker, claims._encoded(source)
        with self.broker.lock:
            self.continuity = self.broker.latest.get("source/continuity")
        if self.continuity is None:
            _fail("TRANSPORT_REQUIRED")

    def _actual(self, observation, endpoint):
        if (type(observation) is not claims._Observation or type(observation.call) is not claims._Call):
            _fail("TRANSPORT_REQUIRED")
        call, frame = observation.call, observation.call.frame
        if (type(frame) is not claims._RequestFrame or frame._seal is not claims._FRAME_SEAL
                or frame.call is not call or frame.active or not frame.completed or frame.phase != "closed"
                or call.client is not self.client or call.endpoint != endpoint
                or call.transport is not self.client._transport or call.target_url != self.client._url
                or claims._encoded(observation.request["source"]) != self.source_json):
            _fail("TRANSPORT_REQUIRED")

    def check(self, *, state, session=None, recovery=None, claim=None, claim_until=None):
        self.client._check_transport()
        if self.client._check_cancelled is not None:
            self.client._check_cancelled()
        with self.broker.lock:
            heartbeat = self.broker.latest.get("heartbeat")
            if (self.broker.process != os.getpid() or heartbeat is None
                    or self.broker.latest.get("source/continuity") is not self.continuity):
                _fail("TRANSPORT_REQUIRED")
            self._actual(heartbeat, "heartbeat")
            self._actual(self.continuity, "source/continuity")
            current, continuity = heartbeat.response, self.continuity.response
            source = json.loads(self.source_json)
            binding = state["binding"]
            expected = {"target_url": self.client._url,
                "home_library_id": source["library_id"], "home_node_id": source["node_id"],
                "target_library_id": continuity["target_identity"]["library_id"],
                "target_node_id": continuity["target_identity"]["node_id"]}
            head_key = ((self.source_json, continuity["receiver_version"]["library_id"]),
                continuity["receiver_version"]["library_epoch"])
            if (head_key in self.broker.continuity_conflicts or continuity["state"] != "current"
                    or continuity["source_identity"] != {key: source[key] for key in ("library_id", "node_id")}
                    or continuity["handshake"] != source["handshake"]
                    or continuity["target_identity"] != self.continuity.request["target"]
                    or binding != expected or state["session_id"] != current["lease"]["session_id"]
                    or state["lease"] is None
                    or claims._lease_key(state["lease"]) != claims._lease_key(current["lease"])
                    or state["lease"]["lease_until"] > current["lease"]["lease_until"]
                    or not claims._live(state["lease"]["lease_until"])
                    or not claims._live(current["lease"]["lease_until"])
                    or current["recovery"]["state"] != "running" or state["recovery"] != current["recovery"]
                    or current["recovery"]["stream"] != {key: continuity["receiver_version"][key]
                        for key in ("library_id", "library_epoch")}):
                _fail("CURRENT_MISMATCH")
            if session is not None:
                if (claims._lease_key(session) != claims._lease_key(current["lease"])
                        or session["lease_until"] > state["lease"]["lease_until"]):
                    _fail("CURRENT_MISMATCH")
            if recovery is not None:
                _same({key: current["recovery"][key] for key in ("generation", "stream", "revision")}, recovery)
            if claim is not None:
                selected = [entry for entry in state["claims"] if entry["item_id"] == claim["item_id"]]
                if (len(selected) != 1 or selected[0]["claim"] != claim
                        or claim_until is None or not claims._live(claim_until)
                        or selected[0]["lease_until"] != claim_until):
                    _fail("CLAIM_EXPIRED")
            return deepcopy(continuity["continuity"])


@dataclass(frozen=True, slots=True, init=False, repr=False)
class CurrentAdmissionView17:
    binding: object
    _prepared: object
    _transport: object
    _initial: object
    _admission: object
    _restore: object
    _basis_json: str
    _job: object
    _owner: tuple
    _seal: object
    _active: bool
    _context_token: object
    _creation_guard: object

    def __init__(self, *, prepared, transport, initial, admission, restore, basis, job, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        values = {"binding": prepared.facts, "_prepared": prepared, "_transport": transport,
            "_initial": initial, "_admission": admission, "_restore": restore,
            "_basis_json": claims._encoded(basis), "_job": deepcopy(job), "_owner": _owner(),
            "_seal": seal, "_active": True, "_context_token": None, "_creation_guard": None}
        for name, value in values.items():
            object.__setattr__(self, name, value)

    def _context(self):
        if (type(self) is not CurrentAdmissionView17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self or self._owner != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        object.__setattr__(self, "_context_token", _CONTEXT.set(self))

    @property
    def kind(self):
        self._context()
        return json.loads(self._basis_json)["kind"]

    @property
    def creation_basis(self):
        self._context()
        return json.loads(self._basis_json)

    def _current(self):
        self._context()
        basis = json.loads(self._basis_json)
        state = self._prepared.store.read()
        if any(state[name] is not None for name in ("source_recovery_pending", "source_control_pending", "budget_pending")):
            _fail("CONTROL_PENDING")
        if self._initial is not None:
            self._initial.check(state=state)
            _same(self._initial.entry["claim"], basis["claim"])
            continuity = self._transport.check(state=state)
        else:
            result = self._admission.result
            continuity = self._transport.check(state=state, session=result["session"],
                recovery={"generation": result["recovery_generation"], "stream": result["recovery_stream"],
                    "revision": result["control_revision"]}, claim=result["claim"],
                claim_until=result["session"]["lease_until"])
            _same(self._admission.admission_ref(), state["source_admission"])
        _same(continuity, basis["continuity"])
        _same((state["local_epoch"], state["source_execution"], state["source_authority"]["origin"]),
            (basis["home_epoch"], self.binding.core, self.binding.origin_ref))

    def _fixed(self, connection, writer):
        self._context()
        if sql.require_writer(connection, profile=self.binding.profile) is not writer or writer.schema_version != 17:
            _fail("WRITER_PERMIT_REQUIRED")
        self._prepared.short(connection)
        genesis._worker(self.binding.profile, connection, expected=self._job)
        self._current()
        # Child consumers register their own original-token fixed comparisons.
        # The final closure here repeats only our already prepared comparisons.
        sql.check(writer, connection=connection, profile=self.binding.profile)

    def verify_for_commit(self, connection, *, writer_permit):
        """Consume this exact original SQL token; never open or borrow a writer."""
        self._fixed(connection, writer_permit)
        if self._admission is not None:
            self._admission.verify_for_commit(connection, writer_permit=writer_permit)
        if self._restore is not None:
            self._restore.verify_for_commit(connection, writer_permit=writer_permit)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
            connection=connection, profile=self.binding.profile)
        self._fixed(connection, writer_permit)

    def verify_source(self):
        """Full validation remains outside every owned SQL writer frame."""
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        self._prepared.full()
        if self._admission is not None:
            self._admission.verify_source()
        if self._restore is not None:
            self._restore.verify_source()
        with sql.connection(self.binding.profile) as con:
            genesis._worker(self.binding.profile, con, expected=self._job)
        self._current()

    def _advance_creation(self, producer):
        """One closed publication producer may advance only its own two heads.

        The producer rereads the full original journal outside SQL and proves
        an exact reserved/committed successor for its frozen descriptor. State,
        origin, admission, transport and SQL identity remain unchanged.
        """
        self._context()
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        from .sync_source_lifecycle17 import PublicationCreationGuard17
        from .sync_source_publication_local2 import PublicationCreationGuard2
        if (type(producer) not in {PublicationCreationGuard17, PublicationCreationGuard2} or producer._view is not self
                or producer.profile.identity != self.binding.profile.identity or producer.store is not self.binding.store):
            _fail("CREATION_PRODUCER_REQUIRED")
        if self._creation_guard is None:
            _same(json.loads(producer._base_head_json), self._prepared.pins.head)
        elif self._creation_guard is not producer:
            _fail("CREATION_PRODUCER_REQUIRED")
        observed = producer._advance_for_view(self)
        if observed["head"]["ordinal"] < self._prepared.pins.head["ordinal"]:
            _fail("CURRENT_MISMATCH")
        if observed["head"] != self._prepared.pins.head:
            self._prepared.pins._advance_heads(observed)
        if self._admission is not None:
            self._admission._advance_creation(producer)
        if self._restore is not None:
            # The same selected restore capture must prove the same sealed
            # successor. Never replace it with a fresh caller certificate.
            self._restore._advance_creation(producer)
        object.__setattr__(self, "_creation_guard", producer)
        self._current()


def require_view(value, *, binding=None):
    if type(value) is not CurrentAdmissionView17:
        _fail("FACTORY_REQUIRED")
    value._context()
    if binding is not None:
        bindings.require_binding(binding)
        _same((value.binding.core_json, value.binding.submitted_url, value.binding.origin_ref),
            (binding.core_json, binding.submitted_url, binding.origin_ref))
    return value


def _same_core_records(prepared):
    digest = permanent.digest(prepared.facts.core)
    found = []
    for folder in ("controls", "requests", "receipts", "publications"):
        for item in prepared.entries[folder].values():
            record = item["record"]
            core_sha = record.get("core_sha256")
            if core_sha is None and type(record.get("result")) is dict:
                core_sha = record["result"].get("core_sha256")
            if core_sha == digest:
                found.append((folder, item))
    return found


def _permanent_branch(prepared, state):
    """Decide from the actual journal, never only rewindable State pointers."""
    related = _same_core_records(prepared)
    ref = state["source_admission"]
    local = import_module("reno.sync_source_readmit_local2")
    settlement = local.current_settlement2(prepared.entries, state)
    graphs = {}
    for graph in settlement["graphs"]:
        for value in (*graph["ancestor_refs"], graph["leaf_ref"]):
            graphs[value["record_id"]] = graph

    def actual_graph(identifier):
        if identifier not in graphs:
            graph = local.control_retirement_graph2(prepared.entries, identifier)
            for value in (*graph["ancestor_refs"], graph["leaf_ref"]):
                graphs[value["record_id"]] = graph
        return graphs[identifier]

    current = {value["record_id"] for value in settlement["current_control_refs"]}
    retired = {value["record_id"] for value in settlement["retired_control_refs"]}
    legacy_admissions = {value["admission_id"]: value for value in settlement["legacy_admission_refs"]}
    history_only = {value["record_id"] for value in settlement["history_only_control_refs"]}
    # A known positive admission cannot cover another subsequently frozen
    # control, a new targeted readmit intent, or an uninstalled publication.
    # Its actual mixed-version ancestor chain remains readable by local2.
    ancestors, identifier = set(), None if ref is None else ref["admission_id"]
    historical_controls = set(history_only)
    while identifier is not None:
        if identifier in ancestors or len(ancestors) >= permanent.MAX_RECORDS:
            _fail("ADMISSION_REQUIRED")
        ancestors.add(identifier)
        item = prepared.entries["receipts"].get(identifier)
        if item is None or item["state"] != "committed":
            _fail("ADMISSION_REQUIRED")
        result = item["record"]["result"]
        if result.get("admission_id") != identifier:
            _fail("ADMISSION_REQUIRED")
        request_item = prepared.entries["requests"].get(identifier)
        if request_item is None or request_item["state"] != "committed":
            _fail("ADMISSION_REQUIRED")
        request_record = request_item["record"]
        request = request_record["request"]
        if (request["kind"] != "source_readmit"
                or request_record["request_sha256"] != result["request_sha256"]):
            _fail("ADMISSION_REQUIRED")
        _same(wire.normalize_readmit_request(request), request)
        _same(wire.normalize_readmit_result(request, result), result)
        _same(result["core_sha256"], permanent.digest(prepared.facts.core))
        if item["record"]["format_version"] == 1:
            _same(local.admission_ref(item["record"]), legacy_admissions.get(identifier))
            identifier = result["previous_admission_id"]
            continue
        if item["record"]["format_version"] == 2:
            _same(item["record"]["request_ref"], permanent.record_ref(request_record))
        selected_ids = set()
        for kind, value in (("budget_defer", request["defer_receipt"]),
                            ("budget_resume", request["resume_receipt"])):
            if value is None:
                continue
            control = prepared.entries["controls"].get(value["request_id"])
            if control is None or control["state"] != "committed":
                _fail("ADMISSION_REQUIRED")
            record = control["record"]
            _same((record["request"]["kind"], record["request_sha256"], record["core_sha256"]),
                  (kind, value["request_sha256"], result["core_sha256"]))
            positives, negatives = local._control_outcomes2(prepared.entries, record)
            if negatives or not any(fact == value for _receipt, fact, _disposition in positives):
                _fail("ADMISSION_REQUIRED")
            selected_ids.add(value["request_id"])
        for disposition in request["dispositions"]:
            stored = prepared.entries["receipts"].get(disposition["request_id"])
            if (stored is None or stored["state"] != "committed"
                    or stored["record"]["result"] != disposition):
                _fail("ADMISSION_REQUIRED")
            selected_ids.add(disposition["original_request_id"])
        for selected_id in selected_ids:
            if selected_id in history_only:
                _fail("ADMISSION_REQUIRED")
            graph = actual_graph(selected_id)
            if graph["leaf_ref"]["record_id"] != selected_id:
                _fail("CONTROL_PENDING")
            historical_controls.add(selected_id)
            historical_controls.update(value["record_id"] for value in graph["ancestor_refs"])
        identifier = result["previous_admission_id"]
    known_controls = current | retired | historical_controls
    for folder, item in related:
        if folder != "controls":
            continue
        record = item["record"]
        if record["record_id"] in history_only:
            continue
        # Every old negative stays a dependency of its unique retirement leaf.
        # The graph is an integrity check, never a paid/current capability.
        actual_graph(record["record_id"])
    for folder, item in related:
        record = item["record"]
        if item["state"] != "committed":
            _fail("CONTROL_PENDING")
        if folder == "controls" and record["record_id"] not in known_controls:
            _fail("CONTROL_PENDING")
        if folder == "publications":
            _fail("CONTROL_PENDING")
        if folder == "requests":
            request = record["request"]
            if request["kind"] == "source_readmit" and record["record_id"] not in ancestors:
                _fail("ADMISSION_REQUIRED")
            if (request["kind"] in {"source_register", "source_dispose"}
                    and request["original_request"]["request_id"] not in known_controls):
                _fail("CONTROL_PENDING")
    return bool(current)


def _admitted(stack, prepared):
    ref = prepared.state["value"]["source_admission"]
    # Only the independent selector-only permanent actual producer reader can
    # supply the new seal. Old local receipt dictionaries cannot authorize17.
    try:
        local = import_module("reno.sync_source_readmit_local2")
    except ModuleNotFoundError as exc:
        if exc.name != "reno.sync_source_readmit_local2":
            raise
        _fail("ADMISSION_PRODUCER_REQUIRED")
    captured = stack.enter_context(local.capture_admission_locked(profile=prepared.profile,
        store=prepared.store, admission_id=ref["admission_id"], expected_record_sha256=ref["record_sha256"]))
    if type(captured) is not local.AdmissionReceiptCapture2:
        _fail("ADMISSION_PRODUCER_REQUIRED")
    captured.verify_source()
    _same(captured.admission_ref(), ref)
    _same(captured.origin_record, prepared.origin)
    _same(captured.core, prepared.facts.core)
    request, result = captured.request, captured.result
    _same(wire.normalize_readmit_request(request), request)
    _same(wire.normalize_readmit_result(request, result), result)
    # V1 receipt session expiry was a caller echo without the new genuine
    # heartbeat lower-bound producer gate. It remains historical chain data.
    if request["format_version"] != 2 or result["replayed"] is not False:
        _fail("ADMISSION_PRODUCER_REQUIRED")
    _same((result["current_home_epoch"], result["core_sha256"], result["item_id"], result["submitted_url_sha256"]),
        (prepared.version["library_epoch"], permanent.digest(prepared.facts.core),
            prepared.facts.core["item_id"], prepared.facts.core["submitted_url_sha256"]))
    restoration = captured.preservation_capture
    provenance = captured.preservation_provenance
    if prepared.version["library_epoch"] != prepared.facts.core["local_epoch"]:
        if provenance is None:
            _fail("RESTORE_REQUIRED")
        wire._exact(provenance, ("format_version", "producer", "operation_id", "receipt_sha256"))
        if provenance["format_version"] != 1 or provenance["producer"] != "home_knowledge_restore":
            _fail("RESTORE_REQUIRED")
        if type(restoration) is not runtime.CompletedRestoreCapture17:
            _fail("RESTORE_REQUIRED")
        _same(restoration.provenance_ref(), provenance)
    elif request["preservation"] is not None or provenance is not None or restoration is not None:
        _fail("RESTORE_UNEXPECTED")
    return captured, restoration


def _actual_observation17(client, broker, observation, endpoint, source):
    """Read the actual closed HTTP frame, not a caller response dictionary."""
    if (type(observation) is not claims._Observation or type(observation.call) is not claims._Call):
        _fail("TRANSPORT_REQUIRED")
    call, frame = observation.call, observation.call.frame
    if (type(frame) is not claims._RequestFrame or frame._seal is not claims._FRAME_SEAL
            or frame is not observation._frame or frame.call is not call or frame.client is not client or frame.active
            or not frame.completed or frame.phase != "closed" or frame.endpoint != endpoint
            or frame.owner != call.owner or frame.target_url != call.target_url
            or call.client is not client or call.endpoint != endpoint
            or call.transport is not client._transport or call.target_url != client._url
            or broker.client is not client or broker.process != os.getpid()
            or claims._encoded(json.loads(frame.content)) != call.request_json
            or frame._observation is not observation
            or frame._observation_json != (call.request_json, observation.response_json)
            or observation.request["source"] != source):
        _fail("TRANSPORT_REQUIRED")


def _current_restore17(stack, prepared):
    """A changed Home epoch needs the actual current completed restore chain."""
    core = prepared.facts.core
    if prepared.version["library_epoch"] == core["local_epoch"]:
        return None
    with sql.connection(prepared.profile) as con:
        rows = con.execute("SELECT operation_id,result_sha256 FROM " + restore_schema.TABLE
            + " WHERE after_library_epoch=? AND library_id=?", (prepared.version["library_epoch"],
                core["binding"]["home_library_id"])).fetchall()
    if len(rows) != 1:
        _fail("RESTORE_REQUIRED")
    restored = stack.enter_context(runtime.capture_completed_locked(profile=prepared.profile,
        store=prepared.store, operation_id=rows[0][0], expected_result_sha256=rows[0][1]))
    if type(restored) is not runtime.CompletedRestoreCapture17:
        _fail("RESTORE_REQUIRED")
    observation = restored.preservation_observation(from_epoch=core["local_epoch"])
    _same(observation["source_core_sha256"], permanent.digest(core))
    _same((observation["preservation"]["from_epoch"], observation["preservation"]["to_epoch"]),
        (core["local_epoch"], prepared.version["library_epoch"]))
    restored.verify_source()
    return restored


def _historical_admitted17(stack, prepared):
    """A prior admission is historical evidence, never a current view."""
    local = import_module("reno.sync_source_readmit_local2")
    factory = getattr(local, "capture_admission_history_locked", None)
    capture_type = getattr(local, "HistoricalAdmissionReceiptCapture2", None)
    if factory is None or capture_type is None:
        _fail("ADMISSION_PRODUCER_REQUIRED")
    ref = prepared.state["value"]["source_admission"]
    captured = stack.enter_context(factory(profile=prepared.profile, store=prepared.store,
        admission_id=ref["admission_id"], expected_record_sha256=ref["record_sha256"]))
    if type(captured) is not capture_type:
        _fail("ADMISSION_PRODUCER_REQUIRED")
    captured.verify_source()
    _same(captured.admission_ref(), ref)
    _same(captured.origin_record, prepared.origin)
    _same(captured.core, prepared.facts.core)
    request, result = captured.request, captured.result
    _same(wire.normalize_readmit_request(request), request)
    _same(wire.normalize_readmit_result(request, result), result)
    if (request["format_version"] != 2 or result["replayed"] is not False
            or not prepared.facts.core["local_epoch"] <= result["current_home_epoch"] < prepared.version["library_epoch"]):
        _fail("ADMISSION_PRODUCER_REQUIRED")
    _same((result["core_sha256"], result["item_id"], result["submitted_url_sha256"]),
        (permanent.digest(prepared.facts.core), prepared.facts.core["item_id"],
            prepared.facts.core["submitted_url_sha256"]))
    return captured


def _scheduling17(stack, prepared, client, job):
    """Classify only genuine missing/expired permission after stable proofs.

    Returning an action cannot grant permission. Every mismatched positive
    frame, receipt, permanent branch or worker remains a hard rejection.
    """
    state, core = prepared.state["value"], prepared.facts.core
    if type(client) is not sync_client.SyncClient or client._source_claim_broker.client is not client:
        _fail("TRANSPORT_REQUIRED")
    _same(client._url, state["binding"]["target_url"])
    if any(state[name] is not None for name in (
            "source_recovery_pending", "source_control_pending", "budget_pending", "pending_publication")):
        _fail("CONTROL_PENDING")
    needs_control_settlement = _permanent_branch(prepared, state)
    if any(item["state"] != "committed" for folder in prepared.entries.values() for item in folder.values()):
        _fail("CONTROL_PENDING")
    if not needs_control_settlement and state["source_admission"] is None and any(
            state["source_authority"][key] is not None for key in ("defer", "resume", "failure", "publication")):
        _fail("ADMISSION_REQUIRED")
    prior, restoration, saved = None, None, None
    legacy_admission = False
    if state["source_admission"] is None:
        expected_claim = prepared.origin["claim"]
        restoration = _current_restore17(stack, prepared)
    else:
        ref = state["source_admission"]
        record = prepared.entries["receipts"][ref["admission_id"]]["record"]
        if record["format_version"] == 1:
            from . import sync_source_legacy_ancestry2 as legacy
            with legacy.capture_legacy_ancestry_locked(profile=prepared.profile, store=prepared.store) as old:
                if old is None:
                    _fail("ADMISSION_REQUIRED")
                _same((old.admission_ref(), old.core, old.origin_ref),
                      (ref, core, prepared.facts.origin_ref))
                saved = old.result
            restoration = _current_restore17(stack, prepared)
            legacy_admission = True
        elif record["result"]["current_home_epoch"] == prepared.version["library_epoch"]:
            prior, restoration = _admitted(stack, prepared)
            saved = prior.result
        else:
            # The historical prefix factory owns a real current-chain reader.
            # End it before independently acquiring the new current restore;
            # neither reader is borrowed or made to cover another endpoint.
            with ExitStack() as historical:
                old = _historical_admitted17(historical, prepared)
                saved = old.result
            restoration = _current_restore17(stack, prepared)
        expected_claim = saved["claim"]
    selected = [entry for entry in state["claims"] if entry["item_id"] == core["item_id"]]
    if len(selected) != 1 or selected[0]["claim"] != expected_claim:
        _fail("CURRENT_MISMATCH")
    broker = client._source_claim_broker
    client._check_transport()
    if client._check_cancelled is not None:
        client._check_cancelled()
    with broker.lock:
        observed = tuple(broker.latest.get(name) for name in ("heartbeat", "pull", "source/continuity"))
        heartbeat, pull, continuity = observed
        if broker.process != os.getpid() or broker.continuity_conflicts:
            _fail("TRANSPORT_REQUIRED")
        for name, observation in zip(("heartbeat", "pull", "source/continuity"), observed):
            if observation is not None:
                _actual_observation17(client, broker, observation, name, prepared.source)
        # Absence only asks the outer scheduler to collect an observation. It
        # is never used as a claim, negative receipt or preservation proof.
        action = "observe" if heartbeat is None or continuity is None else None
        if heartbeat is not None:
            heart = heartbeat.response
            if (state["lease"] is None or state["session_id"] != heart["lease"]["session_id"]
                    or claims._lease_key(state["lease"]) != claims._lease_key(heart["lease"])
                    or state["lease"]["lease_until"] > heart["lease"]["lease_until"]
                    or not claims._live(state["lease"]["lease_until"])
                    or not claims._live(heart["lease"]["lease_until"])
                    or state["recovery"] != heart["recovery"] or heart["recovery"]["state"] != "running"):
                _fail("CURRENT_MISMATCH")
        if continuity is not None:
            response = continuity.response
            if (response["state"] != "current" or response["source_identity"] != {
                    key: prepared.source[key] for key in ("library_id", "node_id")}
                    or response["target_identity"] != continuity.request["target"]
                    or response["target_identity"] != {"library_id": core["binding"]["target_library_id"],
                        "node_id": core["binding"]["target_node_id"]}
                    or response["handshake"] != prepared.source["handshake"]
                    or state["recovery"] is None or state["recovery"]["stream"] != {
                        key: response["receiver_version"][key] for key in ("library_id", "library_epoch")}):
                _fail("CURRENT_MISMATCH")
        stale = needs_control_settlement or (state["source_admission"] is None and (
            prepared.version["library_epoch"] != core["local_epoch"]
            or (state["recovery"]["generation"], state["recovery"]["stream"]) != (
                core["recovery_generation"], core["recovery_stream"])))
        if saved is not None:
            if selected[0]["lease_until"] != saved["session"]["lease_until"]:
                _fail("CURRENT_MISMATCH")
            stale = legacy_admission or needs_control_settlement or (saved["current_home_epoch"] != prepared.version["library_epoch"]
                or claims._lease_key(saved["session"]) != claims._lease_key(state["lease"])
                or not claims._live(saved["session"]["lease_until"])
                or (saved["recovery_generation"], saved["recovery_stream"], saved["control_revision"]) != (
                    state["recovery"]["generation"], state["recovery"]["stream"], state["recovery"]["revision"]))
            # A same-current-epoch admission remains independent of ordinary
            # pull, as its real producer separately established this claim.
            if not stale and action is None:
                _same(prior.creation_basis["continuity"], continuity.response["continuity"])
        needs_pull = saved is None or stale
        if needs_pull:
            if pull is None:
                action = "observe"
            else:
                request, response = pull.request, pull.response
                if (state["lease"] is None
                        or claims._lease_key(request["lease"]) != claims._lease_key(state["lease"])
                        or request["lease"]["lease_until"] > state["lease"]["lease_until"]
                        or heartbeat is not None and state["lease"]["lease_until"] > heartbeat.response["lease"]["lease_until"]
                        or response["recovery"] != state["recovery"]):
                    _fail("CURRENT_MISMATCH")
                entries = [entry for entry in response["claims"] if entry["item_id"] == core["item_id"]]
                if len(entries) == 1:
                    _same(entries[0], selected[0])
                    _same(entries[0]["claim"], expected_claim)
                    if not claims._live(entries[0]["lease_until"]):
                        stale = True
                elif request["claim_limit"] == 0 and response["claims"] == []:
                    stale = True
                else:
                    _fail("CURRENT_MISMATCH")
        if stale and action is None:
            action = "readmit"
        observations = tuple((value, None if value is None else (value.call.request_json, value.response_json))
            for value in observed)
    fingerprint = claims._encoded({"state": state, "source": prepared.source, "version": prepared.version,
        "cookie": prepared.cookie, "schema_sha": prepared.schema_sha, "origin": prepared.origin,
        "entries": prepared.entries, "job": job,
        "restore": None if restoration is None else restoration.provenance_ref()})
    prepared.full()
    with sql.connection(prepared.profile) as con:
        genesis._worker(prepared.profile, con, expected=job)
    return action, fingerprint, observations


def _recheck_scheduling17(signal):
    """Consumed only after the original lexical proof has completely ended."""
    with bindings._capture_prepared_locked(profile=signal._profile, store=signal._store,
            item_id=signal._item_id) as prepared, ExitStack() as stack:
        with sql.connection(prepared.profile) as con:
            job = genesis._worker(prepared.profile, con)
            _same((job["job_id"], job["continuation_id"], job["execution_generation"]),
                (prepared.facts.core["root_job_id"], prepared.facts.core["continuation_id"],
                    prepared.facts.core["execution_generation"]))
        with ExitStack() as proof:
            action, fingerprint, observations = _scheduling17(proof, prepared, signal._client, job)
        if (action != signal._action or fingerprint != signal._fingerprint
                or len(observations) != len(signal._observations)
                or any(actual[0] is not expected[0] or actual[1] != expected[1]
                    for actual, expected in zip(observations, signal._observations))):
            _fail("CURRENT_MISMATCH")


@contextmanager
def capture_current_admission_locked(*, profile, store, client, item_id, binding=None):
    """Capture one genuine current permission; accept selectors or exact facts."""
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    if binding is not None:
        bindings.require_binding(binding, profile=profile, store=store, item_id=item_id)
    with bindings._capture_prepared_locked(profile=profile, store=store, item_id=item_id) as prepared, ExitStack() as stack:
        if binding is not None:
            _same((prepared.facts.core_json, prepared.facts.submitted_url, prepared.facts.origin_ref),
                (binding.core_json, binding.submitted_url, binding.origin_ref))
        state, core = prepared.state["value"], prepared.facts.core
        if any(state[name] is not None for name in ("source_recovery_pending", "source_control_pending", "budget_pending")):
            _fail("CONTROL_PENDING")
        _permanent_branch(prepared, state)
        with sql.connection(profile) as con:
            job = genesis._worker(profile, con)
            _same((job["job_id"], job["continuation_id"], job["execution_generation"]),
                (core["root_job_id"], core["continuation_id"], core["execution_generation"]))
        with ExitStack() as proof:
            action, fingerprint, observations = _scheduling17(proof, prepared, client, job)
        if action is not None:
            from .sync_source_opportunity17 import _emit_required17
            _emit_required17(action=action, prepared=prepared, client=client,
                fingerprint=fingerprint, observations=observations)
        transport = _CurrentTransport(client, prepared.source)
        initial, admitted, restoration = None, None, None
        if state["source_admission"] is None:
            # Selection is based on actual State. Failure never falls back from
            # an admission branch or an unresolved permanent control chain.
            if any(state["source_authority"][key] is not None for key in ("defer", "resume", "failure", "publication")):
                _fail("ADMISSION_REQUIRED")
            _same(prepared.version["library_epoch"], core["local_epoch"])
            initial = stack.enter_context(claims.capture_initial_claim(client, source=prepared.source, item_id=item_id))
            initial.check(state=state)
            _same(initial.entry["claim"], prepared.origin["claim"])
            _same((state["recovery"]["generation"], state["recovery"]["stream"]),
                (core["recovery_generation"], core["recovery_stream"]))
            kind, session, claim = "original_core", state["lease"], initial.entry["claim"]
        else:
            admitted, restoration = _admitted(stack, prepared)
            result = admitted.result
            kind, session, claim = "admission", result["session"], result["claim"]
        continuity = transport.check(state=state)
        if admitted is not None:
            _same(admitted.creation_basis["continuity"], continuity)
        basis = {"format_version": 1, "kind": kind, "core_sha256": permanent.digest(core),
            "home_epoch": prepared.version["library_epoch"], "session": deepcopy(session), "claim": deepcopy(claim),
            "recovery_generation": state["recovery"]["generation"], "recovery_stream": deepcopy(state["recovery"]["stream"]),
            "control_revision": state["recovery"]["revision"], "origin_ref": prepared.facts.origin_ref,
            "admission_ref": deepcopy(state["source_admission"]), "continuity": continuity}
        permanent._basis(basis, permanent.digest(core), core["binding"])
        value = CurrentAdmissionView17(prepared=prepared, transport=transport, initial=initial,
            admission=admitted, restore=restoration, basis=basis, job=job, seal=_SEAL)
        token = _CURRENT.set(value)
        object.__setattr__(value, "_context_token", _CONTEXT.set(value))
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            object.__setattr__(value, "_active", False)
            _CONTEXT.reset(value._context_token)
            _CURRENT.reset(token)
