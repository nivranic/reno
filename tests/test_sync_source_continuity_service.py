"""Genuine old16 receipts followed by first-install historical17 SQL/files.

Ordinary consumers run the declared historical variant under real current17.
Existing explicit outer released16 callers keep their temporary prospective17
producer scope; those callers close it before returning to global16 negatives.
No default current17 image is reverse-installed, and no old receipt is seeded.
"""
from copy import deepcopy
from contextlib import closing
from contextvars import copy_context
from datetime import datetime, timedelta, timezone
import hashlib
import json
import sqlite3
import threading
from types import SimpleNamespace
import uuid

import pytest

from reno import config, db, migrations, storage_admin, storage_lifecycle as lifecycle
from reno import sync_budget_wait as old_budget, sync_source_control as old_source
from reno import sync_source_continuity as external, sync_source_continuity_service as service
from reno import sync_source_readmit_schema as schema, sync_source_readmit_contract as wire
from reno import sync_source_readmit_schema2 as current_schema
from reno import sync_protocol as protocol, sync_queue as queue
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract as sample
from tests.schema_fixtures import (assert_current17_runtime, assert_released16_image,
    initialize_released16, released16, _assert_no_live_owners, _assert_released_runtime)


NOW = datetime(2030, 1, 1, tzinfo=timezone.utc)
STAMP = "2030-01-01T00:00:00.000Z"
URL = "https://www.bilibili.com/video/BV1xx411c7mD"


def rules():
    return {"claim_origin_version": 1, "recovery_fence_version": 1,
        "derivation_binding_version": 1, "upload_intent_version": 1,
        "source_control_continuity_version": 1,
        "json_roots": {key: [kind.__name__ for kind in kinds] for key, kinds in protocol._JSON_ROOTS.items()},
        "integer_columns": protocol._INTEGER_COLUMNS, "real_columns": protocol._REAL_COLUMNS,
        "local_columns": protocol._LOCAL_COLUMNS, "step_names": protocol.STEP_NAMES,
        "media_extensions": protocol.MEDIA_EXTENSIONS,
        "limits": [protocol.MAX_MEDIA_BYTES, protocol.MAX_ROWS, protocol.MAX_CELL_BYTES, protocol.MAX_ROW_BYTES,
            protocol.MAX_BATCH_BYTES, protocol.MAX_JSON_DEPTH, protocol.MAX_JSON_NODES, protocol.MAX_URL_LENGTH]}


def _all_original_rows(con):
    names = [row[0] for row in con.execute(
        "SELECT name FROM main.sqlite_master WHERE type='table' ORDER BY name")]
    return {name: sorted((tuple(row) for row in con.execute(
        'SELECT * FROM main."' + name.replace('"', '""') + '"')), key=repr) for name in names}


def build_released16_cloud_environment(profile, request):
    """Actual old producer; caller owns its complete released16/Cloud phase."""
    _assert_released_runtime()
    initialize_released16(profile.database)
    lifecycle.initialize(profile)
    legacy_source = legacy_source_receipt = None
    with storage_admin.connection(profile) as con:
        con.execute("UPDATE meta SET value='7' WHERE key='library_epoch'")
        target = dict(zip(("library_id", "node_id"), queue._local_identity(con)))
        source = {**{key: sample.SOURCE[key] for key in ("library_id", "node_id")}, "handshake": protocol.schema_handshake(con)}
        assert source["handshake"]["migration_version"] == 16
        assert source["handshake"]["protocol_version"] == 2
        queue.bind_peer(con, source, now=NOW)
        con.execute("INSERT INTO sync_session(slot,session_id,lease_token,generation,lease_until,updated_at) VALUES(1,?,?,?,?,?)",
            (sample.LEASE["session_id"], sample.LEASE["lease_token"], sample.LEASE["generation"], sample.LEASE["lease_until"], STAMP))
        con.execute("INSERT INTO sync_queue(item_id,url,status,attempts,owner_id,owner_generation,lease_token,lease_generation,lease_until,created_at,updated_at) VALUES(7,?,'claimed',3,?,?,?,?,?,?,?)",
            (URL, sample.LEASE["session_id"], sample.LEASE["generation"], sample.CLAIM["lease_token"], sample.CLAIM["lease_generation"], sample.LEASE["lease_until"], STAMP, STAMP))
        legacy = sample.old_request()
        legacy.update(source=deepcopy(source), target=deepcopy(target), recovery_generation=0,
                      recovery_stream={"library_id": target["library_id"], "library_epoch": 7})
        old_result = old_budget.apply_request(con, legacy, now=NOW)
        resume = deepcopy(legacy)
        resume = {key: resume[key] for key in ("source", "target", "lease", "recovery_generation", "recovery_stream")}
        resume.update(kind="budget_resume", request_id=str(uuid.uuid4()), defer_request_id=legacy["request_id"])
        resume_result = old_budget.apply_request(con, resume, now=NOW)
        pulled = queue.claim(con, source, deepcopy(sample.LEASE), limit=1, now=NOW)
        fresh_claim = deepcopy(pulled[0]["claim"])
        if getattr(request, "param", None) == "legacy_source_fail":
            legacy_source = sample.old_request("source_fail")
            legacy_source.update(source=deepcopy(source), target=deepcopy(target), claim=deepcopy(fresh_claim),
                recovery_generation=0, recovery_stream={"library_id": target["library_id"], "library_epoch": 7},
                submitted_url_sha256=hashlib.sha256(URL.encode()).hexdigest())
            legacy_source_receipt = old_source.apply_request(con, legacy_source, now=NOW)
        con.commit()
        old_rows = _all_original_rows(con)
        assert old_budget._receipt(con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
            (legacy["request_id"],)).fetchone(), con=con)[1] == old_result
        assert old_budget._receipt(con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?",
            (resume["request_id"],)).fetchone(), con=con)[1] == resume_result
    assert_released16_image(profile.database)
    return SimpleNamespace(profile=profile, source=source, target=target, legacy=legacy,
        legacy_receipt=old_result, legacy_resume=resume, legacy_resume_receipt=resume_result,
        fresh_claim=fresh_claim, legacy_source=legacy_source,
        legacy_source_receipt=legacy_source_receipt, original16_rows=old_rows)


