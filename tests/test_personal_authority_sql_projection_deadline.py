"""r832: fixed actual SQLite projection and finite new-alias fault boundaries.

Root alone imports/collects/runs. QA SQL preparation never calls old schema,
snapshot or wire validators; product observers/reference owners stay actual.
"""
from collections import Counter, defaultdict, deque
from contextlib import contextmanager
import copy
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest

from reno import personal_authority_committed_image_deadline as images
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_sql_observation_deadline as observation
from reno import personal_authority_sql_projection_deadline as subject
from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_schema as receiver
from reno import personal_preservation_contract as contract
from reno import storage_lifecycle as lifecycle


pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows QA EX and private WAL image")
ALIASES = (
    "_monotonic", "_observation", "_observation_enter", "_observation_exit",
    "_observation_token", "_observation_evidence", "_query", "_personal_reference",
    "_receiver_reference", "_reference_enter", "_reference_exit", "_reference_query",
    "_rows_enter", "_rows_exit", "_fetchone", "_fetchmany", "_schema_tokens",
    "_schema_json", "_schema_decode", "_freeze_reference", "_snapshot_uuid",
    "_contract_text", "_typed_cell", "_canonical_bytes", "_decode", "_wire_digest",
    "_sha256", "_hash_update", "_hash_hexdigest", "_source_fail", "_snapshot_fail",
    "_receiver_fail", "_snapshot_validate", "_normalize_prepare", "_prepare_sha256",
    "_attachment_manifest", "_normalize_receipt", "_typed_table_digest",
    "_snapshot_document", "_snapshot_counts",
)
NATIVE_ALIASES = (
    "_monotonic", "_require_guard", "_guard_verify", "_profile_identity",
    "_image_token", "_image_connection", "_image_fingerprint", "_image_active",
    "_connect_memory", "_cursor", "_execute", "_fetchone", "_fetchmany",
    "_cursor_close", "_con_close", "_cursor_connection", "_setlimit",
    "_set_progress_handler", "_set_authorizer", "_row_factory", "_text_factory",
    "_typed_cell", "_canonical_bytes", "_projection_authorizer",
)
SESSION_FIELDS = {"phase", "retired", "failed", "normal_exit", "source_borrowed",
                  "profile_identity", "source_fingerprint", "source",
                  "personal_reference", "receiver_reference"}
ROLE_FIELDS = {"connection_state", "entered", "normal_exit", "native_call_starts",
               "native_call_completions", "cleanup_call_starts", "cursor_create_starts",
               "cursor_states", "data_rows", "data_typed_bytes", "metadata_rows",
               "metadata_typed_bytes", "fee_complete", "vm_reported_quanta",
               "previous_limits", "progress_restore_state", "authorizer_restore_state"}
ROWS_FIELDS = {"phase", "retired", "failed", "normal_exit", "cursor_state",
               "rows_observed", "eof_observed"}
RESULT_FIELDS = {"schema_version", "source_fingerprint", "meta", "personal_tables",
                 "receiver_facility", "receiver_tables", "integrity", "foreign_keys"}
SUMMARY_FIELDS = {"columns", "count", "sha256", "schema_sha256"}
PROJECT_TABLE = "renovation_project"
STAMP = "2026-10-12T00:00:00Z"
POSITIVE_IDS = ("populated-no-receiver", "populated-empty-receiver",
                "three-meta-no-receiver", "three-meta-prepared-receiver")
RAW_QUERIES = {
    'SELECT ' + ','.join('"' + column + '"' for column in columns) + ' FROM main."' + table + '"': table
    for table, columns in {**contract.PERSONAL_COLUMNS, **contract.RECEIVER_COLUMNS}.items()
}


def uid(number):
    return f"00000000-0000-4000-8000-{number:012d}"


PROJECT, PROJECT2, SOURCE, ATTACHMENT = uid(10), uid(11), uid(12), uid(13)
SOURCE_CREATE, ATTACH_PUT = uid(20), uid(21)
LIBRARY, PRIMARY = uid(900), uid(901)
SOURCE_LIBRARY, SOURCE_PRIMARY, SNAPSHOT, TRANSFER = uid(600), uid(601), uid(602), uid(603)
BODY = b"%PDF-r832-original-known-body\x00\xe4\xb8\xad"
BODY_SHA = hashlib.sha256(BODY).hexdigest()


def native(path):
    value = os.path.abspath(os.fspath(path))
    if value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def sql_rows(con, statement, parameters=()):
    cursor = con.execute(statement, parameters)
    try:
        return [tuple(row) for row in cursor.fetchall()]
    finally:
        cursor.close()


def insert(con, table, mapping):
    names = tuple(mapping)
    statement = 'INSERT INTO "' + table + '" (' + ','.join('"' + n + '"' for n in names) + ') VALUES ('
    statement += ','.join('?' for _ in names) + ')'
    sql_rows(con, statement, tuple(mapping[n] for n in names))


def independent_tokens(sql):
    pieces = re.findall(r"'(?:[^']|'')*'|\"(?:[^\"]|\"\")*\"|[A-Za-z_][A-Za-z_0-9]*|[0-9]+|[^\s]", sql, re.S)
    return [p if p.startswith(("'", '"')) else p.upper() for p in pieces]


def independent_layout(con, table):
    """Limited direct reads of the test's own QA writer, outside product ledger."""
    raw = sql_rows(con, "SELECT type,tbl_name,rootpage,sql FROM sqlite_schema WHERE name=?", (table,))
    assert len(raw) == 1 and raw[0][:2] == ("table", table) and raw[0][2] > 0
    info = sql_rows(con, f'PRAGMA table_xinfo("{table}")')
    columns = [dict(zip(("name", "type", "not_null", "default", "pk", "hidden"), row[1:7])) for row in info]
    primary = [row[1] for row in sorted(info, key=lambda r: r[5]) if row[5]]
    unique = []
    for row in sql_rows(con, f'PRAGMA index_list("{table}")'):
        if row[2]:
            assert not row[4]
            quoted = row[1].replace("'", "''")
            unique.append([r[2] for r in sql_rows(con, "PRAGMA index_info('" + quoted + "')")])
    foreign = [list(row[2:8]) for row in sql_rows(con, f'PRAGMA foreign_key_list("{table}")')]
    return {"columns": columns, "primary_key": primary, "order_by": list(primary),
            "unique_keys": sorted(unique), "foreign_keys": sorted(foreign),
            "sql_tokens": independent_tokens(raw[0][3])}


def raw_records(con, columns):
    result = {}
    for table, names in columns.items():
        statement = 'SELECT ' + ','.join('"' + n + '"' for n in names) + ' FROM main."' + table + '"'
        result[table] = [dict(zip(names, row)) for row in sql_rows(con, statement)]
    return result


def persistent_view(record):
    view = {}
    for name, value in record.items():
        if name.endswith("_json"):
            view["context_snapshot" if name == "context_json" else name[:-5]] = json.loads(value)
        else:
            view[name] = value
    return view


