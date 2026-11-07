"""Pure joint-schema declarations; candidates never confer SQL/write authority.

Only the reviewed first fresh profile has an immutable content candidate.
Runtime publication requires later complete producer/capacity evidence, so the
static published set is empty. There are no SQL, filesystem or runtime APIs.
"""

import json

from . import _personal_authority_joint_sql_schema_refs as _refs


__all__ = ("JointSqlSchemaError", "validate_catalog_declaration")

MAX_CATALOG_BYTES = 262144
MAX_CATALOG_NODES = 32768
MAX_CATALOG_DEPTH = 16
_MAX_SIGNED = 2**63 - 1
_MAX_METADATA = 2**31 - 1
_FAMILY = "personal_authority_joint_sql_catalog"
_STATES = ("base_only", "joint_unbound", "joint_bound")
_PUBLISHED_PROFILE_KEYS = ()
_CATALOG_FIELDS = (
    "family", "format_version", "namespace_version", "state", "base_objects",
    "base_table_contracts", "authority_objects", "authority_table_contracts",
    "temp_collisions", "sqlite_schema_cookie", "sqlite_user_version",
)
_OBJECT_FIELDS = ("type", "name", "table_name", "sql")
_TABLE_FIELDS = ("table_name", "columns", "foreign_keys", "indexes")
_COLUMN_FIELDS = (
    "cid", "name", "declared_type", "not_null", "default_sql",
    "primary_key_position", "hidden",
)
_FOREIGN_FIELDS = (
    "id", "sequence", "target_table", "from_column", "to_column",
    "on_update", "on_delete", "match",
)
_INDEX_FIELDS = ("sequence", "name", "unique", "origin", "partial", "parts")
_PART_FIELDS = ("sequence", "cid", "name", "descending", "collation", "key")
_ACTIONS = ("NO ACTION", "RESTRICT", "SET NULL", "SET DEFAULT", "CASCADE")
_ASCII_LOWER = str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz")


class JointSqlSchemaError(ValueError):
    def __init__(self, suffix):
        self.code = "JOINT_SQL_SCHEMA_" + suffix
        super().__init__(self.code)


def _fail(suffix):
    raise JointSqlSchemaError(suffix)


def _need(condition, suffix):
    if not condition:
        _fail(suffix)


def _snapshot(value):
    """Detach exact builtin JSON values under incremental traversal limits.

    Repeated aliases are charged at every position; only ancestor cycles fail.
    Field sets, sorting, canonicalization and reference comparisons come later.
    """
    nodes = text_bytes = 0
    ancestors = set()

    def visit(item, depth):
        nonlocal nodes, text_bytes
        nodes += 1
        _need(nodes <= MAX_CATALOG_NODES and depth <= MAX_CATALOG_DEPTH, "LIMIT")
        kind = type(item)
        if kind is str:
            _need(len(item) <= MAX_CATALOG_BYTES, "LIMIT")
            try:
                encoded = item.encode("utf-8", "strict")
            except UnicodeError:
                _fail("TEXT_INVALID")
            text_bytes += len(encoded)
            _need(text_bytes <= MAX_CATALOG_BYTES, "LIMIT")
            return item
        if kind is int:
            _need(-2**63 <= item <= _MAX_SIGNED, "INTEGER_INVALID")
            return item
        if item is None:
            return None
        if kind is bool or kind is float:
            _fail("INTEGER_INVALID")
        _need(kind is dict or kind is list, "FIELDS_INVALID")
        _need(len(item) <= MAX_CATALOG_NODES, "LIMIT")
        identity = id(item)
        _need(identity not in ancestors, "FIELDS_INVALID")
        ancestors.add(identity)
        try:
            if kind is list:
                return [visit(child, depth + 1) for child in item]
            result = {}
            for key, child in item.items():
                _need(type(key) is str, "FIELDS_INVALID")
                copied_key = visit(key, depth + 1)
                result[copied_key] = visit(child, depth + 1)
            return result
        finally:
            ancestors.remove(identity)

    owned = visit(value, 0)
    try:
        raw = json.dumps(owned, ensure_ascii=False, sort_keys=True,
                         separators=(",", ":"), allow_nan=False).encode("utf-8")
    except (UnicodeError, ValueError):
        _fail("TEXT_INVALID")
    _need(len(raw) <= MAX_CATALOG_BYTES, "LIMIT")
    return owned


