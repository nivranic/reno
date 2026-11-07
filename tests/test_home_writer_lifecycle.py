"""Real Home reference writers cooperate with GC on disposable profiles."""
from copy import deepcopy
import json
import multiprocessing
import os
import threading

import pytest

from reno import storage_admin, storage_lifecycle as lifecycle, sync_agent as agent, sync_state
from reno.sync_state import StateStore
from tests.test_report_lifecycle import exclusive_available
from tests.test_sync_agent import add_video, cycle, env, install_import
from tests.test_sync_state import binding, claim, snapshot, state_profile, store


def _exclusive(profile, entered, release, requested=None, attempted=None):
    if requested is not None:
        requested.wait(15)
    if attempted is not None:
        attempted.set()
    with lifecycle.exclusive(profile, timeout=15):
        entered.set()
        release.wait(15)


def _update(profile, attempted, done):
    value = StateStore(profile.database.parent, profile=profile)
    attempted.set()
    value.update({"seeded": True})
    done.set()


def _finish(process):
    process.join(10)
    if process.is_alive():
        process.terminate()
        process.join(5)


def recovery(store, epoch=1):
    return {"generation": 0, "revision": 0, "state": "running", "source_floor": None,
            "stream": {"library_id": store.read()["binding"]["target_library_id"], "library_epoch": epoch}}


def test_profileless_store_is_read_only_and_mutation_cannot_create_state(tmp_path):
    root = tmp_path / "data"
    value = StateStore(root)
    for operation in (lambda: value.initialize(binding(), 1),
                      lambda: value.update({"seeded": True}),
                      lambda: value.write_publication(snapshot(), [])):
        with pytest.raises(lifecycle.StorageLifecycleError) as caught:
            operation()
        assert caught.value.code == "STORAGE_PROFILE_REQUIRED"
    assert not root.exists()


def test_data_root_must_match_profile_without_creating_either(tmp_path):
    profile = state_profile(tmp_path / "declared", initialize=False)
    with pytest.raises(lifecycle.StorageLifecycleError) as caught:
        StateStore(tmp_path / "other", profile=profile)
    assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
    assert not profile.database.parent.exists() and not (tmp_path / "other").exists()


def test_state_writer_really_waits_for_another_process_exclusive(store):
    ctx = multiprocessing.get_context("spawn")
    entered, release, attempted, done = (ctx.Event() for _ in range(4))
    collector = ctx.Process(target=_exclusive, args=(store.profile, entered, release))
    writer = ctx.Process(target=_update, args=(store.profile, attempted, done))
    before = (store.data_dir / "sync-state.json").read_bytes()
    collector.start()
    try:
        assert entered.wait(10)
        writer.start()
        assert attempted.wait(10)
        assert not done.wait(0.25)
        assert (store.data_dir / "sync-state.json").read_bytes() == before
        release.set()
        assert done.wait(10)
    finally:
        release.set()
        _finish(collector)
        if writer.pid is not None:
            _finish(writer)
    assert collector.exitcode == writer.exitcode == 0
    assert store.read()["seeded"] is True


@pytest.mark.parametrize("operation", ["initialize", "update", "observe", "archive", "ack", "install",
                                       "retire", "review", "outbound", "outbox"])
