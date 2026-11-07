"""Actual targeted SQL effects with the explicitly prospective17 fixture.

The shared fixture uses real DDL, a real fingerprint getter and real files.
Its synthetic future capability is not a production schema17 migration.
"""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
import sqlite3
import uuid

import pytest

from reno import migrations, storage_admin
from reno import sync_budget_wait as budget, sync_queue as queue
from reno import sync_source_control as source_control
from reno import sync_source_continuity_service as service
from reno import sync_source_readmission as readmission
from reno import sync_source_readmit_contract as wire, sync_source_readmit_schema as schema
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract as sample
from tests.test_sync_source_continuity_service import env, activated, bound, NOW, STAMP, URL
from tests.test_sync_queue import publication


def request(env):
    value = bound(env, sample.readmit())
    value["original"]["claim"] = deepcopy(env.fresh_claim)
    value["expected_claim"] = deepcopy(env.fresh_claim)
    value["original"]["submitted_url_sha256"] = hashlib.sha256(URL.encode()).hexdigest()
    return value


def snapshot(env):
    with storage_admin.connection(env.profile) as con:
        names = [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")]
        return {name: [tuple(row) for row in con.execute('SELECT * FROM "' + name.replace('"', '""') + '" ORDER BY 1')]
                for name in names}


def item(env, item_id=7):
    with storage_admin.connection(env.profile) as con:
        return dict(con.execute("SELECT * FROM sync_queue WHERE item_id=?", (item_id,)).fetchone())


def change_control(con):
    con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,updated_at) "
                "VALUES(1,'running',0,1,?) ON CONFLICT(slot) DO UPDATE SET revision=revision+1", (STAMP,))
    assert con.execute("SELECT revision FROM sync_recovery_control WHERE slot=1").fetchone()[0] == 1


def register(env, active, original):
    value = bound(env, sample.register(original))
    value["original_request"] = deepcopy(original)
    value["continuity"] = {"format_version": 1, "continuity_id": active["continuity_id"],
        "contract_sha256": service.external._digest(active), "target_epoch": active["target_epoch"]}
    return service.register(profile=env.profile, request=value, now=NOW)


def old(env, kind="budget_defer", *, claim=None, parent=None):
    value = bound(env, sample.old_request(kind))
    if kind == "budget_resume":
        value["defer_request_id"] = parent
    else:
        value["claim"] = deepcopy(claim or env.fresh_claim)
    return value


def apply(env, active, original):
    register(env, active, original)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW) as permit:
            assert permit.receipt is None
            function = source_control.apply_request if original["kind"] == "source_fail" else budget.apply_request
            return function(con, original, now=NOW)


def disposed(env, body, original, registered):
    value = {key: deepcopy(body[key]) for key in wire._BOUND}
    value.update(kind="source_dispose", request_id=str(uuid.uuid4()),
                 original_request=deepcopy(original), registration=deepcopy(registered))
    return service.dispose(profile=env.profile, request=value, now=NOW)


def test_targeted_held_transfer_preserves_attempts_and_every_other_item_and_table(env):
    activated(env)
    with storage_admin.connection(env.profile) as con:
        con.execute("INSERT INTO sync_queue(item_id,url,status,attempts,owner_id,owner_generation,lease_token,lease_generation,lease_until,created_at,updated_at) "
            "VALUES(8,?,'claimed',5,?,?,?,?,?,?,?)", (URL, sample.LEASE["session_id"], sample.LEASE["generation"],
                str(uuid.uuid4()), 9, "2000-01-01T00:00:00.000Z", STAMP, STAMP))
        con.commit()
    before = snapshot(env)
    other = item(env, 8)
    body = request(env)
    result = readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert result == wire.normalize_readmit_result(body, result)
    assert result["attempts"] == 3 and result["attempt_basis"] == "held_claim"
    assert result["claim"]["lease_generation"] == env.fresh_claim["lease_generation"] + 1
    assert result["claim"]["lease_token"] != env.fresh_claim["lease_token"]
    assert item(env, 8) == other
    after = snapshot(env)
    for table in before:
        if table not in {"sync_queue", schema.ADMISSION}:
            assert after[table] == before[table], table


def test_expired_original_claim_transfers_only_after_current_session_is_valid(env):
    activated(env)
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE sync_queue SET lease_until='2000-01-01T00:00:00.000Z' WHERE item_id=7")
        con.commit()
    result = readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert result["attempts"] == 3 and item(env)["lease_until"] == sample.LEASE["lease_until"]


def test_global16_cannot_acquire_fresh_admission(env, monkeypatch):
    activated(env)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 16)
    with pytest.raises(SyncError, match="conditions") as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"


