"""Genuine SH/readonly completed provenance; no effect or writer authority."""
from contextlib import contextmanager
from contextvars import copy_context
import copy
from copy import deepcopy
import json
import os
from pathlib import Path
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import pytest

from reno import db, home_recovery as legacy, home_recovery17 as restore
from reno import home_recovery_runtime17 as runtime, home_recovery_schema as schema
from reno import home_recovery_source as sources, source_preservation_bundle as bundles
from reno import source_preservation_witness as witness, storage_backups as backups
from reno import storage_lifecycle as lifecycle, sync_state, storage_root_inputs as inputs
from reno import budget_writer_witness as budget_writer, migrations
from tests.test_home_recovery17 import old16_environment, actual_home16, env, dual_env, cloud_environment
from tests.test_home_recovery17 import apply, receipt, upgrade17
from tests import test_home_recovery_home as old_home


@pytest.fixture
def completed(env):
    operation, _ = apply(restore, env)
    env.operation_id = operation
    env.record = receipt(env, operation)
    return env


@contextmanager
def capture(value, **kwargs):
    with value.store.reference_operation():
        with runtime.capture_completed_locked(profile=value.profile, store=value.store,
                operation_id=value.operation_id, **kwargs) as observed:
            yield observed


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@pytest.mark.parametrize("method", ["_advance_creation", "_advance_state"])
def test_detached_successor_dto_cannot_replace_original_completed_proof(completed, method):
    state = deepcopy(completed.store.read())
    def forbidden(*args, **kwargs):
        pytest.fail("A detached successor DTO supplied a private effect")
    with capture(completed) as observed:
        reference = observed.provenance_ref()
        original_state = deepcopy(observed._state)
        original_payload = deepcopy(observed._home._reader.payload)
        dto = SimpleNamespace(profile=completed.profile, store=completed.store,
            _active=True, _written=True, _advance_for_capture=forbidden)
        for producer in (object(), reference, dto):
            with pytest.raises(runtime.RuntimeRestoreError):
                getattr(observed, method)(producer)
        assert observed._state == original_state
        assert observed._home._reader.payload == original_payload
        assert observed.provenance_ref() == reference
        observed.verify_source()
    assert completed.store.read() == state


@pytest.fixture
def future17_writer(completed, monkeypatch):
    """Isolated future version gate; the real factory still owns SQL/BEGIN.

    Production SCHEMA_VERSION stays 16. Only this fixture's existing actual17
    database passes the substituted gate, using its real complete DDL/FK
    validator. Other databases retain the original migrations.validate. This
    does not grant Home admission or connect budget.reserve/mark_dispatching.
    """
    actual_schema, original_validate = restore._actual_schema, migrations.validate
    expected = completed.profile.database.resolve()

    @contextmanager
    def open_window(*, write=True):
        assert migrations.SCHEMA_VERSION == 16
        with monkeypatch.context() as scoped:
            def isolated_gate(con):
                rows = [row for row in con.execute("PRAGMA database_list") if row[1] == "main"]
                if len(rows) == 1 and Path(rows[0][2]).resolve() == expected:
                    actual_schema(con)
                else:
                    original_validate(con)
            scoped.setattr(migrations, "validate", isolated_gate)
            with budget_writer.connection(completed.profile, write=write) as con:
                yield con
            assert migrations.SCHEMA_VERSION == 16
    return open_window


def test_budget_runtime_consumer_global16_factory_still_rejects_actual17(completed):
    assert migrations.SCHEMA_VERSION == 16
    with pytest.raises(migrations.SchemaVersionError, match="supports 16"):
        with budget_writer.connection(completed.profile, write=True):
            pytest.fail("The component's test gate cannot open production schema17")
    assert not budget_writer.in_write_window()
    with sqlite3.connect(completed.profile.database) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"


def test_budget_runtime_consumer_same_capture_two_factory_windows_accept_legal_ledger_delta(completed, future17_writer):
    with sqlite3.connect(completed.profile.database) as con:
        paid_before = tuple(con.execute("SELECT * FROM budget_attempt WHERE attempt_id=?",
            (completed.paid.attempt_id,)).fetchone())
    with capture(completed) as observed:
        original_ref, prior_token = observed.provenance_ref(), None
        for ordinal, timestamp in enumerate(("2026-10-04T00:00:00.000000Z", "2026-10-05T00:00:00.000000Z")):
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                assert token.schema_version == 17
                assert token is not prior_token
                if prior_token is not None:
                    with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                        observed.verify_for_commit(con, writer_permit=prior_token)
                con.execute("UPDATE budget_operation SET updated_at=? WHERE operation_id=?",
                    (timestamp, completed.unknown.operation_id))
                observed.verify_for_commit(con, writer_permit=token)
                prior_token = token
            assert observed.provenance_ref() == original_ref
            with sqlite3.connect(completed.profile.database) as con:
                assert con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
                    (completed.unknown.operation_id,)).fetchone()[0] == timestamp
    with sqlite3.connect(completed.profile.database) as con:
        assert con.execute("SELECT state FROM budget_attempt WHERE attempt_id=?",
            (completed.unknown.attempt_id,)).fetchone()[0] == "dispatching"
        assert tuple(con.execute("SELECT * FROM budget_attempt WHERE attempt_id=?",
            (completed.paid.attempt_id,)).fetchone()) == paid_before


