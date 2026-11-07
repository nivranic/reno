"""Actual SQL/owned State/permanent files, never caller DTO witness authority."""
from contextlib import contextmanager
from contextvars import copy_context
from copy import copy
from dataclasses import replace
import hashlib
import json
import os
import sqlite3
import threading
import uuid

import pytest

from reno import db, migrations, storage_lifecycle as lifecycle, sync_state
from reno import sync_source_authority as authority, sync_source_continuity as continuity
from reno import sync_source_readmit_schema as readmit_schema
from reno import source_preservation_witness as witness
from reno.sync_protocol import SyncError
from tests.test_source_preservation import released16_environment
from tests.test_source_preservation_v17 import env, build_historical17_environment, schema17
from tests import test_storage_source_authority_roots as home_files
from tests import test_sync_source_readmit_contract as sample
from tests import test_sync_source_readmit_schema as sql_records


def prepare17(env):
    # All prospective DDL is actually committed in this isolated database
    # before the private producer opens its own connection. Global stays16.
    with schema17(env) as con:
        con.commit()


@contextmanager
def window(env, *, writable=True, prepare=True):
    if prepare:
        prepare17(env)
    with env.store.reference_operation():
        with witness.sql_window_locked(profile=env.profile, store=env.store, writable=writable) as con:
            yield con


def snapshot(env):
    sqlite_files = {env.profile.database.name + suffix for suffix in ("", "-wal", "-shm")}
    # Windows live SHM/byte-lock handles cannot be read with pathlib. SQL
    # facts are checked through the actual connection; lock existence/size
    # still participates without attempting to read its locked byte.
    return {str(path.relative_to(env.profile.database.parent.parent)):
                (None if path.name.endswith(".lock") else hashlib.sha256(path.read_bytes()).hexdigest(),
                 path.stat().st_size, path.stat().st_mtime_ns)
            for root in (env.profile.database.parent, env.profile.checkpoints)
            for path in root.rglob("*") if path.is_file() and path.name not in sqlite_files}


def install_core(env):
    env.store.enable_source_execution()
    state = env.store.read()
    stream = {"library_id": state["binding"]["target_library_id"], "library_epoch": 1}
    env.store.update({"recovery": {"generation": 0, "revision": 0, "state": "running",
        "source_floor": None, "stream": stream}})
    core = {"format_version": 1, "binding": state["binding"], "item_id": 7,
        "submitted_url_sha256": "a" * 64, "root_job_id": env.root,
        "continuation_id": env.job["continuation_id"], "execution_generation": 1,
        "local_epoch": 1, "recovery_generation": 0, "recovery_stream": stream,
        "resolution_input_sha256": "b" * 64}
    env.store.install_source_execution(core)
    env.store.enable_source_readmit()
    return core


def test_actual_schema17_two_absent_facilities_are_pinned_without_initialization(env):
    with window(env) as con:
        before = snapshot(env)
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            capsules = value.capsules()
            assert set(capsules) == {"cloud_continuity", "home_authority"}
            assert all(item["presence"] == "absent" for item in capsules.values())
            assert value.references() == {"cloud_continuity": [], "home_authority": []}
            observed = value.state_observation()
            assert observed["present"] and observed["state_format_version"] == 1
            assert observed["authority_projection"] is None
            assert "target_url" not in json.dumps(observed)
        assert snapshot(env) == before and con.in_transaction


def test_original_witness_cannot_survive_actual_sql_transaction_replacement(env):
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
        with window(env) as con:
            with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
                con.execute("COMMIT")
                con.execute("BEGIN IMMEDIATE")
                value.capsules()


def test_actual_state5_permanent_ref_cannot_turn_missing_authority_into_absence(env):
    core = install_core(env)
    state = env.store.read()
    state["source_authority"] = {"format_version": 1,
        "core_sha256": sync_state._source_digest(core), "origin": {"format_version": 1,
            "record_kind": "origin", "record_id": str(uuid.uuid4()), "record_sha256": "c" * 64},
        "defer": None, "resume": None, "failure": None, "publication": None}
    # Actual typed historical-State fixture with its referenced permanent
    # files lost; this is not a caller DTO offered to the factory.
    with env.store._mutating() as directory:
        sync_state._write(directory, sync_state._STATE, sync_state._validate(state))
    prepare17(env)
    before = snapshot(env)
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_INCOMPLETE"):
        with window(env, prepare=False) as con:
            with witness.capture_locked(con, profile=env.profile, store=env.store):
                pass
    assert snapshot(env) == before


