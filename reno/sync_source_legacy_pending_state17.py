"""Immutable original-UUID State preimage for receipt-first pending bridges.

This private DATA namespace is separate from closed authority record kinds.
Only the actual pending capture may create its capsule before the sole CAS;
reads yield facts, never writer/paid capabilities. Backups and retention must
explicitly preserve this namespace before production activation.
"""
from __future__ import annotations

import base64
from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os

from . import budget_writer_witness as sql, source_preservation_witness as witness
from . import storage_backups as backups, storage_lifecycle as lifecycle
from . import sync_media as safe, sync_source_authority as authority, sync_state
from .sync_protocol import SyncError

DIRECTORY = ".reno-legacy-pending-state-v1"
MAX_BYTES = 5 * sync_state.MAX_STATE_BYTES + 16 * 1024
FIELDS = frozenset({"format_version", "request_id", "owner_profile_sha256", "core_sha256",
    "intent_sha256", "receipt_sha256", "before_raw_base64", "before_raw_sha256",
    "before_bytes", "before_json", "after_json", "after_sha256"})
APPLIED_FIELDS = frozenset({"format_version", "request_id", "owner_profile_sha256", "core_sha256",
    "intent_sha256", "receipt_sha256", "capsule_sha256", "observed_after_sha256"})
_APPEND_SEAL = object()
_APPEND_CURRENT = ContextVar("reno_pending_state_append17", default=None)
_APPEND_CONTEXT = ContextVar("reno_pending_state_append17_context", default=None)


def _fail(code):
    raise SyncError("Original pending State preimage could not be verified",
        "SYNC_SOURCE_LEGACY_PENDING_STATE17_" + code, 409) from None


def path(profile, request_id):
    profile = lifecycle._profile(profile)
    authority._uuid(request_id)
    return profile.database.parent / DIRECTORY / (request_id + ".json")


def validate(value, *, profile, intent, receipt, origin):
    if type(value) is not dict or set(value) != FIELDS or type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail("INVALID")
    if (value["request_id"] != intent["record_id"] or value["owner_profile_sha256"] != authority.profile_sha256(profile)
            or value["core_sha256"] != origin["core_sha256"] or value["intent_sha256"] != authority.digest(intent)
            or value["receipt_sha256"] != authority.digest(receipt)):
        _fail("ORIGINAL_CHANGED")
    for field in ("before_raw_sha256", "after_sha256"):
        authority._sha(value[field])
    if type(value["before_bytes"]) is not int or not 1 <= value["before_bytes"] <= sync_state.MAX_STATE_BYTES:
        _fail("INVALID")
    try:
        if type(value["before_raw_base64"]) is not str:
            _fail("INVALID")
        raw = base64.b64decode(value["before_raw_base64"], validate=True)
        if len(raw) != value["before_bytes"] or hashlib.sha256(raw).hexdigest() != value["before_raw_sha256"]:
            _fail("ORIGINAL_CHANGED")
        before = backups._decode(raw)
        for field in ("before_json", "after_json"):
            if type(value[field]) is not str or len(value[field].encode()) > sync_state.MAX_STATE_BYTES:
                _fail("INVALID")
        parsed_before = backups._decode(value["before_json"].encode())
        after = backups._decode(value["after_json"].encode())
        if before != parsed_before:
            _fail("ORIGINAL_CHANGED")
        if sync_state._validate(before).decode() != value["before_json"] or sync_state._validate(after).decode() != value["after_json"]:
            _fail("INVALID")
        if hashlib.sha256(value["after_json"].encode()).hexdigest() != value["after_sha256"]:
            _fail("ORIGINAL_CHANGED")
    except SyncError:
        raise
    except (ValueError, TypeError, UnicodeError, RecursionError):
        _fail("INVALID")
    from . import sync_source_legacy_pending_intake17 as pending
    if pending._after(before, intent, receipt, origin, mode="consume") != after:
        _fail("EFFECT_CHANGED")
    return before, after


