"""Real SQLite reference caching without caching any observed database state."""
from contextlib import closing
import sqlite3

import pytest

from reno import personal_mirror_transfer_schema as schema


@pytest.fixture(autouse=True)
def clear_reference_cache():
    schema._reference_contract.cache_clear()
    yield
    schema._reference_contract.cache_clear()


@pytest.fixture
def actual_schema():
    with closing(sqlite3.connect(":memory:")) as con:
        schema._install_reference(con)
        yield con


def test_cold_and_warm_validation_both_read_actual_objects_and_table_contracts(actual_schema, monkeypatch):
    calls = []
    original_objects, original_contract = schema._objects, schema._contract

    def objects(con, master):
        if con is actual_schema:
            calls.append(("objects", master))
        return original_objects(con, master)

    def contract(con, table):
        if con is actual_schema:
            calls.append(("contract", table))
        return original_contract(con, table)

    monkeypatch.setattr(schema, "_objects", objects)
    monkeypatch.setattr(schema, "_contract", contract)
    schema.validate(actual_schema)
    schema.validate(actual_schema)

    per_call = [("objects", "sqlite_temp_master"), ("objects", "main.sqlite_master")]
    per_call += [("contract", table) for table in schema.TABLES]
    assert calls == per_call * 2
    assert schema._reference_contract.cache_info().misses == 1
    assert schema._reference_contract.cache_info().hits == 1


@pytest.mark.parametrize("factory", ["text_subclass", "numeric_subclass"])
def test_cold_and_warm_validation_preserve_equal_actual_scalar_subclasses(actual_schema, factory):
    class MetadataText(str):
        pass

    class MetadataInteger(int):
        pass

    if factory == "text_subclass":
        actual_schema.text_factory = lambda value: MetadataText(value.decode("utf-8"))
    else:
        actual_schema.row_factory = lambda _cursor, row: tuple(
            MetadataInteger(value) if type(value) is int else value for value in row)

    schema.validate(actual_schema)
    schema.validate(actual_schema)
    assert schema._reference_contract.cache_info().misses == 1
    assert schema._reference_contract.cache_info().hits == 1


@pytest.mark.parametrize("change", ["trigger", "column", "check"])
def test_warm_reference_still_rejects_actual_schema_drift(actual_schema, change):
    schema.validate(actual_schema)
    with closing(sqlite3.connect(":memory:")) as changed:
        for name, declaration in schema.TABLES.items():
            if change == "check" and name == schema.HEAD:
                assert "AND slot=1" in declaration
                declaration = declaration.replace("AND slot=1", "AND slot IN(1,2)", 1)
            changed.execute(f"CREATE TABLE {name} ({declaration})")
        for statement in schema.INDEXES + schema.TRIGGERS:
            changed.execute(statement)
        if change == "trigger":
            changed.execute("DROP TRIGGER trg_personal_mirror_receipt_update")
        elif change == "column":
            changed.execute(f"ALTER TABLE {schema.HEAD} ADD COLUMN unexpected TEXT")

        with pytest.raises(schema.PersonalMirrorSchemaError, match="INCOMPATIBLE"):
            schema.validate(changed)
    assert schema._reference_contract.cache_info().misses == 1
    assert schema._reference_contract.cache_info().hits == 1


def test_warm_temp_collision_is_rejected_before_reference_lookup(actual_schema):
    schema.validate(actual_schema)
    before = schema._reference_contract.cache_info()
    actual_schema.execute("CREATE TEMP TABLE personal_mirror_transfer_shadow(value TEXT)")
    with pytest.raises(schema.PersonalMirrorSchemaError, match="TEMP_COLLISION"):
        schema.validate(actual_schema)
    assert schema._reference_contract.cache_info() == before


@pytest.mark.parametrize("change", ["table_text", "table_order", "index_text", "index_order", "trigger_text", "trigger_order", "prefix", "sqlite_version"])
def test_complete_reference_inputs_and_their_order_partition_cache(actual_schema, monkeypatch, change):
    schema.validate(actual_schema)
    first_key = schema._reference_key()
    if change == "table_text":
        modified = dict(schema.TABLES)
        modified[schema.HEAD] += "\n"
        monkeypatch.setattr(schema, "TABLES", modified)
    elif change == "table_order":
        monkeypatch.setattr(schema, "TABLES", dict(reversed(tuple(schema.TABLES.items()))))
    elif change == "index_text":
        monkeypatch.setattr(schema, "INDEXES", (schema.INDEXES[0] + "\n", *schema.INDEXES[1:]))
    elif change == "index_order":
        monkeypatch.setattr(schema, "INDEXES", tuple(reversed(schema.INDEXES)))
    elif change == "trigger_text":
        monkeypatch.setattr(schema, "TRIGGERS", (schema.TRIGGERS[0] + "\n", *schema.TRIGGERS[1:]))
    elif change == "trigger_order":
        monkeypatch.setattr(schema, "TRIGGERS", tuple(reversed(schema.TRIGGERS)))
    elif change == "prefix":
        monkeypatch.setattr(schema, "_PREFIXES", (*schema._PREFIXES, "unrelated_reference_prefix_"))
    else:
        # Only partitioning the recorded version key; this remains one real engine.
        monkeypatch.setattr(sqlite3, "sqlite_version", sqlite3.sqlite_version + ".key-test")

    assert schema._reference_key() != first_key
    with closing(sqlite3.connect(":memory:")) as changed:
        schema._install_reference(changed)
        schema.validate(changed)
    assert schema._reference_contract.cache_info().misses == 2
    assert schema._reference_contract.cache_info().currsize == 2


