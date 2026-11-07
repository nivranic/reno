"""Actual SQL17/State/facility reads of controlled preexisting wire1 history.

Genesis and knowledge restore are real isolated producers. Historical v1
controls/readmit files below are controlled existing fixtures, not evidence
that a legacy remote producer or a paid-source dispatch was accepted. The
history reader itself receives no substituted capture, owner or constructor
seal. Reading these files must never create/refund/replay any paid operation.
"""
from contextvars import copy_context
from copy import deepcopy
import os
import uuid

import pytest

from reno import budget_schema, budget_writer_witness as sql
from reno import home_recovery_runtime17 as restore_runtime, migrations
from reno import sync_source_admission_history1 as history
from reno import sync_source_authority as permanent, sync_source_binding17 as binding17
from reno import sync_source_admission17 as current_admission, sync_source_readmit_contract as wire
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_restore_cycle17 import restored_origin17
from tests.test_sync_source_authority import projection_payload
from tests import test_sync_source_readmit_contract as sample


def _id():
    return str(uuid.uuid4())


def _ref(receipt):
    result = receipt["result"]
    return {"format_version": 1, "admission_id": receipt["record_id"],
        **{key: result[key] for key in ("core_sha256", "request_sha256", "admission_sha256")},
        "record_sha256": permanent.digest(receipt)}


def _current(value, kind):
    state = value.store.read()
    return {"kind": kind, "request_id": _id(), "source": deepcopy(value.source),
        "target": deepcopy(value.target), "lease": deepcopy(state["lease"]),
        "recovery_generation": state["recovery"]["generation"],
        "recovery_stream": deepcopy(state["recovery"]["stream"]),
        "control_revision": state["recovery"]["revision"]}


def _bound(value, kind):
    body = _current(value, kind)
    core, origin = value.core, value.origin
    body.update(original={"core_sha256": origin["core_sha256"], "home_epoch": core["local_epoch"],
        "item_id": core["item_id"], "submitted_url_sha256": core["submitted_url_sha256"],
        "claim": deepcopy(origin["claim"]), "recovery_generation": core["recovery_generation"],
        "recovery_stream": deepcopy(core["recovery_stream"])},
        current_home_epoch=value.store.read()["local_epoch"], preservation=deepcopy(value.preservation))
    return body


def _before(value, request, previous=None):
    state = value.store.read()
    return {"binding": deepcopy(state["binding"]),
        "local_epoch": request.get("current_home_epoch", state["local_epoch"]),
        "core_sha256": value.origin["core_sha256"], "admission_ref": None if previous is None else _ref(previous),
        "recovery_generation": request["recovery_generation"], "recovery_stream": deepcopy(request["recovery_stream"]),
        "control_revision": request["control_revision"],
        "claim": deepcopy(value.origin["claim"] if previous is None else previous["result"]["claim"]),
        "budget_pending_sha256": None, "source_control_pending_sha256": None,
        "pending_publication": None, "source_authority": deepcopy(state["source_authority"])}


def _intent(value, body, previous=None, *, resume_provenance=None):
    return {"format_version": 1, "record_kind": "recovery_intent", "record_id": body["request_id"],
        "core_sha256": value.origin["core_sha256"], "request_sha256": wire.request_sha256(body),
        "request": deepcopy(body), "expected_before": _before(value, body, previous),
        "preservation_basis": deepcopy(value.preservation_provenance),
        "manual_resume_basis": deepcopy(resume_provenance)}


def _receipt(intent, result, *, parent=None):
    return {"format_version": 1, "record_kind": "recovery_receipt", "record_id": intent["record_id"],
        "request_sha256": intent["request_sha256"], "result": deepcopy(result),
        "result_sha256": permanent.digest(result), "parent_defer": deepcopy(parent)}


def _control(value, kind="budget_defer", *, parent_id=None):
    body = _current(value, kind)
    body.pop("control_revision")
    claim = deepcopy(value.origin["claim"])
    if kind == "budget_resume":
        body["defer_request_id"] = parent_id
    else:
        body.update(claim=claim, reason="daily_limit", retry_at="2099-01-02T00:00:00.000Z",
            budget_operation_id=_id())
    basis = {"format_version": 1, "kind": "original_core", "core_sha256": value.origin["core_sha256"],
        "home_epoch": value.store.read()["local_epoch"], "session": deepcopy(body["lease"]), "claim": claim,
        "recovery_generation": body["recovery_generation"], "recovery_stream": deepcopy(body["recovery_stream"]),
        "control_revision": value.store.read()["recovery"]["revision"],
        "origin_ref": permanent.record_ref(value.origin), "admission_ref": None,
        # The real isolated Genesis used a17 handshake. Keep its actual
        # bounded continuity hint in this controlled existing wire1 record;
        # this dictionary is not a Cloud producer or a current permission.
        "continuity": deepcopy(value.remote.payloads["continuity"]["continuity"])}
    return {"format_version": 1, "record_kind": "control", "record_id": body["request_id"],
        "core_sha256": value.origin["core_sha256"], "request_sha256": wire.digest(body),
        "request": body, "creation_basis": basis}


