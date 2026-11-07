"""Actual released16 origin producer, then isolated prospective17 intake.

The origin is produced by bind_execution/_install_initial_core under the real
old owned worker/State/SQL contract before any17 DDL exists. No seed_origin or
caller-made permanent reference substitutes for that production. The scoped
future gate concerns only this disposable DB, not production17 activation.
Modern restore CAS is a target until the exact runtime/StateCAS dispatch is
wired; these tests do not replace those gates to obtain a passing result.
"""
from contextlib import contextmanager
from contextvars import copy_context
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import hashlib
import json
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import budget_writer_witness as sql, home_recovery as old_restore
from reno import home_recovery17 as restore17, home_recovery_runtime17 as runtime
from reno import home_recovery_source as sources, migrations, storage_admin
from reno import sync_source_authority as authority, sync_source_binding17 as binding
from reno import sync_source_execution as execution, sync_source_lifecycle17 as effects
from reno import sync_source_legacy_origin_intake17 as intake, sync_source_readmit_schema
from reno import sync_state, worker
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env, install_import, add_video
from tests.test_sync_budget_propagation import home_cycle
from tests.test_sync_source_execution_binding import executing, bind
from tests.test_sync_source_genesis17 import future17_gate, origin_records, permanent_files
from tests.test_sync_source_execution_state import request as old_budget_request


@pytest.fixture
def legacy16(env, monkeypatch):
    install_import(env, monkeypatch)
    with home_cycle(env) as cycle, executing(cycle) as (job, _):
        proof = bind(cycle)  # Actual legacy producer, no fixture origin file.
        value = SimpleNamespace(profile=cycle.storage_profile, store=cycle.store,
            core=proof.core, proof=proof, job=deepcopy(job), remote=env, source=deepcopy(cycle.source))
        assert value.store.read()["format_version"] == 3
        assert "source_authority" not in value.store.read()
        files = origin_records(value)
        assert len(files) == 1 and files[0][1]["core"] == proof.core
        value.origin = deepcopy(files[0][1])
    assert worker.execution_context() is None
    assert migrations.SCHEMA_VERSION == 16
    yield value


@contextmanager
def upgraded(value, monkeypatch):
    with storage_admin.connection(value.profile) as con:
        sync_source_readmit_schema.install(con)
        con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
        con.commit()
    with future17_gate(value, monkeypatch):
        yield value


def select(value, **changes):
    selectors = {"profile": value.profile, "store": value.store,
        "item_id": value.core["item_id"], "expected_core_sha256": authority.digest(value.core),
        "expected_record_sha256": authority.digest(value.origin)}
    selectors.update(changes)
    return intake.capture_legacy_origin_intake17_locked(**selectors)


def install(captured):
    before, after = captured.expected_state_effect()
    with effects._cas(captured.owner, captured._schema, captured._selected_pins,
            before, after, control=captured) as guard:
        with guard.writer():
            guard.replace()
        assert captured.origin_state_effect(guard) == (before, after)
        captured.adopt_state_effect(guard)
    return after


def snapshot(value):
    path = value.store.data_dir / "sync-state.json"
    return deepcopy(value.store.read()), (path.stat().st_mtime_ns, path.read_bytes()), permanent_files(value)


def test_actual_old_producer_before_upgrade_cannot_be_admitted_as_schema17(legacy16):
    before = snapshot(legacy16)
    with pytest.raises((SyncError, ValueError, sqlite3.DatabaseError)):
        with select(legacy16):
            pytest.fail("Actual16 should not gain an active17 consumer")
    assert snapshot(legacy16) == before


@pytest.mark.parametrize("state_version", [3, 4, 5])
def test_actual_old_origin_intake_is_only_representation_and_original_reference(legacy16, monkeypatch, state_version):
    value = legacy16
    if state_version == 4:
        value.store.enable_source_control()
    elif state_version == 5:
        value.store.enable_source_readmit()
    before = deepcopy(value.store.read())
    original_files = permanent_files(value)
    with upgraded(value, monkeypatch):
        with select(value) as captured:
            assert type(captured) is intake.LegacyOriginIntakeCapture17
            assert captured.core == value.core
            assert captured.origin_ref == authority.record_ref(value.origin)
            assert captured.preservation_capture is captured.resume_capture is None
            assert not any(hasattr(captured, name) for name in ("admit_paid", "request", "result", "operation", "writer"))
            expected_before, expected_after = captured.expected_state_effect()
            assert expected_before == before
            assert expected_after["source_authority"] == {
                "format_version": 1, "core_sha256": authority.digest(value.core),
                "origin": authority.record_ref(value.origin), "defer": None,
                "resume": None, "failure": None, "publication": None}
            for key, actual in before.items():
                if key not in {"format_version", "source_authority"}:
                    assert expected_after[key] == actual
            assert value.store.read() == before
            after = install(captured)
            assert value.store.read() == after
        # The binding reader owns a new independent authority lifetime; it
        # cannot borrow/nest the still-live original intake's private owner.
        facts = binding.capture_home_binding_locked(profile=value.profile, store=value.store,
            item_id=value.core["item_id"])
        assert facts.core == value.proof.core
        assert facts.resolution_operation == value.proof.resolution_operation
        assert facts.namespace == value.proof.namespace
    assert permanent_files(value) == original_files
    with pytest.raises(SyncError):
        captured.verify_source()


