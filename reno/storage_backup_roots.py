"""Read-only backup graph capture under the producer's existing group guard.

Backup files are immutable inputs, never fake StorageProfiles or live SQLite
sources. Discovery's raw digest proves a stable reading round. The public input
scan and semantic bindings contain only reference projections and opaque IDs.
Legacy descriptors are evidence to re-read, not authoritative retirement verdicts.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass
import base64
import hashlib
import os
from pathlib import Path
import sqlite3
import time

from . import storage_artifacts as artifacts, storage_backups as backups
from . import storage_lifecycle as lifecycle, storage_root_inputs as inputs
from . import sync_media as safe
from . import storage_home_recovery_roots as home_recovery_roots

_NS = artifacts.NAMESPACES
_DERIVED_REASONS = frozenset({"retirement_unproven", "database_dependencies_unreadable", "invalid_checkpoint_boundary",
    "invalid_upload_pin", "invalid_dependency", "media_mapping_unknown", "invalid_media_pointer", "manifest_set_incomplete",
    "manifest_unreadable", "legacy_manifest_unknown"})
_OLD_COLUMNS = {
    "meta": ("key", "value"), "video_asset": ("video_id", "files_json"), "frame": ("video_id", "path"),
    "sync_report": ("storage_key", "sha256", "byte_size"),
    "job": ("video_id", "kind", "status", "payload_json"), "sync_queue": ("video_id", "status"),
}


@dataclass(frozen=True)
class BackupLimits:
    max_backups: int = 4096
    max_image_bytes: int = 2 * 1024 * 1024 * 1024
    max_total_image_bytes: int = 8 * 1024 * 1024 * 1024
    max_read_bytes: int = 32 * 1024 * 1024 * 1024
    max_metadata_bytes: int = 64 * 1024 * 1024
    deadline_seconds: float = 120.0
    input_limits: inputs.InputLimits = inputs.InputLimits()

    def __post_init__(self):
        import math
        if (type(self.max_backups) is not int or not 1 <= self.max_backups <= backups.MAX_BACKUPS
                or type(self.max_image_bytes) is not int or not 1 <= self.max_image_bytes <= backups.MAX_IMAGE
                or type(self.max_total_image_bytes) is not int or self.max_total_image_bytes < 1
                or type(self.max_read_bytes) is not int or self.max_read_bytes < 1
                or type(self.max_metadata_bytes) is not int or self.max_metadata_bytes < 1
                or type(self.deadline_seconds) not in (int, float) or not math.isfinite(self.deadline_seconds)
                or self.deadline_seconds <= 0 or not isinstance(self.input_limits, inputs.InputLimits)):
            raise ValueError("Invalid backup graph limits")


class BackupRootError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__(code)
        self.code, self.retriable = code, retriable


class _CallerCancelled(BaseException):
    def __init__(self, original):
        self.original = original


def _changed():
    raise BackupRootError("SOURCE_CHANGED", retriable=True)


def _hash(value):
    return hashlib.sha256(inputs.canonical_bytes(value)).hexdigest()


def _merge(scans):
    def items(field):
        values = {value for scan in scans for value in getattr(scan, field)}
        return tuple(sorted(values, key=lambda value: inputs.canonical_bytes(asdict(value))))
    return inputs.InputScan(*(items(field) for field in ("references", "selectors", "blockers", "source_bindings")))


def _code(reason):
    # Error strings never become public diagnostics, even from a supplied sidecar.
    allowed = {"backup_reserved", "backup_retiring", "backup_unreadable_or_missing", "catalog_unreadable",
        "builtin_backup_discovery_unreadable", "catalog_destination_conflict", "retired_backup_reappeared",
        "retired_backup_location_unreadable", "unmaterialized_wal", "unmaterialized_journal", *_DERIVED_REASONS}
    return "BACKUP_" + reason.upper() if type(reason) is str and reason in allowed else "BACKUP_SOURCE_UNPROVEN"


class _Budget:
    def __init__(self, limits, check):
        self.limits, self.check_cancelled = limits, check
        self.started, self.read_bytes, self.metadata_bytes = time.perf_counter(), 0, 0
        self.image_sizes, self.image_paths, self.catalog_records = {}, {}, {}
        self.candidate_sequence = None
        self.failed = None

    def fail(self, code):
        self.failed = BackupRootError(code)
        raise self.failed

    def check(self):
        self.check_cancelled()
        if self.failed is not None:
            raise self.failed
        if time.perf_counter() - self.started > self.limits.deadline_seconds:
            self.fail("BACKUP_DEADLINE_LIMIT")

    def catalog_record(self, record):
        self.check()
        self.catalog_records[(record["backup_id"], record["destination"])] = record

    def candidates(self, paths):
        self.check()
        sequence = tuple(sorted(backups._key(path) for path in paths))
        if self.candidate_sequence is not None and sequence != self.candidate_sequence:
            _changed()
        self.candidate_sequence = sequence
        self.image_paths = {backups._key(path): Path(path) for path in paths}
        if len(paths) > self.limits.max_backups:
            self.fail("BACKUP_COUNT_LIMIT")

    def before_read(self, path, size, content):
        self.check()
        key = backups._key(path)
        main_image = key in self.image_paths
        metadata = not main_image and path.suffix.lower() == ".json"
        if (main_image or not metadata) and size > self.limits.max_image_bytes:
            self.fail("BACKUP_IMAGE_LIMIT")
        if key in self.image_paths:
            if key in self.image_sizes and self.image_sizes[key] != size:
                _changed()
            if key not in self.image_sizes:
                if sum(self.image_sizes.values()) + size > self.limits.max_total_image_bytes:
                    self.fail("BACKUP_TOTAL_IMAGE_LIMIT")
                self.image_sizes[key] = size
        # Reserve the full observed read before opening/allocating its contents.
        if self.read_bytes + size > self.limits.max_read_bytes:
            self.fail("BACKUP_READ_BYTES_LIMIT")
        if metadata and self.metadata_bytes + size > self.limits.max_metadata_bytes:
            self.fail("BACKUP_METADATA_BYTES_LIMIT")

    def read_chunk(self, path, size, content):
        self.check()
        self.read_bytes += size
        if backups._key(path) not in self.image_paths and path.suffix.lower() == ".json":
            self.metadata_bytes += size
        if self.read_bytes > self.limits.max_read_bytes:
            self.fail("BACKUP_READ_BYTES_LIMIT")
        if self.metadata_bytes > self.limits.max_metadata_bytes:
            self.fail("BACKUP_METADATA_BYTES_LIMIT")


def _source_files(path, limits, check, read_control):
    """Observe ordinary backup/descriptor/WAL/journal bytes, never source SHM."""
    result = {}
    for suffix in ("", backups.SUFFIX, "-wal", "-journal"):
        check()
        target = path.with_name(path.name + suffix)
        try:
            _, identity = backups._read(target, maximum=limits.max_image_bytes if suffix != backups.SUFFIX else backups.MAX_JSON,
                                         content=False, read_control=read_control)
            result[suffix] = identity
        except FileNotFoundError:
            result[suffix] = None
        except (backups.BackupError, safe.SyncError, OSError):
            result[suffix] = {"unreadable": True}
    return result


def _open_immutable(path, limits=None):
    # No regular mode=ro fallback: that could recover WAL or touch source SHM.
    con = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
    con.row_factory = sqlite3.Row
    try:
        limits = limits or inputs.InputLimits()
        reader = inputs._Reader(con, limits)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_cell_bytes)
        con.set_progress_handler(reader.progress, 1)
        def setup(action, first, second, schema, source):
            if action == sqlite3.SQLITE_PRAGMA and (first, second) in (("query_only", "ON"), ("trusted_schema", "OFF")):
                return sqlite3.SQLITE_OK
            return sqlite3.SQLITE_DENY
        con.set_authorizer(setup)
        con.execute("PRAGMA query_only=ON")
        con.execute("PRAGMA trusted_schema=OFF")
        con.set_progress_handler(None, 0)
        return con
    except BaseException:
        con.close()
        raise


class _LegacyReader(inputs._Reader):
    """Same bounded closed SQL rules, with historical optional field contracts."""
    def authorize(self, action, first, second, schema, source):
        continuity = inputs.continuity_authorize(action, first, second, schema, source)
        if continuity is not None:
            return continuity
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_PRAGMA and first == "table_xinfo" and second == self.active:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and schema == "main" and source is None:
            if first == "sqlite_master" and second in ("name", "type", "sql"):
                return sqlite3.SQLITE_OK
            if first == self.active and second in _OLD_COLUMNS.get(first, ()):
                return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY

    def project(self, name, *, required=(), optional=False):
        self.check()
        self.active = name
        try:
            found = self.con.execute("SELECT type,sql FROM sqlite_master WHERE name=? LIMIT 2", (name,)).fetchall()
            for row in found:
                self.consume(row, count=False)
            if not found and optional:
                return []
            if (len(found) != 1 or found[0][0] != "table" or type(found[0][1]) is not str
                    or not " ".join(found[0][1].upper().split()).startswith("CREATE TABLE ")):
                raise inputs._Invalid("INPUT_TABLE_UNSAFE")
            actual = {}
            for row in self.con.execute(f'PRAGMA table_xinfo("{name}")'):
                self.consume(row, count=False)
                actual[row[1]] = row
            fields = tuple(c for c in _OLD_COLUMNS[name] if c in actual)
            if not set(required) <= set(fields):
                raise inputs._Invalid("INPUT_COLUMNS_UNSAFE")
            for c in fields:
                if actual[c][2].upper() != ("INTEGER" if c == "byte_size" else "TEXT") or actual[c][6] != 0:
                    raise inputs._Invalid("INPUT_COLUMNS_UNSAFE")
            if not fields:
                raise inputs._Invalid("INPUT_COLUMNS_UNSAFE")
            projection = ",".join('"' + c + '"' for c in fields)
            where = " WHERE key IN ('migration_version','library_id','library_epoch','kb_revision')" if name == "meta" else ""
            result = []
            for row in self.con.execute(f'SELECT {projection} FROM "{name}"{where}'):
                self.consume(row)
                result.append(dict(zip(fields, row)))
            return sorted(result, key=inputs.canonical_bytes)
        except sqlite3.Error:
            raise inputs._Invalid(self.exhausted or "INPUT_SQL_UNSAFE") from None


def _read_meta(con, limits):
    reader = _LegacyReader(con, limits)
    previous = con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_cell_bytes)
    con.set_authorizer(reader.authorize)
    con.set_progress_handler(reader.progress, 1)
    try:
        rows = reader.project("meta", required=("key", "value"), optional=True)
        values = {r["key"]: r["value"] for r in rows}
        if len(values) != len(rows):
            raise inputs._Invalid("INPUT_SCHEMA_UNKNOWN")
        version = values.get("migration_version", "0")
        if version not in tuple(str(v) for v in range(18)):
            raise inputs._Invalid("INPUT_SCHEMA_UNKNOWN")
        library = values.get("library_id")
        if library is not None:
            backups._uuid(library)
        return int(version), library
    finally:
        con.set_progress_handler(None, 0)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous)


def _legacy_inputs(con, *, profile, root_ids, member_id, source_id, version, limits):
    """Schema 0-6: parse known fields safely; missing task payload predates v2."""
    collector = inputs._Collector(profile, root_ids, member_id, "backup", source_id, limits)
    reader = _LegacyReader(con, limits)
    previous = con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, limits.max_cell_bytes)
    con.set_authorizer(reader.authorize)
    con.set_progress_handler(reader.progress, 1)
    contracts = {
        "video_asset": (("files_json",), False, ("sync_media",)),
        "frame": (("path",), True, ("sync_media",)),
        "sync_report": (("storage_key", "sha256", "byte_size"), version < 6, ("sync_report",)),
        "job": (("video_id", "kind", "status"), True, _NS),
        "sync_queue": (("video_id", "status"), True, ("sync_media",)),
    }
    try:
        for table, (required, optional, scope) in contracts.items():
            try:
                rows = reader.project(table, required=required, optional=optional)
                collector.binding(table, "present", rows)
            except (inputs._Invalid, ValueError, TypeError):
                collector.block(scope, reader.exhausted or "BACKUP_LEGACY_SCHEMA_UNSAFE", table)
                continue
            for index, row in enumerate(rows):
                locator = [table, index]
                try:
                    if table in ("video_asset", "frame"):
                        values = inputs._json(row["files_json"], limits) if table == "video_asset" else {"path": row["path"]}
                        if type(values) is not dict:
                            raise ValueError
                        for field, path in values.items():
                            if path is None:
                                continue
                            key = inputs.media_storage_key(path, profile=profile, source_video_id=row.get("video_id"))
                            if key:
                                collector.reference("sync_media", key, [*locator, field])
                    elif table == "sync_report":
                        if (row["storage_key"].rsplit("-", 1)[-1] != row["sha256"] + ".md"
                                or type(row["byte_size"]) is not int or not 0 <= row["byte_size"] <= 4 * 1024 * 1024):
                            raise ValueError
                        collector.reference("sync_report", row["storage_key"], locator, row["sha256"], row["byte_size"])
                    elif table == "job":
                        if row["status"] not in ("pending", "running", "done", "failed", "cancelled"):
                            raise ValueError
                        if row["status"] not in ("pending", "running"):
                            continue
                        from .tasks import normalize
                        payload = inputs._json(row["payload_json"], limits) if "payload_json" in row else {}
                        normalize(row["kind"], row["video_id"], payload, internal=row["kind"] == "sync")
                        if row["kind"] == "process":
                            collector.selector(row["video_id"], locator)
                    elif table == "sync_queue":
                        if row["status"] not in ("queued", "claimed", "retry_wait", "budget_wait", "dead", "done"):
                            raise ValueError
                        if row["status"] in ("queued", "claimed", "retry_wait", "budget_wait") and row["video_id"] is not None:
                            collector.selector(row["video_id"], locator)
                except (ValueError, TypeError, KeyError, AttributeError):
                    collector.block(scope, "BACKUP_LEGACY_REFERENCE_INVALID", locator)
        return collector.result()
    finally:
        con.set_progress_handler(None, 0)
        con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, previous)


def _known_dependencies(collector, dependencies):
    if type(dependencies) is not list or len(dependencies) > backups.MAX_ROWS:
        collector.block(_NS, "BACKUP_DEPENDENCIES_UNKNOWN", "descriptor")
        return
    for index, value in enumerate(dependencies):
        namespace = value.get("namespace") if type(value) is dict else None
        scope = (namespace,) if namespace in _NS else _NS
        try:
            if type(value) is not dict or set(value) - {"namespace", "storage_key", "sha256", "byte_size", "origin"}:
                raise ValueError
            collector.reference(namespace, value["storage_key"], ["descriptor", index], value.get("sha256"), value.get("byte_size"))
        except (ValueError, TypeError, KeyError):
            collector.block(scope, "BACKUP_DEPENDENCIES_UNKNOWN", ["descriptor", index])


def _manifest_inputs(manifests, *, profile, root_ids, member_id, source_id, limits):
    collector = inputs._Collector(profile, root_ids, member_id, "backup", source_id, limits)
    if manifests is None:
        collector.block(("local_report",), "BACKUP_LEGACY_MANIFEST_UNKNOWN", "manifests")
        return collector.result()
    if type(manifests) is not dict or set(manifests) != set(backups.REPORTS):
        collector.block(("local_report", "sync_report"), "BACKUP_MANIFEST_SET_INVALID", "manifests")
        return collector.result()
    scans = []
    for name, item in sorted(manifests.items()):
        raw, unreadable = None, False
        try:
            if item != {"state": "absent"}:
                raw = base64.b64decode(item["raw_base64"], validate=True)
                if (item["state"] != "present" or len(raw) != item["byte_size"]
                        or hashlib.sha256(raw).hexdigest() != item["sha256"]):
                    raise ValueError
        except (ValueError, TypeError, KeyError):
            raw, unreadable = None, True
        scans.append(inputs.parse_manifest(raw, name=name, profile=profile, root_ids=root_ids,
            member_id=member_id, source_kind="backup", source_id=source_id + ":manifest:" + name,
            unreadable=unreadable, limits=limits))
    return _merge(scans)


@dataclass
class BackupCapture:
    scan: inputs.InputScan
    checkpoint_graphs: tuple
    semantic_bindings: tuple
    rawbindings: dict
    # Private exclusions, not ownership registrations or byte-hold references.
    # Includes foreign source databases and destinations discovered in the
    # shared DATA catalog; never serialize these paths in a public preview.
    protected_paths: tuple[Path, ...]
    # Private main-image byte accounting, not repeated hashing I/O volume.
    image_bytes: int
    _profile: lifecycle.StorageProfile
    _limits: BackupLimits
    _check: object
    _files: tuple
    _read_control: _Budget
    _proof_captures: tuple = ()

    @property
    def inputs(self):
        return self.scan

    def verify_source(self):
        self._check()
        lifecycle.require_guard(self._profile)
        if self.rawbindings["discovery_digest"] is None:
            # An incomplete budget-limited discovery has only profile-wide
            # blockers, no successful scan/eligibility to silently certify.
            return
        try:
            current = backups.discover_locked(self._profile, read_control=self._read_control)
            if current["digest"] != self.rawbindings["discovery_digest"]:
                _changed()
            for path, observed in self._files:
                if _source_files(path, self._limits, self._check, self._read_control) != observed:
                    _changed()
            for graph in self.checkpoint_graphs:
                graph.verify_source()
            for capture in self._proof_captures:
                capture.verify_source()
        except BackupRootError:
            raise
        except (backups.BackupError, safe.SyncError, OSError, sqlite3.Error, ValueError):
            _changed()


@contextmanager
def _capture_locked(profile, *, root_ids, member_id, library_id, node_id=None, policy=None, limits=None, cache=None, check_cancelled=None):
    """Keep this context in the caller's whole-round ExitStack until final CAS.

    ``node_id`` identifies the current member only. Existing backup descriptors
    do not freeze a receiver node ID, so it is never passed as historical proof.
    Checkpoint parsing receives ``node_id=None`` and preserves that uncertainty.
    """
    guard = lifecycle.require_guard(profile)
    limits = BackupLimits() if limits is None else limits
    if not isinstance(limits, BackupLimits):
        raise ValueError("Invalid backup graph limits")
    backups._uuid(library_id)
    if node_id is not None:
        backups._uuid(node_id)
    check = check_cancelled or (lambda: None)
    budget = _Budget(limits, check)
    budget.check()
    collector = inputs._Collector(profile, root_ids, member_id, "backup", "discovery", limits.input_limits)
    for namespace in _NS:
        root = profile.media if namespace == "sync_media" else profile.checkpoints if namespace == "checkpoint" else profile.reports
        if artifacts.root_identity(root, profile=profile)["root_id"] != root_ids[namespace]:
            raise BackupRootError("BACKUP_ROOT_BINDING_MISMATCH")
    collector.binding("limits", "present", [asdict(limits)])
    try:
        discovered = backups.discover_locked(profile, read_control=budget)
    except BackupRootError as exc:
        if exc.retriable:
            raise
        collector.block(_NS, exc.code, "discovery")
        bases = {profile.database, *budget.image_paths.values(),
                 *(Path(value) for record in budget.catalog_records.values()
                   for value in (record["destination"], record["source_profile"]["database"]))}
        result = BackupCapture(collector.result(), (), (), {"discovery_digest": None, "files": ()},
            tuple(sorted({base.with_name(base.name + suffix) for base in bases
                          for suffix in ("", backups.SUFFIX, "-wal", "-shm", "-journal")}, key=backups._key)),
            sum(budget.image_sizes.values()), profile, limits, check, (), budget)
        yield result
        guard.verify()
        return
    budget.check()
    sources, graphs, proof_captures, observations, semantics = [], [], [], [], []
    records = {}
    protected_bases = {backups._key(profile.database): profile.database}
    protected_bases.update({backups._key(found["path"]): Path(found["path"]) for found in discovered["backups"]})
    try:
        for record in budget.catalog_records.values():
            # Include even foreign records in raw stability; their own member
            # parses their references under its exact producer profile.
            protected_bases[backups._key(record["destination"])] = Path(record["destination"])
            source_database = Path(record["source_profile"]["database"])
            protected_bases[backups._key(source_database)] = source_database
            if record["source_profile"] == backups._profile_json(profile):
                records[backups._key(record["destination"])] = record
    except (backups.BackupError, safe.SyncError, OSError):
        collector.block(_NS, "BACKUP_CATALOG_UNREADABLE", "catalog")
    if len(discovered["backups"]) > limits.max_backups:
        collector.block(_NS, "BACKUP_COUNT_LIMIT", "discovery")
    with ExitStack() as stack:
        for index, found in enumerate(discovered["backups"][:limits.max_backups]):
            try:
                budget.check()
            except BackupRootError as exc:
                collector.block(_NS, exc.code, ["remaining", index])
                break
            path = Path(found["path"])
            record = records.get(backups._key(path))
            source_id = record["backup_id"] if record else _hash(["legacy", backups._key(path)])
            local = inputs._Collector(profile, root_ids, member_id, "backup", source_id, limits.input_limits)
            observed = _source_files(path, limits, check, budget)
            observations.append((path, observed))
            state = found["state"]
            incomplete = state in ("reserved", "retiring") or "database_file" not in found
            if state == "retired":
                for ns, reasons in found["blockers"].items():
                    for reason in reasons:
                        local.block((ns,), _code(reason), "retired")
                semantics.append({"source_id": source_id, "state": state, "blocked": bool(local.blockers)})
                sources.append(local.result())
                continue
            companions = any(observed[suffix] not in (None, {"unreadable": True})
                             and observed[suffix]["byte_size"] > 0 for suffix in ("-wal", "-journal"))
            if companions or any(observed[suffix] == {"unreadable": True} for suffix in ("-wal", "-journal")):
                incomplete = True
                local.block(_NS, "BACKUP_UNMATERIALIZED_COMPANION", "companions")
            if incomplete:
                descriptor = record.get("descriptor") if record else None
                _known_dependencies(local, (descriptor or {}).get("dependencies", found.get("dependencies", [])))
                for ns, reasons in found["blockers"].items():
                    for reason in reasons:
                        local.block((ns,), _code(reason), "incomplete")
                if not local.blockers:
                    local.block(_NS, "BACKUP_IMAGE_UNPROVEN", "incomplete")
                if descriptor:
                    sources.append(_manifest_inputs(descriptor["manifests"], profile=profile, root_ids=root_ids,
                        member_id=member_id, source_id=source_id, limits=limits.input_limits))
                semantics.append({"source_id": source_id, "state": state, "complete": False})
                sources.append(local.result())
                continue
            producer_matches = True
            try:
                budget.check()
                if observed[""] != found["database_file"]:
                    _changed()
                with safe._Directory(path.parent, base=path.parent) as directory:
                    with directory.open_file(path.name) as handle:
                        info = os.fstat(handle.fileno())
                        if (str(info.st_dev), str(info.st_ino), info.st_size, str(info.st_mtime_ns)) != tuple(
                                found["database_file"][k] for k in ("device", "inode", "byte_size", "mtime_ns")):
                            _changed()
                        # Linux's private held fd makes replacement-path races
                        # irrelevant. Windows's ordinary safe handle denies writes
                        # and deletion until this image has finished parsing.
                        sqlite_path = Path(f"/proc/self/fd/{handle.fileno()}") if os.name == "posix" and Path("/proc/self/fd").is_dir() else path
                        con = _open_immutable(sqlite_path, limits.input_limits)
                        try:
                            schema, image_library = _read_meta(con, limits.input_limits)
                            if image_library is not None and image_library != library_id:
                                producer_matches = False
                                local.block(_NS, "BACKUP_PRODUCER_IDENTITY_UNKNOWN", "image")
                                raise inputs._Invalid("BACKUP_PRODUCER_IDENTITY_UNKNOWN")
                            if schema >= 7:
                                projection = inputs.parse_image(con, profile=profile, root_ids=root_ids, member_id=member_id,
                                    source_kind="backup", source_id=source_id, limits=limits.input_limits)
                            else:
                                projection = _legacy_inputs(con, profile=profile, root_ids=root_ids, member_id=member_id,
                                    source_id=source_id, version=schema, limits=limits.input_limits)
                            sources.append(projection)
                            if schema >= 16:
                                receipt_reader = inputs._Reader(con, limits.input_limits)
                                receipt_reader.provenance17 = schema == 17
                                con.set_authorizer(receipt_reader.authorize)
                                home_rows = home_recovery_roots.normalize_projection(receipt_reader.table(home_recovery_roots.TABLE),
                                    schema_version=schema)
                                provenance = None
                                if schema == 17:
                                    public = inputs.read_provenance_projection17(con, check_cancelled=check)
                                    provenance = {row["operation_id"]: row["result_sha256"] for row in public[home_recovery_roots.TABLE]}
                                proof_roots = stack.enter_context(home_recovery_roots.capture_locked(home_rows, profile=profile,
                                    root_ids=root_ids, member_id=member_id, library_id=library_id,
                                    source_kind="backup", source_id=source_id, check_cancelled=check,
                                    schema_version=schema, provenance=provenance))
                                proof_captures.append(proof_roots)
                                sources.append(proof_roots.inputs)
                            sources.append(_manifest_inputs(found.get("frozen_manifests"), profile=profile, root_ids=root_ids,
                                member_id=member_id, source_id=source_id, limits=limits.input_limits))
                            if schema >= 7:
                                from . import storage_checkpoint_roots as checkpoints
                                graph = stack.enter_context(checkpoints.capture_image_locked(con, profile=profile,
                                    root_ids=root_ids, member_id=member_id, library_id=image_library,
                                    node_id=None, policy=policy, source_kind="backup", source_id=source_id,
                                    live_retention=False, cache=cache, check_cancelled=check))
                                graphs.append(graph)
                                sources.append(graph.inputs)
                            # These are re-read projections, not unconditional
                            # descriptor dependency copies. In particular only R05
                            # decides each original-image checkpoint retirement edge.
                            for ns, reasons in found["blockers"].items():
                                for reason in reasons:
                                    if reason not in _DERIVED_REASONS:
                                        local.block((ns,), _code(reason), "discovery")
                            if image_library is None and record is None:
                                local.block(("sync_media", "sync_report"), "BACKUP_PRODUCER_IDENTITY_UNKNOWN", "legacy")
                            semantics.append({"source_id": source_id, "state": state, "schema": schema,
                                              "library_id": image_library, "projection_sha256": projection.digest})
                        finally:
                            con.close()
                        directory.verify()
                if _source_files(path, limits, check, budget) != observed:
                    _changed()
            except BackupRootError as exc:
                if exc.retriable:
                    raise
                local.block(_NS, exc.code, "image")
            except (inputs._Invalid, backups.BackupError, safe.SyncError, OSError, sqlite3.Error, ValueError, TypeError) as exc:
                if getattr(exc, "retriable", False):
                    _changed()
                local.block(_NS, "BACKUP_IMAGE_UNPROVEN", "image")
                if producer_matches:
                    _known_dependencies(local, (record or {}).get("descriptor", {}).get("dependencies", found.get("dependencies", [])))
            sources.append(local.result())
        # Aggregate discovery reasons that do not originate in individual images.
        image_reasons = {(ns, reason) for found in discovered["backups"] for ns, reasons in found["blockers"].items() for reason in reasons}
        for ns, reasons in discovered["blockers"].items():
            for reason in reasons:
                if (ns, reason) not in image_reasons:
                    collector.block((ns,), _code(reason), "discovery")
        sources.append(collector.result())
        result = BackupCapture(_merge(sources), tuple(graphs), tuple(semantics),
            {"discovery_digest": discovered["digest"], "files": tuple((str(p), token) for p, token in observations)},
            tuple(sorted({base.with_name(base.name + suffix) for base in protected_bases.values()
                          for suffix in ("", backups.SUFFIX, "-wal", "-shm", "-journal")}, key=backups._key)),
            sum(token[""]["byte_size"] for _path, token in observations
                if type(token[""]) is dict and "byte_size" in token[""]),
            profile, limits, check, tuple(observations), budget, tuple(proof_captures))
        result.verify_source()
        guard.verify()
        # Caller cancellation/business errors are primary; do not replace one
        # with an unrelated verification failure while unwinding the context.
        yield result
        result.verify_source()
        guard.verify()


def scan_locked(profile, **kwargs):
    """Convenience for isolated inspection; group previews must keep capture open."""
    with capture_locked(profile, **kwargs) as capture:
        return capture


@contextmanager
def capture_locked(profile, *, root_ids, member_id, library_id, node_id=None, policy=None, limits=None, cache=None, check_cancelled=None):
    """Capture one producer's backup graph; preserve caller cancellation identity.

    Keep the yielded capture in the group preview's ExitStack through its final
    source verification. No external backup is turned into a guarded profile.
    """
    def check():
        if check_cancelled is not None:
            try:
                check_cancelled()
            except BaseException as exc:
                raise _CallerCancelled(exc) from None
    try:
        with _capture_locked(profile, root_ids=root_ids, member_id=member_id, library_id=library_id,
                             node_id=node_id, policy=policy, limits=limits, cache=cache, check_cancelled=check) as capture:
            yield capture
    except _CallerCancelled as exc:
        raise exc.original
