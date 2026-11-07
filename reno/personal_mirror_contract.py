"""Fixed personal-domain snapshot facts, without mirror or promotion authority.

Version 1 carries the original rows of all ten personal tables. JSON stored in
cells is validated but never decoded and rewritten in the snapshot. Knowledge
targets may be missing or stale; their immutable saved evidence is retained.
Only ready attachments require a body in the manifest. Staging/failed rows and
their operation identifiers remain in the raw table without claiming a body.

This module neither installs a schema nor grants a receiver permission to read,
publish, mutate, share, or become a personal primary.
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
import hashlib
import json
from pathlib import PurePosixPath
import re
import sqlite3
from types import MappingProxyType
import uuid

from . import evidence_identity as identity

FORMAT_VERSION = 1
MAX_SNAPSHOT_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000
MAX_CELL_BYTES = 1024 * 1024
META_KEYS = ("default_project_id", "library_id", "personal_primary_id", "primary_epoch")

# This is a wire-contract pin, deliberately independent of mutable migrations.
# Any future personal schema needs a new contract, not an inferred projection.
_DDL = {
    "renovation_project": """project_id TEXT PRIMARY KEY, name TEXT NOT NULL,
        profile_json TEXT NOT NULL DEFAULT '{}', revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT""",
    "decision_card": """card_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        title TEXT NOT NULL, question TEXT NOT NULL DEFAULT '', context_json TEXT NOT NULL DEFAULT '{}',
        space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '',
        alternatives_json TEXT NOT NULL DEFAULT '[]', selected_option_id TEXT, decision_text TEXT NOT NULL DEFAULT '',
        decision_reason TEXT NOT NULL DEFAULT '', open_questions_json TEXT NOT NULL DEFAULT '[]',
        content_snapshot_json TEXT NOT NULL DEFAULT '{}', state TEXT NOT NULL DEFAULT 'considering'
        CHECK(state IN ('considering','adopted','not_adopted','archived')), revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT""",
    "source_record": """source_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        source_type TEXT NOT NULL CHECK(source_type IN ('manual_note','standard_record','product_document','quote','site_photo')),
        title TEXT NOT NULL, publisher TEXT NOT NULL DEFAULT '', document_no TEXT NOT NULL DEFAULT '',
        edition TEXT NOT NULL DEFAULT '', published_on TEXT NOT NULL DEFAULT '', valid_from TEXT NOT NULL DEFAULT '',
        valid_to TEXT NOT NULL DEFAULT '', source_url TEXT NOT NULL DEFAULT '', locator TEXT NOT NULL DEFAULT '',
        excerpt TEXT NOT NULL DEFAULT '', scope_json TEXT NOT NULL DEFAULT '{}',
        verification_state TEXT NOT NULL DEFAULT 'unverified' CHECK(verification_state IN ('unverified','user_checked')),
        validity TEXT NOT NULL DEFAULT 'unknown' CHECK(validity IN ('unknown','current','superseded','withdrawn')),
        checked_at TEXT, check_note TEXT NOT NULL DEFAULT '', revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "source_attachment": """attachment_id TEXT PRIMARY KEY, source_id TEXT NOT NULL REFERENCES source_record(source_id),
        project_id TEXT NOT NULL REFERENCES renovation_project(project_id), sha256 TEXT, byte_size INTEGER,
        media_type TEXT NOT NULL, original_name TEXT NOT NULL, storage_key TEXT NOT NULL UNIQUE,
        state TEXT NOT NULL CHECK(state IN ('staging','ready','failed')), created_at TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE, error TEXT""",
    "personal_evidence_ref": """ref_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        owner_kind TEXT NOT NULL, owner_id TEXT NOT NULL, target_kind TEXT NOT NULL, target_id TEXT NOT NULL,
        fingerprint_version INTEGER NOT NULL DEFAULT 1, target_fingerprint TEXT, snapshot_json TEXT NOT NULL,
        created_at TEXT NOT NULL""",
    "checklist_instance": """instance_id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES renovation_project(project_id),
        title TEXT NOT NULL, space TEXT NOT NULL DEFAULT '', stage TEXT NOT NULL DEFAULT '', basis_kb_revision INTEGER,
        lifecycle_state TEXT NOT NULL DEFAULT 'active' CHECK(lifecycle_state IN ('active','archived')),
        revision INTEGER NOT NULL DEFAULT 1, created_at TEXT NOT NULL, updated_at TEXT NOT NULL, archived_at TEXT""",
    "checklist_item": """item_id TEXT PRIMARY KEY, instance_id TEXT NOT NULL REFERENCES checklist_instance(instance_id),
        title TEXT NOT NULL, conditions_json TEXT NOT NULL DEFAULT '{}', method_text TEXT NOT NULL DEFAULT '',
        basis_kind TEXT NOT NULL, criteria_json TEXT NOT NULL DEFAULT '{}',
        criteria_state TEXT NOT NULL DEFAULT 'needs_verification' CHECK(criteria_state IN ('known','needs_verification')),
        position INTEGER NOT NULL DEFAULT 0, revision INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL""",
    "checklist_check": """check_id TEXT PRIMARY KEY, item_id TEXT NOT NULL REFERENCES checklist_item(item_id),
        item_revision INTEGER NOT NULL, criteria_snapshot_hash TEXT NOT NULL,
        operation_id TEXT NOT NULL UNIQUE, commit_seq INTEGER NOT NULL,
        record_kind TEXT NOT NULL CHECK(record_kind IN ('inspection','remediation','void')), result TEXT,
        measurements_json TEXT NOT NULL DEFAULT '[]', note TEXT NOT NULL DEFAULT '',
        attachment_refs_json TEXT NOT NULL DEFAULT '[]', checked_at TEXT NOT NULL, created_at TEXT NOT NULL,
        supersedes_check_id TEXT REFERENCES checklist_check(check_id), UNIQUE(item_id,commit_seq)""",
    "personal_revision": """entity_kind TEXT NOT NULL, entity_id TEXT NOT NULL, revision INTEGER NOT NULL,
        snapshot_json TEXT NOT NULL, operation_id TEXT, created_at TEXT NOT NULL,
        PRIMARY KEY(entity_kind,entity_id,revision)""",
    "mutation_receipt": """operation_id TEXT PRIMARY KEY, scope TEXT NOT NULL, request_hash TEXT NOT NULL,
        response_status INTEGER NOT NULL, response_json TEXT NOT NULL, created_at TEXT NOT NULL""",
}
TABLE_NAMES = tuple(_DDL)
_ENTITY_TABLES = {
    "project": ("renovation_project", "project_id"), "card": ("decision_card", "card_id"),
    "source": ("source_record", "source_id"), "attachment": ("source_attachment", "attachment_id"),
    "checklist": ("checklist_instance", "instance_id"), "checklist_item": ("checklist_item", "item_id"),
    "check": ("checklist_check", "check_id"),
}
_HISTORY_KINDS = frozenset({"project", "card", "source", "checklist", "checklist_item"})
_SAVED_REF_FIELDS = {"card": "evidence_refs", "checklist_item": "refs"}
_SAVED_REF_KEYS = ("ref_id", "project_id", "owner_kind", "owner_id", "target_kind", "target_id",
                   "fingerprint_version", "target_fingerprint", "created_at", "snapshot")
