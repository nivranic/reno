"""Local persistence tests use disposable files and actual process locks."""
from copy import deepcopy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import threading
import uuid

import pytest

from reno import storage_lifecycle as lifecycle, sync_snapshot, sync_state
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore


STAMP = "2026-09-30T10:00:00.000Z"


def binding():
    return {**{key: str(uuid.uuid4()) for key in (
        "home_library_id", "home_node_id", "target_library_id", "target_node_id")},
        "target_url": "https://sync.example.test/reno"}


def snapshot():
    return {"publication_id": str(uuid.uuid4()),
            "version": {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 4},
            "videos": [], "globals": {"knowledge_cluster": [], "conflict_case": []}, "reports": []}


def claim(item=1):
    return {"item_id": item, "url": "https://www.bilibili.com/video/BV1Test?private=raw",
            "attempts": 1, "lease_until": STAMP,
            "claim": {"item_id": item, "lease_token": str(uuid.uuid4()), "lease_generation": 3}}


def state_profile(root, *, initialize=True):
    root = Path(root).absolute()
    profile = lifecycle.StorageProfile(root / "reno.db", root.parent / "media",
                                       root.parent / "reports", root.parent / "checkpoints")
    if initialize:
        lifecycle.initialize(profile)
    return profile


@pytest.fixture()
def store(tmp_path):
    profile = state_profile(tmp_path / "data")
    result = StateStore(profile.database.parent, profile=profile)
    result.initialize(binding(), 1)
    return result


def _hold(root, entered, release):
    with StateStore(root).exclusive():
        entered.set()
        release.wait(20)


def _crash_before_rename(root, ready):
    def crash(*_):
        ready.set()
        os._exit(23)
    sync_state._replace = crash
    StateStore(root, profile=state_profile(root, initialize=False)).update({"seeded": True})


def _stop(process):
    process.join(5)
    if process.is_alive():
        process.terminate()
        process.join(5)


def _directory_link(link, target):
    if os.name == "nt":
        run = subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)],
                             capture_output=True, creationflags=subprocess.CREATE_NO_WINDOW)
        assert run.returncode == 0
    else:
        link.symlink_to(target, target_is_directory=True)


def test_defaults_are_persistent_and_returns_are_detached(store):
    original = store.read()
    assert original["format_version"] == 1 and original["seeded"] is False
    assert original["published_content_sha256"] is None
    assert original["published_reports_sha256"] is None
    assert original["inbound"] == {"stream": None, "after": 0, "through": None}
    copy = store.initialize(original["binding"], 1)
    assert copy == original
    copy["status"]["state"] = "changed"
    assert store.read() == original
    assert StateStore(store.data_dir).read() == original


def test_binding_and_version_cannot_change(store):
    old = store.read()
    for change in ({"binding": binding()}, {"format_version": 2}, {"format_version": True}):
        with pytest.raises(SyncError):
            store.update(change)
        assert store.read() == old
    with pytest.raises(SyncError, match="different binding"):
        store.initialize(binding(), 1)
    assert store.read() == old


def test_epoch_increase_retains_cursor_and_original_publication(store):
    saved = store.write_publication(snapshot(), [])
    old = store.read()
    cursor = {"stream": {"library_id": old["binding"]["target_library_id"], "library_epoch": 4},
              "after": 10, "through": 30}
    store.update({"inbound": cursor, "pending_publication": saved})
    current = store.initialize(old["binding"], 2)
    assert current["inbound"] == cursor and current["pending_publication"] == saved
    assert store.read_publication(saved)["snapshot"]["version"]["library_epoch"] == 1
    with pytest.raises(SyncError, match="backwards"):
        store.update({"local_epoch": 1})
    with pytest.raises(SyncError, match="backwards"):
        store.initialize(old["binding"], 1)
    rotated = str(uuid.uuid4())
    assert store.update({"session_id": rotated})["session_id"] == rotated


