"""Pinned, file-only Home17 dependency observations, never execution authority.

The caller supplies the twelve public columns from its actual committed image
and retains that image's SQL CAS. This reader does not read a live database,
StateStore or worker and never resolves a sealed restore/bundle producer.
Matching proposal/receipt row hashes are file declarations, not a 35-column
SQL proof. Every image parsed here is the exact registered original image.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import asdict
import hashlib
import os
from pathlib import Path
import sqlite3
import tempfile
import time
from types import SimpleNamespace
import uuid

from . import home_preservation_contract17 as contract
from . import home_authority_pending_contract17 as pending_contract
from . import home_recovery_source as sources, home_recovery_source17 as sources17
from . import source_preservation as base, source_preservation_bundle as bundle
from . import storage_backups as backups, storage_home_recovery_roots as old
from . import storage_home_roots as home, storage_lifecycle as lifecycle
from . import storage_root_inputs as inputs, storage_source as physical
from . import storage_legacy_pending_dependencies17 as pending_dependencies
from . import storage_source_authority_roots as authority_roots
from . import storage_source_continuity_roots as cloud_roots
from . import sync_media as safe, sync_source_authority as authority
from . import sync_source_continuity as cloud, sync_state
from .sync_recovery import _Pins

DIRECTORY = ".reno-home-knowledge-restores-v2"
_REQUEST = frozenset("format_version action operation_id request request_sha256 library_id node_id "
    "owner_profile_sha256 source before_capture_sha256 bundle_id".split())
_INTENT = frozenset("format_version action operation_id request_sha256 reservation_sha256 bundle_id "
    "manifest_sha256 bundle_receipt_sha256 certificate_sha256 file_set_sha256".split())
_PROPOSAL = frozenset("format_version action operation_id intent_sha256 row_sha256 committed_at".split())
_RECEIPT = frozenset("format_version action operation_id intent_sha256 proposal_sha256 row_sha256 "
    "result_sha256 checkpoint_sha256 bundle_receipt_sha256 committed_at".split())
_SOURCE = frozenset("source_kind source_id source_version source_sha256 source_binding_sha256 "
    "knowledge_sha256 row_counts".split())
_ERRORS = (ValueError, OSError, sqlite3.Error, TypeError, KeyError, UnicodeError, RecursionError)


def _fail(code="HOME17_DEPENDENCY_UNPROVEN"):
    raise old.HomeRecoveryRootError(code) from None


def _encoded(value):
    return backups._json(value)


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _same(value, expected):
    if value != expected:
        _fail()


class _ProofPins(_Pins):
    """Immutable handles, released mutable catalog reads, exact namespaces."""
    def __init__(self, stack, profile, callback):
        super().__init__(stack)
        self.profile, self.callback = profile, callback
        self.started, self.directories, self.names, self.absences, self.mutable = time.monotonic(), [], [], [], []
        self.error = None

    def check(self):
        if self.error is not None:
            raise self.error
        try:
            self.callback()
            if time.monotonic() - self.started > 120:
                _fail("HOME17_DEPENDENCY_LIMIT")
        except BaseException as error:
            self.error = error
            raise

    def _read(self, stream, size, *, capture=False):
        os.lseek(stream.fileno(), 0, os.SEEK_SET)
        found, chunks, count = hashlib.sha256(), [], 0
        while count <= size:
            self.check()
            raw = os.read(stream.fileno(), min(1024 * 1024, size + 1 - count))
            if not raw:
                break
            count += len(raw)
            found.update(raw)
            if capture:
                chunks.append(raw)
        return found.hexdigest(), count, b"".join(chunks) if capture else None

    def directory(self, path, *, base):
        self.check()
        directory = self.stack.enter_context(safe._Directory(path, base=base))
        self.directories.append(directory)
        return directory

    def read(self, path, maximum, *, expected=None, mutable=False, optional=False, capture=True):
        self.check()
        try:
            directory = self.directory(path.parent, base=path.parent)
        except FileNotFoundError:
            parent = self.directory(path.parent.parent, base=path.parent.parent)
            self.absences.append((parent, path.parent.name))
            if not optional:
                raise
            return None, None
        try:
            raw, identity = backups._read(path, maximum=maximum, content=capture,
                read_control=self)
        except FileNotFoundError:
            self.absences.append((directory, path.name))
            if not optional:
                raise
            return None, None
        if expected is not None and identity["sha256"] != expected:
            _fail("HOME17_DEPENDENCY_CHANGED")
        if mutable:
            signature = physical.file_signature(directory.entry_stat(path.name))
            self.mutable.append((directory, path.name, signature, identity, maximum))
        else:
            pinned = self.add(path.parent, path.name, identity["sha256"], identity["byte_size"], maximum,
                capture=capture)
            if capture and pinned != raw:
                _fail("HOME17_DEPENDENCY_CHANGED")
        return raw, identity

    def document(self, path, maximum, **kwargs):
        raw, identity = self.read(path, maximum, **kwargs)
        if raw is None:
            return None, None
        value = backups._decode(raw)
        if _encoded(value) != raw:
            _fail()
        return value, identity

    def namespace(self, directory, expected):
        self.check()
        found = []
        with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
            for item in entries:
                self.check()
                if len(found) >= len(expected):
                    _fail("HOME17_DEPENDENCY_CHANGED")
                safe._safe_stat(directory.entry_stat(item.name), directory=item.name == "files")
                found.append(item.name)
        if set(found) != set(expected):
            _fail("HOME17_DEPENDENCY_CHANGED")
        self.names.append((directory, tuple(sorted(found))))

    def verify(self):
        self.check()
        for directory in self.directories:
            directory.verify()
        for directory, name in self.absences:
            self.check()
            try:
                directory.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("HOME17_DEPENDENCY_CHANGED")
        for directory, names in self.names:
            current = []
            with os.scandir(directory.fd if os.name != "nt" else safe._native_path(directory.path)) as entries:
                for item in entries:
                    self.check()
                    if len(current) >= len(names):
                        _fail("HOME17_DEPENDENCY_CHANGED")
                    current.append(item.name)
            if tuple(sorted(current)) != names:
                _fail("HOME17_DEPENDENCY_CHANGED")
        for directory, name, signature, identity, maximum in self.mutable:
            self.check()
            if physical.file_signature(directory.entry_stat(name)) != signature:
                _fail("HOME17_DEPENDENCY_CHANGED")
            if backups._read(directory.path / name, maximum=maximum, content=False,
                    read_control=self)[1] != identity:
                _fail("HOME17_DEPENDENCY_CHANGED")
        super().verify()


class _Image:
    def __init__(self, con, descriptor):
        self.connection, self.descriptor = con, descriptor
        self.schema = int(con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0])
        observed = {key: value for key, value in con.execute("SELECT key,value FROM main.meta WHERE key IN "
            "('library_id','library_epoch','kb_revision')")}
        self.version = {"library_id": observed["library_id"], "library_epoch": int(observed["library_epoch"]),
            "kb_revision": int(observed["kb_revision"])}
        _same(self.schema, descriptor["schema_version"])
        _same(self.version, descriptor["source_version"])


def _authorize(action, first, second, database, origin):
    continuity = inputs.continuity_authorize(action, first, second, database, origin)
    if continuity is not None:
        return continuity
    if action == sqlite3.SQLITE_SELECT:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ and database == "main" and origin is None:
        allowed = {"meta": {"key", "value"}, "video_asset": {"video_id", "files_json", "content_sha256"},
            "frame": {"video_id", "path"}, "sync_report": {"name", "storage_key", "sha256", "byte_size"},
            "sqlite_master": {"name", "type", "sql", "tbl_name"},
            old.TABLE: {*old.COLUMNS, "result_sha256"}, **{k: set(v) for k, v in cloud_roots.SQL_COLUMNS.items()}}
        if first in allowed and second in allowed[first]:
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first == "table_xinfo" and second in cloud_roots.SQL_COLUMNS:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _image(pins, collector, profile, backup_id, image_sha, library_id, *, descriptor_sha=None):
    catalog_path = backups._catalog(profile) / (backup_id + ".json")
    catalog, _ = pins.document(catalog_path, backups.MAX_JSON, mutable=True)
    with backups.controlled_reads(pins):
        record, _ = backups._record(catalog_path)
    _same(catalog, record)
    if (record["state"] not in {"ready", "retiring"} or record["source_profile"] != backups._profile_json(profile)
            or record["backup_id"] != backup_id):
        _fail()
    path = Path(record["destination"])
    descriptor, identity = pins.document(path.with_name(path.name + backups.SUFFIX), backups.MAX_JSON,
        expected=descriptor_sha)
    backups._descriptor(descriptor, record)
    _same(descriptor, record["descriptor"])
    _same(identity["sha256"], record["descriptor_sha256"])
    _same(descriptor["source_roots"], backups._roots(profile))
    _same(descriptor["source_version"]["library_id"], library_id)
    _same(descriptor["database_file"]["sha256"], image_sha)
    _, image_identity = pins.read(path, backups.MAX_IMAGE, expected=image_sha, capture=False)
    _same(image_identity, descriptor["database_file"])
    for suffix in ("-wal", "-journal", "-shm"):
        raw, _ = pins.read(path.with_name(path.name + suffix), backups.MAX_IMAGE, optional=True)
        if raw:
            _fail()
    # SQLite reads this private copy of the pinned original bytes, not a path
    # that can become a different image between pinning and sqlite3.connect.
    image_stream = next(item[2] for item in reversed(pins.files) if item[0].path / item[1] == path)
    temporary = pins.stack.enter_context(tempfile.TemporaryDirectory(prefix="reno-home17-public-image-"))
    copied = Path(temporary) / "image.sqlite"
    os.lseek(image_stream.fileno(), 0, os.SEEK_SET)
    copied_hash, copied_size = hashlib.sha256(), 0
    with copied.open("xb") as output:
        while chunk := os.read(image_stream.fileno(), 1024 * 1024):
            pins.check()
            output.write(chunk)
            copied_hash.update(chunk)
            copied_size += len(chunk)
            if copied_size > image_identity["byte_size"]:
                _fail("HOME17_DEPENDENCY_CHANGED")
    _same(copied_hash.hexdigest(), image_sha)
    _same(copied_size, image_identity["byte_size"])
    os.chmod(copied, 0o600)
    con = sqlite3.connect(copied.as_uri() + "?mode=ro&immutable=1", uri=True)
    pins.stack.callback(con.close)
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, sources.MAX_CELL_BYTES)
    con.set_authorizer(_authorize)
    steps = [0]
    def progress():
        steps[0] += 1000
        pins.check()
        return int(steps[0] > 10_000_000)
    con.set_progress_handler(progress, 1000)
    found = _Image(con, descriptor)
    # Registered dependencies only increase roots. They do not authorize a
    # ready producer or validate the descriptor's private-body derivation.
    for index, dependency in enumerate(descriptor["dependencies"]):
        pins.check()
        namespace = dependency.get("namespace") if type(dependency) is dict else None
        try:
            if type(dependency) is not dict or set(dependency) - {"namespace", "storage_key", "sha256", "byte_size", "origin"}:
                _fail()
            collector.reference(namespace, dependency["storage_key"], [backup_id, index],
                dependency.get("sha256"), dependency.get("byte_size"))
        except _ERRORS:
            collector.block((namespace,) if namespace in inputs.NAMESPACES else inputs.NAMESPACES,
                "HOME17_IMAGE_DEPENDENCY_UNKNOWN", backup_id)
    collector.binding("home17_registered_image", "observed", [{"backup_id": backup_id, "image_sha256": image_sha,
        "descriptor_sha256": identity["sha256"], "schema_version": found.schema, "catalog_state": record["state"]}])
    return found


def _media(image, collector, pins, profile, locator):
    con, limits = image.connection, collector.limits
    for table, columns in (("video_asset", ("video_id", "files_json", "content_sha256")),
                           ("frame", ("video_id", "path"))):
        count = 0
        for raw in con.execute('SELECT ' + ','.join(columns) + ' FROM main."' + table + '" LIMIT ?', (limits.max_rows + 1,)):
            pins.check()
            count += 1
            if count > limits.max_rows:
                _fail("HOME17_DEPENDENCY_LIMIT")
            row = dict(zip(columns, raw))
            if table == "video_asset":
                values = inputs._json(row["files_json"], limits)
                if type(values) is not dict:
                    _fail()
            else:
                values = {"frame": row["path"]}
            for field, value in values.items():
                if value is None:
                    continue
                key = inputs.media_storage_key(value, profile=profile, source_video_id=row["video_id"])
                if key:
                    collector.reference("sync_media", key, [locator, table, count, field],
                        row["content_sha256"] if table == "video_asset" and field == "video" else None)


def _source(pins, collector, row, profile):
    backup_id = str(uuid.uuid5(uuid.UUID(row["source_id"]), "reno.home.knowledge.checkpoint.v1"))
    image = _image(pins, collector, profile, backup_id, row["source_sha256"], row["library_id"])
    if not 10 <= image.schema <= 17:
        _fail()
    _media(image, collector, pins, profile, [row["operation_id"], "source"])
    path = sources._source_path(profile, row["source_id"])
    raw, _ = pins.read(path, sources.MAX_DECLARATION_BYTES, expected=row["source_binding_sha256"], optional=True)
    parser = sources17 if image.schema == 17 else sources
    declaration = None if raw is None else parser._declaration(raw)
    if declaration is not None:
        _same(declaration["source_version"], image.version)
        _same(declaration["schema_version"], image.schema)
        _same(declaration["backup_id"], backup_id)
        _same(declaration["descriptor_sha256"], _sha(_encoded(image.descriptor)))
        _same(declaration["profile_sha256"], sources._profile_sha(profile))
        for key in ("source_id", "library_id", "node_id"):
            _same(declaration[key], row[key])
        _same(declaration["image_sha256"], row["source_sha256"])
        for dependency in declaration["media_dependencies"]:
            # Non-managed original/audio/frame paths remain protected by the
            # original image edge; this GC only indexes managed sync media.
            if dependency["storage_key"].startswith("sync/"):
                collector.reference("sync_media", dependency["storage_key"], [row["operation_id"], "declaration"],
                    dependency["sha256"], dependency["byte_size"])
    intent_raw, _ = pins.read(path.with_name(row["source_id"] + ".intent.json"), sources.MAX_DECLARATION_BYTES, optional=True)
    intent = None if intent_raw is None else parser._intent(intent_raw)
    if intent is not None:
        _same(intent["source_version"], image.version)
        _same(intent["backup_id"], backup_id)
        _same(intent["schema_version"], image.schema)
        _same(intent["profile_sha256"], sources._profile_sha(profile))
        for key in ("source_id", "library_id", "node_id"):
            _same(intent[key], row[key])
        if declaration is not None:
            _same({key: intent[key] for key in sources._INTENT_FIELDS - {"action"}},
                {key: declaration[key] for key in sources._INTENT_FIELDS - {"action"}})
    companion_raw, _ = pins.read(path.with_name(row["source_id"] + ".sql17.json"), sources.MAX_DECLARATION_BYTES, optional=True)
    if companion_raw is not None:
        if image.schema != 17:
            _fail()
        companion = backups._decode(companion_raw)
        if _encoded(companion) != companion_raw or set(companion) != sources17._COMPANION_FIELDS:
            _fail()
        if (type(companion["format_version"]) is not int or companion["format_version"] != 1
                or companion["action"] != "produce_home_checkpoint17"
                or companion["schema_version"] != 17):
            _fail()
        for key, expected in (("source_id", row["source_id"]), ("library_id", row["library_id"]),
                ("node_id", row["node_id"]), ("profile_sha256", sources._profile_sha(profile)), ("source_version", image.version)):
            _same(companion[key], expected)
        old._sha(companion["sql_image_sha256"])
        old._sha(companion["intent_sha256"])
        if type(companion["created_at"]) is not str or not backups._TIME.fullmatch(companion["created_at"]):
            _fail()
        if intent is not None:
            parser._companion(companion_raw, intent, _sha(intent_raw))
    elif image.schema == 17 and declaration is not None:
        _fail()
    collector.binding("home17_source_dependencies", "observed", [{"backup_id": backup_id, "schema_version": image.schema,
        "source_sha256": row["source_sha256"], "declaration_present": declaration is not None}])
    return image.version, None if declaration is None else declaration["knowledge_sha256"]


class _CopiesReader:
    """Existing pure parsers read only this original pinned copied-file map."""
    def __init__(self, profile, copies, check, *, home_kind=False):
        self.profile, self.copies, self.check = profile, copies, check
        self.limits = authority_roots.CaptureLimits() if home_kind else cloud_roots.CaptureLimits()
        self.known_refs, self.absences, self.anchor_errors = {}, [], []
        self.payload, self.facility_seen = None, False
        self.publication_payloads, self.publication_payload_errors = {}, []
        self.payload_total = self.payload_count = 0

    def legacy_pending_state(self, document, *, entries=None):
        """Original verified copies only; preserve declared absent/empty states."""
        self.check()
        return bundle._copied_pending_state(self.profile, document, self.copies,
            entries=entries, check=self.check)

    def legacy_pending_dependencies(self, document, *, entries):
        """Original declared copies only; no current-path fallback."""
        self.check()
        return bundle._copied_pending_dependencies(self.profile, document, self.copies,
            entries=entries, check=self.check)

    def _key(self, path):
        if lifecycle._key(path) == lifecycle._key(self.profile.checkpoints):
            return "CHECKPOINTS", ""
        if path.is_relative_to(self.profile.database.parent):
            relative = path.relative_to(self.profile.database.parent).as_posix()
            return "DATA", "" if relative == "." else relative
        if path.is_relative_to(self.profile.checkpoints):
            return "CHECKPOINTS", path.relative_to(self.profile.checkpoints).as_posix()
        _fail()

    def directory(self, path, *, base):
        self.check()
        kind, key = self._key(path)
        if key and not any(k == kind and name.startswith(key + "/") for k, name in self.copies):
            # A present facility includes empty typed directories. Their exact
            # conceptual namespace is fixed by the pinned canonical contract.
            prefixes = (cloud.DIRECTORY, authority.DIRECTORY + "/" + authority.VERSION_DIRECTORY)
            if not any(key.startswith(prefix) and ("DATA", prefix + "/contract.json") in self.copies for prefix in prefixes):
                raise FileNotFoundError(path)
        return SimpleNamespace(path=path)

    def read(self, directory, name, *, mutable=False, optional=False, decode=True):
        self.check()
        kind, key = self._key(directory.path)
        raw = self.copies.get((kind, (key + "/" if key and key != "." else "") + name))
        if raw is None:
            if optional:
                return None
            _fail()
        if not decode:
            return raw
        value = sync_state._decode(raw)
        _same(_encoded(value), raw)
        return value

    def listing(self, directory, allowed, maximum, *, subdirectories=()):
        self.check()
        kind, key = self._key(directory.path)
        prefix = key + "/" if key and key != "." else ""
        names = {name[len(prefix):].split("/")[0] for k, name in self.copies if k == kind and name.startswith(prefix)}
        if (kind, key + "/contract.json") in self.copies:
            names.update(subdirectories)
        if key == authority.DIRECTORY:
            names.add(authority.VERSION_DIRECTORY)
        ordinary = [name for name in names if not cloud._PART.fullmatch(name)]
        if len(ordinary) > maximum or any(not allowed(name) for name in ordinary):
            _fail()
        return sorted(ordinary)

    @contextmanager
    def payload_window(self):
        # The bundle pins already enforce their original physical read budget.
        # This adapter never opens a current/live path or grants permission.
        self.check()
        yield
        self.check()

    def read_publication_payload(self, directory, name):
        from . import sync_source_publication_local2 as publication
        raw = self.read(directory, name, decode=False)
        self.payload_count += 1
        self.payload_total += len(raw)
        if (len(raw) > publication.MAX_PAYLOAD_BYTES
                or self.payload_total > publication.MAX_PAYLOAD_TOTAL_BYTES
                or self.payload_count > publication.MAX_PAYLOAD_FILES + authority.MAX_INTERNAL_PARTS):
            _fail()
        return raw

    def publication_payload_listing(self, directory, allowed, maximum, *, subdirectories=()):
        kind, key = self._key(directory.path)
        prefix = key + "/" if key else ""
        names = {name[len(prefix):].split("/")[0] for k, name in self.copies
                 if k == kind and name.startswith(prefix)}
        parts = sorted(name for name in names if authority._PART.fullmatch(name))
        if len(parts) > self.limits.max_parts:
            _fail()
        for name in parts:
            self.read_publication_payload(directory, name)
            self.publication_payload_errors.append("PAYLOAD_UNPUBLISHED_PART")
        ordinary = sorted(names - set(parts))
        if len(ordinary) > maximum or any(not allowed(name) for name in ordinary):
            _fail()
        self.check()
        return ordinary


def _bundle(pins, row, profile):
    operation, bundle_id = row["operation_id"], str(uuid.uuid5(uuid.UUID(row["operation_id"]), "reno.home.preservation.bundle.v1"))
    root = profile.database.parent
    reservation, reservation_identity = pins.document(root / bundle.OPERATIONS / (operation + ".json"), bundle.MAX_METADATA_BYTES)
    bundle._reservation(reservation, profile, operation, reservation.get("root_job_id"))
    _same((reservation["library_id"], reservation["node_id"]), (row["library_id"], row["node_id"]))
    maintenance, maintenance_identity = pins.document(root / bundle.OPERATIONS / (operation + ".receipt.json"), bundle.MAX_METADATA_BYTES)
    path = root / bundle.DIRECTORY / bundle_id
    directory = pins.directory(path, base=root)
    pins.namespace(directory, {"manifest.json", "certificate.json", "files"})
    manifest, _ = pins.document(path / "manifest.json", bundle.MAX_METADATA_BYTES)
    cert_raw, _ = pins.read(path / "certificate.json", base.MAX_CERTIFICATE_BYTES)
    document = bundle._bundle_certificate(cert_raw, reservation)
    expected = {"format_version": 1, "bundle_id": bundle_id, "operation_id": operation,
        "owner_profile_sha256": reservation["owner_profile_sha256"], "library_id": row["library_id"], "node_id": row["node_id"],
        "knowledge_version": reservation["knowledge_version"], "database_backup": manifest.get("database_backup"),
        "certificate": {"format_version": 2, "sha256": reservation["certificate_sha256"], "byte_size": len(cert_raw)},
        "permanent_witnesses": document["permanent_witnesses"], "files": reservation["files"],
        "file_set_sha256": reservation["file_set_sha256"], "created_at": reservation["created_at"]}
    expected["manifest_sha256"] = _sha(_encoded(expected))
    _same(manifest, expected)
    facts = {"backup_id": row["protection_backup_id"], "descriptor_sha256": row["protection_descriptor_sha256"],
        "image_sha256": row["protection_image_sha256"]}
    _same(manifest["database_backup"], facts)
    _same(row["checkpoint_json"], _encoded(bundle._checkpoint_wrapper(document, manifest)).decode())
    copies_dir = pins.directory(path / "files", base=root)
    pins.namespace(copies_dir, {bundle._copy_name(entry) for entry in reservation["files"]})
    copies, identities = {}, []
    for entry in reservation["files"]:
        name = bundle._copy_name(entry)
        raw, identity = pins.read(path / "files" / name, bundle._file_limit(entry), expected=entry["sha256"])
        _same(len(raw), entry["byte_size"])
        copies[(entry["root_kind"], entry["relative_key"])] = raw
        identities.append({"name": name, "identity": identity})
    for name in ("certificate.json", "manifest.json"):
        _, identity = backups._read(path / name, maximum=bundle.MAX_METADATA_BYTES, content=False,
            read_control=pins)
        identities.append({"name": name, "identity": identity})
    _same(maintenance, {"format_version": 1, "action": "preserve_home17", "operation_id": operation,
        "bundle_id": bundle_id, "reservation_sha256": reservation_identity["sha256"],
        "manifest_sha256": manifest["manifest_sha256"], "certificate_sha256": _sha(cert_raw),
        "files": identities, "created_at": reservation["created_at"]})
    bundle._copied_pending_dependencies(profile, document, copies,
        entries=reservation["files"], check=pins.check)
    return document, manifest, copies, reservation_identity["sha256"], maintenance_identity["sha256"]


def _producer(pins, row, profile, document, manifest, maintenance_sha, *, provenance):
    path, operation = profile.database.parent / DIRECTORY, row["operation_id"]
    request, request_identity = pins.document(path / (operation + ".request.json"), 2 * 1024 * 1024)
    intent, intent_identity = pins.document(path / (operation + ".intent.json"), 2 * 1024 * 1024)
    proposal, proposal_identity = pins.document(path / (operation + ".proposal.json"), 16 * 1024)
    receipt, _ = pins.document(path / (operation + ".receipt.json"), 16 * 1024)
    for value, fields in ((request, _REQUEST), (intent, _INTENT), (proposal, _PROPOSAL), (receipt, _RECEIPT)):
        if (set(value) != fields or type(value["format_version"]) is not int or value["format_version"] != 2
                or value["operation_id"] != operation or value["action"] != "home_knowledge_restore"):
            _fail()
    source = request["source"]
    if type(source) is not dict or set(source) != _SOURCE or source["source_kind"] != "home_checkpoint":
        _fail()
    for key in ("source_id", "source_sha256", "source_binding_sha256", "source_kind"):
        _same(source[key], row[key])
    for key in ("source_sha256", "source_binding_sha256", "knowledge_sha256"):
        old._sha(source[key])
    if type(source["row_counts"]) is not dict or set(source["row_counts"]) != set(sources.COLUMNS):
        _fail()
    for count in source["row_counts"].values():
        old._integer(count, maximum=sources.MAX_ROWS)
    _same(request["owner_profile_sha256"], _sha(_encoded(list(profile.identity))))
    _same((request["library_id"], request["node_id"]), (row["library_id"], row["node_id"]))
    _same(request["bundle_id"], manifest["bundle_id"])
    _same(request["before_capture_sha256"], manifest["certificate"]["sha256"])
    from .home_recovery import _request
    _same(request["request"], _request(row["source_id"], request["request"].get("expected_digest")))
    _same(request["request_sha256"], _sha(_encoded(request["request"])))
    _same(intent, {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation,
        "request_sha256": request["request_sha256"], "reservation_sha256": request_identity["sha256"],
        "bundle_id": manifest["bundle_id"], "manifest_sha256": manifest["manifest_sha256"],
        "bundle_receipt_sha256": maintenance_sha, "certificate_sha256": manifest["certificate"]["sha256"],
        "file_set_sha256": manifest["file_set_sha256"]})
    _same(proposal["intent_sha256"], intent_identity["sha256"])
    old._sha(proposal["row_sha256"])
    if type(proposal["committed_at"]) is not str or not backups._TIME.fullmatch(proposal["committed_at"]):
        _fail()
    old._sha(receipt["result_sha256"])
    _same(receipt, {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation,
        "intent_sha256": intent_identity["sha256"], "proposal_sha256": proposal_identity["sha256"],
        "row_sha256": proposal["row_sha256"], "result_sha256": receipt["result_sha256"],
        "checkpoint_sha256": row["checkpoint_sha256"], "bundle_receipt_sha256": maintenance_sha,
        "committed_at": proposal["committed_at"]})
    if provenance is not None:
        _same(provenance.get(operation), receipt["result_sha256"])
    rebuilt = {"operation_id": request["operation_id"], "library_id": request["library_id"], "node_id": request["node_id"],
        **{key: source[key] for key in ("source_kind", "source_id", "source_sha256", "source_binding_sha256")},
        "protection_backup_id": manifest["database_backup"]["backup_id"],
        "protection_descriptor_sha256": manifest["database_backup"]["descriptor_sha256"],
        "protection_image_sha256": manifest["database_backup"]["image_sha256"],
        "checkpoint_json": _encoded(bundle._checkpoint_wrapper(document, manifest)).decode(),
        "checkpoint_sha256": _sha(_encoded(bundle._checkpoint_wrapper(document, manifest)))}
    _same(row, rebuilt)
    return source


def _copied_roots(pins, collector, row, profile, document, copies, protection, root_ids, member_id, *, entries):
    home_reader = _CopiesReader(profile, copies, pins.check, home_kind=True)
    original_home = pending_contract.normalize_summary(document["permanent_witnesses"]["home_authority"])
    pending, pending_images = home_reader.legacy_pending_state(document, entries=entries)
    _requirements, dependency_records, dependency_unknown = home_reader.legacy_pending_dependencies(
        document, entries=entries)
    state_raw = copies.get(("DATA", sync_state._STATE))
    state = None if state_raw is None else sync_state._decode(state_raw)
    if state is not None:
        sync_state._validate(state)
    if state is None:
        home_summary, references = authority_roots._capture_absence(home_reader,
            format_version=original_home["format_version"], legacy_pending=pending)
    else:
        home_summary, references = authority_roots._capture(home_reader, state["binding"],
            format_version=original_home["format_version"], legacy_pending=pending)
    _same(home_summary, document["permanent_witnesses"]["home_authority"])
    sql = inputs.read_continuity_projection(protection.connection)
    cloud_summary, _ = cloud_roots._capture(_CopiesReader(profile, copies, pins.check), sql, None)
    _same(cloud_summary, document["permanent_witnesses"]["cloud_continuity"])
    captured_records = [] if state_raw is None else [home.CapturedRecord("state", sync_state._STATE, state_raw)]
    identity = {"library_id": row["library_id"], "node_id": row["node_id"]}
    for item in document["checkpoint"]["outboxes"]:
        pins.check()
        raw = copies.get(("DATA", sync_state._OUTBOX + "/" + item["publication_id"] + ".json"))
        if raw is None or len(raw) != item["byte_size"] or _sha(raw) != item["sha256"]:
            _fail()
        value = sync_state._decode(raw)
        value = home._outbox(value, item["publication_id"] + ".json", identity)
        _same(_sha(_encoded(value)), item["semantic_sha256"])
        for video in value["snapshot"]["videos"]:
            media = video["media"]
            collector.reference("sync_media", f"sync/{media['video_id']}/{media['sha256']}.{media['extension']}",
                [row["operation_id"], item["publication_id"]], media["sha256"], media["byte_size"])
        captured_records.append(home.CapturedRecord("outbox", item["publication_id"] + ".json", raw))
    for dependency in dependency_records:
        pins.check()
        kind = ({pending_dependencies.ROLE_MAPPING_V1: "sources",
                 pending_dependencies.ROLE_MAPPING_V2: "sources_v2",
                 pending_dependencies.ROLE_OUTBOX: "outbox"})[dependency.role]
        observed = home.CapturedRecord(kind, dependency.name, dependency.raw)
        existing = [record for record in captured_records if (record.kind, record.name) == (kind, dependency.name)]
        if existing:
            if len(existing) != 1 or existing[0].raw != dependency.raw:
                _fail()
        else:
            captured_records.append(observed)
    from . import sync_source_publication_local2 as publication
    for (kind, key), raw in sorted(copies.items()):
        if kind != "DATA" or not key.startswith(publication.PAYLOAD_DIRECTORY + "/"):
            continue
        if not key.startswith(publication.PAYLOAD_FAMILY + "/"):
            name = key[len(publication.PAYLOAD_DIRECTORY) + 1:]
            if "/" in name or not authority._PART.fullmatch(name):
                _fail()
            captured_records.append(home.CapturedRecord("part", key, raw))
            continue
        name = key[len(publication.PAYLOAD_FAMILY) + 1:]
        if "/" in name:
            _fail()
        if authority._PART.fullmatch(name):
            captured_records.append(home.CapturedRecord("part", key, raw))
        else:
            captured_records.append(home.CapturedRecord("publication_payload", name, raw))
    provenance = {old.TABLE: [{"operation_id": op, "result_sha256": sha} for op, sha in
        protection.connection.execute('SELECT operation_id,result_sha256 FROM main."' + old.TABLE + '"')]}
    projection = {"summary": home_summary, "references": list(references), "unknown": []}
    if original_home["format_version"] == 2:
        projection["legacy_pending_state"] = pending
        # Missing dependencies omitted by an older original manifest remain
        # blockers. Proven original raw maps/outboxes above enter the typed
        # parser; no current namespace may complete this copied selection.
        for issue in dependency_unknown:
            pins.check()
            selected = row["operation_id"] + ":" + issue.get("request_id", "unknown") + ":" + issue.get("phase", "unknown")
            if (issue.get("role") in pending_dependencies.MAPPING_ROLES
                    or issue.get("code") == "MAPPING_ID_UNPROVEN"):
                collector.block(("sync_media",), "HOME17_LEGACY_PENDING_MAPPING_UNPROVEN", selected)
            else:
                collector.block(("sync_media", "sync_report"), "HOME17_LEGACY_PENDING_OUTBOX_UNPROVEN", selected)
    return home.parse_authority_links(home.CapturedBundle(tuple(captured_records), ()),
        projection, profile=profile,
        root_ids=root_ids, member_id=member_id, source_identity=identity, provenance=provenance,
        check_cancelled=pins.check, **({"legacy_pending_images": pending_images}
            if original_home["format_version"] == 2 else {}))


def _merge(scans):
    return inputs.InputScan(*(tuple(sorted(set(item for scan in scans for item in getattr(scan, field)),
        key=lambda item: inputs.canonical_bytes(asdict(item))))
        for field in ("references", "selectors", "blockers", "source_bindings")))


@contextmanager
def capture_locked(rows, *, profile, root_ids, member_id, library_id, schema_version,
                   node_id=None, source_kind="live", source_id="live", limits=None,
                   check_cancelled=None, provenance=None):
    """Hold original file/image dependency pins through the group final CAS.

    ``schema_version`` and ``rows`` must come from the outer actual-image
    parser. Optional ``provenance`` is that same image's independently captured
    operation_id -> result_sha256 projection, never a live database lookup.
    This API does not establish those caller facts or issue a new capability.
    """
    if type(schema_version) is not int or schema_version != 17:
        _fail("HOME17_DEPENDENCY_SCHEMA_REQUIRED")
    if check_cancelled is not None and not callable(check_cancelled):
        raise ValueError("Invalid cancellation callback")
    limits = inputs.InputLimits(deadline_seconds=120) if limits is None else limits
    if not isinstance(limits, inputs.InputLimits):
        raise ValueError("Invalid dependency limits")
    if provenance is not None and (type(provenance) is not dict or any(type(k) is not str or type(v) is not str
            for k, v in provenance.items())):
        raise ValueError("Invalid same-image provenance projection")
    guard = lifecycle.require_guard(profile)
    def check():
        if check_cancelled is not None:
            try:
                check_cancelled()
            except BaseException as error:
                raise old._CallerCancelled(error) from None
        guard.verify()
    try:
        with ExitStack() as stack:
            pins = _ProofPins(stack, profile, check)
            collector = inputs._Collector(profile, root_ids, member_id, source_kind, source_id, limits)
            normalized = old.normalize_projection(rows, schema_version=17)
            collector.binding(old.TABLE, "present", normalized)
            scans = []
            for row in normalized:
                pins.check()
                if row["library_id"] != library_id or node_id is not None and row["node_id"] != node_id:
                    collector.block(("sync_media", "sync_report"), "HOME17_DEPENDENCY_IDENTITY_UNKNOWN", row["operation_id"])
                    continue
                contract.normalize_checkpoint(row["checkpoint_json"], expected_sha256=row["checkpoint_sha256"])
                # An independently readable original source image contributes
                # roots even when permanent restore metadata is unavailable.
                source_facts = None
                try:
                    source_facts = _source(pins, collector, row, profile)
                except _ERRORS:
                    pins.check()
                    collector.block(("sync_media",), "HOME17_SOURCE_IMAGE_UNPROVEN", row["operation_id"])
                try:
                    document, manifest, copies, _reservation_sha, maintenance_sha = _bundle(pins, row, profile)
                    protection = _image(pins, collector, profile, row["protection_backup_id"], row["protection_image_sha256"],
                        library_id, descriptor_sha=row["protection_descriptor_sha256"])
                    _same(protection.schema, 17)
                    _same(protection.version, document["knowledge_version"])
                    scans.append(_copied_roots(pins, collector, row, profile, document, copies, protection,
                        root_ids, member_id, entries=manifest["files"]))
                except _ERRORS:
                    pins.check()
                    collector.block(("sync_media", "sync_report"), "HOME17_BUNDLE_DEPENDENCY_UNPROVEN", row["operation_id"])
                    continue
                try:
                    requested = _producer(pins, row, profile, document, manifest, maintenance_sha, provenance=provenance)
                    if source_facts is None:
                        _fail()
                    _same(requested["source_version"], source_facts[0])
                    if source_facts[1] is not None:
                        _same(requested["knowledge_sha256"], source_facts[1])
                    collector.binding("home17_dependency_files", "closed", [{key: row[key] for key in old.COLUMNS if key != "checkpoint_json"}])
                except _ERRORS:
                    pins.check()
                    # All observable roots above remain. Missing metadata does
                    # not erase them or fabricate extra media uncertainty.
                    collector.binding("home17_dependency_files", "unproven", [{"operation_id": row["operation_id"]}])
            capture = old.RecoveryRootCapture(_merge([collector.result(), *scans]), pins, guard, check)
            capture.verify_source()
            yield capture
            capture.verify_source()
    except old._CallerCancelled as error:
        raise error.error from None
