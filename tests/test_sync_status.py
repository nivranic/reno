"""Public sync status never serializes private state or initializes a syncer."""
from datetime import datetime, timedelta, timezone
import json
import uuid

from fastapi.testclient import TestClient
import pytest

from reno import config, db, personal_schema, storage_admin, storage_lifecycle, sync_home_status, sync_queue
from reno.sync_state import StateStore


NOW = datetime(2026, 10, 1, 3, tzinfo=timezone.utc)
STAMP = "2026-10-01T03:00:00.000Z"
HOME_FIELDS = {"enabled", "configured", "state", "updated_at", "last_success_at", "retry_at",
               "error_code", "seeded", "has_pending_publication", "work_phase", "lease_valid"}


@pytest.fixture()
def home(tmp_path, monkeypatch):
    root = tmp_path / "data"
    media = tmp_path / "media"
    for name, value in {"DATA": root, "MEDIA": media, "ORIG": media / "originals", "AUDIO": media / "audio",
                        "FRAMES": media / "frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, value)
    values = {"node_role": "home", "sync_enabled": True, "sync_cloud_url": "https://private-target.example/secret-path",
              "sync_target_library_id": str(uuid.uuid4()), "sync_target_node_id": str(uuid.uuid4()),
              "sync_interval_seconds": 60, "sync_token": "private-sync-token", "auth_token": "browser-token"}
    config.set_local(values)
    node_id = str(uuid.uuid4())
    binding = {"home_library_id": str(uuid.uuid4()), "home_node_id": node_id,
               "target_library_id": values["sync_target_library_id"], "target_node_id": values["sync_target_node_id"],
               "target_url": values["sync_cloud_url"]}
    return root, values, binding


def private_state(home):
    root, _, binding = home
    profile = storage_admin.profile()
    storage_lifecycle.initialize(profile)
    store = StateStore(root, profile=profile)
    state = store.initialize(binding, 1)
    (root / "node-identity.json").write_text(json.dumps({"instance_id": binding["home_node_id"]}), encoding="utf-8")
    claim_token, lease_token = str(uuid.uuid4()), str(uuid.uuid4())
    values = {
        "lease": {"session_id": state["session_id"], "lease_token": lease_token,
                  "generation": 4, "lease_until": "2026-10-01T03:10:00.000Z"},
        "claims": [{"item_id": 23, "url": "https://b23.tv/private-source?token=private-query-secret",
                    "attempts": 1, "lease_until": "2026-10-01T03:10:00.000Z",
                    "claim": {"item_id": 23, "lease_token": claim_token, "lease_generation": 8}}],
        "work": {"item_id": 23, "lease_generation": 8, "video_id": "privateVideo", "phase": "processed", "source_mapping_id": "a" * 64},
        "pending_publication": {"publication_id": str(uuid.uuid4()), "sha256": "b" * 64, "byte_size": 90},
        "published_content_sha256": "c" * 64, "published_reports_sha256": "d" * 64,
        "seeded": True,
        "status": {"state": "processing", "error_code": None, "updated_at": STAMP,
                   "last_success_at": "2026-10-01T02:00:00.000Z", "retry_at": None},
    }
    store.update(values)
    return store, [*binding.values(), state["session_id"], lease_token, claim_token, "private-query-secret", "privateVideo",
                   values["pending_publication"]["publication_id"], "a" * 64, "b" * 64, "c" * 64, "d" * 64]


def test_missing_state_is_not_started_and_read_creates_nothing(home, monkeypatch):
    root, _, _ = home
    monkeypatch.setattr(db, "connect", lambda *a, **k: pytest.fail("Home status cannot open or initialize a database"))
    monkeypatch.setattr(StateStore, "initialize", lambda *a, **k: pytest.fail("Status cannot initialize state"))
    before = root.exists()
    result = sync_home_status.summary(now=NOW)
    assert set(result) == HOME_FIELDS
    assert result["state"] == "not_started" and result["enabled"] and result["configured"]
    assert root.exists() == before and not (root / "sync-state.json").exists()
    assert not (root / "sync-state.lock").exists() and not (root / "reno.db").exists()


def test_projection_excludes_all_private_sentinels_and_never_opens_outbox(home, monkeypatch):
    store, secrets = private_state(home)
    before = (home[0] / "sync-state.json").read_bytes()
    monkeypatch.setattr(StateStore, "read_publication", lambda *a: pytest.fail("Status must not read publication payloads"))
    result = sync_home_status.summary(now=NOW)
    assert set(result) == HOME_FIELDS
    assert result == {"enabled": True, "configured": True, "state": "processing", "updated_at": STAMP,
                      "last_success_at": "2026-10-01T02:00:00.000Z", "retry_at": None, "error_code": None,
                      "seeded": True, "has_pending_publication": True, "work_phase": "processed", "lease_valid": True}
    encoded = json.dumps(result)
    for secret in secrets + ["private-sync-token", str(home[0])]:
        assert secret not in encoded
    for key in ("binding", "session_id", "lease_token", "claims", "pending_publication", "uploaded_media", "source_mapping_id"):
        assert key not in result
    assert (home[0] / "sync-state.json").read_bytes() == before


@pytest.mark.parametrize("control_state,expected", [("held", "recovery_held"), ("resume_pending", "recovery_resuming")])
def test_recovery_projection_overrides_stale_processing_without_private_fields(home, control_state, expected):
    store, secrets = private_state(home)
    state = store.read()
    store.observe_recovery({"generation": 1, "revision": 2, "state": control_state,
        "stream": {"library_id": state["binding"]["target_library_id"], "library_epoch": 1},
        "source_floor": {"library_id": state["binding"]["home_library_id"], "library_epoch": 1, "kb_revision": 9}})
    before = (home[0] / "sync-state.json").read_bytes()
    result = sync_home_status.summary(now=NOW)
    assert set(result) == HOME_FIELDS and result["state"] == expected
    assert all(secret not in json.dumps(result) for secret in secrets)
    assert not {"recovery", "source_floor", "revision", "generation"} & result.keys()
    assert (home[0] / "sync-state.json").read_bytes() == before


def test_expired_or_other_session_lease_is_not_valid(home):
    store, _ = private_state(home)
    assert not sync_home_status.summary(now=NOW + timedelta(minutes=10))["lease_valid"]
    lease = store.read()["lease"]
    lease["session_id"] = str(uuid.uuid4())
    store.update({"lease": lease})
    assert not sync_home_status.summary(now=NOW)["lease_valid"]


@pytest.mark.parametrize("enabled,expected", [(False, "disabled"), ("true", "disabled"), (True, "processing")])
def test_enabled_and_configured_are_independent(home, enabled, expected):
    private_state(home)
    config.set_local({"sync_enabled": enabled})
    result = sync_home_status.summary(now=NOW)
    assert result["state"] == expected and result["configured"] is True
    assert result["enabled"] is (enabled is True)
    if not result["enabled"]:
        assert not result["lease_valid"]


@pytest.mark.parametrize("change", [
    {"sync_token": ""}, {"sync_token": 123}, {"sync_token": "contains space"}, {"sync_token": "令牌"},
    {"sync_cloud_url": "https://user:secret@example.test"}, {"sync_cloud_url": "https://example.test?secret=x"},
    {"sync_cloud_url": "http://example.test"}, {"sync_cloud_url": "http://127.0.0.1:8765"},
    {"sync_target_library_id": "secret-library"}, {"sync_target_node_id": "secret-node"},
    {"sync_interval_seconds": 0},
])
def test_invalid_configuration_is_safe_and_does_not_initialize(home, change):
    config.set_local(change)
    result = sync_home_status.summary(now=NOW)
    assert result["state"] == "unconfigured" and not result["configured"]
    assert not (home[0] / "sync-state.json").exists()
    assert "secret" not in json.dumps(result)


def test_fresh_credential_rotation_overrides_cached_configuration(home):
    private_state(home)
    config.get("sync_enabled")
    values = json.loads(config.local_path().read_text(encoding="utf-8"))
    values["sync_token"] = ""
    config.local_path().write_text(json.dumps(values), encoding="utf-8")
    assert sync_home_status.summary(now=NOW)["state"] == "unconfigured"


def test_unknown_local_states_and_codes_never_escape(home):
    store, _ = private_state(home)
    status = store.read()["status"]
    store.update({"status": {**status, "state": "privateStateSecret", "error_code": "SYNC_PRIVATE_TOKEN_SECRET"}})
    result = sync_home_status.summary(now=NOW)
    assert result["state"] == "unknown" and result["error_code"] == "SYNC_UNKNOWN_ERROR"
    assert "privateStateSecret" not in json.dumps(result) and "SYNC_PRIVATE_TOKEN_SECRET" not in json.dumps(result)
    store.update({"status": {**status, "state": "retry_wait", "error_code": "SYNC_CLIENT_NETWORK"}})
    assert sync_home_status.summary(now=NOW)["error_code"] == "SYNC_CLIENT_NETWORK"


@pytest.mark.parametrize("kind", ["target_library", "target_node", "target_url", "local_node"])
def test_binding_mismatch_is_a_fixed_public_error(home, kind):
    private_state(home)
    if kind == "local_node":
        (home[0] / "node-identity.json").write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    else:
        key = {"target_library": "sync_target_library_id", "target_node": "sync_target_node_id", "target_url": "sync_cloud_url"}[kind]
        config.set_local({key: "https://different-secret.example" if kind == "target_url" else str(uuid.uuid4())})
    result = sync_home_status.summary(now=NOW)
    assert result["state"] == "state_error" and result["error_code"] == "SYNC_STATE_BINDING_MISMATCH"
    assert not result["lease_valid"] and result["work_phase"] is None


@pytest.mark.parametrize("kind", ["state", "node", "exception"])
def test_corruption_and_operating_errors_are_sanitized_without_repair(home, monkeypatch, kind):
    private_state(home)
    target = home[0] / ("node-identity.json" if kind == "node" else "sync-state.json")
    if kind == "exception":
        def fail(*args):
            raise OSError("private filesystem token=secret")
        monkeypatch.setattr(StateStore, "read", fail)
    else:
        target.write_bytes(b'{"private-secret":')
    before = target.read_bytes()
    result = sync_home_status.summary(now=NOW)
    assert result["state"] == "state_error" and result["error_code"] == "SYNC_STATUS_UNAVAILABLE"
    assert "private" not in json.dumps(result) and "secret" not in json.dumps(result)
    assert target.read_bytes() == before


@pytest.mark.parametrize("role", ["home", "all"])
def test_home_endpoint_uses_browser_gate_and_never_claims_remote_pairing(home, role):
    from app.main import create_app
    private_state(home)
    config.set_local({"node_role": role})
    with TestClient(create_app(), base_url="https://status.example.test") as client:
        assert client.get("/api/sync-status", headers={"X-Sync-Token": "private-sync-token"}).status_code == 401
        response = client.get("/api/sync-status?offset=10&limit=10", headers={"X-Reno-Token": "browser-token"})
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "home" and result["node_role"] == role
    assert result["paired"] is None and result["compatible"] is None and result["online"] is False
    assert result["queue"] == {"items": [], "offset": 10, "limit": 10, "total": 0}
    assert "private-sync-token" not in response.text and "private-query-secret" not in response.text


@pytest.fixture()
def cloud(tmp_path, monkeypatch):
    root = tmp_path / "cloud"
    media = tmp_path / "media"
    for name, value in {"DATA": root, "MEDIA": media, "ORIG": media / "originals", "AUDIO": media / "audio",
                        "FRAMES": media / "frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, value)
    config.set_local({"node_role": "cloud", "sync_token": "cloud-sync-secret", "auth_token": "browser-token"})
    db.initialize()
    con = db.connect()
    yield con
    con.close()


def seed_cloud(con):
    con.execute("INSERT INTO sync_pair(slot,source_library_id,source_node_id,target_library_id,target_node_id,protocol_version,schema_fingerprint,paired_at) "
                "VALUES(1,?,?,?,?,1,?,?)", (str(uuid.uuid4()), str(uuid.uuid4()), db.knowledge_version(con)["library_id"],
                personal_schema.node_identity(config.DATA), "a" * 64, STAMP))
    session_id, token = str(uuid.uuid4()), str(uuid.uuid4())
    con.execute("INSERT INTO sync_session(slot,session_id,lease_token,generation,lease_until,updated_at,metrics_json) "
                "VALUES(1,?,?,3,?,?,?)", (session_id, token, "2026-10-01T03:10:00.000Z", STAMP, '{}'))
    sync_queue.enqueue(con, [f"https://b23.tv/item{i}?token=private-url-secret" for i in range(1, 7)], now=NOW)
    con.execute("INSERT INTO video_asset(video_id,status,sync_state) VALUES('readyVideo','processed','ready')")
    con.execute("INSERT INTO video_asset(video_id,status,sync_state) VALUES('hiddenVideo','processed','pending')")
    con.execute("UPDATE sync_queue SET status='done',video_id='readyVideo' WHERE item_id=1")
    con.execute("UPDATE sync_queue SET status='done',video_id='hiddenVideo' WHERE item_id=2")
    con.execute("UPDATE sync_queue SET status='done',video_id='deletedVideo' WHERE item_id=3")
    for item, expiry in ((4, "2026-10-01T03:09:00.000Z"), (5, "2026-10-01T02:59:00.000Z")):
        con.execute("UPDATE sync_queue SET status='claimed',owner_id=?,owner_generation=3,lease_token=?,lease_generation=2,lease_until=? WHERE item_id=?",
                    (session_id, str(uuid.uuid4()), expiry, item))
    con.execute("UPDATE sync_queue SET status='claimed',owner_id=?,owner_generation=2,lease_token=?,lease_generation=2,lease_until=? WHERE item_id=6",
                (session_id, str(uuid.uuid4()), "2026-10-01T03:09:00.000Z"))
    con.commit()
    return token


def test_queue_public_projection_checks_claim_validity_ready_links_and_pagination(cloud):
    token = seed_cloud(cloud)
    before = [tuple(row) for row in cloud.execute("SELECT * FROM sync_queue ORDER BY item_id")]
    first = sync_queue.list_items(cloud, offset=0, limit=3, now=NOW)
    second = sync_queue.list_items(cloud, offset=3, limit=3, now=NOW)
    assert first["total"] == second["total"] == 6
    assert [item["item_id"] for item in first["items"]] == [6, 5, 4]
    assert [item["claim_active"] for item in first["items"]] == [False, False, True]
    assert all(not item["can_view_video"] for item in first["items"])
    assert [item["item_id"] for item in second["items"]] == [3, 2, 1]
    assert [item["can_view_video"] for item in second["items"]] == [False, False, True]
    assert all(not item["claim_active"] for item in second["items"])
    assert "private-url-secret" not in json.dumps(first) and token not in json.dumps(first)
    assert [tuple(row) for row in cloud.execute("SELECT * FROM sync_queue ORDER BY item_id")] == before
    expired = sync_queue.list_items(cloud, offset=0, limit=3, now=NOW + timedelta(minutes=11))
    assert all(not item["claim_active"] for item in expired["items"])


def test_cloud_endpoint_retains_existing_summary_and_never_reads_home_state(cloud, monkeypatch):
    from app.main import create_app
    seed_cloud(cloud)
    monkeypatch.setattr(sync_home_status, "summary", lambda **kwargs: pytest.fail("Cloud must not read private home state"))
    with TestClient(create_app(), base_url="https://status.example.test") as client:
        response = client.get("/api/sync-status?offset=3&limit=3", headers={"X-Reno-Token": "browser-token"})
    assert response.status_code == 200
    result = response.json()
    assert result["mode"] == "cloud" and result["home"] is None
    assert {"configured", "compatible", "paired", "online", "counts", "metrics", "last_seen", "lease_until", "expired_claims", "retry_due"} <= set(result)
    assert result["queue"]["total"] == 6 and len(result["queue"]["items"]) == 3
    assert "cloud-sync-secret" not in response.text and "private-url-secret" not in response.text


@pytest.mark.parametrize("role", ["cloud", "home"])
def test_pagination_validation_applies_to_both_modes(home, role):
    from app.main import create_app
    config.set_local({"node_role": role})
    with TestClient(create_app(), base_url="https://status.example.test") as client:
        for query in ("offset=-1", "limit=0", "limit=101"):
            response = client.get("/api/sync-status?" + query, headers={"X-Reno-Token": "browser-token"})
            assert response.status_code == 400
