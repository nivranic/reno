"""Actual17 immutable Home knowledge sources, with original SQL provenance.

This image supplies only the closed knowledge projection. It is not a ticket
to restore State, execution, admission or permanent external witness facts.
The current Home restore operation protects those facts with its own bundle.
Legacy declarations and unfinished legacy images retain their original bytes.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
import uuid

from . import config, db, home_recovery_source as old
from . import source_preservation_bundle as bundle, source_preservation_witness as witness
from . import storage_backups as backups, storage_lifecycle as lifecycle
from . import sync_media as safe, sync_snapshot, sync_state, tasks
from .sync_recovery import _Pins as _LegacyPins

DIRECTORY = old.DIRECTORY
MAX_DECLARATION_BYTES = old.MAX_DECLARATION_BYTES
MAX_ROWS = old.MAX_ROWS
MAX_CELL_BYTES = old.MAX_CELL_BYTES
MAX_KNOWLEDGE_BYTES = old.MAX_KNOWLEDGE_BYTES
MAX_SQL_STEPS = old.MAX_SQL_STEPS
MAX_SECONDS = old.MAX_SECONDS
HomeRecoverySourceError = old.HomeRecoverySourceError
_fail, _json, _sha = old._fail, old._json, old._sha
_FIELDS, _INTENT_FIELDS = old._FIELDS, old._INTENT_FIELDS
_COMPANION_FIELDS = frozenset("format_version action source_id intent_sha256 profile_sha256 "
    "library_id node_id source_version schema_version sql_image_sha256 created_at".split())


def _declaration(raw):
    """An independent exact17 parser; the old parser remains closed at16."""
    try:
        value = backups._decode(raw)
        if (set(value) != _FIELDS or type(value["format_version"]) is not int
                or value["format_version"] != 1 or value["source_kind"] != "home_checkpoint"):
            _fail("SOURCE_INVALID")
        for key in ("source_id", "library_id", "node_id", "backup_id"):
            backups._uuid(value[key])
        for key in ("profile_sha256", "descriptor_sha256", "image_sha256", "knowledge_sha256"):
            if type(value[key]) is not str or not backups._SHA.fullmatch(value[key]):
                _fail("SOURCE_INVALID")
        sync_snapshot._version(value["source_version"])
        if value["source_version"]["library_id"] != value["library_id"]:
            _fail("SOURCE_INVALID")
        if type(value["schema_version"]) is not int or value["schema_version"] != 17:
            _fail("SOURCE_SCHEMA_UNSUPPORTED")
        if type(value["created_at"]) is not str or not backups._TIME.fullmatch(value["created_at"]):
            _fail("SOURCE_INVALID")
        dependencies, keys = value["media_dependencies"], set()
        if type(dependencies) is not list or len(dependencies) > MAX_ROWS:
            _fail("SOURCE_INVALID")
        for item in dependencies:
            if (type(item) is not dict or set(item) != {"storage_key", "sha256", "byte_size"}
                    or type(item["storage_key"]) is not str or not item["storage_key"]
                    or Path(item["storage_key"]).is_absolute() or "\\" in item["storage_key"]
                    or ":" in item["storage_key"] or ".." in Path(item["storage_key"]).parts
                    or type(item["sha256"]) is not str or not backups._SHA.fullmatch(item["sha256"])
                    or type(item["byte_size"]) is not int or not 0 <= item["byte_size"] <= backups.MAX_IMAGE
                    or item["storage_key"] in keys):
                _fail("SOURCE_INVALID")
            keys.add(item["storage_key"])
        return value
    except HomeRecoverySourceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail("SOURCE_INVALID")


def _intent(raw):
    try:
        value = backups._decode(raw)
        if set(value) != _INTENT_FIELDS or value["action"] != "produce_home_checkpoint":
            _fail("SOURCE_PROVENANCE_INVALID")
        common = {key: item for key, item in value.items() if key != "action"}
        _declaration(_json({**common, "source_kind": "home_checkpoint",
            "descriptor_sha256": "0" * 64, "image_sha256": "0" * 64}))
        if value["backup_id"] != _backup_id(value["source_id"]):
            _fail("SOURCE_PROVENANCE_INVALID")
        return value
    except HomeRecoverySourceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail("SOURCE_PROVENANCE_INVALID")


def _companion(raw, intent, intent_sha):
    try:
        value = backups._decode(raw)
        if (set(value) != _COMPANION_FIELDS or type(value["format_version"]) is not int
                or value["format_version"] != 1 or value["action"] != "produce_home_checkpoint17"
                or value["intent_sha256"] != intent_sha
                or any(value[key] != intent[key] for key in ("source_id", "profile_sha256", "library_id",
                    "node_id", "source_version", "schema_version", "created_at"))
                or type(value["schema_version"]) is not int or value["schema_version"] != 17
                or type(value["sql_image_sha256"]) is not str
                or not backups._SHA.fullmatch(value["sql_image_sha256"])):
            _fail("SOURCE_PROVENANCE_INVALID")
        return value
    except HomeRecoverySourceError:
        raise
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError):
        _fail("SOURCE_PROVENANCE_INVALID")


def _backup_id(source_id):
    backups._uuid(source_id)
    return str(uuid.uuid5(uuid.UUID(source_id), "reno.home.knowledge.checkpoint.v1"))


def _paths(profile, source_id):
    path = old._source_path(profile, source_id)
    return (path, path.with_name(source_id + ".intent.json"), path.with_name(source_id + ".sql17.json"),
            backups._catalog(profile) / (_backup_id(source_id) + ".json"),
            profile.database.with_name(profile.database.name + ".bak-home-knowledge-" + source_id))


def _long_read(profile):
    lifecycle.require_guard(profile)
    window = witness._WINDOW.get()
    if window is not None:
        window.check()
        if (window.profile.identity != profile.identity or window._writable is not False
                or window.connection.execute("PRAGMA query_only").fetchone()[0] != 1):
            _fail("SOURCE_READ_WINDOW_REQUIRED")


class _ReadBudget:
    def __init__(self, profile, check_cancelled=None):
        self.profile, self.check_cancelled = profile, check_cancelled
        self.started, self.error = time.monotonic(), None

    def check(self):
        if self.error is not None:
            raise self.error
        try:
            if self.check_cancelled is not None:
                self.check_cancelled()
            if time.monotonic() - self.started > MAX_SECONDS:
                _fail("SOURCE_LIMIT")
            lifecycle.require_guard(self.profile).verify()
        except BaseException as exc:
            self.error = exc
            raise

    def progress(self):
        try:
            self.check()
        except BaseException:
            return 1
        return 0


@contextmanager
def _sql_budget(con, control):
    """Bound this producer-owned SQL reader without replacing its trace."""
    steps = [0]
    def progress():
        steps[0] += 1000
        if steps[0] > MAX_SQL_STEPS:
            try:
                _fail("SOURCE_LIMIT")
            except HomeRecoverySourceError as exc:
                control.error = exc
            return 1
        return control.progress()
    con.set_progress_handler(progress, 1000)
    try:
        yield
    finally:
        con.set_progress_handler(None, 0)


class _Pins(_LegacyPins):
    """Exact pins with a bounded cancellable full-read lifetime."""
    def __init__(self, stack, profile, control):
        super().__init__(stack)
        self.profile, self.control = profile, control

    def _read(self, stream, size, *, capture=False):
        _long_read(self.profile)
        self.control.check()
        os.lseek(stream.fileno(), 0, os.SEEK_SET)
        digest, chunks, total = hashlib.sha256(), [], 0
        while total <= size:
            self.control.check()
            chunk = os.read(stream.fileno(), min(128 * 1024, size + 1 - total))
            if not chunk:
                break
            total += len(chunk)
            digest.update(chunk)
            if capture:
                chunks.append(chunk)
        self.control.check()
        return digest.hexdigest(), total, b"".join(chunks) if capture else None

    def verify(self):
        _long_read(self.profile)
        self.control.check()
        super().verify()


def _exists(path, profile):
    # A malformed, unsafe or dangling existing entry never means absence.
    try:
        with safe._Directory(path.parent, base=profile.database.parent) as directory:
            try:
                directory.entry_stat(path.name)
            except FileNotFoundError:
                return False
            directory.verify()
            return True
    except FileNotFoundError:
        return False


def _artifact_exists(registry, destination, profile):
    return any(_exists(path, profile) for path in (registry, destination,
        destination.with_name(destination.name + backups.SUFFIX),
        destination.with_name(destination.name + "-wal"), destination.with_name(destination.name + "-shm"),
        destination.with_name(destination.name + "-journal")))


def _persist(path, value, pins, *, maximum=MAX_DECLARATION_BYTES):
    """Publish canonical immutable bytes, then keep their exact readback pin."""
    raw = _json(value)
    if len(raw) > maximum:
        _fail("SOURCE_LIMIT")
    pins.verify()
    if _exists(path, pins.profile):
        found, _ = old._pin_file(pins, path, maximum, capture=True)
        if found != raw:
            _fail("SOURCE_PROVENANCE_INVALID")
    else:
        with safe._Directory(path.parent, base=pins.profile.database.parent, create=True):
            backups._write_json(path, value, replace=False)
        found, _ = old._pin_file(pins, path, maximum, capture=True)
        if found != raw:
            _fail("SOURCE_CHANGED")
    pins.verify()
    return hashlib.sha256(raw).hexdigest()


def _binding(intent, source_id, profile, actual):
    identity = actual.identity()
    if (intent["source_id"] != source_id or intent["library_id"] != identity["library_id"]
            or intent["node_id"] != identity["node_id"] or intent["profile_sha256"] != old._profile_sha(profile)
            or intent["backup_id"] != _backup_id(source_id)):
        _fail("SOURCE_IDENTITY_MISMATCH")


def _identity_pin(pins, profile, actual):
    raw, _ = old._pin_file(pins, profile.database.parent / "node-identity.json",
                          MAX_DECLARATION_BYTES, capture=True)
    if backups._decode(raw) != {"instance_id": actual.identity()["node_id"]}:
        _fail("SOURCE_IDENTITY_MISMATCH")


def _archive(stack, pins, descriptor):
    """SQLite sees only a verified private copy of the held original image."""
    temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="reno-home-recovery-source17-"))
    image = Path(temporary) / "image.sqlite"
    stream = pins.files[-1][2]
    os.lseek(stream.fileno(), 0, os.SEEK_SET)
    copied, digest = 0, hashlib.sha256()
    with image.open("xb") as target:
        while True:
            pins.control.check()
            chunk = os.read(stream.fileno(), 128 * 1024)
            if not chunk:
                break
            copied += len(chunk)
            if copied > descriptor["database_file"]["byte_size"]:
                _fail("SOURCE_CHANGED")
            digest.update(chunk)
            target.write(chunk)
        target.flush()
        os.fsync(target.fileno())
    if (copied != descriptor["database_file"]["byte_size"]
            or digest.hexdigest() != descriptor["database_file"]["sha256"]):
        _fail("SOURCE_CHANGED")
    if os.name != "nt":
        image.chmod(0o600)
    archive = sqlite3.connect(image.as_uri() + "?mode=ro&immutable=1", uri=True)
    stack.callback(archive.close)
    archive.row_factory = sqlite3.Row
    archive.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_CELL_BYTES)
    archive.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, MAX_DECLARATION_BYTES)
    archive.execute("PRAGMA query_only=ON")
    archive.execute("PRAGMA trusted_schema=OFF")
    steps = [0]
    def progress():
        steps[0] += 1000
        if steps[0] > MAX_SQL_STEPS:
            try:
                _fail("SOURCE_LIMIT")
            except HomeRecoverySourceError as exc:
                pins.control.error = exc
            return 1
        return pins.control.progress()
    archive.set_progress_handler(progress, 1000)
    return archive


def _image(stack, pins, intent, *, companion=None, allow_reserved=False):
    profile = pins.profile
    _, _, _, registry, destination = _paths(profile, intent["source_id"])
    record, record_identity = backups._record(registry)
    if (record["backup_id"] != intent["backup_id"] or record["destination"] != backups._key(destination)
            or record["kind"] != "maintenance" or record["source_profile"] != backups._profile_json(profile)):
        _fail("SOURCE_PROVENANCE_INVALID")
    if record["state"] not in ({"ready", "reserved"} if allow_reserved else {"ready"}):
        _fail("SOURCE_NOT_READY")
    if record["state"] == "ready":
        old._pin_file(pins, registry, backups.MAX_JSON, expected=record_identity)
    try:
        descriptor, descriptor_sha = backups._verify_ready(record, profile)
    except FileNotFoundError:
        _fail("SOURCE_NOT_READY")
    if (descriptor["source_version"] != intent["source_version"]
            or descriptor["schema_version"] != intent["schema_version"]):
        _fail("SOURCE_CHANGED")
    _, sidecar_identity = old._pin_file(pins, destination.with_name(destination.name + backups.SUFFIX),
                                      backups.MAX_JSON)
    if sidecar_identity["sha256"] != descriptor_sha:
        _fail("SOURCE_CHANGED")
    old._pin_file(pins, destination, backups.MAX_IMAGE, expected=descriptor["database_file"])
    archive = _archive(stack, pins, descriptor)
    schema = archive.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
    if schema is None or schema[0] != str(intent["schema_version"]):
        _fail("SOURCE_SCHEMA_UNSUPPORTED")
    if db.knowledge_version(archive) != intent["source_version"]:
        _fail("SOURCE_CHANGED")
    if companion is not None and bundle._sql_digest17(archive) != companion["sql_image_sha256"]:
        _fail("SOURCE_CHANGED")
    archive.set_authorizer(old._authorize_knowledge)
    rows = old._read_knowledge(archive)
    if _sha(rows) != intent["knowledge_sha256"]:
        _fail("SOURCE_CHANGED")
    if old._pin_media(pins, rows, profile) != intent["media_dependencies"]:
        _fail("SOURCE_MEDIA_CHANGED")
    pins.verify()
    if backups._record(registry) != (record, record_identity):
        _fail("SOURCE_CHANGED")
    if record["state"] == "reserved":
        # The original sidecar/image has already passed every original intent
        # comparison. This is a catalog transition, never a new image copy.
        ready = {**record, "state": "ready", "descriptor_sha256": descriptor_sha, "descriptor": descriptor}
        backups._write_json(registry, ready, replace=True)
        record = ready
    old._pin_file(pins, registry, backups.MAX_JSON)
    if backups._record(registry)[0] != record:
        _fail("SOURCE_CHANGED")
    return descriptor, descriptor_sha, rows


@dataclass(frozen=True, slots=True)
class HomeKnowledgeSource:
    declaration: dict
    declaration_sha256: str
    rows: dict
    knowledge_sha256: str
    _pins: object
    _intent: dict
    _intent_sha256: str
    _companion: dict
    _companion_sha256: str

    def public(self):
        return {"source_kind": "home_checkpoint", "source_id": self.declaration["source_id"],
                "source_version": dict(self.declaration["source_version"]),
                "source_sha256": self.declaration["image_sha256"],
                "source_binding_sha256": self.declaration_sha256,
                "knowledge_sha256": self.knowledge_sha256,
                "row_counts": {name: len(rows) for name, rows in self.rows.items()}}

    def verify(self):
        self._pins.verify()
        if (_sha(self.rows) != self.knowledge_sha256 or _sha(self.declaration) != self.declaration_sha256
                or _sha(self._intent) != self._intent_sha256 or _sha(self._companion) != self._companion_sha256):
            _fail("SOURCE_CHANGED")


@contextmanager
def _errors(control):
    try:
        yield
    except HomeRecoverySourceError:
        raise
    except (ValueError, OSError, sqlite3.Error, KeyError, TypeError, UnicodeError, RecursionError) as exc:
        if control.error is not None:
            raise control.error
        read_control = backups._READ_CONTROL.get()
        if read_control is not None and read_control.error is exc:
            raise
        _fail("SOURCE_INVALID")


@contextmanager
def open_source(con, source_id, *, profile, actual_witness):
    """Borrow the actual sealed read window and retain every original pin."""
    actual = witness.require_read_capture(actual_witness, connection=con, profile=profile)
    profile = lifecycle._profile(profile)
    control = _ReadBudget(profile, actual._store.check_cancelled)
    with _errors(control), backups.controlled_reads(control), ExitStack() as stack:
        pins = _Pins(stack, profile, control)
        path, intent_path, companion_path, _, _ = _paths(profile, source_id)
        raw, identity = old._pin_file(pins, path, MAX_DECLARATION_BYTES, capture=True)
        declaration = _declaration(raw)
        # Immutable metadata is canonical; a hash of a DTO cannot stand in for
        # the exact persisted binding bytes.
        if raw != _json(declaration):
            _fail("SOURCE_PROVENANCE_INVALID")
        try:
            intent_raw, intent_identity = old._pin_file(pins, intent_path, MAX_DECLARATION_BYTES, capture=True)
        except FileNotFoundError:
            _fail("SOURCE_PROVENANCE_MISSING")
        intent = _intent(intent_raw)
        if (intent_raw != _json(intent)
                or any(declaration[key] != intent[key] for key in _INTENT_FIELDS - {"action"})):
            _fail("SOURCE_PROVENANCE_INVALID")
        try:
            companion_raw, companion_identity = old._pin_file(pins, companion_path,
                MAX_DECLARATION_BYTES, capture=True)
        except FileNotFoundError:
            _fail("SOURCE_PROVENANCE_MISSING")
        companion = _companion(companion_raw, intent, intent_identity["sha256"])
        if companion_raw != _json(companion):
            _fail("SOURCE_PROVENANCE_INVALID")
        _binding(intent, source_id, profile, actual)
        _identity_pin(pins, profile, actual)
        descriptor, descriptor_sha, rows = _image(stack, pins, intent, companion=companion)
        if (descriptor_sha != declaration["descriptor_sha256"]
                or descriptor["database_file"]["sha256"] != declaration["image_sha256"]):
            _fail("SOURCE_CHANGED")
        source = HomeKnowledgeSource(declaration, identity["sha256"], rows, _sha(rows), pins,
            intent, intent_identity["sha256"], companion, companion_identity["sha256"])
        source.verify()
        yield source
        source.verify()


def _current(con, pins, intent):
    if db.knowledge_version(con) != intent["source_version"]:
        _fail("SOURCE_CHANGED")
    rows = old._read_knowledge(con)
    if _sha(rows) != intent["knowledge_sha256"]:
        _fail("SOURCE_CHANGED")
    if old._pin_media(pins, rows, pins.profile) != intent["media_dependencies"]:
        _fail("SOURCE_MEDIA_CHANGED")
    return bundle._sql_digest17(con)


def _produce(con, actual, profile, source_id):
    from . import home_recovery_source_dispatch as dispatch
    witness.require_read_capture(actual, connection=con, profile=profile)
    control = _ReadBudget(profile, actual._store.check_cancelled)
    with _errors(control), backups.controlled_reads(control), _sql_budget(con, control), ExitStack() as stack:
        pins = _Pins(stack, profile, control)
        path, intent_path, companion_path, registry, destination = _paths(profile, source_id)
        if _exists(path, profile):
            # Both valid and invalid old declarations keep precedence. Never
            # rewrite this UUID or interpret its old schema as actual17.
            with dispatch.open_source(con, source_id, profile=profile, actual_witness=actual) as source:
                return {**source.public(), "replayed": True}
        _identity_pin(pins, profile, actual)
        if _exists(intent_path, profile):
            intent_raw, intent_identity = old._pin_file(pins, intent_path, MAX_DECLARATION_BYTES, capture=True)
            shape = backups._decode(intent_raw)
            if type(shape.get("schema_version")) is int and 10 <= shape["schema_version"] <= 16:
                intent = old._intent(intent_raw)
                _binding(intent, source_id, profile, actual)
                # Continue only an already complete original image. No
                # companion or current17 SQL comparison can manufacture an
                # earlier source or replace its original knowledge bytes.
                if not _exists(registry, profile):
                    _fail("SOURCE_NOT_READY")
                descriptor, descriptor_sha, _ = _image(stack, pins, intent, allow_reserved=True)
                declaration = {**{key: item for key, item in intent.items() if key != "action"},
                    "source_kind": "home_checkpoint", "descriptor_sha256": descriptor_sha,
                    "image_sha256": descriptor["database_file"]["sha256"]}
                old._declaration(_json(declaration))
                _persist(path, declaration, pins)
                with old.open_source(con, source_id, profile=profile) as source:
                    source.verify()
                    pins.verify()
                    return {**source.public(), "replayed": False}
            intent = _intent(intent_raw)
            if intent_raw != _json(intent):
                _fail("SOURCE_PROVENANCE_INVALID")
        else:
            if _artifact_exists(registry, destination, profile) or _exists(companion_path, profile):
                _fail("SOURCE_PROVENANCE_MISSING")
            identity = actual.identity()
            rows = old._read_knowledge(con)
            dependencies = old._pin_media(pins, rows, profile)
            intent = {"format_version": 1, "action": "produce_home_checkpoint", "source_id": source_id,
                "library_id": identity["library_id"], "node_id": identity["node_id"],
                "profile_sha256": old._profile_sha(profile), "source_version": db.knowledge_version(con),
                "schema_version": 17, "backup_id": _backup_id(source_id), "knowledge_sha256": _sha(rows),
                "media_dependencies": dependencies, "created_at": backups._stamp()}
            _intent(_json(intent))
            _persist(intent_path, intent, pins)
            intent_raw, intent_identity = old._pin_file(pins, intent_path, MAX_DECLARATION_BYTES, capture=True)
        _binding(intent, source_id, profile, actual)
        has_artifact = _artifact_exists(registry, destination, profile)
        if _exists(companion_path, profile):
            companion_raw, companion_identity = old._pin_file(pins, companion_path,
                MAX_DECLARATION_BYTES, capture=True)
            companion = _companion(companion_raw, intent, intent_identity["sha256"])
            if companion_raw != _json(companion):
                _fail("SOURCE_PROVENANCE_INVALID")
        else:
            if has_artifact:
                _fail("SOURCE_PROVENANCE_MISSING")
            sql_sha = _current(con, pins, intent)
            companion = {"format_version": 1, "action": "produce_home_checkpoint17",
                "intent_sha256": intent_identity["sha256"], "sql_image_sha256": sql_sha,
                **{key: intent[key] for key in ("source_id", "profile_sha256", "library_id", "node_id",
                    "source_version", "schema_version", "created_at")}}
            _companion(_json(companion), intent, intent_identity["sha256"])
            _persist(companion_path, companion, pins)
        if not _exists(registry, profile):
            if has_artifact:
                _fail("SOURCE_PROVENANCE_MISSING")
            if _current(con, pins, intent) != companion["sql_image_sha256"]:
                _fail("SOURCE_CHANGED")
            pins.verify()
            # This committed backup ignores pending SQL. The producer owns EX
            # and an original readonly witness, not a SQL write reservation.
            backups.create(con, destination, profile=profile, kind="maintenance", backup_id=intent["backup_id"])
        descriptor, descriptor_sha, _ = _image(stack, pins, intent, companion=companion, allow_reserved=True)
        declaration = {**{key: item for key, item in intent.items() if key != "action"},
            "source_kind": "home_checkpoint", "descriptor_sha256": descriptor_sha,
            "image_sha256": descriptor["database_file"]["sha256"]}
        _declaration(_json(declaration))
        _persist(path, declaration, pins)
        with open_source(con, source_id, profile=profile, actual_witness=actual) as source:
            source.verify()
            pins.verify()
            return {**source.public(), "replayed": False}


def create_checkpoint(*, profile, source_id):
    """Explicit local producer; no facility setup, migration or paid work."""
    if config.is_cloud():
        _fail("ROLE_REQUIRED")
    profile = lifecycle._profile(profile)
    _backup_id(source_id)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        with store.exclusive(), store._guard, lifecycle.exclusive(profile, timeout=1):
            with store.reference_operation():
                with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
                    con.execute("PRAGMA query_only=ON")
                    with witness.capture_locked(con, profile=profile, store=store) as actual:
                        result = _produce(con, actual, profile, source_id)
    return result


def read_checkpoint(*, profile, source_id):
    """Pure reader: no worker, setup, migration or new metadata/provenance."""
    if config.is_cloud():
        _fail("ROLE_REQUIRED")
    profile = lifecycle._profile(profile)
    _backup_id(source_id)
    # Probe without lock=True, which would create a missing lock. Release the
    # read handle before StateStore opens its existing RW locking handle.
    try:
        with safe._Directory(profile.database.parent, base=profile.database.parent) as directory:
            with directory.open_file(sync_state._LOCK):
                pass
            directory.verify()
    except FileNotFoundError:
        _fail("STATE_LOCK_MISSING")
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    from . import home_recovery_source_dispatch as dispatch
    with store.exclusive(), store._guard, lifecycle.shared(profile, timeout=1):
        with store.reference_operation():
            with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
                con.execute("PRAGMA query_only=ON")
                with witness.capture_locked(con, profile=profile, store=store) as actual:
                    with dispatch.open_source(con, source_id, profile=profile, actual_witness=actual) as source:
                        result = source.public()
    return result