def transition_historical17_cloud_environment(value):
    """First explicit fixture DDL after every original producer owner exits."""
    _assert_no_live_owners()
    assert_released16_image(value.profile.database)
    with lifecycle.shared(value.profile):
        with closing(sqlite3.connect(value.profile.database)) as con:
            con.row_factory = sqlite3.Row
            con.execute("PRAGMA foreign_keys=ON")
            con.execute("BEGIN IMMEDIATE")
            assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
            assert not schema.preflight(con, require_absent=True)
            before = _all_original_rows(con)
            assert before == value.original16_rows
            # A specific caller may select joint-v2 INSTALL on true absence;
            # that is not removal/reinstallation of already-existing v1 DDL.
            schema.install(con)
            con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
            value.historical_variant = current_schema.detect(con)
            migrations.validate(con)
            after = _all_original_rows(con)
            assert set(after) == set(before) | set(schema.COLUMNS)
            for table, original in before.items():
                if table == "meta":
                    without_version = lambda rows: [row for row in rows if row[0] != "migration_version"]
                    assert without_version(after[table]) == without_version(original)
                else:
                    assert after[table] == original
            assert con.execute("PRAGMA foreign_key_check").fetchone() is None
            con.commit()
    _assert_no_live_owners()


def refresh_historical17_cloud_handshake(value):
    # Explicit OFFLINE synthetic-fixture initialization after first17 install.
    # This is not queue.refresh_peer_schema or an HTTP maintenance receipt:
    # the fixture deliberately retains its active2030 session/claimed leases.
    # Keep identities, paired_at, old requests, paid/queue/history facts exact;
    # only the pair's two protocol cells adopt the actual new handshake.
    _assert_no_live_owners()
    source, legacy = value.source, value.legacy
    source_identity = {key: source[key] for key in ("library_id", "node_id")}
    historical_names = ("legacy", "legacy_receipt", "legacy_resume", "legacy_resume_receipt",
                        "legacy_source", "legacy_source_receipt")
    historical_before = deepcopy({name: getattr(value, name) for name in historical_names})
    with storage_admin.connection(value.profile) as con:
        con.execute("BEGIN IMMEDIATE")
        before = _all_original_rows(con)
        pair_columns = [row[1] for row in con.execute("PRAGMA main.table_info(sync_pair)")]
        pair = dict(con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone())
        assert before["sync_pair"] == [tuple(pair[name] for name in pair_columns)]
        assert {key: pair["source_" + key] for key in ("library_id", "node_id")} == source_identity
        assert {key: pair["target_" + key] for key in ("library_id", "node_id")} == value.target
        source["handshake"] = protocol.schema_handshake(con)
        assert source["handshake"]["core_schema_sha256"] != legacy["source"]["handshake"]["core_schema_sha256"]
        # Do not force either protocol2 historical callers or production3
        # callers into the other branch: use this real handshake's two fields.
        changed = con.execute("UPDATE sync_pair SET protocol_version=?,schema_fingerprint=? "
            "WHERE slot=1 AND protocol_version=? AND schema_fingerprint=?",
            (source["handshake"]["protocol_version"], source["handshake"]["core_schema_sha256"],
             pair["protocol_version"], pair["schema_fingerprint"]))
        assert changed.rowcount == 1
        expected = {**pair, "protocol_version": source["handshake"]["protocol_version"],
                    "schema_fingerprint": source["handshake"]["core_schema_sha256"]}
        actual = dict(con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone())
        assert actual == expected
        assert queue._pair(con, source) == expected
        after = _all_original_rows(con)
        assert set(after) == set(before)
        for table, original in before.items():
            if table == "sync_pair":
                assert after[table] == [tuple(expected[name] for name in pair_columns)]
            else:
                assert after[table] == original
        assert {name: getattr(value, name) for name in historical_names} == historical_before
        assert {key: source[key] for key in ("library_id", "node_id")} == source_identity
        assert legacy["source"]["handshake"]["migration_version"] == 16
        assert legacy["source"]["handshake"]["protocol_version"] == 2
        con.commit()
    _assert_no_live_owners()

@pytest.fixture
def env(tmp_path, monkeypatch, request):
    # The two existing outer released16 callers deliberately select a
    # temporary historical17 producer. Do not upgrade their later global16
    # negatives, and do not let that historical scope cover current17 users.
    historical16_caller = migrations.SCHEMA_VERSION == 16
    if historical16_caller:
        _assert_released_runtime()
    else:
        assert_current17_runtime()
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    monkeypatch.setattr(config, "is_cloud", lambda: True)
    with released16(monkeypatch):
        value = build_released16_cloud_environment(profile, request)
    _assert_no_live_owners()
    if historical16_caller:
        # These mutations belong to the caller's explicit monkeypatch.context;
        # its finally restores exact original16 declarations before yielding.
        monkeypatch.setattr(migrations, "SCHEMA_VERSION", 17)
        monkeypatch.setattr(protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", 1, raising=False)
        monkeypatch.setattr(protocol, "schema_rules", rules, raising=False)
    else:
        # Keep production protocol3 and every published capability untouched.
        assert_current17_runtime()
    transition_historical17_cloud_environment(value)
    refresh_historical17_cloud_handshake(value)
    return value


def bound(env, value):
    value = deepcopy(value)
    def walk(item):
        if type(item) is dict:
            if "source" in item and "target" in item:
                item["source"] = deepcopy(env.source)
                item["target"] = deepcopy(env.target)
            if "recovery_generation" in item:
                item["recovery_generation"] = 0
            if "control_revision" in item:
                item["control_revision"] = 0
            if "recovery_stream" in item:
                item["recovery_stream"] = {"library_id": env.target["library_id"], "library_epoch": 7}
            for child in item.values():
                walk(child)
        elif type(item) is list:
            for child in item:
                walk(child)
    walk(value)
    if value.get("kind") in {"budget_defer", "source_fail"}:
        value["claim"] = deepcopy(env.fresh_claim)
    elif value.get("kind") == "source_register" and value["original_request"]["kind"] != "budget_resume":
        value["original_request"]["claim"] = deepcopy(env.fresh_claim)
    if value.get("kind") == "source_fail":
        value["submitted_url_sha256"] = hashlib.sha256(URL.encode()).hexdigest()
    return value


def activated(env):
    return service.activate(profile=env.profile, continuity_id=str(uuid.uuid4()), activated_at=STAMP)


def register_request(env, active, *, kind="budget_defer"):
    old = bound(env, sample.old_request(kind))
    if kind == "budget_resume":
        old["defer_request_id"] = env.legacy["request_id"]
    request = bound(env, sample.register(old))
    request["continuity"] = {"format_version": 1, "continuity_id": active["continuity_id"],
        "contract_sha256": external._digest(active), "target_epoch": active["target_epoch"]}
    return request


def dispose_request(env, old, registration=None):
    value = bound(env, sample.dispose(old))
    value["original_request"] = deepcopy(old)
    value["registration"] = deepcopy(registration)
    if old["kind"] == "source_fail":
        value["original"]["submitted_url_sha256"] = old["submitted_url_sha256"]
    return value


def rows(env):
    with storage_admin.connection(env.profile) as con:
        return {table: [tuple(row) for row in con.execute("SELECT * FROM " + table + " ORDER BY 1")]
            for table in (*schema.TABLES, "sync_queue", "sync_queue_budget_receipt", "sync_queue_source_receipt")}


def install_conflicting_positive_fixture(con, env, original):
    """Install canonical restored SQL evidence, without invoking a leaf gate.

    This deliberately incompatible restore fixture grants no authority. Copy
    the actual old16 receipt representation, bind it to the exact later body,
    and validate the resulting stored row with the actual receipt reader.
    """
    queued = con.execute("SELECT * FROM sync_queue WHERE item_id=7").fetchone()
    actual = dict(con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (env.legacy["request_id"],)).fetchone())
    raw_original = wire.canonical_json(original)
    result = {**env.legacy_receipt, "request_id": original["request_id"],
        "request_sha256": wire.digest(original), "lease_generation": original["claim"]["lease_generation"],
        "attempts": queued["attempts"] - 1, **{key: original[key] for key in ("reason", "retry_at", "budget_operation_id")}}
    raw_result = wire.canonical_json(result)
    actual.update(request_id=original["request_id"], request_json=raw_original,
        request_sha256=wire.digest(original), result_json=raw_result, result_sha256=wire.digest(result),
        lease_generation=original["claim"]["lease_generation"])
    columns = tuple(actual)
    con.execute("INSERT INTO sync_queue_budget_receipt(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")",
        tuple(actual[name] for name in columns))
    con.execute("UPDATE sync_queue SET status='budget_wait',attempts=?,retry_at=?,budget_wait_reason=?,budget_operation_id=?,owner_id=NULL,owner_generation=0,lease_token=NULL,lease_until=NULL WHERE item_id=7",
        (result["attempts"], result["retry_at"], result["reason"], result["budget_operation_id"]))
    stored = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (original["request_id"],)).fetchone()
    assert old_budget._receipt(stored, raw_original, con=con)[1] == result