def test_all_state_publications_hold_exact_profile_through_atomic_write(store, monkeypatch, operation):
    observed = recovery(store)
    store.observe_recovery(observed)
    store.update({"claims": [claim()], "work": {"item_id": 1, "lease_generation": 3,
                  "video_id": "BV1Test", "phase": "downloaded"}})
    payload = snapshot()
    metadata = store.write_publication(payload, [], recovery_stream=observed["stream"])
    store.install_publication(metadata, recovery_generation=0, recovery_stream=observed["stream"])
    writes = []
    original = sync_state._write
    def protected(directory, name, data, **kwargs):
        guard = lifecycle.require_guard(store.profile)
        guard.verify()
        writes.append(name)
        assert not exclusive_available(store.profile)
        return original(directory, name, data, **kwargs)
    monkeypatch.setattr(sync_state, "_write", protected)
    operations = {
        "initialize": lambda: store.initialize(store.read()["binding"], 2),
        "update": lambda: store.update({"seeded": True}),
        "observe": lambda: store.observe_recovery(recovery(store, 2)),
        "archive": lambda: store.archive_progress("recovery"),
        "ack": lambda: store.acknowledge_publication(metadata, recovery_generation=0,
            recovery_stream=observed["stream"], version=payload["version"], content_sha256="a" * 64,
            reports_sha256="b" * 64, preserve_work=False),
        "install": lambda: store.install_publication(metadata, recovery_generation=0,
            recovery_stream=observed["stream"]),
        "retire": lambda: store.retire_progress(recovery_generation=0, recovery_stream=observed["stream"], archive=True),
        "review": lambda: store.apply_review_page({"seeded": True}, claim_changes={"claims": [], "work": None},
            recovery=observed, archive_work=True),
        "outbound": lambda: store.advance_outbound({"stream": observed["stream"], "after": 1, "through": None},
            recovery_stream=observed["stream"]),
        "outbox": lambda: store.write_publication(snapshot(), [], recovery_stream=observed["stream"]),
    }
    operations[operation]()
    assert writes
    assert lifecycle.current_guard() is None
    assert exclusive_available(store.profile)


def test_heartbeat_new_epoch_waits_for_ex_and_archives_old_pending_refs(store):
    old_control = recovery(store)
    store.observe_recovery(old_control)
    metadata = store.write_publication(snapshot(), [], recovery_stream=old_control["stream"])
    store.install_publication(metadata, recovery_generation=0, recovery_stream=old_control["stream"])
    before = store.read()
    ctx = multiprocessing.get_context("spawn")
    entered, release = ctx.Event(), ctx.Event()
    collector = ctx.Process(target=_exclusive, args=(store.profile, entered, release))
    done, started, errors = threading.Event(), threading.Event(), []
    next_control = recovery(store, 2)
    def pulse():
        started.set()
        try:
            store.observe_recovery(next_control)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    collector.start()
    pulse_thread = threading.Thread(target=pulse)
    try:
        assert entered.wait(10)
        pulse_thread.start()
        assert started.wait(5) and not done.wait(0.25)
        # Do not wait on the writer's RLock to inspect the blocked state.
        assert json.loads((store.data_dir / "sync-state.json").read_bytes()) == before
        release.set()
        assert done.wait(10)
    finally:
        release.set()
        _finish(collector)
        if pulse_thread.ident is not None:
            pulse_thread.join(10)
    assert collector.exitcode == 0 and not errors
    after = store.read()
    assert after["recovery"] == next_control and after["pending_publication"] is None
    saved = [json.loads(path.read_bytes()) for path in (store.data_dir / "sync-work-history").glob("*.json")]
    assert len(saved) == 1 and saved[0]["pending_publication"] == metadata
    assert saved[0]["recovery"] == old_control


@pytest.mark.parametrize("exception_type", [OSError, FileNotFoundError, RuntimeError, ValueError])
@pytest.mark.parametrize("operation", ["initialize", "update", "outbox", "nested_archive"])
def test_cancellation_preserves_exact_exception_and_does_not_publish(store, exception_type, operation):
    sentinel = exception_type("caller cancellation")
    before = (store.data_dir / "sync-state.json").read_bytes()
    def cancelled():
        raise sentinel
    value = StateStore(store.data_dir, profile=store.profile, check_cancelled=cancelled)
    if operation == "nested_archive":
        # First two entry checks succeed; cancellation arises in the nested
        # archive operation under the outer mutation's filesystem sanitizer.
        store.observe_recovery(recovery(store))
        store.update({"claims": [claim()]})
        before = (store.data_dir / "sync-state.json").read_bytes()
        calls = [0]
        def nested_check():
            calls[0] += 1
            if calls[0] == 3:
                raise sentinel
        value.check_cancelled = nested_check
        context = lifecycle.shared(store.profile)
        action = lambda: value.observe_recovery(recovery(store, 2))
    else:
        from contextlib import nullcontext
        context = nullcontext()
        action = {"initialize": lambda: value.initialize(store.read()["binding"], 1),
                  "update": lambda: value.update({"seeded": True}),
                  "outbox": lambda: value.write_publication(snapshot(), [])}[operation]
    with context:
        with pytest.raises(exception_type) as caught:
            action()
    assert caught.value is sentinel
    assert (store.data_dir / "sync-state.json").read_bytes() == before
    assert not (store.data_dir / "sync-outbox").exists()
    assert not (store.data_dir / "sync-work-history").exists()
    assert exclusive_available(store.profile)


