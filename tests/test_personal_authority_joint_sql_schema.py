"""Independent complete A/B catalog content tests; no SQLite or runtime receipts.

Portable fixtures retain the entire actual reference files. Catalog projections
exclude only acquisition fields that are outside the catalog wire contract.
No subject private reference literal is used to construct an expected catalog.
"""

import copy
import hashlib
import inspect
import json
from functools import lru_cache
from pathlib import Path

import pytest

from reno import personal_authority_joint_sql_schema as schema


PROFILE = "fresh17.admission2_dispose2.receiver_absent"
FIXTURE_PINS = {
    "a-pre": (713346, "0a608a60509090a0642f572c2208dde8d878aa8911343c42f281a55aacadfa72"),
    "a-post": (765559, "2c03ab6ae1bc37d653ad0de1b73dbe008a4525d7db9da6dcd1db3ab6dac5daf9"),
    "b-pre": (713346, "4e4664237c5d64a8da89efdd468f67b26adb4293ad036b2c989b14a47885f4f3"),
    "b-post": (765559, "fbf3940ca8366bdbd549e8301e368b088bea9018ef48c487f2d5a831aec9e17a"),
}
AUTHORITY_NAMES = {
    "idx_personal_authority_mutation_grant",
    "sqlite_autoindex_personal_authority_mutation_commit_1",
    "personal_authority_binding", "personal_authority_mutation_commit",
    "trg_personal_authority_binding_delete", "trg_personal_authority_binding_insert",
    "trg_personal_authority_binding_update", "trg_personal_authority_mutation_delete",
    "trg_personal_authority_mutation_insert", "trg_personal_authority_mutation_update",
}
AUTHORITY_TABLES = {"personal_authority_binding", "personal_authority_mutation_commit"}
CATALOG_FIELDS = {
    "family", "format_version", "namespace_version", "state", "base_objects",
    "base_table_contracts", "authority_objects", "authority_table_contracts",
    "temp_collisions", "sqlite_schema_cookie", "sqlite_user_version",
}
INTEGER_CASES = [('catalog', 'format_version', 'true'), ('catalog', 'format_version', 'false'), ('catalog', 'format_version', 'float'), ('catalog', 'format_version', 'text'), ('catalog', 'format_version', 'null'), ('catalog', 'namespace_version', 'true'), ('catalog', 'namespace_version', 'false'), ('catalog', 'namespace_version', 'float'), ('catalog', 'namespace_version', 'text'), ('catalog', 'namespace_version', 'null'), ('catalog', 'sqlite_schema_cookie', 'true'), ('catalog', 'sqlite_schema_cookie', 'false'), ('catalog', 'sqlite_schema_cookie', 'float'), ('catalog', 'sqlite_schema_cookie', 'text'), ('catalog', 'sqlite_schema_cookie', 'null'), ('catalog', 'sqlite_user_version', 'true'), ('catalog', 'sqlite_user_version', 'false'), ('catalog', 'sqlite_user_version', 'float'), ('catalog', 'sqlite_user_version', 'text'), ('catalog', 'sqlite_user_version', 'null'), ('column', 'cid', 'true'), ('column', 'cid', 'false'), ('column', 'cid', 'float'), ('column', 'cid', 'text'), ('column', 'cid', 'null'), ('column', 'not_null', 'true'), ('column', 'not_null', 'false'), ('column', 'not_null', 'float'), ('column', 'not_null', 'text'), ('column', 'not_null', 'null'), ('column', 'primary_key_position', 'true'), ('column', 'primary_key_position', 'false'), ('column', 'primary_key_position', 'float'), ('column', 'primary_key_position', 'text'), ('column', 'primary_key_position', 'null'), ('column', 'hidden', 'true'), ('column', 'hidden', 'false'), ('column', 'hidden', 'float'), ('column', 'hidden', 'text'), ('column', 'hidden', 'null'), ('foreign', 'id', 'true'), ('foreign', 'id', 'false'), ('foreign', 'id', 'float'), ('foreign', 'id', 'text'), ('foreign', 'id', 'null'), ('foreign', 'sequence', 'true'), ('foreign', 'sequence', 'false'), ('foreign', 'sequence', 'float'), ('foreign', 'sequence', 'text'), ('foreign', 'sequence', 'null'), ('index', 'sequence', 'true'), ('index', 'sequence', 'false'), ('index', 'sequence', 'float'), ('index', 'sequence', 'text'), ('index', 'sequence', 'null'), ('index', 'unique', 'true'), ('index', 'unique', 'false'), ('index', 'unique', 'float'), ('index', 'unique', 'text'), ('index', 'unique', 'null'), ('index', 'partial', 'true'), ('index', 'partial', 'false'), ('index', 'partial', 'float'), ('index', 'partial', 'text'), ('index', 'partial', 'null'), ('part', 'sequence', 'true'), ('part', 'sequence', 'false'), ('part', 'sequence', 'float'), ('part', 'sequence', 'text'), ('part', 'sequence', 'null'), ('part', 'cid', 'true'), ('part', 'cid', 'false'), ('part', 'cid', 'float'), ('part', 'cid', 'text'), ('part', 'cid', 'null'), ('part', 'descending', 'true'), ('part', 'descending', 'false'), ('part', 'descending', 'float'), ('part', 'descending', 'text'), ('part', 'descending', 'null'), ('part', 'key', 'true'), ('part', 'key', 'false'), ('part', 'key', 'float'), ('part', 'key', 'text'), ('part', 'key', 'null')]


