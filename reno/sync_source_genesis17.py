"""Selector-only first Home17 origin, published before one sealed State CAS.

The real HTTP claim capture, live worker leases, original private SQL windows
and permanent owner are independent proofs. None of them, nor the returned
core dictionary, grants current source admission or a paid/provider effect.
Full schema and journal verification stays outside each short SQL writer.
"""
from __future__ import annotations

import asyncio
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
import json
import os
import threading
import uuid

from . import budget_writer_witness as sql, db, home_recovery17 as schema17
from . import source_preservation_witness as pins, storage_lifecycle as lifecycle
from . import sync_access, sync_client, sync_source_authority as permanent
from . import sync_source_claim as claims, sync_source_execution as execution
from . import sync_state, tasks
from .sync_protocol import SyncError


_SEAL = object()
_CURRENT = ContextVar("reno_home_source_genesis17", default=None)
_CONTEXT = ContextVar("reno_home_source_genesis17_context", default=None)


def _fail(code):
    raise SyncError("First Home source origin could not be verified", "SYNC_SOURCE_GENESIS17_" + code, 409) from None


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _same(actual, expected):
    if actual != expected:
        _fail("CREATION_MISMATCH")


def _worker(profile, connection, *, expected=None):
    current = execution._job(profile, connection=connection)
    if expected is not None:
        _same(current, expected)
    row = connection.execute("SELECT lease_until FROM main.job WHERE job_id=?", (current["job_id"],)).fetchone()
    worker = connection.execute("SELECT owner_id,lease_until FROM main.worker_lease WHERE scope=?",
        (tasks.WORKER_SCOPE,)).fetchone()
    now = tasks.clock_ms()
    if (row is None or type(row[0]) is not int or row[0] <= now or worker is None
            or worker[0] != current["owner_id"] or type(worker[1]) is not int or worker[1] <= now):
        raise tasks.TaskError("任务执行租约已失效", "TASK_OWNER_LOST", 409)
    return current


class _SchemaCapture:
    """Own real readonly schema/identity observations and bounded identity pins."""
    def __init__(self, profile, store, stack):
        self.profile, self.store = profile, store
        with sql.connection(profile) as con:
            self.sha = schema17._actual_schema(con)
            self.cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            self.source = sync_access.identity(con)
            if self.source["handshake"]["migration_version"] != 17:
                _fail("SCHEMA_REQUIRED")
            self.identity = pins._IdentityCapture(store, con, stack)
            self.version = db.knowledge_version(con)
            self.job = _worker(profile, con)

    def short(self, con):
        self.identity.verify_for_commit(con)
        if (con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0] != "17"
                or con.execute("PRAGMA main.schema_version").fetchone()[0] != self.cookie):
            _fail("SCHEMA_CHANGED")
        version = db.knowledge_version(con)
        _same({key: version[key] for key in ("library_id", "library_epoch")},
            {key: self.version[key] for key in ("library_id", "library_epoch")})
        _worker(self.profile, con, expected=self.job)
        return version

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        with sql.connection(self.profile) as con:
            _same(schema17._actual_schema(con), self.sha)
            _same(sync_access.identity(con), self.source)
            self.short(con)


class _OriginPin:
    """One exact bounded committed origin, never a whole ledger traversal."""
    def __init__(self, owner, observed, record):
        _same(observed["entries"]["origins"].get(record["record_id"]), {"state": "committed", "record": record})
        directory = owner._directory(observed["ledger"], "origins")
        name = record["record_id"] + ".json"
        _same(owner._read(directory, name), record)
        self.owner, self.record = owner, deepcopy(record)
        self.evidence = owner.pin_index[(directory.path, name)]

    def short(self):
        directory, name, stream, signature, path_signature, sha = self.evidence
        directory.verify()
        if (permanent._signature(directory.entry_stat(name)) != path_signature
                or permanent._signature(os.fstat(stream.fileno())) != signature):
            _fail("ORIGIN_CHANGED")
        os.lseek(stream.fileno(), 0, os.SEEK_SET)
        raw = os.read(stream.fileno(), permanent.MAX_RECORD_BYTES + 1)
        if (len(raw) > permanent.MAX_RECORD_BYTES or permanent.hashlib.sha256(raw).digest() != sha
                or permanent._signature(directory.entry_stat(name)) != path_signature
                or permanent._signature(os.fstat(stream.fileno())) != signature):
            _fail("ORIGIN_CHANGED")


