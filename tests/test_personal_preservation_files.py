"""Private package IO keeps physical identity and immutable publication."""
from contextlib import ExitStack
import hashlib
import os

import pytest

from reno import personal_preservation_files as files, sync_media as safe


def test_private_publication_never_replaces_original_fact(tmp_path):
    with safe._Directory(tmp_path, base=tmp_path) as directory:
        files.mkdir_child(directory, "catalog")
        with safe._Directory(tmp_path / "catalog", parent=directory) as catalog:
            raw = b'{"kind":"original"}'
            assert files.publish(catalog, "request.json", raw) == hashlib.sha256(raw).hexdigest()
            original = (catalog.path / "request.json").stat()
            with pytest.raises(FileExistsError):
                files.publish(catalog, "request.json", b'{"kind":"different"}')
            assert (catalog.path / "request.json").read_bytes() == raw
            assert (catalog.path / "request.json").stat().st_ino == original.st_ino


def test_derived_state_can_be_replaced_without_changing_request(tmp_path):
    with safe._Directory(tmp_path, base=tmp_path) as directory:
        files.publish(directory, "request.json", b"original")
        files.publish(directory, "state.json", b"reserved", replace=True)
        files.publish(directory, "state.json", b"ready", replace=True)
    assert (tmp_path / "state.json").read_bytes() == b"ready"
    assert (tmp_path / "request.json").read_bytes() == b"original"


def test_pinned_file_separates_handle_and_path_and_rehashes(tmp_path):
    path = tmp_path / "payload.raw"
    path.write_bytes(b"original bytes")
    with files.FileHold(path, maximum=100) as held:
        assert held.raw == b"original bytes"
        assert held.sha256 == hashlib.sha256(held.raw).hexdigest()
        assert held.path_identity["inode"] == held.handle_identity["inode"]
        held.verify()
    with pytest.raises(files.PreservationFileError, match="FILE_CLOSED"):
        held.verify()


@pytest.mark.skipif(os.name == "nt", reason="Windows pinned package handle denies replacement")
def test_same_sha_different_inode_is_not_a_valid_package(tmp_path):
    path, replacement = tmp_path / "payload.raw", tmp_path / "replacement.raw"
    path.write_bytes(b"same bytes")
    replacement.write_bytes(path.read_bytes())
    with pytest.raises(files.PreservationFileError, match="FILE_CHANGED"):
        with files.FileHold(path, maximum=100) as held:
            replacement.replace(path)
            held.verify()


def test_hardlink_and_oversize_are_rejected(tmp_path):
    path = tmp_path / "payload.raw"
    path.write_bytes(b"original")
    with pytest.raises(files.PreservationFileError, match="FILE_TOO_LARGE"):
        with files.FileHold(path, maximum=1):
            pass
    alias = tmp_path / "alias.raw"
    try:
        os.link(path, alias)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {exc.errno}")
    with pytest.raises(files.PreservationFileError, match="FILE_UNSAFE"):
        with files.FileHold(path, maximum=100):
            pass


def test_primary_cancellation_survives_cleanup(tmp_path):
    path = tmp_path / "payload.raw"
    path.write_bytes(b"original")
    class Cancelled(BaseException):
        pass
    with pytest.raises(Cancelled):
        with files.FileHold(path, maximum=100):
            raise Cancelled()


@pytest.mark.skipif(os.name != "nt", reason="Windows private checks must accept original long paths")
@pytest.mark.parametrize("directory", [True, False], ids=["directory", "file"])
def test_require_private_accepts_original_long_paths_without_changing_objects(tmp_path, directory):
    raw = b'{"kind":"original long-path payload"}'
    with ExitStack() as stack:
        held = stack.enter_context(safe._Directory(tmp_path, base=tmp_path))
        while len(str(held.path)) < 300:
            name = "private-" + "a" * 48
            files.mkdir_child(held, name)
            held = stack.enter_context(safe._Directory(held.path / name, parent=held))
        assert files.publish(held, "payload.json", raw) == hashlib.sha256(raw).hexdigest()
        path = held.path if directory else held.path / "payload.json"
        assert len(str(path)) >= 300 and not str(path).startswith("\\\\?\\")
        parent = held.parent if directory else held
        original = parent.entry_stat(path.name)
        original_payload = files.identity(held.entry_stat("payload.json"))
        with held.open_file("payload.json") as stream:
            assert stream.read() == raw

        files.require_private(path, directory=directory)

        final = parent.entry_stat(path.name)
        fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns", "st_ctime_ns", "st_mode", "st_nlink")
        assert tuple(getattr(final, field) for field in fields) == tuple(
            getattr(original, field) for field in fields)
        assert files.identity(held.entry_stat("payload.json")) == original_payload
        with held.open_file("payload.json") as stream:
            assert stream.read() == raw