_JSON_TYPES = {
    "profile_json": dict, "context_json": dict, "alternatives_json": list,
    "open_questions_json": list, "content_snapshot_json": dict, "scope_json": dict,
    "snapshot_json": dict, "conditions_json": dict, "criteria_json": dict,
    "measurements_json": list, "attachment_refs_json": list, "response_json": dict,
}
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_FINGERPRINT = re.compile(r"v1:[0-9a-f]{64}\Z")
_DECIMAL = re.compile(r"(?:0|[1-9][0-9]*)\Z")
_SQL_TOKEN = re.compile(r"'(?:''|[^'])*'|\"(?:\"\"|[^\"])*\"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]", re.S)


class PersonalSnapshotError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


def _fail(code):
    raise PersonalSnapshotError(code)


def _json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _fail("SNAPSHOT_DUPLICATE_JSON_KEY")
        result[key] = value
    return result


def _invalid_constant(_value):
    _fail("SNAPSHOT_INVALID_JSON")


def _decode(value, expected=dict):
    try:
        out = json.loads(value, object_pairs_hook=_pairs, parse_constant=_invalid_constant)
        if type(out) is not expected:
            _fail("SNAPSHOT_INVALID_JSON")
        # Also reject finite-overflow floats (e.g. 1e999) and surrogate strings.
        _json(out).encode("utf-8")
        return out
    except (ValueError, TypeError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, PersonalSnapshotError):
            raise
        _fail("SNAPSHOT_INVALID_JSON")


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            raise ValueError
    except (ValueError, AttributeError, TypeError):
        _fail("SNAPSHOT_INVALID_UUID")
    return value


def _text(value, *, nonempty=False):
    try:
        if (type(value) is not str or "\x00" in value
                or len(value.encode("utf-8")) > MAX_CELL_BYTES or nonempty and not value):
            raise ValueError
    except (ValueError, UnicodeError):
        _fail("SNAPSHOT_INVALID_VALUE")


def _integer(value, minimum=0):
    if type(value) is not int or not minimum <= value <= 2**63 - 1:
        _fail("SNAPSHOT_INVALID_VALUE")


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail("SNAPSHOT_INVALID_SHA256")


def _fingerprint(value):
    if type(value) is not str or not _FINGERPRINT.fullmatch(value):
        _fail("SNAPSHOT_INVALID_FINGERPRINT")


def storage_key(value):
    _text(value, nonempty=True)
    path = PurePosixPath(value)
    if (path.is_absolute() or str(path) != value or ".." in path.parts
            or "\\" in value or ":" in value or len(path.parts) != 2 or path.parts[0] != "files"):
        _fail("SNAPSHOT_INVALID_ATTACHMENT_PATH")
    return path


def file_identity(value):
    if type(value) is not dict or set(value) != {"device", "inode", "byte_size", "mtime_ns", "ctime_ns"}:
        _fail("SNAPSHOT_INVALID_FILE_IDENTITY")
    _integer(value["byte_size"])
    for key in ("device", "inode", "mtime_ns", "ctime_ns"):
        if type(value[key]) is not str or not _DECIMAL.fullmatch(value[key]):
            _fail("SNAPSHOT_INVALID_FILE_IDENTITY")
    if int(value["inode"]) == 0:
        _fail("SNAPSHOT_INVALID_FILE_IDENTITY")


def _tokens(sql):
    if type(sql) is not str:
        _fail("SNAPSHOT_SCHEMA_DRIFT")
    return [token if token.startswith(("'", '"')) else token.upper() for token in _SQL_TOKEN.findall(sql)]


