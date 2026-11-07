"""The existing budget APIs must use their own original writer window.

These checks exercise reserve/replay/dispatch and paid result consumption;
they never open a provider connection or authorize a Home17 admission.
"""
from datetime import datetime, timezone
import hashlib
import sqlite3
import uuid

import pytest

from reno import budget, budget_contract, budget_writer_witness as witness
from reno import db, storage_lifecycle as lifecycle


NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
REQUEST = hashlib.sha256(b"budget writer integration request").hexdigest()


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                     tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(value.database)
    lifecycle.initialize(value)
    return value


def _policy(**values):
    return budget_contract.parse_policy({"budget_enabled": True, **values})


def _spec():
    return budget.OperationSpec(str(uuid.uuid4()), 1, "model", "integration:model:0",
                                hashlib.sha256(b"actual frozen integration input").hexdigest())


def _attempt():
    return budget.AttemptSpec(REQUEST, 1, "paas_v4", 100)


def _reserve(profile, spec, *, guard=None):
    return budget.reserve(spec, _attempt(), profile=profile, policy=_policy(), now=NOW,
                          reservation_guard=guard)


def _dispatch(profile, reservation, *, guard=None, policy=None):
    return budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256,
        str(uuid.uuid4()), expected_revision=reservation.revision, profile=profile,
        policy=policy or _policy(), now=NOW, dispatch_guard=guard)


def _query(profile, sql, parameters=()):
    with sqlite3.connect(profile.database) as con:
        return con.execute(sql, parameters).fetchall()


def test_all_three_original_guard_sites_receive_distinct_owned_writers(profile):
    observed = []

    def guard(con):
        token = witness.require_writer(con, profile=profile)
        observed.append((con, token))
        assert con.in_transaction
        assert witness.check(token, connection=con, profile=profile) is token

    spec = _spec()
    original = _reserve(profile, spec, guard=guard)
    assert _reserve(profile, spec, guard=guard) == original
    assert _dispatch(profile, original, guard=guard).won
    assert len(observed) == 3
    assert len({id(con) for con, _ in observed}) == 3
    assert len({id(token) for _, token in observed}) == 3
    for con, token in observed:
        with pytest.raises(witness.BudgetWriterWitnessError):
            witness.check(token, connection=con, profile=profile)
    assert _query(profile, "SELECT state FROM budget_attempt") == [("dispatching",)]


@pytest.mark.parametrize("site", ["new_reserve", "held_reserve", "dispatch"])
@pytest.mark.parametrize("boundary", ["commit", "rollback", "executescript"])
def test_real_guard_cannot_replace_original_transaction_and_return_success(profile, site, boundary):
    spec = _spec()
    reservation = _reserve(profile, spec) if site != "new_reserve" else None
    calls = []

    def replace(con):
        calls.append(True)
        con.set_trace_callback(None)
        if boundary == "commit":
            con.commit()
        elif boundary == "rollback":
            con.rollback()
        else:
            con.executescript("SELECT 1;")
        con.execute("BEGIN IMMEDIATE")
        assert con.in_transaction

    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_LEDGER_INVALID"):
        if site == "dispatch":
            _dispatch(profile, reservation, guard=replace)
        else:
            _reserve(profile, spec, guard=replace)
    assert calls == [True]
    assert _query(profile, "SELECT state FROM budget_attempt") == (
        [] if site == "new_reserve" else [("reserved",)])
    assert _query(profile, "SELECT dispatched_at FROM budget_attempt") == (
        [] if site == "new_reserve" else [(None,)])


@pytest.mark.parametrize("site", ["reserve", "dispatch"])
def test_original_guard_is_called_once_and_normal_dml_survives_short_checks(profile, site):
    spec = _spec()
    reservation = _reserve(profile, spec) if site == "dispatch" else None
    guard_calls, terminal_calls = [], []

    def guard(con):
        token = witness.require_writer(con, profile=profile)
        guard_calls.append(True)
        con.execute("INSERT INTO meta(key,value) VALUES ('integration_guard_dml','retained')")

        def terminal():
            terminal_calls.append(True)
            assert witness.check(token, connection=con, profile=profile) is token
            assert con.execute("SELECT state FROM budget_attempt").fetchone()[0] == (
                "reserved" if site == "reserve" else "dispatching")

        witness.register_commit_check(token, terminal, connection=con, profile=profile)

    if site == "reserve":
        _reserve(profile, spec, guard=guard)
    else:
        assert _dispatch(profile, reservation, guard=guard).won
    assert guard_calls == terminal_calls == [True]
    assert _query(profile, "SELECT value FROM meta WHERE key='integration_guard_dml'") == [("retained",)]