def _canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


@lru_cache(maxsize=4)
def _fixture_raw(key):
    path = Path(__file__).parent / "fixtures/personal_authority_joint_sql_schema" / (
        "reference-" + key + ".json")
    raw = path.read_bytes()
    assert (len(raw), hashlib.sha256(raw).hexdigest()) == FIXTURE_PINS[key]
    return raw


def _reference(key):
    return json.loads(_fixture_raw(key))


def _catalog(state="joint_unbound", source="a"):
    pre, post = _reference(source + "-pre"), _reference(source + "-post")
    authority_objects = [row for row in post["main_objects"]
                         if row["name"] in AUTHORITY_NAMES]
    authority_tables = [row for row in post["table_contracts"]
                        if row["table_name"] in AUTHORITY_TABLES]
    assert len(authority_objects) == 10 and len(authority_tables) == 2
    assert [row for row in post["main_objects"] if row["name"] not in AUTHORITY_NAMES] == pre["main_objects"]
    assert [row for row in post["table_contracts"] if row["table_name"] not in AUTHORITY_TABLES] == pre["table_contracts"]
    observed = pre if state == "base_only" else post
    return {
        "family": "personal_authority_joint_sql_catalog", "format_version": 1,
        "namespace_version": 3, "state": state,
        "base_objects": pre["main_objects"], "base_table_contracts": pre["table_contracts"],
        "authority_objects": [] if state == "base_only" else authority_objects,
        "authority_table_contracts": [] if state == "base_only" else authority_tables,
        "temp_collisions": [], "sqlite_schema_cookie": observed["sqlite_schema_cookie"],
        "sqlite_user_version": observed["sqlite_user_version"],
    }


def _table(catalog, name="personal_authority_mutation_commit"):
    return next(row for role in ("base", "authority")
                for row in catalog[role + "_table_contracts"] if row["table_name"] == name)


def _target(catalog, location):
    journal = _table(catalog)
    return {
        "catalog": catalog, "object": catalog["base_objects"][0],
        "authority-object": catalog["authority_objects"][0], "table": journal,
        "column": journal["columns"][0], "foreign": journal["foreign_keys"][0],
        "index": journal["indexes"][0], "part": journal["indexes"][0]["parts"][0],
        "auxiliary": journal["indexes"][0]["parts"][-1],
    }[location]


def _error(function, value, suffix):
    with pytest.raises(schema.JointSqlSchemaError) as caught:
        function(value)
    assert type(caught.value) is schema.JointSqlSchemaError
    assert isinstance(caught.value, ValueError)
    assert caught.value.code == "JOINT_SQL_SCHEMA_" + suffix
    assert str(caught.value) == caught.value.code


def _domain_only(catalog):
    before = _canonical(catalog)
    assert schema._validate_catalog_structure(catalog) is None
    _error(schema._match_catalog_candidate, catalog, "CATALOG_INVALID")
    assert _canonical(catalog) == before


def _sort_objects(catalog, role):
    catalog[role + "_objects"].sort(key=lambda row: (row["type"], row["name"]))


def _remove_table(catalog, name):
    catalog["base_objects"] = [row for row in catalog["base_objects"] if row["name"] != name]
    catalog["base_table_contracts"] = [row for row in catalog["base_table_contracts"]
                                       if row["table_name"] != name]


