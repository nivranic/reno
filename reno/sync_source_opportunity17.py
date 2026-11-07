"""A one-use scheduling signal, with no current or paid execution authority.

Keep this module cheap to import from model and pipeline exception handlers.
The actual admission factory owns classification and its selector-only recheck;
the exception merely survives the unwind to the original outer scheduler.
"""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
import os
import threading

from .sync_protocol import SyncError

_SEAL = object()
_PENDING = ContextVar("reno_home_opportunity17_pending", default=None)


def _owner():
    try:
        task = asyncio.current_task()
    except RuntimeError:
        task = None
    return os.getpid(), threading.get_ident(), task


def _fail():
    raise SyncError("Home scheduling signal could not be verified",
        "SYNC_SOURCE_OPPORTUNITY17_REQUIRED", 409) from None


class ReadmissionRequired17(SyncError):
    """Exact sealed exception; callers cannot construct a scheduling request."""
    __slots__ = ("_action", "_profile", "_store", "_client", "_broker", "_transport", "_url",
        "_item_id", "_binding", "_fingerprint", "_observations", "_owner", "_token", "_seal", "_consumed")

    def __init__(self, *, action, prepared, client, fingerprint, observations, seal):
        from . import sync_source_binding17 as bindings
        if (seal is not _SEAL or action not in {"observe", "readmit"}
                or type(prepared) is not bindings._PreparedBinding17):
            _fail()
        super().__init__("A current Home source observation is required",
            "SYNC_SOURCE_OPPORTUNITY17_REQUIRED", 409)
        self._action, self._profile, self._store = action, prepared.profile, prepared.store
        self._client, self._broker, self._transport, self._url = (
            client, client._source_claim_broker, client._transport, client._url)
        self._item_id, self._binding = prepared.facts.core["item_id"], prepared.facts
        self._fingerprint, self._observations, self._owner = fingerprint, observations, _owner()
        self._seal, self._consumed = seal, False
        self._token = _PENDING.set(self)

    @property
    def action(self):
        if type(self) is not ReadmissionRequired17 or self._seal is not _SEAL:
            _fail()
        return self._action


def _emit_required17(*, action, prepared, client, fingerprint, observations):
    """Only the real prepared classifier calls this after full verification."""
    raise ReadmissionRequired17(action=action, prepared=prepared, client=client,
        fingerprint=fingerprint, observations=observations, seal=_SEAL)


def consume_required17(value, *, profile, store, client, item_id, binding=None):
    """Recheck and consume in the original outer context after every lock exits.

    The result selects a bounded refresh/readmit attempt. It never authorizes a
    reserve, dispatch, source effect or publication; those still capture a new
    real current-admission view on the retried original business operation.
    """
    from . import budget_writer_witness as sql, storage_lifecycle as lifecycle
    from . import sync_source_admission17 as admissions, sync_source_binding17 as bindings
    from . import sync_source_authority as permanent, sync_source_claim as claims
    from . import home_recovery_runtime17 as runtime, sync_source_readmit_local2 as local
    if (type(value) is not ReadmissionRequired17 or value._seal is not _SEAL or value._consumed
            or value._owner != _owner() or _PENDING.get() is not value
            or type(profile) is not lifecycle.StorageProfile or profile.identity != value._profile.identity
            or store is not value._store or client is not value._client
            or type(item_id) is not int or item_id != value._item_id
            or client._source_claim_broker is not value._broker or client._transport is not value._transport
            or client._url != value._url or sql.in_write_window() or store._directory is not None
            or lifecycle.current_guard() is not None or admissions._CURRENT.get() is not None
            or permanent._CURRENT.get() is not None or claims._CURRENT.get() is not None
            or runtime._CURRENT.get() or local._CURRENT.get() is not None):
        _fail()
    if binding is not None:
        bindings.require_binding(binding, profile=profile, store=store, item_id=item_id)
        if (binding.core_json, binding.submitted_url, binding.origin_ref) != (
                value._binding.core_json, value._binding.submitted_url, value._binding.origin_ref):
            _fail()
    # Resetting the original token proves the actual Python Context, not only
    # copied ContextVar values or matching process/thread identifiers.
    try:
        _PENDING.reset(value._token)
    except (ValueError, RuntimeError):
        _fail()
    _PENDING.set(None)
    value._consumed = True
    admissions._recheck_scheduling17(value)
    return value._action