def test_current_global16_or_missing_actual_capability_never_activates_or_registers(env, monkeypatch):
    before = rows(env)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 16)
    with pytest.raises(SyncError) as caught:
        service.activate(profile=env.profile, continuity_id=str(uuid.uuid4()), activated_at=STAMP)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 17)
    monkeypatch.delattr(protocol, "schema_rules")
    with pytest.raises(SyncError) as caught:
        service.activate(profile=env.profile, continuity_id=str(uuid.uuid4()), activated_at=STAMP)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"
    assert rows(env) == before
    assert not (env.profile.database.parent / external.INTENT_NAME).exists()


def test_only_version17_and_a_constant_are_insufficient_without_real_rule(env, monkeypatch):
    monkeypatch.setattr(protocol, "schema_rules", lambda: {"source_control_continuity_version": 0})
    with pytest.raises(SyncError) as caught:
        activated(env)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"


def test_activation_binds_actual_peer_node_epoch_profile_and_exact_uuid(env):
    before = rows(env)
    active = activated(env)
    assert active["target_identity"] == env.target
    assert active["source_identity"] == {key: env.source[key] for key in ("library_id", "node_id")}
    assert active["owner_profile_sha256"] == external.profile_sha256(env.profile)
    assert service.activate(profile=env.profile, continuity_id=active["continuity_id"], activated_at=STAMP) == active
    after = rows(env)
    for table in ("sync_queue", "sync_queue_budget_receipt", "sync_queue_source_receipt"):
        assert after[table] == before[table]
    with pytest.raises(SyncError):
        service.activate(profile=env.profile, continuity_id=str(uuid.uuid4()), activated_at=STAMP)


def test_registration_keeps_exact_original_body_and_never_changes_queue(env):
    active = activated(env)
    request = register_request(env, active)
    before = rows(env)
    result = service.register(profile=env.profile, request=request, now=NOW)
    assert result["replayed"] is False
    assert rows(env)["sync_queue"] == before["sync_queue"]
    stored = rows(env)[schema.REGISTRATION]
    assert len(stored) == 1
    same = service.register(profile=env.profile, request=request, now=NOW + timedelta(days=36500))
    assert same == {**result, "replayed": True}
    assert rows(env)[schema.REGISTRATION] == stored


def test_an_old16_body_cannot_be_registered_after_activation(env):
    active = activated(env)
    request = register_request(env, active)
    request["source"] = deepcopy(env.legacy["source"])
    request["original_request"]["source"] = deepcopy(env.legacy["source"])
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        service.register(profile=env.profile, request=request, now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"
    assert rows(env) == before


def test_exact_historical_positive_dispose_ignores_expired_lease_and_current_queue(env):
    activated(env)
    body = dispose_request(env, env.legacy)
    body["lease"]["lease_until"] = "2000-01-01T00:00:00.000Z"
    before = rows(env)["sync_queue"]
    result = service.dispose(profile=env.profile, request=body, now=NOW)
    assert result["disposition"] == "applied" and result["original_receipt"] == env.legacy_receipt
    assert rows(env)["sync_queue"] == before
    assert service.dispose(profile=env.profile, request=body, now=NOW) == {**result, "replayed": True}


def test_missing_legacy_receipt_is_unknown_and_cannot_be_disposed_negative(env):
    activated(env)
    old = deepcopy(env.legacy)
    old["request_id"] = str(uuid.uuid4())
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        service.dispose(profile=env.profile, request=dispose_request(env, old), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED"
    assert rows(env) == before


def test_positive_original_resume_keeps_the_actual_parent_defer_receipt_permanently(env):
    activated(env)
    body = dispose_request(env, env.legacy_resume)
    result = service.dispose(profile=env.profile, request=body, now=NOW + timedelta(days=36500))
    assert result["disposition"] == "applied" and result["original_kind"] == "budget_resume"
    with storage_admin.connection(env.profile) as con:
        row = schema.validate_disposition_record(con.execute("SELECT * FROM " + schema.DISPOSITION + " WHERE request_id=?", (body["request_id"],)).fetchone())
    assert json.loads(row["defer_receipt_json"]) == env.legacy_receipt
    assert row["defer_receipt_sha256"] == wire.digest(env.legacy_receipt)


def test_registered_negative_persists_before_sql_and_blocks_the_original_effect(env):
    active = activated(env)
    registered_body = register_request(env, active)
    registered = service.register(profile=env.profile, request=registered_body, now=NOW)
    request = dispose_request(env, registered_body["original_request"], registered)
    before = rows(env)["sync_queue"]
    result = service.dispose(profile=env.profile, request=request, now=NOW)
    assert result["disposition"] == "not_applied" and result["original_receipt"] is None
    assert rows(env)["sync_queue"] == before
    with service.writer(profile=env.profile) as con:
        with pytest.raises(SyncError) as caught:
            with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
                pytest.fail("Tombstoned original effect was authorized")
        assert caught.value.code == "SYNC_SOURCE_READMIT_OUTCOME_MISMATCH"


def test_sealed_before_effect_runs_real_old_effect_in_same_sql_writer(env):
    active = activated(env)
    registered_body = register_request(env, active)
    service.register(profile=env.profile, request=registered_body, now=NOW)
    original = registered_body["original_request"]
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW) as permit:
            assert permit.receipt is None
            permit.check(con, profile=env.profile)
            result = old_budget.apply_request(con, original, now=NOW)
    assert result["status"] == "budget_wait"
    after = rows(env)
    with service.writer(profile=env.profile, capability=False) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW + timedelta(days=36500)) as permit:
            assert permit.receipt == {**result, "replayed": True}
    assert rows(env) == after
    with pytest.raises(SyncError) as caught:
        permit.check(con, profile=env.profile)
    assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"