@pytest.mark.parametrize("phase", ["pre", "post"], ids=["pre", "post"])
def test_portable_actual_AB_full_SQL_and_raw_named_metadata(phase):
    a, b = _reference("a-" + phase), _reference("b-" + phase)
    for field in ("main_objects", "temp_objects", "table_contracts", "raw_main_object_rows",
                  "raw_temp_object_rows", "raw_table_metadata", "actual_meta_rows",
                  "sqlite_user_version", "raw_user_version"):
        assert _canonical(a[field]) == _canonical(b[field])
    assert a["sqlite_schema_cookie"] != b["sqlite_schema_cookie"]
    assert a["reference_only_creation_order"] != b["reference_only_creation_order"]
    assert a["database_list"][0]["file"] != b["database_list"][0]["file"]


@pytest.mark.parametrize("state,source", [
    ("base_only", "a"), ("joint_unbound", "a"), ("joint_bound", "a"),
    ("base_only", "b"), ("joint_unbound", "b"), ("joint_bound", "b"),
], ids=["a-base", "a-unbound", "a-bound-declaration", "b-base", "b-unbound", "b-bound-declaration"])
def test_complete_actual_catalog_structure_and_candidate(state, source):
    catalog = _catalog(state, source)
    assert set(catalog) == CATALOG_FIELDS
    before = _canonical(catalog)
    assert schema._validate_catalog_structure(catalog) is None
    assert schema._match_catalog_candidate(catalog) == (PROFILE, state)
    assert _canonical(catalog) == before
    # joint_bound here is a schema state declaration, with no binding-row proof.
    assert "binding_rows" not in catalog and "logical_head" not in catalog


def test_complete_census_retains_FTS_sequence_autoindexes_and_auxiliary_parts():
    catalog = _catalog()
    assert len(catalog["base_objects"]) == 187
    assert len(catalog["base_table_contracts"]) == 58
    assert len(catalog["authority_objects"]) == 10
    assert len(catalog["authority_table_contracts"]) == 2
    names = {row["name"] for row in catalog["base_objects"]}
    assert {"atom_fts", "atom_fts_data", "atom_fts_idx", "atom_fts_content",
            "atom_fts_docsize", "atom_fts_config", "sqlite_sequence"} <= names
    null_rows = [row for row in catalog["base_objects"] if row["sql"] is None]
    assert len(null_rows) == 53 and all(row["type"] == "index" for row in null_rows)
    parts = [part for role in ("base", "authority")
             for table in catalog[role + "_table_contracts"] for index in table["indexes"]
             for part in index["parts"]]
    assert any(part["cid"] == -1 and part["name"] is None and part["key"] == 0 for part in parts)
    assert schema._match_catalog_candidate(catalog) == (PROFILE, "joint_unbound")


@pytest.mark.parametrize("change", ["nullable-default", "nullable-parent-column", "nullable-collation",
                                    "expression-minus2", "empty-declared-type"],
                         ids=["nullable-default", "nullable-parent-column", "nullable-collation",
                              "expression-minus2", "empty-declared-type"])
def test_nullable_and_signed_metadata_structure_is_separate_from_reference(change):
    catalog = _catalog()
    if change == "nullable-default":
        column = next(col for table in catalog["base_table_contracts"] for col in table["columns"]
                      if col["default_sql"] is not None)
        column["default_sql"] = None
    elif change == "nullable-parent-column":
        _target(catalog, "foreign")["to_column"] = None
    elif change == "nullable-collation":
        _target(catalog, "part")["collation"] = None
    elif change == "expression-minus2":
        _target(catalog, "auxiliary")["cid"] = -2
        assert _target(catalog, "auxiliary")["name"] is None
    else:
        _target(catalog, "column")["declared_type"] = ""
    _domain_only(catalog)


