"""Private Home source authority, persisted before the first source request.

Cloud claim generations, mapping generations and worker continuations remain
separate. A restored state image alone never supplies a running worker owner.
"""
from dataclasses import dataclass
from contextvars import ContextVar
from datetime import datetime, timezone
import hashlib
import json
import sqlite3
import threading
import uuid

from . import budget, config, db, storage_admin, tasks, worker
from .budget_contract import BudgetDeferred
from .source_resolution_contract import RESOLVER_CONTRACT
from .sync_protocol import validate_ingest_url


_SEAL = object()
_GENESIS_SEAL = object()
_GENESIS = ContextVar("reno_home_source_origin_genesis", default=None)


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"))


def _sha(value):
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _job(profile, *, connection=None, fresh_admission=True):
    current = worker.execution_context()
    actual_profile = worker._execution_profile()
    if (current is None or current["kind"] != "sync" or actual_profile is None
            or actual_profile.identity != profile.identity or not tasks.holds_process_lock()
            or storage_admin.profile().identity != profile.identity or config.is_cloud()):
        raise BudgetDeferred("unsupported_capability")
    # Thread-local context describes the caller, while the persisted SQL row
    # proves ownership. Never replace either with a reconstructed continuation.
    if fresh_admission:
        worker.check_admission(connection=connection)
    else:
        stop = getattr(worker._current, "stop", None)
        if stop is not None and stop.is_set():
            raise BudgetDeferred("manual_pause")
    con = db.connect(profile.database) if connection is None else connection
    try:
        if connection is not None:
            storage_admin.from_connection(con, profile=profile)
        row = con.execute("SELECT * FROM job WHERE job_id=?", (current["job_id"],)).fetchone()
        if (row is None or row["status"] != "running" or row["owner_id"] != current["owner_id"]
                or (row["continuation_id"], row["execution_generation"]) != (
                    current["continuation_id"], current["execution_generation"])):
            raise tasks.TaskError("任务执行身份已失效", "TASK_OWNER_LOST", 409)
        tasks._require_sync_job(row)
    finally:
        if connection is None:
            con.close()
    return current


def _peer(binding):
    from .sync_agent import settings
    peer = settings()
    if (peer["url"] != binding["target_url"]
            or any(peer["sync_target_" + key] != binding["target_" + key] for key in ("library_id", "node_id"))):
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")


@dataclass(frozen=True, slots=True, repr=False, init=False)
class OriginGenesisGuard:
    """Only the private first-core transaction can produce an origin.

    The old HomeExecutionBinding seal and caller-owned SQL connections are not
    genesis witnesses. This ticket never grants admission or a source effect.
    """
    owner: object
    store: object
    profile: object
    _connection: object
    core_json: str
    entry_json: str
    thread: int
    _seal: object

    def __init__(self, owner, profile, core, entry, connection, *, seal):
        from .sync_source_authority import _fail
        if (seal is not _GENESIS_SEAL or type(connection) is not sqlite3.Connection
                or not connection.in_transaction or owner.lock.store.read().get("source_execution") is not None):
            _fail("ORIGIN_CREATION_REQUIRED")
        for name, value in (("owner", owner), ("store", owner.lock.store), ("profile", profile),
                ("_connection", connection), ("core_json", _canonical(core)), ("entry_json", _canonical(entry)),
                ("thread", threading.get_ident()), ("_seal", seal)):
            object.__setattr__(self, name, value)

    def check(self, record=None):
        from .sync_source_authority import _fail, digest
        from .sync_access import identity
        if (_GENESIS.get() is not self or self._seal is not _GENESIS_SEAL
                or threading.get_ident() != self.thread or not self._connection.in_transaction):
            _fail("ORIGIN_CREATION_REQUIRED")
        self.owner._check()
        current = _job(self.profile, connection=self._connection)
        core, entry = json.loads(self.core_json), json.loads(self.entry_json)
        state = self.store.read()
        _peer(core["binding"])
        local, version = identity(self._connection), db.knowledge_version(self._connection)
        if local["handshake"]["migration_version"] >= 17:
            _fail("CONTINUITY_PRODUCER_REQUIRED")
        if ((current["job_id"], current["continuation_id"], current["execution_generation"]) !=
                (core["root_job_id"], core["continuation_id"], core["execution_generation"])
                or (local["library_id"], local["node_id"], version["library_epoch"]) !=
                (core["binding"]["home_library_id"], core["binding"]["home_node_id"], core["local_epoch"])
                or state["binding"] != core["binding"] or state["local_epoch"] != core["local_epoch"]
                or state.get("source_execution") not in (None, core)
                or not any(saved == entry for saved in state["claims"])
                or entry["item_id"] != core["item_id"]
                or hashlib.sha256(validate_ingest_url(entry["url"]).encode("utf-8")).hexdigest() != core["submitted_url_sha256"]
                or state["recovery"] is None or state["recovery"]["state"] != "running"
                or (state["recovery"]["generation"], state["recovery"]["stream"]) !=
                (core["recovery_generation"], core["recovery_stream"])):
            _fail("CREATION_MISMATCH")
        if record is not None and (record["record_kind"] != "origin" or record["core"] != core
                or record["core_sha256"] != digest(core) or record["claim"] != entry["claim"]):
            _fail("CREATION_MISMATCH")


