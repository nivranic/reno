"""Actual isolated personal-mirror receiver preparation and installation.

Source facts use original personal APIs and committed capture; receiver pair,
session, node identity and facilities use their explicit production producers.
No accepted mirror, stored attachment or successful receipt is seeded. HTTP
transport/routes, cloud reading capability, handoff and sharing are out of scope.
"""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import json
import sqlite3
import uuid

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from reno import config, db, personal, personal_schema, personal_mirror_snapshot as capture
from reno import personal_mirror_contract as facts, personal_mirror_transfer as runtime
from reno import personal_mirror_transfer_contract as wire, personal_mirror_transfer_schema as schema
from reno import personal_mirror_transfer_files as files
from reno import storage_lifecycle as lifecycle, sync_protocol, sync_queue as queue
from reno.sync_protocol import SyncError

NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)
BODY = b"%PDF-1.4\nactual synthetic source upload\n%%EOF\n"


def uid():
    return str(uuid.uuid4())


@pytest.fixture
def mirror_env(tmp_path, monkeypatch):
    source_root, target_root = tmp_path / "home", tmp_path / "cloud"
    def select(root, role, *, enabled=True):
        data = root / "data"
        data.mkdir(parents=True, exist_ok=True)
        local = root / "config.synthetic.json"
        local.write_text(json.dumps({"node_role": role, "personal_features_enabled": True,
            "field_checks_enabled": True, "personal_mirror_enabled": enabled}), encoding="utf-8")
        monkeypatch.setenv("RENO_DATA_DIR", str(data))
        monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
        monkeypatch.delenv("RENO_PERSONAL_MIRROR_ENABLED", raising=False)
        for name, path in {"DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
                "FRAMES": root / "media/frames", "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames_cache"}.items():
            monkeypatch.setattr(config, name, path)
        monkeypatch.setattr(config, "_cache", None)
        return data
    source_data = select(source_root, "home")
    source_path = source_data / "reno.db"
    db.initialize(source_path)
    source_profile = lifecycle.StorageProfile(source_path, source_root / "media", source_data / "generated", source_data / "sync-checkpoints")
    lifecycle.initialize(source_profile)
    con = db.connect(source_path)
    metadata = dict(con.execute("SELECT key,value FROM meta WHERE key IN ('library_id','default_project_id','primary_epoch')"))
    source_node = personal_schema.node_identity(source_data)
    source_identity = {"library_id": metadata["library_id"], "node_id": source_node,
        "handshake": sync_protocol.schema_handshake(con)}
    con.close()
    created, _ = personal.create_source(metadata["default_project_id"], {"operation_id": uid(),
        "primary_epoch": int(metadata["primary_epoch"]), "changes": {"source_type": "manual_note", "title": "绍兴合成核对资料"}})
    source_id = created["source"]["source_id"]
    target_data = select(target_root, "cloud")
    target_path = target_data / "reno.db"
    db.initialize(target_path)
    profile = lifecycle.StorageProfile(target_path, target_root / "media", target_data / "generated", target_data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with lifecycle.shared(profile):
        con = db.connect(target_path)
        con.execute("BEGIN IMMEDIATE")
        schema.install(con, profile=profile)
        queue.bind_peer(con, source_identity, now=NOW)
        lease = queue.heartbeat(con, source_identity, uid(), now=NOW)["lease"]
        con.commit()
        receiver = {"library_id": db.knowledge_version(con)["library_id"],
                    "node_id": personal_schema.node_identity(target_data)}
        con.close()
    env = {"source_root": source_root, "source_data": source_data, "source_path": source_path,
        "source_profile": source_profile, "target_root": target_root, "data": target_data,
        "profile": profile, "source": source_identity, "lease": lease, "receiver": receiver,
        "project_id": metadata["default_project_id"], "source_id": source_id,
        "primary_epoch": int(metadata["primary_epoch"]), "select": select}
    env["prepare"] = make_prepare(env)
    return env


def make_prepare(env, *, with_attachment=False, previous=None, expected_revision=0):
    """Capture actual source facts; optional ready file uses original upload API."""
    env["select"](env["source_root"], "home")
    if with_attachment:
        from app.personal_api import router
        app = FastAPI()
        app.include_router(router)
        with TestClient(app) as client:
            answer = client.post(f"/api/sources/{env['source_id']}/attachments",
                headers={"X-Operation-Id": uid(), "X-Primary-Epoch": str(env["primary_epoch"])},
                files={"file": ("synthetic.pdf", BODY, "application/pdf")})
            assert answer.status_code in {200, 201}, answer.text
    snapshot = capture.capture_snapshot(snapshot_id=uid(), expected_library_id=env["source"]["library_id"],
        expected_primary_id=env["source"]["node_id"], expected_epoch=env["primary_epoch"]).snapshot
    env["select"](env["target_root"], "cloud")
    return {"format_version": 1, "kind": "personal_mirror_prepare", "operation_id": uid(),
        "snapshot_id": snapshot.snapshot_id, "snapshot_sha256": snapshot.snapshot_sha256,
        "snapshot_json": snapshot.snapshot_json,
        "source": {"library_id": env["source"]["library_id"], "node_id": env["source"]["node_id"],
            "personal_primary_id": env["source"]["node_id"], "primary_epoch": env["primary_epoch"]},
        "receiver": deepcopy(env["receiver"]), "previous": previous, "expected_revision": expected_revision}


def arguments(env):
    return {"profile": env["profile"], "source": env["source"], "lease": env["lease"], "now": NOW}


def commit_selector(prepare):
    return {"format_version": 1, "kind": "personal_mirror_commit", "operation_id": prepare["operation_id"],
            "prepare_sha256": wire.prepare_sha256(prepare)}


def table_state(env):
    con = db.connect(env["profile"].database)
    try:
        result = {table: [tuple(row) for row in con.execute(f'SELECT * FROM main."{table}" ORDER BY rowid')]
            for table in (*facts.TABLE_NAMES, *schema.TABLES)}
        result["meta"] = [tuple(row) for row in con.execute("SELECT * FROM meta ORDER BY key")]
        return result
    finally:
        con.close()


def test_actual_prepare_freezes_zero_attachment_parent_without_personal_install(mirror_env):
    env = mirror_env
    before = table_state(env)
    with lifecycle.shared(env["profile"]):
        result = runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        repeated = runtime.prepare_mirror_locked(**arguments(env), prepare=deepcopy(env["prepare"]))
        original = runtime.original_prepare_locked(**arguments(env), operation_id=env["prepare"]["operation_id"],
            expected_prepare_sha256=wire.prepare_sha256(env["prepare"]))
    assert original == env["prepare"] and repeated == result
    assert result["attachment_count"] == 0 and result["state"] == "prepared"
    after = table_state(env)
    assert len(after[schema.INTENT]) == 1 and after[schema.RECEIPT] == after[schema.HEAD] == []
    for table in (*facts.TABLE_NAMES, "meta"):
        assert after[table] == before[table]


def test_actual_zero_attachment_commit_preserves_receiver_identity_and_raw_source_facts(mirror_env):
    env = mirror_env
    node_before = (env["data"] / "node-identity.json").read_bytes()
    con = db.connect(env["profile"].database)
    knowledge_before = db.knowledge_version(con)
    con.close()
    selected = commit_selector(env["prepare"])
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        result = runtime.commit_mirror_locked(**arguments(env), commit=selected)
        observed = runtime.accepted_mirror_facts_locked(**arguments(env))
        replay = runtime.commit_mirror_locked(**arguments(env), commit=selected)
        persisted = runtime.mirror_receipt_locked(**arguments(env), commit=selected)
    assert wire.normalize_receipt(env["prepare"], result) == result
    assert replay == persisted == {**result, "replayed": True}
    assert observed["snapshot"].snapshot_bytes == env["prepare"]["snapshot_json"].encode()
    assert observed["head"]["revision"] == 1
    con = db.connect(env["profile"].database)
    try:
        document = facts.validate_snapshot(env["prepare"]["snapshot_json"]).document
        for table in facts.TABLE_NAMES:
            columns = [item["name"] for item in document["schema"]["tables"][table]["columns"]]
            order = ",".join('"' + name + '"' for name in document["schema"]["tables"][table]["order_by"])
            rows = [dict(zip(columns, tuple(row))) for row in con.execute(f'SELECT * FROM "{table}" ORDER BY {order}')]
            assert rows == document["tables"][table]
        assert db.knowledge_version(con) == knowledge_before
        assert personal.capabilities(con)["personal_write"] is False
        assert personal.capabilities(con)["personal_read"] is False  # no cloud UI capability enabled
    finally:
        con.close()
    assert (env["data"] / "node-identity.json").read_bytes() == node_before


def test_actual_uploaded_attachment_must_be_stored_before_commit(mirror_env):
    env = mirror_env
    prepared = make_prepare(env, with_attachment=True)
    selected = commit_selector(prepared)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=prepared)
        before = table_state(env)
        with pytest.raises(SyncError):
            runtime.commit_mirror_locked(**arguments(env), commit=selected)
        assert table_state(env) == before
        descriptor = wire.attachment_manifest(prepared)[0]
        files.store_attachment_locked(**arguments(env), operation_id=prepared["operation_id"],
            expected_prepare_sha256=selected["prepare_sha256"], attachment_id=descriptor["attachment_id"], chunks=[BODY])
        result = runtime.commit_mirror_locked(**arguments(env), commit=selected)
    assert result["counts"]["attachment_manifest"] == 1
    assert (env["data"] / "personal-attachments" / descriptor["storage_key"]).read_bytes() == BODY


