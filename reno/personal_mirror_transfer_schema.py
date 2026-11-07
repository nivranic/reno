"""Independent operational personal-mirror tables, explicitly installed only.

No personal facts, mutation receipts, identities, schema metadata or abilities
are seeded. The caller owns an actual profile guard and outer transaction.
These four tables are not part of the ten-table personal snapshot contract.
"""
from __future__ import annotations

import sqlite3
import uuid
from functools import lru_cache

from . import storage_admin, storage_lifecycle as lifecycle
from .storage_schema import _contract

FORMAT_VERSION = 1
INTENT = "personal_mirror_transfer_intent"
ATTACHMENT = "personal_mirror_transfer_attachment"
RECEIPT = "personal_mirror_transfer_receipt"
HEAD = "personal_mirror_transfer_head"


def _integer(name, minimum=0):
    return f"typeof({name})='integer' AND {name}>={minimum}"


TABLES = {
    INTENT: f"""operation_id TEXT PRIMARY KEY NOT NULL,
        format_version INTEGER NOT NULL CHECK({_integer('format_version', 1)} AND format_version=1),
        prepare_json TEXT NOT NULL, prepare_sha256 TEXT NOT NULL,
        snapshot_id TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        source_primary_id TEXT NOT NULL, source_primary_epoch INTEGER NOT NULL CHECK({_integer('source_primary_epoch', 1)}),
        receiver_library_id TEXT NOT NULL, receiver_node_id TEXT NOT NULL,
        previous_snapshot_id TEXT, previous_snapshot_sha256 TEXT,
        expected_revision INTEGER NOT NULL CHECK({_integer('expected_revision')} AND expected_revision<9223372036854775807),
        state TEXT NOT NULL CHECK(state IN ('prepared','installed')),
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
        CHECK(source_node_id=source_primary_id AND source_library_id<>receiver_library_id
            AND source_node_id<>receiver_node_id),
        CHECK((expected_revision=0 AND previous_snapshot_id IS NULL AND previous_snapshot_sha256 IS NULL)
            OR (expected_revision>0 AND previous_snapshot_id IS NOT NULL AND previous_snapshot_sha256 IS NOT NULL)),
        UNIQUE(source_library_id,source_primary_id,source_primary_epoch,snapshot_id),
        UNIQUE(operation_id,prepare_sha256,snapshot_id,snapshot_sha256),
        UNIQUE(operation_id,source_library_id,source_node_id,source_primary_id,source_primary_epoch,
            receiver_library_id,receiver_node_id)""",
    ATTACHMENT: f"""operation_id TEXT NOT NULL REFERENCES {INTENT}(operation_id),
        attachment_id TEXT NOT NULL, storage_key TEXT NOT NULL,
        sha256 TEXT NOT NULL, byte_size INTEGER NOT NULL CHECK({_integer('byte_size', 1)} AND byte_size<=1073741824),
        descriptor_json TEXT NOT NULL, descriptor_sha256 TEXT NOT NULL,
        state TEXT NOT NULL CHECK(state IN ('reserved','stored','installed')),
        local_file_device TEXT, local_file_inode TEXT, local_file_mtime_ns TEXT, local_file_ctime_ns TEXT,
        stored_at TEXT,
        CHECK((state='reserved' AND local_file_device IS NULL AND local_file_inode IS NULL
            AND local_file_mtime_ns IS NULL AND local_file_ctime_ns IS NULL AND stored_at IS NULL)
            OR (state IN ('stored','installed') AND local_file_device IS NOT NULL AND local_file_inode IS NOT NULL
                AND local_file_mtime_ns IS NOT NULL AND local_file_ctime_ns IS NOT NULL AND stored_at IS NOT NULL)),
        PRIMARY KEY(operation_id,attachment_id), UNIQUE(operation_id,storage_key)""",
    RECEIPT: f"""operation_id TEXT PRIMARY KEY NOT NULL,
        format_version INTEGER NOT NULL CHECK({_integer('format_version', 1)} AND format_version=1),
        prepare_sha256 TEXT NOT NULL, snapshot_id TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL,
        installed_revision INTEGER NOT NULL UNIQUE CHECK({_integer('installed_revision', 1)}),
        receipt_json TEXT NOT NULL, receipt_sha256 TEXT NOT NULL, committed_at TEXT NOT NULL,
        FOREIGN KEY(operation_id,prepare_sha256,snapshot_id,snapshot_sha256)
            REFERENCES {INTENT}(operation_id,prepare_sha256,snapshot_id,snapshot_sha256),
        UNIQUE(operation_id,installed_revision,snapshot_id,snapshot_sha256,receipt_sha256)""",
    HEAD: f"""slot INTEGER PRIMARY KEY NOT NULL CHECK({_integer('slot', 1)} AND slot=1),
        format_version INTEGER NOT NULL CHECK({_integer('format_version', 1)} AND format_version=1),
        operation_id TEXT NOT NULL,
        source_library_id TEXT NOT NULL, source_node_id TEXT NOT NULL,
        source_primary_id TEXT NOT NULL, source_primary_epoch INTEGER NOT NULL CHECK({_integer('source_primary_epoch', 1)}),
        receiver_library_id TEXT NOT NULL, receiver_node_id TEXT NOT NULL,
        revision INTEGER NOT NULL CHECK({_integer('revision', 1)}),
        snapshot_id TEXT NOT NULL, snapshot_sha256 TEXT NOT NULL, receipt_sha256 TEXT NOT NULL,
        updated_at TEXT NOT NULL,
        CHECK(source_node_id=source_primary_id AND source_library_id<>receiver_library_id
            AND source_node_id<>receiver_node_id),
        FOREIGN KEY(operation_id,revision,snapshot_id,snapshot_sha256,receipt_sha256)
            REFERENCES {RECEIPT}(operation_id,installed_revision,snapshot_id,snapshot_sha256,receipt_sha256),
        FOREIGN KEY(operation_id,source_library_id,source_node_id,source_primary_id,source_primary_epoch,
            receiver_library_id,receiver_node_id)
            REFERENCES {INTENT}(operation_id,source_library_id,source_node_id,source_primary_id,source_primary_epoch,
                receiver_library_id,receiver_node_id)""",
}
INDEXES = (
    f"CREATE INDEX idx_personal_mirror_intent_state ON {INTENT}(state,expected_revision)",
    f"CREATE INDEX idx_personal_mirror_attachment_state ON {ATTACHMENT}(operation_id,state)",
)
TRIGGERS = (
    # REPLACE's implicit DELETE omits DELETE triggers when recursive_triggers
    # is off. BEFORE INSERT rejects every protected key before that shortcut;
    # exact replay belongs to the later runtime's original-row lookup.
    f"""CREATE TRIGGER trg_personal_mirror_intent_insert BEFORE INSERT ON {INTENT}
        WHEN EXISTS(SELECT 1 FROM {INTENT} WHERE operation_id=NEW.operation_id
            OR (source_library_id=NEW.source_library_id AND source_primary_id=NEW.source_primary_id
                AND source_primary_epoch=NEW.source_primary_epoch AND snapshot_id=NEW.snapshot_id))
        BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_INTENT_KEY_REUSED'); END""",
    f"""CREATE TRIGGER trg_personal_mirror_attachment_insert BEFORE INSERT ON {ATTACHMENT}
        WHEN EXISTS(SELECT 1 FROM {ATTACHMENT} WHERE operation_id=NEW.operation_id
            AND (attachment_id=NEW.attachment_id OR storage_key=NEW.storage_key))
        BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_ATTACHMENT_KEY_REUSED'); END""",
    f"""CREATE TRIGGER trg_personal_mirror_receipt_insert BEFORE INSERT ON {RECEIPT}
        WHEN EXISTS(SELECT 1 FROM {RECEIPT} WHERE operation_id=NEW.operation_id
            OR installed_revision=NEW.installed_revision)
        BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_RECEIPT_KEY_REUSED'); END""",
    f"""CREATE TRIGGER trg_personal_mirror_head_insert BEFORE INSERT ON {HEAD}
        WHEN EXISTS(SELECT 1 FROM {HEAD} WHERE slot=NEW.slot)
        BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_HEAD_KEY_REUSED'); END""",
    f"CREATE TRIGGER trg_personal_mirror_receipt_update BEFORE UPDATE ON {RECEIPT} BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_RECEIPT_IMMUTABLE'); END",
    f"CREATE TRIGGER trg_personal_mirror_receipt_delete BEFORE DELETE ON {RECEIPT} BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_RECEIPT_IMMUTABLE'); END",
    f"""CREATE TRIGGER trg_personal_mirror_intent_identity BEFORE UPDATE ON {INTENT}
        WHEN NEW.operation_id IS NOT OLD.operation_id OR NEW.format_version IS NOT OLD.format_version
            OR NEW.prepare_json IS NOT OLD.prepare_json OR NEW.prepare_sha256 IS NOT OLD.prepare_sha256
            OR NEW.snapshot_id IS NOT OLD.snapshot_id OR NEW.snapshot_sha256 IS NOT OLD.snapshot_sha256
            OR NEW.source_library_id IS NOT OLD.source_library_id OR NEW.source_node_id IS NOT OLD.source_node_id
            OR NEW.source_primary_id IS NOT OLD.source_primary_id OR NEW.source_primary_epoch IS NOT OLD.source_primary_epoch
            OR NEW.receiver_library_id IS NOT OLD.receiver_library_id OR NEW.receiver_node_id IS NOT OLD.receiver_node_id
            OR NEW.previous_snapshot_id IS NOT OLD.previous_snapshot_id OR NEW.previous_snapshot_sha256 IS NOT OLD.previous_snapshot_sha256
            OR NEW.expected_revision IS NOT OLD.expected_revision OR NEW.created_at IS NOT OLD.created_at
            OR (OLD.state='installed' AND NEW.state<>'installed')
        BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_INTENT_IMMUTABLE'); END""",
    f"CREATE TRIGGER trg_personal_mirror_intent_delete BEFORE DELETE ON {INTENT} BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_INTENT_IMMUTABLE'); END",
    f"""CREATE TRIGGER trg_personal_mirror_attachment_identity BEFORE UPDATE ON {ATTACHMENT}
        WHEN NEW.operation_id IS NOT OLD.operation_id OR NEW.attachment_id IS NOT OLD.attachment_id
            OR NEW.storage_key IS NOT OLD.storage_key OR NEW.sha256 IS NOT OLD.sha256
            OR NEW.byte_size IS NOT OLD.byte_size OR NEW.descriptor_json IS NOT OLD.descriptor_json
            OR NEW.descriptor_sha256 IS NOT OLD.descriptor_sha256
            OR (OLD.state='installed' AND NEW.state<>'installed')
            OR (OLD.state IN ('stored','installed') AND (
                NEW.local_file_device IS NOT OLD.local_file_device OR NEW.local_file_inode IS NOT OLD.local_file_inode
                OR NEW.local_file_mtime_ns IS NOT OLD.local_file_mtime_ns OR NEW.local_file_ctime_ns IS NOT OLD.local_file_ctime_ns
                OR NEW.stored_at IS NOT OLD.stored_at OR NEW.state='reserved'))
        BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_ATTACHMENT_IMMUTABLE'); END""",
    f"CREATE TRIGGER trg_personal_mirror_attachment_delete BEFORE DELETE ON {ATTACHMENT} BEGIN SELECT RAISE(ABORT,'PERSONAL_MIRROR_ATTACHMENT_IMMUTABLE'); END",
)
_PREFIXES = ("personal_mirror_transfer_", "idx_personal_mirror_", "trg_personal_mirror_")