def _install_initial_core(store, profile, core, entry):
    """Origin-first publication under private SQL and actual State ownership."""
    from . import sync_source_authority as permanent
    with store.reference_operation():
        store.enable_source_execution()
        # A preexisting core follows the retained legacy path. No facility,
        # activation, origin or new authority is manufactured on reentry.
        if store.read().get("source_execution") is not None:
            return store.install_source_execution(core)
        with permanent.owned(store, initialize=True) as owner:
            try:
                observed = owner.observe()
            except permanent.SyncError as exc:
                if exc.code not in {"SYNC_SOURCE_AUTHORITY_ACTIVATION_INCOMPLETE", "SYNC_SOURCE_AUTHORITY_MISSING", "SYNC_SOURCE_AUTHORITY_HEAD_INCOMPLETE"}:
                    raise
                observed = None
            with storage_admin.connection(profile) as connection:
                connection.execute("BEGIN IMMEDIATE")
                guard = OriginGenesisGuard(owner, profile, core, entry, connection, seal=_GENESIS_SEAL)
                token = _GENESIS.set(guard)
                try:
                    guard.check()
                    sha = permanent.digest(core)
                    record_id = str(uuid.uuid5(uuid.UUID(core["continuation_id"]), "source-origin:v1:" + sha))
                    selector = (record_id, sha, permanent.digest(entry["claim"]))
                    if observed is None:
                        try:
                            observed = owner.observe(resume_origin=selector)
                        except permanent.SyncError as exc:
                            if exc.code not in {"SYNC_SOURCE_AUTHORITY_ACTIVATION_INCOMPLETE", "SYNC_SOURCE_AUTHORITY_MISSING"}:
                                raise
                            active = permanent.validate_contract(owner._read(owner.root, permanent.INTENT_NAME), profile=profile)
                            if active["binding"] != core["binding"]:
                                permanent._fail("PEER_MISMATCH")
                            guard.check()
                            owner.activate_files(active)
                            observed = owner.observe(resume_origin=selector)
                    if observed["contract"] is None:
                        active = {"format_version": 1, "facility_id": str(uuid.uuid4()),
                            "owner_profile_sha256": permanent.profile_sha256(profile), "binding": core["binding"],
                            "activated_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
                        owner.activate_files(active)
                        observed = owner.observe()
                    previous = observed["entries"]["origins"].get(record_id)
                    record = previous["record"] if previous is not None else {
                        "format_version": 1, "record_kind": "origin", "record_id": record_id,
                        "binding": core["binding"], "core": core, "core_sha256": sha, "claim": entry["claim"],
                        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
                    guard.check(record)
                    owner.publish("origins", record, producer=guard)
                    guard.check(record)
                    saved = store.install_source_execution(core)
                    guard.check(record)
                    connection.commit()
                    return saved
                finally:
                    _GENESIS.reset(token)
                    if connection.in_transaction:
                        connection.rollback()


@dataclass(frozen=True, slots=True, repr=False)
class HomeExecutionBinding:
    profile: object
    store: object
    core_json: str
    submitted_url: str
    _seal: object

    def __post_init__(self):
        if self._seal is not _SEAL:
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")

    @property
    def core(self):
        return json.loads(self.core_json)

    @property
    def namespace(self):
        value = self.core
        return _sha({key: value[key] for key in ("binding", "item_id", "submitted_url_sha256")})

    def check(self, *, connection=None):
        """Fresh admission proof; this method creates or repairs no state."""
        current = _job(self.profile, connection=connection)
        core = self.core
        _peer(core["binding"])
        if hashlib.sha256(validate_ingest_url(self.submitted_url).encode("utf-8")).hexdigest() != core["submitted_url_sha256"]:
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
        if (current["job_id"], current["continuation_id"], current["execution_generation"]) != (
                core["root_job_id"], core["continuation_id"], core["execution_generation"]):
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
        self.store.assert_source_execution(core)
        state = self.store.read_observation()
        control = state["recovery"]
        if (state["binding"] != core["binding"] or state["local_epoch"] != core["local_epoch"]
                or control is None or control["state"] != "running"
                or (control["generation"], control["stream"]) != (core["recovery_generation"], core["recovery_stream"])):
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
        if not any(entry["item_id"] == core["item_id"] and hashlib.sha256(
                validate_ingest_url(entry["url"]).encode("utf-8")).hexdigest() == core["submitted_url_sha256"]
                for entry in state["claims"]):
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
        con = db.connect(self.profile.database) if connection is None else connection
        try:
            if connection is not None:
                storage_admin.from_connection(con, profile=self.profile)
            from .sync_access import identity
            if identity(con)["handshake"]["migration_version"] >= 17:
                # The retained old seal cannot authenticate a continuity or
                # admission. A distinct actual new17 factory must be wired
                # before existing cores gain any fresh source opportunity.
                raise BudgetDeferred("unsupported_capability")
            version = db.knowledge_version(con)
            if (version["library_id"], version["library_epoch"]) != (
                    core["binding"]["home_library_id"], core["local_epoch"]):
                raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
        finally:
            if connection is None:
                con.close()

    def operation(self, kind, key, input_sha256):
        core = self.core
        return budget.OperationSpec(core["continuation_id"], core["execution_generation"], kind,
            "home:" + self.namespace + ":" + key, input_sha256, core["root_job_id"])

    @property
    def resolution_operation(self):
        return self.operation("source_resolution", "resolution:v1", self.core["resolution_input_sha256"])


def bind_execution(store, profile, entry, *, recovery_generation, recovery_stream):
    """Explicitly save the real original worker/item binding before DNS."""
    current = _job(profile)
    submitted = validate_ingest_url(entry["url"])
    budget._integer(entry["item_id"], minimum=1, code="BUDGET_IDENTITY_INVALID")
    state = store.read_observation()
    _peer(state["binding"])
    if not any(saved == entry for saved in state["claims"]):
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    if (state["recovery"] is None or state["recovery"]["state"] != "running"
            or (state["recovery"]["generation"], state["recovery"]["stream"]) != (recovery_generation, recovery_stream)):
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    core = _candidate_core(state, current, entry, recovery_generation=recovery_generation, recovery_stream=recovery_stream)
    _install_initial_core(store, profile, core, entry)
    value = HomeExecutionBinding(profile, store, _canonical(core), submitted, _SEAL)
    value.check()
    return value


def _candidate_core(state, current, entry, *, recovery_generation, recovery_stream):
    """Pure original bytes; this builder alone supplies no persistence right."""
    submitted_sha = hashlib.sha256(validate_ingest_url(entry["url"]).encode("utf-8")).hexdigest()
    core = {"format_version": 1, "binding": state["binding"], "item_id": entry["item_id"],
        "submitted_url_sha256": submitted_sha, "root_job_id": current["job_id"],
        "continuation_id": current["continuation_id"], "execution_generation": current["execution_generation"],
        "local_epoch": state["local_epoch"], "recovery_generation": recovery_generation, "recovery_stream": recovery_stream}
    namespace = _sha({key: core[key] for key in ("binding", "item_id", "submitted_url_sha256")})
    core["resolution_input_sha256"] = _sha({"format_version": 1, "kind": "source_resolution",
        "resolver_contract": RESOLVER_CONTRACT, "namespace": namespace, "submitted_url_sha256": submitted_sha})
    return core


def historical_binding(store, profile):
    """Read original paid facts for control delivery; grant no fresh admission.

    The original worker and peer still have to be real. Changed recovery fences
    and manual policy waits do not rewrite historical source identity; check()
    continues to reject them before any new source dispatch.
    """
    from .sync_access import identity
    if store.profile is None or store.profile.identity != profile.identity:
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    current = _job(profile, fresh_admission=False)
    state = store.read_observation()
    core = state.get("source_execution")
    if (core is None or state["binding"] != core["binding"]
            or (current["job_id"], current["continuation_id"], current["execution_generation"]) !=
                (core["root_job_id"], core["continuation_id"], core["execution_generation"])):
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    _peer(core["binding"])
    with storage_admin.connection(profile) as con:
        local = identity(con)
        if (local["library_id"], local["node_id"]) != (
                core["binding"]["home_library_id"], core["binding"]["home_node_id"]):
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    submitted = [validate_ingest_url(entry["url"]) for entry in state["claims"]
                 if entry["item_id"] == core["item_id"]]
    if (len(submitted) != 1 or hashlib.sha256(submitted[0].encode("utf-8")).hexdigest() != core["submitted_url_sha256"]):
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    store.assert_source_execution(core)
    return HomeExecutionBinding(profile, store, _canonical(core), submitted[0], _SEAL)


def completed_binding(store, profile, history):
    """Read an exact immutable terminal for the still-owned original job.

    This recovers the crash window between state clear and SQL task completion.
    A caller-supplied dictionary must equal the store's authoritative terminal;
    it cannot recreate a live core or authorize a new source operation.
    """
    from .sync_access import identity
    if store.profile is None or store.profile.identity != profile.identity:
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    current = _job(profile, fresh_admission=False)
    if type(history) is not dict or history.get("reason") not in {"source_failed", "source_published"}:
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    lookup = store.completed_source_failure if history["reason"] == "source_failed" else store.completed_source_publication
    actual = lookup(root_job_id=current["job_id"],
        continuation_id=current["continuation_id"], execution_generation=current["execution_generation"])
    if actual is None or actual != history:
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    state = store.read_observation()
    core = actual["source_execution"]
    if (state.get("source_execution") is not None or state.get("source_control_pending") is not None
            or state.get("budget_pending") is not None or state["binding"] != core["binding"]):
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    _peer(core["binding"])
    with storage_admin.connection(profile) as con:
        local, version = identity(con), db.knowledge_version(con)
        if (local["library_id"], local["node_id"], version["library_epoch"]) != (
                core["binding"]["home_library_id"], core["binding"]["home_node_id"], core["local_epoch"]):
            raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    submitted = [validate_ingest_url(entry["url"]) for entry in actual["claims"] if entry["item_id"] == core["item_id"]]
    if len(submitted) != 1 or hashlib.sha256(submitted[0].encode("utf-8")).hexdigest() != core["submitted_url_sha256"]:
        raise budget.BudgetLedgerError("BUDGET_IDENTITY_INVALID")
    return HomeExecutionBinding(profile, store, _canonical(core), submitted[0], _SEAL)
