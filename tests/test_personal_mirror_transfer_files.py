"""Real isolated prepares/uploads and held attachment files, not DTO proofs.

The fixture creates original personal facts through the existing source APIs,
captures them, and prepares the real receiver/manifest through its runtime.
No stored rows or installed receipt is seeded. This proves neither transport
routes nor production capability activation, primary handoff or sharing.
"""
from concurrent.futures import ThreadPoolExecutor
from contextvars import copy_context
from datetime import timedelta
import os
import uuid

import pytest

from reno import db, personal_mirror_transfer as runtime
from reno import personal_mirror_transfer_contract as wire, personal_mirror_transfer_schema as schema
from reno import personal_mirror_transfer_files as files, personal_mirror_sqlite as mirror_sql
from reno import storage_lifecycle as lifecycle, sync_queue as queue
from reno.sync_protocol import SyncError
from tests.test_personal_mirror_transfer import mirror_env, make_prepare, arguments, BODY, NOW, table_state, commit_selector


def prepared(env, *, attachment=True):
    original = make_prepare(env, with_attachment=attachment)
    with lifecycle.shared(env["profile"]):
        runtime.prepare_mirror_locked(**arguments(env), prepare=original)
    descriptors = wire.attachment_manifest(original)
    return original, descriptors[0] if descriptors else None


def selectors(env, original, descriptor):
    return {**arguments(env), "operation_id": original["operation_id"],
        "expected_prepare_sha256": wire.prepare_sha256(original), "attachment_id": descriptor["attachment_id"]}


def path(env, descriptor):
    return env["data"] / files.ROOT / descriptor["storage_key"]


def hold_selectors(env, original):
    return {"profile": env["profile"], "operation_id": original["operation_id"],
        "expected_prepare_sha256": wire.prepare_sha256(original)}


def attachment_rows(env, original):
    con = db.connect(env["profile"].database)
    try:
        return [dict(row) for row in con.execute(f'SELECT * FROM "{schema.ATTACHMENT}" WHERE operation_id=? ORDER BY attachment_id',
            (original["operation_id"],))]
    finally:
        con.close()


def test_original_prepare_reserves_manifest_and_streamed_body_stores_its_actual_identity(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    before = table_state(env)
    assert attachment_rows(env, original)[0]["state"] == "reserved"
    with lifecycle.shared(env["profile"]):
        result = files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY[:9], b"", BODY[9:]])
        with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
            assert type(held) is files.PreparedAttachmentHold
            held.verify_source()
    stored = attachment_rows(env, original)[0]
    assert stored["state"] == result["state"] == "stored"
    assert stored["descriptor_json"] == before[schema.ATTACHMENT][0][5]
    assert path(env, descriptor).read_bytes() == BODY
    assert stored["local_file_inode"] == str(path(env, descriptor).stat().st_ino)
    assert stored["local_file_inode"] != "0"
    assert table_state(env)[schema.RECEIPT] == []


@pytest.mark.parametrize("chunks", [[BODY[:-1]], [BODY + b"x"], [b"x" * len(BODY)], [bytearray(BODY)]])
def test_bad_stream_keeps_parent_reserved_and_preserves_unknown_staging_files(mirror_env, chunks):
    env = mirror_env
    original, descriptor = prepared(env)
    staging = env["data"] / files.ROOT / files.STAGING
    staging.mkdir(parents=True)
    unknown = staging / "unmanaged-original.txt"
    unknown.write_bytes(b"original")
    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=chunks)
    assert table_state(env) == before
    assert not path(env, descriptor).exists()
    assert unknown.read_bytes() == b"original"
    assert sorted(item.name for item in staging.iterdir()) == ["unmanaged-original.txt", "upload.lock"]


def test_published_body_survives_pre_sql_cancel_and_original_uuid_recovers_without_new_body(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    target = path(env, descriptor)
    class Cancelled(Exception):
        pass
    def cancelled():
        if target.exists():
            raise Cancelled("controlled after publication")
    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(Cancelled):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY], check_cancelled=cancelled)
    assert target.read_bytes() == BODY and table_state(env) == before
    stamp = target.stat().st_mtime_ns
    with lifecycle.shared(env["profile"]):
        result = files.store_attachment_locked(**selectors(env, original, descriptor), chunks=None)
    assert result["operation_id"] == original["operation_id"] and result["state"] == "stored"
    assert target.read_bytes() == BODY and target.stat().st_mtime_ns == stamp


