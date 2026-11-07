"""r834 fixed55 IDs/56 instances; actual new aliases and independent full raw oracles.

Root alone imports/collects/runs this test. No runtime was executed by the author.
Only the 125 new r834 aliases admit fixed faults. Previously approved r833/held/node
aliases are read-only actual-forward joins during the real origin context only.
"""
from collections import Counter, defaultdict
from contextlib import contextmanager
import base64
import copy
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import stat
import tempfile
from types import SimpleNamespace

import pytest
from reno import personal_authority_origin_reservation_deadline as locked
from reno import personal_authority_origin_reservation_owner_deadline as owned
from reno import personal_authority_origin_reservation_io_deadline as publication
from reno import personal_authority_origin_reservation as reservation
from reno import personal_authority_origin_reservation_io as original_io
from reno import personal_authority_origin_reservation_contract as reservation_contract
from reno import personal_authority_origin_capture_deadline as capture
from reno import personal_authority_origin_source as originordinary
from reno import personal_authority_namespace3_deadline as deadline
from reno import personal_authority_directory_deadline as directories
from reno import personal_authority_source_deadline as nodes
from reno import personal_authority_committed_image_deadline as images
from reno import personal_authority_live_sql_held_deadline as held
from reno import personal_mirror_contract as facts
from reno import personal_mirror_transfer_schema as receiver
from reno import personal_preservation_contract as contract
from reno import storage_lifecycle as lifecycle

pytestmark = pytest.mark.skipif(os.name != "nt", reason="actual Windows EX/native/held FD protocol")
PROJECT_TABLE = "renovation_project"
STAMP = "2026-10-12T00:00:00Z"
ORIGIN_ALIASES = ("_monotonic", "_deadline", "_restricted_budget", "_effective_token", "_profile", "_uuid",
    "_current_guard", "_require_guard", "_exclusive", "_directory", "_directory_enter", "_directory_exit",
    "_directory_verify", "_entry_stat", "_image", "_image_enter", "_image_exit", "_fingerprint", "_image_verify",
    "_read_budget", "_read_budget_evidence", "_held", "_held_enter", "_held_exit", "_held_verify", "_held_evidence",
    "_node_directory", "_node", "_node_enter", "_node_exit", "_node_verify", "_node_bytes", "_project_sql",
    "_canonical_bytes", "_digest", "_encode_intent", "_sha256_hex", "_deepcopy")
SUFFIXES = ("", "-wal", "-journal")
ROOT_NAMES = ("data", "media", "reports", "checkpoints")
FIRST = ".reno-personal-authority-origin-v1-reservation.json"
SECOND = ".reno-personal-authority-origin-v1-observation.json"
UNKNOWN = ".RENO-PERSONAL-AUTHORITY-unknown. "
DIRECT_RAW = (b"r834-independent-publisher-raw\x00" + "绍兴原始bytes".encode("utf-8")) * 256
assert 8192 < len(DIRECT_RAW) <= 64 * 1024


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
        base = tmp_path / f"r834-{sequence}-{mode}"
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
                sql_rows(rw, "CREATE TABLE r834_nonpersonal_facts(key TEXT PRIMARY KEY,value BLOB)")
                sql_rows(rw, "INSERT INTO r834_nonpersonal_facts VALUES(?,?)", ("known", b"nonpersonal\x00\xff"))
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


class RawLedger:
    def __init__(self, patch, *, observe):
        self.observe = observe
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
                if self.observe():
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


class OriginLedger:
    def __init__(self, patch, item):
        self.item = item
        self.functional_depth = 0
        self.original = {name: getattr(capture, name) for name in ORIGIN_ALIASES}
        self.calls, self.returns = Counter(), Counter()
        self.events, self.call_order, self.entry_probes = [], [], []
        self.budgets, self.tokens, self.directories, self.image_owners, self.images = [], [], [], [], []
        self.read_budgets, self.holders, self.node_holders, self.projections = [], {}, [], []
        self.file_facts, self.node_facts, self.fingerprints, self.node_bytes = {}, [], [], []
        self.documents, self.exclusive_contexts, self.exit_attempts = [], [], []
        self.raw = RawLedger(patch, observe=lambda: self.functional_depth > 0)
        for name in ORIGIN_ALIASES:
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
                self.functional_depth += 1
                try:
                    result = self.original[_name](*args, **kwargs)
                finally:
                    self.functional_depth -= 1
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
                    assert type(result) is dict
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
            patch.setattr(capture, name, forward)


def assert_origin_attempts(ledger):
    for factory, exit_name, resources in (("_directory", "_directory_exit", ledger.directories),
        ("_image", "_image_exit", ledger.image_owners), ("_node", "_node_exit", ledger.node_holders),
        ("_held", "_held_exit", [value[0] for value in ledger.holders.values()])):
        assert Counter(id(obj) for name, obj, _ in ledger.exit_attempts if name == exit_name) == Counter(map(id, resources))
    assert Counter(id(lease) for lease, _, _ in ledger.raw.exits) == Counter(ledger.raw.leases.keys())
    if ledger.node_holders:
        assert len(ledger.raw.node_exits) == 1


def assert_origin_full(item, ledger, result):
    assert type(result) is originordinary.OriginObservation and type(result.intent_bytes) is type(result.evidence_bytes) is bytes
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
    read_values = [value for name, args, kwargs, value in ledger.events if name == "_read_budget_evidence"]
    assert len(read_values) == 1
    read = read_values[0]
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
        histories = [value for name, args, kwargs, value in ledger.events
                     if name == "_held_evidence" and args == (owner,)]
        assert len(histories) == 1
        history = histories[0]
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
    assert_origin_attempts(ledger)
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
    assert ledger.returns["_image_exit"] == 1
    assert next(value for name, args, kwargs, value in ledger.events if name == "_image_exit") is None


LOCKED_ALIASES = (
    "_monotonic", "_deadline", "_restricted_budget", "_effective_token", "_profile_paths", "_uuid", "_sha",
    "_require_guard", "_key", "_directory", "_directory_enter", "_directory_exit", "_directory_verify",
    "_directory_identity", "_scandir", "_entries_enter", "_entries_next", "_entry_name", "_entries_exit",
    "_node_directory", "_node", "_node_enter", "_node_exit", "_node_verify", "_node_bytes", "_node_id",
    "_file_evidence", "_control", "_control_enter", "_control_exit", "_control_verify", "_control_bytes",
    "_read_lease", "_read_enter", "_read_exit", "_raw_seek", "_raw_read", "_raw_fileno", "_fstat",
    "_file_identity", "_hash_new", "_hash_update", "_hash_hex", "_bytes_join", "_encode", "_decode",
    "_stage_name", "_cross_validate", "_encode_observation", "_digest", "_sha256_hex", "_deepcopy",
    "_publish", "_origin_context", "_origin_enter", "_origin_exit", "_result")
