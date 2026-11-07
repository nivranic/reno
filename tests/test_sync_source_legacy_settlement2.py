"""Controlled existing wire1 history into the real Home readmit2 flow.

The prior wire1/mixed wire2 documents and State selector are explicitly
installed existing reader baselines. They do not prove an old Cloud producer,
first old Home receipt-first accounting, or a complete schema16 migration.
New intent/HTTP receipt/State effects use the original Home factories and the
isolated peer's real SyncClient parser; no production permission gate is
replaced. Pure State copies confer no authority. Nothing calls a paid source.
"""
from contextvars import copy_context
from copy import deepcopy
import hashlib
import os

import pytest

from reno import sync_source_admission17 as admissions, sync_source_admission_history1 as history
from reno import sync_source_authority as permanent, sync_source_binding17 as bindings
from reno import sync_source_lifecycle17 as effects, sync_source_opportunity17 as opportunity
from reno import sync_source_readmit_local2 as local
from reno import sync_state
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract as sample
from tests.test_sync_source_admission_history1 import (
    actual_history, env, restored_origin17,
    _admit, _control, _id, _negative, _ref, _unchanged,
)
from tests.test_sync_source_authority import projection_payload
from tests.test_sync_source_legacy_ancestry2 import (
    _closed_wire, _entries, _install_existing_current, _pair2, _rehash2, _state_for,
)
from tests.test_sync_source_claim import observe
from tests.test_sync_source_control_scheduler2 import accept_readmit, assert_current_after_readmit
from tests.test_sync_source_restore_cycle17 import actual_call


def _seed_preserving_prefix(value, records):
    """Append controlled existing reader documents without rewriting history.

    The genuine restore owns original physical file identities as well as
    bytes. This fixture first proves the actual complete journal is an exact
    prefix of the requested baseline; it cannot replace a fork or reset an
    original file's times. It supplies neither an old producer nor permission.
    """
    root = value.profile.database.parent
    ledger = root / permanent.DIRECTORY / permanent.VERSION_DIRECTORY
    payload = projection_payload(value.activation, value.original_records + records)
    desired = _entries(value, records)
    for folder, entries in desired.items():
        for item in entries.values():
            record = item["record"]
            assert permanent.validate_record(folder, record, value.activation) == record
    wanted = {ledger / folder / (identifier + ".json"): permanent._encoded(record)
        for folder, values in payload["records"].items() for identifier, record in values.items()}
    wanted.update({ledger / "journal" / f"{event['ordinal']:08d}.json": permanent._encoded(event)
        for event in payload["journal"]})
    def original(path):
        raw, info = path.read_bytes(), path.stat()
        assert info.st_nlink == 1
        return ((info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns),
                hashlib.sha256(raw).hexdigest(), raw)
    with value.store.reference_operation():
        before_state = value.store.read()
        with permanent.owned(value.store) as owner:
            actual = owner.observe_readonly()
            observed = {name: deepcopy(actual[name]) for name in (
                "contract", "entries", "head_status", "incomplete", "journal_head", "actual_heads")}
        assert observed["contract"] == value.activation
        assert observed["head_status"] == "complete" and observed["incomplete"] == []
        old_count = observed["journal_head"]["ordinal"]
        assert observed["actual_heads"] == {"db": observed["journal_head"], "checkpoint": observed["journal_head"]}
        assert old_count <= len(payload["journal"])
        journal = ledger / "journal"
        actual_journal = sorted(journal.glob("*.json"))
        assert [path.name for path in actual_journal] == [f"{ordinal:08d}.json" for ordinal in range(1, old_count + 1)]
        for path, event in zip(actual_journal, payload["journal"][:old_count]):
            assert path.read_bytes() == permanent._encoded(event)
        if old_count:
            assert observed["journal_head"]["sha256"] == permanent.digest(payload["journal"][old_count - 1])
        else:
            assert observed["journal_head"]["sha256"] == permanent.digest(value.activation)
        immutable = [root / permanent.INTENT_NAME, ledger / "contract.json",
                     value.profile.checkpoints / permanent.SEAL_NAME, *actual_journal]
        for path in immutable[:3]:
            assert path.read_bytes() == permanent._encoded(value.activation)
        for folder, entries in observed["entries"].items():
            paths = sorted((ledger / folder).glob("*.json"))
            assert {path.stem for path in paths} == set(entries)
            for path in paths:
                item = entries[path.stem]
                assert item["state"] == "committed"
                assert desired[folder][path.stem] == item
                assert path.read_bytes() == wanted[path] == permanent._encoded(item["record"])
            immutable.extend(paths)
        original_files = {path: original(path) for path in immutable}
        # Preflight every existing target before adding any new fixture file.
        for path, raw in wanted.items():
            if path.exists():
                assert path.read_bytes() == raw
        for path, raw in wanted.items():
            if not path.exists():
                with path.open("xb") as stream:
                    stream.write(raw)
                if os.name != "nt":
                    path.chmod(0o600)
        for path, head in ((root / permanent.DB_HEAD_NAME, payload["db_head"]),
                (value.profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME, payload["checkpoint_head"])):
            raw = permanent._encoded(head)
            if path.read_bytes() != raw:
                path.write_bytes(raw)
        assert {path: original(path) for path in immutable} == original_files
        with permanent.owned(value.store) as owner:
            current = owner.observe_readonly()
            assert current["head_status"] == "complete" and current["incomplete"] == []
            assert current["entries"] == desired
            assert current["actual_heads"] == {"db": payload["db_head"], "checkpoint": payload["checkpoint_head"]}
            assert current["journal_head"] == payload["db_head"]
        assert {path: original(path) for path in immutable} == original_files
        assert value.store.read() == before_state


