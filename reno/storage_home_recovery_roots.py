"""Closed Home recovery proof edges; no private receipt bodies or retirement.

Knowledge/state writers own the receipts. This reader captures their fixed root
projection and its actual registered images, manifests and original outboxes.
Receipt presence never grants permission to retire one of these references.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from dataclasses import asdict, dataclass
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

from . import home_recovery_schema as schema, storage_backups as backups, home_preservation_contract17 as contract17
from . import storage_lifecycle as lifecycle, sync_state


TABLE = schema.TABLE
COLUMNS = ("operation_id", "library_id", "node_id", "source_kind", "source_id", "source_sha256",
    "source_binding_sha256", "protection_backup_id", "protection_descriptor_sha256",
    "protection_image_sha256", "checkpoint_json", "checkpoint_sha256")
MAX_PROJECTION_BYTES = 16 * 1024 * 1024
_CHECKPOINT = frozenset("state_present absence_sha256 state_sha256 state_bytes canonical_state_sha256 "
    "state_format_version source_execution_sha256 source_execution_lineage outboxes".split())


class HomeRecoveryRootError(ValueError):
    def __init__(self, code="HOME_RECOVERY_ROOT_INVALID"):
        self.code = code
        super().__init__(code)


class _CallerCancelled(BaseException):
    def __init__(self, error):
        self.error = error


def _fail(code="HOME_RECOVERY_ROOT_INVALID"):
    raise HomeRecoveryRootError(code) from None


def _sha(value):
    if type(value) is not str or not backups._SHA.fullmatch(value):
        _fail()


def _integer(value, minimum=0, maximum=2**63 - 1):
    if type(value) is not int or not minimum <= value <= maximum:
        _fail()


def _checkpoint(raw, expected):
    value = schema._decode(raw, schema.MAX_CHECKPOINT_BYTES)
    if set(value) != _CHECKPOINT or hashlib.sha256(raw.encode("utf-8")).hexdigest() != expected:
        _fail()
    if type(value["state_present"]) is not bool:
        _fail()
    _integer(value["state_bytes"], maximum=sync_state.MAX_STATE_BYTES)
    if not value["state_present"]:
        if (value != {"state_present": False,
                "absence_sha256": hashlib.sha256(b'{"format_version":1,"state":"absent"}').hexdigest(),
                "state_sha256": None, "state_bytes": 0, "canonical_state_sha256": None,
                "state_format_version": None, "source_execution_sha256": None,
                "source_execution_lineage": None, "outboxes": []}):
            _fail()
        return value
    if value["absence_sha256"] is not None or value["state_bytes"] < 2:
        _fail()
    for key in ("state_sha256", "canonical_state_sha256"):
        _sha(value[key])
    _integer(value["state_format_version"], 1, 4)
    lineage = value["source_execution_lineage"]
    if lineage is None:
        if value["source_execution_sha256"] is not None:
            _fail()
    else:
        _sha(value["source_execution_sha256"])
        if type(lineage) is not dict or set(lineage) != {"root_job_id", "continuation_id", "execution_generation"}:
            _fail()
        backups._uuid(lineage["continuation_id"])
        for key in ("root_job_id", "execution_generation"):
            _integer(lineage[key], 1)
    outboxes = value["outboxes"]
    if type(outboxes) is not list or len(outboxes) > 2:
        _fail()
    seen = set()
    for item in outboxes:
        if type(item) is not dict or set(item) != {"publication_id", "sha256", "byte_size", "semantic_sha256"}:
            _fail()
        backups._uuid(item["publication_id"])
        if item["publication_id"] in seen:
            _fail()
        seen.add(item["publication_id"])
        for key in ("sha256", "semantic_sha256"):
            _sha(item[key])
        _integer(item["byte_size"], 2, sync_state.MAX_OUTBOX_BYTES)
    return value


def _projected_checkpoint(row, schema_version):
    """Select the new closed format only for an actual17 image projection."""
    if type(schema_version) is not int or not 1 <= schema_version <= 17:
        _fail()
    raw, expected = row["checkpoint_json"], row["checkpoint_sha256"]
    value = schema._decode(raw, schema.MAX_CHECKPOINT_BYTES)
    if set(value) == _CHECKPOINT:
        return _checkpoint(raw, expected)
    if schema_version != 17 or row["source_kind"] != "home_checkpoint":
        _fail()
    wrapper = contract17.normalize_checkpoint(raw, expected_sha256=expected)
    if (wrapper["bundle"]["bundle_id"] != str(uuid.uuid5(uuid.UUID(row["operation_id"]),
            "reno.home.preservation.bundle.v1"))
            or row["protection_backup_id"] != str(uuid.uuid5(uuid.UUID(row["operation_id"]),
                "reno.home.preservation.backup.v1"))):
        _fail()
    binding = wrapper["permanent_witnesses"]["home_authority"]["binding"]
    if binding is not None and (binding["home_library_id"], binding["home_node_id"]) != (row["library_id"], row["node_id"]):
        _fail()
    return wrapper["checkpoint"]


def normalize_projection(rows, *, schema_version=16):
    """Copy only the twelve public root columns; never read frontier/paid JSON."""
    try:
        if type(rows) is not list or len(rows) > backups.MAX_ROWS:
            _fail()
        copied, seen, total = [], set(), 0
        for raw in rows:
            row = dict(raw)
            if set(row) != set(COLUMNS) or row["source_kind"] not in schema.SOURCE_KINDS:
                _fail()
            total += sum(len(value.encode("utf-8")) for value in row.values() if type(value) is str)
            if total > MAX_PROJECTION_BYTES:
                _fail("HOME_RECOVERY_ROOT_LIMIT")
            for key in ("operation_id", "library_id", "node_id", "source_id", "protection_backup_id"):
                backups._uuid(row[key])
            if row["operation_id"] in seen:
                _fail()
            seen.add(row["operation_id"])
            for key in (key for key in COLUMNS if key.endswith("sha256")):
                _sha(row[key])
            _projected_checkpoint(row, schema_version)
            copied.append(row)
        return sorted(copied, key=lambda row: row["operation_id"])
    except HomeRecoveryRootError:
        raise
    except (ValueError, TypeError, KeyError, AttributeError, UnicodeError, RecursionError):
        _fail()


def backup_ids(rows, *, schema_version=16):
    values = set()
    for row in normalize_projection(rows, schema_version=schema_version):
        values.add(row["protection_backup_id"])
        values.add(row["source_id"] if row["source_kind"] == "registered_backup" else
            str(uuid.uuid5(uuid.UUID(row["source_id"]), "reno.home.knowledge.checkpoint.v1")))
    return frozenset(values)


def read_projection(con, *, maximum_rows=100_000):
    """For protected immutable/committed images with a caller-owned read policy."""
    if type(maximum_rows) is not int or not 1 <= maximum_rows <= backups.MAX_ROWS:
        _fail("HOME_RECOVERY_ROOT_LIMIT")
    found = con.execute("SELECT type,sql FROM main.sqlite_master WHERE name=? COLLATE NOCASE LIMIT 2", (TABLE,)).fetchall()
    if (len(found) != 1 or found[0][0] != "table" or type(found[0][1]) is not str
            or not found[0][1].upper().lstrip().startswith("CREATE TABLE")):
        _fail()
    columns = {row[1]: row for row in con.execute('PRAGMA main.table_xinfo("' + TABLE + '")')}
    if any(key not in columns or columns[key][2].upper() != "TEXT" or columns[key][6] for key in COLUMNS):
        _fail()
    rows, total = [], 0
    for raw in con.execute('SELECT ' + ','.join('"' + key + '"' for key in COLUMNS) + ' FROM main."' + TABLE + '" LIMIT ?',
                           (maximum_rows + 1,)):
        if len(rows) >= maximum_rows:
            _fail("HOME_RECOVERY_ROOT_LIMIT")
        total += sum(len(value.encode("utf-8")) for value in raw if type(value) is str)
        if total > MAX_PROJECTION_BYTES:
            _fail("HOME_RECOVERY_ROOT_LIMIT")
        rows.append(dict(zip(COLUMNS, raw)))
    version = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
    if version is None or type(version[0]) is not str or version[0] not in {str(n) for n in range(1, 18)}:
        _fail()
    return normalize_projection(rows, schema_version=int(version[0]))


def retention_holds_locked(profile, records, selected, *, operation_holds=None):
    """Exact reverse references from current and remaining committed images.

    The caller already holds EX. A selected image that itself must remain is
    included in the transitive closure before another candidate can retire.
    Unreadable remaining proof images cannot be treated as containing no edges.
    """
    from . import storage_readonly as readonly
    from . import storage_home_recovery_operation_holds17 as operations
    lifecycle.require_guard(profile)
    if operation_holds is None:
        with operations.capture_operation_holds_locked(profile, records) as captured:
            return retention_holds_locked(profile, records, selected, operation_holds=captured)
    if not isinstance(operation_holds, operations.OperationHoldCapture):
        _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
    operation_holds.verify_source()
    if operation_holds.unknown:
        _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
    selected = set(selected)

    def edges(con):
        row = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()
        if row is None or type(row[0]) is not str or row[0] not in {str(value) for value in range(18)}:
            _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
        if int(row[0]) < 16:
            if con.execute("SELECT 1 FROM main.sqlite_master WHERE name=? COLLATE NOCASE", (TABLE,)).fetchone():
                _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
            return frozenset()
        from . import storage_root_inputs as inputs
        def authorize(action, first, second, database, origin):
            # Retention owns this private image reader. Permit only the
            # continuity adapter's fixed public fields and reserved-name
            # metadata checks, retaining its explicit private-column DENY.
            if action == sqlite3.SQLITE_READ and type(first) is str and first.casefold() == TABLE:
                return (sqlite3.SQLITE_OK if database == "main" and origin is None
                        and second in COLUMNS else sqlite3.SQLITE_DENY)
            allowed = inputs.continuity_authorize(action, first, second, database, origin)
            if allowed is not None:
                return allowed
            return readonly._readonly_authorizer(action, first, second, database, origin)
        con.set_authorizer(authorize)
        if int(row[0]) >= 17:
            inputs.read_continuity_projection(con)
        elif inputs.continuity_reserved_projection(con):
            _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
        return backup_ids(read_projection(con), schema_version=int(row[0]))

    with readonly.committed_image_locked(profile) as current:
        protected = set(operation_holds.backup_ids)
        protected.update(edges(current.connection))
        current.verify_source()
    references, unknown = {}, set()
    for backup_id, record in records.items():
        if record["state"] not in ("ready", "retiring"):
            continue
        try:
            descriptor, _ = backups._verify_ready(record, profile)
            path = Path(record["destination"])
            image = sqlite3.connect(path.as_uri() + "?mode=ro&immutable=1", uri=True)
            try:
                image.execute("PRAGMA query_only=ON")
                image.execute("PRAGMA trusted_schema=OFF")
                image.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, schema.MAX_CHECKPOINT_BYTES * 4)
                references[backup_id] = edges(image)
            finally:
                image.close()
            if backups._read(path, maximum=backups.MAX_IMAGE, content=False)[1] != descriptor["database_file"]:
                _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
        except (ValueError, OSError, sqlite3.Error, KeyError, TypeError):
            unknown.add(backup_id)
    remaining = {backup_id for backup_id, record in records.items()
                 if record["state"] in ("ready", "retiring") and backup_id not in selected}
    while True:
        if remaining & unknown:
            _fail("HOME_RECOVERY_RETENTION_UNKNOWN")
        before = set(protected)
        before_remaining = set(remaining)
        for backup_id in remaining:
            protected.update(references.get(backup_id, ()))
        remaining.update(selected & protected)
        if before == protected and before_remaining == remaining:
            operation_holds.verify_source()
            return frozenset(protected)


@dataclass
class RecoveryRootCapture:
    inputs: object
    _pins: object
    _guard: object
    _check: object

    def verify_source(self):
        try:
            self._check()
            self._guard.verify()
            self._pins.verify()
        except _CallerCancelled as exc:
            error = exc.error
            while isinstance(error, _CallerCancelled):
                error = error.error
            raise error from None


@contextmanager
def _capture_locked(rows, *, profile, root_ids, member_id, library_id, node_id=None,
                   source_kind="live", source_id="live", check_cancelled=None):
    """Retain exact proof files through the caller's final group CAS check."""
    from . import storage_root_inputs as inputs, home_recovery_source as sources, storage_home_roots as home
    from .sync_recovery import _Pins
    check = check_cancelled or (lambda: None)
    guard = lifecycle.require_guard(profile)
    collector = inputs._Collector(profile, root_ids, member_id, source_kind, source_id, inputs.InputLimits())
    class ProofPins(_Pins):
        def _read(self, stream, size, *, capture=False):
            os.lseek(stream.fileno(), 0, os.SEEK_SET)
            digest, chunks, total = hashlib.sha256(), [], 0
            while total <= size:
                check()
                chunk = os.read(stream.fileno(), min(1024 * 1024, size + 1 - total))
                if not chunk:
                    break
                total += len(chunk)
                digest.update(chunk)
                if capture:
                    chunks.append(chunk)
            return digest.hexdigest(), total, b"".join(chunks) if capture else None
    with ExitStack() as stack:
        pins = ProofPins(stack)

        def pin(path, maximum, expected_sha=None):
            check()
            _, identity = backups._read(path, maximum=maximum, content=False)
            if expected_sha is not None and identity["sha256"] != expected_sha:
                _fail("HOME_RECOVERY_PROOF_CHANGED")
            return pins.add(path.parent, path.name, identity["sha256"], identity["byte_size"], maximum, capture=True)

        def image(backup_id, image_sha, descriptor_sha=None):
            catalog = backups._catalog(profile) / (backup_id + ".json")
            raw = pin(catalog, backups.MAX_JSON)
            record, _ = backups._record(catalog)
            if (record["state"] != "ready" or record["source_profile"] != backups._profile_json(profile)
                    or backups._decode(raw) != record):
                _fail("HOME_RECOVERY_PROOF_UNPROVEN")
            descriptor, sha = backups._verify_ready(record, profile)
            if descriptor_sha is not None and sha != descriptor_sha or descriptor["database_file"]["sha256"] != image_sha:
                _fail("HOME_RECOVERY_PROOF_CHANGED")
            if descriptor["source_version"]["library_id"] != library_id:
                _fail("HOME_RECOVERY_PROOF_IDENTITY_UNKNOWN")
            destination = Path(record["destination"])
            pin(destination.with_name(destination.name + backups.SUFFIX), backups.MAX_JSON, sha)
            # Images are hashed/pinned without copying their private body into
            # Python; the separate backup graph parses the closed SQL roots.
            _, identity = backups._read(destination, maximum=backups.MAX_IMAGE, content=False)
            if identity != descriptor["database_file"]:
                _fail("HOME_RECOVERY_PROOF_CHANGED")
            pins.add(destination.parent, destination.name, image_sha, identity["byte_size"], backups.MAX_IMAGE)
            return descriptor

        try:
            normalized = normalize_projection(rows)
            collector.binding(TABLE, "present", normalized)
        except (ValueError, TypeError, KeyError, sqlite3.Error):
            collector.block(inputs.NAMESPACES, "HOME_RECOVERY_ROOT_INVALID", TABLE)
            normalized = []
        for row in normalized:
            check()
            checkpoint = _checkpoint(row["checkpoint_json"], row["checkpoint_sha256"])
            locator = row["operation_id"]
            if row["library_id"] != library_id or node_id is not None and row["node_id"] != node_id:
                collector.block(inputs.NAMESPACES, "HOME_RECOVERY_PROOF_IDENTITY_UNKNOWN", locator)
                continue
            try:
                image(row["protection_backup_id"], row["protection_image_sha256"], row["protection_descriptor_sha256"])
                if row["source_kind"] == "registered_backup":
                    image(row["source_id"], row["source_sha256"], row["source_binding_sha256"])
                else:
                    source_path = sources._source_path(profile, row["source_id"])
                    raw = pin(source_path, sources.MAX_DECLARATION_BYTES, row["source_binding_sha256"])
                    declaration = sources._declaration(raw)
                    intent_raw = pin(source_path.with_name(row["source_id"] + ".intent.json"), sources.MAX_DECLARATION_BYTES)
                    intent = sources._intent(intent_raw)
                    if (any(declaration[key] != intent[key] for key in sources._INTENT_FIELDS - {"action"})
                            or any(declaration[key] != row[key] for key in ("source_id", "library_id", "node_id"))
                            or declaration["profile_sha256"] != sources._profile_sha(profile)
                            or declaration["image_sha256"] != row["source_sha256"]):
                        _fail("HOME_RECOVERY_PROOF_CHANGED")
                    image(declaration["backup_id"], row["source_sha256"], declaration["descriptor_sha256"])
                    for dependency in declaration["media_dependencies"]:
                        check()
                        pins.add(profile.media, dependency["storage_key"], dependency["sha256"],
                                 dependency["byte_size"], backups.MAX_IMAGE)
                collector.binding("home_recovery_images", "closed", [{key: row[key] for key in COLUMNS if key != "checkpoint_json"}])
            except (ValueError, OSError, sqlite3.Error, TypeError, KeyError):
                check()
                collector.block(inputs.NAMESPACES, "HOME_RECOVERY_PROOF_UNPROVEN", locator)
            for item in checkpoint["outboxes"]:
                try:
                    path = profile.database.parent / sync_state._OUTBOX / (item["publication_id"] + ".json")
                    raw = pin(path, sync_state.MAX_OUTBOX_BYTES, item["sha256"])
                    if len(raw) != item["byte_size"]:
                        _fail()
                    envelope = json.loads(raw)
                    if hashlib.sha256(schema.canonical_json(envelope).encode()).hexdigest() != item["semantic_sha256"]:
                        _fail()
                    envelope = home._outbox(envelope, path.name, {"library_id": row["library_id"], "node_id": row["node_id"]})
                    for video in envelope["snapshot"]["videos"]:
                        media = video["media"]
                        collector.reference("sync_media", f"sync/{media['video_id']}/{media['sha256']}.{media['extension']}",
                            [locator, item["publication_id"]], media["sha256"], media["byte_size"])
                    collector.binding("home_recovery_outbox", "closed", [item])
                except (ValueError, OSError, TypeError, KeyError, UnicodeError, RecursionError):
                    check()
                    collector.block(("sync_media", "sync_report"), "HOME_RECOVERY_OUTBOX_UNPROVEN", [locator, item["publication_id"]])
        capture = RecoveryRootCapture(collector.result(), pins, guard, check)
        capture.verify_source()
        yield capture
        capture.verify_source()