def test_reference_builder_uses_only_captured_ddl_and_filter_key(actual_schema, monkeypatch):
    key = schema._reference_key()
    expected = (schema._freeze_reference(schema._objects(actual_schema, "main.sqlite_master")),
        tuple((name, schema._freeze_reference(schema._contract(actual_schema, name))) for name in schema.TABLES))
    monkeypatch.setattr(schema, "TABLES", {"unrelated": "INVALID DDL"})
    monkeypatch.setattr(schema, "INDEXES", ("INVALID INDEX SQL",))
    monkeypatch.setattr(schema, "TRIGGERS", ("INVALID TRIGGER SQL",))
    monkeypatch.setattr(schema, "_PREFIXES", ("unrelated_",))
    assert schema._reference_contract(key) == expected
    assert schema._reference_contract.cache_info().misses == 1


def test_shared_reference_contains_only_immutable_scalar_tuples(actual_schema):
    schema.validate(actual_schema)
    reference = schema._reference_contract(schema._reference_key())

    def immutable(value):
        if type(value) is tuple:
            return all(immutable(child) for child in value)
        return value is None or any(type(value) is kind for kind in (str, bytes, int, float, bool))

    assert immutable(reference)
    original = reference[0][0]
    detached = list(reference[0])
    detached[0] = ("polluted",)
    with pytest.raises(TypeError):
        reference[0][0] = ("polluted",)
    assert schema._reference_contract(schema._reference_key())[0][0] == original
    schema.validate(actual_schema)


def test_reference_freeze_rejects_custom_metaclass_without_equality_callback():
    equality_calls = []

    class EqualityTrap(type):
        def __eq__(cls, other):
            equality_calls.append(other)
            return True

    class MutablePretender(metaclass=EqualityTrap):
        pass

    with pytest.raises(schema.PersonalMirrorSchemaError, match="INCOMPATIBLE"):
        schema._freeze_reference((MutablePretender(),))
    assert equality_calls == []


@pytest.mark.parametrize("warm", [False, True])
def test_objects_mismatch_short_circuits_before_actual_table_contracts(actual_schema, monkeypatch, warm):
    if warm:
        schema.validate(actual_schema)
    actual_schema.execute("DROP TRIGGER trg_personal_mirror_receipt_update")
    original = schema._contract

    def contract(con, table):
        if con is actual_schema:
            pytest.fail("Objects mismatch must reject before actual table contract reads")
        return original(con, table)

    monkeypatch.setattr(schema, "_contract", contract)
    with pytest.raises(schema.PersonalMirrorSchemaError, match="INCOMPATIBLE"):
        schema.validate(actual_schema)


def test_failed_reference_build_is_not_cached():
    tables, indexes, triggers, prefixes, version = schema._reference_key()
    key = (tables, (*indexes, "INVALID INDEX SQL"), triggers, prefixes, version)
    for _ in range(2):
        with pytest.raises(sqlite3.Error):
            schema._reference_contract(key)
    assert schema._reference_contract.cache_info().misses == 2
    assert schema._reference_contract.cache_info().currsize == 0


def test_reference_connection_is_closed_on_success_and_build_failure(monkeypatch):
    connections = []
    original_connect = sqlite3.connect

    def connect(*args, **kwargs):
        con = original_connect(*args, **kwargs)
        connections.append(con)
        return con

    monkeypatch.setattr(sqlite3, "connect", connect)
    key = schema._reference_key()
    schema._reference_contract(key)
    tables, indexes, triggers, prefixes, version = key
    with pytest.raises(sqlite3.Error):
        schema._reference_contract((tables, (*indexes, "INVALID INDEX SQL"), triggers, prefixes, version))
    assert len(connections) == 2
    for con in connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            con.execute("SELECT 1")
