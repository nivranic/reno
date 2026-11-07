"""Private mirror writers reject caller replacement of the outer transaction.

Only this opener installs the mandatory engine authorizer, with statement
caching disabled. Normal authorizers retain their DENY semantics; clearing a
caller authorizer never removes the observer. A one-use lexical boundary
permits the runtime's BEGIN/COMMIT/ROLLBACK, and is consumed before callbacks
run. No budget capability or another module's private seal is borrowed.

This is a private ownership API, not a Python sandbox against native code or
deliberate modification of private attributes/base set_authorizer calls.
"""
from contextvars import ContextVar
import os
from pathlib import Path
import sqlite3
import threading

from . import migrations, storage_lifecycle as lifecycle
from .sync_protocol import SyncError

_SEAL = object()
_OPENING = ContextVar("reno_personal_mirror_sqlite_open", default=None)
_BOUNDARY = ContextVar("reno_personal_mirror_sqlite_boundary", default=None)


def _fail():
    raise SyncError("Personal mirror writer transaction changed",
                    "PERSONAL_MIRROR_WRITER_CHANGED", 409) from None


class _MirrorConnection(sqlite3.Connection):
    __slots__ = ("_seal", "_owner", "_closed", "_protected", "_rejected", "_observer", "_user_authorizer")

    def __init__(self, *args, **kwargs):
        if type(self) is not _MirrorConnection or _OPENING.get() is not _SEAL:
            _fail()
        kwargs["cached_statements"] = 0
        super().__init__(*args, **kwargs)
        self._seal, self._owner = _SEAL, (os.getpid(), threading.get_ident())
        self._closed = self._protected = self._rejected = False
        self._user_authorizer, self._observer = None, self._observe
        sqlite3.Connection.set_authorizer(self, self._observer)

    def _observe(self, action, *arguments):
        if action == sqlite3.SQLITE_TRANSACTION and self._protected:
            boundary = _BOUNDARY.get()
            if (boundary is None or boundary[0] is not self
                    or boundary[1] != arguments[0] or boundary[2]):
                self._rejected = True
                return sqlite3.SQLITE_DENY
            # A trace/progress/user authorizer reentry cannot reuse the permit.
            boundary[2] = True
        if self._user_authorizer is not None:
            return self._user_authorizer(action, *arguments)
        return sqlite3.SQLITE_OK

    def set_authorizer(self, callback):
        if callback is not None and not callable(callback):
            raise TypeError("authorizer_callback must be callable or None")
        sqlite3.Connection.set_authorizer(self, self._observer)
        self._user_authorizer = callback

    def commit(self):
        if self._protected:
            self._rejected = True
            _fail()
        return super().commit()

    def rollback(self):
        if self._protected:
            self._rejected = True
            _fail()
        return super().rollback()

    def close(self):
        if self._protected:
            self._rejected = True
            _fail()
        self._closed = True
        try:
            return super().close()
        finally:
            self._user_authorizer = None


def is_owned(con):
    return (type(con) is _MirrorConnection and con._seal is _SEAL and not con._closed
            and con._owner == (os.getpid(), threading.get_ident()))


def was_rejected(con):
    return type(con) is _MirrorConnection and con._rejected


def _boundary(con, operation, function):
    if not is_owned(con) or _BOUNDARY.get() is not None:
        _fail()
    token = _BOUNDARY.set([con, operation, False])
    try:
        return function()
    finally:
        _BOUNDARY.reset(token)


def begin_writer(con):
    if not is_owned(con) or con._protected or con.in_transaction:
        _fail()
    con._protected, con._rejected = True, False
    try:
        _boundary(con, "BEGIN", lambda: con.execute("BEGIN IMMEDIATE"))
        check_writer(con)
    except BaseException:
        abort_writer(con)
        raise


def check_writer(con):
    if not is_owned(con) or not con._protected or con._rejected or not con.in_transaction:
        _fail()


def commit_writer(con):
    check_writer(con)
    try:
        _boundary(con, "COMMIT", lambda: sqlite3.Connection.commit(con))
        if con._rejected or con.in_transaction:
            _fail()
    finally:
        # The caller's write-window marker remains active through its finally.
        if not con.in_transaction:
            con._protected = False


def rollback_writer(con):
    if not is_owned(con):
        _fail()
    try:
        if con.in_transaction:
            _boundary(con, "ROLLBACK", lambda: sqlite3.Connection.rollback(con))
    finally:
        if not con.in_transaction:
            con._protected = False


def close_owned(con):
    """End this owned connection even if caller hooks refuse SQL cleanup.

    Closing the native handle rolls back unfinished writes. Keep protection
    active until that handle closes; no denied cleanup reopens permission.
    """
    if (type(con) is not _MirrorConnection or con._seal is not _SEAL
            or con._owner != (os.getpid(), threading.get_ident())):
        _fail()
    try:
        sqlite3.Connection.close(con)
    finally:
        con._closed, con._protected, con._user_authorizer = True, False, None


def abort_writer(con):
    """Preserve the caller's original failure, then always release the handle."""
    try:
        if is_owned(con) and con.in_transaction:
            rollback_writer(con)
    except BaseException:
        pass
    finally:
        try:
            close_owned(con)
        except BaseException:
            pass


def open_existing(profile):
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    path = Path(profile.database)
    if not path.is_file():
        raise migrations.SchemaVersionError("Database does not exist; run: python -m reno init-db")
    token = _OPENING.set(_SEAL)
    try:
        con = sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True, timeout=30,
                              factory=_MirrorConnection, cached_statements=0)
    finally:
        _OPENING.reset(token)
    try:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA foreign_keys=ON")
        migrations.validate(con)
        return con
    except BaseException:
        con.close()
        raise
