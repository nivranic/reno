"""Backup graph fixtures are real isolated backup productions, never live data."""
import hashlib
import json
from pathlib import Path
from contextlib import contextmanager
import sqlite3
import uuid

import pytest

from reno import db, migrations, storage_artifacts as artifacts, storage_backups as backups
from reno import storage_backup_roots as roots, storage_gc_contract as contract, storage_lifecycle as lifecycle
from reno import sync_upload_pins as pins

SHA = "a" * 64
OTHER_SHA = "b" * 64
STAMP = "2026-10-01T00:00:00.000Z"


def environment(folder, *, name="reno.db", version=migrations.SCHEMA_VERSION, data=None):
    data = data or folder / "data"
    data.mkdir(parents=True, exist_ok=True)
    profile = lifecycle.StorageProfile(data / name, folder / "media", folder / "reports", folder / "checkpoints")
    if version == migrations.SCHEMA_VERSION:
        db.initialize(profile.database)
        con = sqlite3.connect(profile.database)
        library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    elif version == 10:
        # A genuine pre-budget schema10 image, not current live DDL relabelled.
        from reno import personal_schema, sync_schema, sync_transfer_schema
        from reno import sync_checkpoint_schema, sync_recovery_schema, storage_schema
        con = sqlite3.connect(profile.database)
        con.executescript(migrations.SCHEMA_PATH.read_text(encoding="utf-8"))
        for module in (personal_schema, sync_schema, sync_transfer_schema,
                       sync_checkpoint_schema, sync_recovery_schema, storage_schema):
            module.install(con)
        library = str(uuid.uuid4())
        con.executemany("INSERT INTO meta VALUES(?,?)", [("migration_version", "10"), ("library_id", library),
                       ("library_epoch", "1"), ("kb_revision", "0")])
        con.commit()
    else:
        con = sqlite3.connect(profile.database)
        con.executescript("""CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE video_asset(video_id TEXT,files_json TEXT,content_sha256 TEXT);
            CREATE TABLE frame(video_id TEXT,path TEXT);
            CREATE TABLE job(video_id TEXT,kind TEXT,status TEXT);
            CREATE TABLE sync_queue(video_id TEXT,status TEXT);""")
        library = str(uuid.uuid4())
        con.executemany("INSERT INTO meta VALUES(?,?)", [("migration_version", str(version)), ("library_id", library),
                       ("library_epoch", "1"), ("kb_revision", "0")])
        if version >= 6:
            con.execute("CREATE TABLE sync_report(storage_key TEXT,sha256 TEXT,byte_size INTEGER)")
        con.commit()
    con.close()
    backups.initialize_offline(profile)
    con = sqlite3.connect(profile.database)
    with lifecycle.shared(profile):
        root_ids = {namespace: artifacts.root_identity(profile.media if namespace == "sync_media" else
            profile.checkpoints if namespace == "checkpoint" else profile.reports, profile=profile)["root_id"]
            for namespace in artifacts.NAMESPACES}
    return {"profile": profile, "con": con, "library_id": library, "node_id": str(uuid.uuid4()),
            "member_id": "mem_" + "c" * 64, "root_ids": root_ids}


@pytest.fixture
def env(tmp_path):
    value = environment(tmp_path / "source")
    yield value
    value["con"].close()


def options(env, **extra):
    return {"root_ids": env["root_ids"], "member_id": env["member_id"], "library_id": env["library_id"],
            "node_id": env["node_id"], "policy": contract.default_policy(), **extra}


def scan(env, **extra):
    with lifecycle.shared(env["profile"]):
        return roots.scan_locked(env["profile"], **options(env, **extra))


def test_budget_wait_keeps_bound_video_root_in_registered_backup(env):
    env["con"].execute("INSERT INTO sync_queue(url,status,video_id,budget_wait_reason) "
                       "VALUES('https://b23.tv/private','budget_wait','waiting-video','indeterminate')")
    env["con"].commit()
    create(env)
    result = scan(env)
    assert not result.scan.blockers
    assert {item.video_id for item in result.scan.selectors} == {"waiting-video"}


def asset(env, *, video="video", sha=SHA, fields=None):
    fields = fields or {"video": f"media/sync/{video}/{sha}.mp4"}
    env["con"].execute("INSERT INTO video_asset(video_id,files_json,content_sha256) VALUES (?,?,?)",
                       (video, json.dumps(fields), sha))
    env["con"].commit()


