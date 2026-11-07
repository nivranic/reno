"""Detached original Home17 facts, read from SQL, State and committed origin.

The selector-only factory is deliberately independent of current HTTP claims,
worker lease expiry and admission. Its result supplies original budget keys,
never permission to perform another physical source or model opportunity.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import os
import uuid

from . import budget, budget_writer_witness as sql, db, home_recovery17 as schema17
from . import source_preservation_witness as witness, storage_lifecycle as lifecycle
from . import storage_admin
from . import storage_backups as backups, storage_source as physical
from . import storage_home_recovery_dependency17 as files
from . import sync_access, sync_source_authority as permanent
from . import sync_source_execution as execution, sync_state, tasks
from . import worker
from .budget_contract import BudgetDeferred
from .sync_protocol import SyncError, validate_ingest_url


_SEAL = object()


def _fail(code):
    raise SyncError("Original Home source facts could not be verified",
        "SYNC_SOURCE_BINDING17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("IDENTITY_CHANGED")


def _selectors(profile, store, item_id):
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity
            or type(item_id) is not int or item_id < 1):
        _fail("FACTORY_REQUIRED")
    return profile


def _root(con, core):
    # Historical facts need the real original root, not a current owner/lease
    # or a reconstruction of the thread-local execution context.
    row = con.execute("SELECT kind,scope_key,video_id,payload_json,continuation_id,execution_generation "
        "FROM main.job WHERE job_id=?", (core["root_job_id"],)).fetchone()
    tasks._require_sync_job(row)
    _same((row["continuation_id"], row["execution_generation"]),
        (core["continuation_id"], core["execution_generation"]))


@dataclass(frozen=True, slots=True, init=False, repr=False)
class HomeBindingFacts17:
    """Closed, detached original facts; no live transport or writer right."""
    profile: object
    store: object
    core_json: str
    submitted_url: str
    _origin_json: str
    _completed_kind: str | None
    _seal: object

    def __init__(self, *, profile, store, core, submitted_url, origin_ref, seal, completed_kind=None):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        for name, value in (("profile", profile), ("store", store),
                ("core_json", execution._canonical(core)), ("submitted_url", submitted_url),
                ("_origin_json", execution._canonical(origin_ref)), ("_seal", seal),
                ("_completed_kind", completed_kind)):
            object.__setattr__(self, name, value)

    @property
    def core(self):
        require_binding(self)
        return json.loads(self.core_json)

    @property
    def origin_ref(self):
        require_binding(self)
        return json.loads(self._origin_json)

    @property
    def namespace(self):
        core = self.core
        return execution._sha({key: core[key] for key in ("binding", "item_id", "submitted_url_sha256")})

    def operation(self, kind, key, input_sha256):
        core = self.core
        return budget.OperationSpec(core["continuation_id"], core["execution_generation"], kind,
            "home:" + self.namespace + ":" + key, input_sha256, core["root_job_id"])

    @property
    def resolution_operation(self):
        return self.operation("source_resolution", "resolution:v1", self.core["resolution_input_sha256"])

    def check_stable(self, *, connection=None):
        """Recheck original identity only; expiry/pause never becomes a gate.

        Without SQL supplied this uses the same independent selector factory.
        A supplied connection receives only a fixed identity/root comparison;
        this method is neither a writer witness nor current admission.
        """
        require_binding(self)
        if connection is not None:
            storage_admin.from_connection(connection, profile=self.profile)
            _root(connection, self.core)
            row = connection.execute("SELECT value FROM main.meta WHERE key='library_id'").fetchone()
            if row is None or row[0] != self.core["binding"]["home_library_id"]:
                _fail("IDENTITY_CHANGED")
            return
        if self._completed_kind is not None:
            core = self.core
            refreshed = capture_completed_home_binding_locked(profile=self.profile, store=self.store,
                root_job_id=core["root_job_id"], continuation_id=core["continuation_id"],
                execution_generation=core["execution_generation"], terminal_kind=self._completed_kind)
            _same((refreshed.core_json, refreshed.submitted_url, refreshed.origin_ref),
                (self.core_json, self.submitted_url, self.origin_ref))
        else:
            with _capture_prepared_locked(profile=self.profile, store=self.store,
                    item_id=self.core["item_id"]) as prepared:
                _same((prepared.facts.core_json, prepared.facts.submitted_url, prepared.facts.origin_ref),
                    (self.core_json, self.submitted_url, self.origin_ref))


def require_binding(value, *, profile=None, store=None, item_id=None):
    """Check only the actual closed facts type, never mint fresh permission."""
    if (type(value) is not HomeBindingFacts17 or value._seal is not _SEAL
            or profile is not None and value.profile.identity != profile.identity
            or store is not None and value.store is not store
            or item_id is not None and json.loads(value.core_json)["item_id"] != item_id):
        _fail("FACTORY_REQUIRED")
    return value


def require_home17_effect_scope(binding=None, *, profile=None, root_job_id=None):
    """Reject an unbound real Home17 sync opportunity; grant no permission.

    Callers run this after paid replay and before every disabled-budget direct
    send. The real private worker and actual SQL row/version decide the role.
    The supplied root/profile only select actual SQL; they grant no worker or
    fresh authority. A copied scope cannot opt a Home17 job out by losing the
    private thread-local worker context.
    Legacy16 and actual non-sync jobs retain their existing execution path.
    """
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    current = getattr(worker._current, "job", None)
    if current is None and root_job_id is None:
        return
    if current is not None and type(current) is not dict:
        raise BudgetDeferred("unsupported_capability")
    actual_profile = getattr(worker._current, "profile", None)
    if actual_profile is None:
        actual_profile = storage_admin.profile() if profile is None else profile
    actual_profile = lifecycle._profile(actual_profile)
    # With a private worker profile, a supplied selector must still match it.
    # Without one, actual SQL decides the role in the selected namespace.
    if profile is not None and (type(profile) is not lifecycle.StorageProfile
            or profile.identity != actual_profile.identity):
        raise BudgetDeferred("unsupported_capability")
    selected_root = current.get("job_id") if root_job_id is None else root_job_id
    if type(selected_root) is not int or selected_root < 1:
        raise BudgetDeferred("unsupported_capability")
    try:
        with sql.connection(actual_profile) as con:
            version = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
            if version is None or type(version[0]) is not str or not version[0].isascii() or not version[0].isdigit():
                raise BudgetDeferred("unsupported_capability")
            if int(version[0]) < 17:
                return
            if current is not None and current.get("job_id") != selected_root:
                raise BudgetDeferred("unsupported_capability")
            row = con.execute("SELECT job_id,kind,scope_key,video_id,payload_json,continuation_id,"
                "execution_generation,owner_id FROM main.job WHERE job_id=?", (selected_root,)).fetchone()
            if row is not None and row["kind"] != "sync":
                return
            if (row is None or current is None or current.get("job_id") != selected_root
                    or current.get("kind") != row["kind"]
                    or (row["continuation_id"], row["execution_generation"], row["owner_id"]) !=
                    (current.get("continuation_id"), current.get("execution_generation"), current.get("owner_id"))):
                raise BudgetDeferred("unsupported_capability")
            tasks._require_sync_job(row)
            if not tasks.holds_process_lock(profile=actual_profile):
                raise BudgetDeferred("unsupported_capability")
            if type(binding) is not HomeBindingFacts17:
                raise BudgetDeferred("unsupported_capability")
            require_binding(binding, profile=actual_profile)
            _same((binding.core["root_job_id"], binding.core["continuation_id"], binding.core["execution_generation"]),
                (row["job_id"], row["continuation_id"], row["execution_generation"]))
            _root(con, binding.core)
    except db.SchemaVersionError:
        # The ordinary global16 gate still refuses a prospective17 database.
        # That refusal must never be converted into an unmetered send.
        raise BudgetDeferred("unsupported_capability") from None


class _FactPins:
    """Independent bounded exact origin/facility pins after journal scan closes."""
    def __init__(self, stack, profile, store, observed, origin):
        def check():
            if store.check_cancelled is not None:
                store.check_cancelled()
        self.pins = files._ProofPins(stack, profile, check)
        self.head = deepcopy(observed["head"])
        self.head_paths = (profile.database.parent / permanent.DB_HEAD_NAME,
            profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME)
        ledger = profile.database.parent / permanent.DIRECTORY / permanent.VERSION_DIRECTORY
        selected = ((profile.database.parent / permanent.INTENT_NAME, observed["contract"]),
            (profile.checkpoints / permanent.SEAL_NAME, observed["contract"]),
            (ledger / "contract.json", observed["contract"]),
            (profile.database.parent / permanent.DB_HEAD_NAME, observed["head"]),
            (profile.checkpoints / permanent.CHECKPOINT_HEAD_NAME, observed["head"]),
            (ledger / "origins" / (origin["record_id"] + ".json"), origin))
        for path, expected in selected:
            document, _ = self.pins.document(path, permanent.MAX_RECORD_BYTES,
                expected=permanent.digest(expected), mutable=path in self.head_paths)
            _same(document, expected)

    def short(self):
        self.pins.check()
        for directory in self.pins.directories:
            directory.verify()
        for directory, name, signature, identity, maximum in self.pins.mutable:
            self.pins.check()
            if physical.file_signature(directory.entry_stat(name)) != signature:
                _fail("ORIGIN_CHANGED")
            if backups._read(directory.path / name, maximum=maximum, content=False,
                    read_control=self.pins)[1] != identity:
                _fail("ORIGIN_CHANGED")
        # Six bounded records only. No journal enumeration or schema scan is
        # repeated inside the original reserve/dispatch transaction.
        for item in self.pins.files:
            self.pins._identity(item)
            directory, name, stream, digest, size, original = item
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            raw = os.read(stream.fileno(), permanent.MAX_RECORD_BYTES + 1)
            if len(raw) != size or hashlib.sha256(raw).hexdigest() != digest:
                _fail("ORIGIN_CHANGED")
            self.pins._identity(item)

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self.pins.verify()

    def _advance_heads(self, observed):
        """Only the admission module's closed creation handoff calls this."""
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        selected = set(self.head_paths)
        originals = tuple(item for item in self.pins.mutable if item[0].path / item[1] in selected)
        if len(originals) != 2:
            _fail("ORIGIN_CHANGED")
        # Head reads release leaf handles, allowing the proven publisher's
        # Windows atomic replacement. Parent handles and exact bounded hashes
        # remain live; every other immutable evidence file stays pinned open.
        for item in originals:
            self.pins.mutable.remove(item)
        try:
            for path in self.head_paths:
                document, _ = self.pins.document(path, permanent.MAX_RECORD_BYTES,
                    expected=permanent.digest(observed["head"]), mutable=True)
                _same(document, observed["head"])
        except BaseException:
            # No partial new pin set can remain usable after failed capture.
            self.pins.mutable[:] = [item for item in self.pins.mutable
                if item[0].path / item[1] not in selected] + list(originals)
            raise
        self.head = deepcopy(observed["head"])


