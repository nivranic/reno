"""P05 storage facts on disposable SQLite databases and artificial files."""
from contextlib import contextmanager
import hashlib
import os
import sqlite3
import uuid

import pytest

from reno import storage_artifacts as artifacts, storage_schema as schema
from reno import storage_lifecycle as lifecycle, sync_recovery_schema, sync_transfer_schema, sync_schema
from reno.sync_protocol import SyncError


@pytest.fixture
def storage_env(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media",
                                       tmp_path / "reports", tmp_path / "checkpoints")
    lifecycle.initialize(profile)
    con = sqlite3.connect(profile.database)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
    con.execute("CREATE TABLE video_asset(video_id TEXT PRIMARY KEY,content_sha256 TEXT,files_json TEXT)")
    sync_transfer_schema.install(con)
    sync_recovery_schema.install(con)
    con.execute("CREATE TABLE sync_published_video (" + sync_schema.TABLES["sync_published_video"] + ")")
    schema.install(con)
    version = {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 0}
    con.executemany("INSERT INTO meta VALUES(?,?)", [(k, str(v)) for k, v in version.items()])
    con.commit()
    yield {"con": con, "profile": profile, "version": version, "tmp_path": tmp_path}
    con.close()


@contextmanager
def transaction(env):
    con = env["con"]
    with lifecycle.shared(env["profile"]):
        con.execute("BEGIN IMMEDIATE")
        try:
            yield con
        finally:
            if con.in_transaction:
                con.rollback()


def media_file(env, content=b"artificial media", video="BVTestStorage"):
    descriptor = {"video_id": video, "sha256": hashlib.sha256(content).hexdigest(), "byte_size": len(content), "extension": "mp4"}
    key = f"sync/{video}/{descriptor['sha256']}.mp4"
    path = env["profile"].media / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return descriptor, key, path


def register_media(env, descriptor, key):
    return artifacts.register(env["con"], profile=env["profile"], namespace="sync_media", root=env["profile"].media,
                              storage_key=key, sha256=descriptor["sha256"], byte_size=descriptor["byte_size"])


def test_schema_is_empty_idempotent_transactional_and_readonly_validated():
    con = sqlite3.connect(":memory:")
    con.execute("BEGIN")
    schema.install(con)
    schema.install(con)
    before = con.total_changes
    schema.validate(con)
    assert con.in_transaction and before == con.total_changes == 0
    assert {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")} == set(schema.TABLES)
    con.rollback()
    assert con.execute("SELECT count(*) FROM sqlite_master").fetchone()[0] == 0
    con.close()


@pytest.mark.parametrize("damage", ["check", "fk", "partial", "index", "column"])
def test_schema_rejects_contract_weakening(damage):
    con = sqlite3.connect(":memory:")
    declarations = dict(schema.TABLES)
    if damage == "check":
        declarations["storage_gc_operation"] = declarations["storage_gc_operation"].replace("CHECK(state IN ('planned','running','completed','blocked'))", "")
    elif damage == "fk":
        declarations["storage_gc_item"] = declarations["storage_gc_item"].replace(" REFERENCES storage_artifact(artifact_id)", "")
    elif damage == "column":
        declarations["storage_artifact"] = declarations["storage_artifact"].replace("root_id TEXT NOT NULL", "root_id BLOB NOT NULL")
    for table, columns in declarations.items():
        con.execute(f"CREATE TABLE {table} ({columns})")
    for sql in schema.INDEXES:
        if damage == "index" and "idx_upload_hold" in sql:
            continue
        if damage == "partial" and "idx_upload_publication" in sql:
            sql = sql.replace("WHERE kind='publication'", "WHERE kind='media'")
        con.execute(sql)
    with pytest.raises(sqlite3.DatabaseError, match="incompatible"):
        schema.validate(con)
    con.close()


@pytest.mark.parametrize("namespace,key", [
    ("sync_media", "../secret.mp4"), ("sync_media", "sync/CON/" + "a" * 64 + ".mp4"),
    ("sync_media", "sync/video/" + "a" * 64 + ".MP4"), ("sync_media", "sync/video/" + "a" * 64 + ".mp4:stream"),
    ("sync_media", "sync\\video\\file.mp4"), ("sync_media", "/sync/video/file.mp4"),
    ("local_report", "checklist.md"), ("local_report", ".versions/unknown-" + "a" * 32 + ".md"),
    ("sync_report", ".sync-versions/checklist-" + "A" * 64 + ".md"),
    ("checkpoint", "00000000000000000000000000000001.sqlite"), ("checkpoint", "backup.sqlite"),
    ("personal_attachment", "photo.jpg"),
])
def test_storage_key_allowlist_rejects_unknown_or_unsafe_names(namespace, key):
    with pytest.raises(SyncError):
        artifacts.validate_storage_key(namespace, key)


@pytest.mark.parametrize("namespace", artifacts.NAMESPACES)
def test_registration_all_namespaces_never_commits_and_preserves_original_file(storage_env, namespace):
    env, data = storage_env, b"artificial content"
    digest = hashlib.sha256(data).hexdigest()
    if namespace == "sync_media":
        root, key = env["profile"].media, f"sync/video/{digest}.mp4"
    elif namespace == "checkpoint":
        root, key = env["profile"].checkpoints, str(uuid.uuid4()) + ".sqlite"
    else:
        root = env["profile"].reports
        key = f".versions/checklist-{uuid.uuid4().hex}.md" if namespace == "local_report" else f".sync-versions/checklist-{digest}.md"
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    with transaction(env) as con:
        record = artifacts.register(con, profile=env["profile"], namespace=namespace, root=root,
                                    storage_key=key, sha256=digest, byte_size=len(data))
        assert artifacts.verify(con, record["artifact_id"], profile=env["profile"], root=root) == record
        assert artifacts.register(con, profile=env["profile"], namespace=namespace, root=root,
                                  storage_key=key, sha256=digest, byte_size=len(data)) == record
        assert con.in_transaction
        with sqlite3.connect(env["profile"].database) as observer:
            assert observer.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
    assert env["con"].execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0
    assert path.read_bytes() == data


def test_artifact_requires_guard_and_caller_transaction(storage_env):
    env = storage_env
    descriptor, key, _ = media_file(env)
    env["con"].execute("BEGIN")
    with pytest.raises(lifecycle.StorageLifecycleError):
        register_media(env, descriptor, key)
    env["con"].rollback()
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError) as caught:
        register_media(env, descriptor, key)
    assert caught.value.code == "STORAGE_TRANSACTION_REQUIRED"