def create(env, *, external=None, name=None):
    profile = env["profile"]
    destination = external or profile.database.parent / (name or (profile.database.name + ".bak-" + uuid.uuid4().hex))
    return backups.create(env["con"], destination, profile=profile, kind="manual")


def catalog(env, created):
    path = env["profile"].database.parent / backups.CATALOG / (created["backup_id"] + ".json")
    return path, json.loads(path.read_text(encoding="utf-8"))


def copy_image(env, destination):
    destination.parent.mkdir(parents=True, exist_ok=True)
    target = sqlite3.connect(destination)
    try:
        env["con"].backup(target)
    finally:
        target.close()


def seed_retirement(env, *, preserve_unowned=False):
    con = env["con"]
    def insert(table, values):
        con.execute(f"INSERT INTO {table}({','.join(values)}) VALUES({','.join('?' for _ in values)})", tuple(values.values()))
    checkpoint_id, publication_id, operation_id = (str(uuid.uuid4()) for _ in range(3))
    version = {"library_id": env["library_id"], "library_epoch": 1, "kb_revision": 0}
    entry = {"checkpoint_id": checkpoint_id, "publication_id": publication_id,
        "receiver_version_json": json.dumps(version), "format_version": 1, "schema_version": 9,
        "storage_key": checkpoint_id + ".sqlite", "sha256": hashlib.sha256(b"artificial archive").hexdigest(),
        "byte_size": 18, "created_at": STAMP}
    insert("sync_publication", {"publication_id": publication_id, "source_library_id": str(uuid.uuid4()),
        "source_node_id": str(uuid.uuid4()), "source_epoch": 1, "source_revision": 0,
        "payload_sha256": SHA, "knowledge_sha256": OTHER_SHA, "committed_at": STAMP,
        "local_version_json": json.dumps(version)})
    insert("sync_checkpoint", entry)
    policy = contract.default_policy()
    info = env["profile"].database.stat()
    root_info = env["profile"].checkpoints.stat()
    item = {"namespace": "checkpoint", "root_id": env["root_ids"]["checkpoint"],
        "root_device": str(root_info.st_dev), "root_inode": str(root_info.st_ino),
        "storage_key": entry["storage_key"], "sha256": entry["sha256"], "byte_size": entry["byte_size"],
        "file_device": str(root_info.st_dev), "file_inode": "20", "file_mtime_ns": "42", "file_ctime_ns": "43",
        "intent": "delete_if_unreferenced"}
    item["artifact_id"] = "art_" + contract.digest({k: item[k] for k in ("namespace", "root_id", "storage_key")})
    request = {"format_version": 1, "action": "storage_gc", "operation_id": operation_id,
        "owner": {"member_id": env["member_id"], "library_id": env["library_id"],
                  "database_identity": {"device": str(info.st_dev), "inode": str(info.st_ino)}},
        "expected_preview_sha256": "d" * 64, "expected_roots_sha256": "e" * 64, "members_sha256": "f" * 64,
        "policy_sha256": contract.digest(policy), "files": [] if preserve_unowned else [item],
        "retirements": [contract.checkpoint_descriptor(entry, root_id=env["root_ids"]["checkpoint"],
                        archive_item_id=None if preserve_unowned else item["artifact_id"])]}
    contract.validate_request(request, policy=policy)
    insert("storage_gc_operation", {"operation_id": operation_id, "request_sha256": contract.digest(request),
        "preview_sha256": request["expected_preview_sha256"], "roots_sha256": request["expected_roots_sha256"],
        "policy_json": contract.canonical_bytes(policy).decode(), "request_json": contract.canonical_bytes(request).decode(),
        "result_json": None, "state": "blocked", "created_at": STAMP, "updated_at": STAMP})
    if not preserve_unowned:
        insert("storage_artifact", {**{k: v for k, v in item.items() if k != "intent"}, "created_at": STAMP, "updated_at": STAMP})
        insert("storage_gc_item", {"operation_id": operation_id, **{k: item[k] for k in contract._ITEM_IDENTITY},
            "state": "pending", "deleted_bytes": 0, "reason_code": None, "updated_at": STAMP})
    insert("sync_checkpoint_retirement", {"origin_kind": "publication", "checkpoint_id": checkpoint_id,
        "operation_id": operation_id, "storage_key": entry["storage_key"], "sha256": entry["sha256"],
        "byte_size": entry["byte_size"], "retired_at": STAMP})
    con.commit()
    return entry


