"""Admit the actual original source origin reference, without new admission.

Released16 installed a permanent origin before its State3/4 core, but did not
save that origin's reference in State. This independent selector-only reader
closes those original facts. Its sole effect promotes the representation and
fills the origin slot; all other State facts retain their original values.
Neither a bare16 restore nor this capture grants paid/transport permission.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import asyncio
import hashlib
import json
import os
import threading
import uuid

from . import budget_writer_witness as sql, db, home_recovery17 as schema17
from . import home_recovery_checkpoint as checkpoint, home_recovery_runtime17 as runtime
from . import home_recovery_schema as restore_schema, source_preservation_witness as witness
from . import storage_lifecycle as lifecycle, storage_source_authority_roots as home_roots
from . import sync_access, sync_source_authority as permanent
from . import sync_source_binding17 as binding, sync_source_execution as execution
from . import sync_source_lifecycle17 as effects, sync_state
from .sync_protocol import SyncError, validate_ingest_url


_SEAL = object()
_CURRENT = ContextVar("reno_legacy_origin_intake17", default=None)
_CONTEXT = ContextVar("reno_legacy_origin_intake17_context", default=None)


def _fail(code):
    raise SyncError("Original source origin could not be admitted",
        "SYNC_SOURCE_LEGACY_ORIGIN17_" + code, 409) from None


def _same(actual, expected):
    if actual != expected:
        _fail("ORIGINAL_CHANGED")


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _select(profile, store, item_id, expected_core_sha256, expected_record_sha256):
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity
            or type(item_id) is not int or item_id < 1 or _CURRENT.get() is not None):
        _fail("SELECTOR_REQUIRED")
    for value in (expected_core_sha256, expected_record_sha256):
        if value is not None:
            sync_state._source_sha(value)
    return profile


def _original(entries, identifier, core):
    selected = entries["origins"].get(identifier)
    if selected is None or selected["state"] != "committed":
        _fail("COMMITTED_ORIGIN_REQUIRED")
    record = selected["record"]
    _same((record["record_kind"], record["format_version"], record["record_id"],
           record["binding"], record["core"], record["core_sha256"]),
          ("origin", 1, identifier, core["binding"], core, permanent.digest(core)))
    return deepcopy(record)


def _pending_conflicts(state, entries, core):
    """Retain old State-only controls; a present permanent fact must agree.

    Old budgets/failure controls can predate permanent control production. A
    missing control is not turned into an authority slot by this bridge. The
    actual State parser still checks the complete original request/receipt.
    """
    pending = state.get("budget_pending")
    requests = [] if pending is None else [pending["defer_request"], pending["resume_request"]]
    failure = state.get("source_control_pending")
    if failure is not None:
        requests.append(failure["request"])
    for request in requests:
        if request is None:
            continue
        saved = entries["controls"].get(request["request_id"])
        if saved is not None:
            _same((saved["state"], saved["record"]["request"], saved["record"]["core_sha256"]),
                  ("committed", request, permanent.digest(core)))


def _after(state, origin, home, entries):
    """Self-derived exact representation, never a caller-owned replacement."""
    after = deepcopy(state)
    if after["format_version"] == 3:
        after.update(format_version=4, source_control_pending=None, source_admission=None)
    if after["format_version"] == 4:
        after.update(format_version=5, source_authority=None, source_recovery_pending=None)
    if after["format_version"] != 5:
        _fail("STATE_REQUIRED")
    core = after["source_execution"]
    authority = after["source_authority"]
    ref = permanent.record_ref(origin)
    if authority is None:
        authority = {"format_version": 1, "core_sha256": permanent.digest(core),
            "origin": None, "defer": None, "resume": None, "failure": None, "publication": None}
    # Original immutable intents record the whole expected_before authority.
    # Filling a missing origin changes that precondition. A separate original
    # pending consumer must save/bridge its exact UUID/receipt first; never
    # silently discard or rewrite the original request to make this pass.
    if authority["origin"] is None and after["source_recovery_pending"] is not None:
        _fail("ORIGINAL_RECOVERY_PENDING_BRIDGE_REQUIRED")
    if authority["origin"] is not None:
        _same(authority["origin"], ref)
    _same(authority["core_sha256"], permanent.digest(core))
    authority["origin"] = ref
    after["source_authority"] = authority
    sync_state._validate(after)
    witness._resolve_state(after, home)
    _pending_conflicts(after, entries, core)
    return after


def _restore_rows(con, core, version):
    raw = con.execute('SELECT ' + ','.join(restore_schema.COLUMNS) + ' FROM main."'
        + restore_schema.TABLE + '" WHERE library_id=? ORDER BY before_library_epoch,operation_id LIMIT ?',
        (core["binding"]["home_library_id"], runtime.MAX_CHAIN + 1)).fetchall()
    if len(raw) > runtime.MAX_CHAIN:
        _fail("RESTORE_CHAIN_LIMIT")
    rows, previous = [], None
    for mapping in raw:
        row = restore_schema.validate_record(mapping)
        parsed = checkpoint.validate(row, schema_version=17)
        _same((row["library_id"], row["node_id"]),
              (core["binding"]["home_library_id"], core["binding"]["home_node_id"]))
        if (row["after_library_epoch"] > version["library_epoch"]
                or row["after_kb_revision"] > version["kb_revision"]):
            _fail("RESTORE_CHAIN_INVALID")
        if previous is not None and (row["before_library_epoch"] != previous["after_library_epoch"]
                or row["before_kb_revision"] < previous["after_kb_revision"]):
            _fail("RESTORE_CHAIN_INCOMPLETE")
        if row["after_library_epoch"] > core["local_epoch"]:
            _same((row["original_core_sha256"], row["root_job_id"], row["continuation_id"],
                   row["execution_generation"]),
                  (permanent.digest(core), core["root_job_id"], core["continuation_id"],
                   core["execution_generation"]))
        rows.append((row, parsed))
        previous = row
    current = core["local_epoch"]
    for row, _ in rows:
        if row["after_library_epoch"] <= current:
            continue
        if row["before_library_epoch"] != current:
            _fail("RESTORE_CHAIN_INCOMPLETE")
        current = row["after_library_epoch"]
    if current != version["library_epoch"]:
        _fail("RESTORE_CHAIN_REQUIRED")
    return rows


class _OriginSchema17:
    """Actual immutable lineage/identity, independent of fresh worker leases."""
    def __init__(self, profile, store, core, state, stack):
        self.profile, self.store, self.core = profile, store, deepcopy(core)
        with sql.connection(profile) as con:
            self.schema_sha = schema17._actual_schema(con)
            self.cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            self.source, self.version = sync_access.identity(con), db.knowledge_version(con)
            if self.source["handshake"]["migration_version"] != 17:
                _fail("SCHEMA_REQUIRED")
            _same((self.source["library_id"], self.source["node_id"], self.version["library_epoch"]),
                  (core["binding"]["home_library_id"], core["binding"]["home_node_id"], state["local_epoch"]))
            if self.version["library_epoch"] < core["local_epoch"]:
                _fail("EPOCH_INVALID")
            self.identity = witness._IdentityCapture(store, con, stack)
            self.job = self._job(con)
            self.rows = _restore_rows(con, core, self.version)
            self.short(con)

    def _job(self, con):
        binding._root(con, self.core)
        row = con.execute("SELECT job_id,kind,scope_key,video_id,payload_json,continuation_id,execution_generation "
            "FROM main.job WHERE job_id=?", (self.core["root_job_id"],)).fetchone()
        if row is None:
            _fail("ORIGINAL_JOB_REQUIRED")
        return dict(row)

    def short(self, con):
        self.identity.verify_for_commit(con)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self.cookie)
        _same(con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0], "17")
        _same(db.knowledge_version(con), self.version)
        _same(self._job(con), self.job)
        actual = con.execute('SELECT ' + ','.join(restore_schema.COLUMNS) + ' FROM main."'
            + restore_schema.TABLE + '" WHERE library_id=? ORDER BY before_library_epoch,operation_id LIMIT ?',
            (self.core["binding"]["home_library_id"], len(self.rows) + 1)).fetchall()
        _same([dict(row) for row in actual], [row for row, _ in self.rows])

    def full(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        execution._peer(self.core["binding"])
        with sql.connection(self.profile) as con:
            _same(schema17._actual_schema(con), self.schema_sha)
            _same(sync_access.identity(con), self.source)
            self.short(con)


class LegacyOriginIntakeCapture17:
    """Lexical origin-reference-only State effect, with its own seal."""
    __slots__ = ("owner", "_profile", "_store", "_seal", "_active", "_owner_context", "_context_token",
        "_state", "_core", "_origin", "_schema", "_home", "_selected_pins", "_pins", "_checkpoint",
        "_before_json", "_after_json", "_signature", "_anchors", "_cas_guard", "_adopted",
        "preservation_capture", "_original_restore", "resume_capture")

    def __init__(self, *, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")

    def _read_control(self):
        self.owner._check()
        self.owner.lock.verify()
        if self._owner_context != _owner():
            _fail("FACTORY_REQUIRED")
        if self._store.check_cancelled is not None:
            self._store.check_cancelled()

    def _context(self):
        if (type(self) is not LegacyOriginIntakeCapture17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self):
            _fail("FACTORY_REQUIRED")
        self._read_control()
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        if (type(self._anchors) is not tuple or len(self._anchors) != 6
                or any(actual is not original for actual, original in zip((self.owner, self._schema,
                    self._home, self._selected_pins, self._pins, self._checkpoint), self._anchors))):
            _fail("FACTORY_REQUIRED")
        _same(self._schema.core, self._core)
        if (self.preservation_capture is not self._original_restore or self.resume_capture is not None
                or self._original_restore is not None and (
                    type(self._original_restore) is not runtime.CompletedRestoreCapture17
                    or self._original_restore._store is not self._store
                    or self._original_restore._profile.identity != self._profile.identity
                    or self._original_restore._owner.reference_frame is not self.owner.lock.reference_frame)):
            _fail("FACTORY_REQUIRED")
        _same(tuple(witness._digest(item) for item in (self._core, self._origin,
            self._before_json, self._after_json)), self._signature)

    @property
    def core(self):
        self._context()
        return deepcopy(self._core)

    @property
    def origin_ref(self):
        self._context()
        return permanent.record_ref(self._origin)

    def expected_state_effect(self):
        self._context()
        return json.loads(self._before_json), json.loads(self._after_json)

    def _require_cas(self, guard):
        self._context()
        if (type(guard) is not effects.StateCASGuard17 or effects._CURRENT.get() is not guard
                or guard._control is not self or guard.owner is not self.owner
                or guard.store is not self._store or guard.profile.identity != self._profile.identity
                or guard._schema is not self._schema or guard._pins is not self._selected_pins
                or guard._before_json != self._before_json or guard._after_json != self._after_json):
            _fail("ORIGIN_STATE_EFFECT_REQUIRED")
        guard._context()

    def origin_state_effect(self, guard):
        """Exact type consumer for runtime restore adoption, never a DTO."""
        self._require_cas(guard)
        if guard is not self._cas_guard or not guard._written:
            _fail("ORIGIN_STATE_EFFECT_REQUIRED")
        _same(self._store.read(), json.loads(self._after_json))
        return self.expected_state_effect()

    def _fixed(self, con, permit):
        guard = self._cas_guard
        self._require_cas(guard)
        if (guard._writer != (con, permit)
                or sql.require_writer(con, profile=self._profile) is not permit
                or permit.schema_version != 17):
            _fail("WRITER_REQUIRED")
        self._schema.short(con)
        self._selected_pins.short()
        self._home.verify_for_commit()
        self._pins.verify_short()
        self._checkpoint.verify_for_commit()
        actual = self._store.read()
        expected = json.loads(self._after_json if guard._written else self._before_json)
        _same(actual, expected)
        if not guard._written:
            _same(witness._state_signature(self._store), self._state["signature"])
        if self.preservation_capture is not None:
            self.preservation_capture._verify_budget_writer(con, permit)
        sql.check(permit, connection=con, profile=self._profile)

    def verify_for_commit(self, con, *, writer_permit=None):
        guard = effects._CURRENT.get()
        self._require_cas(guard)
        permit = sql.require_writer(con, profile=self._profile)
        if (writer_permit is not permit or guard._writer != (con, permit)
                or self._cas_guard is not None and self._cas_guard is not guard):
            _fail("WRITER_REQUIRED")
        self._cas_guard = guard
        if self.preservation_capture is not None:
            self.preservation_capture.verify_for_commit(con, writer_permit=permit)
        self._fixed(con, permit)
        sql.register_commit_check(permit, lambda: self._fixed(con, permit),
            connection=con, profile=self._profile)

    def adopt_state_effect(self, guard):
        """Renew only after the actual sole guarded State CAS has committed."""
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self.origin_state_effect(guard)
        if guard._writer is not None or guard._failure is not None:
            _fail("ORIGIN_STATE_EFFECT_REQUIRED")
        if self.preservation_capture is not None:
            self.preservation_capture._advance_state(guard)
        after = witness._state(self._store)
        _same(after["value"], json.loads(self._after_json))
        self._schema.full()
        self._home.verify_source()
        self._pins.verify()
        self._checkpoint.verify_full()
        witness._resolve_state(after["value"], self._home)
        self._state, self._adopted = after, True

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        if self._cas_guard is not None and self._cas_guard._written and not self._adopted:
            self.adopt_state_effect(self._cas_guard)
        witness._verify_state_full(self._store, self._state)
        self._schema.full()
        self._selected_pins.full()
        self._home.verify_source()
        self._pins.verify()
        self._checkpoint.verify_full()
        if self.preservation_capture is not None:
            self.preservation_capture.verify_source()
        self.owner.verify()
        _same(self._store.read(), json.loads(self._after_json if self._adopted else self._before_json))


@contextmanager
def capture_legacy_origin_intake17_locked(*, profile, store, item_id,
        expected_core_sha256=None, expected_record_sha256=None):
    """Read actual original facts using selectors only; no writes or repair."""
    profile = _select(profile, store, item_id, expected_core_sha256, expected_record_sha256)
    with store.reference_operation(), effects._owned(store) as owner, ExitStack() as stack:
        state = witness._state(store)
        before = state["value"]
        if before is None or before["format_version"] not in (3, 4, 5):
            _fail("STATE_REQUIRED")
        core = before.get("source_execution")
        if core is None or core["item_id"] != item_id:
            _fail("ORIGINAL_CORE_REQUIRED")
        execution._peer(core["binding"])
        _same(before["binding"], core["binding"])
        entries = [entry for entry in before["claims"] if entry["item_id"] == item_id]
        if len(entries) != 1:
            _fail("ORIGINAL_SOURCE_REQUIRED")
        _same(hashlib.sha256(validate_ingest_url(entries[0]["url"]).encode()).hexdigest(), core["submitted_url_sha256"])
        if expected_core_sha256 is not None:
            _same(permanent.digest(core), expected_core_sha256)
        identifier = str(uuid.uuid5(uuid.UUID(core["continuation_id"]),
            "source-origin:v1:" + permanent.digest(core)))
        observed = owner.observe_readonly()
        if observed["contract"] is None or observed["head_status"] != "complete" or observed["incomplete"]:
            _fail("COMPLETE_ORIGINAL_FACILITY_REQUIRED")
        origin = _original(observed["entries"], identifier, core)
        if expected_record_sha256 is not None:
            _same(permanent.digest(origin), expected_record_sha256)
        captured = LegacyOriginIntakeCapture17(seal=_SEAL)
        captured.owner, captured._profile, captured._store = owner, profile, store
        captured._seal, captured._active, captured._owner_context = _SEAL, True, _owner()
        captured._context_token = None
        captured._state, captured._core, captured._origin = state, deepcopy(core), origin
        captured._schema = _OriginSchema17(profile, store, core, before, stack)
        captured._home = stack.enter_context(home_roots.capture_locked(profile, binding=before["binding"],
            format_version=2, limits=home_roots.CaptureLimits(deadline_seconds=120), check_cancelled=captured._read_control))
        if captured._home.unknown or captured._home.summary["presence"] != "present":
            _fail("COMPLETE_ORIGINAL_FACILITY_REQUIRED")
        captured._selected_pins = effects._SelectedPins(stack, profile, store, observed, [("origins", origin)])
        captured._pins = runtime._RuntimePins(stack, profile, captured._read_control)
        captured._checkpoint = witness._CheckpointCache(store, state, stack)
        rows = captured._schema.rows
        captured.preservation_capture = None
        if any(parsed.wrapper is not None for _, parsed in rows):
            selected = rows[-1][0]
            if rows[-1][1].wrapper is None:
                _fail("MODERN_RESTORE_CHAIN_TERMINAL_REQUIRED")
            captured.preservation_capture = stack.enter_context(runtime.capture_completed_locked(
                profile=profile, store=store, operation_id=selected["operation_id"],
                expected_result_sha256=selected["result_sha256"]))
            _same([row for row, _ in captured.preservation_capture._records], [row for row, _ in rows])
        else:
            # Full original intent/image proof is historical evidence only.
            # No Runtime17/foreign producer seal is constructed for bare16.
            for row, _ in rows:
                runtime._legacy_row(captured._pins, row, profile)
        after = _after(before, origin, captured._home, observed["entries"])
        captured._before_json, captured._after_json = execution._canonical(before), execution._canonical(after)
        captured._signature = tuple(witness._digest(item) for item in (captured._core, captured._origin,
            captured._before_json, captured._after_json))
        captured._anchors = (owner, captured._schema, captured._home, captured._selected_pins,
            captured._pins, captured._checkpoint)
        captured._cas_guard, captured._adopted = None, False
        captured._original_restore, captured.resume_capture = captured.preservation_capture, None
        token = _CURRENT.set(captured)
        captured._context_token = _CONTEXT.set(captured)
        try:
            captured.verify_source()
            yield captured
            captured.verify_source()
        finally:
            captured._active = False
            _CONTEXT.reset(captured._context_token)
            _CURRENT.reset(token)


def require_legacy_origin_intake17(value, *, profile=None, store=None):
    if type(value) is not LegacyOriginIntakeCapture17:
        _fail("FACTORY_REQUIRED")
    value._context()
    if profile is not None and value._profile.identity != lifecycle._profile(profile).identity:
        _fail("FACTORY_REQUIRED")
    if store is not None and value._store is not store:
        _fail("FACTORY_REQUIRED")
    return value


def install_legacy_origin_reference17_locked(*, profile, store, item_id,
        expected_core_sha256=None, expected_record_sha256=None):
    """Install only the self-read old origin, never a caller State/permission.

    A complete already installed original is a pure replay. This entry neither
    discovers new work nor makes a historical source eligible for paid work.
    """
    with capture_legacy_origin_intake17_locked(profile=profile, store=store,
            item_id=item_id, expected_core_sha256=expected_core_sha256,
            expected_record_sha256=expected_record_sha256) as captured:
        before, after = captured.expected_state_effect()
        if before != after:
            with effects._cas(captured.owner, captured._schema, captured._selected_pins,
                    before, after, control=captured) as guard:
                with guard.writer():
                    guard.replace()
        captured.verify_source()
        result = {"core": captured.core, "origin_ref": captured.origin_ref}
    return result
