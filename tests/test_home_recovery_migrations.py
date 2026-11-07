"""Schema16 maintenance preserves every released paid/private operational fact."""
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import budget, db, home_recovery_schema as home_schema, migrations, personal_schema, review
from reno import storage_backups, storage_lifecycle, sync_checkpoints, sync_protocol
from tests.test_budget_ledger import profile, operation, attempt, policy, dispatch, complete, REQUEST, NOW  # noqa: F401
from tests.test_home_recovery_source import env  # noqa: F401
from tests.schema_fixtures import released16


@pytest.fixture(autouse=True)
def historical_schema16_contract(monkeypatch):
    """Own the released target before profile/env initialization and teardown."""
    with released16(monkeypatch):
        yield


def freeze_v15(path):
    with closing(sqlite3.connect(path)) as con:
        con.execute("DROP TABLE IF EXISTS home_knowledge_recovery_operation")
        con.execute("UPDATE meta SET value='15' WHERE key='migration_version'")
        con.commit()


def snapshot(path):
    with closing(sqlite3.connect(path)) as con:
        tables = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        rows = {table: sorted([tuple(row) for row in con.execute('SELECT * FROM "' + table + '"' +
            (" WHERE key!='migration_version'" if table == "meta" else ""))], key=repr) for table in tables}
        ddl = {row[1]: tuple(row) for row in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}
        return rows, ddl


def seed_v15(profile):
    continuation = str(uuid.uuid4())
    with closing(db.connect(profile.database)) as con:
        root = con.execute("INSERT INTO job(kind,continuation_id,execution_generation,owner_id) VALUES('sync',?,1,'original-owner')",
                           (continuation,)).lastrowid
        con.execute("INSERT INTO meta VALUES('original-private-checkpoint',?)", (b"\x00\xffpaid-private-body-original",))
        con.commit()
    paid = budget.reserve(operation(continuation=continuation, root_job_id=root), attempt(),
                          profile=profile, policy=policy(), now=NOW)
    assert dispatch(profile, paid).won
    complete(profile, paid, text="paid original body 保留原始响应")
    preserved = {}
    for name, content in (("config.synthetic.json", b'{"private_marker":"synthetic-only"}'),
                          ("sync-state.json", b'{"format_version":4,"opaque_original":"preserve-as-file"}'),
                          ("personal-attachments/original.pdf", b"%PDF-synthetic-original")):
        path = profile.database.parent / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        preserved[path] = content
    identity = profile.database.parent / "node-identity.json"
    preserved[identity] = identity.read_bytes()
    freeze_v15(profile.database)
    return paid, preserved


def test_initialize_adds_empty_home_receipts_without_creating_authority(profile):
    assert migrations.SCHEMA_VERSION == 16
    with closing(db.connect(profile.database)) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "16"
        home_schema.validate(con)
        assert con.execute("SELECT COUNT(*) FROM home_knowledge_recovery_operation").fetchone()[0] == 0
        assert not con.execute("SELECT name FROM sqlite_master WHERE name LIKE 'sync_source_%readmit%'").fetchall()
    assert sync_checkpoints.ARCHIVE_SCHEMA_VERSION == 9
    assert set(sync_checkpoints.SUPPORTED_SCHEMA_VERSIONS) == {7, 9}


def test_v15_connect_is_read_only_and_requires_explicit_migration(profile):
    seed_v15(profile)
    before = snapshot(profile.database)
    with pytest.raises(migrations.SchemaVersionError, match="migration to 16"):
        db.connect(profile.database)
    assert snapshot(profile.database) == before
    assert not list(profile.database.parent.glob("*.bak-migrate-*"))


