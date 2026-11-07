"""Isolated schema/record contracts cannot manufacture continuity or admission."""
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
from types import MappingProxyType
import uuid

import pytest

from reno import sync_source_readmit_contract as wire, sync_source_readmit_schema as schema
from tests import test_sync_source_readmit_contract as sample


def set_json(row, name, value):
    raw = wire.canonical_json(value, maximum=schema.MAX_CONTROL_BYTES)
    row[name + "_json"] = raw
    row[name + "_sha256"] = hashlib.sha256(raw.encode("utf-8")).hexdigest()


def identities(source=sample.SOURCE, target=sample.TARGET):
    return {prefix + "_" + key: value[key] for prefix, value in (("source", source), ("target", target))
            for key in ("library_id", "node_id")}


def activation():
    contract = {"format_version": 1, "continuity_id": sample.identifier(),
        "owner_profile_sha256": "a" * 64,
        "source_identity": {key: sample.SOURCE[key] for key in ("library_id", "node_id")},
        "target_identity": deepcopy(sample.TARGET), "target_epoch": sample.STREAM["library_epoch"],
        "activated_at": sample.COMMITTED}
    row = {key: contract[key] for key in ("format_version", "continuity_id", "owner_profile_sha256", "target_epoch", "activated_at")}
    row.update(identities())
    set_json(row, "contract", contract)
    return row


def registration(*, active=None, old=None):
    active = active or activation()
    body = sample.register(old)
    body["continuity"] = {"format_version": 1, "continuity_id": active["continuity_id"],
        "contract_sha256": active["contract_sha256"], "target_epoch": active["target_epoch"]}
    result = sample.registration(body)
    row = {key: result[key] for key in ("request_id", "format_version", "continuity_id", "core_sha256", "item_id",
        "original_request_id", "original_request_sha256", "original_kind", "registration_sha256", "committed_at")}
    row.update(identities(body["source"], body["target"]))
    set_json(row, "original_request", body["original_request"])
    set_json(row, "request", body)
    set_json(row, "result", result)
    return row


def disposition(*, registered=None, applied=True, old=None, parent=None):
    old = old or sample.old_request(legacy=registered is None)
    body = sample.dispose(old)
    if registered is not None:
        body["registration"] = json.loads(registered["result_json"])
    result = sample.disposition(body, applied=applied, parent=parent)
    row = {key: result[key] for key in ("request_id", "format_version", "continuity_id", "core_sha256", "item_id",
        "original_request_id", "original_request_sha256", "original_kind", "registration_sha256", "disposition", "committed_at")}
    row.update(identities(body["source"], body["target"]), disposition_sha256=wire.disposition_sha256(result),
               defer_receipt_json=None, defer_receipt_sha256=None)
    set_json(row, "original_request", old)
    set_json(row, "request", body)
    set_json(row, "result", result)
    if parent is not None:
        set_json(row, "defer_receipt", parent)
    return row


def admission(*, body=None, refund=False):
    body = body or sample.readmit(refund=refund)
    result = sample.admission(body)
    row = {key: result[key] for key in ("admission_id", "format_version", "core_sha256", "item_id",
        "submitted_url_sha256", "current_home_epoch", "recovery_generation", "control_revision",
        "previous_admission_id", "attempts", "attempt_basis", "admission_sha256", "committed_at")}
    row.update(identities(body["source"], body["target"]),
        receiver_library_epoch=result["recovery_stream"]["library_epoch"],
        lease_generation=result["claim"]["lease_generation"])
    set_json(row, "request", body)
    set_json(row, "result", result)
    return row


def parent_resume(*, registered=False):
    parent_body = sample.old_request(legacy=not registered)
    parent = sample.old_receipt(parent_body)
    old = sample.old_request("budget_resume", legacy=not registered, defer_request_id=parent["request_id"])
    return old, parent


@pytest.fixture
def con():
    connection = sqlite3.connect(":memory:")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("CREATE TABLE sync_queue(item_id INTEGER PRIMARY KEY)")
    connection.executemany("INSERT INTO sync_queue VALUES(?)", ((7,), (8,)))
    connection.execute("CREATE TABLE personal_blob(id INTEGER PRIMARY KEY,value BLOB)")
    connection.execute("INSERT INTO personal_blob VALUES(1,?)", (b"\x00\xffsynthetic-local-data",))
    connection.commit()
    yield connection
    connection.close()


def insert(con, table, row):
    columns = schema.COLUMNS[table]
    return con.execute("INSERT INTO " + table + "(" + ",".join(columns) + ") VALUES(" +
                       ",".join("?" for _ in columns) + ")", [row[key] for key in columns])