class PersonalMirrorSchemaError(ValueError):
    def __init__(self, code):
        self.code = "PERSONAL_MIRROR_SCHEMA_" + code
        super().__init__(self.code)


def _fail(code):
    raise PersonalMirrorSchemaError(code) from None


def _objects(con, master):
    rows = con.execute(f"SELECT type,name,tbl_name,sql FROM {master} ORDER BY type,name")
    return [tuple(row) for row in rows if str(row[2]).lower() in TABLES
        or any(str(row[1]).lower().startswith(prefix) for prefix in _PREFIXES)]


def _install_reference(con):
    for name, declaration in TABLES.items():
        con.execute(f"CREATE TABLE {name} ({declaration})")
    for statement in INDEXES + TRIGGERS:
        con.execute(statement)


def _reference_key():
    return (tuple(TABLES.items()), tuple(INDEXES), tuple(TRIGGERS),
            tuple(_PREFIXES), sqlite3.sqlite_version)


def _freeze_reference(value):
    value_type = type(value)
    if (value is None or value_type is str or value_type is bytes or value_type is int
            or value_type is float or value_type is bool):
        return value
    if value_type is tuple or value_type is list:
        return tuple(_freeze_reference(child) for child in value)
    _fail("INCOMPATIBLE")


@lru_cache(maxsize=8)
def _reference_contract(key):
    """Immutable expected metadata only; never retain actual database state."""
    tables, indexes, triggers, prefixes, _sqlite_version = key
    table_names = frozenset(name for name, _declaration in tables)
    reference = sqlite3.connect(":memory:")
    try:
        for name, declaration in tables:
            reference.execute(f"CREATE TABLE {name} ({declaration})")
        for statement in indexes + triggers:
            reference.execute(statement)
        # Only the captured key governs reference construction and filtering.
        rows = reference.execute("SELECT type,name,tbl_name,sql FROM main.sqlite_master ORDER BY type,name")
        objects = [tuple(row) for row in rows if str(row[2]).lower() in table_names
            or any(str(row[1]).lower().startswith(prefix) for prefix in prefixes)]
        return (_freeze_reference(objects),
                tuple((name, _freeze_reference(_contract(reference, name))) for name, _declaration in tables))
    finally:
        reference.close()