def test_callback_cannot_publish_a_corrupted_flushed_temporary_body(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    staging = env["data"] / files.ROOT / files.STAGING
    corrupted = False
    def change_completed_temp():
        nonlocal corrupted
        if corrupted or not staging.exists():
            return
        for temporary in staging.glob("*.part"):
            if temporary.stat().st_size == len(BODY):
                temporary.write_bytes(b"x" * len(BODY))
                corrupted = True
                return
    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises((SyncError, OSError)):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY],
            check_cancelled=change_completed_temp)
    assert table_state(env) == before and not path(env, descriptor).exists()
    with lifecycle.shared(env["profile"]):
        result = files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
    assert result["state"] == "stored" and path(env, descriptor).read_bytes() == BODY


def test_same_uuid_stored_retry_reuses_bytes_mtime_and_original_sql_rows(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    with lifecycle.shared(env["profile"]):
        first = files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        before, stamp = table_state(env), path(env, descriptor).stat().st_mtime_ns
        def forbidden_body():
            pytest.fail("Original stored-body replay consumed a new body")
            yield b""
        repeated = files.store_attachment_locked(**selectors(env, original, descriptor), chunks=forbidden_body())
    assert repeated == first and table_state(env) == before
    assert path(env, descriptor).stat().st_mtime_ns == stamp


def test_callback_cannot_replace_original_session_with_a_new_actual_generation(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    target, changed = path(env, descriptor), False
    later = NOW + timedelta(seconds=601)
    before = table_state(env)
    def rotate_actual_session():
        nonlocal changed
        if changed or not target.exists():
            return
        con = db.connect(env["profile"].database)
        try:
            renewed = queue.heartbeat(con, env["source"], str(uuid.uuid4()), now=later)["lease"]
            con.commit()
        finally:
            con.close()
        env["lease"].clear()
        env["lease"].update(renewed)
        changed = True
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY],
            check_cancelled=rotate_actual_session)
    assert changed and table_state(env) == before and target.read_bytes() == BODY
    recovered = selectors(env, original, descriptor)
    recovered["now"] = later
    with lifecycle.shared(env["profile"]):
        result = files.store_attachment_locked(**recovered, chunks=None)
    assert result["state"] == "stored" and target.read_bytes() == BODY


def test_post_commit_store_cancel_preserves_original_stored_identity_for_retry(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    class Cancelled(Exception):
        pass
    def cancel_after_actual_commit():
        if not files.in_write_window() and attachment_rows(env, original)[0]["state"] == "stored":
            raise Cancelled("controlled after stored commit")
    with lifecycle.shared(env["profile"]), pytest.raises(Cancelled):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY],
            check_cancelled=cancel_after_actual_commit)
    before, stamp = table_state(env), path(env, descriptor).stat().st_mtime_ns
    assert attachment_rows(env, original)[0]["state"] == "stored"
    with lifecycle.shared(env["profile"]):
        result = files.store_attachment_locked(**selectors(env, original, descriptor), chunks=None)
    assert result["state"] == "stored" and table_state(env) == before
    assert path(env, descriptor).read_bytes() == BODY and path(env, descriptor).stat().st_mtime_ns == stamp


