"""Private resource ownership for the budget ledger's original SQL window.

This module opens its own connection under SH; it does not turn a borrowed
connection, DTO, worker context, or admission into a writer.  The witness says
nothing about a Home admission or the right to make a paid request. A private
SQLite factory observes every prepared outer transaction boundary and disables
statement caching. Checks compare that original engine generation before and
after all SQL/callbacks; a savepoint alone cannot prove transaction continuity.
This is an application ownership boundary, not a sandbox against arbitrary
same-process Python introspection or deliberate discovery of private anchors.
Consumers are fixed-SQL callbacks: they must finish any caller SAVEPOINT scope
before a witness check. Rotating the private engine anchor releases nested
savepoints while preserving their DML and the original outer transaction.
"""
from __future__ import annotations

import asyncio
from contextlib import contextmanager
from contextvars import ContextVar
import os
import secrets
import sqlite3
import stat
import threading
import uuid

from . import budget_schema, budget_writer_sqlite as owned_sqlite, db, storage_lifecycle as lifecycle


_CURRENT = ContextVar("reno_budget_owned_sql_window", default=None)
_CONTEXT_GATE = ContextVar("reno_budget_owned_sql_context", default=None)
_WRITE_FRAME = ContextVar("reno_budget_owned_write_frame", default=0)
_SEAL = object()
_MAX_COMMIT_CHECKS = 64


class BudgetWriterWitnessError(sqlite3.DatabaseError):
    def __init__(self):
        self.code = "BUDGET_WRITER_REQUIRED"
        super().__init__(self.code)


def _fail():
    raise BudgetWriterWitnessError() from None


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _key(value):
    return os.path.normcase(os.path.abspath(os.fspath(value)))


def _file_identity(path):
    try:
        value = os.stat(path, follow_symlinks=False)
        if not stat.S_ISREG(value.st_mode):
            _fail()
        return value.st_dev, value.st_ino
    except OSError:
        _fail()


def _main(con, profile):
    rows = [row for row in con.execute("PRAGMA database_list") if row[1] == "main"]
    if len(rows) != 1 or not rows[0][2] or _key(rows[0][2]) != _key(profile.database):
        _fail()
    return _key(rows[0][2])


def _metadata(con):
    rows = list(con.execute("SELECT key,value FROM main.meta "
                            "WHERE key IN ('library_id','migration_version')"))
    values = {row[0]: row[1] for row in rows}
    try:
        library = values["library_id"]
        version = values["migration_version"]
        if (len(rows) != 2 or type(library) is not str or str(uuid.UUID(library)) != library
                or type(version) is not str or str(int(version)) != version or int(version) < 1):
            _fail()
        return library, int(version)
    except (KeyError, TypeError, ValueError, AttributeError):
        _fail()


def _close_connection(con):
    con.close()


def in_write_window():
    """Conservative, SQL-free full-scan rejection flag; grants no authority.

    True throughout the factory's write resource frame, including COMMIT and
    cleanup after its token is expired. A copied context may remain true.
    """
    return _WRITE_FRAME.get() > 0


@contextmanager
def _write_frame(write):
    token = _WRITE_FRAME.set(_WRITE_FRAME.get() + 1) if write else None
    try:
        yield
    except BaseException:
        if token is not None:
            try:
                _WRITE_FRAME.reset(token)
            except BaseException:
                pass
        raise
    else:
        if token is not None:
            _WRITE_FRAME.reset(token)


class BudgetWriterWitness:
    """A closed, context-bound proof of the factory's original write window."""
    __slots__ = ("_seal", "_connection", "_profile", "_profile_id", "_guard", "_owner",
                 "_main", "_file_id", "_library_id", "_schema_version", "_active",
                 "_writable", "_anchor", "_generation", "_checking", "_closing", "_context_token", "_checks")

    def __init__(self, *, seal, connection, profile, guard, file_identity, metadata, generation=None):
        if seal is not _SEAL:
            _fail()
        self._seal, self._connection, self._profile = seal, connection, profile
        self._profile_id, self._guard, self._owner = profile.identity, guard, _owner()
        self._file_id, self._main = file_identity, _main(connection, profile)
        self._library_id, self._schema_version = metadata
        self._active, self._writable = True, True
        self._checking, self._closing, self._context_token = False, False, None
        self._checks = []
        self._generation = generation
        self._anchor = "reno_budget_" + secrets.token_hex(24)
        connection.execute("SAVEPOINT " + self._anchor)

    @property
    def library_id(self):
        return self._library_id

    @property
    def schema_version(self):
        return self._schema_version

    def _verify(self, con, profile):
        # Thread/context checks precede every SQLite call. A copied context has
        # identical ContextVar values but cannot reset a token from its source.
        if (type(self) is not BudgetWriterWitness or self._seal is not _SEAL
                or _CURRENT.get() is not self or not self._active or not self._writable
                or self._checking or self._owner != _owner()
                or not owned_sqlite.is_owned(con) or con is not self._connection
                or type(profile) is not lifecycle.StorageProfile
                or profile.identity != self._profile_id or _CONTEXT_GATE.get() is not self):
            _fail()
        try:
            _CONTEXT_GATE.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail()
        self._context_token = _CONTEXT_GATE.set(self)
        self._checking = True
        try:
            if (owned_sqlite.transaction_generation(con) != self._generation
                    or lifecycle.require_guard(self._profile) is not self._guard
                    or not con.in_transaction
                    or not (con.row_factory is None or con.row_factory is sqlite3.Row)
                    or con.execute("PRAGMA query_only").fetchone()[0] != 0
                    or _main(con, self._profile) != self._main
                    or _file_identity(self._profile.database) != self._file_id
                    or _metadata(con) != (self._library_id, self._schema_version)):
                _fail()
            # RELEASE keeps all legitimate DML and does not commit the outer
            # BEGIN. A replaced transaction cannot contain this original name.
            con.execute("RELEASE SAVEPOINT " + self._anchor)
            self._anchor = "reno_budget_" + secrets.token_hex(24)
            con.execute("SAVEPOINT " + self._anchor)
            # Trace/progress/authorizer callbacks may replace the transaction
            # inside any of the preceding SQL calls, including SAVEPOINT itself.
            if (owned_sqlite.transaction_generation(con) != self._generation
                    or not con.in_transaction):
                _fail()
        except sqlite3.DatabaseError:
            self._active = False
            _fail()
        except BaseException:
            # Loss of the original transaction/proof is permanent; reopening a
            # transaction or toggling query_only cannot revive this object.
            self._active = False
            raise
        finally:
            self._checking = False
        return self