def test_v15_upgrade_adds_only_home_ddl_and_keeps_original_rows_bytes_and_fingerprint(profile, monkeypatch):
    paid, preserved = seed_v15(profile)
    before_rows, before_ddl = snapshot(profile.database)
    with closing(sqlite3.connect(profile.database)) as con, monkeypatch.context() as patch:
        con.row_factory = sqlite3.Row
        patch.setattr(migrations, "SCHEMA_VERSION", 15)
        before_handshake = sync_protocol.schema_handshake(con)

    def forbidden(*args, **kwargs):
        pytest.fail("Schema16 may not reseed identities or replay existing knowledge")

    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(review, "replay", forbidden)
    result = db.migrate(profile.database, profile=profile)
    assert result.changed and result.version == 16
    actual_rows, actual_ddl = snapshot(profile.database)
    assert actual_rows.pop(home_schema.TABLE) == []
    assert actual_rows == before_rows
    assert {name: actual_ddl[name] for name in before_ddl} == before_ddl
    assert set(actual_ddl) - set(before_ddl) == set(home_schema.RESERVED_NAMES)
    assert snapshot(result.backup_path) == (before_rows, before_ddl)
    note = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (note["from_version"], note["to_version"]) == (15, 16)
    with closing(sqlite3.connect(result.backup_path)) as image:
        assert image.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "15"
        assert image.execute("PRAGMA foreign_key_check").fetchall() == []
    with closing(db.connect(profile.database)) as con:
        home_schema.validate(con)
        after_handshake = sync_protocol.schema_handshake(con)
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    assert before_handshake["core_schema_sha256"] == after_handshake["core_schema_sha256"]
    assert (before_handshake["migration_version"], after_handshake["migration_version"]) == (15, 16)
    assert budget.get_result(paid.attempt_id, REQUEST, profile=profile).text == "paid original body 保留原始响应"
    assert all(path.read_bytes() == content for path, content in preserved.items())
    assert not db.migrate(profile.database, profile=profile).changed


def test_home_install_failure_rolls_back_all_new_ddl_and_old_facts(profile, monkeypatch):
    seed_v15(profile)
    before = snapshot(profile.database)
    original = home_schema.install

    def interrupted(con):
        original(con)
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (home_schema.TABLE,)).fetchone()
        raise sqlite3.DatabaseError("synthetic schema16 interruption")

    monkeypatch.setattr(home_schema, "install", interrupted)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic schema16"):
        db.migrate(profile.database, profile=profile)
    assert snapshot(profile.database) == before
    with closing(sqlite3.connect(profile.database)) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "15"


