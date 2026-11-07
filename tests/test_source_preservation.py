"""Preservation captures use actual isolated SQL/state and no adapter sends."""
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace
import uuid

import pytest

from reno import budget, budget_contract, config, db, home_recovery_schema, source_preservation as preservation
from reno import storage_admin, storage_lifecycle, sync_queue, sync_state, tasks
from tests.schema_fixtures import initialize_released16, released16


NOW = datetime(2026, 10, 2, 0, 0, tzinfo=timezone.utc)
PRIVATE = "artificial-private-source-and-paid-body"


def build_released16_environment(tmp_path):
    """Cold original16 producer; its caller owns the full old lifetime."""
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                               tmp_path / "reports", tmp_path / "checkpoints")
    initialize_released16(profile.database)
    storage_lifecycle.initialize(profile)
    root = tasks.submit_sync(profile=profile)
    with storage_admin.connection(profile) as con:
        library, node = sync_queue._local_identity(con)
        job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (root,)).fetchone())
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    store.initialize({"home_library_id": library, "home_node_id": node,
        "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()),
        "target_url": "https://synthetic.example.test"}, 1)
    return SimpleNamespace(profile=profile, store=store, root=root, job=job, library=library)



@contextmanager
def released16_environment(tmp_path, monkeypatch):
    # Keep every actual task/State/SQL/profile owner and its teardown inside
    # the original16 rules. This is not a yielding parent for modern17 users.
    with released16(monkeypatch):
        yield build_released16_environment(tmp_path)


@pytest.fixture
def env(tmp_path, monkeypatch):
    with released16_environment(tmp_path, monkeypatch) as value:
        yield value

@contextmanager
def transaction(env, *, immediate=True, released_v15=True):
    with env.store.reference_operation():
        con = db.connect(env.profile.database)
        try:
            con.execute("BEGIN IMMEDIATE" if immediate else "BEGIN")
            if released_v15:
                freeze_released_v15(con)
            yield con
        finally:
            con.set_authorizer(None)
            con.set_trace_callback(None)
            con.rollback()
            con.close()


def freeze_released_v15(con):
    """Keep the released15 cases real after ordinary initialization moves to16.

    This is isolated fixture DDL in a transaction opened through the current
    migration gate, never an ordinary connection bypass or production upgrade.
    """
    version = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0]
    if version == "15":
        assert not home_recovery_schema.preflight(con, require_absent=True)
        return
    assert version == "16"
    home_recovery_schema.validate(con)
    assert con.execute("SELECT COUNT(*) FROM " + home_recovery_schema.TABLE).fetchone()[0] == 0
    # SQLite drops all table-owned indexes/triggers with this actual table;
    # preflight below independently proves the entire future namespace absent.
    con.execute('DROP TABLE "' + home_recovery_schema.TABLE + '"')
    con.execute("UPDATE meta SET value='15' WHERE key='migration_version'")
    assert not home_recovery_schema.preflight(con, require_absent=True)


def capture(env, con):
    return preservation.capture(con, profile=env.profile, store=env.store, root_job_id=env.root)


def reserve(env, kind="model", *, generation=1, key=None):
    sha = hashlib.sha256((kind + str(generation)).encode()).hexdigest()
    operation = budget.OperationSpec(env.job["continuation_id"], generation, kind,
        key or "outside-home:consumer:" + kind, sha, env.root)
    arguments = {"adapter": "paas_v4", "effective_max_tokens": 100} if kind == "model" else (
        {"resolver_contract_version": 1, "resolver_phase": "dns", "resolver_max_attempts": config.source_resolution_attempt_limit()}
        if kind == "source_resolution" else {"source_contract_version": 1,
            **({"download_segment": "video"} if kind == "download" else {})})
    attempt = budget.AttemptSpec(sha, **arguments)
    policy = budget_contract.parse_policy({"budget_enabled": True,
        "download_interval_min_seconds": 0, "download_interval_max_seconds": 0})
    result = budget.reserve(operation, attempt, profile=env.profile, policy=policy, now=NOW)
    return result, sha, policy


def dispatch(env, reserved, policy):
    assert budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256,
        str(uuid.uuid4()), expected_revision=reserved.revision, profile=env.profile,
        policy=policy, now=NOW).won


