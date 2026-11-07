"""Original wire1 pending recovery with an actual unreferenced old origin.

An immutable intent's expected_before remains original. A selector-only stage
can close its intent/State gap without filling the origin slot. One subsequent
receipt-first CAS installs the actual origin reference and the original result
effect together. This module produces no genesis, paid permit or fresh lease.
Exact authority, transport and StateCAS dispatch is intentionally required.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import uuid

from . import budget_writer_witness as sql, home_recovery_runtime17 as runtime
from . import source_preservation_witness as witness, storage_lifecycle as lifecycle
from . import storage_source_authority_roots as home_roots
from . import sync_source_admission_history1 as history, sync_source_authority as permanent
from . import sync_source_claim as engine, sync_source_execution as execution
from . import sync_source_legacy_intake17 as legacy, sync_source_legacy_origin_intake17 as origin_intake
from . import sync_source_lifecycle17 as effects, sync_source_readmit_contract as wire
from . import sync_source_transport17 as transport, sync_state
from .sync_protocol import SyncError

_SEAL = object()
_CURRENT = ContextVar("reno_legacy_pending17_capture", default=None)
_CONTEXT = ContextVar("reno_legacy_pending17_context", default=None)
_PUBLISHER = ContextVar("reno_legacy_pending17_http_publisher", default=None)
_HTTP = ContextVar("reno_legacy_pending17_http_call", default=None)


def _fail(code):
    raise SyncError("Original pending recovery could not be bridged",
        "SYNC_SOURCE_LEGACY_PENDING17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("ORIGINAL_CHANGED")


def _selectors(profile, store, request_id, request_sha256, expected_intent_sha256=None):
    profile = legacy._selectors(profile, store)
    permanent._uuid(request_id)
    permanent._sha(request_sha256)
    if expected_intent_sha256 is not None:
        permanent._sha(expected_intent_sha256)
    return profile


def _pending(intent):
    return {"format_version": 1, "kind": intent["request"]["kind"],
        "request_id": intent["record_id"], "request_sha256": intent["request_sha256"],
        "core_sha256": intent["core_sha256"], "intent_sha256": permanent.digest(intent)}


def _original_bare16_preservation(pins, profile, rows, intent, core):
    """Compare an original wire1 prefix; never establish certificate17 trust.

    The owning reader has already verified every bare row with _legacy_row:
    its actual pinned protection image reconstructs the original SQL/frontier
    and binds the original intent's before_capture_sha256. Reading that same
    intent again through the held pins recovers the released16 comparison
    hash. This returns facts only, without a restore/admission capability.
    A modern selected wrapper stays with its existing17 validator.
    """
    request, provenance = intent["request"], intent["preservation_basis"]
    if request["current_home_epoch"] == core["local_epoch"]:
        return None
    if provenance is None:
        _fail("ORIGINAL_RESTORE_PREFIX_REQUIRED")
    permanent._exact(provenance, ("format_version", "producer", "operation_id", "receipt_sha256"))
    permanent._version(provenance["format_version"])
    permanent._uuid(provenance["operation_id"])
    permanent._sha(provenance["receipt_sha256"])
    if provenance["producer"] != "home_knowledge_restore":
        _fail("ORIGINAL_RESTORE_PREFIX_REQUIRED")
    matches = [(index, row, parsed) for index, (row, parsed) in enumerate(rows)
        if row["operation_id"] == provenance["operation_id"]]
    if len(matches) != 1:
        _fail("ORIGINAL_RESTORE_PREFIX_REQUIRED")
    end, selected, parsed = matches[0]
    _same((selected["result_sha256"], selected["after_library_epoch"]),
        (provenance["receipt_sha256"], request["current_home_epoch"]))
    if parsed.wrapper is not None:
        return None
    if (intent["format_version"] != 1 or request.get("format_version", 1) != 1
            or request["kind"] not in {"source_dispose", "source_readmit"}
            or request["source"]["handshake"]["migration_version"] != 16):
        _fail("ORIGINAL_SOURCE16_RESTORE_REQUIRED")
    _same(wire.normalize_request(request), request)
    _same((request["source"]["library_id"], request["source"]["node_id"], intent["core_sha256"]),
        (core["binding"]["home_library_id"], core["binding"]["home_node_id"], permanent.digest(core)))
    epoch, comparison, frontier = core["local_epoch"], None, None
    lineage = {name: core[name] for name in ("root_job_id", "continuation_id", "execution_generation")}
    for row, checked in rows[:end + 1]:
        if row["after_library_epoch"] <= core["local_epoch"]:
            continue
        if (row["before_library_epoch"] != epoch or row["after_library_epoch"] <= epoch
                or checked.wrapper is not None):
            _fail("ORIGINAL_SOURCE16_RESTORE_CHAIN_INCOMPLETE")
        _same((row["library_id"], row["node_id"], row["original_core_sha256"],
            row["root_job_id"], row["continuation_id"], row["execution_generation"]),
            (core["binding"]["home_library_id"], core["binding"]["home_node_id"], permanent.digest(core),
                core["root_job_id"], core["continuation_id"], core["execution_generation"]))
        _same((checked.checkpoint["source_execution_sha256"], checked.checkpoint["source_execution_lineage"]),
            (permanent.digest(core), lineage))
        original, _ = pins.document(runtime.legacy._intent_path(profile, row["operation_id"]),
            runtime.schema.MAX_REQUEST_BYTES)
        _same((original["operation_id"], original["library_id"], original["node_id"],
            original["request_sha256"], original["profile_sha256"], original["source_binding_sha256"]),
            (row["operation_id"], row["library_id"], row["node_id"], row["request_sha256"],
                runtime.base._sha(list(profile.identity)), row["source_binding_sha256"]))
        _same(original["request"], json.loads(row["request_json"]))
        permanent._sha(original["before_capture_sha256"])
        frontier = json.loads(row["frontier_json"])
        _same((runtime.base._sha(frontier), frontier["root_job_id"]),
            (row["frontier_sha256"], core["root_job_id"]))
        comparison, epoch = original["before_capture_sha256"], row["after_library_epoch"]
    if comparison is None or epoch != request["current_home_epoch"]:
        _fail("ORIGINAL_SOURCE16_RESTORE_CHAIN_INCOMPLETE")
    return {"format_version": 1, "frontier_sha256": runtime.base._sha(frontier),
        "certificate_sha256": comparison, "from_epoch": core["local_epoch"], "to_epoch": epoch}


def _verify_original_restore_prefix(current, pins, profile, rows, intent, core):
    comparison = _original_bare16_preservation(pins, profile, rows, intent, core)
    if comparison is None:
        legacy._restore_prefix(current, intent, core)
    else:
        _same(intent["request"]["preservation"], comparison)


def _verify_saved_restore_history(profile, store, owner, state, intent, origin):
    """Read original restore proof independently before saving/replaying facts.

    Current17 captures close before receipt publication changes the Home head;
    none is relabeled or passed to a publication/paid producer. Bare history
    retains its own actual pins and original comparison hash during this read.
    """
    with ExitStack() as stack:
        schema = origin_intake._OriginSchema17(profile, store, origin["core"], state, stack)
        pins = runtime._RuntimePins(stack, profile, owner.lock.verify)
        rows, current = schema.rows, None
        if any(parsed.wrapper is not None for _, parsed in rows):
            if rows[-1][1].wrapper is None:
                _fail("MODERN_RESTORE_CHAIN_TERMINAL_REQUIRED")
            selected = rows[-1][0]
            current = stack.enter_context(runtime.capture_completed_locked(profile=profile, store=store,
                operation_id=selected["operation_id"], expected_result_sha256=selected["result_sha256"]))
            _same([row for row, _ in current._records], [row for row, _ in rows])
        for row, parsed in rows:
            if parsed.wrapper is None:
                runtime._legacy_row(pins, row, profile)
        _verify_original_restore_prefix(current, pins, profile, rows, intent, origin["core"])
        schema.full()
        pins.verify()


def _original(observed, state, request_id, request_sha256, expected_intent_sha256=None):
    """Resolve the actual immutable origin independently of missing State refs."""
    if state is None or state["format_version"] != 5 or state["source_execution"] is None:
        _fail("STATE5_REQUIRED")
    core = state["source_execution"]
    execution._peer(core["binding"])
    _same(state["binding"], core["binding"])
    intent = history._committed(observed["entries"], "requests", request_id)
    request = intent["request"]
    if request["kind"] not in {"source_dispose", "source_readmit"} or request.get("format_version", 1) != 1:
        _fail("ORIGINAL_WIRE1_REQUIRED")
    _same((intent["core_sha256"], intent["request_sha256"], wire.request_sha256(request)),
        (permanent.digest(core), request_sha256, request_sha256))
    if expected_intent_sha256 is not None:
        _same(permanent.digest(intent), expected_intent_sha256)
    identifier = str(uuid.uuid5(uuid.UUID(core["continuation_id"]),
        "source-origin:v1:" + permanent.digest(core)))
    origin = origin_intake._original(observed["entries"], identifier, core)
    history._original(request, origin)
    before_authority = intent["expected_before"]["source_authority"]
    if before_authority is not None and before_authority["origin"] is not None:
        _fail("MISSING_ORIGINAL_REFERENCE_REQUIRED")
    authority = state["source_authority"]
    if authority is not None:
        _same(authority["core_sha256"], permanent.digest(core))
        if authority["origin"] is not None:
            _same(authority["origin"], permanent.record_ref(origin))
    selected = effects._entry(state, core)
    _same(hashlib.sha256(selected["url"].encode()).hexdigest(), core["submitted_url_sha256"])
    return intent, origin


def _receipt(observed, intent, *, required=True, expected_record_sha256=None):
    item = observed["entries"]["receipts"].get(intent["record_id"])
    if item is None:
        if required:
            _fail("COMMITTED_RECEIPT_REQUIRED")
        return None
    if item["state"] != "committed":
        _fail("ORIGINAL_RECEIPT_COMPLETION_REQUIRED")
    record = item["record"]
    _same((record["format_version"], record["request_sha256"], record["result_sha256"]),
        (1, intent["request_sha256"], permanent.digest(record["result"])))
    parent = record["parent_defer"]
    _same(wire.normalize_result(intent["request"], record["result"],
        defer_receipt=None if parent is None else parent["receipt"]), record["result"])
    if expected_record_sha256 is not None:
        _same(permanent.digest(record), expected_record_sha256)
    return record


def _control(entries, original, origin, records, *, required):
    selected = entries["controls"].get(original["request_id"])
    if selected is None:
        if required:
            _fail("COMMITTED_CONTROL_REQUIRED")
        return None
    record = history._committed(entries, "controls", original["request_id"])
    _same((record["request"], record["request_sha256"], record["core_sha256"]),
        (original, wire.digest(original), origin["core_sha256"]))
    permanent._peer(original, origin["binding"])
    basis = record["creation_basis"]
    if basis["origin_ref"] is not None:
        _same(basis["origin_ref"], permanent.record_ref(origin))
    records.append(record)
    return record


def _parent(entries, state, original, origin, records):
    if original["kind"] != "budget_resume":
        return None
    waiting = state["budget_pending"]
    if (waiting is None or waiting["resume_request"] != original
            or waiting["defer_request"] is None or waiting["defer_receipt"] is None):
        _fail("ORIGINAL_PARENT_REQUIRED")
    parent = {"request": deepcopy(waiting["defer_request"]), "receipt": deepcopy(waiting["defer_receipt"])}
    _same(original["defer_request_id"], parent["request"]["request_id"])
    wire._budget_receipt_for(parent["request"], parent["receipt"])
    _control(entries, parent["request"], origin, records, required=False)
    legacy._historical_parent(entries, {"core_sha256": origin["core_sha256"]}, {"parent_defer": parent})
    return parent


def _facts(con, entries, intent, receipt, origin, state):
    """Read original controls, outcomes, admission ancestors and both manual hashes."""
    request = intent["request"]
    records, manual_rows = [origin, intent], []
    if request["kind"] == "source_readmit":
        previous_id = request["previous_admission_id"]
        chain, consumed, prior_records, manual = legacy._history(entries, origin, previous_id, con, state)
        previous = None if not chain else chain[-1][1]
        _before_admission(intent, origin, previous)
        records.extend(prior_records)
        manual_rows.extend(manual)
        selected = legacy._consumed(entries, origin, intent, previous, records, manual_rows, con)
        if {item["control_ref"]["record_id"] for item in selected} & {
                item["control_ref"]["record_id"] for item in consumed}:
            _fail("CONTROL_ALREADY_CONSUMED")
        if receipt is not None:
            _same(receipt["result"]["previous_admission_id"], previous_id)
            if previous is not None and receipt["result"]["attempt_basis"] == "held_claim":
                _same(receipt["result"]["attempts"], previous["result"]["attempts"])
            _same(receipt["parent_defer"], None)
        facts = (chain, consumed, tuple(selected))
    else:
        original = request["original_request"]
        control = _control(entries, original, origin, records,
            required=receipt is not None and receipt["result"]["disposition"] == "not_applied")
        registration = request["registration"]
        if registration is not None:
            if control is None:
                _fail("COMMITTED_REGISTRATION_CONTROL_REQUIRED")
            history._registration(entries, intent, control, records)
        parent = _parent(entries, state, original, origin, records)
        if receipt is not None:
            _same(receipt["parent_defer"], parent if receipt["result"]["disposition"] == "applied" else None)
            if receipt["result"]["disposition"] == "not_applied" and registration is None:
                _fail("COMMITTED_REGISTRATION_REQUIRED")
            # A present outcome for the same original UUID must agree. A
            # missing SQL row or local control never supplies a negative fact.
            for item in entries["receipts"].values():
                record, result = item["record"], item["record"]["result"]
                if result.get("original_request_id") != original["request_id"] or "disposition" not in result:
                    continue
                _same(item["state"], "committed")
                _same(result["disposition"], receipt["result"]["disposition"])
                if result["disposition"] == "applied":
                    _same(result["original_receipt"], receipt["result"]["original_receipt"])
                records.append(record)
        facts = (control, registration, parent)
    if receipt is not None:
        records.append(receipt)
    unique = {(record["record_kind"], record["record_id"]): record for record in records}
    return tuple(unique.values()), tuple(deepcopy(manual_rows)), witness._digest(facts)


def _before_admission(intent, origin, previous):
    """Independent legacy before check; never temporarily add an origin ref."""
    request, before = intent["request"], intent["expected_before"]
    permanent._before(before, origin["binding"], origin["core_sha256"])
    claim = origin["claim"] if previous is None else previous["result"]["claim"]
    prior = None if previous is None else history._admission_ref(previous)
    _same((before["local_epoch"], before["claim"], before["admission_ref"], request["expected_claim"]),
        (request["current_home_epoch"], claim, prior, claim))
    for key in ("recovery_generation", "recovery_stream", "control_revision"):
        _same(before[key], request[key])
    if before["source_authority"] is not None:
        _same(before["source_authority"]["core_sha256"], origin["core_sha256"])
        ref = before["source_authority"]["origin"]
        if ref is not None:
            _same(ref, permanent.record_ref(origin))


def _after(state, intent, receipt, origin, *, mode):
    """Rebuild the complete sole effect from original persisted facts."""
    core, request = origin["core"], intent["request"]
    _same(state["source_execution"], core)
    _same(intent["expected_before"], effects._before(state, core))
    _same(effects._lease_key(state["lease"]), effects._lease_key(request["lease"]))
    pending = _pending(intent)
    if mode == "stage":
        if state["source_recovery_pending"] not in (None, pending):
            _fail("OTHER_PENDING_REQUIRED")
        after = deepcopy(state)
        after["source_recovery_pending"] = pending
        sync_state._validate(after)
        return after
    if mode != "consume" or receipt is None:
        _fail("ORIGINAL_RECEIPT_REQUIRED")
    _same(state["source_recovery_pending"], pending)
    result, parent = receipt["result"], receipt["parent_defer"]
    _same(wire.normalize_result(request, result,
        defer_receipt=None if parent is None else parent["receipt"]), result)
    after = deepcopy(state)
    authority = after["source_authority"]
    if authority is None:
        authority = {"format_version": 1, "core_sha256": permanent.digest(core),
            "origin": None, "defer": None, "resume": None, "failure": None, "publication": None}
    if authority["origin"] is not None:
        _same(authority["origin"], permanent.record_ref(origin))
    authority["origin"] = permanent.record_ref(origin)
    after["source_authority"] = authority
    if request["kind"] == "source_readmit":
        current = effects._entry(state, core)
        _same(current["claim"], request["expected_claim"])
        _same((state["local_epoch"], state["recovery"]["generation"], state["recovery"]["stream"],
            state["recovery"]["revision"]), (result["current_home_epoch"], result["recovery_generation"],
            result["recovery_stream"], result["control_revision"]))
        after["source_admission"] = history._admission_ref(receipt)
        selected = {**deepcopy(current), "attempts": result["attempts"],
            "claim": deepcopy(result["claim"]), "lease_until": result["session"]["lease_until"]}
        after["claims"] = [selected if entry["item_id"] == core["item_id"] else deepcopy(entry)
            for entry in state["claims"]]
        if request["defer_receipt"] is not None:
            if state["budget_pending"] is not None or request["resume_receipt"] is None:
                _fail("ORIGINAL_WAIT_NOT_CLOSED")
            for slot in ("defer", "resume"):
                ref = authority[slot]
                if ref is not None:
                    _same(ref["record_id"], request[slot + "_receipt"]["request_id"])
                authority[slot] = None
    else:
        original = request["original_request"]
        slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[original["kind"]]
        target = after["source_control_pending"] if slot == "failure" else after["budget_pending"]
        request_key, receipt_key = (("request", "receipt") if slot == "failure" else
            (slot + "_request", slot + "_receipt"))
        if target is None or target[request_key] != original:
            _fail("ORIGINAL_CONTROL_CHANGED")
        if result["disposition"] == "applied":
            positive = result["original_receipt"]
            if target[receipt_key] is not None and not sync_state._same_receipt(target[receipt_key], positive):
                _fail("ORIGINAL_RECEIPT_CHANGED")
            target[receipt_key] = deepcopy(positive)
        elif result["disposition"] == "not_applied":
            if target[receipt_key] is not None:
                _fail("POSITIVE_CONTROL_CANNOT_RETIRE")
            if slot == "failure":
                after["source_control_pending"] = None
            elif slot == "defer":
                if target["resume_request"] is not None:
                    _fail("ORIGINAL_PARENT_IN_USE")
                after["budget_pending"] = None
            else:
                target.update(resume_request=None, resume_receipt=None)
            authority[slot] = None
        else:
            _fail("OUTCOME_REQUIRED")
    after["source_recovery_pending"] = None
    sync_state._validate(after)
    return after


class LegacyPendingIntakeCapture17:
    """One self-derived stage or receipt consumption; no admission producer."""
    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    _read_control = origin_intake.LegacyOriginIntakeCapture17._read_control
    _require_cas = origin_intake.LegacyOriginIntakeCapture17._require_cas
    verify_for_commit = origin_intake.LegacyOriginIntakeCapture17.verify_for_commit
    adopt_state_effect = origin_intake.LegacyOriginIntakeCapture17.adopt_state_effect

    def _context(self):
        if (type(self) is not LegacyPendingIntakeCapture17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self):
            _fail("FACTORY_REQUIRED")
        self._read_control()
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        if (type(self._anchors) is not tuple or len(self._anchors) != 6
                or any(actual is not original for actual, original in zip((self.owner, self._schema,
                    self._home, self._selected_pins, self._pins, self._checkpoint), self._anchors))):
            _fail("FACTORY_REQUIRED")
        _same(self._schema.core, self._core)
        if (self.preservation_capture is not self._original_restore or self.resume_capture is not None
                or self._original_restore is not None and (
                    type(self._original_restore) is not runtime.CompletedRestoreCapture17
                    or self._original_restore._store is not self._store
                    or self._original_restore._profile.identity != self._profile.identity
                    or self._original_restore._owner.reference_frame is not self.owner.lock.reference_frame)):
            _fail("ORIGINAL_RESTORE_REQUIRED")
        _same(self._signature, tuple(witness._digest(item) for item in (self._core, self._origin,
            self._intent, self._receipt, self._mode, self._manual_rows, self._facts_signature,
            self._effect_before_json, self._effect_after_json, self._replay, self._before_json, self._after_json)))

    @property
    def core(self):
        self._context()
        return deepcopy(self._core)

    @property
    def request(self):
        self._context()
        return deepcopy(self._intent["request"])

    @property
    def result(self):
        self._context()
        return None if self._receipt is None else deepcopy(self._receipt["result"])

    @property
    def origin_ref(self):
        self._context()
        return permanent.record_ref(self._origin)

    def expected_state_effect(self):
        self._context()
        before, after = json.loads(self._before_json), json.loads(self._after_json)
        _same(json.loads(self._effect_after_json), _after(json.loads(self._effect_before_json), self._intent,
            self._receipt, self._origin, mode=self._mode))
        if self._replay:
            _same(before, after)
        else:
            _same(after, json.loads(self._effect_after_json))
        return before, after

    def pending_state_effect(self, guard):
        """Exact runtime branch must validate this complete effect after CAS."""
        self._require_cas(guard)
        if guard is not self._cas_guard or not guard._written:
            _fail("PENDING_STATE_EFFECT_REQUIRED")
        _same(self._store.read(), json.loads(self._after_json))
        return self.expected_state_effect()

    # The reused mechanical adoption algorithm calls this named hook, whose
    # body is this independent exact pending proof, never an origin-only DTO.
    origin_state_effect = pending_state_effect

    def _fixed(self, con, permit):
        origin_intake.LegacyOriginIntakeCapture17._fixed(self, con, permit)
        from . import sync_source_resume_basis2 as manual
        for identifier, row in self._manual_rows:
            _same(manual._row(con, self._core, identifier), row)

    def verify_source(self):
        origin_intake.LegacyOriginIntakeCapture17.verify_source(self)
        _verify_original_restore_prefix(self.preservation_capture, self._pins, self._profile,
            self._schema.rows, self._intent, self._core)
        observed = self.owner.observe_readonly()
        history._frame(observed)
        with sql.connection(self._profile) as con:
            records, manual, signature = _facts(con, observed["entries"], self._intent,
                self._receipt, self._origin, json.loads(self._effect_before_json))
        _same((records, manual, signature), (self._records, self._manual_rows, self._facts_signature))


@contextmanager
def capture_legacy_pending17_locked(*, profile, store, request_id, request_sha256,
        expected_intent_sha256=None, expected_record_sha256=None, mode="consume"):
    profile = _selectors(profile, store, request_id, request_sha256, expected_intent_sha256)
    if type(mode) is not str or mode not in {"stage", "consume"} or _CURRENT.get() is not None:
        _fail("FACTORY_REQUIRED")
    if expected_record_sha256 is not None:
        permanent._sha(expected_record_sha256)
    with store.reference_operation(), ExitStack() as stack:
        owner = legacy._owned(stack, store)
        state = witness._state(store)
        before = state["value"]
        observed = owner.observe_readonly()
        history._frame(observed)
        intent, origin = _original(observed, before, request_id, request_sha256, expected_intent_sha256)
        receipt = _receipt(observed, intent, required=mode == "consume", expected_record_sha256=expected_record_sha256)
        from . import sync_source_legacy_pending_state17 as capsules
        capsule = capsules.read_locked(profile=profile, request_id=request_id) if mode == "consume" else None
        applied = capsules.read_applied_locked(profile=profile, request_id=request_id) if mode == "consume" else None
        if applied is not None and capsule is None:
            _fail("ORIGINAL_CAPSULE_REQUIRED")
        effect_before, replay = before, False
        if capsule is not None:
            original_before, original_after = capsules.validate(capsule["value"], profile=profile,
                intent=intent, receipt=receipt, origin=origin)
            if applied is not None:
                capsules.validate_applied(applied["value"], capsule=capsule)
                if (before["source_recovery_pending"] == _pending(intent) or before["source_authority"] is None
                        or before["source_authority"]["origin"] != permanent.record_ref(origin)):
                    _fail("APPLIED_STATE_ROLLBACK")
                # An observed completed original operation is historical. It
                # may be read after heartbeat/status changes without writing
                # or replacing any of the current State axes.
                effect_before, replay = original_before, True
            elif before == original_after:
                _same(state["raw_sha256"], capsule["value"]["after_sha256"])
                effect_before, replay = original_before, True
            elif before != original_before or state["raw_sha256"] != capsule["value"]["before_raw_sha256"]:
                _fail("ORIGINAL_CAPSULE_CONFLICT")
        captured = object.__new__(LegacyPendingIntakeCapture17)
        captured.owner, captured._profile, captured._store = owner, profile, store
        captured._seal, captured._active, captured._owner_context = _SEAL, True, origin_intake._owner()
        captured._context_token, captured._mode = None, mode
        captured._replay = replay
        captured._state, captured._core, captured._origin = state, deepcopy(origin["core"]), deepcopy(origin)
        captured._intent, captured._receipt = deepcopy(intent), deepcopy(receipt)
        captured._schema = origin_intake._OriginSchema17(profile, store, captured._core, before, stack)
        captured._home = stack.enter_context(home_roots.capture_locked(profile, binding=before["binding"],
            format_version=2, limits=home_roots.CaptureLimits(deadline_seconds=120), check_cancelled=captured._read_control))
        if captured._home.unknown or captured._home.summary["presence"] != "present":
            _fail("COMPLETE_ORIGINAL_FACILITY_REQUIRED")
        with sql.connection(profile) as con:
            records, captured._manual_rows, captured._facts_signature = _facts(
                con, observed["entries"], intent, receipt, origin, effect_before)
        captured._records = deepcopy(records)
        folders = {"origin": "origins", "control": "controls", "recovery_intent": "requests", "recovery_receipt": "receipts"}
        captured._selected_pins = effects._SelectedPins(stack, profile, store, observed,
            [(folders[record["record_kind"]], record) for record in records])
        captured._pins = runtime._RuntimePins(stack, profile, captured._read_control)
        if capsule is not None:
            pinned = capsules.read_locked(profile=profile, request_id=request_id, pins=captured._pins)
            _same((pinned["value"], pinned["identity"]), (capsule["value"], capsule["identity"]))
        if applied is not None:
            pinned = capsules.read_applied_locked(profile=profile, request_id=request_id, pins=captured._pins)
            _same((pinned["value"], pinned["identity"]), (applied["value"], applied["identity"]))
        captured._checkpoint = witness._CheckpointCache(store, state, stack)
        rows = captured._schema.rows
        captured.preservation_capture = None
        if any(parsed.wrapper is not None for _, parsed in rows):
            if rows[-1][1].wrapper is None:
                _fail("MODERN_RESTORE_CHAIN_TERMINAL_REQUIRED")
            selected = rows[-1][0]
            captured.preservation_capture = stack.enter_context(runtime.capture_completed_locked(
                profile=profile, store=store, operation_id=selected["operation_id"],
                expected_result_sha256=selected["result_sha256"]))
            _same([row for row, _ in captured.preservation_capture._records], [row for row, _ in rows])
        for row, parsed in rows:
            if parsed.wrapper is None:
                runtime._legacy_row(captured._pins, row, profile)
        # Actual bare16 restore bytes are comparison history, never promoted
        # to a trusted17 preservation certificate by this pending bridge.
        _verify_original_restore_prefix(captured.preservation_capture, captured._pins, profile,
            rows, intent, captured._core)
        effect_after = _after(effect_before, intent, receipt, origin, mode=mode)
        after = before if replay else effect_after
        witness._resolve_state(after, captured._home)
        captured._before_json, captured._after_json = execution._canonical(before), execution._canonical(after)
        captured._effect_before_json = execution._canonical(effect_before)
        captured._effect_after_json = execution._canonical(effect_after)
        captured._signature = tuple(witness._digest(item) for item in (captured._core, captured._origin,
            captured._intent, captured._receipt, captured._mode, captured._manual_rows, captured._facts_signature,
            captured._effect_before_json, captured._effect_after_json, captured._replay, captured._before_json, captured._after_json))
        captured._anchors = (owner, captured._schema, captured._home, captured._selected_pins,
            captured._pins, captured._checkpoint)
        captured._cas_guard, captured._adopted = None, False
        captured._original_restore, captured.resume_capture = captured.preservation_capture, None
        token = _CURRENT.set(captured)
        captured._context_token = _CONTEXT.set(captured)
        try:
            captured.verify_source()
            yield captured
            captured.verify_source()
        finally:
            captured._active = False
            _CONTEXT.reset(captured._context_token)
            _CURRENT.reset(token)


def install_original_pending17_locked(*, profile, store, request_id, request_sha256, expected_intent_sha256=None):
    """Actual17 transitional producer for a genuine old intent/State gap."""
    with capture_legacy_pending17_locked(profile=profile, store=store, request_id=request_id,
            request_sha256=request_sha256, expected_intent_sha256=expected_intent_sha256, mode="stage") as captured:
        before, after = captured.expected_state_effect()
        if before != after:
            with effects._cas(captured.owner, captured._schema, captured._selected_pins,
                    before, after, control=captured) as guard:
                with guard.writer():
                    guard.replace()
        captured.verify_source()
        return deepcopy(after)


def reconcile_original_pending17_locked(*, profile, store, request_id, request_sha256,
        expected_intent_sha256=None, expected_record_sha256=None):
    with capture_legacy_pending17_locked(profile=profile, store=store, request_id=request_id,
            request_sha256=request_sha256, expected_intent_sha256=expected_intent_sha256,
            expected_record_sha256=expected_record_sha256) as captured:
        before, after = captured.expected_state_effect()
        if before != after:
            from . import sync_source_legacy_pending_state17 as capsules
            capsules.prepare_locked(captured)
            with effects._cas(captured.owner, captured._schema, captured._selected_pins,
                    before, after, control=captured) as guard:
                with guard.writer():
                    guard.replace()
        from . import sync_source_legacy_pending_state17 as capsules
        capsules.finish_locked(captured)
        captured.verify_source()
        return deepcopy(after)


class LegacyPendingHttpPublisher17:
    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    @property
    def record(self):
        self.check()
        return deepcopy(self._record)

    def check(self, record=None):
        if (type(self) is not LegacyPendingHttpPublisher17 or self._seal is not _SEAL or not self._active
                or _PUBLISHER.get() is not self or permanent._CURRENT.get() is not self.owner
                or self._owner_context != engine._owner() or sql.in_write_window()
                or self._ticket is not self._original_ticket or type(self._ticket) is not transport.HttpReceiptCapture17):
            _fail("FACTORY_REQUIRED")
        self.owner._check()
        self._ticket.check()
        _same(permanent.digest(self._record), self._signature)
        _same((self._ticket.request, self._ticket.result), (self._intent["request"], self._record["result"]))
        if record is not None:
            _same(record, self._record)
        observed = self.owner.observe_readonly()
        _same(history._committed(observed["entries"], "requests", self._intent["record_id"]), self._intent)
        _same(origin_intake._original(observed["entries"], self._origin["record_id"], self._origin["core"]), self._origin)


def require_legacy_pending_http17(owner, kind, record, producer, *, entries):
    if kind != "receipts" or type(producer) is not LegacyPendingHttpPublisher17 or producer.owner is not owner:
        _fail("HTTP_PRODUCER_REQUIRED")
    producer.check(record)
    _same(history._committed(entries, "requests", record["record_id"]), producer._intent)


def publish_original_pending_receipt17_locked(*, profile, store, client, request_id, request_sha256,
        expected_intent_sha256=None):
    """Original HTTP first save; committed replay performs no RPC or repair."""
    profile = _selectors(profile, store, request_id, request_sha256, expected_intent_sha256)
    with store.reference_operation(), ExitStack() as stack:
        owner = legacy._owned(stack, store)
        observed = owner.observe_readonly()
        intent, origin = _original(observed, store.read(), request_id, request_sha256, expected_intent_sha256)
        existing = observed["entries"]["receipts"].get(request_id)
        if existing is not None and (existing["state"] != "committed" or observed["head_status"] != "complete" or observed["incomplete"]):
            from .sync_source_legacy_pending_receipt17 import complete_original_pending_receipt17_locked
            return complete_original_pending_receipt17_locked(profile=profile, store=store,
                request_id=request_id, request_sha256=request_sha256,
                expected_record_sha256=permanent.digest(existing["record"]))
        history._frame(observed)
        state = witness._state(store)
        legacy._schema(profile, store, origin, stack)
        _verify_saved_restore_history(profile, store, owner, state["value"], intent, origin)
        if existing is not None:
            record = _receipt(observed, intent)
            from . import sync_source_legacy_pending_state17 as capsules
            capsule = capsules.read_locked(profile=profile, request_id=request_id)
            historical_state = state["value"]
            if capsule is not None:
                historical_state, _ = capsules.validate(capsule["value"], profile=profile,
                    intent=intent, receipt=record, origin=origin)
            with sql.connection(profile) as con:
                _facts(con, observed["entries"], intent, record, origin, historical_state)
            witness._verify_state_full(store, state)
            _same(history._frame(owner.observe_readonly()), history._frame(observed))
            return {"record": deepcopy(record), "ref": permanent.record_ref(record)}
        # Closing the original remote fact survives a later local pause.
        # The immutable original before-image is checked by _facts; exact
        # current-before/CAS equality belongs only to stage and consumption.
        if state["value"]["source_recovery_pending"] is not None:
            _same(state["value"]["source_recovery_pending"], _pending(intent))
        ticket = stack.enter_context(transport.capture_receipt(client,
            request_id=request_id, request_sha256=request_sha256))
        _same(ticket.request, intent["request"])
        _same(ticket.provenance["target_url_sha256"], hashlib.sha256(origin["binding"]["target_url"].encode()).hexdigest())
        records = []
        parent = (_parent(observed["entries"], state["value"], intent["request"]["original_request"], origin, records)
            if intent["request"]["kind"] == "source_dispose" and ticket.result["disposition"] == "applied" else None)
        if parent is not None:
            if ticket.defer_receipt is None:
                _fail("ORIGINAL_PARENT_HTTP_RECEIPT_REQUIRED")
            _same(parent["receipt"], {**ticket.defer_receipt, "replayed": False})
        record = {"format_version": 1, "record_kind": "recovery_receipt", "record_id": request_id,
            "request_sha256": request_sha256, "result": ticket.result,
            "result_sha256": permanent.digest(ticket.result), "parent_defer": parent}
        permanent.validate_record("receipts", record, observed["contract"])
        with sql.connection(profile) as con:
            _facts(con, observed["entries"], intent, record, origin, state["value"])
        witness._verify_state_full(store, state)
        producer = object.__new__(LegacyPendingHttpPublisher17)
        producer.owner, producer._ticket, producer._original_ticket = owner, ticket, ticket
        producer._intent, producer._origin, producer._record = deepcopy(intent), deepcopy(origin), deepcopy(record)
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


class LegacyOriginalHttpCall17:
    """Original intent selector for the engine's real legacy16 HTTP frame."""
    def __new__(cls, *args, **kwargs):
        _fail("FACTORY_REQUIRED")

    def check(self, client, endpoint, content, target_url):
        if (type(self) is not LegacyOriginalHttpCall17 or self._seal is not _SEAL or not self._active
                or _HTTP.get() is not self or self._owner != engine._owner()
                or client is not self._client or client._transport is not self._transport):
            _fail("ORIGINAL_HTTP_SELECTOR_REQUIRED")
        _same((endpoint, content, target_url, client._url),
            (self._endpoint, self._content, self._target_url, self._target_url))
        _same(self._signature, witness._digest((self._endpoint,
            hashlib.sha256(self._content).hexdigest(), self._target_url)))
        return True


