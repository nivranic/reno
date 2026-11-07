"""Held original personal facts and private replay of their exact SQLite raw.

The caller owns an existing exact-profile EX guard. A live capture never owns
StateStore, creates a sender/receiver facility, changes SQL, or grants restore
authority. Its observers remain live until successful context exit, including
the final source-fingerprint checks required despite the worker heartbeat.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import stat
import time
import uuid

from . import config, personal_mirror_contract as facts
from . import personal_mirror_transfer_contract as wire
from . import personal_mirror_transfer_schema as receiver_schema
from . import personal_mirror_transport as sender
from . import personal_preservation_contract as contract
from . import personal_preservation_files as package_files
from . import storage_lifecycle as lifecycle, storage_readonly as readonly
from . import storage_source as source, sync_media as safe, sync_state

MAX_ROWS = 100_000
MAX_SQL_BYTES = 128 * 1024 * 1024
MAX_CELL_BYTES = 1024 * 1024
MAX_SQL_STEPS = 20_000_000
_PRIVATE_ROOTS = {".reno-personal-preservation-catalog-v1",
                  ".reno-personal-preservation-bundles-v1"}
_IDENTITY_KEYS = ("device", "inode", "byte_size", "mtime_ns", "ctime_ns")


def _fail(code, *, retriable=False):
    raise contract.PersonalPreservationError(code, retriable=retriable)


def _same(actual, expected, code="SOURCE_CHANGED"):
    if actual != expected:
        _fail(code, retriable=code == "SOURCE_CHANGED")


class _Budget:
    def __init__(self, limits, check):
        self.limits = deepcopy(limits)
        self.deadline = time.monotonic() + limits["max_seconds"]
        self.callback = check or (lambda: None)
        self.files = self.byte_size = self.directories = 0

    def check(self):
        self.callback()
        if time.monotonic() > self.deadline:
            _fail("TIME_BUDGET_EXCEEDED")

    def file(self, size):
        self.check()
        self.files += 1
        self.byte_size += size
        if self.files > self.limits["max_files"] or self.byte_size > self.limits["max_bytes"]:
            _fail("SOURCE_BUDGET_EXCEEDED")


class _SourceStack(ExitStack):
    """Close every observer without replacing an existing primary failure."""
    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        except BaseException as cleanup:
            if exc is not None and cleanup is not exc:
                exc.add_note("PERSONAL_PRESERVATION_SOURCE_CLEANUP_FAILED")
                return False
            raise


class _HeldFile:
    """Original path/fstat facts, with a reader alive through terminal checks."""
    def __init__(self, directory, name, stack, budget, *, live_sql=False):
        self.directory, self.name, self.check = directory, name, budget.check
        self.path = directory.path / name
        self.path_identity = package_files.identity(directory.entry_stat(name))
        budget.file(self.path_identity["byte_size"])
        self.byte_size = self.path_identity["byte_size"]
        self.stream = stack.enter_context(source.source_reader(directory, name)
            if live_sql else directory.open_file(name))
        self.handle_identity = package_files.identity(os.fstat(self.stream.fileno()))
        _same({k: v for k, v in self.path_identity.items() if k != "ctime_ns"},
              {k: v for k, v in self.handle_identity.items() if k != "ctime_ns"})
        self.directory_identity = package_files.directory_identity(directory)
        self.sha256, self.raw = self._read(collect=False)
        self.verify()
        # This callback precedes stream and directory closure in the stack.
        stack.push(self._finish)

    def _finish(self, exc_type, _exc, _tb):
        if exc_type is None:
            self.verify()
        return False

    def _read(self, *, collect):
        self.check()
        self.stream.seek(0)
        digest, total, pieces = hashlib.sha256(), 0, []
        while True:
            self.check()
            chunk = self.stream.read(128 * 1024)
            if not chunk:
                break
            total += len(chunk)
            if total > self.byte_size:
                _fail("SOURCE_CHANGED", retriable=True)
            digest.update(chunk)
            if collect:
                pieces.append(chunk)
        self.check()
        _same(total, self.byte_size)
        return digest.hexdigest(), b"".join(pieces) if collect else None

    def bytes(self, maximum):
        if self.byte_size > maximum:
            _fail("CONTROL_FILE_TOO_LARGE")
        self.verify()
        digest, raw = self._read(collect=True)
        self._verify_identities()
        _same(digest, self.sha256)
        self.verify()
        return raw

    def _verify_identities(self):
        self.check()
        self.directory.verify()
        _same(package_files.directory_identity(self.directory), self.directory_identity)
        _same(package_files.identity(self.directory.entry_stat(self.name)), self.path_identity)
        _same(package_files.identity(os.fstat(self.stream.fileno())), self.handle_identity)

    def verify(self):
        self._verify_identities()
        if hasattr(self, "sha256"):
            digest = self._read(collect=False)[0]
            self._verify_identities()
            _same(digest, self.sha256)
        self.check()


@dataclass(frozen=True, slots=True)
class TempCleanupReceiptV1:
    """CLOSED records legal cleanup return only; path absence is unobserved."""
    format_version: int
    kind: str
    scope: str
    phase: str
    created_returned: bool
    cleanup_attempted: bool
    cleanup_returned: bool
    cleanup_return_legal: bool
    failed: bool
    cleanup_state: str
    cached_directory_identity: tuple[int, int] | None
    post_cleanup_path_missing: bool | None


class _PersonalPrivateDirectory(readonly._PrivateDirectory):
    """Set privacy on only our empty new TEMP before any original SQL copy."""
    def __init__(self):
        # Initialize failure/receipt caches before any TEMP-producing API.
        self._private_ready = False
        self._receipt_phase, self._cleanup_state = "new", "empty"
        self._created_returned = self._cleanup_attempted = False
        self._cleanup_returned = self._cleanup_return_legal = False
        self._cached_directory_identity = None
        self._cleanup_first = self._cleanup_first_tb = None
        try:
            super().__init__()
            self._cache_original_identity()
            self._cleanup_state = "owned"
            if os.name == "nt":
                package_files._windows_acl(self.path, directory=True, initialize=True)
            package_files.require_private(self.path, directory=True)
            self._private_ready = True
            self._verify()
        except BaseException as primary:
            self._capture_cleanup(primary)
            self._receipt_phase = "retired"
            try:
                # Only the original inherited identities admit safe cleanup.
                if self._cache_original_identity():
                    self.cleanup()
                else:
                    self._cleanup_state = "unknown"
            except BaseException as failure:
                self._capture_cleanup(failure)
            self._raise_cleanup_first()
        self._created_returned = True
        self._receipt_phase = "active"

    def _cache_original_identity(self):
        # Read only inherited acquisition caches; never acquire new metadata.
        own = self.__dict__
        identity, parent_identity = own.get("identity"), own.get("parent_identity")
        if (type(identity) is tuple and len(identity) == 2
                and all(type(item) is int for item in identity)):
            self._cached_directory_identity = identity
        return (self._cached_directory_identity is not None
                and type(parent_identity) is tuple and len(parent_identity) == 2
                and all(type(item) is int for item in parent_identity)
                and own.get("temporary") is not None
                and own.get("path") is not None and own.get("parent") is not None)

    def _capture_cleanup(self, failure):
        if self._cleanup_first is None:
            self._cleanup_first = failure
            self._cleanup_first_tb = BaseException.__dict__["__traceback__"].__get__(failure)
        elif failure is not self._cleanup_first:
            try:
                BaseException.add_note(self._cleanup_first, "PRIVATE_IMAGE_CLEANUP_FAILED")
            except BaseException:
                pass

    def _raise_cleanup_first(self):
        if self._cleanup_first is not None:
            raise BaseException.with_traceback(self._cleanup_first, self._cleanup_first_tb) from None

    @property
    def cleanup_receipt(self):
        # Own scalar/tuple caches only; no filesystem, provider or clock reads.
        return TempCleanupReceiptV1(
            format_version=1, kind="private_temp", scope="owner_cleanup_facts_only",
            phase=self._receipt_phase, created_returned=self._created_returned,
            cleanup_attempted=self._cleanup_attempted,
            cleanup_returned=self._cleanup_returned,
            cleanup_return_legal=self._cleanup_return_legal,
            failed=self._cleanup_first is not None, cleanup_state=self._cleanup_state,
            cached_directory_identity=self._cached_directory_identity,
            post_cleanup_path_missing=None)

    @staticmethod
    def _actual_cleanup(temporary):
        return temporary.cleanup()

    def cleanup(self):
        if self._cleanup_attempted or self._cleanup_state == "unknown":
            self._raise_cleanup_first()
            return None
        self._cleanup_attempted = True
        self._receipt_phase, self._cleanup_state = "retired", "attempted"
        try:
            # Preserve the base detach -> verify -> actual cleanup sequence.
            self.temporary._finalizer.detach()
            self._verify()
            actual = self._actual_cleanup(self.temporary)
            self._cleanup_returned = True
            if actual is not None:
                _fail("PRIVATE_IMAGE_CLEANUP_INVALID_RESULT")
        except BaseException as failure:
            self._cleanup_state = "unknown"
            self._capture_cleanup(failure)
        else:
            self._cleanup_return_legal = True
            self._cleanup_state = "closed"
        self._raise_cleanup_first()
        return None

    def _verify(self):
        super()._verify()
        if self._private_ready:
            package_files.require_private(self.path, directory=True)

    def initialize_file(self, path):
        """Initialize only our newly created empty TEMP file before SQL bytes."""
        self._verify()
        path = Path(path)
        if not path.is_absolute() or path.parent != self.path:
            _fail("PRIVATE_IMAGE_UNSAFE")
        original = package_files.identity(path.stat(follow_symlinks=False))
        if original["byte_size"] != 0:
            _fail("PRIVATE_IMAGE_NOT_EMPTY")
        if os.name == "nt":
            package_files._windows_acl(path, directory=False, initialize=True)
        package_files.require_private(path, directory=False)
        current = package_files.identity(path.stat(follow_symlinks=False))
        _same((current["device"], current["inode"], current["byte_size"]),
              (original["device"], original["inode"], 0), "PRIVATE_IMAGE_CHANGED")
        self._verify()


def _authorizer(columns, *, checking_integrity=False):
    def authorize(action, first, second, database, origin):
        if origin is not None:
            return sqlite3.SQLITE_DENY
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ:
            if (database == "main" and (checking_integrity or
                    first in columns and (second in columns[first] or second == ""))):
                return sqlite3.SQLITE_OK
            if (database == "temp" and first in {"sqlite_temp_master", "sqlite_master"}
                    and second in {"type", "name", "tbl_name", "rootpage", "sql"}):
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and database in {None, "main"}:
            if first in {"table_xinfo", "table_info", "index_list", "index_info", "index_xinfo", "foreign_key_list"}:
                return sqlite3.SQLITE_OK
            if (checking_integrity and second is None and
                    first in {"integrity_check", "foreign_key_check", "data_version"}):
                # FTS5 xConnect reads data_version while SQLite checks the
                # copied complete schema. Never grant the assignment form or
                # expose this pragma in the subsequent domain projection.
                return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and second in {"count", "length", "typeof", "lower"}:
            return sqlite3.SQLITE_OK
        if (checking_integrity and action == sqlite3.SQLITE_FUNCTION and
                second in {"substr", "replace", "glob", "json_valid", "json_type"}):
            # Fixed innocuous builtins used by schema17 CHECK expressions.
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    return authorize


def _schema_digest(con, table):
    layout = facts._describe_table(con, table)
    return contract.digest(layout)


def _project_sql(con, fingerprint, check):
    """Typed raw-cell summaries under a bounded, fixed read-only projection."""
    columns = {name: set(cols) for name, cols in
               {**contract.PERSONAL_COLUMNS, **contract.RECEIVER_COLUMNS}.items()}
    columns.update(meta={"key", "value"},
        sqlite_master={"type", "name", "tbl_name", "rootpage", "sql"},
        sqlite_schema={"type", "name", "tbl_name", "rootpage", "sql"})
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL_BYTES)
    con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, 32 * 1024)
    steps, cancellation, cancellation_tb = 0, None, None
    def progress():
        nonlocal steps, cancellation, cancellation_tb
        try:
            check()
        except BaseException as exc:
            if cancellation is None:
                cancellation = exc
                cancellation_tb = BaseException.__dict__["__traceback__"].__get__(exc)
            return 1
        steps += 1000
        return int(steps > MAX_SQL_STEPS)
    exit_failure = readonly._ExitFailure()
    try:
        con.set_progress_handler(progress, 1000)
        try:
            check()
            # These pragmas read the copied whole DB; no source DB/SHM is opened.
            con.set_authorizer(_authorizer(columns, checking_integrity=True))
            if con.execute("PRAGMA main.integrity_check").fetchmany(2) != [("ok",)]:
                _fail("SQL_INTEGRITY_INVALID")
            if con.execute("PRAGMA main.foreign_key_check").fetchone() is not None:
                _fail("SQL_FOREIGN_KEYS_INVALID")
            con.set_authorizer(_authorizer(columns))
            descriptor = facts.validate_database_schema(con)
            if any(row[0] in facts.TABLE_NAMES for row in con.execute(
                    "SELECT tbl_name FROM main.sqlite_master WHERE type='trigger'")):
                _fail("PERSONAL_SCHEMA_INVALID")
            row = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
            if row != ("17",):
                _fail("SQL_SCHEMA_VERSION_UNSUPPORTED")
            meta_rows = list(con.execute(
                "SELECT key,value FROM main.meta WHERE key IN (?,?,?,?) ORDER BY key", facts.META_KEYS))
            meta_keys = [row[0] for row in meta_rows]
            core_meta_keys = [key for key in facts.META_KEYS if key != "default_project_id"]
            missing_default_project = meta_keys == core_meta_keys
            if meta_keys != list(facts.META_KEYS) and not missing_default_project:
                _fail("PERSONAL_META_INVALID")
            meta = dict(meta_rows)
            facts._uuid(meta["library_id"])
            if (type(meta["personal_primary_id"]) is not str or
                    missing_default_project and meta["personal_primary_id"] != ""):
                _fail("PERSONAL_META_INVALID")
            if meta["personal_primary_id"]:
                facts._uuid(meta["personal_primary_id"])
            try:
                epoch = int(meta["primary_epoch"])
            except (ValueError, TypeError):
                _fail("PERSONAL_META_INVALID")
            if epoch < 1 or meta["primary_epoch"] != str(epoch):
                _fail("PERSONAL_META_INVALID")
            all_rows, summaries, row_count, sql_bytes = {}, {}, 0, 0
            def table(name, expected_columns):
                nonlocal row_count, sql_bytes
                check()
                actual_columns = [c["name"] for c in facts._describe_table(con, name)["columns"]]
                _same(actual_columns, list(expected_columns), "SQL_COLUMNS_INVALID")
                query = 'SELECT ' + ','.join('"' + n + '"' for n in actual_columns) + ' FROM main."' + name + '"'
                rows = []
                for raw in con.execute(query):
                    check()
                    values = tuple(raw)
                    row_count += 1
                    sql_bytes += len(contract.canonical_bytes([contract.typed_cell(v) for v in values]))
                    if row_count > MAX_ROWS or sql_bytes > MAX_SQL_BYTES:
                        _fail("SQL_BUDGET_EXCEEDED")
                    rows.append(values)
                all_rows[name] = [dict(zip(actual_columns, row)) for row in rows]
                summary = contract.typed_table_digest(actual_columns, rows)
                summary["schema_sha256"] = _schema_digest(con, name)
                return summary
            for name, cols in contract.PERSONAL_COLUMNS.items():
                summaries[name] = table(name, cols)
            if missing_default_project and any(summary["count"] for summary in summaries.values()):
                # Native uninitialized cloud facts have three original meta rows
                # and ten empty personal tables. Do not invent a default project
                # or accept an incomplete populated personal domain as that case.
                _fail("PERSONAL_META_INVALID")
            receiver_objects = receiver_schema._objects(con, "main.sqlite_master")
            receiver_present = bool(receiver_objects)
            receiver_summaries = {}
            if receiver_present:
                receiver_schema.validate(con)
                for name, cols in contract.RECEIVER_COLUMNS.items():
                    receiver_summaries[name] = table(name, cols)
                _receiver_semantics(all_rows, check)
            meta_summary = contract.typed_table_digest(["key", "value"], meta_rows)
            meta_summary["schema_sha256"] = _schema_digest(con, "meta")
            result = {"schema_version": 17, "source_fingerprint": deepcopy(fingerprint),
                "meta": meta_summary, "personal_tables": summaries,
                "receiver_facility": "installed" if receiver_present else "absent",
                "receiver_tables": receiver_summaries, "integrity": "ok", "foreign_keys": "ok"}
            check()
            return result, meta, all_rows
        except sqlite3.Error:
            if cancellation is not None:
                raise BaseException.with_traceback(cancellation, cancellation_tb) from None
            if steps > MAX_SQL_STEPS:
                _fail("SQL_BUDGET_EXCEEDED")
            _fail("SQL_INVALID")
    except BaseException as original:
        exit_failure.capture(original,
            original_tb=cancellation_tb if original is cancellation else None)
        raise
    finally:
        try:
            con.set_progress_handler(None, 0)
        except BaseException as cleanup:
            exit_failure.capture(cleanup, note="PERSONAL_SQL_PROGRESS_RESTORE_FAILED")
        try:
            con.set_authorizer(readonly._readonly_authorizer)
        except BaseException as cleanup:
            exit_failure.capture(cleanup, note="PERSONAL_SQL_AUTHORIZER_RESTORE_FAILED")
        exit_failure.raise_if_any()


def _receiver_semantics(rows, check):
    """Original facility rows, without calling receiver runtime write gates."""
    intents = {r["operation_id"]: r for r in rows[receiver_schema.INTENT]}
    prepared, receipts = {}, {}
    for operation, record in intents.items():
        check()
        value = wire.normalize_prepare(contract.decode(record["prepare_json"]))
        source_fact, target, previous = value["source"], value["receiver"], value["previous"]
        expected = {"operation_id": value["operation_id"], "format_version": 1,
            "prepare_sha256": wire.prepare_sha256(value), "snapshot_id": value["snapshot_id"],
            "snapshot_sha256": value["snapshot_sha256"],
            "source_library_id": source_fact["library_id"], "source_node_id": source_fact["node_id"],
            "source_primary_id": source_fact["personal_primary_id"], "source_primary_epoch": source_fact["primary_epoch"],
            "receiver_library_id": target["library_id"], "receiver_node_id": target["node_id"],
            "previous_snapshot_id": None if previous is None else previous["snapshot_id"],
            "previous_snapshot_sha256": None if previous is None else previous["snapshot_sha256"],
            "expected_revision": value["expected_revision"]}
        _same({key: record[key] for key in expected}, expected, "RECEIVER_INTENT_INVALID")
        prepared[operation] = value
    attachment_rows = {}
    for record in rows[receiver_schema.ATTACHMENT]:
        check()
        value = prepared.get(record["operation_id"])
        if value is None:
            _fail("RECEIVER_ATTACHMENT_INVALID")
        declared = {d["attachment_id"]: d for d in wire.attachment_manifest(value)}
        descriptor = declared.get(record["attachment_id"])
        if descriptor is None:
            _fail("RECEIVER_ATTACHMENT_INVALID")
        _same({key: record[key] for key in ("storage_key", "sha256", "byte_size")},
              {key: descriptor[key] for key in ("storage_key", "sha256", "byte_size")},
              "RECEIVER_ATTACHMENT_INVALID")
        _same(contract.decode(record["descriptor_json"]), descriptor, "RECEIVER_ATTACHMENT_INVALID")
        _same(record["descriptor_sha256"], wire._digest(descriptor), "RECEIVER_ATTACHMENT_INVALID")
        if intents[record["operation_id"]]["state"] == "installed" and record["state"] != "installed":
            _fail("RECEIVER_ATTACHMENT_INVALID")
        attachment_rows.setdefault(record["operation_id"], []).append(record)
    for record in rows[receiver_schema.RECEIPT]:
        check()
        operation = record["operation_id"]
        value = prepared.get(operation)
        if value is None or intents[operation]["state"] != "installed":
            _fail("RECEIVER_RECEIPT_INVALID")
        receipt = wire.normalize_receipt(value, contract.decode(record["receipt_json"]))
        for key in ("operation_id", "format_version", "prepare_sha256", "snapshot_id", "snapshot_sha256",
                    "installed_revision", "receipt_sha256", "committed_at"):
            _same(record[key], receipt[key], "RECEIVER_RECEIPT_INVALID")
        if receipt["replayed"] is not False:
            _fail("RECEIVER_RECEIPT_INVALID")
        receipts[operation] = receipt
    for operation, intent in intents.items():
        if intent["state"] == "installed":
            if operation not in receipts or {r["attachment_id"] for r in attachment_rows.get(operation, [])} != {
                    d["attachment_id"] for d in wire.attachment_manifest(prepared[operation])}:
                _fail("RECEIVER_RECEIPT_INVALID")
    heads = rows[receiver_schema.HEAD]
    if len(heads) > 1:
        _fail("RECEIVER_HEAD_INVALID")
    if heads:
        head = heads[0]
        operation = head["operation_id"]
        if operation not in receipts:
            _fail("RECEIVER_HEAD_INVALID")
        for key in ("source_library_id", "source_node_id", "source_primary_id", "source_primary_epoch",
                    "receiver_library_id", "receiver_node_id"):
            _same(head[key], intents[operation][key], "RECEIVER_HEAD_INVALID")
        receipt = receipts[operation]
        for left, right in (("revision", "installed_revision"), ("snapshot_id", "snapshot_id"),
                            ("snapshot_sha256", "snapshot_sha256"), ("receipt_sha256", "receipt_sha256")):
            _same(head[left], receipt[right], "RECEIVER_HEAD_INVALID")
    elif receipts:
        _fail("RECEIVER_HEAD_INVALID")


class _Capture:
    def __init__(self, profile, guard, stack, budget):
        self.profile, self.guard, self.stack, self.budget = profile, guard, stack, budget
        self.directories, self.inventories, self.holds, self.records = {}, {}, {}, {}
        self.requirements = []
        self.files = {}
        self.image = None
        self._active = True
        self._selection = None

    @property
    def selection(self):
        if not self._active or self._selection is None:
            _fail("SOURCE_OBSERVER_CLOSED")
        return deepcopy(self._selection)

    def directory(self, root_kind, relative="", *, observe_names=False):
        key = root_kind, relative
        if key in self.directories:
            directory = self.directories[key]
        else:
            path = self.root_paths[root_kind] / relative
            try:
                directory = self.stack.enter_context(safe._Directory(path, base=self.root_paths[root_kind]))
            except FileNotFoundError:
                directory = None
            self.directories[key] = directory
        if observe_names and key not in self.inventories:
            self.budget.directories += 1
            if self.budget.directories > self.budget.limits["max_files"]:
                _fail("SOURCE_BUDGET_EXCEEDED")
            names = self._names(root_kind, relative, directory)
            self.inventories[key] = {"root_kind": root_kind, "relative_key": relative,
                "present": directory is not None,
                "identity": None if directory is None else package_files.directory_identity(directory),
                "names": names, "names_sha256": contract.digest(names)}
        return directory

    def _names(self, root_kind, relative, directory):
        if directory is None:
            return []
        self.budget.check()
        names = directory.names()
        if root_kind == "DATA" and relative == "":
            # The producer creates its private catalogs below DATA. Neither
            # these names nor parent mtime are evidence about source subtrees.
            names = [name for name in names if name not in _PRIVATE_ROOTS]
        if len(names) > self.budget.limits["max_files"]:
            _fail("SOURCE_BUDGET_EXCEEDED")
        for name in names:
            safe._entry_name(name)
        return sorted(names)

    def add(self, root_kind, relative, role="unclassified_raw", *, live_sql=False, optional=False):
        key = root_kind, relative
        if key in self.holds:
            return self.holds[key]
        path = Path(relative)
        directory_key = path.parent.as_posix()
        if directory_key == ".":
            directory_key = ""
        directory = self.directory(root_kind, directory_key, observe_names=True)
        if directory is None:
            if optional:
                return None
            _fail("REQUIRED_BODY_MISSING")
        try:
            hold = _HeldFile(directory, path.name, self.stack, self.budget, live_sql=live_sql)
        except FileNotFoundError:
            if optional:
                return None
            _fail("REQUIRED_BODY_MISSING")
        inventory = self.inventories[root_kind, directory_key]
        self.holds[key] = hold
        self.records[key] = {"root_kind": root_kind, "relative_key": relative, "role": role,
            "sha256": hold.sha256, "byte_size": hold.byte_size,
            "source_directory_identity": {**hold.directory_identity, "names_sha256": inventory["names_sha256"]},
            "source_path_identity": deepcopy(hold.path_identity),
            "source_handle_identity": deepcopy(hold.handle_identity), "binding_refs": []}
        return hold

    def classify(self, key, role, binding=None):
        if key not in self.records:
            _fail("REQUIRED_BODY_MISSING")
        record = self.records[key]
        if record["role"] == "unclassified_raw":
            record["role"] = role
        if binding is not None and binding not in record["binding_refs"]:
            record["binding_refs"].append(binding)

    def tree(self, root_kind, relative):
        directory = self.directory(root_kind, relative, observe_names=True)
        if directory is None:
            return False
        for name in self.inventories[root_kind, relative]["names"]:
            self.budget.check()
            info = directory.entry_stat(name)
            is_directory = stat.S_ISDIR(info.st_mode)
            safe._safe_stat(info, directory=is_directory)
            nested = relative + "/" + name if relative else name
            if is_directory:
                if len(Path(nested).parts) > 64:
                    _fail("SOURCE_TREE_TOO_DEEP")
                self.tree(root_kind, nested)
            else:
                self.add(root_kind, nested)
        return True

    def required(self, kind, key, descriptor, *, operation_id, binding, expected_identity=None):
        hold = self.holds.get(key)
        if hold is None:
            _fail("REQUIRED_BODY_MISSING")
        _same((hold.sha256, hold.byte_size), (descriptor["sha256"], descriptor["byte_size"]),
              "REQUIRED_BODY_CHANGED")
        if expected_identity is not None:
            _same({k: hold.handle_identity[k] for k in _IDENTITY_KEYS}, expected_identity,
                  "REQUIRED_BODY_IDENTITY_CHANGED")
        role = {"current_ready": "current_personal_attachment", "historical_home": "historical_home_attachment",
                "receiver_stored": "receiver_stored_attachment", "sender_frozen": "sender_frozen_attachment"}[kind]
        self.classify(key, role, binding)
        self.requirements.append({"kind": kind, "_key": key, "operation_id": operation_id,
            "attachment_id": descriptor["attachment_id"], "sha256": descriptor["sha256"],
            "byte_size": descriptor["byte_size"], "binding_sha256": binding,
            "expected_file_identity": deepcopy(expected_identity)})

    def control(self, relative, maximum):
        hold = self.holds.get(("DATA", sender.ROOT + "/" + relative))
        if hold is None:
            _fail("SENDER_CONTROL_MISSING")
        raw = hold.bytes(maximum)
        return hold, raw, contract.decode(raw)

    def verify(self):
        if not self._active:
            _fail("SOURCE_OBSERVER_CLOSED")
        self.budget.check()
        _same(lifecycle.require_guard(self.profile, exclusive=True), self.guard, "SOURCE_GUARD_CHANGED")
        self.guard.verify()
        self.image.verify_source()
        for key, inventory in self.inventories.items():
            directory = self.directories[key]
            if directory is None:
                path = self.root_paths[key[0]] / key[1]
                try:
                    path.stat(follow_symlinks=False)
                except FileNotFoundError:
                    continue
                _fail("SOURCE_CHANGED", retriable=True)
            _same(package_files.directory_identity(directory), inventory["identity"])
            _same(self._names(*key, directory), inventory["names"])
        for directory in self.root_directories.values():
            directory.verify()
        for held in self.holds.values():
            held.verify()
        self.image.verify_source()
        self.budget.check()


def _canonical_operation(name, suffix):
    if not name.endswith(suffix):
        return None
    value = name[:-len(suffix)]
    try:
        return value if str(uuid.UUID(value)) == value else None
    except (ValueError, AttributeError):
        return None


def _sender_facts(capture, meta, node_hold, node_id):
    if not capture.tree("DATA", sender.ROOT):
        return "absent"
    marker_hold, marker_raw, marker = capture.control(sender.MARKER, sender.MAX_CONTROL_BYTES)
    sender._exact(marker, {"format_version", "kind", "profile", "binding", "node_sha256", "node_file_identity"})
    if type(marker["format_version"]) is not int:
        _fail("SENDER_FACILITY_INVALID")
    binding = sync_state._binding(marker["binding"])
    _same(marker, {"format_version": 1, "kind": "personal_mirror_sender_facility", "profile": list(capture.profile.identity),
        "binding": binding, "node_sha256": node_hold.sha256,
        "node_file_identity": {key: node_hold.handle_identity[key] for key in _IDENTITY_KEYS}},
        "SENDER_FACILITY_INVALID")
    _same((binding["home_library_id"], binding["home_node_id"]),
          (meta["library_id"], node_id), "SENDER_FACILITY_INVALID")
    _same(meta["personal_primary_id"], node_id, "SENDER_PRIMARY_INVALID")
    lock = capture.holds.get(("DATA", sender.ROOT + "/" + sender.LOCK))
    if lock is None or lock.byte_size != 1:
        _fail("SENDER_FACILITY_INVALID")
    capture.classify(("DATA", sender.ROOT + "/" + sender.MARKER), "sender_facility_raw", marker_hold.sha256)
    capture.classify(("DATA", sender.ROOT + "/" + sender.LOCK), "sender_lock_raw", marker_hold.sha256)
    catalog_hold, catalog_raw, value = capture.control(sender.CATALOG, sender.MAX_CATALOG_BYTES)
    catalog = sender._catalog(catalog_raw, value, marker_hold.sha256)
    capture.classify(("DATA", sender.ROOT + "/" + sender.CATALOG), "sender_catalog_raw", marker_hold.sha256)
    declared = {entry["operation_id"]: entry for entry in catalog["entries"]}
    requests, blobs, receipts = {}, {}, {}
    names = capture.inventories["DATA", sender.ROOT]["names"]
    for name in names:
        operation = _canonical_operation(name, ".prepare.json")
        if operation is None:
            continue
        held, raw, request = capture.control(name, sender.MAX_REQUEST_BYTES)
        request = sender._normalize_request(request)
        _same(request["prepare"]["operation_id"], operation, "SENDER_REQUEST_INVALID")
        _same(request["binding"], binding, "SENDER_REQUEST_INVALID")
        requests[operation] = (held, raw, request)
        capture.classify(("DATA", sender.ROOT + "/" + name), "sender_request_raw", held.sha256)
    gap = set(requests) - set(declared)
    if set(declared) - set(requests) or len(gap) > 1:
        _fail("SENDER_HISTORY_INVALID")
    if gap:
        operation = next(iter(gap))
        if any(entry["receipt_file_sha256"] is None for entry in catalog["entries"]):
            _fail("SENDER_HISTORY_INVALID")
        _same(requests[operation][2]["previous_operation_id"], catalog["head_operation_id"], "SENDER_HISTORY_INVALID")
    ordered = [entry["operation_id"] for entry in catalog["entries"]] + sorted(gap)
    previous_request = None
    for position, operation in enumerate(ordered):
        held, raw, request = requests[operation]
        prepared = request["prepare"]
        _same(prepared["expected_revision"], position, "SENDER_HISTORY_INVALID")
        if previous_request is not None:
            prior = previous_request["prepare"]
            _same(prepared["previous"], {"snapshot_id": prior["snapshot_id"], "snapshot_sha256": prior["snapshot_sha256"]},
                  "SENDER_HISTORY_INVALID")
            wire.normalize_prepare(prepared, prior_snapshot_json=prior["snapshot_json"])
        entry = declared.get(operation)
        if entry is not None:
            expected = sender._entry(request, raw)
            _same({**entry, "blob_manifest_sha256": None, "receipt_file_sha256": None}, expected,
                  "SENDER_HISTORY_INVALID")
        previous_request = request
    for name in names:
        operation = _canonical_operation(name, ".blobs.json")
        if operation is not None:
            if operation not in requests or operation not in declared:
                _fail("SENDER_HISTORY_INVALID")
            held, raw, manifest = capture.control(name, sender.MAX_CATALOG_BYTES)
            request = requests[operation][2]
            prepared = request["prepare"]
            sender._exact(manifest, {"format_version", "kind", "operation_id", "prepare_sha256", "entries"})
            if type(manifest["format_version"]) is not int:
                _fail("SENDER_BLOBS_INVALID")
            _same((manifest["format_version"], manifest["kind"], manifest["operation_id"], manifest["prepare_sha256"]),
                  (1, "personal_mirror_sender_blobs", operation, wire.prepare_sha256(prepared)), "SENDER_BLOBS_INVALID")
            originals = wire.attachment_manifest(prepared)
            if type(manifest["entries"]) is not list or len(manifest["entries"]) != len(originals):
                _fail("SENDER_BLOBS_INVALID")
            for actual, descriptor in zip(manifest["entries"], originals):
                sender._exact(actual, {"attachment_id", "storage_key", "sha256", "byte_size", "file_identity"})
                _same({k: v for k, v in actual.items() if k != "file_identity"},
                      {k: v for k, v in descriptor.items() if k != "file_identity"}, "SENDER_BLOBS_INVALID")
                facts.file_identity(actual["file_identity"])
                key = ("DATA", sender.ROOT + "/" + sender._blob_directory(operation) + "/" + facts.storage_key(actual["storage_key"]).name)
                capture.required("sender_frozen", key, actual, operation_id=operation,
                    binding=held.sha256, expected_identity=actual["file_identity"])
            entry = declared[operation]
            if entry["blob_manifest_sha256"] is not None:
                _same(entry["blob_manifest_sha256"], held.sha256, "SENDER_BLOBS_INVALID")
            elif entry["receipt_file_sha256"] is not None:
                _fail("SENDER_HISTORY_INVALID")
            blobs[operation] = held.sha256
            capture.classify(("DATA", sender.ROOT + "/" + name), "sender_blob_manifest_raw", held.sha256)
        operation = _canonical_operation(name, ".receipt.json")
        if operation is not None:
            if operation not in declared:
                _fail("SENDER_HISTORY_INVALID")
            held, raw, wrapper = capture.control(name, sender.MAX_CONTROL_BYTES)
            sender._exact(wrapper, {"format_version", "kind", "request_sha256", "receipt"})
            if type(wrapper["format_version"]) is not int:
                _fail("SENDER_RECEIPT_INVALID")
            request_hold, _, request = requests[operation]
            _same((wrapper["format_version"], wrapper["kind"], wrapper["request_sha256"]),
                  (1, "personal_mirror_sender_receipt", request_hold.sha256), "SENDER_RECEIPT_INVALID")
            wire.normalize_receipt(request["prepare"], wrapper["receipt"])
            entry = declared[operation]
            if entry["blob_manifest_sha256"] is None:
                _fail("SENDER_HISTORY_INVALID")
            if entry["receipt_file_sha256"] is not None:
                _same(entry["receipt_file_sha256"], held.sha256, "SENDER_RECEIPT_INVALID")
            receipts[operation] = held.sha256
            capture.classify(("DATA", sender.ROOT + "/" + name), "sender_receipt_raw", request_hold.sha256)
    for operation, entry in declared.items():
        if entry["blob_manifest_sha256"] is not None and operation not in blobs:
            _fail("SENDER_BLOBS_MISSING")
        if entry["receipt_file_sha256"] is not None and operation not in receipts:
            _fail("SENDER_RECEIPT_MISSING")
        if operation in receipts and operation not in blobs:
            _fail("SENDER_BLOBS_MISSING")
    for operation, (held, raw, request) in requests.items():
        if operation in blobs:
            continue
        for descriptor in wire.attachment_manifest(request["prepare"]):
            key = ("DATA", "personal-attachments/" + str(facts.storage_key(descriptor["storage_key"])))
            capture.required("historical_home", key, descriptor, operation_id=operation,
                binding=held.sha256, expected_identity=descriptor["file_identity"])
    return "installed"


@contextmanager
def capture_locked(profile, *, operation_id, request_sha256, limits, check_cancelled=None):
    """Produce a closed selection only from continuously held original files.

    Success includes every observer's exit-time verification. The caller must
    publish source_verified only after this context has exited successfully.
    """
    facts._uuid(operation_id)
    facts._sha(request_sha256)
    if check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    limits = contract.validate_limits(limits)
    profile = lifecycle._profile(profile)
    guard = lifecycle.require_guard(profile, exclusive=True)
    budget = _Budget(limits, check_cancelled)
    budget.check()
    capture = None
    with _SourceStack() as stack:
        capture = _Capture(profile, guard, stack, budget)
        # Registered first: becomes inactive after all exit-time source checks,
        # on failures as well as success. Closed DTOs cannot be borrowed holds.
        stack.callback(setattr, capture, "_active", False)
        local_config = config.local_path().absolute()
        capture.root_paths = {"DATA": profile.database.parent, "WORKSPACE": Path(config.ROOT).absolute(),
            "CONFIG": local_config.parent, "MEDIA": profile.media, "REPORTS": profile.reports,
            "CHECKPOINTS": profile.checkpoints}
        capture.root_directories = {}
        roots = {}
        for root_kind, path in capture.root_paths.items():
            directory = capture.directory(root_kind)
            if directory is None:
                _fail("SOURCE_ROOT_MISSING")
            capture.root_directories[root_kind] = directory
            roots[root_kind] = {"path": contract.canonical_path(str(path)),
                **package_files.directory_identity(directory), "membership": "unknown"}
        data = capture.root_directories["DATA"]
        capture.image = stack.enter_context(readonly.committed_image_locked(profile,
            max_bytes=min(limits["max_bytes"], readonly.MAX_BYTES_LIMIT), attempts=1,
            check_cancelled=budget.check, private_directory_factory=_PersonalPrivateDirectory))
        sql, meta, rows = _project_sql(capture.image.connection, capture.image.source_fingerprint, budget.check)
        for suffix, role in (("", "database_raw"), ("-wal", "database_wal_raw"), ("-journal", "database_journal_raw")):
            fingerprint = capture.image.source_fingerprint["files"][suffix]
            held = capture.add("DATA", profile.database.name + suffix, role, live_sql=True, optional=bool(suffix))
            if fingerprint is None:
                _same(held, None)
            else:
                if held is None:
                    _fail("SOURCE_CHANGED", retriable=True)
                _same({k: held.path_identity[k] for k in _IDENTITY_KEYS},
                      {k: fingerprint[k] for k in _IDENTITY_KEYS})
                _same((held.sha256, held.byte_size), (fingerprint["sha256"], fingerprint["byte_size"]))
        node = capture.add("DATA", "node-identity.json", "node_identity_raw")
        node_value = contract.decode(node.bytes(4096))
        if type(node_value) is not dict or set(node_value) != {"instance_id"}:
            _fail("NODE_IDENTITY_INVALID")
        node_id = facts._uuid(node_value["instance_id"])
        capture.add("CONFIG", local_config.name, "local_config_raw", optional=True)
        state = capture.add("DATA", "sync-state.json", "knowledge_state_raw", optional=True)
        if state is not None:
            sync_state._validate(contract.decode(state.bytes(sync_state.MAX_STATE_BYTES)))
        capture.tree("DATA", "personal-attachments")
        for row in sorted(rows["source_attachment"], key=lambda r: r["attachment_id"]):
            if row["state"] == "ready":
                key = ("DATA", "personal-attachments/" + str(facts.storage_key(row["storage_key"])))
                binding = contract.typed_table_digest(list(contract.PERSONAL_COLUMNS["source_attachment"]),
                    [tuple(row[c] for c in contract.PERSONAL_COLUMNS["source_attachment"])])["sha256"]
                capture.required("current_ready", key, row, operation_id=row["operation_id"], binding=binding)
        if sql["receiver_facility"] == "installed":
            for row in sorted(rows[receiver_schema.ATTACHMENT], key=lambda r: (r["operation_id"], r["attachment_id"])):
                if row["state"] in {"stored", "installed"}:
                    key = ("DATA", "personal-attachments/" + str(facts.storage_key(row["storage_key"])))
                    identity = {"device": row["local_file_device"], "inode": row["local_file_inode"],
                        "byte_size": row["byte_size"], "mtime_ns": row["local_file_mtime_ns"],
                        "ctime_ns": row["local_file_ctime_ns"]}
                    binding = contract.typed_table_digest(list(contract.RECEIVER_COLUMNS[receiver_schema.ATTACHMENT]),
                        [tuple(row[c] for c in contract.RECEIVER_COLUMNS[receiver_schema.ATTACHMENT])])["sha256"]
                    capture.required("receiver_stored", key, row, operation_id=row["operation_id"],
                        binding=binding, expected_identity=identity)
        sender_facility = _sender_facts(capture, meta, node, node_id)
        files = []
        for index, key in enumerate(sorted(capture.records)):
            record = capture.records[key]
            record["entry_id"] = "f" + str(index).zfill(8)
            record["binding_refs"].sort()
            files.append(record)
            capture.files[record["entry_id"]] = capture.holds[key]
        key_ids = {key: record["entry_id"] for key, record in capture.records.items()}
        requirements = []
        for index, row in enumerate(sorted(capture.requirements, key=lambda r: (r["kind"], r["operation_id"], r["attachment_id"], r["_key"]))):
            selected = {k: v for k, v in row.items() if k != "_key"}
            selected.update(requirement_id="r" + str(index).zfill(8), entry_id=key_ids[row["_key"]])
            requirements.append(selected)
        source_profile = {name: contract.canonical_path(str(getattr(profile, name)))
                          for name in ("database", "media", "reports", "checkpoints")}
        capture._selection = contract.validate_selection({"format_version": 1, "kind": "personal_preservation_selection",
            "operation_id": operation_id, "request_sha256": request_sha256,
            "source_observation": {"library_id": meta["library_id"], "node_id": node_id,
                "personal_primary_id": meta["personal_primary_id"] or None, "primary_epoch": int(meta["primary_epoch"]),
                "profile_sha256": contract.digest(source_profile), "node_sha256": node.sha256,
                "roots": roots, "sender_facility": sender_facility, "receiver_facility": sql["receiver_facility"]},
            "sql": sql, "inventories": [capture.inventories[k] for k in sorted(capture.inventories)],
            "files": files, "requirements": requirements,
            "unknowns": [r["entry_id"] for r in files if r["role"] == "unclassified_raw"],
            "limits_sha256": contract.digest(limits)})
        capture.verify()
        yield capture
        capture.verify()


class PayloadImage:
    def __init__(self, connection, selection, copies, check):
        self.connection, self.selection, self.copies, self.check = connection, deepcopy(selection), dict(copies), check
        self.snapshot_bytes = sum(row["byte_size"] for row in selection["files"]
                                  if row["role"] in {"database_raw", "database_wal_raw", "database_journal_raw"})
        self._active = True

    def verify(self):
        if not self._active:
            _fail("PAYLOAD_IMAGE_CLOSED")
        self.check()
        for row in self.selection["files"]:
            if row["role"] in {"database_raw", "database_wal_raw", "database_journal_raw"}:
                held = self.copies.get(row["entry_id"])
                if held is None:
                    _fail("PAYLOAD_SQL_MISSING")
                held.verify()
                _same((held.sha256, held.handle_identity["byte_size"]), (row["sha256"], row["byte_size"]),
                      "PAYLOAD_SQL_CHANGED")
        self.check()


@contextmanager
def payload_image_locked(selection, copies, *, check_cancelled=None):
    """Replay pinned exact package DB/WAL in protected TEMP, without a profile.

    copies are live FileHold observers owned by the caller. The read-only
    connection may receive another restricted parser policy after typed SQL
    correspondence; source SHM and source lifecycle facilities are never used.
    """
    selection = contract.validate_selection(selection)
    if type(copies) is not dict or check_cancelled is not None and not callable(check_cancelled):
        _fail("INVALID_ARGUMENT")
    check = check_cancelled or (lambda: None)
    check()
    sql_files = {row["role"]: row for row in selection["files"] if row["role"] in
                 {"database_raw", "database_wal_raw", "database_journal_raw"}}
    if "database_raw" not in sql_files:
        _fail("PAYLOAD_SQL_MISSING")
    if sum(row["byte_size"] for row in sql_files.values()) > readonly.MAX_BYTES_LIMIT:
        _fail("PAYLOAD_SQL_TOO_LARGE")
    temporary = connection = image = None
    try:
        temporary = _PersonalPrivateDirectory()
        database_path = temporary.path / "image.sqlite"
        for role, suffix in (("database_raw", ""), ("database_wal_raw", "-wal"), ("database_journal_raw", "-journal")):
            row = sql_files.get(role)
            fingerprint = selection["sql"]["source_fingerprint"]["files"][suffix]
            if row is None:
                if fingerprint is not None:
                    _fail("PAYLOAD_SQL_MISSING")
                continue
            if fingerprint is None or (row["sha256"], row["byte_size"]) != (fingerprint["sha256"], fingerprint["byte_size"]):
                _fail("PAYLOAD_SQL_FINGERPRINT_INVALID")
            if suffix == "-journal" and row["byte_size"]:
                _fail("PAYLOAD_ROLLBACK_JOURNAL_PRESENT")
            held = copies.get(row["entry_id"])
            if type(held) is not package_files.FileHold:
                _fail("PAYLOAD_SQL_HOLD_REQUIRED")
            held.verify()
            _same((held.sha256, held.handle_identity["byte_size"]), (row["sha256"], row["byte_size"]), "PAYLOAD_SQL_CHANGED")
            check()
            temporary._verify()
            target_path = database_path.with_name(database_path.name + suffix)
            fd = os.open(target_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_BINARY", 0), 0o600)
            digest, byte_size = hashlib.sha256(), 0
            with os.fdopen(fd, "wb") as target:
                temporary.initialize_file(target_path)
                held.stream.seek(0)
                while True:
                    check()
                    chunk = held.stream.read(128 * 1024)
                    if not chunk:
                        break
                    byte_size += len(chunk)
                    if byte_size > row["byte_size"]:
                        _fail("PAYLOAD_SQL_CHANGED")
                    digest.update(chunk)
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            _same((digest.hexdigest(), byte_size), (row["sha256"], row["byte_size"]), "PAYLOAD_SQL_CHANGED")
            package_files.require_private(target_path, directory=False)
            held.verify()
        temporary._verify()
        connection = readonly._open_private(database_path)
        actual, _, _ = _project_sql(connection, selection["sql"]["source_fingerprint"], check)
        _same(actual, selection["sql"], "PAYLOAD_SQL_SEMANTICS_CHANGED")
        image = PayloadImage(connection, selection, copies, check)
        image.verify()
        yield image
        image.verify()
        temporary._verify()
    except BaseException as exc:
        if image is not None:
            image._active = False
        readonly._cleanup(None, connection, temporary, primary=exc)
        raise
    else:
        if image is not None:
            image._active = False
        readonly._cleanup(None, connection, temporary)


def verify_sql_payload(selection, copies, *, check_cancelled=None):
    """Verify original SHA/size, committed WAL and fixed typed SQL semantics."""
    with payload_image_locked(selection, copies, check_cancelled=check_cancelled) as image:
        image.verify()
