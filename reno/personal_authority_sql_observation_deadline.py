"""Fixed SQL observation under one live committed image and original deadline.

Borrow the source Connection; own each exact Cursor and two fixed memory
references. Resource normal_exit is not complete SQL projection/source_closed.
VM quanta are cooperative callback fees, not exact native opcode observations.
"""
from __future__ import annotations

import math
import sqlite3
import time

from . import personal_authority_committed_image_deadline as committed
from . import personal_authority_namespace3_deadline as deadline
from . import personal_mirror_contract as facts
from . import personal_mirror_transfer_schema as receiver
from . import personal_preservation_contract as contract
from . import personal_preservation_source as preservation
from . import storage_lifecycle as lifecycle
from . import storage_readonly as readonly

_monotonic = time.monotonic
_NEW, _ACTIVE, _RETIRED = "new", "active", "retired"
_EMPTY, _ATTEMPTED, _OWNED = "empty", "attempted", "owned"
_CLOSED, _UNKNOWN, _BORROWED = "closed", "unknown", "borrowed"
_RETURNED, _FAILED = "returned", "failed"
_VM_INTERVAL = 1000
_LENGTH_LIMIT, _SQL_LENGTH_LIMIT = 1048576, 32768
_DATA_ROWS, _DATA_BYTES = 100000, 134217728
_SOURCE_METADATA_ROWS, _SOURCE_METADATA_BYTES = 100000, 134217728
_REFERENCE_METADATA_ROWS, _REFERENCE_METADATA_BYTES = 4096, 8388608
_SOURCE_VM, _REFERENCE_VM = 20000000, 50000
_SOURCE_CALLS, _REFERENCE_CALLS = 404096, 16384
_SOURCE_CURSORS, _REFERENCE_CURSORS, _LIVE_CURSORS = 4096, 256, 8
_FETCH_MAX = 64
_SUFFIXES = ("", "-wal", "-journal")
_DECIMAL_FIELDS = ("device", "inode", "mtime_ns", "ctime_ns")
_FILE_FIELDS = frozenset((*_DECIMAL_FIELDS, "byte_size", "sha256"))
_EMPTY_SHA256 = "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
_PERSONAL_COLUMNS = tuple((name, tuple(columns))
                          for name, columns in contract.PERSONAL_COLUMNS.items())
_RECEIVER_COLUMNS = tuple((name, tuple(columns))
                          for name, columns in contract.RECEIVER_COLUMNS.items())
_PERSONAL_TABLES = tuple(name for name, _ in _PERSONAL_COLUMNS)
_RECEIVER_TABLES = tuple(name for name, _ in _RECEIVER_COLUMNS)
_PERSONAL_DDL = tuple(f"CREATE TABLE {name} ({ddl})"
                      for name, ddl in facts._DDL.items())
_RECEIVER_DDL = (tuple(f"CREATE TABLE {name} ({ddl})"
                       for name, ddl in receiver.TABLES.items())
                 + tuple(receiver.INDEXES) + tuple(receiver.TRIGGERS))
_META_KEYS = tuple(facts.META_KEYS)
_PROJECTION_COLUMNS = {
    name: frozenset(columns) for name, columns in
    (*_PERSONAL_COLUMNS, *_RECEIVER_COLUMNS)}
_PROJECTION_COLUMNS.update(meta=frozenset(("key", "value")),
    sqlite_master=frozenset(("type", "name", "tbl_name", "rootpage", "sql")),
    sqlite_schema=frozenset(("type", "name", "tbl_name", "rootpage", "sql")))
_INTEGRITY_RECIPES = frozenset(("integrity", "foreign_keys_check"))
_SOURCE_SIMPLE = {
    "integrity": "PRAGMA main.integrity_check",
    "foreign_keys_check": "PRAGMA main.foreign_key_check",
    "personal_triggers": "SELECT tbl_name FROM main.sqlite_master WHERE type='trigger'",
    "migration_version": "SELECT value FROM main.meta WHERE key='migration_version'",
    "personal_meta": "SELECT key,value FROM main.meta WHERE key IN (?,?,?,?) ORDER BY key",
    "temp_objects": "SELECT type,name,tbl_name,sql FROM sqlite_temp_master ORDER BY type,name",
}
_MAIN_OBJECTS = "SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name"


class SqlObservationDeadlineError(ValueError):
    def __init__(self, suffix):
        self.code = "SQL_OBSERVATION_DEADLINE_" + suffix
        super().__init__(self.code)


Error = SqlObservationDeadlineError


def _fail(suffix):
    raise SqlObservationDeadlineError(suffix) from None


def _note(primary):
    try:
        BaseException.add_note(primary, "SQL_OBSERVATION_DEADLINE_CLEANUP_FAILED")
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


def _require_guard(profile, *, exclusive=True):
    return lifecycle.require_guard(profile, exclusive=exclusive)