def test_marker_without_receipt_is_unknown_after_actual_sql_rollback(env):
    active = activated(env)
    registered_body = register_request(env, active)
    registered = service.register(profile=env.profile, request=registered_body, now=NOW)
    original = registered_body["original_request"]
    before = rows(env)
    with pytest.raises(RuntimeError, match="synthetic-after-marker"):
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                raise RuntimeError("synthetic-after-marker")
    assert rows(env) == before
    with service.writer(profile=env.profile) as con, pytest.raises(SyncError) as caught:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            pytest.fail("Unknown effect was retried")
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"
    with pytest.raises(SyncError) as caught:
        service.dispose(profile=env.profile, request=dispose_request(env, original, registered), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"


def test_no_original_receipt_after_fresh_callback_rolls_sql_back_without_new_rights(env):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    before = rows(env)
    with service.writer(profile=env.profile) as con, pytest.raises(SyncError) as caught:
        with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
            con.execute("UPDATE sync_queue SET attempts=2 WHERE item_id=7")
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_RECEIPT_REQUIRED"
    assert rows(env) == before


def test_registration_external_to_sql_crash_gap_reuses_original_permanent_result(env, monkeypatch):
    active = activated(env)
    request = register_request(env, active)
    real = service._insert
    def crash(con, table, row):
        if table == schema.REGISTRATION:
            raise RuntimeError("synthetic-registration-sql-gap")
        return real(con, table, row)
    monkeypatch.setattr(service, "_insert", crash)
    with pytest.raises(RuntimeError, match="synthetic-registration-sql-gap"):
        service.register(profile=env.profile, request=request, now=NOW)
    monkeypatch.setattr(service, "_insert", real)
    assert not rows(env)[schema.REGISTRATION]
    with storage_admin.connection(env.profile) as con:
        con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,restore_operation_id,updated_at) VALUES(1,'running',1,1,NULL,NULL,?)", (STAMP,))
        con.commit()
    result = service.register(profile=env.profile, request=request, now=NOW + timedelta(seconds=30))
    assert result["committed_at"] == STAMP
    assert len(rows(env)[schema.REGISTRATION]) == 1


def test_external_negative_sql_gap_replays_exact_uuid_even_after_lease_expiry(env, monkeypatch):
    active = activated(env)
    registered_body = register_request(env, active)
    registered = service.register(profile=env.profile, request=registered_body, now=NOW)
    request = dispose_request(env, registered_body["original_request"], registered)
    real = service._insert
    def crash(con, table, row):
        if table == schema.DISPOSITION:
            raise RuntimeError("synthetic-negative-sql-gap")
        return real(con, table, row)
    monkeypatch.setattr(service, "_insert", crash)
    with pytest.raises(RuntimeError, match="synthetic-negative-sql-gap"):
        service.dispose(profile=env.profile, request=request, now=NOW)
    monkeypatch.setattr(service, "_insert", real)
    assert not rows(env)[schema.DISPOSITION]
    with storage_admin.connection(env.profile) as con:
        con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,restore_operation_id,updated_at) VALUES(1,'running',1,1,NULL,NULL,?)", (STAMP,))
        con.commit()
    before = rows(env)["sync_queue"]
    result = service.dispose(profile=env.profile, request=request, now=NOW + timedelta(days=36500))
    assert result["committed_at"] == STAMP and result["disposition"] == "not_applied"
    assert rows(env)["sync_queue"] == before


def test_unowned_con_or_caller_dictionary_never_borrows_the_effect_seam(env):
    active = activated(env)
    request = register_request(env, active)
    before = rows(env)
    with storage_admin.connection(env.profile) as con, pytest.raises(SyncError) as caught:
        with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
            pytest.fail("Unowned SQL connection was authorized")
    assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"
    with pytest.raises(SyncError):
        with service.before_effect({"con": con}, profile=env.profile, original_request=request["original_request"], now=NOW):
            pytest.fail("Caller dictionary was authorized")
    assert rows(env) == before


def test_activation_file_to_sql_gap_cannot_rebind_the_original_uuid(env, monkeypatch):
    identifier = str(uuid.uuid4())
    real = service._insert
    def crash(con, table, row):
        if table == schema.ACTIVATION:
            raise RuntimeError("synthetic-activation-sql-gap")
        return real(con, table, row)
    monkeypatch.setattr(service, "_insert", crash)
    with pytest.raises(RuntimeError):
        service.activate(profile=env.profile, continuity_id=identifier, activated_at=STAMP)
    monkeypatch.setattr(service, "_insert", real)
    with pytest.raises(SyncError):
        activated(env)
    result = service.activate(profile=env.profile, continuity_id=identifier, activated_at=STAMP)
    assert result["continuity_id"] == identifier


def deny_commit(monkeypatch, profile):
    """Use SQLite's real authorizer: BEGIN and SQL succeed, COMMIT fails."""
    actual = migrations._open
    observed = []
    def opened(path):
        con = actual(path)
        if lifecycle._key(path) == lifecycle._key(profile.database):
            def authorize(action, first, second, database, context):
                if action == sqlite3.SQLITE_TRANSACTION:
                    observed.append(first)
                    if first == "COMMIT":
                        return sqlite3.SQLITE_DENY
                return sqlite3.SQLITE_OK
            con.set_authorizer(authorize)
        return con
    monkeypatch.setattr(migrations, "_open", opened)
    return observed