def test_budget_runtime_consumer_initial_and_terminal_checks_use_only_fixed_short_proof(completed, future17_writer, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A budget writer consumer may not acquire full source, schema, ledger, or producer proof")
    with capture(completed) as observed:
        original_short = runtime.CompletedRestoreCapture17._verify_short_files
        original_register = budget_writer.register_commit_check
        counts = {"short": 0, "register": 0}
        def short(value):
            counts["short"] += 1
            return original_short(value)
        def register(*args, **kwargs):
            counts["register"] += 1
            return original_register(*args, **kwargs)
        with monkeypatch.context() as scoped:
            scoped.setattr(runtime.CompletedRestoreCapture17, "_verify_short_files", short)
            scoped.setattr(budget_writer, "register_commit_check", register)
            scoped.setattr(runtime._RuntimePins, "verify", forbidden)
            scoped.setattr(witness, "_verify_state_full", forbidden)
            scoped.setattr(restore, "_actual_schema", forbidden)
            scoped.setattr(restore, "resolve_producer_locked", forbidden)
            scoped.setattr(bundles, "open_completed_locked", forbidden)
            scoped.setattr(runtime.base, "_sql_capture", forbidden)
            for _ in range(2):
                with future17_writer() as con:
                    token = budget_writer.require_writer(con, profile=completed.profile)
                    observed.verify_for_commit(con, writer_permit=token)
        # Exactly one initial and one factory-held terminal comparison per
        # writer. The terminal callback never re-registers a public callback.
        assert counts == {"short": 4, "register": 2}


def test_budget_runtime_consumer_rejects_dto_copied_permit_and_raw_connection(completed, future17_writer):
    with capture(completed) as observed:
        with future17_writer() as con:
            token = budget_writer.require_writer(con, profile=completed.profile)
            for fake in (None, {}, SimpleNamespace(schema_version=17), copy.copy(token)):
                with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                    observed.verify_for_commit(con, writer_permit=fake)
            raw = sqlite3.connect(completed.profile.database)
            try:
                with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                    observed.verify_for_commit(raw, writer_permit=token)
            finally:
                raw.close()
            assert budget_writer.require_writer(con, profile=completed.profile) is token
            observed.verify_for_commit(con, writer_permit=token)


@pytest.mark.parametrize("query_only", [0, 1])
def test_budget_runtime_consumer_rejects_factory_read_scope_after_query_only_flip(completed, future17_writer, query_only):
    with capture(completed) as observed:
        with future17_writer(write=False) as con:
            con.execute("PRAGMA query_only=" + str(query_only))
            assert not budget_writer.in_write_window()
            with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                observed.verify_for_commit(con, writer_permit=None)


def test_budget_runtime_consumer_rejects_schema16_real_writer(completed):
    with capture(completed) as observed:
        with sqlite3.connect(completed.profile.database) as con:
            con.execute("UPDATE meta SET value='16' WHERE key='migration_version'")
        try:
            with budget_writer.connection(completed.profile, write=True) as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                assert token.schema_version == 16
                with pytest.raises(runtime.RuntimeRestoreError, match="SCHEMA_REQUIRED"):
                    observed.verify_for_commit(con, writer_permit=token)
        finally:
            with sqlite3.connect(completed.profile.database) as con:
                con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")


def test_budget_runtime_consumer_foreign_thread_and_copied_context_fail_before_sql(completed, future17_writer):
    with capture(completed) as observed:
        with future17_writer() as con:
            token = budget_writer.require_writer(con, profile=completed.profile)
            statements, errors = [], []
            con.set_trace_callback(statements.append)
            copied = copy_context()
            with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                copied.run(observed.verify_for_commit, con, writer_permit=token)
            def consume():
                try:
                    observed.verify_for_commit(con, writer_permit=token)
                except BaseException as error:
                    errors.append(error)
            thread = threading.Thread(target=lambda: copied.run(consume))
            thread.start()
            thread.join(timeout=5)
            assert not thread.is_alive()
            assert len(errors) == 1 and type(errors[0]) is runtime.RuntimeRestoreError
            assert errors[0].code == "HOME_RUNTIME17_FACTORY_REQUIRED"
            assert statements == []
            observed.verify_for_commit(con, writer_permit=token)


@pytest.mark.parametrize("boundary", ["commit", "rollback", "trace"])
def test_budget_runtime_consumer_rejects_replaced_original_transaction(completed, future17_writer, boundary):
    with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
        with capture(completed) as observed:
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                if boundary == "trace":
                    switched = []
                    def replace(statement):
                        if not switched and statement == "SELECT value FROM main.meta WHERE key='migration_version'":
                            switched.append(True)
                            con.commit()
                            con.execute("BEGIN IMMEDIATE")
                    con.set_trace_callback(replace)
                else:
                    getattr(con, boundary)()
                    con.execute("BEGIN IMMEDIATE")
                observed.verify_for_commit(con, writer_permit=token)


def test_budget_runtime_consumer_terminal_state_change_rolls_back_ledger_delta(completed, future17_writer):
    with sqlite3.connect(completed.profile.database) as con:
        original = con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
            (completed.unknown.operation_id,)).fetchone()[0]
    with pytest.raises(runtime.RuntimeRestoreError, match="STATE_CHANGED"):
        with capture(completed) as observed:
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                observed.verify_for_commit(con, writer_permit=token)
                con.execute("UPDATE budget_operation SET updated_at=? WHERE operation_id=?",
                    ("2026-10-06T00:00:00.000000Z", completed.unknown.operation_id))
                state = completed.store.read()
                completed.store.update({"local_epoch": state["local_epoch"] + 1})
    with sqlite3.connect(completed.profile.database) as con:
        assert con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
            (completed.unknown.operation_id,)).fetchone()[0] == original


