"""Actual permanent files/OS locks, using isolated synthetic peers only.

These tests cover the external half. No SQL activation or control effect is
authorized by this module, and no provider or remote platform is called.
"""
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import uuid

import pytest

from reno import storage_lifecycle as lifecycle, sync_source_continuity as external
from reno import sync_source_readmit_contract as contract
from reno.sync_protocol import SyncError
from tests.test_sync_source_readmit_contract import (SOURCE, TARGET, COMMITTED,
    old_request, register, registration, dispose, disposition)


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(value)
    return value


def activation(profile):
    return {"format_version": 1, "continuity_id": str(uuid.uuid4()),
        "owner_profile_sha256": external.profile_sha256(profile),
        "source_identity": {key: SOURCE[key] for key in ("library_id", "node_id")},
        "target_identity": deepcopy(TARGET), "target_epoch": 7, "activated_at": COMMITTED}


@contextmanager
def owner(profile, *, initialize=False):
    with lifecycle.shared(profile), external.locked(profile, initialize=initialize, timeout=0) as guard:
        yield guard


def activated(profile):
    value = activation(profile)
    with owner(profile, initialize=True) as guard:
        assert guard.activate_files(value) == value
    return value


def bodies(active, old=None):
    request = register(old)
    request["continuity"] = {"format_version": 1, "continuity_id": active["continuity_id"],
        "contract_sha256": external._digest(active), "target_epoch": active["target_epoch"]}
    result = registration(request)
    return request, result


def negative(request, registered):
    body = dispose(request["original_request"], registered=True)
    body["registration"] = deepcopy(registered)
    return body, disposition(body, applied=False)


def snapshot(profile):
    return {str(path): (path.stat().st_mtime_ns, path.read_bytes())
        for root in profile.roots for path in root.rglob("*") if path.is_file()}


@contextmanager
def rejected(code):
    with pytest.raises(SyncError) as caught:
        yield
    assert caught.value.code == "SYNC_SOURCE_CONTINUITY_" + code
    assert "/" not in str(caught.value) and "\\" not in str(caught.value)


def test_ordinary_acquisition_requires_sh_and_never_creates_files(profile):
    before = snapshot(profile)
    with pytest.raises(lifecycle.StorageLifecycleError):
        with external.locked(profile):
            pass
    with lifecycle.shared(profile), rejected("MISSING"):
        with external.locked(profile, timeout=0):
            pass
    assert snapshot(profile) == before


def test_explicit_empty_activation_exact_replay_and_pure_observation(profile):
    active = activated(profile)
    before = snapshot(profile)
    with owner(profile) as guard:
        observed = guard.observe()
        assert observed["contract"] == active
        assert observed["entries"] == {kind: {} for kind in external.KINDS}
        assert guard.activate_files(active) == active
    assert snapshot(profile) == before
    with owner(profile) as guard, rejected("CONTRACT_MISMATCH"):
        guard.activate_files({**active, "continuity_id": str(uuid.uuid4())})


@pytest.mark.parametrize("field,value", [("target_epoch", True), ("target_epoch", 0),
    ("activated_at", "2030-02-30T00:00:00.000Z"), ("continuity_id", "secret/path"),
    ("format_version", True), ("owner_profile_sha256", "a" * 64)])
def test_bad_or_other_profile_activation_is_rejected_before_intent(profile, field, value):
    body = activation(profile)
    body[field] = value
    with owner(profile, initialize=True) as guard:
        with pytest.raises(SyncError):
            guard.activate_files(body)
    assert not (profile.database.parent / external.INTENT_NAME).exists()


@pytest.mark.parametrize("anchor", [external.INTENT_NAME, external.DB_HEAD_NAME,
    external.SEAL_NAME, external.CHECKPOINT_HEAD_NAME, "contract.json"])
def test_each_missing_activation_anchor_fails_closed(profile, anchor):
    activated(profile)
    if anchor in (external.SEAL_NAME, external.CHECKPOINT_HEAD_NAME):
        path = profile.checkpoints / anchor
    elif anchor == "contract.json":
        path = profile.database.parent / external.DIRECTORY / anchor
    else:
        path = profile.database.parent / anchor
    path.unlink()
    with owner(profile) as guard, pytest.raises(SyncError):
        guard.observe()


def test_registration_is_permanent_exact_body_and_contains_no_lease_or_private_body(profile):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        assert guard.register(request, result) == result
        assert guard.register(request, result) == result
        actual = guard.observe()
        assert actual["head"]["ordinal"] == 2
        assert actual["entries"]["registrations"][result["original_request_id"]]["state"] == "committed"
    record = profile.database.parent / external.DIRECTORY / "registrations" / (result["original_request_id"] + ".json")
    raw = record.read_text(encoding="utf-8")
    assert "lease_token" not in raw and "original_request\"" not in raw
    changed = deepcopy(request["original_request"])
    changed["retry_at"] = "2099-01-03T00:00:00.000Z"
    changed_request, changed_result = bodies(active, changed)
    with owner(profile) as guard, rejected("REQUEST_MISMATCH"):
        guard.register(changed_request, changed_result)