@pytest.mark.parametrize("operation", ["activation", "registration", "negative"])
def test_actual_sql_commit_failure_replays_only_the_exact_file_fact(env, monkeypatch, operation):
    identifier = str(uuid.uuid4())
    active = None if operation == "activation" else activated(env)
    request = None if active is None else register_request(env, active)
    if operation == "negative":
        registered = service.register(profile=env.profile, request=request, now=NOW)
        request = dispose_request(env, request["original_request"], registered)
    before = rows(env)
    def apply(stamp):
        if operation == "activation":
            return service.activate(profile=env.profile, continuity_id=identifier, activated_at=STAMP)
        if operation == "registration":
            return service.register(profile=env.profile, request=request, now=stamp)
        return service.dispose(profile=env.profile, request=request, now=stamp)
    with monkeypatch.context() as patch:
        observed = deny_commit(patch, env.profile)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            apply(NOW)
    assert observed == ["BEGIN", "COMMIT", "ROLLBACK"]
    assert rows(env) == before
    if operation == "activation":
        with pytest.raises(SyncError):
            service.activate(profile=env.profile, continuity_id=str(uuid.uuid4()), activated_at=STAMP)
    else:
        wrong = deepcopy(request)
        wrong["request_id"] = str(uuid.uuid4())
        with pytest.raises(SyncError):
            if operation == "registration":
                service.register(profile=env.profile, request=wrong, now=NOW)
            else:
                service.dispose(profile=env.profile, request=wrong, now=NOW)
        assert rows(env) == before
    result = apply(NOW + timedelta(days=36500))
    assert result["activated_at" if operation == "activation" else "committed_at"] == STAMP
    assert rows(env)["sync_queue"] == before["sync_queue"]


def test_original_effect_sql_commit_failure_leaves_unknown_marker_without_reexecution(env, monkeypatch):
    active = activated(env)
    request = register_request(env, active)
    registration = service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    before = rows(env)
    with monkeypatch.context() as patch:
        observed = deny_commit(patch, env.profile)
        with pytest.raises(sqlite3.DatabaseError, match="not authorized"):
            with service.writer(profile=env.profile) as con:
                with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                    old_budget.apply_request(con, original, now=NOW)
    assert observed == ["BEGIN", "COMMIT", "ROLLBACK"]
    assert rows(env) == before
    with service.writer(profile=env.profile) as con, pytest.raises(SyncError) as caught:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            pytest.fail("COMMIT failure authorized original reexecution")
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"
    with pytest.raises(SyncError) as caught:
        service.dispose(profile=env.profile, request=dispose_request(env, original, registration), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"
    assert rows(env) == before


@pytest.mark.parametrize("field", ["fingerprint", "session", "generation", "revision", "epoch"])
def test_actual_mutated_pair_session_and_control_never_publish_registration_or_effect(env, field):
    active = activated(env)
    request = register_request(env, active)
    with storage_admin.connection(env.profile) as con:
        if field == "fingerprint":
            con.execute("UPDATE sync_pair SET schema_fingerprint=? WHERE slot=1", ("f" * 64,))
        elif field == "session":
            con.execute("UPDATE sync_session SET lease_token=? WHERE slot=1", (str(uuid.uuid4()),))
        elif field == "epoch":
            con.execute("UPDATE meta SET value='8' WHERE key='library_epoch'")
        else:
            con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,restore_operation_id,updated_at) VALUES(1,'running',?,?,NULL,NULL,?)",
                (1 if field == "generation" else 0, 1 if field == "revision" else 0, STAMP))
        con.commit()
    before = rows(env)
    with pytest.raises(SyncError):
        service.register(profile=env.profile, request=request, now=NOW)
    assert rows(env) == before
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        observed = guard.observe()
        assert all(not values for values in observed["entries"].values())


def test_writer_is_bound_to_actual_connection_profile_and_thread_even_with_copied_context(env):
    active = activated(env)
    request = register_request(env, active)
    before = rows(env)
    other_profile = lifecycle.StorageProfile(env.profile.database.parent / "other.db", env.profile.media,
                                              env.profile.reports, env.profile.checkpoints)
    foreign = migrations._open(env.profile.database)
    errors = []
    try:
        with service.writer(profile=env.profile) as con:
            for actual, profile in ((foreign, env.profile), (con, other_profile)):
                with pytest.raises(SyncError) as caught:
                    with service.before_effect(actual, profile=profile, original_request=request["original_request"], now=NOW):
                        pytest.fail("Foreign writer owner borrowed an effect permit")
                assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"
            def use():
                try:
                    with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
                        errors.append("authorized")
                except SyncError as exc:
                    errors.append(exc.code)
            for copied in (False, True):
                target = copy_context().run if copied else None
                thread = threading.Thread(target=(lambda: target(use)) if copied else use)
                thread.start()
                thread.join(timeout=10)
                assert not thread.is_alive()
        assert errors == ["SYNC_SOURCE_READMIT_WRITER_REQUIRED"] * 2
    finally:
        foreign.close()
    assert rows(env) == before


def test_registered_source_failure_writes_actual_original_receipt_and_positive_disposition(env):
    active = activated(env)
    request = register_request(env, active, kind="source_fail")
    registration = service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            receipt = old_source.apply_request(con, original, now=NOW)
    assert receipt["status"] == "dead"
    result = service.dispose(profile=env.profile, request=dispose_request(env, original, registration), now=NOW)
    assert result["disposition"] == "applied" and result["original_receipt"] == receipt
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW + timedelta(days=36500)) as permit:
            assert permit.receipt == {**receipt, "replayed": True}


def test_registered_budget_resume_writes_actual_receipt_and_keeps_its_parent(env):
    active = activated(env)
    defer = register_request(env, active)
    service.register(profile=env.profile, request=defer, now=NOW)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=defer["original_request"], now=NOW):
            parent = old_budget.apply_request(con, defer["original_request"], now=NOW)
    resume = register_request(env, active, kind="budget_resume")
    resume["original_request"]["defer_request_id"] = defer["original_request"]["request_id"]
    registration = service.register(profile=env.profile, request=resume, now=NOW)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=resume["original_request"], now=NOW):
            receipt = old_budget.apply_request(con, resume["original_request"], now=NOW)
    assert receipt["status"] == "queued" and receipt["attempts"] == parent["attempts"]
    request = dispose_request(env, resume["original_request"], registration)
    result = service.dispose(profile=env.profile, request=request, now=NOW)
    assert result["original_receipt"] == receipt and result["original_kind"] == "budget_resume"
    with storage_admin.connection(env.profile) as con:
        row = schema.validate_disposition_record(con.execute("SELECT * FROM " + schema.DISPOSITION + " WHERE request_id=?", (request["request_id"],)).fetchone())
    assert json.loads(row["defer_receipt_json"]) == parent
    assert row["defer_receipt_sha256"] == wire.digest(parent)