def known_snapshot(con, schema):
    """Build complete known facts from new QA SQL; no old validator/cache call."""
    insert(con, PROJECT_TABLE, {"project_id": PROJECT, "name": "内嵌绍兴项目",
        "profile_json": '{ "city" : "绍兴" }', "revision": 1,
        "created_at": STAMP, "updated_at": STAMP, "archived_at": None})
    insert(con, "source_record", {"source_id": SOURCE, "project_id": PROJECT,
        "source_type": "manual_note", "title": "独立已知来源", "excerpt": "原说明",
        "scope_json": '{ "scope" : ["绍兴"] }', "created_at": STAMP, "updated_at": STAMP})
    descriptor = {"attachment_id": ATTACHMENT, "storage_key": "files/" + ATTACHMENT + ".pdf",
        "sha256": BODY_SHA, "byte_size": len(BODY),
        "file_identity": {"device": "1", "inode": "2", "byte_size": len(BODY),
                          "mtime_ns": "3", "ctime_ns": "4"}}
    insert(con, "source_attachment", {"attachment_id": ATTACHMENT, "source_id": SOURCE,
        "project_id": PROJECT, "sha256": BODY_SHA, "byte_size": len(BODY),
        "media_type": "application/pdf", "original_name": "原始文件.pdf",
        "storage_key": descriptor["storage_key"], "state": "ready", "created_at": STAMP,
        "operation_id": ATTACH_PUT, "error": None})
    rows = raw_records(con, contract.PERSONAL_COLUMNS)
    project_view = persistent_view(rows[PROJECT_TABLE][0])
    source_view = persistent_view(rows["source_record"][0])
    attachment = rows["source_attachment"][0]
    insert(con, "personal_revision", {"entity_kind": "project", "entity_id": PROJECT,
        "revision": 1, "snapshot_json": canonical(project_view).decode(),
        "operation_id": None, "created_at": STAMP})
    insert(con, "personal_revision", {"entity_kind": "source", "entity_id": SOURCE,
        "revision": 1, "snapshot_json": canonical(source_view).decode(),
        "operation_id": SOURCE_CREATE, "created_at": STAMP})
    source_response = {"operation_id": SOURCE_CREATE, "source": source_view}
    attachment_response = {"operation_id": ATTACH_PUT,
        "attachment": {k: attachment[k] for k in ("attachment_id", "source_id", "project_id",
            "sha256", "byte_size", "media_type", "original_name", "state", "created_at")}}
    for operation, scope, response in (
            (SOURCE_CREATE, f"project:{PROJECT}:sources:create", source_response),
            (ATTACH_PUT, f"source:{SOURCE}:attachment", attachment_response)):
        insert(con, "mutation_receipt", {"operation_id": operation, "scope": scope,
            "request_hash": "a" * 64, "response_status": 201,
            "response_json": canonical(response).decode(), "created_at": STAMP})
    tables = raw_records(con, contract.PERSONAL_COLUMNS)
    for table, records in tables.items():
        keys = schema["tables"][table]["primary_key"]
        records.sort(key=lambda r: tuple(r[k] for k in keys))
    meta = {"default_project_id": PROJECT, "library_id": SOURCE_LIBRARY,
            "personal_primary_id": SOURCE_PRIMARY, "primary_epoch": "1"}
    counts = {table: len(records) for table, records in tables.items()}
    counts.update(meta=4, attachment_manifest=1)
    document = {"format_version": 1, "snapshot_id": SNAPSHOT,
        "source": {"library_id": SOURCE_LIBRARY, "personal_primary_id": SOURCE_PRIMARY, "primary_epoch": 1},
        "meta": [{"key": key, "value": meta[key]} for key in facts.META_KEYS],
        "schema": copy.deepcopy(schema), "tables": tables,
        "attachment_manifest": [descriptor], "counts": counts}
    raw = canonical(document)
    prepare = {"format_version": 1, "kind": "personal_mirror_prepare", "operation_id": TRANSFER,
        "snapshot_id": SNAPSHOT, "snapshot_sha256": hashlib.sha256(raw).hexdigest(),
        "snapshot_json": raw.decode(),
        "source": {"library_id": SOURCE_LIBRARY, "node_id": SOURCE_PRIMARY,
                   "personal_primary_id": SOURCE_PRIMARY, "primary_epoch": 1},
        "receiver": {"library_id": LIBRARY, "node_id": PRIMARY},
        "previous": None, "expected_revision": 0}
    for table in reversed(tuple(contract.PERSONAL_COLUMNS)):
        sql_rows(con, 'DELETE FROM "' + table + '"')
    assert all(not values for values in raw_records(con, contract.PERSONAL_COLUMNS).values())
    return SimpleNamespace(document=document, prepare=prepare, descriptor=descriptor,
        prepare_json=canonical(prepare).decode(), prepare_sha=hashlib.sha256(canonical(prepare)).hexdigest())


def add_receiver_rows(con, known, mutation=None):
    prepare, descriptor = known.prepare, known.descriptor
    text, sha = known.prepare_json, known.prepare_sha
    if mutation == "prepare-sha":
        sha = "b" * 64
        assert sha != known.prepare_sha
    elif mutation == "duplicate-key":
        text = '{"operation_id":' + json.dumps(TRANSFER) + ',' + text[1:]
        assert sql_rows(con, "SELECT json_valid(?)", (text,)) == [(1,)]
    insert(con, receiver.INTENT, {"operation_id": TRANSFER, "format_version": 1,
        "prepare_json": text, "prepare_sha256": sha, "snapshot_id": SNAPSHOT,
        "snapshot_sha256": prepare["snapshot_sha256"], "source_library_id": SOURCE_LIBRARY,
        "source_node_id": SOURCE_PRIMARY, "source_primary_id": SOURCE_PRIMARY,
        "source_primary_epoch": 1, "receiver_library_id": LIBRARY, "receiver_node_id": PRIMARY,
        "previous_snapshot_id": None, "previous_snapshot_sha256": None, "expected_revision": 0,
        "state": "prepared", "created_at": STAMP, "updated_at": STAMP})
    insert(con, receiver.ATTACHMENT, {"operation_id": TRANSFER, "attachment_id": ATTACHMENT,
        "storage_key": descriptor["storage_key"], "sha256": BODY_SHA, "byte_size": len(BODY),
        "descriptor_json": canonical(descriptor).decode(),
        "descriptor_sha256": hashlib.sha256(canonical(descriptor)).hexdigest(), "state": "stored",
        "local_file_device": "1", "local_file_inode": "2", "local_file_mtime_ns": "3",
        "local_file_ctime_ns": "4", "stored_at": STAMP})


def add_personal_rows(con):
    for project, name, revision in ((PROJECT2, "第二个'O\\项目", 1.25), (PROJECT, "r832-cpu-first-绍兴", 1)):
        insert(con, PROJECT_TABLE, {"project_id": project, "name": name,
            "profile_json": '{ "city" : "绍兴", "quote" : "\\\"" }', "revision": revision,
            "created_at": STAMP, "updated_at": STAMP, "archived_at": None})
    insert(con, "source_record", {"source_id": SOURCE, "project_id": PROJECT,
        "source_type": "manual_note", "title": "原始BLOB说明", "excerpt": b"original\x00\xff",
        "scope_json": '{ "scope" : [ "绍兴" ] }', "created_at": STAMP, "updated_at": STAMP})
    insert(con, "source_attachment", {"attachment_id": ATTACHMENT, "source_id": SOURCE,
        "project_id": PROJECT, "sha256": None, "byte_size": None, "media_type": "application/pdf",
        "original_name": "未完成.pdf", "storage_key": "files/" + ATTACHMENT + ".pdf",
        "state": "failed", "created_at": STAMP, "operation_id": ATTACH_PUT, "error": "未定"})


