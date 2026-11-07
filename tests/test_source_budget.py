"""Source opportunity boundaries use artificial data and a synthetic runner."""
import json
import hashlib
import threading
from types import SimpleNamespace
from pathlib import Path
from contextlib import contextmanager
import uuid

import pytest

from reno import budget, config, db, source_budget, source_contract, storage_admin, tasks, worker
from reno.budget import BudgetLedgerError
from reno.budget_contract import BudgetDeferred


URL = "https://synthetic.invalid/video/one"


def SyntheticPlan():
    return source_contract.SourcePlan("source_metadata", URL)


@pytest.fixture
def source_environment(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    config.set_local({"budget_enabled": True})
    db.initialize()
    profile = storage_admin.profile()
    storage_admin.initialize(profile)
    with storage_admin.connection(profile) as con:
        job_id = tasks.enqueue(con, kind="ingest", payload={"target": URL}, profile=profile)
        con.commit()
        job = dict(con.execute("SELECT * FROM job WHERE job_id=?", (job_id,)).fetchone())
    calls = []
    def run(prepared, **kwargs):
        calls.append(prepared)
        body = source_contract.canonical_json({"id": "one", "title": "Synthetic source"})
        return source_contract.SourceProcessResult(prepared.request_sha256, True, True, 0, "exited",
            stdout_bytes=len(body.encode()), stdout_sha256=hashlib.sha256(body.encode()).hexdigest(), metadata_json=body)
    def verify(artifact, *, profile):
        path = profile.media / artifact.relpath
        try:
            stat = path.stat()
            if (path.is_symlink() or stat.st_nlink != 1 or (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns) !=
                    (artifact.device, artifact.inode, artifact.size_bytes, artifact.mtime_ns)
                    or hashlib.sha256(path.read_bytes()).hexdigest() != artifact.sha256):
                raise ValueError
        except (ValueError, OSError):
            raise source_contract.SourceContractError("SOURCE_ARTIFACT_INVALID") from None
        return path
    runner = SimpleNamespace(prepare=lambda plan, **kwargs: plan, run=run, verify_artifact=verify)
    monkeypatch.setattr(source_budget, "_components", lambda: (source_contract, runner))
    monkeypatch.setattr(worker, "check_admission", lambda **kwargs: None)
    with worker._execution(job, str(uuid.uuid4()), threading.Event(), profile=profile):
        yield profile, calls, runner


def synthetic_scope(profile, **kwargs):
    job = worker.execution_context()
    return source_budget.SourceScope(profile, job["continuation_id"], job["execution_generation"], job["job_id"], **kwargs)


def test_enabled_missing_scope_blocks_before_any_source_runner(source_environment):
    _, calls, _ = source_environment
    with pytest.raises(BudgetDeferred, match="unsupported_capability"):
        source_budget.run_source(SyntheticPlan())
    assert calls == []


def test_paid_source_reentry_never_spawns_second_process(source_environment):
    profile, calls, _ = source_environment
    with source_budget.scope(synthetic_scope(profile)):
        first = source_budget.run_source(SyntheticPlan())
        second = source_budget.run_source(SyntheticPlan())
        assert first.metadata_json == second.metadata_json
    assert len(calls) == 1


def test_unknown_launch_is_persisted_and_never_retried(source_environment):
    profile, calls, runner = source_environment
    def timeout(prepared, **kwargs):
        calls.append(prepared)
        raise TimeoutError("synthetic private launch diagnostic")
    runner.run = timeout
    with source_budget.scope(synthetic_scope(profile)):
        for _ in range(2):
            with pytest.raises(BudgetDeferred, match="indeterminate"):
                source_budget.run_source(SyntheticPlan())
    assert len(calls) == 1


def test_source_opportunity_rejects_business_writer_without_touching_it(source_environment):
    profile, calls, _ = source_environment
    con = db.connect(profile.database)
    try:
        con.execute("BEGIN IMMEDIATE")
        con.execute("INSERT INTO meta(key,value) VALUES('caller-pending','preserve')")
        with source_budget.scope(synthetic_scope(profile, business_connection=con)):
            with pytest.raises(BudgetLedgerError, match="BUDGET_BUSINESS_TRANSACTION_ACTIVE"):
                source_budget.run_source(SyntheticPlan())
        assert con.in_transaction and con.execute("SELECT value FROM meta WHERE key='caller-pending'").fetchone()[0] == "preserve"
    finally:
        con.rollback()
        con.close()
    assert calls == []


def success(plan, profile, *, metadata=None, items=()):
    if plan.kind == "source_metadata":
        body = source_contract.canonical_json(metadata or {"id": "one", "title": "Synthetic source"})
        return source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited",
            stdout_bytes=len(body.encode()), stdout_sha256=hashlib.sha256(body.encode()).hexdigest(), metadata_json=body)
    if plan.kind == "source_enumeration":
        return source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited", items=tuple(items))
    path = profile.media / plan.artifact_relpath
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"synthetic paid media " + plan.segment.encode())
    stat = path.stat()
    artifact = source_contract.SourceArtifact(plan.segment, plan.artifact_relpath, stat.st_size,
        hashlib.sha256(path.read_bytes()).hexdigest(), stat.st_dev, stat.st_ino, stat.st_mtime_ns)
    return source_contract.SourceProcessResult(plan.request_sha256, True, True, 0, "exited", artifact=artifact)