def test_external_negative_cannot_be_hidden_by_sql_restore_and_a_conflicting_positive(env, tmp_path):
    active = activated(env)
    request = register_request(env, active)
    registration = service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    snapshot = tmp_path / "before-negative.db"
    with storage_admin.connection(env.profile) as con, sqlite3.connect(snapshot) as backup:
        con.backup(backup)
    service.dispose(profile=env.profile, request=dispose_request(env, original, registration), now=NOW)
    # Actually restore SQL to a previous committed image without replacing the
    # external journal/heads, then install an incompatible legacy positive.
    # This deliberately bypasses the prospective service to model restoration.
    with sqlite3.connect(snapshot) as backup, storage_admin.connection(env.profile) as con:
        backup.backup(con)
        install_conflicting_positive_fixture(con, env, original)
        con.commit()
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        service.dispose(profile=env.profile, request=dispose_request(env, original), now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_OPERATION_CONFLICT"
    with service.writer(profile=env.profile) as con, pytest.raises(SyncError) as caught:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            pytest.fail("SQL positive hid the permanent external negative")
    assert caught.value.code == "SYNC_SOURCE_READMIT_OPERATION_CONFLICT"
    assert rows(env) == before


@pytest.mark.parametrize("operation", ["registration", "negative", "effect"])
def test_actual_two_head_publication_gap_repairs_only_the_exact_selector(env, monkeypatch, operation):
    active = activated(env)
    request = register_request(env, active)
    if operation != "registration":
        registration = service.register(profile=env.profile, request=request, now=NOW)
        if operation == "negative":
            request = dispose_request(env, request["original_request"], registration)
    original = request["original_request"]
    before = rows(env)
    actual_write = external.sync_state._write
    def broken(directory, name, raw, **options):
        if name == external.CHECKPOINT_HEAD_NAME:
            raise RuntimeError("synthetic-second-head-publication-gap")
        return actual_write(directory, name, raw, **options)
    with monkeypatch.context() as patch:
        patch.setattr(external.sync_state, "_write", broken)
        with pytest.raises(RuntimeError, match="synthetic-second-head-publication-gap"):
            if operation == "registration":
                service.register(profile=env.profile, request=request, now=NOW)
            elif operation == "negative":
                service.dispose(profile=env.profile, request=request, now=NOW)
            else:
                with service.writer(profile=env.profile) as con:
                    with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                        pytest.fail("Effect was authorized before both heads were published")
    assert rows(env) == before
    assert (env.profile.database.parent / external.DB_HEAD_NAME).read_bytes() != (
        env.profile.checkpoints / external.CHECKPOINT_HEAD_NAME).read_bytes()
    wrong = deepcopy(request)
    wrong["request_id"] = str(uuid.uuid4())
    with pytest.raises(SyncError) as caught:
        if operation == "registration":
            service.register(profile=env.profile, request=wrong, now=NOW)
        elif operation == "negative":
            service.dispose(profile=env.profile, request=wrong, now=NOW)
        else:
            with service.writer(profile=env.profile,
                    resume_request=("effects", original["request_id"], "0" * 64)):
                pytest.fail("An unrelated effect selector repaired heads")
    assert caught.value.code == "SYNC_SOURCE_CONTINUITY_HEAD_INCOMPLETE"
    assert rows(env) == before
    if operation == "registration":
        result = service.register(profile=env.profile, request=request, now=NOW + timedelta(days=36500))
    elif operation == "negative":
        result = service.dispose(profile=env.profile, request=request, now=NOW + timedelta(days=36500))
    else:
        with service.writer(profile=env.profile,
                resume_request=("effects", original["request_id"], wire.digest(original))) as con:
            with pytest.raises(SyncError) as caught:
                with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                    pytest.fail("An exact repaired effect head authorized blind reexecution")
            assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_UNKNOWN"
        assert rows(env) == before
        return
    assert result["committed_at"] == STAMP
    assert rows(env)["sync_queue"] == before["sync_queue"]


def test_service_does_not_rehash_old_journals_inside_its_actual_sql_writer(env, monkeypatch):
    active = activated(env)
    request = register_request(env, active)
    registration = service.register(profile=env.profile, request=request, now=NOW)
    old_names = {path.name for path in (
        env.profile.database.parent / external.DIRECTORY / "journal").iterdir()}
    real_read, real_observe, real_verify = external.ContinuityGuard._read, external.ContinuityGuard.observe, external.ContinuityGuard._verify_pins
    sql_reads = []
    def reading(guard, directory, name, **options):
        owner = service._CURRENT.get()
        if owner is not None and owner.con.in_transaction and directory.path.name == "journal":
            assert name not in old_names, "The SQL writer re-read an old journal body"
            sql_reads.append(name)
        return real_read(guard, directory, name, **options)
    def observing(guard, **options):
        assert service._CURRENT.get() is None, "The SQL writer rescanned the external closure"
        return real_observe(guard, **options)
    def verifying(guard, *, full):
        assert not full or service._CURRENT.get() is None, "The SQL writer rehashed all pinned content"
        return real_verify(guard, full=full)
    monkeypatch.setattr(external.ContinuityGuard, "_read", reading)
    monkeypatch.setattr(external.ContinuityGuard, "observe", observing)
    monkeypatch.setattr(external.ContinuityGuard, "_verify_pins", verifying)
    service.register(profile=env.profile, request=request, now=NOW)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
            old_budget.apply_request(con, request["original_request"], now=NOW)
    service.dispose(profile=env.profile, request=dispose_request(env, request["original_request"], registration), now=NOW)
    assert sql_reads and not set(sql_reads) & old_names


def test_positive_disposition_cannot_change_the_core_bound_by_external_registration(env):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
            old_budget.apply_request(con, request["original_request"], now=NOW)
    body = dispose_request(env, request["original_request"])
    body["original"]["core_sha256"] = "9" * 64
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        service.dispose(profile=env.profile, request=body, now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_OPERATION_CONFLICT"
    assert rows(env) == before


def advance_cloud_epoch(env):
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE meta SET value='8' WHERE key='library_epoch'")
        con.execute("INSERT INTO sync_recovery_control(slot,state,generation,revision,source_floor_json,restore_operation_id,updated_at) VALUES(1,'running',1,1,NULL,NULL,?)", (STAMP,))
        con.commit()


@pytest.mark.parametrize("operation", ["registration_replay", "registration_gap", "negative_replay", "negative_gap", "positive_dispose", "positive_replay"])
def test_exact_stored_history_survives_actual_cloud_epoch_advance(env, monkeypatch, operation):
    active = activated(env)
    registered_body = register_request(env, active)
    target = schema.REGISTRATION if operation == "registration_gap" else schema.DISPOSITION
    real_insert = service._insert
    def broken(con, table, row):
        if table == target:
            raise RuntimeError("synthetic-historical-sql-gap")
        return real_insert(con, table, row)
    if operation == "registration_gap":
        with monkeypatch.context() as patch:
            patch.setattr(service, "_insert", broken)
            with pytest.raises(RuntimeError, match="synthetic-historical-sql-gap"):
                service.register(profile=env.profile, request=registered_body, now=NOW)
        request, expected = registered_body, None
    else:
        registered = service.register(profile=env.profile, request=registered_body, now=NOW)
        request = registered_body
        expected = registered
        if operation.startswith("positive"):
            with service.writer(profile=env.profile) as con:
                with service.before_effect(con, profile=env.profile, original_request=registered_body["original_request"], now=NOW):
                    original_receipt = old_budget.apply_request(con, registered_body["original_request"], now=NOW)
        if not operation.startswith("registration"):
            request = dispose_request(env, registered_body["original_request"], registered)
            expected = None
            if operation == "negative_gap":
                with monkeypatch.context() as patch:
                    patch.setattr(service, "_insert", broken)
                    with pytest.raises(RuntimeError, match="synthetic-historical-sql-gap"):
                        service.dispose(profile=env.profile, request=request, now=NOW)
            elif operation.endswith("replay"):
                expected = service.dispose(profile=env.profile, request=request, now=NOW)
    advance_cloud_epoch(env)
    before = rows(env)
    if operation.startswith("registration"):
        result = service.register(profile=env.profile, request=request, now=NOW + timedelta(days=36500))
    else:
        result = service.dispose(profile=env.profile, request=request, now=NOW + timedelta(days=36500))
    if expected is not None:
        assert result == {**expected, "replayed": True}
    elif operation.endswith("gap"):
        assert result["committed_at"] == STAMP
    if operation.startswith("positive"):
        assert result["original_receipt"] == original_receipt
        with service.writer(profile=env.profile, capability=False) as con:
            with service.before_effect(con, profile=env.profile, original_request=registered_body["original_request"], now=NOW + timedelta(days=36500)) as permit:
                assert permit.receipt == {**original_receipt, "replayed": True}
    assert rows(env)["sync_queue"] == before["sync_queue"]
    with storage_admin.connection(env.profile) as con:
        row = schema.validate_activation_record(con.execute("SELECT * FROM " + schema.ACTIVATION).fetchone())
        assert row["target_epoch"] == 7
        assert con.execute("SELECT value FROM meta WHERE key='library_epoch'").fetchone()[0] == "8"


def test_epoch_history_replay_does_not_authorize_first_negative_or_new_effect(env):
    active = activated(env)
    body = register_request(env, active)
    registered = service.register(profile=env.profile, request=body, now=NOW)
    negative = dispose_request(env, body["original_request"], registered)
    advance_cloud_epoch(env)
    before = rows(env)
    # An exact historical registration remains readable, but no tombstone or
    # effect for its missing original has yet been recorded in this contract.
    assert service.register(profile=env.profile, request=body, now=NOW)["replayed"] is True
    with pytest.raises(SyncError) as caught:
        service.dispose(profile=env.profile, request=negative, now=NOW)
    assert caught.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED"
    with service.writer(profile=env.profile) as con, pytest.raises(SyncError) as caught:
        with service.before_effect(con, profile=env.profile, original_request=body["original_request"], now=NOW):
            pytest.fail("An old registration authorized a fresh epoch effect")
    assert caught.value.code == "SYNC_SOURCE_READMIT_CONTINUITY_REQUIRED"
    with pytest.raises(SyncError):
        service.register(profile=env.profile, request=register_request(env, active), now=NOW)
    assert rows(env) == before
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        observed = guard.observe()
        assert not observed["entries"]["effects"] and not observed["entries"]["tombstones"]


def test_leaf_gate_requires_a_live_before_effect_frame_even_inside_actual_writer(env):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    before = rows(env)
    with service.writer(profile=env.profile) as con:
        with pytest.raises(SyncError) as caught:
            service.require_effect(con, original)
        assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_REQUIRED"
    assert rows(env) == before


def test_leaf_gate_allows_only_exact_body_during_its_fresh_frame_and_expires_after_yield(env):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            assert service.require_effect(con, deepcopy(original)) is None
            receipt = old_budget.apply_request(con, original, now=NOW)
            assert service.require_effect(con, original) == {**receipt, "replayed": True}
            captured = copy_context()
        with pytest.raises(SyncError) as caught:
            service.require_effect(con, original)
        assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_REQUIRED"
        with pytest.raises(SyncError) as caught:
            captured.run(service.require_effect, con, original)
        assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_REQUIRED"
    with pytest.raises(SyncError) as caught:
        service.require_effect(con, original)
    assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"


@pytest.mark.parametrize("field", ["request_id", "retry_at", "claim"])
def test_leaf_gate_cannot_borrow_an_effect_frame_for_another_uuid_or_body(env, field):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    changed = deepcopy(original)
    if field == "retry_at":
        changed[field] = "2098-01-02T00:00:00.000Z"
    elif field == "claim":
        changed[field]["lease_token"] = str(uuid.uuid4())
    else:
        changed[field] = str(uuid.uuid4())
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            with pytest.raises(SyncError) as caught:
                service.require_effect(con, changed)
            assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_BODY_MISMATCH"
            assert service.require_effect(con, original) is None
            old_budget.apply_request(con, original, now=NOW)


def test_leaf_gate_cannot_escape_actual_connection_or_a_copied_context_thread(env):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    foreign = migrations._open(env.profile.database)
    errors = []
    try:
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                with pytest.raises(SyncError) as caught:
                    service.require_effect(foreign, original)
                assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"
                def use():
                    try:
                        service.require_effect(con, original)
                        errors.append("authorized")
                    except SyncError as exc:
                        errors.append(exc.code)
                copied = copy_context()
                thread = threading.Thread(target=lambda: copied.run(use))
                thread.start()
                thread.join(timeout=10)
                assert not thread.is_alive()
                assert errors == ["SYNC_SOURCE_READMIT_WRITER_REQUIRED"]
                old_budget.apply_request(con, original, now=NOW)
    finally:
        foreign.close()


def test_leaf_gate_positive_frame_cannot_turn_a_deleted_receipt_into_a_fresh_effect(env):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
            receipt = old_budget.apply_request(con, original, now=NOW)
    before = rows(env)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=original, now=NOW) as permit:
            permit.receipt.clear()  # Caller mutation does not change the frame.
            assert service.require_effect(con, original) == {**receipt, "replayed": True}
        with pytest.raises(SyncError) as caught:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                con.execute("DELETE FROM sync_queue_budget_receipt WHERE request_id=?", (original["request_id"],))
                service.require_effect(con, original)
        assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_RECEIPT_REQUIRED"
    assert rows(env) == before


@pytest.mark.parametrize("ending", ["-- leading comment\n COMMIT", "/* leading comment */ END", " /* first */ -- second\n ROLLBACK"])
def test_leaf_gate_does_not_survive_actual_transaction_replacement_hidden_by_comments(env, ending):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                con.execute(ending)
                con.execute("-- replacement\n /* same boolean */ BEGIN IMMEDIATE")
                assert con.in_transaction
                service.require_effect(con, original)
    assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"
    assert rows(env) == before


def test_leaf_gate_rejects_a_nested_frame_before_recording_another_effect(env):
    active = activated(env)
    request = register_request(env, active)
    other = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    service.register(profile=env.profile, request=other, now=NOW)
    with service.writer(profile=env.profile) as con:
        with service.before_effect(con, profile=env.profile, original_request=request["original_request"], now=NOW):
            with pytest.raises(SyncError) as caught:
                with service.before_effect(con, profile=env.profile, original_request=other["original_request"], now=NOW):
                    pytest.fail("A nested frame borrowed source permission")
            assert caught.value.code == "SYNC_SOURCE_READMIT_EFFECT_REQUIRED"
            old_budget.apply_request(con, request["original_request"], now=NOW)
    with lifecycle.shared(env.profile), external.locked(env.profile) as guard:
        observed = guard.observe()
        assert set(observed["entries"]["effects"]) == {request["original_request"]["request_id"]}


def assert_no_continuity_facilities(profile):
    assert not any((profile.database.parent / name).exists() for name in (
        external.DIRECTORY, external.INTENT_NAME, external.DB_HEAD_NAME, external.LOCK_NAME))
    assert not any((profile.checkpoints / name).exists() for name in (external.SEAL_NAME, external.CHECKPOINT_HEAD_NAME))


@pytest.mark.parametrize("kind", ["budget_defer", "budget_resume"])
def test_unactivated17_reads_only_actual_legacy16_positive_without_leaf_or_file_effects(env, monkeypatch, kind):
    original = env.legacy if kind == "budget_defer" else env.legacy_resume
    with storage_admin.connection(env.profile) as con:
        expected = old_budget._receipt(con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (original["request_id"],)).fetchone(), con=con)[1]
    before = rows(env)
    actual_open = migrations._open
    opened = []
    forbidden = {sqlite3.SQLITE_INSERT, sqlite3.SQLITE_UPDATE, sqlite3.SQLITE_DELETE,
                 sqlite3.SQLITE_CREATE_TABLE, sqlite3.SQLITE_DROP_TABLE, sqlite3.SQLITE_ALTER_TABLE}
    def reading(path, **options):
        assert options == {"readonly": True}
        con = actual_open(path, **options)
        opened.append(lifecycle._key(path))
        def authorize(action, first, second, database, context):
            assert action not in forbidden, "Legacy history attempted a SQL write"
            return sqlite3.SQLITE_OK
        con.set_authorizer(authorize)
        return con
    def forbidden_call(*args, **kwargs):
        pytest.fail("Legacy history attempted a leaf effect or external facility")
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_open", reading)
        patch.setattr(external, "locked", forbidden_call)
        patch.setattr(old_budget, "apply_request", forbidden_call)
        patch.setattr(old_source, "apply_request", forbidden_call)
        result = service.legacy_receipt(profile=env.profile, original_request=original)
    assert result == {**expected, "replayed": True}
    assert opened == [lifecycle._key(env.profile.database)]
    assert rows(env) == before
    assert_no_continuity_facilities(env.profile)