def seed(env):
    paid, request, policy = reserve(env)
    dispatch(env, paid, policy)
    budget.settle(paid.attempt_id, request, profile=env.profile, result_text=PRIVATE, now=NOW)
    unknown, _, policy = reserve(env, "source_metadata", key="home:old:metadata")
    dispatch(env, unknown, policy)
    for kind in ("download", "source_enumeration", "source_resolution"):
        reserve(env, kind)
    with storage_admin.connection(env.profile) as con:
        con.execute("BEGIN IMMEDIATE")
        con.execute("UPDATE job SET execution_generation=2,wait_mode='blocked',"
                    "blocked_reason='indeterminate',manual_paused=1 WHERE job_id=?", (env.root,))
        con.execute("INSERT INTO meta(key,value) VALUES(?,?)",
            ("budget:manual:execution:preserve-private-checkpoint", PRIVATE.encode()))
        con.execute("UPDATE meta SET value=? WHERE key='default_project_id'", (PRIVATE,))
        con.commit()
    reserve(env, generation=2)
    return paid, unknown


def test_all_root_kinds_namespaces_generations_and_private_bytes_are_preserved(env):
    paid, unknown = seed(env)
    with transaction(env) as con:
        before = capture(env, con)
        document = before.public()
        assert document["schema_version"] == "15"
        assert home_recovery_schema.TABLE not in {row["name"] for row in document["tables"]}
        operations = document["frontier"]["operations"]
        assert len(operations) == 6
        assert {row["kind"] for row in operations} == set(budget.KINDS)
        assert {row["execution_generation"] for row in operations} == {1, 2}
        attempts = {a["attempt_id"]: a for row in operations for a in row["attempts"]}
        assert attempts[paid.attempt_id]["state"] == "settled"
        assert attempts[unknown.attempt_id]["state"] == "dispatching"
        assert PRIVATE not in before.document_json
        assert "outside-home" not in before.document_json
        assert document["continuity"] == "not_established"
        assert document["scope"] == "local_capture_comparison"
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        assert con.in_transaction


def test_only_knowledge_step_and_epoch_revision_can_change(env):
    seed(env)
    with transaction(env) as con:
        before = capture(env, con)
        con.execute("INSERT INTO meta(key,value) VALUES('step:isolated:atomize',?)", (PRIVATE.encode(),))
        con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key IN ('library_epoch','kb_revision')")
        result = preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)
        assert result.unchanged and result.before_sha256 != result.after_sha256


@pytest.mark.parametrize("statement,parameters", [
    ("UPDATE job SET manual_paused=0 WHERE job_id=?", "root"),
    ("UPDATE task_request SET payload_signature='changed'", ()),
    ("UPDATE meta SET value=? WHERE key='default_project_id'", (b"changed",)),
    ("UPDATE meta SET value=? WHERE key='budget:manual:execution:preserve-private-checkpoint'", (b"changed",)),
    ("UPDATE worker_lease SET lease_until=lease_until+1", ()),
])
def test_operational_mutations_cannot_pass_a_certificate(env, statement, parameters):
    seed(env)
    with transaction(env) as con:
        # Give the worker table an actual persistent row before capture.
        con.execute("INSERT OR IGNORE INTO worker_lease(scope,lease_until) VALUES('worker',1)")
        before = capture(env, con)
        con.execute(statement, (env.root,) if parameters == "root" else parameters)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)
        assert con.in_transaction


def test_binary_meta_is_typed_and_never_exported(env):
    with transaction(env) as con:
        con.execute("INSERT INTO meta(key,value) VALUES('opaque-private',?)", (b"\x00\xffprivate",))
        before = capture(env, con)
        con.execute("UPDATE meta SET value=? WHERE key='opaque-private'", ("00ffprivate",))
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)
        assert "private" not in before.document_json


def test_original_budget_integrity_validator_checks_paid_body(env):
    paid, _ = seed(env)
    with transaction(env) as con:
        con.execute("UPDATE budget_attempt SET result_text=? WHERE attempt_id=?", ("x" * len(PRIVATE), paid.attempt_id))
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_LEDGER_INVALID"):
            capture(env, con)
        assert con.in_transaction