PUBLICATION_ALIASES = (
    "_monotonic", "_token", "_profile", "_profile_paths", "_require_guard", "_directory_verify",
    "_directory_token", "_directory_path", "_operation", "_native_path", "_path_stat", "_safe_stat", "_identity",
    "_create_file", "_file_information", "_close_handle", "_current_process", "_open_process_token",
    "_token_information", "_sid_string", "_security_descriptor", "_local_free", "_get_last_error",
    "_open_osfhandle", "_file_io", "_close_fd", "_retire_raw", "_raw_fileno", "_raw_seek", "_raw_read",
    "_raw_write", "_raw_flush", "_fsync", "_fstat", "_nt_set_information", "_readonly_lease",
    "_readonly_enter", "_readonly_exit", "_parent", "_parent_enter", "_parent_exit", "_parent_verify",
    "_parent_entry_stat", "_creator", "_creator_enter", "_creator_exit", "_alive_reader", "_alive_enter",
    "_alive_exit", "_sha256_hex", "_published_file", "_copy_identity")
OWNED_ALIASES = (
    "_monotonic", "_deadline", "_restricted_budget", "_effective_token", "_uuid", "_sha", "_profile_paths",
    "_current_guard", "_require_guard", "_exclusive", "_acquisition_clock", "_exclusive_enter",
    "_exclusive_exit", "_reserve_locked", "_resume_locked", "_validate_result")
ALIAS_GROUPS = (("L", locked, LOCKED_ALIASES), ("P", publication, PUBLICATION_ALIASES),
                ("O", owned, OWNED_ALIASES))
assert tuple(len(row[2]) for row in ALIAS_GROUPS) == (57, 52, 16)
EXIT_TO_ENTER = {"L12": "L11", "L19": "L15", "L23": "L22", "L30": "L29", "L35": "L34",
                 "L56": "L55", "P38": "P37", "P41": "P40", "P46": "P45", "P49": "P48", "O13": "O12"}


def arguments():
    return {"domain_id": DOMAIN, "facility_id": FACILITY, "init_operation_id": INIT_OPERATION}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def native_identity(path):
    info = os.stat(native(path), follow_symlinks=False)
    assert not stat.S_ISLNK(info.st_mode)
    assert not (getattr(info, "st_file_attributes", 0) & 0x400)
    return {"device": str(info.st_dev), "inode": str(info.st_ino), "byte_size": info.st_size,
            "mtime_ns": str(info.st_mtime_ns), "ctime_ns": str(info.st_ctime_ns),
            "mode": info.st_mode, "nlink": info.st_nlink}


def native_full(path):
    before = native_identity(path)
    with open(native(path), "rb") as stream:
        raw = stream.read()
        assert stream.read(1) == b""
    assert native_identity(path) == before and before["byte_size"] == len(raw)
    return raw, before


def authority_files(item):
    result = {}
    with os.scandir(native(item.profile.database.parent)) as entries:
        for entry in entries:
            name = entry.name
            if name.rstrip(" .").casefold().startswith(".reno-personal-authority-"):
                assert not entry.is_symlink()
                result[name] = native_full(item.profile.database.parent / name)
    return result


def stage_name(name):
    return name + "." + INIT_OPERATION + ".stage"


def create_fixture_file(item, name, raw):
    """Only a new Root QA profile; exact extended native path, never unlink."""
    parent = item.profile.database.parent
    fd = os.open(native(parent / name), os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_BINARY, 0o600)
    try:
        with os.fdopen(fd, "wb", closefd=False) as stream:
            assert stream.write(raw) == len(raw)
            stream.flush()
            os.fsync(fd)
    finally:
        os.close(fd)
    with os.scandir(native(parent)) as entries:
        actual_names = [entry.name for entry in entries]
    assert actual_names.count(name) == 1
    assert name.encode("utf-8") == next(n.encode("utf-8") for n in actual_names if n == name)
    assert native_full(parent / name)[0] == raw


@contextmanager
def caller_ex(item):
    with lifecycle.exclusive(item.profile) as guard:
        item.guard = guard
        yield guard
        assert lifecycle.require_guard(item.profile, exclusive=True) is guard
    item.guard = None


def requested_token():
    return deadline.Namespace3Deadline(locked._monotonic() + 30.0)


def reserve(item, token, callback=None):
    return locked.reserve_origin_locked(item.profile, deadline_token=token,
                                         check_cancelled=callback, **arguments())


def resume(item, token, first_sha, second_sha=None, callback=None, *, domain=DOMAIN):
    options = arguments()
    options["domain_id"] = domain
    return locked.resume_origin_locked(item.profile, deadline_token=token,
        expected_reservation_sha256=first_sha, expected_observation_sha256=second_sha,
        check_cancelled=callback, **options)


def prepare(item, patch, *, complete):
    """One fixed preparation invocation; failure is setup failure, never retried."""
    sentinel = reservation.OriginReservationError("TEST_PREPARATION_FIRST")
    with patch.context() as preparation:
        if not complete:
            def reject_origin(*args, **kwargs):
                raise sentinel
            preparation.setattr(locked, "_origin_context", reject_origin)
        with caller_ex(item):
            token = requested_token()
            if complete:
                value = reserve(item, token)
                assert type(value) is reservation.OriginReservationResult
            else:
                with pytest.raises(reservation.OriginReservationError) as caught:
                    reserve(item, token)
                assert caught.value is sentinel
    files = authority_files(item)
    assert set(files) == ({FIRST, SECOND} if complete else {FIRST})
    return files


