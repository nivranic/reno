"""Genuine old origins/controls and tasks.resume_sync's two distinct hashes.

The original origin and control1 files are produced in an actual isolated16
worker/State/SQL scope. Only tasks.resume_sync writes its four-column receipt.
A real prospective17 knowledge restore supplies the genuine certificate; the
original-reference bridge and original HTTP wire1 receipt factories remain
unsubstituted. No foreign seal, fixture task_request INSERT, real platform or
provider call is used. The ancestry State pointer is an isolated reader
baseline selecting that saved receipt, not evidence of a production readmit
State effect or permission for another source opportunity.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from copy import deepcopy
import json
import uuid
from types import SimpleNamespace

import pytest

from reno import budget_writer_witness as sql, home_recovery17 as restore17
from reno import home_recovery_runtime17 as runtime, home_recovery_source as sources
from reno import source_preservation_witness as witness, storage_admin
from reno import storage_roots, storage_root_inputs as inputs
from reno import storage_source_authority_roots as roots
from reno import sync_access, sync_source_admission_history1 as history
from reno import sync_source_authority as permanent, sync_source_execution as execution
from reno import sync_source_legacy_ancestry2 as ancestry, sync_source_legacy_intake17 as legacy_http
from reno import sync_source_legacy_origin_intake17 as origin_intake
from reno import sync_source_lifecycle17 as effects, sync_source_readmit_contract as wire
from reno import sync_source_readmit_schema, sync_state, tasks
from reno.budget_contract import BudgetDeferred
from reno.sync_protocol import SyncError
from tests.conftest import released16_env as env
from tests.test_sync_agent import install_import, add_video
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import executing, bind
from tests.test_sync_source_genesis17 import future17_gate, permanent_files
from tests.test_sync_source_claim import environment
from tests import test_sync_source_readmit_contract as sample


def _control(value, kind, *, defer_id=None):
    state, core = value.store.read(), value.core
    body = {"kind": kind, "request_id": str(uuid.uuid4()), "source": deepcopy(value.source16),
        "target": {key: core["binding"]["target_" + key] for key in ("library_id", "node_id")},
        "lease": deepcopy(state["lease"]), "recovery_generation": state["recovery"]["generation"],
        "recovery_stream": deepcopy(state["recovery"]["stream"])}
    if kind == "budget_resume":
        body["defer_request_id"] = defer_id
    else:
        # This opaque original wait identity is not a paid reservation or a
        # claimed model result. The released16 control producer accepts and
        # persists it; the task authorization is bound to the real defer UUID.
        body.update(claim=deepcopy(state["claims"][0]["claim"]), reason="daily_limit",
            retry_at=None, budget_operation_id=str(uuid.uuid4()))
    basis = {"format_version": 1, "kind": "original_core", "core_sha256": permanent.digest(core),
        "home_epoch": state["local_epoch"], "session": deepcopy(state["lease"]),
        "claim": deepcopy(value.origin["claim"]), "recovery_generation": state["recovery"]["generation"],
        "recovery_stream": deepcopy(state["recovery"]["stream"]),
        "control_revision": state["recovery"]["revision"], "origin_ref": permanent.record_ref(value.origin),
        "admission_ref": None, "continuity": None}
    return {"format_version": 1, "record_kind": "control", "record_id": body["request_id"],
        "core_sha256": permanent.digest(core), "request_sha256": wire.digest(body),
        "request": body, "creation_basis": basis}


def _publish_control16(value, record, proof):
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        with storage_admin.connection(value.profile) as con:
            con.execute("BEGIN IMMEDIATE")
            with permanent.producer(owner, proof, con) as actual:
                result = owner.publish("controls", record, producer=actual)
            con.commit()
        assert owner.read(permanent.record_ref(result)) == record
        return result


def _intake_origin(value):
    with origin_intake.capture_legacy_origin_intake17_locked(profile=value.profile, store=value.store,
            item_id=value.core["item_id"], expected_core_sha256=permanent.digest(value.core),
            expected_record_sha256=permanent.digest(value.origin)) as captured:
        before, after = captured.expected_state_effect()
        with effects._cas(captured.owner, captured._schema, captured._selected_pins,
                before, after, control=captured) as guard:
            with guard.writer():
                guard.replace()
            captured.adopt_state_effect(guard)
    assert value.store.read()["source_authority"]["origin"] == permanent.record_ref(value.origin)


def _capture_receipt(value):
    with value.store.reference_operation():
        with runtime.capture_completed_locked(profile=value.profile, store=value.store,
                operation_id=value.restore_id) as restored:
            preservation = restored.preservation_observation(from_epoch=value.core["local_epoch"])
            provenance = restored.provenance_ref()
        with sql.connection(value.profile) as con:
            source = sync_access.identity(con)
        state = value.store.read()
        request = {"kind": "source_readmit", "request_id": str(uuid.uuid4()), "source": source,
            "target": {key: value.core["binding"]["target_" + key] for key in ("library_id", "node_id")},
            "lease": deepcopy(state["lease"]), "current_home_epoch": state["local_epoch"],
            "recovery_generation": state["recovery"]["generation"],
            "recovery_stream": deepcopy(state["recovery"]["stream"]),
            "control_revision": state["recovery"]["revision"],
            "original": {"core_sha256": permanent.digest(value.core), "home_epoch": value.core["local_epoch"],
                "item_id": value.core["item_id"], "submitted_url_sha256": value.core["submitted_url_sha256"],
                "claim": deepcopy(value.origin["claim"]), "recovery_generation": value.core["recovery_generation"],
                "recovery_stream": deepcopy(value.core["recovery_stream"])},
            "preservation": preservation["preservation"], "expected_claim": deepcopy(value.origin["claim"]),
            "previous_admission_id": None, "dispositions": [],
            "defer_receipt": sample.old_receipt(value.defer["request"]),
            "resume_receipt": sample.old_receipt(value.resume["request"],
                parent=sample.old_receipt(value.defer["request"])),
            "manual_resume_basis": {"authorization_id": value.manual_row["request_id"],
                "authorization_sha256": value.manual_row["payload_signature"],
                "defer_request_id": value.defer["record_id"]}}
        assert wire.normalize_request(request) == request
        intent = {"format_version": 1, "record_kind": "recovery_intent", "record_id": request["request_id"],
            "core_sha256": permanent.digest(value.core), "request_sha256": wire.request_sha256(request),
            "request": request, "expected_before": effects._before(state, value.core),
            "preservation_basis": provenance, "manual_resume_basis": {
                "format_version": 1, "producer": "sync_budget_resume",
                "operation_id": value.manual_row["request_id"], "receipt_sha256": permanent.digest(value.manual_row)}}
        proof = execution.historical_binding(value.store, value.profile)
        with permanent.owned(value.store) as owner, storage_admin.connection(value.profile) as con:
            con.execute("BEGIN IMMEDIATE")
            with permanent.producer(owner, proof, con, historical=True) as actual:
                owner.publish("requests", intent, producer=actual)
            con.commit()
    remote = environment()
    remote.payloads["readmit"] = sample.admission(request)
    remote.client.source_readmit(request)  # Real closed HTTP engine; synthetic external peer.
    with value.store.reference_operation():
        saved = legacy_http.publish_legacy_http_receipt17_locked(profile=value.profile, store=value.store,
            client=remote.client, request_id=intent["record_id"], request_sha256=intent["request_sha256"])
    value.intent, value.receipt, value.http = intent, saved["record"], remote
    assert intent["manual_resume_basis"]["receipt_sha256"] != request["manual_resume_basis"]["authorization_sha256"]


@pytest.fixture
def actual_dual_sha(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, owner):
        proof = bind(cycle)  # Actual released16 first-core/origin producer.
        value = SimpleNamespace(profile=cycle.storage_profile, storage_profile=cycle.storage_profile,
            store=cycle.store, core=proof.core, source16=deepcopy(cycle.source), root=job["job_id"])
        with value.store.reference_operation(), permanent.owned(value.store) as saved:
            originals = list(saved.observe_readonly()["entries"]["origins"].values())
            assert len(originals) == 1 and originals[0]["state"] == "committed"
            value.origin = deepcopy(originals[0]["record"])
        value.defer = _publish_control16(value, _control(value, "budget_defer"), proof)
        value.store.install_budget_defer_request(value.defer["request"])
        with storage_admin.connection(value.profile) as con:
            initial_requests = [tuple(row) for row in con.execute(
                "SELECT request_id,job_id,payload_signature,created_at FROM main.task_request")]
        tasks.resume_sync(job["job_id"], profile=value.profile)  # Sole actual authorization writer.
        with storage_admin.connection(value.profile) as con:
            root = con.execute("SELECT * FROM main.job WHERE job_id=?", (job["job_id"],)).fetchone()
            resume_id, payload_sha = tasks._sync_resume_identity(con, root, value.defer["record_id"])
            rows = con.execute("SELECT request_id,job_id,payload_signature,created_at FROM main.task_request "
                "WHERE request_id=?", (resume_id,)).fetchall()
            actual_requests = [tuple(row) for row in con.execute(
                "SELECT request_id,job_id,payload_signature,created_at FROM main.task_request")]
        assert len(rows) == 1
        value.manual_row = dict(rows[0])
        assert value.manual_row["payload_signature"] == payload_sha
        assert len(actual_requests) == len(initial_requests) + 1
        assert set(initial_requests) <= set(actual_requests)
        assert permanent.digest(value.manual_row) != value.manual_row["payload_signature"]
        value.resume = _publish_control16(value, _control(value, "budget_resume", defer_id=value.defer["record_id"]), proof)
        # Release the real original worker through its actual durable control.
        # There is no fabricated SQL owner or generation for the later reader.
        assert tasks.defer(job["job_id"], owner, BudgetDeferred("manual_pause"), profile=value.profile)
    source_id = str(uuid.uuid4())
    sources.create_checkpoint(profile=value.profile, source_id=source_id)
    add_video("dual_sha_unrelated_after_checkpoint")
    with storage_admin.connection(value.profile) as con:
        sync_source_readmit_schema.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    with future17_gate(value, monkeypatch):
        view = restore17.preview(profile=value.profile, source_id=source_id)
        value.restore_id = str(uuid.uuid4())
        restore17.apply(profile=value.profile, source_id=source_id,
            expected_digest=view["digest"], operation_id=value.restore_id)
        _intake_origin(value)
        with tasks.process_lock(profile=value.profile) as acquired, value.store.exclusive(), executing(value):
            assert acquired
            _capture_receipt(value)
        yield value


def _history(value):
    return history.capture_legacy_admission_history_locked(profile=value.profile, store=value.store,
        admission_id=value.receipt["record_id"], expected_record_sha256=permanent.digest(value.receipt))


def _projection(value):
    with value.store.reference_operation(), roots.capture_locked(value.profile,
            binding=value.store.read()["binding"], limits=roots.CaptureLimits(deadline_seconds=120)) as captured:
        captured.verify_source()
        return captured.public_summary()


def _proofs(value, projection=None, provenance=None):
    projection = _projection(value) if projection is None else projection
    with value.store.reference_operation(), sql.connection(value.profile) as con:
        provenance = inputs.read_provenance_projection17(con) if provenance is None else provenance
        budget = storage_roots._Budget(storage_roots.RootLimits(), None)
        return storage_roots._manual_proofs(con, projection, provenance,
            value.core["binding"]["home_library_id"], budget)


def _state_selector(value):
    # Existing receipt selector only. This detached representation grants no
    # State writer, current paid admission, transport or model opportunity.
    state = deepcopy(value.store.read())
    state["source_admission"] = history._admission_ref(value.receipt)
    return state


def test_real_tasks_producer_keeps_different_payload_and_row_hashes_in_history1(actual_dual_sha, monkeypatch):
    value = actual_dual_sha
    before_state, before_files, before_http = deepcopy(value.store.read()), permanent_files(value), deepcopy(value.http.seen)
    with sql.connection(value.profile) as con:
        before_rows = [tuple(row) for row in con.execute("SELECT request_id,job_id,payload_signature,created_at FROM main.task_request")]
    def no_send(*args, **kwargs):
        pytest.fail("Historical manual authorization triggered a new request")
    monkeypatch.setattr(value.http.client, "_request", no_send)
    with _history(value) as captured:
        assert type(captured) is history.LegacyAdmissionHistoryCapture1
        assert captured.request["manual_resume_basis"]["authorization_sha256"] == value.manual_row["payload_signature"]
        assert value.intent["manual_resume_basis"]["receipt_sha256"] == permanent.digest(value.manual_row)
        assert captured._manual_rows == [(value.defer["record_id"], value.manual_row)]
        assert {row["record_id"] for row in captured.consumed_control_refs} == {
            value.defer["record_id"], value.resume["record_id"]}
        captured.verify_source()
    assert value.store.read() == before_state and permanent_files(value) == before_files and value.http.seen == before_http
    with sql.connection(value.profile) as con:
        assert [tuple(row) for row in con.execute("SELECT request_id,job_id,payload_signature,created_at FROM main.task_request")] == before_rows


def test_ancestry2_projection_and_real_reader_validate_same_original_manual_row(actual_dual_sha):
    value = actual_dual_sha
    state = _state_selector(value)
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        entries = owner.observe_readonly()["entries"]
        projected = ancestry.legacy_consumption2(entries, state)
    assert {row["record_id"] for row in projected["control_refs"]} == {value.defer["record_id"], value.resume["record_id"]}
    # Install a controlled existing reader baseline, as in the established
    # ancestry tests. Only this existing receipt ref changes; it supplies no
    # proof that the production readmit effect/pending bridge has completed.
    with value.store.reference_operation():
        sync_state._write(value.store._directory, sync_state._STATE, sync_state._validate(state))
    before = permanent_files(value)
    with ancestry.capture_legacy_ancestry_locked(profile=value.profile, store=value.store) as captured:
        assert captured.admission_ref() == state["source_admission"]
        assert captured._manual_rows == [(value.defer["record_id"], value.manual_row)]
        captured.verify_source()
    assert permanent_files(value) == before and value.store.read() == state


def test_gc_manual_proof_is_actual_row_hash_and_keeps_original_payload_hash(actual_dual_sha):
    value = actual_dual_sha
    before_state, before_files = deepcopy(value.store.read()), permanent_files(value)
    result = _proofs(value)
    assert result[(permanent.digest(value.core), value.manual_row["request_id"])] == permanent.digest(value.manual_row)
    assert result[(permanent.digest(value.core), value.manual_row["request_id"])] != value.manual_row["payload_signature"]
    assert value.store.read() == before_state and permanent_files(value) == before_files


@pytest.mark.parametrize("changed", ["wire_sha", "row_sha", "uuid", "defer"])
def test_gc_manual_hash_or_context_tampering_remains_unproven(actual_dual_sha, changed):
    value = actual_dual_sha
    projection = _projection(value)
    row = next(row for row in projection["references"] if row["kind"] == "requests" and row["record_id"] == value.intent["record_id"])
    manual = next(edge for edge in row["refs"] if edge["kind"] == "manual_resume")
    provenance = next(edge for edge in row["refs"] if edge.get("producer") == "sync_budget_resume")
    if changed == "wire_sha":
        manual["authorization_sha256"] = permanent.digest(value.manual_row)
    elif changed == "row_sha":
        provenance["receipt_sha256"] = value.manual_row["payload_signature"]
    elif changed == "uuid":
        provenance["operation_id"] = str(uuid.uuid4())
    else:
        manual["defer_request_id"] = str(uuid.uuid4())
    assert _proofs(value, projection=projection) == {}


def test_gc_provenance_copy_cannot_replace_the_actual_sql_row(actual_dual_sha):
    value = actual_dual_sha
    projection = _projection(value)
    with sql.connection(value.profile) as con:
        provenance = inputs.read_provenance_projection17(con)
    row = next(row for row in provenance["task_request"] if row["request_id"] == value.manual_row["request_id"])
    row["created_at"] = "2099-01-01T00:00:00.000Z"
    edge = next(edge for item in projection["references"] if item["record_id"] == value.intent["record_id"]
        for edge in item["refs"] if edge.get("producer") == "sync_budget_resume")
    edge["receipt_sha256"] = permanent.digest(row)
    assert _proofs(value, projection=projection, provenance=provenance) == {}


def test_gc_manual_proof_does_not_adopt_a_changed_original_job_generation(actual_dual_sha):
    value = actual_dual_sha
    expected = {(permanent.digest(value.core), value.manual_row["request_id"]): permanent.digest(value.manual_row)}
    assert _proofs(value) == expected
    with storage_admin.connection(value.profile) as con:
        con.execute("UPDATE main.job SET execution_generation=execution_generation+1 WHERE job_id=?",
            (value.core["root_job_id"],))
        con.commit()
    assert _proofs(value) == {}


def test_gc_callback_cannot_replace_the_actual_row_after_sql_comparison(actual_dual_sha):
    value = actual_dual_sha
    projection = _projection(value)
    with sql.connection(value.profile) as con:
        provenance = inputs.read_provenance_projection17(con)
    borrowed = next(row for row in provenance["task_request"]
        if row["request_id"] == value.manual_row["request_id"])
    edge = next(edge for item in projection["references"] if item["record_id"] == value.intent["record_id"]
        for edge in item["refs"] if edge.get("producer") == "sync_budget_resume")
    observation = {"job_read": False, "changed": False}

    def trace(statement):
        if "FROM main.job WHERE job_id=" in statement:
            observation["job_read"] = True

    def check():
        if observation["job_read"] and not observation["changed"]:
            # Only detached public inputs change. The genuine SQL receipt,
            # its real timestamp and original immutable authority files stay.
            borrowed["created_at"] = "2099-01-01T00:00:00.000Z"
            edge["receipt_sha256"] = permanent.digest(borrowed)
            observation["changed"] = True

    with value.store.reference_operation(), sql.connection(value.profile) as con:
        con.set_trace_callback(trace)
        try:
            result = storage_roots._manual_proofs(con, projection, provenance,
                value.core["binding"]["home_library_id"], storage_roots._Budget(storage_roots.RootLimits(), check))
        finally:
            con.set_trace_callback(None)
        actual = dict(con.execute("SELECT request_id,job_id,payload_signature,created_at FROM main.task_request "
            "WHERE request_id=?", (value.manual_row["request_id"],)).fetchone())
    assert observation["changed"] and actual == value.manual_row
    assert result == {(permanent.digest(value.core), value.manual_row["request_id"]): permanent.digest(actual)}


@pytest.mark.parametrize("changed", ["row_timestamp", "payload_signature", "root_lineage"])
def test_live_history_capture_and_gc_reject_actual_row_or_job_drift(actual_dual_sha, changed):
    value = actual_dual_sha
    with pytest.raises((SyncError, ValueError)):
        with _history(value) as captured:
            with storage_admin.connection(value.profile) as con:
                if changed == "row_timestamp":
                    con.execute("UPDATE main.task_request SET created_at=? WHERE request_id=?",
                        ("2099-01-01T00:00:00.000Z", value.manual_row["request_id"]))
                elif changed == "payload_signature":
                    con.execute("UPDATE main.task_request SET payload_signature=? WHERE request_id=?",
                        ("f" * 64, value.manual_row["request_id"]))
                else:
                    con.execute("UPDATE main.job SET continuation_id=? WHERE job_id=?",
                        (str(uuid.uuid4()), value.core["root_job_id"]))
                con.commit()
            with pytest.raises((SyncError, ValueError)):
                captured.verify_source()
            assert _proofs(value) == {}
            # Final observation rejects the same drift, without rereading an
            # old capture as a smaller valid history or resending an outcome.
    with pytest.raises((SyncError, ValueError)):
        with _history(value):
            pytest.fail("Changed actual task row or lineage supplied valid history")


def test_history_manual_rows_are_sealed_against_private_mutation(actual_dual_sha):
    value = actual_dual_sha
    with pytest.raises(SyncError):
        with _history(value) as captured:
            captured._manual_rows[0][1]["created_at"] = "2099-01-01T00:00:00.000Z"
            captured.verify_source()


def test_history_and_ancestry_cannot_be_used_from_a_foreign_copied_context(actual_dual_sha):
    value = actual_dual_sha
    with _history(value) as captured:
        context = copy_context()
        def foreign():
            with pytest.raises(SyncError):
                captured.verify_source()
        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(context.run, foreign).result()
        captured.verify_source()
    with value.store.reference_operation(), sql.connection(value.profile, write=True):
        with pytest.raises(SyncError):
            with _history(value):
                pytest.fail("A naked writer supplied manual-history capability")