@contextmanager
def capture_locked(rows, *, profile, root_ids, member_id, library_id, node_id=None,
                   source_kind="live", source_id="live", check_cancelled=None,
                   schema_version=16, provenance=None):
    """Keep caller cancellation separate from malformed proof evidence."""
    if check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid read cancellation callback")
    if type(schema_version) is not int or not 1 <= schema_version <= 17:
        raise ValueError("Invalid read schema version")
    def check():
        if check_cancelled is not None:
            try:
                check_cancelled()
            except BaseException as exc:
                raise _CallerCancelled(exc) from None
    try:
        if schema_version == 17:
            from . import storage_home_recovery_dependency17 as dependency
            from . import storage_root_inputs as inputs
            try:
                normalized = normalize_projection(rows, schema_version=17)
            except (ValueError, TypeError, KeyError):
                normalized = None
            if normalized is not None:
                legacy_rows, new_rows = [], []
                for row in normalized:
                    value = schema._decode(row["checkpoint_json"], schema.MAX_CHECKPOINT_BYTES)
                    (legacy_rows if set(value) == _CHECKPOINT else new_rows).append(row)
                with ExitStack() as stack:
                    captures = []
                    if legacy_rows or not new_rows:
                        captures.append(stack.enter_context(_capture_locked(legacy_rows, profile=profile,
                            root_ids=root_ids, member_id=member_id, library_id=library_id, node_id=node_id,
                            source_kind=source_kind, source_id=source_id, check_cancelled=check)))
                    if new_rows:
                        captures.append(stack.enter_context(dependency.capture_locked(new_rows, profile=profile,
                            root_ids=root_ids, member_id=member_id, library_id=library_id, node_id=node_id,
                            source_kind=source_kind, source_id=source_id, schema_version=17,
                            provenance=provenance, check_cancelled=check)))
                    class CombinedPins:
                        def verify(self):
                            for captured in captures:
                                captured.verify_source()
                    sections = ("references", "selectors", "blockers", "source_bindings")
                    merged = inputs.InputScan(**{name: tuple(sorted({item for captured in captures
                        for item in getattr(captured.inputs, name)},
                        key=lambda item: inputs.canonical_bytes(asdict(item)))) for name in sections})
                    combined = RecoveryRootCapture(merged, CombinedPins(), lifecycle.require_guard(profile), check)
                    combined.verify_source()
                    yield combined
                    combined.verify_source()
                return
        with _capture_locked(rows, profile=profile, root_ids=root_ids, member_id=member_id,
                library_id=library_id, node_id=node_id, source_kind=source_kind,
                source_id=source_id, check_cancelled=check) as capture:
            yield capture
    except _CallerCancelled as exc:
        raise exc.error