def check(value, *, connection, profile):
    """Short stat/fixed-SQL check after any caller SAVEPOINT scope has ended.

    No new lock or long SHA read occurs. Private anchor rotation preserves DML
    but does not promise to preserve an open caller SAVEPOINT stack.
    """
    if type(value) is not BudgetWriterWitness:
        _fail()
    return value._verify(connection, profile)


def require_writer(connection, *, profile):
    """Get only this context's live, original factory writer; never mint one."""
    return check(_CURRENT.get(), connection=connection, profile=profile)


def register_commit_check(value, callback, *, connection, profile):
    """Register an already prepared, fixed short comparison before COMMIT.

    This is not an application callback replay. The binding is responsible for
    supplying a short comparison without new locks, full hashes, or network.
    """
    value = check(value, connection=connection, profile=profile)
    if not callable(callback) or value._closing or len(value._checks) >= _MAX_COMMIT_CHECKS:
        _fail()
    value._checks.append(callback)


@contextmanager
def connection(profile, *, write=False):
    """Own the existing budget SH/connect/validate/BEGIN/commit/close resource.

    The caller retains the original budget exception mapping and business
    connection checks. Both openers retain the global schema gate. Read scopes
    receive no writer witness, even if callers change PRAGMA query_only.
    """
    if type(profile) is not lifecycle.StorageProfile or type(write) is not bool:
        _fail()
    with lifecycle.shared(profile) as guard, _write_frame(write):
        original_file = _file_identity(profile.database)
        con = owned_sqlite.open_writer(profile) if write else db.connect(profile.database)
        primary_failed, witness, current_token = False, None, None
        try:
            if ((not owned_sqlite.is_owned(con) if write else type(con) is not sqlite3.Connection)
                    or not (con.row_factory is None or con.row_factory is sqlite3.Row)):
                _fail()
            budget_schema.validate(con)
            original_metadata = _metadata(con)
            before_begin = owned_sqlite.transaction_generation(con) if write else None
            con.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if write:
                generation = owned_sqlite.transaction_generation(con)
                if (generation != before_begin + 1 or not con.in_transaction
                        or _file_identity(profile.database) != original_file
                        or _metadata(con) != original_metadata
                        or con.execute("PRAGMA query_only").fetchone()[0] != 0
                        or owned_sqlite.transaction_generation(con) != generation):
                    _fail()
                witness = BudgetWriterWitness(seal=_SEAL, connection=con, profile=profile,
                    guard=guard, file_identity=original_file, metadata=original_metadata, generation=generation)
                current_token = _CURRENT.set(witness)
                witness._context_token = _CONTEXT_GATE.set(witness)
                check(witness, connection=con, profile=profile)
            yield con
            if write:
                check(witness, connection=con, profile=profile)
                witness._closing = True
                for callback in tuple(witness._checks):
                    callback()
                    check(witness, connection=con, profile=profile)
                # Invalid before COMMIT, including its synchronous trace hooks.
                witness._active = False
                con.commit()
            else:
                con.rollback()
        except BaseException:
            primary_failed = True
            if witness is not None:
                witness._active = False
            if write:
                try:
                    con.rollback()
                except BaseException:
                    pass
            raise
        finally:
            cleanup_error = None
            if witness is not None:
                witness._active = False
                # _verify rotates the context token; always reset the live one.
                if witness._context_token is not None:
                    try:
                        _CONTEXT_GATE.reset(witness._context_token)
                    except BaseException as exc:
                        cleanup_error = exc
                if current_token is not None:
                    try:
                        _CURRENT.reset(current_token)
                    except BaseException as exc:
                        if cleanup_error is None:
                            cleanup_error = exc
            try:
                _close_connection(con)
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
            if not primary_failed and cleanup_error is not None:
                raise cleanup_error
