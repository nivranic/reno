"""Explicit, backed-up schema changes. Ordinary request connections never migrate.

Call ``initialize`` only for a new database and ``migrate`` in a maintenance
window with the worker and write API stopped. Migrations use individual SQL
statements inside one transaction: executescript would commit that transaction.
"""
from dataclasses import dataclass
from datetime import datetime, timezone
from functools import lru_cache
import hashlib
import json
import os
import re
from pathlib import Path
import sqlite3
import uuid

SCHEMA_PATH = Path(__file__).parent / "schema" / "schema.sql"
SCHEMA_VERSION = 17
STEP_TABLES = ("frame", "visual_observation", "transcript_segment", "ocr_span")
ADDITIONS = {
    "video_asset": {"source_published_at": "TEXT"},
    "visual_observation": {"measurement": "TEXT"},
    "knowledge_atom": {"dimension": "TEXT", "evidence_nature": "TEXT",
                       "exceptions": "TEXT", "prices_json": "TEXT"},
    "knowledge_cluster": {"derivation_json": "TEXT"},
    "conflict_case": {"derivation_json": "TEXT"},
    "user_decision": {
        "event_id": "TEXT", "client_id": "TEXT", "decided_at": "TEXT",
        "target_fingerprint": "TEXT", "target_snapshot_json": "TEXT",
        "event_schema_version": "INTEGER",
    },
    "job": {"request_id": "TEXT", "scope_key": "TEXT", "payload_json": "TEXT NOT NULL DEFAULT '{}'",
            "owner_id": "TEXT", "lease_until": "INTEGER", "result_json": "TEXT",
            "started_at": "TEXT", "completed_at": "TEXT"},
}


class SchemaVersionError(RuntimeError):
    """Database is absent, unsupported, or needs an explicit upgrade."""


@dataclass(frozen=True)
class MigrationResult:
    version: int
    changed: bool
    backup_path: Path | None = None
    manifest_path: Path | None = None


