"""Budget UI APIs use isolated config and storage, never a provider or real library."""
import json

import pytest
from fastapi.testclient import TestClient

from reno import config, db, storage_admin, storage_lifecycle


@pytest.fixture
def budget_api(tmp_path, monkeypatch):
    local = tmp_path / "config.test.json"
    local.write_text(json.dumps({"auth_token": "", "web_ui": "classic",
        "zhipu_api_key": "synthetic-private-value", "unknown_business": {"keep": 1}}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(tmp_path / "data"))
    for name, path in {"DATA": tmp_path / "data", "MEDIA": tmp_path / "media",
        "ORIG": tmp_path / "media/originals", "AUDIO": tmp_path / "media/audio",
        "FRAMES": tmp_path / "media/frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    profile = storage_admin.profile()
    db.initialize(profile.database)
    storage_lifecycle.initialize(profile)
    from app.main import create_app
    client = TestClient(create_app())
    yield client, profile, local
    client.close()


def test_read_exposes_actual_settings_and_empty_local_ledger_without_secrets(budget_api):
    client, profile, local = budget_api
    config_bytes = local.read_bytes()
    # SQLite read-only opens can create WAL/SHM sidecars. Configuration reads
    # and budget GET must still create no writer lock, tmp file or business root.
    def paths():
        return {path.relative_to(local.parent) for path in local.parent.rglob("*")
                if not path.name.endswith((".db-wal", ".db-shm"))}
    before = paths()
    response = client.get("/api/budget")
    assert response.status_code == 200
    body = response.json()
    assert body["settings"]["effective_policy"]["budget_enabled"] is False
    assert body["settings"]["source_resolution_max_attempts"] == 24
    assert body["usage"]["scope"] == "local_ledger_only"
    assert body["usage"]["history_before_ledger"] == "unknown"
    assert body["usage"]["attempts"]["model"]["admissions"] == 0
    assert body["usage"]["tokens"]["total_tokens"]["reported"] == 0
    assert body["capabilities"]["edit_policy"] is True
    assert body["capabilities"]["home_control"] is False
    assert "synthetic-private-value" not in response.text
    assert str(profile.database) not in response.text
    assert local.read_bytes() == config_bytes
    assert paths() == before


def test_normal_api_token_required_and_sync_token_does_not_authorize_budget(budget_api):
    client, _, _ = budget_api
    config.set_local({"auth_token": "synthetic-api-token", "sync_token": "synthetic-sync-token"})
    assert client.get("/api/budget").status_code == 401
    assert client.get("/api/budget", headers={"X-Sync-Token": "synthetic-sync-token"}).status_code == 401
    assert client.get("/api/budget", headers={"X-Reno-Token": "synthetic-api-token"}).status_code == 200


def test_invalid_ledger_is_unavailable_and_never_an_empty_usage_snapshot(budget_api):
    client, profile, _ = budget_api
    con = db.connect(profile.database)
    con.execute("DROP TABLE budget_attempt")
    con.commit()
    con.close()
    response = client.get("/api/budget")
    assert response.status_code == 503
    assert response.json()["code"] == "BUDGET_VIEW_UNAVAILABLE"
    assert "usage" not in response.json()
    assert str(profile.database) not in response.text


def test_cloud_read_is_local_and_only_its_model_fields_are_editable(budget_api):
    client, _, _ = budget_api
    config.set_local({"node_role": "cloud"})
    response = client.get("/api/budget")
    assert response.status_code == 200
    body = response.json()
    assert body["capabilities"]["node_role"] == "cloud"
    assert body["capabilities"]["home_control"] is False
    assert body["settings"]["fields"]["model_daily_attempts"]["editable"] is True
    assert body["settings"]["fields"]["download_daily_videos"]["editable"] is False
    assert body["settings"]["fields"]["source_resolution_max_attempts"]["editable"] is False
    assert body["settings"]["fields"]["model_total_tokens_mode"]["editable"] is False


def test_settings_save_uses_revision_and_retains_unrelated_private_config(budget_api):
    client, _, local = budget_api
    revision = client.get("/api/budget").json()["settings"]["settings_revision"]
    response = client.post("/api/config/budget", json={"expected_settings_revision": revision,
        "updates": {"model_daily_attempts": 0, "download_daily_videos": None,
                    "source_resolution_max_attempts": 25}})
    assert response.status_code == 200
    result = response.json()
    assert result["effective_policy"]["model_daily_attempts"] == 0
    assert result["effective_policy"]["download_daily_videos"] is None
    assert result["source_resolution_max_attempts"] == 25
    assert result["settings_revision"] != revision
    assert result["fields"]["model_daily_attempts"]["source"] == "local"
    saved = json.loads(local.read_text(encoding="utf-8"))
    assert saved["unknown_business"] == {"keep": 1}
    assert saved["zhipu_api_key"] == "synthetic-private-value"
    assert "synthetic-private-value" not in response.text
    stale = client.post("/api/config/budget", json={"expected_settings_revision": revision,
        "updates": {"model_daily_attempts": 10}})
    assert stale.status_code == 409
    assert stale.json()["code"] == "BUDGET_SETTINGS_CHANGED"
    assert json.loads(local.read_text(encoding="utf-8"))["model_daily_attempts"] == 0


@pytest.mark.parametrize("updates", [
    {"zhipu_api_key": "must-not-save"}, {"model_total_tokens_mode": "reported_only"},
    {"model_daily_attempts": True}, {"model_daily_attempts": -1},
    {"download_rate_bytes_per_second": 0}, {"source_resolution_max_attempts": 0},
    {"budget_timezone": "invalid/not-a-zone"},
    {"download_interval_min_seconds": 5, "download_interval_max_seconds": 4},
])
def test_settings_invalid_updates_never_write(budget_api, updates):
    client, _, local = budget_api
    revision = client.get("/api/budget").json()["settings"]["settings_revision"]
    before = local.read_bytes()
    response = client.post("/api/config/budget", json={"expected_settings_revision": revision, "updates": updates})
    assert response.status_code == 400
    assert local.read_bytes() == before
    assert "must-not-save" not in response.text


def test_environment_field_is_read_only_and_rejected_on_save(budget_api, monkeypatch):
    client, _, local = budget_api
    monkeypatch.setenv("RENO_MODEL_DAILY_ATTEMPTS", "7")
    settings = client.get("/api/budget").json()["settings"]
    field = settings["fields"]["model_daily_attempts"]
    assert field == {"value": 7, "source": "environment", "editable": False}
    before = local.read_bytes()
    response = client.post("/api/config/budget", json={"expected_settings_revision": settings["settings_revision"],
        "updates": {"model_daily_attempts": 9}})
    assert response.status_code == 409
    assert response.json()["code"] == "BUDGET_SETTINGS_ENV_CONTROLLED"
    assert local.read_bytes() == before


def test_bad_config_is_not_recreated_by_budget_save(budget_api):
    client, _, local = budget_api
    revision = client.get("/api/budget").json()["settings"]["settings_revision"]
    local.write_text("{broken-synthetic-private", encoding="utf-8")
    before = local.read_bytes()
    # The application's gate is already cached; budget policy must read fresh.
    response = client.post("/api/config/budget", json={"expected_settings_revision": revision,
        "updates": {"model_daily_attempts": 1}})
    assert response.status_code == 503
    assert response.json()["code"] == "BUDGET_SETTINGS_UNAVAILABLE"
    assert local.read_bytes() == before
    assert "broken-synthetic-private" not in response.text


def test_cloud_cannot_save_home_download_fields_but_can_save_local_model_cap(budget_api):
    client, _, local = budget_api
    config.set_local({"node_role": "cloud"})
    settings = client.get("/api/budget").json()["settings"]
    before = local.read_bytes()
    refused = client.post("/api/config/budget", json={"expected_settings_revision": settings["settings_revision"],
        "updates": {"download_daily_videos": 1}})
    assert refused.status_code == 403
    assert refused.json()["code"] == "BUDGET_SETTINGS_ROLE_FORBIDDEN"
    assert local.read_bytes() == before
    saved = client.post("/api/config/budget", json={"expected_settings_revision": settings["settings_revision"],
        "updates": {"model_daily_attempts": 1}})
    assert saved.status_code == 200
    assert saved.json()["effective_policy"]["model_daily_attempts"] == 1
