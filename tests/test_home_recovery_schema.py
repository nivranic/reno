"""The new receipt DDL/pure contract never creates a restore or admission."""
from copy import deepcopy
import hashlib
import json
import sqlite3
from types import MappingProxyType
import uuid

import pytest

from reno import home_recovery_schema as schema


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def set_json(row, name, value):
    raw = encoded(value)
    row[name] = raw
    row[name[:-5] + "_sha256"] = hashlib.sha256(raw.encode()).hexdigest()


def record(*, lineage=False):
    value = {name: "a" * 64 for name in schema.COLUMNS if name.endswith("sha256")}
    value.update({name: str(uuid.uuid4()) for name in ("operation_id", "library_id", "node_id", "source_id", "protection_backup_id")})
    value.update(format_version=1, action="knowledge_restore", source_kind="home_checkpoint",
        before_library_epoch=3, before_kb_revision=12, after_library_epoch=4, after_kb_revision=17,
        original_core_sha256=None, root_job_id=None, continuation_id=None, execution_generation=None,
        committed_at="2026-10-02T01:02:03.123456Z")
    if lineage:
        value.update(original_core_sha256="b" * 64, root_job_id=1,
                     continuation_id=str(uuid.uuid4()), execution_generation=1)
    for prefix in ("before", "after"):
        set_json(value, prefix + "_version_json", {"library_id": value["library_id"],
            "library_epoch": value[prefix + "_library_epoch"], "kb_revision": value[prefix + "_kb_revision"]})
    set_json(value, "frontier_json", {"root_job_id": value["root_job_id"], "operations": []})
    set_json(value, "checkpoint_json", {"state_sha256": "c" * 64, "outboxes": []})
    set_json(value, "request_json", {"format_version": 1, "operation_id": value["operation_id"],
                                 "source_id": value["source_id"]})
    set_json(value, "result_json", {"format_version": 1, "operation_id": value["operation_id"],
                                  "state": "committed"})
    return value


@pytest.fixture
def con():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE job(job_id INTEGER PRIMARY KEY)")
    connection.execute("INSERT INTO job VALUES(1)")
    yield connection
    connection.close()


def insert(con, row):
    return con.execute("INSERT INTO " + schema.TABLE + "(" + ",".join(schema.COLUMNS) + ") VALUES(" +
                       ",".join("?" for _ in schema.COLUMNS) + ")", [row[name] for name in schema.COLUMNS])


def objects(con):
    return [tuple(row) for row in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def test_install_only_adds_empty_receipt_ddl_and_is_losslessly_idempotent(con):
    original_job = con.execute("SELECT * FROM job").fetchall()
    assert not schema.preflight(con)
    schema.install(con)
    schema.validate(con)
    assert schema.preflight(con, require_absent=False)
    assert con.execute("SELECT * FROM " + schema.TABLE).fetchall() == []
    assert con.execute("SELECT * FROM job").fetchall() == original_job
    before = objects(con)
    schema.install(con)
    assert objects(con) == before
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name='meta'").fetchone()


def test_valid_detached_record_supports_no_pending_core_and_full_original_lineage(con):
    schema.install(con)
    for lineage in (False, True):
        row = record(lineage=lineage)
        result = schema.validate_record(MappingProxyType(row))
        assert result == row and result is not row
        result["node_id"] = str(uuid.uuid4())
        assert result != row
        row["library_id"] = str(uuid.uuid4()) if lineage else row["library_id"]
        if lineage:
            for prefix in ("before", "after"):
                body = json.loads(row[prefix + "_version_json"])
                body["library_id"] = row["library_id"]
                set_json(row, prefix + "_version_json", body)
        insert(con, schema.validate_record(row))
    assert con.execute("SELECT count(*) FROM " + schema.TABLE).fetchone()[0] == 2
    con.row_factory = sqlite3.Row
    assert schema.validate_record(con.execute("SELECT * FROM " + schema.TABLE + " LIMIT 1").fetchone())


@pytest.mark.parametrize("prefix,key", [("before", "library_id"), ("after", "library_id"),
                                       ("before", "library_epoch"), ("after", "kb_revision")])
def test_version_json_cannot_disagree_with_epoch_revision_or_library_columns(prefix, key):
    row = record()
    body = json.loads(row[prefix + "_version_json"])
    body[key] = str(uuid.uuid4()) if key == "library_id" else body[key] + 1
    set_json(row, prefix + "_version_json", body)
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)


