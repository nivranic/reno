"""Actual Cycle/outbox/descriptor/CAS boundaries; only external data is synthetic."""
from copy import deepcopy
import hashlib

import pytest

from reno import report, storage_admin, sync_agent, sync_sources, sync_state
from reno import sync_source_lifecycle17 as publications, sync_source_authority as permanent
from reno.sync_source_opportunity17 import ReadmissionRequired17, consume_required17
from reno.sync_protocol import SyncError
from tests.test_sync_agent import env, add_video
from tests.test_sync_source_genesis17 import home17, live17, create


@pytest.fixture
def publication17(live17):
    value = live17
    value.core = create(value)
    video_id = "publication17-video"
    add_video(video_id)
    with storage_admin.connection(value.profile) as con:
        asset = dict(con.execute("SELECT * FROM video_asset WHERE video_id=?", (video_id,)).fetchone())
    url = value.remote.entry["url"]
    source_map = sync_sources.SourceMap(value.profile.database.parent, profile=value.profile,
        source_library_id=value.source["library_id"], source_node_id=value.source["node_id"])
    resolved = {"resolution_version": 1, "submitted_url": url, "resolved_url": url,
        "resolved_at": "2026-01-01T00:00:00.000000Z", "hops": [
            {"url": url, "peer_ip": "8.8.8.8", "status": 200, "next_url": None}]}
    origin = source_map.record(resolved, extracted_video_id=url.rsplit("/", 1)[1], asset=asset)
    value.bindings = [{"video_id": video_id, "claim": deepcopy(value.remote.entry["claim"]), "origin": origin}]
    report.run_all(profile=value.profile)
    value.cycle = sync_agent._Cycle(value.store, value.client, value.source, sync_agent.settings(), value.stop)
    value.cycle.knowledge_generation = value.remote.recovery["generation"]
    value.cycle.knowledge_stream = deepcopy(value.remote.recovery["stream"])
    return value


def outboxes(value):
    return {path.name: path.read_bytes() for path in (value.profile.database.parent / "sync-outbox").glob("*.json")}


def descriptors(value):
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        return deepcopy(owner.observe()["entries"]["publications"])


def snapshot(value):
    with value.store.reference_operation(), value.cycle.storage_connection() as con:
        con.execute("BEGIN")
        return value.cycle.snapshot(con, verify=True)


def factory(value, selected, bindings=None):
    return publications.freeze_publication17_locked(profile=value.profile, store=value.store,
        client=value.client, snapshot=selected, claims=value.bindings if bindings is None else bindings,
        recovery_generation=value.cycle.knowledge_generation, recovery_stream=value.cycle.knowledge_stream)


def freeze(value):
    with value.store.exclusive():
        value.cycle.freeze(value.bindings)


def test_actual_cycle_missing_observation_signals_before_any_outbox_and_then_freezes(publication17):
    value = publication17
    value.client._source_claim_broker.latest.pop("source/continuity")
    before = value.store.read()
    with pytest.raises(ReadmissionRequired17) as raised:
        freeze(value)
    assert value.store.read() == before and outboxes(value) == {} and descriptors(value) == {}
    assert value.store._directory is None
    assert consume_required17(raised.value, profile=value.cycle.storage_profile, store=value.store,
        client=value.client, item_id=value.core["item_id"]) == "observe"
    value.client.source_continuity(value.source,
        {key: value.target[key] for key in ("library_id", "node_id")})
    freeze(value)
    state, records, files = value.store.read(), descriptors(value), outboxes(value)
    assert len(records) == len(files) == 1
    identifier = state["pending_publication"]["publication_id"]
    record = records[identifier]["record"]
    assert records[identifier]["state"] == "committed"
    assert state["source_authority"]["publication"] == permanent.record_ref(record)
    assert hashlib.sha256(files[identifier + ".json"]).hexdigest() == record["metadata"]["sha256"]
    assert record["creation_basis"]["claim"] == value.bindings[0]["claim"]


def test_committed_descriptor_state_gap_recovers_its_original_uuid_without_fresh_admission(publication17, monkeypatch):
    value = publication17
    stop = RuntimeError("isolated committed descriptor before State install")
    with monkeypatch.context() as scoped:
        def interrupted(**selectors):
            raise stop
        scoped.setattr(publications, "install_publication17_locked", interrupted)
        with pytest.raises(RuntimeError) as raised:
            freeze(value)
        assert raised.value is stop
    before_state, before_files, before_records = value.store.read(), outboxes(value), descriptors(value)
    assert before_state["pending_publication"] is before_state["source_authority"]["publication"] is None
    assert len(before_records) == len(before_files) == 1
    identifier = next(iter(before_records))
    value.client._source_claim_broker.latest.clear()
    def forbidden(*args, **kwargs):
        pytest.fail("A committed original publication bought another admission")
    from reno import sync_source_admission17 as admission
    monkeypatch.setattr(admission, "capture_current_admission_locked", forbidden)
    # Actual Cycle recomputes a candidate snapshot; the permanent selected
    # descriptor wins, and its exact old outbox is never rewritten or replaced.
    freeze(value)
    state = value.store.read()
    assert state["pending_publication"] == before_records[identifier]["record"]["metadata"]
    assert state["source_authority"]["publication"] == permanent.record_ref(before_records[identifier]["record"])
    assert outboxes(value) == before_files and descriptors(value) == before_records


@pytest.mark.parametrize("changed", ["snapshot", "claim", "media_sha", "media_size"])
def test_caller_snapshot_or_wrong_claim_is_rejected_before_outbox_mutation(publication17, changed):
    value = publication17
    selected, bindings = snapshot(value), deepcopy(value.bindings)
    if changed == "snapshot":
        selected["videos"][0]["tables"]["knowledge_atom"][0]["claim"] = "caller replacement of actual SQL knowledge"
    elif changed == "claim":
        bindings[0]["claim"]["lease_generation"] += 1
    elif changed == "media_sha":
        selected["videos"][0]["media"]["sha256"] = "f" * 64
    else:
        selected["videos"][0]["media"]["byte_size"] += 1
    with pytest.raises(SyncError) as raised:
        factory(value, selected, bindings)
    assert type(raised.value) is not ReadmissionRequired17
    assert outboxes(value) == {} and descriptors(value) == {}
    assert value.store.read()["pending_publication"] is None


def test_observation_lost_after_actual_outbox_write_is_hard_refusal_with_same_view(publication17, monkeypatch):
    value = publication17
    original = sync_state._write
    written = []
    def lose_observation(directory, name, data, **options):
        answer = original(directory, name, data, **options)
        if directory.path.name == "sync-outbox":
            written.append(name)
            value.client._source_claim_broker.latest.pop("source/continuity")
        return answer
    monkeypatch.setattr(sync_state, "_write", lose_observation)
    with pytest.raises(SyncError) as raised:
        freeze(value)
    assert type(raised.value) is not ReadmissionRequired17
    try:
        assert len(written) == len(outboxes(value)) == 1
    except AssertionError as failure:
        # Preserve an earlier refusal when it prevented the intended write boundary.
        raise failure from raised.value
    records = descriptors(value)
    assert len(records) == 1 and next(iter(records.values()))["state"] == "reserved"
    assert value.store.read()["pending_publication"] is None
    # The permanent original selection was written before any outbox bytes;
    # later recovery completes it without buying another current observation.
    value.client._source_claim_broker.latest.clear()
    monkeypatch.setattr(sync_state, "_write", original)
    original_files = outboxes(value)
    freeze(value)
    assert outboxes(value) == original_files
    assert next(iter(descriptors(value).values()))["state"] == "committed"
