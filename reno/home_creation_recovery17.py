"""Original permanent-creation completion; never a fresh paid admission.

This reader retains the actual sole reservation, including absent record files
and lagging heads. It owns an independent real readonly17 SQL/artifact closure.
Only the original sealed completion producer can advance its two-event tail.
"""
from __future__ import annotations

from collections import namedtuple
from contextlib import ExitStack, contextmanager, nullcontext
from contextvars import ContextVar
from copy import deepcopy
import asyncio
import os
import threading
import time

from . import budget_writer_witness as sql, db, home_recovery17 as restore
from . import home_recovery_runtime17 as runtime, home_recovery_schema as restore_schema
from . import source_preservation_witness as witness, storage_lifecycle as lifecycle
from . import storage_root_inputs as inputs, storage_source_authority_roots as home_roots
from . import storage_source_continuity_roots as cloud_roots
from . import storage_legacy_pending_state17 as pending_roots
from . import sync_source_authority as authority
from . import sync_state

_CURRENT = ContextVar("reno_original_creation_recovery17", default=None)
_CONTEXT = ContextVar("reno_original_creation_recovery17_context", default=None)
_SEAL = object()
_READ_CURRENT = ContextVar("reno_original_creation_owned_read17", default=None)
_READ_CONTEXT = ContextVar("reno_original_creation_owned_read17_context", default=None)
_READ_SCOPE_SEAL = object()
_READ_SCOPE_LOCK = threading.RLock()
_READ_SCOPES = {}
_ReadBinding17 = namedtuple("_ReadBinding17", (
    "capture", "owner", "profile", "store", "context", "lock", "storage_guard",
    "state_guard", "state_directory", "reference_frame", "checkpoint_root", "owner_stack",
    "store_profile", "owner_profile", "selectors", "started", "reader_stack", "callback",
    "readers", "retired_readers", "home", "cloud", "pins", "closing", "cloud_reader", "current_reader",
    "context_token", "current_token", "reader_anchors"))


class CreationRecoveryError(ValueError):
    def __init__(self, code):
        self.code = "HOME_CREATION_RECOVERY17_" + code
        super().__init__(self.code)


def _fail(code):
    raise CreationRecoveryError(code) from None


def _same(actual, expected):
    if actual != expected:
        _fail("ORIGINAL_CHANGED")


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _copy_frame(value):
    # The ledger is the original live directory handle, not a serializable
    # fact. Retain its real owner; copy only detached canonical observations.
    return {key: item if key == "ledger" else deepcopy(item) for key, item in value.items()}


def _frame(owner, folder, identifier, expected=None):
    """Self-read real heads; derive a prefix without altering actual evidence."""
    value = owner.observe_readonly()
    item = value["entries"][folder].get(identifier)
    if item is None:
        _fail("ORIGINAL_REQUIRED")
    record = item["record"]
    if record.get("format_version") not in {1, 2}:
        _fail("VERSION_REQUIRED")
    if (folder == "controls" and record["format_version"] == 1
            and record["request"]["source"]["handshake"]["migration_version"] != 17):
        _fail("VERSION_REQUIRED")
    if expected is not None:
        _same(authority.digest(record), expected)
    if any(entry["state"] != "committed" for kind, records in value["entries"].items()
           for key, entry in records.items() if (kind, key) != (folder, identifier)):
        _fail("OTHER_RESERVATION")
    if any(selected != (folder, identifier) for selected in value["incomplete"]):
        _fail("OTHER_RESERVATION")
    head = value["journal_head"]
    journal = owner._directory(value["ledger"], "journal")
    tail = owner._read(journal, f"{head['ordinal']:08d}.json")
    if item["state"] == "reserved":
        _same((tail["kind"], tail["key"], tail["record"], tail["state"], authority.digest(tail)),
              (folder, identifier, record, "reserved", head["sha256"]))
        reserved = tail
    else:
        # A completed descriptor is independently readable even if later facts
        # exist. Completion may mutate only a still-reserved original tail.
        candidates = []
        for ordinal in range(1, head["ordinal"] + 1):
            event = owner._read(journal, f"{ordinal:08d}.json")
            if (event["kind"], event["key"]) == (folder, identifier):
                candidates.append(event)
        _same([event["state"] for event in candidates], ["reserved", "committed"])
        _same([event["record"] for event in candidates], [record, record])
        if candidates[1]["ordinal"] != candidates[0]["ordinal"] + 1:
            _fail("TAIL_REQUIRED")
        reserved = candidates[0]
    base_head = owner._head(value["contract"], reserved["ordinal"] - 1, reserved["previous_sha256"])
    base = _copy_frame(value)
    del base["entries"][folder][identifier]
    base.update(head=base_head, journal_head=base_head)
    return _copy_frame(value), deepcopy(record), base, deepcopy(reserved)