class FunctionalLedger:
    """Only explicit functional calls/returns. No private owner or runtime inspection."""
    def __init__(self, patch, item):
        self.patch, self.item = patch, item
        self.original, self.calls, self.returns = {}, Counter(), Counter()
        self.before, self.after = {}, {}
        self.events, self.stack, self.errors, self.projected_errors = [], [], [], []
        self.tokens, self.directories, self.directory_paths, self.root_facts = [], {}, {}, {}
        self.controls, self.control_leases, self.streams, self.scans, self.fileno = {}, {}, {}, defaultdict(list), {}
        self.known, self.exit_attempts = defaultdict(dict), defaultdict(list)
        self.origins, self.origin_ledgers, self.origin_patch = [], [], None
        self.fd_generations, self.fd_current, self.raw_fds, self.handles = [], {}, {}, []
        self.publisher_streams, self.publisher_fileno, self.publisher_scans = {}, {}, defaultdict(list)
        self.fd_close_attempts, self.raw_retire_attempts, self.handle_close_attempts = [], [], []
        self.parent_exit_active = False
        self.arm = {"L": None, "P": None, "O": None}
        self.capture_fd, self.capture_wrapper, self.recovery_events = None, None, []
        self.primary_tb = {}
        for prefix, module, names in ALIAS_GROUPS:
            for ordinal, name in enumerate(names, 1):
                code = f"{prefix}{ordinal:02d}"
                self.original[code] = getattr(module, name)
                patch.setattr(module, name, self.wrapper(code))

    def __enter__(self):
        return self

    def __exit__(self, kind, primary, tb):
        self.stop_origin_observation()
        return False

    def observe_origin(self):
        assert self.origin_patch is None
        self.origin_patch = self.patch.context()
        nested = self.origin_patch.__enter__()
        self.origin_ledgers.append(OriginLedger(nested, self.item))

    def stop_origin_observation(self):
        if self.origin_patch is not None:
            patch, self.origin_patch = self.origin_patch, None
            patch.__exit__(None, None, None)

    def active(self, code):
        return any(row[0] == code for row in self.stack)

    def active_args(self, code):
        return next(args for name, args in reversed(self.stack) if name == code)

    def wrapper(self, code):
        def forward(*args, **kwargs):
            self.calls[code] += 1
            ordinal = self.calls[code]
            if code in EXIT_TO_ENTER:
                self.exit_attempts[code].append(id(args[0]))
            if code == "P26":
                self.fd_close_attempts.append(self.fd_current.get(args[0]))
            elif code == "P27":
                self.raw_retire_attempts.append(id(args[0]))
            elif code == "P16":
                self.handle_close_attempts.append(args[0])
            if code in self.before:
                self.before[code](ordinal, args, kwargs)
            if code == "L55":
                self.observe_origin()
            self.stack.append((code, args))
            try:
                actual = self.original[code](*args, **kwargs)
            except BaseException as failure:
                self.errors.append((code, ordinal, failure))
                raise
            else:
                self.returns[code] += 1
                self.record_actual(code, ordinal, args, kwargs, actual)
                projected = actual
                if code in {"L01", "P01", "O01"} and self.arm[code[0]] is not None:
                    projected = self.arm[code[0]]
                if code in self.after:
                    try:
                        projected = self.after[code](ordinal, args, kwargs, projected)
                    except BaseException as failure:
                        self.projected_errors.append((code, ordinal, failure))
                        raise
                if code not in {"L01", "P01", "O01"}:
                    self.events.append(("deliver", code, ordinal, args, kwargs, projected))
                return projected
            finally:
                self.stack.pop()
                if code in {"L55", "L56"} and (code == "L56" or self.returns[code] < ordinal):
                    self.stop_origin_observation()
        return forward

    def record_actual(self, code, ordinal, args, kwargs, value):
        if code not in {"L01", "P01", "O01"}:
            self.events.append(("actual", code, ordinal, args, kwargs, value))
        if code in {"L04", "O04", "P02", "P07"}:
            self.tokens.append(value)
        if code == "L10":
            self.directory_paths[id(value)] = args[0]
        elif code == "L11":
            self.directories[id(args[0])] = value
        elif code == "L14":
            self.root_facts[self.directory_paths[id(args[0])]] = value
        elif code == "L28":
            self.controls[id(value)] = (value, args[1])
        elif code == "L33":
            control = self.active_args("L29")[0]
            self.control_leases[id(value)] = id(control)
        elif code == "L34":
            self.streams[id(value)] = (value, self.controls[self.control_leases[id(args[0])]][1])
        elif code == "L38":
            key = id(args[0])
            assert type(value) is int and value >= 0
            assert key not in self.fileno or self.fileno[key] == value
            self.fileno[key] = value
        elif code == "L36":
            assert value == 0 and args[1:] == (0, os.SEEK_SET)
            key = id(args[0])
            self.scans[key].append({"parts": [], "eofs": 0, "calls": 0, "fd": self.fileno[key]})
        elif code == "L37":
            scan = self.scans[id(args[0])][-1]
            assert type(value) is bytes and len(value) <= args[1]
            scan["parts"].append(value)
            scan["calls"] += 1
            scan["eofs"] += int(value == b"")
        elif code == "L55":
            self.origins.append(value)
        elif code == "P14":
            if type(value) is int and value not in {0, -1, (1 << 64) - 1}:
                self.handles.append((value, args))
        elif code == "P24":
            assert type(value) is int and value >= 0
            generation = len(self.fd_generations)
            self.fd_generations.append({"fd": value, "handle": args[0], "closed_actual": False})
            self.fd_current[value] = generation
        elif code == "P25":
            assert type(value) is io.FileIO and value.closefd is False
            self.raw_fds[id(value)] = (value, self.fd_current[args[0]])
        elif code == "P26" and value is None:
            self.fd_generations[self.fd_current[args[0]]]["closed_actual"] = True
        elif code == "P28":
            key = id(args[0])
            self.publisher_streams[key] = args[0]
            assert type(value) is int and value >= 0
            assert key not in self.publisher_fileno or self.publisher_fileno[key] == value
            self.publisher_fileno[key] = value
        elif code == "P29":
            assert value == 0 and args[1:] == (0, os.SEEK_SET)
            key = id(args[0])
            self.publisher_scans[key].append({"parts": [], "eofs": 0, "fd": self.publisher_fileno[key]})
        elif code == "P30":
            assert type(value) is bytes and len(value) <= args[1]
            scan = self.publisher_scans[id(args[0])][-1]
            scan["parts"].append(value)
            scan["eofs"] += int(value == b"")
        if code in EXIT_TO_ENTER.values():
            owner = value if code == "L15" else args[0]
            self.known[code][id(owner)] = owner

    def raise_same(self, primary):
        try:
            raise primary
        except BaseException:
            self.primary_tb.setdefault(id(primary), BaseException.__dict__["__traceback__"].__get__(primary))
            raise

    def expire(self, prefix, token):
        self.arm[prefix] = token.absolute_monotonic

    def positions(self, code, phase="actual"):
        return [index for index, row in enumerate(self.events) if row[:2] == (phase, code)]

    def actuals(self, code):
        return [row[5] for row in self.events if row[:2] == ("actual", code)]

    def no_repeat_cleanup(self):
        for attempts in self.exit_attempts.values():
            counts = Counter(attempts)
            assert all(type(count) is int and count == 1 for count in counts.values())
        fd_counts = Counter(self.fd_close_attempts)
        assert all(type(count) is int and count == 1 for count in fd_counts.values())
        assert all(count == 1 for count in Counter(self.raw_retire_attempts).values())

    def complete_cleanup(self):
        self.no_repeat_cleanup()
        for exit_code, enter_code in EXIT_TO_ENTER.items():
            expected = Counter(self.known[enter_code].keys())
            actual = Counter(self.exit_attempts[exit_code])
            assert actual == expected
            assert all(type(count) is int and count == 1 for count in actual.values())
        assert all(row["closed_actual"] for row in self.fd_generations)
        assert Counter(self.fd_close_attempts) == Counter(range(len(self.fd_generations)))


def same_exception(call, primary, ledger=None):
    with pytest.raises(type(primary)) as caught:
        call()
    assert caught.value is primary
    if ledger is not None and id(primary) in ledger.primary_tb:
        tb = BaseException.__dict__["__traceback__"].__get__(caught.value)
        original_tb = ledger.primary_tb[id(primary)]
        while tb is not None and tb is not original_tb:
            tb = tb.tb_next
        assert tb is original_tb
    return caught.value


def assert_controls(ledger, files, *, two_anchor=False):
    wanted = {FIRST: 9, SECOND: 9 if two_anchor else 8}
    for key, (stream, name) in ledger.streams.items():
        scans = ledger.scans[key]
        assert len(scans) == wanted[name]
        assert len({scan["fd"] for scan in scans}) == 1
        assert all(b"".join(scan["parts"]) == files[name][0] and scan["eofs"] == 1 for scan in scans)


