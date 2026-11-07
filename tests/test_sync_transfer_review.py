from tests.test_sync_transfer import receiver_guard
"""Independent S03 adversarial checks; synthetic files and isolated databases."""
from copy import deepcopy
from contextlib import contextmanager
from datetime import timedelta
import hashlib
import os
import threading
import uuid

import pytest

from reno import db, report, review, sync_media, sync_queue, sync_reports, sync_transfer, storage_lifecycle
from reno.sync_protocol import SyncError
from tests.test_sync_transfer import NOW, VIDEO, env, next_payload, publish, report_entry, upload_binding  # noqa: F401


def test_waiting_for_write_lock_rechecks_actual_session_expiry(env, monkeypatch):
    with receiver_guard(env):
        """Admission before a blocked BEGIN IMMEDIATE cannot authorize later writes."""
        attempted = threading.Event()
        clock = {"now": NOW}
        original_clock = sync_queue._clock
        monkeypatch.setattr(sync_queue, "_clock", lambda _: original_clock(clock["now"]))
        failures = []
        frozen_stream = upload_binding(env)["recovery_stream"]
        env["con"].execute("BEGIN IMMEDIATE")

        def request():
            with storage_lifecycle.shared(env["profile"]):
                con = db.connect(env["target_path"])
                con.set_trace_callback(lambda sql: attempted.set() if sql == "BEGIN IMMEDIATE" else None)
                try:
                    sync_transfer.publish_snapshot(con, env["identity"], env["lease"], env["payload"],
                        media_store=env["store"], profile=env["profile"], recovery_stream=frozen_stream)
                    con.commit()
                except BaseException as exc:
                    failures.append(exc)
                    con.rollback()
                finally:
                    con.close()

        thread = threading.Thread(target=request, daemon=True)
        thread.start()
        try:
            assert attempted.wait(5), "Publication never attempted to acquire the write lock"
            clock["now"] = NOW + timedelta(seconds=601)
        finally:
            env["con"].rollback()
            thread.join(8)
        assert not thread.is_alive()
        assert len(failures) == 1 and isinstance(failures[0], SyncError)
        assert failures[0].code == "SYNC_LEASE_EXPIRED"
        assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
        assert env["con"].execute("SELECT count(*) FROM knowledge_atom").fetchone()[0] == 0


@pytest.mark.parametrize("replayed", [False, True])
def test_media_guard_exit_work_cannot_outlive_session_and_then_publish(env, monkeypatch, replayed):
    with receiver_guard(env):
        if replayed:
            publish(env)
            env["con"].commit()
        clock = {"now": NOW}
        original_clock = sync_queue._clock
        original_hold = env["store"].hold_verified
        monkeypatch.setattr(sync_queue, "_clock", lambda _: original_clock(clock["now"]))

        @contextmanager
        def slow_final_verification(descriptors):
            with original_hold(descriptors) as paths:
                yield paths
            # Mirrors a final media SHA pass that consumes the remaining lease.
            clock["now"] = NOW + timedelta(seconds=601)

        monkeypatch.setattr(env["store"], "hold_verified", slow_final_verification)
        if replayed:
            assert publish(env)["replayed"] is True
            assert clock["now"] == NOW  # Completed receipts do not reopen media.
            return
        with pytest.raises(SyncError) as rejected:
            publish(env)
        assert rejected.value.code == "SYNC_LEASE_EXPIRED"
        assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == int(replayed)


def test_old_read_transaction_keeps_matching_report_and_knowledge_after_commit(env):
    with receiver_guard(env):
        env["payload"]["reports"] = [report_entry(env, "# 旧报告\n")]
        publish(env)
        env["con"].commit()
        observer = db.connect(env["target_path"])
        try:
            observer.execute("BEGIN")
            previous_version = db.knowledge_version(observer)
            previous_manifest = sync_reports.manifest(observer, "checklist")
            old_claim = observer.execute("SELECT claim FROM knowledge_atom").fetchone()[0]
            value = next_payload(env)
            value["videos"][0]["tables"]["knowledge_atom"][0]["claim"] = "更新后的基层检查建议"
            value["reports"] = [{**report_entry(env, "# 新报告\n"), "version": value["version"]}]
            publish(env, value)
            env["con"].commit()
            assert db.knowledge_version(observer) == previous_version
            assert observer.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == old_claim
            assert sync_reports.manifest(observer, "checklist") == previous_manifest
            assert report.read_report("checklist", manifest=previous_manifest, con=observer) == "# 旧报告\n"
            observer.rollback()
            assert observer.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "更新后的基层检查建议"
            current_manifest = sync_reports.manifest(observer, "checklist")
            assert current_manifest["publication_id"] == value["publication_id"]
            assert report.read_report("checklist", manifest=current_manifest, con=observer) == "# 新报告\n"
        finally:
            observer.close()