def test_post_commit_hold_cancel_preserves_original_receipt_for_replay(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    selected = commit_selector(original)
    class Cancelled(Exception):
        pass
    def cancel_after_actual_commit():
        if not runtime.in_write_window() and table_state(env)[schema.RECEIPT]:
            raise Cancelled("controlled after installed commit")
    with lifecycle.shared(env["profile"]):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        with pytest.raises(Cancelled):
            runtime.commit_mirror_locked(**arguments(env), commit=selected,
                check_cancelled=cancel_after_actual_commit)
        before, stamp = table_state(env), path(env, descriptor).stat().st_mtime_ns
        assert len(before[schema.RECEIPT]) == 1 and attachment_rows(env, original)[0]["state"] == "installed"
        persisted = runtime.mirror_receipt_locked(**arguments(env), commit=selected)
        replay = runtime.commit_mirror_locked(**arguments(env), commit=selected)
    assert replay == persisted and replay["replayed"] is True
    assert table_state(env) == before and path(env, descriptor).stat().st_mtime_ns == stamp


def test_same_name_different_body_collision_never_overwrites_or_adopts_it(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    target = path(env, descriptor)
    target.parent.mkdir(parents=True)
    target.write_bytes(b"independent original")
    stamp, before = target.stat().st_mtime_ns, table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
    assert target.read_bytes() == b"independent original" and target.stat().st_mtime_ns == stamp
    assert table_state(env) == before


@pytest.mark.parametrize("selector", ["operation_id", "prepare_sha256", "attachment_id"])
def test_actual_parent_and_manifest_selectors_cannot_be_replaced(mirror_env, selector):
    env = mirror_env
    original, descriptor = prepared(env)
    selected = selectors(env, original, descriptor)
    field = "expected_prepare_sha256" if selector == "prepare_sha256" else selector
    selected[field] = "f" * 64 if selector == "prepare_sha256" else str(uuid.uuid4())
    before = table_state(env)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError):
        files.store_attachment_locked(**selected, chunks=[BODY])
    assert table_state(env) == before and not path(env, descriptor).exists()


def test_external_hardlink_is_rejected_without_deleting_either_original_name(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    target = path(env, descriptor)
    linked = target.with_suffix(".external-link")
    with lifecycle.shared(env["profile"]):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        before = table_state(env)
        os.link(target, linked)
        with pytest.raises(SyncError):
            with files.hold_prepared_attachments_locked(**hold_selectors(env, original)):
                pytest.fail("A multiply linked attachment became a closed file proof")
    assert target.read_bytes() == linked.read_bytes() == BODY
    assert table_state(env) == before


def test_zero_attachment_hold_is_actual_parent_fact_without_creating_body_directories(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env, attachment=False)
    assert descriptor is None
    with lifecycle.shared(env["profile"]):
        with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
            assert type(held) is files.PreparedAttachmentHold
            held.verify_source()
    assert not (env["data"] / files.ROOT).exists()
    assert attachment_rows(env, original) == []
    with pytest.raises(SyncError):
        held.verify_source()


def test_short_writer_never_hashes_full_files_and_requires_the_original_profile(mirror_env, monkeypatch):
    env = mirror_env
    original, descriptor = prepared(env)
    actual_full = files._HeldFile.full
    def outside_only(self, check):
        assert runtime.in_write_window() is False and files._WRITE_WINDOW.get() is None
        return actual_full(self, check)
    monkeypatch.setattr(files._HeldFile, "full", outside_only)
    with lifecycle.shared(env["profile"]):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
            with runtime._connection(env["profile"]) as con:
                token = runtime._begin_writer(con)
                try:
                    held.verify_for_commit(con, profile=env["profile"])
                    with pytest.raises(SyncError):
                        held.verify_source()
                    with pytest.raises(SyncError):
                        held.verify_for_commit(con, profile=env["source_profile"])
                finally:
                    mirror_sql.rollback_writer(con)
                    runtime._WRITE_WINDOW.reset(token)


def test_public_seal_or_copied_thread_context_cannot_replace_the_closed_hold(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    with pytest.raises(SyncError):
        files.PreparedAttachmentHold(profile=env["profile"], files={}, seal=object())
    with lifecycle.shared(env["profile"]):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
            context = copy_context()
            with ThreadPoolExecutor(max_workers=1) as executor:
                with pytest.raises(SyncError):
                    executor.submit(context.run, held.verify_source).result()


@pytest.mark.parametrize("field", ["_cookie", "_profile", "_node_fact"])
def test_closed_hold_rejects_private_binding_replacement_within_a_valid_group_guard(mirror_env, field):
    env = mirror_env
    original, descriptor = prepared(env)
    with lifecycle.shared_many([env["profile"], env["source_profile"]]):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
            previous = getattr(held, field)
            replacement = {"_cookie": held._cookie + 1, "_profile": env["source_profile"],
                "_node_fact": (str(uuid.uuid4()), "f" * 64)}[field]
            setattr(held, field, replacement)
            try:
                with pytest.raises(SyncError):
                    held.verify_source()
            finally:
                setattr(held, field, previous)
            held.verify_source()


def test_held_body_replacement_is_denied_on_windows_or_detected_on_posix(mirror_env):
    env = mirror_env
    original, descriptor = prepared(env)
    target = path(env, descriptor)
    with lifecycle.shared(env["profile"]):
        files.store_attachment_locked(**selectors(env, original, descriptor), chunks=[BODY])
        if os.name == "nt":
            with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
                with pytest.raises(OSError):
                    target.rename(target.with_suffix(".original"))
                held.verify_source()
        else:
            with pytest.raises(SyncError):
                with files.hold_prepared_attachments_locked(**hold_selectors(env, original)) as held:
                    target.rename(target.with_suffix(".original"))
                    target.write_bytes(BODY)
                    held.verify_source()