def assert_reservation_full(item, ledger, result, *, owned_result=False, resume_kind=None, before=None):
    files = authority_files(item)
    assert set(files) == {FIRST, SECOND}
    assert type(result) is reservation.OriginReservationResult
    assert all(type(value) is str for value in (result.init_operation_id, result.reservation_sha256, result.observation_sha256))
    assert result.init_operation_id == INIT_OPERATION
    assert result.reservation_sha256 == sha(files[FIRST][0])
    assert result.observation_sha256 == sha(files[SECOND][0])
    assert len(ledger.origins) == len(ledger.origin_ledgers) == 1
    origin = ledger.origins[0]
    assert_origin_full(item, ledger.origin_ledgers[0], origin)
    physical = {"profile_paths": list(item.profile.identity), "roots": {name: {
        "path": lifecycle._key(path), "identity": ledger.root_facts[path]} for name, path in zip(
        ROOT_NAMES, (item.profile.database.parent, item.profile.media, item.profile.reports, item.profile.checkpoints))}}
    node_values = ledger.actuals("L27")
    assert node_values and all(value == node_values[0] for value in node_values)
    node = {"node_id": PRIMARY, **node_values[0]}
    assert set(node_values[0]) == {"sha256", "byte_size", "directory_identity", "path_identity", "handle_identity"}
    assert node["sha256"] == sha(item.node_raw) and node["byte_size"] == len(item.node_raw)
    first = {"format_version": 1, "family": "personal_authority_origin_reservation", "kind": "origin_reservation",
        "init_operation_id": INIT_OPERATION, "domain_id": DOMAIN, "facility_id": FACILITY,
        "profile": physical, "authority_profile_paths_sha256": sha(canonical(list(item.profile.identity))),
        "physical_profile_sha256": sha(canonical(physical)), "node": node}
    assert files[FIRST][0] == canonical(first)
    second = {"format_version": 1, "family": "personal_authority_origin_reservation", "kind": "origin_observation",
        "init_operation_id": INIT_OPERATION, "reservation_sha256": sha(files[FIRST][0]),
        "origin_intent_sha256": sha(origin.intent_bytes), "origin_intent_base64": base64.b64encode(origin.intent_bytes).decode("ascii"),
        "origin_evidence_sha256": sha(origin.evidence_bytes), "origin_evidence_base64": base64.b64encode(origin.evidence_bytes).decode("ascii")}
    assert files[SECOND][0] == canonical(second)
    assert base64.b64decode(second["origin_intent_base64"], validate=True) == origin.intent_bytes
    assert base64.b64decode(second["origin_evidence_base64"], validate=True) == origin.evidence_bytes
    reservation_contract.cross_validate(files[FIRST][0], files[SECOND][0])
    if before is not None:
        assert all(files[name] == previous for name, previous in before.items())
    assert ledger.tokens and all(token is ledger.tokens[0] for token in ledger.tokens)
    for code in ("L10", "L53", "L54", "P39", "P44", "P47", "O14", "O15"):
        for phase, name, _, args, kwargs, value in ledger.events:
            if phase == "actual" and name == code:
                assert kwargs["deadline_token"] is ledger.tokens[0]
    expected_scans = 3 if resume_kind == "two" else (4 if resume_kind == "first" else 8)
    assert ledger.calls["L15"] == expected_scans
    assert ledger.returns["L19"] == expected_scans
    assert_controls(ledger, files, two_anchor=resume_kind == "two")
    ledger.complete_cleanup()
    assert all(value is None for code in ("L12", "L19", "L23", "L30", "L35", "P38", "P41", "P46", "P49") for value in ledger.actuals(code))
    assert ledger.actuals("L56") == [False]
    origin_at = ledger.positions("L55")[0]
    first_enter = next(index for index, event in enumerate(ledger.events) if event[:2] == ("actual", "L29")
                       and ledger.controls[id(event[3][0])][1] == FIRST)
    assert first_enter < origin_at < ledger.positions("L56")[0]
    if resume_kind != "two":
        assert ledger.positions("L53")[0] < first_enter if resume_kind is None else ledger.calls["L53"] == 1
    else:
        assert ledger.calls["L53"] == 0
    last_names = ledger.positions("L19")[-2:]
    assert len(last_names) == 2 and ledger.positions("L56")[0] < last_names[0] < last_names[1]
    physical_exits = [index for index, event in enumerate(ledger.events) if event[0] == "actual"
                      and (event[1] in {"L23", "L30"} or (event[1] == "L12" and event[2] > (0 if resume_kind else 1)))]
    assert physical_exits and last_names[1] < min(physical_exits)
    if owned_result:
        assert ledger.actuals("O13") == [False]
        assert ledger.positions("L56")[0] < ledger.positions("O13")[0]
        assert lifecycle.current_guard() is None


def assert_publisher_full(item, ledger, result, token):
    files = authority_files(item)
    assert set(files) == {FIRST} and files[FIRST][0] == DIRECT_RAW
    assert type(result) is original_io.PublishedFile
    assert result.raw_sha256 == sha(DIRECT_RAW) and type(result.byte_size) is int and result.byte_size == len(DIRECT_RAW)
    actual_identity = files[FIRST][1]
    assert all(result.identity[key] == actual_identity[key] for key in ("device", "inode", "byte_size", "mtime_ns"))
    assert ledger.tokens and all(value is token for value in ledger.tokens)
    assert ledger.calls["P35"] == ledger.returns["P35"] == 1 and ledger.actuals("P35") == [0]
    creates = [event for event in ledger.events if event[:2] == ("actual", "P14")]
    assert any(event[3][2] == 1 and event[3][4] == 1 for event in creates)
    assert any(event[3][2] == 7 and event[3][4] == 3 for event in creates)
    assert ledger.calls["P31"] >= 2 and all(type(value) is int and value > 0 for value in ledger.actuals("P31"))
    assert ledger.actuals("P33") == [None]
    assert sorted(len(scans) for scans in ledger.publisher_scans.values()) == [1, 1, 2]
    for scans in ledger.publisher_scans.values():
        assert len({scan["fd"] for scan in scans}) == 1
        assert all(b"".join(scan["parts"]) == DIRECT_RAW and scan["eofs"] == 1 for scan in scans)
    for code in ("P16",):
        assert all(type(value) is int and -(1 << 31) <= value < (1 << 31) and value != 0 for value in ledger.actuals(code))
    assert ledger.actuals("P22") == [None, None]
    assert all(value is None for code in ("P26", "P27", "P38", "P41", "P46", "P49") for value in ledger.actuals(code))
    assert ledger.positions("P35")[0] < ledger.positions("P48")[0] < ledger.positions("P49")[0]
    assert ledger.positions("P49")[0] < ledger.positions("P46")[0] < ledger.positions("P37")[0]
    assert ledger.positions("P37")[0] < ledger.positions("P38")[0] < ledger.positions("P41")[0]
    ledger.complete_cleanup()


@pytest.mark.parametrize("case", ("R01", "R02", "R03", "R04"), ids=("R01", "R02", "R03", "R04"))
def test_complete_actual_reservation(qa_case, monkeypatch, case):
    with qa_case("absent" if case in {"R02", "R04"} else "wal") as item:
        before = prepare(item, monkeypatch, complete=case == "R04") if case in {"R03", "R04"} else None
        with monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
            callback_calls = []
            callback = lambda: callback_calls.append("user")
            if case in {"R02", "R04"}:
                assert lifecycle.current_guard() is None
                if case == "R02":
                    result = owned.reserve_origin(item.profile, check_cancelled=callback, **arguments())
                else:
                    result = owned.resume_origin(item.profile, expected_reservation_sha256=sha(before[FIRST][0]),
                        expected_observation_sha256=sha(before[SECOND][0]), check_cancelled=callback, **arguments())
                assert_reservation_full(item, ledger, result, owned_result=True,
                                        resume_kind="two" if case == "R04" else None, before=before)
            else:
                with caller_ex(item):
                    token = requested_token()
                    result = reserve(item, token, callback) if case == "R01" else resume(item, token, sha(before[FIRST][0]), callback=callback)
                    assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard
                    assert_reservation_full(item, ledger, result,
                                            resume_kind="first" if case == "R03" else None, before=before)
            assert callback_calls == ["user"]