def test_missing_original_rows_do_not_prove_never_dispatched(env):
    paid, _ = seed(env)
    with transaction(env) as con:
        before = capture(env, con)
        con.execute("DELETE FROM budget_attempt WHERE operation_id=?", (paid.operation_id,))
        con.execute("DELETE FROM budget_operation WHERE operation_id=?", (paid.operation_id,))
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)
        current = capture(env, con).public()
        assert current["continuity"] == "not_established"
        assert len(current["frontier"]["operations"]) == 5
        assert "never_dispatched" not in json.dumps(current)


def test_empty_frontier_is_an_observation_without_dispatch_authority(env):
    with transaction(env) as con:
        value = capture(env, con).public()
        assert value["frontier"]["operations"] == []
        assert value["continuity"] == "not_established"


@pytest.mark.parametrize("ddl", ["CREATE TABLE unknown_operational(secret BLOB)",
                               "ALTER TABLE task_request ADD COLUMN unknown_private BLOB"])
def test_unknown_tables_or_columns_cannot_be_omitted(env, ddl):
    with transaction(env) as con:
        con.execute(ddl)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_SCHEMA_UNSUPPORTED"):
            capture(env, con)


def test_actual_profile_state_ownership_and_existing_transaction_are_required(env):
    with storage_admin.connection(env.profile) as con:
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_TRANSACTION_REQUIRED"):
            capture(env, con)
        con.execute("BEGIN")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_STATE_OWNERSHIP_REQUIRED"):
            capture(env, con)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHECKPOINT_INVALID"):
            preservation.capture(con, profile=env.profile, store=env.store.read(), root_job_id=env.root)
    with transaction(env) as con:
        wrong = replace(env.profile, media=env.profile.media.parent / "wrong-media")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_PROFILE_MISMATCH"):
            preservation.capture(con, profile=wrong, store=env.store, root_job_id=env.root)


def test_module_only_reads_and_does_not_claim_to_prove_immediate_writer(env):
    with transaction(env, immediate=False) as con:
        prohibited = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
            sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_TRANSACTION}
        statements = []
        con.set_authorizer(lambda action, *_: sqlite3.SQLITE_DENY if action in prohibited else sqlite3.SQLITE_OK)
        con.set_trace_callback(statements.append)
        before = capture(env, con)
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        con.set_authorizer(None)
        con.set_trace_callback(None)
        assert all(not sql.startswith(("BEGIN", "COMMIT", "ROLLBACK", "INSERT", "UPDATE", "DELETE")) for sql in statements)


def install_outbox(env):
    snapshot = {"publication_id": str(uuid.uuid4()),
        "version": {"library_id": env.library, "library_epoch": 1, "kb_revision": 0},
        "videos": [], "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}
    metadata = env.store.write_publication(snapshot, [])
    env.store.update({"pending_publication": metadata})
    return metadata


def test_checkpoint_uses_actual_bounded_state_and_exact_pending_outbox(env):
    metadata = install_outbox(env)
    with transaction(env) as con:
        before = capture(env, con)
        assert before.public()["checkpoint"]["outboxes"][0]["sha256"] == metadata["sha256"]
        env.store.update({"seeded": True})
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)


@pytest.mark.parametrize("action", ["missing", "corrupt"])
def test_outbox_missing_or_changed_fails_without_private_paths(env, action):
    metadata = install_outbox(env)
    with transaction(env) as con:
        before = capture(env, con)
        path = env.store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")
        path.unlink() if action == "missing" else path.write_bytes(b"private-corrupt")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHECKPOINT_INVALID") as caught:
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)
        assert str(path) not in str(caught.value)


def test_capture_bounds_private_scan_and_certificate(env, monkeypatch):
    with transaction(env) as con:
        con.execute("INSERT INTO meta(key,value) VALUES('oversize-private',?)", (b"z" * 1000,))
        monkeypatch.setattr(preservation, "MAX_CELL_BYTES", 999)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_LIMIT"):
            capture(env, con)


