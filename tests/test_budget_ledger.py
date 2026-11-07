"""Durable P06 admission and dispatch using only isolated synthetic storage."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from reno import budget, budget_contract as contract, db, storage_lifecycle

NOW = datetime(2026, 10, 1, 12, tzinfo=timezone.utc)
INPUT = hashlib.sha256(b"frozen synthetic input").hexdigest()
REQUEST = hashlib.sha256(b"synthetic actual request").hexdigest()


@pytest.fixture
def profile(tmp_path):
    value = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                             tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(value.database)
    storage_lifecycle.initialize(value)
    return value


def policy(**values):
    return contract.parse_policy({"budget_enabled": True, **values})


def operation(*, kind="model", continuation=None, key="atomize:batch:0", **values):
    return budget.OperationSpec(continuation or str(uuid.uuid4()), 1, kind, key, INPUT, **values)


def attempt(*, ordinal=1, adapter="paas_v4", request=REQUEST, output=100):
    return budget.AttemptSpec(request, ordinal, adapter, output)


def reserve(profile, spec=None, request=None, settings=None, now=NOW):
    return budget.reserve(spec or operation(), request or attempt(), profile=profile,
                          policy=settings or policy(), now=now)


def dispatch(profile, reservation, settings=None, now=NOW, token=None):
    return budget.mark_dispatching(reservation.attempt_id, reservation.reservation_sha256,
        token or str(uuid.uuid4()), expected_revision=reservation.revision,
        profile=profile, policy=settings or policy(), now=now)


def query(profile, sql, parameters=()):
    with sqlite3.connect(profile.database) as con:
        con.row_factory = sqlite3.Row
        return [dict(row) for row in con.execute(sql, parameters)]


def complete(profile, reservation, *, text="paid answer", usage=None, outcome="full_result", now=NOW):
    return budget.settle(reservation.attempt_id, REQUEST, profile=profile, result_text=text,
        usage=usage, outcome=outcome, now=now)


def job(profile, continuation, generation=1):
    with sqlite3.connect(profile.database) as con:
        return con.execute("INSERT INTO job(kind,continuation_id,execution_generation) VALUES ('process',?,?)",
                           (continuation, generation)).lastrowid


@pytest.mark.parametrize("ceiling", [1, 127999])
def test_glm_effective_ceiling_cannot_claim_less_than_actual_payload_floor(ceiling):
    with pytest.raises(budget.BudgetLedgerError):
        attempt(adapter="glm53_anthropic", output=ceiling)


def test_glm_floor_and_paas_lower_effective_ceiling_are_valid_payload_facts():
    assert attempt(adapter="glm53_anthropic", output=128000).effective_max_tokens == 128000
    assert attempt(output=1).effective_max_tokens == 1


def test_root_job_id_accepts_integer_and_checks_existing_durable_lineage(profile):
    continuation = str(uuid.uuid4())
    root = job(profile, continuation)
    result = reserve(profile, operation(continuation=continuation, root_job_id=root))
    assert query(profile, "SELECT root_job_id FROM budget_operation")[0]["root_job_id"] == root
    assert result.state == "reserved"


@pytest.mark.parametrize("wrong", ["1", True, 0, -1])
def test_root_job_id_rejects_nonpositive_or_coerced_integer(wrong):
    with pytest.raises(budget.BudgetLedgerError):
        operation(root_job_id=wrong)


@pytest.mark.parametrize("binding", ["absent", "wrong_lineage", "wrong_generation", "missing_lineage"])
def test_root_job_id_refuses_nonexistent_or_mismatched_lineage(profile, binding):
    continuation = str(uuid.uuid4())
    if binding == "absent":
        root = 999
    elif binding == "wrong_lineage":
        root = job(profile, str(uuid.uuid4()))
    elif binding == "wrong_generation":
        root = job(profile, continuation, 2)
    else:
        root = job(profile, None)
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, operation(continuation=continuation, root_job_id=root))
    assert query(profile, "SELECT * FROM budget_operation") == []


def test_existing_operation_does_not_mask_wrong_new_job_binding(profile):
    continuation = str(uuid.uuid4())
    reserve(profile, operation(continuation=continuation, root_job_id=job(profile, continuation)))
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, operation(continuation=continuation, root_job_id=job(profile, str(uuid.uuid4()))))


def test_operation_identity_uses_each_actual_library_not_callers_job_number(profile, tmp_path):
    other = storage_lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
                                            tmp_path / "other/reports", tmp_path / "other/checkpoints")
    db.initialize(other.database)
    storage_lifecycle.initialize(other)
    continuation = str(uuid.uuid4())
    left_root, right_root = job(profile, continuation), job(other, continuation)
    assert left_root == right_root
    left = reserve(profile, operation(continuation=continuation, root_job_id=left_root))
    right = reserve(other, operation(continuation=continuation, root_job_id=right_root))
    assert left.operation_id != right.operation_id
    libraries = [query(value, "SELECT library_id FROM budget_operation")[0]["library_id"] for value in (profile, other)]
    assert len(set(libraries)) == 2


def test_reserve_replay_is_same_attempt_and_dispatch_has_one_winner(profile):
    spec = operation()
    first = reserve(profile, spec)
    replay = reserve(profile, spec)
    assert first == replay and first.state == "reserved"
    assert dispatch(profile, first).won is True
    assert dispatch(profile, replay).won is False
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


def test_request_mismatch_and_reused_dispatch_token_fail_closed(profile):
    first, second = reserve(profile), reserve(profile)
    token = str(uuid.uuid4())
    assert dispatch(profile, first, token=token).won
    with pytest.raises(budget.BudgetLedgerError):
        dispatch(profile, second, token=token)
    spec = operation()
    reserve(profile, spec)
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, spec, attempt(request=hashlib.sha256(b"changed").hexdigest()))


def test_dispatch_and_release_require_revision_even_when_snapshot_digest_is_unchanged(profile):
    first = reserve(profile)
    with sqlite3.connect(profile.database) as con:
        con.execute("UPDATE budget_attempt SET reservation_revision=reservation_revision+1 WHERE attempt_id=?",
                    (first.attempt_id,))
    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_RESERVATION_CONFLICT"):
        dispatch(profile, first)
    with pytest.raises(budget.BudgetLedgerError, match="BUDGET_RESERVATION_CONFLICT"):
        budget.release_reserved(first.attempt_id, first.reservation_sha256, expected_revision=first.revision,
                                profile=profile, now=NOW)


def test_daily_quota_wait_is_durable_and_creates_no_fake_attempt(profile):
    limited = policy(model_daily_attempts=1)
    reserve(profile, settings=limited)
    spec = operation()
    with pytest.raises(contract.BudgetDeferred) as caught:
        reserve(profile, spec, settings=limited)
    assert caught.value.reason == "daily_limit" and caught.value.resume_at > NOW
    waiting = query(profile, "SELECT * FROM budget_operation WHERE operation_id=?", (caught.value.operation_id,))[0]
    assert waiting["state"] == "waiting" and waiting["wait_reason"] == "daily_limit"
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1
    next_day = NOW + timedelta(days=1)
    assert reserve(profile, spec, settings=limited, now=next_day).state == "reserved"


def test_adapter_labels_do_not_split_model_daily_allowance(profile):
    limited = policy(model_daily_attempts=1)
    reserve(profile, settings=limited)
    with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        reserve(profile, request=attempt(adapter="glm53_anthropic", output=128000), settings=limited)


def test_dispatch_revalidates_current_policy_and_unholds_waiting_reservation(profile):
    spec = operation()
    reserved = reserve(profile, spec)
    initial = query(profile, "SELECT initial_reservation_json FROM budget_attempt")[0]
    with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        dispatch(profile, reserved, policy(model_daily_attempts=0))
    row = query(profile, "SELECT * FROM budget_attempt")[0]
    assert row["state"] == "reserved" and row["reservation_held"] == 0
    assert row["initial_reservation_json"] == initial["initial_reservation_json"]
    recovered = budget.reserve(spec, attempt(), profile=profile, policy=policy(), now=NOW)
    assert recovered.attempt_id == reserved.attempt_id and dispatch(profile, recovered).won


@pytest.mark.parametrize("entry", ["reserve", "dispatch"])
def test_default_policy_is_fresh_after_writer_admission_gap(profile, monkeypatch, entry):
    original = budget._connection
    current = {"policy": policy(model_daily_attempts=1)}
    reads = []
    active = {"con": None}
    def settings():
        reads.append(active["con"] is not None and active["con"].in_transaction)
        return current["policy"].public()
    monkeypatch.setattr(budget.config, "budget_settings", settings)
    first = reserve(profile) if entry == "dispatch" else None
    @contextmanager
    def after_writer(*args, **kwargs):
        with original(*args, **kwargs) as con:
            # Simulates a file policy change while admission waits for SH/SQL.
            current["policy"] = policy(model_daily_attempts=0)
            active["con"] = con
            try:
                yield con
            finally:
                active["con"] = None
    monkeypatch.setattr(budget, "_connection", after_writer)
    with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        if entry == "reserve":
            budget.reserve(operation(), attempt(), profile=profile, now=NOW)
        else:
            budget.mark_dispatching(first.attempt_id, first.reservation_sha256, str(uuid.uuid4()),
                                    expected_revision=first.revision, profile=profile, now=NOW)
    assert reads and reads[-1] is True
    assert not any(row["state"] == "dispatching" for row in query(profile, "SELECT * FROM budget_attempt"))


def test_default_clock_is_read_after_writer_gap_not_before_midnight(profile, monkeypatch):
    original = budget._connection
    current = {"now": NOW}
    monkeypatch.setattr(budget, "_now", lambda value: current["now"] if value is None else value)
    @contextmanager
    def after_writer(*args, **kwargs):
        with original(*args, **kwargs) as con:
            current["now"] = NOW + timedelta(days=1)
            yield con
    monkeypatch.setattr(budget, "_connection", after_writer)
    result = budget.reserve(operation(), attempt(), profile=profile, policy=policy())
    row = query(profile, "SELECT current_reservation_json FROM budget_attempt WHERE attempt_id=?", (result.attempt_id,))[0]
    assert json.loads(row["current_reservation_json"])["window"]["local_date"] == "2026-10-02"


def test_cross_day_dispatch_migrates_same_uuid_and_freezes_first_dispatch(profile):
    spec = operation()
    first = reserve(profile, spec)
    tomorrow = NOW + timedelta(days=1)
    assert dispatch(profile, first, now=tomorrow).won
    row = query(profile, "SELECT * FROM budget_attempt")[0]
    initial, current = json.loads(row["initial_reservation_json"]), json.loads(row["current_reservation_json"])
    assert initial["window"]["local_date"] == "2026-10-01"
    assert current["window"]["local_date"] == "2026-10-02"
    assert row["reservation_revision"] == 1 and row["attempt_id"] == first.attempt_id
    complete(profile, first, usage=contract.parse_usage("paas_v4", {"prompt_tokens": 2, "completion_tokens": 3,
        "total_tokens": 5}), now=tomorrow + timedelta(days=1))
    row_after = query(profile, "SELECT * FROM budget_attempt")[0]
    assert row_after["first_dispatch_json"] == row["first_dispatch_json"]
    assert row_after["current_window_start_utc"] == row["current_window_start_utc"]


def test_timezone_switch_counts_positive_original_window_overlap_once(profile):
    limited = policy(model_daily_attempts=1)
    first = reserve(profile, settings=limited)
    dispatch(profile, first, limited)
    switched = policy(model_daily_attempts=1, budget_timezone="Asia/Shanghai")
    with pytest.raises(contract.BudgetDeferred, match="daily_limit"):
        reserve(profile, settings=switched)
    # UTC midnight following the recorded UTC window only touches the boundary.
    midnight = datetime(2026, 10, 2, tzinfo=timezone.utc)
    assert reserve(profile, settings=limited, now=midnight).state == "reserved"


def test_clock_rollback_persists_wait_without_creating_attempt(profile):
    reserve(profile)
    with pytest.raises(contract.BudgetDeferred, match="clock_rollback") as caught:
        reserve(profile, now=NOW - timedelta(seconds=1))
    waiting = query(profile, "SELECT * FROM budget_operation WHERE operation_id=?", (caught.value.operation_id,))[0]
    assert waiting["wait_reason"] == "clock_rollback" and waiting["resume_at"] is None
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


def test_reserved_release_is_idempotent_but_cannot_release_dispatched_or_unknown(profile):
    first = reserve(profile)
    released = budget.release_reserved(first.attempt_id, first.reservation_sha256,
        expected_revision=first.revision, profile=profile, now=NOW)
    assert released.state == "released"
    assert budget.release_reserved(first.attempt_id, first.reservation_sha256,
        expected_revision=released.revision, profile=profile, now=NOW) == released
    second = reserve(profile)
    dispatch(profile, second)
    budget.settle(second.attempt_id, REQUEST, profile=profile, response_complete=False, now=NOW)
    with pytest.raises(budget.BudgetLedgerError):
        budget.release_reserved(second.attempt_id, second.reservation_sha256,
            expected_revision=second.revision, profile=profile, now=NOW)


def test_unknown_blocks_automatic_retry_but_late_complete_response_can_settle(profile):
    spec = operation()
    first = reserve(profile, spec)
    dispatch(profile, first)
    unknown = budget.settle(first.attempt_id, REQUEST, profile=profile, response_complete=False, now=NOW)
    assert unknown.response_complete is False and unknown.storage_state == "none"
    row = query(profile, "SELECT * FROM budget_attempt")[0]
    assert row["state"] == "indeterminate" and row["settlement_sha256"] is None
    with pytest.raises(contract.BudgetDeferred, match="indeterminate"):
        reserve(profile, spec, attempt(ordinal=2), now=NOW + timedelta(days=1))
    late = complete(profile, first, text="late answer", now=NOW + timedelta(days=1))
    assert late.response_complete and late.text == "late answer"
    assert query(profile, "SELECT * FROM budget_attempt")[0]["state"] == "settled"


def test_complete_missing_usage_reuses_body_without_inventing_tokens(profile):
    first = reserve(profile)
    dispatch(profile, first)
    settled = complete(profile, first)
    assert settled.response_complete and settled.text == "paid answer" and settled.usage.quality == "missing"
    cached = budget.get_result(first.attempt_id, REQUEST, profile=profile)
    assert cached == settled and cached.usage.total_tokens is None
    totals = budget.summarize(profile=profile, policy=policy(), now=NOW)
    assert totals["model_attempt_admissions"] == 1
    assert totals["reported_total_tokens"] is None and totals["unknown_usage_attempts"] == 1


def test_empty_ledger_never_claims_zero_historical_external_consumption(profile):
    with sqlite3.connect(profile.database) as con:
        con.execute("INSERT INTO video_asset(video_id,title) VALUES ('legacy-video','processed before ledger')")
        con.execute("INSERT INTO knowledge_atom(id,video_id,claim) VALUES ('legacy-atom','legacy-video','existing knowledge')")
    summary = budget.summarize(profile=profile, policy=policy(), now=NOW)
    assert summary["scope"] == "local_ledger_only" and summary["history_before_ledger"] == "unknown"
    assert summary["model_attempt_admissions"] == 0 and summary["reported_total_tokens"] == 0
    assert query(profile, "SELECT * FROM knowledge_atom")[0]["id"] == "legacy-atom"


def test_readonly_lookup_is_exact_and_does_not_create_operation_or_read_policy(profile, monkeypatch):
    monkeypatch.setattr(budget.config, "budget_settings", lambda: pytest.fail("readonly lookup must not consult quota"))
    spec = operation()
    before = profile.database.read_bytes()
    assert budget.get_operation(spec, profile=profile) is None
    assert budget.get_attempt(str(uuid.uuid4()), 1, profile=profile) is None
    assert profile.database.read_bytes() == before
    assert query(profile, "SELECT * FROM budget_operation") == []


def test_readonly_operation_restores_closed_metadata_and_latest_ordinal(profile, monkeypatch):
    spec = operation()
    first = reserve(profile, spec)
    dispatch(profile, first)
    complete(profile, first, text="private paid answer", outcome="repair")
    second = reserve(profile, spec, attempt(ordinal=2))
    budget.release_reserved(second.attempt_id, second.reservation_sha256, expected_revision=second.revision,
                            profile=profile, now=NOW)
    monkeypatch.setattr(budget.config, "budget_settings", lambda: pytest.fail("cache lookup precedes current daily quota"))
    view = budget.get_operation(spec, profile=profile)
    assert view.operation_id == first.operation_id and view.max_attempts == 8 and view.latest_ordinal == 2
    assert type(view.attempts) is tuple and len(view.attempts) == 2
    assert view.attempts[0].response_complete and view.attempts[0].outcome == "repair"
    assert view.attempts[1].state == "released"
    assert budget.get_attempt(view.operation_id, 1, profile=profile) == view.attempts[0]
    assert budget.get_attempt(view.operation_id, 3, profile=profile) is None
    with pytest.raises(AttributeError):
        view.max_attempts = 128
    public = view.public()
    public["attempts"].clear()
    assert len(view.attempts) == 2 and "private" not in json.dumps(view.public())
    assert "provider_request_id" not in json.dumps(view.public())
    changed = budget.OperationSpec(spec.continuation_id, 2, spec.kind, spec.suboperation_key, spec.input_sha256)
    assert budget.get_operation(changed, profile=profile) is None


def test_readonly_lookup_preserves_infinite_wait_reason(profile):
    spec = operation()
    with pytest.raises(contract.BudgetDeferred, match="unsupported_capability"):
        reserve(profile, spec, settings=policy(model_daily_output_tokens=1))
    view = budget.get_operation(spec, profile=profile)
    assert view.state == "waiting" and view.wait_reason == "unsupported_capability" and view.resume_at is None
    assert view.latest_ordinal == 0 and view.attempts == ()


def test_readonly_summary_uses_one_snapshot_during_concurrent_settlement(profile, monkeypatch):
    with sqlite3.connect(profile.database) as con:
        assert con.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
    first = reserve(profile)
    dispatch(profile, first)
    original = budget._counts
    once = []
    def interleave(con, *args, **kwargs):
        counts = original(con, *args, **kwargs)
        if not once:
            once.append(True)
            complete(profile, first, usage=contract.parse_usage("paas_v4", {"prompt_tokens": 2,
                "completion_tokens": 3, "total_tokens": 5}))
        return counts
    monkeypatch.setattr(budget, "_counts", interleave)
    before = budget.summarize(profile=profile, policy=policy(), now=NOW)
    assert before["unknown_usage_attempts"] == 1 and before["stored_results"] == 0
    after = budget.summarize(profile=profile, policy=policy(), now=NOW)
    assert after["unknown_usage_attempts"] == 0 and after["stored_results"] == 1
    assert after["reported_total_tokens"] == 5


@pytest.mark.parametrize("kind,raw", [
    ("model", "not-json"), ("model", '{"secret":"private-input"}'),
    ("model", '{"download_interval_seconds":0}'),
    ("download", "not-json"), ("download", '{"download_interval_seconds":true}'),
    ("download", '{ "download_interval_seconds":0 }'),
    ("download", '{"download_interval_seconds":0,"download_interval_seconds":1}'),
    ("download", '{"secret":"private-input"}'),
])
def test_wait_metadata_is_canonical_closed_and_never_leaks_bad_input(profile, kind, raw):
    spec = operation(kind=kind)
    request = attempt() if kind == "model" else budget.AttemptSpec(REQUEST, download_segment="video")
    first = reserve(profile, spec, request)
    with sqlite3.connect(profile.database) as con:
        con.execute("UPDATE budget_operation SET wait_details_json=? WHERE operation_id=?", (raw, first.operation_id))
    with pytest.raises(budget.BudgetLedgerError) as caught:
        reserve(profile, spec, request)
    assert "private-input" not in str(caught.value)


def test_settlement_duplicate_is_idempotent_and_conflicting_digest_is_rejected(profile):
    first = reserve(profile)
    dispatch(profile, first)
    usage = contract.parse_usage("paas_v4", {"prompt_tokens": 4, "completion_tokens": 6, "total_tokens": 10})
    result = complete(profile, first, usage=usage)
    assert complete(profile, first, usage=usage, now=NOW + timedelta(seconds=10)) == result
    with pytest.raises(budget.BudgetLedgerError):
        complete(profile, first, usage=usage, text="different paid answer")
    summary = budget.summarize(profile=profile, policy=policy(), now=NOW)
    assert summary["reported_total_tokens"] == 10 and summary["model_attempt_admissions"] == 1


def test_reported_threshold_stops_next_attempt_and_output_cap_requires_proof(profile):
    settings = policy(model_reported_tokens_stop_at=5)
    first = reserve(profile, settings=settings)
    dispatch(profile, first, settings)
    complete(profile, first, usage=contract.parse_usage("paas_v4", {"total_tokens": 7}))
    with pytest.raises(contract.BudgetDeferred, match="reported_tokens_limit"):
        reserve(profile, settings=settings)
    with pytest.raises(contract.BudgetDeferred, match="unsupported_capability"):
        reserve(profile, settings=policy(model_daily_output_tokens=1000))


def test_retry_ordinal_limit_is_initial_and_released_attempt_still_uses_ordinal(profile):
    spec = operation()
    first = reserve(profile, spec, settings=policy(model_operation_max_attempts=2))
    budget.release_reserved(first.attempt_id, first.reservation_sha256,
        expected_revision=first.revision, profile=profile, now=NOW)
    second = reserve(profile, spec, attempt(ordinal=2), settings=policy(model_operation_max_attempts=128))
    dispatch(profile, second)
    complete(profile, second, outcome="repair")
    with pytest.raises(contract.BudgetDeferred, match="attempt_limit"):
        reserve(profile, spec, attempt(ordinal=3), settings=policy(model_operation_max_attempts=128))
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 2


def test_full_result_cannot_be_reissued_just_because_job_commit_failed(profile):
    spec = operation()
    first = reserve(profile, spec)
    dispatch(profile, first)
    complete(profile, first)
    assert reserve(profile, spec).state == "settled"
    assert dispatch(profile, first).won is False
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile, spec, attempt(ordinal=2))


def test_result_bounds_keep_paid_digest_and_usage_and_block_free_retry(profile, monkeypatch):
    monkeypatch.setattr(budget, "MAX_RESULT_BYTES", 4)
    monkeypatch.setattr(budget, "MAX_OPERATION_RESULT_BYTES", 8)
    spec = operation()
    first = reserve(profile, spec)
    dispatch(profile, first)
    usage = contract.parse_usage("paas_v4", {"total_tokens": 9})
    result = complete(profile, first, text="too long", usage=usage, outcome="repair")
    assert result.text is None and result.storage_state == "oversize"
    assert result.result_sha256 == hashlib.sha256(b"too long").hexdigest()
    assert result.usage.total_tokens == 9
    with pytest.raises(contract.BudgetDeferred, match="result_capacity"):
        reserve(profile, spec, attempt(ordinal=2))


def test_operation_capacity_waits_before_new_request_and_keeps_existing_paid_results(profile, monkeypatch):
    monkeypatch.setattr(budget, "MAX_RESULT_BYTES", 4)
    monkeypatch.setattr(budget, "MAX_OPERATION_RESULT_BYTES", 8)
    spec = operation()
    first = reserve(profile, spec)
    dispatch(profile, first)
    complete(profile, first, text="1234", outcome="repair")
    second = reserve(profile, spec, attempt(ordinal=2))
    dispatch(profile, second)
    complete(profile, second, text="5678", outcome="repair")
    with pytest.raises(contract.BudgetDeferred, match="result_capacity"):
        reserve(profile, spec, attempt(ordinal=3))
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 2
    assert budget.get_result(first.attempt_id, REQUEST, profile=profile).text == "1234"
    assert budget.get_result(second.attempt_id, REQUEST, profile=profile).text == "5678"


def test_result_public_projection_excludes_body_and_private_provider_id(profile):
    first = reserve(profile)
    dispatch(profile, first)
    result = budget.settle(first.attempt_id, REQUEST, profile=profile, result_text="private answer",
                          provider_request_id="private provider id", now=NOW)
    public = result.public()
    assert public["response_complete"] and public["usage_quality"] == "missing"
    assert public["result_storage_state"] == "stored"
    assert "private" not in json.dumps(public) and "text" not in public and "provider_request_id" not in public


@pytest.mark.parametrize("field", ["body", "usage"])
def test_corrupted_private_result_cannot_replace_paid_response(profile, field):
    first = reserve(profile)
    dispatch(profile, first)
    usage = contract.parse_usage("paas_v4", {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
    complete(profile, first, usage=usage)
    with sqlite3.connect(profile.database) as con:
        if field == "body":
            con.execute("UPDATE budget_attempt SET result_text='evil answer' WHERE attempt_id=?", (first.attempt_id,))
        else:
            value = usage.public()
            value["total_tokens"] = 999
            raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
            con.execute("UPDATE budget_attempt SET observed_usage_json=? WHERE attempt_id=?", (raw, first.attempt_id))
    with pytest.raises(budget.BudgetLedgerError):
        budget.get_result(first.attempt_id, REQUEST, profile=profile)


@pytest.mark.parametrize("field", ["first_dispatch_json", "observed_usage_json"])
def test_corrupted_meter_fact_cannot_be_used_for_admission_or_summary(profile, field):
    first = reserve(profile)
    dispatch(profile, first)
    usage = contract.parse_usage("paas_v4", {"prompt_tokens": 2, "completion_tokens": 3, "total_tokens": 5})
    complete(profile, first, usage=usage)
    row = query(profile, "SELECT * FROM budget_attempt")[0]
    value = json.loads(row[field])
    if field == "first_dispatch_json":
        value["ordinal"] = 2
    else:
        value["total_tokens"] = 999
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(profile.database) as con:
        if field == "first_dispatch_json":
            con.execute("UPDATE budget_attempt SET first_dispatch_json=?,first_dispatch_sha256=? WHERE attempt_id=?",
                        (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), first.attempt_id))
        else:
            con.execute("UPDATE budget_attempt SET observed_usage_json=? WHERE attempt_id=?", (raw, first.attempt_id))
    with pytest.raises(budget.BudgetLedgerError):
        reserve(profile)
    with pytest.raises(budget.BudgetLedgerError):
        budget.summarize(profile=profile, policy=policy(), now=NOW)


def test_download_pacing_sample_is_durable_and_logical_quota_is_not_per_process(profile, monkeypatch):
    settings = policy(download_daily_videos=1, download_interval_min_seconds=10, download_interval_max_seconds=10)
    first_spec = operation(kind="download", key="media:0")
    media_attempt = budget.AttemptSpec(REQUEST, download_segment="video")
    first = reserve(profile, first_spec, media_attempt, settings)
    dispatch(profile, first, settings)
    complete(profile, first, outcome="http_retry", text=None)
    retry = reserve(profile, first_spec, budget.AttemptSpec(REQUEST, ordinal=2, download_segment="video"), settings, NOW + timedelta(seconds=10))
    assert dispatch(profile, retry, settings, NOW + timedelta(seconds=10)).won
    second_spec = operation(kind="download", key="media:1")
    with pytest.raises(contract.BudgetDeferred):
        reserve(profile, second_spec, media_attempt, settings, NOW + timedelta(seconds=10))
    waiting = query(profile, "SELECT * FROM budget_operation WHERE continuation_id=?", (second_spec.continuation_id,))[0]
    assert json.loads(waiting["wait_details_json"])["download_interval_seconds"] == 10
    summary = budget.summarize(profile=profile, policy=settings, now=NOW + timedelta(seconds=10))
    assert summary["download_logical_admissions"] == 1 and summary["download_process_attempts"] == 2


def test_disabled_policy_releases_old_pacing_wait_without_erasing_original_sample(profile, monkeypatch):
    settings = policy(download_interval_min_seconds=10, download_interval_max_seconds=10)
    media_attempt = budget.AttemptSpec(REQUEST, download_segment="video")
    first = reserve(profile, operation(kind="download"), media_attempt, settings)
    dispatch(profile, first, settings)
    waiting_spec = operation(kind="download")
    with pytest.raises(contract.BudgetDeferred, match="download_pacing"):
        reserve(profile, waiting_spec, media_attempt, settings)
    monkeypatch.setattr(budget.secrets, "randbelow", lambda _: pytest.fail("pacing sample must not be redrawn"))
    disabled = contract.parse_policy({"budget_enabled": False, "download_interval_min_seconds": 10,
                                     "download_interval_max_seconds": 10})
    recovered = reserve(profile, waiting_spec, media_attempt, disabled)
    assert dispatch(profile, recovered, disabled).won
    details = query(profile, "SELECT wait_details_json FROM budget_operation WHERE operation_id=?",
                    (recovered.operation_id,))[0]
    assert json.loads(details["wait_details_json"])["download_interval_seconds"] == 10


@pytest.mark.parametrize("field", ["first_dispatch_json", "current_reservation_json"])
def test_postdispatch_snapshot_tampering_cannot_reuse_paid_operation(profile, field):
    first = reserve(profile)
    dispatch(profile, first)
    row = query(profile, "SELECT * FROM budget_attempt")[0]
    value = json.loads(row[field])
    if field == "first_dispatch_json":
        value["ordinal"] = 2
        sha_field = "first_dispatch_sha256"
    else:
        value["admitted_at"] = "2026-10-01T11:00:00.000000Z"
        sha_field = "current_reservation_sha256"
    raw = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    with sqlite3.connect(profile.database) as con:
        con.execute(f"UPDATE budget_attempt SET {field}=?,{sha_field}=? WHERE attempt_id=?",
                    (raw, hashlib.sha256(raw.encode("utf-8")).hexdigest(), first.attempt_id))
    with pytest.raises(budget.BudgetLedgerError):
        budget.get_result(first.attempt_id, REQUEST, profile=profile)


def test_sql_cleanup_failure_does_not_replace_primary_conflict(profile, monkeypatch):
    from reno import budget_writer_sqlite, budget_writer_witness

    first = reserve(profile)
    cleanup_calls = []
    original_rollback = budget_writer_sqlite._BudgetConnection.rollback

    def rollback(con):
        original_rollback(con)
        cleanup_calls.append("rollback")
        raise RuntimeError("secondary rollback failure")

    def close(con):
        con.close()
        cleanup_calls.append("close")
        raise RuntimeError("secondary close failure")

    # Fault the real factory's original connection, not a borrowed DTO wrapper.
    monkeypatch.setattr(budget_writer_sqlite._BudgetConnection, "rollback", rollback)
    monkeypatch.setattr(budget_writer_witness, "_close_connection", close)
    with pytest.raises(budget.BudgetLedgerError) as caught:
        budget.mark_dispatching(first.attempt_id, "0" * 64, str(uuid.uuid4()), expected_revision=first.revision,
                                profile=profile, policy=policy(), now=NOW)
    assert caught.value.code == "BUDGET_RESERVATION_CONFLICT"
    assert cleanup_calls == ["rollback", "close"]


def test_ledger_rejects_callers_write_transaction_before_opening_own_connection(profile, monkeypatch):
    business = db.connect(profile.database)
    business.execute("BEGIN IMMEDIATE")
    called = []
    original = budget.db.connect
    monkeypatch.setattr(budget.db, "connect", lambda *args, **kwargs: called.append(True) or original(*args, **kwargs))
    try:
        with pytest.raises(budget.BudgetLedgerError):
            budget.reserve(operation(), attempt(), profile=profile, policy=policy(), now=NOW, business_connection=business)
        assert called == [] and business.in_transaction
    finally:
        business.rollback()
        business.close()


_CHILD = r"""
import json, sys, uuid
from datetime import datetime
from reno import budget, budget_contract as contract, storage_lifecycle
args = json.loads(sys.argv[1])
profile = storage_lifecycle.StorageProfile(**args['profile'])
spec = budget.OperationSpec(args['continuation'], 1, 'model', 'race:0', args['input'])
request = budget.AttemptSpec(args['request'], adapter='paas_v4', effective_max_tokens=100)
now = datetime.fromisoformat(args['now'])
policy = contract.parse_policy({'budget_enabled': True, 'model_daily_attempts': 1})
sys.stdin.readline()
try:
    if args.get('attempt_id'):
        result = budget.mark_dispatching(args['attempt_id'], args['reservation_sha256'], str(uuid.uuid4()),
            expected_revision=args['expected_revision'],
            profile=profile, policy=policy, now=now)
        print(json.dumps({'won': result.won}), flush=True)
    else:
        result = budget.reserve(spec, request, profile=profile, policy=policy, now=now)
        print(json.dumps({'reserved': True}), flush=True)