def test_same_source_version_report_refresh_preserves_receiver_review_and_row_identity(env):
    with receiver_guard(env):
        publish(env)
        con = env["con"]
        con.commit()
        review.record_decision(con, atom_id="atom", action="reject", client_id="cloud-reviewer")
        con.commit()
        version = db.knowledge_version(con)
        tables = ("knowledge_atom", "evidence_ref", "atom_fts", "user_decision")
        before = {name: [tuple(row) for row in con.execute(f"SELECT rowid,* FROM {name} ORDER BY rowid")]
                  for name in tables}
        value = deepcopy(env["payload"])
        value["publication_id"] = str(uuid.uuid4())
        value["reports"] = [report_entry(env)]
        publish(env, value)
        con.commit()
        assert db.knowledge_version(con) == version
        assert {name: [tuple(row) for row in con.execute(f"SELECT rowid,* FROM {name} ORDER BY rowid")]
                for name in tables} == before
        metadata = report.metadata()["checklist"]
        assert metadata["has_content"] and metadata["status"] == "stale"


@pytest.mark.parametrize("queued_url", [
    "https://www.bilibili.com/video/BV1Q541167Qg",
    "https://b23.tv/unrelated",
    "https://www.douyin.com/video/7134567890123456789",
])
def test_new_publication_cannot_complete_an_unrelated_original_url(env, queued_url):
    with receiver_guard(env):
        publish(env)
        con = env["con"]
        con.commit()
        sync_queue.enqueue(con, [queued_url], now=NOW)
        item = sync_queue.claim(con, env["identity"], env["lease"], now=NOW)[0]
        con.commit()
        con.execute("UPDATE renovation_project SET name='保留调用方草稿'")
        value = deepcopy(env["payload"])
        value["publication_id"] = str(uuid.uuid4())
        with pytest.raises(SyncError):
            publish(env, value, claims=[{"video_id": VIDEO, "claim": item["claim"]}])
        assert con.execute("SELECT status FROM sync_queue WHERE item_id=?", (item["item_id"],)).fetchone()[0] == "claimed"
        assert con.execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM sync_queue_publication").fetchone()[0] == 0
        assert con.execute("SELECT name FROM renovation_project").fetchone()[0] == "保留调用方草稿"
        assert con.in_transaction


def test_media_removal_during_database_publication_is_blocked_or_rolls_back(env, monkeypatch):
    with receiver_guard(env):
        path = env["store"].verified_path(env["media"])
        original = sync_reports.install_pointers
        outcome = {"attempted": False, "blocked": False}

        def remove_media(con, entries, publication_id, local_version, **kwargs):
            outcome["attempted"] = True
            try:
                path.unlink()
            except OSError:
                outcome["blocked"] = True
            return original(con, entries, publication_id, local_version, **kwargs)

        monkeypatch.setattr(sync_reports, "install_pointers", remove_media)
        failure = None
        try:
            publish(env)
            env["con"].commit()
        except (SyncError, OSError) as exc:
            failure = exc
        assert outcome["attempted"]
        if outcome["blocked"]:
            assert failure is None and path.is_file()
        else:
            assert failure is not None
            assert env["con"].execute("SELECT count(*) FROM sync_publication").fetchone()[0] == 0
            assert env["con"].execute("SELECT count(*) FROM video_asset").fetchone()[0] == 0


