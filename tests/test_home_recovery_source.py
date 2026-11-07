"""Real isolated Home checkpoint images; no provider or platform requests."""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import uuid

import pytest

from reno import config, db, home_recovery_source as sources
from reno import storage_admin, storage_backups, storage_lifecycle, sync_state, tasks
from tests.schema_fixtures import initialize_released16, released16


def _build_source_home16(tmp_path):
    profile = storage_lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                               tmp_path / "reports", tmp_path / "checkpoints")
    initialize_released16(profile.database)
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


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The original SDK remains on its released16 version for production,
    # readers, original-UUID retries and every owner's complete lifetime.
    with released16(monkeypatch):
        yield _build_source_home16(tmp_path)


@contextmanager
def opened(profile, source_id):
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN")
        with sources.open_source(con, source_id, profile=profile) as source:
            yield con, source
        con.rollback()


def create(env, source_id=None):
    profile = env[0]
    identifier = source_id or str(uuid.uuid4())
    return identifier, sources.create_checkpoint(profile=profile, source_id=identifier)


def sql_rows(profile):
    with storage_admin.connection(profile) as con:
        return {row[0]: [tuple(item) for item in con.execute('SELECT * FROM "' + row[0] + '"')]
                for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")}


def test_checkpoint_produces_bound_registered_image_without_sql_mutation(env):
    profile = env[0]
    before = sql_rows(profile)
    source_id, result = create(env)
    assert result["source_kind"] == "home_checkpoint" and result["source_id"] == source_id
    assert result["replayed"] is False
    assert sql_rows(profile) == before
    with opened(profile, source_id) as (_, source):
        assert source.rows["knowledge_atom"][0]["claim"] == "原知识"
        assert source.rows["meta"] == [{"key": "step:home_video:atomize", "value": "original-step"}]
        public = json.dumps(source.public(), ensure_ascii=False)
        assert "private" not in public and str(profile.database.parent) not in public
        assert len(source.declaration["media_dependencies"]) == 3
    with storage_lifecycle.shared(profile):
        found = storage_backups.discover_locked(profile)
    assert len(found["backups"]) == 1 and found["backups"][0]["state"] == "ready"


def test_exact_checkpoint_retry_reads_original_source_after_new_personal_writes(env):
    profile = env[0]
    source_id, first = create(env)
    with storage_admin.connection(profile) as con:
        con.execute("UPDATE meta SET value=? WHERE key='private-test-blob'", (b"changed-private",))
        db.bump_kb_revision(con)
        con.commit()
    before = sql_rows(profile)
    again = sources.create_checkpoint(profile=profile, source_id=source_id)
    assert again == {**first, "replayed": True}
    assert sql_rows(profile) == before


def test_unregistered_backup_and_bare_path_never_become_a_source(env):
    profile = env[0]
    identifier = str(uuid.uuid4())
    with storage_admin.connection(profile) as con:
        db.snapshot(con, profile.database.with_name("ordinary-backup.db"), profile=profile)
    with pytest.raises(sources.HomeRecoverySourceError):
        with opened(profile, identifier):
            pytest.fail("An ordinary backup lacks a Home provenance declaration")
    with pytest.raises(ValueError):
        sources.create_checkpoint(profile=profile, source_id=str(profile.database))


@pytest.mark.parametrize("index", [1, 2, 3])
def test_changed_video_audio_and_frame_bytes_are_not_reinterpreted(env, index):
    source_id, _ = create(env)
    path = env[index]
    body = path.read_bytes()
    path.write_bytes(b"X" + body[1:])
    with pytest.raises(sources.HomeRecoverySourceError):
        with opened(env[0], source_id):
            pytest.fail("The checkpoint must retain its original media digests")


def test_source_node_identity_is_original_and_cannot_be_rebound(env):
    source_id, _ = create(env)
    identity = env[0].database.parent / "node-identity.json"
    identity.write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    with pytest.raises(sources.HomeRecoverySourceError, match="SOURCE_IDENTITY_MISMATCH"):
        with opened(env[0], source_id):
            pytest.fail("A new node cannot inherit the original source binding")


def test_source_library_identity_is_not_imported_from_the_backup(env):
    source_id, _ = create(env)
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        con.commit()
    with pytest.raises(sources.HomeRecoverySourceError, match="SOURCE_IDENTITY_MISMATCH"):
        with opened(env[0], source_id):
            pytest.fail("An image cannot overwrite current library identity")


def test_source_requires_the_actual_lifetime_guard(env):
    source_id, _ = create(env)
    con = db.connect(env[0].database)
    try:
        with pytest.raises(storage_lifecycle.StorageLifecycleError):
            with sources.open_source(con, source_id, profile=env[0]):
                pytest.fail("Reading a source must not manufacture SH after SQL")
    finally:
        con.close()


def test_checkpoint_refuses_busy_worker_without_stopping_it(env):
    profile = env[0]
    identifier = str(uuid.uuid4())
    with tasks.process_lock(profile=profile) as acquired:
        assert acquired
        with pytest.raises(sources.HomeRecoverySourceError, match="WORKER_BUSY"):
            sources.create_checkpoint(profile=profile, source_id=identifier)
        assert tasks.holds_process_lock(profile=profile)
    assert not (profile.database.parent / sources.DIRECTORY / (identifier + ".json")).exists()


def test_open_source_keeps_caller_exception_identity(env):
    source_id, _ = create(env)
    original = ValueError("synthetic-caller-failure")
    with pytest.raises(ValueError) as caught:
        with opened(env[0], source_id):
            raise original
    assert caught.value is original


def test_modified_materialized_knowledge_cannot_commit_as_the_original_source(env):
    source_id, _ = create(env)
    with pytest.raises(sources.HomeRecoverySourceError, match="SOURCE_CHANGED"):
        with opened(env[0], source_id) as (_, source):
            source.rows["knowledge_atom"][0]["claim"] = "different-content"


def test_broken_evidence_does_not_publish_a_source(env):
    profile = env[0]
    identifier = str(uuid.uuid4())
    with storage_admin.connection(profile) as con:
        con.execute("UPDATE evidence_ref SET source_item_id='missing-source'")
        con.commit()
    with pytest.raises(ValueError):
        sources.create_checkpoint(profile=profile, source_id=identifier)
    assert not (profile.database.parent / sources.DIRECTORY / (identifier + ".json")).exists()


def test_future_knowledge_column_cannot_be_silently_omitted_from_a_source(env):
    profile = env[0]
    identifier = str(uuid.uuid4())
    with storage_admin.connection(profile) as con:
        con.execute("ALTER TABLE frame ADD COLUMN unknown_local_input TEXT")
        con.commit()
    with pytest.raises(ValueError):
        sources.create_checkpoint(profile=profile, source_id=identifier)
    assert not (profile.database.parent / sources.DIRECTORY / (identifier + ".json")).exists()


def test_sql_reader_cannot_inspect_personal_or_paid_tables(env):
    import sqlite3
    profile = env[0]
    with storage_admin.connection(profile) as con:
        con.set_authorizer(sources._authorize_knowledge)
        assert con.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "原知识"
        for table in ("budget_attempt", "job", "source_record", "renovation_project", "user_decision"):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute('SELECT * FROM "' + table + '"').fetchall()
        con.set_authorizer(None)


def declaration_gap(env, identifier, monkeypatch):
    original = storage_backups._write_json
    target = env[0].database.parent / sources.DIRECTORY / (identifier + ".json")
    def interrupted(path, *args, **kwargs):
        if path == target:
            raise OSError("synthetic-declaration-gap")
        return original(path, *args, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(storage_backups, "_write_json", interrupted)
        with pytest.raises(OSError, match="synthetic-declaration-gap"):
            sources.create_checkpoint(profile=env[0], source_id=identifier)
    assert not target.exists()
    assert target.with_name(identifier + ".intent.json").exists()


def test_ready_image_declaration_gap_keeps_the_original_node(env, monkeypatch):
    identifier = str(uuid.uuid4())
    declaration_gap(env, identifier, monkeypatch)
    identity = env[0].database.parent / "node-identity.json"
    identity.write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    with pytest.raises(sources.HomeRecoverySourceError, match="SOURCE_IDENTITY_MISMATCH"):
        sources.create_checkpoint(profile=env[0], source_id=identifier)


def test_ready_original_image_can_finish_after_later_knowledge_edits(env, monkeypatch):
    identifier = str(uuid.uuid4())
    declaration_gap(env, identifier, monkeypatch)
    with storage_admin.connection(env[0]) as con:
        con.execute("UPDATE knowledge_atom SET claim='当前新知识'")
        con.commit()
        current = db.knowledge_version(con)
    result = sources.create_checkpoint(profile=env[0], source_id=identifier)
    assert result["source_version"]["kb_revision"] < current["kb_revision"]
    with opened(env[0], identifier) as (_, source):
        assert source.rows["knowledge_atom"][0]["claim"] == "原知识"
    with storage_admin.connection(env[0]) as con:
        assert con.execute("SELECT claim FROM knowledge_atom").fetchone()[0] == "当前新知识"


def test_yielded_source_declaration_cannot_change_the_original_node(env):
    identifier, _ = create(env)
    with pytest.raises(sources.HomeRecoverySourceError, match="SOURCE_CHANGED"):
        with opened(env[0], identifier) as (_, source):
            source.declaration["node_id"] = str(uuid.uuid4())


def test_controlled_read_callback_keeps_its_actual_exception(env):
    identifier, _ = create(env)
    original = ValueError("synthetic-cancelled-control")
    class Control:
        def check(self):
            raise original
    with pytest.raises(ValueError) as caught:
        with storage_backups.controlled_reads(Control()):
            with opened(env[0], identifier):
                pytest.fail("Cancelled reads cannot produce a usable source")
    assert caught.value is original


def test_cloud_cannot_produce_a_home_checkpoint(env, monkeypatch):
    monkeypatch.setattr(config, "is_cloud", lambda: True)
    with pytest.raises(sources.HomeRecoverySourceError, match="ROLE_REQUIRED"):
        sources.create_checkpoint(profile=env[0], source_id=str(uuid.uuid4()))