@pytest.mark.skipif(os.name != "nt", reason="Windows creation ctime permits this actual same-signature fixture")
def test_same_signature_state_body_change_is_rejected_when_sql_window_exits(env):
    prepare17(env)
    path = env.profile.database.parent / sync_state._STATE
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_STATE_CHANGED"):
        with window(env, prepare=False) as con:
            with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
                previous = path.read_bytes()
                changed = previous.replace(b'"seeded":false', b'"seeded":true ')
                assert previous != changed and len(changed) == len(previous)
                signature = witness._state_signature(env.store)
                stat = path.stat()
                path.write_bytes(changed)
                os.utime(path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
                assert witness._state_signature(env.store) == signature
                assert env.store.read()["seeded"] is True
                observed = value.state_observation()
                # Short proofs may still expose the original sealed observation
                # inside the writer; full exit must detect changed raw content.
                assert observed["raw_sha256"] == hashlib.sha256(previous).hexdigest()


def test_caller_row_factory_cannot_use_overloaded_equality_to_enter_actual_sql_gate(env):
    class CallerFactory:
        def __eq__(self, other):
            return True

        def __call__(self, cursor, row):
            return sqlite3.Row(cursor, row)

    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
        with window(env) as con:
            with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
                con.row_factory = CallerFactory()
                value.capsules()


@pytest.mark.parametrize("ending", ["COMMIT", "ROLLBACK", "END", "; /* boundary */ COMMIT",
                                   "\ufeff; -- boundary\nROLLBACK"])
@pytest.mark.parametrize("verify_method", [True, False])
def test_original_transaction_generation_is_required_at_method_and_context_exit(env, ending, verify_method):
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
        with window(env) as con:
            with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
                con.execute(ending)
                con.execute("; /* new transaction */ BEGIN IMMEDIATE")
                if verify_method:
                    value.verify_source()


def test_savepoint_rollback_does_not_replace_the_actual_owned_transaction(env):
    with window(env) as con:
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            con.execute("SAVEPOINT comparison")
            con.execute("ROLLBACK TRANSACTION TO comparison")
            con.execute("RELEASE comparison")
            value.verify_source()


def test_copy_closed_and_wrong_owner_captures_are_never_consumed(env):
    with window(env) as con:
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            assert witness.require_capture(value, connection=con, profile=env.profile, store=env.store) is value
            with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
                copy(value).capsules()
            for offered in ({"capsules": value.capsules()}, object()):
                with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_FACTORY_REQUIRED"):
                    witness.require_capture(offered, connection=con, profile=env.profile, store=env.store)
            foreign = replace(env.profile, reports=env.profile.reports.parent / "other-reports")
            with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_FACTORY_REQUIRED"):
                witness.require_capture(value, connection=con, profile=foreign, store=env.store)
            other = migrations._open(env.profile.database)
            try:
                with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_FACTORY_REQUIRED"):
                    witness.require_capture(value, connection=other, profile=env.profile, store=env.store)
            finally:
                other.close()
        with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
            value.verify_source()
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
        value.capsules()


def test_factory_constructor_cannot_accept_caller_seal_or_public_adapter_dtos():
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_FACTORY_REQUIRED"):
        witness.WitnessCapture(seal=object(), profile=None, connection=None, store=None, ownership=None,
            guard=None, state={"present": False}, projection={}, cloud={"presence": "absent"},
            home={"presence": "absent"})


def test_copied_context_on_another_thread_cannot_borrow_actual_sql_or_state_ownership(env):
    with window(env) as con:
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            errors = []
            def use():
                try:
                    value.capsules()
                except BaseException as exc:
                    errors.append(exc)
            thread = threading.Thread(target=copy_context().run, args=(use,))
            thread.start()
            thread.join(timeout=3)
            assert not thread.is_alive() and len(errors) == 1
            assert type(errors[0]) is witness.WitnessCaptureError and errors[0].code == "SOURCE_WITNESS_OWNER_REQUIRED"
            value.verify_source()


def test_arbitrary_sql_and_nested_factory_windows_cannot_be_borrowed(env):
    with schema17(env) as con:
        with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
            with witness.capture_locked(con, profile=env.profile, store=env.store):
                pass
    with window(env) as con:
        with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
            with witness.sql_window_locked(profile=env.profile, store=env.store):
                pass
        with witness.capture_locked(con, profile=env.profile, store=env.store):
            with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_OWNER_REQUIRED"):
                with witness.capture_locked(con, profile=env.profile, store=env.store):
                    pass


def test_schema16_and_unowned_state_are_rejected_without_initialization(tmp_path, monkeypatch):
    # The schema16 negative has its own complete original16 lifetime.
    # It never borrows a current17 fixture or changes its version gate.
    with released16_environment(tmp_path / "released16", monkeypatch) as env:
        before = snapshot(env)
        with env.store.reference_operation():
            with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_SCHEMA_REQUIRED"):
                with witness.sql_window_locked(profile=env.profile, store=env.store):
                    pass
        assert snapshot(env) == before
    # The independent unowned-State negative consumes historical17-v1
    # under real current17 after every old producer owner has closed.
    env = build_historical17_environment(tmp_path / "historical17-unowned", monkeypatch)
    prepare17(env)
    with lifecycle.shared(env.profile):
        with pytest.raises(SyncError) as error:
            with witness.sql_window_locked(profile=env.profile, store=env.store):
                pass
        assert error.value.code == "SYNC_SOURCE_AUTHORITY_LOCK_REQUIRED"


@pytest.mark.parametrize("method", ["state", "state_bytes", "public_sql"])
def test_real_state_or_public_sql_drift_is_rejected_and_never_reconciled(env, method):
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_(STATE|SQL)_CHANGED"):
        with window(env) as con:
            with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
                if method == "state":
                    env.store.update({"seeded": True})
                elif method == "state_bytes":
                    path = env.profile.database.parent / sync_state._STATE
                    path.write_bytes(path.read_bytes() + b" ")
                else:
                    row = sql_records.admission()
                    sql_records.insert(con, readmit_schema.ADMISSION, row)
                value.verify_source()


@pytest.mark.parametrize("kind", ["cloud_tree", "cloud_head", "home_tree", "home_head"])
def test_actual_partial_permanent_facility_is_unknown_and_cannot_mint_a_witness(env, kind):
    prepare17(env)
    if kind == "cloud_tree":
        (env.profile.database.parent / continuity.DIRECTORY).mkdir()
    elif kind == "home_tree":
        (env.profile.database.parent / authority.DIRECTORY).mkdir()
    else:
        name = continuity.DB_HEAD_NAME if kind == "cloud_head" else authority.DB_HEAD_NAME
        (env.profile.database.parent / name).write_bytes(b"{}")
    before = snapshot(env)
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_INCOMPLETE"):
        with window(env, prepare=False):
            pass
    assert snapshot(env) == before


def test_stateless_actual_absence_needs_no_binding_and_does_not_create_state(env):
    prepare17(env)
    path = env.profile.database.parent / sync_state._STATE
    path.unlink()
    before = snapshot(env)
    with window(env, prepare=False) as con:
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            assert value.capsules()["home_authority"]["binding"] is None
            assert all(item["presence"] == "absent" for item in value.capsules().values())
            assert value.state_observation() == {"present": False, "byte_size": 0, "raw_sha256": None,
                "canonical_sha256": None, "state_format_version": None, "authority_projection": None}
    assert not path.exists() and snapshot(env) == before


def test_valid_whole_state_larger_than_one_permanent_record_keeps_actual_hash_and_bounds(env):
    env.store.update({"uploaded_media": {"large_media_" + str(number): {
        "video_id": "large_media_" + str(number), "sha256": "a" * 64,
        "byte_size": 1, "extension": "mp4"} for number in range(600)}})
    raw = (env.profile.database.parent / sync_state._STATE).read_bytes()
    assert 64 * 1024 < len(raw) < sync_state.MAX_STATE_BYTES
    with window(env) as con:
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            observed = value.state_observation()
            assert observed["byte_size"] == len(raw) and observed["raw_sha256"] == hashlib.sha256(raw).hexdigest()
            assert "large_media_" not in json.dumps(observed)


def seed_origin(env, core):
    active = home_files.activate(env.store)
    core_sha = authority.digest(core)
    record = {"format_version": 1, "record_kind": "origin",
        "record_id": str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + core_sha)),
        "binding": core["binding"], "core": core, "core_sha256": core_sha,
        "claim": {"item_id": core["item_id"], "lease_token": str(uuid.uuid4()), "lease_generation": 1},
        "created_at": sample.COMMITTED}
    authority.validate_record("origins", record, active)
    home_files.seed(env.store, (("origins", record),))
    return record