def test_failed_commit_rolls_back_exact_personal_meta_head_and_receipt(mirror_env, monkeypatch):
    env = mirror_env
    selected = commit_selector(env["prepare"])
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        before = table_state(env)
        failure = RuntimeError("synthetic SQL installation failure")
        actual = runtime._install_personal_rows
        def interrupted(con, document, *, check_cancelled):
            assert runtime.in_write_window()
            actual(con, document, check_cancelled=check_cancelled)
            raise failure
        monkeypatch.setattr(runtime, "_install_personal_rows", interrupted)
        with pytest.raises(RuntimeError) as caught:
            runtime.commit_mirror_locked(**arguments(env), commit=selected)
        assert caught.value is failure
        assert table_state(env) == before
        assert runtime.in_write_window() is False


def test_next_snapshot_uses_actual_predecessor_and_refuses_changed_accepted_rows(mirror_env):
    env = mirror_env
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        first = runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
    next_prepare = make_prepare(env, previous={"snapshot_id": first["snapshot_id"],
        "snapshot_sha256": first["snapshot_sha256"]}, expected_revision=1)
    con = db.connect(env["profile"].database)
    con.execute("UPDATE renovation_project SET name='offline changed mirror'")
    con.commit()
    con.close()
    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError, match="mirror facts"):
        runtime.prepare_mirror_locked(**arguments(env), prepare=next_prepare)
    assert table_state(env) == before


