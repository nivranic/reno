"""Committed-image checkpoint graphs and retention, without collection.

Only explicit profile paths and identities are used. Logical restore edges,
physical archive holds and transitive media/report roots remain distinct.
The caller owns the original committed SQL image and the complete group guard.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from datetime import datetime
import hashlib
import json
import math
import os
import re
import sqlite3
import time

from . import storage_artifacts as artifacts, storage_gc_contract as contract
from . import storage_lifecycle as lifecycle, storage_root_inputs as inputs
from . import storage_source, sync_checkpoints as checkpoints, sync_media as safe, sync_protocol as protocol
from . import sync_snapshot
from . import storage_home_recovery_roots as home_recovery_roots

_SCOPES = ("checkpoint", "sync_media", "sync_report")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_COLUMNS = {
    "meta": ("key", "value"),
    "sync_checkpoint": tuple(sorted(checkpoints._ENTRY_KEYS)),
    "sync_recovery_checkpoint": tuple(sorted(checkpoints._RECOVERY_ENTRY_KEYS)),
    "sync_publication": ("publication_id", "committed_at", "local_version_json"),
    "sync_recovery_operation": ("operation_id", "action", "created_at", "request_sha256", "request_json", "result_json"),
    "sync_recovery_control": ("slot", "state", "generation", "revision", "source_floor_json", "restore_operation_id"),
    "storage_artifact": ("artifact_id", "namespace", "root_id", "storage_key", "sha256", "byte_size"),
    "storage_gc_operation": ("operation_id", "request_json"),
    home_recovery_roots.TABLE: home_recovery_roots.COLUMNS,
    **inputs.CONTINUITY_COLUMNS,
}


@dataclass(frozen=True)
class CheckpointLimits:
    max_archives: int = 512
    max_archive_bytes: int = checkpoints.MAX_ARCHIVE_BYTES
    max_hash_bytes: int = 1024 * 1024 * 1024
    max_rows: int = 100_000
    max_references: int = 100_000
    max_cell_bytes: int = 4 * 1024 * 1024
    max_sql_bytes: int = 64 * 1024 * 1024
    max_vm_steps: int = 2_000_000
    max_json_nodes: int = 1_000_000
    max_json_depth: int = 24
    max_depth: int = 2
    deadline_seconds: float = 30.0

    def __post_init__(self):
        for name, value in asdict(self).items():
            if name == "deadline_seconds":
                if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= 3600:
                    raise ValueError("Invalid checkpoint limits")
            elif type(value) is not int or not 1 <= value <= 8 * 1024**3:
                raise ValueError("Invalid checkpoint limits")
        if self.max_archives > 4096 or self.max_archive_bytes > checkpoints.MAX_ARCHIVE_BYTES:
            raise ValueError("Invalid checkpoint limits")


class CheckpointRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


class _Invalid(ValueError):
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def _hash(value):
    return hashlib.sha256(inputs.canonical_bytes(value)).hexdigest()


class _Budget:
    def __init__(self, limits, check=None):
        self.limits, self.check_cancelled = limits, check or (lambda: None)
        self.started = time.monotonic()
        self.rows = self.sql_bytes = self.hash_bytes = self.steps = self.archives = self.references = 0
        self.cancelled = None
        self.exhausted = None

    def check_cancellation(self):
        if self.cancelled is not None:
            raise self.cancelled
        try:
            self.check_cancelled()
        except BaseException as exc:
            self.cancelled = exc
            raise

    def check(self):
        self.check_cancellation()
        if self.exhausted is not None:
            raise _Invalid(self.exhausted)
        if time.monotonic() - self.started > self.limits.deadline_seconds:
            self.exhausted = "CHECKPOINT_DEADLINE_LIMIT"
            raise _Invalid("CHECKPOINT_DEADLINE_LIMIT")

    def progress(self):
        self.steps += 1000
        if self.steps > self.limits.max_vm_steps:
            self.exhausted = "CHECKPOINT_VM_LIMIT"
        try:
            self.check()
            return 0
        except BaseException:
            return 1

    def row(self, cursor, values):
        self.check()
        self.rows += 1
        if self.rows > self.limits.max_rows:
            raise _Invalid("CHECKPOINT_ROW_LIMIT")
        for value in values:
            if value is None:
                length = 0
            elif type(value) is str:
                length = len(value.encode("utf-8"))
            elif type(value) in (int, float):
                length = 8
            else:
                raise _Invalid("CHECKPOINT_CELL_INVALID")
            if length > self.limits.max_cell_bytes:
                raise _Invalid("CHECKPOINT_CELL_LIMIT")
            self.sql_bytes += length
        if self.sql_bytes > self.limits.max_sql_bytes:
            raise _Invalid("CHECKPOINT_SQL_BYTES_LIMIT")
        return sqlite3.Row(cursor, values)

    def configure(self, con):
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self.limits.max_cell_bytes)
        con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, min(self.limits.max_cell_bytes, 1024 * 1024))
        con.set_progress_handler(self.progress, 1000)
        con.row_factory = self.row

    def preserve_cancellation(self):
        if self.cancelled is not None:
            raise self.cancelled

    def json(self, raw):
        if type(raw) is not str or len(raw.encode("utf-8")) > self.limits.max_cell_bytes:
            raise _Invalid("CHECKPOINT_JSON_LIMIT")
        value = contract.decode(raw)
        stack, nodes = [(value, 0)], 0
        while stack:
            self.check()
            item, depth = stack.pop()
            nodes += 1
            if nodes > self.limits.max_json_nodes or depth > self.limits.max_json_depth:
                raise _Invalid("CHECKPOINT_JSON_LIMIT")
            if type(item) is dict:
                stack.extend((child, depth + 1) for child in item.values())
            elif type(item) is list:
                stack.extend((child, depth + 1) for child in item)
        return value


class _Reader:
    def __init__(self, con, budget):
        self.con, self.budget, self.checked = con, budget, set()
        self.previous_row_factory = con.row_factory
        self.previous_length = con.getlimit(sqlite3.SQLITE_LIMIT_LENGTH)
        self.previous_sql = con.getlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH)

    def authorize(self, action, first, second, schema, source):
        continuity = inputs.continuity_authorize(action, first, second, schema, source)
        if continuity is not None:
            return continuity
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == "table_xinfo" and second in _COLUMNS:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main" and source is None:
            if first == "sqlite_master" and second in ("name", "type", "sql"):
                return sqlite3.SQLITE_OK
            if second in _COLUMNS.get(first, ()):
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def install(self):
        self.budget.configure(self.con)
        self.con.set_authorizer(self.authorize)

    def query(self, sql, parameters=()):
        self.install()
        try:
            return [dict(row) for row in self.con.execute(sql, parameters)]
        except sqlite3.Error:
            self.budget.preserve_cancellation()
            raise _Invalid(self.budget.exhausted or "CHECKPOINT_SQL_INVALID") from None

    def ordinary(self, name, *, optional=False):
        if name in self.checked:
            return True
        found = self.query("SELECT type,sql FROM sqlite_master WHERE name=? LIMIT 2", (name,))
        if not found and optional:
            return False
        if (len(found) != 1 or found[0]["type"] != "table" or type(found[0]["sql"]) is not str
                or not " ".join(found[0]["sql"].upper().split()).startswith("CREATE TABLE ")
                or "CREATE VIRTUAL TABLE" in " ".join(found[0]["sql"].upper().split())):
            raise _Invalid("CHECKPOINT_SCHEMA_UNSAFE")
        columns = self.query(f'PRAGMA table_xinfo("{name}")')
        by_name = {row["name"]: row for row in columns}
        integers = {"format_version", "schema_version", "byte_size", "slot", "generation", "revision"}
        if any(column not in by_name or by_name[column]["hidden"] != 0
               or by_name[column]["type"].upper() != ("INTEGER" if column in integers else "TEXT") for column in _COLUMNS[name]):
            raise _Invalid("CHECKPOINT_SCHEMA_UNSAFE")
        self.checked.add(name)
        return True

    def rows(self, table, *, fields=None, where="", args=(), optional=False):
        if not self.ordinary(table, optional=optional):
            return []
        fields = fields or _COLUMNS[table]
        return self.query("SELECT " + ",".join('"' + c + '"' for c in fields) + ' FROM "' + table + '"' + where, args)

    def finish(self):
        self.con.set_progress_handler(None, 0)
        self.con.row_factory = self.previous_row_factory
        self.con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, self.previous_length)
        self.con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, self.previous_sql)


@dataclass(frozen=True)
class ArchiveNode:
    member_id: str
    source_kind: str
    source_id: str
    origin_kind: str
    checkpoint_id: str
    descriptor: dict
    logical_recovery_edge: bool
    physical_archive_hold: bool
    retirement_status: str
    retirement_code: str
    valid: bool
    recoverable: bool
    retained: bool
    retention_reasons: tuple[str, ...]
    dependencies: tuple[inputs.Reference, ...]


@dataclass(frozen=True)
class CheckpointScan:
    inputs: inputs.InputScan
    effective_inputs: inputs.InputScan
    nodes: tuple[ArchiveNode, ...]
    proposed_retirements: tuple[dict, ...]
    semantic_bindings: tuple[inputs.SourceBinding, ...]

    def inputs_for_retirements(self, identifiers):
        """Remove only a chosen subset of this source's eligible live edges."""
        chosen = set(identifiers)
        eligible = {(r["origin_kind"], r["checkpoint_id"]) for r in self.proposed_retirements}
        if not chosen <= eligible:
            raise ValueError("Unknown proposed retirement")
        if not chosen:
            return self.inputs
        refs = []
        for node in self.nodes:
            removed = (node.origin_kind, node.checkpoint_id) in chosen
            if node.logical_recovery_edge and not removed:
                refs.extend(node.dependencies)
            if node.physical_archive_hold or node.logical_recovery_edge and not removed:
                refs.append(_archive_ref(node))
        binding = inputs.SourceBinding(self.nodes[0].member_id, self.nodes[0].source_kind,
            self.nodes[0].source_id, "effective_edges", "selected", _hash(sorted(chosen)), len(chosen))
        return inputs.InputScan(_ordered(refs), (), self.inputs.blockers, _ordered((*self.inputs.source_bindings, binding)))


