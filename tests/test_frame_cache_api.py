"""API/CLI use the same bounded cache without changing knowledge or evidence."""
from io import BytesIO
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from reno import cli, config, db, frame_cache, storage_admin


def jpeg():
    output = BytesIO()
    Image.new("RGB", (24, 16), "blue").save(output, "JPEG")
    return output.getvalue()


@pytest.fixture
def cache_api(tmp_path, monkeypatch):
    from app import api
    from app.main import create_app

    monkeypatch.setattr(config, "DATA", tmp_path / "data")
    monkeypatch.setattr(config, "MEDIA", tmp_path / "media")
    monkeypatch.setattr(config, "FRAME_CACHE", tmp_path / "frames_cache")
    monkeypatch.setattr(config, "_cache", {**config.DEFAULTS, "auth_token": "", "web_ui": "classic"})
    db.initialize()
    storage_admin.initialize(storage_admin.profile())
    source = tmp_path / "media" / "source.mp4"
    source.parent.mkdir(parents=True, exist_ok=True)
    source.write_bytes(b"synthetic-source")
    con = db.connect()
    con.execute("INSERT INTO video_asset(video_id,content_sha256,files_json,duration_ms,status) VALUES(?,?,?,?,?)",
                ("cache_api", hashlib.sha256(source.read_bytes()).hexdigest(),
                 json.dumps({"video": str(source)}), 1000, "processed"))
    con.commit()
    before = db.knowledge_version(con)
    calls = []

    def extract(path, ms, output):
        assert path == source
        calls.append(ms)
        output.write_bytes(jpeg())
        return True

    monkeypatch.setattr(api, "extract_frame", extract)
    client = TestClient(create_app())
    yield client, con, before, calls
    client.close()
    con.close()


def test_zero_budget_returns_frame_but_does_not_keep_it_or_change_knowledge(cache_api, monkeypatch):
    client, con, before, calls = cache_api
    monkeypatch.setenv("RENO_FRAME_CACHE_MAX_BYTES", "0")
    for _ in range(2):
        response = client.get("/api/frame/cache_api/0")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/jpeg"
        assert response.content == jpeg()
    assert calls == [0, 0]
    assert frame_cache.preview(root=config.FRAME_CACHE, budget_bytes=0)["managed_bytes"] == 0
    assert db.knowledge_version(con) == before


def test_response_already_owns_bytes_when_cache_is_evicted(cache_api):
    from app import api

    client, con, before, calls = cache_api
    response = api.frame_at("cache_api", 123)
    assert response.body == jpeg()
    assert client.get("/api/frame/cache_api/123").content == jpeg()
    assert calls == [123]
    preview = frame_cache.preview(root=config.FRAME_CACHE, budget_bytes=0)
    result = frame_cache.prune(root=config.FRAME_CACHE, budget_bytes=0, expected_digest=preview["digest"])
    assert result["deleted_bytes"] == len(jpeg())
    assert response.body == jpeg()
    assert db.knowledge_version(con) == before


def test_bad_budget_is_controlled_without_extraction_or_internal_paths(cache_api, monkeypatch):
    client, _, _, calls = cache_api
    monkeypatch.setenv("RENO_FRAME_CACHE_MAX_BYTES", "-1")
    response = client.get("/api/frame/cache_api/0")
    assert response.status_code == 503
    assert str(config.DATA) not in response.text
    assert calls == []


def test_failed_extraction_is_retryable(cache_api, monkeypatch):
    from app import api

    client, _, _, calls = cache_api
    original = api.extract_frame
    monkeypatch.setattr(api, "extract_frame", lambda *_: False)
    response = client.get("/api/frame/cache_api/0")
    assert response.status_code >= 500
    assert response.json()["code"].startswith("FRAME_CACHE_")
    monkeypatch.setattr(api, "extract_frame", original)
    assert client.get("/api/frame/cache_api/0").content == jpeg()
    assert calls == [0]


def test_cli_preview_does_not_initialize_cache_or_database(tmp_path, monkeypatch, capsys):
    cache = tmp_path / "absent" / "frames_cache"
    monkeypatch.setattr(config, "FRAME_CACHE", cache)
    monkeypatch.setattr(config, "DATA", tmp_path / "absent" / "data")
    assert cli.main(["frame-cache"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["managed_bytes"] == 0 and result["digest"]
    assert not (tmp_path / "absent").exists()


@pytest.mark.parametrize("suffix", [["--apply"], ["--expected-digest", "a" * 64], ["--expected-digest", ""]])
def test_cli_requires_explicit_apply_and_preview(tmp_path, monkeypatch, capsys, suffix):
    cache = tmp_path / "absent"
    monkeypatch.setattr(config, "FRAME_CACHE", cache)
    assert cli.main(["frame-cache", *suffix]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "FRAME_CACHE_EXPECTATION_REQUIRED"
    assert not cache.exists()


def test_cli_uses_preview_digest_and_reports_evicted_bytes(cache_api, capsys):
    client, con, before, _ = cache_api
    assert client.get("/api/frame/cache_api/0").status_code == 200
    assert cli.main(["frame-cache", "--budget-bytes", "0"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["candidate_bytes"] == len(jpeg())
    # A hit changes the persisted access sequence, so the earlier plan expires.
    assert client.get("/api/frame/cache_api/0").status_code == 200
    assert cli.main(["frame-cache", "--budget-bytes", "0", "--apply", "--expected-digest", preview["digest"]]) == 2
    assert json.loads(capsys.readouterr().err)["code"].startswith("FRAME_CACHE_")
    assert cli.main(["frame-cache", "--budget-bytes", "0"]) == 0
    fresh = json.loads(capsys.readouterr().out)
    assert cli.main(["frame-cache", "--budget-bytes", "0", "--apply", "--expected-digest", fresh["digest"]]) == 0
    assert json.loads(capsys.readouterr().out)["deleted_bytes"] == len(jpeg())
    assert db.knowledge_version(con) == before


@pytest.mark.parametrize("value", ["-1", "1.5", "true", " 5", str(2**63)])
def test_invalid_config_budget_never_creates_cache(tmp_path, monkeypatch, capsys, value):
    cache = tmp_path / "absent"
    monkeypatch.setattr(config, "FRAME_CACHE", cache)
    monkeypatch.setenv("RENO_FRAME_CACHE_MAX_BYTES", value)
    assert cli.main(["frame-cache"]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "FRAME_CACHE_CONFIG_INVALID"
    assert not cache.exists()


def test_budget_reader_observes_config_updates_without_general_cache_reload(isolated_test_config, monkeypatch):
    config._cache = {**config.DEFAULTS, "frame_cache_max_bytes": 100}
    isolated_test_config.write_text('{"frame_cache_max_bytes": 2048}', encoding="utf-8")
    assert config.frame_cache_budget_bytes() == 2048
    isolated_test_config.write_text('{"frame_cache_max_bytes": 1024}', encoding="utf-8")
    assert config.frame_cache_budget_bytes() == 1024
    monkeypatch.setenv("RENO_FRAME_CACHE_MAX_BYTES", "0")
    assert config.frame_cache_budget_bytes() == 0
    assert config._cache["frame_cache_max_bytes"] == 100
