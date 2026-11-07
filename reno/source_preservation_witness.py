"""Private, live factory for actual SQL/State/permanent-file observations.

This comparison capability is not restore, readmit or dispatch authority. It
reads its own fixed SQL projection and actual owned State bytes; callers cannot
replace either permanent capture with a DTO. A trusted restore producer must
still bind the full certificate/private bundle to its permanent operation.
No facility, head, negative fact or State image is initialized or repaired.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os
import sqlite3
import threading

from . import storage_admin, storage_lifecycle as lifecycle, storage_root_inputs as inputs
from . import storage_source as source
from . import sync_state, sync_source_authority as authority
from . import migrations, sync_source_continuity_service as continuity_service
from . import storage_source_continuity_roots as cloud_roots, storage_source_authority_roots as home_roots
from . import source_preservation as preservation
from . import sync_media as safe
from . import sync_queue, sync_source_continuity_admin as identity_admin

_CURRENT = ContextVar("reno_private_permanent_witness_capture", default=None)
_WINDOW = ContextVar("reno_private_permanent_witness_sql_window", default=None)
_SEAL = object()


class WitnessCaptureError(ValueError):
    def __init__(self, code):
        self.code = "SOURCE_WITNESS_" + code
        super().__init__(self.code)


def _fail(code):
    raise WitnessCaptureError(code) from None


def _digest(value):
    # Inputs here came from bounded actual State/public SQL validators, not a
    # caller DTO. The per-record 64KiB authority hash cap is inapplicable to
    # whole State images or the entire four-table public projection.
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")).hexdigest()


def _state(store):
    before = _state_signature(store)
    try:
        raw = sync_state._read_bytes(store._directory, sync_state._STATE, sync_state.MAX_STATE_BYTES)
    except FileNotFoundError:
        if before is not None or _state_signature(store) is not None:
            _fail("STATE_CHANGED")
        return {"present": False, "byte_size": 0, "raw_sha256": None,
                "canonical_sha256": None, "value": None, "signature": None}
    value = store._read(store._directory)
    # The caller owns the State OS/thread lock. A foreign/noncooperating writer
    # still cannot swap bytes between the normalized observation and its hash.
    if (sync_state._read_bytes(store._directory, sync_state._STATE, sync_state.MAX_STATE_BYTES) != raw
            or _state_signature(store) != before):
        _fail("STATE_CHANGED")
    return {"present": True, "byte_size": len(raw), "raw_sha256": hashlib.sha256(raw).hexdigest(),
            "canonical_sha256": _digest(value), "value": value, "signature": before}


def _state_signature(store):
    try:
        return source.file_signature(store._directory.entry_stat(sync_state._STATE))
    except FileNotFoundError:
        return None


def _verify_state_full(store, before):
    # On Windows ctime is creation time. A same-size in-place overwrite plus
    # restored mtime can have an identical path signature. The short proof is
    # therefore bounded by a complete raw/canonical check after SQL ends.
    after = _state(store)
    if any(after[key] != before[key] for key in ("present", "byte_size", "raw_sha256", "canonical_sha256")):
        _fail("STATE_CHANGED")


class _IdentityCapture:
    """Pin the actual bounded local identity; never create a replacement."""

    def __init__(self, store, connection, stack):
        self.directory, self.name = store._directory, "node-identity.json"
        self.check_cancelled = store.check_cancelled
        self.cancelled = None
        try:
            self.before = source.file_signature(self.directory.entry_stat(self.name))
            self.stream = stack.enter_context(source.source_reader(self.directory, self.name))
            self.opened = source.file_signature(os.fstat(self.stream.fileno()))
            raw = self._read()
            self.node_id = identity_admin._node(raw)
            self.raw_sha256 = hashlib.sha256(raw).hexdigest()
            self.library_id, observed = sync_queue._local_identity(connection)
            if observed != self.node_id:
                _fail("IDENTITY_CHANGED")
            self.verify_for_commit(connection)
        except WitnessCaptureError:
            raise
        except (OSError, ValueError, safe.SyncError) as exc:
            if exc is self.cancelled:
                raise
            _fail("IDENTITY_INVALID")
        stack.callback(self.verify_for_commit, None)

    def _read(self):
        if self.check_cancelled is not None:
            try:
                self.check_cancelled()
            except BaseException as exc:
                self.cancelled = exc
                raise
        os.lseek(self.stream.fileno(), 0, os.SEEK_SET)
        raw = os.read(self.stream.fileno(), 4097)
        if len(raw) > 4096 or self.before[2] > 4096 or len(raw) != self.before[2]:
            _fail("IDENTITY_INVALID")
        return raw

    def verify_for_commit(self, connection):
        self.directory.verify()
        if (source.file_signature(self.directory.entry_stat(self.name)) != self.before
                or source.file_signature(os.fstat(self.stream.fileno())) != self.opened
                or hashlib.sha256(self._read()).hexdigest() != self.raw_sha256
                or source.file_signature(self.directory.entry_stat(self.name)) != self.before
                or source.file_signature(os.fstat(self.stream.fileno())) != self.opened):
            _fail("IDENTITY_CHANGED")
        if connection is not None:
            row = connection.execute("SELECT value FROM main.meta WHERE key='library_id'").fetchone()
            if row is None or row[0] != self.library_id:
                _fail("IDENTITY_CHANGED")


class _CheckpointCache:
    """Actual pending outboxes, fully read before the SQL write reservation."""

    def __init__(self, store, state, stack):
        self.store, self.state, self.files = store, state, []
        self.outboxes = []
        value = state["value"]
        references = {}
        if value is not None:
            if value["pending_publication"] is not None:
                item = value["pending_publication"]
                references[item["publication_id"]] = (item, value["legacy_pending_stream"])
            attempt = value["recovery_attempt"]
            if attempt is not None and attempt["publication_id"] not in references:
                item = store.find_publication(attempt["publication_id"], audit=True)
                if item is None:
                    _fail("CHECKPOINT_INVALID")
                references[item["publication_id"]] = (item, attempt["stream"])
        if references:
            directory = stack.enter_context(safe._Directory(store.data_dir / sync_state._OUTBOX, base=store.data_dir))
            total = 0
            for identifier, (metadata, stream) in sorted(references.items()):
                self.check()
                total += metadata["byte_size"]
                if total > preservation.MAX_OUTBOX_TOTAL_BYTES:
                    _fail("CHECKPOINT_LIMIT")
                name = identifier + ".json"
                before = source.file_signature(directory.entry_stat(name))
                held = stack.enter_context(directory.open_file(name))
                opened = source.file_signature(os.fstat(held.fileno()))
                if before[:4] != opened[:4] or before[2] != metadata["byte_size"]:
                    _fail("CHECKPOINT_CHANGED")
                envelope = store.read_publication(metadata, proved_stream=stream)
                self.files.append((directory, name, held, before, opened, metadata["sha256"]))
                self.outboxes.append({**metadata, "semantic_sha256": _digest(envelope)})
        self.verify_full()
        # Registered after every held directory/file context, so the final
        # bounded SHA happens before handles close and after SQL has ended.
        stack.callback(self.verify_full)

    def check(self):
        if self.store.check_cancelled is not None:
            self.store.check_cancelled()

    def verify_for_commit(self):
        self.check()
        for directory, name, held, before, opened, _sha in self.files:
            self.check()
            directory.verify()
            if (source.file_signature(directory.entry_stat(name)) != before
                    or source.file_signature(os.fstat(held.fileno())) != opened):
                _fail("CHECKPOINT_CHANGED")

    def verify_full(self):
        self.verify_for_commit()
        for directory, name, held, before, opened, expected in self.files:
            self.check()
            os.lseek(held.fileno(), 0, os.SEEK_SET)
            found, remaining = hashlib.sha256(), before[2]
            while remaining:
                self.check()
                chunk = os.read(held.fileno(), min(128 * 1024, remaining))
                if not chunk:
                    _fail("CHECKPOINT_CHANGED")
                remaining -= len(chunk)
                found.update(chunk)
            self.check()
            if (os.read(held.fileno(), 1) or found.hexdigest() != expected
                    or source.file_signature(directory.entry_stat(name)) != before
                    or source.file_signature(os.fstat(held.fileno())) != opened):
                _fail("CHECKPOINT_CHANGED")

    def public(self, *, root_job_id, library_id, node_id):
        value = self.state["value"]
        if value is None:
            if root_job_id is not None:
                _fail("CHECKPOINT_INVALID")
            return {"state_present": False,
                "absence_sha256": _digest({"format_version": 1, "state": "absent"}),
                "state_sha256": None, "state_bytes": 0, "canonical_state_sha256": None,
                "state_format_version": None, "source_execution_sha256": None,
                "source_execution_lineage": None, "outboxes": []}
        if (value["binding"]["home_library_id"], value["binding"]["home_node_id"]) != (library_id, node_id):
            _fail("CHECKPOINT_INVALID")
        core = value.get("source_execution")
        if core is not None and core["root_job_id"] != root_job_id:
            _fail("CHECKPOINT_INVALID")
        return {"state_present": True, "absence_sha256": None,
            "state_sha256": self.state["raw_sha256"], "state_bytes": self.state["byte_size"],
            "canonical_state_sha256": self.state["canonical_sha256"], "state_format_version": value["format_version"],
            "source_execution_sha256": None if core is None else _digest(core),
            "source_execution_lineage": None if core is None else {key: core[key] for key in (
                "root_job_id", "continuation_id", "execution_generation")}, "outboxes": deepcopy(self.outboxes)}


class _SqlWindow:
    def __init__(self, connection, profile, store, ownership, guard, state, projection, cloud, home, checkpoint, identity, writable):
        self.connection, self.profile, self.store = connection, profile, store
        self._writable = writable
        self.ownership, self.guard = ownership, guard
        self.thread, self.generation = threading.get_ident(), 0
        self.state, self.sql_sha256 = state, _digest(projection)
        self.cloud, self.home = cloud, home
        self.checkpoint = checkpoint
        self.identity = identity

    def observe(self, statement):
        if continuity_service._transaction_boundary(statement):
            self.generation += 1

    def check(self):
        if (_WINDOW.get() is not self or self.thread != threading.get_ident()
                or self.generation != 0 or not self.connection.in_transaction
                or not (self.connection.row_factory is None or self.connection.row_factory is sqlite3.Row)):
            _fail("OWNER_REQUIRED")
        self.ownership.verify()
        if lifecycle.require_guard(self.profile) is not self.guard:
            _fail("OWNER_REQUIRED")

    def verify_for_commit(self):
        self.check()
        self.identity.verify_for_commit(self.connection)
        if _state_signature(self.store) != self.state["signature"]:
            _fail("STATE_CHANGED")
        if _digest(inputs.read_continuity_projection(self.connection)) != self.sql_sha256:
            _fail("SQL_CHANGED")
        self.checkpoint.verify_for_commit()
        for captured in (self.cloud, self.home):
            short = getattr(captured, "verify_for_commit", None)
            if not callable(short):
                _fail("SHORT_PROOF_REQUIRED")
            short()
        _resolve_state(self.state["value"], self.home)


@contextmanager
def sql_window_locked(*, profile, store, writable=False):
    """Own the SQL connection and exact transaction under prior State + SH.

    This is an explicit local comparison/maintenance producer window. It never
    starts a worker or platform/model request and has no transport permissions.
    Private connection ownership preserves any subsequently installed SQL
    authorizer; the trace records a transaction count, never statement text.
    """
    profile = lifecycle._profile(profile)
    if (type(writable) is not bool or type(store) is not sync_state.StateStore
            or store.profile is None or store.profile.identity != profile.identity or _WINDOW.get() is not None):
        _fail("OWNER_REQUIRED")
    ownership = authority._Ownership(store)
    ownership.verify()
    guard = lifecycle.require_guard(profile)
    con = migrations._open(profile.database, readonly=not writable)
    try:
        with ExitStack() as stack:
            storage_admin.from_connection(con, profile=profile)
            version = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
            if version is None or version[0] != "17":
                _fail("SCHEMA_REQUIRED")
            con.execute("PRAGMA busy_timeout=0")
            # The long immutable SHA/file closure belongs to a read snapshot,
            # before this producer takes its SQL write reservation.
            con.execute("BEGIN")
            identity = _IdentityCapture(store, con, stack)
            state = _state(store)
            stack.callback(_verify_state_full, store, state)
            checkpoint = _CheckpointCache(store, state, stack)
            projection = inputs.read_continuity_projection(con)
            # These readers remain pinned across the bounded maintenance
            # comparison/copy, not only their initial preview. Keep their
            # lifetime within the same 120-second maintenance ceiling; normal
            # public root captures retain their shorter default.
            cloud_limits = cloud_roots.CaptureLimits(deadline_seconds=120)
            home_limits = home_roots.CaptureLimits(deadline_seconds=120)
            cloud = stack.enter_context(cloud_roots.capture_locked(profile, sql_projection=projection,
                limits=cloud_limits, check_cancelled=store.check_cancelled))
            if state["value"] is None:
                home = stack.enter_context(home_roots.capture_absence_locked(profile,
                    format_version=2, limits=home_limits, check_cancelled=store.check_cancelled))
            else:
                home = stack.enter_context(home_roots.capture_locked(profile, binding=state["value"]["binding"],
                    format_version=2, limits=home_limits, check_cancelled=store.check_cancelled))
            if any(capture.summary["presence"] not in {"absent", "present"} or capture.unknown
                   for capture in (cloud, home)):
                _fail("INCOMPLETE")
            _resolve_state(state["value"], home)
            cloud.verify_source()
            home.verify_source()
            con.rollback()
            con.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
            window = _SqlWindow(con, profile, store, ownership, guard, state, projection, cloud, home, checkpoint, identity, writable)
            con.set_trace_callback(window.observe)
            token = _WINDOW.set(window)
            try:
                window.verify_for_commit()
                yield con
                window.verify_for_commit()
                guard.verify()
                if writable:
                    con.commit()
                else:
                    con.rollback()
            finally:
                _WINDOW.reset(token)
                con.set_trace_callback(None)
                con.rollback()
            # Both context-managed readers perform their complete SHA checks
            # here, outside the SQL writer. Failed postcommit verification
            # cannot grant a successful result or rewind its permanent receipt.
    finally:
        con.rollback()
        con.close()


def _resolve_state(state, home):
    if state is None or state["format_version"] != 5:
        return
    # Validate the full actual State before interpreting any public pointer.
    projection = sync_state.source_readmit_projection(state)
    core = projection["core_sha256"]
    rows = {(row["kind"], row["record_id"]): row for row in home.references}

    def resolve(kind, identifier, sha):
        row = rows.get((kind, identifier))
        if (row is None or row["state"] != "committed" or row["record_sha256"] != sha
                or row["core_sha256"] != core):
            _fail("INCOMPLETE")
        return row

    actual = projection["source_authority"]
    if actual is not None:
        for slot in ("origin", "defer", "resume", "failure", "publication"):
            ref = actual[slot]
            if ref is None:
                continue
            row = resolve("origins" if slot == "origin" else "publications" if slot == "publication" else "controls",
                          ref["record_id"], ref["record_sha256"])
            if slot in {"defer", "resume", "failure"} and not any(
                    edge["kind"] == "original_control" and edge["original_kind"] == {
                        "defer": "budget_defer", "resume": "budget_resume", "failure": "source_fail"}[slot]
                    for edge in row["refs"]):
                _fail("INCOMPLETE")
    admission = projection["source_admission"]
    if admission is not None:
        row = resolve("receipts", admission["admission_id"], admission["record_sha256"])
        if not any(edge["kind"] == "admission" and {key: edge.get(key) for key in admission} == admission
                   for edge in row["refs"]):
            _fail("INCOMPLETE")
    pending = projection["source_recovery_pending"]
    if pending is not None:
        row = resolve("requests", pending["request_id"], pending["intent_sha256"])
        if not any(edge["kind"] == "recovery_request" and edge["request_id"] == pending["request_id"]
                   and edge["request_sha256"] == pending["request_sha256"]
                   and edge["request_kind"] == pending["kind"] and edge["core_sha256"] == pending["core_sha256"]
                   for edge in row["refs"]):
            _fail("INCOMPLETE")


class WitnessCapture:
    __slots__ = ("_seal", "_profile", "_connection", "_store", "_ownership", "_guard",
                 "_thread", "_active", "_state", "_sql_sha256", "_cloud", "_home")

    def __init__(self, *, seal, profile, connection, store, ownership, guard, state, projection, cloud, home):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._profile, self._connection, self._store = seal, profile, connection, store
        self._ownership, self._guard = ownership, guard
        self._thread, self._active = threading.get_ident(), True
        self._state, self._sql_sha256 = state, _digest(projection)
        self._cloud, self._home = cloud, home

    def _check(self):
        if (_CURRENT.get() is not self or not self._active or self._seal is not _SEAL
                or self._thread != threading.get_ident() or not self._connection.in_transaction
                or not (self._connection.row_factory is None or self._connection.row_factory is sqlite3.Row)):
            _fail("OWNER_REQUIRED")
        window = _WINDOW.get()
        if (type(window) is not _SqlWindow or window.connection is not self._connection
                or window.store is not self._store or window.profile.identity != self._profile.identity):
            _fail("OWNER_REQUIRED")
        window.check()
        self._ownership.verify()
        if lifecycle.require_guard(self._profile) is not self._guard:
            _fail("OWNER_REQUIRED")
        storage_admin.from_connection(self._connection, profile=self._profile)
        schema = self._connection.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
        if schema is None or schema[0] != "17":
            _fail("SCHEMA_REQUIRED")

    def verify_source(self):
        self._check()
        # The owned window captured all content before BEGIN IMMEDIATE;
        # short physical/SQL proofs bind that capture during this transaction.
        _WINDOW.get().verify_for_commit()
        if any(capture.summary["presence"] not in {"absent", "present"} or capture.unknown
               for capture in (self._cloud, self._home)):
            _fail("INCOMPLETE")
        _resolve_state(self._state["value"], self._home)

    def capsules(self):
        self.verify_source()
        return {"cloud_continuity": self._cloud.summary, "home_authority": self._home.summary}

    def references(self):
        self.verify_source()
        return {"cloud_continuity": list(self._cloud.references), "home_authority": list(self._home.references)}

    def state_observation(self):
        """Public state hashes/refs only; private URL and State body stay local."""
        self.verify_source()
        value = self._state["value"]
        projection = None
        if value is not None and value["format_version"] == 5:
            projection = sync_state.source_readmit_projection(value)
        return {"present": self._state["present"], "byte_size": self._state["byte_size"],
                "raw_sha256": self._state["raw_sha256"], "canonical_sha256": self._state["canonical_sha256"],
                "state_format_version": None if value is None else value["format_version"],
                "authority_projection": deepcopy(projection)}

    def checkpoint(self, *, root_job_id, library_id, node_id):
        """Original nine keys, with actual version5 State only in this window."""
        self.verify_source()
        return _WINDOW.get().checkpoint.public(root_job_id=root_job_id, library_id=library_id, node_id=node_id)

    def identity(self):
        """The actual node/profile identity captured before SQL writer entry."""
        self.verify_source()
        actual = _WINDOW.get().identity
        return {"library_id": actual.library_id, "node_id": actual.node_id}


def require_capture(value, *, connection, profile, store):
    """Only this factory's currently live actual owner can be consumed."""
    if (type(value) is not WitnessCapture or value._connection is not connection
            or value._store is not store or value._profile.identity != lifecycle._profile(profile).identity):
        _fail("FACTORY_REQUIRED")
    value.verify_source()
    return value