@pytest.mark.parametrize("case", ("R05", "R06", "R07", "R08", "R09", "R10", "R11", "R12", "R13"),
                         ids=("R05", "R06", "R07", "R08", "R09", "R10", "R11", "R12", "R13"))
def test_resume_binding_and_existing_names(qa_case, monkeypatch, case):
    with qa_case("wal") as item:
        before = None
        if case not in {"R11"}:
            before = prepare(item, monkeypatch, complete=case in {"R07", "R09"})
        if case == "R09":
            writer = sqlite3.connect(native(item.profile.database))
            try:
                sql_rows(writer, "PRAGMA wal_autocheckpoint=0")
                sql_rows(writer, "UPDATE r834_nonpersonal_facts SET value=? WHERE key='known'", (b"changed-raw-only\x00",))
                writer.commit()
            finally:
                writer.close()
        elif case == "R10":
            create_fixture_file(item, UNKNOWN, b"r834-exact-unknown-trailing-dot-space\x00")
            assert UNKNOWN.encode("utf-8").hex() == "2e52454e4f2d504552534f4e414c2d415554484f524954592d756e6b6e6f776e2e20"
        elif case == "R11":
            create_fixture_file(item, stage_name(FIRST), b"actual-first-stage")
        elif case == "R12":
            create_fixture_file(item, stage_name(SECOND), b"actual-second-stage")
        all_before = authority_files(item)
        with monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger, caller_ex(item):
            token = requested_token()
            suffix = {"R05": "OBSERVATION_MISMATCH", "R06": "RESERVATION_MISMATCH",
                "R07": "OBSERVATION_MISMATCH", "R08": "RESERVATION_MISMATCH", "R09": "OBSERVATION_MISMATCH",
                "R10": "UNKNOWN_NAMES", "R11": "ALREADY_PRESENT", "R12": "UNKNOWN_NAMES", "R13": "ALREADY_PRESENT"}[case]
            if case in {"R11", "R13"}:
                call = lambda: reserve(item, token)
            else:
                first_sha = sha(before[FIRST][0])
                if case == "R06":
                    first_sha = ("0" if first_sha[0] != "0" else "1") + first_sha[1:]
                second_sha = sha(before[SECOND][0]) if case in {"R07", "R09"} else ("a" * 64 if case == "R05" else None)
                if case == "R07":
                    second_sha = ("0" if second_sha[0] != "0" else "1") + second_sha[1:]
                call = lambda: resume(item, token, first_sha, second_sha, domain=uid(919) if case == "R08" else DOMAIN)
            coded(call, reservation.OriginReservationError, "ORIGIN_RESERVATION_" + suffix)
            assert ledger.calls["L53"] == 0
            assert ledger.calls["L54"] == (1 if case == "R09" else 0)
            assert authority_files(item) == all_before
            ledger.no_repeat_cleanup()
            assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard


@pytest.mark.parametrize("case", ("R14", "R15", "R16", "R17", "R18", "R19", "R20", "R21", "R22", "R23", "R24", "R25", "R26"),
                         ids=("R14", "R15", "R16", "R17", "R18", "R19", "R20", "R21", "R22", "R23", "R24", "R25", "R26"))
def test_fixed_locked_protocol_faults(qa_case, monkeypatch, case):
    with qa_case("absent" if case == "R26" else "wal") as item:
        with monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
            sentinel = reservation.OriginReservationError("TEST_FIXED_FIRST")
            hit = []
            if case == "R14":
                def inject_target(ordinal, args, kwargs):
                    if ordinal == 1:
                        assert args[2] == FIRST
                        create_fixture_file(item, FIRST, b"independent-target-already-exists")
                        hit.append("L53-first")
                ledger.before["L53"] = inject_target
            elif case in {"R15", "R16", "R17"}:
                def corrupt_metadata(ordinal, args, kwargs, value):
                    if ordinal != 1:
                        return value
                    assert type(value) is original_io.PublishedFile
                    metadata = dict(value.identity)
                    raw_sha, byte_size = value.raw_sha256, value.byte_size
                    if case == "R15":
                        raw_sha = ("0" if raw_sha[0] != "0" else "1") + raw_sha[1:]
                    elif case == "R16":
                        byte_size = True
                    else:
                        metadata["inode"] = str(int(metadata["inode"]) + 1)
                    hit.append("L53-first")
                    return original_io.PublishedFile(raw_sha, byte_size, metadata)
                ledger.after["L53"] = corrupt_metadata
            elif case == "R18":
                def corrupt_control(ordinal, args, kwargs, value):
                    if ordinal == 1:
                        assert ledger.controls[id(args[0])][1] == FIRST and type(value) is bytes
                        changed = value.replace(DOMAIN.encode(), uid(919).encode(), 1)
                        assert len(changed) == len(value) and sum(a != b for a, b in zip(value, changed)) == 1
                        hit.append("L32-first")
                        return changed
                    return value
                ledger.after["L32"] = corrupt_control
            elif case in {"R19", "R20", "R22", "R24"}:
                target, fixed_ordinal = {"R19": ("L31", 1), "R20": ("L56", 1),
                                         "R22": ("L23", 1), "R24": ("P41", 2)}[case]
                def throw_after_normal(ordinal, args, kwargs, value):
                    if ordinal == fixed_ordinal:
                        assert value is (False if target == "L56" else None)
                        if target == "L31":
                            assert ledger.controls[id(args[0])][1] == FIRST and ledger.calls["L54"] == 0
                            assert id(args[0]) in ledger.known["L29"]
                        hit.append(target)
                        ledger.raise_same(sentinel)
                    return value
                ledger.after[target] = throw_after_normal
            elif case in {"R21", "R23", "R25", "R26"}:
                target, fixed_ordinal = {"R21": ("L56", 1), "R23": ("L12", 2),
                                         "R25": ("L30", 1), "R26": ("O13", 1)}[case]
                def wrong_exit(ordinal, args, kwargs, value):
                    if ordinal == fixed_ordinal:
                        if target in {"L56", "O13"}:
                            assert value is False and args[2] is None
                        else:
                            assert value is None
                        if target == "L12":
                            assert ledger.directory_paths[id(args[0])] == item.profile.checkpoints
                        elif target == "L30":
                            assert ledger.controls[id(args[0])][1] == SECOND
                        hit.append(target)
                        return None if target in {"L56", "O13"} else False
                    return value
                ledger.after[target] = wrong_exit
            if case == "R26":
                call = lambda: owned.reserve_origin(item.profile, **arguments())
                coded(call, owned.OriginReservationOwnerError, "ORIGIN_RESERVATION_OWNER_RESULT_INVALID")
                assert lifecycle.current_guard() is None
                assert ledger.calls["O13"] == 1 and ledger.actuals("O13") == [False]
            else:
                with caller_ex(item):
                    token = requested_token()
                    call = lambda: reserve(item, token)
                    if case in {"R19", "R20", "R22", "R24"}:
                        same_exception(call, sentinel, ledger)
                    else:
                        suffix = "PUBLICATION_MISMATCH" if case in {"R15", "R16", "R17", "R18"} else "STATE_UNAVAILABLE"
                        coded(call, reservation.OriginReservationError, "ORIGIN_RESERVATION_" + suffix)
            assert len(hit) == 1
            ledger.no_repeat_cleanup()
            files = authority_files(item)
            assert FIRST in files
            if case in {"R20", "R21", "R22", "R23", "R24", "R25", "R26"}:
                assert SECOND in files
            if case in {"R14", "R15", "R16", "R17", "R18", "R19"}:
                assert ledger.calls["L54"] == 0 and SECOND not in files


