"""Locate consumed admission1 history from the actual current parent chain.

The pure projection classifies immutable records. The selector-only reader
owns its actual State and facility scope; it must close before any append or
State effect. Neither interface grants creation, retirement or paid authority.
"""
from contextlib import ExitStack, contextmanager, nullcontext
from copy import deepcopy

from . import budget_writer_witness as sql, storage_lifecycle as lifecycle
from . import sync_source_admission_history1 as legacy
from . import sync_source_authority as permanent, sync_source_readmit_contract as wire, sync_state
from .sync_protocol import SyncError


def _fail(code):
    raise SyncError("Original legacy admission ancestry could not be verified",
                    "SYNC_SOURCE_LEGACY_ANCESTRY2_" + code, 409)


def _same(actual, expected):
    if actual != expected:
        _fail("REFERENCE_MISMATCH")


def _committed(entries, folder, identifier):
    item = entries[folder].get(identifier)
    if item is None or item["state"] != "committed":
        _fail("REFERENCE_REQUIRED")
    record = item["record"]
    _same(record["record_id"], identifier)
    return record


def legacy_prefix2(entries, state):
    """Return the first legacy ref on this exact chain, with no capability."""
    from . import sync_source_readmit_local2 as local
    core, authority, prior = state["source_execution"], state["source_authority"], state["source_admission"]
    if core is None or authority is None:
        _fail("ORIGIN_REQUIRED")
    origin = permanent._resolve_record(entries, authority["origin"])
    _same((origin["core"], origin["core_sha256"]), (core, permanent.digest(core)))
    _same(authority["core_sha256"], origin["core_sha256"])
    if prior is None:
        return None
    receipt = _committed(entries, "receipts", prior["admission_id"])
    _same(local.admission_ref(receipt), prior)
    seen = set()
    while True:
        identifier = receipt["record_id"]
        if identifier in seen or len(seen) >= permanent.MAX_RECORDS:
            _fail("ADMISSION_CYCLE")
        seen.add(identifier)
        intent = _committed(entries, "requests", identifier)
        request, result = intent["request"], receipt["result"]
        if request["kind"] != "source_readmit":
            _fail("ADMISSION_REQUIRED")
        _same(wire.normalize_readmit_request(request), request)
        _same(wire.normalize_readmit_result(request, result), result)
        legacy._original(request, origin)
        _same((intent["request_sha256"], receipt["request_sha256"], receipt["result_sha256"]),
              (wire.request_sha256(request), wire.request_sha256(request), permanent.digest(result)))
        _same((intent["core_sha256"], result["core_sha256"], result["admission_id"]),
              (origin["core_sha256"], origin["core_sha256"], identifier))
        if receipt["format_version"] == 2:
            _same(receipt["core_sha256"], origin["core_sha256"])
        if not core["local_epoch"] <= result["current_home_epoch"] <= state["local_epoch"]:
            _fail("HISTORY_EPOCH_MISMATCH")
        if receipt["format_version"] == 1:
            if intent["format_version"] != 1 or request.get("format_version", 1) != 1:
                _fail("VERSION_MISMATCH")
            return legacy._admission_ref(receipt)
        if receipt["format_version"] != 2 or intent["format_version"] != 2 or request["format_version"] != 2:
            _fail("VERSION_MISMATCH")
        _same(receipt["request_ref"], permanent.record_ref(intent))
        local._bound_result(intent, receipt)
        _same((intent["expected_before"]["binding"], intent["expected_before"]["core_sha256"],
               intent["creation_basis"]["core_sha256"]),
              (origin["binding"], origin["core_sha256"], origin["core_sha256"]))
        before, basis = intent["expected_before"], intent["creation_basis"]
        _same((before["local_epoch"], basis["home_epoch"]),
              (request["current_home_epoch"], request["current_home_epoch"]))
        if before["source_authority"] is None:
            _fail("ORIGIN_REQUIRED")
        _same((before["source_authority"]["core_sha256"], before["source_authority"]["origin"]),
              (origin["core_sha256"], authority["origin"]))
        previous = result["previous_admission_id"]
        parent = None if previous is None else _committed(entries, "receipts", previous)
        parent_ref = None if parent is None else local.admission_ref(parent)
        parent_claim = origin["claim"] if parent is None else parent["result"]["claim"]
        _same((request["expected_claim"], intent["creation_basis"]["claim"], intent["expected_before"]["claim"]),
              (parent_claim, parent_claim, parent_claim))
        _same((intent["creation_basis"]["origin_ref"], intent["creation_basis"]["admission_ref"],
               intent["expected_before"]["admission_ref"]),
              (authority["origin"], parent_ref, parent_ref))
        if parent is None:
            return None
        if result["current_home_epoch"] < parent["result"]["current_home_epoch"]:
            _fail("HISTORY_EPOCH_MISMATCH")
        if result["attempt_basis"] == "held_claim":
            _same(result["attempts"], parent["result"]["attempts"])
        receipt = parent


