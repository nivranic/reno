"""Actual isolated create/verify factories with independent disk observations.

All raw bytes are synthetic.  Faults are injected explicitly after calling the
real I/O/close operation; ordinary profile locks, source descriptors, copies and
disk inventories remain real.  No test confers domain authority or uses SQL.
"""
from dataclasses import FrozenInstanceError
import asyncio
import errno
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
import weakref

import pytest

from reno import personal_authority_files as authority
from reno import personal_authority_preservation_source as source
from reno import personal_authority_preservation_target as target
from reno import personal_authority_raw_preservation as preservation
from reno import personal_authority_raw_preservation_contract as contract
from reno import personal_preservation_files as private_files
from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_personal_authority_preservation_source import _tree, _write


ROOT = ".reno-personal-authority-namespace-v2"
NS = ".reno-authority-raw-preservation-v1"
NODE = "22222222-2222-4222-8222-222222222222"
ERROR = preservation.AuthorityRawPreservationError
LATE = {"source-verified.json", "manifest.json", "receipt.json"}


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
    info = path.stat(follow_symlinks=False)
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def _position(identity):
    return identity["device"], identity["inode"]


def _snapshot(root):
    """Only the current test's exact operation directory is traversed."""
    if not root.exists():
        return None
    result = {}
    for path in [root, *root.rglob("*")]:
        info = path.stat(follow_symlinks=False)
        result[path.relative_to(root).as_posix()] = (
            info.st_dev, info.st_ino, info.st_mode, info.st_nlink,
            info.st_mtime_ns, info.st_ctime_ns,
            getattr(info, "st_file_attributes", None),
            None if path.is_dir() else path.read_bytes())
    return result


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


def _verify(profile, result, **options):
    with lifecycle.exclusive(profile):
        return preservation.verify_raw_preservation_locked(profile,
            operation_id=result.operation_id, expected_request_sha256=result.request_sha256, **options)


