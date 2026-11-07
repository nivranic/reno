"""Actual Home producers/HTTP/record1/pending/closed StateCAS17 integration.

The isolated prospective17 fixture uses a real completed restore17, genesis17
producer, historical Home binding, live SQL producer and original transport.
Its controlled HTTP peer returns a wire1 contract result. This is not evidence
of a real Cloud SQL producer, schema16 migration, global17 activation or public
reconcile dispatch; those separate integrations remain required.

The restored positive CAS remains a target test until authority/restore exact
type dispatch is wired. It must not be counted as passing from static parsing.
This module never monkeypatches those capability gates or skips that target.
Committed zero-RPC cases do not cover reserved/file/lagging-head crash recovery;
those windows still need the original exact receipt completion producer.
"""
from contextlib import ExitStack, contextmanager
from copy import deepcopy
import json
import uuid

import pytest

from reno import budget_writer_witness as sql, storage_admin
from reno import home_recovery_runtime17 as runtime
from reno import sync_source_authority as permanent, sync_source_execution as execution
from reno import sync_source_legacy_intake17 as intake, sync_source_lifecycle17 as effects
from reno import sync_source_readmit_contract as wire, sync_source_transport17 as transport
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_restore_cycle17 import restored_origin17
from tests import test_sync_source_readmit_contract as sample


def original_intent(value):
    """Real origin and original Home request producer; no seeded permanent row."""
    core = value.core  # Returned by the fixture's actual genesis17 producer.
    with value.store.reference_operation():
        binding = execution.historical_binding(value.store, value.profile)
        with permanent.owned(value.store) as owner:
            state = value.store.read()
            observed = owner.observe_readonly()
            origin = effects._origin(observed, state, core)
            before = effects._before(state, core)
            assert state["local_epoch"] > core["local_epoch"]
            with runtime.capture_completed_locked(profile=value.profile, store=value.store,
                    operation_id=value.restore_operation) as restored:
                preservation = restored.preservation_observation(from_epoch=core["local_epoch"])
                restore_ref = restored.provenance_ref()
                assert preservation["source_core_sha256"] == permanent.digest(core)
                assert preservation["preservation"]["to_epoch"] == state["local_epoch"]
            request = {"kind": "source_readmit", "request_id": str(uuid.uuid4()),
                "source": deepcopy(value.source), "target": deepcopy(value.target),
                "lease": deepcopy(state["lease"]), "current_home_epoch": state["local_epoch"],
                "recovery_generation": state["recovery"]["generation"],
                "recovery_stream": deepcopy(state["recovery"]["stream"]),
                "control_revision": state["recovery"]["revision"],
                "original": {"core_sha256": origin["core_sha256"], "home_epoch": core["local_epoch"],
                    "item_id": core["item_id"], "submitted_url_sha256": core["submitted_url_sha256"],
                    "claim": deepcopy(origin["claim"]), "recovery_generation": core["recovery_generation"],
                    "recovery_stream": deepcopy(core["recovery_stream"])},
                "preservation": preservation["preservation"], "expected_claim": deepcopy(before["claim"]),
                "previous_admission_id": None, "dispositions": [], "defer_receipt": None,
                "resume_receipt": None, "manual_resume_basis": None}
            assert wire.normalize_request(request) == request
            record = {"format_version": 1, "record_kind": "recovery_intent", "record_id": request["request_id"],
                "core_sha256": origin["core_sha256"], "request_sha256": wire.request_sha256(request),
                "request": request, "expected_before": before,
                "preservation_basis": restore_ref, "manual_resume_basis": None}
            with storage_admin.connection(value.profile) as connection:
                connection.execute("BEGIN IMMEDIATE")
                with permanent.producer(owner, binding, connection, historical=True) as producer:
                    saved = owner.publish("requests", record, producer=producer)
                connection.commit()
            return core, {"record": saved, "ref": permanent.record_ref(saved)}