def _consumed(value, kind):
    if kind == "negative":
        control = _control(value)
        records, negative = _negative(value, control)
        intent, receipt = _admit(value, dispositions=(negative,))
        controls = (control,)
    else:
        defer = _control(value)
        resume = _control(value, "budget_resume", parent_id=defer["record_id"])
        deferred = sample.old_receipt(defer["request"], attempts=2)
        resumed = sample.old_receipt(resume["request"], attempts=2, parent=defer["request"]["claim"])
        manual = {"authorization_id": _id(), "authorization_sha256": "d" * 64,
                  "defer_request_id": defer["record_id"]}
        intent, receipt = _admit(value, defer=deferred, resume=resumed, manual=manual)
        records = [("controls", defer), ("controls", resume)]
        controls = (defer, resume)
    records.extend((("requests", intent), ("receipts", receipt)))
    return records, receipt, controls


def _existing(value, kind="negative", *, mixed=False):
    """Install a controlled existing baseline before testing real consumers."""
    records, old, controls = _consumed(value, kind)
    current = old
    if mixed:
        intent2, current = _pair2(value, old)
        records.extend((("requests", intent2), ("receipts", current)))
    _seed_preserving_prefix(value, records)
    _install_existing_current(value, current)
    state = value.store.read()
    # The fixture's default Genesis/restore path creates no active controls;
    # installing this existing admission baseline does not clear any wait.
    assert all(state["source_authority"][name] is None for name in ("defer", "resume", "failure", "publication"))
    assert state["budget_pending"] is state["source_control_pending"] is None
    return old, current, controls


def _only_history(selection, old, controls):
    refs = [permanent.record_ref(record) for record in controls]
    assert selection["history_only_control_refs"] == refs
    assert selection["legacy_admission_refs"] == [_ref(old)]
    assert selection["current_control_refs"] == selection["retired_control_refs"] == []
    assert selection["graphs"] == selection["dispositions"] == []
    assert selection["defer_fact"] is selection["resume_fact"] is None


@pytest.mark.parametrize("kind", ("negative", "budget_pair"))
@pytest.mark.parametrize("mixed", (False, True))
def test_pure_settlement_excludes_only_exact_consumed_legacy_controls(actual_history, kind, mixed):
    value = actual_history
    records, old, controls = _consumed(value, kind)
    current = old
    if mixed:
        intent, current = _pair2(value, old)
        records.extend((("requests", intent), ("receipts", current)))
    entries, state = _entries(value, records), _state_for(value, current)
    before = deepcopy((entries, state))
    _only_history(local.current_settlement2(entries, state), old, controls)
    assert (entries, state) == before


@pytest.mark.parametrize("mixed", (False, True))
def test_unknown_committed_legacy_control_is_not_excluded_with_consumed_history(actual_history, mixed):
    value = actual_history
    records, old, _controls = _consumed(value, "negative")
    current = old
    if mixed:
        intent, current = _pair2(value, old)
        records.extend((("requests", intent), ("receipts", current)))
    unknown = _control(value)
    records.append(("controls", unknown))
    entries, state = _entries(value, records), _state_for(value, current)
    before = deepcopy((entries, state))
    with pytest.raises(SyncError):
        local.current_settlement2(entries, state)
    assert (entries, state) == before