def _guard_verify(guard):
    return lifecycle.StorageGuard.verify(guard)


def _profile_identity(profile):
    return lifecycle.StorageProfile.__dict__["identity"].__get__(profile)


def _image_token(image):
    return committed.DeadlineCommittedImage.__dict__["deadline_token"].__get__(image)


def _image_connection(image):
    return committed.DeadlineCommittedImage.__dict__["connection"].__get__(image)


def _image_fingerprint(image):
    return committed.DeadlineCommittedImage.__dict__["source_fingerprint"].__get__(image)


def _image_active(image):
    return committed.DeadlineCommittedImage.require_active(image)


def _connect_memory():
    return sqlite3.connect(":memory:")


def _cursor(con):
    return sqlite3.Connection.cursor(con)


def _execute(cursor, sql, parameters=()):
    return sqlite3.Cursor.execute(cursor, sql, parameters)


def _fetchone(cursor):
    return sqlite3.Cursor.fetchone(cursor)


def _fetchmany(cursor, size):
    return sqlite3.Cursor.fetchmany(cursor, size)


def _cursor_close(cursor):
    return sqlite3.Cursor.close(cursor)


def _con_close(con):
    return sqlite3.Connection.close(con)


def _cursor_connection(cursor):
    return sqlite3.Cursor.__dict__["connection"].__get__(cursor)


def _setlimit(con, category, value):
    return sqlite3.Connection.setlimit(con, category, value)


def _set_progress_handler(con, callback, interval):
    return sqlite3.Connection.set_progress_handler(con, callback, interval)


def _set_authorizer(con, callback):
    return sqlite3.Connection.set_authorizer(con, callback)


def _row_factory(con):
    return sqlite3.Connection.__dict__["row_factory"].__get__(con)


def _text_factory(con):
    return sqlite3.Connection.__dict__["text_factory"].__get__(con)


def _typed_cell(value):
    return contract.typed_cell(value)


def _canonical_bytes(value):
    return contract.canonical_bytes(value)


def _projection_authorizer(*, checking_integrity=False):
    return preservation._authorizer(_PROJECTION_COLUMNS,
                                    checking_integrity=checking_integrity)


def _detach(value):
    if type(value) is dict:
        return {key: _detach(item) for key, item in value.items()}
    if type(value) is tuple:
        return tuple(_detach(item) for item in value)
    if type(value) is list:
        return [_detach(item) for item in value]
    return value


def _fingerprint(identity, value):
    if (type(value) is not dict or any(type(key) is not str for key in value)
            or set(value) != {"format_version", "profile_identity", "files"}
            or type(value["format_version"]) is not int or value["format_version"] != 1
            or type(value["profile_identity"]) is not list
            or len(value["profile_identity"]) != 4
            or any(type(item) is not str for item in value["profile_identity"])
            or value["profile_identity"] != list(identity)):
        _fail("INVALID_ARGUMENT")
    files = value["files"]
    if (type(files) is not dict or any(type(key) is not str for key in files)
            or set(files) != set(_SUFFIXES) or files[""] is None):
        _fail("INVALID_ARGUMENT")
    total = 0
    for suffix in _SUFFIXES:
        row = files[suffix]
        if row is None:
            continue
        if (type(row) is not dict or any(type(key) is not str for key in row)
                or set(row) != _FILE_FIELDS or type(row["byte_size"]) is not int
                or row["byte_size"] < 0 or type(row["sha256"]) is not str
                or len(row["sha256"]) != 64
                or any(c not in "0123456789abcdef" for c in row["sha256"])):
            _fail("INVALID_ARGUMENT")
        for name in _DECIMAL_FIELDS:
            item = row[name]
            if type(item) is not str:
                _fail("INVALID_ARGUMENT")
            try:
                actual = int(item)
            except ValueError:
                _fail("INVALID_ARGUMENT")
            if str(actual) != item:
                _fail("INVALID_ARGUMENT")
        if suffix == "-journal" and (row["byte_size"] != 0
                                      or row["sha256"] != _EMPTY_SHA256):
            _fail("INVALID_ARGUMENT")
        total += row["byte_size"]
    if total > readonly.MAX_BYTES_LIMIT:
        _fail("SQL_BUDGET")
    return _detach(value)


class _CursorSlot:
    __slots__ = ("cursor", "state", "child", "integrity")

    def __init__(self, *, child=None, integrity=False):
        self.cursor, self.state = None, _EMPTY
        self.child, self.integrity = child, integrity