class ArchiveCache:
    """One guard round only; cached bytes never erase source provenance."""
    def __init__(self):
        self.guard = None
        self.files, self.archives = {}, {}

    def bind(self, guard):
        if self.guard is None:
            self.guard = guard
        elif self.guard is not guard:
            raise CheckpointRootError("CHECKPOINT_CACHE_GUARD_MISMATCH")


class _Files:
    def __init__(self, profile, guard, budget, cache):
        self.profile, self.guard, self.budget, self.cache = profile, guard, budget, cache
        self.observed = {}
        cache.bind(guard)

    def _read(self, root, key, maximum, *, keep=False, use_cache=True):
        self.budget.check()
        path = root / key
        try:
            directory = safe._Directory(path.parent, base=root).__enter__()
        except FileNotFoundError:
            return None, ("missing",), None
        except (OSError, safe.SyncError):
            return None, ("unsafe",), None
        try:
            try:
                before = storage_source.file_signature(directory.entry_stat(path.name))
            except FileNotFoundError:
                return None, ("missing",), None
            except (OSError, ValueError):
                return None, ("unsafe",), None
            if before[2] > maximum:
                return None, ("too_large", before), None
            physical = tuple((dev, ino) for _, dev, ino in directory.identities), before
            if use_cache and physical in self.cache.files:
                raw, token, digest = self.cache.files[physical]
                if not keep or raw is not None:
                    directory.verify()
                    return raw, token, digest
            try:
                stream = directory.open_file(path.name)
            except (OSError, ValueError):
                return None, ("unreadable", before), None
            try:
                opened = storage_source.file_signature(os.fstat(stream.fileno()))
                if opened[:4] != before[:4]:
                    raise CheckpointRootError("CHECKPOINT_SOURCE_CHANGED", retriable=True)
                digest, size, chunks = hashlib.sha256(), 0, []
                while True:
                    self.budget.check()
                    remaining = self.budget.limits.max_hash_bytes - self.budget.hash_bytes
                    if remaining <= 0 and size < before[2]:
                        raise _Invalid("CHECKPOINT_HASH_BYTES_LIMIT")
                    chunk = stream.read(min(128 * 1024, max(1, remaining + 1)))
                    if not chunk:
                        break
                    size += len(chunk)
                    self.budget.hash_bytes += len(chunk)
                    if self.budget.hash_bytes > self.budget.limits.max_hash_bytes:
                        raise _Invalid("CHECKPOINT_HASH_BYTES_LIMIT")
                    if size > before[2]:
                        raise CheckpointRootError("CHECKPOINT_SOURCE_CHANGED", retriable=True)
                    digest.update(chunk)
                    if keep:
                        chunks.append(chunk)
                if size != before[2] or storage_source.file_signature(os.fstat(stream.fileno())) != opened:
                    raise CheckpointRootError("CHECKPOINT_SOURCE_CHANGED", retriable=True)
            finally:
                stream.close()
            if storage_source.file_signature(directory.entry_stat(path.name)) != before:
                raise CheckpointRootError("CHECKPOINT_SOURCE_CHANGED", retriable=True)
            directory.verify()
            token, digest = ("present", physical), digest.hexdigest()
            raw = b"".join(chunks) if keep else None
            self.cache.files[physical] = raw, token, digest
            return raw, token, digest
        finally:
            directory.__exit__(None, None, None)

    def read(self, root, key, maximum, *, keep=False):
        raw, token, digest = self._read(root, key, maximum, keep=keep)
        self.observed[(str(root), key)] = (root, key, maximum, token, digest)
        return raw, token, digest

    @property
    def rawbindings(self):
        return tuple((str(root), key, token, sha) for root, key, _maximum, token, sha in self.observed.values())

    def verify(self):
        self.guard.verify()
        old_budget = self.budget
        self.budget = _Budget(old_budget.limits, old_budget.check_cancelled)
        try:
            # Empty/incomplete graphs still poll caller cancellation. With no
            # file work, do not turn an already controlled scan deadline into
            # a new context-exit failure. Actual reads retain their own bounds.
            self.budget.check_cancellation()
            for root, key, maximum, token, digest in self.observed.values():
                _raw, now, sha = self._read(root, key, maximum, use_cache=False)
                if (now, sha) != (token, digest):
                    raise CheckpointRootError("CHECKPOINT_SOURCE_CHANGED", retriable=True)
            if self.observed:
                self.budget.check()
            else:
                self.budget.check_cancellation()
        except _Invalid as exc:
            self.budget.preserve_cancellation()
            raise CheckpointRootError(exc.code) from None
        finally:
            self.budget = old_budget
        self.guard.verify()