def test_freeze_has_no_gc_window_between_export_outbox_and_pending(env, monkeypatch):
    add_video()
    assert cycle(env)["state"] == "idle"
    profile = storage_admin.profile()
    value = StateStore(profile.database.parent, profile=profile)
    source, _ = agent._local()
    active = agent._Cycle(value, env, source, agent.settings(), threading.Event())
    control = value.read()["recovery"]
    active.knowledge_generation, active.knowledge_stream = control["generation"], control["stream"]
    ctx = multiprocessing.get_context("spawn")
    requested, attempted, entered, release = (ctx.Event() for _ in range(4))
    collector = ctx.Process(target=_exclusive, args=(profile, entered, release, requested, attempted))
    stages = []
    original_snapshot, original_outbox, original_pending = active.snapshot, value.write_publication, value.install_publication
    def export(con, **kwargs):
        result = original_snapshot(con, **kwargs)
        requested.set()
        assert attempted.wait(10)
        assert not entered.wait(0.15)
        stages.append("export")
        return result
    def outbox(*args, **kwargs):
        lifecycle.require_guard(profile)
        assert not entered.is_set()
        stages.append("outbox")
        return original_outbox(*args, **kwargs)
    def pending(*args, **kwargs):
        lifecycle.require_guard(profile)
        assert not entered.is_set()
        result = original_pending(*args, **kwargs)
        assert value.read()["pending_publication"] is not None
        stages.append("pending")
        return result
    monkeypatch.setattr(active, "snapshot", export)
    monkeypatch.setattr(value, "write_publication", outbox)
    monkeypatch.setattr(value, "install_publication", pending)
    collector.start()
    try:
        with value.exclusive():
            active.freeze([])
        assert stages == ["export", "outbox", "pending"]
        assert entered.wait(10)
    finally:
        requested.set()
        release.set()
        _finish(collector)
    assert collector.exitcode == 0
    assert value.read()["pending_publication"] is not None


def test_agent_proof_map_and_work_install_use_one_guard_without_pipeline_or_network(env, monkeypatch):
    from reno import sync_sources
    install_import(env, monkeypatch)
    stages = []
    original_record, original_update, original_advance = sync_sources.SourceMap.record, StateStore.update, agent._Cycle.advance_work
    original_proof = sync_sources._asset_sha
    held = []
    seed = []
    origins = []
    def proof(*args, **kwargs):
        guard = lifecycle.require_guard(kwargs["profile"])
        assert guard is held[0] and stages == ["seed"]
        result = original_proof(*args, **kwargs)
        assert lifecycle.require_guard(kwargs["profile"]) is guard
        stages.append("proof")
        return result
    def record(self, *args, **kwargs):
        guard = lifecycle.require_guard(self.profile)
        assert guard is held[0] and stages == ["seed"]
        saved = StateStore(self.data_root, profile=self.profile).read_observation()["work"]
        assert saved == seed[0]
        assert saved.get("source_mapping_id") is None
        assert saved["source_mapping_generation"] == kwargs["mapping_generation"]
        assert saved["source_mapping_inputs"]["recorded_at"] == kwargs["recorded_at"]
        assert saved["source_mapping_inputs"]["media_sha256"] == kwargs["asset"]["content_sha256"]
        result = original_record(self, *args, **kwargs)
        assert lifecycle.require_guard(self.profile) is guard
        origins.append(result)
        stages.append("map")
        return result
    def update(self, changes):
        work = changes.get("work")
        if work is None or work.get("phase") != "downloaded":
            return original_update(self, changes)
        guard = lifecycle.require_guard(self.profile)
        if not held:
            assert not stages and work.get("source_mapping_id") is None
            assert work["source_mapping_generation"] and work["source_mapping_inputs"]
            held.append(guard)
            stages.append("seed")
        else:
            assert guard is held[0] and stages == ["seed", "proof", "map"]
            assert work["source_mapping_generation"] == seed[0]["source_mapping_generation"]
            assert work["source_mapping_inputs"] == seed[0]["source_mapping_inputs"]
            assert work["source_mapping_id"] == origins[0]["mapping_id"]
            stages.append("link")
        result = original_update(self, changes)
        assert lifecycle.require_guard(self.profile) is guard
        assert self.read_observation()["work"] == work
        if not seed:
            seed.append(deepcopy(work))
        return result
    def advance(self, work):
        assert lifecycle.current_guard() is None
        assert stages == ["seed", "proof", "map", "link"]
        stages.append("pipeline")
        return original_advance(self, work)
    monkeypatch.setattr(sync_sources, "_asset_sha", proof)
    monkeypatch.setattr(sync_sources.SourceMap, "record", record)
    monkeypatch.setattr(StateStore, "update", update)
    monkeypatch.setattr(agent._Cycle, "advance_work", advance)
    assert cycle(env)["state"] == "idle"
    assert stages == ["seed", "proof", "map", "link", "pipeline"]