class _Role:
    __slots__ = ("name", "con", "connection_state", "entered", "normal_exit",
                 "native_starts", "native_completions", "cleanup_starts",
                 "cursor_starts", "slots", "data_rows", "data_bytes",
                 "metadata_rows", "metadata_bytes", "pending_fees", "vm",
                 "previous", "progress_started", "authorizer_started",
                 "progress_restore", "authorizer_restore", "mode", "ref",
                 "index_names")

    def __init__(self, name):
        self.name, self.con = name, None
        self.connection_state = _BORROWED if name == "source" else _EMPTY
        self.entered = self.normal_exit = False
        self.native_starts = self.native_completions = self.cleanup_starts = 0
        self.cursor_starts = self.data_rows = self.data_bytes = 0
        self.metadata_rows = self.metadata_bytes = self.pending_fees = self.vm = 0
        self.slots, self.previous = [], [None, None]
        self.progress_started = self.authorizer_started = False
        self.progress_restore = self.authorizer_restore = _EMPTY
        self.mode, self.ref, self.index_names = _EMPTY, None, set()

    def snapshot(self):
        return {"connection_state": self.connection_state,
                "entered": self.entered, "normal_exit": self.normal_exit,
                "native_call_starts": self.native_starts,
                "native_call_completions": self.native_completions,
                "cleanup_call_starts": self.cleanup_starts,
                "cursor_create_starts": self.cursor_starts,
                "cursor_states": tuple(slot.state for slot in self.slots),
                "data_rows": self.data_rows, "data_typed_bytes": self.data_bytes,
                "metadata_rows": self.metadata_rows,
                "metadata_typed_bytes": self.metadata_bytes,
                "fee_complete": self.pending_fees == 0,
                "vm_reported_quanta": self.vm,
                "previous_limits": tuple(self.previous),
                "progress_restore_state": self.progress_restore,
                "authorizer_restore_state": self.authorizer_restore}


def _recipe(role, recipe, table, index_name):
    if (type(recipe) is not str or (table is not None and type(table) is not str)
            or (index_name is not None and type(index_name) is not str)):
        _fail("INVALID_ARGUMENT")
    source = role.name == "source"
    personal = role.name == "personal_reference"
    receiver_role = role.name == "receiver_reference"
    personal_tables = (*_PERSONAL_TABLES, "meta") if source else _PERSONAL_TABLES
    all_tables = (*personal_tables, *_RECEIVER_TABLES) if source else (
        _PERSONAL_TABLES if personal else _RECEIVER_TABLES)
    described_tables = all_tables if source else personal_tables
    if recipe in _SOURCE_SIMPLE:
        if not source or table is not None or index_name is not None:
            _fail("INVALID_ARGUMENT")
        return _SOURCE_SIMPLE[recipe], _META_KEYS if recipe == "personal_meta" else ()
    if recipe == "main_objects":
        if not (source or receiver_role) or table is not None or index_name is not None:
            _fail("INVALID_ARGUMENT")
        return _MAIN_OBJECTS, ()
    if recipe in {"schema_object", "table_xinfo"}:
        if not (source or personal) or table not in described_tables or index_name is not None:
            _fail("INVALID_ARGUMENT")
        if recipe == "schema_object":
            return "SELECT type,tbl_name,rootpage,sql FROM sqlite_schema WHERE name=?", (table,)
        return f"PRAGMA table_xinfo({table})", ()
    if recipe in {"index_list", "foreign_key_list"}:
        if table not in all_tables or index_name is not None:
            _fail("INVALID_ARGUMENT")
        return f"PRAGMA {recipe}({table})", ()
    if recipe == "index_info":
        if (not (source or personal) or table not in described_tables
                or index_name is None or (table, index_name) not in role.index_names):
            _fail("INVALID_ARGUMENT")
        quoted = index_name.replace("'", "''")
        return f"PRAGMA index_info('{quoted}')", ()
    if recipe == "raw_table":
        if not source or table not in (*_PERSONAL_TABLES, *_RECEIVER_TABLES) or index_name is not None:
            _fail("INVALID_ARGUMENT")
        columns = dict((*_PERSONAL_COLUMNS, *_RECEIVER_COLUMNS))[table]
        names = ",".join('"' + name + '"' for name in columns)
        return f'SELECT {names} FROM main."{table}"', ()
    if recipe in {"table_info", "table_sql", "index_sql", "index_xinfo"}:
        if not (source or receiver_role) or table not in _RECEIVER_TABLES:
            _fail("INVALID_ARGUMENT")
        if recipe in {"table_info", "table_sql"}:
            if index_name is not None:
                _fail("INVALID_ARGUMENT")
            if recipe == "table_info":
                return f"PRAGMA table_info({table})", ()
            return "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table,)
        if index_name is None or (table, index_name) not in role.index_names:
            _fail("INVALID_ARGUMENT")
        if recipe == "index_sql":
            return "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (index_name,)
        quoted = index_name.replace('"', '""')
        return f'PRAGMA index_xinfo("{quoted}")', ()
    _fail("INVALID_ARGUMENT")