@pytest.mark.parametrize("mixed", (False, True))
def test_real_settlement_capture_closes_legacy_reader_and_keeps_its_own_original_pins(actual_history, mixed):
    value = actual_history
    old, _current, controls = _existing(value, mixed=mixed)
    before = _unchanged(value)
    with value.store.reference_operation(), local.capture_current_settlement_locked(
            profile=value.profile, store=value.store) as captured:
        assert history._CURRENT.get() is None
        assert captured.history_only_control_refs == tuple(permanent.record_ref(record) for record in controls)
        assert captured.legacy_admission_refs == (_ref(old),)
        assert captured.dispositions == ()
        assert captured.defer_fact is captured.resume_fact is None
        detached = captured.history_only_control_refs
        detached[0]["record_sha256"] = "f" * 64
        assert captured.history_only_control_refs == tuple(permanent.record_ref(record) for record in controls)
        with pytest.raises(SyncError):
            copy_context().run(lambda: captured.history_only_control_refs)
        captured.verify_source()
        assert history._CURRENT.get() is None
        with pytest.raises(SyncError):
            admissions.require_view(captured)
    for getter in (lambda: captured.history_only_control_refs, lambda: captured.legacy_admission_refs,
                   captured.verify_source):
        with pytest.raises(SyncError):
            getter()
    for extra in ({"history_only_control_refs": []}, {"legacy_admission_refs": []}, {"state": before[0]}):
        with pytest.raises(TypeError):
            with local.capture_current_settlement_locked(profile=value.profile, store=value.store, **extra):
                pass
    assert _unchanged(value) == before


def test_real_settlement_reader_rejects_an_unconsumed_legacy_control_without_effects(actual_history):
    value = actual_history
    records, old, _controls = _consumed(value, "negative")
    records.append(("controls", _control(value)))
    _seed_preserving_prefix(value, records)
    _install_existing_current(value, old)
    before = _unchanged(value)
    with pytest.raises(SyncError):
        with value.store.reference_operation(), local.capture_current_settlement_locked(
                profile=value.profile, store=value.store):
            pass
    assert _unchanged(value) == before


def test_settlement_own_closure_detects_a_new_unregistered_file_after_legacy_reader_closed(actual_history):
    value = actual_history
    _existing(value)
    before = _unchanged(value)
    with value.store.reference_operation(), local.capture_current_settlement_locked(
            profile=value.profile, store=value.store) as captured:
        assert history._CURRENT.get() is None
        unknown = _control(value)
        path = value.profile.database.parent / permanent.DIRECTORY / permanent.VERSION_DIRECTORY / "controls" / (unknown["record_id"] + ".json")
        path.write_bytes(permanent._encoded(unknown))
        if os.name != "nt":
            path.chmod(0o600)
        try:
            with pytest.raises(SyncError):
                captured.verify_source()
        finally:
            path.unlink()
    assert _unchanged(value) == before


@pytest.mark.parametrize("kind", ("negative", "budget_pair"))
def test_actual_prepared_branch_does_not_reinterpret_consumed_legacy_as_control2_retirement(actual_history, kind):
    value = actual_history
    _existing(value, kind)
    before = _unchanged(value)
    with value.store.reference_operation(), bindings._capture_prepared_locked(
            profile=value.profile, store=value.store, item_id=value.core["item_id"]) as prepared:
        assert admissions._permanent_branch(prepared, value.store.read()) is False
        prepared.full()
    assert _unchanged(value) == before


def test_same_epoch_wire1_requires_real_readmit_opportunity_and_never_current_paid_view(actual_history):
    value = actual_history
    _old, current, _controls = _existing(value)
    state = value.store.read()
    assert current["result"]["current_home_epoch"] == state["local_epoch"]
    # Isolated peer response data still travels through the original HTTP
    # frame/parser. No observation, prepared capture or admission seal is set.
    value.remote.payloads["pull"]["claims"] = deepcopy(state["claims"])
    observe(value.remote)
    before = _unchanged(value)
    calls = deepcopy(value.remote.seen)
    with pytest.raises(opportunity.ReadmissionRequired17) as caught:
        with value.store.reference_operation(), admissions.capture_current_admission_locked(
                profile=value.profile, store=value.store, client=value.client,
                item_id=value.core["item_id"]):
            pytest.fail("An existing wire1 admission cannot supply current paid permission")
    assert caught.value.action == "readmit"
    assert history._CURRENT.get() is None
    assert _unchanged(value) == before
    assert value.remote.seen == calls


