"""Bounded, read-only reference projections from an already committed image.

The caller owns snapshot capture, profile/root identity proof and the connection.
This module neither opens source databases nor discovers/registers owned files.
``parse_image`` owns the connection's authorizer/progress hooks while running;
it leaves a read-only authorizer installed and clears only its progress handler.
A subsequent image parser may replace those hooks with its own read-only policy.
No transaction is begun, committed or rolled back here.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
import hashlib
import json
import math
import ntpath
import os
from pathlib import PurePosixPath
import re
import sqlite3
import time

from . import storage_artifacts as artifacts, sync_upload_pins as pins
from . import storage_home_recovery_roots as home_recovery_roots
from . import storage_source_continuity_roots as continuity_roots
from . import sync_source_readmit_schema as continuity_schema
from . import sync_source_readmit_schema2 as continuity_schema2
from .sync_protocol import SyncError, _json_bytes, validate_media_descriptor, validate_video_id

NAMESPACES = artifacts.NAMESPACES
REPORT_NAMES = ("incremental_diff", "checklist", "conflicts", "prices", "summary")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_TOKEN = re.compile(r"[A-Za-z0-9_.:-]{1,200}\Z")
_PIN_COLUMNS = ("pin_id kind publication_id parent_pin_id source_library_id source_node_id "
                "source_epoch source_revision target_library_id target_library_epoch recovery_generation "
                "descriptor_json intent_sha256 state artifact_id abandon_operation_id abandon_request_sha256").split()
_COLUMNS = {
    "meta": ("key", "value"),
    "video_asset": ("video_id", "files_json"),
    "frame": ("video_id", "frame_id", "path"),
    "sync_report": ("name", "storage_key", "sha256", "byte_size"),
    "sync_upload_pin": tuple(_PIN_COLUMNS),
    "job": ("job_id", "video_id", "kind", "status", "payload_json"),
    "sync_queue": ("item_id", "video_id", "status"),
    home_recovery_roots.TABLE: home_recovery_roots.COLUMNS,
    **continuity_roots.SQL_COLUMNS,
}
CONTINUITY_COLUMNS = continuity_roots.SQL_COLUMNS
CONTINUITY_RESERVED_NAMES = continuity_schema.RESERVED_NAMES
HOME_RESTORE_PROVENANCE_COLUMNS = ("operation_id", "result_sha256")
MANUAL_RESUME_PROVENANCE_COLUMNS = ("request_id", "job_id", "payload_signature", "created_at")
PUBLIC_JOB_COLUMNS_V17 = ("job_id", "kind", "scope_key", "video_id", "continuation_id", "execution_generation")
PROVENANCE_COLUMNS_V17 = {home_recovery_roots.TABLE: HOME_RESTORE_PROVENANCE_COLUMNS,
                          "task_request": MANUAL_RESUME_PROVENANCE_COLUMNS}
_CONTINUITY_INDEXES = frozenset((*CONTINUITY_RESERVED_NAMES[4:14],
    *("sqlite_autoindex_" + table + "_1" for table in CONTINUITY_COLUMNS)))
_INTEGER_COLUMNS = {"job_id", "item_id", "byte_size", "source_epoch", "source_revision",
                    "target_library_epoch", "recovery_generation", "format_version", "target_epoch",
                    "current_home_epoch", "receiver_library_epoch", "control_revision", "lease_generation", "attempts"}


def continuity_authorize(action, first, second, database, origin):
    """Exact future-table/metadata policy supplement; no private body READ.

    Return None for an unrelated action so each caller retains its existing
    policy. The metadata grant permits the frozen DDL/index/trigger/FK validator
    and reserved-name check only; it never permits values of private JSON cells.
    """
    if action == sqlite3.SQLITE_READ and first in CONTINUITY_COLUMNS:
        return (sqlite3.SQLITE_OK if database == "main" and origin is None
                and second in CONTINUITY_COLUMNS[first] else sqlite3.SQLITE_DENY)
    if action == sqlite3.SQLITE_READ and origin is None:
        if database == "main" and first in {"sqlite_master", "sqlite_schema"} and second in {"name", "type", "sql", "tbl_name"}:
            return sqlite3.SQLITE_OK
        if database == "temp" and first in {"sqlite_temp_master", "sqlite_master"} and second == "name":
            return sqlite3.SQLITE_OK
        # SQLite emits a column-free READ when an INTEGER PRIMARY KEY lookup
        # is optimized to row existence; this grants no private queue column.
        if database == "main" and first == "sync_queue" and second in {"item_id", ""}:
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and database in {None, "main"}:
        if first in {"table_info", "foreign_key_list", "index_list", "table_xinfo"} and second in CONTINUITY_COLUMNS:
            return sqlite3.SQLITE_OK
        if first == "table_info" and second == "sync_queue":
            return sqlite3.SQLITE_OK
        if first == "index_xinfo" and second in _CONTINUITY_INDEXES:
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second == "lower":
        return sqlite3.SQLITE_OK
    return None


def continuity_reserved_projection(connection):
    """Actual main/TEMP reserved names, never any business row or private body."""
    result = []
    for database, master in (("main", "main.sqlite_master"), ("temp", "sqlite_temp_master")):
        query = "SELECT name FROM " + master + " WHERE lower(name) IN (" + ",".join("?" for _ in CONTINUITY_RESERVED_NAMES) + ")"
        result.extend({"database": database, "name": row[0]} for row in connection.execute(query, CONTINUITY_RESERVED_NAMES))
    return sorted(result, key=canonical_bytes)


def provenance_authorize(action, first, second, database, origin, *, enabled):
    """Schema17-only public provenance scalars, with no JSON-column grant."""
    if (enabled and action == sqlite3.SQLITE_PRAGMA and database in {None, "main"}
            and (first == "table_xinfo" and second in PROVENANCE_COLUMNS_V17
                 or first == "foreign_key_list" and second == "task_request")):
        return sqlite3.SQLITE_OK
    if (enabled and action == sqlite3.SQLITE_READ and database == "main" and origin is None
            and first == "job" and second in {*PUBLIC_JOB_COLUMNS_V17, ""}):
        return sqlite3.SQLITE_OK
    if action != sqlite3.SQLITE_READ or first not in PROVENANCE_COLUMNS_V17:
        return None
    if (first == home_recovery_roots.TABLE and second in home_recovery_roots.COLUMNS
            and not (enabled and second in PROVENANCE_COLUMNS_V17[first])):
        return None
    return (sqlite3.SQLITE_OK if enabled and database == "main" and origin is None
            and second in PROVENANCE_COLUMNS_V17[first] else sqlite3.SQLITE_DENY)


def read_provenance_projection17(connection, *, limits=None, check_cancelled=None, consume_row=None):
    """Actual public producer facts; does not establish manual/fresh authority."""
    limits = InputLimits() if limits is None else limits
    if (not isinstance(connection, sqlite3.Connection) or not isinstance(limits, InputLimits)
            or check_cancelled is not None and not callable(check_cancelled)
            or consume_row is not None and not callable(consume_row)):
        raise ValueError("Invalid provenance projection options")
    reader = _Reader(connection, limits)
    def check():
        if check_cancelled is not None:
            check_cancelled()
        reader.check()
    check()
    result = {}
    for table, columns in PROVENANCE_COLUMNS_V17.items():
        found = connection.execute("SELECT type,sql FROM main.sqlite_master WHERE name=? COLLATE NOCASE LIMIT 2", (table,)).fetchall()
        if len(found) != 1 or found[0][0] != "table" or type(found[0][1]) is not str:
            _fail("INPUT_PROVENANCE_SCHEMA_INVALID")
        actual = {row[1]: tuple(row) for row in connection.execute('PRAGMA main.table_xinfo("' + table + '")')}
        if any(column not in actual or actual[column][6]
                or actual[column][2].upper() != ("INTEGER" if column == "job_id" else "TEXT") for column in columns):
            _fail("INPUT_PROVENANCE_SCHEMA_INVALID")
        if table == home_recovery_roots.TABLE:
            if actual["operation_id"][5] != 1:
                _fail("INPUT_PROVENANCE_SCHEMA_INVALID")
        elif (set(actual) != set(MANUAL_RESUME_PROVENANCE_COLUMNS) or actual["request_id"][5] != 1
                or any(actual[column][3] != 1 for column in ("job_id", "payload_signature", "created_at"))
                or [tuple(row)[2:8] for row in connection.execute('PRAGMA main.foreign_key_list("task_request")')]
                    != [("job", "job_id", "job_id", "NO ACTION", "NO ACTION", "NONE")]):
            _fail("INPUT_PROVENANCE_SCHEMA_INVALID")
        rows, seen = [], set()
        query = 'SELECT ' + ','.join('"' + column + '"' for column in columns) + ' FROM main."' + table + '" LIMIT ?'
        for raw in connection.execute(query, (limits.max_rows + 1,)):
            check()
            reader.consume(raw)
            if consume_row is not None:
                consume_row(raw)
            row = dict(zip(columns, raw))
            key = row[columns[0]]
            pins._uuid(key)
            if key in seen:
                _fail("INPUT_PROVENANCE_INVALID")
            seen.add(key)
            signature = row["result_sha256"] if table == home_recovery_roots.TABLE else row["payload_signature"]
            if type(signature) is not str or not _SHA.fullmatch(signature):
                _fail("INPUT_PROVENANCE_INVALID")
            if table == "task_request":
                if type(row["job_id"]) is not int or not 1 <= row["job_id"] <= 2**63 - 1:
                    _fail("INPUT_PROVENANCE_INVALID")
                if type(row["created_at"]) is not str or not 10 <= len(row["created_at"]) <= 64:
                    _fail("INPUT_PROVENANCE_INVALID")
                try:
                    stamp = datetime.fromisoformat(row["created_at"].replace("Z", "+00:00"))
                    if stamp.tzinfo is None:
                        _fail("INPUT_PROVENANCE_INVALID")
                except ValueError:
                    _fail("INPUT_PROVENANCE_INVALID")
            rows.append(row)
        result[table] = sorted(rows, key=canonical_bytes)
    for job_id in sorted({row["job_id"] for row in result["task_request"]}):
        check()
        found = connection.execute("SELECT job_id FROM main.job WHERE job_id=? LIMIT 2", (job_id,)).fetchall()
        for row in found:
            reader.consume(row)
            if consume_row is not None:
                consume_row(row)
        if len(found) != 1 or type(found[0][0]) is not int or found[0][0] != job_id:
            _fail("INPUT_PROVENANCE_FK_INVALID")
    check()
    return result


def read_continuity_projection(connection, *, limits=None, check_cancelled=None, consume_row=None):
    """Read four exact public projections under the caller's existing policy.

    The caller owns a committed/private connection, authorizer and progress
    handler. No policy is removed or private body validated. Optional row
    consumption also charges the caller's aggregate budget.
    """
    if (not isinstance(connection, sqlite3.Connection)
            or check_cancelled is not None and not callable(check_cancelled)
            or consume_row is not None and not callable(consume_row)):
        raise ValueError("Invalid continuity projection options")
    limits = InputLimits() if limits is None else limits
    if not isinstance(limits, InputLimits):
        raise ValueError("Invalid continuity projection limits")
    reader = _Reader(connection, limits)
    def check():
        if check_cancelled is not None:
            check_cancelled()
        reader.check()
    def consume(row):
        check()
        reader.consume(row)
        if consume_row is not None:
            consume_row(row)
    check()
    try:
        continuity_schema2.validate_historical(connection)
    except sqlite3.Error:
        _fail("INPUT_SOURCE_CONTINUITY_SCHEMA_INVALID")
    check()
    rows = {}
    for table, columns in CONTINUITY_COLUMNS.items():
        values = []
        query = 'SELECT ' + ','.join('"' + column + '"' for column in columns) + ' FROM main."' + table + '" LIMIT ?'
        for raw in connection.execute(query, (limits.max_rows + 1,)):
            consume(raw)
            values.append(dict(zip(columns, raw)))
        rows[table] = values
    try:
        normalized = continuity_roots.normalize_sql(rows)
    except continuity_roots.ContinuityRootError:
        _fail("INPUT_SOURCE_CONTINUITY_INVALID")
    item_ids = {row["item_id"] for table in (continuity_schema.REGISTRATION, continuity_schema.DISPOSITION,
                                           continuity_schema.ADMISSION) for row in normalized[table]}
    for item_id in sorted(item_ids):
        found = connection.execute('SELECT item_id FROM main.sync_queue WHERE item_id=? LIMIT 2', (item_id,)).fetchall()
        for row in found:
            consume(row)
        if len(found) != 1 or type(found[0][0]) is not int or found[0][0] != item_id:
            _fail("INPUT_SOURCE_CONTINUITY_FK_INVALID")
    check()
    return normalized


@dataclass(frozen=True)
class InputLimits:
    max_vm_steps: int = 1_000_000
    deadline_seconds: float = 5.0
    max_rows: int = 50_000
    max_cell_bytes: int = 256 * 1024
    max_total_bytes: int = 16 * 1024 * 1024
    max_objects: int = 100_000
    max_json_nodes: int = 16_384
    max_json_depth: int = 16

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                    raise ValueError("Invalid input limits")
            elif type(value) is not int or value <= 0:
                raise ValueError("Invalid input limits")


@dataclass(frozen=True, order=True)
class Reference:
    """A logical hold, never ownership or deletion eligibility.

    Downstream candidate indexes must compare physical storage keys using the
    root platform's equivalence rules (including Windows case equivalence),
    rather than case-sensitive string equality. Preserve this logical spelling
    for provenance; legal video identifiers are themselves case-sensitive.
    """
    namespace: str
    root_id: str
    storage_key: str
    member_id: str
    source_kind: str
    source_id: str
    locator: str
    sha256: str | None = None
    byte_size: int | None = None


@dataclass(frozen=True, order=True)
class Selector:
    namespace: str
    root_id: str
    video_id: str
    member_id: str
    source_kind: str
    source_id: str
    locator: str
    sha256: str | None = None
    extension: str | None = None


@dataclass(frozen=True, order=True)
class Blocker:
    namespace: str
    root_id: str
    member_id: str
    source_kind: str
    source_id: str
    code: str
    locator: str


@dataclass(frozen=True, order=True)
class SourceBinding:
    member_id: str
    source_kind: str
    source_id: str
    section: str
    state: str
    sha256: str
    row_count: int


@dataclass(frozen=True)
class InputScan:
    references: tuple[Reference, ...] = ()
    selectors: tuple[Selector, ...] = ()
    blockers: tuple[Blocker, ...] = ()
    source_bindings: tuple[SourceBinding, ...] = ()

    def canonical_bytes(self):
        return canonical_bytes({"format_version": 1, **asdict(self)})

    @property
    def digest(self):
        return hashlib.sha256(self.canonical_bytes()).hexdigest()

    def public_summary(self):
        # Never expose paths, payloads, URLs or free-form source locators.
        return {"format_version": 1, "digest": self.digest,
                "reference_count": len(self.references), "selector_count": len(self.selectors),
                "source_count": len(self.source_bindings),
                "blockers": sorted({(b.namespace, b.root_id, b.code) for b in self.blockers})}


def canonical_bytes(value):
    """Canonical JSON of controlled models; reject arbitrary Python objects."""
    def check(item, depth=0):
        if depth > 32:
            raise ValueError("Canonical value too deep")
        if item is None or type(item) in (str, bool, int):
            return
        if type(item) is float and math.isfinite(item):
            return
        if type(item) in (list, tuple):
            for child in item:
                check(child, depth + 1)
            return
        if type(item) is dict and all(type(k) is str for k in item):
            for child in item.values():
                check(child, depth + 1)
            return
        raise ValueError("Unsupported canonical value")
    check(value)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")


class _Invalid(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _fail(code):
    raise _Invalid(code)


def _digest(value):
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


def _json(raw, limits):
    if type(raw) not in (str, bytes) or len(raw if type(raw) is bytes else raw.encode("utf-8")) > limits.max_cell_bytes:
        _fail("INPUT_CELL_LIMIT")
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                _fail("INPUT_JSON_INVALID")
            result[key] = value
        return result
    try:
        value = json.loads(raw, object_pairs_hook=unique,
                           parse_constant=lambda _: _fail("INPUT_JSON_INVALID"))
        nodes = 0
        def visit(item, depth=0):
            nonlocal nodes
            nodes += 1
            if nodes > limits.max_json_nodes or depth > limits.max_json_depth:
                _fail("INPUT_JSON_LIMIT")
            if type(item) is dict:
                for child in item.values():
                    visit(child, depth + 1)
            elif type(item) is list:
                for child in item:
                    visit(child, depth + 1)
        visit(value)
        return value
    except (ValueError, UnicodeError, RecursionError) as exc:
        if isinstance(exc, _Invalid):
            raise
        _fail("INPUT_JSON_INVALID")


class _Collector:
    def __init__(self, profile, root_ids, member_id, source_kind, source_id, limits):
        if (set(root_ids) != set(NAMESPACES) or any(type(v) is not str or not _SHA.fullmatch(v) for v in root_ids.values())
                or type(member_id) is not str or not _TOKEN.fullmatch(member_id)
                or source_kind not in {"live", "backup", "checkpoint", "manifest", "home"}
                or type(source_id) is not str or len(source_id.encode("utf-8")) > 4096):
            raise ValueError("Invalid reference source binding")
        self.profile, self.root_ids, self.limits = profile, dict(root_ids), limits
        self.started = time.perf_counter()
        self.origin = dict(member_id=member_id, source_kind=source_kind, source_id=_digest(source_id))
        self.refs, self.selectors, self.blockers, self.bindings = [], [], [], []
        self._blocked = set()

    def check_size(self):
        if time.perf_counter() - self.started > self.limits.deadline_seconds:
            _fail("INPUT_DEADLINE_LIMIT")
        if len(self.refs) + len(self.selectors) + len(self.bindings) >= self.limits.max_objects:
            _fail("INPUT_OBJECT_LIMIT")

    def block(self, namespaces, code, locator):
        if len(self.blockers) >= self.limits.max_objects:
            code, locator = "INPUT_OBJECT_LIMIT", "remaining_sources"
        for ns in namespaces:
            blocker = Blocker(namespace=ns, root_id=self.root_ids[ns], **self.origin, code=code, locator=_digest(locator))
            if blocker not in self._blocked:
                self._blocked.add(blocker)
                self.blockers.append(blocker)

    def reference(self, namespace, key, locator, sha256=None, byte_size=None):
        artifacts.validate_storage_key(namespace, key)
        if sha256 is not None and (type(sha256) is not str or not _SHA.fullmatch(sha256)):
            _fail("INPUT_DESCRIPTOR_INVALID")
        if byte_size is not None and (type(byte_size) is not int or byte_size < 0):
            _fail("INPUT_DESCRIPTOR_INVALID")
        self.check_size()
        self.refs.append(Reference(namespace, self.root_ids[namespace], key, **self.origin,
                                   locator=_digest(locator), sha256=sha256, byte_size=byte_size))

    def selector(self, video_id, locator, sha256=None, extension=None):
        validate_video_id(video_id)
        self.check_size()
        self.selectors.append(Selector("sync_media", self.root_ids["sync_media"], video_id,
                                      **self.origin, locator=_digest(locator), sha256=sha256, extension=extension))

    def binding(self, section, state, rows):
        self.check_size()
        self.bindings.append(SourceBinding(**self.origin, section=section, state=state,
                                           sha256=_digest(rows), row_count=len(rows)))

    def result(self):
        ordered = lambda values: tuple(sorted(set(values), key=lambda v: canonical_bytes(asdict(v))))
        return InputScan(ordered(self.refs), ordered(self.selectors), ordered(self.blockers), ordered(self.bindings))


def media_storage_key(value, *, profile, source_video_id=None):
    """Pure lexical mapping using MEDIA.parent; never resolves/follows links.

    Only a proven path inside this exact media root can be discarded as local
    non-candidate media. Cross-drive/foreign-root legacy paths are blockers.
    Candidate filesystem identity is a separate responsibility of the scanner.
    Legal video-ID case is retained. The later ownership candidate index must
    match Windows physical key aliases; this parser never chooses/deletes files.
    """
    if type(value) is not str or not value or len(value.encode("utf-8")) > 4096 or "\0" in value:
        _fail("INPUT_MEDIA_PATH_INVALID")
    normalized = value.replace("\\", "/")
    if any(p in (".", "..", "") for p in normalized.strip("/").split("/")):
        _fail("INPUT_MEDIA_PATH_INVALID")
    media = os.path.abspath(os.fspath(profile.media)).replace("\\", "/")
    windows = bool(ntpath.splitdrive(media)[0])
    foreign_drive = ntpath.splitdrive(normalized)[0]
    if foreign_drive and not windows:
        _fail("INPUT_MEDIA_PATH_OUTSIDE_PROFILE")
    if ":" in normalized and not re.match(r"^[A-Za-z]:/", normalized):
        _fail("INPUT_MEDIA_PATH_INVALID")
    absolute = normalized.startswith("/") or bool(foreign_drive)
    target = normalized if absolute else str(PurePosixPath(media).parent / normalized)
    root_cmp, target_cmp = (media.casefold(), target.casefold()) if windows else (media, target)
    if not target_cmp.startswith(root_cmp.rstrip("/") + "/"):
        _fail("INPUT_MEDIA_PATH_OUTSIDE_PROFILE")
    key = target[len(media.rstrip("/")) + 1:]
    prefix = key.split("/", 1)[0]
    if windows and prefix.rstrip(" .").casefold() == "sync" and prefix != "sync":
        # NTFS path equality must not turn a noncanonical spelling of a managed
        # path into an ignored local file. Win32 also strips trailing dots and
        # spaces in ordinary path components. Do not guess the owner's casing.
        _fail("INPUT_MEDIA_PATH_NONCANONICAL")
    if prefix == "sync":
        if windows and source_video_id is not None:
            validate_video_id(source_video_id)
            parts = key.split("/")
            if len(parts) > 1 and parts[1].casefold() == source_video_id.casefold() and parts[1] != source_video_id:
                _fail("INPUT_MEDIA_PATH_NONCANONICAL")
        return artifacts.validate_storage_key("sync_media", key)
    # Other paths physically belong to MEDIA, but are never GC candidates.
    return None


class _Reader:
    def __init__(self, con, limits):
        self.con, self.limits = con, limits
        self.started, self.steps, self.rows, self.size = time.perf_counter(), 0, 0, 0
        self.active = None
        self.provenance17 = False
        self.exhausted = None

    def check(self):
        if self.exhausted:
            _fail(self.exhausted)
        if time.perf_counter() - self.started > self.limits.deadline_seconds:
            self.exhausted = "INPUT_DEADLINE_LIMIT"
            _fail(self.exhausted)

    def progress(self):
        self.steps += 1
        if self.steps > self.limits.max_vm_steps:
            self.exhausted = "INPUT_VM_LIMIT"
        try:
            self.check()
            return 0
        except _Invalid:
            return 1

    def authorize(self, action, first, second, schema, source):
        provenance = provenance_authorize(action, first, second, schema, source, enabled=self.provenance17)
        if provenance is not None:
            return provenance
        continuity = continuity_authorize(action, first, second, schema, source)
        if continuity is not None:
            return continuity
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == "table_xinfo" and second == self.active:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main" and source is None:
            if first == "sqlite_master" and second in ("name", "type", "sql"):
                return sqlite3.SQLITE_OK
            if first == self.active and second in _COLUMNS.get(first, ()):
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def consume(self, row, *, count=True):
        self.check()
        if count:
            self.rows += 1
            if self.rows > self.limits.max_rows:
                self.exhausted = "INPUT_ROW_LIMIT"
                _fail(self.exhausted)
        for value in row:
            if value is None:
                length = 0
            elif type(value) is str:
                length = len(value.encode("utf-8"))
            elif type(value) in (int, float):
                length = 8
            else:
                _fail("INPUT_SQL_CELL_INVALID")
            if length > self.limits.max_cell_bytes:
                _fail("INPUT_CELL_LIMIT")
            self.size += length
            if self.size > self.limits.max_total_bytes:
                self.exhausted = "INPUT_TOTAL_LIMIT"
                _fail(self.exhausted)

    def table(self, name, *, optional=False):
        self.check()
        self.active = name
        try:
            found = self.con.execute("SELECT type,sql FROM sqlite_master WHERE name=? LIMIT 2", (name,)).fetchall()
            for row in found:
                self.consume(row, count=False)
            if not found and optional:
                return []
            if len(found) != 1 or found[0][0] != "table" or type(found[0][1]) is not str:
                _fail("INPUT_TABLE_UNSAFE")
            sql = " ".join(found[0][1].upper().split())
            if not sql.startswith("CREATE TABLE ") or "CREATE VIRTUAL TABLE" in sql:
                _fail("INPUT_TABLE_UNSAFE")
            columns = {}
            for row in self.con.execute(f'PRAGMA table_xinfo("{name}")'):
                self.consume(row, count=False)
                columns[row[1]] = row
            for column in _COLUMNS[name]:
                expected = "INTEGER" if column in _INTEGER_COLUMNS else "TEXT"
                if column not in columns or columns[column][2].upper() != expected or columns[column][6] != 0:
                    _fail("INPUT_COLUMNS_UNSAFE")
            fields = ",".join('"' + c + '"' for c in _COLUMNS[name])
            where = " WHERE key IN ('migration_version','library_id')" if name == "meta" else ""
            result = []
            for row in self.con.execute(f'SELECT {fields} FROM "{name}"{where}'):
                self.consume(row)
                result.append(dict(zip(_COLUMNS[name], row)))
            return sorted(result, key=canonical_bytes)
        except sqlite3.Error:
            _fail(self.exhausted or "INPUT_SQL_UNSAFE")


def _pin_binding(row):
    return pins.normalize_binding({"publication_id": row["publication_id"], "source_node_id": row["source_node_id"],
        "source_version": {"library_id": row["source_library_id"], "library_epoch": row["source_epoch"],
                           "kb_revision": row["source_revision"]},
        "recovery_stream": {"library_id": row["target_library_id"], "library_epoch": row["target_library_epoch"]},
        "recovery_generation": row["recovery_generation"]})


def _parse_pins(rows, collector, library_id):
    by_id = {}
    for row in rows:
        key = row["pin_id"]
        if type(key) is not str or key in by_id:
            _fail("INPUT_PIN_INVALID")
        by_id[key] = row
    publications = set()
    descriptor_videos = set()
    for row in rows:
        kind, state, descriptor = row["kind"], row["state"], None
        if kind == "legacy":
            descriptor = validate_media_descriptor(_json(row["descriptor_json"], collector.limits))
            value = {"source_library_id": pins._uuid(row["source_library_id"]),
                     "source_node_id": pins._uuid(row["source_node_id"]), "descriptor": descriptor}
            digest = hashlib.sha256(_json_bytes(value)).hexdigest()
            empty = ("publication_id", "parent_pin_id", "source_epoch", "source_revision", "target_library_id",
                     "target_library_epoch", "recovery_generation", "abandon_operation_id", "abandon_request_sha256")
            if state != "legacy_hold" or row["pin_id"] != "legacy_" + digest or any(row[k] is not None for k in empty):
                _fail("INPUT_PIN_INVALID")
        elif kind in ("publication", "media"):
            binding = _pin_binding(row)
            if binding["recovery_stream"]["library_id"] != library_id:
                _fail("INPUT_PIN_BINDING_INVALID")
            if kind == "publication":
                digest = hashlib.sha256(_json_bytes(binding)).hexdigest()
                if (row["publication_id"] in publications or row["pin_id"] != "pubpin_" + digest
                        or row["parent_pin_id"] is not None or row["descriptor_json"] is not None
                        or row["artifact_id"] is not None or state not in ("reserved", "published", "abandoned")):
                    _fail("INPUT_PIN_INVALID")
                publications.add(row["publication_id"])
            else:
                descriptor = validate_media_descriptor(_json(row["descriptor_json"], collector.limits))
                digest = hashlib.sha256(_json_bytes({**binding, "descriptor": descriptor})).hexdigest()
                parent = by_id.get(row["parent_pin_id"])
                video_key = (row["parent_pin_id"], descriptor["video_id"])
                if (parent is None or parent["kind"] != "publication" or _pin_binding(parent) != binding
                        or video_key in descriptor_videos or row["pin_id"] != "pin_" + digest
                        or state not in ("reserved", "stored", "published", "abandoned")
                        or (state in ("reserved", "stored") and parent["state"] != "reserved")
                        or (state in ("published", "abandoned") and parent["state"] != state)
                        or (state in ("stored", "published") and type(row["artifact_id"]) is not str)
                        or row["abandon_operation_id"] is not None or row["abandon_request_sha256"] is not None):
                    _fail("INPUT_PIN_BINDING_INVALID")
                descriptor_videos.add(video_key)
        else:
            _fail("INPUT_PIN_INVALID")
        if row["intent_sha256"] != digest:
            _fail("INPUT_PIN_INVALID")
        if kind == "publication":
            if state == "abandoned":
                operation = pins._uuid(row["abandon_operation_id"])
                expected = hashlib.sha256(_json_bytes({"binding": binding, "operation_id": operation})).hexdigest()
                if row["abandon_request_sha256"] != expected:
                    _fail("INPUT_PIN_INVALID")
            elif row["abandon_operation_id"] is not None or row["abandon_request_sha256"] is not None:
                _fail("INPUT_PIN_INVALID")
        if descriptor is not None and state in ("reserved", "stored", "legacy_hold"):
            key = f'sync/{descriptor["video_id"]}/{descriptor["sha256"]}.{descriptor["extension"]}'
            collector.reference("sync_media", key, ["pin", row["pin_id"]], descriptor["sha256"], descriptor["byte_size"])


def parse_image(connection, *, profile, root_ids, member_id, source_kind="live", source_id="current", limits=None):
    """Extract closed projections; malformed sources yield scoped blockers.

    ``connection`` must be the caller's stable committed image, never a live
    connection containing uncommitted work. This pure parser cannot prove that
    external capture contract. It installs no writable SQL capability.
    Only live/whole-database backup schema 7-17 is supported here. Knowledge
    archives have a different meta/schema contract and require their own parser;
    checkpoint/recovery/home/backup dependency closure is not this projection.
    """
    limits = limits or InputLimits()
    collector = _Collector(profile, root_ids, member_id, source_kind, source_id, limits)
    if source_kind not in ("live", "backup"):
        collector.block(NAMESPACES, "INPUT_IMAGE_KIND_UNSUPPORTED", "image")
        return collector.result()
    reader = _Reader(connection, limits)
    # Enforce cell allocation bounds inside SQLite before Python receives rows.
    previous_length = connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_cell_bytes)
    connection.set_authorizer(reader.authorize)
    connection.set_progress_handler(reader.progress, 1)
    try:
        try:
            meta_rows = reader.table("meta")
            meta = {r["key"]: r["value"] for r in meta_rows}
            # Versions 7-9 share these reference projections. Version 8 adds
            # recovery metadata; version 9 adds relation derivation only. Their
            # checkpoint/recovery graph belongs to the separate graph parser.
            if len(meta) != len(meta_rows) or meta.get("migration_version") not in ("7", "8", "9", "10", "11", "12", "13", "14", "15", "16", "17"):
                _fail("INPUT_SCHEMA_UNKNOWN")
            library_id = pins._uuid(meta.get("library_id"))
            reader.provenance17 = meta["migration_version"] == "17"
            collector.binding("meta", "present", meta_rows)
        except (_Invalid, SyncError, ValueError, TypeError):
            collector.block(NAMESPACES, reader.exhausted or "INPUT_SCHEMA_UNKNOWN", "meta")
            return collector.result()
        try:
            reserved = continuity_reserved_projection(connection)
            if int(meta["migration_version"]) < 17:
                if reserved:
                    collector.binding("source_continuity", "unknown", reserved)
            else:
                projection = read_continuity_projection(connection, limits=limits,
                    check_cancelled=reader.check, consume_row=reader.consume)
                public_rows = [{"table": table, **row} for table in sorted(projection) for row in projection[table]]
                collector.binding("source_continuity", "present", public_rows)
        except (_Invalid, SyncError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
            code = getattr(exc, "code", "INPUT_SOURCE_CONTINUITY_UNKNOWN")
            if reader.exhausted or code.endswith("_LIMIT"):
                collector.block(NAMESPACES, reader.exhausted or code, "source_continuity")
                return collector.result()
            try:
                collector.binding("source_continuity", "unknown", [{"code": code}])
            except _Invalid as limit:
                collector.block(NAMESPACES, limit.code, "source_continuity")
                return collector.result()
        if reader.provenance17:
            try:
                provenance = read_provenance_projection17(connection, limits=limits,
                    check_cancelled=reader.check, consume_row=reader.consume)
                collector.binding("source_provenance", "present", [{"table": table, **row}
                    for table in sorted(provenance) for row in provenance[table]])
            except (_Invalid, SyncError, ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                code = getattr(exc, "code", "INPUT_PROVENANCE_UNKNOWN")
                if reader.exhausted or code.endswith("_LIMIT"):
                    collector.block(NAMESPACES, reader.exhausted or code, "source_provenance")
                    return collector.result()
                try:
                    collector.binding("source_provenance", "unknown", [{"code": code}])
                except _Invalid as limit:
                    collector.block(NAMESPACES, limit.code, "source_provenance")
                    return collector.result()
        scopes = {"video_asset": ("sync_media",), "frame": ("sync_media",),
                  "sync_report": ("sync_report",), "sync_upload_pin": ("sync_media",),
                  "job": NAMESPACES, "sync_queue": ("sync_media",)}
        if int(meta["migration_version"]) >= 16:
            try:
                projection = home_recovery_roots.normalize_projection(reader.table(home_recovery_roots.TABLE),
                    schema_version=int(meta["migration_version"]))
                collector.binding(home_recovery_roots.TABLE, "present", projection)
            except (_Invalid, ValueError, TypeError, KeyError):
                collector.block(NAMESPACES, "HOME_RECOVERY_ROOT_INVALID", home_recovery_roots.TABLE)
        else:
            reader.active = home_recovery_roots.TABLE
            if connection.execute("SELECT 1 FROM main.sqlite_master WHERE name=? COLLATE NOCASE", (home_recovery_roots.TABLE,)).fetchone():
                collector.block(NAMESPACES, "HOME_RECOVERY_SCHEMA_UNKNOWN", home_recovery_roots.TABLE)
        for table, namespaces in scopes.items():
            try:
                rows = reader.table(table, optional=table == "sync_upload_pin" and int(meta["migration_version"]) < 10)
                collector.binding(table, "present", rows)
            except (_Invalid, SyncError, ValueError, TypeError) as exc:
                collector.block(namespaces, getattr(exc, "code", "INPUT_SOURCE_INVALID"), table)
                continue
            if table == "sync_upload_pin":
                try:
                    _parse_pins(rows, collector, library_id)
                except (_Invalid, SyncError, ValueError, TypeError, KeyError) as exc:
                    collector.block(namespaces, getattr(exc, "code", "INPUT_PIN_INVALID"), table)
                continue
            for index, row in enumerate(rows):
                locator = [table, index]
                try:
                    if table == "video_asset":
                        validate_video_id(row["video_id"])
                        files = _json(row["files_json"], limits)
                        if type(files) is not dict:
                            _fail("INPUT_FILES_INVALID")
                        for field, value in files.items():
                            if value is None:
                                continue
                            key = media_storage_key(value, profile=profile, source_video_id=row["video_id"])
                            if key:
                                collector.reference("sync_media", key, [*locator, field])
                    elif table == "frame":
                        key = media_storage_key(row["path"], profile=profile, source_video_id=row["video_id"])
                        if key:
                            collector.reference("sync_media", key, locator)
                    elif table == "sync_report":
                        if (row["name"] not in REPORT_NAMES or row["storage_key"] != f'.sync-versions/{row["name"]}-{row["sha256"]}.md'
                                or type(row["byte_size"]) is not int or not 0 <= row["byte_size"] <= 4 * 1024 * 1024):
                            _fail("INPUT_REPORT_INVALID")
                        collector.reference("sync_report", row["storage_key"], locator, row["sha256"], row["byte_size"])
                    elif table == "job":
                        if row["status"] not in ("pending", "running", "done", "failed", "cancelled"):
                            _fail("INPUT_JOB_INVALID")
                        if row["status"] not in ("pending", "running"):
                            continue
                        from .tasks import normalize
                        payload = _json(row["payload_json"], limits)
                        normalize(row["kind"], row["video_id"], payload,
                                  internal=row["kind"] in ("sync", "ingest", "favlist", "personal_mirror"))
                        if row["kind"] == "process" or (row["kind"] == "ingest" and row["video_id"] is not None):
                            collector.selector(row["video_id"], locator)
                    elif table == "sync_queue":
                        if row["status"] not in ("queued", "claimed", "retry_wait", "budget_wait", "dead", "done"):
                            _fail("INPUT_QUEUE_INVALID")
                        if row["status"] in ("queued", "claimed", "retry_wait", "budget_wait") and row["video_id"] is not None:
                            collector.selector(row["video_id"], locator)
                except (_Invalid, SyncError, ValueError, TypeError, KeyError) as exc:
                    collector.block(namespaces, getattr(exc, "code", "INPUT_SOURCE_INVALID"), locator)
        return collector.result()
    finally:
        connection.set_progress_handler(None, 0)
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous_length)


def parse_manifest(raw, *, name, profile, root_ids, member_id, source_kind="manifest", source_id="current",
                   unreadable=False, limits=None):
    """Parse captured manifest bytes/absence only; perform no filesystem I/O."""
    limits = limits or InputLimits()
    collector = _Collector(profile, root_ids, member_id, source_kind, source_id, limits)
    if name not in REPORT_NAMES:
        raise ValueError("Unknown report manifest")
    try:
        if unreadable:
            _fail("INPUT_MANIFEST_UNREADABLE")
        if raw is None:
            collector.binding(name, "absent", [])
            return collector.result()
        if type(raw) is not bytes or len(raw) > limits.max_total_bytes:
            _fail("INPUT_MANIFEST_LIMIT")
        collector.binding(name, "present", [hashlib.sha256(raw).hexdigest()])
        value = _json(raw, limits)
        allowed = {"report_type", "generated_at", "library_id", "library_epoch", "kb_revision", "review_events_sha256",
                   "derivation_summary", "policy_version", "status", "content_file", "content_sha256", "byte_size",
                   "error_class", "last_attempt_at", "synced", "source_version", "publication_id", "source_reviews_match"}
        if type(value) is not dict or set(value) - allowed or value.get("report_type") != name:
            _fail("INPUT_MANIFEST_INVALID")
        if "synced" in value and type(value["synced"]) is not bool:
            _fail("INPUT_MANIFEST_INVALID")
        key = value.get("content_file")
        if key is None:
            # The normal first generating/failed manifest has no content yet.
            if "content_sha256" in value or value.get("status") not in ("generating", "failed"):
                _fail("INPUT_MANIFEST_INVALID")
            return collector.result()
        namespace = "sync_report" if value.get("synced") else "local_report"
        artifacts.validate_storage_key(namespace, key)
        if key.rsplit("/", 1)[-1].rsplit("-", 1)[0] != name:
            _fail("INPUT_MANIFEST_INVALID")
        sha = value.get("content_sha256")
        if type(sha) is not str or not _SHA.fullmatch(sha):
            _fail("INPUT_MANIFEST_INVALID")
        if namespace == "sync_report" and key != f".sync-versions/{name}-{sha}.md":
            _fail("INPUT_MANIFEST_INVALID")
        size = value.get("byte_size")
        if size is not None and (type(size) is not int or not 0 <= size <= 4 * 1024 * 1024):
            _fail("INPUT_MANIFEST_INVALID")
        collector.reference(namespace, key, name, sha, size)
    except (_Invalid, SyncError, ValueError, TypeError) as exc:
        collector.block(("local_report", "sync_report"), getattr(exc, "code", "INPUT_MANIFEST_INVALID"), name)
    return collector.result()