def _describe_table(con, table):
    raw = con.execute("SELECT type,tbl_name,rootpage,sql FROM sqlite_schema WHERE name=?", (table,)).fetchall()
    if len(raw) != 1 or raw[0][0] != "table" or raw[0][1] != table or raw[0][2] <= 0:
        _fail("SNAPSHOT_SCHEMA_DRIFT")
    columns = [{"name": r[1], "type": r[2], "not_null": r[3], "default": r[4], "pk": r[5], "hidden": r[6]}
               for r in con.execute(f"PRAGMA table_xinfo({table})")]
    if not columns or len(columns) > 64:
        _fail("SNAPSHOT_SCHEMA_DRIFT")
    primary_key = [c["name"] for c in sorted(columns, key=lambda c: c["pk"]) if c["pk"]]
    unique = []
    for row in con.execute(f"PRAGMA index_list({table})").fetchall():
        if row[2]:
            if row[4]:
                _fail("SNAPSHOT_SCHEMA_DRIFT")
            # Parameters are unavailable for PRAGMA; quote source names as SQL
            # literals. Schema bootstrap/row budgets belong to the capture.
            quoted = row[1].replace("'", "''")
            unique.append([r[2] for r in con.execute(f"PRAGMA index_info('{quoted}')")])
    foreign_keys = [[r[2], r[3], r[4], r[5], r[6], r[7]] for r in con.execute(f"PRAGMA foreign_key_list({table})")]
    return {"columns": columns, "primary_key": primary_key, "order_by": primary_key,
            "unique_keys": sorted(unique), "foreign_keys": sorted(foreign_keys), "sql_tokens": _tokens(raw[0][3])}


@lru_cache(maxsize=1)
def _schema_bytes():
    con = sqlite3.connect(":memory:")
    try:
        for name, ddl in _DDL.items():
            con.execute(f"CREATE TABLE {name} ({ddl})")
        return _json({"personal_schema_version": FORMAT_VERSION,
                      "tables": {name: _describe_table(con, name) for name in TABLE_NAMES}}).encode("utf-8")
    finally:
        con.close()


def schema_descriptor():
    """Detached exact column, PK, ordering, FK, unique and CHECK descriptor."""
    return _decode(_schema_bytes())


def validate_database_schema(con):
    """Read-only fixed-schema check; no seed, DDL or migration on this DB."""
    expected = schema_descriptor()
    actual = {"personal_schema_version": FORMAT_VERSION,
              "tables": {name: _describe_table(con, name) for name in TABLE_NAMES}}
    if actual != expected:
        _fail("SNAPSHOT_SCHEMA_DRIFT")
    # A derived view or generated expression cannot masquerade as raw rows.
    return expected


def _rows(document):
    tables = document["tables"]
    if type(tables) is not dict or set(tables) != set(TABLE_NAMES):
        _fail("SNAPSHOT_TABLE_SET_MISMATCH")
    indexes, total = {}, 0
    for table, descriptor in document["schema"]["tables"].items():
        rows = tables[table]
        if type(rows) is not list:
            _fail("SNAPSHOT_INVALID_ROWS")
        total += len(rows)
        if total > MAX_ROWS:
            _fail("SNAPSHOT_TOO_MANY_ROWS")
        columns = descriptor["columns"]
        primary = descriptor["primary_key"]
        index, previous, uniques = {}, None, [set() for _ in descriptor["unique_keys"]]
        for row in rows:
            if type(row) is not dict or set(row) != {c["name"] for c in columns}:
                _fail("SNAPSHOT_COLUMN_SET_MISMATCH")
            for column in columns:
                value, name = row[column["name"]], column["name"]
                if value is None:
                    if column["not_null"] or column["pk"]:
                        _fail("SNAPSHOT_NULL_REQUIRED_VALUE")
                    continue
                if column["type"] == "INTEGER":
                    _integer(value)
                else:
                    _text(value)
                if name in _JSON_TYPES:
                    _decode(value, _JSON_TYPES[name])
                if name in {"revision", "item_revision", "commit_seq"}:
                    _integer(value, 1)
            key = tuple(row[name] for name in primary)
            if previous is not None and key <= previous:
                _fail("SNAPSHOT_DUPLICATE_OR_UNSORTED_KEY")
            previous = key
            index[key] = row
            for names, seen in zip(descriptor["unique_keys"], uniques):
                values = tuple(row[name] for name in names)
                if None not in values:
                    if values in seen:
                        _fail("SNAPSHOT_DUPLICATE_UNIQUE_KEY")
                    seen.add(values)
        indexes[table] = index
    return indexes


def _entity(indexes, kind, entity_id):
    if kind not in _ENTITY_TABLES:
        _fail("SNAPSHOT_INVALID_ENTITY_KIND")
    _uuid(entity_id)
    row = indexes[_ENTITY_TABLES[kind][0]].get((entity_id,))
    if row is None:
        _fail("SNAPSHOT_MISSING_ENTITY")
    return row


def _project(indexes, kind, entity_id):
    row = _entity(indexes, kind, entity_id)
    if kind == "project":
        return entity_id
    if kind == "checklist_item":
        return _project(indexes, "checklist", row["instance_id"])
    if kind == "check":
        return _project(indexes, "checklist_item", row["item_id"])
    return row["project_id"]


def _same_project(indexes, kind, entity_id, project_id):
    if _project(indexes, kind, entity_id) != project_id:
        _fail("SNAPSHOT_CROSS_PROJECT_REFERENCE")