@pytest.mark.parametrize("kind", ["budget_defer", "source_fail"])
def test_schema17_leaf_cannot_bypass_effect_frame_with_plain_connection_or_writer(env, kind):
    activated(env)
    original = old(env, kind)
    function = source_control.apply_request if kind == "source_fail" else budget.apply_request
    before = snapshot(env)
    with storage_admin.connection(env.profile) as con:
        with pytest.raises(SyncError) as caught:
            function(con, original, now=NOW)
        assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"
    with service.writer(profile=env.profile) as con:
        with pytest.raises(SyncError) as caught:
            function(con, original, now=NOW)
        assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_REQUIRED"
    assert snapshot(env) == before


def test_exact_receipt_replay_is_not_fresh_claim_even_after_lease_control_and_row_change(env):
    activated(env)
    body = request(env)
    result = readmission.readmit(profile=env.profile, request=body, now=NOW)
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE sync_queue SET status='dead',attempts=5,lease_token=? WHERE item_id=7", (str(uuid.uuid4()),))
        change_control(con)
        con.commit()
    before = snapshot(env)
    replay = readmission.readmit(profile=env.profile, request=body, now=NOW + timedelta(days=40000))
    assert replay == {**result, "replayed": True} and snapshot(env) == before


def test_same_admission_uuid_different_body_cannot_replay(env):
    activated(env)
    body = request(env)
    readmission.readmit(profile=env.profile, request=body, now=NOW)
    body["preservation"]["frontier_sha256"] = "1" * 64
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_OPERATION_CONFLICT" and snapshot(env) == before


def next_request(body, result):
    value = deepcopy(body)
    value.update(request_id=str(uuid.uuid4()), previous_admission_id=result["admission_id"],
                 expected_claim=deepcopy(result["claim"]))
    return value


def test_previous_admission_keeps_original_root_and_prevents_fork_and_missing_previous(env):
    activated(env)
    body = request(env)
    first = readmission.readmit(profile=env.profile, request=body, now=NOW)
    second_body = next_request(body, first)
    second = readmission.readmit(profile=env.profile, request=second_body, now=NOW)
    assert second["attempts"] == first["attempts"] == 3
    assert second["claim"]["lease_generation"] == first["claim"]["lease_generation"] + 1
    before = snapshot(env)
    for bad in (next_request(body, first), {**body, "request_id": str(uuid.uuid4())}):
        with pytest.raises(SyncError):
            readmission.readmit(profile=env.profile, request=bad, now=NOW)
        assert snapshot(env) == before
    with storage_admin.connection(env.profile) as con:
        records = [json.loads(row[0]) for row in con.execute("SELECT request_json FROM " + schema.ADMISSION)]
    assert all(record["original"] == body["original"] for record in records)


def test_other_core_cannot_borrow_previous_claim(env):
    activated(env)
    body = request(env)
    first = readmission.readmit(profile=env.profile, request=body, now=NOW)
    bad = next_request(body, first)
    bad["original"]["core_sha256"] = "1" * 64
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=bad, now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_LINEAGE_MISMATCH" and snapshot(env) == before


@pytest.mark.parametrize("status", ["queued", "retry_wait", "dead", "done", "budget_wait"])
def test_unproven_queued_or_terminal_or_wait_state_does_not_grant_readmission(env, status):
    activated(env)
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE sync_queue SET status=? WHERE item_id=7", (status,))
        con.commit()
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == ("SYNC_BUDGET_DEFER_REQUIRED" if status == "budget_wait" else "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH")
    assert snapshot(env) == before


def test_ordinary_reclaim_generation_cannot_be_relabelled_as_original_admission(env):
    activated(env)
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE sync_queue SET lease_token=?,lease_generation=lease_generation+1,attempts=4 WHERE item_id=7", (str(uuid.uuid4()),))
        con.commit()
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CLAIM_MISMATCH" and snapshot(env) == before


def test_permanent_source_terminal_prevents_readmission_even_if_queue_is_rewound(env):
    active = activated(env)
    original_row = item(env)
    terminal = apply(env, active, old(env, "source_fail"))
    assert terminal["status"] == "dead"
    with storage_admin.connection(env.profile) as con:
        keys = tuple(original_row)
        con.execute("UPDATE sync_queue SET " + ",".join(key + "=?" for key in keys) + " WHERE item_id=7",
                    tuple(original_row[key] for key in keys))
        con.commit()
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH" and snapshot(env) == before


def test_permanent_publication_binding_prevents_readmission_of_still_claimed_row(env):
    activated(env)
    with storage_admin.connection(env.profile) as con:
        publication({"con": con, "source": env.source}, item={"item_id": 7, "claim": env.fresh_claim}, lease=sample.LEASE)
    assert item(env)["status"] == "claimed"
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH" and snapshot(env) == before


def test_omitted_registered_control_requires_real_negative_disposition(env):
    active = activated(env)
    body = request(env)
    original = old(env)
    registration = register(env, active, original)
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_DISPOSITION_REQUIRED" and snapshot(env) == before
    negative = disposed(env, body, original, registration)
    before = snapshot(env)
    with pytest.raises(SyncError):
        readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert snapshot(env) == before
    body["dispositions"] = [negative]
    assert readmission.readmit(profile=env.profile, request=body, now=NOW)["attempts"] == 3


