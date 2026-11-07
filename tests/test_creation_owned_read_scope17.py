"""DATA-only integration-test candidate; requires formally installed reviewed patch.

These tests have only been AST-parsed, never imported or executed in this DATA task.
Original four failed nodes remain a separate exact required execution selection.
"""
from contextlib import contextmanager
from contextvars import copy_context
from copy import copy, deepcopy
import os
import threading
import time

import pytest

from reno import home_creation_recovery17 as creation, home_recovery_runtime17 as runtime
from reno import budget, source_preservation_witness as witness
from reno import storage_lifecycle as lifecycle, storage_source_authority_roots as home_roots
from reno import sync_source_authority as permanent
from tests.test_sync_agent import env
from tests.test_sync_source_genesis17 import home17, live17
from tests.test_sync_source_publication_freeze17 import publication17, freeze, outboxes
from tests.test_sync_source_publication_local2 import (
    actual, blobs, original, crash_after_write, forbid_new_creation)


def reserved(value, monkeypatch):
    with monkeypatch.context() as fault:
        stop, hit = crash_after_write(fault, "reserved_journal")
        with pytest.raises(RuntimeError) as raised:
            freeze(value)
        assert raised.value is stop and hit
    return original(value)


@contextmanager
def captured(value, record):
    with value.store.reference_operation():
        with creation.capture_original_creation_recovery17_locked(profile=value.profile,
                store=value.store, folder="publications", record_id=record["record_id"],
                expected_record_sha256=permanent.digest(record)) as cap:
            yield cap


def test_actual_scope_rejects_copies_threads_and_copied_context(publication17, monkeypatch):
    value = publication17
    record = reserved(value, monkeypatch)
    before = deepcopy(value.store.read()), actual(value), blobs(value), outboxes(value), deepcopy(value.remote.seen)
    with captured(value, record) as cap:
        scope = creation._read_scope(cap)
        cap._read_control()
        for call in (copy(cap)._read_control, copy(scope).check,
                     lambda: copy_context().run(cap._read_control)):
            with pytest.raises(creation.CreationRecoveryError) as raised:
                call()
            assert raised.value.code == "HOME_CREATION_RECOVERY17_FACTORY_REQUIRED"
        def copied_with_fresh_cached_token():
            token = creation._READ_CONTEXT.set(scope)
            scope._context_token = token  # ordinary data, not private registration
            try:
                with pytest.raises(creation.CreationRecoveryError) as raised:
                    cap._read_control()
                assert raised.value.code == "HOME_CREATION_RECOVERY17_FACTORY_REQUIRED"
            finally:
                creation._READ_CONTEXT.reset(token)
        copy_context().run(copied_with_fresh_cached_token)
        # Ordinary cached fields cannot hide genuine roots or change closing.
        scope.pins = scope.cloud = None
        scope.__dict__["readers"] = ()
        scope.__dict__["retired_readers"] = scope.readers
        scope.__dict__["closing"] = True
        assert scope.readers and not scope.retired_readers and not scope.closing
        cap._read_control()
        errors = []
        def foreign_thread():
            try:
                cap._read_control()
            except BaseException as error:
                errors.append(error)
        thread = threading.Thread(target=foreign_thread)
        thread.start()
        thread.join(timeout=5)
        assert not thread.is_alive() and len(errors) == 1
        assert errors[0].code == "HOME_CREATION_RECOVERY17_FACTORY_REQUIRED"
        cap.verify_source()
    with pytest.raises(creation.CreationRecoveryError):
        cap._read_control()
    assert creation._READ_CURRENT.get() is None and not creation._READ_SCOPES
    assert (value.store.read(), actual(value), blobs(value), outboxes(value), value.remote.seen) == before


def test_actual_marker_change_after_cheap_callback_is_refused_at_full_edge(publication17, monkeypatch):
    value = publication17
    record = reserved(value, monkeypatch)
    with value.store.reference_operation():
        guard = lifecycle.require_guard(value.profile)
        path = guard._locks[0].directory.path / lifecycle.LOCK_NAME
        saved, stamp = path.read_bytes(), path.stat()
        try:
            with pytest.raises(lifecycle.StorageLifecycleError):
                with creation.capture_original_creation_recovery17_locked(profile=value.profile,
                        store=value.store, folder="publications", record_id=record["record_id"]) as cap:
                    cap._read_control()
                    # Use actual bytes after the locked first byte, keep length,
                    # inode and mtime. A cheap cancellation check is not full proof.
                    with path.open("r+b") as stream:
                        stream.seek(1)
                        stream.write(b"X" if saved[1:2] != b"X" else b"Y")
                        stream.flush()
                        os.fsync(stream.fileno())
                    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
                    cap._read_control()
                    cap.verify_source()
        finally:
            with path.open("r+b") as stream:
                stream.seek(1)
                stream.write(saved[1:2])
                stream.flush()
                os.fsync(stream.fileno())
            os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
        assert path.read_bytes() == saved


