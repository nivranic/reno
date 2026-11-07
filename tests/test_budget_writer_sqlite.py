"""Controlled SQLite engine observation without external services or real data."""
from contextvars import copy_context
import sqlite3

import pytest

from reno import budget_writer_sqlite as owned, budget_writer_witness as witness
from reno import db, storage_lifecycle as lifecycle


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                     tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(value.database)
    lifecycle.initialize(value)
    return value


def _put(con):
    con.execute("INSERT INTO main.meta(key,value) VALUES ('budget_writer_engine_test','original')")


def _result(profile):
    with sqlite3.connect(profile.database) as con:
        return con.execute("SELECT value FROM meta WHERE key='budget_writer_engine_test'").fetchone()


def _boundary(con, mode):
    if mode == "commit":
        con.commit()
    elif mode == "rollback":
        con.rollback()
    elif mode == "base_commit":
        sqlite3.Connection.commit(con)
    elif mode == "base_rollback":
        sqlite3.Connection.rollback(con)
    elif mode == "execute_end":
        con.execute("END TRANSACTION")
    elif mode == "base_execute":
        sqlite3.Connection.execute(con, "COMMIT")
    elif mode == "cursor_commit":
        con.cursor().execute("COMMIT")
    elif mode == "custom_cursor_commit":
        class CustomCursor(sqlite3.Cursor):
            pass
        con.cursor(factory=CustomCursor).execute("COMMIT")
    elif mode == "comment_commit":
        con.execute("\ufeff /* synthetic */ -- prefix\n COMMIT TRANSACTION")
    elif mode == "executescript":
        con.executescript("SELECT 1;")
    elif mode == "cursor_executescript":
        con.cursor().executescript("SELECT 1;")
    elif mode == "custom_cursor_executescript":
        class CustomCursor(sqlite3.Cursor):
            pass
        con.cursor(factory=CustomCursor).executescript("SELECT 1;")
    elif mode == "isolation_level":
        con.isolation_level = None
    elif mode == "autocommit":
        if not hasattr(con, "autocommit"):
            pytest.skip("SQLite autocommit attribute requires Python 3.12+")
        con.autocommit = True
        con.autocommit = sqlite3.LEGACY_TRANSACTION_CONTROL
    elif mode == "context_commit":
        con.__exit__(None, None, None)
    else:
        con.__exit__(RuntimeError, RuntimeError("synthetic rollback"), None)


@pytest.mark.parametrize("mode", ["commit", "rollback", "base_commit", "base_rollback", "execute_end",
    "base_execute", "cursor_commit", "custom_cursor_commit", "comment_commit", "executescript",
    "cursor_executescript", "custom_cursor_executescript", "isolation_level", "autocommit",
    "context_commit", "context_rollback"])
def test_all_connection_and_cursor_boundaries_permanently_invalidate_original_window(profile, mode):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = witness.require_writer(con, profile=profile)
            _put(con)
            con.set_authorizer(None)
            con.set_trace_callback(None)
            con.set_progress_handler(None, 0)
            _boundary(con, mode)
            con.execute("BEGIN IMMEDIATE")
            assert con.in_transaction
            witness.check(original, connection=con, profile=profile)
    rolled_back = mode in {"rollback", "base_rollback", "context_rollback"}
    assert _result(profile) == (None if rolled_back else ("original",))


def test_opener_keeps_original_schema_and_fk_contract_and_has_no_writer_token_by_itself(profile):
    with lifecycle.shared(profile):
        con = owned.open_writer(profile)
        try:
            assert owned.is_owned(con) and con.row_factory is sqlite3.Row
            assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
            con.execute("BEGIN IMMEDIATE")
            assert owned.transaction_generation(con) == 1
            with pytest.raises(witness.BudgetWriterWitnessError):
                witness.require_writer(con, profile=profile)
        finally:
            con.close()
    with pytest.raises(sqlite3.DatabaseError):
        owned.transaction_generation(con)


def test_private_opener_does_not_open_without_existing_real_sh_guard(profile):
    with pytest.raises(lifecycle.StorageLifecycleError):
        owned.open_writer(profile)