def _try_redirect_after_verify(monkeypatch, root, tmp_path):
    """Attempt replacement after validation while the protected handle is open."""
    directory = root / ".sync-versions"
    moved, outside = tmp_path / "moved-versions", tmp_path / "foreign-directory"
    outside.mkdir()
    marker = outside / "private.txt"
    marker.write_bytes(b"private content must remain unchanged")
    original = sync_media._Directory.verify
    result = {"attempted": False, "blocked": False, "swapped": False,
              "outside": outside, "marker": marker}

    def attack(handle):
        original(handle)
        if handle.path != directory or result["attempted"]:
            return
        # Creation verifies the nearest existing ancestor before this target
        # exists. Attack only after the target's identity and handle are held;
        # ENOENT from that earlier check is not proof that a swap was blocked.
        if not any(path == directory for path, _, _ in handle.identities):
            return
        result["attempted"] = True
        # Both explicit move targets stay in this test's disposable directory.
        assert directory.resolve().is_relative_to(tmp_path.resolve())
        assert moved.resolve().is_relative_to(tmp_path.resolve())
        try:
            os.rename(directory, moved)
        except OSError as exc:
            if os.name != "nt" or not isinstance(exc, PermissionError):
                raise
            result["blocked"] = True
            return
        if os.name == "nt":
            import _winapi
            _winapi.CreateJunction(str(outside), str(directory))
        else:
            directory.symlink_to(outside, target_is_directory=True)
        result["swapped"] = True

    monkeypatch.setattr(sync_media._Directory, "verify", attack)
    return result


@pytest.mark.parametrize("operation", ["prepare", "read"])
def test_report_directory_swap_after_validation_cannot_touch_foreign_files(tmp_path, monkeypatch, operation):
    root = tmp_path / "review-reports"
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                                root, tmp_path / "checkpoints")
    storage_lifecycle.initialize(profile)
    version = {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 1}
    content = "# Protected report\n"
    entry = {"name": "checklist", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
             "policy_version": report.POLICY_VERSION, "version": version,
             "generated_at": "2026-01-01T00:00:00.000Z", "review_events_sha256": hashlib.sha256(b"").hexdigest()}
    entries = sync_reports.validate_reports([entry], version)
    if operation == "read":
        with storage_lifecycle.shared(profile):
            prepared = sync_reports.prepare_files(root, entries, profile=profile)[0]
        manifest = {"report_type": entry["name"], "content_sha256": entry["sha256"],
                    "content_file": prepared["storage_key"], "byte_size": prepared["byte_size"]}
    result = _try_redirect_after_verify(monkeypatch, root, tmp_path)
    failed = None
    content = None
    try:
        with storage_lifecycle.shared(profile):
            if operation == "prepare":
                sync_reports.prepare_files(root, entries, profile=profile)
            else:
                content = sync_reports.read_content(root, manifest, profile=profile)
    except (SyncError, OSError, storage_lifecycle.StorageLifecycleError) as exc:
        failed = exc
    assert result["attempted"]
    assert result["marker"].read_bytes() == b"private content must remain unchanged"
    assert sorted(path.name for path in result["outside"].iterdir()) == ["private.txt"]
    if os.name == "nt":
        assert result["blocked"] and not result["swapped"]
        assert failed is None, repr(failed.__context__)
        if operation == "read":
            assert content == entry["content"]
    else:
        assert result["swapped"] and not result["blocked"]
        assert failed is not None or (operation == "read" and content is None)


def test_report_temporary_names_work_beyond_windows_max_path(tmp_path):
    root = tmp_path / ("report-root-" + "x" * 65)
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                                root, tmp_path / "checkpoints")
    storage_lifecycle.initialize(profile)
    version = {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 1}
    content = "# Long-path report\n"
    entry = {"name": "checklist", "content": content, "sha256": hashlib.sha256(content.encode()).hexdigest(),
             "policy_version": report.POLICY_VERSION, "version": version,
             "generated_at": "2026-01-01T00:00:00.000Z", "review_events_sha256": hashlib.sha256(b"").hexdigest()}
    entries = sync_reports.validate_reports([entry], version)
    with storage_lifecycle.shared(profile):
        prepared = sync_reports.prepare_files(root, entries, profile=profile)[0]
        manifest = {"report_type": entry["name"], "content_sha256": entry["sha256"],
                    "content_file": prepared["storage_key"], "byte_size": prepared["byte_size"]}
        assert sync_reports.read_content(root, manifest, profile=profile) == content
