"""Explicit administrative CLI on enrolled artificial TEMP libraries."""
import json
from contextlib import closing
import sqlite3
import uuid

import pytest

from reno import cli, config, storage_gc as gc, storage_roots as roots
from tests.test_storage_roots_cli import library, create_library, arguments  # noqa: F401
from tests.test_storage_roots_group import registered


def preview(profile, capsys, *policy):
    assert cli.main(["storage-roots", *arguments(profile), *policy]) == 0
    return json.loads(capsys.readouterr().out)["preview_sha256"]


def test_default_preview_never_invokes_collection(library, capsys, monkeypatch):
    profile, item, path = library
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Preview must not call any collector")
    monkeypatch.setattr(gc, "apply", forbidden)
    monkeypatch.setattr(gc, "resume", forbidden)
    digest = preview(profile, capsys)
    assert len(digest) == 64 and path.exists()


def test_explicit_cli_really_deletes_and_replays_original_receipt(library, tmp_path, capsys, monkeypatch):
    profile, item, path = library
    digest = preview(profile, capsys)
    identifier = str(uuid.uuid4())
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Administrative profile must never fall back to config")
    monkeypatch.setattr(config, "db_path", forbidden)
    command = ["storage-roots", *arguments(profile), "--apply", "--expected-digest", digest, "--operation-id", identifier]
    assert cli.main(command) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["state"] == "completed" and first["deleted_bytes"] == item["byte_size"] and not path.exists()
    assert str(tmp_path) not in json.dumps(first)
    assert cli.main(command) == 0
    assert json.loads(capsys.readouterr().out) == first
    assert cli.main(["storage-roots", *arguments(profile), "--resume", "--operation-id", identifier]) == 0
    assert json.loads(capsys.readouterr().out) == first


@pytest.mark.parametrize("flags", [
    ["--apply"], ["--apply", "--operation-id", "PRIVATE_UUID"],
    ["--apply", "--expected-digest", "PRIVATE_DIGEST"], ["--resume"],
    ["--expected-digest", "PRIVATE_DIGEST"], ["--operation-id", "PRIVATE_UUID"],
    ["--resume", "--operation-id", "PRIVATE_UUID", "--expected-digest", "PRIVATE_DIGEST"],
    ["--resume", "--operation-id", "PRIVATE_UUID", "--keep-publication", "3"],
    ["--resume", "--operation-id", "PRIVATE_UUID", "--keep-recovery", "3"],
    ["--resume", "--operation-id", "PRIVATE_UUID", "--namespace", "sync_media"],
])
def test_missing_or_conflicting_operation_arguments_never_invoke_gc(library, capsys, monkeypatch, flags):
    profile, _item, path = library
    def forbidden(*_args, **_kwargs):
        raise AssertionError("Invalid arguments must fail before any GC or scan")
    monkeypatch.setattr(gc, "apply", forbidden)
    monkeypatch.setattr(gc, "resume", forbidden)
    monkeypatch.setattr(roots, "preview", forbidden)
    assert cli.main(["storage-roots", *arguments(profile), *flags]) == 2
    output = capsys.readouterr()
    assert not output.out and json.loads(output.err)["code"] == "ROOTS_ARGUMENT_INVALID"
    assert "PRIVATE" not in output.err and path.exists()


def test_apply_and_resume_are_mutually_exclusive(capsys):
    with pytest.raises(SystemExit) as error:
        cli.main(["storage-roots", "--apply", "--resume"])
    assert error.value.code == 2
    assert json.loads(capsys.readouterr().err)["code"] == "ROOTS_ARGUMENT_INVALID"


def test_stale_preview_and_unknown_resume_are_sanitized(library, capsys):
    profile, _item, path = library
    identifier = str(uuid.uuid4())
    assert cli.main(["storage-roots", *arguments(profile), "--apply", "--expected-digest", "a" * 64,
                     "--operation-id", identifier]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "GC_PREVIEW_CHANGED"
    assert path.exists()
    assert cli.main(["storage-roots", *arguments(profile), "--resume", "--operation-id", identifier]) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "GC_OPERATION_NOT_FOUND"


def test_cli_resume_uses_original_nondefault_policy_and_selection(library, tmp_path, capsys, monkeypatch):
    profile, _item, path = library
    identifier = str(uuid.uuid4())
    policy = ["--keep-publication", "1", "--keep-recovery", "2", "--namespace", "sync_media"]
    digest = preview(profile, capsys, *policy)
    original = gc._execute
    def stopped(*_args, **_kwargs):
        raise RuntimeError("PRIVATE_PATH / synthetic stop after journal")
    monkeypatch.setattr(gc, "_execute", stopped)
    assert cli.main(["storage-roots", *arguments(profile), *policy, "--apply", "--expected-digest", digest,
                     "--operation-id", identifier]) == 2
    output = capsys.readouterr()
    assert json.loads(output.err)["code"] == "GC_OPERATION_FAILED" and "PRIVATE" not in output.err
    assert path.exists()
    with closing(sqlite3.connect(profile.database)) as con:
        con.row_factory = sqlite3.Row
        untouched, _later_item = registered({"profile": profile, "con": con}, content=b"later owner candidate")
    monkeypatch.setattr(gc, "_execute", original)
    assert cli.main(["storage-roots", *arguments(profile), "--resume", "--operation-id", identifier]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "completed" and result["selected_count"] == 1
    assert not path.exists() and untouched.exists()


def test_blocked_receipt_returns_nonzero_and_records_actual_bytes(library, capsys, monkeypatch):
    profile, item, path = library
    digest = preview(profile, capsys)
    def denied(*_args, **_kwargs):
        raise OSError("PRIVATE_PATH / unlink denied")
    monkeypatch.setattr(gc, "_unlink", denied)
    assert cli.main(["storage-roots", *arguments(profile), "--apply", "--expected-digest", digest,
                     "--operation-id", str(uuid.uuid4())]) == 2
    output = capsys.readouterr()
    assert output.out, output.err
    result = json.loads(output.out)
    assert not output.err and result["state"] == "blocked" and result["deleted_bytes"] == 0
    assert result["items"][0]["artifact_id"] == item["artifact_id"] and path.exists()
    assert "PRIVATE" not in output.out


def test_partial_deletion_keeps_observed_bytes_in_blocked_cli_receipt(library, capsys, monkeypatch):
    profile, first_item, first_path = library
    with closing(sqlite3.connect(profile.database)) as con:
        con.row_factory = sqlite3.Row
        second_path, second_item = registered({"profile": profile, "con": con},
            content=b"second file remains", video="zz_failed")
    digest = preview(profile, capsys)
    original = gc._unlink
    def selective(con, selected_profile, file, check, **options):
        if file["artifact_id"] == second_item["artifact_id"]:
            raise OSError("synthetic second unlink failure")
        return original(con, selected_profile, file, check, **options)
    monkeypatch.setattr(gc, "_unlink", selective)
    assert cli.main(["storage-roots", *arguments(profile), "--apply", "--expected-digest", digest,
                     "--operation-id", str(uuid.uuid4())]) == 2
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert not output.err and result["state"] == "blocked" and result["selected_count"] == 2
    assert result["deleted_bytes"] == first_item["byte_size"]
    by_id = {item["artifact_id"]: item for item in result["items"]}
    assert by_id[first_item["artifact_id"]]["state"] == "deleted"
    assert by_id[second_item["artifact_id"]]["state"] == "blocked"
    assert not first_path.exists() and second_path.exists()