def test_missing_library_is_not_created_or_silently_initialized(profile):
    profile.database.unlink()
    with pytest.raises((lifecycle.StorageLifecycleError, witness.BudgetWriterWitnessError)):
        with witness.connection(profile, write=True):
            pytest.fail("missing database must not be created")
    assert not profile.database.exists()


def test_original_begin_generation_is_fixed_before_factory_metadata_callbacks(profile, monkeypatch):
    open_original = owned.open_writer
    observed = {"begin": False, "fired": False, "boundary_succeeded": False}

    def open_controlled(value):
        con = open_original(value)

        def trace(statement):
            if statement.startswith("BEGIN IMMEDIATE"):
                observed["begin"] = True
            if (observed["begin"] and statement.startswith("SELECT key,value FROM main.meta")
                    and not observed["fired"]):
                observed["fired"] = True
                con.commit()
                con.execute("BEGIN IMMEDIATE")
                observed["boundary_succeeded"] = True

        con.set_trace_callback(trace)
        return con

    monkeypatch.setattr(owned, "open_writer", open_controlled)
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True):
            pytest.fail("the original BEGIN was replaced before factory capture")
    assert observed == {"begin": True, "fired": True, "boundary_succeeded": True}
    assert not witness.in_write_window()


def test_private_connection_type_cannot_be_constructed_or_subclassed_by_caller(profile):
    class CallerSubclass(owned._BudgetConnection):
        pass

    for factory in (owned._BudgetConnection, CallerSubclass):
        with pytest.raises(sqlite3.DatabaseError):
            sqlite3.connect(profile.database, factory=factory)
    foreign = sqlite3.connect(profile.database)
    try:
        assert not owned.is_owned(foreign)
        with pytest.raises(sqlite3.DatabaseError):
            owned.transaction_generation(foreign)
    finally:
        foreign.close()


@pytest.mark.parametrize("cursor", [False, True])
def test_repeated_identical_sql_cannot_reuse_a_cached_transaction_vm(profile, cursor):
    with lifecycle.shared(profile):
        con = owned.open_writer(profile)
        target = con.cursor() if cursor else con
        try:
            observed = owned.transaction_generation(con)
            for _ in range(4):
                target.execute("BEGIN IMMEDIATE")
                assert owned.transaction_generation(con) == observed + 1
                target.execute("COMMIT")
                assert owned.transaction_generation(con) == observed + 2
                observed += 2
        finally:
            con.close()


def test_authorizer_removal_preserves_internal_observer_and_caller_authorizer_semantics(profile):
    seen = []
    with lifecycle.shared(profile):
        con = owned.open_writer(profile)
        try:
            con.set_authorizer(lambda action, *arguments: seen.append(action) or sqlite3.SQLITE_OK)
            con.execute("SELECT 1").fetchone()
            assert sqlite3.SQLITE_SELECT in seen
            con.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_SELECT else sqlite3.SQLITE_OK)
            with pytest.raises(sqlite3.DatabaseError) as denied:
                con.execute("SELECT 1")
            assert denied.value.sqlite_errorcode == sqlite3.SQLITE_AUTH
            with pytest.raises(TypeError):
                con.set_authorizer(object())
            with pytest.raises(sqlite3.DatabaseError):
                con.execute("SELECT 1")
            con.set_authorizer(None)
            assert con.execute("SELECT 1").fetchone()[0] == 1
            before = owned.transaction_generation(con)
            con.execute("BEGIN IMMEDIATE")
            con.set_authorizer(None)
            con.commit()
            assert owned.transaction_generation(con) == before + 2
        finally:
            con.close()


def test_caller_authorizer_ignore_and_exception_keep_sqlite_semantics(profile):
    with lifecycle.shared(profile):
        con = owned.open_writer(profile)
        try:
            con.set_authorizer(lambda action, *args: sqlite3.SQLITE_IGNORE
                if action == sqlite3.SQLITE_READ else sqlite3.SQLITE_OK)
            assert con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone() is None

            def reject(*args):
                raise RuntimeError("synthetic caller authorizer failure")

            con.set_authorizer(reject)
            with pytest.raises(sqlite3.DatabaseError):
                con.execute("SELECT 1")
            con.set_authorizer(None)
            assert con.execute("SELECT 1").fetchone()[0] == 1
        finally:
            con.close()


