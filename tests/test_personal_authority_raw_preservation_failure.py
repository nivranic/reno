"""V1 diagnostics keep large legitimate predecessors and a small own-byte cap.

Runtime cases use fresh synthetic profiles with the original EX/SH locks.
Caller cancellation is injected before request publication or the first copy.
"""
from copy import deepcopy
import hashlib
import json
import uuid

import pytest

from reno import personal_authority_preservation_target as target
from reno import personal_authority_preservation_source as source
from reno import personal_authority_raw_preservation as preservation
from reno import personal_authority_raw_preservation_contract as contract
from reno import storage_lifecycle as lifecycle, sync_media as safe
from tests.test_personal_authority_preservation_source import _tree, profile
from tests.test_personal_authority_raw_preservation2 import (
    _assert_preserved, _children, _native, _preserved_files, _write,
)


def _wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
        separators=(",", ":"), allow_nan=False).encode("utf-8")


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def test_pre_request_cancellation_still_writes_one_small_unbound_failure_and_closes_resources(profile, monkeypatch):
    tree = _tree(profile)
    _write(tree / "synthetic.raw", b"source must survive a failure before request publication")
    old_source = _preserved_files(tree)
    operation = str(uuid.uuid4())
    root = profile.database.parent / ".reno-authority-raw-preservation-v1" / operation
    original_error = RuntimeError("synthetic original cancellation before request publication")
    requests, callbacks, streams, directories, failure_entries = [], [], [], [], []
    original_publish, original_failure = target.publish_control, target.write_failure_once
    original_open, original_enter = safe._Directory.open_file, source._OwnedDirectory.__enter__

    def publish(token, key, kind, value):
        assert key == "request.json" and kind == "request" and requests == []
        assert _native(root / "failures").is_dir()
        assert not _native(root / "request.json").exists()
        requests.append((value["run_id"], value["limits"]["max_failure_bytes"]))
        # Use the unchanged publication path; its first original budget
        # callback cancels before the request's temporary writer opens.
        return original_publish(token, key, kind, value)

    def cancelled():
        if requests:
            callbacks.append(True)
            raise original_error

    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        if (directory.path / name).is_relative_to(root):
            streams.append((stream, directory.path / name))
        return stream

    def entered(directory):
        value = original_enter(directory)
        directories.append(directory)
        return value

    def failure(operation_token, *, phase, exc):
        failure_entries.append((phase, exc))
        return original_failure(operation_token, phase=phase, exc=exc)

    monkeypatch.setattr(target, "publish_control", publish)
    monkeypatch.setattr(target, "write_failure_once", failure)
    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(source._OwnedDirectory, "__enter__", entered)
    with lifecycle.exclusive(profile):
        with pytest.raises(RuntimeError) as raised:
            preservation.create_raw_preservation_locked(profile, operation_id=operation,
                check_cancelled=cancelled)
    assert raised.value is original_error and callbacks == [True]
    assert failure_entries == [("request", original_error)] and len(requests) == 1
    failures = list(_children(root / "failures"))
    assert len(failures) == 1
    raw = _native(failures[0]).read_bytes()
    document = json.loads(raw)
    assert len(raw) <= requests[0][1] == 16384 and _wire(document) == raw
    assert document["format_version"] == 1
    assert document["kind"] == "authority_raw_preservation_failure"
    assert document["operation_id"] == operation
    assert document["run_id"] == requests[0][0] == failures[0].stem
    assert document["phase"] == "request"
    assert document["code"] == "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED"
    assert document["request_sha256"] is document["selection_sha256"] is None
    assert document["effects"] == [] and document["effects_complete"] is False
    assert set(_preserved_files(root)) == {"failures/" + failures[0].name}
    assert not _native(root / "request.json").exists()
    assert not _native(root / "receipt.json").exists()
    assert streams and all(stream.closed for stream, _path in streams)
    assert [path for _stream, path in streams] == failures
    assert directories and all(directory.handles == [] for directory in directories)
    _assert_preserved(tree, old_source)


