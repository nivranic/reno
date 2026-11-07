"""Schema17: immutable Cloud continuity/control/admission representations.

Installation is DDL only. Typed record checks bind canonical bodies/results to
their fixed columns; they do not establish external-anchor continuity, prove a
negative outcome, validate a live writer or grant source dispatch permission.
"""
from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
import hashlib
import json
import re
import sqlite3
import uuid

from . import sync_source_readmit_contract as wire
from .storage_schema import _contract


SCHEMA_VERSION = 17
FORMAT_VERSION = 1
MAX_INTEGER = 2**63 - 1
MAX_ACTIVATION_BYTES = 4096
MAX_ORIGINAL_BYTES = 16 * 1024
MAX_CONTROL_BYTES = wire.MAX_CONTROL_BYTES
MAX_READMIT_BYTES = wire.MAX_READMIT_BYTES
ACTIVATION = "sync_source_continuity_activation"
REGISTRATION = "sync_source_control_registration"
DISPOSITION = "sync_source_control_disposition"
ADMISSION = "sync_source_admission"
_IDENTITIES = ("source_library_id", "source_node_id", "target_library_id", "target_node_id")
COLUMNS = {
    ACTIVATION: ("continuity_id", "format_version", "owner_profile_sha256", *_IDENTITIES,
        "target_epoch", "contract_json", "contract_sha256", "activated_at"),
    REGISTRATION: ("request_id", "format_version", *_IDENTITIES, "continuity_id", "core_sha256",
        "item_id", "original_request_id", "original_request_sha256", "original_kind",
        "original_request_json", "request_json", "request_sha256", "registration_sha256",
        "result_json", "result_sha256", "committed_at"),
    DISPOSITION: ("request_id", "format_version", *_IDENTITIES, "core_sha256", "item_id",
        "original_request_id", "original_request_sha256", "original_kind", "original_request_json",
        "continuity_id", "registration_sha256", "disposition", "request_json", "request_sha256",
        "result_json", "result_sha256", "disposition_sha256", "defer_receipt_json",
        "defer_receipt_sha256", "committed_at"),
    ADMISSION: ("admission_id", "format_version", *_IDENTITIES, "core_sha256", "item_id",
        "submitted_url_sha256", "current_home_epoch", "receiver_library_epoch", "recovery_generation",
        "control_revision", "previous_admission_id", "lease_generation", "attempts", "attempt_basis",
        "request_json", "request_sha256", "admission_sha256", "result_json", "result_sha256", "committed_at"),
}


def _uuid_sql(name, *, nullable=False):
    valid = (f"typeof({name})='text' AND length({name})=36 AND substr({name},9,1)='-' "
        f"AND substr({name},14,1)='-' AND substr({name},19,1)='-' AND substr({name},24,1)='-' "
        f"AND length(replace({name},'-',''))=32 AND replace({name},'-','') NOT GLOB '*[^0-9a-f]*'")
    return "TEXT" + ("" if nullable else " NOT NULL") + " CHECK(" + (
        f"{name} IS NULL OR ({valid})" if nullable else valid) + ")"


def _sha_sql(name, *, nullable=False):
    valid = f"typeof({name})='text' AND length({name})=64 AND {name} NOT GLOB '*[^0-9a-f]*'"
    return "TEXT" + ("" if nullable else " NOT NULL") + " CHECK(" + (
        f"{name} IS NULL OR ({valid})" if nullable else valid) + ")"


def _integer_sql(name, *, minimum=1, maximum=MAX_INTEGER):
    return f"INTEGER NOT NULL CHECK(typeof({name})='integer' AND {name} BETWEEN {minimum} AND {maximum})"


def _json_sql(name, maximum, *, nullable=False):
    valid = (f"typeof({name})='text' AND length(CAST({name} AS BLOB)) BETWEEN 2 AND {maximum} "
        f"AND json_valid({name}) AND CASE WHEN json_valid({name}) THEN json_type({name})='object' ELSE 0 END")
    return "TEXT" + ("" if nullable else " NOT NULL") + " CHECK(" + (
        f"{name} IS NULL OR ({valid})" if nullable else valid) + ")"