def _q(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def _open(path, *, readonly=False):
    con = sqlite3.connect(Path(path).resolve().as_uri() +
                          ("?mode=ro" if readonly else "?mode=rw"),
                          uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    return con


def _statements():
    pending = ""
    for line in SCHEMA_PATH.read_text(encoding="utf-8").splitlines(keepends=True):
        pending += line
        if sqlite3.complete_statement(pending):
            yield pending.strip()
            pending = ""
    if pending.strip():
        raise SchemaVersionError("Schema source contains an incomplete SQL statement")


def _create_schema(con, *, readmit_variant=None):
    """Build an explicit schema reference, without adopting unknown objects.

    None retains the released schema16 shape even after global17 activation.
    Callers creating the current application schema select its variant below;
    historical effect readers must declare the exact version they compare.
    """
    if readmit_variant is not None and (
            type(readmit_variant) is not str or readmit_variant not in
            {"v1", "admission2", "admission2_dispose2"}):
        raise SchemaVersionError("Unsupported source readmission schema variant")
    for statement in _statements():
        con.execute(statement)
    from . import personal_schema, sync_schema, sync_transfer_schema, sync_checkpoint_schema, sync_recovery_schema, storage_schema, budget_schema, home_recovery_schema
    personal_schema.install(con)
    sync_schema.install(con)
    sync_transfer_schema.install(con)
    sync_checkpoint_schema.install(con)
    sync_recovery_schema.install(con)
    storage_schema.install(con)
    budget_schema.install(con)
    home_recovery_schema.install(con)
    if readmit_variant is not None:
        from . import sync_source_readmit_schema, sync_source_readmit_schema2
        if readmit_variant == "v1":
            sync_source_readmit_schema.install(con)
        elif readmit_variant == "admission2":
            sync_source_readmit_schema2.install(con)
        else:
            sync_source_readmit_schema2.install_dispose2(con)


def _current_readmit_variant():
    return "admission2_dispose2" if SCHEMA_VERSION >= 17 else None


def _tables(con):
    return {row[0] for row in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}


def _columns(con, table):
    return {row["name"]: dict(row) for row in con.execute(f"PRAGMA table_info({_q(table)})")}


@lru_cache(maxsize=4)
def _contract(readmit_variant=None):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        _create_schema(con, readmit_variant=readmit_variant)
        return {table: _columns(con, table) for table in _tables(con)}
    finally:
        con.close()


def _version(con):
    if "meta" not in _tables(con):
        return 0
    row = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()
    if row is None:
        return 0
    try:
        version = int(row[0])
    except (ValueError, TypeError) as exc:
        raise SchemaVersionError("Invalid migration_version; database left unchanged") from exc
    if version < 0 or version > SCHEMA_VERSION:
        raise SchemaVersionError(f"Unsupported database schema {version}; application supports {SCHEMA_VERSION}")
    return version


def validate(con):
    """Read-only compatibility check; never initializes or repairs the schema."""
    version = _version(con)
    if version != SCHEMA_VERSION:
        raise SchemaVersionError(
            f"Database schema {version} needs migration to {SCHEMA_VERSION}. "
            "Stop workers/write API, then run: python -m reno migrate-db")
    for table, expected in _contract(_current_readmit_variant()).items():
        actual = _columns(con, table)
        missing = expected.keys() - actual.keys()
        if missing:
            raise SchemaVersionError(f"Database schema is incomplete: {table} missing {', '.join(sorted(missing))}")
        expected_pk = {name: column["pk"] for name, column in expected.items() if column["pk"]}
        actual_pk = {name: column["pk"] for name, column in actual.items() if column["pk"]}
        if expected_pk != actual_pk:
            raise SchemaVersionError(f"Unsupported primary key in {table}; database left unchanged")
        if table in ("knowledge_cluster", "conflict_case"):
            binding = actual["derivation_json"]
            if str(binding["type"]).upper() != "TEXT" or binding["notnull"] or binding["dflt_value"] is not None:
                raise SchemaVersionError(f"Invalid nullable derivation binding column in {table}")
    event_indexes = [row for row in con.execute("PRAGMA index_list(user_decision)") if row["unique"]]
    if not any([row["name"] for row in con.execute(f"PRAGMA index_info({_q(index['name'])})")] == ["event_id"]
               for index in event_indexes):
        raise SchemaVersionError("Missing unique user_decision event_id constraint")
    triggers = {row[0] for row in con.execute("SELECT name FROM sqlite_master WHERE type='trigger'")}
    required_triggers = set(re.findall(r"CREATE TRIGGER IF NOT EXISTS (\w+)", SCHEMA_PATH.read_text(encoding="utf-8")))
    if required_triggers - triggers:
        raise SchemaVersionError("Missing knowledge revision triggers; database schema is incomplete")
    metadata = dict(con.execute("SELECT key,value FROM meta WHERE key IN ('library_id','library_epoch','kb_revision')"))
    try:
        uuid.UUID(metadata["library_id"])
        if int(metadata["library_epoch"]) < 1 or int(metadata["kb_revision"]) < 0:
            raise ValueError("negative revision")
    except (KeyError, ValueError, TypeError) as exc:
        raise SchemaVersionError("Missing or invalid library identity metadata") from exc
    from . import personal_schema, sync_schema, sync_transfer_schema, sync_checkpoint_schema, sync_recovery_schema, storage_schema, budget_schema, home_recovery_schema
    try:
        personal_schema.validate(con)
        sync_schema.validate(con)
        sync_transfer_schema.validate(con)
        sync_checkpoint_schema.validate(con)
        sync_recovery_schema.validate(con)
        storage_schema.validate(con)
        budget_schema.validate(con)
        home_recovery_schema.validate(con)
        if version >= 17:
            from . import sync_source_readmit_schema2
            # Exact historical dispatch grants no newer producer capability.
            # Explicit migrate-db below upgrades an older recognized variant.
            sync_source_readmit_schema2.detect(con)
    except sqlite3.DatabaseError as exc:
        raise SchemaVersionError(str(exc)) from exc


def _seed_metadata(con):
    for key, value in (("library_id", str(uuid.uuid4())), ("library_epoch", "1"), ("kb_revision", "0")):
        con.execute("INSERT OR IGNORE INTO meta(key,value) VALUES (?,?)", (key, value))
    con.execute("INSERT OR REPLACE INTO meta(key,value) VALUES ('migration_version',?)", (str(SCHEMA_VERSION),))


def _integrity(con):
    messages = [row[0] for row in con.execute("PRAGMA integrity_check")]
    if messages != ["ok"] or con.execute("PRAGMA foreign_key_check").fetchone():
        raise SchemaVersionError("Database integrity/reference check failed; migration rolled back")


def initialize(path):
    """Initialize a new/empty database; an existing legacy database is refused."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path, timeout=30)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    try:
        con.execute("BEGIN IMMEDIATE")
        if _tables(con):
            validate(con)
            con.rollback()
            return MigrationResult(SCHEMA_VERSION, False)
        _create_schema(con, readmit_variant=_current_readmit_variant())
        _seed_metadata(con)
        from . import personal_schema
        personal_schema.seed(con, path.parent)
        validate(con)
        _integrity(con)
        con.commit()
        con.execute("PRAGMA journal_mode=WAL")
        return MigrationResult(SCHEMA_VERSION, True)
    except Exception:
        con.rollback()
        raise
    finally:
        con.close()


def _counts(con):
    return {table: con.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0]
            for table in sorted(_tables(con))}


def _attachments(path):
    root = path.parent / "personal-attachments"
    result = []
    if root.exists():
        for file in sorted(root.rglob("*")):
            if file.is_file():
                if not file.resolve().is_relative_to(root.resolve()):
                    raise SchemaVersionError("Attachment manifest contains a path outside its directory")
                digest = hashlib.sha256()
                with file.open("rb") as stream:
                    for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                        digest.update(chunk)
                result.append({"storage_key": file.relative_to(root).as_posix(),
                               "byte_size": file.stat().st_size, "sha256": digest.hexdigest()})
    return result


def _backup(con, path, version, row_counts, *, profile):
    from . import storage_backups, storage_lifecycle
    storage_lifecycle.require_guard(profile)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    backup = path.with_name(f"{path.name}.bak-migrate-{stamp}-{uuid.uuid4().hex[:8]}")
    storage_backups.create(con, backup, profile=profile, kind="migration")
    image = sqlite3.connect(backup.as_uri() + "?mode=ro&immutable=1", uri=True)
    try:
        _integrity(image)
    finally:
        image.close()
    # Keep the released migration explanation distinct from the protected
    # .reno-backup.json dependency descriptor produced by storage_backups.
    manifest = backup.with_name(backup.name + ".json")
    manifest.write_text(json.dumps({
        "from_version": version, "to_version": SCHEMA_VERSION,
        "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        "row_counts": row_counts, "attachments": _attachments(path),
        "attachment_files_copied": False,
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup, manifest


def _legacy_time(value):
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            return None  # Old timezone is unknown; do not invent event ordering.
        return parsed.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")
    except (AttributeError, TypeError, ValueError):
        return None


def _upgrade_v0(con):
    tables = _tables(con)
    for required in ("meta", "video_asset", "knowledge_atom", "user_decision"):
        if required not in tables:
            raise SchemaVersionError(f"Unrecognized legacy database: missing {required}")
    # Preserve old, globally-keyed step outputs. The legacy table remains as an
    # audit copy; no DROP, step-marker clearing, or lossy regeneration is used.
    archived = {}
    for table in STEP_TABLES:
        columns = _columns(con, table)
        if columns and "video_id" not in {name for name, c in columns.items() if c["pk"]}:
            archive = "migration_legacy_v0_" + table
            if archive in tables:
                raise SchemaVersionError(f"Existing migration archive {archive}; inspect before retrying")
            con.execute(f"ALTER TABLE {_q(table)} RENAME TO {_q(archive)}")
            archived[table] = (archive, columns)
    for table, columns in ADDITIONS.items():
        actual = _columns(con, table)
        if actual:
            for name, declaration in columns.items():
                if name not in actual:
                    con.execute(f"ALTER TABLE {_q(table)} ADD COLUMN {_q(name)} {declaration}")
    _create_schema(con)
    for table, (archive, original_columns) in archived.items():
        common = _columns(con, table).keys() & original_columns.keys()
        names = ",".join(_q(name) for name in sorted(common))
        con.execute(f"INSERT INTO {_q(table)} ({names}) SELECT {names} FROM {_q(archive)}")
        con.execute(f"CREATE INDEX {_q('idx_' + table + '_video_v1')} ON {_q(table)}(video_id)")
    _seed_metadata(con)
    library_id = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    for row in con.execute("SELECT * FROM user_decision WHERE event_id IS NULL").fetchall():
        event_id = str(uuid.uuid5(uuid.UUID(library_id), f"legacy-decision:{row['decision_id']}"))
        snapshot = json.dumps({"binding_status": "legacy_unbound", "atom_id": row["atom_id"],
                               "conflict_id": row["conflict_id"], "created_at": row["created_at"]}, ensure_ascii=False)
        con.execute("""UPDATE user_decision SET event_id=?,client_id='legacy',decided_at=?,
                       target_snapshot_json=?,event_schema_version=0 WHERE decision_id=?""",
                    (event_id, _legacy_time(row["created_at"]), snapshot, row["decision_id"]))


def _upgrade_v1(con):
    """Add queue ownership while retaining every historical job row."""
    from .tasks import normalize, _canonical, TaskError
    actual = _columns(con, "job")
    for name, declaration in ADDITIONS["job"].items():
        if name not in actual:
            con.execute(f"ALTER TABLE job ADD COLUMN {_q(name)} {declaration}")
    _create_schema(con)
    library_id = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    seen_active = {}
    for row in con.execute("SELECT * FROM job ORDER BY job_id").fetchall():
        request_id = row["request_id"] or str(uuid.uuid5(uuid.UUID(library_id), f"legacy-job:{row['job_id']}"))
        try:
            payload = json.loads(row["payload_json"] or "{}")
            scope, payload = normalize(row["kind"], row["video_id"], payload)
        except (TaskError, ValueError, TypeError):
            con.execute("UPDATE job SET status='failed',error='旧任务参数需人工核对',request_id=? WHERE job_id=?",
                        (request_id, row["job_id"]))
            continue
        status, error = row["status"], row["error"]
        if status in ("pending", "running"):
            if scope in seen_active:
                status, error = "superseded", f"迁移时合并至任务 #{seen_active[scope]}；历史记录保留"
            else:
                seen_active[scope] = row["job_id"]
                status = "pending"  # Explicit maintenance window: old process is stopped.
        con.execute("""UPDATE job SET request_id=?,scope_key=?,payload_json=?,status=?,error=?,
                       owner_id=NULL,lease_until=NULL WHERE job_id=?""",
                    (request_id, scope, _canonical(payload), status, error, row["job_id"]))
        signature = hashlib.sha256(_canonical({"kind": row["kind"], "video_id": row["video_id"],
                                               "payload": payload}).encode()).hexdigest()
        con.execute("INSERT OR IGNORE INTO task_request(request_id,job_id,payload_signature,created_at) VALUES (?,?,?,?)",
                    (request_id, row["job_id"], signature, row["created_at"] or datetime.now(timezone.utc).isoformat()))
    _seed_metadata(con)


def migrate(path, *, profile=None):
    """Upgrade an existing DB explicitly, retaining backup and a row/file manifest.

    A reserved write lock prevents concurrent writes while the independent
    backup connection captures the committed database. Stop application writes
    before calling: this lock alone does not constitute a maintenance window.
    """
    from . import config, storage_admin, storage_lifecycle
    path = Path(path).absolute()
    if not path.is_file():
        raise SchemaVersionError("Database does not exist; run: python -m reno init-db")
    key = lambda value: os.path.normcase(os.path.abspath(os.fspath(value)))
    if profile is None:
        if key(path) != key(config.db_path()):
            raise storage_lifecycle.StorageLifecycleError("STORAGE_PROFILE_REQUIRED", 400)
        profile = storage_admin.profile()
    if not isinstance(profile, storage_lifecycle.StorageProfile) or key(profile.database) != key(path):
        raise storage_lifecycle.StorageLifecycleError("STORAGE_PROFILE_MISMATCH", 400)
    # Offline facility setup is a separate explicit action. Even a no-op
    # migration opens its SQL connection only under established protection.
    with storage_lifecycle.shared(profile):
        return _migrate_locked(path, profile)


def _migrate_locked(path, profile):
    from . import storage_lifecycle
    storage_lifecycle.require_guard(profile)
    con = _open(path)
    try:
        # SQLite table rebuilds with circular job/budget FKs require this on the
        # private migration connection before BEGIN. Ordinary readers/writers
        # keep enforcement enabled; _integrity checks all references pre/post.
        con.execute("PRAGMA foreign_keys=OFF")
        con.execute("BEGIN IMMEDIATE")
        version = _version(con)
        upgrade_readmit = False
        if version == 17:
            from . import sync_source_readmit_schema2, sync_source_readmit_upgrade2
            try:
                upgrade_readmit = sync_source_readmit_schema2.detect(con) != "admission2_dispose2"
            except sqlite3.DatabaseError as exc:
                raise SchemaVersionError("Schema17 source readmission contract is invalid; database left unchanged") from exc
        if version == SCHEMA_VERSION and not upgrade_readmit:
            validate(con)
            if version == 17:
                # Delegate even a same-variant replay: the actual upgrader
                # checks all immutable raw records and FK references too.
                sync_source_readmit_upgrade2.upgrade_locked(con, profile=profile)
            con.rollback()
            return MigrationResult(SCHEMA_VERSION, False)
        if SCHEMA_VERSION >= 17 and version < 17:
            from . import sync_source_readmit_schema2
            try:
                sync_source_readmit_schema2.preflight(con, require_absent=True)
            except sqlite3.DatabaseError as exc:
                raise SchemaVersionError("Schema17 source readmission name collision; explicit maintenance is required") from exc
        if version < 16:
            from . import home_recovery_schema
            try:
                home_recovery_schema.preflight(con, require_absent=True)
            except sqlite3.DatabaseError as exc:
                raise SchemaVersionError("Schema16 Home recovery name collision; explicit maintenance is required") from exc
        if version < 14 and con.execute("SELECT 1 FROM sqlite_master WHERE name='sync_queue_budget_receipt' COLLATE NOCASE").fetchone():
            # This operational namespace first belongs to schema14. An older
            # database's same-name object is unknown even if its DDL happens to
            # match; reject before backups or any existing-schema mutation.
            raise SchemaVersionError("Schema14 sync budget receipt name collision; explicit maintenance is required")
        if version < 15 and con.execute("SELECT 1 FROM sqlite_master WHERE name COLLATE NOCASE IN "
                "('sync_queue_source_receipt','idx_sync_source_terminal_item')").fetchone():
            raise SchemaVersionError("Schema15 source receipt name collision; explicit maintenance is required")
        _integrity(con)
        before = _counts(con)
        backup, manifest = _backup(con, path, version, before, profile=profile)
        if version == 0:
            _upgrade_v0(con)
        if version <= 1:
            _upgrade_v1(con)
        if version <= 2:
            from . import personal_schema
            personal_schema.install(con)
            personal_schema.seed(con, path.parent)
            _seed_metadata(con)
        if version <= 3:
            # Personal classifications are explicit, optional labels. Never
            # infer a historical card's stage from mutable project data or a
            # free-form time condition. Existing cards keep unknown labels.
            for column in ("space", "stage"):
                if column not in _columns(con, "decision_card"):
                    con.execute(f"ALTER TABLE decision_card ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")
            from . import personal_schema
            personal_schema.install(con)
            _seed_metadata(con)
        if version <= 4:
            from . import sync_schema
            sync_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 5:
            from . import sync_transfer_schema
            sync_transfer_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 6:
            from . import sync_checkpoint_schema
            sync_checkpoint_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 7:
            from . import sync_recovery_schema
            sync_recovery_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 8:
            for table in ("knowledge_cluster", "conflict_case"):
                if "derivation_json" not in _columns(con, table):
                    con.execute(f"ALTER TABLE {table} ADD COLUMN derivation_json TEXT")
            # Recreate only the affected revision triggers. Existing rows,
            # statuses and knowledge revision remain untouched by this DDL.
            for name in ("kb_cluster_update", "kb_conflict_update"):
                con.execute(f"DROP TRIGGER IF EXISTS {name}")
            for statement in _statements():
                if re.search(r"CREATE TRIGGER IF NOT EXISTS kb_(?:cluster|conflict)_update\b", statement):
                    con.execute(statement)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 9:
            from . import storage_schema
            storage_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 10:
            from . import budget_schema
            budget_schema.install(con)
            budget_schema.seed_legacy_job_lineage(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version == 11:
            from . import budget_schema
            budget_schema.upgrade_v11(con)
        if version in (11, 12):
            from . import budget_schema
            budget_schema.upgrade_v12(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version in (11, 12, 13):
            from . import budget_schema
            budget_schema.upgrade_v13(con)
        if version <= 13:
            from . import sync_schema
            sync_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version == 14:
            from . import sync_schema
            sync_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if version <= 15:
            from . import home_recovery_schema
            home_recovery_schema.install(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
        if SCHEMA_VERSION >= 17:
            if version < 17:
                # The additive four-table namespace joins this same backed-up
                # outer transaction. It never rewrites released paid/private
                # rows, StateStore bytes or permanent authority files.
                sync_source_readmit_schema2.install_dispose2(con)
                con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (str(SCHEMA_VERSION),))
            else:
                # A schema17 version label does not imply the latest CHECK
                # variant. Rebuild only through the existing lossless upgrader.
                sync_source_readmit_upgrade2.upgrade_locked(con, profile=profile)
        # Legacy decisions have no trustworthy content binding. Recompute the
        # compatibility status in the same transaction, otherwise old rejected
        # rows would still be silently excluded by the default SQL retrieval.
        # v12/v13 preserve every old budget/task column during CHECK rebuilds.
        # Do not rewrite derived review
        # states, knowledge revisions, queue history or personal identities.
        if version <= 3:
            from . import review
            review.replay(con)
        for table, count in before.items():
            if table != "meta" and con.execute(f"SELECT COUNT(*) FROM {_q(table)}").fetchone()[0] != count:
                raise SchemaVersionError(f"Migration changed row count for {table}; rolled back")
        validate(con)
        _integrity(con)
        con.commit()
        return MigrationResult(SCHEMA_VERSION, True, backup, manifest)
    except Exception:
        con.rollback()
        raise
    finally:
        try:
            if con.in_transaction:
                con.rollback()
            con.execute("PRAGMA foreign_keys=ON")
        finally:
            con.close()
