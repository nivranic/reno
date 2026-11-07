"""Schema15 is additive; source receipts never rewrite paid or private facts."""
from contextlib import closing
import json
import sqlite3
import uuid

import pytest

from reno import budget, db, migrations, personal_schema, review, sync_protocol, sync_schema
from tests.test_budget_ledger import profile, operation, attempt, policy, dispatch, complete, REQUEST  # noqa: F401
from tests.test_budget_ledger import NOW as LEDGER_NOW
from tests.test_sync_budget_wait import request as defer_request, apply as defer
from tests.test_sync_events import populate
from tests.test_sync_queue import start, add_claim
from tests.migration_helpers import drop_future_home_recovery
from tests.schema_fixtures import released16


@pytest.fixture(autouse=True)
def historical_schema16_contract(monkeypatch):
    """Preserve the original additive migration and its whole paid lifetime."""
    with released16(monkeypatch):
        yield


def freeze_v14(path):
    """Keep released schema14 declarations and every old row byte-for-byte."""
    with closing(sqlite3.connect(path)) as con:
        drop_future_home_recovery(con)
        con.execute("DROP TABLE sync_queue_source_receipt")
        con.execute("UPDATE meta SET value='14' WHERE key='migration_version'")
        con.commit()


def rows(con):
    result = {}
    for table in sorted(migrations._tables(con)):
        where = " WHERE key!='migration_version'" if table == "meta" else ""
        result[table] = sorted([tuple(row) for row in con.execute(f'SELECT * FROM "{table}"{where}')], key=repr)
    return result


def schema(con):
    return {name: (kind, parent, sql) for kind, name, parent, sql in con.execute(
        "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%'")}


def snapshot(path):
    with closing(sqlite3.connect(path)) as con:
        return rows(con), schema(con)


def seed_v14(profile, tmp_path):
    with closing(db.connect(profile.database)) as con:
        populate(con)
    paid = budget.reserve(operation(), attempt(), profile=profile, policy=policy(), now=LEDGER_NOW)
    assert dispatch(profile, paid).won
    complete(profile, paid, text="paid original body: 保留原始响应")
    source_path = tmp_path / "home" / "reno.db"
    db.initialize(source_path)
    with closing(db.connect(source_path)) as con:
        source = {"library_id": db.knowledge_version(con)["library_id"],
                  "node_id": personal_schema.node_identity(source_path.parent),
                  "handshake": sync_protocol.schema_handshake(con)}
    with closing(db.connect(profile.database)) as con:
        env = {"con": con, "path": profile.database, "source": source}
        lease = start(env)
        body = defer_request(env, lease, add_claim(env, lease), retry_at=None)
        defer(env, body)
    private = profile.database.parent / "personal-attachments" / "original.pdf"
    private.parent.mkdir(exist_ok=True)
    private.write_bytes(b"%PDF-synthetic-private-original")
    freeze_v14(profile.database)
    return paid, private


def test_genuine_v14_upgrade_adds_only_source_receipt_and_preserves_every_old_fact(profile, tmp_path, monkeypatch):
    paid, private = seed_v14(profile, tmp_path)
    before_rows, before_schema = snapshot(profile.database)
    identity_path = profile.database.parent / "node-identity.json"
    preserved = {path: path.read_bytes() for path in (identity_path, private)}
    with closing(sqlite3.connect(profile.database)) as con, monkeypatch.context() as patch:
        patch.setattr(migrations, "SCHEMA_VERSION", 14)
        old_handshake = sync_protocol.schema_handshake(con)
        assert "source_fail" not in con.execute(
            "SELECT sql FROM sqlite_master WHERE name='sync_queue_budget_receipt'").fetchone()[0]
    def forbidden(*_args, **_kwargs):
        pytest.fail("Additive source migration cannot seed identities or replay knowledge")
    monkeypatch.setattr(personal_schema, "seed", forbidden)
    monkeypatch.setattr(review, "replay", forbidden)
    result = db.migrate(profile.database, profile=profile)
    assert result.changed and result.version == migrations.SCHEMA_VERSION
    actual_rows, actual_schema = snapshot(profile.database)
    assert actual_rows.pop("sync_queue_source_receipt") == []
    from reno import home_recovery_schema
    assert actual_rows.pop(home_recovery_schema.TABLE) == []
    assert actual_rows == before_rows
    assert {name: actual_schema[name] for name in before_schema} == before_schema
    assert set(actual_schema) - set(before_schema) == {"sync_queue_source_receipt", "idx_sync_source_terminal_item",
        home_recovery_schema.TABLE, "idx_home_recovery_committed", "idx_home_recovery_root",
        "idx_home_recovery_epoch", "home_recovery_no_update", "home_recovery_no_delete"}
    assert snapshot(result.backup_path) == (before_rows, before_schema)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))
    assert (manifest["from_version"], manifest["to_version"]) == (14, migrations.SCHEMA_VERSION)
    with closing(sqlite3.connect(result.backup_path)) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "14"
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    with closing(db.connect(profile.database)) as con:
        current_handshake = sync_protocol.schema_handshake(con)
        assert con.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert con.execute("PRAGMA foreign_key_check").fetchall() == []
        sync_schema.validate(con)
    assert current_handshake["core_schema_sha256"] == old_handshake["core_schema_sha256"]
    assert (old_handshake["migration_version"], current_handshake["migration_version"]) == (14, migrations.SCHEMA_VERSION)
    from reno import sync_checkpoints
    assert sync_checkpoints.ARCHIVE_SCHEMA_VERSION == 9
    assert set(sync_checkpoints.SUPPORTED_SCHEMA_VERSIONS) == {7, 9}
    assert budget.get_result(paid.attempt_id, REQUEST, profile=profile).text == "paid original body: 保留原始响应"
    assert all(path.read_bytes() == value for path, value in preserved.items())
    assert not db.migrate(profile.database, profile=profile).changed