def historical_state(env, changes):
    # Closed actual on-disk history/restoration fixture, never producer input.
    state = env.store.read()
    state.update(changes)
    with env.store._mutating() as directory:
        sync_state._write(directory, sync_state._STATE, sync_state._validate(state))


def authority_state(core, **slots):
    return {"format_version": 1, "core_sha256": authority.digest(core),
            **dict.fromkeys(("origin", "defer", "resume", "failure", "publication")), **slots}


def test_actual_committed_origin_ref_is_closed_against_whole_healthy_home_record_set(env):
    core = install_core(env)
    origin = seed_origin(env, core)
    historical_state(env, {"source_authority": authority_state(core, origin=authority.record_ref(origin))})
    with window(env) as con:
        before = snapshot(env)
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            assert value.capsules()["home_authority"]["presence"] == "present"
            assert value.capsules()["home_authority"]["record_count"] == 1
            assert value.state_observation()["authority_projection"]["source_authority"]["origin"] == authority.record_ref(origin)
            assert value.references()["home_authority"][0]["record_id"] == origin["record_id"]
        assert snapshot(env) == before


@pytest.mark.parametrize("slot", ["origin", "defer", "resume", "failure", "publication", "admission", "pending"])
def test_healthy_present_facility_cannot_hide_any_missing_current_state5_reference(env, slot):
    core = install_core(env)
    seed_origin(env, core)
    identifier, core_sha = str(uuid.uuid4()), authority.digest(core)
    if slot == "admission":
        changes = {"source_admission": {"format_version": 1, "admission_id": identifier,
            "core_sha256": core_sha, "request_sha256": "a" * 64,
            "admission_sha256": "b" * 64, "record_sha256": "c" * 64}}
    elif slot == "pending":
        changes = {"source_recovery_pending": {"format_version": 1, "kind": "source_dispose",
            "request_id": identifier, "request_sha256": "a" * 64,
            "core_sha256": core_sha, "intent_sha256": "b" * 64}}
    else:
        ref = {"format_version": 1, "record_kind": "origin" if slot == "origin" else "publication" if slot == "publication" else "control",
               "record_id": identifier, "record_sha256": "c" * 64}
        changes = {"source_authority": authority_state(core, **{slot: ref})}
    historical_state(env, changes)
    prepare17(env)
    before = snapshot(env)
    with pytest.raises(witness.WitnessCaptureError, match="SOURCE_WITNESS_INCOMPLETE"):
        with window(env, prepare=False):
            pass
    assert snapshot(env) == before