@pytest.mark.parametrize("site", ["reserve", "dispatch"])
def test_prepared_terminal_cas_failure_rolls_back_budget_dml_without_replaying_guard(profile, site):
    spec = _spec()
    reservation = _reserve(profile, spec) if site == "dispatch" else None
    guard_calls = []

    class ChangedOriginal(RuntimeError):
        pass

    failure = ChangedOriginal("synthetic changed pinned authority")

    def guard(con):
        token = witness.require_writer(con, profile=profile)
        guard_calls.append(True)

        def terminal():
            assert con.execute("SELECT state FROM budget_attempt").fetchone()[0] == (
                "reserved" if site == "reserve" else "dispatching")
            raise failure

        witness.register_commit_check(token, terminal, connection=con, profile=profile)

    with pytest.raises(ChangedOriginal) as caught:
        if site == "reserve":
            _reserve(profile, spec, guard=guard)
        else:
            _dispatch(profile, reservation, guard=guard)
    assert caught.value is failure
    assert guard_calls == [True]
    assert _query(profile, "SELECT state FROM budget_attempt") == (
        [] if site == "reserve" else [("reserved",)])


def test_paid_replay_result_and_late_settlement_do_not_require_fresh_application_guard(profile):
    spec = _spec()
    reservation = _reserve(profile, spec)
    assert _dispatch(profile, reservation).won

    def no_fresh_guard(_con):
        pytest.fail("a paid attempt replay must not reacquire Home admission")

    paid = _reserve(profile, spec, guard=no_fresh_guard)
    assert paid.attempt_id == reservation.attempt_id
    assert not _dispatch(profile, paid, guard=no_fresh_guard).won
    budget.settle(reservation.attempt_id, REQUEST, profile=profile, result_text="stored paid answer", now=NOW)
    settled = _reserve(profile, spec, guard=no_fresh_guard)
    assert settled.attempt_id == reservation.attempt_id
    assert settled.state == "settled"
    result = budget.get_result(reservation.attempt_id, REQUEST, profile=profile)
    assert result.response_complete and result.text == "stored paid answer"
    budget.settle(reservation.attempt_id, REQUEST, profile=profile, result_text="stored paid answer", now=NOW)
    assert _query(profile, "SELECT COUNT(*) FROM budget_attempt") == [(1,)]


def test_quota_rejection_does_not_run_dispatch_guard_or_grant_external_opportunity(profile):
    reservation = _reserve(profile, _spec())

    def no_guard(_con):
        pytest.fail("quota rejection must occur before the original dispatch guard")

    with pytest.raises(budget_contract.BudgetDeferred, match="daily_limit"):
        _dispatch(profile, reservation, guard=no_guard, policy=_policy(model_daily_attempts=0))
    assert _query(profile, "SELECT state,dispatched_at FROM budget_attempt") == [("reserved", None)]


def test_business_connection_and_original_exception_boundaries_remain_owned(profile):
    business = sqlite3.connect(profile.database)
    try:
        business.execute("BEGIN")
        with pytest.raises(budget.BudgetLedgerError, match="BUDGET_BUSINESS_TRANSACTION_ACTIVE"):
            budget.reserve(_spec(), _attempt(), profile=profile, policy=_policy(), now=NOW,
                           business_connection=business)
        assert business.in_transaction
        business.rollback()
        observed = []

        def guard(con):
            assert con is not business
            observed.append(witness.require_writer(con, profile=profile))

        budget.reserve(_spec(), _attempt(), profile=profile, policy=_policy(), now=NOW,
                       business_connection=business, reservation_guard=guard)
        assert len(observed) == 1
        assert business.execute("SELECT 1").fetchone()[0] == 1
    finally:
        business.close()