def received(value):
    core, intent = original_intent(value)
    request = intent["record"]["request"]
    value.remote.payloads["readmit"] = sample.admission(request)
    answer = value.client.source_readmit(request)
    before = deepcopy(value.store.read())
    with value.store.reference_operation():
        saved = intake.publish_legacy_http_receipt17_locked(profile=value.profile, store=value.store,
            client=value.client, request_id=request["request_id"], request_sha256=intent["record"]["request_sha256"])
    assert value.store.read() == before  # receipt-first never installs State
    return core, intent, saved, answer


def pending(value):
    core, intent, receipt, answer = received(value)
    effects.install_recovery_pending17_locked(profile=value.profile, store=value.store,
        request_id=intent["record"]["record_id"])
    return core, intent, receipt, answer


def test_actual_http_wire1_keeps_original_outer_record1_bytes_and_state(restored_origin17):
    core, intent, receipt, answer = received(restored_origin17)
    record = receipt["record"]
    assert record == {"format_version": 1, "record_kind": "recovery_receipt",
        "record_id": intent["record"]["record_id"], "request_sha256": intent["record"]["request_sha256"],
        "result": answer, "result_sha256": permanent.digest(answer), "parent_defer": None}
    assert answer["format_version"] == record["format_version"] == 1
    assert restored_origin17.store.read()["source_execution"] == core
    assert restored_origin17.store.read()["source_admission"] is None
    with restored_origin17.store.reference_operation(), permanent.owned(restored_origin17.store) as owner:
        assert owner.read(receipt["ref"]) == record


@pytest.mark.parametrize("install_pending", [False, True])
def test_committed_selector_and_publisher_replay_need_no_client_broker_or_rpc(restored_origin17, monkeypatch, install_pending):
    value = restored_origin17
    _, intent, receipt, _ = received(value)
    identifier, request_sha = intent["record"]["record_id"], intent["record"]["request_sha256"]
    if install_pending:
        effects.install_recovery_pending17_locked(profile=value.profile, store=value.store,
            request_id=identifier)
    before_files, before_state, seen = permanent_files(value), deepcopy(value.store.read()), deepcopy(value.remote.seen)
    def forbidden(*args, **kwargs):
        pytest.fail("Committed historical replay requested a new ticket/RPC/capability")
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", forbidden)
    monkeypatch.setattr(transport, "capture_receipt", forbidden)
    monkeypatch.setattr(intake, "capture_legacy_baseline_admission17_locked", forbidden)
    original_connection = sql.connection
    @contextmanager
    def read_only_connection(profile, *, write=False):
        assert write is False
        with original_connection(profile, write=False) as connection:
            yield connection
    monkeypatch.setattr(sql, "connection", read_only_connection)
    with value.store.reference_operation():
        replay = intake.publish_legacy_http_receipt17_locked(profile=value.profile, store=value.store,
            client=None, request_id=identifier, request_sha256=request_sha)
        selected = intake.read_legacy_http_receipt17_locked(profile=value.profile, store=value.store,
            request_id=identifier, request_sha256=request_sha,
            expected_record_sha256=receipt["ref"]["record_sha256"])
        assert replay == selected == receipt
        assert intake._PUBLISHER.get() is intake._CURRENT.get() is None
        # A returned dictionary is an isolated fact copy, never a live guard.
        replay["record"]["result"]["attempts"] += 1
        assert intake.read_legacy_http_receipt17_locked(profile=value.profile, store=value.store,
            request_id=identifier, request_sha256=request_sha) == receipt
    assert permanent_files(value) == before_files  # Includes original bytes and mtimes.
    assert value.store.read() == before_state and value.remote.seen == seen