@pytest.mark.parametrize("body_failure", [False, True])
def test_real_final_reads_hold_owner_after_public_retirement_and_close_all_pins(
        publication17, monkeypatch, body_failure):
    value = publication17
    record = reserved(value, monkeypatch)
    stop, cleanup_stop = RuntimeError("original body cancellation"), RuntimeError("last checkpoint fault")
    final_reads, held_files, final_full = [], [], []
    real_reader_verify = home_roots._Reader.verify
    real_checkpoint_verify = witness._CheckpointCache.verify_full
    real_full = creation._OwnedReadScope17.full

    def observe_reader(reader):
        scope = creation._READ_CURRENT.get()
        if scope is not None and scope.closing:
            binding = scope.check()
            assert not binding.capture._active and creation._CURRENT.get() is None
            assert permanent._CURRENT.get() is binding.owner and binding.lock.held
            assert lifecycle._CURRENT.get() is binding.storage_guard
            held_files.extend(item[2] for item in reader.files if item[2] is not None)
            final_reads.append(reader)
        return real_reader_verify(reader)

    def checkpoint_fault(cache):
        answer = real_checkpoint_verify(cache)
        scope = creation._READ_CURRENT.get()
        if scope is not None and scope.closing:
            raise cleanup_stop
        return answer

    def observe_full(scope):
        result = real_full(scope)
        if scope.closing:
            final_full.append(scope)
        return result

    monkeypatch.setattr(home_roots._Reader, "verify", observe_reader)
    monkeypatch.setattr(witness._CheckpointCache, "verify_full", checkpoint_fault)
    monkeypatch.setattr(creation._OwnedReadScope17, "full", observe_full)
    with pytest.raises(RuntimeError) as raised:
        with captured(value, record):
            if body_failure:
                def original_body_frame():
                    raise stop
                original_body_frame()
    assert raised.value is (stop if body_failure else cleanup_stop)
    if body_failure:
        frames, traceback = [], raised.value.__traceback__
        while traceback is not None:
            frames.append(traceback.tb_frame.f_code.co_name)
            traceback = traceback.tb_next
        assert "original_body_frame" in frames
    assert final_reads and final_full and held_files
    assert all(stream.closed for stream in held_files)
    assert value.store._directory is None and not creation._READ_SCOPES


