"""Genuine original publication creation, disk faults and selector-only replay."""
from contextlib import ExitStack
from copy import deepcopy
import hashlib
import json

import pytest

from reno import storage_lifecycle as lifecycle, sync_state
from reno import sync_source_authority as permanent, sync_source_lifecycle17 as engine
from reno import sync_source_publication_local2 as publications
from reno.sync_protocol import SyncError
from reno.sync_source_opportunity17 import ReadmissionRequired17
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17
from tests.test_sync_source_publication_freeze17 import publication17, freeze, snapshot, outboxes


def actual(value):
    with value.store.reference_operation(), permanent.owned(value.store) as owner:
        frame = owner.observe_readonly()
        return {key: deepcopy(frame[key]) for key in ("entries", "incomplete", "actual_heads", "journal_head", "head_status")}


def blobs(value):
    return {path.name: path.read_bytes() for path in
            (value.store.data_dir / publications.PAYLOAD_DIRECTORY / publications.PAYLOAD_VERSION).glob("*.json")}


def original(value):
    values = actual(value)["entries"]["publications"]
    assert len(values) == 1
    return deepcopy(next(iter(values.values()))["record"])


def forbid_new_creation(monkeypatch, value):
    from reno import sync_source_admission17 as admission, sync_snapshot, sync_reports
    def forbidden(*args, **kwargs):
        pytest.fail("A permanent original publication regenerated data or bought fresh permission")
    monkeypatch.setattr(admission, "capture_current_admission_locked", forbidden)
    monkeypatch.setattr(sync_snapshot, "export_snapshot", forbidden)
    monkeypatch.setattr(sync_reports, "export_reports", forbidden)
    value.client._source_claim_broker.latest.clear()


def crash_after_write(monkeypatch, point):
    write = permanent.AuthorityGuard._write
    outbox_write = sync_state._write
    stop = RuntimeError("actual publication crash at " + point)
    hit = []
    def authority_write(owner, directory, name, raw, **options):
        answer = write(owner, directory, name, raw, **options)
        value = json.loads(raw)
        matched = (point == "reserved_journal" and directory.path.name == "journal" and value.get("state") == "reserved"
            or point == "reserved_db_head" and name == permanent.DB_HEAD_NAME and value.get("ordinal") == 3
            or point == "record_file" and directory.path.name == "publications"
            or point == "committed_journal" and directory.path.name == "journal" and value.get("state") == "committed")
        if matched and not hit:
            hit.append((directory.path, name))
            raise stop
        return answer
    def state_write(directory, name, raw, **options):
        answer = outbox_write(directory, name, raw, **options)
        if point == "outbox" and directory.path.name == sync_state._OUTBOX and not hit:
            hit.append((directory.path, name))
            raise stop
        return answer
    monkeypatch.setattr(permanent.AuthorityGuard, "_write", authority_write)
    monkeypatch.setattr(sync_state, "_write", state_write)
    return stop, hit


@pytest.mark.parametrize("point", ["reserved_journal", "reserved_db_head", "outbox", "record_file", "committed_journal"])
def test_actual_disk_crash_completes_original_body_uuid_and_claim_without_new_export(publication17, monkeypatch, point):
    value = publication17
    with monkeypatch.context() as fault:
        stop, hit = crash_after_write(fault, point)
        with pytest.raises(RuntimeError) as raised:
            freeze(value)
        assert raised.value is stop and hit
    saved = original(value)
    before = actual(value)
    assert saved["format_version"] == 2 and value.store.read()["pending_publication"] is None
    body = blobs(value)[saved["payload_ref"]["sha256"] + ".json"]
    assert hashlib.sha256(body).hexdigest() == saved["metadata"]["sha256"]
    if point in {"reserved_journal", "reserved_db_head", "committed_journal"}:
        assert before["head_status"] == "lagging"
    before_blobs = blobs(value)
    forbid_new_creation(monkeypatch, value)
    freeze(value)
    after = actual(value)
    assert after["entries"]["publications"][saved["record_id"]] == {"state": "committed", "record": saved}
    assert after["head_status"] == "complete" and after["incomplete"] == []
    assert after["actual_heads"] == {"db": after["journal_head"], "checkpoint": after["journal_head"]}
    assert outboxes(value) == {saved["record_id"] + ".json": body} and blobs(value) == before_blobs
    state = value.store.read()
    assert state["pending_publication"] == saved["metadata"]
    assert state["source_authority"]["publication"] == permanent.record_ref(saved)


def test_reserved_selection_before_materialization_recovers_original_candidate(publication17, monkeypatch):
    value = publication17
    stop = RuntimeError("actual reservation before materialization")
    with monkeypatch.context() as fault:
        def interrupted(*args, **kwargs):
            raise stop
        fault.setattr(publications, "materialize_publication2", interrupted)
        with pytest.raises(RuntimeError) as raised:
            freeze(value)
        assert raised.value is stop
    saved, saved_blobs = original(value), blobs(value)
    assert outboxes(value) == {} and actual(value)["incomplete"] == [("publications", saved["record_id"])]
    forbid_new_creation(monkeypatch, value)
    freeze(value)
    assert blobs(value) == saved_blobs
    assert outboxes(value)[saved["record_id"] + ".json"] == saved_blobs[saved["payload_ref"]["sha256"] + ".json"]