def _assert_disk_package(profile, result, expected):
    """Recompute the entire persisted graph independently of product builders."""
    root = profile.database.parent / NS / result.operation_id
    assert _key(result.package_path) == _key(root)
    all_files = {path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*") if path.is_file()}
    payloads = {path: raw for path, raw in all_files.items() if path.startswith("payload/")}
    facts = {path: raw for path, raw in all_files.items() if not path.startswith("payload/")}
    documents = {path: json.loads(raw) for path, raw in facts.items()}
    assert all(_wire(documents[path]) == raw for path, raw in facts.items())
    metadata, capsule = documents["source-metadata.json"], documents["capsule.json"]
    request, selection = documents["request.json"], documents["selection.json"]
    verified, manifest, receipt = (documents[path] for path in
        ("source-verified.json", "manifest.json", "receipt.json"))
    assert result.run_id == request["run_id"]
    assert result.request_sha256 == _sha(facts["request.json"])
    for field, path in (("manifest_sha256", "manifest.json"), ("receipt_sha256", "receipt.json"),
                        ("capsule_sha256", "capsule.json"), ("source_projection_sha256", "source-projection.json")):
        assert getattr(result, field) == _sha(facts[path])
    assert request["operation_id"] == capsule["operation_id"] == result.operation_id
    assert request["target"]["namespace_identity"] == _identity(root.parent)
    assert request["target"]["operation_identity"] == _identity(root)
    assert request["target"]["operation_relative_key"] == result.operation_id
    assert selection["request_sha256"] == result.request_sha256
    assert selection["source_metadata_sha256"] == _sha(facts["source-metadata.json"])
    assert selection["source_projection_sha256"] == _sha(facts["source-projection.json"])
    assert metadata["source_projection"] == documents["source-projection.json"]
    assert metadata["capsule"]["copies"] == []
    assert metadata["capsule"]["coverage"]["declared_copies"] == "none"
    assert capsule["files"] == metadata["capsule"]["files"]
    for field in capsule.keys() - {"copies", "coverage"}:
        assert capsule[field] == metadata["capsule"][field]
    assert capsule["artifacts"] == capsule["links"] == []
    sources = {row["relative_key"]: row for row in capsule["files"]}
    assert set(sources) == set(expected)
    assert selection["source_file_ids"] == [row["entry_id"] for row in capsule["files"]]
    copies = {row["source_entry_id"]: row for row in capsule["copies"]}
    assert set(copies) == {row["entry_id"] for row in sources.values()}
    assert len(payloads) == len(copies)
    expected_facts = {"request.json", "attempts/" + result.run_id + ".json", "source-metadata.json",
        "source-projection.json", "selection.json", "capsule.json", *LATE}
    forbidden = {_position(row["source_identity"]) for row in capsule["directories"]}
    forbidden.update(_position(row["source_path_identity"]) for row in sources.values())
    forbidden.update(_position(row["identity"]) for row in request["profile"]["roots"].values())
    forbidden.add(_position(_identity(root.parent)))
    seen = set()
    for relative, raw in expected.items():
        entry = sources[relative]
        assert entry["sha256"] == _sha(raw) and entry["byte_size"] == len(raw)
        actual = copies[entry["entry_id"]]
        path = actual["target_relative_key"]
        assert payloads[path] == raw and actual["sha256"] == _sha(raw) and actual["byte_size"] == len(raw)
        assert _position(actual["copy_path_identity"]) == _position(_identity(root / path))
        assert _position(actual["copy_handle_identity"]) == _position(_identity(root / path))
        assert actual["target_parent_identity"] == _identity((root / path).parent)
        position = _position(_identity(root / path))
        assert position not in forbidden | seen
        seen.add(position)
        copy_id = path.split("/")[1]
        attempt_path = "copy-attempts/" + copy_id + ".json"
        record_path = "copy-records/" + entry["entry_id"] + ".json"
        expected_facts.update({attempt_path, record_path})
        assert documents[record_path]["copy"] == actual
        assert documents[record_path]["copy_attempt_sha256"] == _sha(facts[attempt_path])
        assert documents[attempt_path]["target_relative_key"] == path
        assert documents[attempt_path]["source_entry_id"] == entry["entry_id"]
    assert set(facts) == expected_facts
    assert manifest["facts"] == [{"relative_key": path, "sha256": _sha(raw), "byte_size": len(raw)}
        for path, raw in sorted(facts.items()) if path not in {"manifest.json", "receipt.json"}]
    assert receipt["manifest_sha256"] == _sha(facts["manifest.json"])
    assert receipt["source_verified_sha256"] == manifest["source_verified_sha256"] == _sha(facts["source-verified.json"])
    assert receipt["publication"] == "published_observed"
    assert set(receipt).isdisjoint({"factory_returned", "publisher_closed", "ready", "owner", "grant", "fence"})
    assert result.outcome == receipt["outcome"] == manifest["outcome"] == ("copied" if expected else "empty_source")
    assert capsule["coverage"]["declared_copies"] == ("complete" if expected else "none")
    a, b = verified["source_closed"], verified["target_closed"]
    assert a["source_file_count"] == len(expected) and a["source_byte_size"] == sum(map(len, expected.values()))
    assert a["source_metadata_sha256"] == _sha(facts["source-metadata.json"])
    assert a["source_projection_sha256"] == _sha(facts["source-projection.json"])
    assert {row["relative_key"] for row in b["files"]} == set(all_files) - LATE
    directories = {"", *(path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir())}
    assert {row["relative_key"] for row in b["directories"]} == directories
    b_paths = directories | (set(all_files) - LATE)
    for row in b["directories"]:
        path = row["relative_key"]
        assert row["identity"] == _identity(root / path)
        assert row["children"] == sorted([key.rpartition("/")[2] for key in b_paths
            if key and key.rpartition("/")[0] == path], key=lambda value: value.encode("utf-8"))
    for row in b["files"]:
        path = row["relative_key"]
        assert row["sha256"] == _sha(all_files[path]) and row["byte_size"] == len(all_files[path])
        assert _position(row["path_identity"]) == _position(_identity(root / path))
        assert _position(row["handle_identity"]) == _position(_identity(root / path))
        assert row["parent_identity"] == _identity((root / path).parent)
    assert result.counters["uncertain_read_calls"] == result.counters["uncertain_write_bytes"] == 0
    assert contract.cross_validate_package(fact_bytes=facts) is None
    return root, documents, all_files


def test_empty_factory_and_readonly_verifier_complete_without_sql_or_authority(profile, monkeypatch):
    def forbidden(*_args, **_options):
        pytest.fail("Raw package factory invoked SQL or initialized domain authority")
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    monkeypatch.setattr(authority, "initialize", forbidden)
    created = _create(profile)
    assert created.kind == "raw_preservation_created"
    root, documents, _files = _assert_disk_package(profile, created, {})
    assert documents["source-metadata.json"]["node_observation"] == {"state": "missing", "entry_id": None}
    assert not profile.database.exists() and not (profile.database.parent / ROOT).exists()
    before = _snapshot(root)
    verified = _verify(profile, created)
    assert verified.kind == "raw_preservation_verified" and verified.operation_id == created.operation_id
    _assert_disk_package(profile, verified, {})
    assert _snapshot(root) == before
    with pytest.raises((FrozenInstanceError, AttributeError)):
        created.outcome = "domain_ready"
    with pytest.raises(TypeError):
        created.counters["read_bytes"] = 0


def test_completion_registry_does_not_retain_finished_async_task_or_factory_result(profile):
    async def factory():
        return _create(profile)

    async def observe():
        task = asyncio.create_task(factory())
        result = await task
        assert result.kind == "raw_preservation_created"
        return weakref.ref(task), weakref.ref(result)

    task_ref, result_ref = asyncio.run(observe())
    gc.collect()
    assert task_ref() is None
    assert result_ref() is None


def test_source_closed_history_does_not_retain_async_task_returning_capture(profile):
    async def factory():
        with lifecycle.exclusive(profile):
            with source.capture_raw_locked(profile, operation_id=_uuid()) as captured:
                assert source.source_metadata(captured)["capsule"]["files"] == []
            assert source.closed_observation(captured)["source_file_count"] == 0
        return captured

    async def observe():
        task = asyncio.create_task(factory())
        captured = await task
        return weakref.ref(task), weakref.ref(captured)

    task_ref, capture_ref = asyncio.run(observe())
    gc.collect()
    assert task_ref() is None
    assert capture_ref() is None


@pytest.mark.parametrize("boundary", ["source", "target"])
def test_unconsumed_closed_history_does_not_retain_async_failed_factory_traceback(profile, monkeypatch, boundary):
    operation = _uuid()
    original_source = source._consume_closed_for_preservation
    original_target = target.consume_closed
    closed_refs, faults = [], []
    def consume_source(captured, operation_token):
        assert captured in source._PRESERVATION_CLOSED
        closed_refs.append(weakref.ref(captured))
        if boundary == "source":
            faults.append("source")
            raise OSError(errno.EIO, "injected after real source close before handoff consumption")
        return original_source(captured, operation_token)
    def consume_target(held, operation_token):
        assert held in target._CLOSED
        closed_refs.append(weakref.ref(held))
        if boundary == "target":
            faults.append("target")
            raise OSError(errno.EIO, "injected after real target close before handoff consumption")
        return original_target(held, operation_token)
    monkeypatch.setattr(source, "_consume_closed_for_preservation", consume_source)
    monkeypatch.setattr(target, "consume_closed", consume_target)

    async def factory():
        return _create(profile, operation_id=operation)

    async def observe():
        task = asyncio.create_task(factory())
        with pytest.raises(ERROR):
            await task
        return weakref.ref(task), tuple(closed_refs)

    task_ref, token_refs = asyncio.run(observe())
    assert faults == [boundary]
    assert len(token_refs) == (1 if boundary == "source" else 2)
    assert not (profile.database.parent / NS / operation / "receipt.json").exists()
    gc.collect()
    assert task_ref() is None
    assert all(reference() is None for reference in token_refs)


def test_complete_factory_preserves_node_unknown_journals_zero_bytes_and_empty_directories(profile):
    tree = _tree(profile)
    raw_node = (' { "instance_id" : "' + NODE + '" } \n').encode()
    expected = {ROOT + "/authority.lock": b"\x00", "node-identity.json": raw_node,
        ROOT + "/facts/fence_1.json": b"opaque fence raw", ROOT + "/unknown.bin": b"\x00\xffopaque",
        ROOT + "/zero.raw": b"", ROOT + "/.stage-pending": b"unpublished original",
        ROOT + "/journal-v1/records/envelope.json": b"original journal record",
        ROOT + "/journal-v3/payloads/body.raw": b"original journal payload",
        ROOT + "/journal-enrollment-v1/operations/node": b"original operation node",
        ROOT + "-future-original.bin": b"unknown top-level original"}
    for relative, raw in expected.items():
        if relative != ROOT + "/authority.lock":
            _write(profile.database.parent / relative, raw)
    (tree / "empty").mkdir()
    created = _create(profile)
    root, documents, _files = _assert_disk_package(profile, created, expected)
    metadata = documents["source-metadata.json"]
    assert metadata["node_observation"]["state"] == "parsed"
    assert metadata["capsule"]["source_context"]["node"]["node_id"] == NODE
    assert any(row["relative_key"] == ROOT + "/empty" and row["children"] == []
        for row in metadata["capsule"]["directories"])
    assert all((profile.database.parent / relative).read_bytes() == raw for relative, raw in expected.items())
    assert all(not row["relative_key"].startswith(NS) for row in metadata["capsule"]["files"])
    before = _snapshot(root)
    _assert_disk_package(profile, _verify(profile, created), expected)
    assert _snapshot(root) == before


@pytest.mark.parametrize("name,raw,state", [
    ("node-identity.json", b"bad original node\x00\xff", "invalid"),
    ("node-identity.json", b'{"instance_id":"00000000-0000-0000-0000-000000000000"}', "invalid"),
    ("node-identity.json", b"x" * 8193, "json_limit"),
    ("NODE-IDENTITY.JSON", ('{"instance_id":"' + NODE + '"}').encode(), "noncanonical_name")])
def test_bad_or_noncanonical_node_remains_the_entire_original_raw_payload(profile, name, raw, state):
    _write(profile.database.parent / name, raw)
    created = _create(profile)
    _root, documents, _files = _assert_disk_package(profile, created, {name: raw})
    metadata = documents["source-metadata.json"]
    assert metadata["node_observation"]["state"] == state
    assert metadata["capsule"]["source_context"]["node"] is None
    assert (profile.database.parent / name).read_bytes() == raw


def test_copy_uses_the_original_source_descriptor_and_closes_each_owned_phase(profile, monkeypatch):
    tree = _tree(profile)
    raw = b"same actual source descriptor\x00" * 6000
    path = _write(tree / "large.raw", raw)
    operation = _uuid()
    package_path = profile.database.parent / NS / operation
    source_opens, source_reads, target_reads, phases = [], [], [], []
    original_open = safe._Directory.open_file
    original_source_read, original_target_read = source._read_bytes_original, target._read_bytes_original
    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        if _key(directory.path / name) == _key(path):
            source_opens.append((stream, stream.fileno()))
            assert len(source_opens) == 1, "Copy phase reopened the original source pathname"
        return stream
    def source_read(stream, count):
        result = original_source_read(stream, count)
        source_reads.append(stream)
        return result
    def target_read(stream, count):
        result = original_target_read(stream, count)
        target_reads.append(stream)
        return result
    def checkpoint(phase):
        phases.append(phase)
        if phase == "source_closed":
            assert source_reads and all(stream.closed for stream in source_reads)
            assert not (package_path / "source-verified.json").exists()
        elif phase == "target_closed":
            assert target_reads and all(stream.closed for stream in target_reads)
            assert not (package_path / "source-verified.json").exists()
        elif phase == "receipt_published":
            assert (package_path / "receipt.json").is_file()
        elif phase == "publisher_closed":
            assert target_reads and all(stream.closed for stream in target_reads)
    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(source, "_read_bytes_original", source_read)
    monkeypatch.setattr(target, "_read_bytes_original", target_read)
    monkeypatch.setattr(preservation, "_checkpoint", checkpoint)
    created = _create(profile, operation_id=operation)
    assert phases == ["source_closed", "target_closed", "receipt_published", "publisher_closed"]
    assert len(source_opens) == 1 and source_opens[0][0].closed
    with pytest.raises(OSError) as closed:
        os.fstat(source_opens[0][1])
    assert closed.value.errno == errno.EBADF
    _assert_disk_package(profile, created, {ROOT + "/authority.lock": b"\x00", ROOT + "/large.raw": raw})


@pytest.mark.parametrize("behavior", ["partial", "zero", "exception_after_partial"])
def test_actual_payload_write_outcomes_control_records_and_retained_partial_files(profile, monkeypatch, behavior):
    raw = b"independent actual payload write bytes"
    source_path = _write(profile.database.parent / "node-identity.json", raw)
    operation = _uuid()
    root = profile.database.parent / NS / operation
    original_open, original_write = safe._Directory.open_file, target._write_bytes_original
    payload_streams, injected = [], []
    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        path = directory.path / name
        if options.get("write") and options.get("create") and path.is_relative_to(root / "payload"):
            payload_streams.append((stream, path))
        return stream
    def write(stream, data):
        if not injected and any(stream is actual for actual, _path in payload_streams):
            assert len(data) == len(raw)
            if behavior == "zero":
                count = original_write(stream, b"")
                assert count == 0
            else:
                count = original_write(stream, data[:3])
                assert count == 3
            injected.append(count)
            if behavior == "exception_after_partial":
                raise OSError(errno.EIO, "injected exception after three actual bytes")
            return count
        return original_write(stream, data)
    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(target, "_write_bytes_original", write)
    if behavior == "partial":
        created = _create(profile, operation_id=operation)
        assert injected == [3]
        _assert_disk_package(profile, created, {"node-identity.json": raw})
    else:
        with pytest.raises(ERROR) as raised:
            _create(profile, operation_id=operation)
        expected_code = "PUBLICATION_FAILED" if behavior == "zero" else "WRITE_UNCERTAIN"
        assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_" + expected_code
        assert injected == ([0] if behavior == "zero" else [3])
        assert len(payload_streams) == 1
        assert payload_streams[0][1].read_bytes() == (b"" if behavior == "zero" else raw[:3])
        assert (root / "selection.json").is_file()
        assert len(list((root / "copy-attempts").iterdir())) == 1
        assert list((root / "copy-records").iterdir()) == []
        assert not (root / "source-verified.json").exists() and not (root / "receipt.json").exists()
        before = _snapshot(root)
        with pytest.raises(ERROR) as repeated:
            _create(profile, operation_id=operation)
        assert repeated.value.code == "AUTHORITY_RAW_PRESERVATION_OPERATION_EXISTS"
        assert _snapshot(root) == before
    assert payload_streams and all(stream.closed for stream, _path in payload_streams)
    assert source_path.read_bytes() == raw


def test_second_operation_does_not_collect_first_target_as_new_source(profile):
    raw = b"original raw node semantics unknown"
    _write(profile.database.parent / "node-identity.json", raw)
    first = _create(profile)
    first_root = Path(first.package_path)
    before = _snapshot(first_root)
    second = _create(profile)
    assert first.operation_id != second.operation_id
    _assert_disk_package(profile, second, {"node-identity.json": raw})
    assert _snapshot(first_root) == before


def test_repeated_completed_operation_is_rejected_without_new_attempt_or_writes(profile):
    result = _create(profile)
    root = Path(result.package_path)
    before = _snapshot(root)
    with pytest.raises(ERROR) as raised:
        _create(profile, operation_id=result.operation_id)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_OPERATION_EXISTS"
    assert _snapshot(root) == before


@pytest.mark.parametrize("prefix", [False, True])
def test_existing_empty_or_partial_operation_is_never_adopted_or_repaired(profile, prefix):
    operation = _uuid()
    data = profile.database.parent
    with safe._Directory(data, base=data) as directory:
        private_files.mkdir_child(directory, NS)
    with safe._Directory(data / NS, base=data) as namespace:
        private_files.mkdir_child(namespace, operation)
    root = data / NS / operation
    if prefix:
        _write(root / "payload/unrecorded.raw", b"retain unrecorded old attempt")
        _write(root / "request.json", b"unfinished prior request")
    before = _snapshot(root)
    with pytest.raises(ERROR) as raised:
        _create(profile, operation_id=operation)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_OPERATION_EXISTS"
    assert _snapshot(root) == before


@pytest.mark.parametrize("entry", ["node-identity.json", ".reno-personal-authority-future"])
def test_unrepresentable_source_is_not_silently_omitted_or_published_as_empty(profile, entry):
    directory = profile.database.parent / entry
    directory.mkdir()
    raw = _write(directory / "original.raw", b"unrepresentable but retained")
    operation = _uuid()
    with pytest.raises(ERROR):
        _create(profile, operation_id=operation)
    root = profile.database.parent / NS / operation
    assert not (root / "source-verified.json").exists() and not (root / "receipt.json").exists()
    assert raw.read_bytes() == b"unrepresentable but retained"


def test_source_close_failure_retains_prefix_and_cannot_publish_A_or_receipt(profile, monkeypatch):
    _write(profile.database.parent / "node-identity.json", b"source close fault raw")
    operation = _uuid()
    original = source._close_stream_original
    closed = []
    def close(stream):
        result = original(stream)
        if not closed:
            closed.append(stream)
            raise OSError(errno.EIO, "injected after real source close")
        return result
    monkeypatch.setattr(source, "_close_stream_original", close)
    with pytest.raises(ERROR):
        _create(profile, operation_id=operation)
    root = profile.database.parent / NS / operation
    assert closed and closed[0].closed
    assert (root / "selection.json").is_file()
    assert not (root / "source-verified.json").exists() and not (root / "receipt.json").exists()
    assert (profile.database.parent / "node-identity.json").read_bytes() == b"source close fault raw"


@pytest.mark.parametrize("mode", ["target", "publisher"])
def test_real_scope_close_followed_by_reported_failure_cannot_return_C(profile, monkeypatch, mode):
    operation = _uuid()
    root = profile.database.parent / NS / operation
    original, failures = target._close_scope_original, []
    def close(state, primary):
        result = original(state, primary)
        if state.mode == mode and primary is None and not failures:
            failures.append(state)
            raise OSError(errno.EIO, "injected after real owned scope close")
        return result
    with monkeypatch.context() as patch:
        patch.setattr(target, "_close_scope_original", close)
        with pytest.raises(ERROR) as raised:
            _create(profile, operation_id=operation)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_CLOSE_FAILED" and failures
    assert (root / "receipt.json").exists() is (mode == "publisher")
    if mode == "publisher":
        assert list((root / "failures").iterdir()) == []
        before = _snapshot(root)
        request_sha = _sha((root / "request.json").read_bytes())
        with lifecycle.exclusive(profile):
            observed = preservation.verify_raw_preservation_locked(profile,
                operation_id=operation, expected_request_sha256=request_sha)
        assert observed.kind == "raw_preservation_verified"
        _assert_disk_package(profile, observed, {})
        assert _snapshot(root) == before
    else:
        assert not (root / "source-verified.json").exists()


def test_late_resource_identity_gate_failure_still_closes_every_original_resource(profile, monkeypatch):
    operation = _uuid()
    root = profile.database.parent / NS / operation
    original_close, original_verify = target._close_scope_original, target._Target.verify_file
    resources, gates = [], []
    def close(state, primary):
        if state.mode == "target" and primary is None and not resources:
            resources.extend((kind, value, tuple(value.handles) if kind == "directory" else ())
                for item in state.resources if item is not None for kind, value in [item])
        return original_close(state, primary)
    def verify(state, record):
        result = original_verify(state, record)
        if state.mode == "target" and state.phase == "closing":
            gates.append(record.stream)
            if len(gates) == 2:
                raise target.AuthorityRawTargetError("TARGET_CHANGED")
        return result
    monkeypatch.setattr(target, "_close_scope_original", close)
    monkeypatch.setattr(target._Target, "verify_file", verify)
    with pytest.raises(ERROR) as raised:
        _create(profile, operation_id=operation)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_TARGET_CHANGED"
    assert len(gates) == 2 and resources
    assert any(kind == "stream" for kind, _value, _handles in resources)
    assert any(kind == "directory" for kind, _value, _handles in resources)
    for kind, value, handles in resources:
        if kind in {"stream", "writer"}:
            assert value.closed
        else:
            assert value.handles == []
            for handle in handles:
                if os.name == "nt":
                    info = safe._HandleInfo()
                    assert not safe._kernel.GetFileInformationByHandle(handle, safe.ctypes.byref(info))
                else:
                    with pytest.raises(OSError) as closed:
                        os.fstat(handle)
                    assert closed.value.errno == errno.EBADF
    assert not (root / "source-verified.json").exists() and not (root / "receipt.json").exists()


def test_receipt_publication_fault_keeps_complete_bytes_without_new_failure_file(profile, monkeypatch):
    operation = _uuid()
    root = profile.database.parent / NS / operation
    reached = []
    def checkpoint(phase):
        if phase == "receipt_published":
            reached.append(phase)
            assert (root / "receipt.json").is_file()
            raise OSError(errno.EIO, "injected after receipt publication")
    with monkeypatch.context() as patch:
        patch.setattr(preservation, "_checkpoint", checkpoint)
        with pytest.raises(ERROR):
            _create(profile, operation_id=operation)
    assert reached == ["receipt_published"] and (root / "receipt.json").is_file()
    assert list((root / "failures").iterdir()) == []
    before = _snapshot(root)
    with lifecycle.exclusive(profile):
        verified = preservation.verify_raw_preservation_locked(profile, operation_id=operation,
            expected_request_sha256=_sha((root / "request.json").read_bytes()))
    assert verified.kind == "raw_preservation_verified"
    _assert_disk_package(profile, verified, {})
    assert _snapshot(root) == before


def test_publisher_rechecks_actual_B_payload_before_publishing_completion(profile, monkeypatch):
    _write(profile.database.parent / "node-identity.json", b"original source bytes")
    operation = _uuid()
    root = profile.database.parent / NS / operation
    changed = []
    def checkpoint(phase):
        if phase == "target_closed":
            payload = next((root / "payload").rglob("*.raw"))
            payload.write_bytes(b"changed after B close")
            changed.append(payload)
    monkeypatch.setattr(preservation, "_checkpoint", checkpoint)
    with pytest.raises(ERROR):
        _create(profile, operation_id=operation)
    assert changed and changed[0].read_bytes() == b"changed after B close"
    assert not (root / "source-verified.json").exists() and not (root / "receipt.json").exists()


def test_verifier_never_reads_current_source_or_writes_target_and_uses_new_observation(profile, monkeypatch):
    path = _write(profile.database.parent / "node-identity.json", b"preserved original node")
    created = _create(profile)
    root = Path(created.package_path)
    path.write_bytes(b"a later independent source version")
    before = _snapshot(root)
    original_open = safe._Directory.open_file
    def open_readonly(directory, name, *args, **options):
        assert not options.get("write") and not options.get("create")
        assert _key(directory.path / name) != _key(path), "Verifier reopened current source"
        return original_open(directory, name, *args, **options)
    def forbidden(*_args, **_options):
        pytest.fail("Read-only verifier attempted source capture or target modification")
    with monkeypatch.context() as patch:
        patch.setattr(safe._Directory, "open_file", open_readonly)
        patch.setattr(source, "capture_raw_locked", forbidden)
        patch.setattr(target, "_write_bytes_original", forbidden)
        verified = _verify(profile, created)
    assert verified.kind == "raw_preservation_verified"
    assert verified.counters["confirmed_write_bytes"] == verified.counters["write_calls"] == 0
    assert verified.counters["read_bytes"] > 0
    _assert_disk_package(profile, verified, {"node-identity.json": b"preserved original node"})
    assert _snapshot(root) == before and path.read_bytes() == b"a later independent source version"


@pytest.mark.parametrize("mutation", ["payload", "control", "extra", "missing_receipt"])
def test_verifier_rejects_actual_tamper_and_incomplete_layout_without_repair(profile, mutation):
    _write(profile.database.parent / "node-identity.json", b"original raw source")
    created = _create(profile)
    root = Path(created.package_path)
    if mutation == "payload":
        next((root / "payload").rglob("*.raw")).write_bytes(b"tampered payload")
    elif mutation == "control":
        (root / "source-projection.json").write_bytes(b"{}")
    elif mutation == "extra":
        _write(root / "unexpected.raw", b"unrecorded file must not be ignored")
    else:
        (root / "receipt.json").rename(profile.database.parent / "retained-receipt.raw")
    before = _snapshot(root)
    with pytest.raises(ERROR):
        _verify(profile, created)
    assert _snapshot(root) == before


def test_wrong_request_digest_and_unknown_operation_are_readonly_failures(profile):
    created = _create(profile)
    root = Path(created.package_path)
    before = _snapshot(root)
    with lifecycle.exclusive(profile), pytest.raises(ERROR) as wrong:
        preservation.verify_raw_preservation_locked(profile, operation_id=created.operation_id,
            expected_request_sha256="0" * 64)
    assert wrong.value.code == "AUTHORITY_RAW_PRESERVATION_REQUEST_MISMATCH"
    assert _snapshot(root) == before
    missing = _uuid()
    with lifecycle.exclusive(profile), pytest.raises(ERROR):
        preservation.verify_raw_preservation_locked(profile, operation_id=missing,
            expected_request_sha256=created.request_sha256)
    assert not (root.parent / missing).exists() and _snapshot(root) == before


def test_verifier_close_failure_never_returns_success_or_modifies_package(profile, monkeypatch):
    created = _create(profile)
    root = Path(created.package_path)
    before = _snapshot(root)
    original, failures = target._close_scope_original, []
    def close(state, primary):
        result = original(state, primary)
        if state.mode == "verify" and primary is None and not failures:
            failures.append(state)
            raise OSError(errno.EIO, "injected after real verifier scope close")
        return result
    monkeypatch.setattr(target, "_close_scope_original", close)
    with pytest.raises(ERROR) as raised:
        _verify(profile, created)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION_CLOSE_FAILED" and failures
    assert _snapshot(root) == before


@pytest.mark.parametrize("nested", ["create", "verify"])
def test_cross_operation_callback_reentry_is_sticky_before_any_nested_IO(profile, nested):
    outer, inner = _uuid(), _uuid()
    caught = []
    def callback():
        if caught:
            return
        with pytest.raises(ERROR) as rejected:
            if nested == "create":
                preservation.create_raw_preservation_locked(profile, operation_id=inner)
            else:
                preservation.verify_raw_preservation_locked(profile, operation_id=inner,
                    expected_request_sha256="0" * 64)
        caught.append(rejected.value)
    with pytest.raises(ERROR) as terminal:
        _create(profile, operation_id=outer, check_cancelled=callback)
    assert caught and terminal.value is caught[0]
    assert terminal.value.code == "AUTHORITY_RAW_PRESERVATION_REENTRANT"
    assert not (profile.database.parent / NS / inner).exists()
    assert not (profile.database.parent / NS / outer / "receipt.json").exists()


@pytest.mark.parametrize("kind", [RuntimeError, KeyboardInterrupt])
def test_original_cancellation_object_survives_factory_cleanup(profile, kind):
    operation = _uuid()
    original = kind("synthetic original caller cancellation")
    def cancelled():
        raise original
    with pytest.raises(kind) as raised:
        _create(profile, operation_id=operation, check_cancelled=cancelled)
    assert raised.value is original
    assert not (profile.database.parent / NS / operation / "receipt.json").exists()


def test_delayed_callback_failure_preserves_original_exception_and_writes_bounded_diagnostic(profile, monkeypatch):
    operation = _uuid()
    root = profile.database.parent / NS / operation
    _write(profile.database.parent / "node-identity.json", b"raw source before caller cancellation")
    original = RuntimeError("synthetic caller cancellation after durable selection")
    failure_entries, callback_faults = [], []
    original_failure = target.write_failure_once
    def failure(operation_token, *, phase, exc):
        failure_entries.append(exc)
        return original_failure(operation_token, phase=phase, exc=exc)
    def cancelled():
        if (root / "selection.json").is_file() and any((root / "copy-attempts").glob("*.json")):
            callback_faults.append(True)
            raise original
    monkeypatch.setattr(target, "write_failure_once", failure)
    with pytest.raises(RuntimeError) as raised:
        _create(profile, operation_id=operation, check_cancelled=cancelled)
    assert raised.value is original and callback_faults == [True]
    assert failure_entries == [original]
    assert (root / "request.json").is_file() and (root / "selection.json").is_file()
    failed = list((root / "failures").iterdir())
    assert len(failed) == 1 and failed[0].is_file()
    raw = failed[0].read_bytes()
    document = json.loads(raw)
    assert _wire(document) == raw and len(raw) <= 16384
    assert document["code"] == "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED"
    assert document["operation_id"] == operation
    assert document["request_sha256"] == _sha((root / "request.json").read_bytes())
    assert document["selection_sha256"] == _sha((root / "selection.json").read_bytes())
    assert document["run_id"] == failed[0].stem
    assert not (root / "source-verified.json").exists() and not (root / "receipt.json").exists()


@pytest.mark.parametrize("action", ["create", "verify"])
def test_public_factory_requires_original_profile_EX_before_target_effects(profile, action):
    operation = _uuid()
    before = {path.name for path in profile.database.parent.iterdir()}
    with pytest.raises(ERROR):
        if action == "create":
            preservation.create_raw_preservation_locked(profile, operation_id=operation)
        else:
            preservation.verify_raw_preservation_locked(profile, operation_id=operation,
                expected_request_sha256="0" * 64)
    assert {path.name for path in profile.database.parent.iterdir()} == before
    assert not (profile.database.parent / NS).exists()