@pytest.mark.parametrize("kind", ("negative", "budget_pair"))
def test_real_new_intent_and_receipt_first_cas_do_not_resubmit_or_refund_legacy_consumption(actual_history, kind):
    value = actual_history
    _old, previous, controls = _existing(value, kind)
    before_state, before_files, before_paid = _unchanged(value)
    before_calls = deepcopy(value.remote.seen)
    intent = actual_call(effects.create_current_readmit17_locked,
        profile=value.profile, store=value.store, client=value.client)
    assert intent["record"]["format_version"] == 2
    body = intent["record"]["request"]
    assert body["previous_admission_id"] == previous["record_id"]
    assert body["expected_claim"] == previous["result"]["claim"]
    assert body["expected_claim"] != value.origin["claim"]
    assert body["dispositions"] == []
    assert body["defer_receipt"] is body["resume_receipt"] is body["resume_basis"] is None
    assert intent["record"]["creation_basis"]["admission_ref"] == _ref(previous)
    assert value.store.read() == before_state
    assert value.remote.seen == before_calls
    assert history._CURRENT.get() is None
    answer = accept_readmit(value, intent, attempts=previous["result"]["attempts"])
    assert answer["attempt_basis"] == "held_claim"
    assert answer["attempts"] == previous["result"]["attempts"]
    assert answer["previous_admission_id"] == previous["record_id"]
    assert value.store.read()["source_admission"]["admission_id"] == body["request_id"]
    assert value.store.read()["source_recovery_pending"] is None
    after_state, after_files, after_paid = _unchanged(value)
    assert after_paid == before_paid
    mutable = {str(value.profile.database.parent / "sync-state.json"),
        str(value.profile.database.parent / permanent.DB_HEAD_NAME),
        str(value.profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME)}
    for path, raw in before_files.items():
        if path not in mutable:
            assert after_files[path] == raw
    assert after_state["source_execution"] == before_state["source_execution"]
    assert after_state["source_authority"] == before_state["source_authority"]
    for control in controls:
        path = value.profile.database.parent / permanent.DIRECTORY / permanent.VERSION_DIRECTORY / "controls" / (control["record_id"] + ".json")
        assert after_files[str(path)] == permanent._encoded(control)
    # The newly published actual receipt2/CAS may now supply a current view;
    # the controlled old baseline itself never did so.
    assert_current_after_readmit(value, answer)


@pytest.mark.parametrize("kind", ("negative", "budget_pair"))
@pytest.mark.parametrize("mixed", (False, True))
def test_consumed_original_control_cannot_create_or_reuse_a_dispose_intent(actual_history, kind, mixed):
    value = actual_history
    _old, _current, controls = _existing(value, kind, mixed=mixed)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        entries = owner.observe_readonly()["entries"]
        existing = [item["record"] for item in entries["requests"].values()
            if item["record"]["request"]["kind"] == "source_dispose"]
    assert len(existing) == (1 if kind == "negative" else 0)
    before, calls = _unchanged(value), deepcopy(value.remote.seen)
    for control in controls:
        request_ids = [_id()]
        if existing:
            request_ids.append(existing[0]["record_id"])
        for request_id in request_ids:
            with value.store.reference_operation(), pytest.raises(SyncError) as caught:
                actual_call(local.create_dispose_intent_locked,
                    profile=value.profile, store=value.store, client=value.client,
                    control_request_id=control["record_id"], request_id=request_id)
            assert caught.value.code.endswith("_CONTROL_ALREADY_CONSUMED")
            assert _unchanged(value) == before
            assert value.remote.seen == calls
        # The public cached-intent branch must reject before returning the
        # old negative's existing dispose request, or choosing a new UUID.
        with pytest.raises(SyncError) as caught:
            actual_call(effects.create_current_dispose17_locked,
                profile=value.profile, store=value.store, client=value.client,
                control_request_id=control["record_id"])
        assert caught.value.code.endswith("_CONTROL_ALREADY_CONSUMED")
        assert _unchanged(value) == before
        assert value.remote.seen == calls


