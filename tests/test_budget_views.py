"""Read projections keep original budget windows and distinguish unknown from zero."""
from datetime import datetime, timedelta, timezone
import hashlib
import sqlite3
import uuid

import pytest

from reno import budget, budget_contract, db, storage_lifecycle


NOW = datetime(2026, 10, 2, 12, tzinfo=timezone.utc)
SHA = hashlib.sha256(b"synthetic-b07-request").hexdigest()


@pytest.fixture
def profile(tmp_path):
    value = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(value.database)
    storage_lifecycle.initialize(value)
    return value


def reserve(profile, *, kind="model", ordinal=1, spec=None, now=NOW, adapter="paas_v4", output=100):
    spec = spec or budget.OperationSpec(str(uuid.uuid4()), 1, kind, "synthetic", SHA)
    attempt = (budget.AttemptSpec(SHA, ordinal, adapter, output) if kind == "model"
        else budget.AttemptSpec(SHA, ordinal, download_segment="video" if kind == "download" else None))
    return spec, budget.reserve(spec, attempt, profile=profile, policy=budget_contract.Policy(), now=now)


def dispatch(profile, reservation, now=NOW):
    return budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256, str(uuid.uuid4()),
        expected_revision=reservation.revision, profile=profile, policy=budget_contract.Policy(), now=now)


def settle(profile, reservation, usage=None, *, complete=True, now=NOW):
    return budget.settle(reservation.attempt_id, SHA, profile=profile, usage=usage,
        response_complete=complete, result_text="synthetic-paid" if complete else None, now=now)


def inspect(profile, *, timezone_name="UTC", now=NOW):
    from reno import budget_views
    return budget_views.inspect(profile=profile,
        policy=budget_contract.Policy(budget_timezone=timezone_name), now=now)


def test_four_states_and_paid_missing_output_have_separate_budget_occupancy(profile):
    _, held = reserve(profile)
    _, in_flight = reserve(profile)
    dispatch(profile, in_flight)
    _, unknown = reserve(profile)
    dispatch(profile, unknown)
    settle(profile, unknown, complete=False)
    _, complete = reserve(profile)
    dispatch(profile, complete)
    settle(profile, complete)
    _, released = reserve(profile)
    budget.release_reserved(released.attempt_id, released.reservation_sha256,
        expected_revision=released.revision, profile=profile, now=NOW)
    body = inspect(profile)
    assert body["attempts"]["model"] == {"admissions": 4, "confirmed": 1,
        "held": 1, "in_flight": 1, "unknown": 1}
    assert body["conservative_output_tokens"] == {"held": 100, "in_flight": 100,
        "unknown": 100, "confirmed_missing_output": 100, "total": 400}
    assert body["tokens"]["total_tokens"] == {"reported": None, "covered_attempts": 0,
        "missing_attempts": 3, "dispatched_attempts": 3}
    assert body["tokens"]["quality_counts"] == {"reported": 0, "partial": 0,
        "missing": 1, "invalid": 0, "unobserved": 2}
    assert body["all_history"]["unknown_attempts"] == 1
    assert body["history_before_ledger"] == "unknown"


def test_each_token_field_has_own_coverage_and_no_derived_total(profile):
    _, paid = reserve(profile, adapter="glm53_anthropic", output=128000)
    dispatch(profile, paid)
    settle(profile, paid, budget_contract.parse_usage("glm53_anthropic", {"input_tokens": 10, "output_tokens": 4}))
    _, partial = reserve(profile)
    dispatch(profile, partial)
    settle(profile, partial, budget_contract.parse_usage("paas_v4", {"prompt_tokens": 3, "total_tokens": 8}))
    _, invalid = reserve(profile)
    dispatch(profile, invalid)
    settle(profile, invalid, budget_contract.parse_usage("paas_v4", {"prompt_tokens": "invalid", "completion_tokens": 2}))
    body = inspect(profile)
    assert body["tokens"]["input_tokens"] == {"reported": 13, "covered_attempts": 2,
        "missing_attempts": 1, "dispatched_attempts": 3}
    assert body["tokens"]["output_tokens"]["reported"] == 6
    assert body["tokens"]["total_tokens"] == {"reported": 8, "covered_attempts": 1,
        "missing_attempts": 2, "dispatched_attempts": 3}
    assert body["tokens"]["quality_counts"] == {"reported": 1, "partial": 1,
        "missing": 0, "invalid": 1, "unobserved": 0}
    assert body["conservative_output_tokens"]["confirmed_missing_output"] == 100


def test_held_only_has_no_provider_observations_and_missing_does_not_count_as_zero(profile):
    _, held = reserve(profile)
    body = inspect(profile)
    assert body["tokens"]["total_tokens"]["reported"] == 0
    assert body["tokens"]["total_tokens"]["dispatched_attempts"] == 0
    dispatch(profile, held)
    body = inspect(profile)
    assert body["tokens"]["total_tokens"]["reported"] is None
    assert body["tokens"]["total_tokens"]["missing_attempts"] == 1