def test_budget_runtime_consumer_terminal_fixed_sql_change_rolls_back(completed, future17_writer):
    with sqlite3.connect(completed.profile.database) as con:
        original = con.execute("SELECT value FROM meta WHERE key='kb_revision'").fetchone()[0]
    with pytest.raises(runtime.RuntimeRestoreError, match="PROVENANCE_INVALID"):
        with capture(completed) as observed:
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                observed.verify_for_commit(con, writer_permit=token)
                con.execute("UPDATE meta SET value=? WHERE key='kb_revision'", (str(int(original) + 1),))
    with sqlite3.connect(completed.profile.database) as con:
        assert con.execute("SELECT value FROM meta WHERE key='kb_revision'").fetchone()[0] == original


@pytest.mark.parametrize("point", ["initial", "terminal"])
def test_budget_runtime_consumer_keeps_original_cancellation_exception(completed, future17_writer, point):
    class Cancelled(RuntimeError):
        pass
    error = Cancelled("original budget consumer cancellation")
    def cancel():
        raise error
    with pytest.raises(Cancelled) as result:
        with capture(completed) as observed:
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                if point == "initial":
                    completed.store.check_cancelled = cancel
                observed.verify_for_commit(con, writer_permit=token)
                if point == "terminal":
                    completed.store.check_cancelled = cancel
    assert result.value is error