@pytest.mark.parametrize("location,field,bad", INTEGER_CASES,
                         ids=['catalog-format_version-true', 'catalog-format_version-false', 'catalog-format_version-float', 'catalog-format_version-text', 'catalog-format_version-null', 'catalog-namespace_version-true', 'catalog-namespace_version-false', 'catalog-namespace_version-float', 'catalog-namespace_version-text', 'catalog-namespace_version-null', 'catalog-sqlite_schema_cookie-true', 'catalog-sqlite_schema_cookie-false', 'catalog-sqlite_schema_cookie-float', 'catalog-sqlite_schema_cookie-text', 'catalog-sqlite_schema_cookie-null', 'catalog-sqlite_user_version-true', 'catalog-sqlite_user_version-false', 'catalog-sqlite_user_version-float', 'catalog-sqlite_user_version-text', 'catalog-sqlite_user_version-null', 'column-cid-true', 'column-cid-false', 'column-cid-float', 'column-cid-text', 'column-cid-null', 'column-not_null-true', 'column-not_null-false', 'column-not_null-float', 'column-not_null-text', 'column-not_null-null', 'column-primary_key_position-true', 'column-primary_key_position-false', 'column-primary_key_position-float', 'column-primary_key_position-text', 'column-primary_key_position-null', 'column-hidden-true', 'column-hidden-false', 'column-hidden-float', 'column-hidden-text', 'column-hidden-null', 'foreign-id-true', 'foreign-id-false', 'foreign-id-float', 'foreign-id-text', 'foreign-id-null', 'foreign-sequence-true', 'foreign-sequence-false', 'foreign-sequence-float', 'foreign-sequence-text', 'foreign-sequence-null', 'index-sequence-true', 'index-sequence-false', 'index-sequence-float', 'index-sequence-text', 'index-sequence-null', 'index-unique-true', 'index-unique-false', 'index-unique-float', 'index-unique-text', 'index-unique-null', 'index-partial-true', 'index-partial-false', 'index-partial-float', 'index-partial-text', 'index-partial-null', 'part-sequence-true', 'part-sequence-false', 'part-sequence-float', 'part-sequence-text', 'part-sequence-null', 'part-cid-true', 'part-cid-false', 'part-cid-float', 'part-cid-text', 'part-cid-null', 'part-descending-true', 'part-descending-false', 'part-descending-float', 'part-descending-text', 'part-descending-null', 'part-key-true', 'part-key-false', 'part-key-float', 'part-key-text', 'part-key-null'])
def test_every_metadata_integer_rejects_bool_float_text_and_null(location, field, bad):
    catalog = _catalog()
    _target(catalog, location)[field] = {
        "true": True, "false": False, "float": 0.0, "text": "0", "null": None,
    }[bad]
    _error(schema._validate_catalog_structure, catalog, "INTEGER_INVALID")


@pytest.mark.parametrize("location,field,value", [
    ("catalog", "sqlite_schema_cookie", -1), ("catalog", "sqlite_user_version", 2147483648),
    ("column", "cid", -1), ("column", "not_null", 2), ("column", "hidden", 4),
    ("column", "primary_key_position", -1), ("foreign", "id", -1), ("foreign", "sequence", -1),
    ("index", "sequence", -1), ("index", "unique", 2), ("index", "partial", 2),
    ("part", "sequence", -1), ("part", "cid", -3), ("part", "cid", 2147483648),
    ("part", "descending", 2), ("part", "key", 2),
    ("catalog", "format_version", 2), ("catalog", "namespace_version", 2),
    ("catalog", "sqlite_user_version", 9223372036854775808),
], ids=["cookie-negative", "user-over32", "column-negative", "not-null-2", "hidden-4", "pk-negative",
        "fk-id-negative", "fk-sequence-negative", "index-sequence-negative", "unique-2", "partial-2",
        "part-sequence-negative", "cid-minus3", "cid-over32", "descending-2", "key-2",
        "format-2", "namespace-2", "user-over64"])
def test_integer_field_domains_remain_exact(location, field, value):
    catalog = _catalog()
    _target(catalog, location)[field] = value
    _error(schema._validate_catalog_structure, catalog, "INTEGER_INVALID")


@pytest.mark.parametrize("cookie,user", [(0, 0), (2147483647, 0), (0, 2147483647), (17, 17)],
                         ids=["zero", "cookie-max", "user-max", "separate-logical-17"])
def test_legal_cookie_and_user_values_do_not_infer_publication(cookie, user):
    catalog = _catalog()
    catalog["sqlite_schema_cookie"], catalog["sqlite_user_version"] = cookie, user
    assert schema._match_catalog_candidate(catalog) == (PROFILE, "joint_unbound")
    _error(schema.validate_catalog_declaration, catalog, "REGISTRY_UNAVAILABLE")
    assert (catalog["sqlite_schema_cookie"], catalog["sqlite_user_version"]) == (cookie, user)


@pytest.mark.parametrize("location,mode", [('catalog', 'extra'), ('catalog', 'missing'), ('object', 'extra'), ('object', 'missing'), ('authority-object', 'extra'), ('authority-object', 'missing'), ('table', 'extra'), ('table', 'missing'), ('column', 'extra'), ('column', 'missing'), ('foreign', 'extra'), ('foreign', 'missing'), ('index', 'extra'), ('index', 'missing'), ('part', 'extra'), ('part', 'missing')], ids=['catalog-extra', 'catalog-missing', 'object-extra', 'object-missing', 'authority-object-extra', 'authority-object-missing', 'table-extra', 'table-missing', 'column-extra', 'column-missing', 'foreign-extra', 'foreign-missing', 'index-extra', 'index-missing', 'part-extra', 'part-missing'])
def test_closed_fields_are_checked_before_publication(location, mode):
    catalog = _catalog()
    target = _target(catalog, location)
    if mode == "extra":
        target["unapproved"] = None
    else:
        del target[next(iter(target))]
    for function in (schema._validate_catalog_structure, schema._match_catalog_candidate,
                     schema.validate_catalog_declaration):
        _error(function, catalog, "FIELDS_INVALID")