@dataclass(frozen=True, slots=True, init=False, repr=False)
class Genesis17Guard:
    """Exact lexical first-origin producer, never fresh execution admission."""
    owner: object
    store: object
    profile: object
    _schema: object
    _claim: object
    _core_json: str
    _entry_json: str
    _before_json: str
    _owner: tuple
    _seal: object
    _active: bool
    _context_token: object
    _writer_frame: object
    _origin: object
    _installed_json: object
    _failure: object

    def __init__(self, *, owner, schema, claim, core, entry, state, seal):
        if (seal is not _SEAL or type(owner) is not permanent.AuthorityGuard
                or type(claim) is not claims.InitialClaimCapture or state.get("source_execution") is not None):
            _fail("PRODUCER_REQUIRED")
        values = {"owner": owner, "store": owner.lock.store, "profile": owner.profile,
            "_schema": schema, "_claim": claim, "_core_json": execution._canonical(core),
            "_entry_json": execution._canonical(entry), "_before_json": execution._canonical(state),
            "_owner": _owner(), "_seal": seal, "_active": True, "_context_token": None,
            "_writer_frame": None, "_origin": None, "_installed_json": None, "_failure": None}
        for key, value in values.items():
            object.__setattr__(self, key, value)

    @property
    def core(self):
        return json.loads(self._core_json)

    def _context(self):
        if (type(self) is not Genesis17Guard or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or self._owner != _owner() or _CONTEXT.get() is not self):
            _fail("PRODUCER_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("PRODUCER_REQUIRED")
        object.__setattr__(self, "_context_token", _CONTEXT.set(self))
        self.owner._check()
        if self.store.check_cancelled is not None:
            self.store.check_cancelled()

    def _state(self, state):
        encoded = execution._canonical(state)
        if encoded != self._before_json and encoded != self._installed_json:
            _fail("STATE_CHANGED")
        self._claim.check(state=state)
        _same(self._claim.entry, json.loads(self._entry_json))
        core = self.core
        _same((state["binding"], state["local_epoch"]), (core["binding"], core["local_epoch"]))
        execution._peer(core["binding"])

    def _fixed(self, con, token):
        self._context()
        if self._writer_frame != (con, token) or sql.require_writer(con, profile=self.profile) is not token:
            _fail("SQL_WINDOW_REQUIRED")
        if token.schema_version != 17:
            _fail("SCHEMA_REQUIRED")
        version = self._schema.short(con)
        core = self.core
        _same((version["library_id"], version["library_epoch"]),
            (core["binding"]["home_library_id"], core["local_epoch"]))
        self._state(self.store.read())
        if self._origin is not None:
            self._origin.short()
        sql.check(token, connection=con, profile=self.profile)

    @contextmanager
    def _state_writer(self):
        self._context()
        if self._writer_frame is not None or sql.in_write_window():
            _fail("SQL_WINDOW_REQUIRED")
        try:
            with sql.connection(self.profile, write=True) as con:
                token = sql.require_writer(con, profile=self.profile)
                object.__setattr__(self, "_writer_frame", (con, token))
                self._fixed(con, token)
                sql.register_commit_check(token, lambda: self._fixed(con, token), connection=con, profile=self.profile)
                yield
                self._fixed(con, token)
        finally:
            object.__setattr__(self, "_writer_frame", None)

    def check(self, record=None):
        try:
            object.__setattr__(self, "_failure", None)
            self._context()
            if self._writer_frame is None:
                with self._state_writer():
                    pass
            else:
                self._fixed(*self._writer_frame)
            if record is not None:
                core, entry = self.core, json.loads(self._entry_json)
                if (record["record_kind"] != "origin" or record["core"] != core
                        or record["core_sha256"] != permanent.digest(core) or record["claim"] != entry["claim"]
                        or record["record_id"] != _origin_id(core)):
                    _fail("CREATION_MISMATCH")
        except BaseException as exc:
            object.__setattr__(self, "_failure", exc)
            raise

    def _committed(self, observed, record):
        self.check(record)
        origin = _OriginPin(self.owner, observed, record)
        installed = json.loads(self._before_json)
        installed.update(source_execution=self.core, source_authority={"format_version": 1,
            "core_sha256": permanent.digest(self.core), "origin": permanent.record_ref(record),
            "defer": None, "resume": None, "failure": None, "publication": None})
        object.__setattr__(self, "_origin", origin)
        object.__setattr__(self, "_installed_json", execution._canonical(installed))

    def check_state_install(self, core, origin_ref, state):
        try:
            object.__setattr__(self, "_failure", None)
            self._context()
            if self._writer_frame is None or self._origin is None:
                _fail("STATE_INSTALL_REQUIRED")
            _same(core, self.core)
            _same(origin_ref, permanent.record_ref(self._origin.record))
            self._state(state)
            _same(self.store.read(), state)
            self._fixed(*self._writer_frame)
        except BaseException as exc:
            object.__setattr__(self, "_failure", exc)
            raise


def _origin_id(core):
    return str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + permanent.digest(core)))


def _observe(owner, core, entry, proof):
    selector = (_origin_id(core), permanent.digest(core), permanent.digest(entry["claim"]))
    try:
        return owner.observe(resume_origin=selector)
    except SyncError as exc:
        if exc.code not in {"SYNC_SOURCE_AUTHORITY_ACTIVATION_INCOMPLETE", "SYNC_SOURCE_AUTHORITY_MISSING"}:
            raise
    active = permanent.validate_contract(owner._read(owner.root, permanent.INTENT_NAME), profile=owner.profile)
    _same(active["binding"], core["binding"])
    proof.check()
    owner.activate_files(active)
    proof.check()
    return owner.observe(resume_origin=selector)