class SqlObservationDeadlineLease:
    """One scope; borrow source Con/actual EX, own finite Cursor/ref slots."""
    __slots__ = ("_profile", "_image", "_token", "_identity", "_fingerprint",
                 "_guard", "_source", "_personal", "_receiver", "_phase",
                 "_enter_started", "_first", "_first_tb", "_normal_exit")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, profile, image, *, deadline_token):
        if (type(self) is not SqlObservationDeadlineLease
                or type(profile) is not lifecycle.StorageProfile
                or type(image) is not committed.DeadlineCommittedImage):
            _fail("INVALID_ARGUMENT")
        self._profile, self._image = profile, image
        self._token = _token(deadline_token)
        self._phase, self._enter_started, self._normal_exit = _NEW, False, False
        self._first = self._first_tb = None
        self._source = _Role("source")
        self._personal = _Role("personal_reference")
        self._receiver = _Role("receiver_reference")
        _clock(self._token)
        identity = _profile_identity(profile)
        if (type(identity) is not tuple or len(identity) != 4
                or any(type(item) is not str for item in identity)):
            _fail("INVALID_RESULT")
        self._identity = tuple(identity)
        self._fingerprint = _fingerprint(identity, _image_fingerprint(image))
        if _image_token(image) is not self._token:
            _fail("INVALID_ARGUMENT")
        _clock(self._token)
        if _image_active(image) is not None:
            _fail("INVALID_RESULT")
        _clock(self._token)
        guard = _require_guard(profile, exclusive=True)
        _clock(self._token)
        if type(guard) is not lifecycle.StorageGuard:
            _fail("INVALID_RESULT")
        self._guard = guard
        self._binding()

    @property
    def deadline_token(self):
        return self._token

    @property
    def evidence(self):
        return {"phase": self._phase, "retired": self._phase == _RETIRED,
                "failed": self._first is not None, "normal_exit": self._normal_exit,
                "source_borrowed": True, "profile_identity": self._identity,
                "source_fingerprint": _detach(self._fingerprint),
                "source": self._source.snapshot(),
                "personal_reference": self._personal.snapshot(),
                "receiver_reference": self._receiver.snapshot()}

    def _capture(self, failure, *, original_tb=None, cleanup=False):
        if self._first is None:
            self._first = failure
            self._first_tb = (BaseException.__dict__["__traceback__"].__get__(failure)
                              if original_tb is None else original_tb)
        elif cleanup and failure is not self._first:
            _note(self._first)

    def _raise_first(self):
        if self._first is not None:
            raise BaseException.with_traceback(self._first, self._first_tb) from None

    def _active_failure(self, failure):
        if self._phase == _ACTIVE:
            self._capture(failure)
            self._raise_first()
        raise failure

    def _binding(self):
        self._raise_first()
        _clock(self._token)
        if _image_token(self._image) is not self._token:
            _fail("INVALID_ARGUMENT")
        if _image_active(self._image) is not None:
            _fail("INVALID_RESULT")
        _clock(self._token)
        if _profile_identity(self._profile) != self._identity:
            _fail("INVALID_ARGUMENT")
        current = _require_guard(self._profile, exclusive=True)
        _clock(self._token)
        if current is not self._guard:
            _fail("INVALID_ARGUMENT")
        actual = _guard_verify(self._guard)
        _clock(self._token)
        if actual is not self._guard:
            _fail("INVALID_RESULT")
        con = _image_connection(self._image)
        if type(con) is not sqlite3.Connection:
            _fail("INVALID_RESULT")
        if self._source.con is not None and con is not self._source.con:
            _fail("INVALID_RESULT")
        _clock(self._token)
        return con

    def _public(self):
        self._raise_first()
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        self._binding()

    def _start(self, role):
        self._raise_first()
        _clock(self._token)
        maximum = _SOURCE_CALLS if role is self._source else _REFERENCE_CALLS
        if role.native_starts >= maximum:
            _fail("RESOURCE_BUDGET")
        role.native_starts += 1

    def _call(self, role, operation, *args, valid=None, record=None):
        self._start(role)
        if operation is _set_progress_handler:
            role.progress_started = True
        elif operation is _set_authorizer:
            role.authorizer_started = True
            role.mode = _UNKNOWN
        try:
            value = operation(*args)
        except sqlite3.Error:
            self._raise_first()
            _fail("SQL_INVALID")
        legal = valid is None or valid(value)
        if legal:
            role.native_completions += 1
            if record is not None:
                record(value)
        _clock(self._token)
        self._raise_first()
        if not legal:
            _fail("INVALID_RESULT")
        return value

    def _progress(self, role):
        def callback():
            try:
                self._raise_first()
                _clock(self._token)
                role.vm += _VM_INTERVAL
                cap = _SOURCE_VM if role is self._source else _REFERENCE_VM
                if role.vm > cap:
                    _fail("VM_BUDGET")
                return 0
            except BaseException as failure:
                self._capture(failure)
                return 1
        return callback

    def _policy(self, role, *, integrity=False):
        self._raise_first()
        _clock(self._token)
        base = _projection_authorizer(checking_integrity=integrity)
        if not callable(base):
            _fail("INVALID_RESULT")
        _clock(self._token)
        def callback(action, first, second, database, origin):
            try:
                self._raise_first()
                _clock(self._token)
                value = base(action, first, second, database, origin)
                if type(value) is not int or value not in (sqlite3.SQLITE_OK, sqlite3.SQLITE_DENY):
                    _fail("INVALID_RESULT")
                _clock(self._token)
                return value
            except BaseException as failure:
                self._capture(failure)
                return sqlite3.SQLITE_DENY
        return callback

    def _install_policy(self, role, *, integrity=False):
        callback = self._policy(role, integrity=integrity)
        self._call(role, _set_authorizer, role.con, callback, valid=lambda v: v is None)
        role.mode = "integrity" if integrity else "projection"

    def _setup(self, role, *, projection):
        self._call(role, _row_factory, role.con, valid=lambda v: v is None)
        self._call(role, _text_factory, role.con, valid=lambda v: v is str)
        for index, (category, target) in enumerate((
                (sqlite3.SQLITE_LIMIT_LENGTH, _LENGTH_LIMIT),
                (sqlite3.SQLITE_LIMIT_SQL_LENGTH, _SQL_LENGTH_LIMIT))):
            previous = self._call(role, _setlimit, role.con, category, -1,
                                  valid=lambda v: type(v) is int and v >= 0,
                                  record=lambda value: role.previous.__setitem__(index, value))
            actual = self._call(role, _setlimit, role.con, category, min(previous, target),
                                valid=lambda v: type(v) is int and v >= 0)
            if actual != previous:
                _fail("INVALID_RESULT")
        callback = self._progress(role)
        self._call(role, _set_progress_handler, role.con, callback, _VM_INTERVAL,
                   valid=lambda v: v is None)
        if projection:
            self._install_policy(role)

    def _reserve_cursor(self, role, *, child=None, integrity=False):
        self._raise_first()
        cap = _SOURCE_CURSORS if role is self._source else _REFERENCE_CURSORS
        if (role.cursor_starts >= cap
                or sum(slot.state == _OWNED for slot in role.slots) >= _LIVE_CURSORS):
            _fail("RESOURCE_BUDGET")
        slot = _CursorSlot(child=child, integrity=integrity)
        role.slots.append(slot)
        return slot

    def _open_cursor(self, role, slot):
        self._start(role)
        role.cursor_starts += 1
        slot.state = _ATTEMPTED
        try:
            value = _cursor(role.con)
        except sqlite3.Error:
            slot.state = _UNKNOWN
            self._raise_first()
            _fail("SQL_INVALID")
        except BaseException:
            slot.state = _UNKNOWN
            self._raise_first()
            raise
        if type(value) is sqlite3.Cursor:
            slot.cursor, slot.state = value, _OWNED
            role.native_completions += 1
        else:
            slot.state = _UNKNOWN
        _clock(self._token)
        self._raise_first()
        if slot.state != _OWNED:
            _fail("INVALID_RESULT")
        self._call(role, _cursor_connection, slot.cursor,
                   valid=lambda con: con is role.con)

    def _execute_slot(self, role, slot, sql, params=()):
        self._call(role, _execute, slot.cursor, sql, params,
                   valid=lambda value: value is slot.cursor)

    def _cleanup_clock(self):
        try:
            _clock(self._token)
        except BaseException as failure:
            self._capture(failure, cleanup=True)

    def _cleanup_call(self, role, operation, *args):
        # This helper never checks first/cap/guard to block physical cleanup.
        self._cleanup_clock()
        role.cleanup_starts += 1
        legal = False
        try:
            value = operation(*args)
            if value is not None:
                _fail("INVALID_RESULT")
            legal = True
        except BaseException as failure:
            self._capture(failure, cleanup=True)
        return legal

    def _close_cursor(self, role, slot):
        if slot.state != _OWNED:
            return
        slot.state = _ATTEMPTED
        legal = self._cleanup_call(role, _cursor_close, slot.cursor)
        slot.state = _CLOSED if legal else _UNKNOWN
        self._cleanup_clock()

    def _close_connection(self, role):
        if role is self._source or role.connection_state != _OWNED:
            return
        role.connection_state = _ATTEMPTED
        legal = self._cleanup_call(role, _con_close, role.con)
        role.connection_state = _CLOSED if legal else _UNKNOWN
        self._cleanup_clock()

    def _restore(self, role):
        if role.progress_started and role.progress_restore == _EMPTY:
            role.progress_restore = _ATTEMPTED
            legal = self._cleanup_call(role, _set_progress_handler, role.con, None, 0)
            role.progress_restore = _RETURNED if legal else _FAILED
            self._cleanup_clock()
        if role.authorizer_started and role.authorizer_restore == _EMPTY:
            role.authorizer_restore = _ATTEMPTED
            legal = self._cleanup_call(role, _set_authorizer, role.con,
                                       readonly._readonly_authorizer)
            role.authorizer_restore = _RETURNED if legal else _FAILED
            self._cleanup_clock()

    def _role_closed(self, role):
        return (all(slot.state == _CLOSED for slot in role.slots)
                and (not role.progress_started or role.progress_restore == _RETURNED)
                and (not role.authorizer_started or role.authorizer_restore == _RETURNED)
                and role.pending_fees == 0)

    def _close_role_cursors(self, role):
        for slot in reversed(role.slots):
            self._close_cursor(role, slot)
            child = slot.child
            if child is not None and child._phase != _RETIRED:
                child._finish()

    def _retire_reference(self, ref):
        role = ref._role
        self._close_role_cursors(role)
        self._restore(role)
        self._close_connection(role)
        role.normal_exit = (self._first is None and role.entered
                            and role.connection_state == _CLOSED and self._role_closed(role))
        ref._phase = _RETIRED

    def _cleanup(self):
        for role in (self._source, self._personal, self._receiver):
            self._close_role_cursors(role)
        self._restore(self._source)
        for role in (self._personal, self._receiver):
            if role.ref is not None and role.ref._enter_started and role.ref._phase != _RETIRED:
                self._retire_reference(role.ref)
        self._cleanup_clock()
        self._phase = _RETIRED

    def __enter__(self):
        self._raise_first()
        if self._phase != _NEW or self._enter_started:
            if self._phase == _ACTIVE:
                try:
                    _fail("CLOSED")
                except BaseException as failure:
                    self._capture(failure)
                    self._raise_first()
            _fail("CLOSED")
        self._enter_started = True
        try:
            self._source.con = self._binding()
            self._setup(self._source, projection=True)
            self._binding()
            self._source.entered = True
            self._phase = _ACTIVE
            return self
        except BaseException as failure:
            self._capture(failure)
            self._cleanup()
            self._raise_first()

    def _query(self, role, recipe_id, table, index_name):
        self._public()
        if role is not self._source:
            if role.ref is None or role.ref._phase != _ACTIVE or not role.entered:
                _fail("CLOSED")
        sql, params = _recipe(role, recipe_id, table, index_name)
        value = object.__new__(SqlRowsDeadlineLease)
        value._session, value._role = self, role
        value._recipe, value._table, value._index = recipe_id, table, index_name
        value._sql, value._params = sql, params
        value._phase, value._enter_started = _NEW, False
        value._slot = None
        value._entered = value._eof = value._normal_exit = value._failed = False
        value._rows = 0
        self._binding()
        return value

    def query(self, recipe_id, *, table=None, index_name=None):
        try:
            return self._query(self._source, recipe_id, table, index_name)
        except BaseException as failure:
            self._active_failure(failure)

    def _reference(self, role):
        self._public()
        if role.ref is not None:
            _fail("CLOSED")
        value = object.__new__(SqlReferenceDeadlineLease)
        value._session, value._role = self, role
        value._phase, value._enter_started = _NEW, False
        role.ref = value
        self._binding()
        return value

    def personal_reference(self):
        try:
            return self._reference(self._personal)
        except BaseException as failure:
            self._active_failure(failure)

    def receiver_reference(self):
        try:
            return self._reference(self._receiver)
        except BaseException as failure:
            self._active_failure(failure)

    def __exit__(self, _kind, primary, traceback):
        if self._phase != _ACTIVE:
            self._raise_first()
            _fail("CLOSED")
        if primary is not None:
            self._capture(primary, original_tb=traceback)
        try:
            self._binding()
        except BaseException as failure:
            self._capture(failure, cleanup=True)
        self._cleanup()
        if self._first is None:
            valid = self._source.entered and self._role_closed(self._source)
            for role in (self._personal, self._receiver):
                if role.connection_state != _EMPTY:
                    valid = valid and role.normal_exit and role.connection_state == _CLOSED
            if not valid:
                self._capture(SqlObservationDeadlineError("INVALID_RESULT"))
        if self._first is not None:
            if self._first is primary:
                return None
            self._raise_first()
        self._source.normal_exit = self._normal_exit = True
        return None