def test_large_real_source_prefix_still_publishes_small_failure_and_preserves_original_cancellation(profile, monkeypatch):
    tree = _tree(profile)
    for index in range(16):
        _write(tree / f"synthetic-{index:02d}.raw", f"source-{index:02d}".encode("ascii"))
    old_source = _preserved_files(tree)
    operation = str(uuid.uuid4())
    root = profile.database.parent / ".reno-authority-raw-preservation-v1" / operation
    original_error = RuntimeError("synthetic original cancellation before first copy effect")
    streams, dispatched, callbacks, failure_entries = [], [], [], []
    original_open, original_copy = safe._Directory.open_file, target.copy_entry
    original_failure = target.write_failure_once

    def opened(directory, name, *args, **options):
        stream = original_open(directory, name, *args, **options)
        path = directory.path / name
        if path.is_relative_to(root) or path.is_relative_to(tree):
            streams.append((stream, path))
        return stream

    def first_copy(token, capture, entry_id):
        assert dispatched == []
        metadata = _native(root / "source-metadata.json").read_bytes()
        assert len(metadata) > 16384
        assert _native(root / "selection.json").is_file()
        assert list(_children(root / "copy-attempts")) == []
        dispatched.append(_preserved_files(root))
        # The original copy implementation's first budget callback performs
        # the cancellation. It remains the actual registered caller exception.
        return original_copy(token, capture, entry_id)

    def cancelled():
        if dispatched:
            callbacks.append(True)
            raise original_error

    def failure(operation_token, *, phase, exc):
        failure_entries.append((phase, exc))
        return original_failure(operation_token, phase=phase, exc=exc)

    monkeypatch.setattr(safe._Directory, "open_file", opened)
    monkeypatch.setattr(target, "copy_entry", first_copy)
    monkeypatch.setattr(target, "write_failure_once", failure)
    with lifecycle.exclusive(profile):
        with pytest.raises(RuntimeError) as raised:
            preservation.create_raw_preservation_locked(profile, operation_id=operation,
                check_cancelled=cancelled)
    assert raised.value is original_error
    assert callbacks == [True] and failure_entries == [("copy", original_error)]
    assert len(dispatched) == 1
    failures = list(_children(root / "failures"))
    assert len(failures) == 1
    raw = _native(failures[0]).read_bytes()
    document = json.loads(raw)
    request_raw = _native(root / "request.json").read_bytes()
    request = json.loads(request_raw)
    assert len(raw) <= request["limits"]["max_failure_bytes"] == 16384
    assert _wire(document) == raw
    assert document["format_version"] == 1
    assert document["kind"] == "authority_raw_preservation_failure"
    assert document["operation_id"] == operation
    assert document["run_id"] == request["run_id"] == failures[0].stem
    assert document["phase"] == "copy"
    assert document["code"] == "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED"
    assert document["request_sha256"] == _sha(request_raw)
    assert document["selection_sha256"] == _sha(_native(root / "selection.json").read_bytes())
    assert document["effects"] == [] and document["effects_complete"] is False
    assert streams and all(stream.closed for stream, _path in streams)
    assert any(path.is_relative_to(tree) for _stream, path in streams)
    assert [path for _stream, path in streams if path.parent == root / "failures"] == failures
    assert list(_children(root / "copy-attempts")) == []
    assert list(_children(root / "copy-records")) == []
    assert list(_children(root / "payload")) == []
    assert not _native(root / "source-verified.json").exists()
    assert not _native(root / "receipt.json").exists()
    _assert_preserved(root, dispatched[0])
    _assert_preserved(tree, old_source)


@pytest.mark.parametrize("entrypoint", ["normalize", "encode", "decode"])
def test_failure_own_bytes_above_16KiB_still_reject_with_valid_bounded_effect_rows(entrypoint):
    small = {
        "format_version": 1,
        "kind": "authority_raw_preservation_failure",
        "operation_id": "11111111-1111-4111-8111-111111111111",
        "run_id": "22222222-2222-4222-8222-222222222222",
        "request_sha256": None,
        "selection_sha256": None,
        "phase": "copy",
        "code": "AUTHORITY_RAW_PRESERVATION_CALLER_CANCELLED",
        "counters": {key: 0 for key in ("read_bytes", "read_calls", "seek_calls",
            "confirmed_write_bytes", "write_calls", "uncertain_read_calls",
            "uncertain_write_bytes", "elapsed_ns")},
        "effects": [{"relative_key": f"synthetic-{index:02d}", "kind": "directory",
            "identity": None, "sha256": None, "byte_size": None} for index in range(16)],
        "effects_complete": False,
    }
    enlarged = deepcopy(small)
    for row in enlarged["effects"]:
        row["relative_key"] += "/" + "/".join(["x" * 200] * 6)
    assert len(_wire(small)) < 16384 < len(_wire(enlarged))

    def check(value):
        if entrypoint == "normalize":
            return contract.normalize_failure(value)
        if entrypoint == "encode":
            return contract.encode("failure", value)
        return contract.decode("failure", _wire(value))

    assert check(small) == (_wire(small) if entrypoint == "encode" else small)
    with pytest.raises(contract.Error) as raised:
        check(enlarged)
    assert raised.value.code == "AUTHORITY_RAW_PRESERVATION1_LIMIT"