@pytest.mark.parametrize("field,value", [("operation_id", "A" * 36), ("source_sha256", "A" * 64),
    ("source_kind", "cloud_archive"), ("action", "resume"), ("format_version", True),
    ("after_library_epoch", 5), ("after_kb_revision", 12), ("after_operational_sha256", "b" * 64),
    ("before_library_epoch", True), ("before_kb_revision", -1), ("committed_at", "2026-02-31T01:02:03.123456Z"),
    ("committed_at", "2026-10-02T01:02:03.123Z")])
def test_invalid_record_identity_type_epoch_bounds_and_calendar_rejected(field, value):
    row = record()
    row[field] = value
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)


@pytest.mark.parametrize("field", ["original_core_sha256", "root_job_id", "continuation_id", "execution_generation"])
def test_optional_lineage_cannot_be_partially_present(field):
    row = record(lineage=True)
    row[field] = None
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)


@pytest.mark.parametrize("field", ["request_json", "result_json", "frontier_json", "checkpoint_json", "before_version_json", "after_version_json"])
def test_every_json_requires_object_canonical_bytes_and_its_original_digest(field):
    row = record()
    set_json(row, field, [])
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)
    row = record()
    row[field] = "{ }"
    row[field[:-5] + "_sha256"] = hashlib.sha256(row[field].encode()).hexdigest()
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)
    row = record()
    row[field[:-5] + "_sha256"] = "f" * 64
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)


@pytest.mark.parametrize("raw", ['{"duplicate":1,"duplicate":2}', '{"invalid":NaN}', '{"invalid":Infinity}', '{"invalid":1e400}'])
def test_duplicate_or_nonfinite_json_is_not_normalized_into_a_new_proof(raw):
    row = record()
    row["frontier_json"] = raw
    row["frontier_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)


@pytest.mark.parametrize("body", [{"response_body": "artificial-private"}, {"nested": {"AUTH-TOKEN": "artificial-secret"}},
                                  {"nested": {"authToken": "artificial-secret"}}, {"lease_token": str(uuid.uuid4())},
                                  {"path": "data/private.sqlite"}, {"value": "E:\\private\\identity.json"},
                                  {"value": "/var/tmp/private.sqlite"}, {"value": "data/private.sqlite"},
                                  {"value": "https://private.invalid/credential"}])
def test_result_receipt_cannot_contain_raw_secret_body_or_paths(body):
    row = record()
    set_json(row, "result_json", body)
    with pytest.raises(schema.HomeRecoveryRecordError, match="HOME_RECOVERY_PRIVATE_RECEIPT") as caught:
        schema.validate_record(row)
    assert "artificial-private" not in str(caught.value) and "credential" not in str(caught.value)


def test_record_columns_are_closed_and_json_limits_are_utf8_bounded(monkeypatch):
    row = record()
    row["never_dispatched"] = True
    with pytest.raises(schema.HomeRecoveryRecordError):
        schema.validate_record(row)
    row = record()
    set_json(row, "request_json", {"detail": "中" * 6000})
    with pytest.raises(schema.HomeRecoveryRecordError, match="HOME_RECOVERY_JSON_LIMIT"):
        schema.validate_record(row)
    row = record()
    value = {}
    for _ in range(26):
        value = {"nested": value}
    set_json(row, "frontier_json", value)
    with pytest.raises(schema.HomeRecoveryRecordError, match="HOME_RECOVERY_JSON_LIMIT"):
        schema.validate_record(row)
    monkeypatch.setattr(schema, "MAX_JSON_NODES", 1)
    with pytest.raises(schema.HomeRecoveryRecordError, match="HOME_RECOVERY_JSON_LIMIT"):
        schema.validate_record(record())


def test_canonical_encoder_rejects_non_utf8_and_nonfinite_values():
    for value in ({"value": "\ud800"}, {"value": float("nan")}, {"value": float("inf")}):
        with pytest.raises(schema.HomeRecoveryRecordError):
            schema.canonical_json(value)


@pytest.mark.parametrize("field,value", [("operation_id", "X" * 36), ("source_sha256", "X" * 64),
    ("source_kind", "cloud_archive"), ("action", "resume"), ("after_library_epoch", 5),
    ("after_kb_revision", 12), ("after_operational_sha256", "b" * 64), ("root_job_id", 1),
    ("request_json", '[]'), ("result_json", '{"large":"' + "x" * 16384 + '"}')])
def test_sql_constraints_reject_malformed_durable_receipts(con, field, value):
    schema.install(con)
    row = record()
    row[field] = value
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, row)
    assert con.execute("SELECT count(*) FROM " + schema.TABLE).fetchone()[0] == 0