@pytest.mark.parametrize("location,field", [
    ("catalog", "family"), ("catalog", "state"), ("object", "name"), ("object", "table_name"),
    ("object", "sql"), ("column", "name"), ("column", "declared_type"),
    ("foreign", "from_column"), ("foreign", "target_table"), ("part", "collation"),
], ids=["family", "state", "object-name", "object-table", "object-sql", "column-name",
        "column-type", "fk-from", "fk-target", "part-collation"])
def test_text_NUL_is_never_normalized(location, field):
    catalog = _catalog()
    _target(catalog, location)[field] = "bad\x00text"
    _error(schema._validate_catalog_structure, catalog, "TEXT_INVALID")


@pytest.mark.parametrize("change", ["object-order", "table-order", "column-order", "fk-order",
                                    "index-order", "part-order", "column-gap", "index-gap", "part-gap",
                                    "fk-duplicate", "negative-cid-name", "column-part-name", "cid-outside-table",
                                    "empty-columns", "empty-parts", "missing-table-contract", "case-column",
                                    "case-object", "cross-group-name", "unknown-state", "wrong-family",
                                    "invalid-action", "invalid-origin", "temp", "base-with-authority", "joint-without-authority"],
                         ids=["object-order", "table-order", "column-order", "fk-order", "index-order", "part-order",
                              "column-gap", "index-gap", "part-gap", "fk-duplicate", "negative-cid-name",
                              "column-part-name", "cid-outside-table", "empty-columns", "empty-parts", "missing-table-contract",
                              "case-column", "case-object", "cross-group-name", "unknown-state", "wrong-family",
                              "invalid-action", "invalid-origin", "temp", "base-with-authority", "joint-without-authority"])
def test_structure_rejects_order_name_domain_and_state_inconsistency(change):
    catalog = _catalog()
    table = _table(catalog)
    if change == "object-order": catalog["base_objects"].reverse()
    elif change == "table-order": catalog["base_table_contracts"].reverse()
    elif change == "column-order": table["columns"].reverse()
    elif change == "fk-order": table["foreign_keys"].reverse()
    elif change == "index-order": table["indexes"].reverse()
    elif change == "part-order": table["indexes"][0]["parts"].reverse()
    elif change == "column-gap": table["columns"][0]["cid"] = 1
    elif change == "index-gap": table["indexes"][0]["sequence"] = 1
    elif change == "part-gap": table["indexes"][0]["parts"][0]["sequence"] = 1
    elif change == "fk-duplicate": table["foreign_keys"].append(copy.deepcopy(table["foreign_keys"][-1]))
    elif change == "negative-cid-name": _target(catalog, "auxiliary")["name"] = "rowid"
    elif change == "column-part-name": _target(catalog, "part")["name"] = "wrong-column"
    elif change == "cid-outside-table": _target(catalog, "part")["cid"] = len(table["columns"])
    elif change == "empty-columns": table["columns"] = []
    elif change == "empty-parts": table["indexes"][0]["parts"] = []
    elif change == "missing-table-contract": catalog["base_table_contracts"].pop()
    elif change == "case-column": table["columns"][1]["name"] = table["columns"][0]["name"].upper()
    elif change == "case-object":
        obj = copy.deepcopy(catalog["base_objects"][0]); obj["name"] = obj["name"].upper()
        catalog["base_objects"].append(obj); _sort_objects(catalog, "base")
    elif change == "cross-group-name":
        catalog["authority_objects"][0]["name"] = catalog["base_objects"][0]["name"]
        _sort_objects(catalog, "authority")
    elif change == "unknown-state": catalog["state"] = "ready"
    elif change == "wrong-family": catalog["family"] = "personal_authority_namespace3"
    elif change == "invalid-action": _target(catalog, "foreign")["on_update"] = "cascade"
    elif change == "invalid-origin": _target(catalog, "index")["origin"] = "PK"
    elif change == "temp": catalog["temp_collisions"] = ["meta"]
    elif change == "base-with-authority": catalog["state"] = "base_only"
    else:
        catalog["authority_objects"] = []; catalog["authority_table_contracts"] = []
    _error(schema._validate_catalog_structure, catalog, "CATALOG_INVALID")