def test_tampered_certificate_is_rejected(env):
    with transaction(env) as con:
        before = capture(env, con)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            preservation.verify_unchanged(con, replace(before, sha256="0" * 64),
                                         profile=env.profile, store=env.store)
        unknown = before.public()
        unknown["unspecified_authority"] = True
        encoded = json.dumps(unknown, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CERTIFICATE_INVALID"):
            preservation.verify_unchanged(con, replace(before, document_json=encoded,
                sha256=hashlib.sha256(encoded.encode()).hexdigest()), profile=env.profile, store=env.store)


def test_paid_source_metadata_and_indeterminate_attempt_are_not_released_or_replaced(env):
    from reno import source_contract
    plan = source_contract.SourcePlan("source_metadata", "https://synthetic.invalid/private-original")
    spec = budget.OperationSpec(env.job["continuation_id"], 1, plan.kind,
                               "outside-home:paid-source", plan.input_sha256, env.root)
    policy = budget_contract.parse_policy({"budget_enabled": True})
    paid = budget.reserve(spec, budget.AttemptSpec(plan.request_sha256, source_contract_version=1),
                          profile=env.profile, policy=policy, now=NOW)
    dispatch(env, paid, policy)
    metadata = source_contract.canonical_json({"id": "synthetic", "title": PRIVATE + "中文"})
    body = source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited",
        stdout_bytes=len(metadata.encode()), stdout_sha256=hashlib.sha256(metadata.encode()).hexdigest(),
        metadata_json=metadata)
    envelope = source_contract.encode_envelope(plan, body)
    budget.settle(paid.attempt_id, plan.request_sha256, profile=env.profile,
                  outcome="source_step_complete", result_text=envelope, now=NOW)
    unknown, request, policy = reserve(env)
    dispatch(env, unknown, policy)
    budget.settle(unknown.attempt_id, request, profile=env.profile, response_complete=False, now=NOW)
    with transaction(env) as con:
        original = [tuple(row) for row in con.execute("SELECT * FROM budget_attempt ORDER BY attempt_id")]
        before = capture(env, con)
        assert {a["state"] for o in before.public()["frontier"]["operations"] for a in o["attempts"]} == {"settled", "indeterminate"}
        assert PRIVATE not in before.document_json
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        assert original == [tuple(row) for row in con.execute("SELECT * FROM budget_attempt ORDER BY attempt_id")]


def test_recovery_outbox_is_captured_even_when_not_the_pending_publication(env):
    first = install_outbox(env)
    target = env.store.read()["binding"]["target_library_id"]
    env.store.update({"pending_publication": None, "recovery_attempt": {
        "generation": 1, "publication_id": first["publication_id"],
        "version": {"library_id": env.library, "library_epoch": 1, "kb_revision": 0},
        "stream": {"library_id": target, "library_epoch": 1}}})
    second = install_outbox(env)
    with transaction(env) as con:
        outboxes = capture(env, con).public()["checkpoint"]["outboxes"]
        assert {item["sha256"] for item in outboxes} == {first["sha256"], second["sha256"]}


def test_operational_sequences_remain_preserved(env):
    with transaction(env) as con:
        con.execute("INSERT INTO sqlite_sequence(name,seq) VALUES('evidence_ref',900)")
        before = capture(env, con)
        con.execute("UPDATE sqlite_sequence SET seq=901 WHERE name='evidence_ref'")
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        con.execute("UPDATE sqlite_sequence SET seq=seq+1 WHERE name='job'")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)


def test_sql_recovery_receipt_and_checkpoint_private_rows_remain_preserved(env):
    operation_id, checkpoint_id = str(uuid.uuid4()), str(uuid.uuid4())
    with transaction(env) as con:
        con.execute("INSERT INTO sync_recovery_operation VALUES(?,?,?,?,?,?)",
            (operation_id, "restore", "a" * 64, json.dumps({"private": PRIVATE}), "{}", "2026-10-02T00:00:00Z"))
        con.execute("INSERT INTO sync_recovery_checkpoint VALUES(?,?,?,?,?,?,?,?,?,?)",
            (checkpoint_id, operation_id, "recovery", json.dumps({"private": PRIVATE}), 1, 9,
             "recovery/synthetic.sqlite", "b" * 64, 1024, "2026-10-02T00:00:00Z"))
        before = capture(env, con)
        assert {row["name"]: row["rows"] for row in before.public()["tables"]}["sync_recovery_checkpoint"] == 1
        assert PRIVATE not in before.document_json
        con.execute("UPDATE sync_recovery_checkpoint SET receiver_version_json='{}'")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)