def empty_archive(env):
    from reno import sync_checkpoints as checkpoints
    checkpoint_id, publication_id = str(uuid.uuid4()), str(uuid.uuid4())
    version = {"library_id": env["library_id"], "library_epoch": 1, "kb_revision": 0}
    entry = {"checkpoint_id": checkpoint_id, "publication_id": publication_id, "receiver_version_json": json.dumps(version),
             "format_version": 1, "schema_version": 9, "storage_key": checkpoint_id + ".sqlite", "created_at": STAMP}
    a = checkpoints._archive_connection()
    try:
        for statement in checkpoints._ddl(9).values():
            a.execute(statement)
        manifest = {k: v for k, v in entry.items() if k not in ("receiver_version_json", "storage_key")}
        manifest.update(receiver_version=version, receiver_node_id=env["node_id"],
                        row_counts={name: 0 for name in checkpoints.KNOWLEDGE_COLUMNS}, issues=[])
        a.execute("INSERT INTO checkpoint_manifest(slot,manifest_json) VALUES(1,?)", (checkpoints._json(manifest),))
        a.commit()
        raw = a.serialize()
    finally:
        a.close()
    entry.update(sha256=hashlib.sha256(raw).hexdigest(), byte_size=len(raw))
    (env["profile"].checkpoints / entry["storage_key"]).write_bytes(raw)
    con = env["con"]
    con.execute("INSERT INTO sync_publication VALUES (?,?,?,?,?,?,?,?,?)", (publication_id, str(uuid.uuid4()),
        str(uuid.uuid4()), 1, 0, SHA, OTHER_SHA, STAMP, json.dumps({**version, "kb_revision": 1})))
    con.execute(f"INSERT INTO sync_checkpoint({','.join(entry)}) VALUES({','.join('?' for _ in entry)})", tuple(entry.values()))
    con.commit()
    return entry


def tree(folder):
    return {str(p.relative_to(folder)): (p.stat().st_mtime_ns, p.read_bytes() if p.is_file() else None)
            for p in folder.rglob("*")}