def _time_sql(name):
    pattern = "".join("[0-9]" if char == "0" else char for char in "0000-00-00T00:00:00.000Z")
    return f"TEXT NOT NULL CHECK(typeof({name})='text' AND length({name})=24 AND {name} GLOB '{pattern}')"


def _declarations(columns, *, primary):
    result = []
    for name in columns:
        if name == "format_version":
            declaration = _integer_sql(name, maximum=1)
        elif name in {"continuity_id", "previous_admission_id"} or name.endswith("_id"):
            declaration = _integer_sql(name) if name == "item_id" else _uuid_sql(name,
                nullable=name == "previous_admission_id" or name == "continuity_id" and columns is COLUMNS[DISPOSITION])
        elif name.endswith("_sha256"):
            declaration = _sha_sql(name, nullable=columns is COLUMNS[DISPOSITION] and name in (
                "registration_sha256", "defer_receipt_sha256"))
        elif name.endswith("_json"):
            maximum = (MAX_ACTIVATION_BYTES if name == "contract_json" else MAX_ORIGINAL_BYTES
                if name in {"original_request_json", "defer_receipt_json"} else MAX_READMIT_BYTES
                if columns is COLUMNS[ADMISSION] else MAX_CONTROL_BYTES)
            declaration = _json_sql(name, maximum, nullable=name == "defer_receipt_json")
        elif name in {"committed_at", "activated_at"}:
            declaration = _time_sql(name)
        elif name == "original_kind":
            declaration = "TEXT NOT NULL CHECK(original_kind IN ('budget_defer','budget_resume','source_fail'))"
        elif name == "disposition":
            declaration = "TEXT NOT NULL CHECK(disposition IN ('applied','not_applied'))"
        elif name == "attempt_basis":
            declaration = "TEXT NOT NULL CHECK(attempt_basis IN ('held_claim','original_defer_refund'))"
        else:
            declaration = _integer_sql(name, minimum=0 if name in {"recovery_generation", "control_revision"} else 1,
                maximum=5 if name == "attempts" else MAX_INTEGER)
        result.append(name + " " + declaration + (" PRIMARY KEY" if name == primary else ""))
    if "source_library_id" in columns:
        result.append("CHECK(source_library_id!=target_library_id AND source_node_id!=target_node_id)")
    if "original_request_id" in columns:
        result.append("CHECK(request_id!=original_request_id)")
    return result


