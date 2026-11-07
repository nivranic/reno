"""Explicit released16 producers and ordinary production17 test migration.

Enter ``released16(monkeypatch)`` BEFORE initializing a historical database
and retain it for the WHOLE old producer/worker/State/SQL lifetime. Exit every
old owner before ``migrate_released16_to_current(profile)``. That helper calls
the ordinary production db.migrate; it never patches a current handshake or
validator and never installs tables/rewrites migration_version itself.

Do not wrap a modern17 child fixture in a yielding old16 parent fixture. Use
an old creation factory which returns AFTER the released scope has closed,
then migrate. A new current17 genesis fixture can use initialize_current17
directly. Never initialize17 and subsequently reinstall v1 or relabel it16.

Historical Cloud17-v1 fixtures are a separate declared schema variant. They
must retain an explicitly built historical reference/real old receipt phase;
they cannot use initialize_current17 and reverse-install v1 over its v2 DDL.
This file does not manufacture that historical17 image or any admission seal.
"""
from __future__ import annotations

from contextlib import closing, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from pathlib import Path
import sqlite3
import threading

from reno import budget_writer_witness as sql, db, home_recovery as home16
from reno import home_recovery17 as home17, migrations, source_preservation as preservation
from reno import source_preservation_witness as witness, storage_admin
from reno import storage_lifecycle as lifecycle, sync_protocol as protocol
from reno import sync_source_authority as authority, sync_source_readmit_schema as readmit1
from reno import sync_source_readmit_schema2 as readmit2, tasks, worker


RELEASED16_RULE_KEYS = frozenset({
    "claim_origin_version", "recovery_fence_version", "derivation_binding_version", "upload_intent_version",
    "json_roots", "integer_columns", "real_columns", "local_columns", "step_names", "media_extensions", "limits",
})
CURRENT17_CAPABILITIES = {
    "SOURCE_CONTROL_CONTINUITY_VERSION": ("source_control_continuity_version", 1),
    "SOURCE_READMIT_VERSION": ("source_readmit_version", 2),
    "SOURCE_DISPOSE_VERSION": ("source_dispose_version", 2),
}
_RELEASED = ContextVar("reno_test_released16_scope", default=None)
_VERSION_LOCK = threading.RLock()


def _released_schema_rules():
    """The complete original descriptor, with no added zero-valued keys.

    Deliberately do not call/filter the current schema_rules. A future unknown
    descriptor key must not silently become part of a historical fingerprint.
    These are the original actual protocol tables/constants; copies prevent
    callers from mutating them through a returned descriptor.
    """
    value = {
        "claim_origin_version": 1,
        "recovery_fence_version": 1,
        "derivation_binding_version": 1,
        "upload_intent_version": 1,
        "json_roots": {key: [kind.__name__ for kind in kinds] for key, kinds in protocol._JSON_ROOTS.items()},
        "integer_columns": protocol._INTEGER_COLUMNS,
        "real_columns": protocol._REAL_COLUMNS,
        "local_columns": protocol._LOCAL_COLUMNS,
        "step_names": protocol.STEP_NAMES,
        "media_extensions": protocol.MEDIA_EXTENSIONS,
        "limits": [protocol.MAX_MEDIA_BYTES, protocol.MAX_ROWS, protocol.MAX_CELL_BYTES,
            protocol.MAX_ROW_BYTES, protocol.MAX_BATCH_BYTES, protocol.MAX_JSON_DEPTH,
            protocol.MAX_JSON_NODES, protocol.MAX_URL_LENGTH],
    }
    # Match schema_rules' original detached JSON representation, including
    # list-versus-tuple semantics in the real handshake's canonical digest.
    import json
    return json.loads(protocol._json_bytes(value))


def _thread_local_snapshot():
    # Keep the actual references. Do not copy/create/reset a worker job, stop
    # event or profile to make a fixture look owned after a scope has leaked.
    return dict(vars(worker._current))


def _assert_thread_local_unchanged(before):
    current = vars(worker._current)
    if set(current) != set(before) or any(current[key] is not value for key, value in before.items()):
        raise AssertionError("Historical fixture leaked its real worker thread-local lifetime")


def _assert_no_live_owners(*, profile=None):
    if (worker.execution_context() is not None or sql.in_write_window() or sql._CURRENT.get() is not None
            or authority._CURRENT.get() is not None or witness._WINDOW.get() is not None
            or lifecycle.current_guard() is not None):
        raise AssertionError("Exit actual worker, State, SH and SQL owners before changing fixture schema")
    # A foreign-profile process lock is still an owner crossing this global
    # version switch. Do not infer a profile from the mutable default config.
    if getattr(tasks._lock_context, "database", None) is not None:
        raise AssertionError("Exit the real task process lock before changing fixture schema")
    with worker._guard:
        alive = any(thread.is_alive() for thread, _stop in worker._threads.values())
    if alive:
        raise AssertionError("Historical/current schema scopes require workers to finish their real lifetimes")


def _assert_released_runtime(expected_rules=None):
    if migrations.SCHEMA_VERSION != 16 or protocol.PROTOCOL_VERSION != 2:
        raise AssertionError("Released16 fixture needs its real schema16/protocol2 context")
    if any(hasattr(protocol, attribute) for attribute in CURRENT17_CAPABILITIES):
        raise AssertionError("Released16 fixture unexpectedly exposes a new source capability")
    if protocol.schema_rules is not _released_schema_rules:
        raise AssertionError("Released16 fixture replaced its exact original descriptor factory")
    rules = protocol.schema_rules()
    if set(rules) != RELEASED16_RULE_KEYS or expected_rules is not None and rules != expected_rules:
        raise AssertionError("Released16 fixture changed its original descriptor/fingerprint")