def test_ready_registered_backup_rebuilds_all_fields_frame_queue_and_pin_roots(env):
    asset(env, fields={"video": f"media/sync/video/{SHA}.mp4", "audio": f"media/sync/video/{OTHER_SHA}.mp4"})
    con = env["con"]
    con.execute("UPDATE video_asset SET sync_state='unready'")
    con.execute("INSERT INTO frame(video_id,frame_id,path) VALUES ('video','f',?)", (f"media/sync/video/{'d'*64}.mp4",))
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES ('queued','process','pending','{}')")
    con.execute("INSERT INTO sync_queue(url,video_id,status) VALUES ('https://b23.tv/private?token=PRIVATE','remote','claimed')")
    binding = {"publication_id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()),
        "source_version": {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 1},
        "recovery_stream": {"library_id": env["library_id"], "library_epoch": 1}, "recovery_generation": 0}
    descriptor = {"video_id": "pinned", "sha256": "e" * 64, "byte_size": 2, "extension": "webm"}
    parent, child = pins._record(binding), pins._record(binding, descriptor=descriptor)
    child["parent_pin_id"] = parent["pin_id"]
    for row in (parent, child):
        con.execute(f"INSERT INTO sync_upload_pin({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    con.commit()
    create(env)
    result = scan(env)
    assert not result.scan.blockers
    assert {r.storage_key for r in result.scan.references} == {
        f"sync/video/{SHA}.mp4", f"sync/video/{OTHER_SHA}.mp4", f"sync/video/{'d'*64}.mp4", f"sync/pinned/{'e'*64}.webm"}
    assert {s.video_id for s in result.scan.selectors} == {"queued", "remote"}


@pytest.mark.parametrize("relative", ["reno.db.bak-old", "maintenance-old/before/reno.db",
                                      "maintenance-old/reno-before-schema6.db", "service-old/reno-before-reload.db"])
def test_each_builtin_is_found_and_legacy_manifest_only_blocks_local_report(env, relative):
    asset(env)
    destination = env["profile"].database.parent / relative
    destination.parent.mkdir(parents=True, exist_ok=True)
    copy_image(env, destination)
    result = scan(env)
    assert len(result.semantic_bindings) == 1
    assert {b.namespace for b in result.scan.blockers} == {"local_report"}
    assert any(r.storage_key == f"sync/video/{SHA}.mp4" for r in result.scan.references)


def test_external_registered_destination_is_included_and_public_projection_is_private(env, tmp_path):
    asset(env)
    external = tmp_path / "sensitive-backup-path" / "private.db"
    external.parent.mkdir()
    create(env, external=external)
    result = scan(env)
    assert not result.scan.blockers and result.scan.references
    assert b"sensitive-backup-path" not in result.scan.canonical_bytes()
    assert str(external) not in json.dumps(result.scan.public_summary())


@pytest.mark.parametrize("version", list(range(7)))
def test_historical_schema_zero_through_six_remains_bounded_and_readable(tmp_path, version):
    env = environment(tmp_path / "old", version=version)
    try:
        asset(env)
        env["con"].execute("INSERT INTO frame(video_id,path) VALUES ('video',?)", (f"media/sync/video/{OTHER_SHA}.mp4",))
        env["con"].execute("INSERT INTO job(video_id,kind,status) VALUES ('work','process','pending')")
        env["con"].commit()
        create(env)
        result = scan(env)
        assert not result.scan.blockers
        assert len(result.scan.references) == 2 and result.scan.selectors[0].video_id == "work"
    finally:
        env["con"].close()


@pytest.mark.parametrize("replacement", ["CREATE VIEW frame AS SELECT 'video' AS video_id,'media/sync/video/x.mp4' AS path",
                                        "CREATE VIRTUAL TABLE frame USING fts5(video_id,path)",
                                        "CREATE TABLE frame(video_id TEXT,path TEXT GENERATED ALWAYS AS (video_id))"])
def test_legacy_unsafe_sql_is_not_an_empty_safe_graph(tmp_path, replacement):
    env = environment(tmp_path / "old", version=0)
    try:
        asset(env)
        env["con"].execute("DROP TABLE frame")
        env["con"].execute(replacement)
        env["con"].commit()
        destination = env["profile"].database.with_name("reno.db.bak-unsafe")
        copy_image(env, destination)
        result = scan(env)
        assert "sync_media" in {b.namespace for b in result.scan.blockers}
    finally:
        env["con"].close()


@pytest.mark.parametrize("companion", ["-wal", "-journal"])
def test_unmaterialized_companion_blocks_four_namespaces_without_recovery(env, companion):
    asset(env)
    target = env["profile"].database.with_name("reno.db.bak-incomplete")
    copy_image(env, target)
    Path(str(target) + companion).write_bytes(b"synthetic unmaterialized journal")
    before = tree(env["profile"].database.parent.parent)
    result = scan(env)
    assert {b.namespace for b in result.scan.blockers} == set(artifacts.NAMESPACES)
    assert tree(env["profile"].database.parent.parent) == before


def test_missing_ready_backup_keeps_known_descriptor_dependencies(env):
    asset(env)
    created = create(env)
    created["path"].unlink()
    result = scan(env)
    assert {b.namespace for b in result.scan.blockers} == set(artifacts.NAMESPACES)
    assert any(r.storage_key == f"sync/video/{SHA}.mp4" for r in result.scan.references)


@pytest.mark.parametrize("state", ["reserved", "retiring"])
def test_unfinished_catalog_states_cannot_release_dependencies(env, state):
    asset(env)
    created = create(env)
    path, record = catalog(env, created)
    record["state"] = state
    if state == "reserved":
        record.pop("descriptor")
        record.pop("descriptor_sha256")
    else:
        record.update(retire_operation_id=str(uuid.uuid4()), retire_parameters={"kind": "manual", "keep": 1},
            retire_files=[{"path": backups._key(p), "identity": backups._read(p, content=False)[1]}
                          for p in (created["path"], created["descriptor_path"])])
    path.write_bytes(backups._json(record))
    result = scan(env)
    assert {b.namespace for b in result.scan.blockers} == set(artifacts.NAMESPACES)
    if state == "retiring":
        assert any(r.storage_key == f"sync/video/{SHA}.mp4" for r in result.scan.references)


@pytest.mark.parametrize("change", ["catalog", "descriptor", "new_builtin"])
def test_whole_round_discovery_and_descriptor_changes_are_retriable(env, change):
    asset(env)
    created = create(env)
    profile = env["profile"]
    with lifecycle.shared(profile):
        with pytest.raises(roots.BackupRootError) as caught:
            with roots.capture_locked(profile, **options(env)) as capture:
                if change == "catalog":
                    path, _ = catalog(env, created)
                    path.write_bytes(path.read_bytes() + b"\n")
                elif change == "descriptor":
                    path = created["descriptor_path"]
                    path.write_bytes(path.read_bytes() + b"\n")
                else:
                    copy_image(env, profile.database.with_name("reno.db.bak-later"))
                capture.verify_source()
        assert caught.value.code == "SOURCE_CHANGED" and caught.value.retriable


def test_foreign_same_data_catalog_preserves_fingerprint_without_borrowing_roots(env, tmp_path):
    other = environment(tmp_path / "other", name="other.db", data=env["profile"].database.parent)
    try:
        asset(other, video="foreign")
        created = create(other, name="reno.db.bak-foreign-producer.db")
        own = scan(env)
        assert not own.scan.references and not own.scan.blockers
        assert created["path"] in own.protected_paths
        assert created["descriptor_path"] in own.protected_paths
        assert Path(str(created["path"]) + "-shm") in own.protected_paths
        foreign = scan(other)
        assert foreign.scan.references and all(r.root_id == other["root_ids"]["sync_media"] for r in foreign.scan.references)
        profile = env["profile"]
        with lifecycle.shared(profile):
            with pytest.raises(roots.BackupRootError):
                with roots.capture_locked(profile, **options(env)) as capture:
                    path, _ = catalog(other, created)
                    path.write_bytes(path.read_bytes() + b"\n")
                    capture.verify_source()
    finally:
        other["con"].close()


@pytest.mark.parametrize("limited", [False, True])
def test_foreign_catalog_source_database_and_companions_are_protected(env, tmp_path, monkeypatch, limited):
    other = environment(tmp_path / "other", name=str(uuid.uuid4()) + ".sqlite", data=env["profile"].database.parent)
    try:
        asset(other, video="foreign")
        create(other)
        if limited:
            create(env)
            create(env)
        original = backups._read
        def read(path, **kwargs):
            assert path != other["profile"].database, "Foreign source databases are private exclusions, never scan inputs"
            return original(path, **kwargs)
        monkeypatch.setattr(backups, "_read", read)
        own = scan(env, **({"limits": roots.BackupLimits(max_backups=1)} if limited else {}))
        assert not own.scan.references
        assert bool(own.scan.blockers) == limited
        for suffix in ("", backups.SUFFIX, "-wal", "-shm", "-journal"):
            assert Path(str(other["profile"].database) + suffix) in own.protected_paths
    finally:
        other["con"].close()


def test_original_backup_retirement_is_verified_without_readding_descriptor_edges(env):
    seed_retirement(env)
    created = create(env)
    descriptor = json.loads(created["descriptor_path"].read_text(encoding="utf-8"))
    assert "retirement_unproven" in descriptor["blockers"]["checkpoint"]
    assert any(d["namespace"] == "checkpoint" for d in descriptor["dependencies"])
    result = scan(env)
    assert not any(r.namespace == "checkpoint" for r in result.scan.references)
    assert not any("RETIREMENT_UNPROVEN" in b.code for b in result.scan.blockers)
    assert not result.scan.blockers


def test_old_backup_cannot_borrow_current_retirement_proof(env):
    entry = seed_retirement(env)
    con = env["con"]
    retired = con.execute("SELECT * FROM sync_checkpoint_retirement").fetchall()
    operations = con.execute("SELECT * FROM storage_gc_operation").fetchall()
    con.execute("DELETE FROM sync_checkpoint_retirement")
    con.execute("DELETE FROM storage_gc_operation")
    con.commit()
    create(env)
    con.executemany("INSERT INTO storage_gc_operation VALUES (" + ",".join("?" for _ in operations[0]) + ")", operations)
    con.executemany("INSERT INTO sync_checkpoint_retirement VALUES (" + ",".join("?" for _ in retired[0]) + ")", retired)
    con.commit()
    result = scan(env)
    assert any(r.namespace == "checkpoint" and r.storage_key == entry["storage_key"] for r in result.scan.references)


def test_retired_unowned_checkpoint_preserves_only_explicit_physical_hold(env):
    entry = seed_retirement(env, preserve_unowned=True)
    create(env)
    result = scan(env)
    assert not result.scan.blockers
    nodes = [n for graph in result.checkpoint_graphs for n in graph.nodes]
    assert len(nodes) == 1 and nodes[0].retirement_status == "proven"
    assert not nodes[0].logical_recovery_edge and nodes[0].physical_archive_hold
    assert any(r.storage_key == entry["storage_key"] for r in result.scan.references)


def test_each_frozen_retirement_is_resolved_individually(env):
    proven = seed_retirement(env)
    unproven = seed_retirement(env)
    env["con"].execute("UPDATE storage_gc_operation SET request_sha256=? WHERE operation_id="
        "(SELECT operation_id FROM sync_checkpoint_retirement WHERE checkpoint_id=?)", ("0" * 64, unproven["checkpoint_id"]))
    env["con"].commit()
    create(env)
    result = scan(env)
    keys = {r.storage_key for r in result.scan.references if r.namespace == "checkpoint"}
    assert proven["storage_key"] not in keys and unproven["storage_key"] in keys
    assert any(b.code == "CHECKPOINT_RETIREMENT_UNPROVEN" for b in result.scan.blockers)


def test_backup_uses_its_frozen_manifest_instead_of_current_report(env):
    profile = env["profile"]
    old_key = ".versions/prices-" + "a" * 32 + ".md"
    new_key = ".versions/prices-" + "b" * 32 + ".md"
    manifest = profile.reports / "prices.manifest.json"
    manifest.write_text(json.dumps({"report_type": "prices", "status": "latest", "content_file": old_key,
                                    "content_sha256": SHA}), encoding="utf-8")
    create(env)
    manifest.write_text(json.dumps({"report_type": "prices", "status": "latest", "content_file": new_key,
                                    "content_sha256": OTHER_SHA}), encoding="utf-8")
    result = scan(env)
    assert not result.scan.blockers
    keys = {r.storage_key for r in result.scan.references if r.namespace == "local_report"}
    assert keys == {old_key}


def test_byte_and_sql_row_budgets_are_blockers_not_empty_eligibility(env):
    asset(env)
    create(env)
    assert scan(env, limits=roots.BackupLimits(max_image_bytes=1)).scan.blockers
    from reno.storage_root_inputs import InputLimits
    assert scan(env, limits=roots.BackupLimits(input_limits=InputLimits(max_rows=1))).scan.blockers


def test_capture_never_initializes_or_touches_backup_shm(env):
    asset(env)
    created = create(env)
    shm = Path(str(created["path"]) + "-shm")
    shm.write_bytes(b"SHM MUST NOT BE OPENED")
    before = tree(env["profile"].database.parent.parent)
    result = scan(env)
    assert not result.scan.blockers
    assert tree(env["profile"].database.parent.parent) == before
    assert shm in result.protected_paths
    assert env["profile"].database in result.protected_paths
    assert Path(str(env["profile"].database) + "-wal") in result.protected_paths


def test_backup_guard_is_the_original_profile_not_a_fake_external_database(env, tmp_path, monkeypatch):
    asset(env)
    external = tmp_path / "outside.db"
    create(env, external=external)
    original = lifecycle.require_guard
    calls = []
    def require(profile):
        calls.append(profile.identity)
        return original(profile)
    monkeypatch.setattr(lifecycle, "require_guard", require)
    result = scan(env)
    assert not result.scan.blockers and calls and set(calls) == {env["profile"].identity}


@pytest.mark.parametrize("call_number", [1, 10])
def test_one_shot_valueerror_cancellation_preserves_original_exception(env, call_number):
    asset(env)
    create(env)
    cancellation = ValueError("private cancellation reason")
    calls = 0
    def cancel():
        nonlocal calls
        calls += 1
        if calls == call_number:
            raise cancellation
    with pytest.raises(ValueError) as caught:
        scan(env, check_cancelled=cancel)
    assert caught.value is cancellation


def test_profile_root_bindings_cannot_be_forged_even_under_valid_guard(env):
    with lifecycle.shared(env["profile"]):
        wrong = {**env["root_ids"], "sync_media": "0" * 64}
        with pytest.raises(roots.BackupRootError) as caught:
            with roots.capture_locked(env["profile"], **options(env, root_ids=wrong)):
                pass
        assert caught.value.code == "BACKUP_ROOT_BINDING_MISMATCH"


def test_backup_bytes_changed_after_projection_invalidates_whole_round(env):
    asset(env)
    created = create(env)
    with lifecycle.shared(env["profile"]):
        with pytest.raises(roots.BackupRootError) as caught:
            with roots.capture_locked(env["profile"], **options(env)) as capture:
                created["path"].write_bytes(created["path"].read_bytes() + b"changed")
                capture.verify_source()
        assert caught.value.retriable and caught.value.code == "SOURCE_CHANGED"


def test_foreign_legacy_library_does_not_claim_current_profile_roots(env):
    asset(env)
    target = env["profile"].database.with_name("reno.db.bak-foreign")
    copy_image(env, target)
    con = sqlite3.connect(target)
    con.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
    con.commit()
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.close()
    result = scan(env)
    assert {b.namespace for b in result.scan.blockers} == set(artifacts.NAMESPACES)
    # References cannot be declared proven roots for a different producer.
    assert not result.scan.references
    assert any(b.code == "BACKUP_PRODUCER_IDENTITY_UNKNOWN" for b in result.scan.blockers)


def test_legacy_row_budget_is_enforced_before_unbounded_reference_materialization(tmp_path):
    env = environment(tmp_path / "old", version=0)
    try:
        for index in range(8):
            asset(env, video=f"v{index}", sha=hex(index + 1)[2:] * 64)
        create(env)
        from reno.storage_root_inputs import InputLimits
        result = scan(env, limits=roots.BackupLimits(input_limits=InputLimits(max_rows=5)))
        assert result.scan.blockers
        assert any(b.code == "INPUT_ROW_LIMIT" for b in result.scan.blockers)
    finally:
        env["con"].close()


def test_checkpoint_capture_instability_requires_whole_round_retry(env, monkeypatch):
    from reno import storage_checkpoint_roots as checkpoints
    create(env)
    @contextmanager
    def changed(*args, **kwargs):
        raise checkpoints.CheckpointRootError("CHECKPOINT_SOURCE_CHANGED", retriable=True)
        yield  # pragma: no cover - define the failing context-manager protocol.
    monkeypatch.setattr(checkpoints, "capture_image_locked", changed)
    with pytest.raises(roots.BackupRootError) as caught:
        scan(env)
    assert caught.value.code == "SOURCE_CHANGED" and caught.value.retriable


def test_one_guard_archive_cache_preserves_each_backup_provenance(env, monkeypatch):
    from reno import storage_checkpoint_roots as checkpoints, sync_checkpoints
    entry = empty_archive(env)
    first, second = create(env), create(env)
    decode = sync_checkpoints._decode
    calls = []
    def counted(*args, **kwargs):
        calls.append(1)
        return decode(*args, **kwargs)
    monkeypatch.setattr(sync_checkpoints, "_decode", counted)
    cache = checkpoints.ArchiveCache()
    with lifecycle.shared(env["profile"]):
        with roots.capture_locked(env["profile"], **options(env, cache=cache)) as capture:
            refs = [r for r in capture.scan.references if r.namespace == "checkpoint" and r.storage_key == entry["storage_key"]]
            assert len(refs) == 2 and len({r.source_id for r in refs}) == 2
            assert len(calls) == 1 and len(cache.archives) == 1
            assert capture.image_bytes == first["path"].stat().st_size + second["path"].stat().st_size


@pytest.mark.parametrize("limit_name", ["max_image_bytes", "max_read_bytes", "max_metadata_bytes", "max_backups", "max_total_image_bytes"])
def test_discovery_limits_apply_before_disallowed_primary_image_reads(env, monkeypatch, limit_name):
    from reno import sync_media
    first, second = create(env), create(env)
    main_paths = {backups._key(first["path"]), backups._key(second["path"])}
    opened = []
    original = sync_media._Directory.open_file
    def tracked(directory, name, *args, **kwargs):
        path = backups._key(directory.path / name)
        if path in main_paths:
            opened.append(path)
        return original(directory, name, *args, **kwargs)
    monkeypatch.setattr(sync_media._Directory, "open_file", tracked)
    limit = first["path"].stat().st_size if limit_name == "max_total_image_bytes" else 1
    result = scan(env, limits=roots.BackupLimits(**{limit_name: limit}))
    assert result.scan.blockers
    assert len(set(opened)) <= (1 if limit_name == "max_total_image_bytes" else 0)
    assert all(b.namespace in artifacts.NAMESPACES for b in result.scan.blockers)


def test_chunk_cancellation_keeps_original_exception_and_resets_read_control(env):
    env["con"].execute("CREATE TABLE private_unprojected_body(body BLOB)")
    env["con"].execute("INSERT INTO private_unprojected_body VALUES (?)", (b"x" * (512 * 1024),))
    env["con"].commit()
    created = create(env)
    cancellation = ValueError("private callback reason")
    observed = []
    class Cancel:
        def check(self):
            pass
        def read_chunk(self, path, size, content):
            if path == created["path"]:
                observed.append(size)
                raise cancellation
    with lifecycle.shared(env["profile"]):
        with pytest.raises(ValueError) as caught:
            backups.discover_locked(env["profile"], read_control=Cancel())
        assert caught.value is cancellation and observed == [128 * 1024]
        # The ContextVar token must be reset, including exception paths.
        assert backups._READ_CONTROL.get() is None
        assert not backups.discover_locked(env["profile"])["blockers"]


def test_repeated_verification_reads_are_charged_to_same_budget(env):
    created = create(env)
    with lifecycle.shared(env["profile"]):
        with roots.capture_locked(env["profile"], **options(env)) as capture:
            before = capture._read_control.read_bytes
            capture.verify_source()
            assert capture._read_control.read_bytes > before + created["path"].stat().st_size


def test_primary_caller_exception_is_not_replaced_by_final_source_change(env):
    created = create(env)
    primary = RuntimeError("original caller cancellation")
    with lifecycle.shared(env["profile"]):
        with pytest.raises(RuntimeError) as caught:
            with roots.capture_locked(env["profile"], **options(env)):
                path, _ = catalog(env, created)
                path.write_bytes(path.read_bytes() + b"\n")
                raise primary
        assert caught.value is primary


def test_declared_main_image_never_uses_metadata_extension_to_bypass_image_budget(tmp_path):
    budget = roots._Budget(roots.BackupLimits(max_image_bytes=1), lambda: None)
    # Current producer rejects .json destinations. The read-control contract
    # still classifies explicit image candidates before filename extensions.
    path = tmp_path / "declared-image.json"
    budget.candidates((path,))
    with pytest.raises(roots.BackupRootError) as caught:
        budget.before_read(path, 100, True)
    assert caught.value.code == "BACKUP_IMAGE_LIMIT"


def test_new_candidate_over_budget_is_source_change_during_reverification(env):
    create(env)
    with lifecycle.shared(env["profile"]):
        with pytest.raises(roots.BackupRootError) as caught:
            with roots.capture_locked(env["profile"], **options(env, limits=roots.BackupLimits(max_backups=1))) as capture:
                create(env)
                capture.verify_source()
        assert caught.value.code == "SOURCE_CHANGED" and caught.value.retriable


def test_discovery_streams_sql_rows_before_aggregate_metadata_limit(tmp_path, monkeypatch):
    env = environment(tmp_path / "old", version=0)
    try:
        payload = json.dumps({"video": "x" * (128 * 1024)})
        env["con"].executemany("INSERT INTO video_asset(video_id,files_json,content_sha256) VALUES(?,?,?)",
                               ((str(i), payload, SHA) for i in range(300)))
        env["con"].commit()
        target = env["profile"].database.with_name("reno.db.bak-large-metadata")
        copy_image(env, target)
        original = sqlite3.connect
        visited = []
        class Cursor(sqlite3.Cursor):
            def execute(self, query, *args, **kwargs):
                self.query = query
                return super().execute(query, *args, **kwargs)
            def __next__(self):
                value = super().__next__()
                if 'FROM "video_asset"' in self.query:
                    visited.append(1)
                return value
            def fetchmany(self, *args, **kwargs):
                assert 'FROM "video_asset"' not in self.query, "Metadata projection must stream rows before retaining a large batch"
                return super().fetchmany(*args, **kwargs)
        class Connection(sqlite3.Connection):
            def execute(self, query, *args, **kwargs):
                return self.cursor(factory=Cursor).execute(query, *args, **kwargs)
        monkeypatch.setattr(sqlite3, "connect", lambda *a, **k: original(*a, **k, factory=Connection))
        class Control:
            def check(self):
                pass
        with lifecycle.shared(env["profile"]):
            result = backups.discover_locked(env["profile"], read_control=Control())
        assert result["blockers"] and 100 <= len(visited) < 150
    finally:
        env["con"].close()