def test_work_accepts_optional_frozen_source_mapping_id(store):
    work = {"item_id": 2, "lease_generation": 3, "video_id": "BV1Test", "phase": "downloaded"}
    assert store.update({"work": work})["work"] == work
    mapped = {**work, "source_mapping_id": "a" * 64}
    assert store.update({"work": mapped})["work"] == mapped
    assert StateStore(store.data_dir).read()["work"] == mapped
    for invalid in (None, 3, "A" * 64, "../../secret", "a" * 63):
        with pytest.raises(SyncError):
            store.update({"work": {**work, "source_mapping_id": invalid}})
        assert store.read()["work"] == mapped


@pytest.mark.parametrize("field", ["published_content_sha256", "published_reports_sha256"])
def test_published_digest_is_strict_and_persistent(store, field):
    assert store.update({field: "a" * 64})[field] == "a" * 64
    assert StateStore(store.data_dir).read()[field] == "a" * 64
    for invalid in (True, 5, "A" * 64, "a" * 63, "https://secret.example"):
        with pytest.raises(SyncError):
            store.update({field: invalid})
    assert store.read()[field] == "a" * 64
    assert store.update({field: None})[field] is None


@pytest.mark.parametrize("changes", [
    {"unknown": 1}, {"seeded": 1}, {"local_epoch": True}, {"local_epoch": 0},
    {"session_id": "not-a-uuid"}, {"claims": [claim(), claim()]},
    {"claims": [{**claim(), "extra": "secret"}]}, {"claims": [claim(i + 1) for i in range(51)]},
    {"inbound": {"stream": None, "after": 1, "through": None}},
    {"inbound": {"stream": {"library_id": str(uuid.uuid4()), "library_epoch": 1}, "after": 4, "through": 3}},
    {"work": {"item_id": 1, "lease_generation": 2, "video_id": "BV1Test", "phase": "invented"}},
    {"uploaded_media": {"../../private": {}}}, {"pending_publication": {"path": "secret"}},
    {"status": {"state": "url-with-secret", "error_code": "https://secret.test", "updated_at": STAMP,
                "last_success_at": None, "retry_at": None}},
])
def test_malformed_updates_leave_state_unchanged(store, changes):
    before = (store.data_dir / "sync-state.json").read_bytes()
    with pytest.raises(SyncError):
        store.update(changes)
    assert (store.data_dir / "sync-state.json").read_bytes() == before


@pytest.mark.parametrize("data", [b'{"inbound":', b'{"format_version":1,"format_version":1}',
                                  b'{"unknown":NaN}', b'\xff', b'[]'])
def test_corrupt_existing_state_is_never_reinitialized(store, data):
    identity = store.read()["binding"]
    path = store.data_dir / "sync-state.json"
    path.write_bytes(data)
    with pytest.raises(SyncError):
        store.initialize(identity, 1)
    with pytest.raises(SyncError):
        store.update({"seeded": True})
    assert path.read_bytes() == data


def test_missing_read_does_not_create_files(tmp_path):
    root = tmp_path / "absent"
    with pytest.raises(SyncError) as error:
        StateStore(root).read()
    assert error.value.code == "SYNC_STATE_MISSING" and not root.exists()


def test_state_size_depth_and_nodes_are_bounded(store, monkeypatch):
    old = store.read()
    for changes in ({"work": {"nested": [[[[[[[[[[[[[[[[[[[]]]]]]]]]]]]]]]]]]]}},
                    {"work": {"large": "x" * sync_state.MAX_STATE_BYTES}},
                    {"work": {"nodes": [None] * (sync_state.MAX_STATE_NODES + 1)}}):
        with pytest.raises(SyncError):
            store.update(changes)
    assert store.read() == old