def _fields(value, expected):
    _need(type(value) is dict and set(value) == set(expected), "FIELDS_INVALID")


def _integer(value, minimum=0, maximum=_MAX_METADATA):
    _need(type(value) is int and minimum <= value <= maximum, "INTEGER_INVALID")


def _text(value, *, empty=False, nullable=False):
    if value is None and nullable:
        return
    _need(type(value) is str and (empty or bool(value)) and "\x00" not in value,
          "TEXT_INVALID")


def _list(value):
    _need(type(value) is list, "FIELDS_INVALID")


def _unique_names(names):
    seen = set()
    for name in names:
        folded = name.translate(_ASCII_LOWER)
        _need(folded not in seen, "CATALOG_INVALID")
        seen.add(folded)


def _objects(objects):
    _list(objects)
    previous = None
    for row in objects:
        _fields(row, _OBJECT_FIELDS)
        _text(row["type"])
        _need(row["type"] in ("table", "index", "trigger", "view"), "CATALOG_INVALID")
        _text(row["name"])
        _text(row["table_name"])
        if row["sql"] is None:
            identity = (row["type"], row["name"], row["table_name"])
            _need(identity in _refs.NULL_SQL_OBJECTS, "CATALOG_INVALID")
        else:
            _text(row["sql"])
        order = (row["type"], row["name"])
        _need(previous is None or previous < order, "CATALOG_INVALID")
        previous = order


def _columns(columns):
    _list(columns)
    _need(bool(columns), "CATALOG_INVALID")
    for position, row in enumerate(columns):
        _fields(row, _COLUMN_FIELDS)
        _integer(row["cid"])
        _need(row["cid"] == position, "CATALOG_INVALID")
        _text(row["name"])
        _text(row["declared_type"], empty=True)
        _integer(row["not_null"], maximum=1)
        _text(row["default_sql"], empty=True, nullable=True)
        _integer(row["primary_key_position"])
        _integer(row["hidden"], maximum=3)
    _unique_names([row["name"] for row in columns])


def _foreign_keys(foreign_keys):
    _list(foreign_keys)
    previous = None
    for row in foreign_keys:
        _fields(row, _FOREIGN_FIELDS)
        _integer(row["id"])
        _integer(row["sequence"])
        _text(row["target_table"])
        _text(row["from_column"])
        _text(row["to_column"], empty=True, nullable=True)
        _text(row["on_update"])
        _text(row["on_delete"])
        _need(row["on_update"] in _ACTIONS and row["on_delete"] in _ACTIONS,
              "CATALOG_INVALID")
        _text(row["match"], empty=True)
        order = (row["id"], row["sequence"])
        _need(previous is None or previous < order, "CATALOG_INVALID")
        previous = order


def _indexes(indexes, columns):
    _list(indexes)
    for position, row in enumerate(indexes):
        _fields(row, _INDEX_FIELDS)
        _integer(row["sequence"])
        _need(row["sequence"] == position, "CATALOG_INVALID")
        _text(row["name"])
        _integer(row["unique"], maximum=1)
        _text(row["origin"])
        _need(row["origin"] in ("c", "u", "pk"), "CATALOG_INVALID")
        _integer(row["partial"], maximum=1)
        _list(row["parts"])
        _need(bool(row["parts"]), "CATALOG_INVALID")
        for part_position, part in enumerate(row["parts"]):
            _fields(part, _PART_FIELDS)
            _integer(part["sequence"])
            _need(part["sequence"] == part_position, "CATALOG_INVALID")
            _integer(part["cid"], minimum=-2)
            _text(part["name"], empty=True, nullable=True)
            if part["cid"] < 0:
                _need(part["name"] is None, "CATALOG_INVALID")
            else:
                _need(part["cid"] < len(columns), "CATALOG_INVALID")
                _need(part["name"] == columns[part["cid"]]["name"], "CATALOG_INVALID")
            _integer(part["descending"], maximum=1)
            _text(part["collation"], empty=True, nullable=True)
            _integer(part["key"], maximum=1)
    _unique_names([row["name"] for row in indexes])