@pytest.mark.parametrize("role,name", [
    ("base", "meta"), ("base", "idx_atom_video"), ("base", "kb_atom_insert"),
    ("base", "atom_fts"), ("base", "atom_fts_data"), ("base", "sqlite_sequence"),
    ("authority", "personal_authority_binding"), ("authority", "trg_personal_authority_binding_delete"),
    ("authority", "idx_personal_authority_mutation_grant"),
], ids=["table", "index", "trigger", "virtual", "shadow", "sequence", "authority-table", "authority-trigger", "authority-index"])
def test_every_SQL_kind_requires_full_text_without_whitespace_normalization(role, name):
    catalog = _catalog()
    row = next(x for x in catalog[role + "_objects"] if x["name"] == name)
    row["sql"] += "\n"
    _domain_only(catalog)


@pytest.mark.parametrize("change", ["column-type", "column-default", "column-not-null", "column-pk", "column-hidden",
                                    "fk-from", "fk-to", "fk-update", "fk-delete", "fk-match",
                                    "index-unique", "index-partial", "index-origin", "part-descending", "part-key",
                                    "part-collation", "fk-renumbered", "index-renumbered", "parts-renumbered"],
                         ids=["column-type", "column-default", "column-not-null", "column-pk", "column-hidden",
                              "fk-from", "fk-to", "fk-update", "fk-delete", "fk-match", "index-unique", "index-partial",
                              "index-origin", "part-descending", "part-key", "part-collation", "fk-renumbered",
                              "index-renumbered", "parts-renumbered"])
def test_valid_metadata_domains_do_not_substitute_for_exact_reference(change):
    catalog = _catalog(); table = _table(catalog)
    if change == "column-type": _target(catalog, "column")["declared_type"] = "text"
    elif change == "column-default": _target(catalog, "column")["default_sql"] = "NULL"
    elif change == "column-not-null": _target(catalog, "column")["not_null"] = 0
    elif change == "column-pk": _target(catalog, "column")["primary_key_position"] = 0
    elif change == "column-hidden": _target(catalog, "column")["hidden"] = 2
    elif change == "fk-from": _target(catalog, "foreign")["from_column"] = "namespace_version"
    elif change == "fk-to": _target(catalog, "foreign")["to_column"] = None
    elif change == "fk-update": _target(catalog, "foreign")["on_update"] = "CASCADE"
    elif change == "fk-delete": _target(catalog, "foreign")["on_delete"] = "RESTRICT"
    elif change == "fk-match": _target(catalog, "foreign")["match"] = "FULL"
    elif change == "index-unique": _target(catalog, "index")["unique"] = 1
    elif change == "index-partial": _target(catalog, "index")["partial"] = 1
    elif change == "index-origin": _target(catalog, "index")["origin"] = "u"
    elif change == "part-descending": _target(catalog, "part")["descending"] = 1
    elif change == "part-key": _target(catalog, "part")["key"] = 0
    elif change == "part-collation": _target(catalog, "part")["collation"] = "binary"
    else:
        rows = table["foreign_keys"] if change == "fk-renumbered" else table["indexes"] if change == "index-renumbered" else table["indexes"][0]["parts"]
        rows.reverse()
        for sequence, row in enumerate(rows):
            row["id" if change == "fk-renumbered" else "sequence"] = sequence
    _domain_only(catalog)


@pytest.mark.parametrize("change", ["unknown-main-view", "authority-prefix-view", "trigger-prefix-view",
                                    "missing-virtual", "missing-shadow", "missing-sequence", "missing-autoindex",
                                    "missing-authority-trigger", "missing-authority-index", "missing-authority-autoindex",
                                    "missing-authority-column", "missing-auxiliary-part", "autoindex-nonnull-sql"],
                         ids=["unknown-main-view", "authority-prefix-view", "trigger-prefix-view", "missing-virtual",
                              "missing-shadow", "missing-sequence", "missing-autoindex", "missing-authority-trigger",
                              "missing-authority-index", "missing-authority-autoindex", "missing-authority-column",
                              "missing-auxiliary-part", "autoindex-nonnull-sql"])