@pytest.mark.parametrize("env", ["legacy_source_fail"], indirect=True)
def test_unactivated17_reads_the_actual_legacy16_source_failure_receipt(env):
    before = rows(env)
    assert env.legacy_source["source"]["handshake"]["migration_version"] == 16
    result = service.legacy_receipt(profile=env.profile, original_request=env.legacy_source)
    assert result == {**env.legacy_source_receipt, "replayed": True}
    assert rows(env) == before
    assert_no_continuity_facilities(env.profile)


def test_missing_legacy_receipt_is_none_without_negative_proof_or_fresh_effect(env):
    original = deepcopy(env.legacy)
    original["request_id"] = str(uuid.uuid4())
    before = rows(env)
    assert service.legacy_receipt(profile=env.profile, original_request=original) is None
    with pytest.raises(SyncError):
        with service.writer(profile=env.profile):
            pytest.fail("Missing old receipt initialized fresh continuity")
    assert rows(env) == before
    assert_no_continuity_facilities(env.profile)


def test_new17_body_never_uses_the_legacy_sql_fallback(env, monkeypatch):
    original = bound(env, sample.old_request())
    before = rows(env)
    def forbidden(*args, **kwargs):
        pytest.fail("A new17 request attempted legacy SQL fallback")
    with monkeypatch.context() as patch:
        patch.setattr(migrations, "_open", forbidden)
        assert service.legacy_receipt(profile=env.profile, original_request=original) is None
    assert rows(env) == before
    assert_no_continuity_facilities(env.profile)


