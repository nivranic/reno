"""Real SQLite ownership windows, with isolated storage and no external calls."""
from contextvars import copy_context
import copy
import sqlite3
import threading

import pytest

from reno import budget_writer_witness as witness, db, storage_lifecycle as lifecycle
from tests.schema_fixtures import initialize_current17, initialize_released16, released16


@pytest.fixture
def profile(tmp_path, monkeypatch):
    """Retain the original16 runtime for the full historical writer lifetime."""
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                     tmp_path / "reports", tmp_path / "checkpoints")
    with released16(monkeypatch):
        initialize_released16(value.database)
        lifecycle.initialize(value)
        yield value


@pytest.fixture
def current_profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "current/data/reno.db", tmp_path / "current/media",
                                     tmp_path / "current/reports", tmp_path / "current/checkpoints")
    initialize_current17(value.database)
    lifecycle.initialize(value)
    return value


def _put(con, value="original"):
    con.execute("INSERT INTO main.meta(key,value) VALUES ('budget_writer_test',?)", (value,))


def _read(profile):
    with sqlite3.connect(profile.database) as con:
        return con.execute("SELECT value FROM meta WHERE key='budget_writer_test'").fetchone()


def _require(con, profile):
    return witness.require_writer(con, profile=profile)


def test_real_factory_retains_normal_dml_and_outer_transaction_until_commit(profile):
    observed = []
    with witness.connection(profile, write=True) as con:
        token = _require(con, profile)
        assert token.library_id == con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        assert token.schema_version == 16
        _put(con)
        for _ in range(5):
            assert _require(con, profile) is token
            assert con.in_transaction
            assert _read(profile) is None
        witness.register_commit_check(token, lambda: observed.append(con.in_transaction),
                                      connection=con, profile=profile)
    assert _read(profile) == ("original",)
    assert observed == [True]
    with pytest.raises(witness.BudgetWriterWitnessError):
        witness.check(token, connection=con, profile=profile)
    with pytest.raises(sqlite3.ProgrammingError):
        con.execute("SELECT 1")


def test_normal_caller_savepoint_and_rollback_to_do_not_replace_outer_transaction(profile):
    with witness.connection(profile, write=True) as con:
        token = _require(con, profile)
        _put(con)
        con.execute("SAVEPOINT caller_scope")
        con.execute("UPDATE meta SET value='discarded' WHERE key='budget_writer_test'")
        con.execute("ROLLBACK TO caller_scope")
        con.execute("RELEASE caller_scope")
        assert witness.check(token, connection=con, profile=profile) is token
    assert _read(profile) == ("original",)


@pytest.mark.parametrize("query_only", [0, 1])
def test_readonly_original_scope_never_has_writer_even_after_query_only_flip(profile, query_only):
    with witness.connection(profile) as con:
        con.execute("PRAGMA query_only=" + str(query_only))
        with pytest.raises(witness.BudgetWriterWitnessError):
            _require(con, profile)


def test_foreign_connection_and_copied_or_duck_dto_do_not_mint_or_consume_original(profile):
    class Duck:
        def check(self, **kwargs):
            return self

    with witness.connection(profile, write=True) as con:
        original = _require(con, profile)
        foreign = sqlite3.connect(profile.database)
        try:
            with pytest.raises(witness.BudgetWriterWitnessError):
                _require(foreign, profile)
            for copied in (copy.copy(original), Duck(), {"active": True, "generation": 0}, None):
                with pytest.raises(witness.BudgetWriterWitnessError):
                    witness.check(copied, connection=con, profile=profile)
            assert _require(con, profile) is original
        finally:
            foreign.close()


def test_wrong_profile_is_not_authority_and_does_not_destroy_right_scope(profile, tmp_path):
    other = lifecycle.StorageProfile(profile.database, tmp_path / "other/media",
                                    tmp_path / "other/reports", tmp_path / "other/checkpoints")
    with witness.connection(profile, write=True) as con:
        original = _require(con, profile)
        with pytest.raises(witness.BudgetWriterWitnessError):
            _require(con, other)
        assert _require(con, profile) is original