def test_real_process_exclusion_and_release(store):
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    owner = ctx.Process(target=_hold, args=(str(store.data_dir), entered, release))
    owner.start()
    try:
        assert entered.wait(10)
        for action in (lambda: store.update({"seeded": True}), lambda: store.initialize(store.read()["binding"], 1)):
            with pytest.raises(SyncError) as error:
                action()
            assert error.value.code == "SYNC_BUSY"
        with pytest.raises(SyncError) as error:
            with store.exclusive():
                pytest.fail("A second syncer acquired the lock")
        assert error.value.code == "SYNC_BUSY"
    finally:
        release.set()
        _stop(owner)
    assert owner.exitcode == 0
    assert store.update({"seeded": True})["seeded"] is True


def test_pulse_thread_updates_while_main_holds_exclusive_and_survives_merge(store):
    old = store.read()
    lease = {"session_id": old["session_id"], "lease_token": str(uuid.uuid4()),
             "generation": 2, "lease_until": STAMP}
    done, errors = threading.Event(), []
    def pulse():
        try:
            store.update({"lease": lease, "claims": [claim()]})
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    with store.exclusive():
        worker = threading.Thread(target=pulse, daemon=True)
        worker.start()
        assert done.wait(5), "Heartbeat blocked behind whole-run thread lock"
        worker.join(5)
        assert not errors
        current = store.update({"seeded": True})
        assert current["lease"] == lease and len(current["claims"]) == 1
        assert current["seeded"] is True


def test_update_reads_latest_disk_state_across_instances(store):
    other = StateStore(store.data_dir, profile=store.profile)
    other.update({"claims": [claim()]})
    assert len(store.update({"seeded": True})["claims"]) == 1


def test_exclusive_does_not_mask_caller_exception_and_releases_lock(store):
    sentinel = ValueError("caller operation failed")
    with pytest.raises(ValueError) as error:
        with store.exclusive():
            raise sentinel
    assert error.value is sentinel
    assert store.update({"seeded": True})["seeded"] is True


def test_interrupted_replace_preserves_old_state(store, monkeypatch):
    before = store.read()
    def fail(*_):
        raise OSError("secret file path must not escape")
    monkeypatch.setattr(sync_state, "_replace", fail)
    with pytest.raises(SyncError) as error:
        store.update({"seeded": True})
    assert "secret" not in str(error.value)
    assert store.read() == before
    assert not list(store.data_dir.glob("*.part"))


def test_process_crash_keeps_old_state_and_ignores_stale_part(store):
    old = store.read()
    ctx = multiprocessing.get_context("spawn")
    ready = ctx.Event()
    process = ctx.Process(target=_crash_before_rename, args=(str(store.data_dir), ready))
    process.start()
    try:
        assert ready.wait(10)
    finally:
        _stop(process)
    assert process.exitcode == 23
    assert StateStore(store.data_dir).read() == old
    assert list(store.data_dir.glob("*.part"))
    assert store.update({"seeded": True})["seeded"] is True


def test_outbox_roundtrip_idempotency_and_immutable_collision(store):
    payload = snapshot()
    metadata = store.write_publication(payload, [])
    path = store.data_dir / "sync-outbox" / (payload["publication_id"] + ".json")
    raw = path.read_bytes()
    assert metadata["sha256"] == hashlib.sha256(raw).hexdigest()
    assert metadata["byte_size"] == len(raw)
    assert store.write_publication(payload, []) == metadata
    assert store.read_publication(metadata) == {"snapshot": payload, "claims": [], "recovery_generation": 0, "recovery_stream": None}
    payload["version"]["kb_revision"] += 1
    with pytest.raises(SyncError) as error:
        store.write_publication(payload, [])
    assert error.value.code == "SYNC_OUTBOX_CONFLICT" and path.read_bytes() == raw
    other = snapshot()
    store.write_publication(other, [])
    assert path.read_bytes() == raw and len(list(path.parent.glob("*.json"))) == 2