@pytest.mark.parametrize("table", [schema.REGISTRATION, schema.DISPOSITION])
def test_legacy_sql_fallback_refuses_same_original_uuid_in_new_control_metadata(env, table):
    active = activated(env)
    if table == schema.REGISTRATION:
        request = sample.register(env.legacy)
        request.update(source=deepcopy(env.legacy["source"]), target=deepcopy(env.target),
            recovery_generation=0, recovery_stream={"library_id": env.target["library_id"], "library_epoch": 7}, control_revision=0)
        request["continuity"] = {"format_version": 1, "continuity_id": active["continuity_id"],
            "contract_sha256": external._digest(active), "target_epoch": 7}
        result = sample.registration(request)
    else:
        request = dispose_request(env, env.legacy)
        result = service._disposition_result(request, env.legacy_receipt, NOW)
    with storage_admin.connection(env.profile) as con:
        # Controlled representation fixture, not a legitimate registration or
        # disposition producer: its mere presence forbids the legacy shortcut.
        service._insert(con, table, service._control_record(request, result))
        con.commit()
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        service.legacy_receipt(profile=env.profile, original_request=env.legacy)
    assert caught.value.code == "SYNC_SOURCE_READMIT_OPERATION_CONFLICT"
    assert rows(env) == before


@pytest.mark.parametrize("ending,reopening", [
    (";ROLLBACK", ";BEGIN IMMEDIATE"),
    ("\ufeffROLLBACK", "\ufeffBEGIN IMMEDIATE"),
    ("/* before */; \ufeffROLLBACK", "; -- after\n\ufeff/* before begin */ BEGIN IMMEDIATE"),
])
def test_leaf_gate_rejects_real_transaction_replacement_with_empty_statement_and_bom_prefix(env, ending, reopening):
    active = activated(env)
    request = register_request(env, active)
    service.register(profile=env.profile, request=request, now=NOW)
    original = request["original_request"]
    before = rows(env)
    with pytest.raises(SyncError) as caught:
        with service.writer(profile=env.profile) as con:
            with service.before_effect(con, profile=env.profile, original_request=original, now=NOW):
                con.execute(ending)
                con.execute(reopening)
                assert con.in_transaction
                try:
                    service.require_effect(con, original)
                except SyncError:
                    raise
                else:
                    pytest.fail("The new SQLite transaction retained an old exact effect grant")
    assert caught.value.code == "SYNC_SOURCE_READMIT_WRITER_REQUIRED"
    assert rows(env) == before