def failure(plan, *, code=1, classification="process_failure"):
    return source_contract.SourceProcessResult(plan.request_sha256, True, True, code, "exited", failure_class=classification)


def table(profile, name):
    with storage_admin.connection(profile) as con:
        return [dict(row) for row in con.execute("SELECT * FROM " + name)]


def test_paid_lookup_precedes_fresh_quota_pause_extras_and_cookies(source_environment, monkeypatch):
    profile, calls, runner = source_environment
    with source_budget.scope(synthetic_scope(profile)):
        first = source_budget.metadata(URL)
        config.set_local({"model_operation_max_attempts": 1, "ytdlp_extra_args": ["--exec", "synthetic"],
                          "ytdlp_cookies": "synthetic-missing-cookie-file"})
        monkeypatch.setattr(worker, "check_admission", lambda **kwargs: pytest.fail("paid result must precede new admission"))
        runner.prepare = lambda *args, **kwargs: pytest.fail("paid result must precede current capability checks")
        assert source_budget.metadata(URL) == first
    assert len(calls) == 1


def test_disabled_without_history_legacy_and_enable_during_gate_are_distinct(source_environment, monkeypatch):
    profile, calls, _ = source_environment
    config.set_local({"budget_enabled": False})
    with source_budget.scope(synthetic_scope(profile)):
        assert source_budget.metadata(URL, legacy=lambda: {"legacy": True}) == {"legacy": True}
    assert calls == [] and table(profile, "budget_operation") == []
    def enable(**kwargs):
        config.set_local({"budget_enabled": True})
    monkeypatch.setattr(worker, "check_admission", enable)
    with source_budget.scope(synthetic_scope(profile)):
        assert source_budget.metadata(URL, legacy=lambda: pytest.fail("fresh enabled policy cannot use raw legacy"))["id"] == "one"
    assert len(calls) == 1


def test_callback_window_cannot_bypass_enable_before_actual_raw_dispatch(source_environment):
    profile, calls, _ = source_environment
    config.set_local({"budget_enabled": False})
    def legacy():
        config.set_local({"budget_enabled": True})
        source_budget.ensure_legacy_dispatch()
        pytest.fail("callback window cannot exempt new enabled policy")
    with source_budget.scope(synthetic_scope(profile)), pytest.raises(BudgetLedgerError):
        source_budget.metadata(URL, legacy=legacy)
    assert calls == [] and table(profile, "budget_operation") == []


