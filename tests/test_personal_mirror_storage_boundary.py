"""Actual opt-in config and isolated group GC protect the personal sender tree."""
import hashlib

import pytest

from reno import config, personal_mirror_transfer as receiver, personal_mirror_transport as sender
from reno import sync_agent as home
from tests.test_storage_roots_group import libraries, enroll, registered, scan, statuses  # noqa: F401


@pytest.mark.parametrize("override, enabled", [(None, False), ("true", True), ("1", True), ("false", False), ("0", False)])
def test_environment_only_mirror_switch_matches_both_sides_without_local_key(monkeypatch, isolated_test_config, override, enabled):
    monkeypatch.delenv("RENO_PERSONAL_MIRROR_ENABLED", raising=False)
    local = config._read_local(isolated_test_config)
    local.pop("personal_mirror_enabled", None)
    import json
    isolated_test_config.write_text(json.dumps(local), encoding="utf-8")
    monkeypatch.setattr(config, "_cache", None)
    if override is not None:
        monkeypatch.setenv("RENO_PERSONAL_MIRROR_ENABLED", override)
    assert sender._enabled(config.get("personal_mirror_enabled")) is enabled
    assert sender._enabled(home._sender_config_snapshot()["personal_mirror_enabled"]) is enabled
    assert receiver._enabled() is enabled
    # Transport opt-in has no authority to activate personal feature gates.
    assert config.get("personal_features_enabled") is False


def test_group_gc_preserves_registered_files_inside_sender_tree(libraries, tmp_path):
    # A custom storage root can overlap a private evidence namespace. Even an
    # old artifact row with an otherwise legal checkpoint key grants no GC.
    checkpoint_root = tmp_path / "a" / "data" / sender.ROOT
    env = libraries(checkpoints=checkpoint_root)
    enroll(env)
    path, item = registered(env, namespace="checkpoint", content=b"original personal request evidence")
    unknown = checkpoint_root / "unknown-private-record.json"
    unknown.write_bytes(b"unknown evidence must remain untouched")
    before = {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (path, unknown)}
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] == "protected"
    assert item["artifact_id"] not in {candidate["artifact_id"] for candidate in result.eligible_artifacts}
    assert {p.name: (p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest()) for p in (path, unknown)} == before
