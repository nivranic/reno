"""Prospective17 actual read-only probes, activation CAS and authenticated SDK."""
from copy import deepcopy
import hashlib
import json
import sqlite3
import uuid

import httpx
import pytest

from reno import cli, migrations, storage_admin
from reno import sync_source_continuity as external, sync_source_continuity_admin as admin
from reno import sync_source_continuity_status_contract as contract
from reno import sync_source_readmit_schema as schema
from reno.sync_protocol import SyncError
from reno.sync_client import SyncClient, SyncClientError
from tests.test_sync_source_continuity_service import env, activated, STAMP, rows
from tests.test_sync_source_recovery_api import http, sdk, TOKEN


def query(env):
    return {"kind": "source_continuity", "source": deepcopy(env.source), "target": deepcopy(env.target)}


def files(env):
    return {str(path.relative_to(env.profile.database.parent.parent)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in env.profile.database.parent.parent.rglob("*") if path.is_file()}


def cli_paths(env):
    args = []
    for key, value in (("db", env.profile.database), ("media", env.profile.media),
                       ("reports", env.profile.reports), ("checkpoints", env.profile.checkpoints)):
        args += ["--" + key, str(value)]
    return args


def test_absent_probe_and_preview_are_read_only_and_do_not_create_any_facility(env, monkeypatch):
    before, sql = files(env), rows(env)
    monkeypatch.setattr(admin.service, "writer", lambda **kw: pytest.fail("probe acquired a writer"))
    observed = admin.status(profile=env.profile, request=query(env))
    assert observed["state"] == "absent" and observed["continuity"] is None and observed["head"] is None
    proposed = admin.preview(profile=env.profile)
    assert {k: v for k, v in proposed.items() if k != "activation_basis_sha256"} == observed
    assert len(proposed["activation_basis_sha256"]) == 64
    assert rows(env) == sql and files(env) == before


def test_private_image_authorizer_denies_control_bodies_and_all_writes(env, monkeypatch):
    original = admin._projection
    before = files(env)
    def checked(con):
        for table, column in ((schema.ACTIVATION, "contract_json"), (schema.REGISTRATION, "request_json"),
                              (schema.DISPOSITION, "original_request_json"), (schema.ADMISSION, "result_json")):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute('SELECT "' + column + '" FROM "' + table + '"').fetchall()
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("UPDATE meta SET value='99' WHERE key='library_epoch'")
        return original(con)
    monkeypatch.setattr(admin, "_projection", checked)
    assert admin.status(profile=env.profile, request=query(env))["state"] == "absent"
    assert files(env) == before


def test_query_detects_committed_source_change_without_blocking_or_reverting_writer(env, monkeypatch):
    original = admin._projection
    def changed(con):
        result = original(con)
        with storage_admin.connection(env.profile) as writable:
            writable.execute("UPDATE meta SET value='8' WHERE key='library_epoch'")
            writable.commit()
        return result
    monkeypatch.setattr(admin, "_projection", changed)
    with pytest.raises(SyncError) as error:
        admin.status(profile=env.profile, request=query(env))
    assert error.value.code == "SYNC_SOURCE_READMIT_BUSY"
    with storage_admin.connection(env.profile) as con:
        assert con.execute("SELECT value FROM meta WHERE key='library_epoch'").fetchone()[0] == "8"


def test_query_pins_actual_node_identity_until_response_is_complete(env, monkeypatch):
    original = admin._projection
    def changed(con):
        result = original(con)
        (env.profile.database.parent / "node-identity.json").write_text(
            json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
        return result
    monkeypatch.setattr(admin, "_projection", changed)
    with pytest.raises(SyncError) as error:
        admin.status(profile=env.profile, request=query(env))
    assert error.value.code == "SYNC_SOURCE_READMIT_BUSY"


@pytest.mark.parametrize("damage,code", [("node", "SYNC_SOURCE_READMIT_PEER_MISMATCH"),
                                        ("schema", "SYNC_SOURCE_READMIT_SCHEMA_REQUIRED")])
def test_actual_local_damage_has_structured_error_and_does_not_become_bad_caller_request(env, http, capsys, damage, code):
    if damage == "node":
        (env.profile.database.parent / "node-identity.json").write_bytes(b"{")
    else:
        with storage_admin.connection(env.profile) as con:
            con.execute("DROP TRIGGER sync_source_control_registration_no_delete")
            con.commit()
    before = files(env)
    response = http.post("/api/sync/source/continuity", json=query(env), headers={"X-Sync-Token": TOKEN})
    assert response.status_code == 409 and response.json()["code"] == code
    assert cli.main(["sync-source-continuity", *cli_paths(env)]) == 2
    output = capsys.readouterr()
    assert output.out == "" and json.loads(output.err)["code"] == code
    assert files(env) == before


def test_actual_activation_uses_preview_basis_and_exact_original_replay(env):
    preview = admin.preview(profile=env.profile)
    identifier = str(uuid.uuid4())
    actual = admin.activate(profile=env.profile, continuity_id=identifier, activated_at=STAMP,
        expected_basis_sha256=preview["activation_basis_sha256"])
    before, sql = files(env), rows(env)
    assert admin.activate(profile=env.profile, continuity_id=identifier, activated_at=STAMP,
        expected_basis_sha256=preview["activation_basis_sha256"]) == actual
    status = admin.status(profile=env.profile, request=query(env))
    assert status["state"] == "current" and status["continuity"] == {
        "format_version": 1, "continuity_id": identifier,
        "contract_sha256": external._digest(actual), "target_epoch": 7}
    assert status["head"]["ordinal"] == 0
    assert rows(env) == sql and files(env) == before


def test_activation_stale_actual_profile_peer_epoch_basis_is_rejected_before_publication(env):
    basis = admin.preview(profile=env.profile)["activation_basis_sha256"]
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE meta SET value='8' WHERE key='library_epoch'")
        con.commit()
    before = rows(env)
    with pytest.raises(SyncError) as error:
        admin.activate(profile=env.profile, continuity_id=str(uuid.uuid4()), activated_at=STAMP,
                       expected_basis_sha256=basis)
    assert error.value.code == "SYNC_SOURCE_READMIT_CONTROL_CHANGED"
    assert rows(env) == before
    assert not (env.profile.database.parent / external.DB_HEAD_NAME).exists()
    assert not (env.profile.checkpoints / external.CHECKPOINT_HEAD_NAME).exists()


def test_historical_activation_remains_fact_and_cannot_be_reported_current(env):
    actual = activated(env)
    with storage_admin.connection(env.profile) as con:
        con.execute("UPDATE meta SET value='8' WHERE key='library_epoch'")
        con.commit()
    before = files(env)
    result = admin.status(profile=env.profile, request=query(env))
    assert result["state"] == "historical"
    assert result["receiver_version"]["library_epoch"] == 8
    assert result["continuity"]["target_epoch"] == actual["target_epoch"] == 7
    assert files(env) == before


@pytest.mark.parametrize("damage", ["missing_head", "bad_head", "unknown_file"])
def test_partial_or_damaged_facility_is_unknown_and_never_repaired(env, damage):
    activated(env)
    head = env.profile.database.parent / external.DB_HEAD_NAME
    if damage == "missing_head":
        head.unlink()
    elif damage == "bad_head":
        head.write_bytes(b"null")
    else:
        (env.profile.database.parent / external.DIRECTORY / "foreign.json").write_bytes(b"{}")
    before, sql = files(env), rows(env)
    result = admin.status(profile=env.profile, request=query(env))
    assert result["state"] == "unknown" and result["continuity"] is None and result["head"] is None
    assert rows(env) == sql and files(env) == before


def test_global16_cannot_probe_or_activate_new_capability(env, monkeypatch):
    before = files(env)
    monkeypatch.setattr(migrations, "SCHEMA_VERSION", 16)
    for operation in (lambda: admin.preview(profile=env.profile),
                      lambda: admin.activate(profile=env.profile, continuity_id=str(uuid.uuid4()),
                          activated_at=STAMP, expected_basis_sha256="a" * 64)):
        with pytest.raises(SyncError) as error:
            operation()
        assert error.value.code == "SYNC_SOURCE_READMIT_CAPABILITY_REQUIRED"
    assert files(env) == before


def test_probe_binds_actual_paired_source_and_target(env):
    before = files(env)
    request = query(env)
    request["source"]["node_id"] = str(uuid.uuid4())
    with pytest.raises(SyncError) as error:
        admin.status(profile=env.profile, request=request)
    assert error.value.code in {"SYNC_PEER_MISMATCH", "SYNC_SOURCE_READMIT_PEER_MISMATCH"}
    assert files(env) == before


def test_actual_sdk_reads_absence_then_current_without_an_activation_http_endpoint(env, http):
    client = sdk(http)
    assert client.source_continuity(env.source, env.target)["state"] == "absent"
    actual = activated(env)
    before = files(env)
    result = client.source_continuity(env.source, env.target)
    assert result["state"] == "current" and result["continuity"]["continuity_id"] == actual["continuity_id"]
    assert http.post("/api/sync/source/activate", json={}, headers={"X-Sync-Token": TOKEN}).status_code == 405
    assert files(env) == before


@pytest.mark.parametrize("content,expected", [(b" " * (contract.MAX_BYTES + 1), 413),
    (b'{"kind":"source_continuity","kind":"source_continuity"}', 400),
    (b'{"kind":"source_continuity","source":NaN,"target":null}', 400)])
def test_status_endpoint_authentication_and_closed_raw_limits(env, http, content, expected):
    before = files(env)
    assert http.post("/api/sync/source/continuity", content=content, headers={"Content-Type": "application/json"}).status_code == 401
    response = http.post("/api/sync/source/continuity", content=content,
        headers={"Content-Type": "application/json", "X-Sync-Token": TOKEN})
    assert response.status_code == expected and files(env) == before


@pytest.mark.parametrize("damage", ["target", "source", "current_epoch", "extra_private", "absent_with_head"])
def test_sdk_rejects_wrong_identity_or_false_current_and_private_response(env, damage):
    result = admin.status(profile=env.profile, request=query(env))
    if damage == "target":
        result["target_identity"]["node_id"] = str(uuid.uuid4())
    elif damage == "source":
        result["source_identity"]["library_id"] = str(uuid.uuid4())
    elif damage == "current_epoch":
        result.update(state="current", continuity={"format_version": 1, "continuity_id": str(uuid.uuid4()),
            "contract_sha256": "a" * 64, "target_epoch": 8}, head={"ordinal": 0, "sha256": "b" * 64})
    elif damage == "extra_private":
        result["private_path"] = "synthetic-sensitive-private-path"
    else:
        result["head"] = {"ordinal": 0, "sha256": "b" * 64}
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=result))
    client = SyncClient("https://sync.example.test", token_provider=lambda: TOKEN, transport=transport)
    with pytest.raises(SyncClientError) as error:
        client.source_continuity(env.source, env.target)
    assert error.value.code == "SYNC_CLIENT_INVALID_RESPONSE"
    assert "synthetic-sensitive" not in str(error.value)


def test_cli_default_preview_explicit_activation_and_uncertain_result_replay(env, capsys):
    args = ["sync-source-continuity", *cli_paths(env)]
    before = files(env)
    assert cli.main(args) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["state"] == "absent" and files(env) == before
    original = [*args, "--apply", "--continuity-id", str(uuid.uuid4()), "--activated-at", STAMP,
                "--expected-digest", preview["activation_basis_sha256"]]
    assert cli.main(original) == 0
    actual = json.loads(capsys.readouterr().out)
    before = files(env)
    assert cli.main(original) == 0 and json.loads(capsys.readouterr().out) == actual
    assert files(env) == before


@pytest.mark.parametrize("extra", [["--apply"], ["--continuity-id", "private-invalid-id"],
                                  ["--private-bad-option", "synthetic-private-path"]])
def test_cli_invalid_arguments_are_sanitized_and_make_no_activation(env, extra, capsys):
    before = files(env)
    try:
        code = cli.main(["sync-source-continuity", *cli_paths(env), *extra])
    except SystemExit as error:
        code = error.code
    assert code == 2
    output = capsys.readouterr()
    assert output.out == "" and "synthetic-private-path" not in output.err and "private-invalid-id" not in output.err
    assert files(env) == before