TABLES = {
    ACTIVATION: ",\n".join(_declarations(COLUMNS[ACTIVATION], primary="continuity_id")),
    REGISTRATION: ",\n".join((*_declarations(COLUMNS[REGISTRATION], primary="request_id"),
        f"FOREIGN KEY(continuity_id) REFERENCES {ACTIVATION}(continuity_id)",
        "FOREIGN KEY(item_id) REFERENCES sync_queue(item_id)")),
    DISPOSITION: ",\n".join((*_declarations(COLUMNS[DISPOSITION], primary="request_id"),
        f"FOREIGN KEY(continuity_id) REFERENCES {ACTIVATION}(continuity_id)",
        f"FOREIGN KEY(registration_sha256) REFERENCES {REGISTRATION}(registration_sha256)",
        "FOREIGN KEY(item_id) REFERENCES sync_queue(item_id)",
        "CHECK((continuity_id IS NULL)=(registration_sha256 IS NULL))",
        "CHECK(disposition!='not_applied' OR continuity_id IS NOT NULL)",
        "CHECK((defer_receipt_json IS NULL)=(defer_receipt_sha256 IS NULL))",
        "CHECK((disposition='applied' AND original_kind='budget_resume')=(defer_receipt_json IS NOT NULL))")),
    ADMISSION: ",\n".join((*_declarations(COLUMNS[ADMISSION], primary="admission_id"),
        "FOREIGN KEY(item_id) REFERENCES sync_queue(item_id)",
        f"FOREIGN KEY(previous_admission_id) REFERENCES {ADMISSION}(admission_id)",
        "CHECK(previous_admission_id IS NULL OR previous_admission_id!=admission_id)")),
}
INDEXES = (
    f"CREATE UNIQUE INDEX idx_source_continuity_identity ON {ACTIVATION}(owner_profile_sha256,source_library_id,source_node_id,target_library_id,target_node_id,target_epoch)",
    f"CREATE UNIQUE INDEX idx_source_registration_original ON {REGISTRATION}(original_request_id)",
    f"CREATE UNIQUE INDEX idx_source_registration_sha ON {REGISTRATION}(registration_sha256)",
    f"CREATE INDEX idx_source_registration_core ON {REGISTRATION}(core_sha256,item_id,committed_at,request_id)",
    f"CREATE UNIQUE INDEX idx_source_disposition_original ON {DISPOSITION}(original_request_id)",
    f"CREATE INDEX idx_source_disposition_core ON {DISPOSITION}(core_sha256,item_id,committed_at,request_id)",
    f"CREATE UNIQUE INDEX idx_source_admission_claim ON {ADMISSION}(item_id,lease_generation)",
    f"CREATE UNIQUE INDEX idx_source_admission_previous ON {ADMISSION}(previous_admission_id) WHERE previous_admission_id IS NOT NULL",
    f"CREATE UNIQUE INDEX idx_source_admission_first ON {ADMISSION}(core_sha256,item_id) WHERE previous_admission_id IS NULL",
    f"CREATE INDEX idx_source_admission_core ON {ADMISSION}(core_sha256,item_id,committed_at,admission_id)",
)
TRIGGERS = tuple(f"CREATE TRIGGER {table}_no_{action.lower()} BEFORE {action} ON {table} "
    "BEGIN SELECT RAISE(ABORT,'SOURCE_READMIT_IMMUTABLE'); END" for table in TABLES for action in ("UPDATE", "DELETE"))
RESERVED_NAMES = tuple(TABLES) + tuple(re.search(r"INDEX (\w+)", sql).group(1) for sql in INDEXES) + tuple(
    re.search(r"TRIGGER (\w+)", sql).group(1) for sql in TRIGGERS)


class SourceReadmitSchemaError(sqlite3.DatabaseError):
    def __init__(self, code="SOURCE_READMIT_SCHEMA_INVALID"):
        self.code = code
        super().__init__(code)


class SourceReadmitRecordError(ValueError):
    def __init__(self):
        super().__init__("SOURCE_READMIT_RECORD_INVALID")


def _shape_fail(code="SOURCE_READMIT_SCHEMA_INVALID"):
    raise SourceReadmitSchemaError(code) from None


def _record_fail():
    raise SourceReadmitRecordError() from None


def _require_queue(con):
    primary = [(row[1], row[2].upper(), row[5]) for row in con.execute("PRAGMA main.table_info(sync_queue)") if row[5]]
    if primary != [("item_id", "INTEGER", 1)]:
        _shape_fail()


def _install(con):
    _require_queue(con)
    for name, declaration in TABLES.items():
        con.execute(f"CREATE TABLE {name} ({declaration})")
    for sql in INDEXES + TRIGGERS:
        con.execute(sql)


def _normalized(sql):
    return " ".join((sql or "").split()).replace('"', '')


def _triggers(con):
    return [(row[0], row[1], _normalized(row[2])) for row in con.execute(
        "SELECT name,tbl_name,sql FROM main.sqlite_master WHERE type='trigger' AND lower(tbl_name) IN (" +
        ",".join("?" for _ in TABLES) + ") ORDER BY name", tuple(TABLES))]