def test_other_root_paid_body_is_also_part_of_global_operational_preservation(env):
    other_root = tasks.submit("judge", profile=env.profile)["job_id"]
    with storage_admin.connection(env.profile) as con:
        other_job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (other_root,)).fetchone())
    other = SimpleNamespace(profile=env.profile, root=other_root, job=other_job)
    paid, request, policy = reserve(other)
    dispatch(other, paid, policy)
    budget.settle(paid.attempt_id, request, profile=env.profile, result_text=PRIVATE, now=NOW)
    with transaction(env) as con:
        before = capture(env, con)
        assert before.public()["frontier"]["operations"] == []
        con.execute("UPDATE budget_attempt SET result_text=NULL,result_storage_state='retired' WHERE attempt_id=?",
                    (paid.attempt_id,))
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)


def test_process_exclusivity_without_caller_reference_operation_is_insufficient(env):
    with env.store.exclusive(), storage_admin.connection(env.profile) as con:
        con.execute("BEGIN IMMEDIATE")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_STATE_OWNERSHIP_REQUIRED"):
            capture(env, con)


def test_other_threads_state_ownership_is_not_borrowed_or_waited_on_after_sql(env):
    entered, release = threading.Event(), threading.Event()
    errors = []
    def owner():
        try:
            with env.store.reference_operation():
                entered.set()
                assert release.wait(10)
        except BaseException as exc:
            errors.append(exc)
    thread = threading.Thread(target=owner)
    thread.start()
    try:
        assert entered.wait(5)
        with storage_admin.connection(env.profile) as con:
            con.execute("BEGIN IMMEDIATE")
            started = time.monotonic()
            with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_STATE_OWNERSHIP_REQUIRED"):
                capture(env, con)
            assert time.monotonic() - started < 1
            assert con.in_transaction
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive() and not errors


@pytest.mark.parametrize("version", [2, 3, 4])
def test_actual_sender_source_and_control_state_versions_are_captured_whole(env, version):
    getattr(env.store, {2: "enable_no_resend", 3: "enable_source_execution", 4: "enable_source_control"}[version])()
    state = env.store.read()
    assert state["format_version"] == version
    if version >= 3:
        env.store.update({"recovery": {"generation": 0, "revision": 0, "state": "running",
            "source_floor": None,
            "stream": {"library_id": state["binding"]["target_library_id"], "library_epoch": 1}}})
        env.store.install_source_execution({"format_version": 1, "binding": state["binding"],
            "item_id": 1, "submitted_url_sha256": "a" * 64, "root_job_id": env.root,
            "continuation_id": env.job["continuation_id"], "execution_generation": 1,
            "local_epoch": 1, "recovery_generation": 0,
            "recovery_stream": {"library_id": state["binding"]["target_library_id"], "library_epoch": 1},
            "resolution_input_sha256": "b" * 64})
    with transaction(env) as con:
        before = capture(env, con)
        assert before.public()["checkpoint"]["state_format_version"] == version
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        env.store.update({"session_id": str(uuid.uuid4())})
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)


def install_v16(con):
    version = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0]
    if version == "15":
        home_recovery_schema.preflight(con)
        home_recovery_schema.install(con)
        con.execute("UPDATE meta SET value='16' WHERE key='migration_version'")
    else:
        assert version == "16"
        home_recovery_schema.validate(con)


def capture_no_core(env, con):
    return preservation.capture(con, profile=env.profile, store=env.store, root_job_id=None)


def make_restore_record(before):
    from tests.test_home_recovery_schema import record, set_json
    document = before.public()
    value = record()
    value.update(library_id=document["library_id"], node_id=document["node_id"],
        before_library_epoch=document["knowledge_version"]["library_epoch"],
        before_kb_revision=document["knowledge_version"]["kb_revision"],
        after_library_epoch=document["knowledge_version"]["library_epoch"] + 1,
        after_kb_revision=document["knowledge_version"]["kb_revision"] + 1,
        before_operational_sha256=document["preserved_sha256"],
        after_operational_sha256=document["preserved_sha256"])
    checkpoint = document["checkpoint"]
    lineage = checkpoint["source_execution_lineage"]
    value.update(original_core_sha256=checkpoint["source_execution_sha256"],
        root_job_id=document["frontier"]["root_job_id"],
        continuation_id=None if lineage is None else lineage["continuation_id"],
        execution_generation=None if lineage is None else lineage["execution_generation"])
    set_json(value, "before_version_json", document["knowledge_version"])
    set_json(value, "after_version_json", {"library_id": value["library_id"],
        "library_epoch": value["after_library_epoch"], "kb_revision": value["after_kb_revision"]})
    set_json(value, "frontier_json", document["frontier"])
    set_json(value, "checkpoint_json", checkpoint)
    return home_recovery_schema.validate_record(value)


