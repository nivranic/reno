"""Private SQLite opener with a mandatory engine transaction observer.

Only the budget resource factory uses this connection. Ordinary caller
authorizers, traces, progress handlers, cursors and transaction APIs retain
their SQLite semantics. Clearing a caller authorizer does not clear the
internal observer. Statement caching is disabled so every prepared outer
transaction boundary is observed, including cursor and reentrant callbacks.
The observer stores only an integer generation, never SQL or its arguments.

This is not a Python sandbox: deliberately invoking the C base implementation
of set_authorizer, modifying private attributes/classes, or using native code
to remove the internal observer bypasses the private ownership API.
"""
from __future__ import annotations

from contextvars import ContextVar
import os
from pathlib import Path
import sqlite3
import threading

from . import migrations, storage_lifecycle as lifecycle


_SEAL = object()
_OPENING = ContextVar("reno_budget_private_sqlite_open", default=None)


def _fail():
    raise sqlite3.DatabaseError("BUDGET_WRITER_REQUIRED") from None


class _BudgetConnection(sqlite3.Connection):
    __slots__ = ("_seal", "_generation", "_user_authorizer", "_observer", "_owner", "_closed")

    def __init__(self, *args, **kwargs):
        if type(self) is not _BudgetConnection or _OPENING.get() is not _SEAL:
            _fail()
        # Engine authorizers run during prepare, not each reuse of a cached VM.
        kwargs["cached_statements"] = 0
        super().__init__(*args, **kwargs)
        self._seal, self._generation, self._user_authorizer = _SEAL, 0, None
        self._owner, self._closed = (os.getpid(), threading.get_ident()), False
        self._observer = self._observe
        sqlite3.Connection.set_authorizer(self, self._observer)

    def _observe(self, action, *arguments):
        if action == sqlite3.SQLITE_TRANSACTION:
            # Count before the caller authorizer. Failed/denied attempts also
            # permanently change the generation; no callback can revive it.
            self._generation += 1
        callback = self._user_authorizer
        if callback is not None:
            return callback(action, *arguments)
        return sqlite3.SQLITE_OK

    def set_authorizer(self, authorizer_callback):
        if authorizer_callback is not None and not callable(authorizer_callback):
            raise TypeError("authorizer_callback must be callable or None")
        # Preserve base closed/thread errors before replacing the caller slot.
        sqlite3.Connection.set_authorizer(self, self._observer)
        self._user_authorizer = authorizer_callback

    def close(self):
        self._closed = True
        try:
            return super().close()
        finally:
            self._user_authorizer = None


def transaction_generation(connection):
    """Read only the original private opener's engine counter; never mint proof."""
    if (type(connection) is not _BudgetConnection or connection._seal is not _SEAL
            or connection._closed or connection._owner != (os.getpid(), threading.get_ident())):
        _fail()
    return connection._generation


def is_owned(connection):
    return type(connection) is _BudgetConnection


def open_writer(profile):
    """Equivalent rw/FK/Row/global-version validation to db.connect, under SH.

    No migration, repair, initialization or alternate schema acceptance occurs.
    This exact factory is required because a borrowed raw connection cannot be
    retrofitted with a non-removable ordinary authorizer hook.
    """
    if type(profile) is not lifecycle.StorageProfile:
        _fail()
    lifecycle.require_guard(profile)
    path = Path(profile.database)
    if not path.is_file():
        raise migrations.SchemaVersionError("Database does not exist; run: python -m reno init-db")
    token = _OPENING.set(_SEAL)
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30,
                              factory=_BudgetConnection, cached_statements=0)
    finally:
        _OPENING.reset(token)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        migrations.validate(con)
        return con
    except BaseException:
        # Preserve cancellation/schema errors even if a cleanup operation fails.
        try:
            con.close()
        except BaseException:
            pass
        raise
