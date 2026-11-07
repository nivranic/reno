"""Normal17 personal mutations cooperate with real lifecycle EX ownership."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

import pytest

from reno import config, db, migrations, personal, storage_admin, sync_protocol
from reno import storage_lifecycle as lifecycle


@pytest.fixture
def bare_library(tmp_path, monkeypatch):
    root = tmp_path / "personal"
    for name, path in {"DATA": root / "data", "MEDIA": root / "media", "ORIG": root / "media/originals",
                       "FRAMES": root / "media/frames", "AUDIO": root / "media/audio",
                       "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setenv("RENO_DATA_DIR", str(root / "data"))
    config.set_local({"personal_features_enabled": True, "field_checks_enabled": True, "node_role": "all"})
    assert migrations.SCHEMA_VERSION == 17 and sync_protocol.PROTOCOL_VERSION == 3
    profile = storage_admin.profile()
    assert db.initialize(profile.database).version == 17
    con = db.connect(profile.database)
    try:
        project = personal.default_project(con)["project"]
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"
    finally:
        con.close()
    return profile, project


@pytest.fixture
def library(bare_library):
    profile, _project = bare_library
    lifecycle.initialize(profile)
    return bare_library


def envelope(changes, *, base=1, epoch=1, operation_id=None):
    return {"operation_id": operation_id or str(uuid.uuid4()), "primary_epoch": epoch,
            "base_revision": base, "changes": changes}


def current(library):
    profile, project = library
    with storage_admin.connection(profile) as con:
        row = tuple(con.execute("SELECT name,revision FROM renovation_project WHERE project_id=?",
                                (project["project_id"],)).fetchone())
        receipts = [tuple(item) for item in con.execute("SELECT * FROM mutation_receipt ORDER BY operation_id")]
        revisions = [tuple(item) for item in con.execute("SELECT * FROM personal_revision ORDER BY operation_id")]
        return row, receipts, revisions


_CHILD = """
import json, pathlib, sys
from reno import db, personal, storage_admin, storage_lifecycle as lifecycle
arg = json.loads(sys.argv[1])
profile = lifecycle.StorageProfile(**arg['profile'])
assert storage_admin.profile().identity == profile.identity
original = db.connect
def guarded_connect(path=None):
    lifecycle.require_guard(profile)
    assert pathlib.Path(path) == profile.database
    pathlib.Path(arg['connected']).write_text('connected', encoding='ascii')
    return original(path)