def _reference(indexes, row):
    project_id = row["project_id"]
    if row["owner_kind"] not in {"card", "checklist", "checklist_item", "check"}:
        _fail("SNAPSHOT_INVALID_REFERENCE")
    _same_project(indexes, row["owner_kind"], row["owner_id"], project_id)
    target_kind, target_id = row["target_kind"], row["target_id"]
    if target_kind not in {"atom", "conflict", "source", "attachment"}:
        _fail("SNAPSHOT_INVALID_REFERENCE")
    _text(target_id, nonempty=True)
    if target_kind in {"source", "attachment"}:
        _same_project(indexes, target_kind, target_id, project_id)
        if target_kind == "attachment" and _entity(indexes, target_kind, target_id)["state"] != "ready":
            _fail("SNAPSHOT_ATTACHMENT_NOT_READY")
    if row["fingerprint_version"] != 1:
        _fail("SNAPSHOT_INVALID_FINGERPRINT")
    if row["target_fingerprint"] is not None:
        _fingerprint(row["target_fingerprint"])
    snap = _decode(row["snapshot_json"])
    if "project_id" in snap and snap["project_id"] != project_id:
        _fail("SNAPSHOT_CROSS_PROJECT_REFERENCE")
    if target_kind in {"source", "attachment"}:
        if snap.get(_ENTITY_TABLES[target_kind][1]) != target_id:
            _fail("SNAPSHOT_INVALID_REFERENCE")


def _receipt_scope(indexes, receipt):
    parts = receipt["scope"].split(":")
    allowed = {
        "project": {(), ("cards", "create"), ("sources", "create"), ("checklists", "create")},
        "card": {(), ("evidence",)}, "source": {(), ("attachment",)},
        "checklist": {()}, "checklist-item": {(), ("check",)},
    }
    if len(parts) < 2 or parts[0] not in allowed or tuple(parts[2:]) not in allowed[parts[0]]:
        _fail("SNAPSHOT_INVALID_RECEIPT_SCOPE")
    kind = "checklist_item" if parts[0] == "checklist-item" else parts[0]
    entity = _entity(indexes, kind, parts[1])
    return kind, entity


def _receipt_entity(indexes, receipt):
    body = _decode(receipt["response_json"])
    parts = receipt["scope"].split(":")
    scope_kind, scope_entity = _receipt_scope(indexes, receipt)
    if tuple(parts[2:]) in {("cards", "create"), ("sources", "create"), ("checklists", "create")}:
        kind = {"cards": "card", "sources": "source", "checklists": "checklist"}[parts[2]]
    elif tuple(parts[2:]) == ("attachment",):
        kind = "attachment"
    elif tuple(parts[2:]) == ("check",):
        kind = "check"
    else:
        kind = scope_kind
    field = {"checklist_item": "item"}.get(kind, kind)
    response = body.get(field)
    pk = _ENTITY_TABLES[kind][1]
    if type(response) is not dict:
        _fail("SNAPSHOT_INVALID_RECEIPT")
    entity_id = response.get(pk)
    entity = _entity(indexes, kind, entity_id)
    scope_id = scope_entity[_ENTITY_TABLES[scope_kind][1]]
    if _project(indexes, kind, entity_id) != _project(indexes, scope_kind, scope_id):
        _fail("SNAPSHOT_CROSS_PROJECT_REFERENCE")
    if kind == scope_kind and entity_id != scope_id:
        _fail("SNAPSHOT_INVALID_RECEIPT")
    if kind == "attachment":
        if (entity["operation_id"] != receipt["operation_id"] or entity["source_id"] != scope_id
                or entity["state"] != "ready" or any(response.get(k) != entity[k] for k in
                  ("attachment_id", "source_id", "project_id", "sha256", "byte_size", "media_type", "original_name", "state", "created_at"))):
            _fail("SNAPSHOT_INVALID_RECEIPT")
    elif kind == "check":
        expected = {k: v for k, v in entity.items() if k not in {"measurements_json", "attachment_refs_json"}}
        expected.update(measurements=_decode(entity["measurements_json"], list),
                        attachment_refs=_decode(entity["attachment_refs_json"], list))
        if entity["operation_id"] != receipt["operation_id"] or entity["item_id"] != scope_id or response != expected:
            _fail("SNAPSHOT_INVALID_RECEIPT")
    else:
        _integer(response.get("revision"), 1)
        if response["revision"] > entity["revision"]:
            _fail("SNAPSHOT_INVALID_RECEIPT")
    return kind, response