def require_read_capture(value, *, connection, profile):
    """Require this factory's original read window before any long source I/O.

    A writable window remains writable proof even if its caller later changes
    query_only. Neither a caller boolean nor a reused transaction is a reader.
    """
    if type(value) is not WitnessCapture:
        _fail("FACTORY_REQUIRED")
    require_capture(value, connection=connection, profile=profile, store=value._store)
    window = _WINDOW.get()
    if (window._writable is not False
            or connection.execute("PRAGMA query_only").fetchone()[0] != 1):
        _fail("READ_WINDOW_REQUIRED")
    return value


@contextmanager
def capture_locked(connection, *, profile, store):
    """Caller already owns actual State reference_operation, SH and SQL read.

    Permanent readers take no writer locks; a concurrent change rejects this
    comparison. No network or paid work belongs in this lifetime.
    """
    profile = lifecycle._profile(profile)
    if (type(connection) is not sqlite3.Connection or not connection.in_transaction
            or not (connection.row_factory is None or connection.row_factory is sqlite3.Row)
            or type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity or _CURRENT.get() is not None):
        _fail("OWNER_REQUIRED")
    window = _WINDOW.get()
    if (type(window) is not _SqlWindow or window.connection is not connection
            or window.store is not store or window.profile.identity != profile.identity):
        _fail("OWNER_REQUIRED")
    window.check()
    ownership = authority._Ownership(store)
    ownership.verify()
    guard = lifecycle.require_guard(profile)
    storage_admin.from_connection(connection, profile=profile)
    schema = connection.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
    if schema is None or schema[0] != "17":
        _fail("SCHEMA_REQUIRED")
    window.verify_for_commit()
    projection = inputs.read_continuity_projection(connection)
    value = WitnessCapture(seal=_SEAL, profile=profile, connection=connection, store=store,
        ownership=ownership, guard=guard, state=window.state, projection=projection,
        cloud=window.cloud, home=window.home)
    token = _CURRENT.set(value)
    try:
        value.verify_source()
        yield value
        value.verify_source()
    finally:
        value._active = False
        _CURRENT.reset(token)