def test_metadata_paid_then_disabled_media_new_operation_stays_tracked(source_environment):
    profile, calls, runner = source_environment
    def run(plan, **kwargs):
        calls.append(plan)
        return success(plan, profile)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        config.set_local({"budget_enabled": False})
        path = source_budget.media(URL, info, "one", "video", legacy=lambda: pytest.fail("tracked execution cannot switch raw"))
        assert path.is_file()
    assert [plan.kind for plan in calls] == ["source_metadata", "download"]
    assert len(table(profile, "budget_operation")) == 2


def test_one_refresh_keeps_genesis_media_operation_and_paid_query_reuses_current_metadata(source_environment):
    profile, calls, runner = source_environment
    failed = [False]
    def run(plan, **kwargs):
        calls.append(plan)
        if plan.kind == "source_metadata":
            return success(plan, profile, metadata={"id": "one", "title": "Current" if plan.phase == "refresh" else "Genesis"})
        if plan.segment == "video" and not failed[0]:
            failed[0] = True
            return failure(plan)
        return success(plan, profile)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        original = source_budget.metadata(URL)
        video = source_budget.media(URL, original, "one", "video")
        current = source_budget.metadata(URL)
        assert current["title"] == "Current"
        assert source_budget.media(URL, current, "one", "video") == video
        assert source_budget.media(URL, current, "one", "audio").is_file()
    assert [plan.kind for plan in calls] == ["source_metadata", "download", "source_metadata", "download", "download"]
    assert [plan.phase for plan in calls if plan.kind == "source_metadata"] == ["initial", "refresh"]
    media_operations = [row for row in table(profile, "budget_operation") if row["kind"] == "download"]
    assert len(media_operations) == 1
    attempts = table(profile, "budget_attempt")
    assert [row["outcome"] for row in attempts] == ["source_step_complete", "source_retry", "full_result", "source_step_complete", "full_result"]
    assert len({row["operation_id"] for row in attempts if row["download_segment"] in ("video", "audio")}) == 1