def test_budget_runtime_full_source_refuses_valid_and_expired_writer_frame_before_any_check(completed, future17_writer, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Full source rejection must precede capture/SQL/file checks")
    with capture(completed) as observed:
        with pytest.raises(budget_writer.BudgetWriterWitnessError):
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                for expired in (False, True):
                    if expired:
                        con.commit()
                        con.execute("BEGIN IMMEDIATE")
                        with pytest.raises(budget_writer.BudgetWriterWitnessError):
                            budget_writer.check(token, connection=con, profile=completed.profile)
                    assert budget_writer.in_write_window()
                    with monkeypatch.context() as scoped:
                        scoped.setattr(runtime.CompletedRestoreCapture17, "_check", forbidden)
                        with pytest.raises(runtime.RuntimeRestoreError, match="READ_WINDOW_REQUIRED"):
                            observed.verify_source()
        assert not budget_writer.in_write_window()
        observed.verify_source()


def test_budget_runtime_full_source_refuses_commit_trace_with_already_expired_token(completed, future17_writer, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("COMMIT trace must not enter a full source check")
    attempts = []
    with capture(completed) as observed:
        with monkeypatch.context() as scoped:
            scoped.setattr(runtime.CompletedRestoreCapture17, "_check", forbidden)
            with future17_writer() as con:
                def at_commit(statement):
                    if statement != "COMMIT":
                        return
                    result = {"write_frame": budget_writer.in_write_window()}
                    try:
                        budget_writer.require_writer(con, profile=completed.profile)
                    except budget_writer.BudgetWriterWitnessError:
                        result["token_expired"] = True
                    try:
                        observed.verify_source()
                    except BaseException as error:
                        result["error_type"], result["error_code"] = type(error), getattr(error, "code", None)
                    attempts.append(result)
                con.set_trace_callback(at_commit)
        assert attempts == [{"write_frame": True, "token_expired": True,
            "error_type": runtime.RuntimeRestoreError, "error_code": "HOME_RUNTIME17_READ_WINDOW_REQUIRED"}]


def test_budget_runtime_factory_writer_guard_precedes_arguments_sql_files_and_cancellation(completed, future17_writer, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("A writer-contained runtime factory must reject before argument/owner/SQL/file/cancel work")
    with completed.store.reference_operation():
        with future17_writer() as con:
            token = budget_writer.require_writer(con, profile=completed.profile)
            assert token.schema_version == 17 and con.in_transaction
            original_cancel = completed.store.check_cancelled
            try:
                with monkeypatch.context() as scoped:
                    scoped.setattr(runtime.lifecycle, "_profile", forbidden)
                    scoped.setattr(runtime.legacy, "_uuid", forbidden)
                    scoped.setattr(runtime.authority, "_Ownership", forbidden)
                    scoped.setattr(runtime._RuntimePins, "read", forbidden)
                    scoped.setattr(restore, "_actual_schema", forbidden)
                    completed.store.check_cancelled = forbidden
                    with pytest.raises(runtime.RuntimeRestoreError, match="READ_WINDOW_REQUIRED"):
                        with runtime.capture_completed_locked(profile=completed.profile, store=completed.store,
                                operation_id=completed.operation_id):
                            pytest.fail("A budget writer cannot start a full runtime provenance acquisition")
            finally:
                completed.store.check_cancelled = original_cancel
            assert budget_writer.require_writer(con, profile=completed.profile) is token


@pytest.mark.parametrize("mutation", ["sql", "state", "artifact"])
def test_budget_runtime_late_commit_mutation_keeps_committed_facts_but_full_exit_prevents_effect(completed, future17_writer, mutation):
    changed = "2026-10-07T00:00:00.000000Z"
    with sqlite3.connect(completed.profile.database) as con:
        original = con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
            (completed.unknown.operation_id,)).fetchone()[0]
        paid_before = tuple(con.execute("SELECT * FROM budget_attempt WHERE attempt_id=?",
            (completed.paid.attempt_id,)).fetchone())
        revision = con.execute("SELECT value FROM meta WHERE key='kb_revision'").fetchone()[0]
    assert original != changed
    expected = {"sql": "PROVENANCE_INVALID", "state": "STATE_CHANGED", "artifact": "ARTIFACT_CHANGED"}[mutation]
    trace, effects = [], []
    returned = False
    with pytest.raises(runtime.RuntimeRestoreError, match=expected):
        with capture(completed) as observed:
            with future17_writer() as con:
                token = budget_writer.require_writer(con, profile=completed.profile)
                observed.verify_for_commit(con, writer_permit=token)
                con.execute("UPDATE budget_operation SET updated_at=? WHERE operation_id=?",
                    (changed, completed.unknown.operation_id))
                def late_mutation(statement):
                    if statement != "COMMIT" or trace:
                        return
                    trace.append(budget_writer.in_write_window())
                    if mutation == "sql":
                        con.execute("UPDATE meta SET value=? WHERE key='kb_revision'", (str(int(revision) + 1),))
                    elif mutation == "state":
                        state = completed.store.read()
                        completed.store.update({"local_epoch": state["local_epoch"] + 1})
                    else:
                        bundle_id, _ = bundles._ids(completed.operation_id)
                        extra = completed.profile.database.parent / bundles.DIRECTORY / bundle_id / "late-commit-extra.json"
                        if os.name == "nt":
                            extra = Path(runtime.files.safe._native_path(extra))
                        extra.write_bytes(b"{}")
                        if os.name != "nt":
                            extra.chmod(0o600)
                con.set_trace_callback(late_mutation)
            returned = True
            assert not budget_writer.in_write_window()
        # This sentinel models the required ordering for a future consumer:
        # all capture/State/SH/SQL resources must exit before any effect. It is
        # not an assertion that budget business/current admission is wired.
        effects.append("after_all_resources_exit")
    assert returned and trace == [True] and effects == []
    with sqlite3.connect(completed.profile.database) as con:
        # COMMIT already happened. The later capture rejection cannot undo or
        # erase the ledger's paid/unknown facts or pretend to refund a call.
        assert con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
            (completed.unknown.operation_id,)).fetchone()[0] == changed
        assert con.execute("SELECT state FROM budget_attempt WHERE attempt_id=?",
            (completed.unknown.attempt_id,)).fetchone()[0] == "dispatching"
        assert tuple(con.execute("SELECT * FROM budget_attempt WHERE attempt_id=?",
            (completed.paid.attempt_id,)).fetchone()) == paid_before
        if mutation == "sql":
            assert con.execute("SELECT value FROM meta WHERE key='kb_revision'").fetchone()[0] == str(int(revision) + 1)


def test_genuine_sh_factory_reads_full_original_provenance_without_ex_or_source_reproduction(completed, monkeypatch):
    def forbidden(*_args, **_kwargs):
        pytest.fail("Runtime provenance cannot acquire or borrow an EX producer")
    monkeypatch.setattr(restore, "resolve_producer_locked", forbidden)
    monkeypatch.setattr(bundles, "open_completed_locked", forbidden)
    monkeypatch.setattr(bundles, "_load_completed", forbidden)
    monkeypatch.setattr(bundles, "_backup_image", forbidden)
    monkeypatch.setattr(sources, "open_source", forbidden)
    monkeypatch.setattr(lifecycle, "exclusive", forbidden)
    before_state = completed.store.read()
    before_db = completed.profile.database.read_bytes()
    with capture(completed, expected_result_sha256=completed.record["result_sha256"]) as observed:
        assert type(observed) is runtime.CompletedRestoreCapture17
        assert witness._WINDOW.get() is None
        assert observed.provenance_ref() == {"format_version": 1, "producer": "home_knowledge_restore",
            "operation_id": completed.operation_id, "receipt_sha256": completed.record["result_sha256"]}
        assert observed.provenance_ref()["receipt_sha256"] != restore._row_sha(completed.record)
        assert observed.chain_observation() == ({"operation_id": completed.operation_id,
            "before_epoch": completed.record["before_library_epoch"],
            "after_epoch": completed.record["after_library_epoch"], "trusted17_producer": True},)
        observed.verify_source()
    assert completed.store.read() == before_state
    assert completed.profile.database.read_bytes() == before_db


def test_completed_capture_survives_original_readonly_witness_exit_and_owns_independent_pins(completed):
    with capture(completed) as observed:
        assert witness._CURRENT.get() is None
        assert all(not item[2].closed for item in observed._pins.files)
        original = observed.provenance_ref()
        original["receipt_sha256"] = "f" * 64
        assert observed.provenance_ref()["receipt_sha256"] == completed.record["result_sha256"]
        with witness.sql_window_locked(profile=completed.profile, store=completed.store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=completed.profile, store=completed.store) as actual:
                witness.require_read_capture(actual, connection=con, profile=completed.profile)
                observed.verify_source()
    assert all(item[2].closed for item in observed._pins.files)
    with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
        observed.provenance_ref()


@pytest.mark.parametrize("mutation", ["missing", "rewritten", "extra"])
def test_completed_capture_rechecks_exact_ddl_in_owned_readonly_window(completed, mutation):
    contract_errors = (schema.HomeRecoverySchemaError, legacy.HomeRecoveryError)
    trigger = "home_recovery_no_delete"
    extra = "runtime17_unexpected_delete_trigger"
    with capture(completed) as observed:
        with sqlite3.connect(completed.profile.database) as writer:
            original = writer.execute("SELECT sql FROM sqlite_master WHERE type='trigger' AND name=?",
                (trigger,)).fetchone()[0]
            if mutation == "extra":
                writer.execute('CREATE TRIGGER "' + extra + '" BEFORE DELETE ON "' + schema.TABLE
                    + '" BEGIN SELECT 1; END')
            else:
                writer.execute('DROP TRIGGER "' + trigger + '"')
                if mutation == "rewritten":
                    writer.execute('CREATE TRIGGER "' + trigger + '" BEFORE DELETE ON "' + schema.TABLE
                        + '" BEGIN SELECT 1; END')
        try:
            # This is a new actual readonly snapshot after the committed DDL
            # mutation, not the factory's earlier accepted SQL transaction.
            with witness.sql_window_locked(profile=completed.profile, store=completed.store, writable=False) as con:
                con.execute("PRAGMA query_only=ON")
                with witness.capture_locked(con, profile=completed.profile, store=completed.store) as actual:
                    witness.require_read_capture(actual, connection=con, profile=completed.profile)
                    with pytest.raises(contract_errors):
                        restore._actual_schema(con)
                    with pytest.raises(contract_errors):
                        observed.verify_source()
        finally:
            # The outer capture's final full verification must not conceal
            # an assertion failure by observing this temporary corrupt DDL.
            with sqlite3.connect(completed.profile.database) as writer:
                if mutation == "extra":
                    writer.execute('DROP TRIGGER "' + extra + '"')
                else:
                    writer.execute('DROP TRIGGER IF EXISTS "' + trigger + '"')
                    writer.execute(original)
        with witness.sql_window_locked(profile=completed.profile, store=completed.store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=completed.profile, store=completed.store) as actual:
                witness.require_read_capture(actual, connection=con, profile=completed.profile)
                assert restore._actual_schema(con) == observed._schema_sha
                observed.verify_source()


def test_old_ex_resolver_remains_closed_under_runtime_sh(completed):
    with completed.store.reference_operation():
        with witness.sql_window_locked(profile=completed.profile, store=completed.store) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=completed.profile, store=completed.store) as actual:
                with pytest.raises(lifecycle.StorageLifecycleError):
                    with restore.resolve_producer_locked(con, profile=completed.profile, store=completed.store,
                            operation_id=completed.operation_id, actual_witness=actual):
                        pytest.fail("EX-only resolver cannot borrow SH")