def apply_knowledge_version(con, row):
    con.execute("UPDATE meta SET value=? WHERE key='library_epoch'", (str(row["after_library_epoch"]),))
    con.execute("UPDATE meta SET value=? WHERE key='kb_revision'", (str(row["after_kb_revision"]),))
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES('step:synthetic:restored','done')")


def insert_restore(con, row):
    con.execute("INSERT INTO " + home_recovery_schema.TABLE + "(" +
        ",".join(home_recovery_schema.COLUMNS) + ") VALUES(" +
        ",".join("?" for _ in home_recovery_schema.COLUMNS) + ")",
        [row[name] for name in home_recovery_schema.COLUMNS])


def test_none_root_preserves_all_paid_tables_without_creating_lineage(env):
    seed(env)
    with transaction(env) as con:
        before_jobs = [tuple(row) for row in con.execute("SELECT * FROM job")]
        before = capture_no_core(env, con)
        assert before.public()["frontier"] == {"root_job_id": None, "root_job_row_sha256": None, "operations": []}
        assert {row["name"]: row["rows"] for row in before.public()["tables"]}["budget_attempt"] == 6
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        assert [tuple(row) for row in con.execute("SELECT * FROM job")] == before_jobs


def test_actual_absent_state_without_core_is_captured_without_initializing_binding(env):
    original_binding = env.store.read()["binding"]
    path = env.store.data_dir / "sync-state.json"
    path.unlink()
    with transaction(env) as con:
        before = capture_no_core(env, con)
        checkpoint = before.public()["checkpoint"]
        assert checkpoint["state_present"] is False and checkpoint["state_sha256"] is None
        assert len(checkpoint["absence_sha256"]) == 64 and checkpoint["source_execution_lineage"] is None
        assert not path.exists()
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHECKPOINT_INVALID"):
            capture(env, con)
        env.store.initialize(original_binding, 1)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)


def install_core(env):
    state = env.store.enable_source_control()
    stream = {"library_id": state["binding"]["target_library_id"], "library_epoch": 1}
    env.store.update({"recovery": {"generation": 0, "revision": 0, "state": "running",
                                  "source_floor": None, "stream": stream}})
    return env.store.install_source_execution({"format_version": 1, "binding": state["binding"],
        "item_id": 1, "submitted_url_sha256": "a" * 64, "root_job_id": env.root,
        "continuation_id": env.job["continuation_id"], "execution_generation": 1,
        "local_epoch": 1, "recovery_generation": 0, "recovery_stream": stream, "resolution_input_sha256": "b" * 64})