def test_public_original_reference_install_replays_without_new_state_or_origin(legacy16, monkeypatch):
    value = legacy16
    arguments = {"profile": value.profile, "store": value.store, "item_id": value.core["item_id"],
        "expected_core_sha256": authority.digest(value.core),
        "expected_record_sha256": authority.digest(value.origin)}
    prefix = permanent_files(value)
    with upgraded(value, monkeypatch):
        result = intake.install_legacy_origin_reference17_locked(**arguments)
        assert result == {"core": value.core, "origin_ref": authority.record_ref(value.origin)}
        before_replay = snapshot(value)
        assert intake.install_legacy_origin_reference17_locked(**arguments) == result
        assert snapshot(value) == before_replay
    assert permanent_files(value) == prefix


def test_original_ref_replay_preserves_bytes_mtime_and_all_authority_slots(legacy16, monkeypatch):
    value = legacy16
    with upgraded(value, monkeypatch):
        with select(value) as captured:
            install(captured)
        before = snapshot(value)
        with select(value) as replay:
            expected_before, after = replay.expected_state_effect()
            assert expected_before == after == before[0]
            replay.verify_source()
        assert snapshot(value) == before


def test_old_budget_pending_is_kept_without_manufacturing_control_authority(legacy16, monkeypatch):
    value = legacy16
    request = old_budget_request(value.core)
    request["source"] = deepcopy(value.source)
    request["claim"] = deepcopy(value.store.read()["claims"][0]["claim"])
    value.store.install_budget_defer_request(request)
    pending = deepcopy(value.store.read()["budget_pending"])
    with upgraded(value, monkeypatch), select(value) as captured:
        before, after = captured.expected_state_effect()
        assert before["budget_pending"] == after["budget_pending"] == pending
        assert after["budget_pending"]["defer_request"]["source"]["handshake"]["migration_version"] == 16
        assert after["source_authority"]["defer"] is None
        assert install(captured)["budget_pending"] == pending


def test_paused_expired_sql_job_is_original_identity_not_fresh_permission(legacy16, monkeypatch):
    value = legacy16
    with storage_admin.connection(value.profile) as con:
        con.execute("UPDATE job SET lease_until=0,manual_paused=1 WHERE job_id=?", (value.core["root_job_id"],))
        con.execute("UPDATE worker_lease SET owner_id=NULL,lease_until=0")
        con.commit()
    with upgraded(value, monkeypatch), select(value) as captured:
        assert worker.execution_context() is None
        before, after = captured.expected_state_effect()
        assert before["source_execution"] == after["source_execution"] == value.core
        install(captured)
        with sql.connection(value.profile) as con:
            row = con.execute("SELECT lease_until,manual_paused FROM job WHERE job_id=?", (value.core["root_job_id"],)).fetchone()
            assert tuple(row) == (0, 1)


@pytest.mark.parametrize("changed", ["bool_item", "wrong_item", "core_sha", "record_sha", "dto_store"])
def test_selector_only_factory_refuses_dtos_and_changed_originals(legacy16, monkeypatch, changed):
    value = legacy16
    arguments = {"bool_item": {"item_id": True}, "wrong_item": {"item_id": value.core["item_id"] + 1},
        "core_sha": {"expected_core_sha256": "f" * 64}, "record_sha": {"expected_record_sha256": "f" * 64},
        "dto_store": {"store": deepcopy(value.store.read())}}[changed]
    with upgraded(value, monkeypatch):
        before = snapshot(value)
        with pytest.raises(SyncError):
            with select(value, **arguments):
                pytest.fail("Changed selector must not produce a capture")
        assert snapshot(value) == before


@pytest.mark.parametrize("changed", ["job_lineage", "peer", "origin", "source_url"])
def test_original_live_facts_are_self_read_and_never_reconstructed(legacy16, monkeypatch, changed):
    value = legacy16
    if changed == "job_lineage":
        with storage_admin.connection(value.profile) as con:
            con.execute("UPDATE job SET continuation_id=? WHERE job_id=?", (str(uuid.uuid4()), value.core["root_job_id"]))
            con.commit()
    elif changed == "peer":
        from reno import config
        config.set_local({"sync_target_node_id": str(uuid.uuid4())})
    elif changed == "origin":
        origin_records(value)[0][0].unlink()
    else:
        claims = value.store.read()["claims"]
        claims[0]["url"] = "https://www.bilibili.com/video/BVchangedIntake"
        value.store.update({"claims": claims})
    with upgraded(value, monkeypatch):
        before = snapshot(value)
        with pytest.raises((SyncError, ValueError, sqlite3.DatabaseError)):
            with select(value):
                pytest.fail("Changed original facts must not produce a capture")
        assert snapshot(value) == before