@pytest.mark.parametrize("point", ["payload", "post_runtime_assignment"])
def test_partial_refresh_reader_gets_actual_final_verification_before_close(
        publication17, monkeypatch, point):
    value = publication17
    saved = reserved(value, monkeypatch)
    forbid_new_creation(monkeypatch, value)
    before_state, before_blobs = deepcopy(value.store.read()), blobs(value)
    before_seen, before_outboxes = deepcopy(value.remote.seen), outboxes(value)
    stop = RuntimeError("original partial refresh at " + point)
    selected_registration = None
    selected_old = selected_new = None
    actual_attempts, final_attempts, successful_actual_reads = [], [], []
    real_payload, real_verify = home_roots._read_payload, home_roots._Reader.verify
    real_refresh = runtime._refresh_home_creation

    def read_registration(scope):
        # The immutable factory registration is the expected reader selection.
        # Observation lists below record actual calls, never grant ownership.
        with creation._READ_SCOPE_LOCK:
            registered = creation._READ_SCOPES.get(scope)
        assert registered is not None and registered.capture is not None
        assert registered.capture.owner is registered.owner
        assert permanent._CURRENT.get() is registered.owner and registered.lock.held
        return registered

    def select_fault(scope, new_reader, *, assigned):
        nonlocal selected_registration, selected_old, selected_new
        registered = read_registration(scope)
        assert not registered.closing
        old_reader = registered.current_reader
        assert registered.home is registered.capture._home
        assert old_reader is not None and old_reader is not new_reader
        assert any(reader is old_reader for reader in registered.readers)
        assert any(reader is new_reader for reader in registered.readers)
        assert not any(reader is old_reader or reader is new_reader for reader in registered.retired_readers)
        assert registered.home._reader is (new_reader if assigned else old_reader)
        assert all(any(anchor[0] is reader for anchor in registered.reader_anchors)
                   for reader in (old_reader, new_reader))
        assert old_reader.files and new_reader.files
        selected_registration, selected_old, selected_new = registered, old_reader, new_reader

    def interrupted_payload(reader):
        answer = real_payload(reader)
        scope = creation._READ_CURRENT.get()
        if point == "payload" and scope is not None and selected_registration is None:
            registered = read_registration(scope)
            if registered.home is not None and reader is not registered.home._reader:
                select_fault(scope, reader, assigned=False)
                raise stop
        return answer

    def interrupted_runtime(cap, producer, observed, folder, **options):
        # Delegates all real namespace/prefix/SHA reads and actual Home update.
        answer = real_refresh(cap, producer, observed, folder, **options)
        scope = creation._READ_CURRENT.get()
        if point == "post_runtime_assignment" and scope is not None and selected_registration is None:
            registered = read_registration(scope)
            assert registered.capture is cap
            select_fault(scope, registered.home._reader, assigned=True)
            # The caller's scope.adopt_reader has not run: original current is
            # still old and neither genuine reader has been retired.
            assert read_registration(scope) is selected_registration
            raise stop
        return answer

    def checked(reader):
        actual_attempts.append(reader)
        scope = creation._READ_CURRENT.get()
        if scope is not None:
            registered = read_registration(scope)
            if registered.closing:
                assert not registered.capture._active and creation._CURRENT.get() is None
                assert lifecycle._CURRENT.get() is registered.storage_guard
                assert any(item is reader for item in registered.readers)
                final_attempts.append(reader)
        answer = real_verify(reader)
        successful_actual_reads.append(reader)
        return answer

    def no_rpc(*args, **kwargs):
        pytest.fail("An original partial publication dispatched new RPC")

    monkeypatch.setattr(home_roots, "_read_payload", interrupted_payload)
    monkeypatch.setattr(runtime, "_refresh_home_creation", interrupted_runtime)
    monkeypatch.setattr(home_roots._Reader, "verify", checked)
    monkeypatch.setattr(value.client, "_request", no_rpc)
    with pytest.raises(RuntimeError) as raised:
        freeze(value)
    assert raised.value is stop and selected_registration is not None
    frames, traceback = [], raised.value.__traceback__
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    assert ("interrupted_payload" if point == "payload" else "interrupted_runtime") in frames
    # The pre-assignment case retains its original real new-reader successful
    # final SHA. The post-assignment case additionally requires both genuine,
    # unretired readers to reach original verify before their handles close;
    # old mutable heads may correctly fail after actual head repair.
    assert any(reader is selected_new for reader in successful_actual_reads)
    if point == "post_runtime_assignment":
        assert selected_registration.current_reader is selected_old
        assert selected_registration.home._reader is selected_new
        assert any(reader is selected_old for reader in final_attempts)
        assert any(reader is selected_new for reader in final_attempts)
    else:
        assert any(reader is selected_new for reader in final_attempts)
    assert any(reader is selected_old for reader in actual_attempts)
    for reader in (selected_old, selected_new):
        assert all(stream.closed for _directory, _name, stream, *_ in reader.files if stream is not None)
        assert all(not directory.handles for directory in reader.directories)
    assert not creation._READ_SCOPES and creation._READ_CURRENT.get() is None
    assert value.store._directory is None
    assert value.store.read() == before_state and blobs(value) == before_blobs
    assert outboxes(value) == before_outboxes and value.remote.seen == before_seen
    assert original(value) == saved
    freeze(value)
    assert original(value) == saved
    assert value.store.read()["pending_publication"]["publication_id"] == saved["record_id"]
    body = before_blobs[saved["payload_ref"]["sha256"] + ".json"]
    assert outboxes(value) == {saved["record_id"] + ".json": body}
    assert blobs(value) == before_blobs and value.remote.seen == before_seen