def _negative(value, control):
    register = _current(value, "source_register")
    register.update(core_sha256=value.origin["core_sha256"], item_id=value.core["item_id"],
        original_request=deepcopy(control["request"]),
        continuity=deepcopy(control["creation_basis"]["continuity"]))
    # Register has no current_home_epoch; its local before still records the
    # same genuine State epoch, without changing its closed wire shape.
    registration_intent = _intent(value, register)
    answer = sample.registration(register)
    answer.update(core_sha256=value.origin["core_sha256"], item_id=value.core["item_id"])
    answer["registration_sha256"] = wire.registration_sha256(answer)
    registered = _receipt(registration_intent, answer)
    dispose = _bound(value, "source_dispose")
    dispose.update(original_request=deepcopy(control["request"]), registration=deepcopy(answer))
    intent = _intent(value, dispose)
    result = sample.disposition(dispose, applied=False)
    result.update(core_sha256=value.origin["core_sha256"], item_id=value.core["item_id"])
    return [("controls", control), ("requests", registration_intent), ("receipts", registered),
        ("requests", intent), ("receipts", _receipt(intent, result))], result


def _admit(value, *, previous=None, dispositions=(), defer=None, resume=None, manual=None):
    body = _bound(value, "source_readmit")
    body.update(expected_claim=deepcopy(value.origin["claim"] if previous is None else previous["result"]["claim"]),
        previous_admission_id=None if previous is None else previous["record_id"],
        dispositions=deepcopy(list(dispositions)), defer_receipt=deepcopy(defer),
        resume_receipt=deepcopy(resume), manual_resume_basis=deepcopy(manual))
    provenance = None if manual is None else {"format_version": 1, "producer": "sync_budget_resume",
        "operation_id": manual["authorization_id"], "receipt_sha256": manual["authorization_sha256"]}
    intent = _intent(value, body, previous, resume_provenance=provenance)
    result = sample.admission(body, attempts=3 if previous is None else previous["result"]["attempts"])
    return intent, _receipt(intent, result)


def _rehash(intent, receipt):
    intent["request_sha256"] = wire.request_sha256(intent["request"])
    receipt["request_sha256"] = intent["request_sha256"]
    receipt["result"]["request_sha256"] = intent["request_sha256"]
    if "admission_id" in receipt["result"]:
        receipt["result"]["disposition_sha256s"] = [wire.disposition_sha256(r)
            for r in intent["request"]["dispositions"]]
        receipt["result"]["admission_sha256"] = wire.admission_sha256(receipt["result"])
    receipt["result_sha256"] = permanent.digest(receipt["result"])


def _seed(value, records):
    """Write a controlled existing history; no operational producer is faked."""
    payload = projection_payload(value.activation, value.original_records + records)
    root = value.profile.database.parent
    ledger = root / permanent.DIRECTORY / permanent.VERSION_DIRECTORY
    journal = ledger / "journal"
    wanted = {f"{e['ordinal']:08d}.json" for e in payload["journal"]}
    for old in journal.glob("*.json"):
        if old.name not in wanted:
            old.unlink()
    for folder in permanent.KINDS:
        expected = payload["records"][folder]
        for old in (ledger / folder).glob("*.json"):
            if old.stem not in expected:
                old.unlink()
        for identifier, record in expected.items():
            path = ledger / folder / (identifier + ".json")
            path.write_bytes(permanent._encoded(record))
            if os.name != "nt":
                path.chmod(0o600)
    for event in payload["journal"]:
        path = journal / f"{event['ordinal']:08d}.json"
        path.write_bytes(permanent._encoded(event))
        if os.name != "nt":
            path.chmod(0o600)
    for path, head in ((root / permanent.DB_HEAD_NAME, payload["db_head"]),
            (value.profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME, payload["checkpoint_head"])):
        path.write_bytes(permanent._encoded(head))
        if os.name != "nt":
            path.chmod(0o600)