def test_factory_cannot_be_entered_from_naked_actual_writer(legacy16, monkeypatch):
    value = legacy16
    with upgraded(value, monkeypatch), value.store.reference_operation(), sql.connection(value.profile, write=True):
        with pytest.raises(SyncError, match="Original source origin"):
            with select(value):
                pytest.fail("A caller writer must not be a capture factory")


def test_capture_cannot_be_publicly_constructed_or_used_in_copied_thread(legacy16, monkeypatch):
    value = legacy16
    with pytest.raises(SyncError):
        intake.LegacyOriginIntakeCapture17(seal=object())
    with pytest.raises(SyncError):
        intake.require_legacy_origin_intake17(value.proof)
    with upgraded(value, monkeypatch), select(value) as captured:
        copied = copy_context()
        def foreign_context():
            with pytest.raises(SyncError):
                intake.require_legacy_origin_intake17(captured)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(copied.run, foreign_context).result()
        captured.verify_source()


def test_live_capture_refuses_mutated_effect_and_rolls_back_without_state_write(legacy16, monkeypatch):
    value = legacy16
    with upgraded(value, monkeypatch):
        before = snapshot(value)
        with pytest.raises(SyncError):
            with select(value) as captured:
                after = json.loads(captured._after_json)
                after["claims"][0]["attempts"] += 1
                captured._after_json = execution._canonical(after)
                captured.verify_source()
        assert snapshot(value) == before


def restore_source16(value):
    identifier = str(uuid.uuid4())
    sources.create_checkpoint(profile=value.profile, source_id=identifier)
    # Unrelated, real isolated knowledge mutation after the actual checkpoint.
    add_video("origin_intake_later")
    return identifier


def test_actual_bare16_restore_is_historical_proof_not_trusted17_certificate(legacy16, monkeypatch):
    value = legacy16
    source_id = restore_source16(value)
    view = old_restore.preview(profile=value.profile, source_id=source_id)
    operation_id = str(uuid.uuid4())
    old_restore.apply(profile=value.profile, source_id=source_id, expected_digest=view["digest"], operation_id=operation_id)
    assert value.store.read()["local_epoch"] == value.core["local_epoch"] + 1
    original_files = permanent_files(value)
    with upgraded(value, monkeypatch), select(value) as captured:
        assert captured.preservation_capture is None
        assert captured._schema.rows[0][1].wrapper is None
        assert captured._schema.rows[0][0]["operation_id"] == operation_id
        assert captured._pins.files  # Actual intent/catalog/descriptor/image pins.
        assert install(captured)["source_execution"] == value.core
        with value.store.reference_operation(), pytest.raises(runtime.RuntimeRestoreError, match="TRUSTED17_PRODUCER_REQUIRED"):
            with runtime.capture_completed_locked(profile=value.profile, store=value.store, operation_id=operation_id):
                pytest.fail("Bare16 receipt must not become a trusted17 restore")
    assert permanent_files(value) == original_files


def test_actual_modern_restore_of_old_core_owns_real_completed_capture_and_origin_only_effect(legacy16, monkeypatch):
    value = legacy16
    source_id = restore_source16(value)
    with upgraded(value, monkeypatch):
        view = restore17.preview(profile=value.profile, source_id=source_id)
        operation_id = str(uuid.uuid4())
        restore17.apply(profile=value.profile, source_id=source_id, expected_digest=view["digest"], operation_id=operation_id)
        before = snapshot(value)
        with select(value) as captured:
            assert type(captured.preservation_capture) is runtime.CompletedRestoreCapture17
            assert captured.preservation_capture._selected["operation_id"] == operation_id
            expected_before, after = captured.expected_state_effect()
            assert expected_before == before[0]
            for name in ("claims", "lease", "recovery", "budget_pending", "source_execution"):
                assert after[name] == expected_before[name]
            install(captured)  # Target until exact runtime State effect is wired.
            captured.preservation_capture.verify_source()
        assert permanent_files(value) == before[2]


def test_actual_legacy_recovery_pending_requires_original_uuid_bridge_without_clearing(legacy16, monkeypatch):
    """Explicit current boundary, not a claim of complete oldpending support."""
    value = legacy16
    value.store.enable_source_readmit()
    # Actual requests cannot be produced here without the separate released
    # readmit producer. Do not fabricate one merely to prove positive intake.
    # The shape-only branch is therefore checked on a copy, independently of
    # all original-file/capture positive tests above, and grants no capability.
    shape = deepcopy(value.store.read())
    shape["source_recovery_pending"] = {"format_version": 1, "kind": "source_readmit",
        "request_id": str(uuid.uuid4()), "request_sha256": "a" * 64,
        "core_sha256": authority.digest(value.core), "intent_sha256": "b" * 64}
    before = snapshot(value)
    with pytest.raises(SyncError, match="Original source origin") as caught:
        intake._after(shape, value.origin, None, {})
    assert caught.value.code.endswith("ORIGINAL_RECOVERY_PENDING_BRIDGE_REQUIRED")
    assert snapshot(value) == before