def test_tampered_publication_is_not_accepted(store):
    payload = snapshot()
    metadata = store.write_publication(payload, [])
    path = store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")
    path.write_bytes(path.read_bytes().replace(b'"kb_revision":4', b'"kb_revision":5'))
    with pytest.raises(SyncError) as error:
        store.read_publication(metadata)
    assert error.value.code == "SYNC_OUTBOX_CORRUPT"
    with pytest.raises(SyncError):
        store.read_publication({**metadata, "publication_id": "../../node-identity"})


@pytest.mark.parametrize("with_origin", [False, True])
def test_claims_and_original_publication_uuid_are_frozen_across_recovery(store, with_origin):
    payload = snapshot()
    tables = {table: [] for table in sync_snapshot.VIDEO_TABLES}
    tables["video_asset"] = [{"video_id": "BV1Test", "status": "processed", "content_sha256": "a" * 64}]
    payload["videos"] = [{"video_id": "BV1Test", "tables": tables,
                          "media": {"video_id": "BV1Test", "sha256": "a" * 64,
                                    "byte_size": 20, "extension": "mp4"}}]
    original_claim = {"video_id": "BV1Test", "claim": claim()["claim"]}
    if with_origin:
        from reno.sync_claims import _origin_digest
        origin = {"origin_version": 1, "source_library_id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()),
                  "submitted_url": "https://b23.tv/alias", "resolved_url": "https://www.bilibili.com/video/BV1Test",
                  "resolution_sha256": "b" * 64, "extracted_video_id": "BV1Test", "video_id": "BV1Test",
                  "media_sha256": "a" * 64, "resolved_at": "2026-09-30T10:00:00.000000Z",
                  "recorded_at": "2026-09-30T10:00:01.000000Z"}
        origin["mapping_id"] = _origin_digest(origin)
        original_claim["origin"] = origin
    metadata = store.write_publication(payload, [original_claim])
    store.update({"pending_publication": metadata})
    restarted = StateStore(store.data_dir, profile=store.profile)
    frozen = restarted.read_publication(restarted.read()["pending_publication"])
    assert frozen["snapshot"]["publication_id"] == payload["publication_id"]
    assert frozen["claims"] == [original_claim]
    new_claim = deepcopy(original_claim)
    new_claim["claim"]["lease_token"] = str(uuid.uuid4())
    with pytest.raises(SyncError) as error:
        restarted.write_publication(payload, [new_claim])
    assert error.value.code == "SYNC_OUTBOX_CONFLICT"
    assert restarted.read_publication(metadata) == frozen
    if with_origin:
        changed_origin = deepcopy(original_claim)
        changed_origin["origin"]["resolution_sha256"] = "c" * 64
        changed_origin["origin"]["mapping_id"] = _origin_digest(changed_origin["origin"])
        with pytest.raises(SyncError) as error:
            restarted.write_publication(payload, [changed_origin])
        assert error.value.code == "SYNC_OUTBOX_CONFLICT"
        assert restarted.read_publication(metadata) == frozen


def test_partial_outbox_is_rejected_not_overwritten(store):
    payload = snapshot()
    metadata = store.write_publication(payload, [])
    path = store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")
    path.write_bytes(b'{"snapshot":')
    with pytest.raises(SyncError) as error:
        store.read_publication(metadata)
    assert error.value.code == "SYNC_OUTBOX_CORRUPT"
    with pytest.raises(SyncError) as error:
        store.write_publication(payload, [])
    assert error.value.code == "SYNC_OUTBOX_CONFLICT" and path.read_bytes() == b'{"snapshot":'


def test_missing_outbox_is_an_explicit_error(store):
    metadata = {"publication_id": str(uuid.uuid4()), "sha256": "a" * 64, "byte_size": 20}
    with pytest.raises(SyncError) as error:
        store.read_publication(metadata)
    assert error.value.code == "SYNC_OUTBOX_MISSING"


