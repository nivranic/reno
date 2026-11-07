"""Read the original real four-column user resume authorization.

The task_request producer, UUID namespace and payload signature are unchanged.
No caller boolean, receipt dictionary, connection or clock creates this seal.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy

from . import budget_writer_witness as sql, storage_lifecycle as lifecycle
from . import sync_source_authority as permanent, sync_source_claim as engine
from . import sync_source_readmit_local2 as local, sync_state, tasks

_SEAL = object()
_CURRENT = ContextVar("reno_actual_manual_resume2", default=None)
_COLUMNS = ("request_id", "job_id", "payload_signature", "created_at")


def _fail(code):
    local._fail("MANUAL_" + code)


def _row(con, core, defer_request_id):
    root = con.execute("SELECT * FROM main.job WHERE job_id=?", (core["root_job_id"],)).fetchone()
    tasks._require_sync_job(root)
    local._same((root["continuation_id"], root["execution_generation"]),
        (core["continuation_id"], core["execution_generation"]))
    request_id, signature = tasks._sync_resume_identity(con, root, defer_request_id)
    record = con.execute("SELECT " + ",".join(_COLUMNS) + " FROM main.task_request WHERE request_id=?", (request_id,)).fetchone()
    if record is None:
        _fail("AUTHORIZATION_REQUIRED")
    record = dict(zip(_COLUMNS, record))
    if record["job_id"] != core["root_job_id"] or record["payload_signature"] != signature:
        _fail("AUTHORIZATION_CONFLICT")
    permanent._time(record["created_at"])
    return record


class ManualResumeCapture2:
    __slots__ = ("_seal", "_owner_context", "_active", "_profile", "_owner", "_core", "_defer", "_record")
    def __init__(self, profile, owner, core, parent, record, *, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._owner_context, self._active = seal, engine._owner(), True
        self._profile, self._owner, self._core = profile, owner, deepcopy(core)
        self._defer, self._record = deepcopy(parent), deepcopy(record)

    def _context(self):
        if (type(self) is not ManualResumeCapture2 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or self._owner_context != engine._owner()):
            _fail("FACTORY_REQUIRED")
        self._owner._check()

    @property
    def parent_defer(self):
        self._context()
        return deepcopy(self._defer)

    @property
    def wire_basis(self):
        self._context()
        return {"kind": "manual", "authorization_id": self._record["request_id"],
            "authorization_sha256": self._record["payload_signature"],
            "defer_request_id": self._defer["request"]["request_id"]}

    def provenance_ref(self):
        self._context()
        return {"format_version": 1, "producer": "sync_budget_resume", "operation_id": self._record["request_id"],
            "receipt_sha256": permanent.digest(self._record)}

    @property
    def resume_provenance(self):
        return self.provenance_ref()

    def _fixed(self, con, token):
        self._context()
        if sql.require_writer(con, profile=self._profile) is not token or token.schema_version != 17:
            _fail("WRITER_REQUIRED")
        local._same(_row(con, self._core, self._defer["request"]["request_id"]), self._record)
        sql.check(token, connection=con, profile=self._profile)

    def verify_for_commit(self, connection, *, writer_permit=None):
        self._fixed(connection, writer_permit)
        sql.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
            connection=connection, profile=self._profile)

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        observed = self._owner.observe()
        local._same(local._actual_parent(observed["entries"], self._defer["request"]["request_id"],
            permanent.digest(self._core)), self._defer)
        with sql.connection(self._profile) as con:
            local._same(_row(con, self._core, self._defer["request"]["request_id"]), self._record)

    def _advance_creation(self, guard, *, parent_capture=None):
        from . import sync_source_lifecycle17 as lifecycle17
        self._context()
        if type(guard) not in {lifecycle17.ControlCreationGuard17, lifecycle17.PublicationCreationGuard17, local.ReadmitIntentProducer2} or guard.owner is not self._owner:
            _fail("CREATION_PRODUCER_REQUIRED")
        if parent_capture is not None:
            if type(parent_capture) is not local.AdmissionReceiptCapture2 or parent_capture.resume_capture is not self:
                _fail("CREATION_PRODUCER_REQUIRED")
            parent_capture._context()
        observed = guard._advance_for_capture(self if parent_capture is None else parent_capture)
        if type(guard) is lifecycle17.ControlCreationGuard17:
            local._same(local._actual_parent(observed["entries"], self._defer["request"]["request_id"],
                permanent.digest(self._core)), self._defer)
            with sql.connection(self._profile) as con:
                local._same(_row(con, self._core, self._defer["request"]["request_id"]), self._record)


@contextmanager
def capture_manual_locked(*, profile, store, defer_request_id, origin_ref=None):
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if type(store) is not sync_state.StateStore or store.profile is None or store.profile.identity != profile.identity:
        _fail("FACTORY_REQUIRED")
    permanent._uuid(defer_request_id)
    permanent._Ownership(store).verify()
    existing = permanent._CURRENT.get()
    if existing is not None and (type(existing) is not permanent.AuthorityGuard or existing.lock.store is not store):
        _fail("FACTORY_REQUIRED")
    with ExitStack() as stack:
        owner = stack.enter_context(nullcontext(existing) if existing is not None else permanent.owned(store))
        observed = owner.observe()
        if origin_ref is None:
            state = store.read()
            if state.get("source_authority") is None:
                _fail("ORIGIN_REQUIRED")
            origin_ref = state["source_authority"]["origin"]
        permanent._ref(origin_ref, kind="origin")
        origin = local._resolve(observed["entries"], origin_ref)
        parent = local._actual_parent(observed["entries"], defer_request_id, origin["core_sha256"])
        with sql.connection(profile) as con:
            row = _row(con, origin["core"], defer_request_id)
        value = ManualResumeCapture2(profile, owner, origin["core"], parent, row, seal=_SEAL)
        token = _CURRENT.set(value)
        try:
            value.verify_source()
            yield value
            value.verify_source()
        finally:
            value._active = False
            _CURRENT.reset(token)