@pytest.mark.parametrize("case", ("R27", "R35"), ids=("R27", "R35"))
def test_public_bootstrap_rejects_before_effects(qa_case, monkeypatch, case):
    with qa_case("absent") as item, monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
        if case == "R27":
            with caller_ex(item):
                coded(lambda: reserve(item, object()), reservation.OriginReservationError, "ORIGIN_RESERVATION_INVALID_ARGUMENT")
            assert ledger.calls["L01"] == ledger.calls["L02"] == ledger.calls["L03"] == ledger.calls["L04"] == 0
        else:
            def nan_clock(ordinal, args, kwargs, value):
                assert ordinal == 1 and type(value) is float
                return float("nan")
            ledger.after["O01"] = nan_clock
            coded(lambda: owned.reserve_origin(item.profile, **arguments()), owned.OriginReservationOwnerError,
                  "ORIGIN_RESERVATION_OWNER_INVALID_ARGUMENT")
            assert ledger.calls["O01"] == 1 and ledger.calls["O02"] == ledger.calls["O03"] == ledger.calls["O04"] == 0
        assert all(ledger.calls[code] == 0 for code in ("L10", "L15", "L21", "L28", "L53", "L54", "P14", "O10", "O12"))
        assert authority_files(item) == {} and lifecycle.current_guard() is None


@pytest.mark.parametrize("case", ("R28",), ids=("R28",))
def test_finite_effective_token_helper(case, monkeypatch):
    with monkeypatch.context() as patch:
        original_factory, original_getter = locked._restricted_budget, locked._effective_token
        factories, getters = [], []
        def factory(requested):
            value = original_factory(requested)
            factories.append((requested, value))
            return value
        def getter(budget):
            value = original_getter(budget)
            getters.append((budget, value))
            return value
        patch.setattr(locked, "_restricted_budget", factory)
        patch.setattr(locked, "_effective_token", getter)
        now = locked._monotonic()
        early, far = deadline.Namespace3Deadline(now + 5.0), deadline.Namespace3Deadline(now + 300.0)
        earlier_budget = locked._make_budget(early)
        far_budget = locked._make_budget(far)
        assert earlier_budget.effective_deadline is early
        effective = far_budget.effective_deadline
        assert type(effective) is deadline.Namespace3Deadline and effective is not far
        assert now < effective.absolute_monotonic < far.absolute_monotonic
        assert [requested for requested, _ in factories] == [early, far]
        assert all(type(budget) is deadline._RestrictedBudget for _, budget in factories)
        assert len(getters) == 2 and all(value is budget.effective_deadline for budget, value in getters)


@pytest.mark.parametrize("case", ("R29", "R30", "R31", "R32", "R33", "R34"), ids=("R29", "R30", "R31", "R32", "R33", "R34"))
def test_fixed_actual_return_expiry(qa_case, monkeypatch, case):
    with qa_case("absent" if case == "R34" else "wal") as item:
        before = prepare(item, monkeypatch, complete=True) if case == "R34" else None
        with monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
            target, fixed_ordinal, prefix = {"R29": ("L53", 1, "L"), "R30": ("L56", 1, "L"),
                "R31": ("L30", 1, "L"), "R32": ("P41", 2, "P"), "R33": ("L12", 2, "L"),
                "R34": ("O13", 1, "O")}[case]
            hit = []
            def arm_after_normal(ordinal, args, kwargs, value):
                if ordinal == fixed_ordinal:
                    if target in {"L56", "O13"}:
                        assert value is False and args[2] is None
                    elif target != "L53":
                        assert value is None
                    if target == "L30":
                        assert ledger.controls[id(args[0])][1] == SECOND
                    if target == "L12":
                        assert ledger.directory_paths[id(args[0])] == item.profile.checkpoints
                    token = ledger.tokens[0]
                    ledger.expire(prefix, token)
                    hit.append(target)
                return value
            ledger.after[target] = arm_after_normal
            if case == "R34":
                call = lambda: owned.resume_origin(item.profile, expected_reservation_sha256=sha(before[FIRST][0]),
                    expected_observation_sha256=sha(before[SECOND][0]), **arguments())
                coded(call, owned.OriginReservationOwnerError, "ORIGIN_RESERVATION_OWNER_DEADLINE")
                assert authority_files(item) == before and lifecycle.current_guard() is None
            else:
                with caller_ex(item):
                    token = requested_token()
                    suffix = "STATE_UNAVAILABLE" if case == "R32" else "DEADLINE"
                    coded(lambda: reserve(item, token), reservation.OriginReservationError, "ORIGIN_RESERVATION_" + suffix)
            assert hit == [target]
            ledger.no_repeat_cleanup()
            files = authority_files(item)
            assert FIRST in files
            if case == "R29":
                assert ledger.calls["L54"] == 0 and SECOND not in files
            else:
                assert SECOND in files


@pytest.mark.parametrize("case", ("R36-SH", "R36-EX"), ids=("R36-SH", "R36-EX"))
def test_actual_ambient_guard_is_not_borrowed(qa_case, monkeypatch, case):
    with qa_case("absent") as item, monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
        factory = lifecycle.shared if case == "R36-SH" else lifecycle.exclusive
        with factory(item.profile) as guard:
            call = (lambda: owned.reserve_origin(item.profile, **arguments())) if case == "R36-SH" else (
                lambda: owned.resume_origin(item.profile, expected_reservation_sha256="a" * 64, **arguments()))
            coded(call, owned.OriginReservationOwnerError, "ORIGIN_RESERVATION_OWNER_GUARD_ALREADY_HELD")
            assert lifecycle.current_guard() is guard
            assert ledger.calls["O10"] == ledger.calls["O12"] == ledger.calls["O13"] == ledger.calls["L53"] == ledger.calls["L54"] == 0
        assert lifecycle.current_guard() is None


@pytest.mark.parametrize("case", ("R37",), ids=("R37",))
def test_first_acquisition_poll_refuses_new_actual_ambient_guard(qa_case, monkeypatch, case):
    with qa_case("absent") as item, monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
        captured_context, captured_guard, callbacks = [], [], []
        def introduce_guard(ordinal, args, kwargs, value):
            if ordinal == 1:
                assert value is None and lifecycle.current_guard() is None
                context = lifecycle.exclusive(item.profile)
                guard = context.__enter__()
                captured_context.append(context)
                captured_guard.append(guard)
            return value
        ledger.after["O11"] = introduce_guard
        try:
            coded(lambda: owned.reserve_origin(item.profile, check_cancelled=lambda: callbacks.append("user"), **arguments()),
                  owned.OriginReservationOwnerError, "ORIGIN_RESERVATION_OWNER_GUARD_ALREADY_HELD")
            assert callbacks == ["user"] and len(captured_guard) == 1
            assert lifecycle.current_guard() is captured_guard[0]
            assert ledger.calls["O12"] == 1 and ledger.returns["O12"] == ledger.calls["O13"] == 0
            assert ledger.calls["L53"] == ledger.calls["L54"] == 0
        finally:
            if captured_context:
                assert len(captured_context) == 1
                assert captured_context[0].__exit__(None, None, None) is False
        assert lifecycle.current_guard() is None