def test_register_is_bound_to_exact_activation_and_peer(profile):
    active = activated(profile)
    request, _ = bodies(active)
    request["continuity"]["contract_sha256"] = "1" * 64
    result = registration(request)
    with owner(profile) as guard, rejected("CONTRACT_MISMATCH"):
        guard.register(request, result)


def test_effect_begin_survives_actual_sql_rollback_and_forbids_negative(profile):
    active = activated(profile)
    request, result = bodies(active)
    con = sqlite3.connect(profile.database)
    try:
        con.execute("CREATE TABLE effect_receipt(id TEXT PRIMARY KEY)")
        con.commit()
        with owner(profile) as guard:
            guard.register(request, result)
            marker = guard.begin_effect(result, begun_at=COMMITTED)
            con.execute("BEGIN IMMEDIATE")
            con.execute("INSERT INTO effect_receipt VALUES(?)", (result["original_request_id"],))
            con.rollback()
            assert con.execute("SELECT COUNT(*) FROM effect_receipt").fetchone()[0] == 0
            assert marker["original_request_sha256"] == result["original_request_sha256"]
        with owner(profile) as guard:
            assert result["original_request_id"] in guard.observe()["entries"]["effects"]
            body, receipt = negative(request, result)
            with rejected("OUTCOME_MISMATCH"):
                guard.commit_not_applied(body, receipt)
    finally:
        con.close()


def test_external_negative_permanently_blocks_old_effect(profile):
    active = activated(profile)
    request, result = bodies(active)
    body, receipt = negative(request, result)
    with owner(profile) as guard:
        guard.register(request, result)
        assert guard.commit_not_applied(body, receipt) == receipt
        assert guard.commit_not_applied(body, receipt) == receipt
        with rejected("OUTCOME_MISMATCH"):
            guard.begin_effect(result, begun_at=COMMITTED)
    with owner(profile) as guard:
        assert guard.observe()["entries"]["tombstones"][result["original_request_id"]]["record"] == receipt


def test_missing_registration_never_creates_begin_or_negative(profile):
    active = activated(profile)
    request, result = bodies(active)
    body, receipt = negative(request, result)
    with owner(profile) as guard:
        with rejected("REGISTRATION_REQUIRED"):
            guard.begin_effect(result, begun_at=COMMITTED)
        with rejected("REGISTRATION_REQUIRED"):
            guard.commit_not_applied(body, receipt)
        assert guard.observe()["head"]["ordinal"] == 0


def test_deleted_committed_begin_is_missing_proof_not_a_negative(profile):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
        guard.begin_effect(result, begun_at=COMMITTED)
    path = profile.database.parent / external.DIRECTORY / "effects" / (result["original_request_id"] + ".json")
    path.unlink()
    with owner(profile) as guard, rejected("RECORD_MISSING"):
        body, receipt = negative(request, result)
        guard.commit_not_applied(body, receipt)


def test_reserved_effect_gap_is_unknown_and_exact_resume_only(profile, monkeypatch):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
    real_write = external.sync_state._write
    def interrupt(directory, name, *args, **kwargs):
        if directory.path.name == "effects":
            raise RuntimeError("synthetic gap")
        return real_write(directory, name, *args, **kwargs)
    monkeypatch.setattr(external.sync_state, "_write", interrupt)
    with pytest.raises(RuntimeError, match="synthetic gap"):
        with owner(profile) as guard:
            guard.begin_effect(result, begun_at=COMMITTED)
    monkeypatch.setattr(external.sync_state, "_write", real_write)
    with owner(profile) as guard:
        assert guard.observe()["incomplete"] == [("effects", result["original_request_id"])]
        body, receipt = negative(request, result)
        with rejected("RECORD_INCOMPLETE"):
            guard.commit_not_applied(body, receipt)
        assert guard.begin_effect(result, begun_at=COMMITTED)["begun_at"] == COMMITTED


