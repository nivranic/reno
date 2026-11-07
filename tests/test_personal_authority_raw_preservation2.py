"""Real isolated format-2 attempts, immutable history and explicit observations.

Fixtures use fresh synthetic TEMP trees and original profile/authority locks.
Faults are injected at named private seams after actual effects where stated.
No test imports old evidence as authority, changes limits, uses SQL or profiles.
"""
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace
import uuid

import pytest

from reno import personal_authority_files as authority
from reno import personal_authority_preservation_admission as admission
from reno import personal_authority_preservation_routing as routing
from reno import personal_authority_preservation_source as source
from reno import personal_authority_preservation_target as target1
from reno import personal_authority_preservation_target2 as target
from reno import personal_authority_raw_preservation as first
from reno import personal_authority_raw_preservation2 as preservation
from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_personal_authority_preservation_source import _tree


ROOT = ".reno-personal-authority-namespace-v2"
NS = ".reno-authority-raw-preservation-v2"
CLAIM = "resume-slot-02.claim"
ANCHOR = "admission-q1-sha256-"
ERROR = preservation.AuthorityRawPreservationError
LATE = {"source-verified.json", "manifest.json", "receipt.json"}


def _native(path):
    """Adapt only an oracle syscall path, retaining the original logical path.

    Windows can enumerate these genuine long fixture names yet silently return
    False from an unprefixed Path.is_file/is_dir. Do not shorten the fixture or
    reuse the product's path helper to decide what the product preserved.
    """
    value = os.path.abspath(os.fspath(path))
    if os.name == "nt" and not value.startswith("\\\\?\\"):
        value = "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value
    return Path(value)


def _children(root):
    for child in _native(root).iterdir():
        yield root / child.name


def _walk(root, pattern="*"):
    native_root = _native(root)
    for path in native_root.rglob(pattern):
        yield root / path.relative_to(native_root)


def _write(path, raw=b"synthetic raw evidence"):
    _native(path.parent).mkdir(parents=True, exist_ok=True)
    _native(path).write_bytes(raw)
    if os.name != "nt":
        _native(path).chmod(0o600)
    return path


def _snapshot(root):
    """Same observation fields as the v1 helper, with independent native paths."""
    if not _native(root).exists():
        return None
    result = {}
    for path in [root, *_walk(root)]:
        info = _native(path).stat(follow_symlinks=False)
        assert stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        result[path.relative_to(root).as_posix()] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, "st_file_attributes", None),
            None if stat.S_ISDIR(info.st_mode) else _native(path).read_bytes())
    return result


def _uuid():
    return str(uuid.uuid4())


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _identity(path):
    info = _native(path).stat(follow_symlinks=False)
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _files(root):
    """Only a current test's exact synthetic operation is traversed."""
    result = {}
    for path in _walk(root):
        info = _native(path).stat(follow_symlinks=False)
        assert stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
        if stat.S_ISREG(info.st_mode):
            result[path.relative_to(root).as_posix()] = _native(path).read_bytes()
    return result


def _preserved_files(root):
    return {key: (_identity(root / key), raw) for key, raw in _files(root).items()}


def _assert_preserved(root, earlier):
    for key, (identity, raw) in earlier.items():
        assert _identity(root / key) == identity
        assert (_native(root / key)).read_bytes() == raw


@pytest.fixture
def profile(tmp_path):
    value = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
        tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(value)
    return value


def _create(profile, *, operation_id=None, **options):
    with lifecycle.exclusive(profile):
        return preservation.create_raw_preservation_locked(profile,
            operation_id=operation_id or _uuid(), **options)


def _resume(profile, operation_id, request_sha, **options):
    with lifecycle.exclusive(profile):
        return preservation.resume_raw_preservation_locked(profile, operation_id=operation_id,
            expected_request_sha256=request_sha, **options)


def _verify(profile, operation_id, request_sha, generation, **options):
    with lifecycle.exclusive(profile):
        return preservation.verify_raw_preservation_locked(profile, operation_id=operation_id,
            expected_request_sha256=request_sha, generation=generation, **options)


