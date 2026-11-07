"""Warm expected schema templates never replace live SQL verification."""
from contextlib import contextmanager
import sqlite3

import pytest

from reno import home_recovery17 as recovery, home_recovery as old
from reno import migrations

VARIANTS = ("v1", "admission2", "admission2_dispose2")
# Observed from the original implementation on each isolated schema variant.
BEFORE_EFFECTS_SHA256 = {'v1': '5ef52151e5532652649b6bb3518731ef4f7f6d487af11f4a02311881510d1689', 'admission2': '5ef52151e5532652649b6bb3518731ef4f7f6d487af11f4a02311881510d1689', 'admission2_dispose2': '5ef52151e5532652649b6bb3518731ef4f7f6d487af11f4a02311881510d1689'}


@contextmanager
def actual_memory_schema(variant):
    con = sqlite3.connect(":memory:")
    con.row_factory = sqlite3.Row
    try:
        migrations._create_schema(con, readmit_variant=variant)
        con.execute("INSERT INTO meta(key,value) VALUES ('migration_version','17')")
        con.commit()
        yield con
    finally:
        con.close()


@pytest.mark.parametrize("variant", VARIANTS)
def test_exact_before_sha_is_unchanged_cold_and_warm(variant):
    expected = BEFORE_EFFECTS_SHA256[variant]
    assert type(expected) is str and len(expected) == 64, "Missing observed original schema-effects SHA fixture"
    recovery._expected_schema_effects_cached.cache_clear()
    with actual_memory_schema(variant) as con:
        assert recovery._actual_schema(con) == expected
        assert recovery._actual_schema(con) == expected


@pytest.mark.parametrize("variant", VARIANTS)
def test_warm_reference_does_not_hide_dropped_actual_knowledge_trigger(variant):
    recovery._expected_schema_effects_cached.cache_clear()
    with actual_memory_schema(variant) as con:
        recovery._actual_schema(con)
        con.execute("DROP TRIGGER kb_atom_insert")
        with pytest.raises(old.HomeRecoveryError) as caught:
            recovery._actual_schema(con)
        assert caught.value.code == "HOME_RECOVERY_TRIGGER_CONTRACT"


@pytest.mark.parametrize("variant", VARIANTS)
def test_warm_reference_does_not_hide_actual_frame_foreign_key(variant):
    recovery._expected_schema_effects_cached.cache_clear()
    with actual_memory_schema(variant) as con:
        recovery._actual_schema(con)
        original_info = [tuple(row) for row in con.execute("PRAGMA main.table_info(frame)")]
        original_fk = [tuple(row) for row in con.execute("PRAGMA main.foreign_key_list(frame)")]
        indexes = [row[0] for row in con.execute("SELECT sql FROM main.sqlite_master WHERE type='index' AND tbl_name='frame' AND sql IS NOT NULL")]
        sql = con.execute("SELECT sql FROM main.sqlite_master WHERE type='table' AND name='frame'").fetchone()[0]
        assert sql.rstrip().endswith(")") and not original_fk
        con.execute("DROP TABLE frame")
        con.execute(sql.rstrip()[:-1] + ", FOREIGN KEY(video_id) REFERENCES video_asset(video_id))")
        for statement in indexes:
            con.execute(statement)
        assert [tuple(row) for row in con.execute("PRAGMA main.table_info(frame)")] == original_info
        assert [tuple(row) for row in con.execute("PRAGMA main.foreign_key_list(frame)")] != original_fk
        with pytest.raises(old.HomeRecoveryError) as caught:
            recovery._actual_schema(con)
        assert caught.value.code == "HOME_RECOVERY_FOREIGN_KEY_CONTRACT"


def test_unknown_variant_cannot_borrow_warm_template():
    recovery._expected_schema_effects_cached.cache_clear()
    for variant in VARIANTS:
        recovery._expected_schema_effects(variant)
        recovery._expected_schema_effects(variant=variant)
    class StringSubclass(str):
        pass
    for unknown in (None, False, 0, "unknown", "v1 ", StringSubclass("v1"), ["v1"]):
        with pytest.raises(old.HomeRecoveryError) as caught:
            recovery._expected_schema_effects(variant=unknown)
        assert caught.value.code == "HOME_RECOVERY_SCHEMA_UNSUPPORTED"
    assert recovery._expected_schema_effects_cached.cache_info().currsize <= 3


def test_template_is_recursive_immutable_values_without_connection(monkeypatch):
    recovery._expected_schema_effects_cached.cache_clear()
    original_connect, opened = sqlite3.connect, []
    def observed_real_connect(*args, **kwargs):
        con = original_connect(*args, **kwargs)
        opened.append(con)
        return con
    # Records real SQLite handles; no fake SQL result or validator substitute.
    monkeypatch.setattr(sqlite3, "connect", observed_real_connect)
    template = recovery._expected_schema_effects("v1")
    def check(value):
        assert not isinstance(value, (sqlite3.Connection, sqlite3.Cursor, list, dict, set))
        if type(value) is tuple:
            for item in value:
                check(item)
        else:
            assert type(value) in {str, int, type(None)}
    check(template)
    with pytest.raises(TypeError):
        template[0][0] = ("replacement", ())
    assert opened
    for con in opened:
        with pytest.raises(sqlite3.ProgrammingError):
            con.execute("SELECT 1")
    assert recovery._expected_schema_effects("v1") is template