def test_ephemeral_database_cannot_claim_durable_storage_ownership(storage_env):
    env = storage_env
    descriptor, key, _ = media_file(env)
    con = sqlite3.connect(":memory:")
    con.execute("BEGIN")
    schema.install(con)
    with lifecycle.shared(env["profile"]), pytest.raises(SyncError) as caught:
        artifacts.register(con, profile=env["profile"], namespace="sync_media", root=env["profile"].media,
                           storage_key=key, sha256=descriptor["sha256"], byte_size=descriptor["byte_size"])
    assert caught.value.code == "STORAGE_PROFILE_MISMATCH"
    con.close()


def test_retransmission_updates_identity_but_old_gc_candidate_stays_distinct(storage_env):
    env = storage_env
    descriptor, key, path = media_file(env)
    with transaction(env) as con:
        old = register_media(env, descriptor, key)
        con.commit()
    replacement = path.with_suffix(".replacement")
    replacement.write_bytes(path.read_bytes())
    os.replace(replacement, path)
    with transaction(env) as con:
        with pytest.raises(SyncError) as caught:
            artifacts.verify(con, old["artifact_id"], profile=env["profile"], root=env["profile"].media)
        assert caught.value.code == "STORAGE_ARTIFACT_CHANGED"
        new = register_media(env, descriptor, key)
        assert new["artifact_id"] == old["artifact_id"] and new["created_at"] == old["created_at"]
        assert tuple(new[k] for k in artifacts.IDENTITY_FIELDS) != tuple(old[k] for k in artifacts.IDENTITY_FIELDS)
        assert artifacts.verify(con, new["artifact_id"], profile=env["profile"], root=env["profile"].media) == new


def test_registered_report_key_cannot_be_adopted_for_different_content(storage_env):
    env = storage_env
    key = ".versions/prices-" + uuid.uuid4().hex + ".md"
    path = env["profile"].reports / key
    path.parent.mkdir()
    path.write_bytes(b"first")
    with transaction(env) as con:
        first = artifacts.register(con, profile=env["profile"], namespace="local_report", root=env["profile"].reports,
                                   storage_key=key, sha256=hashlib.sha256(b"first").hexdigest(), byte_size=5)
        path.write_bytes(b"other")
        con.execute("INSERT INTO meta VALUES('caller-write','keep')")
        with pytest.raises(SyncError) as caught:
            artifacts.register(con, profile=env["profile"], namespace="local_report", root=env["profile"].reports,
                               storage_key=key, sha256=hashlib.sha256(b"other").hexdigest(), byte_size=5)
        assert caught.value.code == "STORAGE_ARTIFACT_CONFLICT"
        assert artifacts.get(con, first["artifact_id"]) == first
        assert con.execute("SELECT value FROM meta WHERE key='caller-write'").fetchone()[0] == "keep"


