"""All settings writers share a real OS lock; budget CAS never rewrites paid facts."""
import json
import os
import subprocess
import sys

import pytest

from reno import budget_settings, config


@pytest.fixture
def settings_file(tmp_path, monkeypatch):
    local = tmp_path / "settings.test.json"
    local.write_text(json.dumps({"zhipu_api_key": "private-synthetic", "other": {"keep": True}}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(tmp_path / "data"))
    for name, path in {"DATA": tmp_path / "data", "MEDIA": tmp_path / "media",
        "ORIG": tmp_path / "media/originals", "AUDIO": tmp_path / "media/audio",
        "FRAMES": tmp_path / "media/frames", "FRAME_CACHE": tmp_path / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    return local


def test_read_creates_no_writer_facilities_or_cache_and_has_exact_nonsecret_sources(settings_file, monkeypatch):
    cached = {"zhipu_api_key": "cached-synthetic", "budget_enabled": True}
    monkeypatch.setattr(config, "_cache", cached)
    before = set(settings_file.parent.iterdir())
    settings = budget_settings.read()
    assert settings["effective_policy"]["budget_enabled"] is False
    assert settings["fields"]["model_daily_attempts"] == {"value": None, "source": "default", "editable": True}
    assert config._cache is cached
    assert set(settings_file.parent.iterdir()) == before
    assert "synthetic" not in json.dumps(settings)
    assert "zhipu_api_key" not in json.dumps(settings)


def test_new_budget_form_survives_unrelated_key_rotation_and_preserves_fresh_values(settings_file):
    original = budget_settings.read()
    config.set_local({"zhipu_api_key": "new-private-synthetic", "other": {"keep": "new"}})
    assert budget_settings.read()["settings_revision"] == original["settings_revision"]
    changed = budget_settings.save({"model_daily_attempts": 0}, original["settings_revision"])
    assert changed["effective_policy"]["model_daily_attempts"] == 0
    saved = json.loads(settings_file.read_text(encoding="utf-8"))
    assert saved["zhipu_api_key"] == "new-private-synthetic"
    assert saved["other"] == {"keep": "new"}


def test_env_change_invalidates_old_form_even_when_new_value_has_same_type(settings_file, monkeypatch):
    original = budget_settings.read()
    monkeypatch.setenv("RENO_MODEL_DAILY_ATTEMPTS", "2")
    before = settings_file.read_bytes()
    with pytest.raises(budget_settings.BudgetSettingsError) as caught:
        budget_settings.save({"download_daily_videos": 3}, original["settings_revision"])
    assert caught.value.code == "BUDGET_SETTINGS_CHANGED"
    assert settings_file.read_bytes() == before


@pytest.mark.parametrize("value", ["NaN", '"not-an-integer"', "true", "-1", "{broken-private-synthetic"])
def test_invalid_environment_never_exposes_raw_value_or_falls_back(settings_file, monkeypatch, value):
    monkeypatch.setenv("RENO_MODEL_DAILY_ATTEMPTS", value)
    before = settings_file.read_bytes()
    with pytest.raises(budget_settings.BudgetSettingsError) as caught:
        budget_settings.read()
    assert caught.value.code == "BUDGET_SETTINGS_UNAVAILABLE"
    assert value not in str(caught.value)
    assert settings_file.read_bytes() == before


def _process(code, **kwargs):
    return subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=dict(os.environ),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **kwargs)


@pytest.mark.parametrize("entry", ["lock", "set_local"])
def test_common_os_lock_has_bounded_contention_for_both_new_and_existing_writers(settings_file, entry):
    code = """
from reno import config
original = config.local_write_lock
config.local_write_lock = lambda: original(timeout=0.15)
try:
    if ENTRY == 'set_local':
        config.set_local({'other': 'must-not-write'})
    else:
        with config.local_write_lock():
            pass
except config.ConfigWriteBusy:
    print('busy', flush=True)
else:
    print('unexpected-write', flush=True)
""".replace("ENTRY", repr(entry))
    before = settings_file.read_bytes()
    with config.local_write_lock():
        process = _process(code)
        output, error = process.communicate(timeout=5)
    assert process.returncode == 0, error
    assert output.strip() == "busy"
    assert settings_file.read_bytes() == before
    assert (settings_file.parent / ("." + settings_file.name + ".write.lock")).is_file()


def test_two_process_cas_forms_have_one_winner_and_one_stale_form(settings_file):
    revision = budget_settings.read()["settings_revision"]
    script = """
import json, sys
from reno import budget_settings
print('ready', flush=True)
sys.stdin.readline()
try:
    result = budget_settings.save({'model_daily_attempts': VALUE}, REVISION)
    print(json.dumps({'status': 'saved', 'value': result['effective_policy']['model_daily_attempts']}), flush=True)
except budget_settings.BudgetSettingsError as error:
    print(json.dumps({'status': error.code}), flush=True)
"""
    processes = [_process(script.replace("VALUE", str(value)).replace("REVISION", repr(revision))) for value in (1, 2)]
    try:
        assert [process.stdout.readline().strip() for process in processes] == ["ready", "ready"]
        for process in processes:
            process.stdin.write("go\n")
            process.stdin.flush()
        results = []
        for process in processes:
            output, error = process.communicate(timeout=8)
            assert process.returncode == 0, error
            results.append(json.loads(output))
    finally:
        for process in processes:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=3)
    assert sorted(result["status"] for result in results) == ["BUDGET_SETTINGS_CHANGED", "saved"]
    saved = next(result for result in results if result["status"] == "saved")
    assert json.loads(settings_file.read_text(encoding="utf-8"))["model_daily_attempts"] == saved["value"]


