"""Exact dual DDL recognition; original v1 checks stay unchanged."""
from copy import deepcopy
import sqlite3

import pytest

from reno import sync_source_readmit_schema as old, sync_source_readmit_schema2 as new
from reno import sync_source_readmit_sql2 as sql2
from reno.sync_protocol import SyncError
from tests import test_sync_source_readmit_contract_v2 as sample


def connection(module):
    con = sqlite3.connect(":memory:")
    con.execute("CREATE TABLE sync_queue(item_id INTEGER PRIMARY KEY)")
    module.install(con)
    return con


def test_exact_variants_are_read_only_and_only_admission_check_changes():
    with connection(old) as con:
        before = tuple(con.iterdump())
        assert new.detect(con) == "v1"
        assert tuple(con.iterdump()) == before
        with pytest.raises(old.SourceReadmitSchemaError):
            new.install(con)
    with connection(new) as con:
        assert new.detect(con) == "admission2"
        with pytest.raises(old.SourceReadmitSchemaError):
            old.validate(con)
    assert {key: value for key, value in new.TABLES.items() if key != new.ADMISSION} == {
        key: value for key, value in old.TABLES.items() if key != old.ADMISSION}
    assert new.COLUMNS == old.COLUMNS and new.INDEXES == old.INDEXES and new.TRIGGERS == old.TRIGGERS


@pytest.mark.parametrize("change", ["extra_trigger", "dropped_index", "temp_shadow", "wide_format"])
def test_neither_variant_accepts_unknown_constraints_objects_or_shadow(change):
    with connection(new) as con:
        if change == "extra_trigger":
            con.execute("CREATE TRIGGER extra BEFORE INSERT ON sync_source_admission BEGIN SELECT 1; END")
        elif change == "dropped_index":
            con.execute("DROP INDEX idx_source_admission_previous")
        elif change == "temp_shadow":
            con.execute("CREATE TEMP TABLE sync_source_admission(x)")
        else:
            con.execute("DROP TABLE sync_source_admission")
            declaration = new.TABLES[new.ADMISSION].replace(new._NEW_FORMAT,
                old._integer_sql("format_version", maximum=3))
            con.execute(f"CREATE TABLE {new.ADMISSION} ({declaration})")
            for statement in new.INDEXES + new.TRIGGERS:
                if new.ADMISSION in statement:
                    con.execute(statement)
        with pytest.raises(old.SourceReadmitSchemaError):
            new.detect(con)


def test_v2_row_semantics_bind_result_to_exact_request_and_old_validator_rejects():
    body = sample.request()
    answer = sample.result(body)
    from reno import sync_source_readmission
    record = sync_source_readmission._record(body, answer)
    assert new.validate_admission_record(record) == record
    with pytest.raises(old.SourceReadmitRecordError):
        old.validate_admission_record(record)
    wrong = deepcopy(record)
    wrong["receiver_library_epoch"] += 1
    with pytest.raises(old.SourceReadmitRecordError):
        new.validate_admission_record(wrong)


@pytest.mark.parametrize("submitted", ["2029-12-31T00:00:00.000Z", "2030-01-03T00:00:00.000Z"])
def test_echoed_admission_expiry_is_live_and_no_later_than_real_sql_session(submitted):
    from datetime import datetime, timezone
    body = sample.request()
    body["lease"]["lease_until"] = submitted
    session = {**body["lease"], "lease_until": "2030-01-02T00:00:00.000Z"}
    with pytest.raises(SyncError):
        sql2._lease_gate(body, session, datetime(2030, 1, 1, tzinfo=timezone.utc))