def read_locked(*, profile, request_id, pins=None):
    """Actual bounded/no-follow bytes; optional held pin remains caller-owned."""
    lifecycle.require_guard(profile)
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    selected = path(profile, request_id)
    if pins is not None:
        try:
            value, identity = pins.document(selected, MAX_BYTES)
        except FileNotFoundError:
            return None
    else:
        try:
            raw, identity = backups._read(selected, maximum=MAX_BYTES)
        except FileNotFoundError:
            return None
        value = backups._decode(raw)
    return {"value": deepcopy(value), "identity": deepcopy(identity), "path": selected}


def read_applied_locked(*, profile, request_id, pins=None):
    lifecycle.require_guard(profile)
    if sql.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    selected = path(profile, request_id).with_name(request_id + ".applied.json")
    try:
        if pins is not None:
            value, identity = pins.document(selected, 16 * 1024)
        else:
            raw, identity = backups._read(selected, maximum=16 * 1024)
            value = backups._decode(raw)
    except FileNotFoundError:
        return None
    return {"value": deepcopy(value), "identity": deepcopy(identity), "path": selected}


def validate_applied(value, *, capsule):
    if (type(value) is not dict or set(value) != APPLIED_FIELDS
            or type(value["format_version"]) is not int or value["format_version"] != 1):
        _fail("APPLIED_INVALID")
    original = capsule["value"]
    expected = {key: original[key] for key in APPLIED_FIELDS - {"capsule_sha256", "observed_after_sha256"}}
    expected.update(capsule_sha256=capsule["identity"]["sha256"], observed_after_sha256=original["after_sha256"])
    if value != expected:
        _fail("APPLIED_ORIGINAL_CHANGED")
    return deepcopy(value)