def _assert_package(profile, result, expected):
    """Rebuild current generation's graph from disk, without a product codec."""
    root = profile.database.parent / NS / result.operation_id
    assert result.package_path == root
    raw = _files(root)
    docs = {key: json.loads(value) for key, value in raw.items()
            if key.endswith(".json") and "/failures/" not in key}
    assert all(_wire(value) == raw[key] for key, value in docs.items())
    base = f"runs/{result.generation:02d}"
    request, selection = docs["request.json"], docs["selection.json"]
    metadata, capsule = docs["source-metadata.json"], docs[base + "/capsule.json"]
    verified, manifest, receipt = (docs[base + "/" + name] for name in
                                  ("source-verified.json", "manifest.json", "receipt.json"))
    assert result.request_sha256 == _sha(raw["request.json"])
    assert result.run_id == docs[base + "/run-attempt.json"]["run_id"]
    assert request["format_version"] == 2
    assert request["target"]["namespace_identity"] == _identity(root.parent)
    assert request["target"]["operation_identity"] == _identity(root)
    anchor = root / (ANCHOR + result.request_sha256)
    assert _native(anchor).is_dir() and list(_children(anchor)) == []
    assert docs["runs/01/run-attempt.json"]["anchor"] == {
        "relative_key": anchor.name, "identity": _identity(anchor)}
    for field, path in (("manifest_sha256", base + "/manifest.json"),
                        ("receipt_sha256", base + "/receipt.json"),
                        ("capsule_sha256", base + "/capsule.json"),
                        ("source_projection_sha256", "source-projection.json")):
        assert getattr(result, field) == _sha(raw[path])
    assert selection["request_sha256"] == result.request_sha256
    for field, path in (("source_metadata_sha256", "source-metadata.json"),
                        ("source_projection_sha256", "source-projection.json"),
                        ("initial_run_attempt_sha256", "runs/01/run-attempt.json")):
        assert selection[field] == _sha(raw[path])
    assert metadata["source_projection"] == docs["source-projection.json"]
    assert metadata["capsule"]["copies"] == []
    assert capsule["files"] == metadata["capsule"]["files"]
    assert capsule["artifacts"] == capsule["links"] == []
    sources = {row["relative_key"]: row for row in capsule["files"]}
    assert set(sources) == set(expected)
    assert selection["source_file_ids"] == sorted(row["entry_id"] for row in sources.values())
    copies = {row["source_entry_id"]: row for row in capsule["copies"]}
    selected = {row["source_entry_id"]: row for row in verified["selected_copies"]}
    assert set(copies) == set(selected) == set(selection["source_file_ids"])
    for key, content in expected.items():
        entry = sources[key]
        assert (entry["sha256"], entry["byte_size"]) == (_sha(content), len(content))
        copied, origin = copies[entry["entry_id"]], selected[entry["entry_id"]]
        record_key = origin["copy_record_relative_key"]
        record = docs[record_key]
        assert origin["copy_record_sha256"] == _sha(raw[record_key])
        assert origin["copy_record_byte_size"] == len(raw[record_key])
        assert origin["origin_slot"] == record["slot"] <= result.generation
        assert origin["origin_run_id"] == record["run_id"]
        assert record["copy"] == copied
        payload = copied["target_relative_key"]
        assert raw[payload] == content
        assert copied["sha256"] == _sha(content) and copied["byte_size"] == len(content)
        assert {name: copied["copy_path_identity"][name] for name in ("device", "inode")} == _identity(root / payload)
        assert {name: copied["copy_handle_identity"][name] for name in ("device", "inode")} == _identity(root / payload)
        assert copied["target_parent_identity"] == _identity((root / payload).parent)
        origin_base = f"runs/{origin['origin_slot']:02d}"
        attempt_key = origin_base + "/copy-attempts/" + payload.split("/")[3] + ".json"
        assert record["copy_attempt_sha256"] == _sha(raw[attempt_key])
        assert docs[attempt_key]["target_relative_key"] == payload
    a, b = verified["source_closed"], verified["target_closed"]
    assert a["source_file_count"] == len(expected)
    assert a["source_byte_size"] == sum(map(len, expected.values()))
    assert b["slot"] == result.generation and b["selected_copies"] == verified["selected_copies"]
    assert b["reserved_slots"] == list(range(1, result.generation + 1))
    late = {base + "/" + name for name in LATE}
    assert {row["relative_key"] for row in b["files"]} == set(raw) - late
    dirs = {"", *(path.relative_to(root).as_posix() for path in _walk(root, "*") if _native(path).is_dir())}
    assert {row["relative_key"] for row in b["directories"]} == dirs
    before_paths = dirs | (set(raw) - late)
    for row in b["directories"]:
        key = row["relative_key"]
        assert row["identity"] == _identity(root / key)
        assert row["children"] == sorted([path.rpartition("/")[2] for path in before_paths
            if path and path.rpartition("/")[0] == key], key=lambda value: value.encode("utf-8"))
    for row in b["files"]:
        key = row["relative_key"]
        assert (row["sha256"], row["byte_size"]) == (_sha(raw[key]), len(raw[key]))
        assert {name: row["path_identity"][name] for name in ("device", "inode")} == _identity(root / key)
        assert row["parent_identity"] == _identity((root / key).parent)
    categories = [row for category in ("facts", "payloads", "markers", "retained") for row in manifest[category]]
    assert len(categories) == len({row["relative_key"] for row in categories})
    assert {row["relative_key"] for row in categories} == (set(raw) - late) | {base + "/source-verified.json"}
    for row in categories:
        assert (row["sha256"], row["byte_size"]) == (_sha(raw[row["relative_key"]]), len(raw[row["relative_key"]]))
    assert receipt["manifest_sha256"] == _sha(raw[base + "/manifest.json"])
    assert receipt["source_verified_sha256"] == _sha(raw[base + "/source-verified.json"])
    assert receipt["publication"] == "published_observed"
    assert set(receipt).isdisjoint({"factory_returned", "publisher_closed", "ready", "owner"})
    if result.generation == 2:
        assert raw[CLAIM] == b""
        attempt = docs[base + "/run-attempt.json"]
        assert attempt["run_id"] != docs["runs/01/run-attempt.json"]["run_id"]
        assert attempt["predecessor"]["run_attempt_sha256"] == _sha(raw["runs/01/run-attempt.json"])
        assert attempt["frozen_selection_sha256"] == _sha(raw["selection.json"])
        new_bytes = sum(row["byte_size"] for row in b["files"] if row["relative_key"].startswith(base + "/"))
        assert b["counters"]["confirmed_write_bytes"] >= new_bytes
    assert result.counters["uncertain_read_calls"] == result.counters["uncertain_write_bytes"] == 0
    return root, docs, raw