def test_complete_census_cannot_be_replaced_by_prefix_filter_or_counts(change):
    catalog = _catalog()
    if change in ("unknown-main-view", "authority-prefix-view", "trigger-prefix-view"):
        name = {"unknown-main-view": "unknown_view", "authority-prefix-view": "personal_authority_unknown",
                "trigger-prefix-view": "trg_personal_authority_extra"}[change]
        role = "base" if change == "unknown-main-view" else "authority"
        catalog[role + "_objects"].append({"type": "view", "name": name, "table_name": name,
                                          "sql": "CREATE VIEW " + name + " AS SELECT 1"})
        _sort_objects(catalog, role)
    elif change in ("missing-virtual", "missing-shadow", "missing-sequence"):
        _remove_table(catalog, {"missing-virtual": "atom_fts", "missing-shadow": "atom_fts_data",
                                "missing-sequence": "sqlite_sequence"}[change])
    elif change == "missing-autoindex":
        index = next(row for row in catalog["base_objects"] if row["sql"] is None)
        catalog["base_objects"].remove(index)
    elif change.startswith("missing-authority-") and change != "missing-authority-column":
        name = {"missing-authority-trigger": "trg_personal_authority_binding_delete",
                "missing-authority-index": "idx_personal_authority_mutation_grant",
                "missing-authority-autoindex": "sqlite_autoindex_personal_authority_mutation_commit_1"}[change]
        catalog["authority_objects"] = [row for row in catalog["authority_objects"] if row["name"] != name]
    elif change == "missing-authority-column": _table(catalog)["columns"].pop()
    elif change == "missing-auxiliary-part": _table(catalog)["indexes"][0]["parts"].pop()
    else:
        next(row for row in catalog["authority_objects"] if row["sql"] is None)["sql"] = "CREATE INDEX changed ON personal_authority_mutation_commit(operation_id)"
    _domain_only(catalog)


@pytest.mark.parametrize("kind", ["table", "explicit-index", "trigger", "unapproved-autoindex"],
                         ids=["table", "explicit-index", "trigger", "unapproved-autoindex"])
def test_SQL_null_is_limited_to_exact_approved_autoindex_identity(kind):
    catalog = _catalog()
    name = {"table": "personal_authority_binding", "explicit-index": "idx_personal_authority_mutation_grant",
            "trigger": "trg_personal_authority_binding_delete"}.get(kind)
    if name:
        row = next(row for row in catalog["authority_objects"] if row["name"] == name)
    else:
        row = next(row for row in catalog["authority_objects"] if row["sql"] is None)
        row["name"] = "sqlite_autoindex_personal_authority_mutation_commit_999"
        _sort_objects(catalog, "authority")
    row["sql"] = None
    _error(schema._validate_catalog_structure, catalog, "CATALOG_INVALID")


@pytest.mark.parametrize("state", ["base_only", "joint_unbound", "joint_bound"], ids=["base", "unbound", "bound-declaration"])
def test_public_validator_rejects_unpublished_valid_candidate(state):
    catalog = _catalog(state)
    assert schema._match_catalog_candidate(catalog) == (PROFILE, state)
    before = _canonical(catalog)
    _error(schema.validate_catalog_declaration, catalog, "REGISTRY_UNAVAILABLE")
    assert _canonical(catalog) == before


@pytest.mark.parametrize("change,suffix", [("integer", "INTEGER_INVALID"), ("nested-field", "FIELDS_INVALID"),
                                          ("part-sequence", "CATALOG_INVALID"), ("temp", "CATALOG_INVALID"),
                                          ("text", "TEXT_INVALID")],
                         ids=["integer", "nested-field", "part-sequence", "temp", "text"])
def test_public_validator_runs_complete_structure_before_unavailable(change, suffix):
    catalog = _catalog()
    if change == "integer": _target(catalog, "foreign")["id"] = True
    elif change == "nested-field": _target(catalog, "part")["extra"] = None
    elif change == "part-sequence": _target(catalog, "part")["sequence"] = 7
    elif change == "temp": catalog["temp_collisions"] = ["personal_authority_binding"]
    else: _target(catalog, "column")["name"] = "bad\x00text"
    _error(schema.validate_catalog_declaration, catalog, suffix)


@pytest.mark.parametrize("entry", ["_classify_locked", "_install_empty_locked", "_observe_locked"],
                         ids=["classify", "install", "observe"])
def test_runtime_entries_are_not_defined_or_exported(entry):
    assert not hasattr(schema, entry)
    assert entry not in schema.__all__


@pytest.mark.parametrize("function", ["_validate_catalog_structure", "_match_catalog_candidate", "validate_catalog_declaration"],
                         ids=["structure", "candidate", "public"])