def test_scope_constructor_first_real_full_failure_retires_every_original_owner(
        publication17, monkeypatch):
    value = publication17
    saved = reserved(value, monkeypatch)
    forbid_new_creation(monkeypatch, value)
    before_state, before_blobs = deepcopy(value.store.read()), blobs(value)
    before_seen, before_outboxes = deepcopy(value.remote.seen), outboxes(value)
    stop = RuntimeError("first actual scope full before factory assignment")
    real_full = creation._OwnedReadScope17.full
    selected = None

    def paid_rows():
        with budget._connection(value.profile) as con:
            return (
                tuple(tuple(row) for row in con.execute(
                    "SELECT * FROM budget_operation ORDER BY operation_id")),
                tuple(tuple(row) for row in con.execute(
                    "SELECT * FROM budget_attempt ORDER BY attempt_id")))

    before_paid = paid_rows()

    def interrupted_first_full(scope):
        nonlocal selected
        with creation._READ_SCOPE_LOCK:
            before = creation._READ_SCOPES.get(scope)
        assert before is not None
        original_clock = before.owner.started, before.owner.operation_depth, before.capture._started
        answer = real_full(scope)  # Real State/SH, marker, directory and original deadline.
        assert (before.owner.started, before.owner.operation_depth, before.capture._started) == original_clock
        if selected is None:
            with creation._READ_SCOPE_LOCK:
                registered = creation._READ_SCOPES.get(scope)
            assert registered is not None and registered.capture is before.capture
            assert registered.owner is before.owner and registered.lock is before.lock
            assert not registered.closing and not registered.readers and not registered.retired_readers
            assert registered.current_reader is None
            assert registered.home is registered.cloud is registered.pins is None
            assert creation._CURRENT.get() is None and creation._CONTEXT.get() is None
            assert registered.capture._context_token is None  # Public assignment has not occurred.
            assert creation._READ_CURRENT.get() is scope and creation._READ_CONTEXT.get() is scope
            assert permanent._CURRENT.get() is registered.owner and registered.lock.held
            directories = []
            for directory in (registered.owner.root, registered.owner.checkpoint_root,
                              *registered.owner.directory_index.values(),
                              *(lock.directory for lock in registered.storage_guard._locks)):
                while directory is not None:
                    if not any(item is directory for item in directories):
                        assert directory.handles
                        directories.append(directory)
                    directory = directory.parent
            streams = tuple(item[2] for item in registered.owner.pins) + tuple(
                lock.stream for lock in registered.storage_guard._locks)
            assert directories and streams and all(not stream.closed for stream in streams)
            # Immutable genuine registration plus actual resources, not a fake
            # reader selection or a caller-supplied verified flag.
            selected = (registered, tuple(directories), streams, original_clock)
            raise stop
        return answer

    def no_rpc(*args, **kwargs):
        pytest.fail("Constructor recovery dispatched new RPC")

    monkeypatch.setattr(creation._OwnedReadScope17, "full", interrupted_first_full)
    monkeypatch.setattr(value.client, "_request", no_rpc)
    with pytest.raises(RuntimeError) as raised:
        with captured(value, saved):
            pytest.fail("A constructor fault yielded a public capture")
    assert raised.value is stop and selected is not None
    frames, traceback = [], raised.value.__traceback__
    while traceback is not None:
        frames.append(traceback.tb_frame.f_code.co_name)
        traceback = traceback.tb_next
    assert "interrupted_first_full" in frames
    registered, directories, streams, original_clock = selected
    assert not registered.capture._active and not registered.lock.held
    assert all(not directory.handles for directory in directories)
    assert all(stream.closed for stream in streams)
    assert creation._READ_CURRENT.get() is None and creation._READ_CONTEXT.get() is None
    assert creation._CURRENT.get() is None and creation._CONTEXT.get() is None
    assert permanent._CURRENT.get() is None and not creation._READ_SCOPES
    assert value.store._directory is None
    assert value.store.read() == before_state and blobs(value) == before_blobs
    assert outboxes(value) == before_outboxes and value.remote.seen == before_seen
    assert paid_rows() == before_paid and original(value) == saved
    # The old selector works in the normal second factory; no UUID replacement.
    with captured(value, saved) as cap:
        assert cap.record == saved
        cap.verify_source()
    assert not creation._READ_SCOPES and value.store._directory is None
    assert value.store.read() == before_state and paid_rows() == before_paid
    freeze(value)
    assert original(value) == saved
    assert value.store.read()["pending_publication"]["publication_id"] == saved["record_id"]
    body = before_blobs[saved["payload_ref"]["sha256"] + ".json"]
    assert outboxes(value) == {saved["record_id"] + ".json": body}
    assert blobs(value) == before_blobs and value.remote.seen == before_seen
    assert paid_rows() == before_paid


def test_actual_owner_deadline_is_not_restarted_or_extended(publication17, monkeypatch):
    value = publication17
    record = reserved(value, monkeypatch)
    with pytest.raises(Exception) as raised:
        with captured(value, record) as cap:
            owner, scope = cap.owner, creation._read_scope(cap)
            before = owner.started, owner.operation_depth, cap._started
            # Actual clock, no altered threshold, owner.started or depth.
            time.sleep(permanent.DEADLINE_SECONDS + 0.05)
            try:
                cap._read_control()
            finally:
                assert (owner.started, owner.operation_depth, cap._started) == before
    assert getattr(raised.value, "code", None) == "SYNC_SOURCE_AUTHORITY_DEADLINE_LIMIT"
    assert not creation._READ_SCOPES and creation._READ_CURRENT.get() is None