def _partial(profile, monkeypatch, *, complete_record=True):
    """One real first attempt stops immediately after a record or its payload."""
    operation = _uuid()
    root = profile.database.parent / NS / operation
    faults = []
    original_publish = target._Target.publish
    def checkpoint(phase):
        if complete_record and phase == "copy_record_published" and not faults:
            faults.append(phase)
            raise OSError(errno.EIO, "synthetic failure after actual formal copy record")
    def publish(state, key, kind, value):
        if not complete_record and kind == "copy_record" and not faults:
            assert (_native(root / value["copy"]["target_relative_key"])).read_bytes()
            faults.append(key)
            raise OSError(errno.EIO, "synthetic failure after payload before formal record")
        return original_publish(state, key, kind, value)
    with monkeypatch.context() as patch:
        patch.setattr(target, "_checkpoint", checkpoint)
        patch.setattr(target._Target, "publish", publish)
        with pytest.raises(ERROR) as raised:
            _create(profile, operation_id=operation)
    assert len(faults) == 1
    assert (_native(root / "selection.json")).is_file()
    assert len(list(_children(root / "runs/01/copy-records"))) == int(complete_record)
    assert not (_native(root / "runs/01/receipt.json")).exists()
    return operation, root, _sha((_native(root / "request.json")).read_bytes()), raised.value


def test_partial_first_attempt_uses_fresh_source_reuses_records_and_preserves_every_old_byte(profile, monkeypatch):
    tree = _tree(profile)
    raw = b"fresh real source for a later missing entry\x00"
    _write(tree / "unknown.raw", raw)
    captures, reads, reused, created = [], [], [], []
    original_capture, original_read = source._capture_for_preservation_locked, source._read_bytes_original
    @contextmanager
    def capture(token):
        with original_capture(token) as held:
            captures.append(held)
            yield held
    def read(stream, count):
        reads.append(stream)
        return original_read(stream, count)
    monkeypatch.setattr(source, "_capture_for_preservation_locked", capture)
    monkeypatch.setattr(source, "_read_bytes_original", read)
    operation, root, request_sha, _failure = _partial(profile, monkeypatch)
    old = _preserved_files(root)
    first_streams = set(reads)
    old_records = {path.stem: _native(path).read_bytes() for path in _children(root / "runs/01/copy-records")}
    selection = json.loads((_native(root / "selection.json")).read_bytes())
    assert 0 < len(old_records) < len(selection["source_file_ids"])
    def checkpoint(phase):
        if phase == "copy_reused":
            reused.append(phase)
        elif phase == "copy_record_published":
            created.append(phase)
    monkeypatch.setattr(target, "_checkpoint", checkpoint)
    resumed = _resume(profile, operation, request_sha)
    assert resumed.kind == "raw_preservation_resumed" and resumed.generation == 2
    assert len(captures) == 2 and captures[0] is not captures[1]
    assert set(reads) - first_streams and all(stream.closed for stream in reads)
    assert len(reused) == len(old_records)
    assert len(created) == len(selection["source_file_ids"]) - len(old_records)
    _assert_preserved(root, old)
    _, docs, _raw = _assert_package(profile, resumed, {ROOT + "/authority.lock": b"\x00", ROOT + "/unknown.raw": raw})
    selected = docs["runs/02/source-verified.json"]["selected_copies"]
    assert {row["source_entry_id"] for row in selected if row["origin_slot"] == 1} == set(old_records)
    assert {path.stem for path in _children(root / "runs/02/copy-records")}.isdisjoint(old_records)
    before = _snapshot(root)
    observed = _verify(profile, operation, request_sha, 2)
    assert observed.kind == "raw_preservation_verified" and observed is not resumed
    with pytest.raises(ERROR):
        _verify(profile, operation, request_sha, 1)
    assert _snapshot(root) == before