def _content(document, indexes):
    tables = document["tables"]
    for kind, (table, pk) in _ENTITY_TABLES.items():
        for row in tables[table]:
            _uuid(row[pk])
            if kind != "project":
                _entity(indexes, "project", _project(indexes, kind, row[pk]))
    choices = {
        ("decision_card", "state"): {"considering", "adopted", "not_adopted", "archived"},
        ("source_record", "source_type"): {"manual_note", "standard_record", "product_document", "quote", "site_photo"},
        ("source_record", "verification_state"): {"unverified", "user_checked"},
        ("source_record", "validity"): {"unknown", "current", "superseded", "withdrawn"},
        ("source_attachment", "state"): {"staging", "ready", "failed"},
        ("checklist_instance", "lifecycle_state"): {"active", "archived"},
        ("checklist_item", "criteria_state"): {"known", "needs_verification"},
        ("checklist_item", "basis_kind"): {"standard_clause", "manufacturer_instruction", "personal_requirement", "video_suggestion", "observation_only"},
        ("checklist_check", "record_kind"): {"inspection", "remediation", "void"},
    }
    for (table, column), allowed in choices.items():
        if any(row[column] not in allowed for row in tables[table]):
            _fail("SNAPSHOT_INVALID_ENUM")
    for row in tables["source_attachment"]:
        _uuid(row["operation_id"])
        storage_key(row["storage_key"])
        _same_project(indexes, "source", row["source_id"], row["project_id"])
        suffix = PurePosixPath(row["storage_key"]).suffix
        types = {".pdf": "application/pdf", ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
        if types.get(suffix) != row["media_type"]:
            _fail("SNAPSHOT_INVALID_ATTACHMENT_TYPE")
        if row["sha256"] is not None:
            _sha(row["sha256"])
        if row["state"] == "ready":
            _sha(row["sha256"])
            _integer(row["byte_size"], 1)
            if row["error"] is not None:
                _fail("SNAPSHOT_INVALID_READY_ATTACHMENT")
    seen_refs = set()
    for row in tables["personal_evidence_ref"]:
        _uuid(row["ref_id"])
        _reference(indexes, row)
        key = tuple(row[k] for k in ("owner_kind", "owner_id", "target_kind", "target_id"))
        if key in seen_refs:
            _fail("SNAPSHOT_DUPLICATE_REFERENCE")
        seen_refs.add(key)
    for row in tables["mutation_receipt"]:
        _uuid(row["operation_id"])
        _sha(row["request_hash"])
        if not 200 <= row["response_status"] < 300:
            _fail("SNAPSHOT_INVALID_RECEIPT")
        body = _decode(row["response_json"])
        if body.get("operation_id") != row["operation_id"]:
            _fail("SNAPSHOT_INVALID_RECEIPT")
        _receipt_entity(indexes, row)
    for row in tables["checklist_check"]:
        _fingerprint(row["criteria_snapshot_hash"])
        _uuid(row["operation_id"])
        item = _entity(indexes, "checklist_item", row["item_id"])
        if row["item_revision"] > item["revision"]:
            _fail("SNAPSHOT_INVALID_CHECK_HISTORY")
        receipt = indexes["mutation_receipt"].get((row["operation_id"],))
        if receipt is None or receipt["scope"] != f"checklist-item:{row['item_id']}:check":
            _fail("SNAPSHOT_MISSING_RECEIPT")
        if row["record_kind"] == "inspection":
            if row["result"] not in {"pass", "fail", "unable_to_verify", "not_applicable"} or row["supersedes_check_id"]:
                _fail("SNAPSHOT_INVALID_CHECK_HISTORY")
        elif row["result"] is not None or not row["note"]:
            _fail("SNAPSHOT_INVALID_CHECK_HISTORY")
        if row["record_kind"] == "void":
            target = _entity(indexes, "check", row["supersedes_check_id"])
            if (target["item_id"] != row["item_id"] or target["item_revision"] != row["item_revision"]
                    or target["record_kind"] == "void" or target["commit_seq"] >= row["commit_seq"]):
                _fail("SNAPSHOT_INVALID_CHECK_HISTORY")
        elif row["supersedes_check_id"] is not None:
            _fail("SNAPSHOT_INVALID_CHECK_HISTORY")
        project_id = _project(indexes, "checklist_item", row["item_id"])
        attachments = _decode(row["attachment_refs_json"], list)
        for aid in attachments:
            _uuid(aid)
        if len(set(attachments)) != len(attachments):
            _fail("SNAPSHOT_DUPLICATE_REFERENCE")
        for aid in attachments:
            _same_project(indexes, "attachment", aid, project_id)
            if _entity(indexes, "attachment", aid)["state"] != "ready":
                _fail("SNAPSHOT_ATTACHMENT_NOT_READY")
    voided = [r["supersedes_check_id"] for r in tables["checklist_check"] if r["record_kind"] == "void"]
    if len(set(voided)) != len(voided):
        _fail("SNAPSHOT_INVALID_CHECK_HISTORY")
    for row in tables["source_attachment"]:
        if row["state"] == "ready":
            receipt = indexes["mutation_receipt"].get((row["operation_id"],))
            if receipt is None or receipt["scope"] != f"source:{row['source_id']}:attachment":
                _fail("SNAPSHOT_MISSING_RECEIPT")
    _history(document, indexes)
    for row in tables["checklist_check"]:
        history = indexes["personal_revision"].get(("checklist_item", row["item_id"], row["item_revision"]))
        if history is None:
            _fail("SNAPSHOT_INCOMPLETE_HISTORY")
        item = _decode(history["snapshot_json"])
        fields = {key: item[key] for key in ("title", "conditions", "method_text", "basis_kind", "criteria", "criteria_state")}
        fields["refs"] = sorted(({"kind": ref["target_kind"], "id": ref["target_id"],
                                  "fingerprint": ref["target_fingerprint"]} for ref in item["refs"]),
                                 key=lambda ref: (ref["kind"], ref["id"]))
        if row["criteria_snapshot_hash"] != identity.fingerprint(fields):
            _fail("SNAPSHOT_CHECK_CRITERIA_MISMATCH")


def _saved_ref_facts(refs, *, owner_kind, owner_id, project_id):
    """Persistent reference set only; no current resolution or derived status."""
    if type(refs) is not list:
        _fail("SNAPSHOT_INVALID_HISTORY")
    facts, seen = [], set()
    for ref in refs:
        if type(ref) is not dict or not set(_SAVED_REF_KEYS) <= set(ref):
            _fail("SNAPSHOT_INVALID_HISTORY")
        ref_id = _uuid(ref["ref_id"])
        if ref_id in seen:
            _fail("SNAPSHOT_DUPLICATE_REFERENCE")
        seen.add(ref_id)
        if (ref["project_id"] != project_id or ref["owner_kind"] != owner_kind
                or ref["owner_id"] != owner_id):
            _fail("SNAPSHOT_INVALID_HISTORY_REFERENCE_OWNER")
        if type(ref["fingerprint_version"]) is not int or ref["fingerprint_version"] != 1:
            _fail("SNAPSHOT_INVALID_FINGERPRINT")
        if ref["target_fingerprint"] is not None:
            _fingerprint(ref["target_fingerprint"])
        _text(ref["created_at"], nonempty=True)
        if type(ref["snapshot"]) is not dict:
            _fail("SNAPSHOT_INVALID_HISTORY")
        facts.append({key: ref[key] for key in _SAVED_REF_KEYS})
    return sorted(facts, key=lambda ref: ref["ref_id"])


def _history(document, indexes):
    revisions = {}
    current_refs = {}
    for raw in document["tables"]["personal_evidence_ref"]:
        current_refs.setdefault((raw["owner_kind"], raw["owner_id"]), []).append(
            {key: _decode(raw["snapshot_json"]) if key == "snapshot" else raw[key] for key in _SAVED_REF_KEYS})
    for row in document["tables"]["personal_revision"]:
        kind, entity_id, revision = row["entity_kind"], row["entity_id"], row["revision"]
        if kind not in _HISTORY_KINDS:
            _fail("SNAPSHOT_INVALID_HISTORY")
        entity = _entity(indexes, kind, entity_id)
        if revision > entity["revision"]:
            _fail("SNAPSHOT_INVALID_HISTORY")
        snap = _decode(row["snapshot_json"])
        pk = _ENTITY_TABLES[kind][1]
        if snap.get(pk) != entity_id or type(snap.get("revision")) is not int or snap["revision"] != revision:
            _fail("SNAPSHOT_INVALID_HISTORY")
        project_id = _project(indexes, kind, entity_id)
        if snap.get("project_id", project_id) != project_id:
            _fail("SNAPSHOT_CROSS_PROJECT_REFERENCE")
        descriptor = document["schema"]["tables"][_ENTITY_TABLES[kind][0]]
        for column in descriptor["columns"]:
            name = column["name"]
            view_name = "context_snapshot" if name == "context_json" else name[:-5] if name.endswith("_json") else name
            if view_name not in snap:
                _fail("SNAPSHOT_INVALID_HISTORY")
            value = snap[view_name]
            if name in _JSON_TYPES:
                if type(value) is not _JSON_TYPES[name]:
                    _fail("SNAPSHOT_INVALID_HISTORY")
            elif value is None:
                if column["not_null"] or column["pk"]:
                    _fail("SNAPSHOT_INVALID_HISTORY")
            elif column["type"] == "INTEGER":
                _integer(value, 1 if name == "revision" else 0)
            else:
                _text(value)
            if revision == entity["revision"]:
                actual = _decode(entity[name], _JSON_TYPES[name]) if name in _JSON_TYPES else entity[name]
                if value != actual:
                    _fail("SNAPSHOT_CURRENT_HISTORY_MISMATCH")
        ref_field = _SAVED_REF_FIELDS.get(kind)
        refs = snap.get(ref_field, []) if ref_field else []
        if type(refs) is not list or ref_field and ref_field not in snap:
            _fail("SNAPSHOT_INVALID_HISTORY")
        for ref in refs:
            if type(ref) is not dict or not {"target_kind", "target_id", "target_fingerprint"} <= set(ref):
                _fail("SNAPSHOT_INVALID_HISTORY")
            if ref.get("project_id", project_id) != project_id:
                _fail("SNAPSHOT_CROSS_PROJECT_REFERENCE")
            if ref["target_kind"] not in {"atom", "conflict", "source", "attachment"}:
                _fail("SNAPSHOT_INVALID_REFERENCE")
            _text(ref["target_id"], nonempty=True)
            if ref["target_fingerprint"] is not None:
                _fingerprint(ref["target_fingerprint"])
            if ref["target_kind"] in {"source", "attachment"}:
                _same_project(indexes, ref["target_kind"], ref["target_id"], project_id)
        if ref_field is not None:
            saved_facts = _saved_ref_facts(refs, owner_kind=kind, owner_id=entity_id, project_id=project_id)
            if revision == entity["revision"]:
                # create_card/update_card/update_card_evidence and checklist
                # create/update_item all record the bound raw refs at this
                # exact revision. Old revisions deliberately retain old refs.
                raw_facts = _saved_ref_facts(current_refs.get((kind, entity_id), []),
                                              owner_kind=kind, owner_id=entity_id, project_id=project_id)
                if _json(saved_facts) != _json(raw_facts):
                    _fail("SNAPSHOT_CURRENT_REFERENCE_HISTORY_MISMATCH")
        if row["operation_id"] is not None:
            _uuid(row["operation_id"])
            receipt = indexes["mutation_receipt"].get((row["operation_id"],))
            if receipt is None:
                _fail("SNAPSHOT_MISSING_RECEIPT")
            allowed_scopes = {
                "project": {f"project:{entity_id}"},
                "card": {f"project:{project_id}:cards:create"} if revision == 1 else
                        {f"card:{entity_id}", f"card:{entity_id}:evidence"},
                "source": {f"project:{project_id}:sources:create"} if revision == 1 else {f"source:{entity_id}"},
                "checklist": {f"project:{project_id}:checklists:create"} if revision == 1 else {f"checklist:{entity_id}"},
                "checklist_item": {f"project:{project_id}:checklists:create"} if revision == 1 else {f"checklist-item:{entity_id}"},
            }
            if receipt["scope"] not in allowed_scopes[kind]:
                _fail("SNAPSHOT_INVALID_HISTORY_RECEIPT")
            response_kind, response = _receipt_entity(indexes, receipt)
            if kind == "checklist_item" and revision == 1:
                items = response.get("items")
                matching = [item for item in items if type(item) is dict and item.get("item_id") == entity_id] if type(items) is list else []
                if len(matching) != 1:
                    _fail("SNAPSHOT_INVALID_HISTORY_RECEIPT")
                response = matching[0]
            elif response_kind != kind or response.get(pk) != entity_id or response.get("revision") != revision:
                _fail("SNAPSHOT_INVALID_HISTORY_RECEIPT")
            # Real producers save these same persistent values in history and
            # in the operation result. Detail views may additionally include
            # current derived status; those fields are not historical authority.
            historical_fields, response_fields = {}, {}
            for column in descriptor["columns"]:
                name = column["name"]
                view_name = "context_snapshot" if name == "context_json" else name[:-5] if name.endswith("_json") else name
                if view_name not in response:
                    _fail("SNAPSHOT_INVALID_HISTORY_RECEIPT")
                historical_fields[view_name] = snap[view_name]
                response_fields[view_name] = response[view_name]
            if _json(historical_fields) != _json(response_fields):
                _fail("SNAPSHOT_HISTORY_RECEIPT_CONTENT_MISMATCH")
            if ref_field is not None:
                response_refs = response.get(ref_field)
                if type(response_refs) is not list:
                    _fail("SNAPSHOT_INVALID_HISTORY_RECEIPT")
                persistent_keys = ("ref_id", "project_id", "owner_kind", "owner_id", "target_kind", "target_id",
                                   "fingerprint_version", "target_fingerprint", "created_at", "snapshot")
                def persistent_refs(values):
                    if any(type(ref) is not dict or not set(persistent_keys) <= set(ref) for ref in values):
                        _fail("SNAPSHOT_INVALID_HISTORY_RECEIPT")
                    return [{key: ref[key] for key in persistent_keys} for ref in values]
                if _json(persistent_refs(refs)) != _json(persistent_refs(response_refs)):
                    _fail("SNAPSHOT_HISTORY_RECEIPT_CONTENT_MISMATCH")
            scope_kind, scope_entity = _receipt_scope(indexes, receipt)
            scope_id = scope_entity[_ENTITY_TABLES[scope_kind][1]]
            if _project(indexes, scope_kind, scope_id) != project_id:
                _fail("SNAPSHOT_CROSS_PROJECT_REFERENCE")
        elif kind != "project" or revision != 1:
            _fail("SNAPSHOT_MISSING_RECEIPT")
        revisions.setdefault((kind, entity_id), []).append(revision)
    for kind in _HISTORY_KINDS:
        table, pk = _ENTITY_TABLES[kind]
        for row in document["tables"][table]:
            actual = sorted(revisions.get((kind, row[pk]), ()))
            # Do not allocate range(current_revision) from untrusted input.
            if len(actual) != row["revision"] or any(value != i for i, value in enumerate(actual, 1)):
                _fail("SNAPSHOT_INCOMPLETE_HISTORY")


def _manifest(document, indexes):
    manifest = document["attachment_manifest"]
    if type(manifest) is not list or len(manifest) > MAX_ROWS:
        _fail("SNAPSHOT_INVALID_ATTACHMENT_MANIFEST")
    ready = {r["attachment_id"]: r for r in document["tables"]["source_attachment"] if r["state"] == "ready"}
    previous, seen = None, set()
    for item in manifest:
        if type(item) is not dict or set(item) != {"attachment_id", "storage_key", "sha256", "byte_size", "file_identity"}:
            _fail("SNAPSHOT_INVALID_ATTACHMENT_MANIFEST")
        aid = _uuid(item["attachment_id"])
        if previous is not None and aid <= previous:
            _fail("SNAPSHOT_DUPLICATE_OR_UNSORTED_KEY")
        previous = aid
        row = ready.get(aid)
        if row is None:
            _fail("SNAPSHOT_ATTACHMENT_SET_MISMATCH")
        storage_key(item["storage_key"])
        _sha(item["sha256"])
        _integer(item["byte_size"], 1)
        file_identity(item["file_identity"])
        if any(item[key] != row[key] for key in ("storage_key", "sha256", "byte_size")):
            _fail("SNAPSHOT_ATTACHMENT_CONTENT_MISMATCH")
        if item["file_identity"]["byte_size"] != item["byte_size"]:
            _fail("SNAPSHOT_ATTACHMENT_CONTENT_MISMATCH")
        seen.add(aid)
    if seen != set(ready):
        _fail("SNAPSHOT_ATTACHMENT_SET_MISMATCH")


def _preserved(current, old):
    if current["source"] != old["source"]:
        _fail("SNAPSHOT_SOURCE_MISMATCH")
    if current["snapshot_id"] == old["snapshot_id"] and current != old:
        _fail("SNAPSHOT_ID_REUSED")
    for table in ("personal_revision", "mutation_receipt", "checklist_check"):
        keys = current["schema"]["tables"][table]["primary_key"]
        now = {tuple(row[key] for key in keys): row for row in current["tables"][table]}
        for row in old["tables"][table]:
            if now.get(tuple(row[key] for key in keys)) != row:
                _fail("SNAPSHOT_IMMUTABLE_HISTORY_CHANGED")
    for table in ("renovation_project", "decision_card", "source_record", "checklist_instance", "checklist_item"):
        pk = current["schema"]["tables"][table]["primary_key"][0]
        now = {row[pk]: row for row in current["tables"][table]}
        for row in old["tables"][table]:
            newer = now.get(row[pk])
            if newer is None or newer["revision"] < row["revision"] or newer["revision"] == row["revision"] and newer != row:
                _fail("SNAPSHOT_ENTITY_REVISION_ROLLBACK")
    now = {r["attachment_id"]: r for r in current["tables"]["source_attachment"]}
    for row in old["tables"]["source_attachment"]:
        if row["state"] == "ready" and now.get(row["attachment_id"]) != row:
            _fail("SNAPSHOT_IMMUTABLE_ATTACHMENT_CHANGED")


@dataclass(frozen=True)
class SnapshotFacts:
    """Validated original bytes. These facts carry no operational capability."""
    snapshot_bytes: bytes
    snapshot_sha256: str
    snapshot_id: str
    _counts_json: str

    @property
    def snapshot_json(self):
        return self.snapshot_bytes.decode("utf-8")

    @property
    def byte_size(self):
        return len(self.snapshot_bytes)

    @property
    def counts(self):
        return MappingProxyType(_decode(self._counts_json))

    @property
    def document(self):
        return _decode(self.snapshot_bytes)


def validate_snapshot(snapshot_json, *, expected_sha256=None, expected_counts=None, prior_snapshot_json=None):
    """Validate a received fact document while retaining its exact UTF-8 bytes.

    An optional prior document proves append-only histories/receipts and rejects
    a historical key reused with different content. It does not make either
    document an accepted mirror, a current primary, or a durable receipt.
    """
    try:
        raw = snapshot_json.encode("utf-8") if type(snapshot_json) is str else snapshot_json
        if type(raw) is not bytes or not 0 < len(raw) <= MAX_SNAPSHOT_BYTES:
            _fail("SNAPSHOT_SIZE_INVALID")
        raw.decode("utf-8")
    except UnicodeError:
        _fail("SNAPSHOT_INVALID_JSON")
    digest = hashlib.sha256(raw).hexdigest()
    if expected_sha256 is not None:
        _sha(expected_sha256)
        if digest != expected_sha256:
            _fail("SNAPSHOT_SHA256_MISMATCH")
    document = _decode(raw)
    required = {"format_version", "snapshot_id", "source", "meta", "schema", "tables", "attachment_manifest", "counts"}
    if set(document) != required or type(document["format_version"]) is not int or document["format_version"] != FORMAT_VERSION:
        _fail("SNAPSHOT_FORMAT_MISMATCH")
    _uuid(document["snapshot_id"])
    if type(document["schema"]) is not dict or _json(document["schema"]) != _schema_bytes().decode("utf-8"):
        _fail("SNAPSHOT_SCHEMA_DRIFT")
    meta_rows = document["meta"]
    if type(meta_rows) is not list or len(meta_rows) != len(META_KEYS):
        _fail("SNAPSHOT_META_MISMATCH")
    meta = {}
    for key, row in zip(META_KEYS, meta_rows):
        if type(row) is not dict or set(row) != {"key", "value"} or row["key"] != key:
            _fail("SNAPSHOT_META_MISMATCH")
        _text(row["value"], nonempty=True)
        meta[key] = row["value"]
    for key in ("default_project_id", "library_id", "personal_primary_id"):
        _uuid(meta[key])
    if not _DECIMAL.fullmatch(meta["primary_epoch"]) or int(meta["primary_epoch"]) < 1:
        _fail("SNAPSHOT_META_MISMATCH")
    _integer(int(meta["primary_epoch"]), 1)
    source = {"library_id": meta["library_id"], "personal_primary_id": meta["personal_primary_id"], "primary_epoch": int(meta["primary_epoch"])}
    if (type(document["source"]) is not dict or document["source"] != source
            or type(document["source"].get("primary_epoch")) is not int):
        _fail("SNAPSHOT_SOURCE_MISMATCH")
    indexes = _rows(document)
    _entity(indexes, "project", meta["default_project_id"])
    _content(document, indexes)
    _manifest(document, indexes)
    counts = {table: len(document["tables"][table]) for table in TABLE_NAMES}
    counts.update(meta=len(META_KEYS), attachment_manifest=len(document["attachment_manifest"]))
    declared = document["counts"]
    if (type(declared) is not dict or set(declared) != set(counts)
            or any(type(value) is not int or value < 0 for value in declared.values()) or declared != counts):
        _fail("SNAPSHOT_COUNT_MISMATCH")
    if expected_counts is not None and (type(expected_counts) is not dict or expected_counts != counts
                                      or any(type(value) is not int for value in expected_counts.values())):
        _fail("SNAPSHOT_COUNT_MISMATCH")
    if prior_snapshot_json is not None:
        prior = validate_snapshot(prior_snapshot_json)
        if prior.snapshot_id == document["snapshot_id"] and prior.snapshot_bytes != raw:
            _fail("SNAPSHOT_ID_REUSED")
        _preserved(document, prior.document)
    return SnapshotFacts(raw, digest, document["snapshot_id"], _json(counts))