class _PendingStateAppendWriter17:
    """Actual original-UUID file append; never an HTTP or detached effect DTO."""

    def __new__(cls, *args, **kwargs):
        _fail("APPEND_FACTORY_REQUIRED")

    def _initialize(self, producer, value, role):
        from . import home_recovery_runtime17 as runtime, storage_source_authority_roots as home_roots
        from . import sync_source_legacy_pending_intake17 as pending
        if (type(producer) is not pending.LegacyPendingIntakeCapture17
                or producer._mode != "consume" or sql.in_write_window()
                or role not in {"legacy_pending_state", "legacy_pending_applied"}):
            _fail("APPEND_FACTORY_REQUIRED")
        producer._context()
        self._producer, self._role = producer, role
        self._owner, self._store, self._profile = producer.owner, producer._store, producer._profile
        self._profile_identity = producer._profile.identity
        self._reference_frame = producer.owner.lock.reference_frame
        self._producer_signature = tuple(producer._signature)
        self._seal, self._active, self._context_token = _APPEND_SEAL, True, None
        self._owner_context, self._state = producer._owner_context, deepcopy(producer._state)
        self._request_id = producer._intent["record_id"]
        suffix = ".json" if role == "legacy_pending_state" else ".applied.json"
        self._expected_name = self._request_id + suffix
        self._expected_path = producer._profile.database.parent / DIRECTORY / self._expected_name
        self._expected_raw = backups._json(value)
        self._body_sha = hashlib.sha256(self._expected_raw).hexdigest()
        self._fixed_append = (role, self._request_id, self._expected_name,
            self._expected_path, self._expected_raw, self._body_sha)
        self._created, self._written_directory, self._created_identity = False, None, None
        self._saved_identity = None
        self._restore = producer.preservation_capture
        homes = [producer._home]
        if self._restore is not None:
            if (type(self._restore) is not runtime.CompletedRestoreCapture17
                    or self._restore is not producer._original_restore
                    or self._restore._store is not producer._store
                    or self._restore._profile.identity != producer._profile.identity
                    or self._restore._owner.reference_frame is not producer.owner.lock.reference_frame):
                _fail("APPEND_FACTORY_REQUIRED")
            self._restore._check()
            homes.append(self._restore._home)
        self._homes = tuple(dict.fromkeys(homes))
        self._children = tuple(home._legacy_pending for home in self._homes)
        if any(type(home) is not home_roots.AuthorityCapture or not home._active
                or home._format_version != 2 or home.unknown or home.summary.get("presence") != "present"
                or home._legacy_pending is None for home in self._homes):
            _fail("HOME2_APPEND_CAPTURE_REQUIRED")
        self._authority_readers = tuple(home._reader for home in self._homes)
        self._authority_guards = tuple(home._guard for home in self._homes)
        self._authority_bases = tuple(witness._digest({"summary": {
            key: value for key, value in home._summary.items() if key != "legacy_pending_state"},
            "references": home._references, "unknown": home._unknown}) for home in self._homes)
        self._child_states = [(child._reader, child._projection_json, child._summary_json,
            child._state_images_json) for child in self._children]
        if role == "legacy_pending_state":
            if producer._replay:
                _fail("APPEND_FACTORY_REQUIRED")
            before, after = validate(value, profile=producer._profile,
                intent=producer._intent, receipt=producer._receipt, origin=producer._origin)
            if (before, after) != producer.expected_state_effect():
                _fail("APPEND_EFFECT_CHANGED")
            if value["before_raw_sha256"] != self._state["raw_sha256"]:
                _fail("APPEND_EFFECT_CHANGED")
        else:
            original = read_locked(profile=producer._profile, request_id=self._request_id, pins=producer._pins)
            if original is None:
                _fail("ORIGINAL_CAPSULE_REQUIRED")
            _, after = validate(original["value"], profile=producer._profile,
                intent=producer._intent, receipt=producer._receipt, origin=producer._origin)
            validate_applied(value, capsule=original)
            if self._state["value"] != after or self._state["raw_sha256"] != original["value"]["after_sha256"]:
                _fail("ACTUAL_AFTER_REQUIRED")
        witness._verify_state_full(producer._store, self._state)

    def _fixed_context(self):
        from . import sync_source_legacy_pending_intake17 as pending
        if (type(self) is not _PendingStateAppendWriter17 or self._seal is not _APPEND_SEAL
                or not self._active or _APPEND_CURRENT.get() is not self or _APPEND_CONTEXT.get() is not self
                or type(self._producer) is not pending.LegacyPendingIntakeCapture17
                or pending._CURRENT.get() is not self._producer or pending._CONTEXT.get() is not self._producer
                or self._producer._seal is not pending._SEAL or not self._producer._active
                or sql.in_write_window()):
            _fail("APPEND_FACTORY_REQUIRED")
        producer = self._producer
        homes = tuple(dict.fromkeys([producer._home] + ([] if self._restore is None else [self._restore._home])))
        suffix = ".json" if self._role == "legacy_pending_state" else ".applied.json"
        if (self._role not in {"legacy_pending_state", "legacy_pending_applied"}
                or producer._mode != "consume" or producer._owner_context != self._owner_context
                or producer.owner is not self._owner or producer._store is not self._store
                or producer._profile is not self._profile or producer._profile.identity != self._profile_identity
                or self._owner.profile.identity != self._profile_identity
                or self._store.profile.identity != self._profile_identity
                or self._owner.lock.store is not self._store
                or self._owner.lock.reference_frame is not self._reference_frame
                or producer.preservation_capture is not self._restore or producer._state != self._state
                or homes != self._homes or tuple(home._legacy_pending for home in homes) != self._children
                or producer._intent["record_id"] != self._request_id
                or self._expected_name != self._request_id + suffix
                or self._expected_path != self._profile.database.parent / DIRECTORY / self._expected_name
                or hashlib.sha256(self._expected_raw).hexdigest() != self._body_sha
                or (self._role, self._request_id, self._expected_name, self._expected_path,
                    self._expected_raw, self._body_sha) != self._fixed_append
                or tuple(producer._signature) != self._producer_signature
                or tuple(witness._digest(item) for item in (producer._core, producer._origin,
                    producer._intent, producer._receipt, producer._mode, producer._manual_rows,
                    producer._facts_signature, producer._effect_before_json, producer._effect_after_json,
                    producer._replay, producer._before_json, producer._after_json)) != self._producer_signature):
            _fail("APPEND_FACTORY_REQUIRED")
        if (tuple(home._reader for home in homes) != self._authority_readers
                or tuple(home._guard for home in homes) != self._authority_guards
                or any(home._format_version != 2 or not home._active for home in homes)
                or tuple(witness._digest({"summary": {
                    key: value for key, value in home._summary.items() if key != "legacy_pending_state"},
                    "references": home._references, "unknown": home._unknown}) for home in homes)
                    != self._authority_bases
                or [(child._reader, child._projection_json, child._summary_json, child._state_images_json)
                    for child in self._children] != self._child_states):
            _fail("APPEND_FACTORY_REQUIRED")
        if lifecycle.require_guard(self._profile) is not self._homes[0]._guard:
            _fail("APPEND_FACTORY_REQUIRED")

    def _context(self):
        self._fixed_context()
        try:
            _APPEND_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("APPEND_FACTORY_REQUIRED")
        self._context_token = _APPEND_CONTEXT.set(self)
        producer = self._producer
        producer._context()
        self._fixed_context()
        if self._restore is not None:
            self._restore._check()
            self._fixed_context()

    def _authorize_home(self, home):
        self._context()
        if not any(home is selected for selected in self._homes):
            _fail("APPEND_FACTORY_REQUIRED")

    def _authorize_home_fixed(self, home):
        self._fixed_context()
        if not any(home is selected for selected in self._homes):
            _fail("APPEND_FACTORY_REQUIRED")

    def _before_immutable_write(self, directory):
        """Final callback, then actual full State proof directly before write."""
        self._context()
        if directory.path != self._expected_path.parent or self._created:
            _fail("APPEND_FACTORY_REQUIRED")
        self._owner.lock.verify()
        directory.verify()
        if self._role == "legacy_pending_applied":
            # Read the actual original capsule through this pinned directory,
            # without a RuntimePins/user callback after the State comparison.
            raw = sync_state._read_bytes(directory, self._request_id + ".json", MAX_BYTES)
            if hashlib.sha256(raw).hexdigest() != backups._decode(self._expected_raw)["capsule_sha256"]:
                _fail("APPEND_ORIGINAL_CHANGED")
        witness._verify_state_full(self._store, self._state)
        self._fixed_context()
        # Callers immediately invoke sync_state._write, whose no-replace
        # operation performs no caller callback. A later check cannot create
        # completion evidence for a State that this boundary did not observe.

    def _accepted_home(self, home, plan):
        from . import storage_legacy_pending_state17 as pending_roots
        if (type(self) is not _PendingStateAppendWriter17 or self._seal is not _APPEND_SEAL
                or not self._active or _APPEND_CURRENT.get() is not self or _APPEND_CONTEXT.get() is not self
                or type(plan) is not pending_roots._AppendPlan17
                or plan.seal is not pending_roots._APPEND_PLAN_SEAL or plan.writer is not self
                or plan.home is not home or not any(home is selected for selected in self._homes)):
            _fail("APPEND_FACTORY_REQUIRED")
        index = next(index for index, selected in enumerate(self._homes) if selected is home)
        child, candidate = self._children[index], plan.candidate
        expected = (plan.original, *plan.cache)
        observed = (child._reader, child._projection_json, child._summary_json, child._state_images_json)
        accepted = (candidate._reader, candidate._projection_json, candidate._summary_json, candidate._state_images_json)
        if (plan.captured is not child or self._child_states[index] != expected or observed != accepted
                or home._legacy_pending is not child
                or home._summary.get("legacy_pending_state") != json.loads(candidate._summary_json)):
            _fail("APPEND_FACTORY_REQUIRED")
        self._child_states[index] = accepted
        self._fixed_context()

    def _record_created(self, directory):
        self._context()
        if self._created or directory.path != self._expected_path.parent:
            _fail("APPEND_FACTORY_REQUIRED")
        raw, observed = backups._read(self._expected_path,
            maximum=MAX_BYTES if self._role == "legacy_pending_state" else 16 * 1024)
        directory.verify()
        if raw != self._expected_raw:
            _fail("APPEND_ORIGINAL_CHANGED")
        self._created, self._written_directory, self._created_identity = True, directory, observed

    def _verify_appended_file(self, reader, directory):
        self._context()
        if (not self._created or self._written_directory is None or self._saved_identity is None
                or directory is None or directory.path != self._expected_path.parent
                or tuple(directory.identities) != tuple(self._written_directory.identities)):
            _fail("APPEND_FACTORY_REQUIRED")
        self._written_directory.verify()
        selected = [record for record in reader.records if record.name == self._expected_name]
        files = [item for item in reader.files if item[1] == self._expected_name]
        if (len(selected) != 1 or selected[0].raw != self._expected_raw or len(files) != 1
                or {**backups._identity(os.fstat(files[0][2].fileno())), "sha256": files[0][5].hex()}
                    != self._created_identity or self._saved_identity != self._created_identity):
            _fail("APPEND_ORIGINAL_CHANGED")

    def _adopt_saved(self, directory, saved):
        self._context()
        if (saved is None or saved["path"] != self._expected_path
                or backups._json(saved["value"]) != self._expected_raw
                or directory.path != self._expected_path.parent):
            _fail("APPEND_ORIGINAL_CHANGED")
        witness._verify_state_full(self._producer._store, self._state)
        if not self._created:
            # A no-write replay grants no relaxed namespace comparison.
            self._producer.verify_source()
            return
        if directory is not self._written_directory or saved["identity"] != self._created_identity:
            _fail("APPEND_ORIGINAL_CHANGED")
        self._saved_identity = deepcopy(saved["identity"])
        # Validate both actual Home closures before renewing either one. Each
        # retains its original component and outer capture object identities.
        plans = [(home, home._prepare_legacy_pending_append17(self)) for home in self._homes]
        for home, plan in plans:
            home._validate_legacy_pending_append17(self, plan)
        self._fixed_context()
        for home, plan in plans:
            home._adopt_legacy_pending_append17(self, plan)
        witness._verify_state_full(self._producer._store, self._state)
        self._producer.verify_source()