def test_sql_rollback_after_effect_marker_cannot_be_omitted_to_get_fresh_claim(env):
    active = activated(env)
    original = old(env)
    register(env, active, original)
    with pytest.raises(RuntimeError, match="simulated crash"):
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                raise RuntimeError("simulated crash after marker")
    before = snapshot(env)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN" and snapshot(env) == before


def test_changing_caller_core_cannot_hide_same_item_unresolved_effect(env):
    active = activated(env)
    original = old(env)
    register(env, active, original)
    with pytest.raises(RuntimeError):
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                raise RuntimeError("simulated crash")
    body = request(env)
    body["original"]["core_sha256"] = "1" * 64
    before = snapshot(env)
    with pytest.raises(SyncError):
        readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert snapshot(env) == before


def test_changing_core_and_origin_claim_cannot_hide_existing_item_admission(env):
    activated(env)
    body = request(env)
    first = readmission.readmit(profile=env.profile, request=body, now=NOW)
    bad = deepcopy(body)
    bad["request_id"] = str(uuid.uuid4())
    bad["original"]["core_sha256"] = "1" * 64
    bad["original"]["claim"] = bad["expected_claim"] = deepcopy(first["claim"])
    before = snapshot(env)
    with pytest.raises(SyncError):
        readmission.readmit(profile=env.profile, request=bad, now=NOW)
    assert snapshot(env) == before


def refund(env, active):
    body = request(env)
    defer = old(env)
    parent = apply(env, active, defer)
    resume = old(env, "budget_resume", parent=defer["request_id"])
    result = apply(env, active, resume)
    body.update(defer_receipt=parent, resume_receipt=result,
        manual_resume_basis={"authorization_id": str(uuid.uuid4()), "authorization_sha256": "5" * 64,
                             "defer_request_id": parent["request_id"]})
    return body


def test_actual_parent_resume_restores_refunded_attempt_once_and_new_defer_refunds_once(env):
    active = activated(env)
    body = refund(env, active)
    assert item(env)["status"] == "queued" and item(env)["attempts"] == 2
    admission = readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert admission["attempts"] == 3 and admission["attempt_basis"] == "original_defer_refund"
    before = snapshot(env)
    assert readmission.readmit(profile=env.profile, request=body, now=NOW) == {**admission, "replayed": True}
    assert snapshot(env) == before
    new_defer = old(env, claim=admission["claim"])
    receipt = apply(env, active, new_defer)
    assert receipt["attempts"] == 2 and item(env)["status"] == "budget_wait"
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=new_defer, now=NOW) as permit:
            assert permit.receipt == {**receipt, "replayed": True}
    assert item(env)["attempts"] == 2


def test_caller_refund_receipts_cannot_replace_actual_sql_parent_and_resume(env):
    active = activated(env)
    body = refund(env, active)
    body["defer_receipt"]["attempts"] = body["resume_receipt"]["attempts"] = 1
    before = snapshot(env)
    with pytest.raises(SyncError):
        readmission.readmit(profile=env.profile, request=body, now=NOW)
    assert snapshot(env) == before


def test_actual_commit_failure_rolls_back_claim_and_admission(env, monkeypatch):
    activated(env)
    before = snapshot(env)
    original_open = migrations._open
    def denied(path):
        con = original_open(path)
        con.set_authorizer(lambda action, first, *_: sqlite3.SQLITE_DENY
            if action == sqlite3.SQLITE_TRANSACTION and first == "COMMIT" else sqlite3.SQLITE_OK)
        return con
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_open", denied)
        with pytest.raises(sqlite3.DatabaseError):
            readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert snapshot(env) == before


@pytest.mark.parametrize("change,code", [("revision", "SYNC_SOURCE_READMIT_CONTROL_CHANGED"),
    ("session", "SYNC_LEASE_MISMATCH"), ("epoch", "SYNC_RECOVERY_STREAM_MISMATCH")])
def test_actual_control_change_before_commit_rolls_back_both_effects(env, monkeypatch, change, code):
    activated(env)
    before = snapshot(env)
    original_insert = service._insert
    def changed(con, table, row):
        original_insert(con, table, row)
        if change == "revision":
            change_control(con)
        elif change == "session":
            assert con.execute("UPDATE sync_session SET generation=generation+1 WHERE slot=1").rowcount == 1
        else:
            assert con.execute("UPDATE meta SET value='8' WHERE key='library_epoch'").rowcount == 1
    monkeypatch.setattr(service, "_insert", changed)
    with pytest.raises(SyncError) as caught:
        readmission.readmit(profile=env.profile, request=request(env), now=NOW)
    assert caught.value.code == code and snapshot(env) == before