class _PreparedBinding17:
    """Private prepared observations retained only by a lexical fresh view."""
    def __init__(self, profile, store, stack, state, source, version, cookie, schema_sha,
            identity, facts, origin, pins, entries):
        self.profile, self.store, self.facts = profile, store, facts
        self.state, self.source, self.version = deepcopy(state), deepcopy(source), deepcopy(version)
        self.cookie, self.schema_sha, self.identity = cookie, schema_sha, identity
        self.origin, self.pins = deepcopy(origin), pins
        self.entries = deepcopy(entries)

    def short(self, con):
        self.identity.verify_for_commit(con)
        version = db.knowledge_version(con)
        _same((version["library_id"], version["library_epoch"]),
            (self.version["library_id"], self.version["library_epoch"]))
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self.cookie)
        _same(con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0], "17")
        _root(con, self.facts.core)
        _same(self.store.read(), self.state["value"])
        self.pins.short()

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        witness._verify_state_full(self.store, self.state)
        self.pins.full()
        with sql.connection(self.profile) as con:
            _same(schema17._actual_schema(con), self.schema_sha)
            _same(sync_access.identity(con), self.source)
            self.short(con)


@contextmanager
def _capture_prepared_locked(*, profile, store, item_id):
    """Internal selectors only; ownership is acquired here and never supplied."""
    profile = _selectors(profile, store, item_id)
    with store.reference_operation(), ExitStack() as stack:
        state = witness._state(store)
        value = state["value"]
        if value is None or value["format_version"] != 5:
            _fail("STATE_REQUIRED")
        core = value.get("source_execution")
        if core is None or core["item_id"] != item_id:
            _fail("CORE_REQUIRED")
        execution._peer(core["binding"])
        authority = value["source_authority"]
        if authority is None or authority["origin"] is None or authority["core_sha256"] != permanent.digest(core):
            _fail("ORIGIN_REQUIRED")
        entries = [entry for entry in value["claims"] if entry["item_id"] == item_id]
        if len(entries) != 1:
            _fail("SOURCE_REQUIRED")
        submitted = validate_ingest_url(entries[0]["url"])
        _same(hashlib.sha256(submitted.encode()).hexdigest(), core["submitted_url_sha256"])
        with sql.connection(profile) as con:
            schema_sha = schema17._actual_schema(con)
            source, version = sync_access.identity(con), db.knowledge_version(con)
            if source["handshake"]["migration_version"] != 17:
                _fail("SCHEMA_REQUIRED")
            cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            identity = witness._IdentityCapture(store, con, stack)
            _same((source["library_id"], source["node_id"], version["library_epoch"]),
                (core["binding"]["home_library_id"], core["binding"]["home_node_id"], value["local_epoch"]))
            if version["library_epoch"] < core["local_epoch"]:
                _fail("EPOCH_INVALID")
            _root(con, core)
        # The full private journal is verified outside both business writers.
        # Its owner ends before a distinct admission/restore capture is opened.
        with permanent.owned(store) as owner:
            observed = owner.observe()
            if observed["contract"] is None:
                _fail("ORIGIN_REQUIRED")
            origin = owner.read(authority["origin"])
            _same(origin["core"], core)
            _same(origin["core_sha256"], permanent.digest(core))
            _same(origin["record_id"], str(uuid.uuid5(uuid.UUID(core["continuation_id"]),
                "source-origin:v1:" + permanent.digest(core))))
            _same(origin["record_kind"], "origin")
            captured = _FactPins(stack, profile, store, observed, origin)
        facts = HomeBindingFacts17(profile=profile, store=store, core=core,
            submitted_url=submitted, origin_ref=authority["origin"], seal=_SEAL)
        prepared = _PreparedBinding17(profile, store, stack, state, source, version, cookie,
            schema_sha, identity, facts, origin, captured, observed["entries"])
        prepared.full()
        yield prepared
        prepared.full()