def test_actual_child_process_exit_after_record_releases_locks_and_parent_resumes_retained_history(profile):
    tree = _tree(profile)
    raw = b"synthetic process-exit recovery source\x00"
    _write(tree / "unknown.raw", raw)
    operation = _uuid()
    repository = Path(__file__).resolve().parents[1]
    script = '''
import os
import sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
from reno import storage_lifecycle as lifecycle
from reno import personal_authority_preservation_target2 as target
from reno import personal_authority_raw_preservation2 as preservation
profile = lifecycle.StorageProfile(*(Path(path) for path in sys.argv[2:6]))
def checkpoint(phase):
    if phase == "copy_record_published":
        os.write(1, b"actual-record-published-exit-73\\n")
        os._exit(73)
target._checkpoint = checkpoint
with lifecycle.exclusive(profile):
    preservation.create_raw_preservation_locked(profile, operation_id=sys.argv[6])
raise AssertionError("synthetic process did not reach the intentional exit boundary")
'''
    completed = subprocess.run([sys.executable, "-I", "-B", "-c", script, str(repository),
        *(str(getattr(profile, field)) for field in ("database", "media", "reports", "checkpoints")), operation],
        cwd=repository, capture_output=True, timeout=90, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    assert completed.returncode == 73, (completed.returncode, completed.stdout, completed.stderr)
    assert completed.stdout == b"actual-record-published-exit-73\n" and completed.stderr == b""
    root = profile.database.parent / NS / operation
    selection = json.loads((_native(root / "selection.json")).read_bytes())
    old_records = {path.stem for path in _children(root / "runs/01/copy-records")}
    assert len(old_records) == 1 < len(selection["source_file_ids"])
    assert list(_children(root / "runs/01/failures")) == []
    assert not (_native(root / "runs/01/receipt.json")).exists()
    old = _preserved_files(root)
    # The actual child exited without Python unwinding. This new EX and S1 SH
    # acquisition must succeed using only the persisted original prefix.
    resumed = _resume(profile, operation, _sha((_native(root / "request.json")).read_bytes()))
    _assert_preserved(root, old)
    _, docs, _ = _assert_package(profile, resumed, {ROOT + "/authority.lock": b"\x00", ROOT + "/unknown.raw": raw})
    selected = docs["runs/02/source-verified.json"]["selected_copies"]
    assert {row["source_entry_id"] for row in selected if row["origin_slot"] == 1} == old_records


def test_complete_first_receipt_still_uses_second_slot_and_both_generations_verify_readonly(profile, monkeypatch):
    raw = b"one original source shared by two generations"
    _write(profile.database.parent / "node-identity.json", raw)
    def forbidden(*_args, **_options):
        pytest.fail("raw preservation invoked SQL or initialized domain authority")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(authority, "initialize", forbidden)
    first_result = _create(profile)
    root, _, _ = _assert_package(profile, first_result, {"node-identity.json": raw})
    old, first_receipt = _preserved_files(root), first_result.receipt_sha256
    captured, original = [], source._capture_for_preservation_locked
    @contextmanager
    def capture(token):
        with original(token) as held:
            captured.append(held)
            yield held
    monkeypatch.setattr(source, "_capture_for_preservation_locked", capture)
    resumed = _resume(profile, first_result.operation_id, first_result.request_sha256)
    assert len(captured) == 1 and resumed.run_id != first_result.run_id
    assert first_result.receipt_sha256 == first_receipt
    _assert_preserved(root, old)
    _, docs, _ = _assert_package(profile, resumed, {"node-identity.json": raw})
    assert list(_children(root / "runs/02/copy-records")) == []
    assert {row["origin_slot"] for row in docs["runs/02/source-verified.json"]["selected_copies"]} == {1}
    before = _snapshot(root)
    monkeypatch.setattr(source, "_capture_for_preservation_locked", forbidden)
    monkeypatch.setattr(target, "_write_bytes_original", forbidden)
    monkeypatch.setattr(target, "write_failure_once", forbidden)
    for generation, result in ((1, first_result), (2, resumed)):
        observed = _verify(profile, result.operation_id, result.request_sha256, generation)
        assert observed.kind == "raw_preservation_verified" and observed.generation == generation
        assert observed.receipt_sha256 == result.receipt_sha256 and observed is not result
    assert _snapshot(root) == before
    with pytest.raises((FrozenInstanceError, AttributeError)):
        resumed.generation = 1
    with pytest.raises(TypeError):
        resumed.counters["read_bytes"] = 0


def test_claim_is_created_fsynced_and_closed_before_the_first_request_content_read(profile, monkeypatch):
    created = _create(profile)
    root = created.package_path
    events, streams, reads = [], {}, []
    original_open, original_read = safe._Directory.open_file, target._read_bytes_original
    original_close, original_sync = target._close_stream_original, target._fsync_original
    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        path = directory.path / name
        if path.is_relative_to(root):
            streams[id(stream)] = (stream, path.relative_to(root).as_posix(), options.get("write", False))
        return stream
    def synced(stream):
        value = original_sync(stream)
        if streams.get(id(stream), (None, None, None))[1:] == (CLAIM, True):
            events.append("claim_fsynced")
        return value
    def closed(stream):
        value = original_close(stream)
        if streams.get(id(stream), (None, None, None))[1:] == (CLAIM, True):
            assert stream.closed
            events.append("claim_writer_closed")
        return value
    def read(stream, count):
        actual, key, _writer = streams[id(stream)]
        assert actual is stream and "claim_writer_closed" in events
        assert events.index("claim_fsynced") < events.index("claim_writer_closed")
        reads.append(key)
        if len(reads) == 1:
            assert key == "request.json"
        return original_read(stream, count)
    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(target, "_fsync_original", synced)
    monkeypatch.setattr(target, "_close_stream_original", closed)
    monkeypatch.setattr(target, "_read_bytes_original", read)
    resumed = _resume(profile, created.operation_id, created.request_sha256)
    assert resumed.generation == 2 and reads
    assert (_native(root / CLAIM)).read_bytes() == b""
    assert events.count("claim_fsynced") == events.count("claim_writer_closed") == 1


@pytest.mark.parametrize("mutation", ["missing", "wrong", "multiple", "nonempty", "wrong_expected"])
def test_bad_admission_anchor_rejects_before_any_content_read_or_claim(profile, monkeypatch, mutation):
    created = _create(profile)
    root, request_sha = created.package_path, created.request_sha256
    anchor = root / (ANCHOR + request_sha)
    other = "0" * 64 if request_sha != "0" * 64 else "1" * 64
    if mutation == "missing":
        _native(anchor).rmdir()
    elif mutation == "wrong":
        _native(anchor).rename(_native(root / (ANCHOR + other)))
    elif mutation == "multiple":
        (_native(root / (ANCHOR + other))).mkdir()
    elif mutation == "nonempty":
        _write(anchor / "unexpected.raw", b"not an empty admission anchor")
    else:
        request_sha = other
    before, reads = _snapshot(root), []
    def forbidden_read(*_args, **_options):
        reads.append(True)
        pytest.fail("invalid admission read content before a claim")
    monkeypatch.setattr(target, "_read_bytes_original", forbidden_read)
    monkeypatch.setattr(source, "_read_bytes_original", forbidden_read)
    with pytest.raises(ERROR):
        _resume(profile, created.operation_id, request_sha)
    assert reads == [] and not (_native(root / CLAIM)).exists()
    assert _snapshot(root) == before


@pytest.mark.parametrize("boundary", ["claim_created", "claim_close"])
def test_native_claim_permanently_consumes_slot_even_when_its_close_or_later_gate_fails(profile, monkeypatch, boundary):
    created = _create(profile)
    root = created.package_path
    original_open, original_close = safe._Directory.open_file, target._close_stream_original
    claims, failures, reads = [], [], []
    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        if directory.path / name == root / CLAIM and options.get("write"):
            claims.append(stream)
        return stream
    def close(stream):
        value = original_close(stream)
        if boundary == "claim_close" and stream in claims and not failures:
            assert stream.closed
            failures.append(boundary)
            raise OSError(errno.EIO, "synthetic failure after actual claim writer close")
        return value
    def checkpoint(phase):
        if boundary == "claim_created" and phase == boundary:
            assert (_native(root / CLAIM)).exists()
            failures.append(phase)
            raise OSError(errno.EIO, "synthetic gate failure after native claim creation")
    def forbidden_read(*_args, **_options):
        reads.append(True)
        pytest.fail("a failed claim close must not read request content")
    with monkeypatch.context() as patch:
        patch.setattr(safe._Directory, "open_file", opened)
        patch.setattr(target, "_close_stream_original", close)
        patch.setattr(admission, "_checkpoint", checkpoint)
        patch.setattr(target, "_read_bytes_original", forbidden_read)
        with pytest.raises(ERROR):
            _resume(profile, created.operation_id, created.request_sha256)
    assert failures == [boundary] and reads == []
    assert claims and all(stream.closed for stream in claims)
    assert (_native(root / CLAIM)).read_bytes() == b"" and not (_native(root / "runs/02")).exists()
    before = _snapshot(root)
    with pytest.raises(ERROR) as exhausted:
        _resume(profile, created.operation_id, created.request_sha256)
    assert exhausted.value.code == "AUTHORITY_RAW_PRESERVATION_ATTEMPTS_EXHAUSTED"
    observed = _verify(profile, created.operation_id, created.request_sha256, 1)
    assert observed.receipt_sha256 == created.receipt_sha256
    assert _snapshot(root) == before


def test_third_producer_attempt_never_reads_contents_or_creates_a_third_slot(profile, monkeypatch):
    created = _create(profile)
    _resume(profile, created.operation_id, created.request_sha256)
    root, reads = created.package_path, []
    before = _snapshot(root)
    def forbidden(*_args, **_options):
        reads.append(True)
        pytest.fail("exhausted producer read target contents")
    monkeypatch.setattr(target, "_read_bytes_original", forbidden)
    with pytest.raises(ERROR) as raised:
        _resume(profile, created.operation_id, created.request_sha256)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_ATTEMPTS_EXHAUSTED"
    with pytest.raises(ERROR):
        _create(profile, operation_id=created.operation_id)
    assert reads == [] and _snapshot(root) == before


@pytest.mark.parametrize("mutation", ["wrong_sha", "over_request_cap"])
def test_matching_anchor_does_not_make_bad_request_free_or_allow_later_fact_reads(profile, monkeypatch, mutation):
    created = _create(profile)
    root = created.package_path
    path = root / "request.json"
    if mutation == "wrong_sha":
        value = json.loads(_native(path).read_bytes())
        value["creation_run_id"] = _uuid()
        _native(path).write_bytes(_wire(value))
    else:
        _native(path).write_bytes(b"x" * (1024**2 + 1))
    before, reads, streams = _preserved_files(root), [], {}
    original_open, original_read = safe._Directory.open_file, target._read_bytes_original
    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        streams[id(stream)] = directory.path / name
        return stream
    def read(stream, count):
        reads.append(streams[id(stream)])
        return original_read(stream, count)
    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(target, "_read_bytes_original", read)
    with pytest.raises(ERROR):
        _resume(profile, created.operation_id, created.request_sha256)
    assert (_native(root / CLAIM)).read_bytes() == b""
    if mutation == "over_request_cap":
        assert reads == []
    else:
        assert reads and set(reads) == {path}
    assert not (_native(root / "runs/02")).exists()
    _assert_preserved(root, before)


def test_request_growing_after_early_metadata_check_is_capped_at_the_actual_held_reader_before_bytes(profile, monkeypatch):
    created = _create(profile)
    root = created.package_path
    request = root / "request.json"
    assert _native(request).stat().st_size < 1024**2
    original_open = target._Target.open_reader
    changes, reads = [], []
    def opened(state, parent, name, key, *, collect=False):
        if state.mode == "resume" and key == "request.json" and not changes:
            # _read_control_original has already accepted the small pathname
            # stat. The real reader now sees a stable, larger native file.
            _native(request).write_bytes(b"x" * (1024**2 + 1))
            changes.append(_native(request).stat().st_size)
        return original_open(state, parent, name, key, collect=collect)
    def forbidden_read(*_args, **_options):
        reads.append(True)
        pytest.fail("oversized held request was read after its early small stat")
    monkeypatch.setattr(target._Target, "open_reader", opened)
    monkeypatch.setattr(target, "_read_bytes_original", forbidden_read)
    with pytest.raises(ERROR):
        _resume(profile, created.operation_id, created.request_sha256)
    assert changes == [1024**2 + 1] and reads == []
    assert (_native(root / CLAIM)).read_bytes() == b"" and not (_native(root / "runs/02")).exists()
    before = _snapshot(root)
    with pytest.raises(ERROR) as exhausted:
        _resume(profile, created.operation_id, created.request_sha256)
    assert exhausted.value.code == "AUTHORITY_RAW_PRESERVATION_ATTEMPTS_EXHAUSTED"
    assert reads == [] and _snapshot(root) == before


@pytest.mark.parametrize("mutation", [
    "injected_listing_omission",
    pytest.param("native_rename", marks=pytest.mark.skipif(os.name == "nt",
        reason="Windows original held readers deny rename/delete; listing omission is tested explicitly separately")),
])
def test_missing_bootstrap_entry_is_rejected_before_prefix_completion_or_fresh_source(profile, monkeypatch, mutation):
    _write(profile.database.parent / "node-identity.json", b"source must never be reopened after missing bootstrap entry")
    created = _create(profile)
    root = created.package_path
    old = _preserved_files(root)
    retained = profile.database.parent / ("test-retained-selection-" + _uuid() + ".json")
    armed, omissions, captures, prefix_completed = [], [], [], []
    original_publish, original_listing = target.publish_control, target._Target.listing
    def published(token, key, kind, value):
        raw = original_publish(token, key, kind, value)
        if key == "runs/02/run-attempt.json":
            armed.append(key)
            if mutation == "native_rename":
                (_native(root / "selection.json")).rename(_native(retained))
        return raw
    def listing(state, directory):
        actual = original_listing(state, directory)
        if (mutation == "injected_listing_omission" and armed and state.mode == "resume"
                and state.phase == "prefix" and directory.key == ""):
            assert "selection.json" in actual
            omissions.append("selection.json")
            # Explicit metadata fault after a real listing, NOT an OS rename.
            return tuple(name for name in actual if name != "selection.json")
        return actual
    def capture(*_args, **_options):
        captures.append(True)
        pytest.fail("missing bootstrap entry reached fresh S1")
    def checkpoint(phase):
        if phase == "prefix_loaded":
            prefix_completed.append(phase)
    monkeypatch.setattr(target, "publish_control", published)
    monkeypatch.setattr(target._Target, "listing", listing)
    monkeypatch.setattr(source, "_capture_for_preservation_locked", capture)
    monkeypatch.setattr(target, "_checkpoint", checkpoint)
    with pytest.raises(ERROR):
        _resume(profile, created.operation_id, created.request_sha256)
    assert armed == ["runs/02/run-attempt.json"]
    assert captures == prefix_completed == [] and (_native(root / CLAIM)).exists()
    assert list(_children(root / "runs/02/copy-records")) == []
    assert not (_native(root / "runs/02/receipt.json")).exists()
    if mutation == "native_rename":
        identity, raw = old.pop("selection.json")
        assert _native(retained).read_bytes() == raw and _identity(retained) == identity
        assert not (_native(root / "selection.json")).exists() and omissions == []
    else:
        assert omissions == ["selection.json"] and not _native(retained).exists()
    _assert_preserved(root, old)
    before = _snapshot(root)
    with pytest.raises(ERROR) as exhausted:
        _resume(profile, created.operation_id, created.request_sha256)
    assert exhausted.value.code == "AUTHORITY_RAW_PRESERVATION_ATTEMPTS_EXHAUSTED"
    assert _snapshot(root) == before and captures == []


def test_invalid_formal_record_rejects_instead_of_downgrading_it_to_retained_payload(profile, monkeypatch):
    _write(profile.database.parent / "node-identity.json", b"actual single source bytes")
    operation, root, request_sha, _ = _partial(profile, monkeypatch)
    record = next(_children(root / "runs/01/copy-records"))
    changed = json.loads(_native(record).read_bytes())
    changed["copy"]["sha256"] = "0" * 64
    _native(record).write_bytes(_wire(changed))
    before, captures = _preserved_files(root), []
    def forbidden(*_args, **_options):
        captures.append(True)
        pytest.fail("invalid formal history reached a fresh source acquisition")
    monkeypatch.setattr(source, "_capture_for_preservation_locked", forbidden)
    with pytest.raises(ERROR):
        _resume(profile, operation, request_sha)
    assert captures == [] and (_native(root / CLAIM)).exists()
    assert not (_native(root / "runs/02/receipt.json")).exists()
    _assert_preserved(root, before)


def test_full_unrecorded_old_payload_is_retained_and_copied_afresh_not_adopted(profile, monkeypatch):
    raw = b"complete raw bytes with no formal original record"
    _write(profile.database.parent / "node-identity.json", raw)
    operation, root, request_sha, _ = _partial(profile, monkeypatch, complete_record=False)
    old_payload = next(_walk(root / "runs/01/payload", "*.raw"))
    assert _native(old_payload).read_bytes() == raw
    old = _preserved_files(root)
    resumed = _resume(profile, operation, request_sha)
    _assert_preserved(root, old)
    _, docs, _ = _assert_package(profile, resumed, {"node-identity.json": raw})
    selected = docs["runs/02/source-verified.json"]["selected_copies"]
    assert len(selected) == 1 and selected[0]["origin_slot"] == 2
    new_payload = root / docs["runs/02/capsule.json"]["copies"][0]["target_relative_key"]
    assert _identity(new_payload) != _identity(old_payload) and _native(new_payload).read_bytes() == raw
    assert old_payload.relative_to(root).as_posix() in {row["relative_key"] for row in docs["runs/02/manifest.json"]["retained"]}


def test_changed_source_is_rejected_despite_an_earlier_complete_receipt(profile, monkeypatch):
    path = _write(profile.database.parent / "node-identity.json", b"original same-size source")
    created = _create(profile)
    old = _preserved_files(created.package_path)
    _native(path).write_bytes(b"modified same-size source")
    captures, original = [], source._capture_for_preservation_locked
    @contextmanager
    def capture(token):
        with original(token) as held:
            captures.append(held)
            yield held
    monkeypatch.setattr(source, "_capture_for_preservation_locked", capture)
    with pytest.raises(ERROR):
        _resume(profile, created.operation_id, created.request_sha256)
    assert len(captures) == 1 and (_native(created.package_path / CLAIM)).exists()
    assert not (_native(created.package_path / "runs/02/receipt.json")).exists()
    _assert_preserved(created.package_path, old)


def test_small_failure_record_accepts_legitimate_source_metadata_larger_than_failure_cap(profile, monkeypatch):
    tree = _tree(profile)
    for index in range(16):
        _write(tree / f"synthetic-{index:02d}.raw", f"source-{index:02d}".encode("ascii"))
    original_source = _preserved_files(tree)
    operation = _uuid()
    root = profile.database.parent / NS / operation
    streams, stopped = [], []
    original_open = safe._Directory.open_file
    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        if (directory.path / name).is_relative_to(root) or (directory.path / name).is_relative_to(tree):
            streams.append(stream)
        return stream
    def stop(_token, _capture, _entry_id):
        metadata = (_native(root / "source-metadata.json")).read_bytes()
        assert len(metadata) > 16384
        assert (_native(root / "selection.json")).is_file()
        stopped.append(metadata)
        raise OSError(errno.EIO, "synthetic first-copy failure after actual large metadata publication")
    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(target, "copy_entry", stop)
    with pytest.raises(ERROR) as raised:
        _create(profile, operation_id=operation)
    assert len(stopped) == 1 and raised.value.code == "AUTHORITY_RAW_PRESERVATION_PUBLICATION_FAILED"
    failures = list(_children(root / "runs/01/failures"))
    assert len(failures) == 1
    raw = _native(failures[0]).read_bytes()
    document = json.loads(raw)
    assert len(raw) <= 16384 and _wire(document) == raw
    assert document["slot"] == 1 and document["operation_id"] == operation
    assert document["request_sha256"] == _sha((_native(root / "request.json")).read_bytes())
    assert document["selection_sha256"] == _sha((_native(root / "selection.json")).read_bytes())
    assert document["code"] == raised.value.code and document["phase"] == "copy"
    assert (_native(root / "source-metadata.json")).read_bytes() == stopped[0]
    assert streams and all(stream.closed for stream in streams)
    assert list(_children(root / "runs/01/copy-records")) == []
    assert not (_native(root / "runs/01/receipt.json")).exists()
    _assert_preserved(tree, original_source)


@pytest.mark.parametrize("generation", [1, 2])
def test_receipt_followed_by_real_publisher_close_failure_never_returns_C_and_verify_does_not_rewrite_it(profile, monkeypatch, generation):
    initial = _create(profile) if generation == 2 else None
    operation = initial.operation_id if initial else _uuid()
    root = profile.database.parent / NS / operation
    original, failures = target._close_scope_original, []
    def close(state, primary):
        value = original(state, primary)
        if state.mode == "publisher" and primary is None and not failures:
            failures.append(state)
            raise OSError(errno.EIO, "synthetic failure reported after actual publisher closure")
        return value
    with monkeypatch.context() as patch:
        patch.setattr(target, "_close_scope_original", close)
        with pytest.raises(ERROR) as failed:
            if initial:
                _resume(profile, operation, initial.request_sha256)
            else:
                _create(profile, operation_id=operation)
    assert failed.value.code == "AUTHORITY_RAW_PRESERVATION_CLOSE_FAILED" and len(failures) == 1
    assert (_native(root / f"runs/{generation:02d}/receipt.json")).is_file()
    assert list(_children(root / f"runs/{generation:02d}/failures")) == []
    before = _snapshot(root)
    observed = _verify(profile, operation, _sha((_native(root / "request.json")).read_bytes()), generation)
    assert observed.kind == "raw_preservation_verified"
    assert failed.value.code == "AUTHORITY_RAW_PRESERVATION_CLOSE_FAILED"
    assert _snapshot(root) == before
    _assert_package(profile, observed, {})


@pytest.mark.parametrize("outer_family,nested", [(1, "create"), (1, "resume"), (1, "verify"),
                                                (2, "create"), (2, "verify")])
def test_cross_family_callback_reentry_is_sticky_before_any_nested_guard_IO(profile, monkeypatch, outer_family, nested):
    outer, inner = _uuid(), _uuid()
    caught, guard_calls = [], []
    in_callback = False
    original_guard = lifecycle.require_guard
    def guard(*args, **options):
        if in_callback:
            guard_calls.append(True)
        return original_guard(*args, **options)
    def callback():
        nonlocal in_callback
        if caught:
            return
        module = preservation if outer_family == 1 else first
        in_callback = True
        try:
            with pytest.raises(ERROR) as raised:
                if nested == "create":
                    module.create_raw_preservation_locked(profile, operation_id=inner)
                elif nested == "resume":
                    module.resume_raw_preservation_locked(profile, operation_id=inner, expected_request_sha256="0" * 64)
                else:
                    options = {"generation": 1} if outer_family == 1 else {}
                    module.verify_raw_preservation_locked(profile, operation_id=inner,
                        expected_request_sha256="0" * 64, **options)
            caught.append(raised.value)
        finally:
            in_callback = False
    monkeypatch.setattr(lifecycle, "require_guard", guard)
    module = first if outer_family == 1 else preservation
    with lifecycle.exclusive(profile):
        with pytest.raises(ERROR) as terminal:
            module.create_raw_preservation_locked(profile, operation_id=outer, check_cancelled=callback)
    assert caught and terminal.value is caught[0]
    assert terminal.value.code == "AUTHORITY_RAW_PRESERVATION_REENTRANT" and guard_calls == []
    assert not (_native(profile.database.parent / NS / inner)).exists()
    assert not (_native(profile.database.parent / ".reno-authority-raw-preservation-v1" / inner)).exists()


@pytest.mark.parametrize("kind", ["unknown", "multiple", "wrong_original"])
def test_operation_routing_rejects_unknown_or_ambiguous_token_before_any_owner_validator(monkeypatch, kind):
    token, calls = object(), []
    def forbidden(*_args, **_options):
        calls.append(True)
        pytest.fail("invalid registry membership reached an owner or guard validator")
    for module in (first, preservation):
        monkeypatch.setattr(module, "_operation_record", forbidden)
        monkeypatch.setattr(module, "_failure_operation_record", forbidden)
    if kind == "multiple":
        monkeypatch.setitem(first._LIVE, token, SimpleNamespace(token=token))
        monkeypatch.setitem(preservation._LIVE, token, SimpleNamespace(token=token))
    elif kind == "wrong_original":
        monkeypatch.setitem(preservation._LIVE, token, SimpleNamespace(token=object()))
    for failure in (False, True):
        with pytest.raises(ERROR) as raised:
            routing.operation_record(token, failure=failure)
        assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_OWNER_MISMATCH"
    assert calls == []


@pytest.mark.parametrize("kind", ["unknown", "multiple", "wrong_type", "wrong_original"])
def test_target_routing_rejects_unknown_or_ambiguous_original_before_source_write(monkeypatch, kind):
    token = target._TargetToken(target._TOKEN) if kind != "wrong_type" else object()
    if kind == "multiple":
        monkeypatch.setitem(target1._LIVE, id(token), SimpleNamespace(token=token))
        monkeypatch.setitem(target._LIVE, id(token), SimpleNamespace(token=token))
    elif kind in {"wrong_type", "wrong_original"}:
        monkeypatch.setitem(target._LIVE, id(token), SimpleNamespace(token=token if kind == "wrong_type" else object()))
    with pytest.raises(ERROR) as raised:
        routing.target_module(token)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_OWNER_MISMATCH"


@pytest.mark.parametrize("action", ["create", "resume", "verify"])
def test_public_v2_factory_requires_original_profile_EX_before_any_target_effect(profile, action):
    before = {path.name for path in _children(profile.database.parent)}
    with pytest.raises(ERROR):
        options = {"operation_id": _uuid()}
        if action != "create":
            options["expected_request_sha256"] = "0" * 64
        if action == "verify":
            options["generation"] = 1
        getattr(preservation, action + "_raw_preservation_locked")(profile, **options)
    assert {path.name for path in _children(profile.database.parent)} == before
    assert not (_native(profile.database.parent / NS)).exists()