class SqlReferenceDeadlineLease:
    """A factory-only reference; only one literal memory Con and fixed DDL."""
    __slots__ = ("_session", "_role", "_phase", "_enter_started")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    @property
    def evidence(self):
        return self._role.snapshot()

    def _public(self):
        self._session._raise_first()
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        self._session._public()

    def __enter__(self):
        parent, role = self._session, self._role
        parent._raise_first()
        if self._phase != _NEW or self._enter_started:
            if self._phase == _ACTIVE:
                try:
                    _fail("CLOSED")
                except BaseException as failure:
                    parent._capture(failure)
                    parent._raise_first()
            _fail("CLOSED")
        if parent._phase != _ACTIVE:
            _fail("CLOSED")
        self._enter_started = True
        try:
            parent._public()
            parent._start(role)
            role.connection_state = _ATTEMPTED
            try:
                value = _connect_memory()
            except sqlite3.Error:
                role.connection_state = _UNKNOWN
                parent._raise_first()
                _fail("SQL_INVALID")
            except BaseException:
                role.connection_state = _UNKNOWN
                parent._raise_first()
                raise
            if type(value) is sqlite3.Connection:
                role.con, role.connection_state = value, _OWNED
                role.native_completions += 1
            else:
                role.connection_state = _UNKNOWN
            _clock(parent._token)
            parent._raise_first()
            if role.connection_state != _OWNED:
                _fail("INVALID_RESULT")
            parent._setup(role, projection=False)
            statements = _PERSONAL_DDL if role is parent._personal else _RECEIVER_DDL
            for sql in statements:
                slot = parent._reserve_cursor(role)
                parent._open_cursor(role, slot)
                parent._execute_slot(role, slot, sql)
                parent._close_cursor(role, slot)
                parent._raise_first()
            parent._install_policy(role)
            parent._binding()
            role.entered = True
            self._phase = _ACTIVE
            return self
        except BaseException as failure:
            parent._capture(failure)
            parent._retire_reference(self)
            parent._raise_first()

    def query(self, recipe_id, *, table=None, index_name=None):
        parent = self._session
        try:
            self._public()
            return parent._query(self._role, recipe_id, table, index_name)
        except BaseException as failure:
            if self._phase == _RETIRED and self._role.normal_exit:
                raise
            parent._active_failure(failure)

    def __exit__(self, _kind, primary, traceback):
        parent = self._session
        if self._phase != _ACTIVE:
            parent._raise_first()
            _fail("CLOSED")
        if primary is not None:
            parent._capture(primary, original_tb=traceback)
        try:
            parent._binding()
        except BaseException as failure:
            parent._capture(failure, cleanup=True)
        parent._retire_reference(self)
        if parent._first is not None:
            if parent._first is primary:
                return None
            parent._raise_first()
        if not self._role.normal_exit:
            parent._capture(SqlObservationDeadlineError("INVALID_RESULT"))
            parent._raise_first()
        return None


