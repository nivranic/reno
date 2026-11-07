"""Actual isolated SQL/State/outboxes/facilities; no provider or transport."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import source_preservation as legacy, source_preservation_v17 as sql
from reno import source_preservation_certificate17 as certificate
from reno import source_preservation_witness as permanent, sync_state
from reno import sync_source_readmit_schema as schema
from reno import storage_admin, sync_source_continuity_service as cloud_service
from tests.test_source_preservation import install_outbox
from tests.test_source_preservation_v17 import env, historical17_paid_environment
from tests.test_source_preservation_witness import install_core, window
from tests import test_storage_source_authority_roots as home_files
from tests import test_sync_source_continuity_service as cloud_fixture
from tests import test_sync_source_readmit_contract as sample, test_sync_source_readmit_schema as sql_records
from tests.test_sync_source_continuity_service import env as cloud_environment


@contextmanager
def captured(env, *, root=True):
    with window(env) as con:
        with permanent.capture_locked(con, profile=env.profile, store=env.store) as witness:
            yield con, witness, certificate.capture(con, profile=env.profile, store=env.store,
                root_job_id=env.root if root else None, witness=witness)


def test_complete17_preserves_paid_unknown_full_sql_and_actual_state(historical17_paid_environment):
    env = historical17_paid_environment
    paid, unknown = env.historical_paid_unknown
    with captured(env) as (con, witness, before):
        document = before.public()
        assert document["format_version"] == 2 and document["schema_version"] == "17"
        assert document["scope"] == "local_capture_comparison" and document["continuity"] == "not_established"
        assert {row["name"] for row in document["tables"]} == set(legacy.PROJECTIONS_V17)
        assert all(row["rows"] == 0 for row in document["tables"] if row["name"] in schema.COLUMNS)
        attempts = {row["attempt_id"]: row for op in document["frontier"]["operations"] for row in op["attempts"]}
        assert attempts[paid.attempt_id]["state"] == "settled"
        assert attempts[unknown.attempt_id]["state"] == "dispatching"
        actual_bytes = (env.store.data_dir / sync_state._STATE).read_bytes()
        assert document["checkpoint"]["state_sha256"] == hashlib.sha256(actual_bytes).hexdigest()
        assert set(document["permanent_witnesses"]) == {"cloud_continuity", "home_authority"}
        assert all(row["presence"] == "absent" for row in document["permanent_witnesses"].values())
        assert "artificial-private" not in before.document_json
        assert "synthetic.example.test" not in before.document_json
        assert certificate.verify_unchanged(con, before, profile=env.profile, store=env.store, witness=witness).unchanged


@pytest.mark.parametrize("version", [1, 2, 3, 4])
def test_actual_legacy_checkpoints_keep_exact_original_nine_keys_and_hashes(env, version):
    if version > 1:
        getattr(env.store, {2: "enable_no_resend", 3: "enable_source_execution", 4: "enable_source_control"}[version])()
    install_outbox(env)
    with env.store.reference_operation():
        state = env.store.read()
        original = legacy._checkpoint(env.store, env.root,
            state["binding"]["home_library_id"], state["binding"]["home_node_id"])
    with captured(env) as (_con, _witness, before):
        assert before.public()["checkpoint"] == original
        assert len(original) == 9 and original["state_format_version"] == version


def test_state5_exact_core_lineage_and_public_ref_set_are_bound(env):
    core = install_core(env)
    with captured(env) as (_con, _witness, before):
        document = before.public()
        assert document["checkpoint"]["state_format_version"] == 5
        assert document["checkpoint"]["source_execution_sha256"] == sync_state._source_digest(core)
        assert document["state_authority"]["source_core_sha256"] == sync_state._source_digest(core)
        assert document["state_authority"]["authority_ref_set_sha256"] == sync_state.source_readmit_projection(
            env.store.read())["refset_sha256"]
        assert document["checkpoint"]["source_execution_lineage"] == {key: core[key] for key in (
            "root_job_id", "continuation_id", "execution_generation")}


def test_private_sql_bytes_outside_selected_frontier_still_change_preservation(env):
    with captured(env) as (con, witness, before):
        con.execute("INSERT INTO meta(key,value) VALUES('private-certificate-probe',?)", (b"\x00\xffprivate",))
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            certificate.verify_unchanged(con, before, profile=env.profile, store=env.store, witness=witness)


def test_state_absence_keeps_actual_absence_and_all_sql_with_no_root(historical17_paid_environment):
    env = historical17_paid_environment
    paid, _unknown = env.historical_paid_unknown
    (env.store.data_dir / sync_state._STATE).unlink()
    with captured(env, root=False) as (_con, _witness, before):
        doc = before.public()
        assert doc["checkpoint"]["state_present"] is False and doc["checkpoint"]["outboxes"] == []
        assert doc["state_authority"]["state_present"] is False
        assert doc["state_authority"]["source_core_sha256"] is None
        assert doc["frontier"] == {"root_job_id": None, "root_job_row_sha256": None, "operations": []}
        assert next(row for row in doc["tables"] if row["name"] == "budget_attempt")["rows"] >= 2
        assert paid.attempt_id not in before.document_json


def test_complete_document_cannot_substitute_sql_only_or_caller_witness(env):
    with captured(env) as (con, witness, before):
        only_sql = sql.capture_sql(con, profile=env.profile, root_job_id=env.root)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            certificate.verify_unchanged(con, only_sql, profile=env.profile, store=env.store, witness=witness)
        with pytest.raises(permanent.WitnessCaptureError, match="SOURCE_WITNESS_FACTORY_REQUIRED"):
            certificate.capture(con, profile=env.profile, store=env.store, root_job_id=env.root,
                                witness=before.public()["permanent_witnesses"])
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            certificate.verify_unchanged(con, replace(before, sha256="0" * 64),
                                        profile=env.profile, store=env.store, witness=witness)


def test_unrecognized_sql_table_is_never_silently_dropped_from_complete17(env):
    with captured(env) as (con, witness, _before):
        con.execute("CREATE TABLE future_private_certificate17(value BLOB)")
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_SCHEMA_UNSUPPORTED"):
            certificate.capture(con, profile=env.profile, store=env.store, root_job_id=env.root, witness=witness)


def test_outbox_full_reads_and_state_full_reads_are_outside_private_sql_writer(env, monkeypatch):
    metadata = install_outbox(env)
    original_state = permanent._state
    original_full = permanent._CheckpointCache.verify_full
    calls = []

    def writer_is_available():
        # Probe the real SQLite writer reservation, rather than trusting the
        # factory's ContextVar alone. This contender changes no row.
        contender = sqlite3.connect(env.profile.database, timeout=0)
        try:
            contender.execute("BEGIN IMMEDIATE")
            contender.rollback()
        finally:
            contender.close()

    def actual_state(store):
        assert permanent._WINDOW.get() is None
        writer_is_available()
        calls.append("state-full")
        return original_state(store)

    def actual_outbox(value):
        assert permanent._WINDOW.get() is None
        writer_is_available()
        calls.append("outbox-full")
        return original_full(value)

    monkeypatch.setattr(permanent, "_state", actual_state)
    monkeypatch.setattr(permanent._CheckpointCache, "verify_full", actual_outbox)
    with captured(env) as (con, witness, before):
        assert before.public()["checkpoint"]["outboxes"][0]["sha256"] == metadata["sha256"]
        assert certificate.verify_unchanged(con, before, profile=env.profile, store=env.store, witness=witness).unchanged
    assert calls.count("state-full") >= 2 and calls.count("outbox-full") >= 2


def test_certificate_capacity_is_bounded_and_no_unknown_keys_are_accepted(env, monkeypatch):
    with captured(env) as (con, witness, before):
        unknown = before.public()
        unknown["invented_permission"] = True
        raw = legacy._encoded(unknown).decode("utf-8")
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            certificate.verify_unchanged(con, replace(before, document_json=raw,
                sha256=hashlib.sha256(raw.encode()).hexdigest()), profile=env.profile, store=env.store, witness=witness)
        monkeypatch.setattr(legacy, "MAX_CERTIFICATE_BYTES", len(before.document_json.encode()) - 1)
        with pytest.raises(legacy.PreservationError, match="SOURCE_PRESERVATION_LIMIT"):
            certificate.capture(con, profile=env.profile, store=env.store, root_job_id=env.root, witness=witness)


def test_live_witness_for_original_root_cannot_be_used_with_a_different_root(env):
    install_core(env)
    with captured(env) as (con, witness, _before):
        with pytest.raises(permanent.WitnessCaptureError, match="SOURCE_WITNESS_CHECKPOINT_INVALID"):
            certificate.capture(con, profile=env.profile, store=env.store, root_job_id=None, witness=witness)


def test_actual_present_facilities_and_all_four_nonempty_sql_tables_are_bound(cloud_environment, monkeypatch):
    other = cloud_environment
    store = sync_state.StateStore(other.profile.database.parent, profile=other.profile)
    store.initialize({"home_library_id": other.target["library_id"], "home_node_id": other.target["node_id"],
        "target_library_id": other.source["library_id"], "target_node_id": other.source["node_id"],
        "target_url": "https://synthetic-peer.example.test"}, 7)
    # Explicit stored historical facts, made with the actual permanent-file
    # writer. They are comparison fixtures, never a fresh Home source factory.
    home_files.activate(store)
    with monkeypatch.context() as scoped:
        scoped.setattr(sample, "SOURCE", {**other.target, "handshake": sample.SOURCE["handshake"]})
        scoped.setattr(sample, "TARGET", {key: other.source[key] for key in ("library_id", "node_id")})
        scoped.setattr(sample, "STREAM", {"library_id": other.source["library_id"], "library_epoch": 7})
        home_files.seed(store, [("controls", home_files.control()), ("publications", home_files.publication())])
    active = cloud_fixture.activated(other)
    request = cloud_fixture.register_request(other, active)
    registered = cloud_service.register(profile=other.profile, request=request, now=cloud_fixture.NOW)
    disposed = cloud_service.dispose(profile=other.profile,
        request=cloud_fixture.dispose_request(other, request["original_request"], registered), now=cloud_fixture.NOW)
    assert disposed["disposition"] == "not_applied"
    # A canonical already-stored synthetic admission tests full private SQL
    # preservation; insertion itself does not represent a runtime admission.
    body = cloud_fixture.bound(other, sample.readmit())
    admitted = sql_records.admission(body=body)
    with storage_admin.connection(other.profile) as con:
        sql_records.insert(con, schema.ADMISSION, schema.validate_record(schema.ADMISSION, admitted))
        con.commit()
    seen = set()
    with store.reference_operation():
        with permanent.sql_window_locked(profile=other.profile, store=store) as con:
            def audit(action, table, column, _database, _origin):
                if action == sqlite3.SQLITE_READ and table in schema.COLUMNS:
                    seen.add((table, column))
                return sqlite3.SQLITE_OK
            con.set_authorizer(audit)
            with permanent.capture_locked(con, profile=other.profile, store=store) as witness:
                before = certificate.capture(con, profile=other.profile, store=store, root_job_id=None, witness=witness)
                document = before.public()
                assert all(row["presence"] == "present" for row in document["permanent_witnesses"].values())
                assert document["permanent_witnesses"]["home_authority"]["record_count"] == 2
                assert document["permanent_witnesses"]["cloud_continuity"]["record_count"] == 2
                assert {row["name"]: row["rows"] for row in document["tables"] if row["name"] in schema.COLUMNS} == {
                    table: 1 for table in schema.COLUMNS}
                assert {(table, column) for table, columns in schema.COLUMNS.items() for column in columns} <= seen
                assert sample.LEASE["lease_token"] not in before.document_json
                assert "target_url" not in before.document_json
                assert certificate.verify_unchanged(con, before, profile=other.profile, store=store, witness=witness).unchanged


@pytest.mark.parametrize("stateless", [False, True])
def test_actual_node_file_cannot_change_after_complete_capture(env, stateless):
    if stateless:
        (env.store.data_dir / sync_state._STATE).unlink()
    with pytest.raises(permanent.WitnessCaptureError, match="SOURCE_WITNESS_IDENTITY_CHANGED"):
        with captured(env, root=not stateless) as (_con, witness, before):
            original = before.public()["node_id"]
            replacement = str(uuid.uuid4())
            assert replacement != original
            (env.store.data_dir / "node-identity.json").write_text(
                json.dumps({"instance_id": replacement}), encoding="utf-8")
            witness.verify_source()


def test_actual_sql_library_identity_cannot_change_after_complete_capture(env):
    with pytest.raises(permanent.WitnessCaptureError, match="SOURCE_WITNESS_IDENTITY_CHANGED"):
        with captured(env) as (con, witness, _before):
            con.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
            witness.verify_source()


def test_missing_node_identity_is_rejected_without_creating_a_substitute(env):
    node = env.store.data_dir / "node-identity.json"
    node.unlink()
    with pytest.raises(permanent.WitnessCaptureError, match="SOURCE_WITNESS_IDENTITY_INVALID"):
        with captured(env):
            pass
    assert not node.exists()