def test_writer_verification_never_rehashes_immutable_home_files_or_state_body(env, monkeypatch):
    core = install_core(env)
    origin = seed_origin(env, core)
    historical_state(env, {"source_authority": authority_state(core, origin=authority.record_ref(origin))})
    actual_rehash = home_files.roots._Reader._rehash
    actual_state_read = sync_state._read_bytes
    observed = {"immutable_full_outside": 0, "state_full_outside": 0}

    def rehash(reader, stream, size, *, payload=False):
        immutable = any(held is stream for _directory, _name, held, _before, _opened, _digest in reader.files)
        if immutable:
            assert witness._WINDOW.get() is None, "immutable body read inside actual SQL producer window"
            observed["immutable_full_outside"] += 1
        return actual_rehash(reader, stream, size, payload=payload)

    def state_read(directory, name, maximum, **kwargs):
        if name == sync_state._STATE:
            assert witness._WINDOW.get() is None, "full State read inside actual SQL producer window"
            observed["state_full_outside"] += 1
        return actual_state_read(directory, name, maximum, **kwargs)

    monkeypatch.setattr(home_files.roots._Reader, "_rehash", rehash)
    monkeypatch.setattr(sync_state, "_read_bytes", state_read)
    with window(env) as con:
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            value.capsules()
            value.references()
            value.state_observation()
            value.verify_source()
    assert observed["immutable_full_outside"] > 0 and observed["state_full_outside"] > 0


