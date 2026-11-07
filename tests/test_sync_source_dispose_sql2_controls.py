"""Real joint Cloud SQL/ledger coverage for resume, failure and mixed proofs.

The prospective fixture owns real temporary DDL, paired sessions, immutable
registrations, effect markers and receipts. No platform or provider is called.
"""
from copy import deepcopy
from datetime import timedelta
import json

import pytest

from reno import storage_admin, storage_lifecycle as lifecycle
from reno import sync_budget_wait as budget, sync_source_control as source_control
from reno import sync_source_continuity as external, sync_source_continuity_service as service
from reno import sync_source_readmit_contract as wire, sync_source_readmit_schema2 as schema
from reno import sync_source_readmit_sql2 as sql2
from reno.sync_protocol import SyncError
from tests.test_sync_source_dispose_sql2 import env, body
from tests.test_sync_source_readmission import apply, old, request, snapshot, item, register, disposed
from tests.test_sync_source_continuity_service import NOW


def registration(value, control):
    with storage_admin.connection(value.profile) as con:
        row = con.execute("SELECT * FROM " + schema.REGISTRATION + " WHERE original_request_id=?",
            (control["request_id"],)).fetchone()
        checked = schema.validate_record(schema.REGISTRATION, row)
        assert json.loads(checked["original_request_json"]) == control
        return json.loads(checked["result_json"])


def disposition_record(value, dispose_request):
    with storage_admin.connection(value.profile) as con:
        row = con.execute("SELECT * FROM " + schema.DISPOSITION + " WHERE request_id=?",
            (dispose_request["request_id"],)).fetchone()
        checked = schema.validate_disposition_record(row)
        assert json.loads(checked["request_json"]) == dispose_request
        return checked


def current_readmit2(value, dispositions):
    result = request(value)
    result.pop("manual_resume_basis")
    result.update(format_version=2, resume_basis=None, dispositions=deepcopy(dispositions),
        current_home_epoch=result["original"]["home_epoch"], preservation=None)
    return result


def assert_original_negative_blocks_effect(value, control):
    before = snapshot(value)
    with service.writer(profile=value.profile) as con:
        with pytest.raises(SyncError) as caught:
            with service.before_effect(con, profile=value.profile, original_request=control, now=NOW):
                pytest.fail("A permanently retired original control acquired effect authority")
        assert caught.value.code == "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH"
    assert snapshot(value) == before


@pytest.mark.parametrize("applied", [False, True])
def test_resume_disposition2_keeps_the_real_sql_parent_and_never_refunds_twice(env, applied):
    defer = old(env)
    parent = apply(env, env.active, defer)
    resume = old(env, "budget_resume", parent=defer["request_id"])
    if applied:
        original_receipt = apply(env, env.active, resume)
        registered = registration(env, resume)
    else:
        original_receipt = None
        registered = register(env, env.active, resume)
    dispose_request = body(env, resume, registered)
    before_queue = item(env)
    result = sql2.dispose(profile=env.profile, request=dispose_request, now=NOW)
    assert result["original_kind"] == "budget_resume"
    assert result["disposition"] == ("applied" if applied else "not_applied")
    assert result["original_receipt"] == original_receipt
    assert item(env) == before_queue
    stored = disposition_record(env, dispose_request)
    if applied:
        assert json.loads(stored["defer_receipt_json"]) == parent
        assert stored["defer_receipt_sha256"] == wire.digest(parent)
    else:
        assert stored["defer_receipt_json"] is None
        assert stored["defer_receipt_sha256"] is None
    with storage_admin.connection(env.profile) as con:
        parent_row = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
            (defer["request_id"],)).fetchone()
        assert budget._receipt(parent_row, con=con) == (defer, parent)
        assert parent_row["request_json"] == wire.canonical_json(defer)
        assert parent_row["request_sha256"] == wire.digest(defer)
        assert parent_row["result_json"] == wire.canonical_json(parent)
        assert parent_row["result_sha256"] == wire.digest(parent)
        child_row = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
            (resume["request_id"],)).fetchone()
        if applied:
            assert budget._receipt(child_row, con=con) == (resume, original_receipt)
        else:
            assert child_row is None
    before = snapshot(env)
    replay = sql2.dispose(profile=env.profile, request=dispose_request, now=NOW + timedelta(days=40000))
    assert replay == {**result, "replayed": True}
    assert snapshot(env) == before
    if not applied:
        assert before_queue["status"] == "budget_wait"
        assert_original_negative_blocks_effect(env, resume)


def test_source_failure_applied2_reads_real_terminal_sql_and_cannot_be_hidden_from_readmission(env):
    control = old(env, "source_fail")
    terminal = apply(env, env.active, control)
    registered = registration(env, control)
    before_queue = item(env)
    assert before_queue["status"] == terminal["status"] == "dead"
    dispose_request = body(env, control, registered)
    result = sql2.dispose(profile=env.profile, request=dispose_request, now=NOW)
    assert result["format_version"] == 2 and result["disposition"] == "applied"
    assert result["original_kind"] == "source_fail" and result["original_receipt"] == terminal
    assert item(env) == before_queue
    with storage_admin.connection(env.profile) as con:
        stored = con.execute("SELECT * FROM sync_queue_source_receipt WHERE request_id=?",
            (control["request_id"],)).fetchone()
        assert source_control._receipt(stored) == (control, terminal)
    assert json.loads(disposition_record(env, dispose_request)["result_json"]) == result
    before = snapshot(env)
    assert sql2.dispose(profile=env.profile, request=dispose_request, now=NOW) == {**result, "replayed": True}
    # Omission of the positive disposition cannot hide the actual SQL terminal.
    with pytest.raises(SyncError) as denied:
        sql2.readmit(profile=env.profile, request=current_readmit2(env, []), now=NOW)
    assert denied.value.code == "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH"
    assert snapshot(env) == before