@pytest.mark.parametrize("damage", ["personal-row", "local-primary", "wrong-lease", "disabled"])
def test_first_prepare_never_wipes_existing_personal_or_bypasses_live_gate(mirror_env, damage):
    env = mirror_env
    args = arguments(env)
    con = db.connect(env["profile"].database)
    if damage == "personal-row":
        con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
                    (uid(), "receiver private project", "2026-10-01", "2026-10-01"))
    elif damage == "local-primary":
        con.execute("UPDATE meta SET value=? WHERE key='personal_primary_id'", (env["receiver"]["node_id"],))
    elif damage == "wrong-lease":
        args["lease"] = {**args["lease"], "lease_token": uid()}
    else:
        local = env["target_root"] / "config.synthetic.json"
        content = json.loads(local.read_text(encoding="utf-8"))
        content["personal_mirror_enabled"] = False
        local.write_text(json.dumps(content), encoding="utf-8")
    con.commit()
    con.close()
    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        runtime.prepare_mirror_locked(**args, prepare=env["prepare"])
    assert table_state(env) == before


def test_same_operation_changed_receiver_or_commit_hash_cannot_replay(mirror_env):
    env = mirror_env
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        before = table_state(env)
        changed = deepcopy(env["prepare"])
        changed["receiver"]["node_id"] = uid()
        with pytest.raises(SyncError):
            runtime.prepare_mirror_locked(**arguments(env), prepare=changed)
        with pytest.raises(SyncError):
            runtime.commit_mirror_locked(**arguments(env), commit={**commit_selector(env["prepare"]), "prepare_sha256": "a" * 64})
        assert table_state(env) == before