@pytest.mark.parametrize("kind", ("negative", "budget_pair"))
@pytest.mark.parametrize("mixed", (False, True))
def test_consumed_control_cannot_reuse_registration_or_stale_authority_slot(actual_history, kind, mixed):
    value = actual_history
    _old, _current, controls = _existing(value, kind, mixed=mixed)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        entries = owner.observe_readonly()["entries"]
        existing = [item["record"] for item in entries["requests"].values()
            if item["record"]["request"]["kind"] == "source_register"]
    assert len(existing) == (1 if kind == "negative" else 0)
    if kind == "budget_pair":
        # Explicitly install an isolated existing stale State baseline. This
        # is not an operational CAS or authority-slot producer. Both original
        # slots still name exact consumed controls, never fresh permission.
        state = deepcopy(value.store.read())
        for control in controls:
            slot = {"budget_defer": "defer", "budget_resume": "resume"}[control["request"]["kind"]]
            state["source_authority"][slot] = permanent.record_ref(control)
        with value.store.reference_operation():
            (value.store.data_dir / "sync-state.json").write_bytes(sync_state._validate(state))
        assert value.store.read() == state
    before, calls = _unchanged(value), deepcopy(value.remote.seen)
    for control in controls:
        request_ids = [None, existing[0]["record_id"] if existing else _id()]
        for request_id in request_ids:
            with pytest.raises(SyncError) as caught:
                actual_call(effects.create_registration17_locked,
                    profile=value.profile, store=value.store, client=value.client,
                    control_request_id=control["record_id"], request_id=request_id)
            assert caught.value.code.endswith("_CONTROL_ALREADY_CONSUMED")
            assert _unchanged(value) == before
            assert value.remote.seen == calls


@pytest.mark.parametrize("kind", ("negative", "budget_pair"))
def test_canonical_current_wire2_cannot_select_consumed_legacy_again(actual_history, kind):
    value = actual_history
    records, old, _controls = _consumed(value, kind)
    original_intent = next(record for folder, record in records
        if folder == "requests" and record["record_id"] == old["record_id"])
    intent, current = _pair2(value, old)
    body = intent["request"]
    if kind == "negative":
        body["dispositions"] = deepcopy(original_intent["request"]["dispositions"])
    else:
        old_body = original_intent["request"]
        body["defer_receipt"] = deepcopy(old_body["defer_receipt"])
        body["resume_receipt"] = deepcopy(old_body["resume_receipt"])
        # An unchanged old pair violates the new expected-claim generation
        # in the wire normalizer. The malicious wrapper below keeps original
        # control IDs but changes only its embedded receipt copies; original
        # saved files are never rewritten. Canonical wire hashes still cannot
        # manufacture a new original outcome or refund an old consumption.
        for fact in (body["defer_receipt"], body["resume_receipt"]):
            fact["lease_generation"] = body["expected_claim"]["lease_generation"]
        body["resume_basis"] = {"kind": "manual", **deepcopy(old_body["manual_resume_basis"])}
        intent["resume_provenance"] = deepcopy(original_intent["manual_resume_basis"])
        current["result"].update(attempt_basis="original_defer_refund",
            attempts=body["defer_receipt"]["attempts"] + 1)
    _rehash2(intent, current)
    _closed_wire(intent, current)
    assert permanent.validate_record("requests", intent, value.activation) == intent
    assert permanent.validate_record("receipts", current, value.activation) == current
    records.extend((("requests", intent), ("receipts", current)))
    entries, state = _entries(value, records), _state_for(value, current)
    pure_before = deepcopy((entries, state))
    _seed_preserving_prefix(value, records)
    _install_existing_current(value, current)
    before, calls = _unchanged(value), deepcopy(value.remote.seen)
    with pytest.raises(SyncError):
        local.current_settlement2(entries, state)
    assert (entries, state) == pure_before
    assert _unchanged(value) == before
    assert value.remote.seen == calls
    with value.store.reference_operation(), pytest.raises(SyncError):
        with local.capture_current_settlement_locked(profile=value.profile, store=value.store):
            pytest.fail("Consumed original controls cannot become new settlement facts")
    assert history._CURRENT.get() is None
    assert _unchanged(value) == before
    assert value.remote.seen == calls
