"""Real prospective Cloud SQL/ASGI disposition2, with no platform effects."""
from copy import deepcopy
import json
import sqlite3
import uuid

import pytest

from reno import storage_admin, storage_lifecycle as lifecycle, sync_protocol as protocol
from reno import sync_source_continuity as external, sync_source_continuity_service as service
from reno import sync_source_readmit_contract as wire, sync_source_readmit_schema as old_schema
from reno import sync_source_readmit_schema2 as schema, sync_source_readmit_sql2 as sql2
from reno.sync_protocol import SyncError
from tests import test_sync_source_continuity_service as original_fixture
from tests.test_sync_source_readmission import apply, old, request, snapshot, item, register, change_control
from tests.test_sync_source_recovery_api import http, sdk


@pytest.fixture
def env(tmp_path, monkeypatch, request):
    # DDL selection happens during this isolated library's explicit setup,
    # before any registration/activation; no reader rebuilds a live table.
    monkeypatch.setattr(old_schema, "install", schema.install_dispose2)
    value = original_fixture.env.__wrapped__(tmp_path, monkeypatch, request)
    def rules():
        return {**original_fixture.rules(), "source_readmit_version": 2, "source_dispose_version": 2}
    monkeypatch.setattr(protocol, "schema_rules", rules)
    monkeypatch.setattr(protocol, "SOURCE_READMIT_VERSION", 2, raising=False)
    monkeypatch.setattr(protocol, "SOURCE_DISPOSE_VERSION", 2, raising=False)
    with storage_admin.connection(value.profile) as con:
        value.source["handshake"] = protocol.schema_handshake(con)
        con.execute("UPDATE sync_pair SET schema_fingerprint=? WHERE slot=1",
            (value.source["handshake"]["core_schema_sha256"],))
        con.commit()
        assert schema.detect(con) == "admission2_dispose2"
    value.active = original_fixture.activated(value)
    return value


def body(value, control, registered=None):
    result = {key: deepcopy(cell) for key, cell in request(value).items() if key in wire._BOUND}
    result.update(format_version=2, kind="source_dispose", request_id=str(uuid.uuid4()),
        current_home_epoch=result["original"]["home_epoch"], preservation=None,
        original_request=deepcopy(control), registration=deepcopy(registered),
        continuity={"format_version": 1, "continuity_id": value.active["continuity_id"],
            "contract_sha256": external._digest(value.active), "target_epoch": value.active["target_epoch"]})
    return wire.normalize_dispose_request(result)


def readmit2(value, disposition):
    result = request(value)
    result.pop("manual_resume_basis")
    result.update(format_version=2, resume_basis=None, dispositions=[deepcopy(disposition)],
        current_home_epoch=result["original"]["home_epoch"], preservation=None)
    return wire.normalize_readmit_request(result)


def test_registered_negative_disposition_is_permanent_before_actual_targeted_readmission(env):
    control = old(env)
    registered = register(env, env.active, control)
    original_row = item(env)
    value = body(env, control, registered)
    result = sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    assert result["format_version"] == 2 and result["disposition"] == "not_applied"
    assert result["original_receipt"] is None and result["replayed"] is False
    assert item(env) == original_row
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        assert guard.observe()["entries"]["tombstones"][control["request_id"]]["record"] == result
    with storage_admin.connection(env.profile) as con:
        row = con.execute("SELECT * FROM " + schema.DISPOSITION).fetchone()
        assert schema.validate_disposition_record(row)["format_version"] == 2
        with pytest.raises(old_schema.SourceReadmitRecordError):
            old_schema.validate_disposition_record(row)
    admitted = sql2.readmit(profile=env.profile, request=readmit2(env, result), now=original_fixture.NOW)
    assert admitted["attempt_basis"] == "held_claim"
    assert admitted["attempts"] == original_row["attempts"]
    assert admitted["claim"]["lease_generation"] == original_row["lease_generation"] + 1
    assert admitted["disposition_sha256s"] == [wire.disposition_sha256(result)]


def test_actual_asgi_sdk_applied_receipt_and_replay_do_not_refund_a_second_time(env, http):
    control = old(env)
    receipt = apply(env, env.active, control)
    with storage_admin.connection(env.profile) as con:
        registration = json.loads(con.execute("SELECT result_json FROM " + schema.REGISTRATION
            + " WHERE original_request_id=?", (control["request_id"],)).fetchone()[0])
    value = body(env, control, registration)
    before_row = item(env)
    client = sdk(http)
    result = client.source_dispose(value)
    assert result["format_version"] == 2 and result["disposition"] == "applied"
    assert result["original_receipt"] == receipt
    assert item(env) == before_row
    with storage_admin.connection(env.profile) as con:
        change_control(con)
        con.commit()
    before = snapshot(env)
    assert client.source_dispose(value) == {**result, "replayed": True}
    assert snapshot(env) == before


def test_unregistered_control_without_sql_positive_never_gains_negative_proof(env):
    value = body(env, old(env))
    before = snapshot(env)
    with pytest.raises(SyncError) as denied:
        sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    assert denied.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED"
    assert snapshot(env) == before
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        assert not guard.observe()["entries"]["tombstones"]