@pytest.fixture
def actual_history(restored_origin17):
    value = restored_origin17
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        observed = owner.observe_readonly()
        value.activation = deepcopy(observed["contract"])
        value.original_records = [(folder, deepcopy(item["record"]))
            for folder, entries in observed["entries"].items() for item in entries.values()]
        value.origin = owner.read(value.store.read()["source_authority"]["origin"])
    with value.store.reference_operation(), restore_runtime.capture_completed_locked(
            profile=value.profile, store=value.store, operation_id=value.restore_operation) as restored:
        value.preservation = restored.preservation_observation(from_epoch=value.core["local_epoch"])["preservation"]
        value.preservation_provenance = restored.provenance_ref()
    assert migrations.SCHEMA_VERSION == 16
    return value


def _capture(value, receipt, **extra):
    return history.capture_legacy_admission_history_locked(profile=value.profile, store=value.store,
        admission_id=receipt["record_id"], expected_record_sha256=permanent.digest(receipt), **extra)


def _unchanged(value):
    paths = [value.profile.database.parent / "sync-state.json"]
    paths.extend((value.profile.database.parent / permanent.DIRECTORY).rglob("*.json"))
    paths.extend((value.profile.database.parent / permanent.DB_HEAD_NAME,
        value.profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME))
    files = {str(p): p.read_bytes() for p in paths if p.is_file()}
    with sql.connection(value.profile) as con:
        paid = {table: sorted((tuple(row) for row in con.execute('SELECT * FROM "' + table + '"')), key=repr)
            for table in budget_schema.TABLES}
    return value.store.read(), files, paid


def test_real_negative_consumption_retains_claim_paid_ids_and_all_source_bytes(actual_history):
    value = actual_history
    control = _control(value)
    records, negative = _negative(value, control)
    intent, receipt = _admit(value, dispositions=(negative,))
    # An unrelated known old control is preserved but cannot be claimed as
    # consumed merely because its core matches the selected admission.
    unrelated = _control(value)
    _seed(value, records + [("controls", unrelated), ("requests", intent), ("receipts", receipt)])
    before = _unchanged(value)
    with _capture(value, receipt) as captured:
        assert captured.admission_ref() == _ref(receipt)
        assert captured.result["claim"] == receipt["result"]["claim"]
        assert captured.request == intent["request"]
        assert captured.core == value.core
        assert captured.consumed_control_refs == (permanent.record_ref(control),)
        assert captured.consumed_controls[0]["disposition"] == "not_applied"
        assert captured.consumed_controls[0]["control_request"]["budget_operation_id"] == control["request"]["budget_operation_id"]
        detached = captured.consumed_controls
        detached[0]["control_request"]["budget_operation_id"] = _id()
        changed = captured.result
        changed["claim"]["lease_token"] = _id()
        assert captured.result == receipt["result"]
        assert captured.consumed_controls[0]["control_request"] == control["request"]
        captured.verify_source()
    assert _unchanged(value) == before


def test_synthetic_equal_sha_pair_without_actual_manual_authorization_is_rejected(actual_history):
    value = actual_history
    defer = _control(value)
    resume = _control(value, "budget_resume", parent_id=defer["record_id"])
    deferred = sample.old_receipt(defer["request"], attempts=2)
    resumed = sample.old_receipt(resume["request"], attempts=2, parent=deferred)
    # Legacy applied outcomes legitimately have registration=None. Their
    # real saved request/receipt and original parent still have to match.
    body = _bound(value, "source_dispose")
    body.update(original_request=deepcopy(resume["request"]), registration=None)
    disposed_intent = _intent(value, body)
    outcome = sample.disposition(body, parent=deferred)
    outcome.update(core_sha256=value.origin["core_sha256"], item_id=value.core["item_id"], original_receipt=deepcopy(resumed))
    disposed = _receipt(disposed_intent, outcome, parent={"request": deepcopy(defer["request"]), "receipt": deepcopy(deferred)})
    manual = {"authorization_id": _id(), "authorization_sha256": "a" * 64,
        "defer_request_id": defer["record_id"]}
    intent, receipt = _admit(value, dispositions=(outcome,), defer=deferred, resume=resumed, manual=manual)
    records = [("controls", defer), ("controls", resume), ("requests", disposed_intent),
        ("receipts", disposed), ("requests", intent), ("receipts", receipt)]
    _seed(value, records)
    before = _unchanged(value)
    # A shaped original pair and an applied disposition do not manufacture
    # tasks.resume_sync's real four-column authorization. The distinct-hash
    # positive flow is covered by test_source_manual_resume_dual_sha17.
    with pytest.raises(SyncError) as caught:
        with _capture(value, receipt):
            pytest.fail("Synthetic equal hashes replaced the real manual authorization")
    assert caught.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
    causes = []
    cause = caught.value.__context__
    while cause is not None:
        causes.append(getattr(cause, "code", None))
        cause = cause.__context__
    assert any(code and code.endswith("MANUAL_RECEIPT_REQUIRED") for code in causes)
    assert _unchanged(value) == before
    disposed["parent_defer"]["receipt"]["attempts"] += 1
    _seed(value, records)
    with pytest.raises(SyncError):
        with _capture(value, receipt):
            pass


