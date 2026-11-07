"""r839 namespace fixture and independent oracles; author did not run product.

The RW lifetime includes subject and independent oracle checks for WAL/prepared.
Absent means optional WAL/journal absent: the database and receiver schema exist.
Approved namespace source/alias/fault bindings are recorded separately; missing
bindings are never converted into a successful assertion or a skipped test.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
from dataclasses import FrozenInstanceError
import base64
import copy
import hashlib
import json
import math
import ntpath
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
import time
from types import SimpleNamespace

import pytest
from reno import personal_authority_namespace3 as original_namespace
from reno import personal_authority_namespace3_contract as namespace_contract
from reno import personal_authority_origin_reservation_contract as origin_contract
from reno import personal_authority_origin_reservation_owner_deadline as origin_setup
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_namespace3_effect_deadline as locked
from reno import personal_authority_namespace3_effect_owner_deadline as owned
from reno import personal_authority_namespace3_io_deadline as publication
from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_schema as receiver
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual temporary Windows namespace3/native protocol")
PROJECT_TABLE = "renovation_project"
STAMP = "2026-10-12T00:00:00Z"
META_KEYS = ("default_project_id", "library_id", "personal_primary_id", "primary_epoch")
SUFFIXES = ("", "-wal", "-journal")
ROOT_NAMES = ("data", "media", "reports", "checkpoints")
FIRST = ".reno-personal-authority-origin-v1-reservation.json"
SECOND = ".reno-personal-authority-origin-v1-observation.json"
RESERVATION = ".reno-personal-authority-namespace-v3-reservation.json"
ROOT_NAME = ".reno-personal-authority-namespace-v3"
SEAL = ".reno-personal-authority-namespace-v3-root-seal.json"
HEADER = ".reno-personal-authority-namespace-v3-header.json"
UNKNOWN = ".RENO-PERSONAL-AUTHORITY-unknown. "
UNKNOWN_RAW = b"r839-raw-name-tail-dot-space\x00\xff" + "绍兴原始名称".encode("utf-8")
SCOPE = "filesystem_namespace3_facts_only"
CREATION = "windows_ntcreate_directory_handle_v1"
POLICY = "current_user_and_system_full_control_protected_v1"
COMMON_FIELDS = {"format_version", "family", "kind", "scope", "init_operation_id", "domain_id", "facility_id"}
DTO_FIELDS = ("scope", "init_operation_id", "domain_id", "facility_id", "origin_reservation_sha256",
              "origin_observation_sha256", "namespace_reservation_sha256", "root_seal_sha256", "header_sha256",
              "creation_kind")
PERSONAL_COLUMNS = {'renovation_project': ('project_id', 'name', 'profile_json', 'revision', 'created_at', 'updated_at', 'archived_at'), 'decision_card': ('card_id', 'project_id', 'title', 'question', 'context_json', 'space', 'stage', 'alternatives_json', 'selected_option_id', 'decision_text', 'decision_reason', 'open_questions_json', 'content_snapshot_json', 'state', 'revision', 'created_at', 'updated_at', 'archived_at'), 'source_record': ('source_id', 'project_id', 'source_type', 'title', 'publisher', 'document_no', 'edition', 'published_on', 'valid_from', 'valid_to', 'source_url', 'locator', 'excerpt', 'scope_json', 'verification_state', 'validity', 'checked_at', 'check_note', 'revision', 'created_at', 'updated_at'), 'source_attachment': ('attachment_id', 'source_id', 'project_id', 'sha256', 'byte_size', 'media_type', 'original_name', 'storage_key', 'state', 'created_at', 'operation_id', 'error'), 'personal_evidence_ref': ('ref_id', 'project_id', 'owner_kind', 'owner_id', 'target_kind', 'target_id', 'fingerprint_version', 'target_fingerprint', 'snapshot_json', 'created_at'), 'checklist_instance': ('instance_id', 'project_id', 'title', 'space', 'stage', 'basis_kb_revision', 'lifecycle_state', 'revision', 'created_at', 'updated_at', 'archived_at'), 'checklist_item': ('item_id', 'instance_id', 'title', 'conditions_json', 'method_text', 'basis_kind', 'criteria_json', 'criteria_state', 'position', 'revision', 'created_at', 'updated_at'), 'checklist_check': ('check_id', 'item_id', 'item_revision', 'criteria_snapshot_hash', 'operation_id', 'commit_seq', 'record_kind', 'result', 'measurements_json', 'note', 'attachment_refs_json', 'checked_at', 'created_at', 'supersedes_check_id'), 'personal_revision': ('entity_kind', 'entity_id', 'revision', 'snapshot_json', 'operation_id', 'created_at'), 'mutation_receipt': ('operation_id', 'scope', 'request_hash', 'response_status', 'response_json', 'created_at')}
RECEIVER_COLUMNS = {'personal_mirror_transfer_intent': ('operation_id', 'format_version', 'prepare_json', 'prepare_sha256', 'snapshot_id', 'snapshot_sha256', 'source_library_id', 'source_node_id', 'source_primary_id', 'source_primary_epoch', 'receiver_library_id', 'receiver_node_id', 'previous_snapshot_id', 'previous_snapshot_sha256', 'expected_revision', 'state', 'created_at', 'updated_at'), 'personal_mirror_transfer_attachment': ('operation_id', 'attachment_id', 'storage_key', 'sha256', 'byte_size', 'descriptor_json', 'descriptor_sha256', 'state', 'local_file_device', 'local_file_inode', 'local_file_mtime_ns', 'local_file_ctime_ns', 'stored_at'), 'personal_mirror_transfer_receipt': ('operation_id', 'format_version', 'prepare_sha256', 'snapshot_id', 'snapshot_sha256', 'installed_revision', 'receipt_json', 'receipt_sha256', 'committed_at'), 'personal_mirror_transfer_head': ('slot', 'format_version', 'operation_id', 'source_library_id', 'source_node_id', 'source_primary_id', 'source_primary_epoch', 'receiver_library_id', 'receiver_node_id', 'revision', 'snapshot_id', 'snapshot_sha256', 'receipt_sha256', 'updated_at')}


def uid(number):
    return f"00000000-0000-4000-8000-{number:012d}"

PROJECT, PROJECT2, SOURCE, ATTACHMENT = uid(10), uid(11), uid(12), uid(13)
SOURCE_CREATE, ATTACH_PUT = uid(20), uid(21)
LIBRARY, PRIMARY = uid(900), uid(901)
SOURCE_LIBRARY, SOURCE_PRIMARY, SNAPSHOT, TRANSFER = uid(600), uid(601), uid(602), uid(603)
DOMAIN, FACILITY, INIT_OPERATION, CARD, CARD_CREATE = uid(910), uid(911), uid(912), uid(913), uid(914)
BODY = b"%PDF-r832-original-known-body\x00\xe4\xb8\xad"
BODY_SHA = hashlib.sha256(BODY).hexdigest()

def native(path):
    value = os.fspath(path)
    if value.startswith("\\\\?\\"):
        return value
    if value.startswith("\\\\"):
        return "\\\\?\\UNC\\" + value[2:]
    if (len(value) < 3 or value[1:3] != ":\\"
            or value[0] not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"):
        raise ValueError("QA native path must be absolute")
    return "\\\\?\\" + value

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
    rows = raw_records(con, PERSONAL_COLUMNS)
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
    tables = raw_records(con, PERSONAL_COLUMNS)
    for table, records in tables.items():
        keys = schema["tables"][table]["primary_key"]
        records.sort(key=lambda r: tuple(r[k] for k in keys))
    meta = {"default_project_id": PROJECT, "library_id": SOURCE_LIBRARY,
            "personal_primary_id": SOURCE_PRIMARY, "primary_epoch": "1"}
    counts = {table: len(records) for table, records in tables.items()}
    counts.update(meta=4, attachment_manifest=1)
    document = {"format_version": 1, "snapshot_id": SNAPSHOT,
        "source": {"library_id": SOURCE_LIBRARY, "personal_primary_id": SOURCE_PRIMARY, "primary_epoch": 1},
        "meta": [{"key": key, "value": meta[key]} for key in META_KEYS],
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
    for table in reversed(tuple(PERSONAL_COLUMNS)):
        sql_rows(con, 'DELETE FROM "' + table + '"')
    assert all(not values for values in raw_records(con, PERSONAL_COLUMNS).values())
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

def populate_origin(con):
    for project, title, revision in ((PROJECT2, "另一个'O\\绍兴项目", 1.25), (PROJECT, "绍兴原始项目", 1)):
        insert(con, PROJECT_TABLE, {"project_id": project, "name": title, "profile_json": '{ "city" : "绍兴" }',
            "revision": revision, "created_at": STAMP, "updated_at": STAMP, "archived_at": None})
    insert(con, "decision_card", {"card_id": CARD, "project_id": PROJECT, "title": "防水原始决策",
        "context_json": '{ "材料" : "未定" }', "created_at": STAMP, "updated_at": STAMP})
    card = persistent_view(raw_records(con, {"decision_card": PERSONAL_COLUMNS["decision_card"]})["decision_card"][0])
    insert(con, "personal_revision", {"entity_kind": "card", "entity_id": CARD, "revision": 1,
        "snapshot_json": canonical(card).decode(), "operation_id": CARD_CREATE, "created_at": STAMP})
    insert(con, "mutation_receipt", {"operation_id": CARD_CREATE, "scope": f"project:{PROJECT}:cards:create",
        "request_hash": "a" * 64, "response_status": 201,
        "response_json": canonical({"operation_id": CARD_CREATE, "card": card}).decode(), "created_at": STAMP})
    insert(con, "source_record", {"source_id": SOURCE, "project_id": PROJECT, "source_type": "manual_note",
        "title": "原BLOB事实", "excerpt": b"raw\x00\xff", "scope_json": '{ "scope" : ["绍兴"] }',
        "created_at": STAMP, "updated_at": STAMP})


def sha(raw):
    assert type(raw) is bytes
    return hashlib.sha256(raw).hexdigest()


def _reject_json_constant(value):
    raise AssertionError("nonfinite JSON constant in fixture oracle")


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        assert type(key) is str and key not in value, "duplicate JSON key"
        value[key] = item
    return value


def decoded(raw, *, canonical_required=True):
    """Independent JSON parser: no product decode/normalize/expected helper."""
    assert type(raw) is bytes and 0 < len(raw) <= 1024 * 1024
    value = json.loads(raw.decode("utf-8", errors="strict"), object_pairs_hook=_unique_object,
                       parse_constant=_reject_json_constant)
    if canonical_required:
        assert canonical(value) == raw, "noncanonical complete raw JSON"
    return value


def fixture_path_key(path):
    """Profile paths only; never apply this to inventory member names."""
    value = os.fspath(path)
    assert type(value) is str and ntpath.isabs(value) and ".." not in Path(value).parts
    return ntpath.normcase(value)


def fixture_path_identity(path):
    info = os.stat(native(path), follow_symlinks=False)
    assert not stat.S_ISLNK(info.st_mode)
    return {"device": str(info.st_dev), "inode": str(info.st_ino)}


def file_identity(info):
    return {"device": str(info.st_dev), "inode": str(info.st_ino), "byte_size": info.st_size,
            "mtime_ns": str(info.st_mtime_ns), "ctime_ns": str(info.st_ctime_ns),
            "mode": info.st_mode, "nlink": info.st_nlink}


def fixture_read_file(path, *, maximum=64 * 1024 * 1024):
    """Fixed fixture phase, outside product observer; no additional Win32 probe."""
    before = file_identity(os.stat(native(path), follow_symlinks=False))
    assert stat.S_ISREG(before["mode"]) and before["nlink"] == 1 and before["byte_size"] <= maximum
    with open(native(path), "rb", buffering=0) as stream:
        handle_before = file_identity(os.fstat(stream.fileno()))
        raw = stream.read(maximum + 1)
        assert type(raw) is bytes and len(raw) <= maximum and stream.read(1) == b""
        handle_after = file_identity(os.fstat(stream.fileno()))
    after = file_identity(os.stat(native(path), follow_symlinks=False))
    assert before == after and handle_before == handle_after
    assert len(raw) == before["byte_size"]
    for field in ("device", "inode", "byte_size", "mtime_ns", "mode", "nlink"):
        assert before[field] == handle_before[field]
    return raw, before, handle_before


def fixture_optional_file(path):
    try:
        return fixture_read_file(path)
    except FileNotFoundError:
        return None


def fixture_raw_sources(item):
    result = {suffix: fixture_optional_file(str(item.profile.database) + suffix) for suffix in SUFFIXES}
    result["node"] = fixture_read_file(item.node_path, maximum=4096)
    if item.known is not None:
        result["payload"] = fixture_read_file(item.payload_path)
    return result


def fixture_sql_snapshot(item):
    """Read known QA writer or one declared readonly oracle, before RW close."""
    if item.rw is not None:
        con, owned = item.rw, False
    else:
        uri = item.profile.database.as_uri() + "?mode=ro"
        con, owned = sqlite3.connect(uri, uri=True), True
    try:
        rosters = dict(PERSONAL_COLUMNS)
        if item.installed:
            rosters.update(RECEIVER_COLUMNS)
        rosters["meta"] = ("key", "value")
        rosters["r839_nonpersonal_facts"] = ("key", "value")
        rows, layouts, summaries = {}, {}, {}
        for table, columns in rosters.items():
            selected = sql_rows(con, 'SELECT ' + ','.join('"' + n + '"' for n in columns) + ' FROM "' + table + '"')
            rows[table] = sorted(independent_row_bytes(row) for row in selected)
            layouts[table] = independent_layout(con, table)
            summaries[table] = independent_summary(columns, selected, layouts[table])
        schema = sql_rows(con, "SELECT type,name,tbl_name,rootpage,sql FROM sqlite_schema ORDER BY type,name")
        return {"rows": rows, "layouts": layouts, "summaries": summaries, "schema": schema}
    finally:
        if owned:
            con.close()


def fixture_topology(base):
    """Extended native inventory preserves exact entry.name, including dot/space."""
    result = {}
    def visit(directory, parent_parts):
        with os.scandir(native(directory)) as entries:
            children = list(entries)
        for entry in children:
            assert type(entry.name) is str and entry.name not in {".", ".."}
            parts = parent_parts + (entry.name,)
            raw_path = os.fspath(directory) + "\\" + entry.name
            info = entry.stat(follow_symlinks=False)
            assert not stat.S_ISLNK(info.st_mode), "fixture topology contains symlink"
            if stat.S_ISDIR(info.st_mode):
                result[parts] = ("directory", {"device": str(info.st_dev), "inode": str(info.st_ino)})
                visit(raw_path, parts)
            else:
                assert stat.S_ISREG(info.st_mode), "fixture topology contains nonregular member"
                result[parts] = ("file", fixture_read_file(raw_path))
    visit(base, ())
    return result


def fixture_source_baseline(item):
    # SQL oracle is read first: any connection-phase WAL effects precede bytes.
    sql = fixture_sql_snapshot(item)
    return {"sql": sql, "raw": fixture_raw_sources(item), "topology": fixture_topology(item.base)}


def assert_source_preserved(item, baseline, *, namespace_created):
    after = fixture_source_baseline(item)
    assert after["sql"] == baseline["sql"] and after["raw"] == baseline["raw"]
    after_tree = dict(after["topology"])
    if namespace_created:
        for parts in (("data", RESERVATION), ("data", HEADER),
                      ("data", ROOT_NAME), ("data", ROOT_NAME, SEAL)):
            assert parts in after_tree
            if parts not in baseline["topology"]:
                after_tree.pop(parts)
    assert after_tree == baseline["topology"]
    assert tuple(fixture_read_file(item.data / name)[0] for name in (FIRST, SECOND)) == (item.first, item.second)


def assert_origin_setup(item, result):
    """Original pair is setup only, with its own effective deadline/EX lifetime."""
    item.first = fixture_read_file(item.data / FIRST, maximum=16 * 1024)[0]
    item.second = fixture_read_file(item.data / SECOND, maximum=64 * 1024)[0]
    first, second = decoded(item.first), decoded(item.second)
    assert first["kind"] == "origin_reservation" and second["kind"] == "origin_observation"
    assert first["format_version"] == second["format_version"] == 1
    assert first["family"] == second["family"] == "personal_authority_origin_reservation"
    for key, value in item.arguments.items():
        assert first[key] == value
    assert second["init_operation_id"] == item.arguments["init_operation_id"]
    assert second["reservation_sha256"] == sha(item.first) == result.reservation_sha256
    assert result.observation_sha256 == sha(item.second)
    paths = [fixture_path_key(p) for p in (item.profile.database, item.profile.media, item.profile.reports, item.profile.checkpoints)]
    physical = {"profile_paths": paths, "roots": {name: {"path": fixture_path_key(path),
        "identity": fixture_path_identity(path)} for name, path in zip(ROOT_NAMES, item.root_paths)}}
    assert first["profile"] == physical
    assert first["authority_profile_paths_sha256"] == sha(canonical(paths))
    assert first["physical_profile_sha256"] == sha(canonical(physical))
    node_raw, path_info, handle_info = fixture_read_file(item.node_path, maximum=4096)
    node = first["node"]
    assert node["node_id"] == decoded(node_raw, canonical_required=False)["instance_id"] == PRIMARY
    assert node["sha256"] == sha(node_raw) and node["byte_size"] == len(node_raw)
    assert node["directory_identity"] == fixture_path_identity(item.data)
    assert node["path_identity"] == path_info
    for key in ("device", "inode", "byte_size", "mtime_ns", "mode", "nlink"):
        assert node["handle_identity"][key] == handle_info[key]
    for role in ("intent", "evidence"):
        actual = base64.b64decode(second["origin_" + role + "_base64"], validate=True)
        assert sha(actual) == second["origin_" + role + "_sha256"]
        assert canonical(decoded(actual)) == actual
    # Shared validators supplement whole raw/JSON/path/node/hash checks.
    origin_contract.cross_validate(item.first, item.second)
    assert lifecycle.current_guard() is None
    item.expected_origin = {"expected_origin_reservation_sha256": sha(item.first),
                            "expected_origin_observation_sha256": sha(item.second)}


def independent_reservation_raw(item):
    """Literal expected record; never call subject _reservation/_common."""
    first, second = decoded(item.first), decoded(item.second)
    return canonical({"format_version": 1, "family": "personal_authority_namespace3",
        "kind": "namespace_reservation", "scope": SCOPE, **item.arguments,
        "profile": first["profile"], "node": first["node"], "creation_kind": CREATION,
        "layout": {"reservation_name": RESERVATION, "root_name": ROOT_NAME,
                   "root_seal_name": SEAL, "header_name": HEADER},
        "origin": {"reservation_sha256": sha(item.first), "observation_sha256": sha(item.second),
            "intent_sha256": second["origin_intent_sha256"], "evidence_sha256": second["origin_evidence_sha256"],
            "authority_profile_paths_sha256": sha(canonical(first["profile"]["profile_paths"])),
            "physical_profile_sha256": sha(canonical(first["profile"])),
            "authority_profile_paths_role": "canonical_four_paths_array_v1",
            "physical_profile_role": "canonical_physical_profile_dict_v1"}})


def _assert_native_identity(value):
    assert type(value) is dict and set(value) == {"volume_serial", "file_index"}
    for field, maximum in (("volume_serial", 2**32 - 1), ("file_index", 2**64 - 1)):
        text = value[field]
        assert type(text) is str and re.fullmatch(r"0|[1-9][0-9]*", text)
        assert int(text) <= maximum


def assert_complete_namespace(item, result, *, approved_native):
    """The native identities must be actual-forward approved returns, never probes."""
    reservation_raw = fixture_read_file(item.data / RESERVATION, maximum=32 * 1024)[0]
    seal_raw = fixture_read_file(item.data / ROOT_NAME / SEAL, maximum=16 * 1024)[0]
    header_raw = fixture_read_file(item.data / HEADER, maximum=16 * 1024)[0]
    reservation, seal, header = map(decoded, (reservation_raw, seal_raw, header_raw))
    assert reservation_raw == independent_reservation_raw(item)
    assert set(reservation) == COMMON_FIELDS | {"profile", "node", "layout", "creation_kind", "origin"}
    assert set(seal) == COMMON_FIELDS | {"namespace_reservation_sha256", "root_name", "creator"}
    assert set(header) == COMMON_FIELDS | {"namespace_reservation_sha256", "root_seal_sha256", "root_name",
                                          "creation_kind", "root_native_identity", "root_path_identity"}
    for document, kind in ((seal, "namespace_root_seal"), (header, "namespace_header")):
        assert document["kind"] == kind and document["format_version"] == 1
        assert document["family"] == "personal_authority_namespace3" and document["scope"] == SCOPE
        assert all(document[key] == value for key, value in item.arguments.items())
        assert document["namespace_reservation_sha256"] == sha(reservation_raw)
        assert document["root_name"] == ROOT_NAME
    creator = seal["creator"]
    assert set(creator) == {"creation_kind", "private_policy", "parent_native_identity", "parent_path_identity",
                            "root_native_identity", "root_path_identity"}
    assert creator["creation_kind"] == header["creation_kind"] == CREATION and creator["private_policy"] == POLICY
    assert creator["parent_path_identity"] == fixture_path_identity(item.data)
    assert creator["root_path_identity"] == header["root_path_identity"] == fixture_path_identity(item.data / ROOT_NAME)
    assert type(approved_native) is dict and set(approved_native) == {"parent", "root"}, "native role binding pending"
    for name in ("parent", "root"):
        _assert_native_identity(approved_native[name])
        assert creator[name + "_native_identity"] == approved_native[name]
    assert header["root_native_identity"] == approved_native["root"]
    assert approved_native["parent"]["volume_serial"] == approved_native["root"]["volume_serial"]
    assert header["root_seal_sha256"] == sha(seal_raw)
    assert type(result) is original_namespace.Namespace3FilesystemFacts
    assert set(type(result).__slots__) == set(DTO_FIELDS) and not hasattr(result, "__dict__")
    expected = {"scope": SCOPE, **item.arguments, "creation_kind": CREATION,
        "origin_reservation_sha256": sha(item.first), "origin_observation_sha256": sha(item.second),
        "namespace_reservation_sha256": sha(reservation_raw), "root_seal_sha256": sha(seal_raw), "header_sha256": sha(header_raw)}
    for field in DTO_FIELDS:
        assert type(getattr(result, field)) is str and getattr(result, field) == expected[field]
    with pytest.raises(FrozenInstanceError):
        result.header_sha256 = "0" * 64
    with os.scandir(native(item.data / ROOT_NAME)) as entries:
        assert {entry.name for entry in entries} == {SEAL}
    with os.scandir(native(item.data)) as entries:
        authority_names = {entry.name for entry in entries if entry.name.rstrip(" .").casefold().startswith(".reno-personal-authority-")}
    assert authority_names == {FIRST, SECOND, RESERVATION, ROOT_NAME, HEADER}
    namespace_contract.cross_validate_complete(item.first, item.second, reservation_raw, seal_raw, header_raw)
    return expected


def fixture_unknown_tail(item):
    """Actual extended native basename fixture, with dual whole raw readback."""
    path = os.fspath(item.data) + "\\" + UNKNOWN
    with open(native(path), "xb", buffering=0) as stream:
        assert stream.write(UNKNOWN_RAW) == len(UNKNOWN_RAW)
        stream.flush()
        os.fsync(stream.fileno())
    for _ in range(2):
        assert fixture_read_file(path)[0] == UNKNOWN_RAW
    with os.scandir(native(item.data)) as entries:
        assert UNKNOWN in {entry.name for entry in entries}
    return path


def namespace_case_factory(tmp_path):
    sequence = 0
    @contextmanager
    def opened(mode="wal"):
        nonlocal sequence
        assert mode in {"wal", "absent", "prepared"}
        qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
        tmp_path.resolve(strict=True).relative_to(qa)
        Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
        sequence += 1
        base = tmp_path / f"r839-{sequence}-{mode}"
        profile = lifecycle.StorageProfile(base / "data/绍兴-namespace.sqlite3", base / "media", base / "reports", base / "checkpoints")
        profile.database.parent.mkdir(parents=True)
        initial = sqlite3.connect(native(profile.database))
        try:
            sql_rows(initial, "CREATE TABLE meta(key TEXT PRIMARY KEY,value TEXT)")
            sql_rows(initial, "INSERT INTO meta VALUES('migration_version','17')")
            initial.commit()
        finally:
            initial.close()
        lifecycle.initialize(profile)
        rw = None
        with lifecycle.exclusive(profile):
            rw = sqlite3.connect(native(profile.database))
            try:
                journal = "DELETE" if mode == "absent" else "WAL"
                assert sql_rows(rw, "PRAGMA journal_mode=" + journal)[0][0].upper() == journal
                sql_rows(rw, "PRAGMA wal_autocheckpoint=0")
                sql_rows(rw, "PRAGMA foreign_keys=ON")
                for table, ddl in facts._DDL.items():
                    sql_rows(rw, "CREATE TABLE " + table + " (" + ddl + ")")
                schema = {"personal_schema_version": 1, "tables": {t: independent_layout(rw, t) for t in PERSONAL_COLUMNS}}
                installed = mode in {"absent", "prepared"}
                if installed:
                    for table, ddl in receiver.TABLES.items():
                        sql_rows(rw, "CREATE TABLE " + table + " (" + ddl + ")")
                    for statement in receiver.INDEXES + receiver.TRIGGERS:
                        sql_rows(rw, statement)
                known = known_snapshot(rw, schema) if mode == "prepared" else None
                if known is not None:
                    add_receiver_rows(rw, known)
                populate_origin(rw)
                sql_rows(rw, "CREATE TABLE r839_nonpersonal_facts(key TEXT PRIMARY KEY,value BLOB)")
                sql_rows(rw, "INSERT INTO r839_nonpersonal_facts VALUES(?,?)", ("known", b"nonpersonal\x00\xff"))
                meta = {"library_id": LIBRARY, "personal_primary_id": PRIMARY, "primary_epoch": "1", "default_project_id": PROJECT}
                for key, value in meta.items():
                    sql_rows(rw, "INSERT INTO meta VALUES(?,?)", (key, value))
                rw.commit()
                node_path = profile.database.parent / "node-identity.json"
                node_raw = ('{"instance_id":"' + PRIMARY + '"}').encode()
                node_path.write_bytes(node_raw)
                payload_path = None
                if known is not None:
                    payload_path = profile.media / known.descriptor["storage_key"]
                    payload_path.parent.mkdir(parents=True)
                    payload_path.write_bytes(BODY)
                if mode == "wal":
                    Path(str(profile.database) + "-journal").write_bytes(b"")
                if mode == "absent":
                    rw.close()
                    rw = None
                item = SimpleNamespace(base=base, data=profile.database.parent, profile=profile,
                    mode=mode, installed=installed, schema=schema, known=known, meta=meta,
                    node_path=node_path, node_raw=node_raw, payload_path=payload_path, rw=rw,
                    root_paths=(profile.database.parent, profile.media, profile.reports, profile.checkpoints),
                    arguments={"domain_id": DOMAIN, "facility_id": FACILITY, "init_operation_id": INIT_OPERATION})
                contents = fixture_raw_sources(item)
                assert contents[""][0]
                assert contents["-wal"] is None if mode == "absent" else bool(contents["-wal"][0])
                assert (None if contents["-journal"] is None else contents["-journal"][0]) == (b"" if mode == "wal" else None)
            except BaseException:
                if rw is not None:
                    rw.close()
                    rw = None
                raise
        assert lifecycle.current_guard() is None
        try:
            # No unsupported deadline_token parameter: this is a separate setup.
            setup = origin_setup.reserve_origin(profile, **item.arguments)
            assert_origin_setup(item, setup)
            item.setup_result = setup
            yield item
        finally:
            # Subject/oracles must run inside this yield, before fixture retirement.
            if rw is not None:
                rw.close()
                item.rw = None
    return opened


@pytest.fixture
def namespace_case(tmp_path):
    return namespace_case_factory(tmp_path)


def requested_namespace_token():
    # This requested namespace token is separate from origin/setup effective T.
    return deadline.Namespace3Deadline(time.monotonic() + 30.0)


def prepare_r_only(item):
    """Publish one canonical R using the actual new publisher and normal exits."""
    assert lifecycle.current_guard() is None
    before = fixture_source_baseline(item)
    raw = independent_reservation_raw(item)
    with lifecycle.exclusive(item.profile) as guard:
        requested = requested_namespace_token()
        budget = deadline._RestrictedBudget(requested)
        state = publication._NamespaceState(budget, budget.effective_deadline)
        stack = publication._new_known_stack(state)
        receipt = publication._claim_stack_receipt(state, stack)
        with publication._typed_scope_guard(state, receipt) as entered:
            assert entered is stack
            parent = stack.enter_parent(publication.open_parent_locked(item.profile, state, write=True))
            published = publication.publish_new_locked(item.profile, parent, RESERVATION,
                RESERVATION + "." + item.arguments["init_operation_id"] + ".stage", raw, state)
            assert type(published.raw_sha256) is str and published.raw_sha256 == sha(raw)
            assert type(published.byte_size) is int and published.byte_size == len(raw)
        # The whole IO publisher, descriptor, parent and stack exited normally.
        assert lifecycle.require_guard(item.profile, exclusive=True) is guard
    assert lifecycle.current_guard() is None
    assert fixture_read_file(item.data / RESERVATION, maximum=32 * 1024)[0] == raw
    after = fixture_source_baseline(item)
    assert after["sql"] == before["sql"] and after["raw"] == before["raw"]
    after_tree = dict(after["topology"])
    assert ("data", RESERVATION) not in before["topology"]
    assert after_tree.pop(("data", RESERVATION))[0] == "file"
    assert after_tree == before["topology"]
    with os.scandir(native(item.data)) as entries:
        names = {entry.name for entry in entries if entry.name.rstrip(" .").casefold().startswith(".reno-personal-authority-")}
    assert names == {FIRST, SECOND, RESERVATION}
    return {"expected_namespace_reservation_sha256": sha(raw)}


@contextmanager
def forward_roles(patch, item, *, phase):
    ledger = NamespaceForwardLedger(patch, item, phase)
    yield ledger


def prepare_complete(item, patch):
    """One explicit setup initializer; the later verify is the sole subject."""
    before = fixture_source_baseline(item)
    with forward_roles(patch, item, phase="complete_setup") as setup_roles:
        setup_result = owned.initialize_namespace3(item.profile, **item.arguments, **item.expected_origin)
        expected = assert_complete_namespace(item, setup_result, approved_native=setup_roles.native_identities())
        setup_roles.assert_complete_protocol(mode="init", ownership="owned")
    assert_source_preserved(item, before, namespace_created=True)
    assert lifecycle.current_guard() is None
    return {name: expected[name.removeprefix("expected_")] for name in (
        "expected_namespace_reservation_sha256", "expected_root_seal_sha256", "expected_header_sha256")}


def invoke_namespace_subject(item, *, mode, ownership, expected, requested=None, callback=None):
    """Six explicit original-signature public entries, exactly one selected call."""
    arguments = {**item.arguments, **item.expected_origin, **expected}
    if ownership == "locked":
        assert type(requested) is deadline.Namespace3Deadline and callback is None
        if mode == "init":
            return locked.initialize_namespace3_locked(item.profile, **arguments, deadline_token=requested)
        if mode == "resume":
            return locked.resume_unstarted_namespace3_locked(item.profile, **arguments, deadline_token=requested)
        assert mode == "verify"
        return locked.verify_namespace3_locked(item.profile, **arguments, deadline_token=requested)
    assert ownership == "owned" and requested is None
    if mode == "init":
        return owned.initialize_namespace3(item.profile, **arguments, check_cancelled=callback)
    if mode == "resume":
        return owned.resume_unstarted_namespace3(item.profile, **arguments, check_cancelled=callback)
    assert mode == "verify"
    return owned.verify_namespace3(item.profile, **arguments, check_cancelled=callback)


POSITIVE_CASES = (
    ("R839-P-L-INIT-WAL", "locked", "init", "wal"),
    ("R839-P-L-INIT-ABSENT", "locked", "init", "absent"),
    ("R839-P-L-INIT-PREPARED", "locked", "init", "prepared"),
    ("R839-P-L-RESUME-WAL", "locked", "resume", "wal"),
    ("R839-P-L-RESUME-ABSENT", "locked", "resume", "absent"),
    ("R839-P-L-RESUME-PREPARED", "locked", "resume", "prepared"),
    ("R839-P-L-VERIFY-WAL", "locked", "verify", "wal"),
    ("R839-P-L-VERIFY-ABSENT", "locked", "verify", "absent"),
    ("R839-P-L-VERIFY-PREPARED", "locked", "verify", "prepared"),
    ("R839-P-O-INIT-WAL", "owned", "init", "wal"),
    ("R839-P-O-INIT-ABSENT", "owned", "init", "absent"),
    ("R839-P-O-INIT-PREPARED", "owned", "init", "prepared"),
    ("R839-P-O-RESUME-WAL", "owned", "resume", "wal"),
    ("R839-P-O-RESUME-ABSENT", "owned", "resume", "absent"),
    ("R839-P-O-RESUME-PREPARED", "owned", "resume", "prepared"),
    ("R839-P-O-VERIFY-WAL", "owned", "verify", "wal"),
    ("R839-P-O-VERIFY-ABSENT", "owned", "verify", "absent"),
    ("R839-P-O-VERIFY-PREPARED", "owned", "verify", "prepared"),
)


@pytest.mark.parametrize("case_id,ownership,mode,fixture_mode", POSITIVE_CASES, ids=[row[0] for row in POSITIVE_CASES])
def test_actual_namespace_positive_matrix(namespace_case, monkeypatch, case_id, ownership, mode, fixture_mode):
    with namespace_case(fixture_mode) as item:
        expected = {}
        if mode == "resume":
            expected = prepare_r_only(item)
        elif mode == "verify":
            with monkeypatch.context() as setup_patch:
                expected = prepare_complete(item, setup_patch)
        # All origin and namespace setup invocations have completed before this
        # baseline; none is counted as this one selected namespace subject.
        before = fixture_source_baseline(item)
        with monkeypatch.context() as subject_patch, forward_roles(subject_patch, item, phase="subject") as roles:
            if ownership == "locked":
                with lifecycle.exclusive(item.profile) as guard:
                    requested = requested_namespace_token()
                    result = invoke_namespace_subject(item, mode=mode, ownership=ownership,
                        expected=expected, requested=requested)
                    assert lifecycle.require_guard(item.profile, exclusive=True) is guard
                    assert_complete_namespace(item, result, approved_native=roles.native_identities())
                    roles.assert_complete_protocol(mode=mode, ownership=ownership)
                    assert_source_preserved(item, before, namespace_created=mode != "verify")
                assert lifecycle.current_guard() is None
            else:
                callback_calls = []
                def callback():
                    callback_calls.append("sole_user_poll")
                result = invoke_namespace_subject(item, mode=mode, ownership=ownership,
                    expected=expected, callback=callback)
                assert callback_calls == ["sole_user_poll"]
                assert lifecycle.current_guard() is None
                assert_complete_namespace(item, result, approved_native=roles.native_identities())
                roles.assert_complete_protocol(mode=mode, ownership=ownership)
                assert_source_preserved(item, before, namespace_created=mode != "verify")
        # The item.rw remains open until this namespace_case yield is left.


class NamespaceForwardLedger:
    """Fixed boundary returns only; no diagnostic native calls or introspection."""
    def __init__(self, patch, item, phase):
        self.patch, self.item, self.phase = patch, item, phase
        self.events, self.alias_stack = [], []
        self.alias_calls, self.alias_returns = Counter(), Counter()
        self.states, self.factories, self.receipts, self.context_receipts = {}, {}, {}, {}
        self.stack_receipts, self.stack_entries, self.stack_exits = {}, defaultdict(list), defaultdict(list)
        self.raw_enters, self.raw_exits, self.exit_attempts = Counter(), Counter(), Counter()
        self.directory_roles, self.directory_owners, self.native_roles = {}, {}, defaultdict(list)
        self.publisher_contexts, self.publishers, self.held_roles = [], [], {}
        self.owner_contexts, self.scans, self.active_scans = [], defaultdict(list), []
        self.relative_open_roles, self.native_count_out, self.native_counts, self.charge_events = [], {}, defaultdict(dict), []
        self.pending_pointer_roles, self.slot_roles, self.closing_slots = {}, {}, []
        self.issuer_contexts, self.physical_tokens, self.node_budgets, self.capture_tokens = [], [], [], []
        self.capture_contexts, self.capture_payloads, self.capture_bytes, self.owned_exits = [], {}, {}, []
        self.known_slots, self.attempted_slots, self.first_returns, self.traceback_returns = {}, Counter(), {}, {}
        self.fault = None
        self._install_selected()
        self._install_protocol()

    def event(self, role, *values):
        self.events.append((role, *values))

    def fixed(self, module, name, maker):
        # Each call site below specifies one literal selected target.
        self.patch.setattr(module, name, maker(getattr(module, name)))

    def _install_selected(self):
        for module, name, maker, alias_id, gate in SELECTED_FORWARD_BINDINGS:
            def bind(original, /, _maker=globals()[maker]):
                return _maker(original, self)
            self.fixed(locked if module == "locked" else publication, name, bind)

    def _install_protocol(self):
        ledger = self
        def state_init(original):
            def forward(receiver, exact_restricted_budget, exact_effective_token, /):
                result = original(receiver, exact_restricted_budget, exact_effective_token)
                assert result is None and id(receiver) not in ledger.states
                ledger.states[id(receiver)] = (receiver, exact_restricted_budget, exact_effective_token)
                ledger.event("state", receiver, exact_restricted_budget, exact_effective_token)
                return result
            return forward
        self.fixed(publication._NamespaceState, "__init__", state_init)

        def issued(kind, original):
            def remember(state, receiver):
                ledger.factories[id(receiver)] = (receiver, state, kind)
                ledger.event("factory", kind, receiver, state)
                return receiver
            return remember
        def issue_physical(original):
            def forward(state, path, /):
                ledger.issuer_contexts.append(("physical", state))
                try:
                    return issued("PHYSICAL", original)(state, original(state, path))
                finally:
                    ledger.issuer_contexts.pop()
            return forward
        def issue_node(original):
            def forward(state, exact_data_directory, /):
                ledger.issuer_contexts.append(("node", state))
                try:
                    return issued("NODE", original)(state, original(state, exact_data_directory))
                finally:
                    ledger.issuer_contexts.pop()
            return forward
        def issue_capture(original):
            def forward(state, profile, domain_id, facility_id, init_operation_id, /):
                receiver = original(state, profile, domain_id, facility_id, init_operation_id)
                ledger.capture_contexts.append(receiver)
                return issued("CAPTURE", original)(state, receiver)
            return forward
        self.fixed(publication, "_issue_physical_context", issue_physical)
        self.fixed(publication, "_issue_node_context", issue_node)
        self.fixed(publication, "_issue_capture_context", issue_capture)

        def physical_ctor(original):
            def forward(receiver, path, *, deadline_token, create=False, base=None, parent=None):
                result = original(receiver, path, deadline_token=deadline_token, create=create, base=base, parent=parent)
                if ledger.issuer_contexts and ledger.issuer_contexts[-1][0] == "physical":
                    state = ledger.issuer_contexts[-1][1]
                    ledger.physical_tokens.append((state, deadline_token))
                return result
            return forward
        def node_ctor(original):
            def forward(receiver, directory, budget):
                result = original(receiver, directory, budget)
                if ledger.issuer_contexts and ledger.issuer_contexts[-1][0] == "node":
                    ledger.node_budgets.append((ledger.issuer_contexts[-1][1], budget))
                return result
            return forward
        def capture_factory(original):
            def forward(profile, *, domain_id, facility_id, init_operation_id, deadline_token, check_cancelled=None):
                receiver = original(profile, domain_id=domain_id, facility_id=facility_id,
                    init_operation_id=init_operation_id, deadline_token=deadline_token, check_cancelled=check_cancelled)
                ledger.capture_tokens.append(deadline_token)
                assert check_cancelled is None
                return receiver
            return forward
        self.fixed(publication.directories.DeadlineDirectory, "__init__", physical_ctor)
        self.fixed(publication.nodes.DeadlineNodeDirectory, "__init__", node_ctor)
        self.fixed(publication.capture, "capture_locked", capture_factory)

        def capture_bytes(role, original):
            def forward(state, exact_receiver, /):
                result = original(state, exact_receiver)
                assert type(result) is bytes
                ledger.capture_bytes.setdefault(id(exact_receiver), {})[role] = result
                return result
            return forward
        self.fixed(locked, "_ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r",
                   lambda original: capture_bytes("intent", original))
        self.fixed(locked, "_ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field",
                   lambda original: capture_bytes("evidence", original))

        def claim(original):
            def forward(state, receiver, fixed_kind):
                receipt = original(state, receiver, fixed_kind)
                assert id(receipt) not in ledger.receipts
                ledger.receipts[id(receipt)] = (receipt, state, receiver, fixed_kind)
                ledger.context_receipts[id(receiver)] = receipt
                ledger.event("claim", receipt, state, receiver, fixed_kind)
                return receipt
            return forward
        self.fixed(publication, "_claim_receipt", claim)

        def stack_enter(original):
            def forward(receiver, receipt):
                ledger.stack_receipts[id(receipt)] = receiver
                return original(receiver, receipt)
            return forward
        self.fixed(publication._KnownStack, "_enter", stack_enter)

        def raw_enter(original):
            def forward(receiver):
                result = original(receiver)
                receipt = ledger.context_receipts.get(id(receiver))
                if receipt is not None:
                    ledger.raw_enters[id(receipt)] += 1
                    ledger.event("normal_enter", receipt, result)
                    if id(receipt) in ledger.stack_receipts:
                        ledger.stack_entries[id(ledger.stack_receipts[id(receipt)])].append(receipt)
                    if ledger.receipts[id(receipt)][3] == "CAPTURE":
                        ledger.capture_payloads[id(receipt)] = result
                    return ledger._after_normal_enter(receipt, result)
                return result
            return forward
        def raw_exit(original):
            def forward(receiver, kind, primary, tb):
                receipt = ledger.context_receipts.get(id(receiver))
                if receipt is not None:
                    ledger.raw_exits[id(receipt)] += 1
                    ledger.event("raw_exit_start", receipt, primary, tb)
                result = original(receiver, kind, primary, tb)
                if receipt is not None:
                    ledger.event("raw_exit_return", receipt, result)
                    ledger._after_normal_exit(receipt, result)
                return result
            return forward
        for target in (publication._IOContext, publication._IOOwner, publication._KnownStack,
                       publication.directories.DeadlineDirectory, publication.nodes.DeadlineHeldFile,
                       publication._GeneratorContextManager):
            self.fixed(target, "__enter__", raw_enter)
            self.fixed(target, "__exit__", raw_exit)

        def typed_enter(original):
            def forward(state, exact_known_receipt, /):
                result = original(state, exact_known_receipt)
                # SCANDIR is an immutable C iterator; its fixed enter/close is
                # witnessed at this alias, never replaced by a proxy iterator.
                if ledger.receipts[id(exact_known_receipt)][3] == "SCANDIR":
                    ledger.raw_enters[id(exact_known_receipt)] += 1
                    if id(exact_known_receipt) in ledger.stack_receipts:
                        ledger.stack_entries[id(ledger.stack_receipts[id(exact_known_receipt)])].append(exact_known_receipt)
                ledger.event("typed_enter_return", exact_known_receipt, result)
                return result
            return forward
        def typed_exit(original):
            def forward(state, exact_known_receipt, /):
                known = ledger.receipts.get(id(exact_known_receipt))
                raw_done = ledger.raw_exits[id(exact_known_receipt)] if known is not None and known[3] != "SCANDIR" else ledger.exit_attempts[id(exact_known_receipt)]
                if known is not None and ledger.raw_enters[id(exact_known_receipt)] and not raw_done:
                    ledger.exit_attempts[id(exact_known_receipt)] += 1
                    if id(exact_known_receipt) in ledger.stack_receipts:
                        stack = ledger.stack_receipts[id(exact_known_receipt)]
                        ledger.stack_exits[id(stack)].append(exact_known_receipt)
                    ledger.event("typed_exit_start", exact_known_receipt)
                result = original(state, exact_known_receipt)
                ledger.event("typed_exit_return", exact_known_receipt, result)
                return result
            return forward
        self.fixed(publication, "_enter_fixed_receipt", typed_enter)
        self.fixed(publication, "_exit_known_receipt", typed_exit)

        def new_directory(original):
            def forward(state, path, owner, parent, name, /):
                receiver = original(state, path, owner, parent, name)
                role = "parent" if path == ledger.item.data else "root" if path == ledger.item.data / ROOT_NAME else None
                assert role is not None
                ledger.directory_roles[id(receiver)] = (receiver, role)
                ledger.directory_owners[id(receiver)] = owner
                return receiver
            return forward
        def native_identity(original):
            def forward(state, exact_receiver, /):
                result = original(state, exact_receiver)
                known = ledger.directory_roles.get(id(exact_receiver))
                if known is not None:
                    _assert_native_identity(result)
                    ledger.native_roles[known[1]].append((result["volume_serial"], result["file_index"]))
                return result
            return forward
        self.fixed(publication, "_new_directory", new_directory)
        self.fixed(publication, "_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read", native_identity)
        self.fixed(locked, "_ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read", native_identity)

        def register_owner(original):
            def forward(state, exact_known_slot, /):
                owner = original(state, exact_known_slot)
                ledger.known_slots[id(exact_known_slot)] = (exact_known_slot, owner)
                ledger.event("owner_registered", exact_known_slot, owner)
                return owner
            return forward
        def attempted(original):
            def forward(state, exact_known_slot, /):
                ledger.attempted_slots[id(exact_known_slot)] += 1
                ledger.event("owner_close_attempt", exact_known_slot)
                return original(state, exact_known_slot)
            return forward
        self.fixed(publication, "_register_known_return", register_owner)
        self.fixed(publication, "_mark_attempted", attempted)

        def issued_owner(original):
            def forward(state, exact_known_slot, /):
                owner = original(state, exact_known_slot)
                ledger.known_slots[id(exact_known_slot)] = (exact_known_slot, owner)
                ledger.event("owner_issued_before_post", exact_known_slot, owner)
                if ledger.relative_open_roles:
                    actual_state, name, directory, create = ledger.relative_open_roles[-1]
                    expected_stage = RESERVATION + "." + ledger.item.arguments["init_operation_id"] + ".stage"
                    if actual_state is state and name == expected_stage and directory is False and create is True:
                        ledger.slot_roles[id(exact_known_slot)] = "publication_stage_creator"
                pending = ledger.pending_pointer_roles.pop(id(state), None)
                if pending is not None:
                    ledger.slot_roles[id(exact_known_slot)] = pending
                if ledger.fault is not None:
                    ledger.fault.after_ownership(ledger, state, exact_known_slot, owner)
                return owner
            return forward
        self.fixed(publication, "_issue_handle_owner_receipt", issued_owner)
        self.fixed(publication, "_issue_pointer_owner_receipt", issued_owner)

        def sid_text_allocation(original):
            def forward(state, exact_cast_view, /):
                result = original(state, exact_cast_view)
                if ledger.publisher_contexts:
                    assert id(state) not in ledger.pending_pointer_roles
                    ledger.pending_pointer_roles[id(state)] = "private_descriptor_sid_text_allocation"
                    ledger.event("sid_text_allocation_operand_return", state, result)
                return result
            return forward
        self.fixed(publication, "_ct_get_sid_text_allocation_pointer_l283c43", sid_text_allocation)
        def closing(original):
            def forward(state, exact_known_slot, /):
                ledger.closing_slots.append(exact_known_slot)
                try:
                    return original(state, exact_known_slot)
                finally:
                    assert ledger.closing_slots.pop() is exact_known_slot
            return forward
        self.fixed(publication, "_close_known_slot", closing)

        def count_byref(original):
            def forward(state, exact_count, /):
                result = original(state, exact_count)
                ledger.native_count_out[id(result)] = (result, exact_count)
                return result
            return forward
        self.fixed(publication, "_ct_byref_read_chunk_count_l517c52", count_byref)
        self.fixed(publication, "_ct_byref_write_chunk_count_l529c53", count_byref)
        def count_read(role, original):
            def forward(state, exact_receiver, /):
                result = original(state, exact_receiver)
                ledger.native_counts[id(exact_receiver)][role] = result
                ledger.event("native_count", exact_receiver, role, result)
                return result
            return forward
        for kind in ("read", "write"):
            for group in ("g1_validate", "g2_charge_operand", "g3_return_reread"):
                name = "_get_exact_w_dword_actual_" + kind + "_count_value_" + kind + "_count_" + group
                self.fixed(publication, name, lambda original, _role=kind + "_" + group: count_read(_role, original))
        def charge(original):
            def forward(state, actual, /):
                ledger.charge_events.append((state, actual))
                ledger.event("charge", state, actual)
                result = original(state, actual)
                ledger.event("charge_return", state, actual, result)
                return result
            return forward
        self.fixed(publication, "_budget_charge", charge)

        def publisher(original):
            def forward(profile, directory, name, stage_name, raw, state):
                record = {"name": name, "raw": raw, "creator": None, "held": [], "returned": False}
                ledger.publisher_contexts.append(record)
                ledger.publishers.append(record)
                ledger.event("publisher_start", name)
                try:
                    result = original(profile, directory, name, stage_name, raw, state)
                    record["returned"] = True
                    ledger.event("publisher_return", name)
                    return result
                finally:
                    assert ledger.publisher_contexts.pop() is record
            return forward
        def relative_open(original):
            def forward(state, parent, name, /, *, directory, create, descriptor, reader_share):
                ledger.relative_open_roles.append((state, name, directory, create))
                try:
                    owner = original(state, parent, name, directory=directory, create=create,
                        descriptor=descriptor, reader_share=reader_share)
                finally:
                    ledger.relative_open_roles.pop()
                ledger.event("relative_open_return", owner, name, directory, create, reader_share)
                if ledger.publisher_contexts and create and not directory:
                    record = ledger.publisher_contexts[-1]
                    assert name == record["name"] + "." + ledger.item.arguments["init_operation_id"] + ".stage"
                    record["creator"] = owner
                return owner
            return forward
        def new_held(original):
            def forward(state, directory, name, owner, maximum, /):
                ledger.owner_contexts.append((owner, name, "reader"))
                try:
                    receiver = original(state, directory, name, owner, maximum)
                    ledger.held_roles[id(receiver)] = (receiver, owner, name)
                    if ledger.publisher_contexts:
                        ledger.publisher_contexts[-1]["held"].append(receiver)
                    return receiver
                finally:
                    ledger.owner_contexts.pop()
            return forward
        def held_bytes(original):
            def forward(state, exact_receiver, /):
                receiver, owner, name = ledger.held_roles[id(exact_receiver)]
                ledger.owner_contexts.append((owner, name, "reader"))
                try:
                    return original(state, exact_receiver)
                finally:
                    ledger.owner_contexts.pop()
            return forward
        def read_exact(original):
            def forward(state, handle, byte_size, maximum, /):
                if ledger.owner_contexts:
                    owner, name, role = ledger.owner_contexts[-1]
                else:
                    record = ledger.publisher_contexts[-1]
                    owner, name, role = record["creator"], record["name"], "creator"
                assert owner is not None
                scan = {"owner": owner, "name": name, "role": role, "handle": handle,
                        "parts": [], "eofs": 0, "returned": False}
                ledger.scans[id(owner)].append(scan)
                ledger.active_scans.append(scan)
                try:
                    result = original(state, handle, byte_size, maximum)
                    scan["returned"] = True
                    assert result == b"".join(scan["parts"])
                    return result
                finally:
                    assert ledger.active_scans.pop() is scan
            return forward
        def read_chunk(original):
            def forward(state, handle, requested, /):
                result = original(state, handle, requested)
                scan = ledger.active_scans[-1]
                assert type(result) is bytes and scan["handle"] == handle
                scan["parts"].append(result)
                scan["eofs"] += int(result == b"")
                return result
            return forward
        self.fixed(publication, "publish_new_locked", publisher)
        self.fixed(publication, "_expand_relative_open", relative_open)
        self.fixed(publication, "_new_heldcontrol", new_held)
        self.fixed(publication, "_ioheldcontrol_bytes", held_bytes)
        self.fixed(publication, "_expand_read_exact", read_exact)
        self.fixed(publication, "_expand_read_chunk", read_chunk)

        def owner_exit(original):
            def forward(context, kind, primary, tb):
                result = original(context, kind, primary, tb)
                ledger.owned_exits.append((context, primary, result, len(ledger.events)))
                ledger.event("owned_ex_return", context, result)
                return result
            return forward
        self.fixed(owned, "_exclusive_exit", owner_exit)

    def before_selected(self, alias):
        if self.fault is not None:
            self.fault.before_alias(self, alias)

    def after_selected(self, alias, result):
        return result if self.fault is None else self.fault.after_alias(self, alias, result)

    def _after_normal_enter(self, receipt, result):
        return result if self.fault is None else self.fault.after_enter(self, receipt, result)

    def _after_normal_exit(self, receipt, result):
        if self.fault is not None:
            self.fault.after_exit(self, receipt, result)

    def native_identities(self):
        result = {}
        for role in ("parent", "root"):
            values = self.native_roles[role]
            assert values and all(value == values[0] for value in values)
            result[role] = dict(zip(("volume_serial", "file_index"), values[0]))
        return result

    def assert_known_once(self):
        for receipt_id, count in self.raw_enters.items():
            assert count == 1
            assert self.exit_attempts[receipt_id] == 1
            kind = self.receipts[receipt_id][3]
            if kind != "SCANDIR":
                assert self.raw_exits[receipt_id] == 1
        for stack_id, entered in self.stack_entries.items():
            assert self.stack_exits[stack_id] == list(reversed(entered)), "mixed receipt LIFO changed"
        assert all(count == 1 for count in self.attempted_slots.values())
        assert set(self.known_slots) <= set(self.attempted_slots), "known owner lacked sole attempt"

    def assert_complete_protocol(self, *, mode, ownership):
        assert len(self.states) == 1
        state, budget, token = next(iter(self.states.values()))
        assert type(budget) is deadline._RestrictedBudget and type(token) is deadline.Namespace3Deadline
        assert len(self.physical_tokens) == 4 and all(s is state and t is token for s, t in self.physical_tokens)
        assert self.node_budgets == [(state, budget)]
        count = 1 if mode == "verify" else 2
        assert len(self.capture_contexts) == len(self.capture_tokens) == count
        assert all(t is token for t in self.capture_tokens)
        for receipt_id, (_, _, _, kind) in self.receipts.items():
            if kind == "CAPTURE":
                assert self.raw_enters[receipt_id] == self.raw_exits[receipt_id] == 1
                observation = self.capture_payloads[receipt_id]
                raw = self.capture_bytes[id(observation)]
                second = decoded(self.item.second)
                assert set(raw) == {"intent", "evidence"}
                for role in ("intent", "evidence"):
                    assert raw[role] == base64.b64decode(second["origin_" + role + "_base64"], validate=True)
        self.assert_known_once()
        expected_names = [] if mode == "verify" else [RESERVATION, SEAL, HEADER] if mode == "init" else [SEAL, HEADER]
        assert [p["name"] for p in self.publishers] == expected_names
        for record in self.publishers:
            assert record["returned"] and record["creator"] is not None and len(record["held"]) == 2
            creator_scans = self.scans[id(record["creator"])]
            assert len(creator_scans) == 2 and all(s["returned"] and s["eofs"] == 1 and b"".join(s["parts"]) == record["raw"] for s in creator_scans)
            readers = [v for v in self.held_roles.values() if v[2] == record["name"]]
            assert len(readers) >= 3 and len({id(v[1]) for v in readers}) == len(readers)
            for _, owner, name in readers:
                scans = self.scans[id(owner)]
                assert len(scans) >= 2 and all(s["returned"] and s["eofs"] == 1 and b"".join(s["parts"]) == record["raw"] for s in scans)
        if mode == "verify":
            for name in ("_native_writefile", "_native_flushfilebuffers", "_native_ntsetinformationfile"):
                assert self.alias_calls["reno.personal_authority_namespace3_io_deadline." + name] == 0
            assert all(event[4] is False for event in self.events if event[0] == "relative_open_return")
        if ownership == "owned":
            assert len(self.owned_exits) == 1 and self.owned_exits[0][1] is None and self.owned_exits[0][2] is False
            assert all(index < self.owned_exits[0][3] for index, event in enumerate(self.events) if event[0] == "typed_exit_return")
        else:
            assert not self.owned_exits


def _make_selected_forward_0(original, ledger):
    def forward(state, exact_directory_list, exact_directory, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_append_directory')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_append_directory'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_append_directory')
            result = original(state, exact_directory_list, exact_directory)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_append_directory'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_append_directory', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_append_directory'
    return forward

def _make_selected_forward_1(original, ledger):
    def forward(state, profile, domain_id, facility_id, init_operation_id, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_capture_factory')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_capture_factory'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_capture_factory')
            result = original(state, profile, domain_id, facility_id, init_operation_id)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_capture_factory'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_capture_factory', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_capture_factory'
    return forward

def _make_selected_forward_2(original, ledger):
    def forward(state, first, second, reservation, seal, header, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_complete_cross')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_complete_cross'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_complete_cross')
            result = original(state, first, second, reservation, seal, header)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_complete_cross'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_complete_cross', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_complete_cross'
    return forward

def _make_selected_forward_3(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify'
    return forward

def _make_selected_forward_4(original, ledger):
    def forward(state, exact_receiver, maximum, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes')
            result = original(state, exact_receiver, maximum)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes'
    return forward

def _make_selected_forward_5(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify'
    return forward

def _make_selected_forward_6(original, ledger):
    def forward(state, exact_second_record, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob')
            result = original(state, exact_second_record)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob'
    return forward

def _make_selected_forward_7(original, ledger):
    def forward(state, profile, domain, facility, operation, expected_first, expected_second, expected_reservation, expected_seal, expected_header, mode, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_arguments')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_arguments'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_arguments')
            result = original(state, profile, domain, facility, operation, expected_first, expected_second, expected_reservation, expected_seal, expected_header, mode)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_arguments'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_arguments', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_arguments'
    return forward

def _make_selected_forward_8(original, ledger):
    def forward(state, kind, first, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_common')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_common'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_common')
            result = original(state, kind, first)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_common'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_common', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_common'
    return forward

def _make_selected_forward_9(original, ledger):
    def forward(state, profile, parent, first_raw, second_raw, reservation_raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_create')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_create'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_create')
            result = original(state, profile, parent, first_raw, second_raw, reservation_raw)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_create'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_create', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_create'
    return forward

def _make_selected_forward_10(original, ledger):
    def forward(state, profile, parent, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_create_root_locked')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_create_root_locked'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_create_root_locked')
            result = original(state, profile, parent)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_create_root_locked'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_create_root_locked', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_create_root_locked'
    return forward

def _make_selected_forward_11(original, ledger):
    def forward(state, raw, expected, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_expect_sha')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_expect_sha'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_expect_sha')
            result = original(state, raw, expected)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_expect_sha'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_expect_sha', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_expect_sha'
    return forward

def _make_selected_forward_12(original, ledger):
    def forward(state, profile, domain, facility, operation, second, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_fresh_origin')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_fresh_origin'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_fresh_origin')
            result = original(state, profile, domain, facility, operation, second)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_fresh_origin'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_fresh_origin', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_fresh_origin'
    return forward

def _make_selected_forward_13(original, ledger):
    def forward(state, directory, /, *, outer):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_names')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_names'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_names')
            result = original(state, directory, outer=outer)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_names'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_names', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_names'
    return forward

def _make_selected_forward_14(original, ledger):
    def forward(state, directory, expected, /, *, outer):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_need_names')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_need_names'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_need_names')
            result = original(state, directory, expected, outer=outer)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_need_names'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_need_names', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_need_names'
    return forward

def _make_selected_forward_15(original, ledger):
    def forward(state, profile, /, *, write):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_open_parent_locked')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_open_parent_locked'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_open_parent_locked')
            result = original(state, profile, write=write)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_open_parent_locked'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_open_parent_locked', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_open_parent_locked'
    return forward

def _make_selected_forward_16(original, ledger):
    def forward(state, profile, parent, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_open_root_locked')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_open_root_locked'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_open_root_locked')
            result = original(state, profile, parent)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_open_root_locked'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_open_root_locked', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_open_root_locked'
    return forward

def _make_selected_forward_17(original, ledger):
    def forward(state, profile, stack, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_physical')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_physical'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_physical')
            result = original(state, profile, stack)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_physical'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_physical', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_physical'
    return forward

def _make_selected_forward_18(original, ledger):
    def forward(state, profile, directory, name, operation, raw, stack, maximum, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_publish')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_publish'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_publish')
            result = original(state, profile, directory, name, operation, raw, stack, maximum)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_publish'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_publish', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_publish'
    return forward

def _make_selected_forward_19(original, ledger):
    def forward(state, profile, directory, name, stage_name, raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_publish_new_locked')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_publish_new_locked'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_publish_new_locked')
            result = original(state, profile, directory, name, stage_name, raw)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_publish_new_locked'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_publish_new_locked', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_publish_new_locked'
    return forward

def _make_selected_forward_20(original, ledger):
    def forward(state, profile, parent, name, maximum, stack, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control')
            result = original(state, profile, parent, name, maximum, stack)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control'
    return forward

def _make_selected_forward_21(original, ledger):
    def forward(state, profile, directory, name, maximum, /, *, creator_live):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control_locked')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control_locked'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control_locked')
            result = original(state, profile, directory, name, maximum, creator_live=creator_live)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control_locked'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control_locked', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_read_control_locked'
    return forward

def _make_selected_forward_22(original, ledger):
    def forward(state, first_raw, second_raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_reservation')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_reservation'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_reservation')
            result = original(state, first_raw, second_raw)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_reservation'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_reservation', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_reservation'
    return forward

def _make_selected_forward_23(original, ledger):
    def forward(state, profile, paths, guard, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_same_guard')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_same_guard'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_same_guard')
            result = original(state, profile, paths, guard)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_same_guard'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_same_guard', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_same_guard'
    return forward

def _make_selected_forward_24(original, ledger):
    def forward(state, profile, first_raw, second_raw, reservation_raw, seal_sha, header_sha, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_complete')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_complete'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_complete')
            result = original(state, profile, first_raw, second_raw, reservation_raw, seal_sha, header_sha)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_complete'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_complete', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_complete'
    return forward

def _make_selected_forward_25(original, ledger):
    def forward(state, profile, paths, guard, directories, node, physical, evidence, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_physical')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_physical'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_physical')
            result = original(state, profile, paths, guard, directories, node, physical, evidence)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_physical'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_physical', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_expand_verify_physical'
    return forward

def _make_selected_forward_26(original, ledger):
    def forward(state, fixed_failure_role, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_fixed_failure')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_fixed_failure'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_fixed_failure')
            result = original(state, fixed_failure_role)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_fixed_failure'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_fixed_failure', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_fixed_failure'
    return forward

def _make_selected_forward_27(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read'
    return forward

def _make_selected_forward_28(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_29(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_30(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read'
    return forward

def _make_selected_forward_31(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_32(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_33(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read'
    return forward

def _make_selected_forward_34(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read'
    return forward

def _make_selected_forward_35(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field'
    return forward

def _make_selected_forward_36(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r'
    return forward

def _make_selected_forward_37(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read'
    return forward

def _make_selected_forward_38(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read'
    return forward

def _make_selected_forward_39(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_40(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_41(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read'
    return forward

def _make_selected_forward_42(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read'
    return forward

def _make_selected_forward_43(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read'
    return forward

def _make_selected_forward_44(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_45(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read'
    return forward

def _make_selected_forward_46(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read'
    return forward

def _make_selected_forward_47(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read'
    return forward

def _make_selected_forward_48(original, ledger):
    def forward(state, exact_second_record, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_intent_blob')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_intent_blob'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_intent_blob')
            result = original(state, exact_second_record)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_intent_blob'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_intent_blob', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_intent_blob'
    return forward

def _make_selected_forward_49(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_iodirectory_verify')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_iodirectory_verify'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_iodirectory_verify')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_iodirectory_verify'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_iodirectory_verify', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_iodirectory_verify'
    return forward

def _make_selected_forward_50(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_ioheldcontrol_bytes')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_ioheldcontrol_bytes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_ioheldcontrol_bytes')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_ioheldcontrol_bytes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_ioheldcontrol_bytes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_ioheldcontrol_bytes'
    return forward

def _make_selected_forward_51(original, ledger):
    def forward(state, scope, init_operation_id, domain_id, facility_id, origin_reservation_sha256, origin_observation_sha256, namespace_reservation_sha256, root_seal_sha256, header_sha256, creation_kind, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts')
            result = original(state, scope, init_operation_id, domain_id, facility_id, origin_reservation_sha256, origin_observation_sha256, namespace_reservation_sha256, root_seal_sha256, header_sha256, creation_kind)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts'
    return forward

def _make_selected_forward_52(original, ledger):
    def forward(state, exact_deadline_node, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_evidence')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_evidence'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_evidence')
            result = original(state, exact_deadline_node)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_evidence'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_evidence', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_evidence'
    return forward

def _make_selected_forward_53(original, ledger):
    def forward(state, exact_data_directory, exact_stack, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_factory')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_factory'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_factory')
            result = original(state, exact_data_directory, exact_stack)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_factory'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_factory', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_factory'
    return forward

def _make_selected_forward_54(original, ledger):
    def forward(state, exact_node_raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_id')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_id'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_id')
            result = original(state, exact_node_raw)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_id'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_id', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_id'
    return forward

def _make_selected_forward_55(original, ledger):
    def forward(state, exact_node, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size')
            result = original(state, exact_node)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size'
    return forward

def _make_selected_forward_56(original, ledger):
    def forward(state, exact_node, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity')
            result = original(state, exact_node)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity'
    return forward

def _make_selected_forward_57(original, ledger):
    def forward(state, exact_node, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity')
            result = original(state, exact_node)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity'
    return forward

def _make_selected_forward_58(original, ledger):
    def forward(state, exact_node, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity')
            result = original(state, exact_node)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity'
    return forward

def _make_selected_forward_59(original, ledger):
    def forward(state, exact_node, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256')
            result = original(state, exact_node)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256'
    return forward

def _make_selected_forward_60(original, ledger):
    def forward(state, exact_first_raw, exact_second_raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_origin_cross')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_origin_cross'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_origin_cross')
            result = original(state, exact_first_raw, exact_second_raw)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_origin_cross'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_origin_cross', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_origin_cross'
    return forward

def _make_selected_forward_61(original, ledger):
    def forward(state, exact_bounded_origin_raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_origin_decode')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_origin_decode'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_origin_decode')
            result = original(state, exact_bounded_origin_raw)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_origin_decode'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_origin_decode', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_origin_decode'
    return forward

def _make_selected_forward_62(original, ledger):
    def forward(state, path, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory')
            result = original(state, path)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory'
    return forward

def _make_selected_forward_63(original, ledger):
    def forward(state, exact_deadline_directory, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_physical_identity')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_physical_identity'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_physical_identity')
            result = original(state, exact_deadline_directory)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_physical_identity'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_physical_identity', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_physical_identity'
    return forward

def _make_selected_forward_64(original, ledger):
    def forward(state, exact_directory, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_physical_path')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_physical_path'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_physical_path')
            result = original(state, exact_directory)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_physical_path'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_physical_path', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_physical_path'
    return forward

def _make_selected_forward_65(original, ledger):
    def forward(state, exact_prefix_copy, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy')
            result = original(state, exact_prefix_copy)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy'
    return forward

def _make_selected_forward_66(original, ledger):
    def forward(state, exact_original_name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy')
            result = original(state, exact_original_name)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy'
    return forward

def _make_selected_forward_67(original, ledger):
    def forward(state, exact_prefix_copy, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith')
            result = original(state, exact_prefix_copy)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith'
    return forward

def _make_selected_forward_68(original, ledger):
    def forward(state, exact_profile_path, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_profile_key')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_profile_key'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_profile_key')
            result = original(state, exact_profile_path)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_profile_key'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_profile_key', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_profile_key'
    return forward

def _make_selected_forward_69(original, ledger):
    def forward(state, exact_fixed_record, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_record_encode')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_record_encode'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_record_encode')
            result = original(state, exact_fixed_record)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_record_encode'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_record_encode', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_record_encode'
    return forward

def _make_selected_forward_70(original, ledger):
    def forward(state, first, second, reservation, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross')
            result = original(state, first, second, reservation)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross'
    return forward

def _make_selected_forward_71(original, ledger):
    def forward(state, exact_original_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_scan')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_scan'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_scan')
            result = original(state, exact_original_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_scan'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_scan', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_scan'
    return forward

def _make_selected_forward_72(original, ledger):
    def forward(state, exact_original_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_scan_next')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_scan_next'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_scan_next')
            result = original(state, exact_original_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_scan_next'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_scan_next', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_scan_next'
    return forward

def _make_selected_forward_73(original, ledger):
    def forward(state, exact_set, exact_name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_set_add_name')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_set_add_name'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_set_add_name')
            result = original(state, exact_set, exact_name)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_set_add_name'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_set_add_name', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_set_add_name'
    return forward

def _make_selected_forward_74(original, ledger):
    def forward(state, canonical_sha, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_sha')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_sha'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_sha')
            result = original(state, canonical_sha)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_sha'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_sha', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_sha'
    return forward

def _make_selected_forward_75(original, ledger):
    def forward(state, canonical_uuid, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_effect_deadline._ns_uuid')
        ledger.alias_calls['reno.personal_authority_namespace3_effect_deadline._ns_uuid'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_effect_deadline._ns_uuid')
            result = original(state, canonical_uuid)
            ledger.alias_returns['reno.personal_authority_namespace3_effect_deadline._ns_uuid'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_effect_deadline._ns_uuid', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_effect_deadline._ns_uuid'
    return forward

def _make_selected_forward_76(original, ledger):
    def forward(state, exact_parts_list, exact_chunk, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._append_bytes')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._append_bytes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._append_bytes')
            result = original(state, exact_parts_list, exact_chunk)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._append_bytes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._append_bytes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._append_bytes'
    return forward

def _make_selected_forward_77(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._budget_before_next')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._budget_before_next'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._budget_before_next')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._budget_before_next'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._budget_before_next', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._budget_before_next'
    return forward

def _make_selected_forward_78(original, ledger):
    def forward(state, actual, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._budget_charge')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._budget_charge'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._budget_charge')
            result = original(state, actual)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._budget_charge'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._budget_charge', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._budget_charge'
    return forward

def _make_selected_forward_79(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._budget_clock_only')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._budget_clock_only'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._budget_clock_only')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._budget_clock_only'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._budget_clock_only', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._budget_clock_only'
    return forward

def _make_selected_forward_80(original, ledger):
    def forward(state, original_name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._budget_name')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._budget_name'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._budget_name')
            result = original(state, original_name)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._budget_name'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._budget_name', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._budget_name'
    return forward

def _make_selected_forward_81(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._budget_poll')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._budget_poll'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._budget_poll')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._budget_poll'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._budget_poll', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._budget_poll'
    return forward

def _make_selected_forward_82(original, ledger):
    def forward(state, requested, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._budget_request')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._budget_request'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._budget_request')
            result = original(state, requested)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._budget_request'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._budget_request', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._budget_request'
    return forward

def _make_selected_forward_83(original, ledger):
    def forward(state, original_primary, fixed_note_role, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._cleanup_note')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._cleanup_note'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._cleanup_note')
            result = original(state, original_primary, fixed_note_role)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._cleanup_note'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._cleanup_note', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._cleanup_note'
    return forward

def _make_selected_forward_84(original, ledger):
    def forward(state, exact_known_slot, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._close_known_slot')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._close_known_slot'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._close_known_slot')
            result = original(state, exact_known_slot)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._close_known_slot'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._close_known_slot', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._close_known_slot'
    return forward

def _make_selected_forward_85(original, ledger):
    def forward(state, exact_fixed_mapping, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._copy_dict')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._copy_dict'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._copy_dict')
            result = original(state, exact_fixed_mapping)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._copy_dict'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._copy_dict', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._copy_dict'
    return forward

def _make_selected_forward_86(original, ledger):
    def forward():
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_bootstrap_pointer_UnicodeString_l108c29')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_bootstrap_pointer_UnicodeString_l108c29'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_bootstrap_pointer_UnicodeString_l108c29')
            result = original()
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_bootstrap_pointer_UnicodeString_l108c29'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_bootstrap_pointer_UnicodeString_l108c29', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_bootstrap_pointer_UnicodeString_l108c29'
    return forward

def _make_selected_forward_87(original, ledger):
    def forward(state, exact_value, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54')
            result = original(state, exact_value)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54'
    return forward

def _make_selected_forward_88(original, ledger):
    def forward(state, exact_descriptor, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50')
            result = original(state, exact_descriptor)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50'
    return forward

def _make_selected_forward_89(original, ledger):
    def forward(state, exact_text, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55')
            result = original(state, exact_text)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55'
    return forward

def _make_selected_forward_90(original, ledger):
    def forward(state, exact_status, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54')
            result = original(state, exact_status)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54'
    return forward

def _make_selected_forward_91(original, ledger):
    def forward(state, exact_value, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76')
            result = original(state, exact_value)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76'
    return forward

def _make_selected_forward_92(original, ledger):
    def forward(state, exact_count, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52')
            result = original(state, exact_count)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52'
    return forward

def _make_selected_forward_93(original, ledger):
    def forward(state, exact_attributes, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63')
            result = original(state, exact_attributes)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63'
    return forward

def _make_selected_forward_94(original, ledger):
    def forward(state, exact_handle, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33')
            result = original(state, exact_handle)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33'
    return forward

def _make_selected_forward_95(original, ledger):
    def forward(state, exact_status, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89')
            result = original(state, exact_status)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89'
    return forward

def _make_selected_forward_96(original, ledger):
    def forward(state, exact_control, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68')
            result = original(state, exact_control)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68'
    return forward

def _make_selected_forward_97(original, ledger):
    def forward(state, exact_dacl, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39')
            result = original(state, exact_dacl)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39'
    return forward

def _make_selected_forward_98(original, ledger):
    def forward(state, exact_descriptor, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65')
            result = original(state, exact_descriptor)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65'
    return forward

def _make_selected_forward_99(original, ledger):
    def forward(state, exact_owner, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55')
            result = original(state, exact_owner)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55'
    return forward

def _make_selected_forward_100(original, ledger):
    def forward(state, exact_pointer, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51')
            result = original(state, exact_pointer)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51'
    return forward

def _make_selected_forward_101(original, ledger):
    def forward(state, exact_revision, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91')
            result = original(state, exact_revision)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91'
    return forward

def _make_selected_forward_102(original, ledger):
    def forward(state, exact_size, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51')
            result = original(state, exact_size)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51'
    return forward

def _make_selected_forward_103(original, ledger):
    def forward(state, exact_sid, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54')
            result = original(state, exact_sid)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54'
    return forward

def _make_selected_forward_104(original, ledger):
    def forward(state, exact_length, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65')
            result = original(state, exact_length)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65'
    return forward

def _make_selected_forward_105(original, ledger):
    def forward(state, exact_length, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74')
            result = original(state, exact_length)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74'
    return forward

def _make_selected_forward_106(original, ledger):
    def forward(state, exact_token, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48')
            result = original(state, exact_token)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48'
    return forward

def _make_selected_forward_107(original, ledger):
    def forward(state, exact_count, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53')
            result = original(state, exact_count)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53'
    return forward

def _make_selected_forward_108(original, ledger):
    def forward(state, exact_ace_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22')
            result = original(state, exact_ace_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22'
    return forward

def _make_selected_forward_109(original, ledger):
    def forward(state, exact_name_buffer, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61')
            result = original(state, exact_name_buffer)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61'
    return forward

def _make_selected_forward_110(original, ledger):
    def forward(state, exact_text_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_cast_sid_text_allocation_pointer_l283c43')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_cast_sid_text_allocation_pointer_l283c43'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_sid_text_allocation_pointer_l283c43')
            result = original(state, exact_text_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_cast_sid_text_allocation_pointer_l283c43'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_sid_text_allocation_pointer_l283c43', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_cast_sid_text_allocation_pointer_l283c43'
    return forward

def _make_selected_forward_111(original, ledger):
    def forward(state, exact_buffer, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14')
            result = original(state, exact_buffer)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14'
    return forward

def _make_selected_forward_112(original, ledger):
    def forward(state, exact_cast_view, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22')
            result = original(state, exact_cast_view)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22'
    return forward

def _make_selected_forward_113(original, ledger):
    def forward(state, exact_cast_view, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_get_sid_text_allocation_pointer_l283c43')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_get_sid_text_allocation_pointer_l283c43'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_get_sid_text_allocation_pointer_l283c43')
            result = original(state, exact_cast_view)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_get_sid_text_allocation_pointer_l283c43'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_get_sid_text_allocation_pointer_l283c43', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_get_sid_text_allocation_pointer_l283c43'
    return forward

def _make_selected_forward_114(original, ledger):
    def forward(state, exact_cast_view, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14')
            result = original(state, exact_cast_view)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14'
    return forward

def _make_selected_forward_115(original, ledger):
    def forward(state, exact_unicode, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34')
            result = original(state, exact_unicode)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34'
    return forward

def _make_selected_forward_116(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43'
    return forward

def _make_selected_forward_117(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34'
    return forward

def _make_selected_forward_118(original, ledger):
    def forward(state, exact_acl_size, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71')
            result = original(state, exact_acl_size)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71'
    return forward

def _make_selected_forward_119(original, ledger):
    def forward(state, exact_rename_info, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97')
            result = original(state, exact_rename_info)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97'
    return forward

def _make_selected_forward_120(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15'
    return forward

def _make_selected_forward_121(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35'
    return forward

def _make_selected_forward_122(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43'
    return forward

def _make_selected_forward_123(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13'
    return forward

def _make_selected_forward_124(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31'
    return forward

def _make_selected_forward_125(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13'
    return forward

def _make_selected_forward_126(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13'
    return forward

def _make_selected_forward_127(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ct_supported_sizes')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ct_supported_sizes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ct_supported_sizes')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ct_supported_sizes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ct_supported_sizes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ct_supported_sizes'
    return forward

def _make_selected_forward_128(original, ledger):
    def forward(state, exact_bounded_json, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json')
            result = original(state, exact_bounded_json)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json'
    return forward

def _make_selected_forward_129(original, ledger):
    def forward(state, exact_known_receipt, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._enter_fixed_receipt')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._enter_fixed_receipt'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._enter_fixed_receipt')
            result = original(state, exact_known_receipt)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._enter_fixed_receipt'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._enter_fixed_receipt', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._enter_fixed_receipt'
    return forward

def _make_selected_forward_130(original, ledger):
    def forward(state, exact_fixed_name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._entry_name')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._entry_name'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._entry_name')
            result = original(state, exact_fixed_name)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._entry_name'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._entry_name', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._entry_name'
    return forward

def _make_selected_forward_131(original, ledger):
    def forward(state, exact_known_receipt, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._exit_known_receipt')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._exit_known_receipt'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._exit_known_receipt')
            result = original(state, exact_known_receipt)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._exit_known_receipt'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._exit_known_receipt', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._exit_known_receipt'
    return forward

def _make_selected_forward_132(original, ledger):
    def forward(state, profile, directory, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_check')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_check'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_check')
            result = original(state, profile, directory)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_check'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_check', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_check'
    return forward

def _make_selected_forward_133(original, ledger):
    def forward(state, handle, /, *, directory):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_info')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_info'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_info')
            result = original(state, handle, directory=directory)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_info'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_info', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_info'
    return forward

def _make_selected_forward_134(original, ledger):
    def forward(state, path, /, *, write):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_open_parent_raw')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_open_parent_raw'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_open_parent_raw')
            result = original(state, path, write=write)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_open_parent_raw'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_open_parent_raw', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_open_parent_raw'
    return forward

def _make_selected_forward_135(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_private_descriptor')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_private_descriptor'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_private_descriptor')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_private_descriptor'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_private_descriptor', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_private_descriptor'
    return forward

def _make_selected_forward_136(original, ledger):
    def forward(state, directory, owner, name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_publish_owned')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_publish_owned'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_publish_owned')
            result = original(state, directory, owner, name)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_publish_owned'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_publish_owned', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_publish_owned'
    return forward

def _make_selected_forward_137(original, ledger):
    def forward(state, handle, requested, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_read_chunk')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_read_chunk'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_read_chunk')
            result = original(state, handle, requested)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_read_chunk'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_read_chunk', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_read_chunk'
    return forward

def _make_selected_forward_138(original, ledger):
    def forward(state, profile, directory, name, maximum, /, *, creator_live):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_read_control_locked')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_read_control_locked'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_read_control_locked')
            result = original(state, profile, directory, name, maximum, creator_live=creator_live)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_read_control_locked'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_read_control_locked', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_read_control_locked'
    return forward

def _make_selected_forward_139(original, ledger):
    def forward(state, handle, byte_size, maximum, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_read_exact')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_read_exact'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_read_exact')
            result = original(state, handle, byte_size, maximum)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_read_exact'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_read_exact', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_read_exact'
    return forward

def _make_selected_forward_140(original, ledger):
    def forward(state, parent, name, /, *, directory, create, descriptor, reader_share):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_relative_open')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_relative_open'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_relative_open')
            result = original(state, parent, name, directory=directory, create=create, descriptor=descriptor, reader_share=reader_share)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_relative_open'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_relative_open', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_relative_open'
    return forward

def _make_selected_forward_141(original, ledger):
    def forward(state, handle, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_rewind')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_rewind'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_rewind')
            result = original(state, handle)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_rewind'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_rewind', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_rewind'
    return forward

def _make_selected_forward_142(original, ledger):
    def forward(state, handle, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_security_check')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_security_check'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_security_check')
            result = original(state, handle)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_security_check'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_security_check', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_security_check'
    return forward

def _make_selected_forward_143(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_supported')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_supported'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_supported')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_supported'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_supported', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_supported'
    return forward

def _make_selected_forward_144(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_system_sid')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_system_sid'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_system_sid')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_system_sid'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_system_sid', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_system_sid'
    return forward

def _make_selected_forward_145(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_token_user')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_token_user'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_token_user')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_token_user'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_token_user', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_token_user'
    return forward

def _make_selected_forward_146(original, ledger):
    def forward(state, handle, raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._expand_write_chunk')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._expand_write_chunk'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._expand_write_chunk')
            result = original(state, handle, raw)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._expand_write_chunk'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._expand_write_chunk', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._expand_write_chunk'
    return forward

def _make_selected_forward_147(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._failure_error_raw')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._failure_error_raw'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._failure_error_raw')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._failure_error_raw'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._failure_error_raw', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._failure_error_raw'
    return forward

def _make_selected_forward_148(original, ledger):
    def forward(state, fixed_failure_role, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._fixed_failure')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._fixed_failure'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._fixed_failure')
            result = original(state, fixed_failure_role)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._fixed_failure'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._fixed_failure', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._fixed_failure'
    return forward

def _make_selected_forward_149(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read'
    return forward

def _make_selected_forward_150(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read'
    return forward

def _make_selected_forward_151(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read'
    return forward

def _make_selected_forward_152(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read'
    return forward

def _make_selected_forward_153(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read'
    return forward

def _make_selected_forward_154(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read'
    return forward

def _make_selected_forward_155(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read'
    return forward

def _make_selected_forward_156(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read'
    return forward

def _make_selected_forward_157(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read'
    return forward

def _make_selected_forward_158(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_159(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read'
    return forward

def _make_selected_forward_160(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read'
    return forward

def _make_selected_forward_161(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read'
    return forward

def _make_selected_forward_162(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_163(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read'
    return forward

def _make_selected_forward_164(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read'
    return forward

def _make_selected_forward_165(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_166(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read'
    return forward

def _make_selected_forward_167(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read'
    return forward

def _make_selected_forward_168(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_169(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_170(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read'
    return forward

def _make_selected_forward_171(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read'
    return forward

def _make_selected_forward_172(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read'
    return forward

def _make_selected_forward_173(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read'
    return forward

def _make_selected_forward_174(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read'
    return forward

def _make_selected_forward_175(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read'
    return forward

def _make_selected_forward_176(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_177(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read'
    return forward

def _make_selected_forward_178(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_179(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read'
    return forward

def _make_selected_forward_180(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read'
    return forward

def _make_selected_forward_181(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read'
    return forward

def _make_selected_forward_182(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read'
    return forward

def _make_selected_forward_183(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read'
    return forward

def _make_selected_forward_184(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read'
    return forward

def _make_selected_forward_185(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read'
    return forward

def _make_selected_forward_186(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read'
    return forward

def _make_selected_forward_187(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read'
    return forward

def _make_selected_forward_188(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read'
    return forward

def _make_selected_forward_189(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read'
    return forward

def _make_selected_forward_190(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read'
    return forward

def _make_selected_forward_191(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read'
    return forward

def _make_selected_forward_192(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read'
    return forward

def _make_selected_forward_193(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read'
    return forward

def _make_selected_forward_194(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read'
    return forward

def _make_selected_forward_195(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock'
    return forward

def _make_selected_forward_196(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos'
    return forward

def _make_selected_forward_197(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read'
    return forward

def _make_selected_forward_198(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read'
    return forward

def _make_selected_forward_199(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read'
    return forward

def _make_selected_forward_200(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a'
    return forward

def _make_selected_forward_201(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a'
    return forward

def _make_selected_forward_202(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read'
    return forward

def _make_selected_forward_203(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att'
    return forward

def _make_selected_forward_204(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read'
    return forward

def _make_selected_forward_205(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read'
    return forward

def _make_selected_forward_206(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g1_validate')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g1_validate'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g1_validate')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g1_validate'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g1_validate', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g1_validate'
    return forward

def _make_selected_forward_207(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand'
    return forward

def _make_selected_forward_208(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread'
    return forward

def _make_selected_forward_209(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g1_validate')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g1_validate'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g1_validate')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g1_validate'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g1_validate', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g1_validate'
    return forward

def _make_selected_forward_210(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand'
    return forward

def _make_selected_forward_211(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread'
    return forward

def _make_selected_forward_212(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248'
    return forward

def _make_selected_forward_213(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240'
    return forward

def _make_selected_forward_214(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240'
    return forward

def _make_selected_forward_215(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post'
    return forward

def _make_selected_forward_216(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_217(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad'
    return forward

def _make_selected_forward_218(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_219(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_220(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_221(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_222(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post'
    return forward

def _make_selected_forward_223(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_224(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read'
    return forward

def _make_selected_forward_225(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read'
    return forward

def _make_selected_forward_226(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns'
    return forward

def _make_selected_forward_227(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_dev')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_dev'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_dev')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_dev'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_dev', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_dev'
    return forward

def _make_selected_forward_228(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_ino')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_ino'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_ino')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_ino'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_ino', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_ino'
    return forward

def _make_selected_forward_229(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_mode')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_mode'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_mode')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_mode'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_mode', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_mode'
    return forward

def _make_selected_forward_230(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns'
    return forward

def _make_selected_forward_231(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink'
    return forward

def _make_selected_forward_232(original, ledger):
    def forward(state, exact_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._get_stat_st_size')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._get_stat_st_size'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_size')
            result = original(state, exact_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._get_stat_st_size'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._get_stat_st_size', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._get_stat_st_size'
    return forward

def _make_selected_forward_233(original, ledger):
    def forward(state, exact_profile, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._guard')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._guard'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._guard')
            result = original(state, exact_profile)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._guard'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._guard', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._guard'
    return forward

def _make_selected_forward_234(original, ledger):
    def forward(state, exact_sha256, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._hash_hex')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._hash_hex'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._hash_hex')
            result = original(state, exact_sha256)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._hash_hex'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._hash_hex', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._hash_hex'
    return forward

def _make_selected_forward_235(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._iodirectory_verify')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._iodirectory_verify'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._iodirectory_verify')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._iodirectory_verify'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._iodirectory_verify', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._iodirectory_verify'
    return forward

def _make_selected_forward_236(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ioheldcontrol_bytes')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ioheldcontrol_bytes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ioheldcontrol_bytes')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ioheldcontrol_bytes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ioheldcontrol_bytes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ioheldcontrol_bytes'
    return forward

def _make_selected_forward_237(original, ledger):
    def forward(state, exact_receiver, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._ioheldcontrol_verify')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._ioheldcontrol_verify'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._ioheldcontrol_verify')
            result = original(state, exact_receiver)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._ioheldcontrol_verify'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._ioheldcontrol_verify', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._ioheldcontrol_verify'
    return forward

def _make_selected_forward_238(original, ledger):
    def forward(state, exact_byte_parts, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._join_bytes')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._join_bytes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._join_bytes')
            result = original(state, exact_byte_parts)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._join_bytes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._join_bytes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._join_bytes'
    return forward

def _make_selected_forward_239(original, ledger):
    def forward(state, exact_known_slot, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._mark_attempted')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._mark_attempted'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._mark_attempted')
            result = original(state, exact_known_slot)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._mark_attempted'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._mark_attempted', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._mark_attempted'
    return forward

def _make_selected_forward_240(original, ledger):
    def forward(state, fixed_exact_name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._name_utf16')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._name_utf16'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._name_utf16')
            result = original(state, fixed_exact_name)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._name_utf16'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._name_utf16', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._name_utf16'
    return forward

def _make_selected_forward_241(original, ledger):
    def forward(state, exact_absolute_text, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_absolute')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_absolute'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_absolute')
            result = original(state, exact_absolute_text)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_absolute'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_absolute', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_absolute'
    return forward

def _make_selected_forward_242(original, ledger):
    def forward(state, known_handle, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_closehandle')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_closehandle'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_closehandle')
            result = original(state, known_handle)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_closehandle'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_closehandle', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_closehandle'
    return forward

def _make_selected_forward_243(original, ledger):
    def forward(state, known_sid, exact_text_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_convertsidtostringsidw')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_convertsidtostringsidw'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_convertsidtostringsidw')
            result = original(state, known_sid, exact_text_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_convertsidtostringsidw'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_convertsidtostringsidw', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_convertsidtostringsidw'
    return forward

def _make_selected_forward_244(original, ledger):
    def forward(state, bounded_sddl, revision, exact_pointer_out, length_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_convertstringsecuritydescriptortosecuritydescriptorw')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_convertstringsecuritydescriptortosecuritydescriptorw'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_convertstringsecuritydescriptortosecuritydescriptorw')
            result = original(state, bounded_sddl, revision, exact_pointer_out, length_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_convertstringsecuritydescriptortosecuritydescriptorw'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_convertstringsecuritydescriptortosecuritydescriptorw', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_convertstringsecuritydescriptortosecuritydescriptorw'
    return forward

def _make_selected_forward_245(original, ledger):
    def forward(state, constant_system_sid, exact_pointer_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_convertstringsidtosidw')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_convertstringsidtosidw'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_convertstringsidtosidw')
            result = original(state, constant_system_sid, exact_pointer_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_convertstringsidtosidw'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_convertstringsidtosidw', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_convertstringsidtosidw'
    return forward

def _make_selected_forward_246(original, ledger):
    def forward(state, path, access, share, attributes, disposition, flags, template, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_createfilew')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_createfilew'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_createfilew')
            result = original(state, path, access, share, attributes, disposition, flags, template)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_createfilew'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_createfilew', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_createfilew'
    return forward

def _make_selected_forward_247(original, ledger):
    def forward(state, known_left_sid, known_right_sid, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_equalsid')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_equalsid'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_equalsid')
            result = original(state, known_left_sid, known_right_sid)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_equalsid'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_equalsid', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_equalsid'
    return forward

def _make_selected_forward_248(original, ledger):
    def forward(state, known_handle, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_flushfilebuffers')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_flushfilebuffers'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_flushfilebuffers')
            result = original(state, known_handle)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_flushfilebuffers'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_flushfilebuffers', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_flushfilebuffers'
    return forward

def _make_selected_forward_249(original, ledger):
    def forward(state, known_acl, index, exact_pointer_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getace')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getace'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getace')
            result = original(state, known_acl, index, exact_pointer_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getace'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getace', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getace'
    return forward

def _make_selected_forward_250(original, ledger):
    def forward(state, known_acl, exact_size_out, size, information_class, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getaclinformation')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getaclinformation'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getaclinformation')
            result = original(state, known_acl, exact_size_out, size, information_class)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getaclinformation'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getaclinformation', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getaclinformation'
    return forward

def _make_selected_forward_251(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess'
    return forward

def _make_selected_forward_252(original, ledger):
    def forward(state, known_handle, exact_info_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getfileinformationbyhandle')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getfileinformationbyhandle'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getfileinformationbyhandle')
            result = original(state, known_handle, exact_info_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getfileinformationbyhandle'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getfileinformationbyhandle', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getfileinformationbyhandle'
    return forward

def _make_selected_forward_253(original, ledger):
    def forward(state, known_sid, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getlengthsid')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getlengthsid'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getlengthsid')
            result = original(state, known_sid)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getlengthsid'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getlengthsid', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getlengthsid'
    return forward

def _make_selected_forward_254(original, ledger):
    def forward(state, known_descriptor, control_out, revision_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getsecuritydescriptorcontrol')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getsecuritydescriptorcontrol'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getsecuritydescriptorcontrol')
            result = original(state, known_descriptor, control_out, revision_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getsecuritydescriptorcontrol'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getsecuritydescriptorcontrol', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getsecuritydescriptorcontrol'
    return forward

def _make_selected_forward_255(original, ledger):
    def forward(state, known_handle, object_kind, information_mask, owner_out, group_out, dacl_out, sacl_out, descriptor_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_getsecurityinfo')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_getsecurityinfo'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_getsecurityinfo')
            result = original(state, known_handle, object_kind, information_mask, owner_out, group_out, dacl_out, sacl_out, descriptor_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_getsecurityinfo'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_getsecurityinfo', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_getsecurityinfo'
    return forward

def _make_selected_forward_256(original, ledger):
    def forward(state, known_descriptor, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_isvalidsecuritydescriptor')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_isvalidsecuritydescriptor'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_isvalidsecuritydescriptor')
            result = original(state, known_descriptor)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_isvalidsecuritydescriptor'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_isvalidsecuritydescriptor', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_isvalidsecuritydescriptor'
    return forward

def _make_selected_forward_257(original, ledger):
    def forward(state, known_sid, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_isvalidsid')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_isvalidsid'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_isvalidsid')
            result = original(state, known_sid)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_isvalidsid'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_isvalidsid', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_isvalidsid'
    return forward

def _make_selected_forward_258(original, ledger):
    def forward(state, known_pointer, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_localfree')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_localfree'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_localfree')
            result = original(state, known_pointer)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_localfree'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_localfree', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_localfree'
    return forward

def _make_selected_forward_259(original, ledger):
    def forward(state, exact_handle_out, access, exact_attributes, exact_status_out, allocation_size, file_attributes, share, disposition, options, ea, ea_length, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_ntcreatefile')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_ntcreatefile'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_ntcreatefile')
            result = original(state, exact_handle_out, access, exact_attributes, exact_status_out, allocation_size, file_attributes, share, disposition, options, ea, ea_length)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_ntcreatefile'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_ntcreatefile', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_ntcreatefile'
    return forward

def _make_selected_forward_260(original, ledger):
    def forward(state, known_handle, exact_status_out, exact_information, information_size, information_class, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_ntsetinformationfile')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_ntsetinformationfile'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_ntsetinformationfile')
            result = original(state, known_handle, exact_status_out, exact_information, information_size, information_class)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_ntsetinformationfile'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_ntsetinformationfile', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_ntsetinformationfile'
    return forward

def _make_selected_forward_261(original, ledger):
    def forward(state, borrowed_process, access, exact_handle_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_openprocesstoken')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_openprocesstoken'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_openprocesstoken')
            result = original(state, borrowed_process, access, exact_handle_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_openprocesstoken'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_openprocesstoken', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_openprocesstoken'
    return forward

def _make_selected_forward_262(original, ledger):
    def forward(state, known_handle, exact_buffer, requested, exact_count_out, overlapped, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_readfile')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_readfile'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_readfile')
            result = original(state, known_handle, exact_buffer, requested, exact_count_out, overlapped)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_readfile'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_readfile', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_readfile'
    return forward

def _make_selected_forward_263(original, ledger):
    def forward(state, known_handle, offset, new_position, method, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_setfilepointerex')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_setfilepointerex'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_setfilepointerex')
            result = original(state, known_handle, offset, new_position, method)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_setfilepointerex'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_setfilepointerex', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_setfilepointerex'
    return forward

def _make_selected_forward_264(original, ledger):
    def forward(state, known_handle, exact_buffer, requested, exact_count_out, overlapped, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._native_writefile')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._native_writefile'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._native_writefile')
            result = original(state, known_handle, exact_buffer, requested, exact_count_out, overlapped)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._native_writefile'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._native_writefile', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._native_writefile'
    return forward

def _make_selected_forward_265(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_aclsize')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_aclsize'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_aclsize')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_aclsize'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_aclsize', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_aclsize'
    return forward

def _make_selected_forward_266(original, ledger):
    def forward(state, path, owner, parent, name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_directory')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_directory'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_directory')
            result = original(state, path, owner, parent, name)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_directory'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_directory', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_directory'
    return forward

def _make_selected_forward_267(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_dword')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_dword'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_dword')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_dword'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_dword', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_dword'
    return forward

def _make_selected_forward_268(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_handle')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_handle'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_handle')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_handle'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_handle', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_handle'
    return forward

def _make_selected_forward_269(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_handleinfo')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_handleinfo'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_handleinfo')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_handleinfo'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_handleinfo', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_handleinfo'
    return forward

def _make_selected_forward_270(original, ledger):
    def forward(state, directory, name, owner, maximum, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_heldcontrol')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_heldcontrol'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_heldcontrol')
            result = original(state, directory, name, owner, maximum)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_heldcontrol'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_heldcontrol', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_heldcontrol'
    return forward

def _make_selected_forward_271(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_iostatus')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_iostatus'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_iostatus')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_iostatus'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_iostatus', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_iostatus'
    return forward

def _make_selected_forward_272(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_known_stack')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_known_stack'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_known_stack')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_known_stack'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_known_stack', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_known_stack'
    return forward

def _make_selected_forward_273(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_lpvoid')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_lpvoid'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_lpvoid')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_lpvoid'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_lpvoid', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_lpvoid'
    return forward

def _make_selected_forward_274(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_lpwstr')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_lpwstr'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_lpwstr')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_lpwstr'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_lpwstr', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_lpwstr'
    return forward

def _make_selected_forward_275(original, ledger):
    def forward(state, structure_size, known_parent_handle, exact_unicode_pointer, attributes_flag, borrowed_descriptor, security_qos, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_objectattributes')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_objectattributes'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_objectattributes')
            result = original(state, structure_size, known_parent_handle, exact_unicode_pointer, attributes_flag, borrowed_descriptor, security_qos)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_objectattributes'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_objectattributes', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_objectattributes'
    return forward

def _make_selected_forward_276(original, ledger):
    def forward(state, raw_sha256, byte_size, native_identity, path_identity, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_publishedfile')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_publishedfile'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_publishedfile')
            result = original(state, raw_sha256, byte_size, native_identity, path_identity)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_publishedfile'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_publishedfile', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_publishedfile'
    return forward

def _make_selected_forward_277(original, ledger):
    def forward(state, replace_false, known_directory_handle, encoded_length, fixed_name, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_renameinfo')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_renameinfo'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_renameinfo')
            result = original(state, replace_false, known_directory_handle, encoded_length, fixed_name)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_renameinfo'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_renameinfo', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_renameinfo'
    return forward

def _make_selected_forward_278(original, ledger):
    def forward(state, bounded_exact_raw_or_empty, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_sha256')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_sha256'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_sha256')
            result = original(state, bounded_exact_raw_or_empty)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_sha256'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_sha256', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_sha256'
    return forward

def _make_selected_forward_279(original, ledger):
    def forward(state, byte_length, maximum_length, exact_text_pointer, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_unicodestring')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_unicodestring'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_unicodestring')
            result = original(state, byte_length, maximum_length, exact_text_pointer)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_unicodestring'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_unicodestring', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_unicodestring'
    return forward

def _make_selected_forward_280(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._new_word')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._new_word'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._new_word')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._new_word'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._new_word', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._new_word'
    return forward

def _make_selected_forward_281(original, ledger):
    def forward(state, validated_absolute_path, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._path')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._path'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._path')
            result = original(state, validated_absolute_path)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._path'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._path', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._path'
    return forward

def _make_selected_forward_282(original, ledger):
    def forward(state, exact_absolute_path, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._path_stat')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._path_stat'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._path_stat')
            result = original(state, exact_absolute_path)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._path_stat'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._path_stat', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._path_stat'
    return forward

def _make_selected_forward_283(original, ledger):
    def forward(state, original_prepared_operand, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._prepare_read_buffer')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._prepare_read_buffer'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._prepare_read_buffer')
            result = original(state, original_prepared_operand)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._prepare_read_buffer'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._prepare_read_buffer', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._prepare_read_buffer'
    return forward

def _make_selected_forward_284(original, ledger):
    def forward(state, original_prepared_operand, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._prepare_token_buffer')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._prepare_token_buffer'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._prepare_token_buffer')
            result = original(state, original_prepared_operand)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._prepare_token_buffer'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._prepare_token_buffer', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._prepare_token_buffer'
    return forward

def _make_selected_forward_285(original, ledger):
    def forward(state, original_prepared_operand, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base')
            result = original(state, original_prepared_operand)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base'
    return forward

def _make_selected_forward_286(original, ledger):
    def forward(state, original_prepared_operand, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer')
            result = original(state, original_prepared_operand)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer'
    return forward

def _make_selected_forward_287(original, ledger):
    def forward(state, original_prepared_operand, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._prepare_write_buffer')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._prepare_write_buffer'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._prepare_write_buffer')
            result = original(state, original_prepared_operand)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._prepare_write_buffer'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._prepare_write_buffer', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._prepare_write_buffer'
    return forward

def _make_selected_forward_288(original, ledger):
    def forward(state, original_primary, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._primary_kind')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._primary_kind'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._primary_kind')
            result = original(state, original_primary)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._primary_kind'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._primary_kind', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._primary_kind'
    return forward

def _make_selected_forward_289(original, ledger):
    def forward(state, original_primary, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._primary_traceback')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._primary_traceback'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._primary_traceback')
            result = original(state, original_primary)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._primary_traceback'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._primary_traceback', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._primary_traceback'
    return forward

def _make_selected_forward_290(original, ledger):
    def forward(state, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._probe_error_raw')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._probe_error_raw'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._probe_error_raw')
            result = original(state)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._probe_error_raw'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._probe_error_raw', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._probe_error_raw'
    return forward

def _make_selected_forward_291(original, ledger):
    def forward(state, exact_profile, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._profile')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._profile'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._profile')
            result = original(state, exact_profile)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._profile'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._profile', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._profile'
    return forward

def _make_selected_forward_292(original, ledger):
    def forward(state, original_secondary, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._record_cleanup_secondary')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._record_cleanup_secondary'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._record_cleanup_secondary')
            result = original(state, original_secondary)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._record_cleanup_secondary'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._record_cleanup_secondary', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._record_cleanup_secondary'
    return forward

def _make_selected_forward_293(original, ledger):
    def forward(state, exact_bounded_raw, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._record_decode')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._record_decode'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._record_decode')
            result = original(state, exact_bounded_raw)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._record_decode'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._record_decode', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._record_decode'
    return forward

def _make_selected_forward_294(original, ledger):
    def forward(state, exact_known_slot, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._register_known_return')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._register_known_return'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._register_known_return')
            result = original(state, exact_known_slot)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._register_known_return'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._register_known_return', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._register_known_return'
    return forward

def _make_selected_forward_295(original, ledger):
    def forward(state, original_primary, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._remember_primary')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._remember_primary'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._remember_primary')
            result = original(state, original_primary)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._remember_primary'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._remember_primary', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._remember_primary'
    return forward

def _make_selected_forward_296(original, ledger):
    def forward(state, exact_stat, directory_role, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._safe_stat')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._safe_stat'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._safe_stat')
            result = original(state, exact_stat, directory_role)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._safe_stat'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._safe_stat', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._safe_stat'
    return forward

def _make_selected_forward_297(original, ledger):
    def forward(state, exact_set, bounded_member, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._set_add_seen')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._set_add_seen'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._set_add_seen')
            result = original(state, exact_set, bounded_member)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._set_add_seen'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._set_add_seen', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._set_add_seen'
    return forward

def _make_selected_forward_298(original, ledger):
    def forward(state, fixed_name, canonical_operation, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._stage_name')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._stage_name'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._stage_name')
            result = original(state, fixed_name, canonical_operation)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._stage_name'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._stage_name', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._stage_name'
    return forward

def _make_selected_forward_299(original, ledger):
    def forward(state, exact_path_stat, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._stat_file_identity')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._stat_file_identity'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._stat_file_identity')
            result = original(state, exact_path_stat)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._stat_file_identity'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._stat_file_identity', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._stat_file_identity'
    return forward

def _make_selected_forward_300(original, ledger):
    def forward(state, known_token, kind, buffer, requested, exact_length_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._token_user_fill')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._token_user_fill'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._token_user_fill')
            result = original(state, known_token, kind, buffer, requested, exact_length_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._token_user_fill'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._token_user_fill', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._token_user_fill'
    return forward

def _make_selected_forward_301(original, ledger):
    def forward(state, known_token, kind, buffer, requested, exact_length_out, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._token_user_probe')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._token_user_probe'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._token_user_probe')
            result = original(state, known_token, kind, buffer, requested, exact_length_out)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._token_user_probe'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._token_user_probe', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._token_user_probe'
    return forward

def _make_selected_forward_302(original, ledger):
    def forward(state, exact_known_receipt, /):
        ledger.alias_stack.append('reno.personal_authority_namespace3_io_deadline._typed_scope_guard')
        ledger.alias_calls['reno.personal_authority_namespace3_io_deadline._typed_scope_guard'] += 1
        try:
            ledger.before_selected('reno.personal_authority_namespace3_io_deadline._typed_scope_guard')
            result = original(state, exact_known_receipt)
            ledger.alias_returns['reno.personal_authority_namespace3_io_deadline._typed_scope_guard'] += 1
            return ledger.after_selected('reno.personal_authority_namespace3_io_deadline._typed_scope_guard', result)
        finally:
            assert ledger.alias_stack.pop() == 'reno.personal_authority_namespace3_io_deadline._typed_scope_guard'
    return forward

SELECTED_FORWARD_BINDINGS = (('locked', '_ns_append_directory', '_make_selected_forward_0', 'SEM-c9f7b20aa961f8367e97', 'ORDINARY_PRE_POST'), ('locked', '_ns_capture_factory', '_make_selected_forward_1', 'SEM-ce108b1b35d1fa70ce0f', 'ORDINARY_PRE_POST'), ('locked', '_ns_complete_cross', '_make_selected_forward_2', 'SEM-d5c3f009f30a484d8b15', 'ORDINARY_PRE_POST'), ('locked', '_ns_deadlinedirectory_verify', '_make_selected_forward_3', 'SEM-7cd2a30ff6503f888206', 'ORDINARY_PRE_POST'), ('locked', '_ns_deadlineheldfile_bytes', '_make_selected_forward_4', 'SEM-2b604f5a427fdb6d3394', 'ORDINARY_PRE_POST'), ('locked', '_ns_deadlineheldfile_verify', '_make_selected_forward_5', 'SEM-d3ac1c168c292fee6b25', 'ORDINARY_PRE_POST'), ('locked', '_ns_evidence_blob', '_make_selected_forward_6', 'SEM-71222b0f5404ef8f8435', 'ORDINARY_PRE_POST'), ('locked', '_ns_expand_arguments', '_make_selected_forward_7', 'SEM-6112d163d9a44cf4933a', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_common', '_make_selected_forward_8', 'SEM-c37a011ca6ad633a8121', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_create', '_make_selected_forward_9', 'SEM-63e5a5a3a7dc754ac6f6', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_create_root_locked', '_make_selected_forward_10', 'SEM-890939268cca9b2a4b45', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_expect_sha', '_make_selected_forward_11', 'SEM-9ce871f8dfb914145195', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_fresh_origin', '_make_selected_forward_12', 'SEM-29faab731134d75cc8f8', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_names', '_make_selected_forward_13', 'SEM-70194768fdf3074c279b', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_need_names', '_make_selected_forward_14', 'SEM-dabba4204f0b9a5905b9', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_open_parent_locked', '_make_selected_forward_15', 'SEM-a025bfc96c98536ed592', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_open_root_locked', '_make_selected_forward_16', 'SEM-ef24b3f90ff0c5165d45', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_physical', '_make_selected_forward_17', 'SEM-17e1cd49629e84c8be88', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_publish', '_make_selected_forward_18', 'SEM-0bfb1caa036068698a95', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_publish_new_locked', '_make_selected_forward_19', 'SEM-28b6c7a4221eac3cdffd', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_read_control', '_make_selected_forward_20', 'SEM-cec244c2ee7059bad5bc', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_read_control_locked', '_make_selected_forward_21', 'SEM-7487b2097be1fcd14988', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_reservation', '_make_selected_forward_22', 'SEM-6eaa36b832c3d664e8eb', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_same_guard', '_make_selected_forward_23', 'SEM-4dbdf27949b97db486b1', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_verify_complete', '_make_selected_forward_24', 'SEM-be49d604da84194aa6e3', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_expand_verify_physical', '_make_selected_forward_25', 'SEM-b7ecf0dc3347efa1583a', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_fixed_failure', '_make_selected_forward_26', 'SEM-2e95ebc9bfddcef7f302', 'CLEANUP_BOOKKEEPING'), ('locked', '_ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read', '_make_selected_forward_27', 'SEM-27a86dd62131b5e283de', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', '_make_selected_forward_28', 'SEM-9fdc08558740c47b30cd', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', '_make_selected_forward_29', 'SEM-317f7326a1a68f5bd2fe', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', '_make_selected_forward_30', 'SEM-ad6722aba470c75df1c0', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', '_make_selected_forward_31', 'SEM-a7d56a3d3f8664712b26', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', '_make_selected_forward_32', 'SEM-f080ebfec602c4426bb1', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', '_make_selected_forward_33', 'SEM-1cf77bedc003ba12715f', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read', '_make_selected_forward_34', 'SEM-ebdef8d1760e9f40dd2f', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field', '_make_selected_forward_35', 'SEM-17c412e97665fb39bf41', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r', '_make_selected_forward_36', 'SEM-155d7a905706274cd9cc', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', '_make_selected_forward_37', 'SEM-728d570466b05117fd0f', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read', '_make_selected_forward_38', 'SEM-fed03a8fedefd13d57ad', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read', '_make_selected_forward_39', 'SEM-cc58de3e9de007d82ed3', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read', '_make_selected_forward_40', 'SEM-a72c75505b84b989bdc5', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read', '_make_selected_forward_41', 'SEM-0445fc860adde162bc78', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read', '_make_selected_forward_42', 'SEM-e9d440cdd80df8da73b6', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', '_make_selected_forward_43', 'SEM-d93831efd36bb8378144', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', '_make_selected_forward_44', 'SEM-c527a1465900cbcbf283', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read', '_make_selected_forward_45', 'SEM-9443d09a5c0428fa4d55', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read', '_make_selected_forward_46', 'SEM-f6e3b7c720e7fdeb952d', 'ORDINARY_PRE_POST'), ('locked', '_ns_get_pinned_imported_os_module_name_original_ordinary_field_read', '_make_selected_forward_47', 'SEM-23d3bb532ac72ee9de87', 'ORDINARY_PRE_POST'), ('locked', '_ns_intent_blob', '_make_selected_forward_48', 'SEM-02258e7920143d10e6d8', 'ORDINARY_PRE_POST'), ('locked', '_ns_iodirectory_verify', '_make_selected_forward_49', 'SEM-ba6c659dd3d99856c526', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_ioheldcontrol_bytes', '_make_selected_forward_50', 'SEM-69863464b7c271e88586', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_new_filesystem_facts', '_make_selected_forward_51', 'SEM-22596de5701ee78f8c72', 'ORDINARY_PRE_POST'), ('locked', '_ns_node_evidence', '_make_selected_forward_52', 'SEM-b488e7de4c243ffcb00f', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_node_factory', '_make_selected_forward_53', 'SEM-8f661dd7f6e9be450028', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_node_id', '_make_selected_forward_54', 'SEM-b4f987c4b538ead1e7f2', 'ORDINARY_PRE_POST'), ('locked', '_ns_node_public_byte_size', '_make_selected_forward_55', 'SEM-92fd67f3b757f6c9df5d', 'ORDINARY_PRE_POST'), ('locked', '_ns_node_public_directory_identity', '_make_selected_forward_56', 'SEM-e3f2fb68210fe729e09f', 'ORDINARY_PRE_POST'), ('locked', '_ns_node_public_handle_identity', '_make_selected_forward_57', 'SEM-7006f2f7dbad24b66b83', 'ORDINARY_PRE_POST'), ('locked', '_ns_node_public_path_identity', '_make_selected_forward_58', 'SEM-3d938a3ac082ae7710bb', 'ORDINARY_PRE_POST'), ('locked', '_ns_node_public_sha256', '_make_selected_forward_59', 'SEM-3d775422852d76d78e15', 'ORDINARY_PRE_POST'), ('locked', '_ns_origin_cross', '_make_selected_forward_60', 'SEM-b8a00b6018ab66414c39', 'ORDINARY_PRE_POST'), ('locked', '_ns_origin_decode', '_make_selected_forward_61', 'SEM-92a8bb79c78922132d7e', 'ORDINARY_PRE_POST'), ('locked', '_ns_physical_directory_factory', '_make_selected_forward_62', 'SEM-20a10948849e9d38ea31', 'ORDINARY_PRE_POST'), ('locked', '_ns_physical_identity', '_make_selected_forward_63', 'SEM-00d488c313e8aa64459a', 'EXPAND_FIXED_SEQUENCE'), ('locked', '_ns_physical_path', '_make_selected_forward_64', 'SEM-79b712f5db6e45a8e4a1', 'ORDINARY_PRE_POST'), ('locked', '_ns_prefix_casefold_copy', '_make_selected_forward_65', 'SEM-5a7b16eaccc52a70932b', 'ORDINARY_PRE_POST'), ('locked', '_ns_prefix_rstrip_copy', '_make_selected_forward_66', 'SEM-919410e2fbace8572bfc', 'ORDINARY_PRE_POST'), ('locked', '_ns_prefix_startswith', '_make_selected_forward_67', 'SEM-24d990082aebb81364ee', 'ORDINARY_PRE_POST'), ('locked', '_ns_profile_key', '_make_selected_forward_68', 'SEM-850f41536d80c1347e94', 'ORDINARY_PRE_POST'), ('locked', '_ns_record_encode', '_make_selected_forward_69', 'SEM-0dc2094ceb84df27b5ba', 'ORDINARY_PRE_POST'), ('locked', '_ns_reservation_cross', '_make_selected_forward_70', 'SEM-3f0141cc24931a0eb477', 'ORDINARY_PRE_POST'), ('locked', '_ns_scan', '_make_selected_forward_71', 'SEM-6577aeec479b4d368de6', 'ENTER_KNOWN_RECEIVER'), ('locked', '_ns_scan_next', '_make_selected_forward_72', 'SEM-0fc858a18c279024e617', 'ORDINARY_PRE_POST'), ('locked', '_ns_set_add_name', '_make_selected_forward_73', 'SEM-61848414a4c5b0679786', 'ORDINARY_PRE_POST'), ('locked', '_ns_sha', '_make_selected_forward_74', 'SEM-b039a4fe76cf95e24253', 'ORDINARY_PRE_POST'), ('locked', '_ns_uuid', '_make_selected_forward_75', 'SEM-f9ad618eef0f8846dd3b', 'ORDINARY_PRE_POST'), ('publication', '_append_bytes', '_make_selected_forward_76', 'SEM-b6e71d60f71a7eeba487', 'ORDINARY_PRE_POST'), ('publication', '_budget_before_next', '_make_selected_forward_77', 'SEM-ab8efb7f9ae812c8bec8', 'INHERITED_BUDGET'), ('publication', '_budget_charge', '_make_selected_forward_78', 'SEM-938e3858f76611fff179', 'INHERITED_BUDGET'), ('publication', '_budget_clock_only', '_make_selected_forward_79', 'SEM-1238f5ab71e22395bcc9', 'INHERITED_BUDGET'), ('publication', '_budget_name', '_make_selected_forward_80', 'SEM-a8ed7ef5ca0dd18d4cea', 'INHERITED_BUDGET'), ('publication', '_budget_poll', '_make_selected_forward_81', 'SEM-4ff3730eaa773b3ea19f', 'INHERITED_BUDGET'), ('publication', '_budget_request', '_make_selected_forward_82', 'SEM-a36b1ee172feb396248b', 'INHERITED_BUDGET'), ('publication', '_cleanup_note', '_make_selected_forward_83', 'SEM-cedbccd554ca09900d06', 'CLEANUP_BOOKKEEPING'), ('publication', '_close_known_slot', '_make_selected_forward_84', 'SEM-0ed17ece11eae5a92293', 'CLEANUP_BOOKKEEPING'), ('publication', '_copy_dict', '_make_selected_forward_85', 'SEM-00e6e026914e5e1687dd', 'ORDINARY_PRE_POST'), ('publication', '_ct_bootstrap_pointer_UnicodeString_l108c29', '_make_selected_forward_86', 'SEM-8497cb970efc60b4d38c', 'BOOTSTRAP'), ('publication', '_ct_byref_info_value_l365c54', '_make_selected_forward_87', 'SEM-c0b838abfcb7c9a6b7dd', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_private_descriptor_descriptor_l291c50', '_make_selected_forward_88', 'SEM-90c0cb48f3b59b8ddb78', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_private_descriptor_text_l280c55', '_make_selected_forward_89', 'SEM-b4f06891469c8cd3110f', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_publish_owned_status_l627c54', '_make_selected_forward_90', 'SEM-84e779bfc6f82d9ed5e8', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_publish_owned_value_l627c76', '_make_selected_forward_91', 'SEM-cb535468ddbedc038db6', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_read_chunk_count_l517c52', '_make_selected_forward_92', 'SEM-1bbf62686ff18bb053b8', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_relative_open_attributes_l402c63', '_make_selected_forward_93', 'SEM-f244a7f12a49561ba13c', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_relative_open_handle_l402c33', '_make_selected_forward_94', 'SEM-ee289b59b63167ab6f4e', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_relative_open_status_l402c89', '_make_selected_forward_95', 'SEM-a3996c71ab161266231c', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_control_l318c68', '_make_selected_forward_96', 'SEM-e7701027c2dbfcfe9b4a', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_dacl_l306c39', '_make_selected_forward_97', 'SEM-36bc30c3d4a5bc56ed7d', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_descriptor_l306c65', '_make_selected_forward_98', 'SEM-c7baa04dedb3eb9ba853', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_owner_l305c55', '_make_selected_forward_99', 'SEM-69407d0eba7d7ed3e679', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_pointer_l338c51', '_make_selected_forward_100', 'SEM-fd75a7a943785c1b4ea5', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_revision_l318c91', '_make_selected_forward_101', 'SEM-2aa2bc7b08f5e38f8d6a', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_security_check_size_l326c51', '_make_selected_forward_102', 'SEM-ff1330b1927c300a8442', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_system_sid_sid_l266c54', '_make_selected_forward_103', 'SEM-83033073e70a09758356', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_token_user_length_l238c65', '_make_selected_forward_104', 'SEM-c997ead31cfd6938a6c7', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_token_user_length_l245c74', '_make_selected_forward_105', 'SEM-d318ee7c1c06fca06e26', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_token_user_token_l233c48', '_make_selected_forward_106', 'SEM-eafd75de474960f37e89', 'ORDINARY_PRE_POST'), ('publication', '_ct_byref_write_chunk_count_l529c53', '_make_selected_forward_107', 'SEM-55fe088edda53612b7f6', 'ORDINARY_PRE_POST'), ('publication', '_ct_cast_borrowed_ace_structure_pointer_l344c22', '_make_selected_forward_108', 'SEM-225f4e9412dfccbec16c', 'ORDINARY_PRE_POST'), ('publication', '_ct_cast_relative_unicode_buffer_pointer_l396c61', '_make_selected_forward_109', 'SEM-a420625e46792da5fbaf', 'ORDINARY_PRE_POST'), ('publication', '_ct_cast_sid_text_allocation_pointer_l283c43', '_make_selected_forward_110', 'SEM-c7b6266044cc8c1c345f', 'COUPLED_OWNER_OUTPUT'), ('publication', '_ct_cast_token_buffer_sid_pointer_l251c14', '_make_selected_forward_111', 'SEM-6abe585c2d08b7923562', 'ORDINARY_PRE_POST'), ('publication', '_ct_get_borrowed_ace_structure_pointer_l344c22', '_make_selected_forward_112', 'SEM-d27fa4439f01f4be4339', 'ORDINARY_PRE_POST'), ('publication', '_ct_get_sid_text_allocation_pointer_l283c43', '_make_selected_forward_113', 'SEM-0682fcf23adda9548f1a', 'COUPLED_OWNER_OUTPUT'), ('publication', '_ct_get_token_buffer_sid_pointer_l251c14', '_make_selected_forward_114', 'SEM-a0766b7561979b06d5d7', 'ORDINARY_PRE_POST'), ('publication', '_ct_pointer_relative_unicode_l398c34', '_make_selected_forward_115', 'SEM-c5a0f44826a8563a5246', 'ORDINARY_PRE_POST'), ('publication', '_ct_pointer_type_AllowedAce_l344c43', '_make_selected_forward_116', 'SEM-835c7bebed259b12d484', 'ORDINARY_PRE_POST'), ('publication', '_ct_pointer_type_w_LPVOID_l251c34', '_make_selected_forward_117', 'SEM-8b44fa29717076f09c9b', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_acl_instance_l326c71', '_make_selected_forward_118', 'SEM-612c83ee5ef12961778e', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_current_rename_instance_l627c97', '_make_selected_forward_119', 'SEM-fc7259ee696a4f07c754', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_fill_pointer_minimum_l249c15', '_make_selected_forward_120', 'SEM-3fd69a6cf2dd4c9ea370', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_object_attributes_type_l397c35', '_make_selected_forward_121', 'SEM-295052fe7546f85d141f', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_probe_pointer_minimum_l241c43', '_make_selected_forward_122', 'SEM-1c7989d12adf13f2a23b', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_supported_attributes_l180c13', '_make_selected_forward_123', 'SEM-f1dd7ba855be2e5aef9f', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_supported_pointer_width_l179c31', '_make_selected_forward_124', 'SEM-c5083d44732205e6b699', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_supported_status_l180c13', '_make_selected_forward_125', 'SEM-d8b49f91f14dfdb67ba4', 'ORDINARY_PRE_POST'), ('publication', '_ct_sizeof_supported_unicode_l180c13', '_make_selected_forward_126', 'SEM-e60f3081e34fb0a4394b', 'ORDINARY_PRE_POST'), ('publication', '_ct_supported_sizes', '_make_selected_forward_127', 'SEM-7be4e8913f14f1956ac5', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_deepcopy_fixed_json', '_make_selected_forward_128', 'SEM-8e2fe4cbc567d5275264', 'ORDINARY_PRE_POST'), ('publication', '_enter_fixed_receipt', '_make_selected_forward_129', 'SEM-7f782d8ab97235eed6fa', 'ENTER_KNOWN_RECEIVER'), ('publication', '_entry_name', '_make_selected_forward_130', 'SEM-3d6225cddf3e14ee6f63', 'ORDINARY_PRE_POST'), ('publication', '_exit_known_receipt', '_make_selected_forward_131', 'SEM-ed161241084c2101563d', 'EXIT_KNOWN_RECEIVER'), ('publication', '_expand_check', '_make_selected_forward_132', 'SEM-f2632d7225a095612e2c', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_info', '_make_selected_forward_133', 'SEM-e0f68f9c8890c3d86a82', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_open_parent_raw', '_make_selected_forward_134', 'SEM-6eedb1c798339b73e436', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_private_descriptor', '_make_selected_forward_135', 'SEM-bd160ab62a5ff3438153', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_publish_owned', '_make_selected_forward_136', 'SEM-f8cf7b739401749c20e0', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_read_chunk', '_make_selected_forward_137', 'SEM-5ac46e24ae5d984519b2', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_read_control_locked', '_make_selected_forward_138', 'SEM-e5bf56f671f9f93f1d6d', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_read_exact', '_make_selected_forward_139', 'SEM-17cc5d6c91544c2cee9a', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_relative_open', '_make_selected_forward_140', 'SEM-4303b1804f6efe9df656', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_rewind', '_make_selected_forward_141', 'SEM-7ec64e3693a07ae87051', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_security_check', '_make_selected_forward_142', 'SEM-f67099f8b9e9725fbae3', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_supported', '_make_selected_forward_143', 'SEM-04b57f0fde18bad1be5b', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_system_sid', '_make_selected_forward_144', 'SEM-6af2abde8281753d88b0', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_token_user', '_make_selected_forward_145', 'SEM-9c727d64daee8a0a9746', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_expand_write_chunk', '_make_selected_forward_146', 'SEM-64668b2cf056624c854a', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_failure_error_raw', '_make_selected_forward_147', 'SEM-276da9612ddb19b7ecf9', 'FAILURE_ERROR_CAPTURE'), ('publication', '_fixed_failure', '_make_selected_forward_148', 'SEM-ec1c9bba8112615e2301', 'CLEANUP_BOOKKEEPING'), ('publication', '_get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read', '_make_selected_forward_149', 'SEM-19c62edf4bc4933e2dcc', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read', '_make_selected_forward_150', 'SEM-6774a4e280f3aae9418b', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read', '_make_selected_forward_151', 'SEM-845582404544f34a17bc', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_aclsize_output_count_original_ordinary_field_read', '_make_selected_forward_152', 'SEM-247b041aeb26885d61f4', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_aclsize_output_used_original_ordinary_field_read', '_make_selected_forward_153', 'SEM-2d8d4a671cfb91321770', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_allowedace_borrowed_view_header_original_ordinary_field_read', '_make_selected_forward_154', 'SEM-51c11a90399485e1f4d7', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read', '_make_selected_forward_155', 'SEM-dec731df4d17edcf5195', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read', '_make_selected_forward_156', 'SEM-86e24420d6b573764f3b', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read', '_make_selected_forward_157', 'SEM-809edc2574ed27c59b09', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read', '_make_selected_forward_158', 'SEM-750f0a7c8de08e7bc565', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read', '_make_selected_forward_159', 'SEM-9e597c4c063bd444d853', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read', '_make_selected_forward_160', 'SEM-e76c9d7173b8019c2530', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read', '_make_selected_forward_161', 'SEM-34ab313e82d5c400680b', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', '_make_selected_forward_162', 'SEM-b6767078d28cc716e09b', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read', '_make_selected_forward_163', 'SEM-2abcd8ca3c3778fc1b79', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read', '_make_selected_forward_164', 'SEM-b1a6a46f0c2d08a188e3', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', '_make_selected_forward_165', 'SEM-b5e768969650647dfa46', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', '_make_selected_forward_166', 'SEM-608c0140fa015358b1f8', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read', '_make_selected_forward_167', 'SEM-510532681bd73a96bd3e', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', '_make_selected_forward_168', 'SEM-1669e459200afed198d5', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', '_make_selected_forward_169', 'SEM-44025b7d618bbb255dd1', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', '_make_selected_forward_170', 'SEM-b3fe9e5c77a80fdb2d16', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read', '_make_selected_forward_171', 'SEM-d5b95692e052bf468ab6', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read', '_make_selected_forward_172', 'SEM-25541e1d0ec2c4475cac', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read', '_make_selected_forward_173', 'SEM-da57293e402a22dbcda0', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read', '_make_selected_forward_174', 'SEM-ab042f06b3eb14e049a7', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read', '_make_selected_forward_175', 'SEM-468d3b1263eea0a5ed64', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read', '_make_selected_forward_176', 'SEM-d02ef662793a6433632a', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read', '_make_selected_forward_177', 'SEM-3bb67565ecf408c64010', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read', '_make_selected_forward_178', 'SEM-097b22a04791def018f6', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read', '_make_selected_forward_179', 'SEM-86526967e207c21e3b0d', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read', '_make_selected_forward_180', 'SEM-d89185d9c03d75cd14a9', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read', '_make_selected_forward_181', 'SEM-5c8800f0f608c6f84bb0', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read', '_make_selected_forward_182', 'SEM-b49e45171ba25d5c1ea1', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read', '_make_selected_forward_183', 'SEM-c27c1aabfe43fdd1acd3', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read', '_make_selected_forward_184', 'SEM-4ddaca2f23750d483d32', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_attributes_original_ordinary_field_read', '_make_selected_forward_185', 'SEM-6ba7fbd35f50342047d0', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_created_original_ordinary_field_read', '_make_selected_forward_186', 'SEM-ce1d77f3929c4febdf57', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_index_high_original_ordinary_field_read', '_make_selected_forward_187', 'SEM-130efd2936c4cd2c7ba8', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_index_low_original_ordinary_field_read', '_make_selected_forward_188', 'SEM-f140c3268740bac3674b', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_links_original_ordinary_field_read', '_make_selected_forward_189', 'SEM-f8c1763204d1c8b12c0f', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_size_high_original_ordinary_field_read', '_make_selected_forward_190', 'SEM-202108a610a2f40f9182', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_size_low_original_ordinary_field_read', '_make_selected_forward_191', 'SEM-edb461dfee53504f9d6b', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_volume_original_ordinary_field_read', '_make_selected_forward_192', 'SEM-2e98a6e04fb478838fb7', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_handleinfo_output_written_original_ordinary_field_read', '_make_selected_forward_193', 'SEM-50f98ce7e92bd3e34adf', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_iostatus_output_information_original_ordinary_field_read', '_make_selected_forward_194', 'SEM-fb768889d124f0d5a764', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_iostatus_output_status_existing_creator_known_raw_nt_completion_before_original630_clock', '_make_selected_forward_195', 'SEM-764ac00069a213c19e84', 'COUPLED_OWNER_OUTPUT'), ('publication', '_get_exact_iostatus_output_status_required_legal_success_known_owner_value_occurrence_before_adoption_pos', '_make_selected_forward_196', 'SEM-34f5eeb4609f77cd4a60', 'COUPLED_OWNER_OUTPUT'), ('publication', '_get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read', '_make_selected_forward_197', 'SEM-e984c10f00ae9795dec7', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read', '_make_selected_forward_198', 'SEM-d0fa5e70033d29f12e08', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', '_make_selected_forward_199', 'SEM-1ea136d4531bdfda967f', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_private_io_owner_with_current_slot_lifetime_failure_known_context_slot_first_primary_tb_note_a', '_make_selected_forward_200', 'SEM-cb6401affa89b22a283f', 'CLEANUP_BOOKKEEPING'), ('publication', '_get_exact_private_io_owner_with_current_slot_lifetime_retired_known_context_slot_first_primary_tb_note_a', '_make_selected_forward_201', 'SEM-ab3264e54090c9f80c9e', 'CLEANUP_BOOKKEEPING'), ('publication', '_get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read', '_make_selected_forward_202', 'SEM-0745bd4ee3eab793a635', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_private_io_owner_with_current_slot_lifetime_value_known_context_slot_first_primary_tb_note_att', '_make_selected_forward_203', 'SEM-45164030eb1282ede4f9', 'CLEANUP_BOOKKEEPING'), ('publication', '_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', '_make_selected_forward_204', 'SEM-bfbc9390d688d97b3e31', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', '_make_selected_forward_205', 'SEM-6dbc8b98c96ab42949fc', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_dword_actual_read_count_value_read_count_g1_validate', '_make_selected_forward_206', 'SEM-5f7d5ee7b576f2f23602', 'COUPLED_COUNT_VALIDATE'), ('publication', '_get_exact_w_dword_actual_read_count_value_read_count_g2_charge_operand', '_make_selected_forward_207', 'SEM-c48660b71a18d03457f8', 'COUPLED_COUNT_CHARGE_OPERAND'), ('publication', '_get_exact_w_dword_actual_read_count_value_read_count_g3_return_reread', '_make_selected_forward_208', 'SEM-12f7eb0e63d7ed706de9', 'COUNT_RETURN_ORDINARY'), ('publication', '_get_exact_w_dword_actual_write_count_value_write_count_g1_validate', '_make_selected_forward_209', 'SEM-5fc056d295e27002eb0a', 'COUPLED_COUNT_VALIDATE'), ('publication', '_get_exact_w_dword_actual_write_count_value_write_count_g2_charge_operand', '_make_selected_forward_210', 'SEM-08041cd6126a2963dc11', 'COUPLED_COUNT_CHARGE_OPERAND'), ('publication', '_get_exact_w_dword_actual_write_count_value_write_count_g3_return_reread', '_make_selected_forward_211', 'SEM-c6fa550ebfa78e8f42eb', 'COUNT_RETURN_ORDINARY'), ('publication', '_get_exact_w_dword_output_value_fill_length_later_reread_after_248', '_make_selected_forward_212', 'SEM-d517c4a5989a57fa09f8', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240', '_make_selected_forward_213', 'SEM-86e0adb0545788b91631', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_dword_output_value_probe_length_domain_after_240', '_make_selected_forward_214', 'SEM-e1590d8e6b14ef2cba93', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_handle_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post', '_make_selected_forward_215', 'SEM-e45470bf5bb713eb3eac', 'COUPLED_OWNER_OUTPUT'), ('publication', '_get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read', '_make_selected_forward_216', 'SEM-7253ecece99ed6261a81', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_lpvoid_allocation_output_value_required_legal_success_known_owner_value_occurrence_before_ad', '_make_selected_forward_217', 'SEM-4602ca6f541410b51ff4', 'COUPLED_OWNER_OUTPUT'), ('publication', '_get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read', '_make_selected_forward_218', 'SEM-b2ea1b265ceee2c3df22', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read', '_make_selected_forward_219', 'SEM-90d8a01d0ecf32343107', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read', '_make_selected_forward_220', 'SEM-6a90e6d215547c58adab', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_lpvoid_output_value_original_ordinary_field_read', '_make_selected_forward_221', 'SEM-0f866cf4b47cb5bd8f85', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_lpvoid_output_value_required_legal_success_known_owner_value_occurrence_before_adoption_post', '_make_selected_forward_222', 'SEM-0ee96e3de2f631cf91a5', 'COUPLED_OWNER_OUTPUT'), ('publication', '_get_exact_w_lpwstr_output_value_original_ordinary_field_read', '_make_selected_forward_223', 'SEM-e4e4fa3a2948063b812e', 'ORDINARY_PRE_POST'), ('publication', '_get_exact_w_word_output_value_original_ordinary_field_read', '_make_selected_forward_224', 'SEM-fff4506732da4c6c570f', 'ORDINARY_PRE_POST'), ('publication', '_get_pinned_imported_os_module_name_original_ordinary_field_read', '_make_selected_forward_225', 'SEM-4bba41d37f900af04b06', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_ctime_ns', '_make_selected_forward_226', 'SEM-33879ed986e10208ab4d', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_dev', '_make_selected_forward_227', 'SEM-194362f46cb940bf7e7d', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_ino', '_make_selected_forward_228', 'SEM-3358cb1ff50bec7c1ac3', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_mode', '_make_selected_forward_229', 'SEM-0f18e86e3bed0a7f382b', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_mtime_ns', '_make_selected_forward_230', 'SEM-161c6fa9206263eab80f', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_nlink', '_make_selected_forward_231', 'SEM-1438116a126756a8a7dc', 'ORDINARY_PRE_POST'), ('publication', '_get_stat_st_size', '_make_selected_forward_232', 'SEM-1181192c0b4a5e244b3a', 'ORDINARY_PRE_POST'), ('publication', '_guard', '_make_selected_forward_233', 'SEM-774ccab23c05419b2737', 'ORDINARY_PRE_POST'), ('publication', '_hash_hex', '_make_selected_forward_234', 'SEM-93127bded222d5c1dea6', 'ORDINARY_PRE_POST'), ('publication', '_iodirectory_verify', '_make_selected_forward_235', 'SEM-fc27499dcef6b934f44b', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_ioheldcontrol_bytes', '_make_selected_forward_236', 'SEM-d72c1b46a51cc390d79c', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_ioheldcontrol_verify', '_make_selected_forward_237', 'SEM-45e349bf697b7ad7b39c', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_join_bytes', '_make_selected_forward_238', 'SEM-5dd76ee613e6fbffc062', 'ORDINARY_PRE_POST'), ('publication', '_mark_attempted', '_make_selected_forward_239', 'SEM-286b33fb9b7323aff81b', 'CLEANUP_BOOKKEEPING'), ('publication', '_name_utf16', '_make_selected_forward_240', 'SEM-50fa4a18d92f5f197d45', 'ORDINARY_PRE_POST'), ('publication', '_native_absolute', '_make_selected_forward_241', 'SEM-dcc6e4d779782510704d', 'ORDINARY_PRE_POST'), ('publication', '_native_closehandle', '_make_selected_forward_242', 'SEM-64fe258d6290c9adaef8', 'CLEANUP_SOLE_NATIVE'), ('publication', '_native_convertsidtostringsidw', '_make_selected_forward_243', 'SEM-0fc6950202721aeb854b', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_convertstringsecuritydescriptortosecuritydescriptorw', '_make_selected_forward_244', 'SEM-be5470446b203641f36c', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_convertstringsidtosidw', '_make_selected_forward_245', 'SEM-22c09c9bd429636155b6', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_createfilew', '_make_selected_forward_246', 'SEM-763174bfe198371ff55d', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_equalsid', '_make_selected_forward_247', 'SEM-4f199b602f58cdadbd19', 'NATIVE_PREDICATE_ORIGINAL_POST'), ('publication', '_native_flushfilebuffers', '_make_selected_forward_248', 'SEM-a77fc0d737d4829834d4', 'NATIVE_BOOL_THEN_ORIGINAL_POST'), ('publication', '_native_getace', '_make_selected_forward_249', 'SEM-c7f60ba12db7221576ad', 'NATIVE_BOOL_THEN_ORIGINAL_POST'), ('publication', '_native_getaclinformation', '_make_selected_forward_250', 'SEM-34b5af0308af504d5032', 'NATIVE_BOOL_THEN_ORIGINAL_POST'), ('publication', '_native_getcurrentprocess', '_make_selected_forward_251', 'SEM-05c85ba713866bbe5028', 'ORDINARY_PRE_POST'), ('publication', '_native_getfileinformationbyhandle', '_make_selected_forward_252', 'SEM-c9324708fa48d5406ec0', 'NATIVE_BOOL_THEN_ORIGINAL_POST'), ('publication', '_native_getlengthsid', '_make_selected_forward_253', 'SEM-807a551c1f466f4f3955', 'NATIVE_PREDICATE_ORIGINAL_POST'), ('publication', '_native_getsecuritydescriptorcontrol', '_make_selected_forward_254', 'SEM-ed40acbe047320ee10b3', 'NATIVE_BOOL_THEN_ORIGINAL_POST'), ('publication', '_native_getsecurityinfo', '_make_selected_forward_255', 'SEM-483f623e9d8f93269d65', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_isvalidsecuritydescriptor', '_make_selected_forward_256', 'SEM-eea107ace7f7de999d50', 'NATIVE_PREDICATE_ORIGINAL_POST'), ('publication', '_native_isvalidsid', '_make_selected_forward_257', 'SEM-c1f25cd812d00e06ffe4', 'NATIVE_PREDICATE_ORIGINAL_POST'), ('publication', '_native_localfree', '_make_selected_forward_258', 'SEM-6acc0cf0e9113e830100', 'CLEANUP_SOLE_NATIVE'), ('publication', '_native_ntcreatefile', '_make_selected_forward_259', 'SEM-39d7e980cefd8bbb2611', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_ntsetinformationfile', '_make_selected_forward_260', 'SEM-fd324bab73084ee8bd06', 'NATIVE_NT_COMPLETION'), ('publication', '_native_openprocesstoken', '_make_selected_forward_261', 'SEM-fb7218b6b87a162ecb89', 'NATIVE_OWNERSHIP_CAPTURE'), ('publication', '_native_readfile', '_make_selected_forward_262', 'SEM-fd378580afdaac371b01', 'NATIVE_COUNT_CAPTURE'), ('publication', '_native_setfilepointerex', '_make_selected_forward_263', 'SEM-91ccbf288262e480dbcd', 'NATIVE_BOOL_THEN_ORIGINAL_POST'), ('publication', '_native_writefile', '_make_selected_forward_264', 'SEM-cd7165bba3a21790e848', 'NATIVE_COUNT_CAPTURE'), ('publication', '_new_aclsize', '_make_selected_forward_265', 'SEM-cca744dd3eab70097746', 'ORDINARY_PRE_POST'), ('publication', '_new_directory', '_make_selected_forward_266', 'SEM-9bfd90d5a25a4fa5e848', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_new_dword', '_make_selected_forward_267', 'SEM-e1f1f5e815b9def8ef4d', 'ORDINARY_PRE_POST'), ('publication', '_new_handle', '_make_selected_forward_268', 'SEM-c427068b93ad9c88a1c4', 'ORDINARY_PRE_POST'), ('publication', '_new_handleinfo', '_make_selected_forward_269', 'SEM-59a14d2e0e9ed17723d6', 'ORDINARY_PRE_POST'), ('publication', '_new_heldcontrol', '_make_selected_forward_270', 'SEM-d300c95d0caba9efabc1', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_new_iostatus', '_make_selected_forward_271', 'SEM-d5fbfe0e44a56ba94276', 'ORDINARY_PRE_POST'), ('publication', '_new_known_stack', '_make_selected_forward_272', 'SEM-e9118b28bfc846d666fe', 'ORDINARY_PRE_POST'), ('publication', '_new_lpvoid', '_make_selected_forward_273', 'SEM-a91e83fd52ea6ceac3d4', 'ORDINARY_PRE_POST'), ('publication', '_new_lpwstr', '_make_selected_forward_274', 'SEM-1e5fa872fd4c3474fa77', 'ORDINARY_PRE_POST'), ('publication', '_new_objectattributes', '_make_selected_forward_275', 'SEM-e03da155ecae34624a99', 'ORDINARY_PRE_POST'), ('publication', '_new_publishedfile', '_make_selected_forward_276', 'SEM-86f6cd443abc345be8f7', 'ORDINARY_PRE_POST'), ('publication', '_new_renameinfo', '_make_selected_forward_277', 'SEM-bf761fe420ddc42374e4', 'ORDINARY_PRE_POST'), ('publication', '_new_sha256', '_make_selected_forward_278', 'SEM-364497820587a2a2b816', 'ORDINARY_PRE_POST'), ('publication', '_new_unicodestring', '_make_selected_forward_279', 'SEM-661a2599b98f1865f78f', 'ORDINARY_PRE_POST'), ('publication', '_new_word', '_make_selected_forward_280', 'SEM-f42e3dd76add88b62012', 'ORDINARY_PRE_POST'), ('publication', '_path', '_make_selected_forward_281', 'SEM-290d60920ad9ecdefa28', 'ORDINARY_PRE_POST'), ('publication', '_path_stat', '_make_selected_forward_282', 'SEM-cfc12326ea2f5a631711', 'ORDINARY_PRE_POST'), ('publication', '_prepare_read_buffer', '_make_selected_forward_283', 'SEM-9ede0dbe56d28556cd8b', 'ORDINARY_PRE_POST'), ('publication', '_prepare_token_buffer', '_make_selected_forward_284', 'SEM-a2a5a725f755d2dca1ac', 'ORDINARY_PRE_POST'), ('publication', '_prepare_token_buffer_base', '_make_selected_forward_285', 'SEM-c654e453d56f17f1afb0', 'ORDINARY_PRE_POST'), ('publication', '_prepare_unicode_name_buffer', '_make_selected_forward_286', 'SEM-45ac29bfaa29d545d8f3', 'ORDINARY_PRE_POST'), ('publication', '_prepare_write_buffer', '_make_selected_forward_287', 'SEM-66c9fd2226ccd8ac04c6', 'ORDINARY_PRE_POST'), ('publication', '_primary_kind', '_make_selected_forward_288', 'SEM-f9e8a961c8412ae95158', 'CLEANUP_BOOKKEEPING'), ('publication', '_primary_traceback', '_make_selected_forward_289', 'SEM-08581d117ba307ae34c9', 'CLEANUP_BOOKKEEPING'), ('publication', '_probe_error_raw', '_make_selected_forward_290', 'SEM-70bd0ef1a9f4269bb7cf', 'PROBE_RAW_ERROR_METADATA'), ('publication', '_profile', '_make_selected_forward_291', 'SEM-277796e28773cf1b8cab', 'ORDINARY_PRE_POST'), ('publication', '_record_cleanup_secondary', '_make_selected_forward_292', 'SEM-2645c6c43318701db81e', 'CLEANUP_BOOKKEEPING'), ('publication', '_record_decode', '_make_selected_forward_293', 'SEM-b733eb945b09e039915e', 'ORDINARY_PRE_POST'), ('publication', '_register_known_return', '_make_selected_forward_294', 'SEM-9dd04768d88d87df4dcd', 'KNOWN_REGISTER_BEFORE_POST'), ('publication', '_remember_primary', '_make_selected_forward_295', 'SEM-7feefdb66f102bc4be82', 'CLEANUP_BOOKKEEPING'), ('publication', '_safe_stat', '_make_selected_forward_296', 'SEM-d1566f05ad28fb3c9abf', 'ORDINARY_PRE_POST'), ('publication', '_set_add_seen', '_make_selected_forward_297', 'SEM-790490143f2ae87abfab', 'ORDINARY_PRE_POST'), ('publication', '_stage_name', '_make_selected_forward_298', 'SEM-ed03a36af851209f5aa3', 'ORDINARY_PRE_POST'), ('publication', '_stat_file_identity', '_make_selected_forward_299', 'SEM-89485f489b31d3e10ee0', 'EXPAND_FIXED_SEQUENCE'), ('publication', '_token_user_fill', '_make_selected_forward_300', 'SEM-12cf8461a6a535fb80c7', 'TOKEN_FILL_NATIVE'), ('publication', '_token_user_probe', '_make_selected_forward_301', 'SEM-08a8b1eff6d3a8a27d5b', 'TOKEN_PROBE_CAPTURE_ONLY'), ('publication', '_typed_scope_guard', '_make_selected_forward_302', 'SEM-cf1f475241c15d251f85', 'EXPAND_FIXED_SEQUENCE'))


def _ordinary_fixed_arguments_0(bank):
    physical = bank.physical
    state = bank.state
    return (state, [], physical)

def _ordinary_fixed_arguments_1(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile, DOMAIN, FACILITY, INIT_OPERATION)

def _ordinary_fixed_arguments_2(bank):
    header_raw = bank.header_raw
    item = bank.item
    reservation_raw = bank.reservation_raw
    seal_raw = bank.seal_raw
    state = bank.state
    return (state, item.first, item.second, reservation_raw, seal_raw, header_raw)

def _ordinary_fixed_arguments_3(bank):
    physical = bank.physical
    state = bank.state
    return (state, physical)

def _ordinary_fixed_arguments_4(bank):
    node = bank.node
    state = bank.state
    return (state, node, 4096)

def _ordinary_fixed_arguments_5(bank):
    node = bank.node
    state = bank.state
    return (state, node)

def _ordinary_fixed_arguments_6(bank):
    second = bank.second
    state = bank.state
    return (state, second)

def _ordinary_fixed_arguments_7(bank):
    physical = bank.physical
    state = bank.state
    return (state, physical)

def _ordinary_fixed_arguments_8(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_9(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_10(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_11(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_12(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_13(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_14(bank):
    entry = bank.entry
    state = bank.state
    return (state, entry)

def _ordinary_fixed_arguments_15(bank):
    observation = bank.observation
    state = bank.state
    return (state, observation)

def _ordinary_fixed_arguments_16(bank):
    observation = bank.observation
    state = bank.state
    return (state, observation)

def _ordinary_fixed_arguments_17(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile.database)

def _ordinary_fixed_arguments_18(bank):
    published = bank.published
    state = bank.state
    return (state, published)

def _ordinary_fixed_arguments_19(bank):
    published = bank.published
    state = bank.state
    return (state, published)

def _ordinary_fixed_arguments_20(bank):
    published = bank.published
    state = bank.state
    return (state, published)

def _ordinary_fixed_arguments_21(bank):
    published = bank.published
    state = bank.state
    return (state, published)

def _ordinary_fixed_arguments_22(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_23(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_24(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_25(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_26(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_27(bank):
    state = bank.state
    return (state, os)

def _ordinary_fixed_arguments_28(bank):
    second = bank.second
    state = bank.state
    return (state, second)

def _ordinary_fixed_arguments_29(bank):
    header_raw = bank.header_raw
    item = bank.item
    reservation_raw = bank.reservation_raw
    seal_raw = bank.seal_raw
    state = bank.state
    return (state, SCOPE, INIT_OPERATION, DOMAIN, FACILITY, sha(item.first), sha(item.second), sha(reservation_raw), sha(seal_raw), sha(header_raw), CREATION)

def _ordinary_fixed_arguments_30(bank):
    item = bank.item
    state = bank.state
    return (state, item.node_raw)

def _ordinary_fixed_arguments_31(bank):
    node = bank.node
    state = bank.state
    return (state, node)

def _ordinary_fixed_arguments_32(bank):
    node = bank.node
    state = bank.state
    return (state, node)

def _ordinary_fixed_arguments_33(bank):
    node = bank.node
    state = bank.state
    return (state, node)

def _ordinary_fixed_arguments_34(bank):
    node = bank.node
    state = bank.state
    return (state, node)

def _ordinary_fixed_arguments_35(bank):
    node = bank.node
    state = bank.state
    return (state, node)

def _ordinary_fixed_arguments_36(bank):
    item = bank.item
    state = bank.state
    return (state, item.first, item.second)

def _ordinary_fixed_arguments_37(bank):
    item = bank.item
    state = bank.state
    return (state, item.first)

def _ordinary_fixed_arguments_38(bank):
    item = bank.item
    state = bank.state
    return (state, item.data)

def _ordinary_fixed_arguments_39(bank):
    physical = bank.physical
    state = bank.state
    return (state, physical)

def _ordinary_fixed_arguments_40(bank):
    state = bank.state
    return (state, UNKNOWN)

def _ordinary_fixed_arguments_41(bank):
    state = bank.state
    return (state, UNKNOWN)

def _ordinary_fixed_arguments_42(bank):
    state = bank.state
    return (state, UNKNOWN.lower())

def _ordinary_fixed_arguments_43(bank):
    item = bank.item
    state = bank.state
    return (state, item.data)

def _ordinary_fixed_arguments_44(bank):
    reservation_raw = bank.reservation_raw
    state = bank.state
    return (state, decoded(reservation_raw))

def _ordinary_fixed_arguments_45(bank):
    item = bank.item
    reservation_raw = bank.reservation_raw
    state = bank.state
    return (state, item.first, item.second, reservation_raw)

def _ordinary_fixed_arguments_46(bank):
    entries = bank.entries
    state = bank.state
    return (state, entries)

def _ordinary_fixed_arguments_47(bank):
    state = bank.state
    return (state, set(), FIRST)

def _ordinary_fixed_arguments_48(bank):
    item = bank.item
    state = bank.state
    return (state, sha(item.first))

def _ordinary_fixed_arguments_49(bank):
    state = bank.state
    return (state, DOMAIN)

def _ordinary_fixed_arguments_50(bank):
    state = bank.state
    return (state, [], b'known\x00\xff')

def _ordinary_fixed_arguments_51(bank):
    state = bank.state
    return (state, {'known': '绍兴'})

def _ordinary_fixed_arguments_52(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_53(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_54(bank):
    state = bank.state
    text_pointer = bank.text_pointer
    return (state, text_pointer)

def _ordinary_fixed_arguments_55(bank):
    state = bank.state
    status = bank.status
    return (state, status)

def _ordinary_fixed_arguments_56(bank):
    rename_value = bank.rename_value
    state = bank.state
    return (state, rename_value)

def _ordinary_fixed_arguments_57(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_58(bank):
    attributes = bank.attributes
    state = bank.state
    return (state, attributes)

def _ordinary_fixed_arguments_59(bank):
    handle_output = bank.handle_output
    state = bank.state
    return (state, handle_output)

def _ordinary_fixed_arguments_60(bank):
    state = bank.state
    status = bank.status
    return (state, status)

def _ordinary_fixed_arguments_61(bank):
    state = bank.state
    word = bank.word
    return (state, word)

def _ordinary_fixed_arguments_62(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_63(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_64(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_65(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_66(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_67(bank):
    acl = bank.acl
    state = bank.state
    return (state, acl)

def _ordinary_fixed_arguments_68(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_69(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_70(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_71(bank):
    handle_output = bank.handle_output
    state = bank.state
    return (state, handle_output)

def _ordinary_fixed_arguments_72(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_73(bank):
    ace_address = bank.ace_address
    state = bank.state
    return (state, ace_address)

def _ordinary_fixed_arguments_74(bank):
    state = bank.state
    unicode_buffer = bank.unicode_buffer
    return (state, unicode_buffer)

def _ordinary_fixed_arguments_75(bank):
    state = bank.state
    token_buffer = bank.token_buffer
    return (state, token_buffer)

def _ordinary_fixed_arguments_76(bank):
    ace_view = bank.ace_view
    state = bank.state
    return (state, ace_view)

def _ordinary_fixed_arguments_77(bank):
    state = bank.state
    token_view = bank.token_view
    return (state, token_view)

def _ordinary_fixed_arguments_78(bank):
    state = bank.state
    unicode_value = bank.unicode_value
    return (state, unicode_value)

def _ordinary_fixed_arguments_79(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_80(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_81(bank):
    acl = bank.acl
    state = bank.state
    return (state, acl)

def _ordinary_fixed_arguments_82(bank):
    rename_value = bank.rename_value
    state = bank.state
    return (state, rename_value)

def _ordinary_fixed_arguments_83(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_84(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_85(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_86(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_87(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_88(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_89(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_90(bank):
    state = bank.state
    return (state, {'known': ['绍兴', 1]})

def _ordinary_fixed_arguments_91(bank):
    state = bank.state
    return (state, FIRST)

def _ordinary_fixed_arguments_92(bank):
    ace_header = bank.ace_header
    state = bank.state
    return (state, ace_header)

def _ordinary_fixed_arguments_93(bank):
    ace_header = bank.ace_header
    state = bank.state
    return (state, ace_header)

def _ordinary_fixed_arguments_94(bank):
    ace_header = bank.ace_header
    state = bank.state
    return (state, ace_header)

def _ordinary_fixed_arguments_95(bank):
    acl = bank.acl
    state = bank.state
    return (state, acl)

def _ordinary_fixed_arguments_96(bank):
    acl = bank.acl
    state = bank.state
    return (state, acl)

def _ordinary_fixed_arguments_97(bank):
    ace = bank.ace
    state = bank.state
    return (state, ace)

def _ordinary_fixed_arguments_98(bank):
    ace = bank.ace
    state = bank.state
    return (state, ace)

def _ordinary_fixed_arguments_99(bank):
    parent_owner = bank.parent_owner
    state = bank.state
    return (state, parent_owner)

def _ordinary_fixed_arguments_100(bank):
    parent_owner = bank.parent_owner
    state = bank.state
    return (state, parent_owner)

def _ordinary_fixed_arguments_101(bank):
    parent_owner = bank.parent_owner
    state = bank.state
    return (state, parent_owner)

def _ordinary_fixed_arguments_102(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_103(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_104(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_105(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_106(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_107(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_108(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_109(bank):
    parent = bank.parent
    state = bank.state
    return (state, parent)

def _ordinary_fixed_arguments_110(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_111(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_112(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_113(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_114(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_115(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_116(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_117(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_118(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_119(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_120(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_121(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_122(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_123(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_124(bank):
    held = bank.held
    state = bank.state
    return (state, held)

def _ordinary_fixed_arguments_125(bank):
    read_buffer = bank.read_buffer
    state = bank.state
    return (state, read_buffer)

def _ordinary_fixed_arguments_126(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_127(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_128(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_129(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_130(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_131(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_132(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_133(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_134(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_135(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_136(bank):
    info = bank.info
    state = bank.state
    return (state, info)

def _ordinary_fixed_arguments_137(bank):
    state = bank.state
    status = bank.status
    return (state, status)

def _ordinary_fixed_arguments_138(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_139(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_140(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile.database)

def _ordinary_fixed_arguments_141(bank):
    parent_owner = bank.parent_owner
    state = bank.state
    return (state, parent_owner)

def _ordinary_fixed_arguments_142(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_143(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_144(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_145(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_146(bank):
    dword = bank.dword
    state = bank.state
    return (state, dword)

def _ordinary_fixed_arguments_147(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_148(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_149(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_150(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_151(bank):
    pointer = bank.pointer
    state = bank.state
    return (state, pointer)

def _ordinary_fixed_arguments_152(bank):
    state = bank.state
    text_pointer = bank.text_pointer
    return (state, text_pointer)

def _ordinary_fixed_arguments_153(bank):
    state = bank.state
    word = bank.word
    return (state, word)

def _ordinary_fixed_arguments_154(bank):
    state = bank.state
    return (state, os)

def _ordinary_fixed_arguments_155(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_156(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_157(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_158(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_159(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_160(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_161(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat)

def _ordinary_fixed_arguments_162(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_163(bank):
    digest = bank.digest
    state = bank.state
    return (state, digest)

def _ordinary_fixed_arguments_164(bank):
    state = bank.state
    return (state, [b'known', b'\x00\xff'])

def _ordinary_fixed_arguments_165(bank):
    state = bank.state
    return (state, RESERVATION)

def _ordinary_fixed_arguments_166(bank):
    item = bank.item
    state = bank.state
    return (state, str(item.data))

def _ordinary_fixed_arguments_167(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_168(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_169(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_170(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_171(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_172(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_173(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_174(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_175(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_176(bank):
    attrs_size = bank.attrs_size
    parent_handle = bank.parent_handle
    state = bank.state
    unicode_pointer = bank.unicode_pointer
    return (state, attrs_size, parent_handle, unicode_pointer, 64, None, None)

def _ordinary_fixed_arguments_177(bank):
    fixture_file_identity = bank.fixture_file_identity
    fixture_native = bank.fixture_native
    reservation_raw = bank.reservation_raw
    state = bank.state
    return (state, sha(reservation_raw), len(reservation_raw), fixture_native, fixture_file_identity)

def _ordinary_fixed_arguments_178(bank):
    parent_handle = bank.parent_handle
    state = bank.state
    return (state, False, parent_handle, len(RESERVATION.encode('utf-16-le')), RESERVATION)

def _ordinary_fixed_arguments_179(bank):
    state = bank.state
    return (state, b'known\x00\xff')

def _ordinary_fixed_arguments_180(bank):
    state = bank.state
    unicode_text_pointer = bank.unicode_text_pointer
    return (state, len(RESERVATION.encode('utf-16-le')), len(RESERVATION.encode('utf-16-le')) + 2, unicode_text_pointer)

def _ordinary_fixed_arguments_181(bank):
    state = bank.state
    return (state,)

def _ordinary_fixed_arguments_182(bank):
    item = bank.item
    state = bank.state
    return (state, item.data)

def _ordinary_fixed_arguments_183(bank):
    item = bank.item
    state = bank.state
    return (state, item.data)

def _ordinary_fixed_arguments_184(bank):
    state = bank.state
    return (state, 32)

def _ordinary_fixed_arguments_185(bank):
    state = bank.state
    return (state, 64)

def _ordinary_fixed_arguments_186(bank):
    state = bank.state
    token_buffer = bank.token_buffer
    return (state, token_buffer)

def _ordinary_fixed_arguments_187(bank):
    state = bank.state
    return (state, RESERVATION)

def _ordinary_fixed_arguments_188(bank):
    state = bank.state
    return (state, (b'known\x00\xff', 7))

def _ordinary_fixed_arguments_189(bank):
    item = bank.item
    state = bank.state
    return (state, item.profile)

def _ordinary_fixed_arguments_190(bank):
    reservation_raw = bank.reservation_raw
    state = bank.state
    return (state, reservation_raw)

def _ordinary_fixed_arguments_191(bank):
    path_stat = bank.path_stat
    state = bank.state
    return (state, path_stat, True)

def _ordinary_fixed_arguments_192(bank):
    state = bank.state
    return (state, set(), 'current')

def _ordinary_fixed_arguments_193(bank):
    state = bank.state
    return (state, RESERVATION, INIT_OPERATION)

ORDINARY_CONTROLLED_CASES = (({'case_id': 'R839-U-SEM-c9f7b20aa961f8367e97', 'module': 'locked', 'name': '_ns_append_directory', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_append_directory', 'arguments_helper': '_ordinary_fixed_arguments_0', 'group': 'physical'}, 'PRE'), ({'case_id': 'R839-U-SEM-c9f7b20aa961f8367e97', 'module': 'locked', 'name': '_ns_append_directory', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_append_directory', 'arguments_helper': '_ordinary_fixed_arguments_0', 'group': 'physical'}, 'POST'), ({'case_id': 'R839-U-SEM-ce108b1b35d1fa70ce0f', 'module': 'locked', 'name': '_ns_capture_factory', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_capture_factory', 'arguments_helper': '_ordinary_fixed_arguments_1', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-ce108b1b35d1fa70ce0f', 'module': 'locked', 'name': '_ns_capture_factory', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_capture_factory', 'arguments_helper': '_ordinary_fixed_arguments_1', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d5c3f009f30a484d8b15', 'module': 'locked', 'name': '_ns_complete_cross', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_complete_cross', 'arguments_helper': '_ordinary_fixed_arguments_2', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d5c3f009f30a484d8b15', 'module': 'locked', 'name': '_ns_complete_cross', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_complete_cross', 'arguments_helper': '_ordinary_fixed_arguments_2', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-7cd2a30ff6503f888206', 'module': 'locked', 'name': '_ns_deadlinedirectory_verify', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify', 'arguments_helper': '_ordinary_fixed_arguments_3', 'group': 'physical'}, 'PRE'), ({'case_id': 'R839-U-SEM-7cd2a30ff6503f888206', 'module': 'locked', 'name': '_ns_deadlinedirectory_verify', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_deadlinedirectory_verify', 'arguments_helper': '_ordinary_fixed_arguments_3', 'group': 'physical'}, 'POST'), ({'case_id': 'R839-U-SEM-2b604f5a427fdb6d3394', 'module': 'locked', 'name': '_ns_deadlineheldfile_bytes', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes', 'arguments_helper': '_ordinary_fixed_arguments_4', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-2b604f5a427fdb6d3394', 'module': 'locked', 'name': '_ns_deadlineheldfile_bytes', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_bytes', 'arguments_helper': '_ordinary_fixed_arguments_4', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-d3ac1c168c292fee6b25', 'module': 'locked', 'name': '_ns_deadlineheldfile_verify', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify', 'arguments_helper': '_ordinary_fixed_arguments_5', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-d3ac1c168c292fee6b25', 'module': 'locked', 'name': '_ns_deadlineheldfile_verify', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_deadlineheldfile_verify', 'arguments_helper': '_ordinary_fixed_arguments_5', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-71222b0f5404ef8f8435', 'module': 'locked', 'name': '_ns_evidence_blob', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob', 'arguments_helper': '_ordinary_fixed_arguments_6', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-71222b0f5404ef8f8435', 'module': 'locked', 'name': '_ns_evidence_blob', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_evidence_blob', 'arguments_helper': '_ordinary_fixed_arguments_6', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-27a86dd62131b5e283de', 'module': 'locked', 'name': '_ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_7', 'group': 'physical'}, 'PRE'), ({'case_id': 'R839-U-SEM-27a86dd62131b5e283de', 'module': 'locked', 'name': '_ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_approved_deadlinedirectory_with_same_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_7', 'group': 'physical'}, 'POST'), ({'case_id': 'R839-U-SEM-9fdc08558740c47b30cd', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_8', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-9fdc08558740c47b30cd', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_8', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-317f7326a1a68f5bd2fe', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_9', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-317f7326a1a68f5bd2fe', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_9', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-ad6722aba470c75df1c0', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_10', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-ad6722aba470c75df1c0', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_10', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-a7d56a3d3f8664712b26', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_11', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-a7d56a3d3f8664712b26', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_11', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-f080ebfec602c4426bb1', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_12', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-f080ebfec602c4426bb1', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_12', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-1cf77bedc003ba12715f', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_13', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-1cf77bedc003ba12715f', 'module': 'locked', 'name': '_ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_13', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-ebdef8d1760e9f40dd2f', 'module': 'locked', 'name': '_ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_14', 'group': 'scan'}, 'PRE'), ({'case_id': 'R839-U-SEM-ebdef8d1760e9f40dd2f', 'module': 'locked', 'name': '_ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_direntry_yielded_by_actual_known_scandir_iterator_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_14', 'group': 'scan'}, 'POST'), ({'case_id': 'R839-U-SEM-17c412e97665fb39bf41', 'module': 'locked', 'name': '_ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field', 'arguments_helper': '_ordinary_fixed_arguments_15', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-17c412e97665fb39bf41', 'module': 'locked', 'name': '_ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_evidence_bytes_original_ordinary_field', 'arguments_helper': '_ordinary_fixed_arguments_15', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-155d7a905706274cd9cc', 'module': 'locked', 'name': '_ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r', 'arguments_helper': '_ordinary_fixed_arguments_16', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-155d7a905706274cd9cc', 'module': 'locked', 'name': '_ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_original_originobservation_returned_by_complete_capture_intent_bytes_original_ordinary_field_r', 'arguments_helper': '_ordinary_fixed_arguments_16', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-728d570466b05117fd0f', 'module': 'locked', 'name': '_ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_17', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-728d570466b05117fd0f', 'module': 'locked', 'name': '_ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_17', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-fed03a8fedefd13d57ad', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_18', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-fed03a8fedefd13d57ad', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_byte_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_18', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-cc58de3e9de007d82ed3', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_19', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-cc58de3e9de007d82ed3', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_19', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-a72c75505b84b989bdc5', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_20', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-a72c75505b84b989bdc5', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_20', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-0445fc860adde162bc78', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_21', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-0445fc860adde162bc78', 'module': 'locked', 'name': '_ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_private_io_publishedfile_normal_publisher_result_raw_sha256_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_21', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e9d440cdd80df8da73b6', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_22', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e9d440cdd80df8da73b6', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_checkpoints_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_22', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d93831efd36bb8378144', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_23', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d93831efd36bb8378144', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_23', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c527a1465900cbcbf283', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_24', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c527a1465900cbcbf283', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_24', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-9443d09a5c0428fa4d55', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_25', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-9443d09a5c0428fa4d55', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_media_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_25', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f6e3b7c720e7fdeb952d', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_26', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-f6e3b7c720e7fdeb952d', 'module': 'locked', 'name': '_ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_exact_storageprofile_already_validated_reports_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_26', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-23d3bb532ac72ee9de87', 'module': 'locked', 'name': '_ns_get_pinned_imported_os_module_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_27', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-23d3bb532ac72ee9de87', 'module': 'locked', 'name': '_ns_get_pinned_imported_os_module_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_get_pinned_imported_os_module_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_27', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-02258e7920143d10e6d8', 'module': 'locked', 'name': '_ns_intent_blob', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_intent_blob', 'arguments_helper': '_ordinary_fixed_arguments_28', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-02258e7920143d10e6d8', 'module': 'locked', 'name': '_ns_intent_blob', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_intent_blob', 'arguments_helper': '_ordinary_fixed_arguments_28', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-22596de5701ee78f8c72', 'module': 'locked', 'name': '_ns_new_filesystem_facts', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts', 'arguments_helper': '_ordinary_fixed_arguments_29', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-22596de5701ee78f8c72', 'module': 'locked', 'name': '_ns_new_filesystem_facts', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_new_filesystem_facts', 'arguments_helper': '_ordinary_fixed_arguments_29', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-b4f987c4b538ead1e7f2', 'module': 'locked', 'name': '_ns_node_id', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_id', 'arguments_helper': '_ordinary_fixed_arguments_30', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b4f987c4b538ead1e7f2', 'module': 'locked', 'name': '_ns_node_id', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_id', 'arguments_helper': '_ordinary_fixed_arguments_30', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-92fd67f3b757f6c9df5d', 'module': 'locked', 'name': '_ns_node_public_byte_size', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size', 'arguments_helper': '_ordinary_fixed_arguments_31', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-92fd67f3b757f6c9df5d', 'module': 'locked', 'name': '_ns_node_public_byte_size', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_byte_size', 'arguments_helper': '_ordinary_fixed_arguments_31', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-e3f2fb68210fe729e09f', 'module': 'locked', 'name': '_ns_node_public_directory_identity', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity', 'arguments_helper': '_ordinary_fixed_arguments_32', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-e3f2fb68210fe729e09f', 'module': 'locked', 'name': '_ns_node_public_directory_identity', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_directory_identity', 'arguments_helper': '_ordinary_fixed_arguments_32', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-7006f2f7dbad24b66b83', 'module': 'locked', 'name': '_ns_node_public_handle_identity', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity', 'arguments_helper': '_ordinary_fixed_arguments_33', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-7006f2f7dbad24b66b83', 'module': 'locked', 'name': '_ns_node_public_handle_identity', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_handle_identity', 'arguments_helper': '_ordinary_fixed_arguments_33', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-3d938a3ac082ae7710bb', 'module': 'locked', 'name': '_ns_node_public_path_identity', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity', 'arguments_helper': '_ordinary_fixed_arguments_34', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-3d938a3ac082ae7710bb', 'module': 'locked', 'name': '_ns_node_public_path_identity', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_path_identity', 'arguments_helper': '_ordinary_fixed_arguments_34', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-3d775422852d76d78e15', 'module': 'locked', 'name': '_ns_node_public_sha256', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256', 'arguments_helper': '_ordinary_fixed_arguments_35', 'group': 'node'}, 'PRE'), ({'case_id': 'R839-U-SEM-3d775422852d76d78e15', 'module': 'locked', 'name': '_ns_node_public_sha256', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_node_public_sha256', 'arguments_helper': '_ordinary_fixed_arguments_35', 'group': 'node'}, 'POST'), ({'case_id': 'R839-U-SEM-b8a00b6018ab66414c39', 'module': 'locked', 'name': '_ns_origin_cross', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_origin_cross', 'arguments_helper': '_ordinary_fixed_arguments_36', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b8a00b6018ab66414c39', 'module': 'locked', 'name': '_ns_origin_cross', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_origin_cross', 'arguments_helper': '_ordinary_fixed_arguments_36', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-92a8bb79c78922132d7e', 'module': 'locked', 'name': '_ns_origin_decode', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_origin_decode', 'arguments_helper': '_ordinary_fixed_arguments_37', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-92a8bb79c78922132d7e', 'module': 'locked', 'name': '_ns_origin_decode', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_origin_decode', 'arguments_helper': '_ordinary_fixed_arguments_37', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-20a10948849e9d38ea31', 'module': 'locked', 'name': '_ns_physical_directory_factory', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory', 'arguments_helper': '_ordinary_fixed_arguments_38', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-20a10948849e9d38ea31', 'module': 'locked', 'name': '_ns_physical_directory_factory', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_physical_directory_factory', 'arguments_helper': '_ordinary_fixed_arguments_38', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-79b712f5db6e45a8e4a1', 'module': 'locked', 'name': '_ns_physical_path', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_physical_path', 'arguments_helper': '_ordinary_fixed_arguments_39', 'group': 'physical'}, 'PRE'), ({'case_id': 'R839-U-SEM-79b712f5db6e45a8e4a1', 'module': 'locked', 'name': '_ns_physical_path', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_physical_path', 'arguments_helper': '_ordinary_fixed_arguments_39', 'group': 'physical'}, 'POST'), ({'case_id': 'R839-U-SEM-5a7b16eaccc52a70932b', 'module': 'locked', 'name': '_ns_prefix_casefold_copy', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy', 'arguments_helper': '_ordinary_fixed_arguments_40', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-5a7b16eaccc52a70932b', 'module': 'locked', 'name': '_ns_prefix_casefold_copy', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_casefold_copy', 'arguments_helper': '_ordinary_fixed_arguments_40', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-919410e2fbace8572bfc', 'module': 'locked', 'name': '_ns_prefix_rstrip_copy', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy', 'arguments_helper': '_ordinary_fixed_arguments_41', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-919410e2fbace8572bfc', 'module': 'locked', 'name': '_ns_prefix_rstrip_copy', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_rstrip_copy', 'arguments_helper': '_ordinary_fixed_arguments_41', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-24d990082aebb81364ee', 'module': 'locked', 'name': '_ns_prefix_startswith', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith', 'arguments_helper': '_ordinary_fixed_arguments_42', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-24d990082aebb81364ee', 'module': 'locked', 'name': '_ns_prefix_startswith', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_prefix_startswith', 'arguments_helper': '_ordinary_fixed_arguments_42', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-850f41536d80c1347e94', 'module': 'locked', 'name': '_ns_profile_key', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_profile_key', 'arguments_helper': '_ordinary_fixed_arguments_43', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-850f41536d80c1347e94', 'module': 'locked', 'name': '_ns_profile_key', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_profile_key', 'arguments_helper': '_ordinary_fixed_arguments_43', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-0dc2094ceb84df27b5ba', 'module': 'locked', 'name': '_ns_record_encode', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_record_encode', 'arguments_helper': '_ordinary_fixed_arguments_44', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-0dc2094ceb84df27b5ba', 'module': 'locked', 'name': '_ns_record_encode', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_record_encode', 'arguments_helper': '_ordinary_fixed_arguments_44', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-3f0141cc24931a0eb477', 'module': 'locked', 'name': '_ns_reservation_cross', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross', 'arguments_helper': '_ordinary_fixed_arguments_45', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-3f0141cc24931a0eb477', 'module': 'locked', 'name': '_ns_reservation_cross', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_reservation_cross', 'arguments_helper': '_ordinary_fixed_arguments_45', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-0fc858a18c279024e617', 'module': 'locked', 'name': '_ns_scan_next', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_scan_next', 'arguments_helper': '_ordinary_fixed_arguments_46', 'group': 'scan'}, 'PRE'), ({'case_id': 'R839-U-SEM-0fc858a18c279024e617', 'module': 'locked', 'name': '_ns_scan_next', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_scan_next', 'arguments_helper': '_ordinary_fixed_arguments_46', 'group': 'scan'}, 'POST'), ({'case_id': 'R839-U-SEM-61848414a4c5b0679786', 'module': 'locked', 'name': '_ns_set_add_name', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_set_add_name', 'arguments_helper': '_ordinary_fixed_arguments_47', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-61848414a4c5b0679786', 'module': 'locked', 'name': '_ns_set_add_name', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_set_add_name', 'arguments_helper': '_ordinary_fixed_arguments_47', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-b039a4fe76cf95e24253', 'module': 'locked', 'name': '_ns_sha', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_sha', 'arguments_helper': '_ordinary_fixed_arguments_48', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b039a4fe76cf95e24253', 'module': 'locked', 'name': '_ns_sha', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_sha', 'arguments_helper': '_ordinary_fixed_arguments_48', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f9ad618eef0f8846dd3b', 'module': 'locked', 'name': '_ns_uuid', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_uuid', 'arguments_helper': '_ordinary_fixed_arguments_49', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-f9ad618eef0f8846dd3b', 'module': 'locked', 'name': '_ns_uuid', 'qualified_name': 'reno.personal_authority_namespace3_effect_deadline._ns_uuid', 'arguments_helper': '_ordinary_fixed_arguments_49', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-b6e71d60f71a7eeba487', 'module': 'publication', 'name': '_append_bytes', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._append_bytes', 'arguments_helper': '_ordinary_fixed_arguments_50', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b6e71d60f71a7eeba487', 'module': 'publication', 'name': '_append_bytes', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._append_bytes', 'arguments_helper': '_ordinary_fixed_arguments_50', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-00e6e026914e5e1687dd', 'module': 'publication', 'name': '_copy_dict', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._copy_dict', 'arguments_helper': '_ordinary_fixed_arguments_51', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-00e6e026914e5e1687dd', 'module': 'publication', 'name': '_copy_dict', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._copy_dict', 'arguments_helper': '_ordinary_fixed_arguments_51', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c0b838abfcb7c9a6b7dd', 'module': 'publication', 'name': '_ct_byref_info_value_l365c54', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54', 'arguments_helper': '_ordinary_fixed_arguments_52', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c0b838abfcb7c9a6b7dd', 'module': 'publication', 'name': '_ct_byref_info_value_l365c54', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_info_value_l365c54', 'arguments_helper': '_ordinary_fixed_arguments_52', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-90c0cb48f3b59b8ddb78', 'module': 'publication', 'name': '_ct_byref_private_descriptor_descriptor_l291c50', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50', 'arguments_helper': '_ordinary_fixed_arguments_53', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-90c0cb48f3b59b8ddb78', 'module': 'publication', 'name': '_ct_byref_private_descriptor_descriptor_l291c50', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_descriptor_l291c50', 'arguments_helper': '_ordinary_fixed_arguments_53', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-b4f06891469c8cd3110f', 'module': 'publication', 'name': '_ct_byref_private_descriptor_text_l280c55', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55', 'arguments_helper': '_ordinary_fixed_arguments_54', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b4f06891469c8cd3110f', 'module': 'publication', 'name': '_ct_byref_private_descriptor_text_l280c55', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_private_descriptor_text_l280c55', 'arguments_helper': '_ordinary_fixed_arguments_54', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-84e779bfc6f82d9ed5e8', 'module': 'publication', 'name': '_ct_byref_publish_owned_status_l627c54', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54', 'arguments_helper': '_ordinary_fixed_arguments_55', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-84e779bfc6f82d9ed5e8', 'module': 'publication', 'name': '_ct_byref_publish_owned_status_l627c54', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_status_l627c54', 'arguments_helper': '_ordinary_fixed_arguments_55', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-cb535468ddbedc038db6', 'module': 'publication', 'name': '_ct_byref_publish_owned_value_l627c76', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76', 'arguments_helper': '_ordinary_fixed_arguments_56', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-cb535468ddbedc038db6', 'module': 'publication', 'name': '_ct_byref_publish_owned_value_l627c76', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_publish_owned_value_l627c76', 'arguments_helper': '_ordinary_fixed_arguments_56', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-1bbf62686ff18bb053b8', 'module': 'publication', 'name': '_ct_byref_read_chunk_count_l517c52', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52', 'arguments_helper': '_ordinary_fixed_arguments_57', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-1bbf62686ff18bb053b8', 'module': 'publication', 'name': '_ct_byref_read_chunk_count_l517c52', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_read_chunk_count_l517c52', 'arguments_helper': '_ordinary_fixed_arguments_57', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f244a7f12a49561ba13c', 'module': 'publication', 'name': '_ct_byref_relative_open_attributes_l402c63', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63', 'arguments_helper': '_ordinary_fixed_arguments_58', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-f244a7f12a49561ba13c', 'module': 'publication', 'name': '_ct_byref_relative_open_attributes_l402c63', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_attributes_l402c63', 'arguments_helper': '_ordinary_fixed_arguments_58', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-ee289b59b63167ab6f4e', 'module': 'publication', 'name': '_ct_byref_relative_open_handle_l402c33', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33', 'arguments_helper': '_ordinary_fixed_arguments_59', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-ee289b59b63167ab6f4e', 'module': 'publication', 'name': '_ct_byref_relative_open_handle_l402c33', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_handle_l402c33', 'arguments_helper': '_ordinary_fixed_arguments_59', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-a3996c71ab161266231c', 'module': 'publication', 'name': '_ct_byref_relative_open_status_l402c89', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89', 'arguments_helper': '_ordinary_fixed_arguments_60', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-a3996c71ab161266231c', 'module': 'publication', 'name': '_ct_byref_relative_open_status_l402c89', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_relative_open_status_l402c89', 'arguments_helper': '_ordinary_fixed_arguments_60', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e7701027c2dbfcfe9b4a', 'module': 'publication', 'name': '_ct_byref_security_check_control_l318c68', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68', 'arguments_helper': '_ordinary_fixed_arguments_61', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e7701027c2dbfcfe9b4a', 'module': 'publication', 'name': '_ct_byref_security_check_control_l318c68', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_control_l318c68', 'arguments_helper': '_ordinary_fixed_arguments_61', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-36bc30c3d4a5bc56ed7d', 'module': 'publication', 'name': '_ct_byref_security_check_dacl_l306c39', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39', 'arguments_helper': '_ordinary_fixed_arguments_62', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-36bc30c3d4a5bc56ed7d', 'module': 'publication', 'name': '_ct_byref_security_check_dacl_l306c39', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_dacl_l306c39', 'arguments_helper': '_ordinary_fixed_arguments_62', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c7baa04dedb3eb9ba853', 'module': 'publication', 'name': '_ct_byref_security_check_descriptor_l306c65', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65', 'arguments_helper': '_ordinary_fixed_arguments_63', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c7baa04dedb3eb9ba853', 'module': 'publication', 'name': '_ct_byref_security_check_descriptor_l306c65', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_descriptor_l306c65', 'arguments_helper': '_ordinary_fixed_arguments_63', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-69407d0eba7d7ed3e679', 'module': 'publication', 'name': '_ct_byref_security_check_owner_l305c55', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55', 'arguments_helper': '_ordinary_fixed_arguments_64', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-69407d0eba7d7ed3e679', 'module': 'publication', 'name': '_ct_byref_security_check_owner_l305c55', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_owner_l305c55', 'arguments_helper': '_ordinary_fixed_arguments_64', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-fd75a7a943785c1b4ea5', 'module': 'publication', 'name': '_ct_byref_security_check_pointer_l338c51', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51', 'arguments_helper': '_ordinary_fixed_arguments_65', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-fd75a7a943785c1b4ea5', 'module': 'publication', 'name': '_ct_byref_security_check_pointer_l338c51', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_pointer_l338c51', 'arguments_helper': '_ordinary_fixed_arguments_65', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-2aa2bc7b08f5e38f8d6a', 'module': 'publication', 'name': '_ct_byref_security_check_revision_l318c91', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91', 'arguments_helper': '_ordinary_fixed_arguments_66', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-2aa2bc7b08f5e38f8d6a', 'module': 'publication', 'name': '_ct_byref_security_check_revision_l318c91', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_revision_l318c91', 'arguments_helper': '_ordinary_fixed_arguments_66', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-ff1330b1927c300a8442', 'module': 'publication', 'name': '_ct_byref_security_check_size_l326c51', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51', 'arguments_helper': '_ordinary_fixed_arguments_67', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-ff1330b1927c300a8442', 'module': 'publication', 'name': '_ct_byref_security_check_size_l326c51', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_security_check_size_l326c51', 'arguments_helper': '_ordinary_fixed_arguments_67', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-83033073e70a09758356', 'module': 'publication', 'name': '_ct_byref_system_sid_sid_l266c54', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54', 'arguments_helper': '_ordinary_fixed_arguments_68', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-83033073e70a09758356', 'module': 'publication', 'name': '_ct_byref_system_sid_sid_l266c54', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_system_sid_sid_l266c54', 'arguments_helper': '_ordinary_fixed_arguments_68', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c997ead31cfd6938a6c7', 'module': 'publication', 'name': '_ct_byref_token_user_length_l238c65', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65', 'arguments_helper': '_ordinary_fixed_arguments_69', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c997ead31cfd6938a6c7', 'module': 'publication', 'name': '_ct_byref_token_user_length_l238c65', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l238c65', 'arguments_helper': '_ordinary_fixed_arguments_69', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d318ee7c1c06fca06e26', 'module': 'publication', 'name': '_ct_byref_token_user_length_l245c74', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74', 'arguments_helper': '_ordinary_fixed_arguments_70', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d318ee7c1c06fca06e26', 'module': 'publication', 'name': '_ct_byref_token_user_length_l245c74', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_length_l245c74', 'arguments_helper': '_ordinary_fixed_arguments_70', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-eafd75de474960f37e89', 'module': 'publication', 'name': '_ct_byref_token_user_token_l233c48', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48', 'arguments_helper': '_ordinary_fixed_arguments_71', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-eafd75de474960f37e89', 'module': 'publication', 'name': '_ct_byref_token_user_token_l233c48', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_token_user_token_l233c48', 'arguments_helper': '_ordinary_fixed_arguments_71', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-55fe088edda53612b7f6', 'module': 'publication', 'name': '_ct_byref_write_chunk_count_l529c53', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53', 'arguments_helper': '_ordinary_fixed_arguments_72', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-55fe088edda53612b7f6', 'module': 'publication', 'name': '_ct_byref_write_chunk_count_l529c53', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_byref_write_chunk_count_l529c53', 'arguments_helper': '_ordinary_fixed_arguments_72', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-225f4e9412dfccbec16c', 'module': 'publication', 'name': '_ct_cast_borrowed_ace_structure_pointer_l344c22', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22', 'arguments_helper': '_ordinary_fixed_arguments_73', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-225f4e9412dfccbec16c', 'module': 'publication', 'name': '_ct_cast_borrowed_ace_structure_pointer_l344c22', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_cast_borrowed_ace_structure_pointer_l344c22', 'arguments_helper': '_ordinary_fixed_arguments_73', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-a420625e46792da5fbaf', 'module': 'publication', 'name': '_ct_cast_relative_unicode_buffer_pointer_l396c61', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61', 'arguments_helper': '_ordinary_fixed_arguments_74', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-a420625e46792da5fbaf', 'module': 'publication', 'name': '_ct_cast_relative_unicode_buffer_pointer_l396c61', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_cast_relative_unicode_buffer_pointer_l396c61', 'arguments_helper': '_ordinary_fixed_arguments_74', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-6abe585c2d08b7923562', 'module': 'publication', 'name': '_ct_cast_token_buffer_sid_pointer_l251c14', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14', 'arguments_helper': '_ordinary_fixed_arguments_75', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-6abe585c2d08b7923562', 'module': 'publication', 'name': '_ct_cast_token_buffer_sid_pointer_l251c14', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_cast_token_buffer_sid_pointer_l251c14', 'arguments_helper': '_ordinary_fixed_arguments_75', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d27fa4439f01f4be4339', 'module': 'publication', 'name': '_ct_get_borrowed_ace_structure_pointer_l344c22', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22', 'arguments_helper': '_ordinary_fixed_arguments_76', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d27fa4439f01f4be4339', 'module': 'publication', 'name': '_ct_get_borrowed_ace_structure_pointer_l344c22', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_get_borrowed_ace_structure_pointer_l344c22', 'arguments_helper': '_ordinary_fixed_arguments_76', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-a0766b7561979b06d5d7', 'module': 'publication', 'name': '_ct_get_token_buffer_sid_pointer_l251c14', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14', 'arguments_helper': '_ordinary_fixed_arguments_77', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-a0766b7561979b06d5d7', 'module': 'publication', 'name': '_ct_get_token_buffer_sid_pointer_l251c14', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_get_token_buffer_sid_pointer_l251c14', 'arguments_helper': '_ordinary_fixed_arguments_77', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c5a0f44826a8563a5246', 'module': 'publication', 'name': '_ct_pointer_relative_unicode_l398c34', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34', 'arguments_helper': '_ordinary_fixed_arguments_78', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c5a0f44826a8563a5246', 'module': 'publication', 'name': '_ct_pointer_relative_unicode_l398c34', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_pointer_relative_unicode_l398c34', 'arguments_helper': '_ordinary_fixed_arguments_78', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-835c7bebed259b12d484', 'module': 'publication', 'name': '_ct_pointer_type_AllowedAce_l344c43', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43', 'arguments_helper': '_ordinary_fixed_arguments_79', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-835c7bebed259b12d484', 'module': 'publication', 'name': '_ct_pointer_type_AllowedAce_l344c43', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_pointer_type_AllowedAce_l344c43', 'arguments_helper': '_ordinary_fixed_arguments_79', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-8b44fa29717076f09c9b', 'module': 'publication', 'name': '_ct_pointer_type_w_LPVOID_l251c34', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34', 'arguments_helper': '_ordinary_fixed_arguments_80', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-8b44fa29717076f09c9b', 'module': 'publication', 'name': '_ct_pointer_type_w_LPVOID_l251c34', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_pointer_type_w_LPVOID_l251c34', 'arguments_helper': '_ordinary_fixed_arguments_80', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-612c83ee5ef12961778e', 'module': 'publication', 'name': '_ct_sizeof_acl_instance_l326c71', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71', 'arguments_helper': '_ordinary_fixed_arguments_81', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-612c83ee5ef12961778e', 'module': 'publication', 'name': '_ct_sizeof_acl_instance_l326c71', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_acl_instance_l326c71', 'arguments_helper': '_ordinary_fixed_arguments_81', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-fc7259ee696a4f07c754', 'module': 'publication', 'name': '_ct_sizeof_current_rename_instance_l627c97', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97', 'arguments_helper': '_ordinary_fixed_arguments_82', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-fc7259ee696a4f07c754', 'module': 'publication', 'name': '_ct_sizeof_current_rename_instance_l627c97', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_current_rename_instance_l627c97', 'arguments_helper': '_ordinary_fixed_arguments_82', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-3fd69a6cf2dd4c9ea370', 'module': 'publication', 'name': '_ct_sizeof_fill_pointer_minimum_l249c15', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15', 'arguments_helper': '_ordinary_fixed_arguments_83', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-3fd69a6cf2dd4c9ea370', 'module': 'publication', 'name': '_ct_sizeof_fill_pointer_minimum_l249c15', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_fill_pointer_minimum_l249c15', 'arguments_helper': '_ordinary_fixed_arguments_83', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-295052fe7546f85d141f', 'module': 'publication', 'name': '_ct_sizeof_object_attributes_type_l397c35', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35', 'arguments_helper': '_ordinary_fixed_arguments_84', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-295052fe7546f85d141f', 'module': 'publication', 'name': '_ct_sizeof_object_attributes_type_l397c35', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_object_attributes_type_l397c35', 'arguments_helper': '_ordinary_fixed_arguments_84', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-1c7989d12adf13f2a23b', 'module': 'publication', 'name': '_ct_sizeof_probe_pointer_minimum_l241c43', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43', 'arguments_helper': '_ordinary_fixed_arguments_85', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-1c7989d12adf13f2a23b', 'module': 'publication', 'name': '_ct_sizeof_probe_pointer_minimum_l241c43', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_probe_pointer_minimum_l241c43', 'arguments_helper': '_ordinary_fixed_arguments_85', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f1dd7ba855be2e5aef9f', 'module': 'publication', 'name': '_ct_sizeof_supported_attributes_l180c13', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13', 'arguments_helper': '_ordinary_fixed_arguments_86', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-f1dd7ba855be2e5aef9f', 'module': 'publication', 'name': '_ct_sizeof_supported_attributes_l180c13', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_attributes_l180c13', 'arguments_helper': '_ordinary_fixed_arguments_86', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c5083d44732205e6b699', 'module': 'publication', 'name': '_ct_sizeof_supported_pointer_width_l179c31', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31', 'arguments_helper': '_ordinary_fixed_arguments_87', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c5083d44732205e6b699', 'module': 'publication', 'name': '_ct_sizeof_supported_pointer_width_l179c31', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_pointer_width_l179c31', 'arguments_helper': '_ordinary_fixed_arguments_87', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d8b49f91f14dfdb67ba4', 'module': 'publication', 'name': '_ct_sizeof_supported_status_l180c13', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13', 'arguments_helper': '_ordinary_fixed_arguments_88', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d8b49f91f14dfdb67ba4', 'module': 'publication', 'name': '_ct_sizeof_supported_status_l180c13', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_status_l180c13', 'arguments_helper': '_ordinary_fixed_arguments_88', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e60f3081e34fb0a4394b', 'module': 'publication', 'name': '_ct_sizeof_supported_unicode_l180c13', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13', 'arguments_helper': '_ordinary_fixed_arguments_89', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e60f3081e34fb0a4394b', 'module': 'publication', 'name': '_ct_sizeof_supported_unicode_l180c13', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._ct_sizeof_supported_unicode_l180c13', 'arguments_helper': '_ordinary_fixed_arguments_89', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-8e2fe4cbc567d5275264', 'module': 'publication', 'name': '_deepcopy_fixed_json', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json', 'arguments_helper': '_ordinary_fixed_arguments_90', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-8e2fe4cbc567d5275264', 'module': 'publication', 'name': '_deepcopy_fixed_json', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._deepcopy_fixed_json', 'arguments_helper': '_ordinary_fixed_arguments_90', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-3d6225cddf3e14ee6f63', 'module': 'publication', 'name': '_entry_name', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._entry_name', 'arguments_helper': '_ordinary_fixed_arguments_91', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-3d6225cddf3e14ee6f63', 'module': 'publication', 'name': '_entry_name', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._entry_name', 'arguments_helper': '_ordinary_fixed_arguments_91', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-19c62edf4bc4933e2dcc', 'module': 'publication', 'name': '_get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_92', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-19c62edf4bc4933e2dcc', 'module': 'publication', 'name': '_get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_flags_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_92', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-6774a4e280f3aae9418b', 'module': 'publication', 'name': '_get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_93', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-6774a4e280f3aae9418b', 'module': 'publication', 'name': '_get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_kind_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_93', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-845582404544f34a17bc', 'module': 'publication', 'name': '_get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_94', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-845582404544f34a17bc', 'module': 'publication', 'name': '_get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aceheader_borrowed_field_of_known_allowedace_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_94', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-247b041aeb26885d61f4', 'module': 'publication', 'name': '_get_exact_aclsize_output_count_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_95', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-247b041aeb26885d61f4', 'module': 'publication', 'name': '_get_exact_aclsize_output_count_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_count_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_95', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-2d8d4a671cfb91321770', 'module': 'publication', 'name': '_get_exact_aclsize_output_used_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_96', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-2d8d4a671cfb91321770', 'module': 'publication', 'name': '_get_exact_aclsize_output_used_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_aclsize_output_used_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_96', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-51c11a90399485e1f4d7', 'module': 'publication', 'name': '_get_exact_allowedace_borrowed_view_header_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_97', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-51c11a90399485e1f4d7', 'module': 'publication', 'name': '_get_exact_allowedace_borrowed_view_header_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_header_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_97', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-dec731df4d17edcf5195', 'module': 'publication', 'name': '_get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_98', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-dec731df4d17edcf5195', 'module': 'publication', 'name': '_get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_allowedace_borrowed_view_mask_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_98', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-86e24420d6b573764f3b', 'module': 'publication', 'name': '_get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_99', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-86e24420d6b573764f3b', 'module': 'publication', 'name': '_get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_failure_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_99', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-809edc2574ed27c59b09', 'module': 'publication', 'name': '_get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_100', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-809edc2574ed27c59b09', 'module': 'publication', 'name': '_get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_retired_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_100', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-750f0a7c8de08e7bc565', 'module': 'publication', 'name': '_get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_101', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-750f0a7c8de08e7bc565', 'module': 'publication', 'name': '_get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_101', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-9e597c4c063bd444d853', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_102', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-9e597c4c063bd444d853', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_budget_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_102', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-e76c9d7173b8019c2530', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_103', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-e76c9d7173b8019c2530', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_handle_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_103', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-34ab313e82d5c400680b', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_104', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-34ab313e82d5c400680b', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_104', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-b6767078d28cc716e09b', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_105', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-b6767078d28cc716e09b', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_105', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-2abcd8ca3c3778fc1b79', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_106', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-2abcd8ca3c3778fc1b79', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_owner_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_106', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-b1a6a46f0c2d08a188e3', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_107', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-b1a6a46f0c2d08a188e3', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_parent_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_107', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-b5e768969650647dfa46', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_108', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-b5e768969650647dfa46', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_108', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-608c0140fa015358b1f8', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_109', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-608c0140fa015358b1f8', 'module': 'publication', 'name': '_get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_directory_with_same_b_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_109', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-510532681bd73a96bd3e', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_110', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-510532681bd73a96bd3e', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_byte_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_110', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-1669e459200afed198d5', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_111', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-1669e459200afed198d5', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_111', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-44025b7d618bbb255dd1', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_112', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-44025b7d618bbb255dd1', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_112', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-b3fe9e5c77a80fdb2d16', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_113', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-b3fe9e5c77a80fdb2d16', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_113', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-d5b95692e052bf468ab6', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_114', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-d5b95692e052bf468ab6', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_known_lifetime_raw_sha256_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_114', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-25541e1d0ec2c4475cac', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_115', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-25541e1d0ec2c4475cac', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_budget_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_115', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-da57293e402a22dbcda0', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_116', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-da57293e402a22dbcda0', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_byte_size_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_116', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-ab042f06b3eb14e049a7', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_117', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-ab042f06b3eb14e049a7', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_directory_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_117', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-468d3b1263eea0a5ed64', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_118', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-468d3b1263eea0a5ed64', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_maximum_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_118', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-d02ef662793a6433632a', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_119', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-d02ef662793a6433632a', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_native_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_119', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-3bb67565ecf408c64010', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_120', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-3bb67565ecf408c64010', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_owner_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_120', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-097b22a04791def018f6', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_121', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-097b22a04791def018f6', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_121', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-86526967e207c21e3b0d', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_122', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-86526967e207c21e3b0d', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_path_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_122', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-d89185d9c03d75cd14a9', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_123', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-d89185d9c03d75cd14a9', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_123', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-5c8800f0f608c6f84bb0', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_124', 'group': 'held'}, 'PRE'), ({'case_id': 'R839-U-SEM-5c8800f0f608c6f84bb0', 'module': 'publication', 'name': '_get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_private_io_held_control_with_same_b_t_stamp_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_124', 'group': 'held'}, 'POST'), ({'case_id': 'R839-U-SEM-b49e45171ba25d5c1ea1', 'module': 'publication', 'name': '_get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_125', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b49e45171ba25d5c1ea1', 'module': 'publication', 'name': '_get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_current_read_ctypes_buffer_raw_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_125', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c27c1aabfe43fdd1acd3', 'module': 'publication', 'name': '_get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_126', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c27c1aabfe43fdd1acd3', 'module': 'publication', 'name': '_get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_high_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_126', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-4ddaca2f23750d483d32', 'module': 'publication', 'name': '_get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_127', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-4ddaca2f23750d483d32', 'module': 'publication', 'name': '_get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_filetime_borrowed_field_of_fresh_handleinfo_low_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_127', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-6ba7fbd35f50342047d0', 'module': 'publication', 'name': '_get_exact_handleinfo_output_attributes_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_128', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-6ba7fbd35f50342047d0', 'module': 'publication', 'name': '_get_exact_handleinfo_output_attributes_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_attributes_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_128', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-ce1d77f3929c4febdf57', 'module': 'publication', 'name': '_get_exact_handleinfo_output_created_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_129', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-ce1d77f3929c4febdf57', 'module': 'publication', 'name': '_get_exact_handleinfo_output_created_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_created_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_129', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-130efd2936c4cd2c7ba8', 'module': 'publication', 'name': '_get_exact_handleinfo_output_index_high_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_130', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-130efd2936c4cd2c7ba8', 'module': 'publication', 'name': '_get_exact_handleinfo_output_index_high_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_high_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_130', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f140c3268740bac3674b', 'module': 'publication', 'name': '_get_exact_handleinfo_output_index_low_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_131', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-f140c3268740bac3674b', 'module': 'publication', 'name': '_get_exact_handleinfo_output_index_low_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_index_low_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_131', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f8c1763204d1c8b12c0f', 'module': 'publication', 'name': '_get_exact_handleinfo_output_links_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_132', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-f8c1763204d1c8b12c0f', 'module': 'publication', 'name': '_get_exact_handleinfo_output_links_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_links_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_132', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-202108a610a2f40f9182', 'module': 'publication', 'name': '_get_exact_handleinfo_output_size_high_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_133', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-202108a610a2f40f9182', 'module': 'publication', 'name': '_get_exact_handleinfo_output_size_high_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_high_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_133', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-edb461dfee53504f9d6b', 'module': 'publication', 'name': '_get_exact_handleinfo_output_size_low_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_134', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-edb461dfee53504f9d6b', 'module': 'publication', 'name': '_get_exact_handleinfo_output_size_low_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_size_low_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_134', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-2e98a6e04fb478838fb7', 'module': 'publication', 'name': '_get_exact_handleinfo_output_volume_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_135', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-2e98a6e04fb478838fb7', 'module': 'publication', 'name': '_get_exact_handleinfo_output_volume_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_volume_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_135', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-50f98ce7e92bd3e34adf', 'module': 'publication', 'name': '_get_exact_handleinfo_output_written_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_136', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-50f98ce7e92bd3e34adf', 'module': 'publication', 'name': '_get_exact_handleinfo_output_written_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_handleinfo_output_written_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_136', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-fb768889d124f0d5a764', 'module': 'publication', 'name': '_get_exact_iostatus_output_information_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_137', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-fb768889d124f0d5a764', 'module': 'publication', 'name': '_get_exact_iostatus_output_information_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_137', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e984c10f00ae9795dec7', 'module': 'publication', 'name': '_get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_138', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e984c10f00ae9795dec7', 'module': 'publication', 'name': '_get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_dev_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_138', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d0fa5e70033d29f12e08', 'module': 'publication', 'name': '_get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_139', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d0fa5e70033d29f12e08', 'module': 'publication', 'name': '_get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_os_stat_result_fresh_path_observation_st_ino_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_139', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-1ea136d4531bdfda967f', 'module': 'publication', 'name': '_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_140', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-1ea136d4531bdfda967f', 'module': 'publication', 'name': '_get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_path_exact_profile_database_parent_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_140', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-0745bd4ee3eab793a635', 'module': 'publication', 'name': '_get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_141', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-0745bd4ee3eab793a635', 'module': 'publication', 'name': '_get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_private_io_owner_with_current_slot_lifetime_retired_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_141', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-bfbc9390d688d97b3e31', 'module': 'publication', 'name': '_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_142', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-bfbc9390d688d97b3e31', 'module': 'publication', 'name': '_get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_database_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_142', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-6dbc8b98c96ab42949fc', 'module': 'publication', 'name': '_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_143', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-6dbc8b98c96ab42949fc', 'module': 'publication', 'name': '_get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_storageprofile_already_validated_identity_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_143', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d517c4a5989a57fa09f8', 'module': 'publication', 'name': '_get_exact_w_dword_output_value_fill_length_later_reread_after_248', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248', 'arguments_helper': '_ordinary_fixed_arguments_144', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d517c4a5989a57fa09f8', 'module': 'publication', 'name': '_get_exact_w_dword_output_value_fill_length_later_reread_after_248', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_fill_length_later_reread_after_248', 'arguments_helper': '_ordinary_fixed_arguments_144', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-86e0adb0545788b91631', 'module': 'publication', 'name': '_get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240', 'arguments_helper': '_ordinary_fixed_arguments_145', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-86e0adb0545788b91631', 'module': 'publication', 'name': '_get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_buffer_size_reread_after_240', 'arguments_helper': '_ordinary_fixed_arguments_145', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e1590d8e6b14ef2cba93', 'module': 'publication', 'name': '_get_exact_w_dword_output_value_probe_length_domain_after_240', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240', 'arguments_helper': '_ordinary_fixed_arguments_146', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e1590d8e6b14ef2cba93', 'module': 'publication', 'name': '_get_exact_w_dword_output_value_probe_length_domain_after_240', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_dword_output_value_probe_length_domain_after_240', 'arguments_helper': '_ordinary_fixed_arguments_146', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-7253ecece99ed6261a81', 'module': 'publication', 'name': '_get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_147', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-7253ecece99ed6261a81', 'module': 'publication', 'name': '_get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_allocation_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_147', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-b2ea1b265ceee2c3df22', 'module': 'publication', 'name': '_get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_148', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b2ea1b265ceee2c3df22', 'module': 'publication', 'name': '_get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_ace_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_148', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-90d8a01d0ecf32343107', 'module': 'publication', 'name': '_get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_149', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-90d8a01d0ecf32343107', 'module': 'publication', 'name': '_get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_dacl_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_149', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-6a90e6d215547c58adab', 'module': 'publication', 'name': '_get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_150', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-6a90e6d215547c58adab', 'module': 'publication', 'name': '_get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_borrowed_owner_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_150', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-0f866cf4b47cb5bd8f85', 'module': 'publication', 'name': '_get_exact_w_lpvoid_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_151', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-0f866cf4b47cb5bd8f85', 'module': 'publication', 'name': '_get_exact_w_lpvoid_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpvoid_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_151', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e4e4fa3a2948063b812e', 'module': 'publication', 'name': '_get_exact_w_lpwstr_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_152', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e4e4fa3a2948063b812e', 'module': 'publication', 'name': '_get_exact_w_lpwstr_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_lpwstr_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_152', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-fff4506732da4c6c570f', 'module': 'publication', 'name': '_get_exact_w_word_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_153', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-fff4506732da4c6c570f', 'module': 'publication', 'name': '_get_exact_w_word_output_value_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_exact_w_word_output_value_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_153', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-4bba41d37f900af04b06', 'module': 'publication', 'name': '_get_pinned_imported_os_module_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_154', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-4bba41d37f900af04b06', 'module': 'publication', 'name': '_get_pinned_imported_os_module_name_original_ordinary_field_read', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_pinned_imported_os_module_name_original_ordinary_field_read', 'arguments_helper': '_ordinary_fixed_arguments_154', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-33879ed986e10208ab4d', 'module': 'publication', 'name': '_get_stat_st_ctime_ns', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns', 'arguments_helper': '_ordinary_fixed_arguments_155', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-33879ed986e10208ab4d', 'module': 'publication', 'name': '_get_stat_st_ctime_ns', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_ctime_ns', 'arguments_helper': '_ordinary_fixed_arguments_155', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-194362f46cb940bf7e7d', 'module': 'publication', 'name': '_get_stat_st_dev', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_dev', 'arguments_helper': '_ordinary_fixed_arguments_156', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-194362f46cb940bf7e7d', 'module': 'publication', 'name': '_get_stat_st_dev', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_dev', 'arguments_helper': '_ordinary_fixed_arguments_156', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-3358cb1ff50bec7c1ac3', 'module': 'publication', 'name': '_get_stat_st_ino', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_ino', 'arguments_helper': '_ordinary_fixed_arguments_157', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-3358cb1ff50bec7c1ac3', 'module': 'publication', 'name': '_get_stat_st_ino', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_ino', 'arguments_helper': '_ordinary_fixed_arguments_157', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-0f18e86e3bed0a7f382b', 'module': 'publication', 'name': '_get_stat_st_mode', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_mode', 'arguments_helper': '_ordinary_fixed_arguments_158', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-0f18e86e3bed0a7f382b', 'module': 'publication', 'name': '_get_stat_st_mode', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_mode', 'arguments_helper': '_ordinary_fixed_arguments_158', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-161c6fa9206263eab80f', 'module': 'publication', 'name': '_get_stat_st_mtime_ns', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns', 'arguments_helper': '_ordinary_fixed_arguments_159', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-161c6fa9206263eab80f', 'module': 'publication', 'name': '_get_stat_st_mtime_ns', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_mtime_ns', 'arguments_helper': '_ordinary_fixed_arguments_159', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-1438116a126756a8a7dc', 'module': 'publication', 'name': '_get_stat_st_nlink', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink', 'arguments_helper': '_ordinary_fixed_arguments_160', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-1438116a126756a8a7dc', 'module': 'publication', 'name': '_get_stat_st_nlink', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_nlink', 'arguments_helper': '_ordinary_fixed_arguments_160', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-1181192c0b4a5e244b3a', 'module': 'publication', 'name': '_get_stat_st_size', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_size', 'arguments_helper': '_ordinary_fixed_arguments_161', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-1181192c0b4a5e244b3a', 'module': 'publication', 'name': '_get_stat_st_size', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._get_stat_st_size', 'arguments_helper': '_ordinary_fixed_arguments_161', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-774ccab23c05419b2737', 'module': 'publication', 'name': '_guard', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._guard', 'arguments_helper': '_ordinary_fixed_arguments_162', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-774ccab23c05419b2737', 'module': 'publication', 'name': '_guard', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._guard', 'arguments_helper': '_ordinary_fixed_arguments_162', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-93127bded222d5c1dea6', 'module': 'publication', 'name': '_hash_hex', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._hash_hex', 'arguments_helper': '_ordinary_fixed_arguments_163', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-93127bded222d5c1dea6', 'module': 'publication', 'name': '_hash_hex', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._hash_hex', 'arguments_helper': '_ordinary_fixed_arguments_163', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-5dd76ee613e6fbffc062', 'module': 'publication', 'name': '_join_bytes', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._join_bytes', 'arguments_helper': '_ordinary_fixed_arguments_164', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-5dd76ee613e6fbffc062', 'module': 'publication', 'name': '_join_bytes', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._join_bytes', 'arguments_helper': '_ordinary_fixed_arguments_164', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-50fa4a18d92f5f197d45', 'module': 'publication', 'name': '_name_utf16', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._name_utf16', 'arguments_helper': '_ordinary_fixed_arguments_165', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-50fa4a18d92f5f197d45', 'module': 'publication', 'name': '_name_utf16', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._name_utf16', 'arguments_helper': '_ordinary_fixed_arguments_165', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-dcc6e4d779782510704d', 'module': 'publication', 'name': '_native_absolute', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._native_absolute', 'arguments_helper': '_ordinary_fixed_arguments_166', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-dcc6e4d779782510704d', 'module': 'publication', 'name': '_native_absolute', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._native_absolute', 'arguments_helper': '_ordinary_fixed_arguments_166', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-05c85ba713866bbe5028', 'module': 'publication', 'name': '_native_getcurrentprocess', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess', 'arguments_helper': '_ordinary_fixed_arguments_167', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-05c85ba713866bbe5028', 'module': 'publication', 'name': '_native_getcurrentprocess', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._native_getcurrentprocess', 'arguments_helper': '_ordinary_fixed_arguments_167', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-cca744dd3eab70097746', 'module': 'publication', 'name': '_new_aclsize', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_aclsize', 'arguments_helper': '_ordinary_fixed_arguments_168', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-cca744dd3eab70097746', 'module': 'publication', 'name': '_new_aclsize', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_aclsize', 'arguments_helper': '_ordinary_fixed_arguments_168', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e1f1f5e815b9def8ef4d', 'module': 'publication', 'name': '_new_dword', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_dword', 'arguments_helper': '_ordinary_fixed_arguments_169', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e1f1f5e815b9def8ef4d', 'module': 'publication', 'name': '_new_dword', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_dword', 'arguments_helper': '_ordinary_fixed_arguments_169', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c427068b93ad9c88a1c4', 'module': 'publication', 'name': '_new_handle', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_handle', 'arguments_helper': '_ordinary_fixed_arguments_170', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c427068b93ad9c88a1c4', 'module': 'publication', 'name': '_new_handle', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_handle', 'arguments_helper': '_ordinary_fixed_arguments_170', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-59a14d2e0e9ed17723d6', 'module': 'publication', 'name': '_new_handleinfo', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_handleinfo', 'arguments_helper': '_ordinary_fixed_arguments_171', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-59a14d2e0e9ed17723d6', 'module': 'publication', 'name': '_new_handleinfo', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_handleinfo', 'arguments_helper': '_ordinary_fixed_arguments_171', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d5fbfe0e44a56ba94276', 'module': 'publication', 'name': '_new_iostatus', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_iostatus', 'arguments_helper': '_ordinary_fixed_arguments_172', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d5fbfe0e44a56ba94276', 'module': 'publication', 'name': '_new_iostatus', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_iostatus', 'arguments_helper': '_ordinary_fixed_arguments_172', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e9118b28bfc846d666fe', 'module': 'publication', 'name': '_new_known_stack', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_known_stack', 'arguments_helper': '_ordinary_fixed_arguments_173', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-e9118b28bfc846d666fe', 'module': 'publication', 'name': '_new_known_stack', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_known_stack', 'arguments_helper': '_ordinary_fixed_arguments_173', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-a91e83fd52ea6ceac3d4', 'module': 'publication', 'name': '_new_lpvoid', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_lpvoid', 'arguments_helper': '_ordinary_fixed_arguments_174', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-a91e83fd52ea6ceac3d4', 'module': 'publication', 'name': '_new_lpvoid', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_lpvoid', 'arguments_helper': '_ordinary_fixed_arguments_174', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-1e5fa872fd4c3474fa77', 'module': 'publication', 'name': '_new_lpwstr', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_lpwstr', 'arguments_helper': '_ordinary_fixed_arguments_175', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-1e5fa872fd4c3474fa77', 'module': 'publication', 'name': '_new_lpwstr', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_lpwstr', 'arguments_helper': '_ordinary_fixed_arguments_175', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-e03da155ecae34624a99', 'module': 'publication', 'name': '_new_objectattributes', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_objectattributes', 'arguments_helper': '_ordinary_fixed_arguments_176', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-e03da155ecae34624a99', 'module': 'publication', 'name': '_new_objectattributes', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_objectattributes', 'arguments_helper': '_ordinary_fixed_arguments_176', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-86f6cd443abc345be8f7', 'module': 'publication', 'name': '_new_publishedfile', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_publishedfile', 'arguments_helper': '_ordinary_fixed_arguments_177', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-86f6cd443abc345be8f7', 'module': 'publication', 'name': '_new_publishedfile', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_publishedfile', 'arguments_helper': '_ordinary_fixed_arguments_177', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-bf761fe420ddc42374e4', 'module': 'publication', 'name': '_new_renameinfo', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_renameinfo', 'arguments_helper': '_ordinary_fixed_arguments_178', 'group': 'parent'}, 'PRE'), ({'case_id': 'R839-U-SEM-bf761fe420ddc42374e4', 'module': 'publication', 'name': '_new_renameinfo', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_renameinfo', 'arguments_helper': '_ordinary_fixed_arguments_178', 'group': 'parent'}, 'POST'), ({'case_id': 'R839-U-SEM-364497820587a2a2b816', 'module': 'publication', 'name': '_new_sha256', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_sha256', 'arguments_helper': '_ordinary_fixed_arguments_179', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-364497820587a2a2b816', 'module': 'publication', 'name': '_new_sha256', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_sha256', 'arguments_helper': '_ordinary_fixed_arguments_179', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-661a2599b98f1865f78f', 'module': 'publication', 'name': '_new_unicodestring', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_unicodestring', 'arguments_helper': '_ordinary_fixed_arguments_180', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-661a2599b98f1865f78f', 'module': 'publication', 'name': '_new_unicodestring', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_unicodestring', 'arguments_helper': '_ordinary_fixed_arguments_180', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-f42e3dd76add88b62012', 'module': 'publication', 'name': '_new_word', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_word', 'arguments_helper': '_ordinary_fixed_arguments_181', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-f42e3dd76add88b62012', 'module': 'publication', 'name': '_new_word', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._new_word', 'arguments_helper': '_ordinary_fixed_arguments_181', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-290d60920ad9ecdefa28', 'module': 'publication', 'name': '_path', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._path', 'arguments_helper': '_ordinary_fixed_arguments_182', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-290d60920ad9ecdefa28', 'module': 'publication', 'name': '_path', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._path', 'arguments_helper': '_ordinary_fixed_arguments_182', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-cfc12326ea2f5a631711', 'module': 'publication', 'name': '_path_stat', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._path_stat', 'arguments_helper': '_ordinary_fixed_arguments_183', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-cfc12326ea2f5a631711', 'module': 'publication', 'name': '_path_stat', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._path_stat', 'arguments_helper': '_ordinary_fixed_arguments_183', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-9ede0dbe56d28556cd8b', 'module': 'publication', 'name': '_prepare_read_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_read_buffer', 'arguments_helper': '_ordinary_fixed_arguments_184', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-9ede0dbe56d28556cd8b', 'module': 'publication', 'name': '_prepare_read_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_read_buffer', 'arguments_helper': '_ordinary_fixed_arguments_184', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-a2a5a725f755d2dca1ac', 'module': 'publication', 'name': '_prepare_token_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_token_buffer', 'arguments_helper': '_ordinary_fixed_arguments_185', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-a2a5a725f755d2dca1ac', 'module': 'publication', 'name': '_prepare_token_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_token_buffer', 'arguments_helper': '_ordinary_fixed_arguments_185', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-c654e453d56f17f1afb0', 'module': 'publication', 'name': '_prepare_token_buffer_base', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base', 'arguments_helper': '_ordinary_fixed_arguments_186', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-c654e453d56f17f1afb0', 'module': 'publication', 'name': '_prepare_token_buffer_base', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_token_buffer_base', 'arguments_helper': '_ordinary_fixed_arguments_186', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-45ac29bfaa29d545d8f3', 'module': 'publication', 'name': '_prepare_unicode_name_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer', 'arguments_helper': '_ordinary_fixed_arguments_187', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-45ac29bfaa29d545d8f3', 'module': 'publication', 'name': '_prepare_unicode_name_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_unicode_name_buffer', 'arguments_helper': '_ordinary_fixed_arguments_187', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-66c9fd2226ccd8ac04c6', 'module': 'publication', 'name': '_prepare_write_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_write_buffer', 'arguments_helper': '_ordinary_fixed_arguments_188', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-66c9fd2226ccd8ac04c6', 'module': 'publication', 'name': '_prepare_write_buffer', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._prepare_write_buffer', 'arguments_helper': '_ordinary_fixed_arguments_188', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-277796e28773cf1b8cab', 'module': 'publication', 'name': '_profile', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._profile', 'arguments_helper': '_ordinary_fixed_arguments_189', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-277796e28773cf1b8cab', 'module': 'publication', 'name': '_profile', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._profile', 'arguments_helper': '_ordinary_fixed_arguments_189', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-b733eb945b09e039915e', 'module': 'publication', 'name': '_record_decode', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._record_decode', 'arguments_helper': '_ordinary_fixed_arguments_190', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-b733eb945b09e039915e', 'module': 'publication', 'name': '_record_decode', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._record_decode', 'arguments_helper': '_ordinary_fixed_arguments_190', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-d1566f05ad28fb3c9abf', 'module': 'publication', 'name': '_safe_stat', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._safe_stat', 'arguments_helper': '_ordinary_fixed_arguments_191', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-d1566f05ad28fb3c9abf', 'module': 'publication', 'name': '_safe_stat', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._safe_stat', 'arguments_helper': '_ordinary_fixed_arguments_191', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-790490143f2ae87abfab', 'module': 'publication', 'name': '_set_add_seen', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._set_add_seen', 'arguments_helper': '_ordinary_fixed_arguments_192', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-790490143f2ae87abfab', 'module': 'publication', 'name': '_set_add_seen', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._set_add_seen', 'arguments_helper': '_ordinary_fixed_arguments_192', 'group': 'plain'}, 'POST'), ({'case_id': 'R839-U-SEM-ed03a36af851209f5aa3', 'module': 'publication', 'name': '_stage_name', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._stage_name', 'arguments_helper': '_ordinary_fixed_arguments_193', 'group': 'plain'}, 'PRE'), ({'case_id': 'R839-U-SEM-ed03a36af851209f5aa3', 'module': 'publication', 'name': '_stage_name', 'qualified_name': 'reno.personal_authority_namespace3_io_deadline._stage_name', 'arguments_helper': '_ordinary_fixed_arguments_193', 'group': 'plain'}, 'POST'))


@pytest.fixture(scope="module")
def ordinary_matrix_env(tmp_path_factory):
    # One original fixture and one complete setup for the entire controlled
    # alias matrix, not an origin/capture/public run per helper row.
    base = tmp_path_factory.mktemp("r839-ordinary-unit-matrix")
    with namespace_case_factory(base)("absent") as item:
        with pytest.MonkeyPatch.context() as setup_patch:
            item.complete_expected = prepare_complete(item, setup_patch)
        item.unit_reservation_raw = fixture_read_file(item.data / RESERVATION)[0]
        item.unit_seal_raw = fixture_read_file(item.data / ROOT_NAME / SEAL)[0]
        item.unit_header_raw = fixture_read_file(item.data / HEADER)[0]
        yield item


class SyntheticUnitGateFault:
    """Fixed consumer clock error; no elapsed-time or native-failure claim."""
    def __init__(self, ledger, qualified_name, phase):
        self.ledger, self.target, self.phase = ledger, qualified_name, phase
        self.primary = original_namespace.Namespace3Error("DEADLINE")
        self.armed, self.hits, self.direct_clocks = False, 0, 0

    def focus(self):
        return self.armed and self.ledger.alias_stack and self.ledger.alias_stack[-1] == self.target

    def reject(self, state):
        self.armed, self.hits = False, self.hits + 1
        assert self.hits == 1
        publication._remember_primary(state, self.primary)
        raise self.primary

    def install(self, patch):
        real_clock = publication._clock
        real_before, real_after = locked._ns_before, locked._ns_after
        def clock(state):
            focused = self.focus()
            if focused:
                self.direct_clocks += 1
            result = real_clock(state)
            if focused and self.direct_clocks == (1 if self.phase == "PRE" else 2):
                self.reject(state)
            return result
        def before(state):
            result = real_before(state)
            if self.focus() and "_effect_deadline." in self.target and self.phase == "PRE":
                self.reject(state)
            return result
        def after(state):
            result = real_after(state)
            if self.focus() and "_effect_deadline." in self.target and self.phase == "POST":
                self.reject(state)
            return result
        patch.setattr(publication, "_clock", clock)
        patch.setattr(locked, "_ns_before", before)
        patch.setattr(locked, "_ns_after", after)


@contextmanager
def controlled_ordinary_inputs(item, ledger, group):
    """Exact approved factories; real owners only in the declared resource groups."""
    with lifecycle.exclusive(item.profile) as guard:
        requested = requested_namespace_token()
        budget = deadline._RestrictedBudget(requested)
        state = publication._NamespaceState(budget, budget.effective_deadline)
        stack = publication._new_known_stack(state)
        with publication._typed_scope_guard(state, publication._claim_stack_receipt(state, stack)):
            bank = SimpleNamespace(state=state, item=item)
            bank.reservation_raw, bank.seal_raw, bank.header_raw = item.unit_reservation_raw, item.unit_seal_raw, item.unit_header_raw
            bank.second = decoded(item.second)
            bank.observation = publication.capture.OriginObservation(
                base64.b64decode(bank.second["origin_intent_base64"], validate=True),
                base64.b64decode(bank.second["origin_evidence_base64"], validate=True))
            bank.path_stat = os.stat(native(item.data), follow_symlinks=False)
            bank.fixture_file_identity = fixture_read_file(item.data / FIRST)[1]
            # These identities are explicitly synthetic DTO unit inputs. They
            # are never used by the actual namespace/native identity oracle.
            bank.fixture_native = {"volume_serial": "1", "file_index": "2"}
            bank.published = publication.PublishedFile(sha(bank.reservation_raw), len(bank.reservation_raw),
                                                      bank.fixture_native, bank.fixture_file_identity)
            bank.digest = publication._new_sha256(state, b"known\x00\xff")
            bank.info, bank.status = publication._new_handleinfo(state), publication._new_iostatus(state)
            bank.acl, bank.dword, bank.word = publication._new_aclsize(state), publication._new_dword(state), publication._new_word(state)
            bank.pointer, bank.text_pointer, bank.handle_output = publication._new_lpvoid(state), publication._new_lpwstr(state), publication._new_handle(state)
            bank.text_pointer.value = "known"
            bank.ace = publication._AllowedAce()
            bank.ace_header, bank.file_time = bank.ace.header, bank.info.created
            bank.ace_address = publication.w.LPVOID(publication.ctypes.addressof(bank.ace))
            bank.ace_view = publication._ct_cast_borrowed_ace_structure_pointer_l344c22(state, bank.ace_address)
            bank.token_buffer = publication._prepare_token_buffer(state, 64)
            bank.token_view = publication._ct_cast_token_buffer_sid_pointer_l251c14(state, bank.token_buffer)
            bank.read_buffer = publication._prepare_read_buffer(state, 32)
            bank.unicode_buffer = publication._prepare_unicode_name_buffer(state, RESERVATION)
            bank.unicode_text_pointer = publication._ct_cast_relative_unicode_buffer_pointer_l396c61(state, bank.unicode_buffer)
            encoded_length = len(RESERVATION.encode("utf-16-le"))
            bank.unicode_value = publication._new_unicodestring(state, encoded_length, encoded_length + 2, bank.unicode_text_pointer)
            bank.unicode_pointer = publication._ct_pointer_relative_unicode_l398c34(state, bank.unicode_value)
            bank.attrs_size = publication._ct_sizeof_object_attributes_type_l397c35(state)
            if group in {"physical", "node"}:
                bank.physical = stack.enter_physical(publication._issue_physical_context(state, item.data))
                if group == "node":
                    bank.node = stack.enter_node(publication._issue_node_context(state, bank.physical))
            if group in {"parent", "held"}:
                bank.parent = stack.enter_parent(publication.open_parent_locked(item.profile, state, write=False))
                bank.parent_owner = ledger.directory_owners[id(bank.parent)]
                bank.parent_handle = publication._get_exact_current_known_private_io_owner_fixed_closer_kind_value_original_ordinary_field_read(state, bank.parent_owner)
                bank.attributes = publication._new_objectattributes(state, bank.attrs_size, bank.parent_handle, bank.unicode_pointer, 64, None, None)
                bank.rename_value = publication._new_renameinfo(state, False, bank.parent_handle, encoded_length, RESERVATION)
                if group == "held":
                    bank.held = stack.enter_held(publication.read_control_locked(item.profile, bank.parent, FIRST, state, 16 * 1024))
            if group == "scan":
                bank.entries = stack.enter_scandir(publication._issue_scandir_context(state, native(item.data)))
                bank.entry = next(bank.entries)
            assert lifecycle.require_guard(item.profile, exclusive=True) is guard
            yield bank


@pytest.mark.parametrize("row,phase", ORDINARY_CONTROLLED_CASES,
                         ids=[row["case_id"] + "-" + phase for row, phase in ORDINARY_CONTROLLED_CASES])
def test_fixed_ordinary_pre_post_gate(ordinary_matrix_env, monkeypatch, row, phase):
    item = ordinary_matrix_env
    before = fixture_source_baseline(item)
    with monkeypatch.context() as patch:
        ledger = NamespaceForwardLedger(patch, item, "controlled_ordinary_unit")
        fault = SyntheticUnitGateFault(ledger, row["qualified_name"], phase)
        fault.install(patch)
        with pytest.raises(original_namespace.Namespace3Error) as caught:
            with controlled_ordinary_inputs(item, ledger, row["group"]) as bank:
                target = getattr(locked if row["module"] == "locked" else publication, row["name"])
                # A normal unit control uses the same accepted fixed argument
                # domain. This is not a public namespace/OS acceptance count.
                arguments = globals()[row["arguments_helper"]](bank)
                target(*arguments)
                fault.armed = True
                target(*globals()[row["arguments_helper"]](bank))
                pytest.fail("synthetic selected gate did not reject")
        assert caught.value is fault.primary and fault.hits == 1
        ledger.assert_known_once()
    assert lifecycle.current_guard() is None
    assert_source_preserved(item, before, namespace_created=False)


class FixedProtocolFault:
    """Only literal rows below choose targets; no caller operation/type/closer."""
    def __init__(self, case_id, target=None, primary=None):
        self.case_id, self.target = case_id, target
        self.primary = RuntimeError("r839-fixed-original-primary") if primary is None else primary
        self.secondary = RuntimeError("r839-fixed-cleanup-secondary")
        self.hits, self.expiry_state, self.expiry_hit = [], None, False
        self.owned_slot, self.information_calls_at_issue = None, None

    def before_alias(self, ledger, alias):
        if alias == "reno.personal_authority_namespace3_effect_deadline._ns_intent_blob" and self.case_id in PRIMARY_CASE_IDS:
            if not self.hits:
                self.hits.append("body_first")
                raise self.primary
        if self.case_id.startswith("R839-N-BEFORE-") and alias == self.target and not self.hits:
            self.hits.append("synthetic_consumer_exception_before_real_native")
            raise self.primary
        if self.case_id == "R839-F-BOOKKEEPING-TB-FAIL" and alias == "reno.personal_authority_namespace3_io_deadline._primary_traceback":
            if not self.hits:
                self.hits.append("body_first")
            raise self.secondary

    def after_alias(self, ledger, alias, result):
        return result

    def after_ownership(self, ledger, state, exact_known_slot, owner):
        if self.case_id == "R839-N-NTCREATE-OWNED-BEFORE-POST" and not self.hits:
            if ledger.relative_open_roles:
                actual_state, name, directory, create = ledger.relative_open_roles[-1]
                expected_stage = RESERVATION + "." + ledger.item.arguments["init_operation_id"] + ".stage"
                if actual_state is state and name == expected_stage and directory is False and create is True:
                    self.hits.append("legal_status_handle_owner_issued_before_register_postclock")
                    self.expiry_state = state
                    self.owned_slot = exact_known_slot
                    self.information_calls_at_issue = ledger.alias_calls[
                        "reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read"]

    def after_enter(self, ledger, receipt, result):
        kind = ledger.receipts[id(receipt)][3]
        if self.case_id == "R839-F-CAPTURE-BAD-PAYLOAD-KNOWN" and kind == "CAPTURE" and not self.hits:
            self.hits.append("actual_capture_normal_enter_then_bad_payload")
            return object()
        if self.case_id == "R839-F-KNOWN-ENTER-LATE" and kind == "CAPTURE" and not self.hits:
            self.hits.append("actual_capture_normal_enter_before_postclock")
            self.expiry_state = ledger.receipts[id(receipt)][1]
        return result

    def after_exit(self, ledger, receipt, result):
        kind = ledger.receipts[id(receipt)][3]
        if self.case_id in PRIMARY_CASE_IDS and kind == "NODE" and "secondary_after_real_node_exit" not in self.hits:
            self.hits.append("secondary_after_real_node_exit")
            raise self.secondary
        if self.case_id == "R839-F-CAPTURE-EXIT-FAIL" and kind == "CAPTURE" and not self.hits:
            assert result is False
            self.hits.append("secondary_after_actual_capture_normal_exit")
            raise self.primary
        if self.case_id == "R839-F-CLEANUP-NO-PRIMARY" and kind == "CAPTURE" and not self.hits:
            self.hits.append("first_after_actual_capture_normal_exit")
            raise self.primary

    def install(self, patch, ledger):
        real_clock = publication._clock
        def clock(state):
            result = real_clock(state)
            if self.expiry_state is state and not self.expiry_hit:
                self.expiry_hit = True
                publication._remember_primary(state, self.primary)
                raise self.primary
            return result
        patch.setattr(publication, "_clock", clock)
        real_remember = publication._remember_primary
        def remember(state, original_primary, /):
            result = real_remember(state, original_primary)
            ledger.first_returns.setdefault(id(state), result)
            return result
        patch.setattr(publication, "_remember_primary", remember)
        real_traceback = publication._primary_traceback
        def traceback(state, original_primary, /):
            result = real_traceback(state, original_primary)
            ledger.traceback_returns.setdefault(id(state), result)
            return result
        patch.setattr(publication, "_primary_traceback", traceback)


PRIMARY_CASE_IDS = {
    "R839-F-PRIMARY-RUNTIMEERROR", "R839-F-PRIMARY-KEYBOARDINTERRUPT",
    "R839-F-PRIMARY-SYSTEMEXIT", "R839-F-PRIMARY-GENERATOREXIT",
}
FIXED_PROTOCOL_CASES = (
    ("R839-F-PRIMARY-RUNTIMEERROR", RuntimeError),
    ("R839-F-PRIMARY-KEYBOARDINTERRUPT", KeyboardInterrupt),
    ("R839-F-PRIMARY-SYSTEMEXIT", SystemExit),
    ("R839-F-PRIMARY-GENERATOREXIT", GeneratorExit),
    ("R839-F-CAPTURE-BAD-PAYLOAD-KNOWN", None),
    ("R839-F-CAPTURE-EXIT-FAIL", RuntimeError),
    ("R839-F-KNOWN-ENTER-LATE", None),
    ("R839-F-CLEANUP-NO-PRIMARY", RuntimeError),
)


@pytest.mark.parametrize("case_id,primary_type", FIXED_PROTOCOL_CASES, ids=[row[0] for row in FIXED_PROTOCOL_CASES])
def test_fixed_capture_primary_cleanup_protocol(ordinary_matrix_env, monkeypatch, case_id, primary_type):
    item = ordinary_matrix_env
    before = fixture_source_baseline(item)
    primary = original_namespace.Namespace3Error("DEADLINE") if case_id == "R839-F-KNOWN-ENTER-LATE" else (
        primary_type("r839-original-first") if primary_type is not None else RuntimeError("unused-domain-first"))
    fault = FixedProtocolFault(case_id, primary=primary)
    with monkeypatch.context() as patch:
        ledger = NamespaceForwardLedger(patch, item, "fixed_capture_or_original_primary")
        ledger.fault = fault
        fault.install(patch, ledger)
        with lifecycle.exclusive(item.profile) as guard:
            with pytest.raises(BaseException) as caught:
                invoke_namespace_subject(item, mode="verify", ownership="locked",
                    expected=item.complete_expected, requested=requested_namespace_token())
            assert fault.hits, "fixed semantic role was not reached"
            if case_id == "R839-F-CAPTURE-BAD-PAYLOAD-KNOWN":
                assert type(caught.value) is publication.Namespace3IOError and caught.value.code == "NAMESPACE3_IO_INVALID_RESULT"
            else:
                assert caught.value is primary
            if case_id in PRIMARY_CASE_IDS:
                assert fault.hits == ["body_first", "secondary_after_real_node_exit"]
                assert any(value is primary for value in ledger.first_returns.values())
                original_tb = next(iter(ledger.traceback_returns.values()))
                assert all(event[3] is original_tb for event in ledger.events
                           if event[0] == "raw_exit_start" and event[2] is primary)
            if case_id == "R839-F-KNOWN-ENTER-LATE":
                assert fault.expiry_hit
            ledger.assert_known_once()
            assert lifecycle.require_guard(item.profile, exclusive=True) is guard
    assert lifecycle.current_guard() is None
    assert_source_preserved(item, before, namespace_created=False)


def test_fixed_traceback_bookkeeping_failure_preserves_first_and_all_known_attempts(ordinary_matrix_env, monkeypatch):
    item = ordinary_matrix_env
    before = fixture_source_baseline(item)
    primary = RuntimeError("r839-primary-with-unavailable-traceback")
    with monkeypatch.context() as patch:
        ledger = NamespaceForwardLedger(patch, item, "fixed_traceback_bookkeeping")
        fault = FixedProtocolFault("R839-F-PRIMARY-RUNTIMEERROR", primary=primary)
        ledger.fault = fault
        fault.install(patch, ledger)
        real_traceback = publication._primary_traceback
        def unavailable(state, original_primary, /):
            if original_primary is primary:
                raise fault.secondary
            return real_traceback(state, original_primary)
        patch.setattr(publication, "_primary_traceback", unavailable)
        with lifecycle.exclusive(item.profile) as guard:
            with pytest.raises(BaseException) as caught:
                invoke_namespace_subject(item, mode="verify", ownership="locked",
                    expected=item.complete_expected, requested=requested_namespace_token())
            assert caught.value is primary and fault.hits
            assert all(event[3] is None for event in ledger.events if event[0] == "raw_exit_start" and event[2] is primary)
            ledger.assert_known_once()
            assert lifecycle.require_guard(item.profile, exclusive=True) is guard
    assert_source_preserved(item, before, namespace_created=False)


NATIVE_BEFORE_CASES = (
    ("R839-N-BEFORE-OPEN-PROCESS-TOKEN", "_native_openprocesstoken"),
    ("R839-N-BEFORE-TOKEN-PROBE", "_token_user_probe"),
    ("R839-N-BEFORE-TOKEN-FILL", "_token_user_fill"),
    ("R839-N-BEFORE-SID-TO-TEXT", "_native_convertsidtostringsidw"),
    ("R839-N-BEFORE-SYSTEM-SID", "_native_convertstringsidtosidw"),
    ("R839-N-BEFORE-DESCRIPTOR", "_native_convertstringsecuritydescriptortosecuritydescriptorw"),
    ("R839-N-BEFORE-GET-SECURITY", "_native_getsecurityinfo"),
    ("R839-N-BEFORE-VALID-DESCRIPTOR", "_native_isvalidsecuritydescriptor"),
    ("R839-N-BEFORE-VALID-SID", "_native_isvalidsid"),
    ("R839-N-BEFORE-EQUAL-SID", "_native_equalsid"),
    ("R839-N-BEFORE-DESCRIPTOR-CONTROL", "_native_getsecuritydescriptorcontrol"),
    ("R839-N-BEFORE-ACL-SIZE", "_native_getaclinformation"),
    ("R839-N-BEFORE-ACE", "_native_getace"),
    ("R839-N-BEFORE-SID-LENGTH", "_native_getlengthsid"),
    ("R839-N-BEFORE-PARENT-OPEN", "_native_createfilew"),
    ("R839-N-BEFORE-NATIVE-INFO", "_native_getfileinformationbyhandle"),
    ("R839-N-BEFORE-CREATE-STAGE", "_native_ntcreatefile"),
    ("R839-N-BEFORE-WRITE", "_native_writefile"),
    ("R839-N-BEFORE-READ", "_native_readfile"),
    ("R839-N-BEFORE-FLUSH", "_native_flushfilebuffers"),
    ("R839-N-BEFORE-PUBLISH", "_native_ntsetinformationfile"),
    ("R839-N-BEFORE-REWIND", "_native_setfilepointerex"),
)


@contextmanager
def native_publication_input(tmp_path, source_item):
    """Fresh direct-IO unit profile; no repeat full-origin/capture setup."""
    qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
    tmp_path.resolve(strict=True).relative_to(qa)
    base = tmp_path / "actual-new-io-unit"
    profile = lifecycle.StorageProfile(base / "data/unit.sqlite3", base / "media", base / "reports", base / "checkpoints")
    lifecycle.initialize(profile)
    # The canonical bytes are fixed DTO unit input from a separately validated
    # namespace setup. This direct publisher unit never claims profile binding,
    # source_closed, public namespace success or installed writer authority.
    item = SimpleNamespace(base=base, data=profile.database.parent, profile=profile,
        arguments=source_item.arguments, unit_raw=source_item.unit_reservation_raw)
    yield item


def direct_publication_subject(item):
    """One direct new IO publisher; fixture EX remains caller-owned."""
    requested = requested_namespace_token()
    budget = deadline._RestrictedBudget(requested)
    state = publication._NamespaceState(budget, budget.effective_deadline)
    stack = publication._new_known_stack(state)
    with publication._typed_scope_guard(state, publication._claim_stack_receipt(state, stack)):
        parent = stack.enter_parent(publication.open_parent_locked(item.profile, state, write=True))
        return publication.publish_new_locked(item.profile, parent, RESERVATION,
            RESERVATION + "." + item.arguments["init_operation_id"] + ".stage", item.unit_raw, state)


@pytest.mark.parametrize("case_id,target_name", NATIVE_BEFORE_CASES, ids=[row[0] for row in NATIVE_BEFORE_CASES])
def test_fixed_native_consumer_exception_preserves_known_owners(ordinary_matrix_env, tmp_path, monkeypatch, case_id, target_name):
    with native_publication_input(tmp_path, ordinary_matrix_env) as item:
        with monkeypatch.context() as patch:
            ledger = NamespaceForwardLedger(patch, item, "synthetic_consumer_before_native")
            qualified = "reno.personal_authority_namespace3_io_deadline." + target_name
            primary = OSError("r839-synthetic-consumer-error-not-real-native-failure")
            fault = FixedProtocolFault(case_id, target=qualified, primary=primary)
            ledger.fault = fault
            with lifecycle.exclusive(item.profile) as guard:
                with pytest.raises(BaseException) as caught:
                    direct_publication_subject(item)
                assert caught.value is primary
                assert fault.hits == ["synthetic_consumer_exception_before_real_native"]
                assert ledger.alias_returns[qualified] == 0
                ledger.assert_known_once()
                assert lifecycle.require_guard(item.profile, exclusive=True) is guard
            assert lifecycle.current_guard() is None
        # Partial own stage/control artifacts remain; fixture/OS retirement is
        # never described as a product normal-close witness or a repair.


@pytest.mark.parametrize("kind,wrong", (("LocalFree", False), ("CloseHandle", True)),
                         ids=("R839-N-LOCALFREE-BOOL-FALSE", "R839-N-CLOSEHANDLE-BOOL-TRUE"))
def test_fixed_cleanup_raw_bool_domain_is_unknown_without_retry(ordinary_matrix_env, tmp_path, monkeypatch, kind, wrong):
    with native_publication_input(tmp_path, ordinary_matrix_env) as item:
        with monkeypatch.context() as patch:
            ledger = NamespaceForwardLedger(patch, item, "actual_cleanup_then_synthetic_illegal_raw_bool")
            original = getattr(publication._kernel, kind)
            calls, altered = [], []
            def cleanup(known_value):
                assert ledger.closing_slots
                exact_slot = ledger.closing_slots[-1]
                role = ledger.slot_roles.get(id(exact_slot))
                result = original(known_value)
                calls.append((exact_slot, role, result))
                target_role = "private_descriptor_sid_text_allocation" if kind == "LocalFree" else "publication_stage_creator"
                if role == target_role:
                    assert not altered, "the fixed semantic lifetime was closed twice"
                    altered.append(exact_slot)
                    return wrong
                return result
            patch.setattr(publication._kernel, kind, cleanup)
            with lifecycle.exclusive(item.profile) as guard:
                with pytest.raises(publication.Namespace3IOError) as caught:
                    direct_publication_subject(item)
                assert caught.value.code == "NAMESPACE3_IO_INVALID_RESULT"
                assert altered and calls
                assert len(altered) == 1
                assert sum(slot is altered[0] for slot, role, result in calls) == 1
                ledger.assert_known_once()
                # Compare source slot lifetime attempts, not numeric HANDLE
                # equality: later lifetimes may legally reuse the same number.
                assert all(count == 1 for count in ledger.attempted_slots.values())
                assert lifecycle.require_guard(item.profile, exclusive=True) is guard
            assert lifecycle.current_guard() is None


def test_fixed_ntcreate_known_stage_owner_precedes_rejecting_postclock(ordinary_matrix_env, tmp_path, monkeypatch):
    """Legal actual native output, then a fixed synthetic clock exception."""
    with native_publication_input(tmp_path, ordinary_matrix_env) as item:
        with monkeypatch.context() as patch:
            ledger = NamespaceForwardLedger(patch, item, "actual_stage_owner_before_synthetic_postclock")
            primary = original_namespace.Namespace3Error("DEADLINE")
            fault = FixedProtocolFault("R839-N-NTCREATE-OWNED-BEFORE-POST", primary=primary)
            ledger.fault = fault
            fault.install(patch, ledger)
            with lifecycle.exclusive(item.profile) as guard:
                with pytest.raises(original_namespace.Namespace3Error) as caught:
                    direct_publication_subject(item)
                assert caught.value is primary and fault.expiry_hit
                assert fault.hits == ["legal_status_handle_owner_issued_before_register_postclock"]
                assert fault.owned_slot is not None
                assert ledger.slot_roles[id(fault.owned_slot)] == "publication_stage_creator"
                assert ledger.attempted_slots[id(fault.owned_slot)] == 1
                information = "reno.personal_authority_namespace3_io_deadline._get_exact_iostatus_output_information_original_ordinary_field_read"
                assert ledger.alias_calls[information] == fault.information_calls_at_issue
                assert ledger.alias_calls["reno.personal_authority_namespace3_io_deadline._native_writefile"] == 0
                ledger.assert_known_once()
                assert lifecycle.require_guard(item.profile, exclusive=True) is guard
            assert lifecycle.current_guard() is None
        # The created stage is retained. The test never removes it, retries a
        # known slot or calls expiry a real elapsed-time/native failure.


FIRST_SHORT_SUBMISSION_CASES = (
    ("R839-N-FIRST-READ-SUBMISSION-7", True, False),
    ("R839-N-FIRST-WRITE-SUBMISSION-7", False, True),
    ("R839-N-FIRST-BOTH-SUBMISSIONS-7", True, True),
)


class FirstShortSubmission:
    """Delegate the first creator request as 7 bytes; keep actual SDK return.

    Product request M remains M; the SDK receives N=7<M exactly once for each
    chosen direction. Later SDK requests keep M. This demonstrates the product
    processing actual smaller counts, not a spontaneous SDK short return for M.
    """
    def __init__(self, ledger, shorten_read, shorten_write):
        self.ledger = ledger
        self.enabled = {"read": shorten_read, "write": shorten_write}
        self.changed, self.calls = Counter(), []

    def sdk_call(self, original, kind, known_handle, exact_buffer, requested, exact_count_out, overlapped):
        ledger = self.ledger
        assert ledger.publisher_contexts and ledger.publisher_contexts[-1]["name"] == RESERVATION
        assert type(requested) is int and requested > 0 and overlapped is None
        record = ledger.publisher_contexts[-1]
        if kind == "read":
            assert ledger.active_scans
            # No raw HANDLE-number lifetime inference: the scan's actual owner
            # receiver binds this SDK submission to the creator semantic role.
            is_creator = ledger.active_scans[-1]["owner"] is record["creator"]
        else:
            is_creator = record["creator"] is not None
        shortened = self.enabled[kind] and is_creator and self.changed[kind] == 0
        if shortened:
            assert requested > 7
            delegated = 7
            self.changed[kind] += 1
        else:
            delegated = requested
        pointer, count = ledger.native_count_out[id(exact_count_out)]
        assert pointer is exact_count_out
        raw_return = original(known_handle, exact_buffer, delegated, exact_count_out, overlapped)
        call = {"kind": kind, "product_request": requested, "sdk_request": delegated,
                "raw_sdk_return": raw_return, "actual_count_receiver": count,
                "shortened_input": shortened, "creator": is_creator}
        self.calls.append(call)
        ledger.event("sdk_submission_return", call)
        return raw_return

    def install(self, patch):
        original_read, original_write = publication._kernel.ReadFile, publication._kernel.WriteFile
        def readfile(known_handle, exact_buffer, requested, exact_count_out, overlapped):
            return self.sdk_call(original_read, "read", known_handle, exact_buffer, requested, exact_count_out, overlapped)
        def writefile(known_handle, exact_buffer, requested, exact_count_out, overlapped):
            return self.sdk_call(original_write, "write", known_handle, exact_buffer, requested, exact_count_out, overlapped)
        patch.setattr(publication._kernel, "ReadFile", readfile)
        patch.setattr(publication._kernel, "WriteFile", writefile)

    def assert_actual_count_charge_order(self):
        events = self.ledger.events
        assert self.calls
        for call in self.calls:
            assert type(call["raw_sdk_return"]) is int and call["raw_sdk_return"] != 0
            assert call["sdk_request"] == (7 if call["shortened_input"] else call["product_request"])
            count = call["actual_count_receiver"]
            values = self.ledger.native_counts[id(count)]
            kind = call["kind"]
            expected = [kind + "_" + group for group in ("g1_validate", "g2_charge_operand", "g3_return_reread")]
            assert set(values) == set(expected)
            actual = values[expected[0]]
            assert type(actual) is int and all(values[name] == actual for name in expected)
            assert (0 if kind == "read" else 1) <= actual <= call["sdk_request"]
            if call["shortened_input"]:
                assert call["creator"] and actual == 7 < call["product_request"]
            start = next(i for i, event in enumerate(events) if event[0] == "sdk_submission_return" and event[1] is call)
            g1, g2, g3 = [next(i for i, event in enumerate(events) if event[0] == "native_count"
                              and event[1] is count and event[2] == name) for name in expected]
            charges = [(i, event) for i, event in enumerate(events) if g2 < i < g3 and event[0] == "charge"]
            charge_returns = [(i, event) for i, event in enumerate(events) if g2 < i < g3 and event[0] == "charge_return"]
            assert len(charges) == len(charge_returns) == 1
            charge_at, charge = charges[0]
            return_at, returned = charge_returns[0]
            assert start < g1 < g2 < charge_at < return_at < g3
            assert charge[2] == returned[2] == actual and returned[3] is None
        assert self.changed == Counter({kind: 1 for kind, enabled in self.enabled.items() if enabled})
        assert all(sum(c["kind"] == kind and c["shortened_input"] for c in self.calls) == int(enabled)
                   for kind, enabled in self.enabled.items())


@pytest.mark.parametrize("case_id,shorten_read,shorten_write", FIRST_SHORT_SUBMISSION_CASES,
                         ids=[row[0] for row in FIRST_SHORT_SUBMISSION_CASES])
def test_actual_direct_io_first_shortened_sdk_request_retains_raw_return(ordinary_matrix_env, tmp_path, monkeypatch,
                                                                       case_id, shorten_read, shorten_write):
    with native_publication_input(tmp_path, ordinary_matrix_env) as item:
        with monkeypatch.context() as patch:
            ledger = NamespaceForwardLedger(patch, item, "actual_sdk_with_first_shortened_request_input")
            shim = FirstShortSubmission(ledger, shorten_read, shorten_write)
            shim.install(patch)
            with lifecycle.exclusive(item.profile) as guard:
                result = direct_publication_subject(item)
                assert type(result) is publication.PublishedFile
                assert result.raw_sha256 == sha(item.unit_raw) and result.byte_size == len(item.unit_raw)
                _assert_native_identity(result.native_identity)
                path = fixture_path_identity(item.data / RESERVATION)
                assert result.path_identity == path
                raw, identity, handle_identity = fixture_read_file(item.data / RESERVATION)
                assert raw == item.unit_raw and sha(raw) == result.raw_sha256
                assert identity["byte_size"] == len(raw) == handle_identity["byte_size"]
                shim.assert_actual_count_charge_order()
                assert len(ledger.publishers) == 1
                record = ledger.publishers[0]
                assert record["returned"] and len(record["held"]) == 2
                creator = ledger.scans[id(record["creator"])]
                assert len(creator) == 2
                assert all(s["returned"] and s["eofs"] == 1 and b"".join(s["parts"]) == item.unit_raw for s in creator)
                for held in record["held"]:
                    _, actual_owner, name = ledger.held_roles[id(held)]
                    assert name == RESERVATION
                    reads = ledger.scans[id(actual_owner)]
                    assert len(reads) == 2
                    assert all(s["returned"] and s["eofs"] == 1 and b"".join(s["parts"]) == item.unit_raw for s in reads)
                ledger.assert_known_once()
                assert lifecycle.require_guard(item.profile, exclusive=True) is guard
            assert lifecycle.current_guard() is None
        # This direct IO unit includes two creator whole+EOF scans and two
        # publisher readers. It has no composition third reader/public DTO claim.
