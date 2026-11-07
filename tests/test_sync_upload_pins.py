"""Publication-scoped pins: no network, live storage, model or real library."""
from copy import deepcopy
import json
import uuid

import pytest

from reno import storage_artifacts as artifacts, sync_upload_pins as pins
from reno.sync_protocol import SyncError
from reno.sync_media import MediaStore
from tests.test_storage_artifacts import storage_env, transaction, media_file, register_media  # noqa: F401


def intent(env, descriptor):
    return {"publication_id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()),
            "source_version": {"library_id": str(uuid.uuid4()), "library_epoch": 2, "kb_revision": 7},
            "recovery_stream": {"library_id": env["version"]["library_id"], "library_epoch": env["version"]["library_epoch"]},
            "recovery_generation": 0, "descriptor": descriptor}


def binding(value):
    return {k: value[k] for k in pins.BINDING_FIELDS}


def publication(con, value, descriptors):
    source, target = value["source_version"], value["recovery_stream"]
    con.execute("INSERT INTO sync_publication VALUES(?,?,?,?,?,?,?,?,?)", (
        value["publication_id"], source["library_id"], value["source_node_id"], source["library_epoch"], source["kb_revision"],
        "a" * 64, "b" * 64, "2026-10-01T01:00:00Z", json.dumps({**target, "kb_revision": 1})))
    con.execute("INSERT INTO sync_publication_fence VALUES(?,?)", (value["publication_id"], value["recovery_generation"]))
    for descriptor in descriptors:
        con.execute("INSERT INTO sync_published_video VALUES(?,?,?,?,?,?,?,?)", (
            value["publication_id"], descriptor["video_id"], source["library_id"], value["source_node_id"],
            source["library_epoch"], source["kb_revision"], descriptor["sha256"], "2026-10-01T01:00:00Z"))
        key = f"media/sync/{descriptor['video_id']}/{descriptor['sha256']}.{descriptor['extension']}"
        con.execute("INSERT INTO video_asset(video_id,content_sha256,files_json,sync_state) VALUES(?,?,?,'ready') "
                    "ON CONFLICT(video_id) DO UPDATE SET content_sha256=excluded.content_sha256,files_json=excluded.files_json,sync_state='ready'",
                    (descriptor["video_id"], descriptor["sha256"], json.dumps({"video": key})))


def stored(env, value, key):
    row = pins.reserve(env["con"], value, profile=env["profile"])
    record = register_media(env, value["descriptor"], key)
    assert pins.mark_stored(env["con"], value, record["artifact_id"], profile=env["profile"])["state"] == "stored"
    return row, record


def complete_publication(con, *, binding, descriptors, profile):
    """The real publisher supplies its SHA-verified live handle context."""
    row = con.execute("SELECT state FROM sync_upload_pin WHERE kind='publication' AND publication_id=?", (binding["publication_id"],)).fetchone()
    if row is None or row[0] != "reserved" or not descriptors:
        return pins.complete_publication(con, binding=binding, descriptors=descriptors, profile=profile)
    with MediaStore(profile.media).hold_verified(descriptors) as held:
        return pins.complete_publication(con, binding=binding, descriptors=descriptors, profile=profile, media_hold=held)


def test_intent_is_canonical_closed_and_detached(storage_env):
    descriptor, _, _ = media_file(storage_env)
    value = intent(storage_env, descriptor)
    reverse = dict(reversed(list(value.items())))
    reverse["descriptor"] = dict(reversed(list(descriptor.items())))
    assert pins.intent_id(value) == pins.intent_id(reverse)
    normalized = pins.normalize_intent(value)
    normalized["source_version"]["kb_revision"] += 1
    assert value["source_version"]["kb_revision"] == 7
    assert pins.intent_id(value) != pins.intent_id(normalized)


@pytest.mark.parametrize("damage", ["extra", "no_stream", "bool_generation", "huge_generation", "bool_epoch", "uuid", "descriptor_extra"])
def test_invalid_intent_does_not_create_pins(storage_env, damage):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value = intent(env, descriptor)
    if damage == "extra":
        value["private"] = "not permitted"
    elif damage == "no_stream":
        value.pop("recovery_stream")
    elif damage == "bool_generation":
        value["recovery_generation"] = False
    elif damage == "huge_generation":
        value["recovery_generation"] = 2**63
    elif damage == "bool_epoch":
        value["source_version"]["library_epoch"] = True
    elif damage == "uuid":
        value["source_node_id"] = "not uuid"
    else:
        value["descriptor"]["extra"] = 1
    with transaction(env) as con:
        with pytest.raises(SyncError):
            pins.reserve(con, value, profile=env["profile"])
        assert con.execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 0