@contextmanager
def released16(monkeypatch):
    """Select original code versions only for a complete historical lifetime.

    This context patches version declarations and the exact old descriptor.
    It does not patch any producer, SQL validator, lock, worker execution,
    lease or handshake implementation. The caller must enter real owners
    inside this scope and close them before leaving it. No worker is stopped
    and no thread-local identity is fabricated or restored by this helper.
    """
    with _VERSION_LOCK:
        _assert_no_live_owners()
        before = _thread_local_snapshot()
        previous = _RELEASED.get()
        if previous is not None and previous[0] != threading.get_ident():
            raise AssertionError("A copied context cannot own historical fixture versions")
        with monkeypatch.context() as scoped:
            scoped.setattr(migrations, "SCHEMA_VERSION", 16)
            scoped.setattr(protocol, "PROTOCOL_VERSION", 2)
            for attribute in CURRENT17_CAPABILITIES:
                scoped.delattr(protocol, attribute, raising=False)
            scoped.setattr(protocol, "schema_rules", _released_schema_rules)
            expected = deepcopy(_released_schema_rules())
            token = _RELEASED.set((threading.get_ident(), object()))
            try:
                _assert_released_runtime(expected)
                yield
            finally:
                try:
                    _assert_no_live_owners()
                    _assert_thread_local_unchanged(before)
                    _assert_released_runtime(expected)
                finally:
                    _RELEASED.reset(token)
        _assert_thread_local_unchanged(before)


def _read_released16(con):
    row = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
    if row is None or row[0] != "16":
        raise AssertionError("Historical fixture must be a complete original16 image, not relabelled current17")
    _, _fingerprint, version, _projection = preservation._schema(con)
    if version != "16":
        raise AssertionError("Historical fixture has a different actual preserved schema")
    home16._actual_schema(con)
    names = {row[0].lower() for row in con.execute("SELECT name FROM main.sqlite_master")}
    if names & {name.lower() for name in readmit1.RESERVED_NAMES}:
        raise AssertionError("Original16 fixture already contains a newer readmission schema namespace")
    checks = [tuple(row) for row in con.execute("PRAGMA main.integrity_check")]
    if checks != [("ok",)]:
        raise AssertionError("Historical fixture database integrity is invalid")
    return db.knowledge_version(con)


def assert_released16_image(database):
    """Pure independent inspection; no version gate or DDL is changed here."""
    path = Path(database).absolute()
    if not path.is_file():
        raise AssertionError("Historical fixture database has not been initialized")
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as con:
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        return _read_released16(con)


def initialize_released16(database):
    """Use ordinary initialize inside the explicit old producer context."""
    selected = _RELEASED.get()
    if selected is None or selected[0] != threading.get_ident():
        raise AssertionError("Create original16 fixtures inside released16(monkeypatch)")
    _assert_released_runtime()
    result = db.initialize(Path(database))
    if result.version != 16:
        raise AssertionError("Original16 initialization selected an unexpected actual version")
    assert_released16_image(database)
    return result


def assert_current17_runtime():
    """Require real published globals; this function never creates a gate."""
    if _RELEASED.get() is not None or migrations.SCHEMA_VERSION != 17 or protocol.PROTOCOL_VERSION != 3:
        raise AssertionError("Current17 fixtures need the production schema17/protocol3 release")
    rules = protocol.schema_rules()
    if type(rules) is not dict:
        raise AssertionError("Production schema descriptor is unavailable")
    for attribute, (key, expected) in CURRENT17_CAPABILITIES.items():
        actual = getattr(protocol, attribute, None)
        if (type(actual) is not int or actual != expected
                or type(rules.get(key)) is not int or rules[key] != expected):
            raise AssertionError("Current17 fixture lacks a real published source capability")


def _read_current17(con):
    migrations.validate(con)
    home17._actual_schema(con)
    if readmit2.detect(con) != "admission2_dispose2":
        raise AssertionError("New/current fixture did not use the real default readmit2/dispose2 variant")
    handshake = protocol.schema_handshake(con)
    if (handshake["protocol_version"], handshake["migration_version"]) != (3, 17):
        raise AssertionError("Actual current fixture handshake is not production protocol3/schema17")
    return db.knowledge_version(con)


def initialize_current17(database):
    """New current image, using normal production initialization once."""
    assert_current17_runtime()
    _assert_no_live_owners()
    result = db.initialize(Path(database))
    if result.version != 17:
        raise AssertionError("Production initialization did not create the current17 schema")
    with closing(db.connect(Path(database))) as con:
        _read_current17(con)
    return result


def migrate_released16_to_current(profile):
    """Real guarded/backed-up migration of a complete original16 fixture.

    Existing original bytes/producer artifacts belong to their dedicated
    tests. This helper checks the true before schema, actual default after
    contract, preservation of library/epoch/revision, and the real migration
    result. It does not fabricate a17 source/admission/worker observation.
    """
    profile = lifecycle._profile(profile)
    assert_current17_runtime()
    _assert_no_live_owners(profile=profile)
    before_worker = _thread_local_snapshot()
    with lifecycle.shared(profile):
        before = assert_released16_image(profile.database)
    result = db.migrate(profile.database, profile=profile)
    if (result.version != 17 or result.changed is not True
            or result.backup_path is None or result.manifest_path is None
            or not result.backup_path.is_file() or not result.manifest_path.is_file()):
        raise AssertionError("Original16 fixture did not complete the ordinary protected/backed-up17 migration")
    with storage_admin.connection(profile) as con:
        after = _read_current17(con)
    if after != before:
        raise AssertionError("Fixture schema migration changed original knowledge library/epoch/revision")
    _assert_thread_local_unchanged(before_worker)
    _assert_no_live_owners(profile=profile)
    return result