def test_complete_two_generation_ancestry_and_missing_parent_rejection(actual_history):
    value = actual_history
    first_intent, first = _admit(value)
    second_intent, second = _admit(value, previous=first)
    records = [("requests", first_intent), ("receipts", first), ("requests", second_intent), ("receipts", second)]
    _seed(value, records)
    with _capture(value, second) as captured:
        assert captured.ancestor_admission_refs == (_ref(first),)
        assert captured.admission_ref() == _ref(second)
        assert captured.request["expected_claim"] == first["result"]["claim"]
        assert captured.result["attempts"] == first["result"]["attempts"]
    _seed(value, records[2:])
    with pytest.raises(SyncError):
        with _capture(value, second):
            pass


@pytest.mark.parametrize("change", ("register_missing", "register_different", "selected_sha", "bound_core_body"))
def test_actual_registration_sha_and_original_body_cannot_be_replaced(actual_history, change):
    value = actual_history
    records, negative = _negative(value, _control(value))
    intent, receipt = _admit(value, dispositions=(negative,))
    records.extend((("requests", intent), ("receipts", receipt)))
    if change == "register_missing":
        records = [record for i, record in enumerate(records) if i not in (1, 2)]
    elif change == "register_different":
        records[2][1]["result"]["continuity_id"] = _id()
        records[2][1]["result"]["registration_sha256"] = wire.registration_sha256(records[2][1]["result"])
        records[2][1]["result_sha256"] = permanent.digest(records[2][1]["result"])
    elif change == "bound_core_body":
        intent["request"]["original"]["submitted_url_sha256"] = "f" * 64
        receipt["result"]["submitted_url_sha256"] = "f" * 64
        _rehash(intent, receipt)
    _seed(value, records)
    expected = "0" * 64 if change == "selected_sha" else permanent.digest(receipt)
    with pytest.raises(SyncError):
        with history.capture_legacy_admission_history_locked(profile=value.profile, store=value.store,
                admission_id=receipt["record_id"], expected_record_sha256=expected):
            pass


def test_context_lifetime_and_independent_capture_never_supply_writer_or_creation(actual_history):
    value = actual_history
    intent, receipt = _admit(value)
    _seed(value, [("requests", intent), ("receipts", receipt)])
    with _capture(value, receipt) as captured:
        with pytest.raises(SyncError):
            copy_context().run(lambda: captured.result)
        assert captured.result == receipt["result"]
        assert type(captured) is history.LegacyAdmissionHistoryCapture1
        assert not isinstance(captured, current_admission.CurrentAdmissionView17)
        for name in ("verify_for_commit", "_advance_creation", "creation_basis", "retirement_head"):
            assert not hasattr(captured, name)
        with pytest.raises(SyncError):
            binding17.require_binding(captured)
        with pytest.raises(SyncError):
            current_admission.require_view(captured)
        with pytest.raises(SyncError):
            with _capture(value, receipt):
                pass
    for getter in (lambda: captured.result, captured.admission_ref, captured.verify_source):
        with pytest.raises(SyncError):
            getter()
    with pytest.raises(SyncError):
        history.LegacyAdmissionHistoryCapture1(owner=None, profile=None, store=None, state=None, frame=None,
            chain=None, consumed=None, origin=None, records=None, observed=None, connection=None, stack=None, seal=object())


def test_writer_rejection_precedes_full_schema_state_and_permanent_reads(actual_history, monkeypatch):
    value = actual_history
    intent, receipt = _admit(value)
    _seed(value, [("requests", intent), ("receipts", receipt)])
    def forbidden(*args, **kwargs):
        pytest.fail("History attempted a full read inside the original writer")
    with _capture(value, receipt) as captured:
        with sql.connection(value.profile, write=True), monkeypatch.context() as scoped:
            scoped.setattr(history.schema17, "_actual_schema", forbidden)
            scoped.setattr(history.witness, "_verify_state_full", forbidden)
            scoped.setattr(captured._owner, "observe_readonly", forbidden)
            for call in (captured.verify_source, lambda: captured.result):
                with pytest.raises(SyncError, match="admission1") as rejected:
                    call()
                assert rejected.value.code == "SYNC_SOURCE_HISTORY1_READ_WINDOW_REQUIRED"
            with pytest.raises(SyncError) as rejected:
                with _capture(value, receipt):
                    pass
            assert rejected.value.code == "SYNC_SOURCE_HISTORY1_READ_WINDOW_REQUIRED"
        captured.verify_source()