def test_none_root_cannot_ignore_an_actual_original_core(env):
    core = install_core(env)
    with transaction(env) as con:
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHECKPOINT_INVALID"):
            capture_no_core(env, con)
        before = capture(env, con).public()
        assert before["checkpoint"]["source_execution_sha256"] == hashlib.sha256(
            json.dumps(core, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        assert before["checkpoint"]["source_execution_lineage"] == {key: core[key] for key in (
            "root_job_id", "continuation_id", "execution_generation")}


def test_v16_additive_receipt_projection_has_full_shape_not_an_ignored_table(env):
    with transaction(env) as con:
        install_v16(con)
        before = capture_no_core(env, con)
        assert before.public()["schema_version"] == "16"
        assert {row["name"]: row["rows"] for row in before.public()["tables"]}[home_recovery_schema.TABLE] == 0
        con.execute("DROP TRIGGER home_recovery_no_delete")
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_SCHEMA_UNSUPPORTED"):
            capture_no_core(env, con)


@pytest.mark.parametrize("state_present", [True, False])
def test_v16_exact_one_receipt_transition_keeps_all_old_facts_state_and_paid_bodies(env, state_present):
    seed(env)
    path = env.store.data_dir / "sync-state.json"
    state_bytes = path.read_bytes() if state_present else None
    if not state_present:
        path.unlink()
    with transaction(env) as con:
        install_v16(con)
        old_jobs = [tuple(row) for row in con.execute("SELECT * FROM job")]
        old_paid = [tuple(row) for row in con.execute("SELECT * FROM budget_attempt ORDER BY attempt_id")]
        before = capture_no_core(env, con)
        record = make_restore_record(before)
        apply_knowledge_version(con, record)
        assert preservation.verify_unchanged(con, before, profile=env.profile, store=env.store).unchanged
        insert_restore(con, record)
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_unchanged(con, before, profile=env.profile, store=env.store)
        result = preservation.verify_restore_committed_transition(con, before, record,
                                                                  profile=env.profile, store=env.store)
        assert result.unchanged and result.operation_id == record["operation_id"]
        assert con.in_transaction and result.preserved_sha256 == before.public()["preserved_sha256"]
        assert [tuple(row) for row in con.execute("SELECT * FROM job")] == old_jobs
        assert [tuple(row) for row in con.execute("SELECT * FROM budget_attempt ORDER BY attempt_id")] == old_paid
        assert path.read_bytes() == state_bytes if state_present else not path.exists()


def test_core_receipt_binds_original_root_and_generation_without_rebinding_it(env):
    core = install_core(env)
    with transaction(env) as con:
        install_v16(con)
        before = capture(env, con)
        record = make_restore_record(before)
        apply_knowledge_version(con, record)
        insert_restore(con, record)
        assert preservation.verify_restore_committed_transition(con, before, record,
            profile=env.profile, store=env.store).unchanged
        assert env.store.read()["source_execution"] == core


@pytest.mark.parametrize("mutation", ["old_meta", "old_job", "extra_receipt", "state"])
def test_exact_receipt_append_cannot_mask_any_other_change(env, mutation):
    seed(env)
    with transaction(env) as con:
        install_v16(con)
        before = capture_no_core(env, con)
        record = make_restore_record(before)
        apply_knowledge_version(con, record)
        insert_restore(con, record)
        if mutation == "old_meta":
            con.execute("UPDATE meta SET value='changed' WHERE key='default_project_id'")
        elif mutation == "old_job":
            con.execute("UPDATE job SET manual_paused=0 WHERE job_id=?", (env.root,))
        elif mutation == "state":
            env.store.update({"seeded": True})
        else:
            other = dict(record)
            other["operation_id"] = str(uuid.uuid4())
            other["library_id"] = str(uuid.uuid4())
            from tests.test_home_recovery_schema import set_json
            for prefix in ("before", "after"):
                body = json.loads(other[prefix + "_version_json"])
                body["library_id"] = other["library_id"]
                set_json(other, prefix + "_version_json", body)
            insert_restore(con, home_recovery_schema.validate_record(other))
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_CHANGED"):
            preservation.verify_restore_committed_transition(con, before, record,
                                                              profile=env.profile, store=env.store)


@pytest.mark.parametrize("binding", ["missing", "wrong_row", "wrong_before_version", "wrong_frontier"])
def test_transition_requires_the_exact_inserted_record_and_actual_version_bindings(env, binding):
    from tests.test_home_recovery_schema import set_json
    with transaction(env) as con:
        install_v16(con)
        before = capture_no_core(env, con)
        record = make_restore_record(before)
        apply_knowledge_version(con, record)
        if binding != "missing":
            insert_restore(con, record)
        expected = dict(record)
        if binding == "wrong_row":
            expected["source_sha256"] = "d" * 64
        elif binding == "wrong_before_version":
            expected["before_kb_revision"] += 1
            expected["after_kb_revision"] += 1
            for prefix in ("before", "after"):
                body = json.loads(expected[prefix + "_version_json"])
                body["kb_revision"] = expected[prefix + "_kb_revision"]
                set_json(expected, prefix + "_version_json", body)
        elif binding == "wrong_frontier":
            set_json(expected, "frontier_json", {"root_job_id": None, "operations": ["changed"]})
        with pytest.raises(preservation.PreservationError, match="SOURCE_PRESERVATION_TRANSITION_INVALID"):
            preservation.verify_restore_committed_transition(con, before, expected,
                                                              profile=env.profile, store=env.store)