def validate(con):
    """Read-only exact columns/checks/FKs/actions/indexes/trigger bindings."""
    _require_queue(con)
    if any(row[0].lower() in RESERVED_NAMES for row in con.execute("SELECT name FROM sqlite_temp_master")):
        _shape_fail()
    reference = sqlite3.connect(":memory:")
    try:
        reference.execute("CREATE TABLE sync_queue(item_id INTEGER PRIMARY KEY)")
        _install(reference)
        if any(_contract(con, table) != _contract(reference, table) for table in TABLES) or _triggers(con) != _triggers(reference):
            _shape_fail()
        query = "SELECT type,name,tbl_name FROM main.sqlite_master WHERE lower(name) IN (" + ",".join("?" for _ in RESERVED_NAMES) + ") ORDER BY name"
        # Row factories vary; compare values instead of sqlite3.Row identity.
        if [tuple(row) for row in con.execute(query, RESERVED_NAMES)] != [tuple(row) for row in reference.execute(query, RESERVED_NAMES)]:
            _shape_fail()
    finally:
        reference.close()


def preflight(con, *, require_absent=True):
    """Reject future names before backup; never adopt same-name old objects."""
    if type(require_absent) is not bool:
        _shape_fail()
    for master in ("main.sqlite_master", "sqlite_temp_master"):
        found = con.execute("SELECT 1 FROM " + master + " WHERE lower(name) IN (" +
            ",".join("?" for _ in RESERVED_NAMES) + ") LIMIT 1", RESERVED_NAMES).fetchone()
        if found:
            if require_absent:
                _shape_fail("SOURCE_READMIT_SCHEMA_COLLISION")
            validate(con)
            return True
    return False


def install(con):
    """Only DDL, without commit, metadata/capability seeds or partial repair."""
    if not preflight(con, require_absent=False):
        _install(con)


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _record_fail()


def _sha(value):
    if type(value) is not str or not re.fullmatch("[0-9a-f]{64}", value):
        _record_fail()


def _integer(value, *, minimum=1, maximum=MAX_INTEGER):
    if type(value) is not int or not minimum <= value <= maximum:
        _record_fail()


def _time(value):
    if type(value) is not str or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z", value):
        _record_fail()
    datetime.fromisoformat(value.replace("Z", "+00:00"))


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _record_fail()
        result[key] = value
    return result


def _decode(row, name, maximum):
    raw = row[name + "_json"]
    if type(raw) is not str or len(raw) > maximum:
        _record_fail()
    encoded = raw.encode("utf-8")
    if not 2 <= len(encoded) <= maximum:
        _record_fail()
    _sha(row[name + "_sha256"])
    if hashlib.sha256(encoded).hexdigest() != row[name + "_sha256"]:
        _record_fail()
    value = json.loads(raw, object_pairs_hook=_pairs, parse_constant=lambda _: _record_fail())
    if type(value) is not dict or wire.canonical_json(value, maximum=maximum) != raw:
        _record_fail()
    return value


def _identity_columns(row, source, target):
    for prefix, identity in (("source", source), ("target", target)):
        if type(identity) is not dict:
            _record_fail()
        for key in ("library_id", "node_id"):
            _uuid(row[prefix + "_" + key])
            if row[prefix + "_" + key] != identity[key]:
                _record_fail()
    if any(row["source_" + key] == row["target_" + key] for key in ("library_id", "node_id")):
        _record_fail()


def _activation(row):
    _uuid(row["continuity_id"])
    _sha(row["owner_profile_sha256"])
    _integer(row["target_epoch"])
    _time(row["activated_at"])
    contract = _decode(row, "contract", MAX_ACTIVATION_BYTES)
    if set(contract) != {"format_version", "continuity_id", "owner_profile_sha256", "source_identity", "target_identity", "target_epoch", "activated_at"}:
        _record_fail()
    for identity in (contract["source_identity"], contract["target_identity"]):
        if type(identity) is not dict or set(identity) != {"library_id", "node_id"}:
            _record_fail()
    _integer(contract["format_version"], maximum=1)
    _integer(contract["target_epoch"])
    _identity_columns(row, contract["source_identity"], contract["target_identity"])
    if any(row[name] != contract[name] for name in ("format_version", "continuity_id", "owner_profile_sha256", "target_epoch", "activated_at")):
        _record_fail()