def test_plain_request_does_not_create_missing_operational_facilities(mirror_env):
    env = mirror_env
    con = db.connect(env["profile"].database)
    con.execute("PRAGMA foreign_keys=OFF")
    for trigger in [row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND name LIKE 'trg_personal_mirror_%'")]:
        con.execute(f'DROP TRIGGER "{trigger}"')
    for table in reversed(tuple(schema.TABLES)):
        con.execute(f'DROP TABLE "{table}"')
    con.commit()
    con.close()
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
    con = db.connect(env["profile"].database)
    assert con.execute("SELECT name FROM sqlite_master WHERE name=?", (schema.INTENT,)).fetchone() is None
    con.close()


def test_successive_actual_source_revision_installs_atomically_and_old_receipt_stays_historical(mirror_env):
    env = mirror_env
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        first = runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
    env["select"](env["source_root"], "home")
    personal.update_project(env["project_id"], {"operation_id": uid(), "primary_epoch": env["primary_epoch"],
        "base_revision": 1, "changes": {"name": "实际来源第二版"}})
    prepared = make_prepare(env, previous={"snapshot_id": first["snapshot_id"],
        "snapshot_sha256": first["snapshot_sha256"]}, expected_revision=1)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=prepared)
        second = runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(prepared))
        old = runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
        accepted = runtime.accepted_mirror_facts_locked(**arguments(env))
    assert second["installed_revision"] == 2
    assert old == {**first, "replayed": True}
    assert accepted["head"]["revision"] == 2
    assert accepted["snapshot"].snapshot_sha256 == prepared["snapshot_sha256"]
    assert accepted["snapshot"].document["tables"]["renovation_project"][0]["name"] == "实际来源第二版"


def test_prepared_competing_operation_cannot_install_after_another_predecessor_wins(mirror_env):
    env = mirror_env
    competing = make_prepare(env)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        runtime.prepare_mirror_locked(**arguments(env), prepare=competing)
        runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
        before = table_state(env)
        with pytest.raises(SyncError, match="mirror facts"):
            runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(competing))
        assert table_state(env) == before


def test_concurrent_receiver_write_between_full_read_and_short_writer_is_preserved(mirror_env, monkeypatch):
    env = mirror_env
    actual = runtime._begin_writer
    private_project = uid()
    def competing(connection):
        con = db.connect(env["profile"].database)
        con.execute("INSERT INTO renovation_project(project_id,name,created_at,updated_at) VALUES(?,?,?,?)",
            (private_project, "new concurrent receiver fact", "2026-10-01", "2026-10-01"))
        con.commit()
        con.close()
        return actual(connection)
    monkeypatch.setattr(runtime, "_begin_writer", competing)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
    con = db.connect(env["profile"].database)
    assert con.execute("SELECT project_id FROM renovation_project").fetchone()[0] == private_project
    assert con.execute(f'SELECT count(*) FROM "{schema.INTENT}"').fetchone()[0] == 0
    con.close()
    assert runtime.in_write_window() is False


def test_large_snapshot_and_schema_validators_never_run_in_short_receiver_writer(mirror_env, monkeypatch):
    env = mirror_env
    snapshot_validator, schema_validator = facts.validate_snapshot, schema.validate
    def snapshot_outside(*args, **kwargs):
        assert not runtime.in_write_window()
        return snapshot_validator(*args, **kwargs)
    def schema_outside(*args, **kwargs):
        assert not runtime.in_write_window()
        return schema_validator(*args, **kwargs)
    monkeypatch.setattr(facts, "validate_snapshot", snapshot_outside)
    monkeypatch.setattr(schema, "validate", schema_outside)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        result = runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
    assert result["installed_revision"] == 1


