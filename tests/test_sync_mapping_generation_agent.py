"""Original local mapping instances survive work, crash and recovery retries."""
from copy import deepcopy
import json
import hashlib
import threading
import uuid

import pytest

from reno import config, db, ingest, storage_admin, storage_lifecycle, sync_agent as agent, sync_sources, tasks
from reno.sync_protocol import SyncError
from reno.sync_state import StateStore
from tests.conftest import released16_env as env
from tests.test_sync_agent import cycle, install_import
from tests.test_sync_sources import STAMP


def local_cycle(remote, store):
    source, _ = agent._local()
    value = agent._Cycle(store, remote, source, agent.settings(), threading.Event())
    recovery = store.read()["recovery"]
    value.knowledge_generation, value.knowledge_stream = recovery["generation"], recovery["stream"]
    return value


@pytest.mark.parametrize("edge", ["after_generation", "before_map", "after_map", "before_link"])
def test_saved_generation_crash_reuses_original_seed_and_map(env, monkeypatch, edge):
    monkeypatch.setattr(sync_sources, "_now", lambda: STAMP)
    install_import(env, monkeypatch)
    original_update = StateStore.update
    original_record = sync_sources.SourceMap.record
    interrupted = False

    def update(store, changes):
        nonlocal interrupted
        work = changes.get("work")
        if work is not None and work.get("source_mapping_generation") and not interrupted:
            if edge == "after_generation" and work.get("source_mapping_id") is None:
                interrupted = True
                result = original_update(store, changes)
                raise OSError("controlled interruption after generation")
            if edge == "before_link" and work.get("source_mapping_id") is not None:
                interrupted = True
                raise OSError("controlled interruption before origin link")
        return original_update(store, changes)

    def record(store, *args, **kwargs):
        nonlocal interrupted
        assert storage_lifecycle.current_guard() is not None
        saved = StateStore(config.DATA, profile=storage_admin.profile()).read_observation()["work"]
        assert saved["source_mapping_generation"] == kwargs["mapping_generation"]
        assert saved["source_mapping_inputs"]["recorded_at"] == kwargs["recorded_at"]
        if edge == "before_map" and not interrupted:
            interrupted = True
            raise OSError("controlled interruption before map")
        result = original_record(store, *args, **kwargs)
        if edge == "after_map" and not interrupted:
            interrupted = True
            raise OSError("controlled interruption after immutable map")
        return result

    monkeypatch.setattr(StateStore, "update", update)
    monkeypatch.setattr(sync_sources.SourceMap, "record", record)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    first = store.read()["work"]
    assert first and first.get("source_mapping_generation") and first.get("source_mapping_inputs")
    assert first.get("source_mapping_id") is None
    assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)
    generation = first["source_mapping_generation"]
    seed = deepcopy(first["source_mapping_inputs"])
    path = config.DATA / "sync-sources" / "v2" / (generation + ".json")
    existed = path.read_bytes() if path.exists() else None
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("saved work cannot resolve another observation"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("saved work cannot be downloaded as a new instance"))
    monkeypatch.setattr(sync_sources, "_now", lambda: pytest.fail("saved work cannot take a new creation timestamp"))
    assert cycle(env)["state"] == "idle"
    wrapper = json.loads(path.read_bytes())
    assert wrapper["mapping_generation"] == generation
    assert wrapper["resolution"] == seed["resolution"] and wrapper["origin"]["recorded_at"] == seed["recorded_at"]
    if existed is not None:
        assert path.read_bytes() == existed
    assert len(list((config.DATA / "sync-sources" / "v2").glob("*.json"))) == 1
    assert not (config.DATA / "sync-sources" / "v1").exists()
    origin = env.publications[-1]["claims"][0]["origin"]
    assert origin == wrapper["origin"] and origin["origin_version"] == 1
    assert "mapping_generation" not in origin


def test_pipeline_failure_keeps_generation_for_ordinary_retry(env, monkeypatch):
    install_import(env, monkeypatch)
    original = agent.pipeline.run_video
    failed = False

    def pipeline(video_id, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            return {"asr": "ERROR: controlled first attempt"}
        return original(video_id, **kwargs)

    monkeypatch.setattr(agent.pipeline, "run_video", pipeline)
    assert cycle(env)["error_code"] == "SYNC_PROCESSING_FAILED"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    work = store.read()["work"]
    assert work["phase"] == "downloaded" and work["source_mapping_generation"]
    raw = (config.DATA / "sync-sources/v2" / (work["source_mapping_generation"] + ".json")).read_bytes()
    assert not any(isinstance(call, tuple) and call[0] == "fail" for call in env.calls)
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("pipeline retry must keep its source instance"))
    assert cycle(env)["state"] == "idle"
    assert (config.DATA / "sync-sources/v2" / (work["source_mapping_generation"] + ".json")).read_bytes() == raw