class FalseyCallback:
    def __init__(self, call):
        self.call = call
    def __bool__(self):
        return False
    def __call__(self):
        return self.call()


@pytest.mark.parametrize("case", ("R38",), ids=("R38",))
def test_falsey_callback_first_precedes_all_subject_owners(qa_case, monkeypatch, case):
    with qa_case("wal") as item, monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger, caller_ex(item):
        token = requested_token()
        primary = OSError("r834-fixed-callback-primary")
        calls, clocks_at_throw = [], []
        def cancelled():
            calls.append("user")
            ledger.expire("L", token)
            clocks_at_throw.append(ledger.calls["L01"])
            ledger.raise_same(primary)
        callback = FalseyCallback(cancelled)
        assert not callback
        same_exception(lambda: reserve(item, token, callback), primary, ledger)
        assert calls == ["user"] and ledger.calls["L01"] == clocks_at_throw[0]
        assert all(ledger.calls[code] == 0 for code in ("L10", "L15", "L21", "L28", "L53", "L54", "P14"))
        assert not any(ledger.known.values()) and not any(ledger.exit_attempts.values())
        assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard
        assert authority_files(item) == {}


@pytest.mark.parametrize("case", ("R39",), ids=("R39",))
def test_fixed_control_primary_and_first_control_cleanup_secondary(qa_case, monkeypatch, case):
    with qa_case("wal") as item, monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger, caller_ex(item):
        primary, secondary = KeyboardInterrupt("r834-fixed-origin-control"), OSError("r834-fixed-first-control-secondary")
        hits = []
        def before_origin(ordinal, args, kwargs):
            assert ordinal == 1
            assert len(ledger.known["L29"]) == 1 and ledger.calls["L53"] == 1
            assert ledger.calls["L32"] == 1
            hits.append("L54-primary")
            ledger.raise_same(primary)
        def secondary_exit(ordinal, args, kwargs, value):
            assert ordinal == 1 and ledger.controls[id(args[0])][1] == FIRST
            assert value is None and args[2] is primary
            hits.append("L30-secondary")
            ledger.raise_same(secondary)
        ledger.before["L54"], ledger.after["L30"] = before_origin, secondary_exit
        same_exception(lambda: reserve(item, requested_token()), primary, ledger)
        assert hits == ["L54-primary", "L30-secondary"]
        assert ledger.calls["L30"] == 1 and ledger.actuals("L30") == [None]
        assert ledger.calls["L55"] == 0 and set(authority_files(item)) == {FIRST}
        assert any("CLEANUP" in note for note in getattr(primary, "__notes__", ()))
        ledger.complete_cleanup()


@pytest.mark.parametrize("case", ("R40", "R41", "R42", "R43", "R44", "R45", "R46", "R47", "R48", "R49", "R50", "R51", "R52", "R53", "R54", "R55"),
                         ids=("R40", "R41", "R42", "R43", "R44", "R45", "R46", "R47", "R48", "R49", "R50", "R51", "R52", "R53", "R54", "R55"))
