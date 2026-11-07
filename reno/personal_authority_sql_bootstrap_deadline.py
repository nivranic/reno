"""Finite private SQLite bootstrap with exact Connection/Cursor ownership.

The sidecar borrows a completed image-byte budget and one existing deadline.
Yielded exact Connections remain raw: their caller APIs and SQLite/TEMP/native
internals gain no automatic clock gates, SQL quotas or write authority here.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import sqlite3
import time

from . import personal_authority_directory_deadline as directories
from . import personal_authority_image_bytes_deadline as images
from . import personal_authority_namespace3_deadline as deadline
from . import storage_readonly as readonly
from . import storage_source as source

_monotonic = time.monotonic
_NEW, _ACTIVE, _RETIRED = "new", "active", "retired"
_EMPTY, _OWNED, _ATTEMPTED = "empty", "owned", "attempted"
_CLOSED, _UNKNOWN = "closed", "unknown"
_SUFFIXES = ("", "-wal")
_NAMES = {"": "image.sqlite", "-wal": "image.sqlite-wal"}
_VM_INTERVAL = 1000


class SqlBootstrapDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "SQL_BOOTSTRAP_DEADLINE_" + suffix
        super().__init__(self.code)


Error = SqlBootstrapDeadlineError


def _fail(suffix):
    raise SqlBootstrapDeadlineError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "SQL_BOOTSTRAP_DEADLINE_CLEANUP_FAILED")
    except BaseException:
        pass


def _token(value):
    try:
        return deadline._token(value)
    except deadline.Namespace3Error:
        _fail("INVALID_ARGUMENT")


def _clock(token):
    absolute = _token(token).absolute_monotonic
    now = _monotonic()
    if type(now) is not float or not math.isfinite(now) or now < 0.0:
        _fail("INVALID_ARGUMENT")
    if now >= absolute:
        _fail("DEADLINE")


def _connect(database, *, uri=True):
    return sqlite3.connect(database, uri=uri)


def _setlimit(con, category, value):
    return sqlite3.Connection.setlimit(con, category, value)


def _set_progress_handler(con, callback, interval):
    return sqlite3.Connection.set_progress_handler(con, callback, interval)


def _set_authorizer(con, callback):
    return sqlite3.Connection.set_authorizer(con, callback)


def _execute(con, sql):
    return sqlite3.Connection.execute(con, sql)


def _fetchall(cursor):
    return sqlite3.Cursor.fetchall(cursor)


def _cursor_close(cursor):
    return sqlite3.Cursor.close(cursor)


def _con_close(con):
    return sqlite3.Connection.close(con)


def _cursor_connection(cursor):
    return sqlite3.Cursor.__dict__["connection"].__get__(cursor)


def _in_transaction(con):
    return sqlite3.Connection.__dict__["in_transaction"].__get__(con)


def _owner_verify(owner):
    return images.ImagePrivateOwner._verify(owner)


def _entry_stat(directory, name):
    return directories.DeadlineDirectory.entry_stat(directory, name)


def _bootstrap_call(operation, *args, **kwargs):
    # Only fixed SQLite API/initial Cursor.close callers use this boundary.
    try:
        return operation(*args, **kwargs)
    except sqlite3.Error:
        raise readonly.ReadonlyImageError("SOURCE_DATABASE_INVALID") from None


def _signature(value):
    if (type(value) is not tuple or len(value) != 5
            or any(type(item) is not int for item in value) or value[2] < 0):
        _fail("INVALID_ARGUMENT")
    return tuple(item for item in value)


@dataclass(frozen=True, slots=True)
class SqlBootstrapCloseReceiptV1:
    """Fixed owner close facts; no Connection, Cursor or authority escapes."""
    format_version: int
    kind: str
    scope: str
    phase: str
    enter_returned: bool
    exit_attempted: bool
    failed: bool
    normal_exit: bool
    connection_state: str
    cursor_states: tuple[str, ...]
    bootstrap_complete: bool
    limits_restored: bool
    exit_in_transaction: bool | None


class SqlBootstrapDeadlineLease:
    """Own one exact Connection and four exact bootstrap Cursor slots."""
    __slots__ = ("_owner", "_budget", "_token", "_copied", "_phase", "_con",
                 "_cursors", "_results", "_previous", "_restored", "_first",
                 "_first_tb", "_bootstrap_complete", "_normal_exit",
                 "_exit_in_transaction", "_enter_started", "_enter_returned", "_exit_attempted")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, private_owner, *, byte_budget):
        if type(self) is not SqlBootstrapDeadlineLease:
            _fail("INVALID_ARGUMENT")
        self._owner, self._budget = private_owner, byte_budget
        self._copied = {}
        self._phase = _NEW
        # Slots pre-exist every resource-producing API.
        self._con = [None, _EMPTY]
        self._cursors = [[None, _EMPTY] for _ in range(4)]
        self._results = [None] * 13
        self._previous, self._restored = [None, None], [False, False]
        self._first = self._first_tb = None
        self._bootstrap_complete = self._normal_exit = False
        self._exit_in_transaction = None
        self._enter_started = False
        self._enter_returned = self._exit_attempted = False
        self._bind()
        _clock(self._token)
        self._owner.directory._live()
        _clock(self._token)

    @property
    def evidence(self):
        return {"phase": self._phase, "connection_state": self._con[1],
                "cursor_states": tuple(slot[1] for slot in self._cursors),
                "bootstrap_complete": self._bootstrap_complete,
                "normal_exit": self._normal_exit,
                "previous_limits": tuple(self._previous),
                "limits_restored": all(self._restored),
                "exit_in_transaction": self._exit_in_transaction,
                "retired": self._phase == _RETIRED}

    @property
    def close_receipt(self):
        # Cached fields only; no clock, owner, SQL or native reads.
        return SqlBootstrapCloseReceiptV1(
            format_version=1, kind="sql_bootstrap",
            scope="native_owner_close_facts_only", phase=self._phase,
            enter_returned=self._enter_returned,
            exit_attempted=self._exit_attempted, failed=self._first is not None,
            normal_exit=self._normal_exit and self._first is None,
            connection_state=self._con[1],
            cursor_states=tuple(slot[1] for slot in self._cursors),
            bootstrap_complete=self._bootstrap_complete,
            limits_restored=all(self._restored),
            exit_in_transaction=self._exit_in_transaction)

    def _bind(self):
        owner, budget = self._owner, self._budget
        if (type(owner) is not images.ImagePrivateOwner
                or type(budget) is not images.ImageByteBudget
                or type(owner.directory) is not directories.DeadlineDirectory):
            _fail("INVALID_ARGUMENT")
        token = _token(budget.deadline_token)
        if (owner.deadline_token is not token or owner.directory.deadline_token is not token
                or budget._first is not None or budget._retired is not True
                or budget._private_owner is not owner
                or type(budget._phases) is not dict or set(budget._phases) != set(_SUFFIXES)
                or type(budget._before) is not dict
                or set(budget._before) != {"", "-wal", "-journal"}
                or type(budget._copied) is not dict):
            _fail("INVALID_ARGUMENT")
        copied = {}
        for suffix in _SUFFIXES:
            expected = budget._before[suffix]
            if expected is None:
                if suffix == "" or budget._phases[suffix] != "absent":
                    _fail("INVALID_ARGUMENT")
                continue
            expected = _signature(expected)
            if budget._phases[suffix] != "hash_complete" or suffix not in budget._copied:
                _fail("INVALID_ARGUMENT")
            current = _signature(budget._copied[suffix])
            if current[2] != expected[2]:
                _fail("INVALID_ARGUMENT")
            copied[suffix] = current
        if set(budget._copied) != set(copied):
            _fail("INVALID_ARGUMENT")
        if self._copied and self._copied != copied:
            _fail("INVALID_ARGUMENT")
        self._copied, self._token = copied, token

    def _capture(self, failure, *, cleanup=False):
        if self._first is None:
            self._first = failure
            self._first_tb = BaseException.__dict__["__traceback__"].__get__(failure)
        elif cleanup and failure is not self._first:
            _note(self._first)

    def _raise_first(self):
        if self._first is not None:
            raise BaseException.with_traceback(self._first, self._first_tb) from None

    def _verify_owner(self):
        _clock(self._token)
        _owner_verify(self._owner)
        _clock(self._token)

    def _path(self, name, *, missing=False):
        _clock(self._token)
        try:
            info = _entry_stat(self._owner.directory, name)
        except FileNotFoundError:
            _clock(self._token)
            if missing:
                return None
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED") from None
        _clock(self._token)
        if missing:
            raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
        return source.file_signature(info)

    def _preflight(self):
        self._bind()
        self._verify_owner()
        for suffix in _SUFFIXES:
            if suffix in self._copied:
                if self._path(_NAMES[suffix]) != self._copied[suffix]:
                    raise readonly.ReadonlyImageError("PRIVATE_IMAGE_CHANGED")
            else:
                self._path(_NAMES[suffix], missing=True)
        self._path("image.sqlite-journal", missing=True)
        self._path("image.sqlite-shm", missing=True)
        self._verify_owner()
        _clock(self._token)

    def _close(self, slot, operation, *, bootstrap=False):
        if slot[1] != _OWNED:
            return
        # A failed preclock records first, but must not block the actual close.
        try:
            _clock(self._token)
        except BaseException as failure:
            self._capture(failure, cleanup=True)
        slot[1] = _ATTEMPTED
        try:
            if bootstrap and self._first is None:
                actual = _bootstrap_call(operation, slot[0])
            else:
                actual = operation(slot[0])
            if actual is not None:
                _fail("INVALID_RESULT")
        except BaseException as failure:
            slot[1] = _UNKNOWN
            self._capture(failure, cleanup=True)
        else:
            slot[1] = _CLOSED
            try:
                _clock(self._token)
            except BaseException as failure:
                self._capture(failure, cleanup=True)

    def _cleanup(self):
        self._exit_attempted = True
        self._phase = _RETIRED
        for slot in reversed(self._cursors):
            self._close(slot, _cursor_close)
        self._close(self._con, _con_close)

    def _none_api(self, index, operation, *args):
        _clock(self._token)
        result = _bootstrap_call(operation, *args)
        self._results[index] = result
        _clock(self._token)
        if result is not None:
            _fail("INVALID_RESULT")

    def _limit_api(self, index, category, value, *, previous=None, restored=None):
        _clock(self._token)
        actual = _bootstrap_call(_setlimit, self._con[0], category, value)
        self._results[index] = actual
        legal = type(actual) is int and actual >= 0
        if legal and previous is not None:
            self._previous[previous] = actual
        _clock(self._token)
        if not legal:
            _fail("INVALID_RESULT")
        if restored is not None:
            self._restored[restored] = True

    def _execute_api(self, index, slot_index, sql):
        slot = self._cursors[slot_index]
        _clock(self._token)
        try:
            cursor = _bootstrap_call(_execute, self._con[0], sql)
        except BaseException:
            slot[1] = _UNKNOWN
            raise
        self._results[index] = cursor
        legal = type(cursor) is sqlite3.Cursor
        if legal:
            slot[0], slot[1] = cursor, _OWNED
        else:
            slot[1] = _UNKNOWN
        # Legal exact Cursor ownership precedes postclock and relationship read.
        _clock(self._token)
        if not legal:
            _fail("INVALID_RESULT")
        _clock(self._token)
        actual_con = _cursor_connection(cursor)
        _clock(self._token)
        if actual_con is not self._con[0]:
            _fail("INVALID_RESULT")
        return cursor

    def _finish_cursor(self, slot_index):
        self._close(self._cursors[slot_index], _cursor_close, bootstrap=True)
        self._raise_first()

    def _bootstrap(self):
        _clock(self._token)
        uri = (self._owner.path / "image.sqlite").as_uri() + "?mode=ro"
        _clock(self._token)
        _clock(self._token)
        try:
            con = _bootstrap_call(_connect, uri, uri=True)
        except BaseException:
            self._con[1] = _UNKNOWN
            raise
        self._results[0] = con
        legal = type(con) is sqlite3.Connection
        if legal:
            self._con[0], self._con[1] = con, _OWNED
        else:
            self._con[1] = _UNKNOWN
        _clock(self._token)
        if not legal:
            _fail("INVALID_RESULT")
        self._limit_api(1, sqlite3.SQLITE_LIMIT_LENGTH, readonly.BOOTSTRAP_MAX_BYTES,
                        previous=0)
        self._limit_api(2, sqlite3.SQLITE_LIMIT_SQL_LENGTH, readonly.BOOTSTRAP_MAX_BYTES,
                        previous=1)
        steps = 0

        def budget():
            nonlocal steps
            steps += _VM_INTERVAL
            return int(steps > readonly.BOOTSTRAP_VM_STEPS)

        self._none_api(3, _set_progress_handler, con, budget, _VM_INTERVAL)
        self._execute_api(4, 0, "PRAGMA query_only=ON")
        self._finish_cursor(0)
        self._execute_api(5, 1, "PRAGMA trusted_schema=OFF")
        self._finish_cursor(1)
        self._none_api(6, _set_authorizer, con, readonly._readonly_authorizer)
        self._execute_api(7, 2, "BEGIN")
        self._finish_cursor(2)
        cursor = self._execute_api(8, 3, "SELECT rootpage FROM sqlite_schema LIMIT 1")
        _clock(self._token)
        rows = _bootstrap_call(_fetchall, cursor)
        self._results[9] = rows
        _clock(self._token)
        if type(rows) is not list:
            _fail("INVALID_RESULT")
        self._finish_cursor(3)
        self._none_api(10, _set_progress_handler, con, None, 0)
        self._limit_api(11, sqlite3.SQLITE_LIMIT_LENGTH, self._previous[0], restored=0)
        self._limit_api(12, sqlite3.SQLITE_LIMIT_SQL_LENGTH, self._previous[1], restored=1)

    def __enter__(self):
        self._raise_first()
        if self._phase != _NEW or self._enter_started:
            _fail("CLOSED")
        self._enter_started = True
        try:
            self._preflight()
            self._bootstrap()
            self._verify_owner()
            _clock(self._token)
            self._bootstrap_complete = True
            self._phase = _ACTIVE
            self._enter_returned = True
            return self._con[0]
        except BaseException as failure:
            self._capture(failure)
            self._cleanup()
            self._raise_first()

    def _terminal(self):
        self._bind()
        self._verify_owner()
        _clock(self._token)
        value = _in_transaction(self._con[0])
        # Store the scalar before its postclock, but never publish illegal data.
        if type(value) is bool:
            self._exit_in_transaction = value
        _clock(self._token)
        if type(value) is not bool:
            _fail("INVALID_RESULT")

    def __exit__(self, _kind, primary, _traceback):
        self._raise_first()
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        if primary is not None:
            self._capture(primary)
        else:
            try:
                self._terminal()
            except BaseException as failure:
                self._capture(failure)
        self._cleanup()
        if self._first is None:
            try:
                self._verify_owner()
                _clock(self._token)
            except BaseException as failure:
                self._capture(failure)
        if self._first is not None:
            if self._first is primary:
                return None
            self._raise_first()
        self._normal_exit = True
        return None


def private_connection(private_owner, *, byte_budget):
    return SqlBootstrapDeadlineLease(private_owner, byte_budget=byte_budget)