def capture_home_binding_locked(*, profile, store, item_id):
    """Return detached original facts after all State/SH/SQL owners exit."""
    with _capture_prepared_locked(profile=profile, store=store, item_id=item_id) as prepared:
        result = prepared.facts
    return result


def capture_completed_home_binding_locked(*, profile, store, root_job_id, continuation_id,
        execution_generation, terminal_kind="source_failed"):
    """Read exact immutable history5/terminal2 facts after the core was cleared.

    The original three SQL-root selectors and terminal kind are the only input.
    Actual State history, original committed origin and terminal authority row
    are read here; no caller-owned history/receipt/core object is accepted.
    """
    profile = _selectors(profile, store, 1)
    sync_state._integer(root_job_id)
    sync_state._uuid(continuation_id)
    sync_state._integer(execution_generation)
    if type(terminal_kind) is not str or terminal_kind not in {"source_failed", "source_published"}:
        _fail("HISTORY_REQUIRED")
    with store.reference_operation(), ExitStack() as stack:
        current = witness._state(store)
        state = current["value"]
        if state is None or state["format_version"] != 5 or state["source_execution"] is not None:
            _fail("HISTORY_REQUIRED")
        getter = store.completed_source_failure if terminal_kind == "source_failed" else store.completed_source_publication
        selected = {"root_job_id": root_job_id, "continuation_id": continuation_id,
            "execution_generation": execution_generation}
        history = getter(**selected)
        if history is None or history["format_version"] != 5:
            _fail("HISTORY_REQUIRED")
        history = sync_state.validate_source_history5(history)
        core, terminal = history["source_execution"], history["source_terminal"]
        if (terminal is None or terminal["format_version"] != 2 or terminal["kind"] != terminal_kind
                or history["reason"] != terminal_kind):
            _fail("HISTORY_REQUIRED")
        _same({name: core[name] for name in selected}, selected)
        _same(core["binding"], state["binding"])
        execution._peer(core["binding"])
        authority = history["source_authority"]
        if authority is None or authority["origin"] is None:
            _fail("ORIGIN_REQUIRED")
        _same(authority["core_sha256"], permanent.digest(core))
        entries = [entry for entry in history["claims"] if entry["item_id"] == core["item_id"]]
        if len(entries) != 1:
            _fail("SOURCE_REQUIRED")
        submitted = validate_ingest_url(entries[0]["url"])
        _same(hashlib.sha256(submitted.encode()).hexdigest(), core["submitted_url_sha256"])
        def cancelled():
            if store.check_cancelled is not None:
                store.check_cancelled()
        physical = files._ProofPins(stack, profile, cancelled)
        history_path = store.data_dir / "sync-work-history" / sync_state._source_history_name(history)
        saved, _ = physical.document(history_path, sync_state.MAX_STATE_BYTES,
            expected=witness._digest(history))
        _same(saved, history)
        with sql.connection(profile) as con:
            schema_sha = schema17._actual_schema(con)
            source, version = sync_access.identity(con), db.knowledge_version(con)
            if source["handshake"]["migration_version"] != 17:
                _fail("SCHEMA_REQUIRED")
            cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            identity = witness._IdentityCapture(store, con, stack)
            _same((source["library_id"], source["node_id"], version["library_epoch"]),
                (core["binding"]["home_library_id"], core["binding"]["home_node_id"], state["local_epoch"]))
            if version["library_epoch"] < history["local_epoch"] or history["local_epoch"] < core["local_epoch"]:
                _fail("EPOCH_INVALID")
            _root(con, core)
        with permanent.owned(store) as owner:
            observed = owner.observe()
            if observed["contract"] is None or observed["incomplete"]:
                _fail("ORIGIN_REQUIRED")
            origin = owner.read(authority["origin"])
            _same(origin["record_kind"], "origin")
            _same(origin["core"], core)
            _same(origin["core_sha256"], permanent.digest(core))
            from .sync_source_lifecycle17 import terminal_authority_locked
            record = terminal_authority_locked(owner, observed, history)
            folder = "controls" if terminal_kind == "source_failed" else "publications"
            expected_kind = "control" if folder == "controls" else "publication"
            _same(record["record_kind"], expected_kind)
            _same(record["core_sha256"], permanent.digest(core))
            if folder == "controls":
                _same(record["request"], terminal["proof"]["request"])
            else:
                _same(record["metadata"]["publication_id"], terminal["proof"]["request"]["binding"]["publication_id"])
                _same(record["claims_sha256"], permanent.digest(terminal["proof"]["claims"]))
            pinned = _FactPins(stack, profile, store, observed, origin)
            ledger = profile.database.parent / permanent.DIRECTORY / permanent.VERSION_DIRECTORY
            exact, _ = physical.document(ledger / folder / (record["record_id"] + ".json"),
                permanent.MAX_RECORD_BYTES, expected=permanent.digest(record))
            _same(exact, record)
        # Re-read exact original selectors and SQL before detaching. Neither a
        # cached history dictionary nor a current claim supplies this proof.
        _same(getter(**selected), history)
        witness._verify_state_full(store, current)
        physical.verify()
        pinned.full()
        with sql.connection(profile) as con:
            _same(schema17._actual_schema(con), schema_sha)
            _same(sync_access.identity(con), source)
            _same(con.execute("PRAGMA main.schema_version").fetchone()[0], cookie)
            _same((db.knowledge_version(con)["library_id"], db.knowledge_version(con)["library_epoch"]),
                (version["library_id"], version["library_epoch"]))
            identity.verify_for_commit(con)
            _root(con, core)
        result = HomeBindingFacts17(profile=profile, store=store, core=core, submitted_url=submitted,
            origin_ref=authority["origin"], seal=_SEAL, completed_kind=terminal_kind)
    return result