def test_reserve_stored_and_publication_are_exact_idempotent_and_caller_owned(storage_env):
    env = storage_env
    descriptor, key, _ = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        initial, record = stored(env, value, key)
        replay = pins.reserve(con, value, profile=env["profile"])
        assert replay["state"] == "stored" and replay["created_at"] == initial["created_at"]
        assert pins.mark_stored(con, value, record["artifact_id"], profile=env["profile"]) == replay
        assert pins.held_descriptors(con) == [descriptor]
        publication(con, value, [descriptor])
        done = complete_publication(con, binding=binding(value), descriptors=[descriptor], profile=env["profile"])
        assert done["state"] == "published" and con.in_transaction
        assert pins.get(con, initial["pin_id"])["state"] == "published"
        assert pins.held_descriptors(con) == []
    assert env["con"].execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 0
    assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["source", "node", "epoch", "revision", "target", "target_epoch", "generation", "descriptor"])
def test_same_publication_cannot_change_any_binding_or_video_descriptor(storage_env, change):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value = intent(env, descriptor)
    modified = deepcopy(value)
    if change == "source":
        modified["source_version"]["library_id"] = str(uuid.uuid4())
    elif change == "node":
        modified["source_node_id"] = str(uuid.uuid4())
    elif change in ("epoch", "revision"):
        modified["source_version"]["library_epoch" if change == "epoch" else "kb_revision"] += 1
    elif change == "target":
        modified["recovery_stream"]["library_id"] = str(uuid.uuid4())
    elif change == "target_epoch":
        modified["recovery_stream"]["library_epoch"] += 1
    elif change == "generation":
        modified["recovery_generation"] += 1
    else:
        modified["descriptor"]["byte_size"] += 1
    with transaction(env) as con:
        original = pins.reserve(con, value, profile=env["profile"])
        with pytest.raises(SyncError):
            pins.reserve(con, modified, profile=env["profile"])
        assert pins.get(con, original["pin_id"]) == original
        assert con.execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 2


@pytest.mark.parametrize("change", ["target", "epoch", "generation", "held", "floor"])
def test_fence_failure_rolls_back_even_new_publication_sentinel(storage_env, change):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        if change == "target":
            value["recovery_stream"]["library_id"] = str(uuid.uuid4())
        elif change == "epoch":
            value["recovery_stream"]["library_epoch"] += 1
        elif change == "generation":
            value["recovery_generation"] = 1
        else:
            value["recovery_generation"] = 1
            con.execute("INSERT INTO sync_recovery_control VALUES(1,?,1,1,?,NULL,'now')", (
                "held" if change == "held" else "resume_pending", json.dumps(value["source_version"])))
        con.execute("INSERT INTO meta VALUES('prior-caller-write','keep')")
        with pytest.raises(SyncError):
            pins.reserve(con, value, profile=env["profile"])
        assert con.execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 0
        assert con.execute("SELECT value FROM meta WHERE key='prior-caller-write'").fetchone()[0] == "keep"


@pytest.mark.parametrize("damage", ["missing_receipt", "unreserved", "reserved_only", "extra_pin", "omitted_pin", "receipt_source", "receipt_fence"])
def test_complete_requires_exact_all_media_and_actual_publication(storage_env, damage):
    env = storage_env
    descriptor, key, _ = media_file(env)
    another, another_key, _ = media_file(env, b"another artificial video", "BVAnother")
    value = intent(env, descriptor)
    with transaction(env) as con:
        if damage != "unreserved":
            if damage == "reserved_only":
                pins.reserve(con, value, profile=env["profile"])
            else:
                stored(env, value, key)
        if damage in ("extra_pin", "omitted_pin"):
            stored(env, {**value, "descriptor": another}, another_key)
        supplied = [descriptor]
        if damage == "omitted_pin":
            supplied = []
        if damage != "missing_receipt":
            publication(con, value, supplied)
        if damage == "receipt_source":
            con.execute("UPDATE sync_published_video SET source_revision=source_revision+1")
        elif damage == "receipt_fence":
            con.execute("UPDATE sync_publication_fence SET generation=generation+1")
        before = [tuple(row) for row in con.execute("SELECT * FROM sync_upload_pin ORDER BY pin_id")]
        with pytest.raises(SyncError):
            complete_publication(con, binding=binding(value), descriptors=supplied, profile=env["profile"])
        assert [tuple(row) for row in con.execute("SELECT * FROM sync_upload_pin ORDER BY pin_id")] == before