def test_state_change_invalidates_original_capture(actual_history):
    value = actual_history
    intent, receipt = _admit(value)
    _seed(value, [("requests", intent), ("receipts", receipt)])
    with pytest.raises(SyncError):
        with _capture(value, receipt) as captured:
            status = value.store.read()["status"]
            status["error_code"] = "history_probe"
            value.store.update({"status": status})
            assert value.store.read()["status"]["error_code"] == "history_probe"
            captured.verify_source()


def test_valid_lagging_head_is_rejected_without_repair(actual_history):
    value = actual_history
    intent, receipt = _admit(value)
    records = [("requests", intent), ("receipts", receipt)]
    _seed(value, records)
    payload = projection_payload(value.activation, value.original_records + records)
    lagging = deepcopy(payload["db_head"])
    lagging["ordinal"] -= 1
    lagging["sha256"] = permanent.digest(payload["journal"][-2])
    path = value.profile.database.parent / permanent.DB_HEAD_NAME
    path.write_bytes(permanent._encoded(lagging))
    before = path.read_bytes()
    with pytest.raises(SyncError) as rejected:
        with _capture(value, receipt):
            pass
    assert rejected.value.code == "SYNC_SOURCE_AUTHORITY_INVALID"
    cause, seen, codes = rejected.value.__context__, set(), []
    while cause is not None and id(cause) not in seen:
        seen.add(id(cause))
        if isinstance(cause, SyncError):
            codes.append(cause.code)
        cause = cause.__context__
    assert "SYNC_SOURCE_HISTORY1_COMPLETE_FACILITY_REQUIRED" in codes
    assert path.read_bytes() == before


def test_private_derived_mutation_cannot_change_facts_or_select_another_real_record(actual_history):
    value = actual_history
    records, negative = _negative(value, _control(value))
    first_intent, first = _admit(value, dispositions=(negative,))
    second_intent, second = _admit(value, previous=first)
    records.extend((("requests", first_intent), ("receipts", first),
        ("requests", second_intent), ("receipts", second)))
    _seed(value, records)
    before = _unchanged(value)
    with _capture(value, first) as captured:
        original_chain = deepcopy(captured._chain)
        captured._chain[-1][1]["result"]["attempts"] += 1
        for call in (lambda: captured.result, captured.verify_source):
            with pytest.raises(SyncError) as rejected:
                call()
            assert rejected.value.code == "SYNC_SOURCE_HISTORY1_FACT_CHANGED"
        captured._chain = deepcopy(original_chain)
        original_consumed = deepcopy(captured._consumed)
        captured._consumed[0]["control_request"]["budget_operation_id"] = _id()
        with pytest.raises(SyncError) as rejected:
            _ = captured.consumed_controls
        assert rejected.value.code == "SYNC_SOURCE_HISTORY1_FACT_CHANGED"
        captured._consumed = deepcopy(original_consumed)
        original_origin = deepcopy(captured._origin)
        captured._origin["core"]["item_id"] += 1
        with pytest.raises(SyncError) as rejected:
            _ = captured.origin_ref
        assert rejected.value.code == "SYNC_SOURCE_HISTORY1_FACT_CHANGED"
        captured._origin = deepcopy(original_origin)
        # The second admission really exists in the same held actual frame.
        # Replacing a derived leaf cannot change the original selector.
        captured._chain.append((deepcopy(second_intent), deepcopy(second)))
        with pytest.raises(SyncError) as rejected:
            captured.admission_ref()
        assert rejected.value.code == "SYNC_SOURCE_HISTORY1_FACT_CHANGED"
        captured._chain = deepcopy(original_chain)
        original_selector = captured._selector
        captured._selector = (second["record_id"], permanent.digest(second))
        with pytest.raises(SyncError) as rejected:
            captured.verify_source()
        assert rejected.value.code == "SYNC_SOURCE_HISTORY1_FACT_CHANGED"
        captured._selector = original_selector
        detached = captured.result
        detached["attempts"] += 1
        assert captured.result == first["result"]
        captured.verify_source()
    assert _unchanged(value) == before
