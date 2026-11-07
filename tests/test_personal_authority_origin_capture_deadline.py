"""r833 fixed actual complete origin chain and finite fault boundaries.

Root alone imports, collects and runs. New QA setup never warms old validators.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import tempfile
from types import SimpleNamespace

import pytest
from reno import personal_authority_origin_capture_deadline as subject
from reno import personal_authority_origin_source as ordinary
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_source_deadline as nodes
from reno import personal_authority_committed_image_deadline as images
from reno import personal_authority_live_sql_held_deadline as held
from reno import personal_authority_sql_projection_deadline as projection
from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_schema as receiver
from reno import personal_preservation_contract as contract
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows EX and held raw FD chain")
PROJECT_TABLE = "renovation_project"
STAMP = "2026-10-12T00:00:00Z"
ALIASES = ("_monotonic", "_deadline", "_restricted_budget", "_effective_token", "_profile", "_uuid",
    "_current_guard", "_require_guard", "_exclusive", "_directory", "_directory_enter", "_directory_exit",
    "_directory_verify", "_entry_stat", "_image", "_image_enter", "_image_exit", "_fingerprint", "_image_verify",
    "_read_budget", "_read_budget_evidence", "_held", "_held_enter", "_held_exit", "_held_verify", "_held_evidence",
    "_node_directory", "_node", "_node_enter", "_node_exit", "_node_verify", "_node_bytes", "_project_sql",
    "_canonical_bytes", "_digest", "_encode_intent", "_sha256_hex", "_deepcopy")
POSITIVE_IDS = ("borrowed-wal-empty-journal", "owned-absent-companions", "borrowed-prepared-receiver")
SUFFIXES = ("", "-wal", "-journal")

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


def coded(call, cls, code):
    with pytest.raises(cls) as caught:
        call()
    assert caught.value.code == code
    return caught.value


def populate_origin(con):
    for project, title, revision in ((PROJECT2, "另一个'O\\绍兴项目", 1.25), (PROJECT, "绍兴原始项目", 1)):
        insert(con, PROJECT_TABLE, {"project_id": project, "name": title, "profile_json": '{ "city" : "绍兴" }',
            "revision": revision, "created_at": STAMP, "updated_at": STAMP, "archived_at": None})
    insert(con, "decision_card", {"card_id": CARD, "project_id": PROJECT, "title": "防水原始决策",
        "context_json": '{ "材料" : "未定" }', "created_at": STAMP, "updated_at": STAMP})
    card = persistent_view(raw_records(con, {"decision_card": contract.PERSONAL_COLUMNS["decision_card"]})["decision_card"][0])
    insert(con, "personal_revision", {"entity_kind": "card", "entity_id": CARD, "revision": 1,
        "snapshot_json": canonical(card).decode(), "operation_id": CARD_CREATE, "created_at": STAMP})
    insert(con, "mutation_receipt", {"operation_id": CARD_CREATE, "scope": f"project:{PROJECT}:cards:create",
        "request_hash": "a" * 64, "response_status": 201,
        "response_json": canonical({"operation_id": CARD_CREATE, "card": card}).decode(), "created_at": STAMP})
    insert(con, "source_record", {"source_id": SOURCE, "project_id": PROJECT, "source_type": "manual_note",
        "title": "原BLOB事实", "excerpt": b"raw\x00\xff", "scope_json": '{ "scope" : ["绍兴"] }',
        "created_at": STAMP, "updated_at": STAMP})


@pytest.fixture
def qa_case(tmp_path):
    sequence = 0
    @contextmanager
    def opened(mode="wal", mutation=None):
        nonlocal sequence
        assert mode in {"wal", "absent", "prepared"}
        qa = Path(os.environ["RENO_QA_ROOT"]).resolve(strict=True)
        tmp_path.resolve(strict=True).relative_to(qa)
        Path(tempfile.gettempdir()).resolve(strict=True).relative_to(qa)
        sequence += 1
        base = tmp_path / f"r833-{sequence}-{mode}"
        profile = lifecycle.StorageProfile(base / "data/绍兴-origin.sqlite3", base / "media", base / "reports", base / "checkpoints")
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
        item = None
        with lifecycle.exclusive(profile):
            rw = sqlite3.connect(native(profile.database))
            try:
                journal = "DELETE" if mode == "absent" else "WAL"
                assert sql_rows(rw, "PRAGMA journal_mode=" + journal)[0][0].upper() == journal
                sql_rows(rw, "PRAGMA wal_autocheckpoint=0")
                sql_rows(rw, "PRAGMA foreign_keys=ON")
                for table, ddl in facts._DDL.items():
                    sql_rows(rw, "CREATE TABLE " + table + " (" + ddl + ")")
                schema = {"personal_schema_version": 1, "tables": {t: independent_layout(rw, t) for t in facts.TABLE_NAMES}}
                installed = mode in {"absent", "prepared"}
                if installed:
                    for table, ddl in receiver.TABLES.items():
                        sql_rows(rw, "CREATE TABLE " + table + " (" + ddl + ")")
                    for statement in receiver.INDEXES + receiver.TRIGGERS:
                        sql_rows(rw, statement)
                known = known_snapshot(rw, schema) if mode == "prepared" else None
                if known is not None:
                    add_receiver_rows(rw, known)
                if mutation != "three-meta":
                    populate_origin(rw)
                sql_rows(rw, "CREATE TABLE r833_nonpersonal_facts(key TEXT PRIMARY KEY,value BLOB)")
                sql_rows(rw, "INSERT INTO r833_nonpersonal_facts VALUES(?,?)", ("known", b"nonpersonal\x00\xff"))
                meta = {"library_id": LIBRARY, "personal_primary_id": "" if mutation == "three-meta" else PRIMARY,
                        "primary_epoch": str((1 << 63)) if mutation == "epoch-max" else "1"}
                if mutation != "three-meta":
                    meta["default_project_id"] = uid(999) if mutation == "default-missing" else PROJECT
                for key, value in meta.items():
                    sql_rows(rw, "INSERT INTO meta VALUES(?,?)", (key, value))
                rows = raw_records(rw, contract.PERSONAL_COLUMNS)
                if installed:
                    rows.update(raw_records(rw, contract.RECEIVER_COLUMNS))
                layouts = {table: independent_layout(rw, table) for table in (*rows, "meta")}
                meta_rows = sql_rows(rw, "SELECT key,value FROM meta WHERE key IN (?,?,?,?) ORDER BY key", facts.META_KEYS)
                rw.commit()
                node_id = uid(998) if mutation == "primary-mismatch" else PRIMARY
                node_raw = ('{"instance_id":"' + node_id + '"}').encode()
                if mutation == "node-duplicate":
                    node_raw = ('{"instance_id":"' + PRIMARY + '","instance_id":"' + PRIMARY + '"}').encode()
                node_path = profile.database.parent / "node-identity.json"
                node_path.write_bytes(node_raw)
                if mode == "wal":
                    profile.database.with_name(profile.database.name + "-journal").write_bytes(b"")
                if mode == "absent":
                    rw.close()
                    rw = None
                contents = {}
                for suffix in SUFFIXES:
                    path = profile.database.with_name(profile.database.name + suffix)
                    contents[suffix] = path.read_bytes() if path.exists() else None
                assert contents[""] and (contents["-wal"] is None if mode == "absent" else bool(contents["-wal"]))
                assert contents["-journal"] == (b"" if mode == "wal" else None)
                item = SimpleNamespace(profile=profile, mode=mode, installed=installed, rows=rows, layouts=layouts,
                    schema=schema, meta=meta, meta_rows=meta_rows, node_raw=node_raw, contents=contents, known=known,
                    guard=None, requested=None)
            except BaseException:
                if rw is not None:
                    rw.close()
                    rw = None
                raise
        assert lifecycle.current_guard() is None
        try:
            yield item
        finally:
            if rw is not None:
                rw.close()

    return opened

def file_evidence(value):
    return {"sha256": value.sha256, "byte_size": value.byte_size,
            "directory_identity": value.directory_identity, "path_identity": value.path_identity,
            "handle_identity": value.handle_identity}


class Scalar:
    def __init__(self, value):
        self.value = value
    def __call__(self):
        return self.value


class RawLedger:
    def __init__(self, patch):
        self.opening_suffix = None
        self.leases, self.raws, self.fds = {}, {}, {}
        self.scans = defaultdict(list)
        self.exits, self.node_exits = [], []
        self.node_raws, self.node_fds = {}, {}
        self.node_read_bytes, self.node_reads, self.node_eofs, self.node_seeks = 0, 0, 0, 0
        def install(module, name, after):
            original = getattr(module, name)
            def forward(*args, **kwargs):
                value = original(*args, **kwargs)
                after(args, value)
                return value
            patch.setattr(module, name, forward)
        def entered(args, value):
            assert type(value) is io.FileIO and self.opening_suffix in SUFFIXES
            self.leases[id(args[0])] = self.opening_suffix
            self.raws[id(value)] = (value, self.opening_suffix)
        def fileno(args, value):
            assert type(value) is int and value >= 0
            key = id(args[0])
            assert key in self.raws
            assert key not in self.fds or self.fds[key] == value
            self.fds[key] = value
        def seek(args, value):
            assert value == 0 and args[1:] == (0, os.SEEK_SET)
            raw, suffix = self.raws[id(args[0])]
            self.scans[suffix].append({"raw": raw, "fd": self.fds[id(raw)], "digest": hashlib.sha256(),
                                      "bytes": 0, "calls": 0, "eofs": 0})
        def read(args, value):
            assert type(value) is bytes and len(value) <= args[1]
            _, suffix = self.raws[id(args[0])]
            scan = self.scans[suffix][-1]
            scan["digest"].update(value)
            scan["bytes"] += len(value)
            scan["calls"] += 1
            scan["eofs"] += int(value == b"")
        def exited(args, value):
            assert value is None
            self.exits.append((args[0], self.leases[id(args[0])], args[2]))
        for name, after in (("_lease_enter", entered), ("_raw_fileno", fileno), ("_raw_seek", seek),
                            ("_raw_read", read), ("_lease_exit", exited)):
            install(held, name, after)
        def node_fd(args, value):
            assert type(args[0]) is io.FileIO and type(value) is int and value >= 0
            key = id(args[0])
            self.node_raws[key] = args[0]
            assert key not in self.node_fds or self.node_fds[key] == value
            self.node_fds[key] = value
        def node_exit(args, value):
            assert value is None
            self.node_exits.append((args[0], args[2]))
        def node_read(args, value):
            assert id(args[0]) in self.node_raws and type(value) is bytes
            self.node_read_bytes += len(value)
            self.node_reads += 1
            self.node_eofs += int(value == b"")
        def node_seek(args, value):
            assert id(args[0]) in self.node_raws and value == 0 and args[1:] == (0, os.SEEK_SET)
            self.node_seeks += 1
        install(nodes, "_raw_fileno", node_fd)
        install(nodes, "_lease_exit", node_exit)
        install(nodes, "_raw_read", node_read)
        install(nodes, "_raw_seek", node_seek)


class Ledger:
    def __init__(self, patch, item):
        self.item = item
        self.original = {name: getattr(subject, name) for name in ALIASES}
        self.calls, self.returns = Counter(), Counter()
        self.events, self.call_order, self.entry_probes = [], [], []
        self.budgets, self.tokens, self.directories, self.image_owners, self.images = [], [], [], [], []
        self.read_budgets, self.holders, self.node_holders, self.projections = [], {}, [], []
        self.file_facts, self.node_facts, self.fingerprints, self.node_bytes = {}, [], [], []
        self.documents, self.exclusive_contexts, self.exit_attempts = [], [], []
        self.raw = RawLedger(patch)
        for name in ALIASES:
            def forward(*args, _name=name, **kwargs):
                self.calls[_name] += 1
                if _name != "_monotonic":
                    self.call_order.append(_name)
                if _name == "_entry_stat":
                    self.entry_probes.append((args, len(self.events)))
                if _name == "_held_enter":
                    self.raw.opening_suffix = self.holders[id(args[0])][1]
                if _name.endswith("_exit"):
                    self.exit_attempts.append((_name, args[0], args[2]))
                result = self.original[_name](*args, **kwargs)
                self.returns[_name] += 1
                if _name not in {"_monotonic", "_deepcopy", "_uuid", "_profile", "_digest", "_sha256_hex"}:
                    self.events.append((_name, args, kwargs, result))
                if _name == "_restricted_budget":
                    assert type(result) is deadline._RestrictedBudget
                    self.budgets.append(result)
                elif _name == "_effective_token":
                    assert result is self.budgets[0].effective_deadline and type(result) is deadline.Namespace3Deadline
                    self.tokens.append(result)
                elif _name == "_exclusive":
                    assert set(kwargs) == {"state"}
                    self.exclusive_contexts.append(result)
                elif _name == "_directory":
                    assert type(result) is directories.DeadlineDirectory
                    assert kwargs["deadline_token"] is self.budgets[0].effective_deadline
                    assert args[0] == item.profile.database.parent and kwargs["base"] == args[0]
                    self.directories.append(result)
                elif _name == "_image":
                    assert type(result) is images.CommittedImageDeadlineLease
                    assert args == (item.profile, self.directories[0]) and kwargs["deadline_token"] is self.budgets[0].effective_deadline
                    self.image_owners.append(result)
                elif _name == "_image_enter":
                    assert type(result) is images.DeadlineCommittedImage
                    assert result.deadline_token is self.budgets[0].effective_deadline
                    self.images.append(result)
                elif _name == "_fingerprint":
                    assert result == self.images[0].source_fingerprint
                    self.fingerprints.append(result)
                elif _name == "_read_budget":
                    assert type(result) is held.LiveSqlHeldBudget and result.deadline_token is self.budgets[0].effective_deadline
                    self.read_budgets.append(result)
                elif _name == "_held":
                    assert type(result) is held.DeadlineLiveSqlHeldFile
                    assert kwargs["deadline_token"] is self.budgets[0].effective_deadline and kwargs["read_budget"] is self.read_budgets[0]
                    self.holders[id(result)] = (result, args[1])
                elif _name == "_held_enter":
                    assert result is args[0]
                    suffix = self.holders[id(result)][1]
                    self.file_facts[suffix] = file_evidence(result)
                elif _name == "_node_directory":
                    assert type(result) is nodes.DeadlineNodeDirectory and args == (self.directories[0], self.budgets[0])
                    assert result.budget is self.budgets[0]
                elif _name == "_node":
                    assert type(result) is nodes.DeadlineHeldFile
                    self.node_holders.append(result)
                elif _name == "_node_enter":
                    assert result is args[0]
                    self.node_facts.append(file_evidence(result))
                elif _name == "_node_bytes":
                    assert args[1] == 4096 and result == item.node_raw
                    self.node_bytes.append(result)
                elif _name == "_project_sql":
                    assert args == (item.profile, self.images[0]) and kwargs["deadline_token"] is self.budgets[0].effective_deadline
                    assert type(result) is tuple and len(result) == 3 and all(type(value) is dict for value in result)
                    self.projections.append(result)
                elif _name == "_canonical_bytes" and type(args[0]) is dict and args[0].get("scope") == "personal-authority-origin-source-v1":
                    self.documents.append((args[0], result))
                return result
            patch.setattr(subject, name, forward)


def arguments():
    return {"domain_id": DOMAIN, "facility_id": FACILITY, "init_operation_id": INIT_OPERATION}


@contextmanager
def borrowed(item, ledger, *, requested=None, callback=None):
    with lifecycle.exclusive(item.profile) as guard:
        item.guard = guard
        if requested is None:
            requested = deadline.Namespace3Deadline(ledger.original["_monotonic"]() + 30.0)
        item.requested = requested
        with subject.capture_locked(item.profile, deadline_token=requested, check_cancelled=callback, **arguments()) as result:
            assert lifecycle.require_guard(item.profile, exclusive=True) is guard
            yield result
        assert lifecycle.require_guard(item.profile, exclusive=True) is guard


def own(item, *, callback=None):
    assert lifecycle.current_guard() is None
    result = subject.observe(item.profile, check_cancelled=callback, **arguments())
    assert lifecycle.current_guard() is None
    return result


def assert_attempts(ledger):
    for factory, exit_name, resources in (("_directory", "_directory_exit", ledger.directories),
        ("_image", "_image_exit", ledger.image_owners), ("_node", "_node_exit", ledger.node_holders),
        ("_held", "_held_exit", [value[0] for value in ledger.holders.values()])):
        assert Counter(id(obj) for name, obj, _ in ledger.exit_attempts if name == exit_name) == Counter(map(id, resources))
    assert Counter(id(lease) for lease, _, _ in ledger.raw.exits) == Counter(ledger.raw.leases.keys())
    if ledger.node_holders:
        assert len(ledger.raw.node_exits) == 1


def assert_full(item, ledger, result):
    assert type(result) is ordinary.OriginObservation and type(result.intent_bytes) is type(result.evidence_bytes) is bytes
    with pytest.raises((AttributeError, TypeError)):
        result.intent_bytes = b"caller-write"
    assert len(ledger.budgets) == len(ledger.directories) == len(ledger.image_owners) == len(ledger.images) == 1
    assert ledger.tokens and all(token is ledger.budgets[0].effective_deadline for token in ledger.tokens)
    token = ledger.budgets[0].effective_deadline
    assert token.absolute_monotonic <= ledger.budgets[0].requested_deadline.absolute_monotonic
    sql, meta, rows = ledger.projections[0]
    assert len(ledger.projections) == 1 and meta == item.meta and rows == item.rows
    assert set(sql) == {"schema_version", "source_fingerprint", "meta", "personal_tables", "receiver_facility", "receiver_tables", "integrity", "foreign_keys"}
    assert sql["schema_version"] == 17 and sql["integrity"] == sql["foreign_keys"] == "ok"
    assert sql["receiver_facility"] == ("installed" if item.installed else "absent")
    personal = {}
    for table, columns in contract.PERSONAL_COLUMNS.items():
        known_rows = [tuple(row[col] for col in columns) for row in item.rows[table]]
        personal[table] = independent_summary(columns, known_rows, item.layouts[table])
    meta_summary = independent_summary(("key", "value"), item.meta_rows, item.layouts["meta"])
    assert sql["meta"] == meta_summary and sql["personal_tables"] == personal
    meta_doc = {"format_version": 1, "scope": "personal-authority-meta-v1", "schema_version": 17, "meta": meta_summary}
    sql_doc = {"format_version": 1, "scope": "personal-authority-ten-and-meta-v1", "schema_version": 17,
               "meta": meta_summary, "personal_tables": personal}
    profile_paths = list(item.profile.identity)
    profile_sha = hashlib.sha256(canonical(profile_paths)).hexdigest()
    intent = {"format_version": 1, "kind": "origin_init_intent", "domain_id": DOMAIN, "operation_id": INIT_OPERATION,
        "issuer": {"facility_id": FACILITY, "node_id": PRIMARY, "profile_sha256": profile_sha},
        "payload": {"current_epoch": 1, "observed_primary_node_id": PRIMARY,
            "observed_personal_meta_sha256": hashlib.sha256(canonical(meta_doc)).hexdigest(),
            "observed_personal_sql_sha256": hashlib.sha256(canonical(sql_doc)).hexdigest()}}
    expected_intent = canonical(intent)
    fingerprint = ledger.fingerprints[0]
    assert fingerprint["profile_identity"] == profile_paths and sql["source_fingerprint"] == fingerprint
    files = {suffix: ledger.file_facts.get(suffix) for suffix in SUFFIXES}
    evidence = {"format_version": 1, "scope": "personal-authority-origin-source-v1", "profile_paths": profile_paths,
        "profile_sha256": profile_sha, "source_fingerprint": fingerprint, "node": ledger.node_facts[0],
        "raw_sql_files": files, "meta_document": meta_doc, "sql_document": sql_doc,
        "intent_sha256": hashlib.sha256(expected_intent).hexdigest()}
    assert result.intent_bytes == expected_intent and result.evidence_bytes == canonical(evidence)
    assert json.loads(result.intent_bytes) == intent and json.loads(result.evidence_bytes) == evidence
    assert ledger.documents == [(evidence, result.evidence_bytes)]
    assert ledger.node_bytes == [item.node_raw]
    assert ledger.node_facts[0]["sha256"] == hashlib.sha256(item.node_raw).hexdigest()
    assert ledger.node_facts[0]["byte_size"] == len(item.node_raw)
    for value in [ledger.node_facts[0], *ledger.file_facts.values()]:
        assert set(value) == {"sha256", "byte_size", "directory_identity", "path_identity", "handle_identity"}
        for name in ("path_identity", "handle_identity"):
            assert value[name]["byte_size"] == value["byte_size"]
            assert set(value[name]) >= {"device", "inode", "byte_size", "mtime_ns", "ctime_ns"}
    read = ledger.read_budgets[0].evidence
    total_bytes = total_calls = total_eofs = 0
    for suffix in SUFFIXES:
        content = item.contents[suffix]
        if content is None:
            assert fingerprint["files"][suffix] is files[suffix] is None
            assert read["passes_started"][suffix] == read["passes_completed"][suffix] == 0
            assert not ledger.raw.scans[suffix]
            continue
        assert files[suffix]["sha256"] == fingerprint["files"][suffix]["sha256"] == hashlib.sha256(content).hexdigest()
        assert files[suffix]["byte_size"] == len(content)
        owner = next(obj for obj, name in ledger.holders.values() if name == suffix)
        history = owner.evidence
        assert history == {"phase": "retired", "lease_state": "closed", "passes_started": 5, "passes_completed": 5,
                           "terminal_verified": True, "normal_exit": True, "source_directory_borrowed": True}
        scans = ledger.raw.scans[suffix]
        assert len(scans) == 5 and len({id(scan["raw"]) for scan in scans}) == len({scan["fd"] for scan in scans}) == 1
        for scan in scans:
            assert scan["bytes"] == len(content) and scan["digest"].hexdigest() == hashlib.sha256(content).hexdigest()
            assert scan["eofs"] == 1
            total_bytes += scan["bytes"]
            total_calls += scan["calls"]
            total_eofs += scan["eofs"]
        assert read["passes_started"][suffix] == read["passes_completed"][suffix] == 5
    assert read["read_bytes"] == total_bytes and read["read_calls"] == total_calls and read["eof_reads"] == total_eofs
    assert read["retired"] is True and read["failed"] is False
    assert ledger.budgets[0].control_bytes == ledger.raw.node_read_bytes > 0
    assert ledger.raw.node_seeks == ledger.raw.node_eofs > 0
    assert len(ledger.raw.node_raws) == len(ledger.raw.node_exits) == 1
    assert_attempts(ledger)
    names = [event[0] for event in ledger.events]
    assert names.index("_image_enter") < names.index("_held_enter") < names.index("_node_enter") < names.index("_project_sql")
    image_exit = names.index("_image_exit")
    assert image_exit < names.index("_node_exit") < names.index("_held_exit") < names.index("_directory_exit")
    suffix_exits = [ledger.holders[id(args[0])][1] for name, args, _, _ in ledger.events if name == "_held_exit"]
    assert suffix_exits == [suffix for suffix in reversed(SUFFIXES) if item.contents[suffix] is not None]
    probes = [(index, args[1]) for args, index in ledger.entry_probes]
    assert [name for _, name in probes] == [item.profile.database.name + suffix for suffix in ("-wal", "-journal") if item.contents[suffix] is None]
    assert all(image_exit < index <= names.index("_directory_exit") for index, _ in probes)
    assert all(all(names.index(exit_name) < index for exit_name in ("_node_exit", "_held_exit")) for index, _ in probes)
    image_history = ledger.image_owners[0].evidence
    assert image_history["normal_exit"] is True and image_history["phase"] == "retired"


class ActualContextProxy:
    """Only the new _exclusive functional alias; exact original protocol forwarding."""
    def __init__(self, actual, *, after_normal=None, normal_result=False):
        self.actual, self.after_normal, self.normal_result = actual, after_normal, normal_result
        self.enter_attempts = self.enter_returns = self.exit_attempts = self.exit_returns = 0
        self.guard = None
    def __enter__(self):
        self.enter_attempts += 1
        value = self.actual.__enter__()
        self.enter_returns += 1
        assert type(value) is lifecycle.StorageGuard and value.exclusive is True
        self.guard = value
        return value
    def __exit__(self, kind, primary, tb):
        self.exit_attempts += 1
        assert self.exit_attempts == 1 and self.enter_returns == 1
        value = self.actual.__exit__(kind, primary, tb)
        self.exit_returns += 1
        if primary is not None:
            return value
        assert value is False
        if self.after_normal is not None:
            self.after_normal()
        return self.normal_result


def install_exclusive_proxy(patch, *, after_normal=None, normal_result=False):
    original = subject._exclusive
    proxies = []
    def factory(profile, budget, *, state):
        value = original(profile, budget, state=state)
        proxy = ActualContextProxy(value, after_normal=after_normal, normal_result=normal_result)
        proxies.append(proxy)
        return proxy
    patch.setattr(subject, "_exclusive", factory)
    return proxies


def complete_borrow(item, ledger, *, consumer=None, callback=None, requested=None):
    with borrowed(item, ledger, requested=requested, callback=callback) as candidate:
        if consumer is not None:
            consumer(candidate)
        result = candidate
    return result


@pytest.mark.parametrize("kind", POSITIVE_IDS, ids=POSITIVE_IDS)
def test_complete_actual_origin(qa_case, monkeypatch, kind):
    mode = {"borrowed-wal-empty-journal": "wal", "owned-absent-companions": "absent", "borrowed-prepared-receiver": "prepared"}[kind]
    with qa_case(mode) as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            if kind == "owned-absent-companions":
                proxies = install_exclusive_proxy(patch)
                result = own(item)
                assert len(proxies) == 1 and proxies[0].enter_returns == proxies[0].exit_returns == 1
                assert proxies[0].guard is not None and lifecycle.current_guard() is None
                assert ledger.call_order.index("_deadline") < ledger.call_order.index("_profile") < ledger.call_order.index("_exclusive")
            else:
                requested = deadline.Namespace3Deadline(1.0e15) if kind == "borrowed-prepared-receiver" else None
                result = complete_borrow(item, ledger, requested=requested)
                if requested is None:
                    assert ledger.budgets[0].effective_deadline is item.requested
                else:
                    assert ledger.budgets[0].requested_deadline is requested and ledger.budgets[0].effective_deadline is not requested
                    assert ledger.budgets[0].effective_deadline.absolute_monotonic < requested.absolute_monotonic
            assert_full(item, ledger, result)
            assert ledger.calls["_exclusive"] == int(kind == "owned-absent-companions")


DOMAIN_IDS = ("invalid-token-before-clock", "borrowed-profile-subclass", "borrowed-sh-rejected",
    "borrowed-other-profile-ex", "owned-existing-sh", "owned-existing-ex",
    "owned-preacquire-callback-creates-guard", "owned-first-acquisition-poll-creates-guard")


@pytest.mark.parametrize("kind", DOMAIN_IDS, ids=DOMAIN_IDS)
def test_exact_public_and_guard_domains(qa_case, monkeypatch, kind):
    with qa_case("absent") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            if kind == "invalid-token-before-clock":
                manager = subject.capture_locked(item.profile, deadline_token=object(), **arguments())
                coded(manager.__enter__, ordinary.OriginSourceError, "ORIGIN_INVALID_ARGUMENT")
                assert all(ledger.calls[name] == 0 for name in ("_monotonic", "_restricted_budget", "_directory", "_image", "_exclusive"))
            elif kind == "borrowed-profile-subclass":
                class Subprofile(lifecycle.StorageProfile):
                    pass
                sub = Subprofile(item.profile.database, item.profile.media, item.profile.reports, item.profile.checkpoints)
                with lifecycle.exclusive(item.profile):
                    token = deadline.Namespace3Deadline(ledger.original["_monotonic"]() + 30.0)
                    manager = subject.capture_locked(sub, deadline_token=token, **arguments())
                    coded(manager.__enter__, ordinary.OriginSourceError, "ORIGIN_INVALID_ARGUMENT")
            elif kind == "borrowed-sh-rejected":
                with lifecycle.shared(item.profile) as external:
                    token = deadline.Namespace3Deadline(ledger.original["_monotonic"]() + 30.0)
                    manager = subject.capture_locked(item.profile, deadline_token=token, **arguments())
                    coded(manager.__enter__, lifecycle.StorageLifecycleError, "STORAGE_LOCK_UPGRADE")
                    assert lifecycle.current_guard() is external and external.verify() is external
            elif kind == "borrowed-other-profile-ex":
                # Prepare the second facility before either profile has a current guard.
                with qa_case("absent") as other:
                    with lifecycle.exclusive(other.profile) as external:
                        token = deadline.Namespace3Deadline(ledger.original["_monotonic"]() + 30.0)
                        manager = subject.capture_locked(item.profile, deadline_token=token, **arguments())
                        coded(manager.__enter__, lifecycle.StorageLifecycleError, "STORAGE_PROFILE_MISMATCH")
                        assert lifecycle.current_guard() is external and external.verify() is external
            elif kind in {"owned-existing-sh", "owned-existing-ex"}:
                factory = lifecycle.shared if kind == "owned-existing-sh" else lifecycle.exclusive
                with factory(item.profile) as external:
                    coded(lambda: subject.observe(item.profile, **arguments()), ordinary.OriginSourceError, "ORIGIN_GUARD_ALREADY_HELD")
                    assert lifecycle.current_guard() is external and external.verify() is external
                assert ledger.calls["_exclusive"] == 0
            else:
                armed = False
                external_manager = external_guard = None
                polls = []
                def callback():
                    nonlocal external_manager, external_guard
                    polls.append(armed)
                    if armed and external_manager is None:
                        external_manager = lifecycle.exclusive(item.profile)
                        external_guard = external_manager.__enter__()
                if kind == "owned-preacquire-callback-creates-guard":
                    original = subject._current_guard
                    def current():
                        nonlocal armed
                        value = original()
                        if value is None:
                            armed = True
                        return value
                    patch.setattr(subject, "_current_guard", current)
                else:
                    original = subject._exclusive
                    def acquire(profile, budget, *, state):
                        nonlocal armed
                        value = original(profile, budget, state=state)
                        armed = True
                        return value
                    patch.setattr(subject, "_exclusive", acquire)
                try:
                    coded(lambda: own(item, callback=callback), ordinary.OriginSourceError, "ORIGIN_GUARD_ALREADY_HELD")
                    assert external_guard is not None and any(polls)
                    assert lifecycle.current_guard() is external_guard and external_guard.verify() is external_guard
                    assert ledger.calls["_exclusive"] == int(kind == "owned-first-acquisition-poll-creates-guard")
                finally:
                    if external_manager is not None:
                        assert external_manager.__exit__(None, None, None) is False
                assert lifecycle.current_guard() is None
            assert ledger.calls["_directory"] == ledger.calls["_image"] == ledger.calls["_project_sql"] == 0


ABSENT_IDS = ("wal-created-after-image-exit", "journal-created-after-image-exit", "accepted-absence-postexpiry", "permission-error-is-not-absence")


@pytest.mark.parametrize("kind", ABSENT_IDS, ids=ABSENT_IDS)
def test_last_absent_metadata_gate(qa_case, monkeypatch, kind):
    with qa_case("absent") as item:
        changed = None
        try:
            with monkeypatch.context() as patch:
                ledger = Ledger(patch, item)
                scalar = Scalar(ledger.original["_monotonic"]())
                permission = PermissionError("r833 new alias terminal metadata permission fault")
                armed, touched = False, []
                original_exit, original_stat = subject._image_exit, subject._entry_stat
                def image_exit(*args):
                    nonlocal armed, changed
                    result = original_exit(*args)
                    assert result is None and args[1:3] == (None, None)
                    armed = True
                    if kind.startswith(("wal-created", "journal-created")):
                        suffix = "-wal" if kind.startswith("wal-") else "-journal"
                        changed = item.profile.database.with_name(item.profile.database.name + suffix)
                        assert not changed.exists()
                        changed.write_bytes(b"")
                    return result
                def entry_stat(directory, name):
                    assert armed and directory is ledger.directories[0]
                    assert all(owner.evidence["phase"] == "retired" for owner, _ in ledger.holders.values())
                    assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard
                    touched.append(name)
                    if kind == "permission-error-is-not-absence":
                        raise permission
                    try:
                        return original_stat(directory, name)
                    except FileNotFoundError:
                        if kind == "accepted-absence-postexpiry":
                            scalar.value = ledger.budgets[0].effective_deadline.absolute_monotonic
                        raise
                patch.setattr(subject, "_image_exit", image_exit)
                patch.setattr(subject, "_entry_stat", entry_stat)
                if kind == "accepted-absence-postexpiry":
                    patch.setattr(subject, "_monotonic", scalar)
                if kind == "permission-error-is-not-absence":
                    with pytest.raises(PermissionError) as caught:
                        complete_borrow(item, ledger)
                    assert caught.value is permission
                else:
                    code = "ORIGIN_TIME_BUDGET_EXCEEDED" if kind == "accepted-absence-postexpiry" else "ORIGIN_SOURCE_CHANGED"
                    coded(lambda: complete_borrow(item, ledger), ordinary.OriginSourceError, code)
                assert touched
                assert all(owner.evidence["passes_completed"] == 5 and owner.evidence["normal_exit"] is True for owner, _ in ledger.holders.values())
                assert ledger.image_owners[0].evidence["normal_exit"] is True
                assert_attempts(ledger)
        finally:
            if changed is not None:
                changed.unlink()


SEMANTIC_IDS = ("origin-three-meta-rejected", "origin-node-primary-mismatch", "origin-default-project-missing", "origin-node-duplicate-key", "origin-epoch-over-maximum")
SEMANTIC_MUTATIONS = ("three-meta", "primary-mismatch", "default-missing", "node-duplicate", "epoch-max")
SEMANTIC_CODES = ("ORIGIN_META_INVALID", "ORIGIN_PRIMARY_MISMATCH", "ORIGIN_DEFAULT_PROJECT_INVALID", "ORIGIN_NODE_INVALID", "ORIGIN_META_INVALID")


@pytest.mark.parametrize("mutation,code", tuple(zip(SEMANTIC_MUTATIONS, SEMANTIC_CODES)), ids=SEMANTIC_IDS)
def test_original_semantic_gate_order(qa_case, monkeypatch, mutation, code):
    with qa_case("wal", mutation=mutation) as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            coded(lambda: complete_borrow(item, ledger), ordinary.OriginSourceError, code)
            assert ledger.calls["_project_sql"] == int(mutation != "node-duplicate")
            if mutation == "three-meta":
                assert all(not item.rows[name] for name in contract.PERSONAL_COLUMNS)
                assert set(item.meta) == {"library_id", "personal_primary_id", "primary_epoch"}
                assert ledger.projections[0][1] == item.meta
            if mutation == "epoch-max":
                assert ledger.projections[0][1]["primary_epoch"] == str(1 << 63)
            assert not ledger.documents
            assert_attempts(ledger)


class FalseyCallback:
    def __init__(self, first):
        self.first, self.armed, self.calls, self.failures = first, False, 0, 0
    def __bool__(self):
        return False
    def __call__(self):
        self.calls += 1
        if self.armed:
            self.failures += 1
            raise self.first


@pytest.mark.parametrize("kind", ("falsey-callback-after-projection", "owned-cancel-after-exit"),
                         ids=("falsey-callback-after-projection", "owned-cancel-after-exit"))
def test_cancel_first_identity_and_cadence(qa_case, monkeypatch, kind):
    with qa_case("absent") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            first = OSError("r833 original user cancellation first")
            callback = FalseyCallback(first)
            if kind == "falsey-callback-after-projection":
                original = subject._project_sql
                def projected(*args, **kwargs):
                    value = original(*args, **kwargs)
                    callback.armed = True
                    return value
                patch.setattr(subject, "_project_sql", projected)
                proxies = install_exclusive_proxy(patch)
            else:
                proxies = install_exclusive_proxy(patch, after_normal=lambda: setattr(callback, "armed", True))
            with pytest.raises(OSError) as caught:
                own(item, callback=callback)
            assert caught.value is first and callback.calls > 0 and callback.failures == 1
            assert len(proxies) == 1 and proxies[0].enter_returns == proxies[0].exit_returns == 1
            assert lifecycle.current_guard() is None
            assert_attempts(ledger)
            if kind == "owned-cancel-after-exit":
                assert ledger.image_owners[0].evidence["normal_exit"] is True
                assert all(owner.evidence["normal_exit"] is True for owner, _ in ledger.holders.values())


@pytest.mark.parametrize("kind", ("consumer-control-first-cleanup-seconds", "caught-held-first-cannot-complete"),
                         ids=("consumer-control-first-cleanup-seconds", "caught-held-first-cannot-complete"))
def test_control_and_caught_lower_first_remain_sticky(qa_case, monkeypatch, kind):
    with qa_case("wal") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            if kind == "consumer-control-first-cleanup-seconds":
                class GetterTrap(KeyboardInterrupt):
                    @property
                    def __traceback__(self):
                        pytest.fail("control traceback getter was read")
                    @__traceback__.setter
                    def __traceback__(self, value):
                        BaseException.__dict__["__traceback__"].__set__(self, value)
                    @property
                    def with_traceback(self):
                        pytest.fail("control with_traceback getter was read")
                first = GetterTrap("r833 caller original control first")
                seconds = {}
                for name in ("_image_exit", "_node_exit", "_held_exit", "_directory_exit"):
                    original = getattr(subject, name)
                    seconds[name] = OSError("r833 actual exit outer second " + name)
                    def broken(*args, _original=original, _name=name):
                        value = _original(*args)
                        assert value is None and args[2] is first
                        raise seconds[_name]
                    patch.setattr(subject, name, broken)
                def consumer(_):
                    raise first
                with pytest.raises(GetterTrap) as caught:
                    complete_borrow(item, ledger, consumer=consumer)
                assert caught.value is first
                assert "ORIGIN_SOURCE_CLEANUP_FAILED" in getattr(first, "__notes__", ())
            else:
                observed = []
                def consumer(_):
                    owner = next(obj for obj, suffix in ledger.holders.values() if suffix == "")
                    assert owner.evidence["passes_completed"] == 3
                    owner.verify()
                    owner.verify()
                    assert owner.evidence["passes_completed"] == 5
                    first = coded(owner.verify, held.LiveSqlHeldDeadlineError, "LIVE_SQL_HELD_DEADLINE_LIMIT")
                    observed.append(first)
                    assert len(ledger.raw.scans[""]) == 5
                with pytest.raises(held.LiveSqlHeldDeadlineError) as caught:
                    complete_borrow(item, ledger, consumer=consumer)
                assert caught.value is observed[0] and len(ledger.raw.scans[""]) == 5
                assert ledger.read_budgets[0].evidence["failed"] is True
            assert_attempts(ledger)


EXIT_IDS = ("held-exit-false-is-invalid", "image-exit-outer-error", "owned-exit-none-is-invalid")


@pytest.mark.parametrize("kind", EXIT_IDS, ids=EXIT_IDS)
def test_normal_exit_representation_and_outer_error_prevent_delivery(qa_case, monkeypatch, kind):
    with qa_case("absent") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            if kind == "owned-exit-none-is-invalid":
                proxies = install_exclusive_proxy(patch, normal_result=None)
                coded(lambda: own(item), ordinary.OriginSourceError, "ORIGIN_INVALID_RESULT")
                assert len(proxies) == 1 and proxies[0].exit_returns == 1 and lifecycle.current_guard() is None
            elif kind == "held-exit-false-is-invalid":
                original = subject._held_exit
                touched = []
                def exit_false(*args):
                    value = original(*args)
                    assert value is None
                    if ledger.holders[id(args[0])][1] == "" and args[2] is None:
                        touched.append(args[0].evidence)
                        return False
                    return value
                patch.setattr(subject, "_held_exit", exit_false)
                coded(lambda: complete_borrow(item, ledger), ordinary.OriginSourceError, "ORIGIN_INVALID_RESULT")
                assert len(touched) == 1 and touched[0]["normal_exit"] is True
                owner = next(obj for obj, suffix in ledger.holders.values() if suffix == "")
                assert owner.evidence == touched[0]
            else:
                first = OSError("r833 image primitive returned then outer alias failed")
                original = subject._image_exit
                def broken(*args):
                    value = original(*args)
                    assert value is None and args[2] is None
                    raise first
                patch.setattr(subject, "_image_exit", broken)
                with pytest.raises(OSError) as caught:
                    complete_borrow(item, ledger)
                assert caught.value is first and ledger.image_owners[0].evidence["normal_exit"] is True
            assert_attempts(ledger)


EXPIRY_IDS = ("evidence-bytes-postreturn-expiry", "source-pin-exit-postreturn-expiry", "owned-ex-exit-postreturn-expiry")


@pytest.mark.parametrize("kind", EXPIRY_IDS, ids=EXPIRY_IDS)
def test_opaque_actual_return_postexpiry_blocks_completion(qa_case, monkeypatch, kind):
    with qa_case("absent") as item:
        with monkeypatch.context() as patch:
            ledger = Ledger(patch, item)
            scalar = Scalar(ledger.original["_monotonic"]())
            patch.setattr(subject, "_monotonic", scalar)
            touched = []
            def arm():
                assert not touched
                touched.append(True)
                scalar.value = ledger.budgets[0].effective_deadline.absolute_monotonic
            if kind == "evidence-bytes-postreturn-expiry":
                original = subject._canonical_bytes
                def canonical_then_expire(value):
                    result = original(value)
                    if type(value) is dict and value.get("scope") == "personal-authority-origin-source-v1":
                        assert type(result) is bytes and json.loads(result) == value
                        arm()
                    return result
                patch.setattr(subject, "_canonical_bytes", canonical_then_expire)
                call = lambda: complete_borrow(item, ledger)
            elif kind == "source-pin-exit-postreturn-expiry":
                original = subject._directory_exit
                def closed_then_expire(*args):
                    result = original(*args)
                    assert result is None and args[2] is None
                    arm()
                    return result
                patch.setattr(subject, "_directory_exit", closed_then_expire)
                call = lambda: complete_borrow(item, ledger)
            else:
                proxies = install_exclusive_proxy(patch, after_normal=arm)
                call = lambda: own(item)
            coded(call, ordinary.OriginSourceError, "ORIGIN_TIME_BUDGET_EXCEEDED")
            assert touched == [True] and scalar.value == ledger.budgets[0].effective_deadline.absolute_monotonic
            assert_attempts(ledger)
            if kind != "evidence-bytes-postreturn-expiry":
                assert ledger.image_owners[0].evidence["normal_exit"] is True
                assert all(owner.evidence["normal_exit"] is True for owner, _ in ledger.holders.values())
            if kind == "owned-ex-exit-postreturn-expiry":
                assert proxies[0].exit_returns == 1 and lifecycle.current_guard() is None