def test_denied_outer_transaction_attempt_is_conservatively_permanent(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = witness.require_writer(con, profile=profile)
            _put(con)
            con.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION else sqlite3.SQLITE_OK)
            with pytest.raises(sqlite3.DatabaseError):
                con.commit()
            assert con.in_transaction
            con.set_authorizer(None)
            witness.check(original, connection=con, profile=profile)
    assert _result(profile) is None


def test_failed_begin_attempt_without_replacing_txn_still_invalidates_original(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = witness.require_writer(con, profile=profile)
            _put(con)
            with pytest.raises(sqlite3.DatabaseError):
                con.execute("BEGIN")
            assert con.in_transaction
            witness.check(original, connection=con, profile=profile)
    assert _result(profile) is None


def test_executemany_keeps_dml_and_savepoint_rollback_to_original_outer_generation(profile):
    with witness.connection(profile, write=True) as con:
        original = witness.require_writer(con, profile=profile)
        generation = owned.transaction_generation(con)
        con.executemany("INSERT INTO meta(key,value) VALUES (?,?)",
                        (("budget_writer_engine_test", "original"), ("engine_extra", "retained")))
        con.execute("SAVEPOINT caller_scope")
        con.executemany("UPDATE meta SET value=? WHERE key=?", (("discarded", "engine_extra"),))
        con.execute("ROLLBACK TO caller_scope")
        con.execute("RELEASE caller_scope")
        assert owned.transaction_generation(con) == generation
        assert witness.check(original, connection=con, profile=profile) is original
    assert _result(profile) == ("original",)


def test_executemany_transaction_attempt_cannot_silently_reuse_original_proof(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = witness.require_writer(con, profile=profile)
            _put(con)
            with pytest.raises(sqlite3.ProgrammingError):
                con.executemany("COMMIT", ((),))
            witness.check(original, connection=con, profile=profile)
    assert _result(profile) is None


def test_engine_counts_boundary_before_caller_authorizer_reentrant_check(profile):
    observed = []
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = witness.require_writer(con, profile=profile)
            _put(con)

            def authorize(action, *args):
                if action == sqlite3.SQLITE_TRANSACTION:
                    try:
                        witness.check(original, connection=con, profile=profile)
                    except witness.BudgetWriterWitnessError:
                        observed.append(True)
                return sqlite3.SQLITE_OK

            con.set_authorizer(authorize)
            con.commit()
            con.set_authorizer(None)
            con.execute("BEGIN IMMEDIATE")
    assert observed == [True] and _result(profile) == ("original",)


def test_sql_free_writer_frame_stays_true_for_invalid_token_commit_and_cleanup(profile, monkeypatch):
    events = []
    assert not witness.in_write_window()
    with witness.connection(profile) as con:
        assert not witness.in_write_window()

    def close(con):
        events.append(("close", witness.in_write_window()))
        with pytest.raises(witness.BudgetWriterWitnessError):
            witness.require_writer(con, profile=profile)
        con.close()

    with witness.connection(profile, write=True) as con:
        original = witness.require_writer(con, profile=profile)
        _put(con)
        traced = []

        def trace(statement):
            traced.append(True)
            if statement == "COMMIT":
                events.append(("commit", witness.in_write_window()))
                with pytest.raises(witness.BudgetWriterWitnessError):
                    witness.check(original, connection=con, profile=profile)

        con.set_trace_callback(trace)
        before = len(traced)
        assert witness.in_write_window() and copy_context().run(witness.in_write_window)
        assert len(traced) == before
        monkeypatch.setattr(witness, "_close_connection", close)
    assert events == [("commit", True), ("close", True)]
    assert not witness.in_write_window()


def test_invalidated_writer_frame_remains_true_until_exception_cleanup(profile):
    assert not witness.in_write_window()
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            con.commit()
            con.execute("BEGIN IMMEDIATE")
            with pytest.raises(witness.BudgetWriterWitnessError):
                witness.require_writer(con, profile=profile)
            assert witness.in_write_window()
    assert not witness.in_write_window()