def test_outbox_hardlink_is_rejected(store, tmp_path):
    metadata = store.write_publication(snapshot(), [])
    path = store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")
    other = tmp_path / "external-publication"
    os.link(path, other)
    try:
        with pytest.raises(SyncError) as error:
            store.read_publication(metadata)
        assert error.value.code == "SYNC_STATE_UNSAFE_PATH"
    finally:
        other.unlink()


def test_outbox_maximum_and_missing_metadata(store, monkeypatch):
    monkeypatch.setattr(sync_state, "MAX_OUTBOX_BYTES", 32)
    with pytest.raises(SyncError):
        store.write_publication(snapshot(), [])
    assert not (store.data_dir / "sync-outbox").exists()


@pytest.mark.parametrize("filename", ["sync-state.json", "sync-state.lock"])
def test_hardlinks_are_rejected_without_touching_external_files(store, tmp_path, filename):
    outside = tmp_path / "outside-secret"
    outside.write_bytes(b"private must remain")
    target = store.data_dir / filename
    target.unlink()
    os.link(outside, target)
    try:
        with pytest.raises(SyncError):
            store.update({"seeded": True})
        assert outside.read_bytes() == b"private must remain"
    finally:
        target.unlink()


@pytest.mark.parametrize("location", ["root", "outbox"])
def test_symlink_or_junction_directories_are_rejected(tmp_path, location):
    outside = tmp_path / "external"
    outside.mkdir()
    marker = outside / "personal.db"
    marker.write_bytes(b"personal")
    root = tmp_path / "data"
    if location == "root":
        profile = state_profile(root)
        (root / lifecycle.LOCK_NAME).unlink()
        root.rmdir()
        linked = root
        _directory_link(linked, outside)
        action = lambda: StateStore(root, profile=profile).initialize(binding(), 1)
    else:
        profile = state_profile(root)
        linked = root / "sync-outbox"
        _directory_link(linked, outside)
        action = lambda: StateStore(root, profile=profile).write_publication(snapshot(), [])
    try:
        with pytest.raises((SyncError, lifecycle.StorageLifecycleError)):
            action()
        assert marker.read_bytes() == b"personal" and sorted(p.name for p in outside.iterdir()) == ["personal.db"]
    finally:
        linked.rmdir() if os.name == "nt" else linked.unlink()


def test_personal_domain_and_identity_are_untouched(store):
    files = {"personal.db": b"personal database", "node-identity.json": b"identity",
             "reno.db": b"knowledge database", "attachments/private.txt": b"attachment"}
    for relative, data in files.items():
        path = store.data_dir / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    store.update({"claims": [claim()], "seeded": True})
    metadata = store.write_publication(snapshot(), [])
    store.update({"pending_publication": metadata})
    store.read_publication(metadata)
    for relative, data in files.items():
        assert (store.data_dir / relative).read_bytes() == data
    if os.name != "nt":
        for path in (store.data_dir / "sync-state.json", store.data_dir / "sync-state.lock",
                     store.data_dir / "sync-outbox" / (metadata["publication_id"] + ".json")):
            assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("url", ["https://user:password@example.test", "https://example.test?token=secret",
                                  "https://example.test/#secret", "http://example.test", "https://example.test/../secret"])
def test_uncontrolled_target_urls_never_persist(tmp_path, url):
    with pytest.raises(SyncError) as error:
        StateStore(tmp_path / "data").initialize({**binding(), "target_url": url}, 1)
    assert "password" not in str(error.value) and "secret" not in str(error.value)
    assert not (tmp_path / "data" / "sync-state.json").exists()


def test_controlled_loopback_url_normalization(tmp_path):
    profile = state_profile(tmp_path / "data")
    value = StateStore(profile.database.parent, profile=profile).initialize(
        {**binding(), "target_url": "http://localhost:8765/"}, 1)
    assert value["binding"]["target_url"] == "http://127.0.0.1:8765"