class _ExitFailures17:
    """Retain the body or first cleanup failure while still closing every pin."""
    def __init__(self):
        self.first = None
        self.notes = 0

    def capture(self, error):
        if self.first is None:
            self.first = (type(error), error, error.__traceback__)
        elif error is not self.first[1] and self.notes < 4:
            self.notes += 1
            try:
                BaseException.add_note(self.first[1],
                    "Creation recovery cleanup also failed: " + type(error).__name__)
            except BaseException:
                # A malformed __notes__ is diagnostic failure, not permission
                # to replace the first body error or stop closing other pins.
                pass

    def run(self, function, *args):
        try:
            return function(*args)
        except BaseException as error:
            self.capture(error)

    def exc_info(self):
        return (None, None, None) if self.first is None else self.first

    def reraise(self):
        if self.first is not None:
            raise BaseException.with_traceback(self.first[1], self.first[2])


class _OwnedExitStack17:
    """Only this factory's known readonly chain; never a generic exit adapter."""
    def __init__(self, failures):
        self._stack, self.failures = ExitStack(), failures

    def enter_context(self, context):
        kind = type(context)
        result = kind.__enter__(context)

        def close(exc_type, error, traceback):
            # Keep the original exception visible to each readonly owner.
            # Suppression cannot turn a failed capture into a successful one.
            answer = self.failures.run(kind.__exit__, context, *self.failures.exc_info())
            if answer is not None and answer is not False:
                self.failures.capture(CreationRecoveryError("UNSUPPORTED_EXIT_SUPPRESSION"))
            return False

        self._stack.push(close)
        return result

    def callback(self, function, *args, **kwargs):
        def close():
            try:
                function(*args, **kwargs)
            except BaseException as error:
                self.failures.capture(error)
        self._stack.callback(close)
        return function

    def close(self):
        self.failures.run(self._stack.__exit__, *self.failures.exc_info())