@pytest.mark.parametrize("changed_selector", ["uuid", "request_sha256", "record_sha256"])
def test_committed_selector_replay_rejects_changed_uuid_or_sha_without_writing(restored_origin17, monkeypatch, changed_selector):
    value = restored_origin17
    _, intent, receipt, _ = received(value)
    def forbidden(*args, **kwargs):
        pytest.fail("A changed historical selector requested HTTP or a ticket")
    monkeypatch.delattr(value.client, "_source_transport17_broker")
    monkeypatch.setattr(value.client, "_request", forbidden)
    monkeypatch.setattr(transport, "capture_receipt", forbidden)
    selectors = {"request_id": intent["record"]["record_id"],
        "request_sha256": intent["record"]["request_sha256"],
        "expected_record_sha256": receipt["ref"]["record_sha256"]}
    selectors[{"uuid": "request_id", "request_sha256": "request_sha256",
        "record_sha256": "expected_record_sha256"}[changed_selector]] = (
            str(uuid.uuid4()) if changed_selector == "uuid" else "f" * 64)
    before_files, before_state = permanent_files(value), deepcopy(value.store.read())
    with value.store.reference_operation(), pytest.raises(SyncError):
        intake.read_legacy_http_receipt17_locked(profile=value.profile, store=value.store, **selectors)
    if changed_selector == "request_sha256":
        with value.store.reference_operation(), pytest.raises(SyncError):
            intake.publish_legacy_http_receipt17_locked(profile=value.profile, store=value.store,
                client=None, request_id=selectors["request_id"], request_sha256=selectors["request_sha256"])
    assert permanent_files(value) == before_files and value.store.read() == before_state


def test_actual_original_pending_closed_cas_installs_only_historical_baseline(restored_origin17, monkeypatch):
    core, intent, receipt, answer = pending(restored_origin17)
    before_files = permanent_files(restored_origin17)
    before_state = deepcopy(restored_origin17.store.read())
    def no_rpc(*args, **kwargs):
        pytest.fail("Original persisted baseline CAS dispatched another RPC")
    monkeypatch.setattr(restored_origin17.client, "_request", no_rpc)
    with restored_origin17.store.reference_operation(), permanent.owned(restored_origin17.store) as owner:
        with intake.capture_legacy_baseline_admission17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
                request_id=intent["record"]["record_id"],
                expected_record_sha256=receipt["ref"]["record_sha256"]) as captured:
            assert type(captured) is intake.LegacyBaselineAdmissionCapture17
            assert captured.core == core and captured.result == answer
            assert type(captured.preservation_capture) is runtime.CompletedRestoreCapture17
            assert captured.resume_capture is None
            assert captured.preservation_capture.provenance_ref() == intent["record"]["preservation_basis"]
            observed = owner.observe_readonly()
            after = effects._recovery_after(observed, before_state, intent["record"], receipt["record"])
            with ExitStack() as stack:
                schema = effects._Schema(restored_origin17.profile, restored_origin17.store, core, before_state, stack)
                pins = effects._SelectedPins(stack, restored_origin17.profile, restored_origin17.store, observed,
                    [("origins", effects._origin(observed, before_state, core)),
                     ("requests", intent["record"]), ("receipts", receipt["record"])])
                with effects._cas(owner, schema, pins, before_state, after, control=captured) as guard:
                    with guard.writer():
                        guard.replace()
                    assert captured.baseline_state_effect(guard) == (before_state, after)
            assert restored_origin17.store.read() == after
    final = restored_origin17.store.read()
    assert final["source_execution"] == before_state["source_execution"] == core
    assert final["source_recovery_pending"] is None
    assert final["source_admission"]["admission_id"] == answer["admission_id"]
    assert final["source_admission"]["record_sha256"] == receipt["ref"]["record_sha256"]
    assert final["claims"][0]["claim"] == answer["claim"]
    assert permanent_files(restored_origin17) == before_files
    # The historical fact remains readable after its original pending is gone.
    with restored_origin17.store.reference_operation():
        assert intake.publish_legacy_http_receipt17_locked(profile=restored_origin17.profile,
            store=restored_origin17.store, client=None, request_id=intent["record"]["record_id"],
            request_sha256=intent["record"]["request_sha256"]) == receipt
    assert restored_origin17.store.read() == final and permanent_files(restored_origin17) == before_files
    assert not any(hasattr(captured, name) for name in ("admit_paid", "_advance_creation", "writer", "operation"))
    with pytest.raises(SyncError):
        captured.verify_source()