@contextmanager
def _append_writer17(captured, value, role):
    if _APPEND_CURRENT.get() is not None:
        _fail("APPEND_FACTORY_REQUIRED")
    writer = object.__new__(_PendingStateAppendWriter17)
    writer._initialize(captured, value, role)
    token = _APPEND_CURRENT.set(writer)
    writer._context_token = _APPEND_CONTEXT.set(writer)
    try:
        writer._context()
        yield writer
        writer._context()
    finally:
        writer._active = False
        _APPEND_CONTEXT.reset(writer._context_token)
        _APPEND_CURRENT.reset(token)


def finish_locked(captured):
    """Record completion only after observing the actual entire CAS after-image.

    A prepared capsule is never completion evidence. The original operation
    can close a State-write/marker gap only when the actual raw State exactly
    equals the capsule after-image; neither a caller flag nor partial State
    projection is accepted to create this immutable applied observation.
    """
    from . import sync_source_legacy_pending_intake17 as pending
    if type(captured) is not pending.LegacyPendingIntakeCapture17:
        _fail("FACTORY_REQUIRED")
    captured._context()
    if captured._mode != "consume" or sql.in_write_window():
        _fail("FACTORY_REQUIRED")
    captured.verify_source()
    original = read_locked(profile=captured._profile, request_id=captured._intent["record_id"], pins=captured._pins)
    if original is None:
        _fail("ORIGINAL_CAPSULE_REQUIRED")
    _, after = validate(original["value"], profile=captured._profile,
        intent=captured._intent, receipt=captured._receipt, origin=captured._origin)
    # Do not register an absence in held pins that this exact factory is
    # about to satisfy. Existing observations are pinned immediately below.
    existing = read_applied_locked(profile=captured._profile, request_id=captured._intent["record_id"])
    if existing is not None:
        validate_applied(existing["value"], capsule=original)
        pinned = read_applied_locked(profile=captured._profile, request_id=captured._intent["record_id"], pins=captured._pins)
        if (pinned["value"], pinned["identity"]) != (existing["value"], existing["identity"]):
            _fail("APPLIED_ORIGINAL_CHANGED")
        return pinned
    observed = witness._state(captured._store)
    if observed["value"] != after or observed["raw_sha256"] != original["value"]["after_sha256"]:
        _fail("ACTUAL_AFTER_REQUIRED")
    value = {key: original["value"][key] for key in
        APPLIED_FIELDS - {"capsule_sha256", "observed_after_sha256"}}
    value.update(capsule_sha256=original["identity"]["sha256"], observed_after_sha256=observed["raw_sha256"])
    validate_applied(value, capsule=original)
    selected = original["path"].with_name(captured._intent["record_id"] + ".applied.json")
    with _append_writer17(captured, value, "legacy_pending_applied") as writer:
        with safe._Directory(writer._expected_path.parent, base=writer._profile.database.parent) as directory:
            try:
                raw = sync_state._read_bytes(directory, writer._expected_name, 16 * 1024)
            except FileNotFoundError:
                writer._before_immutable_write(directory)
                sync_state._write(directory, writer._expected_name, writer._expected_raw, immutable=True)
                writer._record_created(directory)
            else:
                if backups._decode(raw) != value:
                    _fail("APPLIED_ORIGINAL_CHANGED")
            directory.verify()
            witness._verify_state_full(captured._store, observed)
            saved = read_applied_locked(profile=writer._profile, request_id=writer._request_id, pins=captured._pins)
            validate_applied(saved["value"], capsule=original)
            writer._adopt_saved(directory, saved)
    return saved