def test_single_catalog_API_has_no_caller_reference_profile_SQL_or_ready(function):
    target = getattr(schema, function)
    parameters = inspect.signature(target).parameters
    assert list(parameters) == ["catalog"]
    assert parameters["catalog"].kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    for key in ("reference", "profile", "callback", "sql", "ready"):
        with pytest.raises(TypeError):
            target(_catalog(), **{key: object()})


class _DictSubclass(dict): pass
class _ListSubclass(list): pass
class _IntSubclass(int): pass
class _StrSubclass(str): pass


@pytest.mark.parametrize("kind", ["dict", "list", "int", "str", "key", "cycle-dict", "cycle-list", "tuple", "bytes"],
                         ids=["dict", "list", "int", "str", "key", "cycle-dict", "cycle-list", "tuple", "bytes"])
def test_exact_builtin_types_and_cycles_are_rejected(kind):
    catalog = _catalog()
    if kind == "dict": catalog = _DictSubclass(catalog)
    elif kind == "list": catalog["base_objects"] = _ListSubclass(catalog["base_objects"])
    elif kind == "int": catalog["sqlite_schema_cookie"] = _IntSubclass(140)
    elif kind == "str": catalog["family"] = _StrSubclass(catalog["family"])
    elif kind == "key": catalog[_StrSubclass("extra")] = None
    elif kind == "cycle-dict": catalog["cycle"] = catalog
    elif kind == "cycle-list":
        value = []; value.append(value); catalog["cycle"] = value
    elif kind == "tuple": catalog["temp_collisions"] = ()
    else: _target(catalog, "object")["sql"] = b"CREATE TABLE bad(x)"
    _error(schema._validate_catalog_structure, catalog, "FIELDS_INVALID")


@pytest.mark.parametrize("kind", ["nan", "positive-infinity", "negative-infinity", "surrogate"],
                         ids=["nan", "positive-infinity", "negative-infinity", "surrogate"])
def test_nonfinite_numbers_and_invalid_Unicode_are_rejected(kind):
    catalog = _catalog()
    if kind == "surrogate":
        catalog["family"] = "\ud800"
        suffix = "TEXT_INVALID"
    else:
        catalog["sqlite_schema_cookie"] = {"nan": float("nan"), "positive-infinity": float("inf"),
                                            "negative-infinity": float("-inf")}[kind]
        suffix = "INTEGER_INVALID"
    _error(schema._validate_catalog_structure, catalog, suffix)


@pytest.mark.parametrize("depth,suffix", [(16, "FIELDS_INVALID"), (17, "LIMIT")], ids=["depth16", "depth17"])
def test_depth_budget_precedes_closed_field_check(depth, suffix):
    value = None
    for _ in range(depth - 1): value = [value]
    _error(schema._validate_catalog_structure, {"probe": value}, suffix)


@pytest.mark.parametrize("children,suffix", [(32765, "FIELDS_INVALID"), (32766, "LIMIT")],
                         ids=["nodes32768", "nodes32769"])
def test_key_inclusive_node_boundary_precedes_closed_fields(children, suffix):
    # One root dict, one key, one list and every positional null count as nodes.
    _error(schema._validate_catalog_structure, {"probe": [None] * children}, suffix)


def test_repeated_aliases_are_charged_per_position_including_keys():
    leaf = {"x": None}
    value = {"probe": [leaf] * 10922}
    assert len(_canonical(value)) < 262144
    _error(schema._validate_catalog_structure, value, "LIMIT")
    assert value["probe"][0] is value["probe"][-1] is leaf


@pytest.mark.parametrize("quotes,suffix", [(131066, "FIELDS_INVALID"), (131067, "LIMIT")],
                         ids=["bytes262144", "bytes262146"])
def test_canonical_escaped_byte_boundary_precedes_closed_fields(quotes, suffix):
    value = {"probe": '"' * quotes}
    assert len(_canonical(value)) == 12 + quotes * 2
    _error(schema._validate_catalog_structure, value, suffix)


def test_rejected_content_and_detached_candidate_result_never_mutate_input():
    first = _catalog(); second = _catalog()
    result = schema._match_catalog_candidate(first)
    assert type(result) is tuple and result == (PROFILE, "joint_unbound")
    first["base_objects"][0]["sql"] += "\n"
    rejected_before = _canonical(first)
    _error(schema._match_catalog_candidate, first, "CATALOG_INVALID")
    assert _canonical(first) == rejected_before
    assert schema._match_catalog_candidate(second) == result
    assert _canonical(second) == _canonical(_catalog())
