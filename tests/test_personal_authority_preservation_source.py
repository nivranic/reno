"""Actual isolated raw files and locks, without SQL, grants or persistent copies.

Fixtures are synthetic raw trees.  Failure injections are explicit; ordinary
reads, hashes, directory resources and native SH/EX exclusion remain real.
"""
from contextlib import contextmanager
from copy import copy
from concurrent.futures import ThreadPoolExecutor
import asyncio
import errno
import hashlib
import json
import os
import uuid

import pytest

from reno import personal_authority_files as locks
from reno import personal_authority_preservation_contract as capsule
from reno import personal_authority_source_projection as projection
from reno import personal_authority_preservation_source as source
from reno import personal_preservation_files as private_files
from reno import storage_lifecycle as lifecycle, sync_media as safe


ROOT = ".reno-personal-authority-namespace-v2"
NODE = "22222222-2222-4222-8222-222222222222"
ERROR = source.AuthorityRawCaptureError


def _uuid():
    return str(uuid.uuid4())


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _key(path):
    return os.path.normcase(os.path.abspath(os.fspath(path)))


def _identity(path):
    info = os.stat(safe._native_path(path), follow_symlinks=False)
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _write(path, raw=b"synthetic raw evidence"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    if os.name != "nt":
        path.chmod(0o600)
    return path


def _tree(profile, name=ROOT, *, lock=True):
    data = profile.database.parent
    with safe._Directory(data, base=data) as parent:
        private_files.mkdir_child(parent, name)
    tree = data / name
    if lock:
        with safe._Directory(tree, base=data) as directory:
            private_files.publish(directory, "authority.lock", b"\x00")
    return tree


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(value)
    return value


@contextmanager
def _capture(profile, *, operation_id=None, **options):
    with lifecycle.exclusive(profile):
        with source.capture_raw_locked(profile, operation_id=operation_id or _uuid(), **options) as held:
            yield held


def _metadata(profile, **options):
    with _capture(profile, **options) as held:
        value = source.source_metadata(held)
    return value, source.closed_observation(held)


def _row(metadata, relative):
    return next(row for row in metadata["capsule"]["files"] if row["relative_key"] == relative)


def _assert_closed_unavailable(held):
    with pytest.raises(ERROR):
        source.closed_observation(held)


def test_absence_reads_no_sql_or_configuration_and_creates_no_facility(profile, monkeypatch):
    import sqlite3
    before = {path.name for path in profile.database.parent.iterdir()}
    def forbidden(*_args, **_options):
        pytest.fail("Raw source observation attempted SQL or authority initialization")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(locks, "initialize", forbidden)
    value, closed = _metadata(profile)
    assert value["node_observation"] == {"state": "missing", "entry_id": None}
    assert value["capsule"]["files"] == []
    assert value["capsule"]["coverage"] == {"declared_tree": "complete", "declared_copies": "none", "reason": None}
    assert closed["source_file_count"] == 0 and closed["source_byte_size"] == 0
    assert not profile.database.exists()
    assert before == {path.name for path in profile.database.parent.iterdir()}


def test_original_noncanonical_json_node_is_parsed_without_reencoding_its_bytes(profile):
    raw = (' { "instance_id" : "' + NODE + '" } \n').encode()
    _write(profile.database.parent / "node-identity.json", raw)
    value, closed = _metadata(profile)
    row = _row(value, "node-identity.json")
    assert value["node_observation"] == {"state": "parsed", "entry_id": row["entry_id"]}
    assert value["capsule"]["source_context"]["node"]["node_id"] == NODE
    assert row["sha256"] == _sha(raw) and row["byte_size"] == len(raw)
    assert closed["source_file_count"] == 1 and closed["source_byte_size"] == len(raw)


def test_complete_raw_tree_keeps_all_bytes_journals_unknown_stage_and_empty_dirs(profile):
    tree = _tree(profile)
    raw_files = {ROOT + "/authority.lock": b"\x00", "node-identity.json": _wire({"instance_id": NODE}),
        ROOT + "/facts/fence_1.json": b"opaque fact", ROOT + "/unknown.bin": b"\x00\xffopaque",
        ROOT + "/zero.raw": b"", ROOT + "/.stage-pending": b"unpublished raw"}
    for journal in ("journal-v1", "journal-v3", "journal-enrollment-v1"):
        for relative in ("records/envelope.json", "payloads/body.raw", "operations/node", "ordinals/p-0",
                         "successors/published", "segments/receipt", "staging/authority.lock"):
            raw_files[f"{ROOT}/{journal}/{relative}"] = (journal + ":" + relative).encode()
    for relative, raw in raw_files.items():
        if relative != ROOT + "/authority.lock":
            _write(profile.database.parent / relative, raw)
    (tree / "empty").mkdir()
    value, closed = _metadata(profile)
    records = {row["relative_key"]: row for row in value["capsule"]["files"]}
    assert set(records) == set(raw_files)
    for relative, raw in raw_files.items():
        row = records[relative]
        assert row["sha256"] == _sha(raw) and row["byte_size"] == len(raw)
        assert row["source_path_identity"]["inode"] == _identity(profile.database.parent / relative)["inode"]
        assert (profile.database.parent / relative).read_bytes() == raw
    assert any(row["relative_key"] == ROOT + "/empty" and row["children"] == []
               for row in value["capsule"]["directories"])
    assert value["capsule"]["copies"] == value["capsule"]["artifacts"] == value["capsule"]["links"] == []
    assert capsule.normalize(value["capsule"]) == value["capsule"]
    assert projection.from_capsule(value["capsule"]) == value["source_projection"]
    assert closed["source_file_count"] == len(raw_files)
    assert closed["source_byte_size"] == sum(map(len, raw_files.values()))


def test_c2_representable_top_level_unknown_and_both_stage_patterns_remain_original_files(profile):
    _tree(profile)
    operation = "11111111-1111-4111-8111-111111111111"
    bodies = {
        ROOT + "-future-original.bin": (b"unknown top-level original\x00", "unclassified_authority_raw"),
        ROOT + "-reserve-" + operation + ".stage": (b"actual unfinished reservation bytes", "namespace_staging_raw"),
        ROOT + "-header-" + operation + ".stage": (b"actual unfinished header bytes", "namespace_staging_raw"),
    }
    for name, (raw, _role) in bodies.items():
        _write(profile.database.parent / name, raw)
    metadata, closed = _metadata(profile)
    assert {row["relative_key"] for row in metadata["capsule"]["files"]} == set(bodies) | {ROOT + "/authority.lock"}
    for name, (raw, role) in bodies.items():
        row = _row(metadata, name)
        assert row["role"] == role and row["sha256"] == _sha(raw) and row["byte_size"] == len(raw)
        assert (profile.database.parent / name).read_bytes() == raw
    assert closed["source_file_count"] == 4
    assert closed["source_byte_size"] == 1 + sum(len(raw) for raw, _role in bodies.values())


def test_read_verify_and_close_use_the_same_original_fd_without_reopening_source(profile, monkeypatch):
    tree = _tree(profile)
    raw = b"original streamed raw\x00" * 15000
    path = _write(tree / "large.raw", raw)
    opened, reads = [], []
    original_open, original_read = safe._Directory.open_file, source._read_bytes_original
    no_reopen = [False]
    def watch_open(directory, name, *args, **options):
        selected = _key(directory.path / name) == _key(path)
        if selected and no_reopen[0]:
            pytest.fail("Original source pathname reopened after capture")
        stream = original_open(directory, name, *args, **options)
        if selected:
            opened.append((stream, stream.fileno()))
        return stream
    def watch_read(stream, count):
        result = original_read(stream, count)
        if opened and stream is opened[0][0]:
            reads.append((stream.fileno(), len(result), count))
        return result
    monkeypatch.setattr(safe._Directory, "open_file", watch_open)
    monkeypatch.setattr(source, "_read_bytes_original", watch_read)
    with _capture(profile) as held:
        no_reopen[0] = True
        value = source.source_metadata(held)
        source.verify_source(held)
        assert _row(value, ROOT + "/large.raw")["sha256"] == _sha(raw)
        assert len(opened) == 1 and reads
        assert all(descriptor == opened[0][1] for descriptor, _length, _request in reads)
        assert all(request <= 128 * 1024 for _descriptor, _length, request in reads)
    assert opened[0][0].closed
    with pytest.raises(OSError) as closed:
        os.fstat(opened[0][1])
    assert closed.value.errno == errno.EBADF


def test_closed_read_and_seek_counts_equal_actual_payload_and_marker_io(profile, monkeypatch):
    tree = _tree(profile)
    _write(tree / "body.raw", b"raw physical bytes" * 10000)
    original_read, original_seek = source._read_bytes_original, source._seek_original
    returned, seeks = [], []
    def read(stream, count):
        result = original_read(stream, count)
        returned.append((stream, len(result)))
        return result
    def seek(stream, offset):
        result = original_seek(stream, offset)
        seeks.append(stream)
        return result
    monkeypatch.setattr(source, "_read_bytes_original", read)
    monkeypatch.setattr(source, "_seek_original", seek)
    value, closed = _metadata(profile)
    assert closed["read_bytes"] == sum(length for _stream, length in returned)
    assert closed["read_calls"] == len(returned) and closed["seek_calls"] == len(seeks)
    assert len({id(stream) for stream, _length in returned}) == len(value["capsule"]["files"])
    assert all(stream.closed for stream, _length in returned)
    assert closed["read_bytes"] > closed["source_byte_size"]


def test_explicit_verify_adds_to_lifetime_io_budget_instead_of_resetting(profile):
    tree = _tree(profile)
    _write(tree / "body.raw", b"not free on repeated read")
    _first, baseline = _metadata(profile)
    with _capture(profile) as held:
        source.source_metadata(held)
        source.verify_source(held)
    extra = source.closed_observation(held)
    assert extra["read_bytes"] >= baseline["read_bytes"] + 2 * baseline["source_byte_size"]
    assert extra["read_calls"] > baseline["read_calls"]
    assert extra["seek_calls"] > baseline["seek_calls"]


def test_closed_observation_exists_only_after_success_and_has_exact_frozen_hashes(profile):
    operation_id = _uuid()
    _write(profile.database.parent / "node-identity.json", b"bad but retained raw node")
    with _capture(profile, operation_id=operation_id) as held:
        metadata = source.source_metadata(held)
        source.verify_source(held)
        _assert_closed_unavailable(held)
    observed = source.closed_observation(held)
    assert observed == {"format_version": 1, "kind": "personal_authority_raw_census_closed",
        "operation_id": operation_id, "source_projection_sha256": _sha(_wire(metadata["source_projection"])),
        "source_metadata_sha256": _sha(_wire(metadata)), "read_bytes": observed["read_bytes"],
        "read_calls": observed["read_calls"], "seek_calls": observed["seek_calls"],
        "source_file_count": 1, "source_byte_size": len(b"bad but retained raw node")}
    assert set(metadata) == {"format_version", "kind", "operation_id", "capsule", "node_observation", "source_projection"}
    assert metadata["kind"] == "personal_authority_raw_source_observation"
    for name in ("ready", "source_verified", "owner", "write_enabled"):
        assert name not in observed and name not in metadata
    for action in (source.source_metadata, source.verify_source):
        with pytest.raises(ERROR):
            action(held)


@pytest.mark.parametrize("raw", [b"not JSON", b'{}', b'null', b'{"instance_id":false}',
    b'{"instance_id":"00000000-0000-0000-0000-000000000000"}',
    ('{"instance_id":"' + NODE + '","instance_id":"' + NODE + '"}').encode(),
    ('{"instance_id":"' + NODE + '","extra":1}').encode(), b'\xff'])
def test_bad_node_semantics_keep_the_entire_actual_raw_file(profile, raw):
    path = _write(profile.database.parent / "node-identity.json", raw)
    value, _closed = _metadata(profile)
    row = _row(value, "node-identity.json")
    assert value["node_observation"] == {"state": "invalid", "entry_id": row["entry_id"]}
    assert value["capsule"]["source_context"]["node"] is None
    assert row["sha256"] == _sha(raw) and row["byte_size"] == len(raw)
    assert path.read_bytes() == raw


def test_node_semantic_limit_does_not_truncate_or_reject_safe_raw_bytes(profile):
    raw = b"x" * 8193
    _write(profile.database.parent / "node-identity.json", raw)
    value, _closed = _metadata(profile, limits=source.RawCaptureLimits(node_json_bytes=16))
    row = _row(value, "node-identity.json")
    assert value["node_observation"] == {"state": "json_limit", "entry_id": row["entry_id"]}
    assert value["capsule"]["source_context"]["node"] is None
    assert row["sha256"] == _sha(raw) and row["byte_size"] == len(raw)


def test_case_variant_node_is_kept_raw_without_canonical_name_adoption(profile):
    raw = _wire({"instance_id": NODE})
    _write(profile.database.parent / "NODE-IDENTITY.JSON", raw)
    value, _closed = _metadata(profile)
    row = _row(value, "NODE-IDENTITY.JSON")
    assert value["node_observation"] == {"state": "noncanonical_name", "entry_id": row["entry_id"]}
    assert value["capsule"]["source_context"]["node"] is None
    assert row["sha256"] == _sha(raw)


@pytest.mark.parametrize("name", ["node-identity.json", "NODE-IDENTITY.JSON"])
def test_node_named_directory_is_unsafe_instead_of_fabricated_missing_node(profile, name):
    directory = profile.database.parent / name
    directory.mkdir()
    path = _write(directory / "original.raw", b"retained unexpected node directory")
    with pytest.raises(ERROR) as raised:
        with _capture(profile):
            pytest.fail("Node directory was downgraded to a missing identity")
    assert raised.value.code == "AUTHORITY_RAW_CAPTURE_SOURCE_UNSAFE"
    assert path.read_bytes() == b"retained unexpected node directory"


@pytest.mark.parametrize("name", [".reno-personal-authority-future", ".RENO-PERSONAL-AUTHORITY-opaque"])
def test_wide_discovery_does_not_silently_filter_unrepresentable_roots(profile, name):
    tree = _tree(profile, name)
    raw = _write(tree / "actual.raw", b"must remain")
    with pytest.raises(ERROR):
        with _capture(profile):
            pytest.fail("Broad reserved raw tree was omitted to fit C2")
    assert raw.read_bytes() == b"must remain" and (tree / "authority.lock").read_bytes() == b"\x00"


@pytest.mark.skipif(os.name == "nt", reason="POSIX exact trailing-dot/space filename fixture")
def test_c2_unrepresentable_name_is_retained_and_never_lowered_or_trimmed(profile):
    tree = _tree(profile)
    path = _write(tree / "original. ", b"unsafe spelling retained")
    with pytest.raises(ERROR):
        with _capture(profile):
            pytest.fail("Original spelling was silently trimmed")
    assert path.read_bytes() == b"unsafe spelling retained"


@pytest.mark.parametrize("damage", ["missing", "invalid", "hardlink"])
def test_missing_bad_or_aliased_lock_never_gets_initialized(profile, damage):
    tree = _tree(profile, lock=False)
    path = tree / "authority.lock"
    if damage != "missing":
        with safe._Directory(tree, base=profile.database.parent) as directory:
            private_files.publish(directory, "authority.lock", b"wrong" if damage == "invalid" else b"\x00")
    if damage == "hardlink":
        os.link(path, tree / "lock-alias")
    before = path.read_bytes() if path.exists() else None
    with pytest.raises(ERROR):
        with _capture(profile):
            pytest.fail("A bad original authority lock was repaired")
    assert (path.read_bytes() if path.exists() else None) == before


def test_actual_authority_sh_exclusion_and_release(profile):
    tree = _tree(profile)
    with _capture(profile):
        with safe._Directory(tree, base=profile.database.parent) as directory:
            with pytest.raises(locks.FilePrimitiveError, match="FILES_LOCK_BUSY"):
                with locks._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
                    pytest.fail("EX entered while source holds actual SH")
    with safe._Directory(tree, base=profile.database.parent) as directory:
        with locks._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
            pass


def test_marker_reads_start_only_after_actual_native_sh_acquisition(profile, monkeypatch):
    tree = _tree(profile)
    marker_identity = _identity(tree / "authority.lock")
    original_attempt, original_read = locks._ExistingLock._attempt, source._read_bytes_original
    acquired_streams, marker_reads = set(), []
    def attempt(lock):
        acquired = original_attempt(lock)
        if acquired:
            acquired_streams.add(id(lock.stream))
        return acquired
    def read(stream, count):
        info = os.fstat(stream.fileno())
        marker = {"device": str(info.st_dev), "inode": str(info.st_ino)} == marker_identity
        if marker:
            assert id(stream) in acquired_streams, "Marker content read before successful native SH acquisition"
        result = original_read(stream, count)
        if marker:
            marker_reads.append(len(result))
        return result
    monkeypatch.setattr(locks._ExistingLock, "_attempt", attempt)
    monkeypatch.setattr(source, "_read_bytes_original", read)
    _value, closed = _metadata(profile)
    assert marker_reads and sum(marker_reads) == closed["read_bytes"]


def test_busy_authority_lock_fails_without_wait_or_new_scope(profile):
    tree = _tree(profile)
    with safe._Directory(tree, base=profile.database.parent) as directory:
        with locks._ExistingLock(directory, exclusive=True, timeout=0, check=lambda: None):
            with pytest.raises(ERROR):
                with _capture(profile):
                    pytest.fail("A busy original authority lock was bypassed")


def test_hardlinked_source_raw_is_not_omitted_from_a_successful_census(profile):
    tree = _tree(profile)
    path = _write(tree / "original.raw", b"source raw")
    os.link(path, tree / "alias.raw")
    with pytest.raises(ERROR):
        with _capture(profile):
            pytest.fail("Unsafe original inode was omitted")
    assert path.read_bytes() == b"source raw"


def test_symlink_does_not_follow_external_raw_into_the_census(profile, tmp_path):
    tree = _tree(profile)
    outside = _write(tmp_path / "outside.raw", b"outside source scope")
    try:
        (tree / "linked.raw").symlink_to(outside)
    except OSError as exc:
        if os.name == "nt" and getattr(exc, "winerror", None) == 1314:
            pytest.skip("Existing Windows token lacks symlink privilege; no privilege change attempted")
        raise
    with pytest.raises(ERROR):
        with _capture(profile):
            pytest.fail("Symlink became an original file")
    assert outside.read_bytes() == b"outside source scope"


def test_new_selected_entry_fails_normal_exit_and_does_not_create_closed_fact(profile):
    held = None
    with pytest.raises(ERROR):
        with _capture(profile) as held:
            _write(profile.database.parent / (ROOT + "-reservation.json"), b"appeared during capture")
    assert held is not None
    _assert_closed_unavailable(held)


def test_unrelated_data_entry_does_not_change_the_raw_selection(profile):
    with _capture(profile) as held:
        before = source.source_metadata(held)
        _write(profile.database.parent / "ordinary-other-file", b"not selected")
        source.verify_source(held)
        assert source.source_metadata(held) == before
    assert source.closed_observation(held)["source_file_count"] == 0


@pytest.mark.parametrize("change", ["bytes", "same_sha_new_inode", "parent"])
def test_original_file_or_parent_mutation_is_denied_or_detected(profile, change):
    tree = _tree(profile)
    path = _write(tree / "child/original.raw", b"unchanged source bytes")
    # Replacement starts outside the selected tree, so the same-SHA case
    # cannot be rejected merely because a second selected name disappears.
    replacement = _write(profile.media / "replacement.raw", path.read_bytes())
    changed = False
    denied = False
    held = None
    try:
        with _capture(profile) as held:
            try:
                if change == "bytes":
                    path.write_bytes(b"changed source content")
                elif change == "same_sha_new_inode":
                    os.replace(replacement, path)
                else:
                    os.replace(path.parent, tree / "saved-child")
                    _write(path, b"unchanged source bytes")
                changed = True
            except PermissionError:
                denied = True
                assert path.read_bytes() == b"unchanged source bytes"
            source.verify_source(held)
    except ERROR:
        assert changed
        _assert_closed_unavailable(held)
    else:
        assert denied and not changed
        assert source.closed_observation(held)["source_file_count"] >= 2


@pytest.mark.parametrize("later_tree", [False, True])
def test_late_callback_after_listing_cannot_change_an_earlier_selected_tree(profile, monkeypatch, later_tree):
    first = _tree(profile, ".reno-personal-authority-namespace-v1")
    _write(first / "a.raw", b"first tree bytes")
    trigger = profile.database.parent
    if later_tree:
        trigger = _tree(profile)
        _write(trigger / "b.raw", b"later tree bytes")
    trigger_id = _identity(trigger)
    original_scandir = os.scandir
    during_verify, armed, changed = [False], [False], []
    @contextmanager
    def enumerated(path):
        info = os.fstat(path) if type(path) is int else os.stat(path, follow_symlinks=False)
        matches = {"device": str(info.st_dev), "inode": str(info.st_ino)} == trigger_id
        with original_scandir(path) as entries:
            yield entries
        if during_verify[0] and matches:
            armed[0] = True
    def change_after_listing():
        if armed[0] and not changed:
            changed.append(_write(first / "late-new.raw", b"late callback addition"))
    held = None
    with pytest.raises(ERROR):
        with _capture(profile, check_cancelled=change_after_listing) as held:
            monkeypatch.setattr(os, "scandir", enumerated)
            during_verify[0] = True
            source.verify_source(held)
            pytest.fail("A late callback altered an earlier verified raw tree")
    assert changed and changed[0].read_bytes() == b"late callback addition"
    _assert_closed_unavailable(held)


def test_callback_reentry_is_rejected_before_touching_the_reader_cursor(profile):
    tree = _tree(profile)
    _write(tree / "a.raw", b"original source")
    holder, reentered = [], []
    armed = [False]
    def check():
        if armed[0] and not reentered:
            reentered.append(None)
            try:
                source.verify_source(holder[0])
            except ERROR as exc:
                reentered[0] = exc
    with pytest.raises(ERROR) as raised:
        with _capture(profile, check_cancelled=check) as held:
            holder.append(held)
            armed[0] = True
            source.verify_source(held)
    assert reentered and reentered[0] is not None
    assert reentered[0].code == "AUTHORITY_RAW_CAPTURE_REENTRANT"
    assert raised.value.code == reentered[0].code
    _assert_closed_unavailable(holder[0])


@pytest.mark.parametrize("kind", [OSError, ValueError, RuntimeError, KeyboardInterrupt])
def test_callback_primary_exception_is_preserved_and_closed_record_is_absent(profile, kind):
    _tree(profile)
    original = kind("synthetic cancellation; do not serialize this body")
    armed = [False]
    held = None
    def check():
        if armed[0]:
            raise original
    with pytest.raises(kind) as raised:
        with _capture(profile, check_cancelled=check) as held:
            armed[0] = True
    assert raised.value is original and held is not None
    _assert_closed_unavailable(held)


def test_user_primary_error_is_not_replaced_by_source_exit_verification(profile):
    _tree(profile)
    original = RuntimeError("synthetic caller primary")
    held = None
    with pytest.raises(RuntimeError) as raised:
        with _capture(profile) as held:
            raise original
    assert raised.value is original
    _assert_closed_unavailable(held)


@pytest.mark.parametrize("resource", ["file", "lock", "directory"])
def test_simulated_failure_after_actual_normal_close_never_records_success(profile, monkeypatch, resource):
    tree = _tree(profile)
    _write(tree / "a.raw", b"source to be normally closed")
    original_read = source._read_bytes_original
    streams, failures = [], []
    armed = [False]
    failure = OSError(errno.EIO, "synthetic close fault after actual resource release")
    def watched_read(stream, count):
        result = original_read(stream, count)
        streams.append(stream)
        return result
    def after_close():
        if armed[0] and not failures:
            failures.append(True)
            raise failure
    monkeypatch.setattr(source, "_read_bytes_original", watched_read)
    if resource == "file":
        original = source._close_stream_original
        def close(stream):
            result = original(stream)
            after_close()
            return result
        monkeypatch.setattr(source, "_close_stream_original", close)
    elif resource == "lock":
        original = locks._ExistingLock.__exit__
        def close(lock, *args):
            result = original(lock, *args)
            after_close()
            return result
        monkeypatch.setattr(locks._ExistingLock, "__exit__", close)
    else:
        original = source._OwnedDirectory.__exit__
        def close(directory, *args):
            result = original(directory, *args)
            after_close()
            return result
        monkeypatch.setattr(source._OwnedDirectory, "__exit__", close)
    held = None
    with pytest.raises(ERROR):
        with _capture(profile) as held:
            armed[0] = True
    assert failures and streams and all(stream.closed for stream in streams)
    _assert_closed_unavailable(held)
    assert (tree / "a.raw").read_bytes() == b"source to be normally closed"


@pytest.mark.skipif(os.name != "nt", reason="Windows native CloseHandle BOOL result")
def test_simulated_false_after_real_windows_directory_handle_release_blocks_closed_fact(profile, monkeypatch):
    tree = _tree(profile)
    _write(tree / "a.raw", b"source survives native close report failure")
    original_close = source._close_directory_handle_original
    armed, released = [False], []
    def false_after_release(handle):
        result = original_close(handle)
        if armed[0] and not released:
            assert result, "Underlying native close must really release the observed test handle"
            released.append(handle)
            return False
        return result
    monkeypatch.setattr(source, "_close_directory_handle_original", false_after_release)
    held = None
    with pytest.raises(ERROR) as raised:
        with _capture(profile) as held:
            armed[0] = True
    assert raised.value.code == "AUTHORITY_RAW_CAPTURE_CLOSE_FAILED" and released
    native = safe._HandleInfo()
    assert not safe._kernel.GetFileInformationByHandle(released[0], safe.ctypes.byref(native))
    _assert_closed_unavailable(held)
    assert (tree / "a.raw").read_bytes() == b"source survives native close report failure"


def test_caller_primary_survives_a_simulated_real_cleanup_close_failure(profile, monkeypatch):
    tree = _tree(profile)
    _write(tree / "a.raw", b"retained source")
    original_close = source._close_stream_original
    primary = RuntimeError("original caller failure")
    failed = []
    def close_then_fail(stream):
        result = original_close(stream)
        if not failed:
            failed.append(stream)
            raise OSError(errno.EIO, "synthetic source cleanup close failure")
        return result
    held = None
    with pytest.raises(RuntimeError) as raised:
        with _capture(profile) as held:
            monkeypatch.setattr(source, "_close_stream_original", close_then_fail)
            raise primary
    assert raised.value is primary and failed and failed[0].closed
    _assert_closed_unavailable(held)


def test_simulated_read_error_cannot_be_caught_and_followed_by_success(profile, monkeypatch):
    tree = _tree(profile)
    _write(tree / "a.raw", b"actual source")
    original_read = source._read_bytes_original
    armed, faults = [False], []
    def read(stream, count):
        if armed[0] and not faults:
            faults.append(True)
            raise OSError(errno.EIO, "synthetic reader failure")
        return original_read(stream, count)
    held = None
    with pytest.raises(ERROR):
        with _capture(profile) as held:
            monkeypatch.setattr(source, "_read_bytes_original", read)
            armed[0] = True
            with pytest.raises(ERROR) as first:
                source.verify_source(held)
            with pytest.raises(ERROR) as again:
                source.source_metadata(held)
            assert first.value.code == again.value.code
    assert faults
    _assert_closed_unavailable(held)


def test_simulated_original_guard_failure_stays_first_after_verifier_is_restored(profile, monkeypatch):
    tree = _tree(profile)
    _write(tree / "a.raw", b"source remains physically unchanged")
    operation_id = _uuid()
    faults = []
    injected = lifecycle.StorageLifecycleError("STORAGE_LOCK_INVALID")
    with lifecycle.exclusive(profile) as guard:
        original_verify = lifecycle.StorageGuard.verify
        held = None
        with pytest.raises(ERROR) as terminal:
            with source.capture_raw_locked(profile, operation_id=operation_id) as held:
                def fail_once(actual_guard):
                    result = original_verify(actual_guard)
                    if actual_guard is guard and not faults:
                        faults.append(True)
                        raise injected
                    return result
                with monkeypatch.context() as patch:
                    patch.setattr(lifecycle.StorageGuard, "verify", fail_once)
                    with pytest.raises(ERROR) as first:
                        source.source_metadata(held)
                # The actual original verifier is restored and healthy; this
                # must not erase the source window's previously observed fault.
                assert guard.verify() is guard
                for action in (source.source_metadata, source.verify_source):
                    with pytest.raises(ERROR) as repeated:
                        action(held)
                    assert repeated.value is first.value
        assert faults and terminal.value is first.value
        _assert_closed_unavailable(held)
    assert (tree / "a.raw").read_bytes() == b"source remains physically unchanged"


@pytest.mark.parametrize("field,invalid", [("max_read_bytes", True), ("max_read_bytes", 0),
    ("max_read_bytes", 384 * 1024**3 + 1), ("deadline_seconds", True), ("deadline_seconds", 0),
    ("deadline_seconds", float("inf")), ("deadline_seconds", 121), ("node_json_bytes", True),
    ("node_json_bytes", 0), ("node_json_bytes", 4097)])
def test_limits_reject_invalid_or_increased_values(field, invalid):
    with pytest.raises((ERROR, ValueError)):
        source.RawCaptureLimits(**{field: invalid})


def test_instance_post_init_override_cannot_hide_an_increased_read_limit(profile):
    limits = source.RawCaptureLimits()
    object.__setattr__(limits, "__post_init__", lambda: None)
    object.__setattr__(limits, "max_read_bytes", 384 * 1024**3 + 1)
    with lifecycle.exclusive(profile), pytest.raises(ERROR) as raised:
        with source.capture_raw_locked(profile, operation_id=_uuid(), limits=limits):
            pytest.fail("An instance validator override raised the fixed read ceiling")
    assert raised.value.code == "AUTHORITY_RAW_CAPTURE_LIMIT_INVALID"


@pytest.mark.parametrize("mutation_time", ["before_entry", "callback"])
def test_explicit_capsule_limits_are_frozen_before_external_mutation(profile, mutation_time):
    tree = _tree(profile)
    _write(tree / "a.raw", b"actual source")
    external = dict(capsule.DEFAULT_LIMITS)
    limits = source.RawCaptureLimits(capsule_limits=external)
    armed = [False]
    def corrupt_external():
        external["max_files"] = 0
        external["unexpected"] = 999
    if mutation_time == "before_entry":
        corrupt_external()
    def mutate_input():
        if armed[0]:
            corrupt_external()
    with _capture(profile, limits=limits, check_cancelled=mutate_input) as held:
        armed[0] = True
        source.verify_source(held)
    assert source.closed_observation(held)["source_file_count"] == 2


@pytest.mark.parametrize("field", ["max_files", "max_directories", "max_entries", "max_total_bytes"])
def test_structural_budget_failure_does_not_publish_a_smaller_success(profile, field):
    tree = _tree(profile)
    _write(tree / "child/a.raw", b"payload beyond one byte")
    _write(tree / "child/b.raw", b"second payload")
    limits = source.RawCaptureLimits(capsule_limits={**capsule.DEFAULT_LIMITS, field: 1})
    with pytest.raises(ERROR):
        with _capture(profile, limits=limits):
            pytest.fail("Raw source was cropped to fit a structural budget")
    assert (tree / "child/a.raw").read_bytes() == b"payload beyond one byte"
    assert (tree / "child/b.raw").exists()


def test_unselected_data_names_still_consume_the_enumeration_budget(profile):
    for number in range(5):
        _write(profile.database.parent / ("ordinary-" + str(number)), b"not source content")
    limits = source.RawCaptureLimits(capsule_limits={**capsule.DEFAULT_LIMITS, "max_entries": 3})
    with pytest.raises(ERROR):
        with _capture(profile, limits=limits):
            pytest.fail("Unselected DATA entries bypassed the finite enumeration budget")
    assert all((profile.database.parent / ("ordinary-" + str(number))).exists() for number in range(5))


def test_read_budget_exhaustion_cannot_be_erased_by_normal_close(profile):
    tree = _tree(profile)
    _write(tree / "a.raw", b"every repeated byte is billed")
    _value, baseline = _metadata(profile)
    held = None
    with pytest.raises(ERROR):
        with _capture(profile, limits=source.RawCaptureLimits(max_read_bytes=baseline["read_bytes"] - 1)) as held:
            source.source_metadata(held)
    if held is not None:
        _assert_closed_unavailable(held)
    assert (tree / "a.raw").read_bytes() == b"every repeated byte is billed"


def test_deadline_exhaustion_prevents_complete_capture(profile):
    with pytest.raises(ERROR):
        with _capture(profile, limits=source.RawCaptureLimits(deadline_seconds=1e-12)):
            pytest.fail("Expired raw source observation yielded success")


def test_no_guard_or_shared_guard_does_not_get_upgraded(profile):
    for context in (None, lifecycle.shared(profile)):
        if context is None:
            with pytest.raises((ERROR, lifecycle.StorageLifecycleError)):
                with source.capture_raw_locked(profile, operation_id=_uuid()):
                    pytest.fail("Raw source invented an EX guard")
        else:
            with context, pytest.raises((ERROR, lifecycle.StorageLifecycleError)):
                with source.capture_raw_locked(profile, operation_id=_uuid()):
                    pytest.fail("Raw source upgraded SH to EX")


@pytest.mark.parametrize("operation_id", ["not-a-uuid", "00000000-0000-0000-0000-000000000000",
    "{11111111-1111-4111-8111-111111111111}", None, True])
def test_operation_binding_requires_exact_canonical_nonzero_uuid(profile, operation_id):
    with lifecycle.exclusive(profile), pytest.raises((ERROR, ValueError)):
        with source.capture_raw_locked(profile, operation_id=operation_id):
            pytest.fail("Invalid operation binding accepted")


def test_profile_subclass_and_noncallable_callback_are_not_accepted(profile):
    class DerivedProfile(lifecycle.StorageProfile):
        pass
    derived = DerivedProfile(profile.database, profile.media, profile.reports, profile.checkpoints)
    with lifecycle.exclusive(profile):
        with pytest.raises((ERROR, ValueError)):
            with source.capture_raw_locked(derived, operation_id=_uuid()):
                pytest.fail("A subclass supplied an original profile capability")
        with pytest.raises((ERROR, ValueError)):
            with source.capture_raw_locked(profile, operation_id=_uuid(), check_cancelled=True):
                pytest.fail("Non-callable cancellation hook accepted")


def test_live_capability_refuses_wrong_profile_operation_thread_copy_and_forgery(profile, tmp_path):
    operation_id = _uuid()
    other = lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
        tmp_path / "other/reports", tmp_path / "other/checkpoints")
    with _capture(profile, operation_id=operation_id) as held:
        source.require_capture(held, profile=profile, operation_id=operation_id)
        for requested_profile, requested_operation in ((other, operation_id), (profile, _uuid())):
            with pytest.raises(ERROR):
                source.require_capture(held, profile=requested_profile, operation_id=requested_operation)
        with pytest.raises(ERROR):
            source.source_metadata(object.__new__(source.RawAuthorityCapture))
        try:
            copied = copy(held)
        except (TypeError, ERROR):
            copied = None
        if copied is not None:
            assert copied is not held
            with pytest.raises(ERROR):
                source.source_metadata(copied)
        with ThreadPoolExecutor(max_workers=1) as executor:
            result = executor.submit(source.verify_source, held)
            with pytest.raises(ERROR):
                result.result(timeout=5)


def test_metadata_and_closed_observation_are_detached_plain_historical_values(profile):
    _write(profile.database.parent / "node-identity.json", b"raw node")
    with _capture(profile) as held:
        expected = source.source_metadata(held)
        detached = source.source_metadata(held)
        detached["capsule"]["files"].clear()
        detached["source_projection"]["directories"].clear()
        assert source.source_metadata(held) == expected
    original = source.closed_observation(held)
    altered = source.closed_observation(held)
    altered["source_file_count"] = 999
    assert source.closed_observation(held) == original
    with ThreadPoolExecutor(max_workers=1) as executor:
        result = executor.submit(source.closed_observation, held)
        with pytest.raises(ERROR):
            result.result(timeout=5)


def test_different_async_task_cannot_borrow_live_or_closed_capture(profile):
    async def denied(action, held):
        with pytest.raises(ERROR):
            action(held)
    with _capture(profile) as held:
        asyncio.run(denied(source.verify_source, held))
    asyncio.run(denied(source.closed_observation, held))