def test_direct_publisher_fixed_native_boundaries(qa_case, monkeypatch, case):
    with qa_case("absent") as item, caller_ex(item):
        token = requested_token()
        with directories.DeadlineDirectory(item.profile.database.parent, deadline_token=token,
                                           base=item.profile.database.parent) as pin:
            with monkeypatch.context() as patch, FunctionalLedger(patch, item) as ledger:
                hits = []
                primary = KeyboardInterrupt("r834-fixed-CRT-output-lost") if case == "R47" else OSError("r834-fixed-fsync-first")
                secondary = OSError("r834-fixed-creator-close-secondary")
                if case == "R41":
                    def preclose_mtime(ordinal, args, kwargs, value):
                        if not ledger.fd_generations or not ledger.fd_generations[0]["closed_actual"]:
                            changed = dict(value)
                            changed["mtime_ns"] = "1"
                            hits.append("P13-preclose-mtime")
                            return changed
                        return value
                    ledger.after["P13"] = preclose_mtime
                elif case == "R42":
                    def incomplete_write(ordinal, args, kwargs, value):
                        if ordinal == 1:
                            assert type(value) is int and 0 < value <= len(args[1])
                            hits.append("P31-first")
                            return 0
                        return value
                    ledger.after["P31"] = incomplete_write
                elif case == "R43":
                    def extra_eof(ordinal, args, kwargs, value):
                        # Fixed creator's first pre-rename EOF, even if earlier
                        # legal FileIO data reads were shorter than requested.
                        if value == b"" and ledger.calls["P35"] == 0 and not hits:
                            assert type(args[0]) is io.FileIO
                            assert ledger.raw_fds[id(args[0])][1] == 0
                            assert b"".join(ledger.publisher_scans[id(args[0])][-1]["parts"]) == DIRECT_RAW
                            hits.append("P30-creator-first-EOF")
                            return b"X"
                        return value
                    ledger.after["P30"] = extra_eof
                elif case == "R44":
                    def status_pending(ordinal, args, kwargs, value):
                        assert ordinal == 1 and type(value) is int and value == 0
                        hits.append("P35-first")
                        return 259
                    ledger.after["P35"] = status_pending
                elif case == "R45":
                    def bad_parent_close(ordinal, args, kwargs, value):
                        if ledger.active("P41"):
                            assert ordinal == 2 and type(value) is int and value != 0
                            hits.append("P16-parent-only")
                            return 0
                        return value
                    ledger.after["P16"] = bad_parent_close
                elif case == "R46":
                    def bad_descriptor_free(ordinal, args, kwargs, value):
                        if ordinal == 2:
                            assert value is None
                            hits.append("P22-descriptor-only")
                            return 1
                        return value
                    ledger.after["P22"] = bad_descriptor_free
                elif case == "R47":
                    def lose_transfer_output(ordinal, args, kwargs, value):
                        assert ordinal == 1 and type(value) is int and value >= 0
                        ledger.capture_fd = value
                        hits.append("P24-lost-output")
                        ledger.raise_same(primary)
                    ledger.after["P24"] = lose_transfer_output
                elif case == "R48":
                    def lose_wrapper_output(ordinal, args, kwargs, value):
                        assert ordinal == 1 and type(value) is io.FileIO and value.closefd is False
                        ledger.capture_wrapper = value
                        hits.append("P25-illegal-output")
                        return object()
                    ledger.after["P25"] = lose_wrapper_output
                elif case == "R49":
                    def fsync_first(ordinal, args, kwargs, value):
                        assert ordinal == 1 and value is None and ledger.calls["P35"] == 0
                        hits.append("P33-primary")
                        ledger.raise_same(primary)
                    def close_second(ordinal, args, kwargs, value):
                        assert ordinal == 1 and value is None and ledger.fd_current[args[0]] == 0
                        assert ledger.fd_generations[0]["closed_actual"] is True
                        assert ledger.calls["P27"] == 1
                        hits.append("P26-secondary")
                        ledger.raise_same(secondary)
                    ledger.after["P33"], ledger.after["P26"] = fsync_first, close_second
                elif case == "R50":
                    def bad_creator_close(ordinal, args, kwargs, value):
                        if ordinal == 2:
                            assert value is None and ledger.fd_current[args[0]] == 0
                            hits.append("P26-creator-only")
                            return False
                        return value
                    ledger.after["P26"] = bad_creator_close
                elif case in {"R51", "R55"}:
                    target = "P49" if case == "R51" else "P38"
                    def expiry_after_reader_exit(ordinal, args, kwargs, value):
                        assert ordinal == 1 and value is None
                        hits.append(target)
                        ledger.expire("P", token)
                        return value
                    ledger.after[target] = expiry_after_reader_exit
                elif case == "R52":
                    def corrupt_postclose_read(ordinal, args, kwargs, value):
                        # Fixed postclose lease's first data return; do not
                        # assume the preceding FileIO reads were always full.
                        if type(args[0]) is io.BufferedReader and value and not hits:
                            assert args[0] is ledger.actuals("P37")[0]
                            assert value == DIRECT_RAW[:len(value)]
                            assert ledger.fd_generations[0]["closed_actual"] is True
                            hits.append("P30-postclose-first-data")
                            return bytes((value[0] ^ 1,)) + value[1:]
                        return value
                    ledger.after["P30"] = corrupt_postclose_read
                elif case == "R53":
                    def expiry_after_stage_handle(ordinal, args, kwargs, value):
                        if args[4] == 1:
                            assert ordinal == 2 and type(value) is int and value > 0
                            hits.append("P14-stage")
                            ledger.expire("P", token)
                        return value
                    ledger.after["P14"] = expiry_after_stage_handle
                elif case == "R54":
                    def expiry_after_CRT(ordinal, args, kwargs, value):
                        assert ordinal == 1 and type(value) is int and value >= 0
                        hits.append("P24-first")
                        ledger.expire("P", token)
                        return value
                    ledger.after["P24"] = expiry_after_CRT
                call = lambda: publication.publish_new_locked(item.profile, pin, FIRST, stage_name(FIRST),
                                                              DIRECT_RAW, deadline_token=token)
                try:
                    if case in {"R40", "R41"}:
                        result = call()
                        assert_publisher_full(item, ledger, result, token)
                        assert not hits if case == "R40" else bool(hits)
                        if case == "R41":
                            assert result.identity["mtime_ns"] != "1"
                    elif case in {"R45", "R46"}:
                        with pytest.raises(OSError):
                            call()
                    elif case in {"R47", "R49"}:
                        same_exception(call, primary, ledger)
                    else:
                        suffix = {"R42": "WRITE_INCOMPLETE", "R43": "READBACK_CHANGED", "R44": "PUBLISH_UNAVAILABLE",
                            "R48": "INVALID_RESULT", "R50": "INVALID_RESULT", "R51": "DEADLINE", "R52": "READBACK_CHANGED",
                            "R53": "DEADLINE", "R54": "DEADLINE", "R55": "DEADLINE"}[case]
                        coded(call, original_io.OriginReservationIOError, "ORIGIN_RESERVATION_IO_" + suffix)
                finally:
                    # These fixture owners came from this fixed actual functional return;
                    # no output/owner inference, no probe and no subject close retry.
                    if ledger.capture_fd is not None:
                        fd, ledger.capture_fd = ledger.capture_fd, None
                        assert case == "R47" and ledger.calls["P26"] == 0
                        assert ledger.calls["P41"] == 1
                        os.close(fd)
                        ledger.recovery_events.append(("fixture-only-FD-close", fd))
                    if ledger.capture_wrapper is not None:
                        wrapper, ledger.capture_wrapper = ledger.capture_wrapper, None
                        assert case == "R48" and ledger.actuals("P26") == [None]
                        assert ledger.calls["P41"] == 1 and ledger.fd_generations[0]["closed_actual"] is True
                        assert wrapper.closefd is False
                        io.FileIO.close(wrapper)
                        ledger.recovery_events.append(("fixture-only-wrapper-retire", id(wrapper)))
                ledger.no_repeat_cleanup()
                assert ledger.calls["L54"] == 0
                if case not in {"R40", "R41"}:
                    assert hits and len(hits) == (2 if case == "R49" else 1)
                files = authority_files(item)
                if case in {"R40", "R41", "R44", "R45", "R50", "R51", "R52", "R55"}:
                    assert set(files) == {FIRST} and files[FIRST][0] == DIRECT_RAW
                else:
                    assert set(files) == {stage_name(FIRST)}
                    residual = files[stage_name(FIRST)][0]
                    if case in {"R43", "R49"}:
                        assert residual == DIRECT_RAW
                    elif case == "R42":
                        actual_write = ledger.actuals("P31")[0]
                        assert type(actual_write) is int and 0 < actual_write <= 8192
                        assert residual == DIRECT_RAW[:actual_write]
                    else:
                        assert residual == b""
                if case == "R47":
                    assert ledger.calls["P24"] == 1 and ledger.calls["P25"] == ledger.calls["P26"] == 0
                    assert ledger.fd_generations[0]["closed_actual"] is False
                    assert len(ledger.recovery_events) == 1
                elif case == "R48":
                    assert ledger.calls["P25"] == ledger.calls["P26"] == 1 and ledger.calls["P27"] == 0
                    assert len(ledger.recovery_events) == 1
                elif case == "R49":
                    assert hits == ["P33-primary", "P26-secondary"]
                    assert ledger.calls["P26"] == ledger.calls["P27"] == 1 and ledger.actuals("P26") == [None]
                    assert ledger.fd_generations[0]["closed_actual"] is True
                    assert any("CLEANUP" in note for note in getattr(primary, "__notes__", ()))
                    assert ledger.positions("P27")[0] < ledger.positions("P26")[0]
                elif case in {"R50", "R51"}:
                    assert ledger.calls["P37"] == 0 and ledger.fd_generations[0]["closed_actual"] is True
                elif case == "R53":
                    assert ledger.calls["P24"] == ledger.calls["P25"] == ledger.calls["P35"] == 0
                    stage_events = [(index, event) for index, event in enumerate(ledger.events)
                                    if event[:2] == ("actual", "P14") and event[3][4] == 1]
                    assert len(stage_events) == 1
                    stage_index, stage_event = stage_events[0]
                    stage_handle = stage_event[5]
                    assert (ledger.calls["P16"] == ledger.returns["P16"]
                            == len(ledger.handle_close_attempts))
                    stage_closes = [event for event in ledger.events[stage_index + 1:]
                                    if event[:2] == ("actual", "P16") and event[3][0] == stage_handle]
                    assert len(stage_closes) == 1
                    returned = stage_closes[0][5]
                    assert type(returned) is int and -(1 << 31) <= returned < (1 << 31) and returned != 0
                elif case == "R54":
                    assert ledger.calls["P24"] == ledger.calls["P26"] == 1 and ledger.calls["P25"] == ledger.calls["P35"] == 0
                    stage_handle = next(handle for handle, args in ledger.handles if args[4] == 1)
                    transfer_position = ledger.positions("P24")[0]
                    assert not any(event[:2] == ("actual", "P16") and event[3][0] == stage_handle
                                   for event in ledger.events[transfer_position+1:])
                elif case == "R55":
                    assert ledger.actuals("P38") == [None] and ledger.calls["P41"] == 1
                assert lifecycle.require_guard(item.profile, exclusive=True) is item.guard