def test_actual_whole_public_sql_larger_than_record_cap_is_bound_to_all_cloud_records(env):
    prepare17(env)
    active = sql_records.activation()
    contract = json.loads(active["contract_json"])
    contract["owner_profile_sha256"] = continuity.profile_sha256(env.profile)
    active["owner_profile_sha256"] = contract["owner_profile_sha256"]
    sql_records.set_json(active, "contract", contract)
    records = [sql_records.registration(active=active) for _ in range(96)]
    # Exact actual historical permanent files and actual matching SQL rows;
    # this uses no network/provider and is not a fresh source-creation grant.
    with lifecycle.shared(env.profile), continuity.locked(env.profile, initialize=True) as guard:
        guard.activate_files(contract)
    # A historical fixture can contain many separate completed operations;
    # forcing them through one live 10s producer lifetime is not that history.
    # Preserve the real bound instead of extending the writer's deadline.
    ledger = env.profile.database.parent / continuity.DIRECTORY
    previous, ordinal = continuity._digest(contract), 0
    for row in records:
        result = json.loads(row["result_json"])
        continuity._record("registrations", result, contract)
        record_path = ledger / "registrations" / (result["original_request_id"] + ".json")
        record_path.write_bytes(continuity._encoded(result))
        if os.name != "nt":
            record_path.chmod(0o600)
        for state in ("reserved", "committed"):
            ordinal += 1
            event = {"format_version": 1, "continuity_id": contract["continuity_id"],
                "ordinal": ordinal, "previous_sha256": previous, "kind": "registrations",
                "key": result["original_request_id"], "record_sha256": continuity._digest(result),
                "record": result, "state": state}
            path = ledger / "journal" / f"{ordinal:08d}.json"
            path.write_bytes(continuity._encoded(event))
            if os.name != "nt":
                path.chmod(0o600)
            previous = continuity._digest(event)
    head = {"format_version": 1, "continuity_id": contract["continuity_id"], "ordinal": ordinal, "sha256": previous}
    for path in (env.profile.database.parent / continuity.DB_HEAD_NAME,
                 env.profile.checkpoints / continuity.CHECKPOINT_HEAD_NAME):
        path.write_bytes(continuity._encoded(head))
    with env.store.reference_operation():
        con = migrations._open(env.profile.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            sql_records.insert(con, readmit_schema.ACTIVATION, active)
            for row in records:
                sql_records.insert(con, readmit_schema.REGISTRATION, row)
            con.commit()
        finally:
            con.close()
    with window(env, prepare=False) as con:
        projection = witness.inputs.read_continuity_projection(con)
        assert len(json.dumps(projection, sort_keys=True).encode()) > 64 * 1024
        with witness.capture_locked(con, profile=env.profile, store=env.store) as value:
            capsule = value.capsules()["cloud_continuity"]
            assert capsule["presence"] == "present" and capsule["record_count"] == len(records)
            assert capsule["journal_count"] == 2 * len(records)
            refs = value.references()["cloud_continuity"]
            permanent = [row for row in refs if row["record_kind"] == "registrations"]
            assert len(permanent) == len(records)
            assert {row["record_id"] for row in permanent} == {row["original_request_id"] for row in records}