def test_interrupted_one_head_cannot_replay_different_record(profile, monkeypatch):
    active = activated(profile)
    request, result = bodies(active)
    real_write = external.sync_state._write
    def interrupt(directory, name, *args, **kwargs):
        if name == external.CHECKPOINT_HEAD_NAME:
            raise RuntimeError("synthetic head gap")
        return real_write(directory, name, *args, **kwargs)
    monkeypatch.setattr(external.sync_state, "_write", interrupt)
    with pytest.raises(RuntimeError, match="synthetic head gap"):
        with owner(profile) as guard:
            guard.register(request, result)
    monkeypatch.setattr(external.sync_state, "_write", real_write)
    with owner(profile) as guard:
        with rejected("HEAD_INCOMPLETE"):
            guard.observe()
        other_request, other_result = bodies(active)
        with rejected("HEAD_INCOMPLETE"):
            guard.register(other_request, other_result)
        assert guard.register(request, result) == result


def test_rewound_head_or_missing_journal_is_not_empty(profile):
    active = activated(profile)
    old_head = (profile.database.parent / external.DB_HEAD_NAME).read_bytes()
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
    (profile.database.parent / external.DB_HEAD_NAME).write_bytes(old_head)
    with owner(profile) as guard, rejected("HEAD_INCOMPLETE"):
        guard.observe()
    (profile.database.parent / external.DIRECTORY / "journal" / "00000001.json").unlink()
    with owner(profile) as guard, rejected("JOURNAL_INVALID"):
        guard.observe()


def test_heads_changed_after_capture_are_rejected_at_owner_exit(profile):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
    with rejected("HEAD_CHANGED"):
        with owner(profile) as guard:
            guard.observe()
            head = profile.database.parent / external.DB_HEAD_NAME
            changed = json.loads(head.read_bytes())
            changed["sha256"] = "0" * 64
            head.write_text(contract.canonical_json(changed), encoding="utf-8")


def test_same_size_rewrite_of_pinned_record_is_rejected_or_denied(profile):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
    path = profile.database.parent / external.DIRECTORY / "registrations" / (result["original_request_id"] + ".json")
    if os.name == "nt":
        with owner(profile) as guard:
            guard.observe()
            with pytest.raises(OSError):
                path.write_bytes(path.read_bytes().replace(b'2030-', b'2031-'))
    else:
        with rejected("CHANGED"):
            with owner(profile) as guard:
                guard.observe()
                path.write_bytes(path.read_bytes().replace(b'2030-', b'2031-'))


def test_guard_cannot_be_borrowed_by_another_thread_or_after_release(profile):
    activated(profile)
    with owner(profile) as guard:
        with ThreadPoolExecutor(max_workers=1) as pool:
            with rejected("LOCK_REQUIRED"):
                pool.submit(guard.observe).result()
    with rejected("LOCK_REQUIRED"):
        guard.observe()


def test_caller_exception_object_is_preserved(profile):
    activated(profile)
    original = ValueError("synthetic caller")
    with pytest.raises(ValueError) as caught:
        with owner(profile):
            raise original
    assert caught.value is original