db.connect = guarded_connect
pathlib.Path(arg['ready']).write_text('attempting', encoding='ascii')
result = personal.update_project(arg['project_id'], arg['envelope'])
pathlib.Path(arg['result']).write_text(json.dumps(result), encoding='utf-8')
"""


def test_real_ex_blocks_mutation_before_sql_and_releases_for_commit(library, tmp_path):
    profile, project = library
    ready, connected, result = (tmp_path / name for name in ("ready", "connected", "result"))
    body = envelope({"name": "guarded project"})
    arguments = {"profile": {name: str(getattr(profile, name)) for name in
                            ("database", "media", "reports", "checkpoints")},
                 "project_id": project["project_id"], "envelope": body,
                 "ready": str(ready), "connected": str(connected), "result": str(result)}
    child = None
    try:
        with lifecycle.exclusive(profile):
            child = subprocess.Popen([sys.executable, "-B", "-u", "-c", _CHILD, json.dumps(arguments)],
                cwd=Path(__file__).resolve().parents[1], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            deadline = time.monotonic() + 15
            while not ready.exists():
                if child.poll() is not None:
                    pytest.fail(child.communicate()[0].decode("utf-8", "replace"))
                assert time.monotonic() < deadline, "Personal writer child failed to reach its real mutation"
                time.sleep(0.02)
            time.sleep(0.15)
            assert child.poll() is None and not connected.exists() and not result.exists()
        output, _ = child.communicate(timeout=15)
        assert child.returncode == 0, output.decode("utf-8", "replace")
        assert connected.exists() and result.exists()
        row, receipts, _revisions = current(library)
        assert row == ("guarded project", 2) and len(receipts) == 1
        assert json.loads(result.read_text(encoding="utf-8"))[0]["operation_id"] == body["operation_id"]
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate(timeout=5)


@pytest.mark.parametrize("failure", [None, "callback", "commit", "rollback"])
def test_guard_covers_commit_rollback_and_close_without_losing_primary_error(library, monkeypatch, failure):
    profile, project = library
    before = current(library)
    class Cancelled(BaseException):
        pass
    cancellation = Cancelled("synthetic personal cancellation")
    original_connect, seen = db.connect, []
    class Connection:
        def __init__(self, con):
            self.con = con
        def __getattr__(self, name):
            return getattr(self.con, name)
        def commit(self):
            lifecycle.require_guard(profile)
            assert self.con.in_transaction
            seen.append("commit")
            if failure == "commit":
                raise cancellation
            return self.con.commit()
        def rollback(self):
            lifecycle.require_guard(profile)
            seen.append("rollback")
            if failure == "rollback":
                raise RuntimeError("synthetic rollback failure")
            return self.con.rollback()
        def close(self):
            lifecycle.require_guard(profile)
            seen.append("close")
            return self.con.close()
    def guarded_connect(path=None):
        lifecycle.require_guard(profile)
        assert Path(path) == profile.database
        seen.append("open")
        return Connection(original_connect(path))
    def callback(con, changes, base, operation_id):
        lifecycle.require_guard(profile)
        seen.append("callback")
        con.execute("UPDATE renovation_project SET name='transaction changed' WHERE project_id=?", (project["project_id"],))
        if failure in {"callback", "rollback"}:
            raise cancellation
        return {"saved": True}
    with monkeypatch.context() as scoped:
        scoped.setattr(db, "connect", guarded_connect)
        if failure is None:
            personal.mutate("test:personal-lifetime", envelope({}), callback)
        else:
            with pytest.raises(Cancelled) as caught:
                personal.mutate("test:personal-lifetime", envelope({}), callback)
            assert caught.value is cancellation
    expected = ["open", "callback", "commit", "close"] if failure is None else (
        ["open", "callback", "commit", "rollback", "close"] if failure == "commit" else
        ["open", "callback", "rollback", "close"])
    assert seen == expected and lifecycle.current_guard() is None
    with lifecycle.exclusive(profile, timeout=0) as guard:
        guard.verify()
    if failure is not None:
        assert current(library) == before
    else:
        assert current(library)[0][0] == "transaction changed" and len(current(library)[1]) == 1


def test_missing_facilities_refuse_before_any_sql_or_initialization(bare_library, monkeypatch):
    profile, project = bare_library
    before = sorted(str(path) for path in profile.database.parent.parent.rglob("*"))
    def forbidden(*args, **kwargs):
        pytest.fail("Mutation must not open SQL or initialize absent lifecycle facilities")
    monkeypatch.setattr(db, "connect", forbidden)
    monkeypatch.setattr(lifecycle, "initialize", forbidden)
    with pytest.raises(personal.PersonalError) as caught:
        personal.update_project(project["project_id"], envelope({"name": "must not write"}))
    assert caught.value.code == "STORAGE_NOT_INITIALIZED"
    assert caught.value.status == 503
    assert sorted(str(path) for path in profile.database.parent.parent.rglob("*")) == before


def test_mutation_borrows_same_profile_guard_without_opening_another_lifecycle_lock(library, monkeypatch):
    profile, _project = library
    with lifecycle.shared(profile) as held:
        def forbidden(*args, **kwargs):
            pytest.fail("An existing same-profile guard must be borrowed")
        monkeypatch.setattr(lifecycle, "_open_lock", forbidden)
        def callback(con, changes, base, operation_id):
            assert lifecycle.require_guard(profile) is held
            return {"saved": True}
        personal.mutate("test:borrowed-personal-lifetime", envelope({}), callback)
        assert lifecycle.require_guard(profile) is held
    assert lifecycle.current_guard() is None


def test_wrong_profile_guard_refuses_mutation_before_sql(library, tmp_path, monkeypatch):
    _profile, project = library
    other = lifecycle.StorageProfile(tmp_path / "other/data/reno.db", tmp_path / "other/media",
                                     tmp_path / "other/reports", tmp_path / "other/checkpoints")
    db.initialize(other.database)
    lifecycle.initialize(other)
    with lifecycle.shared(other):
        monkeypatch.setattr(db, "connect", lambda *args, **kwargs: pytest.fail("Wrong guard reached SQL"))
        with pytest.raises(personal.PersonalError) as caught:
            personal.update_project(project["project_id"], envelope({"name": "must not write"}))
        assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
        assert caught.value.status == 409


def test_original_receipt_replay_precedes_cas_but_never_precedes_epoch_gate(library):
    profile, project = library
    original = envelope({"name": "original project"})
    first = personal.update_project(project["project_id"], original)
    personal.update_project(project["project_id"], envelope({"name": "latest project"}, base=2))
    before = current(library)
    assert personal.update_project(project["project_id"], original) == first
    assert current(library) == before
    with pytest.raises(personal.PersonalError) as caught:
        personal.update_project(project["project_id"], {**original, "changes": {"name": "another request"}})
    assert caught.value.code == "IDEMPOTENCY_KEY_REUSED"
    with storage_admin.connection(profile) as con:
        con.execute("UPDATE meta SET value='2' WHERE key='primary_epoch'")
        con.commit()
    with pytest.raises(personal.PersonalError) as caught:
        personal.update_project(project["project_id"], original)
    assert caught.value.code == "PRIMARY_CHANGED"
    assert current(library) == before
    with lifecycle.exclusive(profile, timeout=0) as guard:
        guard.verify()