def test_receiver_bootstrap_preserves_saved_generation_until_current_claim_resumes(env, monkeypatch):
    install_import(env, monkeypatch)
    record = sync_sources.SourceMap.record
    interrupted = False

    def fail_before_map(*args, **kwargs):
        nonlocal interrupted
        if not interrupted:
            interrupted = True
            raise OSError("controlled map gap")
        return record(*args, **kwargs)

    monkeypatch.setattr(sync_sources.SourceMap, "record", fail_before_map)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved_work = deepcopy(store.read()["work"])
    generation = saved_work["source_mapping_generation"]
    assert saved_work.get("source_mapping_id") is None
    env.recovery.update(state="resume_pending", generation=1, revision=1)
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("recovery cannot choose another observation"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("recovery cannot choose another instance"))
    result = cycle(env)
    assert result["state"] == "idle" and result["recovery_bootstrapped"] is True
    assert store.read()["work"] == saved_work
    assert env.publications[-1]["claims"] == []
    assert not (config.DATA / "sync-sources/v2" / (generation + ".json")).exists()
    assert cycle(env)["state"] == "idle"
    mapping = json.loads((config.DATA / "sync-sources/v2" / (generation + ".json")).read_bytes())
    assert mapping["mapping_generation"] == generation
    assert mapping["resolution"] == saved_work["source_mapping_inputs"]["resolution"]
    assert mapping["origin"]["recorded_at"] == saved_work["source_mapping_inputs"]["recorded_at"]
    assert env.publications[-1]["claims"][0]["origin"] == mapping["origin"]
    assert store.read()["work"] is None
    assert len(list((config.DATA / "sync-sources/v2").glob("*.json"))) == 1


@pytest.mark.parametrize("map_exists", [False, True])
def test_renewed_claim_finishes_original_mapping_gap_before_reuse(env, monkeypatch, map_exists):
    install_import(env, monkeypatch)
    original_update, original_record = StateStore.update, sync_sources.SourceMap.record
    interrupted = False

    def update(store, changes):
        nonlocal interrupted
        work = changes.get("work")
        if map_exists and work is not None and work.get("source_mapping_id") and not interrupted:
            interrupted = True
            raise OSError("controlled link gap before claim renewal")
        return original_update(store, changes)

    def record(*args, **kwargs):
        nonlocal interrupted
        if not map_exists and not interrupted:
            interrupted = True
            raise OSError("controlled map gap before claim renewal")
        return original_record(*args, **kwargs)

    monkeypatch.setattr(StateStore, "update", update)
    monkeypatch.setattr(sync_sources.SourceMap, "record", record)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved = deepcopy(store.read()["work"])
    assert saved.get("source_mapping_id") is None
    path = config.DATA / "sync-sources/v2" / (saved["source_mapping_generation"] + ".json")
    assert path.exists() == map_exists
    old_bytes = path.read_bytes() if map_exists else None
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("renewal cannot resolve a new observation"))
    monkeypatch.setattr(ingest, "ingest_url", lambda *a, **k: pytest.fail("renewal cannot download a new instance"))
    monkeypatch.setattr(sync_sources, "_now", lambda: pytest.fail("renewal must use the original timestamp"))
    env.claims[0]["claim"].update(lease_token=str(uuid.uuid4()), lease_generation=2)
    assert cycle(env)["state"] == "idle"
    wrapper = json.loads(path.read_bytes())
    assert wrapper["mapping_generation"] == saved["source_mapping_generation"]
    assert wrapper["origin"]["recorded_at"] == saved["source_mapping_inputs"]["recorded_at"]
    assert env.publications[-1]["claims"][0]["claim"]["lease_generation"] == 2
    assert env.publications[-1]["claims"][0]["origin"] == wrapper["origin"]
    assert len(list(path.parent.glob("*.json"))) == 1
    if old_bytes is not None:
        assert path.read_bytes() == old_bytes