def test_forged_low_level_refresh_has_no_new_runner_or_operation(source_environment):
    profile, calls, _ = source_environment
    with source_budget.scope(synthetic_scope(profile)):
        source_budget.metadata(URL)
        forged = source_contract.SourcePlan("source_metadata", URL, phase="refresh",
            refresh_attempt_id=str(uuid.uuid4()), refresh_settlement_sha256="e" * 64)
        with pytest.raises(BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
            source_budget.run_source(forged)
    assert len(calls) == 1 and len(table(profile, "budget_attempt")) == 1


@pytest.mark.parametrize("code,classification,fallback", [(1, "process_failure", True), (0, "artifact_invalid", False)])
def test_audio_fallback_requires_complete_normal_nonzero_failure(source_environment, code, classification, fallback):
    profile, calls, runner = source_environment
    def run(plan, **kwargs):
        calls.append(plan)
        return failure(plan, code=code, classification=classification) if plan.segment == "audio" else success(plan, profile)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        source_budget.media(URL, info, "one", "video")
        for _ in range(2):
            if fallback:
                assert source_budget.media(URL, info, "one", "audio") is None
            else:
                with pytest.raises(source_budget.SourceProcessError):
                    source_budget.media(URL, info, "one", "audio")
    assert [plan.segment for plan in calls].count("audio") == 1


def test_paid_file_replacement_cannot_be_bypassed_by_new_registered_database_proof(source_environment):
    profile, calls, runner = source_environment
    runner.run = lambda plan, **kwargs: (calls.append(plan) or success(plan, profile))
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        path = source_budget.media(URL, info, "one", "video")
        path.write_bytes(b"a different synthetic file")
        with storage_admin.connection(profile) as con:
            db.upsert_asset(con, {"video_id": "one", "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                "source_url": URL, "files": {"video": path.relative_to(profile.media.parent).as_posix()}}, profile=profile)
            con.commit()
        with pytest.raises(source_contract.SourceContractError):
            source_budget.media(URL, info, "one", "video")
    assert len(calls) == 2


def test_normal_complete_oversized_projection_persists_bounded_failure_without_retry(source_environment):
    profile, calls, runner = source_environment
    plan = source_contract.SourcePlan("source_enumeration", URL)
    items = tuple((str(index), "x" * 999) for index in range(3000))
    def run(prepared, **kwargs):
        calls.append(prepared)
        return success(prepared, profile, items=items)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        with pytest.raises(BudgetDeferred, match="result_capacity"):
            source_budget.run_source(plan)
        again = source_budget.run_source(plan)
        assert not again.successful and again.failure_class == "invalid_output"
    attempts = table(profile, "budget_attempt")
    assert len(calls) == 1 and len(attempts) == 1
    assert attempts[0]["response_complete"] == 1 and attempts[0]["outcome"] == "terminal_error"


@pytest.mark.parametrize("kind", ["source_metadata", "source_enumeration"])
def test_normal_failure_stage_cap_is_four_across_reentry(source_environment, kind):
    profile, calls, runner = source_environment
    runner.run = lambda plan, **kwargs: (calls.append(plan) or failure(plan))
    plan = source_contract.SourcePlan(kind, URL)
    with source_budget.scope(synthetic_scope(profile)):
        for _ in range(2):
            with pytest.raises(source_budget.SourceProcessError):
                source_budget._bounded(plan)
    assert len(calls) == 4
    assert [row["outcome"] for row in table(profile, "budget_attempt")] == ["source_retry"] * 3 + ["terminal_error"]


def test_video_total_cap_four_and_refresh_single_failure_never_authorizes_new_uuid(source_environment):
    profile, calls, runner = source_environment
    def run(plan, **kwargs):
        calls.append(plan)
        return success(plan, profile) if plan.kind == "source_metadata" else failure(plan)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        for _ in range(2):
            with pytest.raises(source_budget.SourceProcessError):
                source_budget.media(URL, info, "one", "video")
    assert [plan.kind for plan in calls].count("source_metadata") == 2
    assert [plan.segment for plan in calls].count("video") == 4
    assert len({row["continuation_id"] for row in table(profile, "budget_operation")}) == 1
    assert len([row for row in table(profile, "budget_operation") if row["kind"] == "download"]) == 1


def test_refresh_normal_failure_is_one_paid_opportunity_and_never_retried(source_environment):
    profile, calls, runner = source_environment
    def run(plan, **kwargs):
        calls.append(plan)
        return success(plan, profile) if plan.kind == "source_metadata" and plan.phase == "initial" else failure(plan)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        for _ in range(2):
            with pytest.raises(source_budget.SourceProcessError):
                source_budget.media(URL, info, "one", "video")
    assert [plan.phase for plan in calls if plan.kind == "source_metadata"] == ["initial", "refresh"]
    assert len(calls) == 3


def test_actual_runner_uses_first_dispatch_policy_after_current_rate_changes(source_environment, monkeypatch):
    profile, calls, runner = source_environment
    config.set_local({"download_rate_bytes_per_second": 1000})
    policies = []
    def prepare(plan, **kwargs):
        policies.append(kwargs["policy"])
        return plan
    runner.prepare = prepare
    original = budget.get_dispatch_policy
    def changed(attempt_id, request_sha256, **kwargs):
        config.set_local({"download_rate_bytes_per_second": 9999})
        return original(attempt_id, request_sha256, **kwargs)
    monkeypatch.setattr(budget, "get_dispatch_policy", changed)
    with source_budget.scope(synthetic_scope(profile)):
        source_budget.metadata(URL)
    assert policies[-1].download_rate_bytes_per_second == 1000
    assert config.budget_settings()["download_rate_bytes_per_second"] == 9999 and len(calls) == 1


def test_supplied_writer_gate_is_read_only_and_transaction_is_unchanged(source_environment, monkeypatch):
    profile, _, _ = source_environment
    seen = []
    def read_only(**kwargs):
        con = kwargs.get("connection")
        if con is not None:
            before = con.total_changes
            assert con.in_transaction
            con.execute("SELECT 1").fetchone()
            assert con.total_changes == before
            seen.append(True)
    monkeypatch.setattr(worker, "check_admission", read_only)
    with source_budget.scope(synthetic_scope(profile)):
        source_budget.metadata(URL)
    assert seen == [True, True]


def test_two_reserved_callers_have_one_cas_winner_and_one_runner(source_environment, monkeypatch):
    profile, calls, _ = source_environment
    job = worker.execution_context()
    original = budget.mark_dispatching
    ready = threading.Barrier(2, timeout=10)
    def synchronized(*args, **kwargs):
        ready.wait()
        return original(*args, **kwargs)
    monkeypatch.setattr(budget, "mark_dispatching", synchronized)
    outcomes = []
    def execute():
        with worker._execution(job, job["owner_id"], threading.Event(), profile=profile):
            try:
                with source_budget.scope(synthetic_scope(profile)):
                    outcomes.append(source_budget.metadata(URL))
            except BudgetDeferred as waiting:
                outcomes.append(waiting.reason)
            except Exception as error:
                outcomes.append(type(error).__name__)
    first, second = threading.Thread(target=execute), threading.Thread(target=execute)
    first.start()
    second.start()
    first.join(15)
    second.join(15)
    assert not first.is_alive() and not second.is_alive()
    assert len(calls) == 1 and len(outcomes) == 2
    assert all(type(value) is dict or value == "indeterminate" for value in outcomes)


def test_known_registered_source_file_is_reused_without_new_media_process(source_environment):
    profile, calls, _ = source_environment
    path = profile.media / "originals/one/registered.mp4"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"previously registered exact synthetic source")
    with storage_admin.connection(profile) as con:
        db.upsert_asset(con, {"video_id": "one", "source_url": URL,
            "content_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "files": {"video": path.relative_to(profile.media.parent).as_posix()}}, profile=profile)
        con.commit()
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        assert source_budget.media(URL, info, "one", "video") == path
    assert len(calls) == 1 and len(table(profile, "budget_operation")) == 1


@pytest.mark.parametrize("entry", ["metadata", "media", "low_level"])
def test_paid_refresh_changed_video_id_is_retained_but_cannot_create_new_media_operation(source_environment, entry):
    profile, calls, runner = source_environment
    def run(plan, **kwargs):
        calls.append(plan)
        return success(plan, profile) if plan.kind == "source_metadata" else failure(plan)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        original = source_budget.metadata(URL)
        genesis_json = source_contract.canonical_json(original)
        original_path = f"originals/one/source-{hashlib.sha256(genesis_json.encode()).hexdigest()}/video.mp4"
        video_plan = source_contract.SourcePlan("download", URL, segment="video", info_json=genesis_json,
            artifact_relpath=original_path)
        failed = source_budget.run_source(video_plan)
        assert not failed.successful
        refresh = source_budget._refresh_plan(URL, source_budget._genesis(URL))
        # Simulate a previous crash immediately after a valid complete refresh
        # settlement: the private paid body really says a different video ID.
        operation = source_budget._operation(refresh)
        view = source_budget._view(operation)
        reserved = budget.reserve(operation, budget.AttemptSpec(refresh.request_sha256, view.latest_ordinal + 1,
            source_contract_version=1), profile=profile)
        dispatched = budget.mark_dispatching(reserved.attempt_id, reserved.reservation_sha256, str(uuid.uuid4()),
            expected_revision=reserved.revision, profile=profile)
        assert dispatched.won
        wrong = success(refresh, profile, metadata={"id": "two", "title": "Changed ID"})
        text = source_contract.encode_envelope(refresh, wrong, input_sha256=operation.input_sha256)
        budget.settle(reserved.attempt_id, refresh.request_sha256, profile=profile, outcome="full_result", result_text=text)
        runner.run = lambda plan, **kwargs: (calls.append(plan) or success(plan, profile))
        with pytest.raises(BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
            if entry == "metadata":
                source_budget.metadata(URL)
            elif entry == "media":
                source_budget.media(URL, original, "one", "video")
            else:
                wrong_json = wrong.metadata_json
                plan = source_contract.SourcePlan("download", URL, segment="video", info_json=wrong_json,
                    artifact_relpath=f"originals/one/source-{hashlib.sha256(wrong_json.encode()).hexdigest()}/video.mp4",
                    phase="refresh", refresh_attempt_id=refresh.refresh_attempt_id,
                    refresh_settlement_sha256=refresh.refresh_settlement_sha256)
                source_budget.run_source(plan, input_sha256=source_budget._operation(video_plan).input_sha256)
        saved = budget.get_result(reserved.attempt_id, refresh.request_sha256, profile=profile)
        assert saved.text == text and saved.response_complete
    assert len(calls) == 2
    operations = [row for row in table(profile, "budget_operation") if row["kind"] == "download"]
    assert len(operations) == 1 and operations[0]["suboperation_key"] == "ingest:media:v1:one"


def test_fresh_changed_id_refresh_is_settled_before_rejection_and_never_relaunched(source_environment):
    profile, calls, runner = source_environment
    def run(plan, **kwargs):
        calls.append(plan)
        if plan.kind == "source_metadata":
            return success(plan, profile, metadata={"id": "two" if plan.phase == "refresh" else "one", "title": "Synthetic"})
        return failure(plan)
    runner.run = run
    with source_budget.scope(synthetic_scope(profile)):
        info = source_budget.metadata(URL)
        for _ in range(2):
            with pytest.raises(BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
                source_budget.media(URL, info, "one", "video")
        with pytest.raises(BudgetLedgerError, match="BUDGET_REQUEST_CONFLICT"):
            source_budget.metadata(URL)
    operations = table(profile, "budget_operation")
    metadata_id = next(row["operation_id"] for row in operations if row["kind"] == "source_metadata")
    attempts = [row for row in table(profile, "budget_attempt") if row["operation_id"] == metadata_id]
    assert len(attempts) == 2 and attempts[-1]["outcome"] == "full_result" and attempts[-1]["response_complete"] == 1
    envelope = json.loads(attempts[-1]["result_text"])
    assert json.loads(envelope["result"]["metadata_json"])["id"] == "two"
    assert len([row for row in operations if row["kind"] == "download"]) == 1 and len(calls) == 3


def test_full_preflight_file_verification_is_outside_sql_and_writer_uses_sealed_fast_witness(source_environment, monkeypatch):
    profile, _, runner = source_environment
    original = budget._connection
    inside_writer = [False]
    checks = []
    seal = object()
    @contextmanager
    def traced(*args, **kwargs):
        with original(*args, **kwargs) as con:
            previous = inside_writer[0]
            inside_writer[0] = kwargs.get("write", False)
            try:
                yield con
            finally:
                inside_writer[0] = previous
    def verify(prepared):
        assert not inside_writer[0]
        checks.append("full-outside-writer")
        return seal
    def prepare(plan, **kwargs):
        if inside_writer[0]:
            assert kwargs["verified_files"] is seal
            checks.append("fast-under-writer")
        return plan
    runner.verify_prepared_files = verify
    runner.prepare = prepare
    monkeypatch.setattr(budget, "_connection", traced)
    with source_budget.scope(synthetic_scope(profile)):
        source_budget.metadata(URL)
    assert checks == ["full-outside-writer", "fast-under-writer", "fast-under-writer"]