@pytest.mark.parametrize("damage", ["hash", "size", "hardlink", "root", "namespace"])
def test_registration_refuses_unproved_file_identity(storage_env, damage):
    env = storage_env
    descriptor, key, path = media_file(env)
    arguments = {"profile": env["profile"], "namespace": "sync_media", "root": env["profile"].media,
                 "storage_key": key, "sha256": descriptor["sha256"], "byte_size": descriptor["byte_size"]}
    if damage == "hash":
        path.write_bytes(b"x" * descriptor["byte_size"])
    elif damage == "size":
        arguments["byte_size"] += 1
    elif damage == "hardlink":
        os.link(path, env["tmp_path"] / "external.mp4")
    elif damage == "root":
        arguments["root"] = env["profile"].reports
    else:
        arguments["namespace"] = "personal_attachment"
    with transaction(env) as con:
        with pytest.raises(SyncError):
            artifacts.register(con, **arguments)
        assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0


@pytest.mark.skipif(os.name == "nt", reason="Creating Windows symlinks needs separate OS privileges")
def test_registration_rejects_symlink_file_and_parent(storage_env):
    env = storage_env
    descriptor, key, path = media_file(env)
    outside = env["tmp_path"] / "outside"
    path.rename(outside)
    path.symlink_to(outside)
    with transaction(env), pytest.raises(SyncError):
        register_media(env, descriptor, key)
    path.unlink()
    path.parent.rmdir()
    outside_dir = env["tmp_path"] / "outside-dir"
    outside_dir.mkdir()
    (outside_dir / path.name).write_bytes(outside.read_bytes())
    path.parent.symlink_to(outside_dir, target_is_directory=True)
    with transaction(env), pytest.raises(SyncError):
        register_media(env, descriptor, key)


def test_verified_proof_hashes_outside_transaction_then_registers_without_another_scan(storage_env, monkeypatch):
    env = storage_env
    descriptor, key, _ = media_file(env)
    with lifecycle.shared(env["profile"]):
        proof = artifacts._inspect_for_registration(env["con"], profile=env["profile"], namespace="sync_media",
            root=env["profile"].media, storage_key=key, sha256=descriptor["sha256"], byte_size=descriptor["byte_size"],
            check_cancelled=lambda: assert_idle(env["con"]))
        monkeypatch.setattr(artifacts, "_inspect", lambda **kw: pytest.fail("Final write must not hash again"))
        env["con"].execute("BEGIN IMMEDIATE")
        row = artifacts._register_verified(env["con"], proof, profile=env["profile"])
        assert artifacts._validate_verified(env["con"], proof, profile=env["profile"]) == row
        env["con"].rollback()


def assert_idle(con):
    assert not con.in_transaction


@pytest.mark.parametrize("damage", ["replaced", "fabricated", "expired_guard", "active_transaction"])
def test_internal_proof_cannot_bypass_live_identity_and_guard_requirements(storage_env, damage):
    env = storage_env
    descriptor, key, path = media_file(env)
    arguments = {"profile": env["profile"], "namespace": "sync_media", "root": env["profile"].media,
                 "storage_key": key, "sha256": descriptor["sha256"], "byte_size": descriptor["byte_size"]}
    with lifecycle.shared(env["profile"]):
        if damage == "active_transaction":
            env["con"].execute("BEGIN")
            with pytest.raises(SyncError) as caught:
                artifacts._inspect_for_registration(env["con"], **arguments)
            assert caught.value.code == "STORAGE_TRANSACTION_ACTIVE"
            env["con"].rollback()
            return
        proof = artifacts._inspect_for_registration(env["con"], **arguments)
        if damage == "replaced":
            alternative = path.with_suffix(".replacement")
            alternative.write_bytes(path.read_bytes())
            os.replace(alternative, path)
        if damage != "expired_guard":
            env["con"].execute("BEGIN IMMEDIATE")
            with pytest.raises(SyncError):
                artifacts._register_verified(env["con"], dict(proof.record) if damage == "fabricated" else proof, profile=env["profile"])
            env["con"].rollback()
            return
    with lifecycle.shared(env["profile"]):
        env["con"].execute("BEGIN IMMEDIATE")
        with pytest.raises(SyncError) as caught:
            artifacts._register_verified(env["con"], proof, profile=env["profile"])
        assert caught.value.code == "STORAGE_PROOF_INVALID"
        env["con"].rollback()


def test_media_hold_proof_expires_when_original_verified_handles_close(storage_env):
    from reno.sync_media import MediaStore
    env = storage_env
    descriptor, key, _ = media_file(env)
    with transaction(env) as con:
        registered = register_media(env, descriptor, key)
        with MediaStore(env["profile"].media).hold_verified([descriptor]) as held:
            proof = artifacts._from_media_hold(con, held, descriptor, profile=env["profile"])
            assert artifacts._validate_verified(con, proof, profile=env["profile"]) == registered
        with pytest.raises(SyncError) as caught:
            artifacts._validate_verified(con, proof, profile=env["profile"])
        assert caught.value.code == "STORAGE_PROOF_INVALID"