def test_long_proof_lifetime_allows_heartbeat_to_publish_new_fence(store):
    old = recovery(store)
    store.observe_recovery(old)
    newer = {**old, "generation": 1, "revision": 1, "state": "held"}
    done, errors = threading.Event(), []
    def pulse():
        try:
            store.observe_recovery(newer)
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    with store.exclusive():
        with store.reference_lifetime():
            guard = lifecycle.require_guard(store.profile)
            thread = threading.Thread(target=pulse)
            thread.start()
            assert done.wait(10), "Long proof paused heartbeat recovery/lease merge"
            thread.join(5)
            assert not errors
            assert store.read_observation()["recovery"] == newer
            assert lifecycle.require_guard(store.profile) is guard
            assert not store.install_publication(store.write_publication(snapshot(), []),
                recovery_generation=0, recovery_stream=old["stream"])
    assert exclusive_available(store.profile)


def test_observer_does_not_wait_for_the_writer_rlock(store):
    entered, release = threading.Event(), threading.Event()
    def writer():
        with store._guard:
            entered.set()
            release.wait(10)
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        assert entered.wait(5)
        with lifecycle.shared(store.profile):
            assert store.read_observation()["seeded"] is False
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()


def test_observer_and_atomic_replaces_remain_whole_images_without_writer_failure(store):
    done, errors, observed = threading.Event(), [], []
    def writer():
        try:
            for index in range(40):
                store.update({"seeded": bool(index % 2)})
        except BaseException as exc:
            errors.append(exc)
        finally:
            done.set()
    thread = threading.Thread(target=writer)
    thread.start()
    try:
        while not done.is_set():
            observed.append(store.read_observation())
    finally:
        thread.join(10)
    assert not errors and observed
    assert all(type(item["seeded"]) is bool and item["binding"] == store.read()["binding"] for item in observed)


def test_reference_lifetime_requires_prior_os_ownership(store):
    with pytest.raises(sync_state.SyncError) as caught:
        with store.reference_lifetime():
            pytest.fail("A proof lifetime cannot invent StateStore ownership")
    assert caught.value.code == "SYNC_STATE_OWNERSHIP_REQUIRED"
    assert lifecycle.current_guard() is None


def test_private_write_cleanup_does_not_replace_the_primary_exception(store, monkeypatch):
    sentinel, cleanup = RuntimeError("primary failure"), OSError("cleanup failure")
    original = sync_state.media._Directory.entry_stat
    failed = [False]
    def fail_replace(*args):
        failed[0] = True
        raise sentinel
    def entry(directory, name):
        if failed[0] and name.endswith(".part"):
            raise cleanup
        return original(directory, name)
    monkeypatch.setattr(sync_state, "_replace", fail_replace)
    monkeypatch.setattr(sync_state.media._Directory, "entry_stat", entry)
    with store.reference_operation():
        with pytest.raises(RuntimeError) as caught:
            sync_state._write(store._directory, "sync-state.json", b"unused")
    assert caught.value is sentinel


def test_atomic_observer_rejects_hardlinked_state_without_touching_outside(store, tmp_path):
    target, outside = store.data_dir / "sync-state.json", tmp_path / "outside-state"
    before = target.read_bytes()
    target.unlink()
    outside.write_bytes(before)
    os.link(outside, target)
    try:
        with pytest.raises(sync_state.SyncError) as caught:
            store.read_observation()
        assert caught.value.code == "SYNC_STATE_UNSAFE_PATH"
        assert outside.read_bytes() == before
    finally:
        target.unlink()


