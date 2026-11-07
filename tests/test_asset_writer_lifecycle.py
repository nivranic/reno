"""Reference-producing asset/repair paths use isolated schema10 files only."""
from contextlib import contextmanager
import hashlib
import importlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import time

import pytest

from reno import db, files_json, ingest, media, pipeline, storage_admin
from reno import storage_lifecycle as lifecycle, tasks


@pytest.fixture
def library(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(profile.database)
    lifecycle.initialize(profile)
    return profile


def asset(profile, video="video", files=None):
    with storage_admin.connection(profile) as con:
        db.upsert_asset(con, {"video_id": video, "content_sha256": hashlib.sha256(video.encode()).hexdigest(),
                              "duration_ms": 1000, "files": files or {}}, profile=profile)
        con.commit()


def originals(profile, video="video"):
    folder = profile.media / "originals" / video
    folder.mkdir(parents=True, exist_ok=True)
    for name in ("video.mp4", "audio.m4a"):
        (folder / name).write_bytes((video + name).encode())
    return folder


def test_asset_and_frame_domain_writers_require_exact_caller_guard(library):
    con = db.connect(library.database)
    try:
        con.execute("BEGIN IMMEDIATE")
        before = con.total_changes
        for function in (lambda: db.upsert_asset(con, {"video_id": "v", "content_sha256": "sha"}, profile=library),
                         lambda: db.replace_frames(con, "v", [], profile=library)):
            with pytest.raises(lifecycle.StorageLifecycleError) as raised:
                function()
            assert raised.value.code == "STORAGE_GUARD_REQUIRED"
            assert con.in_transaction and con.total_changes == before
        con.rollback()
        with lifecycle.shared(library):
            db.upsert_asset(con, {"video_id": "v", "content_sha256": "sha"}, profile=library)
            db.replace_frames(con, "v", [{"frame_id": "f", "pts_ms": 0, "origin": "scene", "dhash": "0",
                                           "path": "media/frames/v/f.jpg"}], profile=library)
            con.commit()
        assert con.execute("SELECT count(*) FROM frame").fetchone()[0] == 1
    finally:
        con.close()


def test_register_hash_and_commit_use_explicit_profile_not_global_media(library, monkeypatch):
    path = originals(library) / "video.mp4"
    original_hash = ingest._sha256
    seen = []
    def hashing(path):
        seen.append(lifecycle.require_guard(library))
        return original_hash(path)
    monkeypatch.setattr(ingest, "_sha256", hashing)
    result = ingest._register("video", {"title": "synthetic"}, path, None, "local_file", None, profile=library)
    assert result["status"] == "imported" and seen
    con = db.connect(library.database)
    try:
        row = dict(db.get_asset(con, "video"))
        assert json.loads(row["files_json"])["video"] == "media/originals/video/video.mp4"
        assert con.execute("SELECT count(*) FROM job WHERE status='pending' AND video_id='video'").fetchone()[0] == 1
    finally:
        con.close()


def test_normalization_commits_only_after_guard_and_preserves_missing_values(library):
    folder = originals(library)
    asset(library, files={"video": "media\\originals\\video\\video.mp4", "audio": "missing.m4a"})
    before = db.connect(library.database)
    try:
        original = before.execute("SELECT files_json FROM video_asset").fetchone()[0]
    finally:
        before.close()
    assert files_json.run(dry_run=True, profile=library)["rewritten"] == 1
    with storage_admin.connection(library) as con:
        assert con.execute("SELECT files_json FROM video_asset").fetchone()[0] == original
    assert files_json.run(profile=library)["rewritten"] == 1
    assert files_json.run(profile=library)["rewritten"] == 0
    with storage_admin.connection(library) as con:
        files = json.loads(con.execute("SELECT files_json FROM video_asset").fetchone()[0])
        assert files == {"video": "media/originals/video/video.mp4", "audio": "missing.m4a"}
    assert (folder / "video.mp4").read_bytes() == b"videovideo.mp4"


def test_media_uses_exact_profile_for_inputs_outputs_and_frame_commit(library, monkeypatch):
    folder = originals(library)
    asset(library, files={"video": "media/originals/video/video.mp4"})
    seen = []
    def extract(src, out):
        seen.append((src, out, lifecycle.require_guard(library)))
        out.write_bytes(b"synthetic wav")
    monkeypatch.setattr(media, "extract_audio", extract)
    monkeypatch.setattr(media, "detect_scenes", lambda path: [])
    monkeypatch.setattr(media, "sample_targets", lambda *args: {})
    monkeypatch.setattr(media.config, "resolve_media", lambda *a: pytest.fail("Media must resolve with its explicit profile"))
    with storage_admin.connection(library) as con:
        result = media.run(con, "video", profile=library)
        assert not con.in_transaction
    assert result["n_frames_kept"] == 0
    assert seen[0][:2] == (folder / "video.mp4", library.media / "audio/video/audio16k.wav")
    assert (library.media / "frames/video").is_dir()


def test_repair_dry_run_then_registered_backup_and_idempotent_rows(library):
    folder = originals(library)
    asset(library, files={"video": "gone.mp4"})
    before = sorted(library.database.parent.iterdir())
    result = files_json.repair(dry_run=True, profile=library)
    assert result["repaired"] == 1 and not result["backup_created"]
    assert sorted(library.database.parent.iterdir()) == before
    result = files_json.repair(profile=library)
    assert result["repaired"] == 1 and result["backup_created"]
    backups = list(library.database.parent.glob("reno.db.bak-files-repair-*"))
    images = [path for path in backups if not path.name.endswith(".reno-backup.json")]
    assert len(images) == 1 and Path(str(images[0]) + ".reno-backup.json").is_file()
    con = sqlite3.connect(images[0].as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        assert json.loads(con.execute("SELECT files_json FROM video_asset").fetchone()[0]) == {"video": "gone.mp4"}
    finally:
        con.close()
    with storage_admin.connection(library) as con:
        files = json.loads(con.execute("SELECT files_json FROM video_asset").fetchone()[0])
    assert files == {"video": "media/originals/video/video.mp4", "audio": "media/originals/video/audio.m4a"}
    assert (folder / "video.mp4").exists()
    assert files_json.repair(dry_run=True, profile=library)["repaired"] == 0


def test_repair_refuses_live_worker_before_sql_or_backup(library, monkeypatch):
    originals(library)
    asset(library)
    @contextmanager
    def no_lifetime(*args):
        pytest.fail("Busy worker must fail before lifetime/DB/backup")
        yield
    with tasks.process_lock(profile=library) as acquired:
        assert acquired
        monkeypatch.setattr(storage_admin, "connection", no_lifetime)
        with pytest.raises(tasks.TaskError, match="路径修复需要本实例写入者已停止") as raised:
            files_json.repair(profile=library)
        assert raised.value.code == "WORKER_BUSY"
    assert not list(library.database.parent.glob("reno.db.bak-files-repair-*"))


def test_repair_script_import_is_inert(library, monkeypatch):
    monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: pytest.fail("Script import must not open SQLite"))
    importlib.import_module("scripts.fix_files_json")


@pytest.mark.parametrize("failure", [OSError, RuntimeError, KeyboardInterrupt])
def test_connection_close_failure_does_not_replace_original(library, monkeypatch, failure):
    original = failure("primary caller exception")
    class BrokenClose:
        def close(self):
            raise OSError("cleanup private exception")
    monkeypatch.setattr(db, "connect", lambda *a, **k: BrokenClose())
    with pytest.raises(failure) as raised:
        with storage_admin.connection(library):
            raise original
    assert raised.value is original


def test_normal_close_failure_still_surfaces(library, monkeypatch):
    original = OSError("normal close failure")
    class BrokenClose:
        def close(self):
            raise original
    monkeypatch.setattr(db, "connect", lambda *a, **k: BrokenClose())
    with pytest.raises(OSError) as raised:
        with storage_admin.connection(library):
            pass
    assert raised.value is original


@pytest.mark.parametrize("operation", ["register", "normalize", "repair", "media"])
def test_reference_producer_waits_for_real_other_process_exclusive(library, tmp_path, operation):
    folder = originals(library)
    asset(library, files={"video": "media\\originals\\video\\video.mp4"})
    ready, done = tmp_path / "ready", tmp_path / "done"
    code = '''
import json,sys
from pathlib import Path
from reno import db,files_json,ingest,media,storage_admin,storage_lifecycle as life
profile=life.StorageProfile(*map(Path,json.loads(sys.argv[1])))
ready,done=map(Path,sys.argv[2:4]); action=sys.argv[4]
ready.write_bytes(b"ready")
if action=="register":
    ingest._register("new",{},profile.media/"originals/video/video.mp4",None,"local_file",None,profile=profile)
elif action=="normalize":
    files_json.run(profile=profile)
elif action=="repair":
    files_json.repair(profile=profile)
else:
    media.extract_audio=lambda src,out: out.write_bytes(b"wav")
    media.detect_scenes=lambda path: []
    media.sample_targets=lambda *a: {}
    with storage_admin.connection(profile) as con:
        media.run(con,"video",profile=profile)
done.write_bytes(b"done")
'''
    paths = json.dumps([str(getattr(library, field)) for field in ("database", "media", "reports", "checkpoints")])
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    process = None
    try:
        with lifecycle.exclusive(library):
            process = subprocess.Popen([sys.executable, "-B", "-c", code, paths, str(ready), str(done), operation],
                                       cwd=Path(__file__).parents[1], stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            deadline = time.monotonic() + 15
            while not ready.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(0.02)
            assert ready.exists(), process.communicate(timeout=1)
            time.sleep(0.15)
            assert process.poll() is None and not done.exists()
        output, errors = process.communicate(timeout=20)
        assert process.returncode == 0, errors.decode(errors="replace")
        assert done.read_bytes() == b"done"
        assert (folder / "video.mp4").exists()
    finally:
        if process is not None and process.poll() is None:
            process.kill()
            process.communicate(timeout=10)