def objects(con):
    return [tuple(row) for row in con.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name")]


def test_install_only_adds_empty_ddl_and_never_seeds_facts_or_commits(con):
    before_personal = con.execute("SELECT * FROM personal_blob").fetchall()
    assert not schema.preflight(con)
    con.execute("BEGIN IMMEDIATE")
    schema.install(con)
    assert con.in_transaction
    schema.validate(con)
    assert schema.preflight(con, require_absent=False)
    assert all(con.execute("SELECT COUNT(*) FROM " + table).fetchone()[0] == 0 for table in schema.TABLES)
    assert con.execute("SELECT * FROM personal_blob").fetchall() == before_personal
    before = objects(con)
    schema.install(con)
    assert objects(con) == before
    assert not con.execute("SELECT 1 FROM sqlite_master WHERE name IN ('meta','sync_pair','sync_queue_source_receipt')").fetchone()
    con.rollback()
    assert not any(con.execute("SELECT 1 FROM sqlite_master WHERE name=?", (table,)).fetchone() for table in schema.TABLES)


@pytest.mark.parametrize("table,builder", [(schema.ACTIVATION, activation), (schema.REGISTRATION, registration),
    (schema.DISPOSITION, disposition), (schema.ADMISSION, admission)])
def test_records_are_detached_closed_exact_canonical_values(table, builder):
    row = builder()
    checked = schema.validate_record(table, MappingProxyType(row))
    assert checked == row and checked is not row
    checked["source_node_id"] = sample.identifier()
    assert checked != row
    for mutation in ({**row, "unknown": "private"}, {key: value for key, value in row.items() if key != "format_version"}):
        with pytest.raises(schema.SourceReadmitRecordError):
            schema.validate_record(table, mutation)


def test_all_four_complete_records_roundtrip_sqlite_rows_and_foreign_keys(con):
    schema.install(con)
    active = activation()
    registered = registration(active=active)
    old = json.loads(registered["original_request_json"])
    disposed = disposition(registered=registered, applied=False, old=old)
    records = ((schema.ACTIVATION, active), (schema.REGISTRATION, registered),
               (schema.DISPOSITION, disposed), (schema.ADMISSION, admission()))
    for table, row in records:
        insert(con, table, schema.validate_record(table, row))
    con.row_factory = sqlite3.Row
    for table, row in records:
        assert schema.validate_record(table, con.execute("SELECT * FROM " + table).fetchone()) == row
    assert con.execute("PRAGMA foreign_key_check").fetchone() is None


@pytest.mark.parametrize("table,builder", [(schema.ACTIVATION, activation), (schema.REGISTRATION, registration),
    (schema.DISPOSITION, disposition), (schema.ADMISSION, admission)])
@pytest.mark.parametrize("field,value", [("format_version", True), ("source_node_id", "A" * 36),
    ("source_library_id", "ffffffff-ffff-ffff-ffff-ffffffffffff")])
def test_typed_fields_and_independent_peer_columns_cannot_be_forged(table, builder, field, value):
    row = builder()
    row[field] = value
    with pytest.raises(schema.SourceReadmitRecordError, match="^SOURCE_READMIT_RECORD_INVALID$"):
        schema.validate_record(table, row)


@pytest.mark.parametrize("table,builder,field", [(schema.ACTIVATION, activation, "contract"),
    (schema.REGISTRATION, registration, "request"), (schema.REGISTRATION, registration, "original_request"),
    (schema.REGISTRATION, registration, "result"), (schema.DISPOSITION, disposition, "request"),
    (schema.DISPOSITION, disposition, "result"), (schema.ADMISSION, admission, "request"),
    (schema.ADMISSION, admission, "result")])
@pytest.mark.parametrize("damage", ["sha", "noncanonical", "duplicate", "nonfinite", "root_list"])
def test_json_exact_bytes_digest_duplicate_finite_and_object_contract(table, builder, field, damage):
    row = builder()
    raw = row[field + "_json"]
    if damage == "sha":
        row[field + "_sha256"] = "0" * 64
    else:
        if damage == "noncanonical":
            raw = json.dumps(json.loads(raw), ensure_ascii=False, indent=1)
        elif damage == "duplicate":
            raw = '{"format_version":1,"format_version":1}'
        elif damage == "nonfinite":
            raw = '{"value":NaN}'
        else:
            raw = "[]"
        row[field + "_json"] = raw
        row[field + "_sha256"] = hashlib.sha256(raw.encode()).hexdigest()
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_record(table, row)


@pytest.mark.parametrize("builder,table", [(registration, schema.REGISTRATION), (disposition, schema.DISPOSITION),
    (admission, schema.ADMISSION)])
def test_stored_result_cannot_be_a_presentation_replay(builder, table):
    row = builder()
    result = json.loads(row["result_json"])
    result["replayed"] = True
    set_json(row, "result", result)
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_record(table, row)


@pytest.mark.parametrize("field", ["target_epoch", "owner_profile_sha256", "activated_at", "continuity_id"])
def test_activation_contract_binds_all_independent_fields(field):
    row = activation()
    row[field] = {"target_epoch": 8, "owner_profile_sha256": "b" * 64,
                  "activated_at": "2031-01-01T00:00:00.000Z", "continuity_id": sample.identifier()}[field]
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_activation_record(row)


@pytest.mark.parametrize("stamp", ["2030-02-31T00:00:00.000Z", "2030-01-01T00:00:00Z", "2030-01-01T00:00:00.000+00:00"])
def test_activation_rejects_impossible_or_noncanonical_calendar(stamp):
    row = activation()
    contract = json.loads(row["contract_json"])
    row["activated_at"] = contract["activated_at"] = stamp
    set_json(row, "contract", contract)
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_activation_record(row)


def test_applied_resume_has_its_exact_original_parent_permanently_available():
    old, parent = parent_resume()
    row = disposition(old=old, parent=parent)
    assert schema.validate_disposition_record(row) == row
    for mutation in ("missing", "half_pair", "wrong_item", "wrong_parent", "presentation_parent"):
        changed = dict(row)
        value = deepcopy(parent)
        if mutation == "missing":
            changed.update(defer_receipt_json=None, defer_receipt_sha256=None)
        elif mutation == "half_pair":
            changed["defer_receipt_sha256"] = None
        else:
            if mutation == "wrong_item":
                value["item_id"] = 8
            elif mutation == "wrong_parent":
                value["request_id"] = sample.identifier()
            else:
                value["replayed"] = True
            set_json(changed, "defer_receipt", value)
        with pytest.raises(schema.SourceReadmitRecordError):
            schema.validate_disposition_record(changed)


def test_negative_requires_original_registration_and_never_supposes_absence_is_authority():
    active = activation()
    registered = registration(active=active)
    old = json.loads(registered["original_request_json"])
    row = disposition(registered=registered, applied=False, old=old)
    assert schema.validate_disposition_record(row) == row
    request = json.loads(row["request_json"])
    request["registration"] = None
    result = json.loads(row["result_json"])
    result.update(continuity_id=None, registration_sha256=None)
    row.update(continuity_id=None, registration_sha256=None, disposition_sha256=wire.disposition_sha256(json.loads(row["result_json"])))
    set_json(row, "request", request)
    set_json(row, "result", result)
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_disposition_record(row)


@pytest.mark.parametrize("field,value", [("item_id", True), ("current_home_epoch", True),
    ("receiver_library_epoch", 8), ("recovery_generation", True), ("control_revision", True),
    ("lease_generation", 5), ("attempts", 4), ("attempt_basis", "original_defer_refund"),
    ("submitted_url_sha256", "f" * 64), ("admission_sha256", "0" * 64),
    ("previous_admission_id", sample.identifier())])
def test_admission_fixed_projection_binds_current_fence_and_original_authority(field, value):
    row = admission()
    row[field] = value
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_admission_record(row)


def test_refund_receipt_preserves_one_original_attempt_slot():
    row = admission(refund=True)
    assert schema.validate_admission_record(row)["attempt_basis"] == "original_defer_refund"
    body = json.loads(row["request_json"])
    assert row["attempts"] == body["defer_receipt"]["attempts"] + 1
    row["attempts"] += 1
    with pytest.raises(schema.SourceReadmitRecordError):
        schema.validate_admission_record(row)


@pytest.mark.parametrize("table,builder", [(schema.ACTIVATION, activation), (schema.REGISTRATION, registration),
    (schema.DISPOSITION, disposition), (schema.ADMISSION, admission)])
def test_each_permanent_table_rejects_update_and_delete(con, table, builder):
    schema.install(con)
    if table == schema.REGISTRATION:
        active = activation()
        insert(con, schema.ACTIVATION, active)
        row = registration(active=active)
    else:
        row = builder()
    insert(con, table, schema.validate_record(table, row))
    before = con.execute("SELECT * FROM " + table).fetchall()
    for sql in ("UPDATE " + table + " SET format_version=1", "DELETE FROM " + table):
        with pytest.raises(sqlite3.IntegrityError, match="SOURCE_READMIT_IMMUTABLE"):
            con.execute(sql)
        assert con.execute("SELECT * FROM " + table).fetchall() == before


def test_activation_identity_cannot_be_reissued_under_a_new_uuid(con):
    schema.install(con)
    first, second = activation(), activation()
    insert(con, schema.ACTIVATION, first)
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.ACTIVATION, second)