def test_unrelated_live_heartbeat_during_full_file_check_does_not_change_personal_cas(mirror_env, monkeypatch):
    env = mirror_env
    actual = files.PreparedAttachmentHold.verify_source
    renewed = False
    def with_heartbeat(held):
        nonlocal renewed
        actual(held)
        if not renewed:
            renewed = True
            con = db.connect(env["profile"].database)
            try:
                queue.heartbeat(con, env["source"], env["lease"]["session_id"], lease=env["lease"],
                    now=NOW + timedelta(seconds=1))
                con.commit()
            finally:
                con.close()
    monkeypatch.setattr(files.PreparedAttachmentHold, "verify_source", with_heartbeat)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        result = runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
    assert renewed and result["installed_revision"] == 1


def test_accepted_empty_meta_changed_during_full_check_is_not_adopted(mirror_env, monkeypatch):
    env = mirror_env
    actual = files.PreparedAttachmentHold.verify_source
    changed = False
    def remove_original_epoch(held):
        nonlocal changed
        actual(held)
        if not changed:
            changed = True
            con = db.connect(env["profile"].database)
            con.execute("DELETE FROM meta WHERE key='primary_epoch'")
            con.commit()
            con.close()
    monkeypatch.setattr(files.PreparedAttachmentHold, "verify_source", remove_original_epoch)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        with pytest.raises(SyncError):
            runtime.commit_mirror_locked(**arguments(env), commit=commit_selector(env["prepare"]))
    con = db.connect(env["profile"].database)
    try:
        assert con.execute("SELECT value FROM meta WHERE key='primary_epoch'").fetchone() is None
        assert con.execute("SELECT count(*) FROM renovation_project").fetchone()[0] == 0
        assert con.execute(f'SELECT count(*) FROM "{schema.RECEIPT}"').fetchone()[0] == 0
        assert con.execute(f'SELECT state FROM "{schema.INTENT}"').fetchone()[0] == "prepared"
    finally:
        con.close()


def test_callback_cannot_adopt_new_same_peer_session_generation(mirror_env):
    env = mirror_env
    changed = False
    original_lease = deepcopy(env["lease"])
    def replace_external_lease():
        nonlocal changed
        if changed:
            return
        changed = True
        con = db.connect(env["profile"].database)
        try:
            replacement = queue.heartbeat(con, env["source"], env["lease"]["session_id"],
                now=NOW + timedelta(seconds=601))["lease"]
            con.commit()
        finally:
            con.close()
        env["lease"].clear()
        env["lease"].update(replacement)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"],
            check_cancelled=replace_external_lease)
    assert changed and env["lease"]["generation"] > original_lease["generation"]
    assert table_state(env)[schema.INTENT] == []


def test_post_commit_cancellation_preserves_original_receipt_and_can_recover(mirror_env):
    env = mirror_env
    selected = commit_selector(env["prepare"])
    failure = RuntimeError("synthetic cancellation after actual COMMIT")
    def cancel_after_commit():
        con = db.connect(env["profile"].database)
        try:
            committed = con.execute(f'SELECT 1 FROM "{schema.RECEIPT}" WHERE operation_id=?',
                (env["prepare"]["operation_id"],)).fetchone()
        finally:
            con.close()
        if committed is not None:
            raise failure
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=env["prepare"])
        with pytest.raises(RuntimeError) as caught:
            runtime.commit_mirror_locked(**arguments(env), commit=selected, check_cancelled=cancel_after_commit)
        assert caught.value is failure
        assert runtime.in_write_window() is False
        recovered = runtime.mirror_receipt_locked(**arguments(env), commit=selected)
        replay = runtime.commit_mirror_locked(**arguments(env), commit=selected)
        accepted = runtime.accepted_mirror_facts_locked(**arguments(env))
    assert recovered == replay and recovered["replayed"] is True
    assert recovered["snapshot_sha256"] == env["prepare"]["snapshot_sha256"]
    assert accepted["head"]["receipt_sha256"] == recovered["receipt_sha256"]
    assert accepted["snapshot"].snapshot_bytes == env["prepare"]["snapshot_json"].encode()
