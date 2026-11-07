"""Consumer execution identity and private, resumable manual maintenance.

These meta records contain selection/progress only. Provider text and usage
belong to the two budget tables. No transport, queue or knowledge export lives
here; completing a manual execution belongs to its business transaction.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
import hashlib
import json
import os
import sqlite3
import uuid

from . import config, db, storage_admin, storage_lifecycle
from .budget import BudgetLedgerError
from .budget_contract import BudgetContractError, BudgetDeferred
from .tasks import TaskError

CONSUMERS = frozenset({"atomize", "judge", "vlm", "summary", "ask", "rerun_atomize", "backfill_v2"})
MAX_META_BYTES = 64 * 1024
_current = ContextVar("reno_model_consumer_execution", default=None)
_RECORD_KEYS = frozenset({"format_version", "consumer", "intent_sha256", "library_id", "continuation_id",
    "execution_generation", "state", "selection", "completed_items", "revision"})


def _fail(code="BUDGET_IDENTITY_INVALID"):
    raise BudgetLedgerError(code) from None


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            _fail()
    except (ValueError, AttributeError):
        _fail()
    return value


def _json(value):
    try:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
        if len(raw.encode("utf-8")) > MAX_META_BYTES:
            _fail("BUDGET_REQUEST_INVALID")
        return raw
    except (ValueError, TypeError, UnicodeError):
        _fail("BUDGET_REQUEST_INVALID")


def _items(value):
    if type(value) not in (tuple, list) or len(value) > 10000:
        _fail("BUDGET_REQUEST_INVALID")
    if any(type(item) is not str or not item or len(item) > 200 for item in value):
        _fail("BUDGET_REQUEST_INVALID")
    if len(set(value)) != len(value):
        _fail("BUDGET_REQUEST_INVALID")
    return tuple(value)


def _profile(value):
    if not isinstance(value, storage_lifecycle.StorageProfile):
        _fail()
    return value


def _no_transaction(connection):
    if connection is not None and connection.in_transaction:
        _fail("BUDGET_BUSINESS_TRANSACTION_ACTIVE")


def _connection_identity(con, profile):
    name = next((row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"), "")
    key = lambda path: os.path.normcase(os.path.abspath(os.fspath(path)))
    if not name or key(name) != key(profile.database):
        _fail()
    row = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()
    if row is None:
        _fail()
    return _uuid(row[0])


def _load_record(value):
    try:
        if type(value) is not str or len(value) > MAX_META_BYTES:
            _fail("BUDGET_LEDGER_INVALID")
        record = json.loads(value)
        if (type(record) is not dict or record.keys() != _RECORD_KEYS
                or type(record["format_version"]) is not int or record["format_version"] != 1):
            _fail("BUDGET_LEDGER_INVALID")
        if record["consumer"] not in CONSUMERS or record["state"] not in ("running", "completed"):
            _fail("BUDGET_LEDGER_INVALID")
        for field in ("library_id", "continuation_id"):
            _uuid(record[field])
        for field in ("execution_generation", "revision"):
            if type(record[field]) is not int or not (1 if field == "execution_generation" else 0) <= record[field] <= 2**63 - 1:
                _fail("BUDGET_LEDGER_INVALID")
        digest = record["intent_sha256"]
        if type(digest) is not str or len(digest) != 64 or any(char not in "0123456789abcdef" for char in digest):
            _fail("BUDGET_LEDGER_INVALID")
        selection, done = _items(record["selection"]), _items(record["completed_items"])
        if not set(done) <= set(selection) or (record["state"] == "completed" and set(done) != set(selection)):
            _fail("BUDGET_LEDGER_INVALID")
        _json(record)
        return record
    except (ValueError, TypeError, KeyError, AttributeError):
        _fail("BUDGET_LEDGER_INVALID")


@dataclass(frozen=True, slots=True)
class Execution:
    profile: storage_lifecycle.StorageProfile
    continuation_id: str
    execution_generation: int
    root_job_id: int | None
    consumer: str
    library_id: str | None = None
    intent_sha256: str | None = None
    selection: tuple[str, ...] = ()
    completed_items: tuple[str, ...] = ()
    tracked: bool = True

    @property
    def manual(self):
        return self.root_job_id is None

    @property
    def record_key(self):
        return None if not self.manual or not self.tracked else "budget:manual:execution:v1:" + self.continuation_id


def _manual_create(profile, consumer, descriptor, selection, new_execution):
    if type(descriptor) is not dict or type(new_execution) is not bool:
        _fail("BUDGET_REQUEST_INVALID")
    digest = hashlib.sha256(_json({"format_version": 1, "consumer": consumer, "intent": descriptor}).encode()).hexdigest()
    active_key = "budget:manual:active:v1:" + consumer + ":" + digest
    enabled = config.budget_settings()["budget_enabled"]
    with storage_admin.connection(profile) as con:
        try:
            con.execute("BEGIN IMMEDIATE")
            library = _connection_identity(con, profile)
            active = con.execute("SELECT value FROM meta WHERE key=?", (active_key,)).fetchone()
            record = None
            if active is not None:
                old_id = _uuid(active[0])
                row = con.execute("SELECT value FROM meta WHERE key=?", ("budget:manual:execution:v1:" + old_id,)).fetchone()
                if row is None:
                    _fail("BUDGET_LEDGER_INVALID")
                record = _load_record(row[0])
                if (record["library_id"] != library or record["consumer"] != consumer
                        or record["intent_sha256"] != digest or record["continuation_id"] != old_id):
                    _fail("BUDGET_IDENTITY_INVALID")
            if not enabled and (record is None or record["state"] == "completed"):
                # Legacy disabled calls have no durable budget execution. An
                # existing running execution is still restored below, so
                # disable cannot turn paid/unknown work into a fresh free call.
                con.commit()
                return Execution(profile, str(uuid.uuid4()), 1, None, consumer, library,
                                 digest, selection, (), False)
            if record is None or record["state"] == "completed" or new_execution:
                identifier = str(uuid.uuid4())
                record = {"format_version": 1, "consumer": consumer, "intent_sha256": digest,
                    "library_id": library, "continuation_id": identifier, "execution_generation": 1,
                    "state": "running", "selection": list(selection), "completed_items": [], "revision": 0}
                con.execute("INSERT INTO meta(key,value) VALUES(?,?)", ("budget:manual:execution:v1:" + identifier, _json(record)))
                con.execute("INSERT INTO meta(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                            (active_key, identifier))
            con.commit()
        except BaseException:
            try:
                con.rollback()
            except BaseException:
                pass
            raise
    return Execution(profile, record["continuation_id"], record["execution_generation"], None,
        consumer, library, digest, tuple(record["selection"]), tuple(record["completed_items"]))


def _manual(profile, consumer, descriptor, selection, new_execution):
    try:
        return _manual_create(profile, consumer, descriptor, selection, new_execution)
    except (BudgetLedgerError, BudgetContractError, BudgetDeferred):
        raise
    except (db.SchemaVersionError, sqlite3.Error, OSError, storage_lifecycle.StorageLifecycleError):
        _fail("BUDGET_LEDGER_INVALID")


@contextmanager
def execution(profile, consumer, intent_descriptor, *, selection=(), new_execution=False, business_connection=None):
    """Reuse a running explicit intent; only explicit fresh execution rotates it.

    A resumed dynamic selector uses its original persisted selection, rather
    than making remaining rows a new execution. Do not put kb_revision in the
    intent: a successful child commit must not invalidate its parent identity.
    """
    profile = _profile(profile)
    if type(consumer) is not str or consumer not in CONSUMERS:
        _fail("BUDGET_REQUEST_INVALID")
    _no_transaction(business_connection)
    selected = _items(selection)
    value = _current.get()
    if value is not None:
        if value.profile.identity != profile.identity or new_execution:
            _fail()
    else:
        from .worker import _execution_profile, execution_context
        job = execution_context()
        if job is not None:
            if _execution_profile() is None or _execution_profile().identity != profile.identity or new_execution:
                _fail()
            identifier = _uuid(job["continuation_id"])
            generation = job["execution_generation"]
            if type(generation) is not int or not 1 <= generation <= 2**63 - 1:
                _fail()
            value = Execution(profile, identifier, generation, job["job_id"], consumer)
        else:
            value = _manual(profile, consumer, intent_descriptor, selected, new_execution)
    token = _current.set(value)
    try:
        yield value
    finally:
        _current.reset(token)


def current_execution():
    return _current.get()


def _record_key(value):
    return "budget:manual:execution:v1:" + value.continuation_id


def _promote_manual(value):
    """Persist this same legacy UUID before any newly tracked reservation."""
    active_key = "budget:manual:active:v1:" + value.consumer + ":" + value.intent_sha256
    blocked = False
    try:
        with storage_admin.connection(value.profile) as con:
            try:
                con.execute("BEGIN IMMEDIATE")
                if _connection_identity(con, value.profile) != value.library_id:
                    _fail()
                row = con.execute("SELECT value FROM meta WHERE key=?", (_record_key(value),)).fetchone()
                if row is None:
                    record = {"format_version": 1, "consumer": value.consumer, "intent_sha256": value.intent_sha256,
                        "library_id": value.library_id, "continuation_id": value.continuation_id,
                        "execution_generation": value.execution_generation, "state": "running",
                        "selection": list(value.selection), "completed_items": list(value.completed_items), "revision": 0}
                    con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (_record_key(value), _json(record)))
                else:
                    record = _load_record(row[0])
                    if (record["continuation_id"], record["execution_generation"], record["library_id"], record["intent_sha256"],
                            record["consumer"], tuple(record["selection"])) != (
                            value.continuation_id, value.execution_generation, value.library_id, value.intent_sha256,
                            value.consumer, value.selection):
                        _fail()
                active = con.execute("SELECT value FROM meta WHERE key=?", (active_key,)).fetchone()
                if active is None:
                    con.execute("INSERT INTO meta(key,value) VALUES(?,?)", (active_key, value.continuation_id))
                elif active[0] != value.continuation_id:
                    old_id = _uuid(active[0])
                    previous = con.execute("SELECT value FROM meta WHERE key=?",
                        ("budget:manual:execution:v1:" + old_id,)).fetchone()
                    old = None if previous is None else _load_record(previous[0])
                    if (old is not None and old["state"] == "completed"
                            and old["continuation_id"] == old_id and old["library_id"] == value.library_id
                            and old["consumer"] == value.consumer and old["intent_sha256"] == value.intent_sha256):
                        # A completed execution has no pending sender to steal.
                        # Promote this same UUID and retain both histories.
                        changed = con.execute("UPDATE meta SET value=? WHERE key=? AND value=?",
                            (value.continuation_id, active_key, old_id))
                        if changed.rowcount != 1:
                            _fail("BUDGET_STATE_CONFLICT")
                    else:
                        # Unknown/running pointers remain a conservative hold.
                        blocked = True
                con.commit()
            except BaseException:
                try:
                    con.rollback()
                except BaseException:
                    pass
                raise
    except (BudgetLedgerError, BudgetContractError, BudgetDeferred):
        raise
    except (db.SchemaVersionError, sqlite3.Error, OSError, storage_lifecycle.StorageLifecycleError):
        _fail("BUDGET_LEDGER_INVALID")
    if blocked:
        raise BudgetDeferred("ledger_unavailable")


def _check_send_state(value, connection=None):
    """Only M01 invokes this after paid/unknown recovery, for a new send."""
    if storage_admin.profile().identity != value.profile.identity:
        raise BudgetDeferred("ledger_unavailable")
    if not value.manual:
        from .worker import _execution_profile, check_admission, execution_context
        job, profile = execution_context(), _execution_profile()
        if (job is None or profile is None or profile.identity != value.profile.identity
                or (job["job_id"], job["continuation_id"], job["execution_generation"]) !=
                (value.root_job_id, value.continuation_id, value.execution_generation)):
            _fail()
        if connection is None:
            check_admission()
        else:
            check_admission(connection=connection)
        return
    if connection is None and not value.tracked and config.budget_settings()["budget_enabled"]:
        _promote_manual(value)
    con = connection if connection is not None else db.connect(value.profile.database)
    try:
        if _connection_identity(con, value.profile) != value.library_id:
            _fail()
        row = con.execute("SELECT value FROM meta WHERE key=?", (_record_key(value),)).fetchone()
        active_key = "budget:manual:active:v1:" + value.consumer + ":" + value.intent_sha256
        active = con.execute("SELECT value FROM meta WHERE key=?", (active_key,)).fetchone()
        if row is None:
            if connection is None and not value.tracked and not config.budget_settings()["budget_enabled"]:
                return  # Untouched legacy call: no ledger/meta writes.
            raise BudgetDeferred("ledger_unavailable")
        if active is None:
            raise BudgetDeferred("ledger_unavailable")
        record = _load_record(row[0])
        if (active[0] != value.continuation_id or record["state"] != "running"
                or record["library_id"] != value.library_id or record["continuation_id"] != value.continuation_id
                or record["execution_generation"] != value.execution_generation
                or record["consumer"] != value.consumer or record["intent_sha256"] != value.intent_sha256
                or tuple(record["selection"]) != value.selection):
            raise BudgetDeferred("ledger_unavailable")
    finally:
        if connection is None:
            con.close()


def _check_send(value, connection=None):
    try:
        _check_send_state(value, connection)
    except (BudgetLedgerError, BudgetContractError, BudgetDeferred):
        raise
    except TaskError as exc:
        if exc.code == "TASK_OWNER_LOST":
            raise BudgetDeferred("ledger_unavailable") from None
        raise
    except (db.SchemaVersionError, sqlite3.Error, OSError, storage_lifecycle.StorageLifecycleError):
        _fail("BUDGET_LEDGER_INVALID")


@contextmanager
def call(value, suboperation_key, *, business_connection=None, version_tags=(), expects_json=False, candidate_guard=None):
    """Bind the consumer identity; M01 binds the complete actual request body."""
    if type(value) is not Execution or _current.get() is not value:
        _fail()
    _no_transaction(business_connection)
    from .model_budget import ModelScope, admission_check, scope
    if candidate_guard is not None and not callable(candidate_guard):
        _fail("BUDGET_REQUEST_INVALID")
    def admit(connection=None):
        _check_send(value, connection)
        if candidate_guard is not None:
            candidate_guard(connection)
    from . import source_budget
    source = source_budget.current_scope()
    home_binding, home_client = None, None
    if source is not None and source.home_execution is not None:
        from .sync_source_binding17 import HomeBindingFacts17, require_binding
        if type(source.home_execution) is HomeBindingFacts17:
            home_binding = require_binding(source.home_execution, profile=value.profile)
            home_client = source.home_client
    with admission_check(admit, dispatch_guard=admit), scope(ModelScope(
            profile=value.profile, continuation_id=value.continuation_id,
            execution_generation=value.execution_generation, suboperation_key=suboperation_key,
            root_job_id=value.root_job_id, business_connection=business_connection,
            version_tags=version_tags, expects_json=expects_json,
            home_execution=home_binding, home_client=home_client)):
        yield


def _checkpoint(con, value, *, completed_items=(), completed=False):
    """Record business progress in the caller's commit; never commit for it."""
    if type(value) is not Execution or type(completed) is not bool:
        _fail()
    if not value.manual:
        return
    if not con.in_transaction or _connection_identity(con, value.profile) != value.library_id:
        _fail("BUDGET_STATE_CONFLICT")
    row = con.execute("SELECT value FROM meta WHERE key=?", (_record_key(value),)).fetchone()
    if row is None:
        if not value.tracked:
            return
        _fail("BUDGET_LEDGER_INVALID")
    record = _load_record(row[0])
    if (record["continuation_id"], record["execution_generation"], record["library_id"], record["intent_sha256"],
            record["consumer"], tuple(record["selection"])) != (
            value.continuation_id, value.execution_generation, value.library_id, value.intent_sha256,
            value.consumer, value.selection):
        _fail()
    done = set(record["completed_items"]) | set(_items(completed_items))
    if not done <= set(record["selection"]) or (completed and done != set(record["selection"])):
        _fail("BUDGET_STATE_CONFLICT")
    record["completed_items"] = [item for item in record["selection"] if item in done]
    record["state"] = "completed" if completed or record["state"] == "completed" else "running"
    record["revision"] += 1
    con.execute("UPDATE meta SET value=? WHERE key=?", (_json(record), _record_key(value)))


def checkpoint(con, value, *, completed_items=(), completed=False):
    """Record business progress without changing the caller's transaction."""
    try:
        return _checkpoint(con, value, completed_items=completed_items, completed=completed)
    except (BudgetLedgerError, BudgetContractError, BudgetDeferred):
        raise
    except (db.SchemaVersionError, sqlite3.Error, OSError, storage_lifecycle.StorageLifecycleError):
        _fail("BUDGET_LEDGER_INVALID")