@contextmanager
def original_legacy_http_call17(*, profile, store, client, request_id, request_sha256,
        expected_intent_sha256=None):
    """Exit SQL/State resources before HTTP; never create the engine frame."""
    from .sync_client import SyncClient
    if type(client) is not SyncClient or _HTTP.get() is not None:
        _fail("ORIGINAL_HTTP_SELECTOR_REQUIRED")
    if (sql.in_write_window() or permanent._CURRENT.get() is not None
            or store._directory is not None or store._reference_frame is not None
            or lifecycle.current_guard() is not None):
        _fail("HTTP_RESOURCES_MUST_BE_CLOSED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("ORIGINAL_HTTP_SELECTOR_REQUIRED")
    permanent._uuid(request_id)
    permanent._sha(request_sha256)
    if expected_intent_sha256 is not None:
        permanent._sha(expected_intent_sha256)
    with store.reference_operation(), ExitStack() as stack:
        owner = legacy._owned(stack, store)
        observed = owner.observe_readonly()
        history._frame(observed)
        state = store.read()
        intent, origin = _original(observed, state, request_id, request_sha256, expected_intent_sha256)
        if intent["request"]["source"]["handshake"]["migration_version"] != 16:
            _fail("ORIGINAL_SOURCE16_REQUIRED")
        _same(state["source_recovery_pending"], _pending(intent))
        _same(intent["expected_before"], effects._before(state, origin["core"]))
        legacy._schema(profile, store, origin, stack)
        _verify_saved_restore_history(profile, store, owner, state, intent, origin)
        body = engine._encoded(intent["request"]).encode("utf-8")
        url = origin["binding"]["target_url"]
    value = object.__new__(LegacyOriginalHttpCall17)
    value._client, value._transport, value._target_url = client, client._transport, url
    value._endpoint = {"source_dispose": "source/dispose", "source_readmit": "source/readmit"}[intent["request"]["kind"]]
    value._content, value._owner = body, engine._owner()
    value._signature = witness._digest((value._endpoint, hashlib.sha256(body).hexdigest(), url))
    value._seal, value._active = _SEAL, True
    token = _HTTP.set(value)
    try:
        value.check(client, value._endpoint, body, url)
        yield value
        value.check(client, value._endpoint, body, url)
    finally:
        value._active = False
        _HTTP.reset(token)


def require_original_legacy_http_call17(client, endpoint, content, *, target_url):
    value = _HTTP.get()
    if type(value) is not LegacyOriginalHttpCall17:
        _fail("ORIGINAL_HTTP_SELECTOR_REQUIRED")
    return value.check(client, endpoint, content, target_url)