def test_registration_and_disposition_have_one_exact_outer_operation_per_original_uuid(con):
    schema.install(con)
    active = activation()
    insert(con, schema.ACTIVATION, active)
    first = registration(active=active)
    old = json.loads(first["original_request_json"])
    insert(con, schema.REGISTRATION, first)
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.REGISTRATION, registration(active=active, old=old))
    insert(con, schema.DISPOSITION, disposition(registered=first, applied=False, old=old))
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.DISPOSITION, disposition(registered=first, applied=True, old=old))


def next_admission(previous, *, expected=None):
    body = sample.readmit()
    body["previous_admission_id"] = previous["admission_id"]
    body["expected_claim"] = deepcopy(expected or json.loads(previous["result_json"])["claim"])
    return admission(body=body)


def test_admission_first_unique_claim_fence_and_no_fork_policy(con):
    schema.install(con)
    first = admission()
    insert(con, schema.ADMISSION, first)
    duplicate_first = admission()
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.ADMISSION, duplicate_first)
    child = next_admission(first)
    insert(con, schema.ADMISSION, schema.validate_admission_record(child))
    # A distinct generation avoids the claim unique index so the permanent
    # previous-admission no-fork index independently rejects this sibling.
    fork = next_admission(first, expected=json.loads(child["result_json"])["claim"])
    assert fork["lease_generation"] != child["lease_generation"]
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.ADMISSION, fork)
    next_value = next_admission(child)
    insert(con, schema.ADMISSION, schema.validate_admission_record(next_value))
    assert con.execute("SELECT COUNT(*) FROM " + schema.ADMISSION).fetchone()[0] == 3