def test_publication_retry_does_not_read_retired_media_or_reopen_uploads(storage_env):
    env = storage_env
    descriptor, key, path = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        stored(env, value, key)
        publication(con, value, [descriptor])
        original = complete_publication(con, binding=binding(value), descriptors=[descriptor], profile=env["profile"])
        con.commit()
    path.unlink()  # Artificially represent later legal retirement, never real GC.
    with transaction(env) as con:
        con.execute("UPDATE video_asset SET content_sha256='later-publication',files_json='{}',sync_state='staged'")
        assert complete_publication(con, binding=binding(value), descriptors=[descriptor], profile=env["profile"]) == original
        with pytest.raises(SyncError) as caught:
            pins.reserve(con, value, profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_PUBLISHED"
        with pytest.raises(SyncError):
            pins.abandon(con, binding=binding(value), operation_id=str(uuid.uuid4()), profile=env["profile"])


@pytest.mark.parametrize("phase", ["before_reserve", "reserved", "stored"])
def test_abandon_permanent_tombstone_covers_late_unknown_descriptors(storage_env, phase):
    env = storage_env
    descriptor, key, _ = media_file(env)
    value, operation_id = intent(env, descriptor), str(uuid.uuid4())
    with transaction(env) as con:
        if phase == "reserved":
            pins.reserve(con, value, profile=env["profile"])
        elif phase == "stored":
            stored(env, value, key)
        tombstone = pins.abandon(con, binding=binding(value), operation_id=operation_id, profile=env["profile"])
        assert tombstone["state"] == "abandoned"
        assert pins.abandon(con, binding=binding(value), operation_id=operation_id, profile=env["profile"]) == tombstone
        assert pins.held_descriptors(con) == []
        for changed in (value, {**value, "descriptor": {**descriptor, "video_id": "UnseenVideo"}},
                        {**value, "source_version": {**value["source_version"], "kb_revision": 88}},
                        {**value, "recovery_stream": {**value["recovery_stream"], "library_epoch": 88}}):
            with pytest.raises(SyncError) as caught:
                pins.reserve(con, changed, profile=env["profile"])
            assert caught.value.code == "SYNC_UPLOAD_ABANDONED"
        publication(con, value, [])
        with pytest.raises(SyncError) as caught:
            complete_publication(con, binding=binding(value), descriptors=[], profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_ABANDONED"


def test_abandon_operation_id_and_parameters_are_bound(storage_env):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value, operation_id = intent(env, descriptor), str(uuid.uuid4())
    with transaction(env) as con:
        original = pins.abandon(con, binding=binding(value), operation_id=operation_id, profile=env["profile"])
        changed = {**binding(value), "publication_id": str(uuid.uuid4())}
        with pytest.raises(SyncError) as caught:
            pins.abandon(con, binding=changed, operation_id=operation_id, profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_ABANDON_CONFLICT"
        with pytest.raises(SyncError):
            pins.abandon(con, binding=binding(value), operation_id=str(uuid.uuid4()), profile=env["profile"])
        assert pins.get(con, original["pin_id"]) == original


def test_zero_media_publication_still_has_terminal_sentinel(storage_env):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        publication(con, value, [])
        original = complete_publication(con, binding=binding(value), descriptors=[], profile=env["profile"])
        assert original["kind"] == "publication" and original["state"] == "published"
        assert complete_publication(con, binding=binding(value), descriptors=[], profile=env["profile"]) == original
        with pytest.raises(SyncError):
            pins.reserve(con, value, profile=env["profile"])


def test_legacy_hold_is_permanent_and_never_completed_by_matching_new_publication(storage_env):
    env = storage_env
    descriptor, key, _ = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        arguments = {"source_library_id": value["source_version"]["library_id"], "source_node_id": value["source_node_id"],
                     "descriptor": descriptor, "profile": env["profile"]}
        old = pins.legacy_hold(con, **arguments)
        assert old["state"] == "legacy_hold" and old["artifact_id"] is None
        _, record = stored(env, value, key)
        old = pins.legacy_hold(con, **arguments, artifact_id=record["artifact_id"])
        publication(con, value, [descriptor])
        complete_publication(con, binding=binding(value), descriptors=[descriptor], profile=env["profile"])
        assert pins.get(con, old["pin_id"])["state"] == "legacy_hold"
        assert pins.held_descriptors(con) == [descriptor]
        assert pins.legacy_hold(con, **arguments) == old


def test_stored_requires_matching_verified_artifact_and_reserved_pin(storage_env):
    env = storage_env
    descriptor, key, _ = media_file(env)
    other, other_key, _ = media_file(env, b"different", "BVAnother")
    value = intent(env, descriptor)
    with transaction(env) as con:
        wrong = register_media(env, other, other_key)
        with pytest.raises(SyncError):
            pins.mark_stored(con, value, wrong["artifact_id"], profile=env["profile"])
        pins.reserve(con, value, profile=env["profile"])
        with pytest.raises(SyncError) as caught:
            pins.mark_stored(con, value, wrong["artifact_id"], profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_ARTIFACT_MISMATCH"
        assert pins.get(con, pins.intent_id(value))["state"] == "reserved"


def test_abandoned_stored_pin_is_not_reopened_by_late_completion(storage_env):
    env = storage_env
    descriptor, key, _ = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        _, record = stored(env, value, key)
        pins.abandon(con, binding=binding(value), operation_id=str(uuid.uuid4()), profile=env["profile"])
        with pytest.raises(SyncError) as caught:
            pins.mark_stored(con, value, record["artifact_id"], profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_ABANDONED"
        assert pins.get(con, pins.intent_id(value))["state"] == "abandoned"


def test_first_complete_checks_exact_extension_installed_by_publication(storage_env):
    env = storage_env
    descriptor, key, path = media_file(env)
    wrong = {**descriptor, "extension": "mkv"}
    wrong_key = key[:-3] + "mkv"
    (env["profile"].media / wrong_key).write_bytes(path.read_bytes())
    value = intent(env, wrong)
    with transaction(env) as con:
        stored(env, value, wrong_key)
        publication(con, value, [descriptor])
        with pytest.raises(SyncError) as caught:
            complete_publication(con, binding=binding(value), descriptors=[wrong], profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_SET_MISMATCH"
        assert pins.get(con, pins.intent_id(value))["state"] == "stored"
        assert pins.held_descriptors(con) == [wrong]


@pytest.mark.parametrize("recovery_state", ["held", "resume_pending", "running"])
def test_known_intent_can_be_abandoned_after_receiver_recovery(storage_env, recovery_state):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value, operation_id = intent(env, descriptor), str(uuid.uuid4())
    with transaction(env) as con:
        pins.reserve(con, value, profile=env["profile"])
        con.execute("UPDATE meta SET value='2' WHERE key='library_epoch'")
        con.execute("INSERT INTO sync_recovery_control VALUES(1,?,1,1,?,NULL,'now')", (
            recovery_state, json.dumps(value["source_version"])))
        before = tuple(con.execute("SELECT * FROM sync_recovery_control").fetchone())
        tombstone = pins.abandon(con, binding=binding(value), operation_id=operation_id, profile=env["profile"])
        assert tombstone["state"] == "abandoned"
        assert pins.abandon(con, binding=binding(value), operation_id=operation_id, profile=env["profile"]) == tombstone
        assert tuple(con.execute("SELECT * FROM sync_recovery_control").fetchone()) == before
        with pytest.raises(SyncError) as caught:
            pins.reserve(con, value, profile=env["profile"])
        assert caught.value.code == "SYNC_UPLOAD_ABANDONED"


@pytest.mark.parametrize("mismatch", [None, "library", "epoch", "future_generation", "past_generation"])
def test_unknown_intent_abandon_requires_current_fence_even_while_held(storage_env, mismatch):
    env = storage_env
    descriptor, _, _ = media_file(env)
    value = intent(env, descriptor)
    with transaction(env) as con:
        con.execute("INSERT INTO sync_recovery_control VALUES(1,'held',1,1,?,NULL,'now')", (json.dumps(value["source_version"]),))
        value["recovery_generation"] = 1
        if mismatch == "library":
            value["recovery_stream"]["library_id"] = str(uuid.uuid4())
        elif mismatch == "epoch":
            value["recovery_stream"]["library_epoch"] += 1
        elif mismatch == "future_generation":
            value["recovery_generation"] = 2
        elif mismatch == "past_generation":
            value["recovery_generation"] = 0
        if mismatch is None:
            assert pins.abandon(con, binding=binding(value), operation_id=str(uuid.uuid4()), profile=env["profile"])["state"] == "abandoned"
        else:
            with pytest.raises(SyncError):
                pins.abandon(con, binding=binding(value), operation_id=str(uuid.uuid4()), profile=env["profile"])
            assert con.execute("SELECT count(*) FROM sync_upload_pin").fetchone()[0] == 0