def minimal_legacy(tmp_path, version):
    path = tmp_path / "original.db"
    with sqlite3.connect(path) as con:
        con.execute("CREATE TABLE job(job_id INTEGER PRIMARY KEY)")
        con.execute("CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
        con.execute("INSERT INTO meta VALUES('migration_version',?)", (str(version),))
    profile = storage_lifecycle.StorageProfile(path, tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    storage_lifecycle.initialize(profile)
    return profile


@pytest.mark.parametrize("version", range(16))
@pytest.mark.parametrize("reserved", home_schema.RESERVED_NAMES)
@pytest.mark.parametrize("uppercase", [False, True])
def test_all_older_versions_refuse_future_names_before_backup(tmp_path, monkeypatch, version, reserved, uppercase):
    profile = minimal_legacy(tmp_path, version)
    name = reserved.upper() if uppercase else reserved
    with sqlite3.connect(profile.database) as con:
        con.execute('CREATE TABLE "' + name + '"(original_value BLOB)')
        con.execute('INSERT INTO "' + name + '" VALUES(?)', (b"original-unknown-data",))
    before = snapshot(profile.database)

    def forbidden(*args, **kwargs):
        pytest.fail("A future-name collision must be refused before backup")

    monkeypatch.setattr(migrations, "_backup", forbidden)
    with pytest.raises(migrations.SchemaVersionError, match="Schema16 Home recovery name collision"):
        db.migrate(profile.database, profile=profile)
    assert snapshot(profile.database) == before
    assert not list(profile.database.parent.glob("*.bak-migrate-*"))


@pytest.mark.parametrize("reserved", home_schema.RESERVED_NAMES)
@pytest.mark.parametrize("uppercase", [False, True])
def test_future_temp_names_are_not_ignored_before_backup(tmp_path, monkeypatch, reserved, uppercase):
    profile = minimal_legacy(tmp_path, 15)
    con = sqlite3.connect(profile.database)
    con.row_factory = sqlite3.Row
    name = reserved.upper() if uppercase else reserved
    con.execute('CREATE TEMP TABLE "' + name + '"(original_value BLOB)')
    con.commit()
    monkeypatch.setattr(migrations, "_open", lambda path: con)
    monkeypatch.setattr(migrations, "_backup", lambda *a, **kw: pytest.fail("TEMP collision reached backup"))
    with pytest.raises(migrations.SchemaVersionError, match="Schema16 Home recovery name collision"):
        db.migrate(profile.database, profile=profile)


def test_old_database_same_shape_home_namespace_is_never_adopted(profile, monkeypatch):
    freeze_v15(profile.database)
    with sqlite3.connect(profile.database) as con:
        home_schema.install(con)
    before = snapshot(profile.database)
    monkeypatch.setattr(migrations, "_backup", lambda *a, **kw: pytest.fail("Unknown same-shape table reached backup"))
    with pytest.raises(migrations.SchemaVersionError, match="Schema16 Home recovery name collision"):
        db.migrate(profile.database, profile=profile)
    assert snapshot(profile.database) == before


@pytest.mark.parametrize("damage", ["missing-table", "missing-trigger", "wrong-index"])
def test_current_home_contract_damage_is_rejected_without_repair(profile, damage):
    with sqlite3.connect(profile.database) as con:
        if damage == "missing-table":
            con.execute("DROP TABLE home_knowledge_recovery_operation")
        elif damage == "missing-trigger":
            con.execute("DROP TRIGGER home_recovery_no_delete")
        else:
            con.execute("DROP INDEX idx_home_recovery_epoch")
            con.execute("CREATE INDEX idx_home_recovery_epoch ON home_knowledge_recovery_operation(after_library_epoch)")
    before = snapshot(profile.database)
    with pytest.raises(migrations.SchemaVersionError):
        db.connect(profile.database)
    assert snapshot(profile.database) == before


def absent_checkpoint():
    return {"state_present": False,
        "absence_sha256": hashlib.sha256(b'{"format_version":1,"state":"absent"}').hexdigest(),
        "state_sha256": None, "state_bytes": 0, "canonical_state_sha256": None,
        "state_format_version": None, "source_execution_sha256": None,
        "source_execution_lineage": None, "outboxes": []}


def home_row(env):
    from reno import home_recovery_source as sources, storage_admin, sync_queue
    from tests.test_home_recovery_schema import record, set_json
    profile = env[0]
    source_id = str(uuid.uuid4())
    sources.create_checkpoint(profile=profile, source_id=source_id)
    declaration_path = sources._source_path(profile, source_id)
    declaration_raw = declaration_path.read_bytes()
    declaration = sources._declaration(declaration_raw)
    protection_id = str(uuid.uuid4())
    with storage_admin.connection(profile) as con:
        library, node = sync_queue._local_identity(con)
        db.snapshot(con, profile.database.with_name("protection-original-" + protection_id + ".db"), profile=profile,
                    kind="maintenance", backup_id=protection_id)
        descriptor, descriptor_sha = storage_backups._verify_ready(storage_backups._record(
            storage_backups._catalog(profile) / (protection_id + ".json"))[0], profile)
        value = record()
        last = con.execute('SELECT MAX(after_library_epoch),MAX(after_kb_revision) FROM "' + home_schema.TABLE + '"').fetchone()
        if last[0] is not None:
            value.update(before_library_epoch=last[0], after_library_epoch=last[0] + 1,
                before_kb_revision=last[1], after_kb_revision=last[1] + 1)
        value.update(library_id=library, node_id=node, source_id=source_id,
            source_sha256=declaration["image_sha256"], source_binding_sha256=hashlib.sha256(declaration_raw).hexdigest(),
            protection_backup_id=protection_id, protection_descriptor_sha256=descriptor_sha,
            protection_image_sha256=descriptor["database_file"]["sha256"])
        for prefix in ("before", "after"):
            set_json(value, prefix + "_version_json", {"library_id": library,
                "library_epoch": value[prefix + "_library_epoch"], "kb_revision": value[prefix + "_kb_revision"]})
        set_json(value, "checkpoint_json", absent_checkpoint())
        home_schema.validate_record(value)
        con.execute('INSERT INTO ' + home_schema.TABLE + '(' + ','.join(home_schema.COLUMNS) + ') VALUES(' +
            ','.join('?' for _ in home_schema.COLUMNS) + ')', tuple(value[key] for key in home_schema.COLUMNS))
        con.commit()
    return value, declaration


def projected(value):
    from reno import storage_home_recovery_roots as roots
    return {key: value[key] for key in roots.COLUMNS}


def root_ids(profile):
    from reno import storage_artifacts
    return {namespace: storage_artifacts.root_identity(root, profile=profile)["root_id"]
        for namespace, root in (("sync_media", profile.media), ("local_report", profile.reports),
                                ("sync_report", profile.reports), ("checkpoint", profile.checkpoints))}


def test_receipt_projection_hashes_are_closed_without_private_body_reads(env):
    from reno import storage_home_recovery_roots as roots
    value, _ = home_row(env)
    with sqlite3.connect(env[0].database) as con:
        read = []
        private = set(home_schema.COLUMNS) - set(roots.COLUMNS)
        def authorize(action, first, second, *_):
            if action == sqlite3.SQLITE_READ and first == home_schema.TABLE:
                read.append(second)
                if second in private:
                    return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        con.set_authorizer(authorize)
        assert roots.read_projection(con) == [projected(value)]
    assert set(read) <= set(roots.COLUMNS)
    assert not {"frontier_json", "request_json", "result_json"} & set(read)


@pytest.mark.parametrize("field,changed", [("operation_id", "not-a-uuid"), ("library_id", True),
    ("source_kind", "unknown"), ("source_sha256", "A" * 64), ("checkpoint_sha256", "f" * 64),
    ("checkpoint_json", '{}'), ("private_body", "synthetic-private-input")])
def test_projection_rejects_unknown_receipt_fields_and_forged_checkpoint(env, field, changed):
    from reno import storage_home_recovery_roots as roots
    value, _ = home_row(env)
    row = {**projected(value), field: changed}
    with pytest.raises(roots.HomeRecoveryRootError):
        roots.normalize_projection([row])


def test_exact_source_and_protection_proof_closure_does_not_block_other_roots(env):
    from reno import storage_home_recovery_roots as roots
    value, declaration = home_row(env)
    profile = env[0]
    with storage_lifecycle.shared(profile):
        with roots.capture_locked([projected(value)], profile=profile, root_ids=root_ids(profile),
            member_id="mem_synthetic", library_id=value["library_id"], node_id=value["node_id"]) as captured:
            assert not captured.inputs.blockers
            assert {value["protection_backup_id"], declaration["backup_id"]} == set(roots.backup_ids([projected(value)]))
            captured.verify_source()


def test_missing_exact_protection_backup_is_not_inferred_as_no_proof(env):
    from reno import storage_home_recovery_roots as roots
    value, _ = home_row(env)
    row = projected(value)
    row["protection_backup_id"] = str(uuid.uuid4())
    profile = env[0]
    with storage_lifecycle.shared(profile):
        with roots.capture_locked([row], profile=profile, root_ids=root_ids(profile), member_id="mem_synthetic",
            library_id=value["library_id"], node_id=value["node_id"]) as captured:
            assert {item.code for item in captured.inputs.blockers} == {"HOME_RECOVERY_PROOF_UNPROVEN"}


def test_retention_blocks_referenced_backup_ids_but_retires_other_maintenance_images(env):
    from reno import storage_admin
    value, declaration = home_row(env)
    profile = env[0]
    garbage = []
    # Create these independent images before copying any Home receipt into
    # them: otherwise their own retained original SQL is another proof root.
    with sqlite3.connect(profile.database) as con:
        con.execute("DROP TRIGGER home_recovery_no_delete")
        con.execute("DELETE FROM home_knowledge_recovery_operation")
        con.execute(home_schema.TRIGGERS[1])
    for index in range(2):
        backup_id = str(uuid.uuid4())
        with storage_admin.connection(profile) as con:
            db.snapshot(con, profile.database.with_name("garbage-" + str(index) + ".db"), profile=profile,
                        kind="maintenance", backup_id=backup_id)
        garbage.append(backup_id)
    with storage_admin.connection(profile) as con:
        con.execute('INSERT INTO ' + home_schema.TABLE + '(' + ','.join(home_schema.COLUMNS) + ') VALUES(' +
            ','.join('?' for _ in home_schema.COLUMNS) + ')', tuple(value[key] for key in home_schema.COLUMNS))
        con.commit()
    result = storage_backups.retain_recent(profile, kind="maintenance", keep=1, operation_id=str(uuid.uuid4()))
    assert set(result["retired"]) == {garbage[0]}
    assert set(result["blocked"]) == {value["protection_backup_id"], declaration["backup_id"]}
    for backup_id in result["blocked"]:
        record, _ = storage_backups._record(storage_backups._catalog(profile) / (backup_id + ".json"))
        assert record["state"] == "ready" and Path(record["destination"]).is_file()


def test_one_shot_controlled_read_exception_keeps_original_identity(env):
    from reno import storage_home_recovery_roots as roots
    value, _ = home_row(env)
    original = ValueError("synthetic single callback failure")
    calls = 0
    def check():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise original
    with storage_lifecycle.shared(env[0]):
        with pytest.raises(ValueError) as found:
            with roots.capture_locked([projected(value)], profile=env[0], root_ids=root_ids(env[0]),
                member_id="mem_synthetic", library_id=value["library_id"], node_id=value["node_id"],
                check_cancelled=check):
                pytest.fail("Read cancellation must not be converted to a proof blocker")
    assert found.value is original


@pytest.mark.parametrize("damage", ["missing_manifest", "missing_image", "changed_manifest", "changed_declaration"])
def test_actual_missing_or_changed_home_proof_files_remain_scoped_unknown(env, damage):
    from reno import storage_home_recovery_roots as roots, home_recovery_source as sources
    value, _ = home_row(env)
    record, _ = storage_backups._record(storage_backups._catalog(env[0]) / (value["protection_backup_id"] + ".json"))
    image = Path(record["destination"])
    manifest = image.with_name(image.name + storage_backups.SUFFIX)
    if damage == "missing_manifest":
        manifest.unlink()
    elif damage == "missing_image":
        image.unlink()
    elif damage == "changed_manifest":
        manifest.write_bytes(manifest.read_bytes() + b" ")
    else:
        declaration = sources._source_path(env[0], value["source_id"])
        declaration.write_bytes(declaration.read_bytes() + b" ")
    with storage_lifecycle.shared(env[0]):
        with roots.capture_locked([projected(value)], profile=env[0], root_ids=root_ids(env[0]),
            member_id="mem_synthetic", library_id=value["library_id"], node_id=value["node_id"]) as captured:
            assert {item.code for item in captured.inputs.blockers} == {"HOME_RECOVERY_PROOF_UNPROVEN"}
            assert not captured.inputs.references


def test_original_outbox_reference_survives_no_resend_and_current_pointer_change(env):
    from reno import storage_home_recovery_roots as roots, sync_state
    from tests.test_storage_home_roots import outbox
    from tests.test_storage_reference_retirement_roots import store, marker, scan
    from tests.test_home_recovery_schema import set_json
    value, _ = home_row(env)
    home_env = (env[0], {"library_id": value["library_id"], "node_id": value["node_id"]})
    state = store(home_env)
    path, metadata, envelope = outbox(home_env, video="BV1Original", legacy=True)
    original_state = state.read()
    state_raw = (state.data_dir / "sync-state.json").read_bytes()
    canonical_state = home_schema.canonical_json(original_state).encode()
    checkpoint = {"state_present": True, "absence_sha256": None,
        "state_sha256": hashlib.sha256(state_raw).hexdigest(),
        "state_bytes": len(state_raw),
        "canonical_state_sha256": hashlib.sha256(canonical_state).hexdigest(),
        "state_format_version": original_state["format_version"], "source_execution_sha256": None,
        "source_execution_lineage": None, "outboxes": [{**metadata,
            "semantic_sha256": hashlib.sha256(home_schema.canonical_json(envelope).encode()).hexdigest()}]}
    set_json(value, "checkpoint_json", checkpoint)
    # Actual original sender tombstone drops the generic outbox edge, while
    # the Home receipt must retain its independently captured original edge.
    marker(home_env, state, metadata)
    assert state.read()["pending_publication"] is None and path.is_file()
    assert not scan(home_env).references
    with storage_lifecycle.shared(env[0]):
        with roots.capture_locked([projected(value)], profile=env[0], root_ids=root_ids(env[0]),
            member_id="mem_synthetic", library_id=value["library_id"], node_id=value["node_id"]) as captured:
            assert not captured.inputs.blockers
            assert {item.storage_key for item in captured.inputs.references} == {
                "sync/BV1Original/" + envelope["snapshot"]["videos"][0]["media"]["sha256"] + ".mp4"}


def replace_synthetic_receipts(con, values):
    con.execute("DROP TRIGGER home_recovery_no_delete")
    con.execute('DELETE FROM "' + home_schema.TABLE + '"')
    con.execute(home_schema.TRIGGERS[1])
    for value in values:
        con.execute('INSERT INTO ' + home_schema.TABLE + '(' + ','.join(home_schema.COLUMNS) + ') VALUES(' +
            ','.join('?' for _ in home_schema.COLUMNS) + ')', tuple(value[key] for key in home_schema.COLUMNS))
    con.commit()


def test_selected_but_held_backup_adds_transitive_original_reverse_edges(env):
    from reno import storage_home_recovery_roots as roots, storage_admin
    first, first_source = home_row(env)
    # A second checkpoint image retains the first receipt, then today's SQL
    # drops the first receipt. The second source image is itself selected for
    # retention but held by today's second receipt, so its old proof edges
    # must be visited rather than treated as absent selected data.
    second, second_source = home_row(env)
    with storage_admin.connection(env[0]) as con:
        replace_synthetic_receipts(con, [second])
    records = {record["backup_id"]: record for record, _ in storage_backups._catalog_entries(env[0])}
    selected = set(records)
    with storage_lifecycle.exclusive(env[0]):
        held = roots.retention_holds_locked(env[0], records, selected)
    assert held == {first["protection_backup_id"], first_source["backup_id"],
        second["protection_backup_id"], second_source["backup_id"]}


def test_gc_retirement_reader_accepts_fixed_home_roots_but_denies_private_columns(env):
    from reno import storage_gc_contract as contract
    value, _ = home_row(env)
    entry = {"checkpoint_id": str(uuid.uuid4()), "publication_id": str(uuid.uuid4()),
        "receiver_version_json": json.dumps({"library_id": value["library_id"], "library_epoch": 1, "kb_revision": 1}),
        "format_version": 1, "schema_version": 9, "storage_key": "pending", "sha256": "a" * 64,
        "byte_size": 10, "created_at": "2026-10-01T00:00:00.000Z"}
    entry["storage_key"] = entry["checkpoint_id"] + ".sqlite"
    with sqlite3.connect(env[0].database) as con:
        result = contract.verify_retirement(con, entry, expected_library_id=value["library_id"])
        assert (result.status, result.code) == ("not_retired", "RETIREMENT_ABSENT")
        for column in ("frontier_json", "request_json", "result_json"):
            with pytest.raises(sqlite3.DatabaseError):
                con.execute('SELECT "' + column + '" FROM "' + home_schema.TABLE + '"').fetchall()
        assert con.execute('SELECT operation_id,checkpoint_sha256 FROM "' + home_schema.TABLE + '"').fetchone() == (
            value["operation_id"], value["checkpoint_sha256"])


def test_projection_cannot_substitute_an_empty_temp_shadow_for_committed_roots(env):
    from reno import storage_home_recovery_roots as roots
    value, _ = home_row(env)
    with sqlite3.connect(env[0].database) as con:
        con.execute('CREATE TEMP TABLE "' + home_schema.TABLE + '" AS SELECT * FROM main."' + home_schema.TABLE + '" WHERE 0')
        assert roots.read_projection(con) == [projected(value)]


@pytest.mark.parametrize("limit", [False, 0, 100_001])
def test_projection_rejects_invalid_bounds_before_any_sql(env, limit):
    from reno import storage_home_recovery_roots as roots
    with sqlite3.connect(env[0].database) as con:
        con.set_authorizer(lambda *_: sqlite3.SQLITE_DENY)
        with pytest.raises(roots.HomeRecoveryRootError, match="HOME_RECOVERY_ROOT_LIMIT"):
            roots.read_projection(con, maximum_rows=limit)