def prepare_locked(captured):
    """Persist only the live factory's actual full before and derived after."""
    from . import sync_source_legacy_pending_intake17 as pending
    if type(captured) is not pending.LegacyPendingIntakeCapture17:
        _fail("FACTORY_REQUIRED")
    captured._context()
    if captured._mode != "consume" or captured._replay or sql.in_write_window():
        _fail("FACTORY_REQUIRED")
    captured.verify_source()
    before, after = captured.expected_state_effect()
    raw = sync_state._read_bytes(captured._store._directory, sync_state._STATE, sync_state.MAX_STATE_BYTES)
    if (len(raw) != captured._state["byte_size"]
            or hashlib.sha256(raw).hexdigest() != captured._state["raw_sha256"]):
        _fail("ORIGINAL_CHANGED")
    witness._verify_state_full(captured._store, captured._state)
    value = {"format_version": 1, "request_id": captured._intent["record_id"],
        "owner_profile_sha256": authority.profile_sha256(captured._profile),
        "core_sha256": captured._origin["core_sha256"], "intent_sha256": authority.digest(captured._intent),
        "receipt_sha256": authority.digest(captured._receipt),
        "before_raw_base64": base64.b64encode(raw).decode("ascii"),
        "before_raw_sha256": hashlib.sha256(raw).hexdigest(), "before_bytes": len(raw),
        "before_json": sync_state._validate(before).decode(), "after_json": sync_state._validate(after).decode(),
        "after_sha256": hashlib.sha256(sync_state._validate(after)).hexdigest()}
    validate(value, profile=captured._profile, intent=captured._intent, receipt=captured._receipt, origin=captured._origin)
    selected = path(captured._profile, captured._intent["record_id"])
    with _append_writer17(captured, value, "legacy_pending_state") as writer:
        with safe._Directory(writer._expected_path.parent, create=True,
                base=writer._profile.database.parent) as directory:
            try:
                existing = sync_state._read_bytes(directory, writer._expected_name, MAX_BYTES)
            except FileNotFoundError:
                if len(writer._expected_raw) > MAX_BYTES:
                    _fail("LIMIT")
                # Immutable no-replace rename; an unknown collision is never
                # overwritten, even when the original State CAS has not run.
                writer._before_immutable_write(directory)
                sync_state._write(directory, writer._expected_name, writer._expected_raw, immutable=True)
                writer._record_created(directory)
            else:
                if backups._decode(existing) != value:
                    _fail("ORIGINAL_CAPSULE_CONFLICT")
            directory.verify()
            witness._verify_state_full(captured._store, captured._state)
            saved = read_locked(profile=writer._profile, request_id=writer._request_id, pins=captured._pins)
            writer._adopt_saved(directory, saved)
    return saved
