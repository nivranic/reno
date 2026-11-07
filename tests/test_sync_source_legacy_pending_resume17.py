"""Released16 resume/undispatched requests through actual Cloud and Home CAS.

The parent is produced by the original budget writer. A real user resume
authorization and the original worker freeze the resume request; its actual
Cloud commit deliberately loses the response. No receipt/registration or
negative outcome is fabricated to make the legacy bridge succeed.
"""
from copy import deepcopy
import json

import pytest

from reno import storage_admin, sync_source_authority as permanent, sync_state
from reno import sync_source_legacy_pending_state17 as capsules
from reno import sync_source_readmit_schema as readmit
from reno.sync_protocol import SyncError
from tests.test_sync_source_genesis17 import permanent_files
from tests.test_sync_source_legacy_pending_intake17 import real_cycle, original_intent, sql_rows
from tests.test_sync_source_legacy_pending_public17 import (
    stage, deliver, reconcile, cloud_result, replay_files, forbid_new_http, state_file)


def original_cloud_rows(value):
    with storage_admin.connection(value.cloud) as connection:
        return {table: tuple(tuple(row) for row in connection.execute(
            'SELECT * FROM "' + table + '" ORDER BY 1'))
            for table in (*readmit.TABLES, "sync_queue", "sync_queue_budget_receipt")}


@pytest.mark.parametrize("real_cycle", ["legacy_resume"], indirect=True)
def test_source16_original_resume_keeps_real_parent_and_consumes_only_one_cas(real_cycle, monkeypatch):
    value = real_cycle
    intent = original_intent(value)
    original = intent["record"]["request"]["original_request"]
    assert original == value.legacy_budget_request and original["kind"] == "budget_resume"
    assert original["source"]["handshake"]["migration_version"] == 16
    assert original["defer_request_id"] == value.legacy_parent["request"]["request_id"]
    assert intent["record"]["request"]["registration"] is None
    assert value.store.read()["budget_pending"]["resume_receipt"] is None
    with storage_admin.connection(value.cloud) as connection:
        for body, receipt in ((value.legacy_parent["request"], value.legacy_parent["receipt"]),
                (original, value.legacy_budget_receipt)):
            stored = connection.execute("SELECT request_json,result_json FROM sync_queue_budget_receipt "
                "WHERE request_id=?", (body["request_id"],)).fetchone()
            assert stored is not None
            assert json.loads(stored["request_json"]) == body
            assert json.loads(stored["result_json"]) == receipt
    staged = stage(value, intent)
    saved = deliver(value, intent)
    answer = cloud_result(value, intent)
    assert answer["disposition"] == "applied" and answer["original_kind"] == "budget_resume"
    assert answer["original_receipt"] == value.legacy_budget_receipt
    assert saved["record"]["parent_defer"] == value.legacy_parent
    assert saved["record"]["result"] == answer
    assert value.store.read() == staged
    paid, home_files, cloud_rows = sql_rows(value.profile), permanent_files(value), original_cloud_rows(value)
    requests, writes, actual_write = deepcopy(value.transport.seen), [], sync_state._write

    def counted(directory, name, raw, **kwargs):
        if directory.path == value.store.data_dir and name == sync_state._STATE:
            writes.append(bytes(raw))
        return actual_write(directory, name, raw, **kwargs)

    monkeypatch.setattr(sync_state, "_write", counted)
    after = reconcile(value, intent)
    assert len(writes) == 1
    assert after["source_authority"]["origin"] == permanent.record_ref(value.origin)
    assert after["source_recovery_pending"] is None
    assert after["budget_pending"] == {**staged["budget_pending"],
        "resume_receipt": value.legacy_budget_receipt}
    for field in ("source_execution", "lease", "local_epoch", "recovery", "claims",
            "work", "pending_publication", "source_control_pending"):
        assert after[field] == staged[field]
    assert sql_rows(value.profile) == paid and permanent_files(value) == home_files
    assert original_cloud_rows(value) == cloud_rows and value.transport.seen == requests
    before = replay_files(value, intent)
    forbid_new_http(value, monkeypatch)
    assert deliver(value, intent) == saved
    assert reconcile(value, intent) == after and reconcile(value, intent) == after
    assert len(writes) == 1 and replay_files(value, intent) == before
    assert original_cloud_rows(value) == cloud_rows and value.transport.seen == requests


@pytest.mark.parametrize("real_cycle", ["legacy_undispatched"], indirect=True)
def test_source16_undispatched_without_registration_retains_pending_and_no_negative_fact(real_cycle):
    value = real_cycle
    intent = original_intent(value)
    original = intent["record"]["request"]["original_request"]
    assert original == value.legacy_budget_request and original["kind"] == "budget_defer"
    assert value.legacy_budget_receipt is None
    assert intent["record"]["request"]["registration"] is None
    assert original["source"]["handshake"]["migration_version"] == 16
    with storage_admin.connection(value.cloud) as connection:
        assert connection.execute("SELECT 1 FROM sync_queue_budget_receipt WHERE request_id=?",
            (original["request_id"],)).fetchone() is None
        assert connection.execute("SELECT 1 FROM " + readmit.REGISTRATION + " WHERE original_request_id=?",
            (original["request_id"],)).fetchone() is None
    staged = stage(value, intent)
    before = state_file(value), permanent_files(value), sql_rows(value.profile), original_cloud_rows(value)
    with pytest.raises(SyncError) as caught:
        deliver(value, intent)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED"
    assert value.store.read() == staged
    assert (state_file(value), permanent_files(value), sql_rows(value.profile), original_cloud_rows(value)) == before
    assert len(value.transport.seen) == 1 and value.transport.seen[0][0] == "/reno/api/sync/source/dispose"
    assert value.store.read()["budget_pending"]["defer_receipt"] is None
    assert value.store.read()["source_recovery_pending"] is not None
    capsule = capsules.path(value.profile, intent["record"]["record_id"])
    assert not capsule.exists() and not capsule.with_name(intent["record"]["record_id"] + ".applied.json").exists()
    with storage_admin.connection(value.cloud) as connection:
        assert connection.execute("SELECT 1 FROM " + readmit.DISPOSITION + " WHERE request_id=?",
            (intent["record"]["record_id"],)).fetchone() is None
