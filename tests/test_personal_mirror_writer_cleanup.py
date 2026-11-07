"""Real mirror-writer cleanup with legitimate SQLite callback policies.

The original connection comes only from runtime._connection. Pair/session,
prepare and personal rows use their actual producers; no seal, receipt or
writer capability is manufactured. Emergency test cleanup runs only after
recording the actual exception/closed/lock/row outcome, so a failed regression
does not leave an active SQLite writer behind for later tests.
"""
from contextlib import contextmanager
import sqlite3

import pytest

from reno import personal_mirror_sqlite as mirror_sql
from reno import personal_mirror_transfer as runtime, storage_lifecycle as lifecycle
from reno.sync_protocol import SyncError
from tests.test_personal_mirror_transfer import (
    arguments, commit_selector, mirror_env, table_state,
)  # noqa: F401


def _closed(con):
    try:
        con.in_transaction
    except sqlite3.ProgrammingError:
        return True
    return False


def _fresh_writer(database):
    """Probe the actual file with a bounded independent SQLite connection."""
    con = sqlite3.connect(database, timeout=0.05)
    try:
        try:
            con.execute("BEGIN IMMEDIATE")
        except sqlite3.Error:
            return False
        return con.in_transaction
    finally:
        if con.in_transaction:
            con.rollback()
        con.close()


def _emergency_cleanup(con):
    if _closed(con):
        return
    # Remove only these tests' caller hooks; the normal method keeps the
    # production engine observer. Consume the original owned cleanup helper,
    # never a caller seal, new transaction or another module's permission.
    for clear in (lambda: con.set_authorizer(None),
                  lambda: con.set_progress_handler(None, 0),
                  lambda: con.set_trace_callback(None)):
        try:
            clear()
        except sqlite3.Error:
            pass
    try:
        if con.in_transaction:
            mirror_sql.rollback_writer(con)
    finally:
        con.close()


def _observe(monkeypatch):
    active, created = [], []
    original = runtime._connection

    @contextmanager
    def observed(profile):
        with original(profile) as con:
            active.append(con)
            created.append(con)
            try:
                yield con
            finally:
                active.remove(con)
    monkeypatch.setattr(runtime, "_connection", observed)
    return active, created


def _interrupted(env, monkeypatch, policy, original_error, *, phase="prepare"):
    if phase == "install":
        with lifecycle.shared(env["profile"]):
            runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
    before = table_state(env)
    active, created = _observe(monkeypatch)
    selected, called = [], []

    def interrupt_actual_writer():
        if selected or not runtime.in_write_window():
            return
        con = active[-1]
        if phase == "install" and con.total_changes == 0:
            return
        selected.append(con)
        policy(con, called)
        raise original_error

    with lifecycle.shared(env["profile"]):
        caught = None
        try:
            try:
                if phase == "prepare":
                    runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"],
                        check_cancelled=interrupt_actual_writer)
                else:
                    runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]),
                        check_cancelled=interrupt_actual_writer)
            except BaseException as exc:
                caught = exc
            # Keep the captured original alive: GC must not masquerade as
            # production cleanup. Observe actual closure before emergency aid.
            result = {"caught": caught, "selected": len(selected), "callback_calls": tuple(called),
                "closed": bool(selected) and _closed(selected[0]),
                "fresh_writer": _fresh_writer(env["profile"].database),
                "rows": table_state(env), "marker": runtime.in_write_window()}
        finally:
            for con in created:
                _emergency_cleanup(con)
    return before, result


@pytest.mark.parametrize("phase", ["prepare", "install"])
def test_denied_cleanup_rollback_preserves_original_error_and_closes_actual_writer(mirror_env, monkeypatch, phase):
    env = mirror_env
    original = RuntimeError("synthetic original writer interruption")
    def deny_rollback(con, called):
        def authorizer(action, first, *_rest):
            if action == sqlite3.SQLITE_TRANSACTION and first == "ROLLBACK":
                called.append(first)
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        con.set_authorizer(authorizer)
    before, actual = _interrupted(env, monkeypatch, deny_rollback, original, phase=phase)
    assert actual["selected"] == 1
    assert actual["caught"] is original, actual
    assert actual["closed"] is True, actual
    assert actual["fresh_writer"] is True, actual
    assert actual["rows"] == before
    assert actual["marker"] is False
    assert table_state(env) == before


@pytest.mark.parametrize("phase", ["prepare", "install"])
def test_progress_cancellation_cannot_replace_original_error_or_leave_writer_live(mirror_env, monkeypatch, phase):
    env = mirror_env
    original = RuntimeError("synthetic original error before cancelled rollback")
    def cancel_progress(con, called):
        def progress():
            called.append("progress")
            return 1
        con.set_progress_handler(progress, 1)
    before, actual = _interrupted(env, monkeypatch, cancel_progress, original, phase=phase)
    assert actual["selected"] == 1
    assert actual["caught"] is original, actual
    assert actual["closed"] is True, actual
    assert actual["fresh_writer"] is True, actual
    assert actual["rows"] == before and actual["marker"] is False
    assert table_state(env) == before


def test_normal_user_authorizer_still_denies_legal_select_and_clear_restores_select(mirror_env):
    env = mirror_env
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        before = table_state(env)
        with runtime._connection(env["profile"]) as con:
            called = []
            def deny_select(action, *_arguments):
                if action == sqlite3.SQLITE_SELECT:
                    called.append(action)
                    return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            con.set_authorizer(deny_select)
            with pytest.raises(sqlite3.DatabaseError) as caught:
                con.execute("SELECT 1").fetchone()
            assert getattr(caught.value, "sqlite_errorcode", None) == sqlite3.SQLITE_AUTH
            assert called == [sqlite3.SQLITE_SELECT]
            con.set_authorizer(None)
            assert con.execute("SELECT 1").fetchone()[0] == 1
        assert _closed(con)
        assert _fresh_writer(env["profile"].database)
    assert table_state(env) == before


@pytest.mark.parametrize("boundary", ["COMMIT", "ROLLBACK"])
@pytest.mark.parametrize("method", ["sql", "cursor", "base", "clear_authorizer"])
def test_rejected_foreign_boundary_keeps_engine_observer_and_closes_original_connection(mirror_env, monkeypatch, boundary, method):
    env = mirror_env
    before = table_state(env)
    active, created = _observe(monkeypatch)
    selected = []
    def replace_outer():
        if selected or not runtime.in_write_window():
            return
        con = active[-1]
        selected.append(con)
        if method == "clear_authorizer":
            con.set_authorizer(lambda *_arguments: sqlite3.SQLITE_OK)
            con.set_authorizer(None)
            getattr(sqlite3.Connection, boundary.lower())(con)
        elif method == "base":
            getattr(sqlite3.Connection, boundary.lower())(con)
        elif method == "cursor":
            con.cursor().execute(boundary)
        else:
            con.execute(boundary)
        pytest.fail("A foreign transaction boundary executed before the mirror writer finished")
    with lifecycle.shared(env["profile"]):
        try:
            with pytest.raises(SyncError) as caught:
                runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"], check_cancelled=replace_outer)
            assert caught.value.code == "PERSONAL_MIRROR_WRITER_CHANGED"
            assert len(selected) == 1 and _closed(selected[0])
            assert _fresh_writer(env["profile"].database)
            assert table_state(env) == before
            assert runtime.in_write_window() is False
        finally:
            for con in created:
                _emergency_cleanup(con)