def test_v14_additive_install_interruption_rolls_back_new_ddl_and_all_old_rows(profile, tmp_path, monkeypatch):
    seed_v14(profile, tmp_path)
    before = snapshot(profile.database)
    original = sync_schema.install
    def interrupted(con):
        original(con)
        assert con.execute("SELECT 1 FROM sqlite_master WHERE name='sync_queue_source_receipt'").fetchone()
        raise sqlite3.DatabaseError("synthetic schema15 interruption")
    monkeypatch.setattr(sync_schema, "install", interrupted)
    with pytest.raises(sqlite3.DatabaseError, match="synthetic"):
        db.migrate(profile.database, profile=profile)
    assert snapshot(profile.database) == before
    with closing(sqlite3.connect(profile.database)) as con:
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "14"


@pytest.mark.parametrize("collision", ["table_unknown", "table_correct_shape", "table_correct_shape_upper", "index", "index_upper"])
def test_future_source_names_refused_before_backup_without_adopting_unknown_objects(profile, tmp_path, monkeypatch, collision):
    seed_v14(profile, tmp_path)
    with closing(sqlite3.connect(profile.database)) as con:
        if collision.startswith("index"):
            index = "IDX_SYNC_SOURCE_TERMINAL_ITEM" if collision.endswith("upper") else "idx_sync_source_terminal_item"
            con.execute(f"CREATE INDEX {index} ON sync_queue(attempts)")
        elif collision == "table_unknown":
            con.execute("CREATE TABLE sync_queue_source_receipt(opaque TEXT)")
            con.execute("INSERT INTO sync_queue_source_receipt VALUES('unknown original data')")
        else:
            table = "SYNC_QUEUE_SOURCE_RECEIPT" if collision.endswith("upper") else "sync_queue_source_receipt"
            con.execute(f"CREATE TABLE {table} ({sync_schema.TABLES['sync_queue_source_receipt']})")
            item = con.execute("SELECT item_id FROM sync_queue LIMIT 1").fetchone()[0]
            con.execute("INSERT INTO sync_queue_source_receipt VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (str(uuid.uuid4()), "a" * 64, "{}", "source_fail", str(uuid.uuid4()), str(uuid.uuid4()),
                 str(uuid.uuid4()), str(uuid.uuid4()), 1, 0, item, 1, "b" * 64, "c" * 64, "d" * 64,
                 "{}", "e" * 64, "2026-01-01T00:00:00.000Z"))
        con.commit()
    before = snapshot(profile.database)
    identity_path = profile.database.parent / "node-identity.json"
    identity = identity_path.read_bytes()
    def forbidden(*_args, **_kwargs):
        pytest.fail("Future operational namespace collision must be rejected before backup")
    monkeypatch.setattr(migrations, "_backup", forbidden)
    with pytest.raises(migrations.SchemaVersionError, match="source receipt name collision"):
        db.migrate(profile.database, profile=profile)
    assert snapshot(profile.database) == before and identity_path.read_bytes() == identity
    assert not list(profile.database.parent.glob("*.bak-migrate-*"))


@pytest.mark.parametrize("damage", ["action_check", "epoch_check", "request_cap", "unique_index"])
def test_source_receipt_same_columns_without_required_constraints_rejected_without_repair(profile, damage):
    with closing(sqlite3.connect(profile.database)) as con:
        if damage == "unique_index":
            con.execute("DROP INDEX idx_sync_source_terminal_item")
            con.execute("CREATE INDEX idx_sync_source_terminal_item ON sync_queue_source_receipt(item_id)")
        else:
            removed = {"action_check": "CHECK(action='source_fail')", "epoch_check": "CHECK(receiver_library_epoch>=1)",
                       "request_cap": ",\n        CHECK(length(CAST(request_json AS BLOB))<=16384)"}[damage]
            declaration = sync_schema.TABLES["sync_queue_source_receipt"].replace(removed, "")
            con.execute("DROP TABLE sync_queue_source_receipt")
            con.execute(f"CREATE TABLE sync_queue_source_receipt ({declaration})")
            con.execute("CREATE UNIQUE INDEX idx_sync_source_terminal_item ON sync_queue_source_receipt(item_id)")
        con.commit()
    before = snapshot(profile.database)
    with pytest.raises((migrations.SchemaVersionError, sqlite3.DatabaseError)):
        db.connect(profile.database)
    assert snapshot(profile.database) == before
