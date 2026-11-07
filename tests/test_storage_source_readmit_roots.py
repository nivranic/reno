"""Prospective schema17 public SQL roots, synthetic database only."""
from dataclasses import replace
import json
import sqlite3

import pytest

from reno import storage_root_inputs as inputs, sync_source_readmit_schema as schema
from tests.test_storage_root_inputs import env, parse, reset, asset  # noqa: F401
from tests.test_sync_source_readmit_schema import activation, registration, disposition, admission, insert


def install17(env, *, populated=False):
    con, _profile, _library = env
    schema.install(con)
    con.execute("UPDATE meta SET value='17' WHERE key='migration_version'")
    if populated:
        con.execute("INSERT INTO sync_queue(item_id,url,status) VALUES(7,'https://synthetic.example.test','queued')")
        active = activation()
        registered = registration(active=active)
        original = json.loads(registered["original_request_json"])
        disposed = disposition(registered=registered, applied=False, old=original)
        admitted = admission()
        for table, row in ((schema.ACTIVATION, active), (schema.REGISTRATION, registered),
                           (schema.DISPOSITION, disposed), (schema.ADMISSION, admitted)):
            insert(con, table, row)
    con.commit()


@pytest.mark.parametrize("populated", [False, True])
def test_prospective_four_table_projection_binds_public_hash_without_global_blocker(env, populated):
    install17(env, populated=populated)
    before = env[0].total_changes
    result = parse(env)
    found = [row for row in result.source_bindings if row.section == "source_continuity"]
    assert len(found) == 1 and found[0].state == "present"
    assert not result.blockers and env[0].total_changes == before
    assert all(private not in result.canonical_bytes() for private in (b"lease_token", b"contract_json", b"original_request_json"))


@pytest.mark.parametrize("table", list(schema.COLUMNS))
def test_every_private_column_is_denied_after_projection_and_public_fields_remain_readable(env, table):
    install17(env, populated=True)
    assert not parse(env).blockers
    for column in schema.COLUMNS[table]:
        if column.endswith("_json"):
            with pytest.raises(sqlite3.DatabaseError):
                env[0].execute('SELECT "' + column + '" FROM main."' + table + '"').fetchall()
    assert env[0].execute('SELECT "' + inputs.CONTINUITY_COLUMNS[table][0] + '" FROM main."' + table + '"').fetchall()


@pytest.mark.parametrize("name", schema.RESERVED_NAMES)
@pytest.mark.parametrize("database", ["main", "temp"])
def test_old16_never_adopts_future_reserved_name_even_case_alias_and_only_binds_unknown(env, name, database):
    con = env[0]
    con.execute('CREATE ' + ("TEMP " if database == "temp" else "") + 'TABLE "' + name.upper() + '"(value TEXT)')
    result = parse(env)
    state = [row.state for row in result.source_bindings if row.section == "source_continuity"]
    assert state == ["unknown"] and not result.blockers


@pytest.mark.parametrize("damage", ["missing_trigger", "extra_index", "foreign_key", "temp_shadow", "view_origin"])
def test_actual_ddl_fk_and_origin_damage_is_scoped_unknown_without_private_reads(env, damage):
    install17(env, populated=True)
    con = env[0]
    if damage == "missing_trigger":
        con.execute('DROP TRIGGER "' + schema.ACTIVATION + '_no_update"')
    elif damage == "extra_index":
        con.execute('CREATE INDEX synthetic_extra ON "' + schema.ADMISSION + '"(item_id)')
    elif damage == "foreign_key":
        con.execute("DELETE FROM sync_queue WHERE item_id=7")
    elif damage == "temp_shadow":
        con.execute('CREATE TEMP TABLE "' + schema.ACTIVATION + '"(value TEXT)')
    else:
        con.execute('CREATE TEMP VIEW synthetic_private AS SELECT contract_json FROM main."' + schema.ACTIVATION + '"')
        con.commit()
        assert not parse(env).blockers
        with pytest.raises(sqlite3.DatabaseError):
            con.execute("SELECT * FROM synthetic_private").fetchall()
        return
    con.commit()
    result = parse(env)
    state = [row.state for row in result.source_bindings if row.section == "source_continuity"]
    assert state == ["unknown"] and not result.blockers


def test_public_projection_and_hash_change_when_actual_fixed_admission_row_changes(env):
    install17(env, populated=False)
    before = parse(env).digest
    reset(env[0])
    env[0].execute("INSERT INTO sync_queue(item_id,url,status) VALUES(7,'https://synthetic.example.test','queued')")
    insert(env[0], schema.ADMISSION, admission())
    assert parse(env).digest != before


def test_helper_keeps_callers_authorizer_and_aggregate_read_budget(env):
    install17(env, populated=True)
    reader = inputs._Reader(env[0], inputs.InputLimits())
    env[0].set_authorizer(reader.authorize)
    projection = inputs.read_continuity_projection(env[0], consume_row=reader.consume)
    assert set(projection) == set(schema.TABLES)
    with pytest.raises(sqlite3.DatabaseError):
        env[0].execute('SELECT contract_json FROM main."' + schema.ACTIVATION + '"').fetchall()
    with pytest.raises(inputs._Invalid, match="INPUT_ROW_LIMIT"):
        inputs.read_continuity_projection(env[0], limits=replace(inputs.InputLimits(), max_rows=1))


def test_helper_cancelled_exception_is_not_translated_to_unknown_or_mutation(env):
    install17(env, populated=True)
    env[0].set_authorizer(inputs._Reader(env[0], inputs.InputLimits()).authorize)
    original = RuntimeError("synthetic caller cancellation")
    def cancelled():
        raise original
    with pytest.raises(RuntimeError) as found:
        inputs.read_continuity_projection(env[0], check_cancelled=cancelled)
    assert found.value is original
