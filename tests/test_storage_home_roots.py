"""Home reference capture/parsing against isolated synthetic filesystem roots."""
from copy import deepcopy
from dataclasses import replace
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import storage_home_roots as home, storage_lifecycle as lifecycle
from reno import sync_snapshot, sync_sources, sync_state
from reno.sync_claims import _origin_digest
from reno.sync_protocol import _json_bytes


STAMP = "2026-10-01T00:00:00.000000Z"
SHA = "a" * 64
OTHER_SHA = "b" * 64
ROOTS = {"sync_media": "1" * 64, "local_report": "2" * 64,
         "sync_report": "2" * 64, "checkpoint": "3" * 64}


@pytest.fixture
def env(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    identity = {"library_id": str(uuid.uuid4()), "node_id": str(uuid.uuid4())}
    return profile, identity


def state(env):
    _, identity = env
    value = {"format_version": 1, "binding": {
        "home_library_id": identity["library_id"], "home_node_id": identity["node_id"],
        "target_library_id": str(uuid.uuid4()), "target_node_id": str(uuid.uuid4()),
        "target_url": "https://private-sync.example.test/reno"},
        "local_epoch": 1, "session_id": str(uuid.uuid4()), "lease": None,
        "inbound": {"stream": None, "after": 0, "through": None},
        "outbound": {"stream": None, "after": 0, "through": None},
        "seeded": False, "claims": [], "work": None, "pending_publication": None,
        "published_version": None, "published_content_sha256": None, "published_reports_sha256": None,
        "uploaded_media": {}, "status": {"state": "idle", "error_code": None,
        "updated_at": STAMP, "last_success_at": None, "retry_at": None},
        "recovery": None, "applied_recovery_generation": 0,
        "recovery_attempt": None, "legacy_pending_stream": None}
    sync_state._validate(value)
    return value


def write(env, relative, value):
    path = env[0].database.parent / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    raw = value if type(value) is bytes else _json_bytes(value)
    path.write_bytes(raw)
    if os.name != "nt":
        # Match the private-file contract of the production Home writers.
        path.chmod(0o600)
    return path


def outbox(env, *, video="BV1Test", sha=SHA, legacy=False):
    tables = {name: [] for name in sync_snapshot.VIDEO_TABLES}
    tables["video_asset"] = [{"video_id": video, "status": "processed", "content_sha256": sha,
                               "title": "PRIVATE SNAPSHOT BODY"}]
    snap = sync_snapshot.validate_snapshot({"publication_id": str(uuid.uuid4()),
        "version": {"library_id": env[1]["library_id"], "library_epoch": 1, "kb_revision": 4},
        "videos": [{"video_id": video, "tables": tables, "media": {
            "video_id": video, "sha256": sha, "byte_size": 20, "extension": "mp4"}}],
        "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []})
    envelope = {"snapshot": snap, "claims": []}
    if not legacy:
        envelope.update(recovery_generation=0, recovery_stream=None)
    raw = _json_bytes(envelope)
    metadata = {"publication_id": snap["publication_id"], "sha256": hashlib.sha256(raw).hexdigest(), "byte_size": len(raw)}
    return write(env, "sync-outbox/" + snap["publication_id"] + ".json", raw), metadata, envelope


def source_map(env, *, video="BV1Test", sha=SHA):
    final = "https://www.bilibili.com/video/" + video + "?private=PRIVATE_QUERY"
    submitted = "https://b23.tv/private?signature=PRIVATE_SIGNATURE"
    resolution = {"resolution_version": 1, "submitted_url": submitted, "resolved_url": final,
        "resolved_at": STAMP, "hops": [
            {"url": submitted, "peer_ip": "8.8.8.8", "status": 302, "next_url": final},
            {"url": final, "peer_ip": "1.1.1.1", "status": 200, "next_url": None}]}
    origin = {"origin_version": 1, "source_library_id": env[1]["library_id"],
        "source_node_id": env[1]["node_id"], "submitted_url": submitted, "resolved_url": final,
        "resolution_sha256": hashlib.sha256(_json_bytes(resolution)).hexdigest(),
        "extracted_video_id": video, "video_id": video, "media_sha256": sha,
        "resolved_at": STAMP, "recorded_at": STAMP}
    origin["mapping_id"] = _origin_digest(origin)
    record = {"origin": origin, "resolution": resolution}
    return write(env, "sync-sources/v1/" + origin["mapping_id"] + ".json", record), record


def history(env, value, *, reason="recovery"):
    fields = ("binding", "local_epoch", "claims", "work", "inbound", "outbound", "pending_publication",
              "recovery_attempt", "recovery", "legacy_pending_stream")
    return write(env, "sync-work-history/" + uuid.uuid4().hex + ".json",
                 {"format_version": 1, "reason": reason, **{key: deepcopy(value[key]) for key in fields}})


def scan(env, **kwargs):
    profile, identity = env
    with lifecycle.shared(profile):
        return home.scan_locked(profile, root_ids=ROOTS, member_id="mem_home_test", source_identity=identity, **kwargs)


@pytest.mark.parametrize("invalid", [False, 0])
def test_capture_limits_do_not_silently_accept_false_values(env, invalid):
    with pytest.raises(ValueError):
        scan(env, limits=invalid)


def test_constructed_capture_error_never_leaks_nonstandard_diagnostic_payload(env):
    write(env, "sync-sources/v1/" + SHA + ".json", b"{}")
    profile, identity = env
    with lifecycle.shared(profile), home.capture_locked(profile) as captured:
        records = tuple(replace(record, raw=None, error={"private": "SECRET_DIAGNOSTIC_URL"})
                        if record.kind == "sources" else record for record in captured.bundle.records)
        result = home.parse_bundle(replace(captured.bundle, records=records), profile=profile,
                                   root_ids=ROOTS, member_id="mem_home_test", source_identity=identity)
    assert {b.code for b in result.blockers} == {"HOME_RECORD_INVALID"}
    assert "SECRET_DIAGNOSTIC_URL" not in json.dumps(result.public_summary())


@pytest.mark.parametrize("damage", ["missing_state_record", "contradictory_directory"])
def test_pure_bundle_requires_explicit_consistent_state_presence(env, damage):
    write(env, "sync-state.json", state(env))
    profile, identity = env
    with lifecycle.shared(profile), home.capture_locked(profile) as captured:
        if damage == "missing_state_record":
            bundle = replace(captured.bundle, records=tuple(r for r in captured.bundle.records if r.kind != "state"))
        else:
            bundle = replace(captured.bundle, directories=tuple((kind, "absent" if kind == "state" else presence)
                                                                for kind, presence in captured.bundle.directories))
        result = home.parse_bundle(bundle, profile=profile, root_ids=ROOTS,
                                   member_id="mem_home_test", source_identity=identity)
    assert {b.code for b in result.blockers} == {"HOME_CAPTURE_INCOMPLETE"}
    assert {b.namespace for b in result.blockers} == {"sync_media", "local_report", "sync_report"}


def tree(root):
    return {str(p.relative_to(root)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in root.rglob("*")}


def test_empty_capture_does_not_initialize_home_files_locks_or_sqlite(env, monkeypatch):
    profile, _ = env
    before = tree(profile.database.parent.parent)
    def forbidden(*args, **kwargs):
        pytest.fail("Home reader must not construct writers or open SQLite")
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    monkeypatch.setattr(sync_sources, "SourceMap", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    result = scan(env)
    assert not result.blockers and not result.references and not result.selectors
    assert tree(profile.database.parent.parent) == before
    assert not (profile.database.parent / "sync-state.lock").exists()


@pytest.mark.parametrize("legacy", [False, True])
def test_absent_state_still_protects_every_old_outbox(env, legacy):
    outbox(env, video="BV1First", legacy=legacy)
    outbox(env, video="BV1Second", sha=OTHER_SHA, legacy=legacy)
    result = scan(env)
    assert not result.blockers
    assert {r.storage_key for r in result.references} == {f"sync/BV1First/{SHA}.mp4", f"sync/BV1Second/{OTHER_SHA}.mp4"}


def test_published_receipt_does_not_release_old_outbox(env):
    _, _, envelope = outbox(env)
    value = state(env)
    value.update(seeded=True, published_version=envelope["snapshot"]["version"],
                 published_content_sha256=SHA, published_reports_sha256=OTHER_SHA)
    write(env, "sync-state.json", value)
    result = scan(env)
    assert not result.blockers and any(r.storage_key == f"sync/BV1Test/{SHA}.mp4" for r in result.references)


def test_absent_state_scans_all_source_maps_without_guessing_extensions(env):
    source_map(env, video="BV1First")
    source_map(env, video="BV1Second", sha=OTHER_SHA)
    result = scan(env)
    assert not result.blockers
    assert {(s.video_id, s.sha256, s.extension) for s in result.selectors} == {
        ("BV1First", SHA, None), ("BV1Second", OTHER_SHA, None)}


def test_uploaded_media_cache_remains_selector_and_has_no_extension(env):
    value = state(env)
    value["uploaded_media"] = {"cached": {"video_id": "cached", "sha256": SHA, "byte_size": 20, "extension": "webm"}}
    write(env, "sync-state.json", value)
    result = scan(env)
    assert not result.blockers
    assert any(s.video_id == "cached" and s.sha256 == SHA and s.extension is None for s in result.selectors)


@pytest.mark.parametrize("current_present", [False, True])
def test_old_history_survives_target_switch_and_missing_current_state(env, current_present):
    value = state(env)
    value["work"] = {"item_id": 1, "lease_generation": 1, "video_id": "old-video", "phase": "downloaded"}
    history(env, value, reason="epoch_changed")
    other = state(env)
    other["work"] = {"item_id": 2, "lease_generation": 1, "video_id": "second-video", "phase": "processed"}
    history(env, other, reason="claim_changed")
    if current_present:
        write(env, "sync-state.json", state(env))
    result = scan(env)
    assert not result.blockers
    assert {s.video_id for s in result.selectors} >= {"old-video", "second-video"}


@pytest.mark.parametrize("where", ["pending", "attempt", "history"])
def test_pending_or_attempt_outbox_must_exist(env, where):
    value = state(env)
    metadata = {"publication_id": str(uuid.uuid4()), "sha256": SHA, "byte_size": 50}
    if where == "attempt":
        value["recovery_attempt"] = {"generation": 0, "publication_id": metadata["publication_id"],
            "version": {"library_id": env[1]["library_id"], "library_epoch": 1, "kb_revision": 3},
            "stream": {"library_id": value["binding"]["target_library_id"], "library_epoch": 1}}
    else:
        value["pending_publication"] = metadata
    if where == "history":
        history(env, value)
    else:
        write(env, "sync-state.json", value)
    result = scan(env)
    assert result.blockers and "sync_media" in {b.namespace for b in result.blockers}


def test_pending_outbox_metadata_hash_mismatch_blocks_instead_of_reusing_content(env):
    _, metadata, _ = outbox(env)
    value = state(env)
    value["pending_publication"] = {**metadata, "sha256": OTHER_SHA}
    write(env, "sync-state.json", value)
    assert scan(env).blockers


def test_valid_pending_metadata_binds_exact_outbox_bytes(env):
    path, metadata, _ = outbox(env)
    value = state(env)
    value["pending_publication"] = metadata
    write(env, "sync-state.json", value)
    first = scan(env)
    assert not first.blockers and first.references
    # A JSON-whitespace-only edit remains decodable but violates frozen bytes.
    path.write_bytes(path.read_bytes() + b"\n")
    second = scan(env)
    assert second.blockers and second.digest != first.digest


@pytest.mark.parametrize("valid_hash", [False, True])
def test_inline_report_is_not_an_external_report_file_root(env, valid_hash):
    path, _, envelope = outbox(env)
    content = "PRIVATE INLINE REPORT BODY"
    envelope["snapshot"]["reports"] = [{"name": "summary", "content": content,
        "sha256": hashlib.sha256(content.encode()).hexdigest() if valid_hash else SHA,
        "policy_version": "reports-v3", "version": envelope["snapshot"]["version"],
        "generated_at": "2026-10-01T00:00:00.000Z", "review_events_sha256": OTHER_SHA}]
    path.write_bytes(_json_bytes(envelope))
    result = scan(env)
    assert bool(result.blockers) is not valid_hash
    assert not any(r.namespace in {"local_report", "sync_report"} for r in result.references)
    assert b"PRIVATE INLINE REPORT BODY" not in result.canonical_bytes()


def test_work_source_mapping_must_exist(env):
    value = state(env)
    value["work"] = {"item_id": 1, "lease_generation": 1, "video_id": "orphan", "phase": "downloaded",
                     "source_mapping_id": SHA}
    write(env, "sync-state.json", value)
    result = scan(env)
    assert "sync_media" in {b.namespace for b in result.blockers}


def test_malformed_state_does_not_hide_valid_old_outbox(env):
    outbox(env)
    write(env, "sync-state.json", b'{"work":')
    result = scan(env)
    assert result.blockers
    assert any(r.storage_key == f"sync/BV1Test/{SHA}.mp4" for r in result.references)


@pytest.mark.parametrize("damage", ["json", "resolution", "identity", "hash", "name"])
def test_bad_source_map_blocks_media_only(env, damage):
    path, record = source_map(env)
    if damage == "json":
        path.write_bytes(b'{"origin":')
    elif damage == "name":
        path.rename(path.with_name(OTHER_SHA + ".json"))
    else:
        if damage == "resolution":
            record["resolution"]["hops"][0]["peer_ip"] = "1.0.0.1"
        elif damage == "identity":
            record["origin"]["source_node_id"] = str(uuid.uuid4())
            record["origin"]["mapping_id"] = _origin_digest(record["origin"])
        else:
            record["origin"]["media_sha256"] = OTHER_SHA
        path.write_bytes(_json_bytes(record))
    result = scan(env)
    assert {b.namespace for b in result.blockers} == {"sync_media"}


@pytest.mark.parametrize("limits", [home.HomeLimits(max_files=1), home.HomeLimits(max_total_bytes=20),
                                    home.HomeLimits(max_outbox_bytes=20)])
def test_capture_limits_never_release_unfinished_outbox_scope(env, limits):
    outbox(env, video="BV1First")
    outbox(env, video="BV1Second", sha=OTHER_SHA)
    result = scan(env, limits=limits)
    assert result.blockers and "sync_media" in {b.namespace for b in result.blockers}


def test_state_byte_and_json_node_limits_yield_blockers(env):
    write(env, "sync-state.json", state(env))
    assert scan(env, limits=home.HomeLimits(max_state_bytes=20)).blockers
    assert scan(env, limits=home.HomeLimits(max_json_nodes=2)).blockers


def test_source_size_limit_has_media_only_scope(env):
    source_map(env)
    assert {b.namespace for b in scan(env, limits=home.HomeLimits(max_source_bytes=20)).blockers} == {"sync_media"}


def test_no_source_database_shm_or_unknown_personal_files_are_touched(env):
    profile, _ = env
    for suffix in ("", "-wal", "-shm", "-journal"):
        Path(str(profile.database) + suffix).write_bytes(b"synthetic source must remain unchanged " + suffix.encode())
    write(env, "attachments/private.pdf", b"private attachment")
    write(env, "config.local.json", b'{"api_key":"PRIVATE_API_KEY"}')
    outbox(env)
    before = tree(profile.database.parent.parent)
    result = scan(env)
    assert not result.blockers
    assert tree(profile.database.parent.parent) == before


@pytest.mark.parametrize("change", ["state", "new_outbox", "removed_source"])
def test_raw_capture_requires_whole_round_stability(env, change):
    value = state(env)
    state_path = write(env, "sync-state.json", value)
    source_path, _ = source_map(env)
    profile, identity = env
    with lifecycle.shared(profile):
        with pytest.raises(home.HomeRootError) as caught:
            with home.capture_locked(profile) as capture:
                home.parse_bundle(capture.bundle, profile=profile, root_ids=ROOTS, member_id="mem_home_test", source_identity=identity)
                if change == "state":
                    value["status"]["updated_at"] = "2026-10-01T01:00:00.000000Z"
                    state_path.write_bytes(_json_bytes(value))
                elif change == "new_outbox":
                    outbox(env)
                else:
                    source_path.unlink()
                capture.verify_source()
        assert caught.value.code == "HOME_SOURCE_CHANGED" and caught.value.retriable


def test_heartbeat_changes_raw_binding_but_not_semantic_digest(env):
    value = state(env)
    value["lease"] = {"session_id": value["session_id"], "lease_token": str(uuid.uuid4()),
                       "generation": 1, "lease_until": STAMP}
    path = write(env, "sync-state.json", value)
    profile, identity = env
    with lifecycle.shared(profile):
        with home.capture_locked(profile) as capture:
            before_raw = deepcopy(capture.rawbindings)
            first = home.parse_bundle(capture.bundle, profile=profile, root_ids=ROOTS,
                                      member_id="mem_home_test", source_identity=identity)
    value["lease"]["lease_until"] = "2026-10-01T01:00:00.000000Z"
    value["status"]["updated_at"] = "2026-10-01T01:00:00.000000Z"
    path.write_bytes(_json_bytes(value))
    with lifecycle.shared(profile):
        with home.capture_locked(profile) as capture:
            second = home.parse_bundle(capture.bundle, profile=profile, root_ids=ROOTS,
                                       member_id="mem_home_test", source_identity=identity)
            assert capture.rawbindings != before_raw
    assert not first.blockers and not second.blockers and first.digest == second.digest


def test_private_url_tokens_and_snapshot_body_are_not_in_results(env):
    source_map(env)
    outbox(env)
    value = state(env)
    lease_token = str(uuid.uuid4())
    value["lease"] = {"session_id": value["session_id"], "lease_token": lease_token,
                       "generation": 1, "lease_until": STAMP}
    write(env, "sync-state.json", value)
    result = scan(env)
    assert not result.blockers
    for raw in (result.canonical_bytes(), json.dumps(result.public_summary()).encode()):
        for secret in (b"PRIVATE_QUERY", b"PRIVATE_SIGNATURE", b"PRIVATE SNAPSHOT BODY", b"private-sync.example.test", lease_token.encode()):
            assert secret not in raw


def test_hardlinked_source_map_is_not_followed_or_modified(env, tmp_path):
    path, _ = source_map(env)
    outside = tmp_path / "outside.json"
    os.link(path, outside)
    before = outside.read_bytes()
    result = scan(env)
    assert {b.namespace for b in result.blockers} == {"sync_media"}
    assert outside.read_bytes() == before


def test_symlinked_outbox_directory_is_blocked_without_reading_external_files(env, tmp_path):
    directory = env[0].database.parent / "sync-outbox"
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / (str(uuid.uuid4()) + ".json")).write_bytes(b"PRIVATE_EXTERNAL_BODY")
    try:
        directory.symlink_to(outside, target_is_directory=True)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Windows file symbolic links require an unavailable privilege")
        raise
    before = tree(outside)
    result = scan(env)
    assert result.blockers and b"PRIVATE_EXTERNAL_BODY" not in result.canonical_bytes()
    assert tree(outside) == before


def test_capture_requires_existing_lifecycle_guard_and_preserves_cancellation(env):
    with pytest.raises(lifecycle.StorageLifecycleError):
        with home.capture_locked(env[0]):
            pass
    cancellation = RuntimeError("synthetic caller cancellation")
    def cancel():
        raise cancellation
    with lifecycle.shared(env[0]):
        with pytest.raises(RuntimeError) as caught:
            with home.capture_locked(env[0], check_cancelled=cancel):
                pass
        assert caught.value is cancellation


def test_parse_bundle_is_pure_after_capture_closes(env, monkeypatch):
    source_map(env)
    profile, identity = env
    with lifecycle.shared(profile):
        with home.capture_locked(profile) as capture:
            bundle = capture.bundle
    def forbidden(*args, **kwargs):
        pytest.fail("Pure parser must not access the source filesystem or construct writers")
    monkeypatch.setattr(Path, "open", forbidden)
    monkeypatch.setattr(sync_state, "StateStore", forbidden)
    monkeypatch.setattr(sync_sources, "SourceMap", forbidden)
    result = home.parse_bundle(bundle, profile=profile, root_ids=ROOTS, member_id="mem_home_test", source_identity=identity)
    assert not result.blockers and result.selectors


def test_pure_bundle_duplicate_records_are_blocked(env):
    outbox(env)
    profile, identity = env
    with lifecycle.shared(profile):
        with home.capture_locked(profile) as capture:
            bundle = capture.bundle
    selected = next(r for r in bundle.records if r.kind == "outbox")
    duplicate = replace(bundle, records=(*bundle.records, selected))
    result = home.parse_bundle(duplicate, profile=profile, root_ids=ROOTS, member_id="mem_home_test", source_identity=identity)
    assert "HOME_DUPLICATE_RECORD" in {b.code for b in result.blockers}


def test_pure_bundle_file_count_cannot_bypass_capture_limits(env):
    outbox(env, video="BV1First")
    outbox(env, video="BV1Second", sha=OTHER_SHA)
    profile, identity = env
    with lifecycle.shared(profile):
        with home.capture_locked(profile) as capture:
            bundle = capture.bundle
    result = home.parse_bundle(bundle, profile=profile, root_ids=ROOTS, member_id="mem_home_test",
                               source_identity=identity, limits=home.HomeLimits(max_files=1))
    assert "HOME_FILE_COUNT_LIMIT" in {b.code for b in result.blockers}


def test_false_limits_are_not_silently_replaced_by_defaults(env):
    profile, identity = env
    with lifecycle.shared(profile):
        with pytest.raises(ValueError):
            with home.capture_locked(profile, limits=False):
                pass
    with pytest.raises(ValueError):
        home.parse_bundle(home.CapturedBundle((), ()), profile=profile, root_ids=ROOTS,
                          member_id="mem_home_test", source_identity=identity, limits=False)


def test_nonstring_captured_error_is_controlled_and_never_exposes_payload(env):
    record = home.CapturedRecord("state", "sync-state.json", error={"PRIVATE_ERROR": "PRIVATE_TOKEN"})
    result = home.parse_bundle(home.CapturedBundle((record,), ()), profile=env[0], root_ids=ROOTS,
                               member_id="mem_home_test", source_identity=env[1])
    assert result.blockers and all(type(b.code) is str for b in result.blockers)
    assert b"PRIVATE_ERROR" not in result.canonical_bytes() and b"PRIVATE_TOKEN" not in result.canonical_bytes()