def test_local_epoch_restore_keeps_unfinished_generation_and_inputs(env, monkeypatch):
    install_import(env, monkeypatch)
    original = agent.pipeline.run_video
    first = True

    def fail_once(*args, **kwargs):
        nonlocal first
        if first:
            first = False
            return {"media": "ERROR: controlled local work gap"}
        return original(*args, **kwargs)

    monkeypatch.setattr(agent.pipeline, "run_video", fail_once)
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved = deepcopy(store.read()["work"])
    con = db.connect()
    con.execute("UPDATE meta SET value=CAST(value AS INTEGER)+1 WHERE key='library_epoch'")
    con.commit()
    con.close()
    monkeypatch.setattr(sync_sources, "resolve_url", lambda *a, **k: pytest.fail("local restore must not choose another instance"))
    assert cycle(env)["state"] == "idle"
    wrapper = json.loads((config.DATA / "sync-sources/v2" / (saved["source_mapping_generation"] + ".json")).read_bytes())
    assert wrapper["origin"]["mapping_id"] == saved["source_mapping_id"]
    assert wrapper["origin"]["recorded_at"] == saved["source_mapping_inputs"]["recorded_at"]


def test_renewed_claim_and_missing_claim_page_do_not_replace_mapping_instance(env, monkeypatch):
    install_import(env, monkeypatch)
    env.fail_publish = True
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    saved = deepcopy(store.read()["work"])
    claims = deepcopy(store.read()["claims"])
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        value = local_cycle(env, store)
        page = env.pull(value.source, value.lease(), claim_limit=1)["events"]
        value._merge_page(page, [])
        assert store.read()["work"] == saved and store.read()["claims"] == claims
        renewed = deepcopy(claims)
        renewed[0]["claim"].update(lease_token=str(uuid.uuid4()), lease_generation=2)
        value._merge_page(page, renewed)
        assert store.read()["work"]["source_mapping_generation"] == saved["source_mapping_generation"]
        assert store.read()["work"]["source_mapping_inputs"] == saved["source_mapping_inputs"]
        assert store.read()["work"]["lease_generation"] == 2


def test_restored_retired_work_cannot_run_or_change_generation_but_new_item_can(env, monkeypatch):
    monkeypatch.setattr(sync_sources, "_now", lambda: STAMP)
    install_import(env, monkeypatch)
    original_pipeline = agent.pipeline.run_video
    monkeypatch.setattr(agent.pipeline, "run_video", lambda *a, **k: {"asr": "ERROR: controlled work gap"})
    assert cycle(env)["state"] == "retry_wait"
    store = StateStore(config.DATA, profile=storage_admin.profile())
    original = store.read()
    old_work = deepcopy(original["work"])
    original_bytes = (config.DATA / "sync-state.json").read_bytes()
    from reno import sync_local_retirement
    with tasks.process_lock() as acquired, store.exclusive():
        assert acquired
        store.archive_progress("claim_changed")
        history = next((config.DATA / "sync-work-history").glob("*.json"))
        map_path = config.DATA / "sync-sources/v2" / (old_work["source_mapping_generation"] + ".json")
        map_bytes = map_path.read_bytes()
        selected = {"format_version": 1, "selected": [
            {"kind": "history_work", "history_name": history.name,
             "raw_sha256": hashlib.sha256(history.read_bytes()).hexdigest(),
             "work_key": sync_local_retirement.work_key(old_work)},
            {"kind": "source_map", "mapping_generation": old_work["source_mapping_generation"],
             "origin_mapping_id": old_work["source_mapping_id"], "raw_sha256": hashlib.sha256(map_bytes).hexdigest()},
        ]}
        store.update({"work": None, "claims": []})  # Explicit selection closes this synthetic work.
        preview = store.preview_retirement(selected)
        store.commit_retirement(selected, expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))
        # Restoring an old state image must retain the permanent evidence.
        (config.DATA / "sync-state.json").write_bytes(original_bytes)
        value = local_cycle(env, store)
        before_calls = list(env.calls)
        for action in (lambda: value.process_claim(), lambda: value.advance_work(deepcopy(old_work)),
                       lambda: value.saved_work_origin(old_work, original["claims"][0])):
            with pytest.raises(SyncError) as caught:
                action()
            assert caught.value.code == "SYNC_NO_RESEND_REFERENCE_RETIRED"
        assert env.calls == before_calls
        assert store.read()["work"]["source_mapping_generation"] == old_work["source_mapping_generation"]
        # Only an explicit new item/work can select another local instance.
        fresh_claim = deepcopy(original["claims"])
        fresh_claim[0]["item_id"] = fresh_claim[0]["claim"]["item_id"] = 2
        store.update({"work": None, "claims": fresh_claim})
        monkeypatch.setattr(agent.pipeline, "run_video", original_pipeline)
        bindings = value.process_claim()
        fresh_work = store.read()["work"]
        assert fresh_work["source_mapping_generation"] != old_work["source_mapping_generation"]
        assert bindings[0]["origin"]["mapping_id"] == old_work["source_mapping_id"]
        assert map_path.read_bytes() == map_bytes