def test_source_failure_negative2_is_real_permanent_retirement_before_one_fresh_claim(env):
    control = old(env, "source_fail")
    registered = register(env, env.active, control)
    before_queue = item(env)
    dispose_request = body(env, control, registered)
    result = sql2.dispose(profile=env.profile, request=dispose_request, now=NOW)
    assert result["original_kind"] == "source_fail" and result["disposition"] == "not_applied"
    assert result["original_receipt"] is None and item(env) == before_queue
    with lifecycle.shared(env.profile), external.locked(env.profile) as owner:
        entries = owner.observe()["entries"]
        assert entries["registrations"][control["request_id"]]["record"] == registered
        assert entries["tombstones"][control["request_id"]]["record"] == result
        assert control["request_id"] not in entries["effects"]
    with storage_admin.connection(env.profile) as con:
        assert con.execute("SELECT 1 FROM sync_queue_source_receipt WHERE request_id=?",
            (control["request_id"],)).fetchone() is None
    assert_original_negative_blocks_effect(env, control)
    admission_body = current_readmit2(env, [result])
    admitted = sql2.readmit(profile=env.profile, request=admission_body, now=NOW)
    assert admitted["attempts"] == before_queue["attempts"]
    assert admitted["claim"]["lease_generation"] == before_queue["lease_generation"] + 1
    assert admitted["attempt_basis"] == "held_claim"
    assert_original_negative_blocks_effect(env, control)


@pytest.mark.parametrize("legacy_kind", ["budget_defer", "source_fail"])
def test_readmit2_consumes_actual_mixed_disposition1_and2_without_relabelling_either(env, legacy_kind):
    first = old(env, legacy_kind)
    second = old(env, "source_fail" if legacy_kind == "budget_defer" else "budget_defer")
    first_registration = register(env, env.active, first)
    second_registration = register(env, env.active, second)
    baseline = request(env)
    legacy_result = disposed(env, baseline, first, first_registration)
    second_request = body(env, second, second_registration)
    second_request["current_home_epoch"] = baseline["current_home_epoch"]
    second_request["preservation"] = deepcopy(baseline["preservation"])
    current_result = sql2.dispose(profile=env.profile, request=second_request, now=NOW)
    assert legacy_result["format_version"] == 1 and current_result["format_version"] == 2
    assert legacy_result["disposition"] == current_result["disposition"] == "not_applied"
    with lifecycle.shared(env.profile), external.locked(env.profile) as owner:
        entries = owner.observe()["entries"]
        assert entries["tombstones"][first["request_id"]]["record"] == legacy_result
        assert entries["tombstones"][second["request_id"]]["record"] == current_result
    with storage_admin.connection(env.profile) as con:
        versions = [row[0] for row in con.execute("SELECT format_version FROM " + schema.DISPOSITION
            + " ORDER BY format_version")]
        assert versions == [1, 2]
    admission_body = deepcopy(baseline)
    admission_body.pop("manual_resume_basis")
    admission_body.update(format_version=2, resume_basis=None,
        dispositions=[deepcopy(legacy_result), deepcopy(current_result)])
    before_queue = item(env)
    before = snapshot(env)
    forged = deepcopy(admission_body)
    forged["dispositions"][0]["format_version"] = 2
    with pytest.raises(SyncError) as denied:
        sql2.readmit(profile=env.profile, request=forged, now=NOW)
    assert denied.value.code == "SYNC_SOURCE_READMIT_DISPOSITION_REQUIRED"
    assert snapshot(env) == before
    admitted = sql2.readmit(profile=env.profile, request=admission_body, now=NOW)
    assert admitted["format_version"] == 2 and admitted["attempt_basis"] == "held_claim"
    assert admitted["attempts"] == before_queue["attempts"]
    assert admitted["disposition_sha256s"] == [wire.disposition_sha256(legacy_result),
        wire.disposition_sha256(current_result)]
    with storage_admin.connection(env.profile) as con:
        record = schema.validate_admission_record(con.execute("SELECT * FROM " + schema.ADMISSION
            + " WHERE admission_id=?", (admitted["admission_id"],)).fetchone())
        assert json.loads(record["request_json"]) == admission_body
        assert json.loads(record["result_json"]) == admitted
    after = snapshot(env)
    for table in before:
        if table not in {"sync_queue", schema.ADMISSION}:
            assert after[table] == before[table], table
    replay_before = snapshot(env)
    assert sql2.readmit(profile=env.profile, request=admission_body, now=NOW) == {**admitted, "replayed": True}
    assert snapshot(env) == replay_before