def test_lock_never_follows_or_adopts_external_hardlink(settings_file, tmp_path):
    external = tmp_path / "external.lock"
    external.write_bytes(b"synthetic-must-keep")
    linked = settings_file.parent / ("." + settings_file.name + ".write.lock")
    try:
        os.link(external, linked)
    except OSError:
        pytest.skip("Hardlink creation is unavailable on this test filesystem")
    before = settings_file.read_bytes()
    with pytest.raises(OSError):
        config.set_local({"other": "must-not-write"})
    assert external.read_bytes() == b"synthetic-must-keep"
    assert settings_file.read_bytes() == before


def test_atomic_write_failure_retains_config_and_cleans_only_own_temporary(settings_file, monkeypatch):
    settings = budget_settings.read()
    before = settings_file.read_bytes()
    def refuse(*args, **kwargs):
        raise OSError("synthetic-private-path-not-for-response")
    monkeypatch.setattr(config.os, "replace", refuse)
    with pytest.raises(budget_settings.BudgetSettingsError) as caught:
        budget_settings.save({"model_daily_attempts": 1}, settings["settings_revision"])
    assert caught.value.code == "BUDGET_SETTINGS_UNAVAILABLE"
    assert "synthetic-private" not in str(caught.value)
    assert settings_file.read_bytes() == before
    assert not list(settings_file.parent.glob("." + settings_file.name + ".*.tmp"))


def test_resolver_setting_is_not_inserted_into_the_frozen_policy_hash(settings_file):
    from reno import budget_contract
    first = budget_settings.read()
    sha = budget_contract.policy_sha256(first["effective_policy"])
    second = budget_settings.save({"source_resolution_max_attempts": 30}, first["settings_revision"])
    assert budget_contract.policy_sha256(second["effective_policy"]) == sha
    assert "source_resolution_max_attempts" not in second["effective_policy"]


@pytest.mark.skipif(os.name == "nt", reason="Windows directory pins deny this POSIX rename substitution")
def test_parent_substitution_after_policy_check_cannot_publish_secret_config_elsewhere(settings_file, monkeypatch):
    first = budget_settings.read()
    before = settings_file.read_bytes()
    selected = settings_file.parent
    original_parent = selected.with_name(selected.name + "-original")
    replacement = selected.with_name(selected.name + "-external")
    replacement.mkdir()
    original = budget_settings._snapshot
    moved = False
    def swap_after_validation(local, environment):
        nonlocal moved
        result = original(local, environment)
        if not moved and getattr(config._local_writer, "context", None) is not None:
            selected.rename(original_parent)
            selected.symlink_to(replacement, target_is_directory=True)
            moved = True
        return result
    monkeypatch.setattr(budget_settings, "_snapshot", swap_after_validation)
    try:
        with pytest.raises(budget_settings.BudgetSettingsError) as caught:
            budget_settings.save({"model_daily_attempts": 1}, first["settings_revision"])
        assert caught.value.code == "BUDGET_SETTINGS_UNAVAILABLE"
        assert (original_parent / settings_file.name).read_bytes() == before
        assert list(replacement.iterdir()) == []
    finally:
        if moved:
            selected.unlink()
            original_parent.rename(selected)