def test_constructor_cannot_accept_caller_seal_or_sql_dto(profile):
    con = sqlite3.connect(profile.database)
    try:
        with pytest.raises(witness.BudgetWriterWitnessError):
            witness.BudgetWriterWitness(seal=object(), connection=con, profile=profile,
                                       guard=object(), file_identity=(1, 2), metadata=("dto", 16))
        with pytest.raises(witness.BudgetWriterWitnessError):
            _require(con, profile)
    finally:
        con.close()


def test_copied_context_and_other_thread_fail_before_sql_without_burning_original(profile):
    observed = []
    with witness.connection(profile, write=True) as con:
        original = _require(con, profile)
        con.set_trace_callback(lambda _statement: observed.append(True))
        before = len(observed)
        copied = copy_context()
        with pytest.raises(witness.BudgetWriterWitnessError):
            copied.run(_require, con, profile)
        errors = []

        def run():
            try:
                copy_context().run(_require, con, profile)
            except BaseException as exc:
                errors.append(exc)

        thread = threading.Thread(target=lambda: copied.run(run))
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive()
        assert len(errors) == 1 and type(errors[0]) is witness.BudgetWriterWitnessError
        assert len(observed) == before
        assert _require(con, profile) is original


def test_original_witness_cannot_be_reused_in_next_real_factory_window(profile):
    with witness.connection(profile, write=True) as first:
        original = _require(first, profile)
    with witness.connection(profile, write=True) as second:
        next_token = _require(second, profile)
        assert next_token is not original
        with pytest.raises(witness.BudgetWriterWitnessError):
            witness.check(original, connection=second, profile=profile)
        assert _require(second, profile) is next_token


@pytest.mark.parametrize("boundary", ["commit", "rollback", "executescript", "autocommit", "end",
                                      "comment_commit", "comment_rollback"])
@pytest.mark.parametrize("remove_trace", [False, True])
def test_original_transaction_anchor_permanently_rejects_reopened_txn(profile, boundary, remove_trace):
    committed = boundary in {"commit", "executescript", "autocommit", "end", "comment_commit"}
    token = None
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            token = _require(con, profile)
            _put(con)
            con.set_trace_callback(lambda _statement: None)
            if remove_trace:
                con.set_trace_callback(None)
            if boundary == "commit":
                con.commit()
            elif boundary == "rollback":
                con.rollback()
            elif boundary == "executescript":
                con.executescript("SELECT 1;")
            elif boundary == "autocommit":
                con.isolation_level = None
            elif boundary == "end":
                con.execute("END TRANSACTION")
            elif boundary == "comment_commit":
                con.execute("\ufeff /* synthetic */ -- prefix\n COMMIT TRANSACTION")
            else:
                con.execute("/* synthetic */ -- prefix\n ROLLBACK TRANSACTION")
            con.execute("BEGIN IMMEDIATE")
            assert con.in_transaction
            with pytest.raises(witness.BudgetWriterWitnessError) as caught:
                witness.check(token, connection=con, profile=profile)
            assert str(caught.value) == "BUDGET_WRITER_REQUIRED"
            # A missing anchor cannot be restored by another new transaction.
            con.rollback()
            con.execute("BEGIN IMMEDIATE")
            with pytest.raises(witness.BudgetWriterWitnessError):
                _require(con, profile)
    # A caller's earlier COMMIT is a real irreversible result. Factory failure
    # cannot pretend that the already committed original DML was rolled back.
    assert _read(profile) == (("original",) if committed else None)


