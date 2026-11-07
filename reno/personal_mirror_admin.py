"""Explicit local personal-mirror selection and exact original delivery.

Read-only preview/status never create directories, state locks or requests.
Apply owns the process, original State and profile EX in that order. Worker
delivery consumes only an explicitly queued immutable request UUID and SHA;
ordinary synchronization cannot discover or select a new personal batch here.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager

from . import personal_mirror_contract as facts, personal_mirror_transport as transport
from . import storage_admin, storage_lifecycle as lifecycle, storage_source as source
from . import sync_agent, sync_media as safe, sync_state, tasks
from .sync_protocol import SyncError


def _fail(code, status=409):
    raise SyncError("Personal mirror administration could not be verified",
                    "PERSONAL_MIRROR_ADMIN_" + code, status) from None


def _profile(profile):
    profile = lifecycle._profile(profile)
    if profile.identity != storage_admin.profile().identity:
        _fail("PROFILE_CHANGED")
    values = sync_agent._sender_config_snapshot()
    if (str(values.get("node_role") or "all").lower() not in {"all", "home"}
            or not transport._enabled(values.get("personal_features_enabled"))
            or not transport._enabled(values.get("personal_mirror_enabled"))):
        _fail("DISABLED", 403)
    return profile


def _selectors(operation_id, expected_request_sha256=None, snapshot_id=None, check_cancelled=None):
    facts._uuid(operation_id)
    if expected_request_sha256 is not None:
        facts._sha(expected_request_sha256)
    if snapshot_id is not None:
        facts._uuid(snapshot_id)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT", 400)
    return check_cancelled or (lambda: None)


@contextmanager
def _existing_state(profile, check):
    """Pin existing DATA and original State lock before any creating adapter."""
    check()
    with ExitStack() as stack:
        # Existing lifecycle facilities are required, never initialized here.
        # This short preflight ends before acquiring original State ownership.
        with lifecycle.shared(profile, check_cancelled=check):
            directory = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
            try:
                lock = directory.entry_stat(sync_state._LOCK)
                state = directory.entry_stat(sync_state._STATE)
            except FileNotFoundError:
                _fail("STATE_NOT_INITIALIZED")
            source.file_signature(lock)
            source.file_signature(state)
            stack.enter_context(source.source_reader(directory, sync_state._LOCK))
            # This read has no mutation or State ownership acquisition.
            sync_state.StateStore(directory.path, profile=profile).read_observation()
        # _Directory prevents a missing root from being recreated by the old
        # general-purpose StateStore.exclusive(create=True) adapter.
        directory.verify()
        yield directory
        directory.verify()


@contextmanager
def _state_owned(profile, check, *, exclusive_profile=False):
    if lifecycle.current_guard() is not None:
        _fail("LOCK_ORDER_INVALID")
    with _existing_state(profile, check):
        store = sync_state.StateStore(profile.database.parent, profile=profile, check_cancelled=check)
        with store.exclusive(existing_only=True):
            guard = lifecycle.exclusive if exclusive_profile else lifecycle.shared
            with guard(profile, check_cancelled=check):
                check()
                yield store
                check()


@contextmanager
def _process_owned(profile, check):
    # Calls within the actual worker reuse its thread/database-bound proof;
    # public apply/init can acquire the same real process lock themselves.
    if lifecycle.current_guard() is not None:
        _fail("LOCK_ORDER_INVALID")
    if tasks.holds_process_lock(profile=profile):
        check()
        yield
        check()
        return
    with _existing_state(profile, check), tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        if not tasks.holds_process_lock(profile=profile):
            _fail("WORKER_LOCK_REQUIRED")
        check()
        yield
        check()


def initialize(profile, *, check_cancelled=None):
    """Explicit sender facility creation; does not pair or initialize State."""
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT", 400)
    check = check_cancelled or (lambda: None)
    profile = _profile(profile)
    with _process_owned(profile, check), _state_owned(profile, check) as store:
        transport.initialize_outbox_locked(store=store, check_cancelled=check)
        # Binding URL/node detail stays private, as does the original marker.
        return {"state": "initialized"}


init = initialize


def preview(profile, *, operation_id, snapshot_id, expected_head_operation_id=None, check_cancelled=None):
    check = _selectors(operation_id, snapshot_id=snapshot_id, check_cancelled=check_cancelled)
    profile = _profile(profile)
    with _state_owned(profile, check) as store:
        return transport.preview_mirror_locked(store=store, operation_id=operation_id,
            snapshot_id=snapshot_id, expected_head_operation_id=expected_head_operation_id,
            check_cancelled=check)


def apply(profile, *, operation_id, snapshot_id, expected_snapshot_sha256,
          expected_head_operation_id=None, check_cancelled=None):
    check = _selectors(operation_id, snapshot_id=snapshot_id, check_cancelled=check_cancelled)
    facts._sha(expected_snapshot_sha256)
    profile = _profile(profile)
    with _process_owned(profile, check), _state_owned(profile, check, exclusive_profile=True) as store:
        frozen = transport.freeze_mirror_locked(store=store, operation_id=operation_id,
            snapshot_id=snapshot_id, expected_head_operation_id=expected_head_operation_id,
            expected_snapshot_sha256=expected_snapshot_sha256, check_cancelled=check)
        return transport.operation_status_locked(store=store, operation_id=operation_id,
            expected_request_sha256=frozen["request_sha256"], check_cancelled=check)


def status(profile, *, operation_id, expected_request_sha256, check_cancelled=None):
    check = _selectors(operation_id, expected_request_sha256, check_cancelled=check_cancelled)
    profile = _profile(profile)
    with _state_owned(profile, check) as store:
        return transport.operation_status_locked(store=store, operation_id=operation_id,
            expected_request_sha256=expected_request_sha256, check_cancelled=check)


def deliver_worker(profile, *, operation_id, expected_request_sha256, check_cancelled=None, client=None):
    """Require the actual worker proof; deliver only the original request."""
    check = _selectors(operation_id, expected_request_sha256, check_cancelled=check_cancelled)
    profile = _profile(profile)
    if not tasks.holds_process_lock(profile=profile):
        _fail("WORKER_LOCK_REQUIRED")
    with _state_owned(profile, check) as store:
        transport.deliver_mirror_locked(store=store, operation_id=operation_id,
            expected_request_sha256=expected_request_sha256, check_cancelled=check, client=client)
        return transport.operation_status_locked(store=store, operation_id=operation_id,
            expected_request_sha256=expected_request_sha256, check_cancelled=check)