except contract.BudgetDeferred as exc:
    print(json.dumps({'reason': exc.reason}), flush=True)
"""


def run_pair(profile, **extra):
    children = []
    try:
        for _ in range(2):
            args = {"profile": {name: str(getattr(profile, name)) for name in
                ("database", "media", "reports", "checkpoints")}, "continuation": str(uuid.uuid4()),
                "input": INPUT, "request": REQUEST, "now": NOW.isoformat(), **extra}
            children.append(subprocess.Popen([sys.executable, "-B", "-u", "-c", _CHILD, json.dumps(args)],
                cwd=Path(__file__).resolve().parents[1], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0))
        for child in children:
            child.stdin.write(b"go\n")
            child.stdin.flush()
        results = []
        for child in children:
            out, err = child.communicate(timeout=15)
            assert child.returncode == 0, err.decode("utf-8", "replace")
            results.append(json.loads(out))
        return results
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.communicate(timeout=5)


def test_two_processes_compete_for_last_daily_admission(profile):
    results = run_pair(profile)
    assert sum(result.get("reserved", False) for result in results) == 1
    assert sum(result.get("reason") == "daily_limit" for result in results) == 1
    assert len(query(profile, "SELECT * FROM budget_attempt")) == 1


def test_two_processes_compete_for_one_dispatch_right(profile):
    reserved = reserve(profile)
    results = run_pair(profile, attempt_id=reserved.attempt_id, reservation_sha256=reserved.reservation_sha256,
                       expected_revision=reserved.revision)
    assert sum(result["won"] for result in results) == 1
    assert query(profile, "SELECT * FROM budget_attempt")[0]["state"] == "dispatching"