@pytest.mark.parametrize("suffix", ["request", "intent", "proposal", "receipt"])
def test_missing_original_completed_metadata_never_repairs_or_creates_receipt(completed, suffix):
    path = completed.profile.database.parent / restore.DIRECTORY / (completed.operation_id + "." + suffix + ".json")
    path.unlink()
    with pytest.raises((ValueError, OSError)):
        with capture(completed):
            pytest.fail("Missing original completed metadata cannot become a runtime seal")
    assert not path.exists()


def test_retired_source_declaration_and_image_are_not_reopened_as_fresh_production(completed):
    declaration = sources._source_path(completed.profile, completed.source_id)
    declaration.unlink()
    declaration.with_name(completed.source_id + ".intent.json").unlink()
    original_source = str(uuid.uuid5(uuid.UUID(completed.source_id), "reno.home.knowledge.checkpoint.v1"))
    record, _ = backups._record(backups._catalog(completed.profile) / (original_source + ".json"))
    Path(record["destination"]).unlink()
    with capture(completed) as observed:
        assert observed.provenance_ref()["operation_id"] == completed.operation_id


def test_arbitrary_connection_and_caller_dto_never_create_missing_budget_writer_permit(completed):
    with capture(completed) as observed:
        con = sqlite3.connect(completed.profile.database)
        try:
            con.execute("BEGIN IMMEDIATE")
            for permit in (None, {}, {"profile": completed.profile, "connection": con, "generation": 0}):
                with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                    observed.verify_for_commit(con, writer_permit=permit)
            con.rollback()
        finally:
            con.close()


