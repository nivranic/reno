"""r881 source-only narrow registry lifetime candidate; Root alone admits/runs.

Actual Windows fixtures check referent lifetime. Callback replay with controlled
replacement rows checks only local anti-id-reuse guards, not natural id reuse,
active original graph, native close, source_closed or writer authority.
"""
import gc
import os
import weakref

import pytest

from reno import personal_authority_enrollment_custody as custody
from tests import test_personal_authority_enrollment_custody as task1

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows custody lifetime fixture")


class _WeakIdentity:
    pass


def _finished_group(tmp_path):
    with task1.actual_scope(tmp_path) as env:
        held_group = env.lease
        group_key, budget_key = id(env.lease), id(env.budget)
        group_row, budget_row = env.journal._registry_pins
        state = {
            "group_key": group_key, "budget_key": budget_key,
            "group_row": group_row, "budget_row": budget_row,
            "group_ref": group_row[0], "budget_ref": budget_row[0],
            "journal_ref": budget_row[1],
            "group_callback": group_row[0].__callback__,
            "budget_callback": budget_row[0].__callback__,
        }
        assert state["group_callback"] is not None
        assert state["budget_callback"] is not None
    # The returned state owns only weakrefs, immutable rows, callbacks and keys.
    # This frame's env/journal/budget disappear before the caller collects.
    return held_group, state


def test_budget_death_removes_only_budget_keys_before_group_death(tmp_path):
    held_group, state = _finished_group(tmp_path)
    gc.collect()
    assert state["budget_ref"]() is None and state["journal_ref"]() is None
    assert state["group_ref"]() is held_group
    assert custody._GROUP_JOURNALS[state["group_key"]] is state["group_row"]
    assert dict.get(custody._GROUP_JOURNALS, state["budget_key"]) is None
    assert dict.get(custody._BUDGET_JOURNALS, state["budget_key"]) is None
    del held_group
    gc.collect()
    assert state["group_ref"]() is None
    assert dict.get(custody._GROUP_JOURNALS, state["group_key"]) is None
    assert dict.get(custody._GROUP_JOURNALS, state["budget_key"]) is None
    assert dict.get(custody._BUDGET_JOURNALS, state["budget_key"]) is None


def test_same_live_group_and_budget_still_reject_second_journal(tmp_path):
    with task1.actual_scope(tmp_path) as env:
        rows = env.journal._registry_pins
        task1.code_error(lambda: task1.new_journal(env), custody.EnrollmentCustodyError,
                         "ENROLLMENT_CUSTODY_CLOSED")
        assert custody._GROUP_JOURNALS[id(env.lease)] is rows[0]
        assert custody._GROUP_JOURNALS[id(env.budget)] is rows[0]
        assert custody._BUDGET_JOURNALS[id(env.budget)] is rows[1]
        assert custody._active_journal_for_budget(env.budget) is env.journal


@pytest.mark.parametrize("callback_kind", ["budget", "group"], ids=["budget", "group"])
@pytest.mark.parametrize("reference_kind", ["original", "foreign"], ids=["original", "foreign"])
def test_old_or_foreign_callback_preserves_new_claim_rows(tmp_path, callback_kind, reference_kind):
    held_group, state = _finished_group(tmp_path)
    gc.collect()
    assert state["budget_ref"]() is None and state["journal_ref"]() is None
    group_anchor, journal_anchor, budget_anchor, foreign_anchor = (_WeakIdentity() for _ in range(4))
    new_claim = object()
    new_group_row = (weakref.ref(group_anchor), weakref.ref(journal_anchor), new_claim)
    new_budget_row = (weakref.ref(budget_anchor), new_group_row[1], new_claim)
    missing = object()
    entries = ((custody._GROUP_JOURNALS, state["group_key"], new_group_row),
               (custody._GROUP_JOURNALS, state["budget_key"], new_group_row),
               (custody._BUDGET_JOURNALS, state["budget_key"], new_budget_row))
    saved = [(mapping, key, dict.get(mapping, key, missing)) for mapping, key, _ in entries]
    try:
        for mapping, key, row in entries:
            mapping[key] = row
        reference = state[callback_kind + "_ref"] if reference_kind == "original" else weakref.ref(foreign_anchor)
        state[callback_kind + "_callback"](reference)
        for mapping, key, row in entries:
            assert dict.get(mapping, key) is row
    finally:
        for mapping, key, prior in saved:
            if prior is missing:
                dict.pop(mapping, key, None)
            else:
                mapping[key] = prior
    assert state["group_ref"]() is held_group