def _ordered(values):
    return tuple(sorted(set(values), key=lambda value: inputs.canonical_bytes(asdict(value))))


def _archive_ref(node):
    d = node.descriptor
    return inputs.Reference("checkpoint", d["root_id"], d["storage_key"], node.member_id,
        node.source_kind, node.source_id, _hash((node.origin_kind, node.checkpoint_id)), d["sha256"], d["byte_size"])


class _Graph:
    def __init__(self, reader, files, *, profile, root_ids, member_id, library_id, node_id,
                 policy, source_kind, source_id, live_retention):
        self.reader, self.files, self.budget = reader, files, files.budget
        self.profile, self.roots, self.member = profile, dict(root_ids), member_id
        self.library, self.node_id, self.policy = library_id, node_id, policy
        self.kind, self.source, self.live = source_kind, _hash(source_id), live_retention
        self.nodes, self.blockers, self.bindings = [], [], []
        self.protected, self.control_valid = set(), True
        self.entries_valid, self.schema, self.current_version = True, None, None

    def block(self, code, locator, namespaces=_SCOPES):
        for ns in namespaces:
            self.blockers.append(inputs.Blocker(ns, self.roots[ns], self.member, self.kind,
                self.source, code, _hash(locator)))

    def bind(self, section, value, *, state="observed"):
        self.bindings.append(inputs.SourceBinding(self.member, self.kind, self.source, section, state,
                                                  _hash(value), len(value)))

    def ref(self, namespace, key, checkpoint_id, *, sha=None, size=None):
        self.budget.references += 1
        if self.budget.references > self.budget.limits.max_references:
            raise _Invalid("CHECKPOINT_REFERENCE_LIMIT")
        artifacts.validate_storage_key(namespace, key)
        return inputs.Reference(namespace, self.roots[namespace], key, self.member, self.kind,
            self.source, _hash((checkpoint_id, namespace, key)), sha, size)

    def control(self, entries):
        rows = self.reader.rows("sync_recovery_control", optional=self.schema < 8)
        if len(rows) > 1:
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        if not rows:
            self.bind("active_recovery", [])
            return
        row = rows[0]
        if (row["slot"] != 1 or row["state"] not in ("running", "held", "resume_pending")
                or type(row["generation"]) is not int or row["generation"] < 0
                or type(row["revision"]) is not int or row["revision"] < row["generation"]):
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        if row["source_floor_json"] is not None:
            checkpoints._version(self.budget.json(row["source_floor_json"]))
        identifier = row["restore_operation_id"]
        if identifier is None:
            if row["state"] != "running" or row["generation"]:
                raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
            self.bind("active_recovery", row)
            return
        checkpoints._uuid(identifier)
        operations = self.reader.rows("sync_recovery_operation", where=" WHERE operation_id=? LIMIT 2", args=(identifier,))
        if len(operations) != 1 or operations[0]["action"] != "restore":
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        op = operations[0]
        request, result = self.budget.json(op["request_json"]), self.budget.json(op["result_json"])
        if (type(request) is not dict or set(request) != {"action", "expected_version", "expected_control_revision", "checkpoint_id", "checkpoint_kind"}
                or request["action"] != "restore" or request["checkpoint_kind"] not in ("publication", "recovery")
                or contract.canonical_bytes(request).decode() != op["request_json"]
                or hashlib.sha256(op["request_json"].encode()).hexdigest() != op["request_sha256"]):
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        checkpoints._version(request["expected_version"])
        if (request["expected_version"]["library_id"] != self.library
                or type(request["expected_control_revision"]) is not int or request["expected_control_revision"] < 0):
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        required = {"operation_id", "action", "checkpoint_id", "checkpoint_kind", "before_version", "version", "control",
                    "protection_checkpoint_id", "committed_at", "replayed"}
        if (type(result) is not dict or set(result) != required or result["operation_id"] != identifier
                or result["action"] != "restore" or result["replayed"] is not False
                or result["checkpoint_id"] != request["checkpoint_id"] or result["checkpoint_kind"] != request["checkpoint_kind"]
                or result["before_version"] != request["expected_version"] or result["committed_at"] != op["created_at"]):
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        version = checkpoints._version(result["version"])
        before = result["before_version"]
        if (version["library_id"] != self.library or self.current_version is None
                or (version["library_epoch"], version["kb_revision"]) <= (before["library_epoch"], before["kb_revision"])
                or (version["library_epoch"], version["kb_revision"]) >
                    (self.current_version["library_epoch"], self.current_version["kb_revision"])):
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        from .sync_client import _recovery
        _recovery(result["control"], target_library_id=self.library)
        if (result["control"]["state"] != "held" or result["control"]["generation"] != row["generation"]
                or result["control"]["revision"] != request["expected_control_revision"] + 1
                or result["control"]["revision"] > row["revision"]):
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        protection = ("recovery", checkpoints._uuid(result["protection_checkpoint_id"]))
        by_id = {(d["origin_kind"], d["checkpoint_id"]): d for d in entries}
        if protection not in by_id or by_id[protection]["origin_id"] != identifier:
            raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
        self.protected.add(protection)
        if row["state"] in ("held", "resume_pending"):
            target = (request["checkpoint_kind"], checkpoints._uuid(request["checkpoint_id"]))
            if target not in by_id:
                raise _Invalid("ACTIVE_RECOVERY_UNPROVEN")
            self.protected.add(target)
        self.bind("active_recovery", {"control": row, "request": request,
                                      "protection": protection, "protected": sorted(self.protected)})

    def origin(self, entry, descriptor):
        recovery = descriptor["origin_kind"] == "recovery"
        table, column = ("sync_recovery_operation", "operation_id") if recovery else ("sync_publication", "publication_id")
        fields = ("operation_id", "action", "created_at") if recovery else _COLUMNS[table]
        rows = self.reader.rows(table, fields=fields, where=f' WHERE "{column}"=? LIMIT 2', args=(descriptor["origin_id"],))
        if len(rows) != 1 or recovery and rows[0]["action"] != "restore":
            raise _Invalid("CHECKPOINT_ORIGIN_UNCOMMITTED")
        stamp = rows[0]["created_at" if recovery else "committed_at"]
        contract._stamp(stamp)
        if not recovery:
            version = checkpoints._version(self.budget.json(rows[0]["local_version_json"]))
            if version["library_id"] != self.library:
                raise _Invalid("CHECKPOINT_ORIGIN_UNCOMMITTED")
        return rows[0]

    def ownership(self, descriptor):
        rows = self.reader.rows("storage_artifact", where=" WHERE namespace='checkpoint' AND root_id=? AND storage_key=? LIMIT 2",
            args=(self.roots["checkpoint"], descriptor["storage_key"]), optional=self.schema < 10)
        if len(rows) > 1:
            raise _Invalid("CHECKPOINT_OWNERSHIP_UNPROVEN")
        if not rows:
            return None
        row = rows[0]
        expected_id = "art_" + contract.digest({key: row[key] for key in ("namespace", "root_id", "storage_key")})
        if (row["artifact_id"] != expected_id or row["sha256"] != descriptor["sha256"]
                or row["byte_size"] != descriptor["byte_size"]):
            raise _Invalid("CHECKPOINT_OWNERSHIP_UNPROVEN")
        return row["artifact_id"]

    def disposition(self, proof, descriptor):
        rows = self.reader.rows("storage_gc_operation", where=" WHERE operation_id=? LIMIT 2", args=(proof.operation_id,))
        if len(rows) != 1:
            raise _Invalid("CHECKPOINT_RETIREMENT_UNPROVEN")
        request = contract.validate_request(self.budget.json(rows[0]["request_json"]))
        selected = next(r for r in request["retirements"] if (r["origin_kind"], r["checkpoint_id"]) ==
                        (descriptor["origin_kind"], descriptor["checkpoint_id"]))
        if selected["archive_disposition"] == "preserve_unowned":
            return True
        item = next(f for f in request["files"] if f["artifact_id"] == selected["archive_item_id"])
        return item["intent"] == "retain"

    def archive(self, entry, descriptor):
        self.budget.archives += 1
        if self.budget.archives > self.budget.limits.max_archives:
            raise _Invalid("CHECKPOINT_ARCHIVE_LIMIT")
        raw, token, sha = self.files.read(self.profile.checkpoints, entry["storage_key"],
                                         self.budget.limits.max_archive_bytes, keep=True)
        if raw is None or len(raw) != entry["byte_size"] or sha != entry["sha256"]:
            raise _Invalid("CHECKPOINT_ARCHIVE_UNAVAILABLE")
        cache_key = (token, sha, entry["format_version"], entry["schema_version"], _hash(asdict(self.budget.limits)))
        cached = self.files.cache.archives.get(cache_key)
        if cached is None:
            archive = None
            start_rows, start_bytes = self.budget.rows, self.budget.sql_bytes
            try:
                archive, manifest = checkpoints._decode(raw, entry, configure_read=self.budget.configure)
                # The legacy decoder predates strict duplicate-key/typed JSON
                # contracts. Keep its restore behavior unchanged, but require
                # unambiguous bounded manifest bytes for retention evidence.
                encoded_manifest = archive.execute("SELECT manifest_json FROM checkpoint_manifest WHERE slot=1").fetchone()[0]
                strict_manifest = self.budget.json(encoded_manifest)
                if (contract.canonical_bytes(strict_manifest) != contract.canonical_bytes(manifest)
                        or any(type(value) is not int or value < 0 for value in manifest["row_counts"].values())
                        or type(manifest["format_version"]) is not int or type(manifest["schema_version"]) is not int):
                    raise _Invalid("CHECKPOINT_ARCHIVE_INVALID")
                # Read the complete closed graph under row/cell/VM/deadline
                # budgets, including unready files and every frame path.
                tables = {name: [dict(row) for row in archive.execute(f'SELECT * FROM "{name}"')]
                          for name in (*checkpoints.columns_for_schema(entry["schema_version"]), "checkpoint_media")}
                self.budget.check()
                cached = manifest, tables, (self.budget.rows - start_rows, self.budget.sql_bytes - start_bytes)
                self.files.cache.archives[cache_key] = cached
            except sqlite3.Error:
                self.budget.preserve_cancellation()
                raise _Invalid(self.budget.exhausted or "CHECKPOINT_ARCHIVE_INVALID") from None
            finally:
                if archive is not None:
                    archive.close()
        else:
            self.budget.rows += cached[2][0]
            self.budget.sql_bytes += cached[2][1]
            if self.budget.rows > self.budget.limits.max_rows or self.budget.sql_bytes > self.budget.limits.max_sql_bytes:
                raise _Invalid("CHECKPOINT_ROW_LIMIT")
        manifest, tables, _cost = cached
        origin_keys = ("operation_id", "origin_kind") if entry["format_version"] == 2 else ("publication_id",)
        if (any(manifest.get(k) != entry[k] for k in ("checkpoint_id", *origin_keys, "format_version", "schema_version", "created_at"))
                or manifest["receiver_version"] != self.budget.json(entry["receiver_version_json"])
                or manifest["receiver_version"]["library_id"] != self.library):
            raise _Invalid("CHECKPOINT_ARCHIVE_IDENTITY_MISMATCH")
        return manifest, tables

    def dependencies(self, descriptor, manifest, tables):
        # Closed schemas 7/9 allow only image -> archive -> dependency. They
        # contain no child archive tables; unknown schema objects are rejected.
        if self.budget.limits.max_depth < 2:
            raise _Invalid("CHECKPOINT_DEPTH_LIMIT")
        checkpoint_id, refs, valid = descriptor["checkpoint_id"], [], not manifest["issues"]
        declared = {}
        for row in tables["checkpoint_media"]:
            value = {key: row[key] for key in ("video_id", "sha256", "byte_size", "extension")}
            protocol.validate_media_descriptor(value)
            expected_key = f"sync/{value['video_id']}/{value['sha256']}.{value['extension']}"
            if row["storage_key"] != expected_key or row["video_id"] in declared:
                raise _Invalid("CHECKPOINT_MEDIA_DESCRIPTOR_INVALID")
            declared[row["video_id"]] = row
            refs.append(self.ref("sync_media", expected_key, checkpoint_id, sha=value["sha256"], size=value["byte_size"]))
        for asset in tables["video_asset"]:
            protocol.validate_video_id(asset["video_id"])
            try:
                files = self.budget.json(asset["files_json"] if asset["files_json"] is not None else "{}")
                if type(files) is not dict:
                    raise ValueError
                for value in files.values():
                    if value is None:
                        continue
                    key = inputs.media_storage_key(value, profile=self.profile)
                    if key is not None:
                        refs.append(self.ref("sync_media", key, checkpoint_id, sha=key.rsplit("/", 1)[1].rsplit(".", 1)[0]))
                if asset["sync_state"] == "ready":
                    item = declared.get(asset["video_id"])
                    key = inputs.media_storage_key(files.get("video"), profile=self.profile)
                    if item is None or item["storage_key"] != key or item["sha256"] != asset["content_sha256"]:
                        valid = False
            except (ValueError, TypeError, KeyError):
                self.budget.preserve_cancellation()
                self.block("CHECKPOINT_MEDIA_MAPPING_UNPROVEN", checkpoint_id, ("sync_media",))
                valid = False
        for frame in tables["frame"]:
            if frame["path"] is not None:
                try:
                    key = inputs.media_storage_key(frame["path"], profile=self.profile)
                    if key is not None:
                        refs.append(self.ref("sync_media", key, checkpoint_id, sha=key.rsplit("/", 1)[1].rsplit(".", 1)[0]))
                except (ValueError, TypeError):
                    self.block("CHECKPOINT_FRAME_MAPPING_UNPROVEN", checkpoint_id, ("sync_media",))
                    valid = False
        for row in tables["sync_report"]:
            try:
                if (row["name"] not in inputs.REPORT_NAMES or type(row["sha256"]) is not str or not _SHA.fullmatch(row["sha256"])
                        or type(row["byte_size"]) is not int or row["byte_size"] < 0
                        or row["storage_key"] != f".sync-versions/{row['name']}-{row['sha256']}.md"):
                    raise ValueError
                refs.append(self.ref("sync_report", row["storage_key"], checkpoint_id, sha=row["sha256"], size=row["byte_size"]))
                if not self.reader.rows("sync_publication", fields=("publication_id",), where=" WHERE publication_id=? LIMIT 1", args=(row["publication_id"],)):
                    valid = False
            except (ValueError, TypeError):
                self.budget.preserve_cancellation()
                self.block("CHECKPOINT_REPORT_MAPPING_UNPROVEN", checkpoint_id, ("sync_report",))
                valid = False
        for ref in _ordered(refs):
            root = self.profile.media if ref.namespace == "sync_media" else self.profile.reports
            maximum = protocol.MAX_MEDIA_BYTES if ref.namespace == "sync_media" else 4 * 1024 * 1024
            _raw, token, sha = self.files.read(root, ref.storage_key, maximum)
            if (not token or token[0] != "present" or sha != ref.sha256
                    or ref.byte_size is not None and token[1][1][2] != ref.byte_size):
                valid = False
        try:
            scoped = ("knowledge_atom", "frame", "transcript_segment", "ocr_span", "visual_observation", "evidence_ref")
            by_video = {asset["video_id"]: {name: [] for name in scoped} for asset in tables["video_asset"]}
            for name in scoped:
                for row in tables[name]:
                    self.budget.check()
                    if row["video_id"] not in by_video:
                        raise ValueError("Orphan knowledge row")
                    by_video[row["video_id"]][name].append(row)
            if set(declared) - set(by_video):
                raise ValueError("Orphan media descriptor")
            videos = [{"video_id": video, "tables": rows} for video, rows in by_video.items()]
            for video in videos:
                self.budget.check()
                sync_snapshot._check_local_references(video)
            sync_snapshot._check_global_references({"videos": videos, "globals": {
                name: tables[name] for name in ("knowledge_cluster", "conflict_case")}})
        except (ValueError, TypeError, KeyError, AttributeError, RecursionError):
            self.budget.preserve_cancellation()
            valid = False
        return _ordered(refs), valid

    def run(self):
        proposed = []
        try:
            meta = self.reader.rows("meta", where=" WHERE key IN ('migration_version','library_id','library_epoch','kb_revision')")
            values = {row["key"]: row["value"] for row in meta}
            if (len(meta) != len(values) or values.get("migration_version") not in {str(i) for i in range(18)}
                    or values.get("library_id") != self.library):
                raise _Invalid("CHECKPOINT_IMAGE_IDENTITY_UNPROVEN")
            schema = int(values["migration_version"])
            self.schema = schema
            if "library_epoch" in values and "kb_revision" in values:
                for key in ("library_epoch", "kb_revision"):
                    if type(values[key]) is not str or not re.fullmatch(r"(?:0|[1-9][0-9]{0,18})", values[key]):
                        raise _Invalid("CHECKPOINT_IMAGE_IDENTITY_UNPROVEN")
                self.current_version = checkpoints._version({"library_id": self.library,
                    "library_epoch": int(values["library_epoch"]), "kb_revision": int(values["kb_revision"])})
            self.bind("image_identity", values)
            try:
                reserved = inputs.continuity_reserved_projection(self.reader.con)
                if schema < 17:
                    if reserved:
                        self.bind("source_continuity", reserved, state="unknown")
                else:
                    public = inputs.read_continuity_projection(self.reader.con, check_cancelled=self.budget.check)
                    self.bind("source_continuity", public, state="present")
            except (inputs._Invalid, sqlite3.Error, ValueError, TypeError, KeyError) as exc:
                self.budget.preserve_cancellation()
                self.bind("source_continuity", {"code": getattr(exc, "code", "CHECKPOINT_SOURCE_CONTINUITY_UNKNOWN")}, state="unknown")
            if schema >= 16:
                home_rows = home_recovery_roots.normalize_projection(self.reader.rows(home_recovery_roots.TABLE),
                    schema_version=schema)
                if any(row["library_id"] != self.library for row in home_rows):
                    raise _Invalid("HOME_RECOVERY_ROOT_INVALID")
                self.bind(home_recovery_roots.TABLE, home_rows)
            elif self.reader.ordinary(home_recovery_roots.TABLE, optional=True):
                raise _Invalid("HOME_RECOVERY_SCHEMA_UNKNOWN")
            raw_entries = []
            for kind, table, minimum in (("publication", "sync_checkpoint", 7), ("recovery", "sync_recovery_checkpoint", 8)):
                rows = self.reader.rows(table, optional=schema < minimum)
                if rows and schema < minimum:
                    raise _Invalid("CHECKPOINT_SCHEMA_UNSAFE")
                for entry in rows:
                    self.budget.check()
                    try:
                        descriptor = contract.checkpoint_descriptor(entry, root_id=self.roots["checkpoint"])
                        if descriptor["origin_kind"] != kind or descriptor["receiver_version"]["library_id"] != self.library:
                            raise ValueError("Foreign checkpoint")
                        raw_entries.append((entry, descriptor))
                    except (ValueError, TypeError, KeyError):
                        self.budget.preserve_cancellation()
                        self.entries_valid = False
                        self.block("CHECKPOINT_ENTRY_INVALID", (kind, _hash(entry)))
            if len(raw_entries) > self.budget.limits.max_archives:
                raise _Invalid("CHECKPOINT_ARCHIVE_LIMIT")
            try:
                self.control([d for _entry, d in raw_entries])
            except (ValueError, TypeError, KeyError, sqlite3.Error) as exc:
                self.budget.preserve_cancellation()
                self.control_valid = False
                self.block(exc.code if isinstance(exc, _Invalid) else "ACTIVE_RECOVERY_UNPROVEN", "active_recovery")
            for entry, descriptor in raw_entries:
                self.budget.check()
                key = descriptor["origin_kind"], descriptor["checkpoint_id"]
                proof = contract.verify_retirement(self.reader.con, entry, expected_library_id=self.library,
                    expected_member_id=self.member, expected_root_id=self.roots["checkpoint"])
                self.budget.check()
                logical, hold, valid, recoverable, dependencies = proof.status != "proven", True, False, False, ()
                reasons = []
                try:
                    if proof.status == "proven":
                        hold = self.disposition(proof, descriptor)
                        if key in self.protected:
                            self.block("ACTIVE_RETIRED_CHECKPOINT", key)
                            logical = True
                    elif proof.status == "unproven":
                        self.block("CHECKPOINT_RETIREMENT_UNPROVEN", key, ("checkpoint",))
                    if logical:
                        origin = self.origin(entry, descriptor)
                        self.bind("checkpoint_origin", {"checkpoint": key, "origin": origin})
                        item_id = self.ownership(descriptor) if schema >= 10 else None
                        descriptor = contract.checkpoint_descriptor(entry, root_id=self.roots["checkpoint"], archive_item_id=item_id)
                        hold = item_id is None
                        manifest, tables = self.archive(entry, descriptor)
                        dependencies, closed = self.dependencies(descriptor, manifest, tables)
                        valid = True
                        node_matches = self.node_id is not None and manifest["receiver_node_id"] == self.node_id
                        recoverable = closed and node_matches and proof.status == "not_retired"
                        # Historical backup descriptors do not freeze the old
                        # receiver node. That prevents authorizing a restore,
                        # but a fully parsed historical graph still proves its
                        # dependencies and keeps its exact archive/media roots.
                        # Do not let this unknown old restore authority fence
                        # an independently proven live owner's retirement.
                        if not node_matches and self.kind == "live":
                            self.block("CHECKPOINT_NODE_BINDING_UNPROVEN", key, ("checkpoint",))
                        if not closed:
                            self.block("CHECKPOINT_NOT_RECOVERABLE", key, ("checkpoint",))
                    else:
                        valid = True  # The historical proof suffices; bytes may be legally gone.
                except CheckpointRootError:
                    raise
                except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, StopIteration, RecursionError) as exc:
                    self.budget.preserve_cancellation()
                    self.block(exc.code if isinstance(exc, _Invalid) else "CHECKPOINT_ARCHIVE_INVALID", key)
                if key in self.protected:
                    reasons.append("active_recovery")
                if not self.live and logical:
                    reasons.append("historical_image")
                if logical and (not valid or not recoverable):
                    reasons.append("unproven_recovery")
                self.nodes.append(ArchiveNode(self.member, self.kind, self.source, key[0], key[1], descriptor,
                    logical, hold, proof.status, proof.code, valid, recoverable, bool(reasons), tuple(reasons), dependencies))
            if self.live:
                for kind in ("publication", "recovery"):
                    indices = [i for i, node in enumerate(self.nodes) if node.origin_kind == kind and node.logical_recovery_edge]
                    indices.sort(key=lambda i: (datetime.fromisoformat(self.nodes[i].descriptor["created_at"].replace("Z", "+00:00")),
                                               self.nodes[i].checkpoint_id), reverse=True)
                    keep = set(indices[:self.policy["keep_" + kind]])
                    recoverable = next((i for i in indices if self.nodes[i].recoverable), None)
                    if indices and recoverable is None:
                        self.block("NO_RECOVERABLE_CHECKPOINT", kind, ("checkpoint",))
                        keep.update(indices)
                    elif recoverable is not None:
                        keep.add(recoverable)
                    for i in indices:
                        node = self.nodes[i]
                        reasons = list(node.retention_reasons)
                        if i in indices[:self.policy["keep_" + kind]]:
                            reasons.append("latest_n")
                        if i == recoverable:
                            reasons.append("latest_recoverable")
                        if schema < 10:
                            reasons.append("retention_schema_unavailable")
                        if not self.control_valid:
                            reasons.append("active_recovery_unproven")
                        if not self.entries_valid:
                            reasons.append("checkpoint_entries_unproven")
                        retained = i in keep or bool(reasons)
                        self.nodes[i] = replace(node, retained=retained, retention_reasons=tuple(sorted(set(reasons))))
                        if not retained and node.valid and node.recoverable and node.retirement_status == "not_retired":
                            proposed.append(node.descriptor)
                if schema < 10 and raw_entries:
                    self.block("CHECKPOINT_RETENTION_SCHEMA_REQUIRED", "image_schema", ("checkpoint",))
            self.budget.check()
        except CheckpointRootError:
            raise
        except (OSError, sqlite3.Error, ValueError, TypeError, KeyError, RecursionError) as exc:
            self.budget.preserve_cancellation()
            self.block(exc.code if isinstance(exc, _Invalid) else "CHECKPOINT_IMAGE_INVALID", "image")
            proposed = []
        self.bind("policy", self.policy)
        self.bind("limits", asdict(self.budget.limits))
        self.bind("graph", [{"descriptor": n.descriptor, "logical": n.logical_recovery_edge,
            "physical_hold": n.physical_archive_hold, "retirement": (n.retirement_status, n.retirement_code),
            "valid": n.valid, "recoverable": n.recoverable, "retained": n.retained,
            "retention_reasons": n.retention_reasons, "dependencies": [asdict(ref) for ref in n.dependencies]} for n in self.nodes])
        references = []
        for node in self.nodes:
            if node.logical_recovery_edge:
                references.extend(node.dependencies)
            if node.logical_recovery_edge or node.physical_archive_hold:
                references.append(_archive_ref(node))
        base = inputs.InputScan(_ordered(references), (), _ordered(self.blockers), _ordered(self.bindings))
        proposals = tuple(sorted(proposed, key=lambda d: (d["origin_kind"], d["checkpoint_id"])))
        scan = CheckpointScan(base, base, tuple(self.nodes), proposals, base.source_bindings)
        return replace(scan, effective_inputs=scan.inputs_for_retirements({(d["origin_kind"], d["checkpoint_id"]) for d in proposals}))