def test_same_restore_proof_accepts_legal_budget_ledger_write_but_does_not_grant_dispatch(completed):
    with capture(completed) as observed:
        before = observed.provenance_ref()
        # Existing unknown attempt remains unknown. A legitimate accounting
        # timestamp update is not an immutable whole-SQL mismatch.
        with sqlite3.connect(completed.profile.database) as con:
            initial = con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
                (completed.unknown.operation_id,)).fetchone()[0]
            changed = "2026-10-03T00:00:00.000000Z"
            assert initial != changed
            con.execute("UPDATE budget_operation SET updated_at=? WHERE operation_id=?",
                (changed, completed.unknown.operation_id))
            assert con.execute("SELECT updated_at FROM budget_operation WHERE operation_id=?",
                (completed.unknown.operation_id,)).fetchone()[0] == changed
        observed.verify_source()
        assert observed.provenance_ref() == before
        with sqlite3.connect(completed.profile.database) as con:
            assert con.execute("SELECT state FROM budget_attempt WHERE attempt_id=?",
                (completed.unknown.attempt_id,)).fetchone()[0] == "dispatching"


@pytest.mark.parametrize("writable", [False, True])
def test_factory_never_borrows_caller_sql_window_even_query_only_flipped(completed, writable):
    with completed.store.reference_operation():
        with witness.sql_window_locked(profile=completed.profile, store=completed.store, writable=writable) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=completed.profile, store=completed.store):
                with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
                    with runtime.capture_completed_locked(profile=completed.profile, store=completed.store,
                            operation_id=completed.operation_id):
                        pytest.fail("Runtime factory must open its own original readonly window")


def test_full_sha_verify_rejects_original_writable_factory_even_query_only_flipped(completed):
    with capture(completed) as observed:
        with witness.sql_window_locked(profile=completed.profile, store=completed.store, writable=True) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=completed.profile, store=completed.store):
                with pytest.raises(runtime.RuntimeRestoreError, match="READ_WINDOW_REQUIRED"):
                    observed.verify_source()
                with pytest.raises(runtime.RuntimeRestoreError, match="WRITER_PERMIT_REQUIRED"):
                    observed.verify_for_commit(con)


def test_owner_reference_frame_profile_and_factory_dto_are_not_interchangeable(completed):
    with pytest.raises((ValueError, lifecycle.StorageLifecycleError)):
        with runtime.capture_completed_locked(profile=completed.profile, store=completed.store,
                operation_id=completed.operation_id):
            pytest.fail("State thread/reference ownership must precede the factory")
    with completed.store.reference_operation():
        with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
            with runtime.capture_completed_locked(profile=completed.profile,
                    store=type("DTO", (), {"profile": completed.profile})(), operation_id=completed.operation_id):
                pytest.fail("Caller DTO cannot own State")
    with capture(completed) as observed:
        with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
            runtime.CompletedRestoreCapture17(seal=object(), profile=completed.profile, store=completed.store,
                owner=observed._owner, guard=observed._guard, pins=observed._pins, state=observed._state,
                identity=observed._identity, checkpoint_capture=observed._checkpoint, cloud=observed._cloud,
                home_capture=observed._home, records=observed._records, version=observed._version,
                projection=observed._sql_projection, selected=observed._selected,
                documents=observed._documents, schema_sha=observed._schema_sha)