def test_committed_external_effect_without_sql_receipt_keeps_unknown(env):
    from reno import sync_budget_wait
    control = old(env)
    registered = register(env, env.active, control)
    with pytest.raises(RuntimeError, match="rollback after original effect"):
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=control,
                    now=original_fixture.NOW):
                sync_budget_wait.apply_request(con, control, now=original_fixture.NOW)
                raise RuntimeError("rollback after original effect")
    before = snapshot(env)
    with pytest.raises(SyncError) as denied:
        sql2.dispose(profile=env.profile, request=body(env, control, registered), now=original_fixture.NOW)
    assert denied.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"
    assert snapshot(env) == before
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        observed = guard.observe()
        assert control["request_id"] in observed["entries"]["effects"]
        assert control["request_id"] not in observed["entries"]["tombstones"]


@pytest.mark.parametrize("change", ["capability", "continuity", "request_version"])
def test_closed_version_or_wrong_actual_continuity_has_no_disposition_effect(env, monkeypatch, change):
    control = old(env)
    registered = register(env, env.active, control)
    value = body(env, control, registered)
    if change == "capability":
        monkeypatch.setattr(protocol, "SOURCE_DISPOSE_VERSION", 1)
    elif change == "continuity":
        value["continuity"]["contract_sha256"] = "f" * 64
    else:
        value.pop("format_version")
        value.pop("continuity")
    before = snapshot(env)
    with pytest.raises((SyncError, wire.SourceReadmitContractError)):
        sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    assert snapshot(env) == before


def test_negative_disposition_replay_keeps_original_body_and_rejects_second_uuid(env):
    control = old(env)
    registered = register(env, env.active, control)
    value = body(env, control, registered)
    result = sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    with storage_admin.connection(env.profile) as con:
        change_control(con)
        con.commit()
    before = snapshot(env)
    assert sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW) == {**result, "replayed": True}
    assert snapshot(env) == before
    changed = deepcopy(value)
    changed["request_id"] = str(uuid.uuid4())
    with pytest.raises(SyncError) as denied:
        sql2.dispose(profile=env.profile, request=changed, now=original_fixture.NOW)
    assert denied.value.code == "SYNC_SOURCE_READMIT_OPERATION_CONFLICT"
    assert snapshot(env) == before


@pytest.mark.parametrize("version", [1, True, False, 3])
def test_private_sql_consumer_cannot_mislabel_explicit_wire_version(env, version):
    control = old(env)
    registered = register(env, env.active, control)
    value = body(env, control, registered)
    before = snapshot(env)
    with pytest.raises(SyncError):
        service._dispose(profile=env.profile, request=value, now=original_fixture.NOW, version=version)
    assert snapshot(env) == before


def test_actual_global16_rejects_disposition2_before_file_or_sql_effect(env, monkeypatch):
    from reno import migrations
    control = old(env)
    registered = register(env, env.active, control)
    value = body(env, control, registered)
    before = snapshot(env)
    with monkeypatch.context() as scoped:
        scoped.setattr(migrations, "SCHEMA_VERSION", 16)
        with pytest.raises(SyncError) as denied:
            sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
        assert denied.value.code == "SYNC_SOURCE_READMIT_SCHEMA_REQUIRED"
    assert snapshot(env) == before
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        assert not guard.observe()["entries"]["tombstones"]


@pytest.mark.parametrize("capability_closed", [False, True])
def test_original_negative_gap_finishes_after_actual_cloud_epoch_advance(env, monkeypatch, capability_closed):
    from tests.test_sync_source_continuity_service import advance_cloud_epoch
    control = old(env)
    registered = register(env, env.active, control)
    value = body(env, control, registered)
    original_insert = service._insert
    def interrupted(con, table, row):
        if table == schema.DISPOSITION:
            raise sqlite3.OperationalError("interrupted original negative SQL insert")
        return original_insert(con, table, row)
    with monkeypatch.context() as scoped:
        scoped.setattr(service, "_insert", interrupted)
        with pytest.raises(sqlite3.OperationalError, match="interrupted original negative"):
            sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        saved = guard.observe()["entries"]["tombstones"][control["request_id"]]["record"]
    advance_cloud_epoch(env)
    if capability_closed:
        monkeypatch.setattr(protocol, "SOURCE_DISPOSE_VERSION", 1)
    before_row = item(env)
    result = sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    assert result == saved
    assert item(env) == before_row
    with storage_admin.connection(env.profile) as con:
        assert json.loads(con.execute("SELECT result_json FROM " + schema.DISPOSITION
            + " WHERE request_id=?", (value["request_id"],)).fetchone()[0]) == saved


def test_actual_cloud_epoch_advance_never_authorizes_first_negative_disposition(env):
    from tests.test_sync_source_continuity_service import advance_cloud_epoch
    control = old(env)
    registered = register(env, env.active, control)
    value = body(env, control, registered)
    advance_cloud_epoch(env)
    before = snapshot(env)
    with pytest.raises(SyncError) as denied:
        sql2.dispose(profile=env.profile, request=value, now=original_fixture.NOW)
    assert denied.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED"
    assert snapshot(env) == before
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        assert not guard.observe()["entries"]["tombstones"]
