"""Actual transaction replacement must not publish partial personal mirrors.

The connection is observed through the runtime's real private opener. All
personal facts, preparations and paired sessions use their real producers;
this does not seed a receipt, install rows or manufacture writer permission.
"""
from contextlib import contextmanager
import sqlite3

import pytest

from reno import personal_mirror_transfer as runtime, storage_lifecycle as lifecycle
from reno.sync_protocol import SyncError
from tests.test_personal_mirror_transfer import (
    arguments, commit_selector, mirror_env, table_state,
)


def replace(con, boundary, method):
    if method == "method":
        getattr(con, boundary)()
    elif method == "cursor":
        con.cursor().execute(boundary.upper())
    elif method == "base":
        getattr(sqlite3.Connection, boundary)(con)
    elif method == "clear_authorizer":
        con.set_authorizer(None)
        getattr(sqlite3.Connection, boundary)(con)
    else:
        con.execute(boundary.upper())
    con.execute("BEGIN IMMEDIATE")


@pytest.mark.parametrize("method", ["method", "sql", "cursor", "base", "clear_authorizer"])
@pytest.mark.parametrize("boundary", ["commit", "rollback"])
def test_callback_cannot_replace_prepare_transaction(mirror_env, monkeypatch, boundary, method):
    env = mirror_env
    observed, replaced = [], []
    original = runtime._connection

    @contextmanager
    def observe_connection(profile):
        with original(profile) as con:
            observed.append(con)
            try:
                yield con
            finally:
                observed.remove(con)

    monkeypatch.setattr(runtime, "_connection", observe_connection)

    def replace_original_transaction():
        if not replaced and runtime.in_write_window():
            replaced.append(boundary)
            con = observed[-1]
            replace(con, boundary, method)

    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"],
            check_cancelled=replace_original_transaction)
    assert replaced == [boundary]
    assert table_state(env) == before
    assert not runtime.in_write_window()


@pytest.mark.parametrize("method", ["method", "sql", "cursor", "base", "clear_authorizer"])
@pytest.mark.parametrize("boundary", ["commit", "rollback"])
def test_callback_cannot_replace_partial_install_transaction(mirror_env, monkeypatch, boundary, method):
    env = mirror_env
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
    observed, replaced = [], []
    original = runtime._connection

    @contextmanager
    def observe_connection(profile):
        with original(profile) as con:
            observed.append(con)
            try:
                yield con
            finally:
                observed.remove(con)

    monkeypatch.setattr(runtime, "_connection", observe_connection)

    def replace_original_transaction():
        if not replaced and runtime.in_write_window():
            con = observed[-1]
            # Wait until installation has actually modified personal rows.
            if con.total_changes == 0:
                return
            replaced.append(boundary)
            replace(con, boundary, method)

    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]),
            check_cancelled=replace_original_transaction)
    assert replaced == [boundary]
    assert table_state(env) == before
    assert not runtime.in_write_window()