@pytest.mark.parametrize("change", ["missing", "corrupt"])
def test_original_blob_loss_rejects_before_head_repair_or_outbox(publication17, monkeypatch, change):
    value = publication17
    with monkeypatch.context() as fault:
        stop, hit = crash_after_write(fault, "reserved_journal")
        with pytest.raises(RuntimeError):
            freeze(value)
        assert hit
    saved = original(value)
    path = value.store.data_dir / publications.payload_key2(saved["payload_ref"])
    if change == "missing":
        path.unlink()
    else:
        raw = path.read_bytes()
        path.write_bytes(raw[:-1] + (b" " if raw[-1:] != b" " else b"\n"))
    before = actual(value)
    forbid_new_creation(monkeypatch, value)
    with pytest.raises((SyncError, FileNotFoundError, ValueError)) as raised:
        freeze(value)
    assert type(raised.value) is not ReadmissionRequired17
    after = actual(value)
    assert after["actual_heads"] == before["actual_heads"] and after["entries"] == before["entries"]
    assert outboxes(value) == {} and value.store.read()["pending_publication"] is None


def test_candidate_without_reservation_is_preparation_not_a_directory_latest_selector(publication17, monkeypatch):
    value = publication17
    from reno import sync_source_admission17 as admission
    stop = RuntimeError("before original journal selection")
    with monkeypatch.context() as fault:
        def interrupted(**selectors):
            raise stop
        fault.setattr(admission, "capture_current_admission_locked", interrupted)
        with pytest.raises(RuntimeError) as raised:
            freeze(value)
        assert raised.value is stop
    candidates = blobs(value)
    assert len(candidates) == 1 and outboxes(value) == {}
    assert actual(value)["entries"]["publications"] == {}
    unselected = json.loads(next(iter(candidates.values())))["snapshot"]["publication_id"]
    freeze(value)
    saved = original(value)
    assert saved["record_id"] != unselected and len(blobs(value)) == 2
    assert saved["record_id"] + ".json" in outboxes(value)


def freeze_original_v1(value):
    """A genuine historical v1 producer, with its original body already saved."""
    from reno.sync_source_admission17 import capture_current_admission_locked
    selected = snapshot(value)
    with value.store.exclusive(), value.store.reference_operation():
        with capture_current_admission_locked(profile=value.profile, store=value.store,
                client=value.client, item_id=value.core["item_id"]) as view:
            metadata = value.store.write_publication(selected, value.bindings,
                recovery_generation=value.cycle.knowledge_generation, recovery_stream=value.cycle.knowledge_stream)
            record = engine._publication_record17(view, metadata, value.store.read_publication(metadata))
            with engine._owned(value.store) as owner:
                observed = owner.observe()
                with engine._publication_creator(owner, view, record, observed) as producer:
                    owner.publish("publications", record, producer=producer)
    return record


@pytest.mark.parametrize("point", ["reserved_journal", "record_file"])
def test_actual_v1_reserved_with_original_outbox_finishes_same_record_bytes(publication17, monkeypatch, point):
    value = publication17
    with monkeypatch.context() as fault:
        stop, hit = crash_after_write(fault, point)
        with pytest.raises(RuntimeError) as raised:
            freeze_original_v1(value)
        assert raised.value is stop and hit
    saved, saved_files = original(value), outboxes(value)
    assert saved["format_version"] == 1 and blobs(value) == {}
    saved_sha = permanent.digest(saved)
    forbid_new_creation(monkeypatch, value)
    freeze(value)
    assert outboxes(value) == saved_files and blobs(value) == {}
    final = actual(value)["entries"]["publications"][saved["record_id"]]
    assert final == {"state": "committed", "record": saved} and permanent.digest(final["record"]) == saved_sha


def test_v1_loose_outbox_without_original_selection_is_not_adopted(publication17, monkeypatch):
    value = publication17
    selected = snapshot(value)
    metadata = value.store.write_publication(selected, value.bindings,
        recovery_generation=value.cycle.knowledge_generation, recovery_stream=value.cycle.knowledge_stream)
    saved_files = outboxes(value)
    forbid_new_creation(monkeypatch, value)
    with value.store.exclusive(), pytest.raises(SyncError):
        engine.create_publication_descriptor17_locked(profile=value.profile, store=value.store,
            client=value.client, publication_id=metadata["publication_id"])
    with pytest.raises(SyncError):
        freeze(value)
    assert outboxes(value) == saved_files and actual(value)["entries"]["publications"] == {}


def test_publication_body_is_held_by_real_identity_and_not_a_copied_profile(publication17):
    value = publication17
    freeze(value)
    saved = original(value)
    with value.store.reference_operation(), ExitStack() as stack:
        pin = publications.capture_payload2_locked(profile=value.profile, payload_ref=saved["payload_ref"], stack=stack)
        assert pin.raw == outboxes(value)[saved["record_id"] + ".json"]
        pin.short()
        pin.full()
        wrong = lifecycle.StorageProfile(value.profile.database, value.profile.media,
            value.profile.reports, value.profile.checkpoints.parent / "wrong-checkpoints")
        with pytest.raises((SyncError, ValueError)):
            publications.capture_payload2_locked(profile=wrong, payload_ref=saved["payload_ref"], stack=stack)
