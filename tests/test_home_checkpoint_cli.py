"""Explicit checkpoint production and read-only default CLI using real DBs."""
import hashlib
import json
import uuid

import pytest

from reno import cli, db, storage_admin, storage_lifecycle
from tests.schema_fixtures import initialize_current17
from tests.test_home_recovery_source import sql_rows


@pytest.fixture
def env(tmp_path):
    # Current public CLI uses an independent production17 image. No yielding
    # old SDK fixture may keep global16 selected during its public calls.
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                               tmp_path / "reports", tmp_path / "checkpoints")
    initialize_current17(profile.database)
    storage_lifecycle.initialize(profile)
    video = profile.media / "originals/video.mp4"
    audio = profile.media / "audio/audio.m4a"
    frame = profile.media / "frames/frame.jpg"
    for path, body in ((video, b"synthetic-video"), (audio, b"synthetic-audio"), (frame, b"synthetic-frame")):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    with storage_admin.connection(profile) as con:
        db.upsert_asset(con, {"video_id": "home_video", "source_type": "local_file", "title": "人工样本",
            "content_sha256": hashlib.sha256(video.read_bytes()).hexdigest(),
            "audio_sha256": hashlib.sha256(audio.read_bytes()).hexdigest(),
            "files": {"video": video.relative_to(profile.media.parent).as_posix(),
                      "audio": audio.relative_to(profile.media.parent).as_posix()}, "status": "processed"}, profile=profile)
        db.replace_frames(con, "home_video", [{"frame_id": "frame_0", "pts_ms": 0,
            "origin": "scene", "dhash": "0x00", "path": frame.relative_to(profile.media.parent).as_posix()}], profile=profile)
        db.replace_segments(con, "home_video", [{"id": "asr_0", "start_ms": 0, "end_ms": 100, "text": "人工原文"}])
        db.replace_atoms(con, "home_video", [{"id": "home_atom", "claim": "原知识", "polarity": "recommend",
            "evidence_refs": [{"modality": "asr", "source_item_id": "asr_0", "start_ms": 0,
                               "end_ms": 100, "evidence_text": "人工原文"}]}])
        con.execute("INSERT INTO meta VALUES('step:home_video:atomize','original-step')")
        con.execute("INSERT INTO meta VALUES('private-test-blob',?)", (b"\x00\xffprivate-should-stay-local",))
        con.commit()
    return profile, video, audio, frame


def arguments(profile, source_id):
    return ["home-checkpoint", source_id, "--db", str(profile.database), "--media", str(profile.media),
            "--reports", str(profile.reports), "--checkpoints", str(profile.checkpoints)]


def test_cli_creates_only_explicitly_and_default_preview_preserves_every_sql_row(env, capsys, monkeypatch):
    profile = env[0]
    identifier = str(uuid.uuid4())
    before = sql_rows(profile)
    assert cli.main(arguments(profile, identifier) + ["--create", "--apply"]) == 0
    created = json.loads(capsys.readouterr().out)
    assert created["source_id"] == identifier and not created["replayed"]
    assert sql_rows(profile) == before
    def forbidden(*args, **kwargs):
        pytest.fail("Default preview cannot initialize facilities or member records")
    monkeypatch.setattr(storage_admin, "setup", forbidden)
    monkeypatch.setattr(storage_admin, "initialize", forbidden)
    assert cli.main(arguments(profile, identifier)) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview == {key: value for key, value in created.items() if key != "replayed"}
    assert sql_rows(profile) == before


@pytest.mark.parametrize("flags", [["--create"], ["--apply"]])
def test_incomplete_creation_arguments_never_create_a_backup(env, capsys, flags):
    profile = env[0]
    identifier = str(uuid.uuid4())
    before = sql_rows(profile)
    assert cli.main(arguments(profile, identifier) + flags) == 2
    assert json.loads(capsys.readouterr().err)["code"] == "HOME_CHECKPOINT_EXPECTATION_REQUIRED"
    assert sql_rows(profile) == before
    assert not list(profile.database.parent.glob("*.bak-home-knowledge-*"))


def test_readonly_missing_database_creates_no_facilities_or_identity(tmp_path, capsys):
    profile = storage_admin.profile(database=tmp_path / "missing/reno.db", media=tmp_path / "media",
                                    reports=tmp_path / "reports", checkpoints=tmp_path / "checkpoints")
    assert cli.main(arguments(profile, str(uuid.uuid4()))) == 2
    assert json.loads(capsys.readouterr().err)["code"] in {"SCHEMA_UPGRADE_REQUIRED", "STORAGE_NOT_INITIALIZED"}
    assert list(tmp_path.iterdir()) == []


def test_invalid_argv_does_not_echo_private_path_or_string(env, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(["home-checkpoint", str(uuid.uuid4()), "--unexpected-private-value"])
    assert caught.value.code == 2
    captured = capsys.readouterr()
    assert "unexpected-private-value" not in captured.err
    assert json.loads(captured.err)["code"] == "HOME_CHECKPOINT_ARGUMENT_INVALID"


def test_partial_custom_profile_is_rejected_with_a_fixed_public_error(env, capsys):
    assert cli.main(["home-checkpoint", str(uuid.uuid4()), "--db", str(env[0].database)]) == 2
    captured = capsys.readouterr()
    assert json.loads(captured.err)["code"] == "STORAGE_PROFILE_REQUIRED"
    assert str(env[0].database) not in captured.err