def test_factory_commit_detects_replacement_even_when_no_getter_runs_after_it(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            _put(con)
            con.set_trace_callback(None)
            con.commit()
            con.execute("BEGIN IMMEDIATE")
    assert _read(profile) == ("original",)


def test_caller_closed_original_connection_cannot_supply_a_writer(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = _require(con, profile)
            _put(con)
            con.close()
            with pytest.raises(witness.BudgetWriterWitnessError):
                witness.check(original, connection=con, profile=profile)
    assert _read(profile) is None


def test_caller_authorizer_cannot_omit_real_engine_anchor_probe(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = _require(con, profile)
            _put(con)
            con.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_SAVEPOINT else sqlite3.SQLITE_OK)
            with pytest.raises(witness.BudgetWriterWitnessError):
                witness.check(original, connection=con, profile=profile)
    assert _read(profile) is None


def test_query_only_denial_permanently_invalidates_original_writer(profile):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = _require(con, profile)
            _put(con)
            con.execute("PRAGMA query_only=1")
            with pytest.raises(witness.BudgetWriterWitnessError):
                witness.check(original, connection=con, profile=profile)
            con.execute("PRAGMA query_only=0")
            with pytest.raises(witness.BudgetWriterWitnessError):
                _require(con, profile)
    assert _read(profile) is None


def test_caller_row_factory_cannot_forge_fixed_sql_observations(profile):
    class Spoof:
        def __eq__(self, other):
            return True

        def __call__(self, cursor, row):
            return row

    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = _require(con, profile)
            con.row_factory = Spoof()
            with pytest.raises(witness.BudgetWriterWitnessError):
                witness.check(original, connection=con, profile=profile)
            con.row_factory = sqlite3.Row
            with pytest.raises(witness.BudgetWriterWitnessError):
                _require(con, profile)


@pytest.mark.parametrize("key,value", [("library_id", "00000000-0000-0000-0000-000000000099"),
                                       ("migration_version", "17")])
def test_actual_library_and_schema_are_fixed_to_original_writer_snapshot(profile, key, value):
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            con.execute("UPDATE main.meta SET value=? WHERE key=?", (value, key))
            _require(con, profile)
    with sqlite3.connect(profile.database) as con:
        assert con.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()[0] != value


def test_global_schema16_gate_remains_closed_to_actual17(current_profile, monkeypatch):
    # The destination is an actual ordinary17 image created before entering
    # the old runtime, rather than a16 image relabelled as17.
    with released16(monkeypatch):
        with pytest.raises(db.SchemaVersionError):
            with witness.connection(current_profile, write=True):
                pytest.fail("actual17 must not bypass the original db.connect gate")


def test_current17_factory_keeps_real_transaction_and_retires_original_witness(current_profile):
    with witness.connection(current_profile, write=True) as con:
        original = _require(con, current_profile)
        assert original.schema_version == 17
        _put(con)
        assert _require(con, current_profile) is original
        assert con.in_transaction
        assert _read(current_profile) is None
    assert _read(current_profile) == ("original",)
    with pytest.raises(witness.BudgetWriterWitnessError):
        witness.check(original, connection=con, profile=current_profile)


def test_expired_original_lifecycle_guard_is_rejected(profile):
    with pytest.raises(lifecycle.StorageLifecycleError):
        with witness.connection(profile, write=True) as con:
            guard = lifecycle.require_guard(profile)
            guard._active = False
            try:
                _require(con, profile)
            finally:
                guard._active = True


def test_actual_main_file_replacement_is_rejected(profile):
    retired = profile.database.with_name("original-retired.db")
    with pytest.raises((witness.BudgetWriterWitnessError, lifecycle.StorageLifecycleError)):
        with witness.connection(profile, write=True) as con:
            try:
                profile.database.rename(retired)
            except PermissionError:
                pytest.skip("OS forbids renaming a currently open SQLite database")
            try:
                with sqlite3.connect(profile.database):
                    pass
                _require(con, profile)
            finally:
                profile.database.unlink()
                retired.rename(profile.database)


def test_getter_only_checks_held_guard_stat_and_fixed_sql_without_new_resources(profile, monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("short writer check opened a new resource")

    with witness.connection(profile, write=True) as con:
        original = _require(con, profile)
        monkeypatch.setattr(lifecycle, "shared", forbidden)
        monkeypatch.setattr(witness.db, "connect", forbidden)
        monkeypatch.setattr(witness.budget_schema, "validate", forbidden)
        assert _require(con, profile) is original


@pytest.mark.parametrize("primary", [RuntimeError("application callback failed"), KeyboardInterrupt()])
def test_callback_failure_and_cancellation_roll_back_and_permanently_expire(profile, primary):
    token = None
    with pytest.raises(type(primary)) as caught:
        with witness.connection(profile, write=True) as con:
            token = _require(con, profile)
            _put(con)
            raise primary
    assert caught.value is primary and _read(profile) is None
    with pytest.raises(witness.BudgetWriterWitnessError):
        witness.check(token, connection=con, profile=profile)


def test_registered_short_commit_failure_preserves_primary_and_rolls_back(profile):
    primary = RuntimeError("prepared short comparison changed")
    calls = []

    def reject():
        calls.append(True)
        raise primary

    with pytest.raises(RuntimeError) as caught:
        with witness.connection(profile, write=True) as con:
            token = _require(con, profile)
            _put(con)
            witness.register_commit_check(token, reject, connection=con, profile=profile)
    assert caught.value is primary and calls == [True] and _read(profile) is None


def test_commit_failure_remains_primary_when_rollback_and_close_also_fail(profile, monkeypatch):
    close_failure = RuntimeError("synthetic close failure")

    def close(con):
        con.close()
        raise close_failure

    with pytest.raises(sqlite3.DatabaseError) as caught:
        with witness.connection(profile, write=True) as con:
            _put(con)
            con.set_authorizer(lambda action, *args: sqlite3.SQLITE_DENY
                if action == sqlite3.SQLITE_TRANSACTION else sqlite3.SQLITE_OK)
            monkeypatch.setattr(witness, "_close_connection", close)
    assert caught.value is not close_failure
    assert caught.value.sqlite_errorcode == sqlite3.SQLITE_AUTH
    assert _read(profile) is None


def test_normal_close_failure_surfaces_after_real_commit(profile, monkeypatch):
    failure = RuntimeError("synthetic close failure")

    def close(con):
        con.close()
        raise failure

    with pytest.raises(RuntimeError) as caught:
        with witness.connection(profile, write=True) as con:
            _put(con)
            monkeypatch.setattr(witness, "_close_connection", close)
    assert caught.value is failure and _read(profile) == ("original",)


def test_primary_callback_failure_is_not_replaced_by_close_failure(profile, monkeypatch):
    primary = RuntimeError("original callback failure")

    def close(con):
        con.close()
        raise AssertionError("secondary close failure")

    with pytest.raises(RuntimeError) as caught:
        with witness.connection(profile, write=True) as con:
            _put(con)
            monkeypatch.setattr(witness, "_close_connection", close)
            raise primary
    assert caught.value is primary and _read(profile) is None


def test_witness_is_already_expired_inside_real_commit_trace_callback(profile):
    errors = []
    with witness.connection(profile, write=True) as con:
        token = _require(con, profile)
        _put(con)

        def observe(statement):
            if statement == "COMMIT":
                try:
                    witness.check(token, connection=con, profile=profile)
                except BaseException as exc:
                    errors.append(exc)

        con.set_trace_callback(observe)
    assert len(errors) == 1 and type(errors[0]) is witness.BudgetWriterWitnessError
    assert _read(profile) == ("original",)


@pytest.mark.parametrize("consumer", ["explicit_check", "factory_final_check"])
def test_real_trace_boundary_during_anchor_rotation_cannot_resume_authority(profile, consumer):
    observed = {"fired": False, "boundary_succeeded": False}
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = _require(con, profile)
            _put(con)

            def trace(statement):
                if statement.startswith("SAVEPOINT ") and not observed["fired"]:
                    observed["fired"] = True
                    con.commit()
                    con.execute("BEGIN IMMEDIATE")
                    observed["boundary_succeeded"] = True

            con.set_trace_callback(trace)
            if consumer == "explicit_check":
                witness.check(original, connection=con, profile=profile)
    assert observed == {"fired": True, "boundary_succeeded": True}
    assert _read(profile) == ("original",)


@pytest.mark.parametrize("position", [3, 4])
@pytest.mark.parametrize("boundary", ["commit", "rollback"])
def test_real_release_progress_boundary_cannot_resume_original_authority(profile, position, boundary):
    observed = {"release": False, "progress": 0, "fired": False, "boundary_succeeded": False}
    with pytest.raises(witness.BudgetWriterWitnessError):
        with witness.connection(profile, write=True) as con:
            original = _require(con, profile)
            _put(con)

            def trace(statement):
                if statement.startswith("RELEASE SAVEPOINT "):
                    observed["release"] = True

            def progress():
                if observed["release"] and not observed["fired"]:
                    observed["progress"] += 1
                    if observed["progress"] == position:
                        observed["fired"] = True
                        getattr(con, boundary)()
                        con.execute("BEGIN IMMEDIATE")
                        observed["boundary_succeeded"] = True
                return 0

            con.set_trace_callback(trace)
            con.set_progress_handler(progress, 1)
            witness.check(original, connection=con, profile=profile)
    assert observed["fired"] and observed["boundary_succeeded"]
    assert _read(profile) == (("original",) if boundary == "commit" else None)