def test_double_completed_restore_chain_checks_each_original_producer(completed):
    second, _ = apply(restore, completed)
    with capture(completed) as observed:
        assert [row["operation_id"] for row in observed.chain_observation()] == [completed.operation_id, second]
    later = completed.profile.database.parent / restore.DIRECTORY / (second + ".receipt.json")
    later.unlink()
    with pytest.raises((ValueError, OSError)):
        with capture(completed):
            pytest.fail("Latest producer cannot hide missing intermediate completion")


def test_actual_dual_capsules_are_reconstructed_from_original_copies(dual_env):
    operation, _ = apply(restore, dual_env)
    dual_env.operation_id, dual_env.record = operation, receipt(dual_env, operation)
    with capture(dual_env) as observed:
        document = observed._documents[0]
        assert all(value["presence"] == "present" for value in document["permanent_witnesses"].values())
        observed.verify_source()


@pytest.mark.parametrize("part", ["certificate", "manifest", "copied_state", "copied_origin", "producer_row_claim"])
def test_original_private_artifact_or_coordinated_row_hash_tamper_never_mints_seal(completed, part):
    bundle_id, _ = bundles._ids(completed.operation_id)
    root = completed.profile.database.parent / bundles.DIRECTORY / bundle_id
    if part in {"certificate", "manifest"}:
        path = root / (part + ".json")
        value = json.loads(path.read_bytes())
        if part == "certificate":
            value["frontier"]["root_job_row_sha256"] = "f" * 64
        else:
            value["file_set_sha256"] = "f" * 64
        path.write_bytes(encoded(value))
    elif part == "producer_row_claim":
        directory = completed.profile.database.parent / restore.DIRECTORY
        proposal_path = directory / (completed.operation_id + ".proposal.json")
        receipt_path = directory / (completed.operation_id + ".receipt.json")
        proposal, value = json.loads(proposal_path.read_bytes()), json.loads(receipt_path.read_bytes())
        proposal["row_sha256"] = value["row_sha256"] = "f" * 64
        proposal_path.write_bytes(encoded(proposal))
        value["proposal_sha256"] = bundles._sha(encoded(proposal))
        receipt_path.write_bytes(encoded(value))
    else:
        manifest = json.loads((root / "manifest.json").read_bytes())
        entry = (next(item for item in manifest["files"] if item["role"] == "checkpoint_state")
            if part == "copied_state" else next(item for item in manifest["files"] if "/origins/" in item["relative_key"]))
        path = root / "files" / bundles._copy_name(entry)
        if os.name == "nt":
            path = Path(runtime.files.safe._native_path(path))
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    with pytest.raises((ValueError, OSError)):
        with capture(completed):
            pytest.fail("Original artifact tampering cannot issue a private runtime seal")


def test_current_live_origin_missing_cannot_be_replaced_by_original_bundle_copy(completed):
    origin = completed.profile.database.parent / runtime.authority.DIRECTORY / runtime.authority.VERSION_DIRECTORY
    (origin / "origins" / (completed.origin["record_id"] + ".json")).unlink()
    with pytest.raises((ValueError, OSError)):
        with capture(completed):
            pytest.fail("Current State origin must be actually live")


def test_original_bundle_namespace_appearance_fails_final_verification(completed):
    bundle_id, _ = bundles._ids(completed.operation_id)
    extra = completed.profile.database.parent / bundles.DIRECTORY / bundle_id / "unselected.json"
    with pytest.raises(ValueError):
        with capture(completed) as observed:
            extra.write_bytes(b"{}")
            if os.name != "nt":
                extra.chmod(0o600)
            observed.verify_source()


def test_catalog_retiring_transition_is_not_ready_provenance(completed):
    path = backups._catalog(completed.profile) / (completed.record["protection_backup_id"] + ".json")
    record = json.loads(path.read_bytes())
    record["state"] = "reserved"
    record.pop("descriptor")
    record.pop("descriptor_sha256")
    path.write_bytes(encoded(record))
    with pytest.raises((ValueError, OSError)):
        with capture(completed):
            pytest.fail("A non-ready original image cannot close runtime provenance")


def test_same_guard_cannot_replace_original_state_reference_frame(completed):
    with capture(completed) as observed:
        original = completed.store._reference_frame
        completed.store._reference_frame = object()
        try:
            with pytest.raises(ValueError):
                observed.provenance_ref()
        finally:
            completed.store._reference_frame = original
        observed.verify_source()


def test_foreign_thread_cannot_consume_capture_or_public_reference_as_seal(completed):
    with capture(completed) as observed:
        errors = []
        def worker():
            try:
                observed.provenance_ref()
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 1 and type(errors[0]) is runtime.RuntimeRestoreError