class _OwnedReadScope17:
    """Factory-held readonly callbacks; no data, SQL or publication authority."""
    def __init__(self, captured, reader_stack, *, seal):
        if (seal is not _READ_SCOPE_SEAL or _READ_CURRENT.get() is not None
                or type(captured) is not OriginalCreationRecoveryCapture17
                or captured._seal is not _SEAL or not captured._active
                or type(reader_stack) is not _OwnedExitStack17):
            _fail("FACTORY_REQUIRED")
        owner, store = captured.owner, captured._store
        if (type(owner) is not authority.AuthorityGuard or authority._CURRENT.get() is not owner
                or type(store) is not sync_state.StateStore or owner.lock.store is not store):
            _fail("FACTORY_REQUIRED")
        callback = captured._read_control
        binding = _ReadBinding17(captured, owner, captured._profile, store, _owner(), owner.lock,
            owner.lock.storage_guard, store._guard, store._directory, store._reference_frame,
            owner.checkpoint_root, owner.stack, store.profile, owner.profile,
            (captured._folder, captured._identifier), captured._started, reader_stack, callback,
            (), (), None, None, None, False, None, None, None, None, ())
        with _READ_SCOPE_LOCK:
            _READ_SCOPES[self] = binding
        current_token = _READ_CURRENT.set(self)
        context_token = _READ_CONTEXT.set(self)
        self._replace(binding, current_token=current_token, context_token=context_token)
        try:
            self.full()
        except BaseException as error:
            # Factory assignment has not happened yet: retire our own tokens
            # and registry even when initial ownership/deadline verification fails.
            failures = _ExitFailures17()
            failures.capture(error)
            failures.run(self.retire)
            failures.reraise()

    def _binding(self):
        with _READ_SCOPE_LOCK:
            binding = _READ_SCOPES.get(self)
        if binding is None:
            _fail("FACTORY_REQUIRED")
        return binding

    @property
    def closing(self):
        return self._binding().closing

    @property
    def readers(self):
        return self._binding().readers

    @property
    def retired_readers(self):
        return self._binding().retired_readers

    @property
    def home(self):
        return self._binding().home

    def _replace(self, before, **changes):
        with _READ_SCOPE_LOCK:
            if _READ_SCOPES.get(self) is not before:
                _fail("FACTORY_REQUIRED")
            _READ_SCOPES[self] = before._replace(**changes)

    def hold_child(self, kind, child):
        binding = self.check()
        if (binding.closing or not binding.capture._active
                or _CURRENT.get() is not binding.capture or _CONTEXT.get() is not binding.capture
                or kind not in {"pins", "cloud", "home"} or getattr(binding, kind) is not None):
            _fail("FACTORY_REQUIRED")
        if kind == "pins":
            if (type(child) is not runtime._RuntimePins or binding.capture._pins is not child
                    or child.stack is not binding.reader_stack or child.profile is not binding.profile
                    or child.callback is not binding.callback):
                _fail("FACTORY_REQUIRED")
            self._replace(binding, pins=child)
        elif kind == "cloud":
            if (type(child) is not cloud_roots.ContinuityCapture or binding.capture._cloud is not child
                    or child._guard is not binding.storage_guard or child._reader.profile is not binding.profile
                    or child._reader.callback is not binding.callback):
                _fail("FACTORY_REQUIRED")
            self._replace(binding, cloud=child, cloud_reader=child._reader)
        else:
            if (type(child) is not ReservedAuthorityCapture17 or binding.capture._home is not child
                    or child._guard is not binding.storage_guard
                    or not any(value is child._reader for value in binding.readers)):
                _fail("FACTORY_REQUIRED")
            self._replace(binding, home=child, current_reader=child._reader)

    def check(self):
        with _READ_SCOPE_LOCK:
            binding = _READ_SCOPES.get(self)
        if binding is None or _READ_CURRENT.get() is not self or _READ_CONTEXT.get() is not self:
            _fail("FACTORY_REQUIRED")
        if (binding.context != _owner() or binding.capture.owner is not binding.owner
                or binding.capture._profile is not binding.profile or binding.capture._store is not binding.store
                or (binding.capture._folder, binding.capture._identifier) != binding.selectors
                or binding.capture._started != binding.started
                or binding.owner.lock is not binding.lock or binding.lock.store is not binding.store
                or binding.owner.stack is not binding.owner_stack
                or binding.owner.root is not binding.state_directory
                or binding.owner.checkpoint_root is not binding.checkpoint_root
                or binding.owner.profile is not binding.owner_profile
                or binding.store.profile is not binding.store_profile
                or binding.store._guard is not binding.state_guard
                or binding.store._directory is not binding.state_directory
                or binding.store._reference_frame is not binding.reference_frame
                or binding.lock.storage_guard is not binding.storage_guard):
            _fail("FACTORY_REQUIRED")
        if binding.pins is not None and (binding.capture._pins is not binding.pins
                or binding.pins.stack is not binding.reader_stack or binding.pins.profile is not binding.profile
                or binding.pins.callback is not binding.callback):
            _fail("FACTORY_REQUIRED")
        if binding.cloud is not None and (binding.capture._cloud is not binding.cloud
                or binding.cloud._reader is not binding.cloud_reader
                or binding.cloud._guard is not binding.storage_guard
                or binding.cloud_reader.profile is not binding.profile
                or binding.cloud_reader.callback is not binding.callback):
            _fail("FACTORY_REQUIRED")
        if binding.home is not None and (binding.capture._home is not binding.home
                or binding.home._guard is not binding.storage_guard
                or not any(value is binding.home._reader for value in binding.readers)
                or any(value is binding.home._reader for value in binding.retired_readers)):
            _fail("FACTORY_REQUIRED")
        for reader, profile, stack, limits, callback in binding.reader_anchors:
            if (type(reader) is not home_roots._Reader or reader.profile is not profile
                    or reader.stack is not stack or reader.limits is not limits
                    or reader.callback is not callback or profile is not binding.profile
                    or stack is not binding.reader_stack or callback is not binding.callback):
                _fail("FACTORY_REQUIRED")
        # Equal ContextVar values in copy_context() do not own this token.
        try:
            _READ_CONTEXT.reset(binding.context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._replace(binding, context_token=_READ_CONTEXT.set(self))
        binding = self._binding()
        binding.owner._check()  # Includes original State/RLock/SH check and ten-second budget.
        if time.monotonic() - binding.started > 120:
            _fail("CAPTURE_LIMIT")
        return binding

    def full(self):
        binding = self.check()
        if binding.home is not None and binding.home._reader is not binding.current_reader:
            _fail("FACTORY_REQUIRED")
        binding.owner._verify_ownership()
        return self.check()

    def register_reader(self, reader):
        binding = self.check()
        if (self.closing or not binding.capture._active or _CURRENT.get() is not binding.capture
                or _CONTEXT.get() is not binding.capture or sql.in_write_window()
                or type(reader) is not home_roots._Reader or reader.profile is not binding.profile
                or reader.stack is not binding.reader_stack or reader.callback is not binding.callback
                or any(value is reader for value in self.readers)):
            _fail("FACTORY_REQUIRED")
        binding = self.full()
        self._replace(binding, readers=binding.readers + (reader,),
            reader_anchors=binding.reader_anchors + (
                (reader, reader.profile, reader.stack, reader.limits, reader.callback),))

    def adopt_reader(self, previous, current):
        binding = self.check()
        if (self.closing or self.home is not binding.capture._home or self.home._reader is not current
                or binding.current_reader is not previous
                or not any(value is previous for value in self.readers)
                or not any(value is current for value in self.readers)
                or any(value is current or value is previous for value in self.retired_readers)):
            _fail("FACTORY_REQUIRED")
        # Only the existing runtime prefix/SHA verifier has updated the capture.
        # Its old mutable heads may now legitimately differ; current pins keep
        # all original immutable identities and the same operation deadline.
        self._replace(binding, retired_readers=binding.retired_readers + (previous,), current_reader=current)

    def finish_readers(self, failures):
        binding = self._binding()
        self._replace(binding, closing=True)
        binding = self._binding()
        failures.run(self.full)
        if binding.pins is not None:
            failures.run(binding.pins.verify)
        if binding.cloud is not None:
            failures.run(binding.cloud.verify_source)
        home_verified = False
        if binding.home is not None:
            def verify_home():
                binding.home.verify_source()
                return True
            home_verified = failures.run(verify_home) is True
        for reader in binding.readers:
            if (not any(value is reader for value in binding.retired_readers)
                    and not (home_verified and binding.home._reader is reader
                        and binding.current_reader is reader)):
                # Also covers an acquired refresh reader never adopted after
                # an exception. Its actual handles have not been closed yet.
                failures.run(reader.verify)
        failures.run(self.full)

    def retire(self):
        failures = _ExitFailures17()
        binding = self._binding()
        try:
            failures.run(_READ_CONTEXT.reset, binding.context_token)
            failures.run(_READ_CURRENT.reset, binding.current_token)
        finally:
            with _READ_SCOPE_LOCK:
                _READ_SCOPES.pop(self, None)
        failures.reraise()


def _read_scope(captured):
    scope = _READ_CURRENT.get()
    if type(scope) is not _OwnedReadScope17:
        _fail("FACTORY_REQUIRED")
    with _READ_SCOPE_LOCK:
        binding = _READ_SCOPES.get(scope)
    if binding is None or binding.capture is not captured:
        _fail("FACTORY_REQUIRED")
    return scope


def _register_refresh_reader17(scope, captured, original, reader):
    """Private runtime seam, accepts only this live original factory scope."""
    if (_read_scope(captured) is not scope or scope.closing
            or captured._home._reader is not original
            or not any(value is original for value in scope.readers)
            or any(value is original for value in scope.retired_readers)
            or reader.limits is not original.limits or reader.callback is not original.callback
            or reader.stack is not original.stack):
        _fail("FACTORY_REQUIRED")
    captured._context()
    scope.register_reader(reader)


class ReservedAuthorityCapture17(home_roots.AuthorityCapture):
    """Actual physical Home capsule with one proven original incomplete tail."""
    def __init__(self, reader, guard, original, frame, *, format_version=1, legacy_pending=None):
        super().__init__(reader, guard)
        self._format_version = home_roots._summary_version(format_version)
        self._legacy_pending = legacy_pending
        if (self._format_version == 1 and legacy_pending is not None
                or self._format_version == 2 and legacy_pending is None):
            _fail("LEGACY_PENDING_CAPTURE_REQUIRED")
        self._version_anchor, self._pending_anchor = self._format_version, legacy_pending
        self._original, self._frame = original, _copy_frame(frame)
        payload = home_roots._read_payload(reader)
        if reader.anchor_errors:
            _fail("ANCHOR_INVALID")
        _same((payload["contract"], payload["intent"], payload["seal"]),
              (frame["contract"], frame["contract"], frame["contract"]))
        _same((payload["db_head"], payload["checkpoint_head"]),
              (frame["actual_heads"]["db"], frame["actual_heads"]["checkpoint"]))
        _same(len(payload["journal"]), frame["journal_head"]["ordinal"])
        _same(authority.digest(payload["journal"][-1]), frame["journal_head"]["sha256"])
        expected = {kind: {key: entry["record"] for key, entry in records.items()}
                    for kind, records in frame["entries"].items()}
        missing = {(kind, key) for kind, records in expected.items()
                   for key in set(records) - set(payload["records"][kind])}
        for kind, key in missing:
            # Incomplete includes a reserved record whose file already exists;
            # derive physical absence from this actual reader, never that flag.
            if ((kind, key) != (original._folder, original._identifier)
                    or frame["entries"][kind][key]["state"] != "reserved"):
                _fail("OTHER_RESERVATION")
            directory = next(item for item in reader.directories if item.path ==
                original._profile.database.parent / authority.DIRECTORY / authority.VERSION_DIRECTORY / kind)
            _same(reader.read(directory, key + ".json", optional=True), None)
            expected[kind].pop(key)
        _same(payload["records"], expected)
        home_roots._validate_publication_payloads(reader, frame["entries"])
        refs = tuple(authority.public_record_projection(kind, entry["record"],
            state=entry["state"], activation=frame["contract"])
            for kind in authority.KINDS for key, entry in sorted(frame["entries"][kind].items()))
        complete = frame["head_status"] == "complete" and all(row["state"] == "committed" for row in refs)
        self._references = refs
        self._unknown = () if complete else ({"scope": "home_authority", "code": "ORIGINAL_RESERVATION"},)
        self._summary = {"format_version": 1, "kind": "home_authority",
            "presence": "present" if complete else "unknown", "absence_sha256": None,
            "owner_profile_sha256": frame["contract"]["owner_profile_sha256"],
            "identity": {"facility_id": frame["contract"]["facility_id"]},
            "binding": {key: frame["contract"]["binding"][key] for key in authority._PUBLIC_BINDING_FIELDS},
            "contract_sha256": home_roots._digest(payload["contract"]),
            "head_ordinal": frame["journal_head"]["ordinal"], "head_sha256": frame["journal_head"]["sha256"],
            "journal_sha256": home_roots._digest(payload["journal"]),
            "record_set_sha256": home_roots._digest(list(refs)),
            "reference_set_sha256": home_roots._digest(list(refs)),
            "record_count": len(refs), "journal_count": len(payload["journal"])}
        with reader.legacy_pending_window():
            self._summary = runtime._with_held_legacy_pending(self._summary, refs,
                format_version=self._format_version, child=self._legacy_pending)

    def _check_pending_anchor(self):
        if self._format_version != self._version_anchor or self._legacy_pending is not self._pending_anchor:
            _fail("LEGACY_PENDING_CAPTURE_CHANGED")

    def verify_source(self):
        self._check_pending_anchor()
        super().verify_source()

    def verify_for_commit(self):
        self._check_pending_anchor()
        super().verify_for_commit()

    def verify_original_prefix(self, original):
        self._original._context()
        self.verify_source()
        current = self.summary
        if (original["presence"] != "present" or current["identity"] != original["identity"]
                or current["contract_sha256"] != original["contract_sha256"]
                or current["head_ordinal"] < original["head_ordinal"]
                or current["head_ordinal"] == original["head_ordinal"]
                    and current["head_sha256"] != original["head_sha256"]):
            _fail("RESTORE_PREFIX_CHANGED")
        runtime._held_pending_prefix(original, self)


class OriginalCreationRecoveryCapture17:
    """A closed original-fact effect, explicitly distinct from paid permits."""
    def __init__(self, *, owner, profile, store, folder, identifier, expected, seal):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self.owner, self._profile, self._store = owner, profile, store
        self._folder, self._identifier, self._expected = folder, identifier, expected
        self._seal, self._active, self._owner_context = seal, True, _owner()
        self._context_token = None
        self._creator = None
        self._actual, self._record, self._base, self._reserved = _frame(owner, folder, identifier, expected)
        self._initial_entries = deepcopy(self._actual["entries"])
        self._initial_state = store.read()
        core = self._initial_state.get("source_execution")
        if core is None or self._initial_state.get("source_authority") is None:
            _fail("ORIGIN_REQUIRED")
        _same(authority.digest(core), self._record["core_sha256"])
        _same(self._record["creation_basis"]["origin_ref"], self._initial_state["source_authority"]["origin"])
        _same(self._record["creation_basis"]["admission_ref"], self._initial_state["source_admission"])
        self._core = deepcopy(core)
        self._started = time.monotonic()

    def _context(self):
        if (type(self) is not OriginalCreationRecoveryCapture17 or self._seal is not _SEAL or not self._active
                or _CURRENT.get() is not self or _CONTEXT.get() is not self
                or self._owner_context != _owner()):
            _fail("FACTORY_REQUIRED")
        try:
            _CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _CONTEXT.set(self)
        self._read_full()

    def _read_full(self):
        _read_scope(self).full()

    def _read_control(self):
        # Final reader callbacks may run after the public effect is retired.
        # The registry retains the exact original owner, frame, SH and budgets.
        _read_scope(self).check()

    @property
    def record(self):
        self._context()
        return deepcopy(self._record)

    @property
    def core(self):
        self._context()
        return deepcopy(self._core)

    @property
    def base_observation(self):
        self._context()
        return _copy_frame(self._base)

    @property
    def actual_observation(self):
        self._context()
        return _copy_frame(self._actual)

    @property
    def record_state(self):
        self._context()
        return self._actual["entries"][self._folder][self._identifier]["state"]

    def _home_capture(self, stack, frame):
        # Every caller already owns its actual readonly17 schema/image window.
        # This is the initial child acquisition, not a refresh after append.
        child = stack.enter_context(pending_roots.capture_locked(self._profile,
            limits=pending_roots.CaptureLimits(deadline_seconds=120), check_cancelled=self._read_control))
        scope = _read_scope(self)
        reader = home_roots._Reader(self._profile, stack,
            home_roots.CaptureLimits(deadline_seconds=120), scope.check().callback)
        scope.register_reader(reader)
        value = ReservedAuthorityCapture17(reader, lifecycle.require_guard(self._profile), self, frame,
            format_version=2, legacy_pending=child)
        self._home = value
        scope.hold_child("home", value)
        value.verify_source()
        return value

    def _acquire(self, stack):
        self._stack = stack
        self._state = witness._state(self._store)
        scope = _read_scope(self)
        callback = scope.check().callback
        self._pins = runtime._RuntimePins(stack, self._profile, callback)
        scope.hold_child("pins", self._pins)
        with sql.connection(self._profile) as con:
            con.execute("PRAGMA query_only=ON")
            from .sync_source_lifecycle17 import _root_owner
            self._job = _root_owner(self._profile, con, self._core)
            self._schema_sha = restore._actual_schema(con)
            self._cookie = con.execute("PRAGMA main.schema_version").fetchone()[0]
            self._version = db.knowledge_version(con)
            _same(self._version["library_epoch"], self._initial_state["local_epoch"])
            self._projection = inputs.read_continuity_projection(con)
            self._identity = witness._IdentityCapture(self._store, con, stack)
            self._checkpoint = witness._CheckpointCache(self._store, self._state, stack)
            self._cloud = stack.enter_context(cloud_roots.capture_locked(self._profile,
                sql_projection=self._projection, limits=cloud_roots.CaptureLimits(deadline_seconds=120),
                check_cancelled=callback))
            scope.hold_child("cloud", self._cloud)
            if self._cloud.unknown or self._cloud.summary["presence"] not in {"present", "absent"}:
                _fail("CLOUD_INCOMPLETE")
            self._home = self._home_capture(stack, self._actual)
            witness._resolve_state(self._state["value"], self._home)
            self._records, self._documents = [], []
            if self._core["local_epoch"] != self._initial_state["local_epoch"]:
                rows = con.execute("SELECT operation_id,result_sha256 FROM " + restore_schema.TABLE
                    + " WHERE library_id=? AND after_library_epoch=?",
                    (self._version["library_id"], self._version["library_epoch"])).fetchall()
                if len(rows) != 1:
                    _fail("RESTORE_REQUIRED")
                selected = restore._record(con, rows[0][0])
                self._records = runtime._chain(con, selected, self._version)
                for row, parsed in self._records:
                    if parsed.wrapper is None:
                        runtime._legacy_row(self._pins, row, self._profile)
                        self._documents.append(None)
                    else:
                        document = runtime._completed_row(self._pins, row, self._profile, con)
                        runtime._current_prefix(document, row["operation_id"], self._pins,
                            self._profile, self._cloud, self._home)
                        self._documents.append(document)
                epochs = self._core["local_epoch"]
                for (row, parsed), document in zip(self._records, self._documents):
                    if row["after_library_epoch"] <= epochs:
                        continue
                    if row["before_library_epoch"] != epochs or document is None:
                        _fail("RESTORE_CHAIN_INCOMPLETE")
                    _same(document["state_authority"]["source_core_sha256"], authority.digest(self._core))
                    epochs = row["after_library_epoch"]
                _same(epochs, self._version["library_epoch"])
            self._row_hashes = tuple(restore._row_sha(row) for row, _ in self._records)
            self._verify_declared_restorations()
        self.verify_source()

    def _preserved_prefix(self, provenance, *, to_epoch):
        """Original documents already proved by this factory; no DTO seal."""
        authority._exact(provenance, ("format_version", "producer", "operation_id", "receipt_sha256"))
        authority._version(provenance["format_version"])
        if provenance["producer"] != "home_knowledge_restore":
            _fail("RESTORE_REQUIRED")
        selected = [(index, row, parsed) for index, (row, parsed) in enumerate(self._records)
            if row["operation_id"] == provenance["operation_id"]]
        if len(selected) != 1:
            _fail("RESTORE_CHAIN_INCOMPLETE")
        index, original, parsed = selected[0]
        _same((original["result_sha256"], original["after_library_epoch"]),
              (provenance["receipt_sha256"], to_epoch))
        epoch = self._core["local_epoch"]
        selected_document = None
        for (row, checked), document in zip(self._records[:index + 1], self._documents[:index + 1]):
            if row["after_library_epoch"] <= epoch:
                continue
            if row["before_library_epoch"] != epoch or checked.wrapper is None or document is None:
                _fail("RESTORE_CHAIN_INCOMPLETE")
            _same(document["state_authority"]["source_core_sha256"], authority.digest(self._core))
            epoch, selected_document = row["after_library_epoch"], document
        if epoch != to_epoch or selected_document is None or parsed.wrapper is None:
            _fail("RESTORE_CHAIN_INCOMPLETE")
        return {"format_version": 1, "frontier_sha256": runtime.base._sha(selected_document["frontier"]),
            "certificate_sha256": parsed.certificate_sha256,
            "from_epoch": self._core["local_epoch"], "to_epoch": to_epoch}

    def _verify_declared_restorations(self):
        entries = self._actual["entries"]
        prior = self._record["creation_basis"]["admission_ref"]
        if prior is not None:
            item = entries["receipts"].get(prior["admission_id"])
            if item is None or item["state"] != "committed":
                _fail("HISTORICAL_ADMISSION_REQUIRED")
            receipt = item["record"]
            _same(authority.digest(receipt), prior["record_sha256"])
            intent_ref = receipt["request_ref"]
            intent_item = entries["requests"].get(intent_ref["record_id"])
            if intent_item is None or intent_item["state"] != "committed":
                _fail("HISTORICAL_ADMISSION_REQUIRED")
            intent = intent_item["record"]
            _same(authority.record_ref(intent), intent_ref)
            request = intent["request"]
            _same(request["kind"], "source_readmit")
            to_epoch = receipt["result"]["current_home_epoch"]
            if to_epoch == self._core["local_epoch"]:
                _same((request["preservation"], intent.get("preservation_provenance")), (None, None))
            else:
                provenance = intent.get("preservation_provenance")
                if provenance is None:
                    _fail("RESTORE_REQUIRED")
                preservation = self._preserved_prefix(provenance, to_epoch=to_epoch)
                _same((request["preservation"], receipt["result"]["preservation"]),
                      (preservation, preservation))
        if self._folder == "controls":
            scope = self._record.get("control_scope", self._record["creation_basis"])
            provenance = self._record.get("preservation_provenance")
            if self._record["format_version"] == 1 and scope["home_epoch"] != self._core["local_epoch"]:
                # Historical wire1 carried the actual creation epoch, not a
                # new provenance field. Select its genuine original row from
                # this already verified full chain without rewriting wire1.
                originals = [row for row, parsed in self._records if row["after_library_epoch"] == scope["home_epoch"]]
                if len(originals) != 1:
                    _fail("RESTORE_CHAIN_INCOMPLETE")
                provenance = {"format_version": 1, "producer": "home_knowledge_restore",
                    "operation_id": originals[0]["operation_id"], "receipt_sha256": originals[0]["result_sha256"]}
            if scope["home_epoch"] == self._core["local_epoch"]:
                _same(provenance, None)
            elif provenance is None:
                _fail("RESTORE_REQUIRED")
            else:
                self._preserved_prefix(provenance, to_epoch=scope["home_epoch"])

    def _producer(self, producer):
        from .sync_source_lifecycle17 import ControlCompletionGuard17
        from .sync_source_publication_local2 import PublicationCompletionGuard2
        if (type(producer) not in {ControlCompletionGuard17, PublicationCompletionGuard2}
                or producer.owner is not self.owner or producer._recovery_capture is not self):
            _fail("COMPLETION_PRODUCER_REQUIRED")
        producer._context()
        if self._creator not in (None, producer):
            _fail("COMPLETION_PRODUCER_REQUIRED")
        self._creator = producer

    def _progress(self, frame):
        _same(frame["contract"], self._actual["contract"])
        entries = deepcopy(frame["entries"])
        selected = entries[self._folder].pop(self._identifier, None)
        before = deepcopy(self._initial_entries)
        original = before[self._folder].pop(self._identifier)
        _same(entries, before)
        if selected is None or selected["record"] != self._record:
            _fail("ORIGINAL_CHANGED")
        if original["state"] == "committed" and selected["state"] != "committed":
            _fail("ORIGINAL_CHANGED")
        if selected["state"] == "reserved":
            _same(frame["journal_head"], self._actual["journal_head"])
        elif original["state"] == "reserved":
            head = frame["journal_head"]
            event = self.owner._read(self.owner._directory(frame["ledger"], "journal"),
                f"{head['ordinal']:08d}.json")
            _same((head["ordinal"], event["previous_sha256"], event["kind"], event["key"],
                   event["record"], event["state"], authority.digest(event)),
                  (self._reserved["ordinal"] + 1, authority.digest(self._reserved), self._folder,
                   self._identifier, self._record, "committed", head["sha256"]))
        else:
            _same(frame["journal_head"], self._actual["journal_head"])

    def _refresh(self, frame):
        self._read_full()
        self._progress(frame)
        if frame == self._actual:
            self._read_full()
            return
        if self._creator is None or sql.in_write_window():
            _fail("COMPLETION_PRODUCER_REQUIRED")
        self._producer(self._creator)
        observed = {**frame, "head": frame["journal_head"]}
        scope = _read_scope(self)
        original = self._home._reader
        runtime._refresh_home_creation(self, self._creator, observed, self._folder,
            _creation_read_scope=scope)
        scope.adopt_reader(original, self._home._reader)
        self._actual = _copy_frame(frame)
        self._read_full()

    def verify_source(self):
        if sql.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._context()
        current = self.owner.observe_readonly()
        self._refresh(current)
        _same(self._store.read(), self._initial_state)
        witness._verify_state_full(self._store, self._state)
        self._pins.verify()
        self._checkpoint.verify_full()
        self._identity.verify_for_commit(None)
        self._cloud.verify_source()
        self._home.verify_source()
        with sql.connection(self._profile) as con:
            _same(restore._actual_schema(con), self._schema_sha)
            self._fixed_sql(con)
        self._read_full()

    def _fixed_sql(self, con):
        from .sync_source_lifecycle17 import _root_owner
        _same(_root_owner(self._profile, con, self._core), self._job)
        _same(con.execute("PRAGMA main.schema_version").fetchone()[0], self._cookie)
        _same(db.knowledge_version(con), self._version)
        _same(inputs.read_continuity_projection(con), self._projection)
        self._identity.verify_for_commit(con)
        if self._records:
            selected = restore._record(con, self._records[-1][0]["operation_id"])
            if selected is None:
                _fail("RESTORE_CHAIN_INCOMPLETE")
            chain = runtime._chain(con, selected, self._version)
            _same(tuple(restore._row_sha(row) for row, _ in chain), self._row_hashes)

    def _fixed(self, con, writer, producer):
        self._context()
        self._producer(producer)
        if sql.require_writer(con, profile=self._profile) is not writer or writer.schema_version != 17:
            _fail("WRITER_REQUIRED")
        _same(self._store.read(), self._initial_state)
        _same(witness._state_signature(self._store), self._state["signature"])
        self._pins.verify_short()
        self._identity.verify_for_commit(con)
        self._checkpoint.verify_for_commit()
        self._cloud.verify_for_commit()
        self._home.verify_for_commit()
        self._fixed_sql(con)
        sql.check(writer, connection=con, profile=self._profile)
        self._read_full()

    def verify_for_creation_commit(self, connection, *, writer_permit, producer):
        self._fixed(connection, writer_permit, producer)
        sql.register_commit_check(writer_permit,
            lambda: self._fixed(connection, writer_permit, producer),
            connection=connection, profile=self._profile)

    def check_head_repair(self, owner, producer):
        self._context()
        self._producer(producer)
        if owner is not self.owner:
            _fail("OWNER_REQUIRED")
        self.verify_source()
        self._read_full()

    def adopt_head_repair(self, owner, producer):
        self._context()
        self._producer(producer)
        if owner is not self.owner:
            _fail("OWNER_REQUIRED")
        receipt = owner.creation_head_repair_receipt(producer=producer)
        _same((receipt.owner, receipt.producer), (owner, producer))
        _same(receipt.original, {"record_kind": self._record["record_kind"],
            "record_id": self._record["record_id"], "record_sha256": authority.digest(self._record)})
        _same(receipt.before_heads, self._actual["actual_heads"])
        _same(receipt.journal_head, self._actual["journal_head"])
        _same(receipt.after_heads, {"db": receipt.journal_head, "checkpoint": receipt.journal_head})
        self.verify_source()
        self._read_full()

    def adopt_materialization(self, producer):
        """An outbox is checked by its exact payload owner, never supplied here."""
        self._context()
        self._producer(producer)
        self.verify_source()
        self._read_full()


@contextmanager
def capture_original_creation_recovery17_locked(*, profile, store, folder, record_id,
                                                expected_record_sha256=None):
    if sql.in_write_window() or _CURRENT.get() is not None:
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    if (folder not in {"controls", "publications"} or type(store) is not sync_state.StateStore
            or store.profile is None or store.profile.identity != profile.identity):
        _fail("FACTORY_REQUIRED")
    authority._uuid(record_id)
    if expected_record_sha256 is not None:
        authority._sha(expected_record_sha256)
    authority._Ownership(store).verify()
    failures = _ExitFailures17()
    owner_stack, reader_stack = _OwnedExitStack17(failures), _OwnedExitStack17(failures)
    captured = scope = token = None
    owned_entered = yielded = False
    try:
        existing = authority._CURRENT.get()
        if existing is not None and (type(existing) is not authority.AuthorityGuard or existing.lock.store is not store):
            _fail("OWNER_REQUIRED")
        owner = owner_stack.enter_context(nullcontext(existing) if existing is not None else authority.owned(store))
        owned_entered = existing is None
        captured = OriginalCreationRecoveryCapture17(owner=owner, profile=profile, store=store,
            folder=folder, identifier=record_id, expected=expected_record_sha256, seal=_SEAL)
        scope = _OwnedReadScope17(captured, reader_stack, seal=_READ_SCOPE_SEAL)
        token = _CURRENT.set(captured)
        captured._context_token = _CONTEXT.set(captured)
        captured._acquire(reader_stack)
        yielded = True
        yield captured
        captured.verify_source()
    except BaseException as error:
        failures.capture(error)
    finally:
        if captured is not None:
            captured._active = False
            if captured._context_token is not None:
                failures.run(_CONTEXT.reset, captured._context_token)
            if token is not None:
                failures.run(_CURRENT.reset, token)
        if scope is not None:
            failures.run(scope.finish_readers, failures)
        failures.run(reader_stack.close)
        if scope is not None:
            # All final reader/checkpoint/identity callbacks ran while the
            # original owner and SH were held. Full ownership is the last
            # observation before the private callback scope is retired.
            failures.run(scope.full)
            if scope.home is not None:
                scope.home._active = False
            failures.run(scope.retire)
        failures.run(owner_stack.close)
    if owned_entered and not yielded:
        # Preserve the original newly-owned factory entry error domain.
        # Yielded body and cleanup failures keep their first object/traceback.
        with authority._errors():
            failures.reraise()
    else:
        failures.reraise()