def test_append_only_triggers_epoch_uniqueness_and_original_job_fk(con):
    schema.install(con)
    row = record(lineage=True)
    insert(con, row)
    with pytest.raises(sqlite3.IntegrityError, match="HOME_RECOVERY_IMMUTABLE"):
        con.execute("UPDATE " + schema.TABLE + " SET result_json='{}'")
    with pytest.raises(sqlite3.IntegrityError, match="HOME_RECOVERY_IMMUTABLE"):
        con.execute("DELETE FROM " + schema.TABLE)
    same_epoch = deepcopy(row)
    same_epoch["operation_id"] = str(uuid.uuid4())
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, same_epoch)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM job WHERE job_id=1")
    assert con.execute("SELECT operation_id FROM " + schema.TABLE).fetchone()[0] == row["operation_id"]


@pytest.mark.parametrize("collision", ["table", "same_shape", "uppercase", "index", "trigger", "temp"])
def test_v15_future_namespace_collisions_are_refused_without_adoption(con, collision):
    if collision == "same_shape":
        schema.install(con)
    elif collision in {"table", "uppercase", "temp"}:
        name = schema.TABLE.upper() if collision == "uppercase" else schema.TABLE
        con.execute("CREATE " + ("TEMP " if collision == "temp" else "") + "TABLE " + name + "(original_secret BLOB)")
    elif collision == "index":
        con.execute("CREATE INDEX IDX_HOME_RECOVERY_EPOCH ON job(job_id)")
    else:
        con.execute("CREATE TRIGGER home_recovery_no_delete AFTER INSERT ON job BEGIN SELECT 1; END")
    before = objects(con)
    with pytest.raises(schema.HomeRecoverySchemaError, match="HOME_RECOVERY_SCHEMA_COLLISION"):
        schema.preflight(con)
    assert objects(con) == before


@pytest.mark.parametrize("damage", ["epoch_check", "operational_check", "fk_action", "index_predicate", "trigger", "extra_trigger"])
def test_full_shape_rejects_same_columns_with_damaged_checks_fk_indexes_or_triggers(con, damage):
    if damage in {"epoch_check", "operational_check", "fk_action"}:
        declaration = schema.TABLES[schema.TABLE]
        if damage == "epoch_check":
            declaration = declaration.replace("CHECK(after_library_epoch=before_library_epoch+1)", "CHECK(after_library_epoch>=1)")
        elif damage == "operational_check":
            declaration = declaration.replace("CHECK(before_operational_sha256=after_operational_sha256)", "CHECK(1)")
        else:
            declaration = declaration.replace("REFERENCES job(job_id)", "REFERENCES job(job_id) ON DELETE SET NULL")
        con.execute("CREATE TABLE " + schema.TABLE + "(" + declaration + ")")
        for sql in schema.INDEXES + schema.TRIGGERS:
            con.execute(sql)
    else:
        schema.install(con)
        if damage == "index_predicate":
            con.execute("DROP INDEX idx_home_recovery_root")
            con.execute("CREATE INDEX idx_home_recovery_root ON " + schema.TABLE + "(root_job_id,execution_generation,committed_at,operation_id)")
        elif damage == "trigger":
            con.execute("DROP TRIGGER home_recovery_no_update")
        else:
            con.execute("CREATE TRIGGER unknown_after_home_restore AFTER INSERT ON " + schema.TABLE + " BEGIN SELECT 1; END")
    before = objects(con)
    with pytest.raises(schema.HomeRecoverySchemaError):
        schema.validate(con)
    with pytest.raises(schema.HomeRecoverySchemaError):
        schema.install(con)
    assert objects(con) == before


def test_ddl_and_validation_respect_caller_transaction_and_query_only(con):
    con.commit()
    con.execute("BEGIN IMMEDIATE")
    before = objects(con)
    schema.install(con)
    assert con.in_transaction
    con.rollback()
    assert objects(con) == before
    schema.install(con)
    con.commit()
    con.execute("PRAGMA query_only=ON")
    schema.validate(con)
    schema.install(con)
    assert con.execute("SELECT * FROM " + schema.TABLE).fetchall() == []


@pytest.mark.parametrize("job_sql", ["CREATE TABLE job(job_id TEXT PRIMARY KEY)",
    "CREATE TABLE job(job_id INTEGER,generation INTEGER,PRIMARY KEY(job_id,generation))"])
def test_install_refuses_an_incompatible_original_job_key_before_new_ddl(job_sql):
    with sqlite3.connect(":memory:") as con:
        con.execute(job_sql)
        before = objects(con)
        with pytest.raises(schema.HomeRecoverySchemaError):
            schema.install(con)
        assert objects(con) == before