@contextmanager
def _producer(owner, schema, claim, core, entry, state):
    value = Genesis17Guard(owner=owner, schema=schema, claim=claim, core=core, entry=entry, state=state, seal=_SEAL)
    token = _CURRENT.set(value)
    object.__setattr__(value, "_context_token", _CONTEXT.set(value))
    try:
        value.check()
        yield value
        value.check()
    finally:
        object.__setattr__(value, "_active", False)
        _CONTEXT.reset(value._context_token)
        _CURRENT.reset(token)


def _replay(owner, schema, captured, state, core):
    expected = {"format_version": 1, "core_sha256": permanent.digest(core),
        "origin": (state.get("source_authority") or {}).get("origin"),
        "defer": None, "resume": None, "failure": None, "publication": None}
    if state.get("source_authority") != expected or expected["origin"] is None:
        _fail("ORIGIN_UNKNOWN")
    captured.check(state=state)
    record = owner.read(expected["origin"])
    if record["record_kind"] != "origin" or record["core"] != core or record["record_id"] != _origin_id(core):
        _fail("ORIGIN_UNKNOWN")
    with sql.connection(schema.profile, write=True) as con:
        token = sql.require_writer(con, profile=schema.profile)
        schema.short(con)
        _same(state, owner.lock.store.read())
        captured.check(state=state)
        sql.check(token, connection=con, profile=schema.profile)
        sql.register_commit_check(token, lambda: (schema.short(con), _same(state, owner.lock.store.read()),
            captured.check(state=state)), connection=con, profile=schema.profile)
    return deepcopy(core)


def create_initial_core(*, profile, store, client, item_id):
    """Create from actual selectors only; return copied facts after all exits."""
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity
            or type(client) is not sync_client.SyncClient or type(item_id) is not int or item_id < 1 or _CURRENT.get() is not None):
        _fail("PRODUCER_REQUIRED")
    proof = None
    try:
        with store.reference_operation(), ExitStack() as stack:
            schema = _SchemaCapture(profile, store, stack)
            with claims.capture_initial_claim(client, source=schema.source, item_id=item_id) as captured:
                before = store.read()
                captured.check(state=before)
                _same((before["binding"]["home_library_id"], before["binding"]["home_node_id"], before["local_epoch"]),
                    (schema.source["library_id"], schema.source["node_id"], schema.version["library_epoch"]))
                entry = captured.entry
                current = schema.job
                core = execution._candidate_core(before, current, entry,
                    recovery_generation=before["recovery"]["generation"], recovery_stream=before["recovery"]["stream"])
                existing = before.get("source_execution")
                if existing is not None:
                    _same(existing, core)
                    if before.get("source_authority") is None or before["source_authority"]["origin"] is None:
                        _fail("ORIGIN_UNKNOWN")
                    with permanent.owned(store) as owner:
                        result = _replay(owner, schema, captured, before, core)
                else:
                    store.enable_source_readmit()
                    before = store.read()
                    captured.check(state=before)
                    if any(before[name] is not None for name in ("source_authority", "source_admission",
                            "source_recovery_pending", "source_control_pending", "budget_pending")):
                        _fail("STATE_CHANGED")
                    with permanent.owned(store, initialize=True) as owner:
                        with _producer(owner, schema, captured, core, entry, before) as proof:
                            observed = _observe(owner, core, entry, proof)
                            if observed["contract"] is None:
                                proof.check()
                                owner.activate_files({"format_version": 1, "facility_id": str(uuid.uuid4()),
                                    "owner_profile_sha256": permanent.profile_sha256(profile), "binding": before["binding"],
                                    "activated_at": tasks.utc_now()})
                                proof.check()
                                observed = owner.observe()
                            previous = observed["entries"]["origins"].get(_origin_id(core))
                            record = previous["record"] if previous is not None else {"format_version": 1,
                                "record_kind": "origin", "record_id": _origin_id(core), "binding": core["binding"],
                                "core": core, "core_sha256": permanent.digest(core), "claim": entry["claim"], "created_at": tasks.utc_now()}
                            proof.check(record)
                            owner.publish("origins", record, producer=proof)
                            proof.check(record)
                            observed = owner.observe()
                            proof._committed(observed, record)
                            with proof._state_writer():
                                result = store._install_source_genesis17(core, permanent.record_ref(record), producer=proof)
                            proof.check(record)
                            schema.full()
                captured.check(state=store.read())
            schema.full()
    except BaseException as exc:
        # Existing filesystem/State sanitizers can wrap a producer callback's
        # original exception. Restore only that actual primary object, never an
        # unrelated cleanup error or an earlier caught test rejection.
        original = None if proof is None else proof._failure
        current = exc
        for _ in range(8):
            if current is original and original is not None:
                raise original from None
            current = getattr(current, "__context__", None)
            if current is None:
                break
        raise
    return deepcopy(result)
