"""Conservative Home17 scheduler choices, without paid/admission capabilities.

The active original core is transferred only by a committed targeted readmit.
Ordinary pull continues to exchange reviews with claim_limit=0. These helpers
return copied selector facts; no result is a source, State or SQL permission.
"""
from __future__ import annotations

from dataclasses import dataclass
from copy import deepcopy

from . import sync_state
from .sync_protocol import SyncError


def _fail():
    raise SyncError("Original Home source cycle cannot be selected",
                    "SYNC_SOURCE_CYCLE17_REQUIRED", 409) from None


@dataclass(frozen=True, slots=True)
class CycleSelectors17:
    ordinary_claim_limit: int
    item_id: int | None
    recovery_request_id: str | None
    publication_id: str | None
    defer_request_id: str | None
    resume_request_id: str | None
    failure_request_id: str | None


def cycle_selectors17_locked(*, store):
    """Read actual State; a returned integer never authenticates an HTTP call."""
    if type(store) is not sync_state.StateStore or store.profile is None:
        _fail()
    with store.reference_operation():
        state = store.read()
        core = state.get("source_execution")
        recovery = state.get("source_recovery_pending")
        publication = state["pending_publication"]
        wait = state.get("budget_pending")
        failure = state.get("source_control_pending")
        # Before genesis the inherited representation may still be old. An
        # existing core in any format keeps its original claim protected.
        ordinary = 1 if core is None and publication is None else 0
        return CycleSelectors17(ordinary, None if core is None else core["item_id"],
            None if recovery is None else recovery["request_id"],
            None if publication is None else publication["publication_id"],
            None if wait is None else wait["defer_request"]["request_id"],
            None if wait is None or wait["resume_request"] is None else wait["resume_request"]["request_id"],
            None if failure is None else failure["request"]["request_id"])


def ordinary_claim_limit17_locked(*, store):
    return cycle_selectors17_locked(store=store).ordinary_claim_limit


def apply_review_page17_locked(*, store, updates, recovery):
    """Advance reviews without replacing a source claim from an ordinary pull."""
    if type(store) is not sync_state.StateStore or store.profile is None:
        _fail()
    with store.reference_operation():
        state = store.read()
        if state.get("source_execution") is None:
            _fail()
        before = {key: deepcopy(state.get(key)) for key in
            ("source_execution", "source_authority", "source_admission", "source_recovery_pending",
             "budget_pending", "source_control_pending", "claims", "work", "pending_publication")}
        store.apply_review_page(updates, recovery=recovery)
        after = store.read()
        if any(after.get(key) != value for key, value in before.items()):
            _fail()
    return deepcopy(after)