def test_original_natural_windows_overlap_after_timezone_change_without_rewriting(profile):
    _, reservation = reserve(profile, now=datetime(2026, 10, 1, 23, tzinfo=timezone.utc))
    before = snapshot_rows(profile)
    body = inspect(profile, timezone_name="Asia/Shanghai", now=datetime(2026, 10, 2, 0, tzinfo=timezone.utc))
    assert body["window"]["local_date"] == "2026-10-02"
    assert body["window"]["timezone"] == "Asia/Shanghai"
    assert body["window"]["start_utc"] == "2026-10-01T16:00:00.000000Z"
    assert body["attempts"]["model"]["held"] == 1
    assert snapshot_rows(profile) == before
    later = inspect(profile, now=datetime(2026, 10, 2, 12, tzinfo=timezone.utc))
    assert later["attempts"]["model"]["admissions"] == 0


def test_dst_window_is_actual_server_natural_day(profile):
    body = inspect(profile, timezone_name="America/New_York", now=datetime(2026, 3, 8, 12, tzinfo=timezone.utc))
    assert body["window"]["start_utc"] == "2026-03-08T05:00:00.000000Z"
    assert body["window"]["end_utc"] == "2026-03-09T04:00:00.000000Z"


def test_download_retry_processes_do_not_claim_another_logical_video_today(profile):
    yesterday = NOW - timedelta(days=1)
    spec, first = reserve(profile, kind="download", now=yesterday)
    dispatch(profile, first, yesterday)
    budget.settle(first.attempt_id, SHA, profile=profile, outcome="http_retry", now=yesterday)
    _, retry = reserve(profile, kind="download", ordinal=2, spec=spec)
    dispatch(profile, retry)
    _, new_held = reserve(profile, kind="download")
    body = inspect(profile)
    assert body["download_logical_admissions"] == 1
    assert body["attempts"]["download"]["admissions"] == 2
    assert body["attempts"]["download"]["held"] == 1
    assert body["attempts"]["download"]["in_flight"] == 1


def test_source_opportunities_remain_distinct_from_model_and_media_counts(profile):
    for kind in ("source_resolution", "source_metadata", "source_enumeration"):
        reserve(profile, kind=kind)
    body = inspect(profile)
    assert body["attempts"]["model"]["admissions"] == 0
    assert body["attempts"]["download"]["admissions"] == 0
    assert all(body["attempts"][kind]["held"] == 1
        for kind in ("source_resolution", "source_metadata", "source_enumeration"))


def test_all_history_unknown_and_waits_are_not_labeled_today(profile):
    yesterday = NOW - timedelta(days=1)
    _, unknown = reserve(profile, now=yesterday)
    dispatch(profile, unknown, yesterday)
    settle(profile, unknown, complete=False, now=yesterday)
    body = inspect(profile)
    assert body["attempts"]["model"]["unknown"] == 0
    assert body["all_history"]["unknown_attempts"] == 1
    assert body["waiting"]["scope"] == "all_history"
    assert body["waiting"]["total"] == 1
    assert body["waiting"]["operations"][0]["reason"] == "indeterminate"
    assert body["waiting"]["operations"][0]["resume_at"] is None


def snapshot_rows(profile):
    with sqlite3.connect(profile.database) as con:
        return con.execute("SELECT * FROM budget_attempt ORDER BY attempt_id").fetchall()


def test_read_is_query_only_and_does_not_fetch_private_result_or_request_content(profile, monkeypatch):
    _, paid = reserve(profile)
    dispatch(profile, paid)
    settle(profile, paid)
    from reno import budget_views, migrations
    original = migrations._open
    reads = []
    def readonly(path, **kwargs):
        assert kwargs == {"readonly": True}
        con = original(path, **kwargs)
        con.set_trace_callback(reads.append)
        return con
    monkeypatch.setattr(migrations, "_open", readonly)
    before = snapshot_rows(profile)
    body = inspect(profile)
    assert snapshot_rows(profile) == before
    assert "synthetic-paid" not in str(body)
    selects = [statement.lower() for statement in reads if statement.lower().startswith("select")]
    assert not any("a.*" in statement or "result_text" in statement or "frozen_request_json" in statement
        for statement in selects)
    assert any(statement.lower() == "pragma query_only=on" for statement in reads)


def test_bad_current_fact_returns_controlled_unavailable_instead_of_counts(profile):
    _, held = reserve(profile)
    with sqlite3.connect(profile.database) as con:
        con.execute("UPDATE budget_attempt SET current_reservation_sha256=? WHERE attempt_id=?", ("0" * 64, held.attempt_id))
    from reno import budget_views
    with pytest.raises(budget_views.BudgetViewError) as caught:
        inspect(profile)
    assert caught.value.code == "BUDGET_VIEW_UNAVAILABLE"
    assert str(profile.database) not in str(caught.value)