def validate(con):
    """Pure read: require the exact complete schema, FKs, CHECKs and triggers."""
    from . import personal_mirror_sqlite as mirror_sql
    if type(con) is not sqlite3.Connection and not mirror_sql.is_owned(con):
        _fail("CONNECTION_REQUIRED")
    if _objects(con, "sqlite_temp_master"):
        _fail("TEMP_COLLISION")
    objects, contracts = _reference_contract(_reference_key())
    if _objects(con, "main.sqlite_master") != list(objects):
        _fail("INCOMPATIBLE")
    for table, expected in contracts:
        columns, refs, indexes, sql = expected
        if _contract(con, table) != (list(columns), list(refs), list(indexes), sql):
            _fail("INCOMPATIBLE")


def install(con, *, profile):
    """Explicit empty DDL under the actual guard and caller's outer transaction.

    No migration_version, meta, personal table, identity, source file, runtime
    capability or global configuration is changed. An existing exact facility
    is a no-op. A partial, drifted or colliding facility is never repaired.
    """
    if type(con) is not sqlite3.Connection or not con.in_transaction:
        _fail("OUTER_TRANSACTION_REQUIRED")
    profile = lifecycle._profile(profile)
    lifecycle.require_guard(profile)
    storage_admin.from_connection(con, profile=profile)
    if _objects(con, "sqlite_temp_master"):
        _fail("TEMP_COLLISION")
    if _objects(con, "main.sqlite_master"):
        validate(con)
        return False
    savepoint = "personal_mirror_install_" + uuid.uuid4().hex
    con.execute(f"SAVEPOINT {savepoint}")
    try:
        _install_reference(con)
        validate(con)
    except BaseException as exc:
        try:
            con.execute(f"ROLLBACK TO {savepoint}")
            con.execute(f"RELEASE {savepoint}")
        except sqlite3.Error:
            exc.add_note("PERSONAL_MIRROR_SCHEMA_SAVEPOINT_ROLLBACK_FAILED")
        raise
    con.execute(f"RELEASE {savepoint}")
    return True