class SqlRowsDeadlineLease:
    """Factory-only bounded raw rows; never expose its owned Cursor/Con."""
    __slots__ = ("_session", "_role", "_recipe", "_table", "_index", "_sql",
                 "_params", "_phase", "_enter_started", "_slot", "_entered",
                 "_eof", "_rows", "_normal_exit", "_failed")

    def __init_subclass__(cls, **kwargs):
        _fail("INVALID_ARGUMENT")

    def __init__(self, *args, **kwargs):
        _fail("INVALID_ARGUMENT")

    @property
    def evidence(self):
        return {"phase": self._phase, "retired": self._phase == _RETIRED,
                "failed": self._failed if self._phase == _RETIRED else self._session._first is not None,
                "normal_exit": self._normal_exit,
                "cursor_state": _EMPTY if self._slot is None else self._slot.state,
                "rows_observed": self._rows, "eof_observed": self._eof}

    def _public(self):
        parent = self._session
        parent._raise_first()
        if self._phase != _ACTIVE:
            _fail("CLOSED")
        parent._public()
        if self._role is not parent._source and self._role.ref._phase != _ACTIVE:
            _fail("CLOSED")
        if self._slot is None or self._slot.state != _OWNED or self._eof:
            _fail("CLOSED")

    def _finish(self):
        self._failed = self._session._first is not None
        self._normal_exit = (not self._failed and self._entered
                             and self._slot is not None and self._slot.state == _CLOSED)
        self._phase = _RETIRED

    def __enter__(self):
        parent, role = self._session, self._role
        parent._raise_first()
        if self._phase != _NEW or self._enter_started:
            if self._phase == _ACTIVE:
                try:
                    _fail("CLOSED")
                except BaseException as failure:
                    parent._capture(failure)
                    parent._raise_first()
            _fail("CLOSED")
        if parent._phase != _ACTIVE:
            _fail("CLOSED")
        self._enter_started = True
        try:
            parent._public()
            if role is not parent._source and role.ref._phase != _ACTIVE:
                _fail("CLOSED")
            integrity = self._recipe in _INTEGRITY_RECIPES
            if role is parent._source:
                if integrity:
                    if any(slot.state == _OWNED for slot in role.slots):
                        _fail("INVALID_ARGUMENT")
                    parent._install_policy(role, integrity=True)
                elif any(slot.state == _OWNED and slot.integrity for slot in role.slots):
                    _fail("INVALID_ARGUMENT")
            self._slot = parent._reserve_cursor(role, child=self, integrity=integrity)
            parent._open_cursor(role, self._slot)
            parent._execute_slot(role, self._slot, self._sql, self._params)
            parent._binding()
            self._entered = True
            self._phase = _ACTIVE
            return self
        except BaseException as failure:
            parent._capture(failure)
            if self._slot is not None:
                parent._close_cursor(role, self._slot)
            self._finish()
            parent._raise_first()

    def _record_rows(self, values):
        role = self._role
        count = len(values)
        self._rows += count
        if self._recipe == "raw_table":
            role.data_rows += count
        else:
            role.metadata_rows += count
        role.pending_fees += count
        if self._recipe == "index_list":
            for row in values:
                if len(row) > 1 and type(row[1]) is str:
                    role.index_names.add((self._table, row[1]))

    def _row_limits(self):
        role = self._role
        if self._recipe == "raw_table":
            if role.data_rows > _DATA_ROWS:
                _fail("SQL_BUDGET")
        else:
            maximum = (_SOURCE_METADATA_ROWS if role is self._session._source
                       else _REFERENCE_METADATA_ROWS)
            if role.metadata_rows > maximum:
                _fail("SQL_BUDGET")
        if self._recipe == "table_xinfo" and self._rows > 64:
            _fail("INVALID_RESULT")

    def _fee(self, row):
        parent, role = self._session, self._role
        if self._recipe == "raw_table":
            expected = dict((*_PERSONAL_COLUMNS, *_RECEIVER_COLUMNS))[self._table]
            if len(row) != len(expected):
                _fail("INVALID_RESULT")
        typed = []
        for value in row:
            if value is not None and type(value) not in (int, float, bytes, str):
                _fail("INVALID_RESULT")
            parent._raise_first()
            _clock(parent._token)
            encoded_cell = _typed_cell(value)
            _clock(parent._token)
            typed.append(encoded_cell)
        parent._raise_first()
        _clock(parent._token)
        raw = _canonical_bytes(typed)
        if type(raw) is not bytes:
            _fail("INVALID_RESULT")
        if self._recipe == "raw_table":
            role.data_bytes += len(raw)
        else:
            role.metadata_bytes += len(raw)
        role.pending_fees -= 1
        # Actual returned encoding is charged before this post-return clock.
        _clock(parent._token)
        parent._raise_first()
        if self._recipe == "raw_table":
            if role.data_bytes > _DATA_BYTES:
                _fail("SQL_BUDGET")
        else:
            maximum = (_SOURCE_METADATA_BYTES if role is parent._source
                       else _REFERENCE_METADATA_BYTES)
            if role.metadata_bytes > maximum:
                _fail("SQL_BUDGET")

    def _fetch(self, *, many, size=1):
        parent, role = self._session, self._role
        self._public()
        if many and (type(size) is not int or not 1 <= size <= _FETCH_MAX):
            _fail("INVALID_ARGUMENT")
        actual_size = min(size, 65 - self._rows) if self._recipe == "table_xinfo" else size
        if actual_size < 1:
            _fail("CLOSED")
        parent._start(role)
        try:
            value = (_fetchmany(self._slot.cursor, actual_size) if many
                     else _fetchone(self._slot.cursor))
        except sqlite3.Error:
            parent._raise_first()
            _fail("SQL_INVALID")
        shape = ((type(value) is list and all(type(row) is tuple for row in value))
                 if many else value is None or type(value) is tuple)
        if shape:
            values = tuple(value) if many else (() if value is None else (value,))
            self._record_rows(values)
            if not values:
                self._eof = True
        else:
            values = ()
        legal = shape and (not many or len(values) <= actual_size)
        if legal:
            role.native_completions += 1
        _clock(parent._token)
        parent._raise_first()
        if not legal:
            _fail("INVALID_RESULT")
        self._row_limits()
        for row in values:
            self._fee(row)
        parent._binding()
        return values if many else (None if self._eof else values[0])

    def fetchone(self):
        try:
            return self._fetch(many=False)
        except BaseException as failure:
            if self._phase == _RETIRED and self._normal_exit:
                raise
            self._session._active_failure(failure)

    def fetchmany(self, size=64):
        try:
            return self._fetch(many=True, size=size)
        except BaseException as failure:
            if self._phase == _RETIRED and self._normal_exit:
                raise
            self._session._active_failure(failure)

    def __exit__(self, _kind, primary, traceback):
        parent, role = self._session, self._role
        if self._phase != _ACTIVE:
            parent._raise_first()
            _fail("CLOSED")
        if primary is not None:
            parent._capture(primary, original_tb=traceback)
        try:
            parent._binding()
        except BaseException as failure:
            parent._capture(failure, cleanup=True)
        parent._close_cursor(role, self._slot)
        if self._slot.integrity and parent._first is None:
            try:
                parent._install_policy(role)
            except BaseException as failure:
                parent._capture(failure, cleanup=True)
        self._finish()
        if parent._first is not None:
            if parent._first is primary:
                return None
            parent._raise_first()
        if not self._normal_exit:
            parent._capture(SqlObservationDeadlineError("INVALID_RESULT"))
            parent._raise_first()
        return None