def _tables(tables, objects):
    _list(tables)
    previous = None
    for row in tables:
        _fields(row, _TABLE_FIELDS)
        _text(row["table_name"])
        _need(previous is None or previous < row["table_name"], "CATALOG_INVALID")
        previous = row["table_name"]
        _columns(row["columns"])
        _foreign_keys(row["foreign_keys"])
        _indexes(row["indexes"], row["columns"])
    declared = {row["name"] for row in objects if row["type"] == "table"}
    _need(declared == {row["table_name"] for row in tables}, "CATALOG_INVALID")
    for row in objects:
        if row["type"] == "table":
            _need(row["name"] == row["table_name"], "CATALOG_INVALID")


def _checked_catalog(catalog):
    owned = _snapshot(catalog)
    _fields(owned, _CATALOG_FIELDS)
    _text(owned["family"])
    _need(owned["family"] == _FAMILY, "CATALOG_INVALID")
    _integer(owned["format_version"], 1, 1)
    _integer(owned["namespace_version"], 3, 3)
    _text(owned["state"])
    _need(owned["state"] in _STATES, "CATALOG_INVALID")
    _integer(owned["sqlite_schema_cookie"])
    _integer(owned["sqlite_user_version"])
    _list(owned["temp_collisions"])
    _need(not owned["temp_collisions"], "CATALOG_INVALID")
    for role in ("base", "authority"):
        _objects(owned[role + "_objects"])
        _tables(owned[role + "_table_contracts"], owned[role + "_objects"])
    _unique_names([row["name"] for role in ("base", "authority")
                   for row in owned[role + "_objects"]])
    if owned["state"] == "base_only":
        _need(not owned["authority_objects"] and not owned["authority_table_contracts"],
              "CATALOG_INVALID")
    else:
        _need(bool(owned["authority_objects"]) and bool(owned["authority_table_contracts"]),
              "CATALOG_INVALID")
    return owned


def _object_signature(objects):
    return tuple(tuple(row[field] for field in _OBJECT_FIELDS) for row in objects)


def _table_signature(tables):
    return tuple((row["table_name"],
                  tuple(tuple(col[field] for field in _COLUMN_FIELDS) for col in row["columns"]),
                  tuple(tuple(fk[field] for field in _FOREIGN_FIELDS) for fk in row["foreign_keys"]),
                  tuple(tuple(index[field] for field in _INDEX_FIELDS[:-1]) +
                        (tuple(tuple(part[field] for field in _PART_FIELDS)
                               for part in index["parts"]),)
                        for index in row["indexes"])) for row in tables)


def _candidate_match_owned(owned):
    _need(_object_signature(owned["base_objects"]) == _refs.BASE_OBJECTS and
          _table_signature(owned["base_table_contracts"]) == _refs.BASE_TABLE_CONTRACTS,
          "CATALOG_INVALID")
    if owned["state"] != "base_only":
        _need(_object_signature(owned["authority_objects"]) == _refs.AUTHORITY_OBJECTS and
              _table_signature(owned["authority_table_contracts"]) == _refs.AUTHORITY_TABLE_CONTRACTS,
              "CATALOG_INVALID")
    return (_refs.PROFILE_KEY, owned["state"])


def _validate_catalog_structure(catalog):
    """Validate a full bounded detached catalog declaration; return None."""
    _checked_catalog(catalog)


def _match_catalog_candidate(catalog):
    """Return fixed profile/state content only; never an actual SQL receipt."""
    return _candidate_match_owned(_checked_catalog(catalog))


def validate_catalog_declaration(catalog):
    """Validate structure, then require a statically published profile entry.

    Empty publication is an enforced policy after complete structure checking,
    not a runtime placeholder. Publishing a candidate requires a later reviewed
    source change with actual full-observation producer/capacity result pins.
    """
    owned = _checked_catalog(catalog)
    _need(bool(_PUBLISHED_PROFILE_KEYS), "REGISTRY_UNAVAILABLE")
    profile_key, state = _candidate_match_owned(owned)
    _need(profile_key in _PUBLISHED_PROFILE_KEYS, "REGISTRY_UNAVAILABLE")
    return state