def test_reused_readonly_transaction_does_not_allow_full_verify(completed):
    with capture(completed) as observed:
        with pytest.raises(ValueError):
            with witness.sql_window_locked(profile=completed.profile, store=completed.store) as con:
                con.execute("PRAGMA query_only=ON")
                con.rollback()
                con.execute("BEGIN")
                observed.verify_source()


def test_missing_original_protection_image_is_not_filled_from_newer_live_database(completed):
    record, _ = backups._record(backups._catalog(completed.profile) /
        (completed.record["protection_backup_id"] + ".json"))
    Path(record["destination"]).unlink()
    with pytest.raises((ValueError, OSError)):
        with capture(completed):
            pytest.fail("Newer current SQL cannot substitute the original protected image")


def test_legacy_bare_history_is_verified_but_never_issued_a_trusted17_producer(old16_environment):
    value = old16_environment
    store = old_home.state(value)
    old_home.changed(value)
    original, _ = old_home.apply(value)
    env = SimpleNamespace(profile=value[0], source_id=value[1], store=store)
    upgrade17(env)
    newer, _ = apply(restore, env)
    env.operation_id, env.record = newer, receipt(env, newer)
    with capture(env) as observed:
        chain = observed.chain_observation()
        assert [row["operation_id"] for row in chain] == [original, newer]
        assert [row["trusted17_producer"] for row in chain] == [False, True]
        assert observed.provenance_ref()["operation_id"] == newer
    env.operation_id = original
    with pytest.raises(runtime.RuntimeRestoreError, match="TRUSTED17_PRODUCER_REQUIRED"):
        with capture(env):
            pytest.fail("Bare history cannot acquire a new17 producer reference")


def test_deleted_earliest_history_row_cannot_shrink_a_completed_chain(old16_environment):
    value = old16_environment
    store = old_home.state(value)
    old_home.changed(value)
    original, _ = old_home.apply(value)
    env = SimpleNamespace(profile=value[0], source_id=value[1], store=store)
    upgrade17(env)
    newer, _ = apply(restore, env)
    env.operation_id, env.record = newer, receipt(env, newer)
    with sqlite3.connect(env.profile.database) as con:
        triggers = con.execute("SELECT name,sql FROM sqlite_master WHERE type='trigger' AND tbl_name=?",
            (schema.TABLE,)).fetchall()
        for name, _ in triggers:
            con.execute('DROP TRIGGER "' + name + '"')
        con.execute('DELETE FROM "' + schema.TABLE + '" WHERE operation_id=?', (original,))
        for _, sql in triggers:
            con.execute(sql)
    with pytest.raises(runtime.RuntimeRestoreError, match="CHAIN_HISTORY_CHANGED"):
        with capture(env):
            pytest.fail("Original prior rows must remain typed exact, even if earliest history disappears")


def test_foreign_profile_with_same_database_cannot_borrow_original_ownership(completed):
    foreign = lifecycle.StorageProfile(completed.profile.database, completed.profile.media,
        completed.profile.reports, completed.profile.checkpoints.with_name("foreign-checkpoints"))
    with completed.store.reference_operation():
        with pytest.raises(runtime.RuntimeRestoreError, match="FACTORY_REQUIRED"):
            with runtime.capture_completed_locked(profile=foreign, store=completed.store,
                    operation_id=completed.operation_id):
                pytest.fail("Same database pathname cannot replace the original full profile")


def test_expected_result_sha_is_actual_sql_result_and_not_receipt_file_or_row_digest(completed):
    for incorrect in (restore._row_sha(completed.record), "f" * 64):
        with pytest.raises(runtime.RuntimeRestoreError, match="RESULT_MISMATCH"):
            with capture(completed, expected_result_sha256=incorrect):
                pytest.fail("Producer reference must bind the actual original SQL result SHA")


def test_owned_factory_reads_private_rows_without_widening_public_sql_projection(completed):
    with completed.store.reference_operation():
        con = sqlite3.connect(completed.profile.database.as_uri() + "?mode=ro", uri=True)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("BEGIN")
            public = inputs._Reader(con, inputs.InputLimits())
            con.set_authorizer(public.authorize)
            for column in ("request_json", "frontier_json", "result_json"):
                with pytest.raises(sqlite3.DatabaseError):
                    con.execute('SELECT ' + column + ' FROM main."' + schema.TABLE + '"').fetchall()
        finally:
            con.close()
        with runtime.capture_completed_locked(profile=completed.profile, store=completed.store,
                operation_id=completed.operation_id) as observed:
            assert tuple(observed._selected) == schema.COLUMNS
            assert observed._selected["request_json"] == completed.record["request_json"]


def test_cancellation_exception_identity_survives_full_provenance_verification(completed):
    class Cancelled(RuntimeError):
        pass
    error = Cancelled("synthetic original cancellation")
    with pytest.raises(Cancelled) as result:
        with capture(completed) as observed:
            completed.store.check_cancelled = lambda: (_ for _ in ()).throw(error)
            observed.verify_source()
    assert result.value is error