def current_consumed_control_ids2(entries, state):
    """Control selectors consumed by the exact current wire2 parent prefix."""
    from . import sync_source_readmit_local2 as local
    identifier = None if state["source_admission"] is None else state["source_admission"]["admission_id"]
    consumed, seen = set(), set()
    while identifier is not None:
        if identifier in seen or len(seen) >= permanent.MAX_RECORDS:
            _fail("ADMISSION_CYCLE")
        seen.add(identifier)
        receipt = _committed(entries, "receipts", identifier)
        if receipt["format_version"] == 1:
            break
        intent = _committed(entries, "requests", identifier)
        request = intent["request"]
        _same(receipt["request_ref"], permanent.record_ref(intent))
        _same(wire.normalize_readmit_request(request), request)
        _same(wire.normalize_readmit_result(request, receipt["result"]), receipt["result"])
        for value in request["dispositions"]:
            stored = _committed(entries, "receipts", value["request_id"])
            _same(stored["result"], value)
            control = _committed(entries, "controls", value["original_request_id"])
            _same((control["core_sha256"], control["request_sha256"]),
                  (value["core_sha256"], value["original_request_sha256"]))
            positives, negatives = local._control_outcomes2(entries, control)
            if not any(negative["result"] == value for negative in negatives) and not any(
                    disposition == value for _receipt, _fact, disposition in positives):
                _fail("OUTCOME_MISMATCH")
            consumed.add(value["original_request_id"])
        for name, kind in (("defer_receipt", "budget_defer"), ("resume_receipt", "budget_resume")):
            value = request[name]
            if value is None:
                continue
            control = _committed(entries, "controls", value["request_id"])
            _same((control["request"]["kind"], control["core_sha256"], control["request_sha256"]),
                  (kind, receipt["result"]["core_sha256"], value["request_sha256"]))
            positives, negatives = local._control_outcomes2(entries, control)
            if negatives or not any(fact == value for _receipt, fact, _disposition in positives):
                _fail("OUTCOME_MISMATCH")
            consumed.add(value["request_id"])
        identifier = receipt["result"]["previous_admission_id"]
    return consumed


def legacy_consumption2(entries, state):
    """Exact consumed refs for this parent prefix; no caller exclusion list."""
    selector = legacy_prefix2(entries, state)
    if selector is None:
        return {"selector_ref": None, "admission_refs": (), "control_refs": (), "consumed_controls": ()}
    origin = permanent._resolve_record(entries, state["source_authority"]["origin"])
    chain, consumed, _records = legacy._facts(entries, origin, selector["admission_id"])
    _same(legacy._admission_ref(chain[-1][1]), selector)
    if current_consumed_control_ids2(entries, state) & {fact["control_ref"]["record_id"] for fact in consumed}:
        _fail("CONTROL_ALREADY_CONSUMED")
    return {"selector_ref": deepcopy(selector),
            "admission_refs": tuple(legacy._admission_ref(receipt) for _intent, receipt in chain),
            "control_refs": tuple(deepcopy(fact["control_ref"]) for fact in consumed),
            "consumed_controls": deepcopy(consumed)}


@contextmanager
def capture_legacy_ancestry_locked(*, profile, store):
    """Select from actual current State; close the history before writing."""
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    with ExitStack() as stack:
        stack.enter_context(store.reference_operation())
        existing = permanent._CURRENT.get()
        if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed, state = owner.observe_readonly(), store.read()
        from . import sync_source_readmit_local2 as local
        local.record_relations2(observed["entries"])
        frame = legacy._frame(observed)
        facts = legacy_consumption2(observed["entries"], state)
        selector = facts["selector_ref"]
        if selector is None:
            yield None
            _same(legacy._frame(owner.observe_readonly()), frame)
            _same(store.read(), state)
            return
        captured = stack.enter_context(legacy.capture_legacy_admission_history_locked(profile=profile, store=store,
            admission_id=selector["admission_id"], expected_record_sha256=selector["record_sha256"]))
        _same((captured.admission_ref(), captured.origin_ref, captured.core, captured.consumed_control_refs),
              (selector, state["source_authority"]["origin"], state["source_execution"], facts["control_refs"]))
        _same(legacy._frame(owner.observe_readonly()), frame)
        _same(store.read(), state)
        captured.verify_source()
        yield captured
        captured.verify_source()