def _control(row, *, disposition):
    request = _decode(row, "request", MAX_CONTROL_BYTES)
    result = _decode(row, "result", MAX_CONTROL_BYTES)
    original = _decode(row, "original_request", MAX_ORIGINAL_BYTES)
    parent = None
    if disposition:
        if (row["defer_receipt_json"] is None) != (row["defer_receipt_sha256"] is None):
            _record_fail()
        if row["defer_receipt_json"] is not None:
            parent = _decode(row, "defer_receipt", MAX_ORIGINAL_BYTES)
            if parent.get("replayed") is not False:
                _record_fail()
        request = wire.normalize_dispose_request(request)
        result = wire.normalize_dispose_result(request, result, defer_receipt=parent)
        if (parent is not None) != (result["disposition"] == "applied" and result["original_kind"] == "budget_resume"):
            _record_fail()
    else:
        request = wire.normalize_register_request(request)
        result = wire.normalize_register_result(request, result)
    if result["replayed"] is not False or request["original_request"] != original:
        _record_fail()
    _identity_columns(row, request["source"], request["target"])
    names = ("request_id", "format_version", "core_sha256", "item_id", "original_request_id", "original_request_sha256", "original_kind", "continuity_id", "registration_sha256", "committed_at")
    for name in names:
        if row[name] != result[name]:
            _record_fail()
    if disposition:
        if row["disposition"] != result["disposition"] or row["disposition_sha256"] != wire.disposition_sha256(result):
            _record_fail()
    _time(row["committed_at"])
    # format/item are independent columns: bool==1 must not bind by equality.
    _integer(row["item_id"])
    _uuid(row["request_id"])
    _uuid(row["original_request_id"])
    _sha(row["core_sha256"])
    if row["continuity_id"] is not None:
        _uuid(row["continuity_id"])
    if row["registration_sha256"] is not None:
        _sha(row["registration_sha256"])
    if disposition:
        _sha(row["disposition_sha256"])


def _admission(row):
    request = wire.normalize_readmit_request(_decode(row, "request", MAX_READMIT_BYTES))
    result = wire.normalize_readmit_result(request, _decode(row, "result", MAX_READMIT_BYTES))
    if result["replayed"] is not False:
        _record_fail()
    _identity_columns(row, request["source"], request["target"])
    for name in ("admission_id", "format_version", "core_sha256", "item_id", "submitted_url_sha256", "current_home_epoch", "recovery_generation", "control_revision", "previous_admission_id", "attempts", "attempt_basis", "admission_sha256", "committed_at"):
        if row[name] != result[name]:
            _record_fail()
    if row["lease_generation"] != result["claim"]["lease_generation"] or row["receiver_library_epoch"] != result["recovery_stream"]["library_epoch"]:
        _record_fail()
    _uuid(row["admission_id"])
    if row["previous_admission_id"] is not None:
        _uuid(row["previous_admission_id"])
    for name in ("core_sha256", "submitted_url_sha256", "admission_sha256"):
        _sha(row[name])
    for name in ("item_id", "current_home_epoch", "receiver_library_epoch", "lease_generation"):
        _integer(row[name])
    for name in ("recovery_generation", "control_revision"):
        _integer(row[name], minimum=0)
    _integer(row["attempts"], maximum=5)
    _time(row["committed_at"])


def validate_record(table, mapping):
    """Return a detached exact record; validity is representation, not authority."""
    try:
        if type(table) is not str or table not in COLUMNS or not isinstance(mapping, (Mapping, sqlite3.Row)):
            _record_fail()
        row = dict(mapping)
        if set(row) != set(COLUMNS[table]):
            _record_fail()
        _integer(row["format_version"], maximum=1)
        {ACTIVATION: _activation, REGISTRATION: lambda value: _control(value, disposition=False),
         DISPOSITION: lambda value: _control(value, disposition=True), ADMISSION: _admission}[table](row)
        return row
    except SourceReadmitRecordError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _record_fail()


def validate_activation_record(mapping):
    return validate_record(ACTIVATION, mapping)


def validate_registration_record(mapping):
    return validate_record(REGISTRATION, mapping)


def validate_disposition_record(mapping):
    return validate_record(DISPOSITION, mapping)


def validate_admission_record(mapping):
    return validate_record(ADMISSION, mapping)