@pytest.fixture
def image_case(tmp_path):
    @contextmanager
    def opened(kind="populated-no-receiver", mutation=None):
        assert kind in POSITIVE_IDS
        qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
        Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
        tmp_path.resolve(strict=True).relative_to(qa)
        root = tmp_path / (kind + ("-" + mutation if mutation else ""))
        profile = lifecycle.StorageProfile(root / "data/绍兴-observation.sqlite3", root / "media",
                                           root / "reports", root / "checkpoints")
        profile.database.parent.mkdir(parents=True)
        initial = sqlite3.connect(native(profile.database))
        try:
            sql_rows(initial, "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            sql_rows(initial, "INSERT INTO meta VALUES('migration_version','17')")
            initial.commit()
        finally:
            initial.close()
        lifecycle.initialize(profile)
        with lifecycle.exclusive(profile) as guard:
            assert type(guard) is lifecycle.StorageGuard and guard.exclusive is True
            rw = sqlite3.connect(native(profile.database))
            first = None
            try:
                assert sql_rows(rw, "PRAGMA journal_mode=WAL")[0][0].lower() == "wal"
                sql_rows(rw, "PRAGMA wal_autocheckpoint=0")
                sql_rows(rw, "PRAGMA foreign_keys=ON")
                for table, ddl in facts._DDL.items():
                    if mutation == "personal-check" and table == "decision_card":
                        old = "CHECK(state IN ('considering','adopted','not_adopted','archived'))"
                        assert old in ddl
                        ddl = ddl.replace(old, "CHECK(state IN ('considering','adopted','not_adopted','archived','r832extra'))", 1)
                    sql_rows(rw, 'CREATE TABLE ' + table + ' (' + ddl + ')')
                personal_schema = {"personal_schema_version": 1,
                    "tables": {t: independent_layout(rw, t) for t in facts.TABLE_NAMES}}
                if mutation == "personal-check":
                    # Reference expected stays the fixed original CHECK. The
                    # QA source layouts below deliberately retain its drift.
                    personal_schema["tables"]["decision_card"]["sql_tokens"] = independent_tokens(
                        "CREATE TABLE decision_card (" + facts._DDL["decision_card"] + ")")
                installed = kind in {"populated-empty-receiver", "three-meta-prepared-receiver"}
                if installed:
                    for table, ddl in receiver.TABLES.items():
                        sql_rows(rw, "CREATE TABLE " + table + " (" + ddl + ")")
                    for statement in receiver.INDEXES + receiver.TRIGGERS:
                        if mutation == "receiver-trigger" and statement.startswith("CREATE TRIGGER trg_personal_mirror_receipt_update "):
                            continue
                        sql_rows(rw, statement)
                known = known_snapshot(rw, personal_schema) if kind == "three-meta-prepared-receiver" else None
                if known is not None:
                    add_receiver_rows(rw, known, mutation=mutation)
                populated = kind.startswith("populated") or mutation == "three-populated"
                if populated:
                    add_personal_rows(rw)
                metadata = {"library_id": LIBRARY, "personal_primary_id": PRIMARY if kind.startswith("populated") else "",
                            "primary_epoch": "1"}
                if kind.startswith("populated"):
                    metadata["default_project_id"] = PROJECT
                for key, value in metadata.items():
                    sql_rows(rw, "INSERT INTO meta VALUES(?,?)", (key, value))
                expected_rows = raw_records(rw, contract.PERSONAL_COLUMNS)
                if installed:
                    expected_rows.update(raw_records(rw, contract.RECEIVER_COLUMNS))
                layouts = {t: independent_layout(rw, t) for t in (*expected_rows, "meta")}
                meta_rows = sql_rows(rw, "SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key", facts.META_KEYS)
                rw.commit()
                assert profile.database.with_name(profile.database.name + "-wal").stat().st_size > 0
                limited = deadline._RestrictedBudget(deadline.Namespace3Deadline(1.0e15))
                token = limited.effective_deadline
                with directories.DeadlineDirectory(profile.database.parent, base=profile.database.parent,
                                                   deadline_token=token) as directory:
                    image_owner = images.committed_image_locked(profile, directory, deadline_token=token, attempts=1)
                    with image_owner as image:
                        assert type(image) is images.DeadlineCommittedImage
                        assert image.deadline_token is token and image.require_active() is None
                        yield SimpleNamespace(profile=profile, guard=guard, token=token, directory=directory,
                            image=image, image_owner=image_owner, kind=kind, installed=installed,
                            personal_schema=personal_schema, layouts=layouts, metadata=metadata,
                            meta_rows=tuple(meta_rows), rows=expected_rows, known=known)
                    rw.close()
                    rw = None
                limited.clock_only()
            except BaseException as failure:
                first = failure
                raise
            finally:
                if rw is not None:
                    try:
                        rw.close()
                    except BaseException:
                        if first is None:
                            raise
                        BaseException.add_note(first, "R832_FIXTURE_OWN_WRITER_CLOSE_FAILED")
    return opened


def independent_row_bytes(row):
    result = []
    for value in row:
        if value is None:
            result.append(["null"])
        elif type(value) is int:
            result.append(["int", value])
        elif type(value) is float:
            assert math.isfinite(value)
            result.append(["float", value.hex()])
        elif type(value) is bytes:
            result.append(["blob", value.hex()])
        else:
            assert type(value) is str
            result.append(["text", value])
    return canonical(result)


def independent_summary(columns, rows, layout):
    encoded = sorted(independent_row_bytes(row) for row in rows)
    digest = hashlib.sha256(canonical(list(columns)))
    for raw in encoded:
        digest.update(len(raw).to_bytes(8, "big"))
        digest.update(raw)
    return {"columns": list(columns), "count": len(encoded), "sha256": digest.hexdigest(),
            "schema_sha256": hashlib.sha256(canonical(layout)).hexdigest()}


class NativeLedger:
    """Actual new-observer alias calls/returns, never raw provider sampling."""
    def __init__(self, patch, item):
        self.original = {name: getattr(observation, name) for name in NATIVE_ALIASES}
        self.calls, self.returns = Counter(), Counter()
        self.source_con = item.image.connection
        self.roles = {id(self.source_con): "source"}
        self.next_roles = deque()
        self.connections, self.cursors, self.executions, self.fetches = [], [], [], []
        self.cursor_con, self.cursor_group = {}, {}
        self.cursor_close_attempts, self.cursor_close_returns = [], []
        self.con_close_attempts, self.con_close_returns = [], []
        self.progress, self.authorizers, self.limits = [], [], []
        self.pending, self.encoded = deque(), []

        def make(name):
            def observed(*args, **kwargs):
                self.calls[name] += 1
                if name == "_cursor_close":
                    self.cursor_close_attempts.append(args[0])
                elif name == "_con_close":
                    assert args[0] is not self.source_con
                    self.con_close_attempts.append(args[0])
                result = self.original[name](*args, **kwargs)
                self.returns[name] += 1
                if name == "_image_token":
                    assert args[0] is item.image and result is item.token
                elif name == "_image_connection":
                    assert result is self.source_con
                elif name in {"_require_guard", "_guard_verify"}:
                    assert result is item.guard
                elif name == "_connect_memory":
                    assert not args and not kwargs and type(result) is sqlite3.Connection and self.next_roles
                    self.roles[id(result)] = self.next_roles.popleft()
                    self.connections.append(result)
                elif name == "_cursor":
                    assert type(result) is sqlite3.Cursor
                    self.cursors.append(result)
                    self.cursor_con[id(result)] = args[0]
                elif name == "_cursor_connection":
                    assert result is self.cursor_con[id(args[0])]
                elif name == "_execute":
                    assert result is args[0]
                    statement = args[1]
                    con = self.cursor_con[id(args[0])]
                    role = self.roles[id(con)]
                    group = "data" if role == "source" and statement in RAW_QUERIES else "metadata"
                    self.cursor_group[id(args[0])] = (role, group)
                    self.executions.append((args[0], con, statement, args[2] if len(args) > 2 else kwargs.get("parameters", ())))
                    if role == "source":
                        assert statement.startswith(("SELECT ", "PRAGMA "))
                elif name in {"_fetchone", "_fetchmany"}:
                    values = ([] if result is None else [result]) if name == "_fetchone" else result
                    assert type(values) is list and all(type(row) is tuple for row in values)
                    role, group = self.cursor_group[id(args[0])]
                    self.fetches.append((name, args[0], tuple(values)))
                    self.pending.extend((role, group, row) for row in values)
                elif name == "_canonical_bytes" and self.pending:
                    role, group, row = self.pending.popleft()
                    assert type(result) is bytes and result == independent_row_bytes(row)
                    self.encoded.append((role, group, row, result))
                elif name == "_set_progress_handler":
                    assert result is None
                    self.progress.append(tuple(args))
                elif name == "_set_authorizer":
                    assert result is None
                    self.authorizers.append(tuple(args))
                elif name == "_setlimit":
                    self.limits.append((*args, result))
                elif name == "_cursor_close":
                    assert result is None
                    self.cursor_close_returns.append(args[0])
                elif name == "_con_close":
                    assert result is None
                    self.con_close_returns.append(args[0])
                return result
            return observed
        for name in NATIVE_ALIASES:
            if name != "_monotonic":
                patch.setattr(observation, name, make(name))


class Ledger:
    def __init__(self, patch, item):
        self.original = {name: getattr(subject, name) for name in ALIASES}
        self.native = NativeLedger(patch, item)
        self.calls, self.returns = Counter(), Counter()
        self.observers, self.refs, self.queries, self.closed_rows = [], [], [], []
        self.ref_roles, self.query_records = {}, {}
        self.values, self.eof = defaultdict(list), set()
        self.states, self.schemas, self.schema_inputs, self.schema_jsons = [], [], [], []
        self.schema_decode_inputs = []
        self.snapshot_results, self.documents, self.counts = [], [], []
        self.adapters, self.fee_windows = [], []
        self.hash_inputs = {}

        def make(name):
            def observed(*args, **kwargs):
                self.calls[name] += 1
                if name in {"_personal_reference", "_receiver_reference"}:
                    self.native.next_roles.append("personal_reference" if name == "_personal_reference" else "receiver_reference")
                fee_before = copy.deepcopy(self.observers[-1].evidence) if name == "_typed_table_digest" and self.observers else None
                result = self.original[name](*args, **kwargs)
                self.returns[name] += 1
                if name == "_observation":
                    assert type(result) is observation.SqlObservationDeadlineLease
                    assert args == (item.profile, item.image) and kwargs == {"deadline_token": item.token}
                    self.observers.append(result)
                elif name == "_observation_token":
                    assert result is item.token
                elif name in {"_observation_enter", "_reference_enter", "_rows_enter"}:
                    assert result is args[0]
                elif name in {"_observation_exit", "_reference_exit", "_rows_exit"}:
                    assert result is None
                    if name == "_rows_exit":
                        self.closed_rows.append(args[0])
                elif name in {"_personal_reference", "_receiver_reference"}:
                    assert type(result) is observation.SqlReferenceDeadlineLease
                    role = "personal_reference" if name == "_personal_reference" else "receiver_reference"
                    self.ref_roles[id(result)] = role
                    self.refs.append(result)
                elif name in {"_query", "_reference_query"}:
                    assert type(result) is observation.SqlRowsDeadlineLease
                    role = "source" if name == "_query" else self.ref_roles[id(args[0])]
                    record = (role, args[1], kwargs.get("table"), kwargs.get("index_name"), result)
                    self.queries.append(record)
                    self.query_records[id(result)] = record
                elif name in {"_fetchone", "_fetchmany"}:
                    if name == "_fetchone":
                        assert result is None or type(result) is tuple
                        values = () if result is None else (result,)
                    else:
                        assert type(result) is tuple and all(type(row) is tuple for row in result)
                        values = result
                    self.values[id(args[0])].extend(values)
                    if not values:
                        self.eof.add(id(args[0]))
                elif name == "_schema_json" and type(args[0]) is dict and set(args[0]) == {"personal_schema_version", "tables"}:
                    assert args[0] == item.personal_schema and type(result) is str
                    assert result.encode() == canonical(item.personal_schema)
                    self.schema_inputs.append(args[0])
                    self.schema_jsons.append(result)
                elif name == "_schema_decode":
                    self.schema_decode_inputs.append(args[0])
                    if type(args[0]) is bytes and args[0] == canonical(item.personal_schema):
                        self.schemas.append(args[0])
                elif name == "_sha256":
                    assert type(args[0]) is bytes
                    self.hash_inputs[id(result)] = args[0]
                elif name == "_hash_update":
                    assert result is None and type(args[1]) is bytes
                elif name == "_hash_hexdigest":
                    assert type(result) is str and re.fullmatch("[0-9a-f]{64}", result)
                if "state" in kwargs:
                    self.states.append(kwargs["state"])
                if "schema_bytes" in kwargs:
                    raw = kwargs["schema_bytes"]
                    assert type(raw) is bytes and raw == canonical(item.personal_schema)
                    self.schemas.append(raw)
                    self.adapters.append((name, args, kwargs, result))
                if name == "_snapshot_validate":
                    assert type(result) is facts.SnapshotFacts
                    self.snapshot_results.append(result)
                elif name == "_snapshot_document":
                    self.documents.append(result)
                elif name == "_snapshot_counts":
                    self.counts.append(result)
                elif name == "_typed_table_digest":
                    assert set(result) == {"columns", "count", "sha256"}
                    fee_after = copy.deepcopy(self.observers[-1].evidence)
                    assert fee_after == fee_before
                    self.fee_windows.append((fee_before, fee_after))
                return result
            return observed
        for name in ALIASES:
            patch.setattr(subject, name, make(name))


class Scalar:
    def __init__(self, value):
        self.value = value

    def __call__(self):
        return self.value


def projected(item):
    return subject.project_sql_locked(item.profile, item.image, deadline_token=item.token)


def assert_closers(ledger):
    native_ledger = ledger.native
    assert Counter(map(id, native_ledger.cursor_close_attempts)) == Counter(map(id, native_ledger.cursors))
    assert Counter(map(id, native_ledger.cursor_close_returns)) == Counter(map(id, native_ledger.cursors))
    assert Counter(map(id, native_ledger.con_close_attempts)) == Counter(map(id, native_ledger.connections))
    assert Counter(map(id, native_ledger.con_close_returns)) == Counter(map(id, native_ledger.connections))
    assert all(c is not native_ledger.source_con for c in native_ledger.con_close_attempts)


def assert_fees(ledger, evidence):
    for role in ("source", "personal_reference", "receiver_reference"):
        value = evidence[role]
        assert set(value) == ROLE_FIELDS
        for group in ("data", "metadata"):
            rows = [row for current, kind, row, raw in ledger.native.encoded if current == role and kind == group]
            total = sum(len(raw) for current, kind, row, raw in ledger.native.encoded if current == role and kind == group)
            assert value[group + "_rows"] == len(rows)
            assert value[group + "_typed_bytes"] == total
        assert value["fee_complete"] is True
    assert not ledger.native.pending


def failed_scope(ledger, first):
    assert ledger.observers
    chosen = ledger.observers[-1]
    history = copy.deepcopy(chosen.evidence)
    assert set(history) == SESSION_FIELDS and history["retired"] is True
    assert history["phase"] == "retired" and history["failed"] is True and history["normal_exit"] is False
    assert history["source_borrowed"] is True and history["source"]["connection_state"] == "borrowed"
    with pytest.raises(type(first)) as caught:
        chosen.__enter__()
    assert caught.value is first and chosen.evidence == history
    assert_closers(ledger)
    return history


def coded(call, cls, code):
    with pytest.raises(cls) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def assert_plain(value):
    if type(value) is dict:
        assert all(type(key) is str for key in value)
        for item in value.values():
            assert_plain(item)
    elif type(value) in (tuple, list):
        for item in value:
            assert_plain(item)
    else:
        assert value is None or type(value) in (bool, int, float, str, bytes)


def assert_sql_oracle(item, product, ledger, *, projected_layouts=None):
    assert type(product) is tuple and len(product) == 3
    assert_plain(product)
    result, meta, rows = product
    assert type(result) is dict and set(result) == RESULT_FIELDS
    assert type(result["schema_version"]) is int and result["schema_version"] == 17
    assert result["source_fingerprint"] == item.image.source_fingerprint
    assert result["integrity"] == result["foreign_keys"] == "ok"
    assert result["receiver_facility"] == ("installed" if item.installed else "absent")
    assert type(meta) is dict and meta == item.metadata
    assert type(rows) is dict and tuple(rows) == tuple(item.rows)
    assert rows == item.rows  # Original known cells and original scan order.
    assert type(result["personal_tables"]) is dict and tuple(result["personal_tables"]) == tuple(contract.PERSONAL_COLUMNS)
    assert type(result["receiver_tables"]) is dict
    assert tuple(result["receiver_tables"]) == (tuple(contract.RECEIVER_COLUMNS) if item.installed else ())
    layouts = item.layouts if projected_layouts is None else projected_layouts
    for group, names in (("personal_tables", contract.PERSONAL_COLUMNS),
                         ("receiver_tables", contract.RECEIVER_COLUMNS if item.installed else {})):
        for table, columns in names.items():
            summary = result[group][table]
            assert type(summary) is dict and set(summary) == SUMMARY_FIELDS
            assert type(summary["columns"]) is list and summary["columns"] == list(columns)
            assert type(summary["count"]) is int
            actual = [tuple(row[column] for column in columns) for row in item.rows[table]]
            assert summary == independent_summary(columns, actual, layouts[table])
            for row in rows[table]:
                assert type(row) is dict and tuple(row) == tuple(columns)
    assert result["meta"] == independent_summary(("key", "value"), item.meta_rows, layouts["meta"])
    project = item.layouts[PROJECT_TABLE]
    assert project["primary_key"] == project["order_by"] == ["project_id"]
    assert ["project_id"] in project["unique_keys"]
    assert all(column["hidden"] == 0 for column in project["columns"])
    assert "CHECK" in item.layouts["decision_card"]["sql_tokens"]
    assert ["renovation_project", "project_id", "project_id", "NO ACTION", "NO ACTION", "NONE"] in item.layouts["source_record"]["foreign_keys"]
    if item.kind.startswith("populated"):
        assert [row["project_id"] for row in rows[PROJECT_TABLE]] == [PROJECT2, PROJECT]
        assert type(rows[PROJECT_TABLE][0]["revision"]) is float and rows[PROJECT_TABLE][0]["revision"] == 1.25
        assert rows["source_record"][0]["excerpt"] == b"original\x00\xff"
        assert rows["source_attachment"][0]["sha256"] is rows["source_attachment"][0]["byte_size"] is None
        assert rows[PROJECT_TABLE][0]["profile_json"] == '{ "city" : "绍兴", "quote" : "\\\"" }'
    else:
        assert set(meta) == {"library_id", "personal_primary_id", "primary_epoch"}
        assert meta["personal_primary_id"] == "" and all(not rows[t] for t in contract.PERSONAL_COLUMNS)
    assert len(ledger.observers) == 1
    owner = ledger.observers[0]
    assert owner.deadline_token is item.token
    evidence = copy.deepcopy(owner.evidence)
    assert set(evidence) == SESSION_FIELDS
    assert evidence["retired"] is True and evidence["phase"] == "retired"
    assert evidence["normal_exit"] is True and evidence["failed"] is False
    assert evidence["source_borrowed"] is True and evidence["source"]["connection_state"] == "borrowed"
    assert evidence["source"]["normal_exit"] is True
    assert evidence["personal_reference"]["connection_state"] == "closed"
    assert evidence["personal_reference"]["normal_exit"] is True
    if item.installed:
        assert evidence["receiver_reference"]["connection_state"] == "closed"
        assert evidence["receiver_reference"]["normal_exit"] is True
    else:
        assert evidence["receiver_reference"]["connection_state"] == "empty"
        assert evidence["receiver_reference"]["entered"] is evidence["receiver_reference"]["normal_exit"] is False
    for role in ("source", "personal_reference", "receiver_reference"):
        assert all(state == "closed" for state in evidence[role]["cursor_states"])
        if role != "receiver_reference" or item.installed:
            assert evidence[role]["progress_restore_state"] == evidence[role]["authorizer_restore_state"] == "returned"
    assert_fees(ledger, evidence)
    assert_closers(ledger)
    assert len(ledger.native.connections) == 1 + int(item.installed)
    assert len(ledger.refs) == 1 + int(item.installed)
    for con in (ledger.native.source_con, *ledger.native.connections):
        progress = [args for args in ledger.native.progress if args[0] is con]
        authorizers = [args for args in ledger.native.authorizers if args[0] is con]
        assert len(progress) == 2 and callable(progress[0][1]) and progress[0][2] == 1000
        assert progress[-1][1:] == (None, 0)
        assert authorizers and all(callable(args[1]) for args in authorizers)
        if con is not ledger.native.source_con:
            assert len(authorizers) == 2
    assert ledger.calls["_observation_enter"] == ledger.calls["_observation_exit"] == 1
    assert ledger.calls["_personal_reference"] == 1
    assert ledger.calls["_receiver_reference"] == int(item.installed)
    assert ledger.fee_windows and all(before == after for before, after in ledger.fee_windows)
    assert len(ledger.fee_windows) == len(item.rows) + 1
    assert ledger.schemas and all(raw is ledger.schemas[0] for raw in ledger.schemas)
    assert ledger.schemas[0] == canonical(item.personal_schema)
    if item.known is not None:
        assert ledger.adapters and ledger.snapshot_results and ledger.documents
        assert all(state is ledger.states[0] for state in ledger.states)
        assert all(f.document == item.known.document for f in ledger.snapshot_results)
        assert all(document == item.known.document for document in ledger.documents)
        assert not ledger.counts and ledger.calls["_snapshot_counts"] == 0
        assert {name for name, _, _, _ in ledger.adapters} >= {"_snapshot_validate", "_normalize_prepare", "_prepare_sha256", "_attachment_manifest"}
        assert not any(name == "_normalize_receipt" for name, _, _, _ in ledger.adapters)
        assert rows[receiver.INTENT][0]["prepare_json"] == item.known.prepare_json
        assert rows[receiver.ATTACHMENT][0]["descriptor_json"] == canonical(item.known.descriptor).decode()
    # Mutating caller facts cannot rewrite the actual owner's detached history.
    image_history = copy.deepcopy(item.image.source_fingerprint)
    result["personal_tables"][PROJECT_TABLE]["columns"].append("caller-only")
    result["source_fingerprint"]["caller_only"] = True
    meta["library_id"] = "caller-only"
    if rows[PROJECT_TABLE]:
        rows[PROJECT_TABLE][0]["name"] = "caller-only"
    assert owner.evidence == evidence and item.image.source_fingerprint == image_history
    assert item.image.require_active() is None
    return evidence


def assert_ordered_protocol(item, ledger):
    """Consume every actual query identity in the original fixed full order."""
    cursor = 0
    def take(role, recipe, table=None, index_name=None):
        nonlocal cursor
        record = ledger.queries[cursor]
        cursor += 1
        assert record[:4] == (role, recipe, table, index_name)
        rows = record[4]
        history = rows.evidence
        assert set(history) == ROWS_FIELDS and history["retired"] is True
        assert history["phase"] == "retired" and history["normal_exit"] is True and history["failed"] is False
        assert history["cursor_state"] == "closed" and history["eof_observed"] is True
        assert id(rows) in ledger.eof and history["rows_observed"] == len(ledger.values[id(rows)])
        return ledger.values[id(rows)]
    def descriptor(role, table):
        raw = take(role, "schema_object", table)
        assert len(raw) == 1 and raw[0][:2] == ("table", table) and raw[0][2] > 0
        take(role, "table_xinfo", table)
        indexes = take(role, "index_list", table)
        for row in indexes:
            if row[2]:
                assert not row[4]
                take(role, "index_info", table, row[1])
        take(role, "foreign_key_list", table)
    def storage(role, table):
        take(role, "table_info", table)
        take(role, "foreign_key_list", table)
        for row in take(role, "index_list", table):
            take(role, "index_sql", table, row[1])
            take(role, "index_xinfo", table, row[1])
        take(role, "table_sql", table)
    assert take("source", "integrity") == [("ok",)]
    assert take("source", "foreign_keys_check") == []
    for table in facts.TABLE_NAMES:
        descriptor("personal_reference", table)
    for table in facts.TABLE_NAMES:
        descriptor("source", table)
    take("source", "personal_triggers")
    assert take("source", "migration_version") == [("17",)]
    assert take("source", "personal_meta") == list(item.meta_rows)
    for table, columns in contract.PERSONAL_COLUMNS.items():
        descriptor("source", table)
        assert take("source", "raw_table", table) == [tuple(row[col] for col in columns) for row in item.rows[table]]
        descriptor("source", table)
    take("source", "main_objects")
    if item.installed:
        assert take("source", "temp_objects") == []
        take("receiver_reference", "main_objects")
        for table in receiver.TABLES:
            storage("receiver_reference", table)
        for table in receiver.TABLES:
            storage("source", table)
        for table, columns in contract.RECEIVER_COLUMNS.items():
            descriptor("source", table)
            assert take("source", "raw_table", table) == [tuple(row[col] for col in columns) for row in item.rows[table]]
            descriptor("source", table)
    descriptor("source", "meta")
    assert cursor == len(ledger.queries) == len(ledger.closed_rows)
    assert Counter(id(record[4]) for record in ledger.queries) == Counter(map(id, ledger.closed_rows))
    assert ledger.calls["_fetchmany"] == 1
    ddl = defaultdict(list)
    for _, con, statement, parameters in ledger.native.executions:
        if statement.startswith("CREATE "):
            assert parameters == ()
            ddl[ledger.native.roles[id(con)]].append(statement)
    assert ddl["personal_reference"] == ["CREATE TABLE " + name + " (" + ddl_text + ")" for name, ddl_text in facts._DDL.items()]
    expected_receiver = (["CREATE TABLE " + name + " (" + ddl_text + ")" for name, ddl_text in receiver.TABLES.items()]
                         + list(receiver.INDEXES) + list(receiver.TRIGGERS)) if item.installed else []
    assert ddl["receiver_reference"] == expected_receiver
    assert not ddl["source"]
    # The two metadata algorithms keep their distinct recipe signatures.
    assert not any(role == "receiver_reference" and recipe in {"schema_object", "table_xinfo", "index_info"}
                   for role, recipe, _, _, _ in ledger.queries)
    for table in contract.RECEIVER_COLUMNS if item.installed else ():
        assert sum(role == "source" and recipe == "schema_object" and name == table
                   for role, recipe, name, _, _ in ledger.queries) == 2


@pytest.mark.parametrize("kind", POSITIVE_IDS, ids=POSITIVE_IDS)
def test_actual_complete_projection(image_case, monkeypatch, kind):
    with image_case(kind) as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            product = projected(item)
            assert_ordered_protocol(item, ledger)
            assert_sql_oracle(item, product, ledger)
    outer = item.image_owner.evidence
    assert outer["phase"] == "retired" and outer["normal_exit"] is True
    assert outer["temp_state"] == outer["private_pin_state"] == outer["sql_state"] == "closed"
    assert outer["source_directory_borrowed"] is True


def drain_actual(owner, recipe, *, reference=False, table=None, index_name=None):
    factory = subject._reference_query if reference else subject._query
    rows = factory(owner, recipe, table=table, index_name=index_name)
    values = []
    with rows:
        while True:
            value = subject._fetchone(rows)
            if value is None:
                return values
            values.append(value)


@pytest.mark.parametrize("kind", ("receiver-reference-domain", "unlisted-source-table", "wrong-index-table"),
                         ids=("receiver-reference-domain", "unlisted-source-table", "wrong-index-table"))
def test_minimal_domain_rejections(image_case, monkeypatch, kind):
    with image_case("populated-empty-receiver") as item:
        fixed = ("schema_object", "table_xinfo", "index_info") if kind != "wrong-index-table" else ("cross-table", "cross-role")
        for selected in fixed:
            with monkeypatch.context() as patch:
                ledger = Ledger(patch, item)
                chosen = subject._observation(item.profile, item.image, deadline_token=item.token)
                first = None
                with pytest.raises(observation.SqlObservationDeadlineError) as raised:
                    with chosen:
                        if kind == "receiver-reference-domain":
                            ref = subject._receiver_reference(chosen)
                            with ref:
                                before = ledger.native.calls["_cursor"], ledger.native.calls["_execute"]
                                arguments = {"table": receiver.INTENT}
                                if selected == "index_info":
                                    arguments["index_name"] = "sqlite_autoindex_personal_mirror_transfer_intent_1"
                                first = coded(lambda: ref.query(selected, **arguments), observation.SqlObservationDeadlineError,
                                              "SQL_OBSERVATION_DEADLINE_INVALID_ARGUMENT")
                                assert (ledger.native.calls["_cursor"], ledger.native.calls["_execute"]) == before
                                with pytest.raises(type(first)) as sticky:
                                    ref.query("main_objects")
                                assert sticky.value is first
                        elif kind == "unlisted-source-table":
                            before = ledger.native.calls["_cursor"], ledger.native.calls["_execute"]
                            arguments = {"table": "job"}
                            if selected == "index_info":
                                arguments["index_name"] = "unlisted-index"
                            first = coded(lambda: chosen.query(selected, **arguments), observation.SqlObservationDeadlineError,
                                          "SQL_OBSERVATION_DEADLINE_INVALID_ARGUMENT")
                            assert (ledger.native.calls["_cursor"], ledger.native.calls["_execute"]) == before
                        elif selected == "cross-table":
                            indexes = drain_actual(chosen, "index_list", table=PROJECT_TABLE)
                            assert indexes and indexes[0][2] == 1
                            before = ledger.native.calls["_cursor"], ledger.native.calls["_execute"]
                            first = coded(lambda: chosen.query("index_info", table="source_record", index_name=indexes[0][1]),
                                          observation.SqlObservationDeadlineError, "SQL_OBSERVATION_DEADLINE_INVALID_ARGUMENT")
                            assert (ledger.native.calls["_cursor"], ledger.native.calls["_execute"]) == before
                        else:
                            ref = subject._personal_reference(chosen)
                            with ref:
                                indexes = drain_actual(ref, "index_list", reference=True, table=PROJECT_TABLE)
                            assert indexes and indexes[0][2] == 1
                            before = ledger.native.calls["_cursor"], ledger.native.calls["_execute"]
                            first = coded(lambda: chosen.query("index_info", table=PROJECT_TABLE, index_name=indexes[0][1]),
                                          observation.SqlObservationDeadlineError, "SQL_OBSERVATION_DEADLINE_INVALID_ARGUMENT")
                            assert (ledger.native.calls["_cursor"], ledger.native.calls["_execute"]) == before
                        with pytest.raises(type(first)) as sticky:
                            chosen.query("migration_version")
                        assert sticky.value is first
                assert raised.value is first
                failed_scope(ledger, first)
                assert item.image.require_active() is None


def test_wrong_token_type(image_case, monkeypatch):
    with image_case() as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            coded(lambda: subject.project_sql_locked(item.profile, item.image, deadline_token=object()),
                  subject.SqlProjectionDeadlineError, "SQL_PROJECTION_DEADLINE_INVALID_ARGUMENT")
            assert ledger.calls["_monotonic"] == ledger.calls["_observation"] == 0
            assert ledger.native.calls["_cursor"] == ledger.native.calls["_connect_memory"] == 0
            assert not ledger.observers and not ledger.native.executions


def test_three_meta_populated_rejected(image_case, monkeypatch):
    with image_case("three-meta-no-receiver", mutation="three-populated") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first = coded(lambda: projected(item), contract.PersonalPreservationError,
                          "PERSONAL_PRESERVATION_PERSONAL_META_INVALID")
            evidence = failed_scope(ledger, first)
            assert_fees(ledger, evidence)
            assert [table for role, recipe, table, _, _ in ledger.queries if role == "source" and recipe == "raw_table"] == list(contract.PERSONAL_COLUMNS)
            assert item.rows[PROJECT_TABLE] and set(item.metadata) == {"library_id", "personal_primary_id", "primary_epoch"}
            assert item.metadata["personal_primary_id"] == ""
            assert not any(recipe == "main_objects" for _, recipe, _, _, _ in ledger.queries)


def test_personal_check_drift(image_case, monkeypatch):
    with image_case(mutation="personal-check") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first = coded(lambda: projected(item), facts.PersonalSnapshotError, "SNAPSHOT_SCHEMA_DRIFT")
            failed_scope(ledger, first)
            assert item.layouts["decision_card"]["sql_tokens"] != item.personal_schema["tables"]["decision_card"]["sql_tokens"]
            assert len(ledger.schema_jsons) == 1 and not any(recipe == "raw_table" for _, recipe, _, _, _ in ledger.queries)


def test_receiver_trigger_missing(image_case, monkeypatch):
    with image_case("populated-empty-receiver", mutation="receiver-trigger") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first = coded(lambda: projected(item), receiver.PersonalMirrorSchemaError, "PERSONAL_MIRROR_SCHEMA_INCOMPATIBLE")
            failed_scope(ledger, first)
            assert len(ledger.native.connections) == 2
            assert not any(recipe == "raw_table" and table in contract.RECEIVER_COLUMNS for _, recipe, table, _, _ in ledger.queries)


def test_fresh_after_layout_projection(image_case, monkeypatch):
    with image_case() as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            original_query, original_fetch = subject._query, subject._fetchone
            found, projected_sql = [], []
            def query(*args, **kwargs):
                rows = original_query(*args, **kwargs)
                if args[1] == "schema_object" and kwargs.get("table") == PROJECT_TABLE:
                    found.append(rows)
                return rows
            def fetch(rows):
                value = original_fetch(rows)
                if len(found) == 3 and rows is found[2] and value is not None:
                    assert type(value) is tuple and len(value) == 4 and value[:2] == ("table", PROJECT_TABLE)
                    assert "'{}'" in value[3] and not projected_sql
                    replacement = value[3].replace("'{}'", "'{\"r832\":1}'", 1)
                    projected_sql.append(replacement)
                    return (*value[:3], replacement)
                return value
            patch.setattr(subject, "_query", query)
            patch.setattr(subject, "_fetchone", fetch)
            product = projected(item)
            assert len(found) == 3 and len(projected_sql) == 1
            expected = copy.deepcopy(item.layouts)
            expected[PROJECT_TABLE]["sql_tokens"] = independent_tokens(projected_sql[0])
            assert expected[PROJECT_TABLE] != item.layouts[PROJECT_TABLE]
            assert product[0]["personal_tables"][PROJECT_TABLE]["schema_sha256"] != hashlib.sha256(canonical(item.layouts[PROJECT_TABLE])).hexdigest()
            assert_ordered_protocol(item, ledger)
            assert_sql_oracle(item, product, ledger, projected_layouts=expected)


@pytest.mark.parametrize("mutation,code", (
    ("prepare-sha", "PERSONAL_PRESERVATION_RECEIVER_INTENT_INVALID"),
    ("duplicate-key", "PERSONAL_PRESERVATION_JSON_DUPLICATE_KEY")),
    ids=("receiver-prepare-sha-mismatch", "receiver-prepare-json-duplicate-key"))
def test_receiver_semantic_errors(image_case, monkeypatch, mutation, code):
    with image_case("three-meta-prepared-receiver", mutation=mutation) as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first = coded(lambda: projected(item), contract.PersonalPreservationError, code)
            evidence = failed_scope(ledger, first)
            assert_fees(ledger, evidence)
            assert [table for role, recipe, table, _, _ in ledger.queries if role == "source" and recipe == "raw_table"] == list(item.rows)
            if mutation == "prepare-sha":
                assert ledger.snapshot_results and ledger.adapters
                assert item.rows[receiver.INTENT][0]["prepare_sha256"] != item.known.prepare_sha
            else:
                assert not ledger.adapters and not ledger.snapshot_results
                assert item.rows[receiver.INTENT][0]["prepare_json"].count('"operation_id"') >= 2


@pytest.mark.parametrize("stage", ("typed-hash", "schema-json", "receiver-decode"),
                         ids=("typed-hash-postreturn-expiry", "schema-json-postreturn-expiry", "receiver-decode-postreturn-expiry"))
def test_cpu_postreturn_expiry(image_case, monkeypatch, stage):
    kind = "three-meta-prepared-receiver" if stage == "receiver-decode" else "populated-no-receiver"
    with image_case(kind) as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            scalar = Scalar(ledger.original["_monotonic"]())
            patch.setattr(subject, "_monotonic", scalar)
            returned = []
            if stage == "typed-hash":
                original_sha, original_hex = subject._sha256, subject._hash_hexdigest
                selected_hashers = []
                expected_columns = canonical(list(contract.PERSONAL_COLUMNS[PROJECT_TABLE]))
                def sha(raw):
                    hasher = original_sha(raw)
                    if raw == expected_columns:
                        selected_hashers.append(hasher)
                    return hasher
                def hexdigest(hasher):
                    value = original_hex(hasher)
                    if any(hasher is selected for selected in selected_hashers):
                        assert re.fullmatch("[0-9a-f]{64}", value) and not returned
                        returned.append(value)
                        scalar.value = item.token.absolute_monotonic
                    return value
                patch.setattr(subject, "_sha256", sha)
                patch.setattr(subject, "_hash_hexdigest", hexdigest)
            elif stage == "schema-json":
                original = subject._schema_json
                def encoded(value):
                    result = original(value)
                    if type(value) is dict and set(value) == {"personal_schema_version", "tables"}:
                        assert value == item.personal_schema and result.encode() == canonical(item.personal_schema) and not returned
                        returned.append(result)
                        scalar.value = item.token.absolute_monotonic
                    return result
                patch.setattr(subject, "_schema_json", encoded)
            else:
                original = subject._decode
                def decoded(raw):
                    value = original(raw)
                    if raw == item.known.prepare_json:
                        assert value == item.known.prepare and not returned
                        returned.append(value)
                        scalar.value = item.token.absolute_monotonic
                    return value
                patch.setattr(subject, "_decode", decoded)
            first = coded(lambda: projected(item), subject.SqlProjectionDeadlineError, "SQL_PROJECTION_DEADLINE_DEADLINE")
            assert len(returned) == 1 and scalar.value == item.token.absolute_monotonic
            evidence = failed_scope(ledger, first)
            assert_fees(ledger, evidence)
            assert evidence["personal_reference"]["connection_state"] == "closed"
            assert item.image.deadline_token is item.token and item.image.require_active() is None
            if stage == "typed-hash":
                assert len(selected_hashers) == 1
                assert [table for role, recipe, table, _, _ in ledger.queries if recipe == "raw_table" and role == "source"] == [PROJECT_TABLE]
            elif stage == "schema-json":
                assert not any(role == "source" and recipe == "schema_object" for role, recipe, _, _, _ in ledger.queries)
            else:
                assert [table for role, recipe, table, _, _ in ledger.queries if recipe == "raw_table" and role == "source"] == list(item.rows)
                assert not ledger.adapters


def test_cpu_first_plus_cleanup_second(image_case, monkeypatch):
    with image_case() as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first, second = OSError("r832 original schema CPU first"), OSError("r832 actual close outer second")
            touched, close_results = [], []
            original_json, original_close = subject._schema_json, observation._con_close
            def broken_json(value):
                if type(value) is dict and set(value) == {"personal_schema_version", "tables"}:
                    assert value == item.personal_schema
                    assert ledger.observers[0].evidence["personal_reference"]["connection_state"] == "owned"
                    touched.append(value)
                    raise first
                return original_json(value)
            def closed_then_second(con):
                result = original_close(con)
                assert result is None and len(ledger.native.connections) == 1 and con is ledger.native.connections[0]
                close_results.append(result)
                raise second
            patch.setattr(subject, "_schema_json", broken_json)
            patch.setattr(observation, "_con_close", closed_then_second)
            with pytest.raises(OSError) as caught:
                projected(item)
            assert caught.value is first and len(touched) == len(close_results) == 1
            evidence = failed_scope(ledger, first)
            assert evidence["personal_reference"]["connection_state"] == "unknown"
            assert evidence["personal_reference"]["normal_exit"] is False
            assert not any(role == "source" and recipe == "schema_object" for role, recipe, _, _, _ in ledger.queries)
            assert item.image.require_active() is None


def test_cleanup_only_failure_no_result(image_case, monkeypatch):
    with image_case() as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first = OSError("r832 actual personal reference close outer first")
            original_close = observation._con_close
            close_results = []
            def closed_then_first(con):
                result = original_close(con)
                assert result is None and len(ledger.native.connections) == 1 and con is ledger.native.connections[0]
                assert len(ledger.schema_jsons) == 1 and ledger.schema_jsons[0].encode() == canonical(item.personal_schema)
                close_results.append(result)
                raise first
            patch.setattr(observation, "_con_close", closed_then_first)
            with pytest.raises(OSError) as caught:
                projected(item)
            assert caught.value is first and len(close_results) == 1
            evidence = failed_scope(ledger, first)
            assert evidence["personal_reference"]["connection_state"] == "unknown"
            assert evidence["personal_reference"]["normal_exit"] is False
            assert not ledger.fee_windows
            assert not any(role == "source" and recipe == "schema_object" for role, recipe, _, _, _ in ledger.queries)
            assert item.image.require_active() is None