class CheckpointCapture:
    def __init__(self, scan, files, profile, guard):
        self.scan, self._files, self._profile, self._guard, self._active = scan, files, profile, guard, True

    @property
    def inputs(self):
        return self.scan.inputs

    @property
    def effective_inputs(self):
        return self.scan.effective_inputs

    @property
    def nodes(self):
        return self.scan.nodes

    @property
    def proposed_retirements(self):
        return self.scan.proposed_retirements

    @property
    def semantic_bindings(self):
        return self.scan.semantic_bindings

    @property
    def rawbindings(self):
        return self._files.rawbindings

    def inputs_for_retirements(self, identifiers):
        return self.scan.inputs_for_retirements(identifiers)

    def verify_source(self):
        if not self._active:
            raise CheckpointRootError("CHECKPOINT_CAPTURE_CLOSED")
        if lifecycle.require_guard(self._profile) is not self._guard:
            raise CheckpointRootError("CHECKPOINT_CAPTURE_GUARD_CHANGED")
        self._files.verify()
        return self.rawbindings


@contextmanager
def capture_image_locked(con, *, profile, root_ids, member_id, library_id, node_id=None, policy=None,
                         source_kind="live", source_id="live", live_retention=True, limits=None, cache=None,
                         check_cancelled=None):
    """Capture one original SQL image; final verification only rereads files.

    The caller may close con after entry. No current identity or retirement is
    borrowed for historical images. Share ArchiveCache only within one guard.
    """
    limits = CheckpointLimits() if limits is None else limits
    policy = contract.default_policy() if policy is None else contract.validate_policy(policy)
    if (not isinstance(limits, CheckpointLimits) or type(live_retention) is not bool
            or type(root_ids) is not dict or set(root_ids) != set(inputs.NAMESPACES)
            or any(type(v) is not str or not _SHA.fullmatch(v) for v in root_ids.values())
            or source_kind not in {"live", "backup"} or live_retention and source_kind != "live"
            or type(source_id) is not str or len(source_id) > 4096
            or check_cancelled is not None and not callable(check_cancelled)):
        raise ValueError("Invalid checkpoint capture options")
    contract._identifier(member_id, "mem_")
    checkpoints._uuid(library_id)
    if node_id is not None:
        checkpoints._uuid(node_id)
    guard = lifecycle.require_guard(profile)
    for namespace, root in (("sync_media", profile.media), ("local_report", profile.reports),
                            ("sync_report", profile.reports), ("checkpoint", profile.checkpoints)):
        if artifacts.root_identity(root, profile=profile)["root_id"] != root_ids[namespace]:
            raise CheckpointRootError("CHECKPOINT_ROOT_BINDING_MISMATCH")
    cache = ArchiveCache() if cache is None else cache
    if not isinstance(cache, ArchiveCache):
        raise ValueError("Invalid archive cache")
    budget = _Budget(limits, check_cancelled)
    files, reader = _Files(profile, guard, budget, cache), _Reader(con, budget)
    try:
        graph = _Graph(reader, files, profile=profile, root_ids=root_ids, member_id=member_id, library_id=library_id,
            node_id=node_id, policy=policy, source_kind=source_kind, source_id=source_id, live_retention=live_retention).run()
    finally:
        reader.finish()
    capture = CheckpointCapture(graph, files, profile, guard)
    try:
        yield capture
        capture.verify_source()
    finally:
        capture._active = False


def scan_image_locked(con, **options):
    with capture_image_locked(con, **options) as captured:
        return captured.scan