def test_original_queue_key_and_record_foreign_references_cannot_be_lost(con):
    schema.install(con)
    value = admission()
    insert(con, schema.ADMISSION, value)
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM sync_queue WHERE item_id=7")
    orphan = next_admission(value)
    orphan["previous_admission_id"] = sample.identifier()
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.ADMISSION, orphan)


@pytest.mark.parametrize("field,value", [("format_version", 2), ("target_epoch", 0),
    ("owner_profile_sha256", "A" * 64), ("continuity_id", "A" * 36),
    ("contract_json", "[]"), ("activated_at", "2030-01-01T00:00:00Z")])
def test_sql_activation_checks_reject_malformed_private_durable_records(con, field, value):
    schema.install(con)
    row = activation()
    row[field] = value
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.ACTIVATION, row)


@pytest.mark.parametrize("field,value", [("attempts", 6), ("lease_generation", 0),
    ("current_home_epoch", 0), ("attempt_basis", "fresh_claim"), ("result_json", "[]"),
    ("recovery_generation", -1)])
def test_sql_admission_checks_reject_unsupported_slots_fences_and_result_shapes(con, field, value):
    schema.install(con)
    row = admission()
    row[field] = value
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.ADMISSION, row)


def test_sql_disposition_never_allows_a_registration_free_negative(con):
    schema.install(con)
    row = disposition()
    row["disposition"] = "not_applied"
    with pytest.raises(sqlite3.IntegrityError):
        insert(con, schema.DISPOSITION, row)


@pytest.mark.parametrize("collision", ["table", "case_table", "view", "index", "trigger", "temp_table", "temp_view", "exact_shape"])
def test_future_namespace_collision_never_adopts_existing_objects(con, collision):
    if collision == "exact_shape":
        schema.install(con)
    elif collision in {"table", "case_table"}:
        name = schema.ACTIVATION.upper() if collision == "case_table" else schema.ACTIVATION
        con.execute("CREATE TABLE " + name + "(unknown TEXT)")
    elif collision == "view":
        con.execute("CREATE VIEW " + schema.ACTIVATION + " AS SELECT 1 AS unknown")
    elif collision == "index":
        con.execute("CREATE INDEX idx_source_admission_first ON personal_blob(value)")
    elif collision == "trigger":
        con.execute("CREATE TRIGGER " + schema.ADMISSION + "_no_delete BEFORE DELETE ON personal_blob BEGIN SELECT 1; END")
    elif collision == "temp_table":
        con.execute("CREATE TEMP TABLE " + schema.ACTIVATION + "(unknown TEXT)")
    else:
        con.execute("CREATE TEMP VIEW " + schema.ACTIVATION + " AS SELECT 1 AS unknown")
    before = objects(con)
    with pytest.raises(schema.SourceReadmitSchemaError, match="SOURCE_READMIT_SCHEMA_COLLISION"):
        schema.preflight(con)
    assert objects(con) == before