def test_freeze_proof_does_not_pause_multiple_heartbeat_network_and_state_merges(env, monkeypatch):
    assert cycle(env)["state"] == "idle"
    profile = storage_admin.profile()
    value = StateStore(profile.database.parent, profile=profile)
    source, _ = agent._local()
    active = agent._Cycle(value, env, source, agent.settings(), threading.Event())
    observed = value.read()["recovery"]
    active.knowledge_generation, active.knowledge_stream = observed["generation"], observed["stream"]
    merged, count = threading.Event(), [0]
    original_observe, original_snapshot = value.observe_recovery, active.snapshot
    def observe(*args, **kwargs):
        result = original_observe(*args, **kwargs)
        count[0] += 1
        if count[0] >= 3:
            merged.set()
        return result
    def slow_proof(con, **kwargs):
        lifecycle.require_guard(profile)
        assert merged.wait(5), "Freeze proof blocked subsequent heartbeat request/merge"
        return original_snapshot(con, **kwargs)
    monkeypatch.setattr(agent, "HEARTBEAT_SECONDS", 0.01)
    monkeypatch.setattr(value, "observe_recovery", observe)
    monkeypatch.setattr(active, "snapshot", slow_proof)
    before_network = sum(isinstance(call, tuple) and call[0] == "heartbeat" for call in env.calls)
    with value.exclusive():
        with active.heartbeat():
            active.freeze([])
    after_network = sum(isinstance(call, tuple) and call[0] == "heartbeat" for call in env.calls)
    assert count[0] >= 3 and after_network - before_network >= 3
    assert not active.lost.is_set() and value.read()["pending_publication"] is not None


def test_observer_opened_image_survives_replace_before_first_handle_stat(store, monkeypatch):
    target = store.data_dir / "sync-state.json"
    triggered = [False]
    if os.name == "nt":
        kernel = sync_state.media._kernel
        original_open, original_info = kernel.CreateFileW, kernel.GetFileInformationByHandle
        observer_handle = [None]
        def opened(path, *args):
            handle = original_open(path, *args)
            if str(path) == sync_state.media._native_path(target) and args[1] == 5:
                observer_handle[0] = handle
            return handle
        def info(handle, buffer):
            if handle == observer_handle[0] and not triggered[0]:
                triggered[0] = True
                store.update({"seeded": True})
            return original_info(handle, buffer)
        monkeypatch.setattr(kernel, "CreateFileW", opened)
        monkeypatch.setattr(kernel, "GetFileInformationByHandle", info)
    else:
        original_stat, old_inode = os.fstat, target.stat().st_ino
        def info(fd):
            before = original_stat(fd)
            if before.st_ino == old_inode and not triggered[0]:
                triggered[0] = True
                store.update({"seeded": True})
            return original_stat(fd)
        monkeypatch.setattr(os, "fstat", info)
    observed = store.read_observation()
    assert triggered[0]
    assert observed["seeded"] is False  # Its open handle still pins the old image.
    assert store.read()["seeded"] is True  # The pathname now names the new image.


@pytest.mark.parametrize("exception_type", [OSError, RuntimeError, KeyboardInterrupt])
def test_connection_cleanup_preserves_primary_snapshot_or_cancellation_failure(env, monkeypatch, exception_type):
    profile = storage_admin.profile()
    value = StateStore(profile.database.parent, profile=profile)
    source, _ = agent._local()
    active = agent._Cycle(value, env, source, agent.settings(), threading.Event())
    sentinel, opened, closed = exception_type("caller failure"), [], []
    class BrokenClose:
        def close(self):
            closed.append(True)
            raise OSError("secondary close failure")
    def connect(path):
        assert path == profile.database
        lifecycle.require_guard(profile)
        opened.append(path)
        return BrokenClose()
    monkeypatch.setattr(agent.db, "connect", connect)
    with pytest.raises(exception_type) as caught:
        with active.storage_connection():
            raise sentinel
    assert caught.value is sentinel and opened == [profile.database] and closed == [True]
    assert lifecycle.current_guard() is None and exclusive_available(profile)