def test_real_child_process_contends_on_same_os_lock(profile):
    activated(profile)
    child = """
import json,sys
from reno import storage_lifecycle as lifecycle,sync_source_continuity as external
from reno.sync_protocol import SyncError
profile=lifecycle.StorageProfile(**json.loads(sys.argv[1]))
with lifecycle.shared(profile):
    try:
        with external.locked(profile,timeout=0):
            print('UNEXPECTED_OPEN')
    except SyncError as error:
        print(error.code)
"""
    args = json.dumps({key: str(getattr(profile, key)) for key in
        ("database", "media", "reports", "checkpoints")})
    with owner(profile):
        result = subprocess.run([sys.executable, "-B", "-c", child, args],
            cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=15,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert result.returncode == 0, result.stderr.decode("utf-8", "replace")
    assert result.stdout.decode().strip() == "SYNC_SOURCE_CONTINUITY_BUSY"


@pytest.mark.skipif(os.name == "nt", reason="Windows pinned handles deny replacement")
def test_same_byte_replacement_of_pinned_anchor_and_ancestor_is_rejected(profile):
    activated(profile)
    with rejected("CHANGED"):
        with owner(profile) as guard:
            guard.observe()
            path = profile.database.parent / external.INTENT_NAME
            other = path.with_suffix(".replacement")
            other.write_bytes(path.read_bytes())
            other.chmod(0o600)
            other.replace(path)
    # A separate instance tests the directory identity, rather than reusing the
    # now-invalid old anchor from the first assertion.
    moved = profile.database.parent.with_name("moved-data")
    with pytest.raises(SyncError):
        with owner(profile) as guard:
            guard.observe()
            profile.database.parent.rename(moved)
            profile.database.parent.mkdir()
            guard.observe()


def test_count_and_deadline_have_closed_bounded_failures(profile, monkeypatch):
    active = activated(profile)
    request, result = bodies(active)
    monkeypatch.setattr(external, "MAX_RECORDS", 0)
    with owner(profile) as guard, rejected("COUNT_LIMIT"):
        guard.register(request, result)
    monkeypatch.setattr(external, "DEADLINE_SECONDS", 0)
    with rejected("DEADLINE_LIMIT"):
        with owner(profile) as guard:
            guard.observe()


def test_unpublished_internal_part_is_preserved_and_does_not_block_exact_resume(profile):
    active = activated(profile)
    request, result = bodies(active)
    directory = profile.database.parent / external.DIRECTORY / "journal"
    leftover = directory / (".sync-" + uuid.uuid4().hex + ".part")
    leftover.write_bytes(b"synthetic unfinished reservation")
    leftover.chmod(0o600)
    with owner(profile) as guard:
        assert guard.observe()["head"]["ordinal"] == 0
        assert guard.register(request, result) == result
    assert leftover.read_bytes() == b"synthetic unfinished reservation"


def test_unknown_file_is_not_adopted_as_an_internal_part(profile):
    activated(profile)
    path = profile.database.parent / external.DIRECTORY / "journal" / "unknown.json"
    path.write_bytes(b"{}")
    path.chmod(0o600)
    with owner(profile) as guard, rejected("UNEXPECTED_FILE"):
        guard.observe()


def test_prefetched_publish_does_not_reread_old_journal_inside_sql_writer(profile, monkeypatch):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
    second_request, second_result = bodies(active)
    con = sqlite3.connect(profile.database)
    try:
        with owner(profile) as guard:
            guard.observe()  # Long closure reads happen before BEGIN.
            real_read = guard._read
            def disallow_old_journal(directory, name, **kwargs):
                if con.in_transaction and directory.path.name == "journal" and name <= "00000002.json":
                    raise AssertionError("old journal read while the SQL writer is held")
                return real_read(directory, name, **kwargs)
            monkeypatch.setattr(guard, "_read", disallow_old_journal)
            con.execute("BEGIN IMMEDIATE")
            assert guard.register(second_request, second_result) == second_result
            guard.verify_for_commit()
            con.commit()
    finally:
        con.close()


def test_observation_dictionary_cannot_change_the_guard_private_closure(profile):
    active = activated(profile)
    request, result = bodies(active)
    with owner(profile) as guard:
        guard.register(request, result)
        observed = guard.observe()
        observed["entries"]["registrations"][result["original_request_id"]]["state"] = "reserved"
        observed["entries"]["registrations"][result["original_request_id"]]["record"]["original_request_sha256"] = "0" * 64
        observed["head"]["ordinal"] = 999
        current = guard.view()
        assert current["head"]["ordinal"] == 2
        assert current["entries"]["registrations"][result["original_request_id"]] == {
            "state": "committed", "record": result}


def test_exact_outer_request_resumes_only_its_interrupted_external_head(profile, monkeypatch):
    active = activated(profile)
    request, result = bodies(active)
    real_write = external.sync_state._write
    def interrupt(directory, name, *args, **kwargs):
        if name == external.CHECKPOINT_HEAD_NAME:
            raise RuntimeError("synthetic publish gap")
        return real_write(directory, name, *args, **kwargs)
    monkeypatch.setattr(external.sync_state, "_write", interrupt)
    with pytest.raises(RuntimeError, match="synthetic publish gap"):
        with owner(profile) as guard:
            guard.register(request, result)
    monkeypatch.setattr(external.sync_state, "_write", real_write)
    with owner(profile) as guard:
        with rejected("HEAD_INCOMPLETE"):
            guard.observe(resume_request=("registrations", str(uuid.uuid4()), contract.request_sha256(request)))
        observed = guard.observe(resume_request=("registrations", request["request_id"], contract.request_sha256(request)))
        assert observed["entries"]["registrations"][result["original_request_id"]]["record"] == result
        assert observed["incomplete"] == [("registrations", result["original_request_id"])]
        assert guard.register(request, result) == result


def test_unexpected_file_after_capture_is_rejected_at_exit(profile):
    activated(profile)
    with rejected("UNEXPECTED_FILE"):
        with owner(profile) as guard:
            guard.observe()
            path = profile.database.parent / external.DIRECTORY / "effects" / "unexpected.json"
            path.write_bytes(b"{}")
            path.chmod(0o600)


def test_fast_commit_check_rejects_head_rewrite_after_prefetch(profile):
    activated(profile)
    with rejected("HEAD_CHANGED"):
        with owner(profile) as guard:
            guard.observe()
            path = profile.database.parent / external.DB_HEAD_NAME
            body = json.loads(path.read_bytes())
            body["sha256"] = "0" * 64
            path.write_text(contract.canonical_json(body), encoding="utf-8")
            guard.verify_for_commit()