@pytest.mark.parametrize("damage", ["missing_table", "missing_index", "wrong_predicate", "extra_index", "missing_trigger", "extra_trigger", "wrong_trigger_binding", "temp_shadow", "same_columns_lost_check"])
def test_full_contract_requires_exact_checks_indexes_and_all_trigger_bindings(con, damage):
    schema.install(con)
    if damage == "missing_table":
        con.execute("DROP TABLE " + schema.ADMISSION)
    elif damage == "missing_index":
        con.execute("DROP INDEX idx_source_admission_first")
    elif damage == "wrong_predicate":
        con.execute("DROP INDEX idx_source_admission_first")
        con.execute("CREATE UNIQUE INDEX idx_source_admission_first ON " + schema.ADMISSION + "(core_sha256,item_id) WHERE previous_admission_id IS NOT NULL")
    elif damage == "extra_index":
        con.execute("CREATE INDEX sabotage ON " + schema.ACTIVATION + "(target_epoch)")
    elif damage == "missing_trigger":
        con.execute("DROP TRIGGER " + schema.ADMISSION + "_no_delete")
    elif damage == "extra_trigger":
        con.execute("CREATE TRIGGER sabotage AFTER INSERT ON " + schema.ACTIVATION + " BEGIN SELECT 1; END")
    elif damage == "wrong_trigger_binding":
        con.execute("DROP TRIGGER " + schema.ADMISSION + "_no_delete")
        con.execute("CREATE TRIGGER " + schema.ADMISSION + "_no_delete BEFORE DELETE ON personal_blob BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END")
    elif damage == "temp_shadow":
        con.execute("CREATE TEMP TABLE " + schema.ACTIVATION + "(unknown TEXT)")
    else:
        # Use actual new DDL with identical columns and indexes/triggers, not
        # a fake schema_version change, to demonstrate lost CHECK detection.
        con.execute("DROP TABLE " + schema.ADMISSION)
        declaration = schema.TABLES[schema.ADMISSION].replace("CHECK(previous_admission_id IS NULL OR previous_admission_id!=admission_id)", "CHECK(1)")
        con.execute("CREATE TABLE " + schema.ADMISSION + "(" + declaration + ")")
        for sql in schema.INDEXES + schema.TRIGGERS:
            if " ON " + schema.ADMISSION + "(" in sql or " ON " + schema.ADMISSION + " BEGIN" in sql:
                con.execute(sql)
    before = objects(con)
    with pytest.raises(schema.SourceReadmitSchemaError):
        schema.validate(con)
    with pytest.raises(schema.SourceReadmitSchemaError):
        schema.install(con)
    assert objects(con) == before


def test_validate_is_read_only_with_row_factories_and_query_only(con):
    schema.install(con)
    con.commit()
    con.row_factory = sqlite3.Row
    before = objects(con)
    statements = []
    con.set_trace_callback(statements.append)
    con.execute("PRAGMA query_only=ON")
    schema.validate(con)
    schema.preflight(con, require_absent=False)
    assert objects(con) == before
    assert not any(re.match(r"\s*(INSERT|UPDATE|DELETE|CREATE|DROP|ALTER|BEGIN|COMMIT|ROLLBACK)\b", sql, re.I) for sql in statements)


@pytest.mark.parametrize("queue_sql", ["CREATE TABLE sync_queue(item_id TEXT PRIMARY KEY)",
    "CREATE TABLE sync_queue(item_id INTEGER,scope TEXT,PRIMARY KEY(item_id,scope))", "CREATE TABLE sync_queue(other_id INTEGER PRIMARY KEY)"])
def test_install_requires_actual_single_integer_queue_key_before_any_new_ddl(queue_sql):
    con = sqlite3.connect(":memory:")
    try:
        con.execute(queue_sql)
        before = objects(con)
        with pytest.raises(schema.SourceReadmitSchemaError):
            schema.install(con)
        assert objects(con) == before
    finally:
        con.close()