def test_selector_capture_cannot_use_a_naked_schema17_writer(restored_origin17):
    _, intent, _, _ = pending(restored_origin17)
    before = deepcopy(restored_origin17.store.read())
    with restored_origin17.store.reference_operation():
        with intake.capture_legacy_baseline_admission17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
                request_id=intent["record"]["record_id"]) as captured:
            with pytest.raises(SyncError, match="legacy admission intake"):
                with sql.connection(restored_origin17.profile, write=True) as connection:
                    captured.verify_for_commit(connection,
                        writer_permit=sql.require_writer(connection, profile=restored_origin17.profile))
    assert restored_origin17.store.read() == before


def test_replaced_public_dto_has_no_original_http_receipt_producer(restored_origin17, monkeypatch):
    _, intent = original_intent(restored_origin17)
    request = intent["record"]["request"]
    monkeypatch.setattr(restored_origin17.client, "source_readmit", lambda request: sample.admission(request))
    restored_origin17.client.source_readmit(request)
    before_files, before_state = permanent_files(restored_origin17), deepcopy(restored_origin17.store.read())
    with restored_origin17.store.reference_operation(), pytest.raises(SyncError):
        intake.publish_legacy_http_receipt17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
            client=restored_origin17.client, request_id=request["request_id"], request_sha256=intent["record"]["request_sha256"])
    assert permanent_files(restored_origin17) == before_files and restored_origin17.store.read() == before_state


def test_receipt_selector_sha_and_actual_pending_are_both_required(restored_origin17):
    _, intent, receipt, _ = received(restored_origin17)
    with restored_origin17.store.reference_operation(), pytest.raises(SyncError):
        with intake.capture_legacy_baseline_admission17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
                request_id=intent["record"]["record_id"], expected_record_sha256=receipt["ref"]["record_sha256"]):
            pytest.fail("A receipt without its original pending authorized installation")
    effects.install_recovery_pending17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
        request_id=intent["record"]["record_id"])
    with restored_origin17.store.reference_operation(), pytest.raises(SyncError):
        with intake.capture_legacy_baseline_admission17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
                request_id=intent["record"]["record_id"], expected_record_sha256="f" * 64):
            pytest.fail("A different original receipt SHA authorized installation")


@pytest.mark.parametrize("capability", [intake.LegacyHttpReceiptPublisher17, intake.LegacyBaselineAdmissionCapture17])
def test_public_constructors_cannot_accept_a_seal_owner_or_result(capability):
    with pytest.raises(SyncError):
        capability(owner=object(), result={}, seal=object())


def test_private_effect_mutation_is_not_a_replacement_state_authority(restored_origin17):
    _, intent, _, _ = pending(restored_origin17)
    with restored_origin17.store.reference_operation(), pytest.raises(SyncError):
        with intake.capture_legacy_baseline_admission17_locked(profile=restored_origin17.profile, store=restored_origin17.store,
                request_id=intent["record"]["record_id"]) as captured:
            after = json.loads(captured._after_json)
            after["claims"][0]["attempts"] += 1
            captured._after_json = json.dumps(after, ensure_ascii=False, allow_nan=False,
                sort_keys=True, separators=(",", ":"))
            captured.verify_source()


def test_replacement_restore_dto_is_not_the_original_held_child(restored_origin17):
    _, intent, _, _ = pending(restored_origin17)
    before = deepcopy(restored_origin17.store.read())
    with restored_origin17.store.reference_operation(), pytest.raises(SyncError):
        with intake.capture_legacy_baseline_admission17_locked(profile=restored_origin17.profile,
                store=restored_origin17.store, request_id=intent["record"]["record_id"]) as captured:
            captured.preservation_capture = object()
            captured.verify_source()
    assert restored_origin17.store.read() == before
