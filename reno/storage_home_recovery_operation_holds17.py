"""Read-only backup holds from original Home maintenance operations.

These file facts preserve original retry dependencies. They grant no restore,
State, source admission, cancellation or physical retirement authority. The
caller owns lifecycle EX and retains this capture through each deletion check.
No live SQL, StateStore or producer is opened by this module.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
import hashlib
import os
from pathlib import Path
import re
import time
import uuid

from . import home_recovery_source as source16, home_recovery_source17 as source17
from . import home_recovery17 as restore17, source_preservation_bundle as bundles
from . import storage_backups as backups, storage_lifecycle as lifecycle
from . import storage_source as files, sync_media as safe, sync_snapshot

_PART = re.compile(r"(?:\.sync-[0-9a-f]{32}\.part|\.backup-write-[0-9a-f]{32}\.tmp)\Z")
_PUBLIC_SOURCE = frozenset("source_kind source_id source_version source_sha256 "
    "source_binding_sha256 knowledge_sha256 row_counts".split())


class OperationHoldError(ValueError):
    def __init__(self, code):
        self.code = "HOME_RECOVERY_OPERATION_HOLDS_" + code
        super().__init__(self.code)


def _fail(code):
    raise OperationHoldError(code) from None


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _json(value):
    return backups._json(value)


def _digest(value):
    return _sha(_json(value))


def _hash(value):
    if type(value) is not str or not backups._SHA.fullmatch(value):
        _fail("PROOF_INVALID")
    return value


@dataclass(frozen=True, slots=True)
class OperationHoldLimits:
    max_operations: int = 4096
    max_files: int = 100_000
    max_metadata_bytes: int = 128 * 1024 * 1024
    max_read_bytes: int = 8 * 1024 * 1024 * 1024
    max_seconds: float = 120


class _Reader:
    def __init__(self, stack, profile, guard, check_cancelled, limits):
        self.stack, self.profile, self.guard = stack, profile, guard
        self.callback, self.limits = check_cancelled, limits
        self.started, self.cancelled = time.monotonic(), None
        self.read_bytes = self.metadata_bytes = self.operation_count = self.file_count = 0
        self.directories, self.namespaces, self.absences, self.held = {}, {}, {}, {}

    def check(self):
        if self.cancelled is not None:
            raise self.cancelled
        if self.callback is not None:
            try:
                self.callback()
            except BaseException as exc:
                self.cancelled = exc
                raise
        if time.monotonic() - self.started > self.limits.max_seconds:
            _fail("LIMIT")
        if lifecycle.require_guard(self.profile, exclusive=True) is not self.guard:
            _fail("OWNER_REQUIRED")
        self.guard.verify()

    def long_read(self):
        self.check()
        from . import source_preservation_witness as witness
        window = witness._WINDOW.get()
        if window is not None and window._writable is not False:
            _fail("READ_WINDOW_REQUIRED")

    def before_read(self, _path, size, content):
        self.long_read()
        if content:
            self.metadata_bytes += size
            if self.metadata_bytes > self.limits.max_metadata_bytes:
                _fail("LIMIT")

    def read_chunk(self, _path, size, _content):
        self.read_bytes += size
        if self.read_bytes > self.limits.max_read_bytes:
            _fail("LIMIT")

    def directory(self, path):
        key = backups._key(path)
        if key not in self.directories:
            self.directories[key] = self.stack.enter_context(safe._Directory(path, base=self.profile.database.parent))
        return self.directories[key]

    def names(self, directory, maximum, *, final=False):
        names = set()
        target = directory.fd if os.name != "nt" else safe._native_path(directory.path)
        with os.scandir(target) as entries:
            for entry in entries:
                self.check()
                names.add(entry.name)
                if len(names) > maximum:
                    _fail("PROOF_CHANGED" if final else "LIMIT")
        directory.verify()
        return names

    def namespace(self, path):
        self.check()
        key = backups._key(path)
        if key in self.namespaces:
            return self.namespaces[key][1]
        parent = self.directory(path.parent)
        try:
            parent.entry_stat(path.name)
        except FileNotFoundError:
            self.absences[key] = (parent, path.name)
            return None
        directory = self.directory(path)
        names = self.names(directory, self.limits.max_files - self.file_count)
        self.file_count += len(names)
        if self.file_count > self.limits.max_files:
            _fail("LIMIT")
        for name in names:
            self.check()
            entry = directory.entry_stat(name)
            # Namespaces may contain only their fixed ordinary file/directory
            # shapes; reparse points, aliases and hardlinked files are rejected.
            if name in {"files"} or self._bundle_directory(path, name):
                safe._safe_stat(entry, directory=True)
            else:
                files.file_signature(entry)
        self.namespaces[key] = (directory, frozenset(names))
        return self.namespaces[key][1]

    @staticmethod
    def _bundle_directory(path, name):
        if path.name != bundles.DIRECTORY:
            return False
        try:
            return backups._uuid(name) == name
        except ValueError:
            return False

    def present(self, path):
        directory = self.directory(path.parent)
        try:
            directory.entry_stat(path.name)
        except FileNotFoundError:
            return False
        return True

    def read(self, path, *, maximum=backups.MAX_JSON, metadata=True, expected_sha=None,
             expected_size=None, capture=True):
        self.long_read()
        key = backups._key(path)
        if key in self.held:
            item = self.held[key]
            raw = item[6]
            if capture and raw is None:
                _fail("PROOF_INVALID")
            if (expected_sha is not None and expected_sha != item[5]
                    or expected_size is not None and expected_size != item[3][2]):
                _fail("PROOF_CHANGED")
            return raw, self._identity(item)
        directory = self.directory(path.parent)
        before = files.file_signature(directory.entry_stat(path.name))
        if before[2] > maximum:
            _fail("LIMIT")
        stream = self.stack.enter_context(directory.open_file(path.name))
        opened = files.file_signature(os.fstat(stream.fileno()))
        if before[:4] != opened[:4]:
            _fail("PROOF_CHANGED")
        if metadata:
            self.metadata_bytes += before[2]
            if self.metadata_bytes > self.limits.max_metadata_bytes:
                _fail("LIMIT")
        self.file_count += 1
        if self.file_count > self.limits.max_files:
            _fail("LIMIT")
        raw, digest = self._read(stream, before[2], capture=capture)
        item = (directory, path.name, stream, before, opened, digest, raw)
        self.held[key] = item
        identity = self._identity(item)
        if (expected_sha is not None and digest != expected_sha
                or expected_size is not None and before[2] != expected_size):
            _fail("PROOF_CHANGED")
        return raw, identity

    def _read(self, stream, size, *, capture):
        self.long_read()
        os.lseek(stream.fileno(), 0, os.SEEK_SET)
        remaining, chunks, digest = size, [], hashlib.sha256()
        while remaining:
            self.check()
            chunk = os.read(stream.fileno(), min(128 * 1024, remaining))
            if not chunk:
                _fail("PROOF_CHANGED")
            remaining -= len(chunk)
            self.read_bytes += len(chunk)
            if self.read_bytes > self.limits.max_read_bytes:
                _fail("LIMIT")
            digest.update(chunk)
            if capture:
                chunks.append(chunk)
        if os.read(stream.fileno(), 1):
            _fail("PROOF_CHANGED")
        self.check()
        return b"".join(chunks) if capture else None, digest.hexdigest()

    def _identity(self, item):
        directory, name, stream, before, opened, digest, _raw = item
        directory.verify()
        if (files.file_signature(directory.entry_stat(name)) != before
                or files.file_signature(os.fstat(stream.fileno())) != opened):
            _fail("PROOF_CHANGED")
        return {**backups._identity(os.fstat(stream.fileno())), "sha256": digest}

    def document(self, path, *, maximum=backups.MAX_JSON):
        raw, identity = self.read(path, maximum=maximum)
        value = backups._decode(raw)
        if raw != _json(value):
            _fail("PROOF_INVALID")
        return value, raw, identity

    def operation(self):
        self.operation_count += 1
        if self.operation_count > self.limits.max_operations:
            _fail("LIMIT")

    def verify_for_commit(self):
        self.check()
        for directory in self.directories.values():
            directory.verify()
        for parent, name in self.absences.values():
            try:
                parent.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("PROOF_CHANGED")
        for directory, original in self.namespaces.values():
            self.check()
            if frozenset(self.names(directory, len(original), final=True)) != original:
                _fail("PROOF_CHANGED")
        for item in self.held.values():
            self.check()
            self._identity(item)

    def verify_source(self):
        self.long_read()
        self.verify_for_commit()
        for item in self.held.values():
            self.check()
            _raw, digest = self._read(item[2], item[3][2], capture=False)
            if digest != item[5]:
                _fail("PROOF_CHANGED")
            self._identity(item)
        self.verify_for_commit()


@dataclass(frozen=True, slots=True, repr=False)
class OperationHoldCapture:
    backup_ids: frozenset[str]
    unknown: frozenset[str]
    _reader: object

    def verify_for_commit(self):
        self._reader.verify_for_commit()

    def verify_source(self):
        self._reader.verify_source()


def _ids(names, suffixes):
    identifiers, invalid = set(), False
    for name in names or ():
        if _PART.fullmatch(name):
            continue
        matched = False
        for suffix in sorted(suffixes, key=len, reverse=True):
            if name.endswith(suffix):
                try:
                    identifiers.add(backups._uuid(name[:-len(suffix)]))
                    matched = True
                except ValueError:
                    pass
                break
        if not matched:
            invalid = True
    return sorted(identifiers), invalid


class _Scanner:
    def __init__(self, reader, records):
        self.reader, self.profile, self.records = reader, reader.profile, records
        self.holds, self.unknown, self.bundle_facts = set(), set(), {}
        self.completed_sources = set()
        self.node = None

    def protect(self, identifier, callback, reason):
        # Derive this original UUID from a canonical operation/source filename
        # before parsing. Damaged content never silently removes its hold.
        self.holds.add(identifier)
        try:
            return callback()
        except (ValueError, OSError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            if exc is self.reader.cancelled or isinstance(exc, OperationHoldError) and exc.code.endswith("_LIMIT"):
                raise
            self.unknown.add(reason)
            return None

    def identity(self):
        # Released node identity files use ordinary JSON spacing. Pin their
        # original bytes and validate the exact shape, never relabel/rewrite
        # them as this module's canonical operation metadata.
        raw, _identity = self.reader.read(self.profile.database.parent / "node-identity.json", maximum=4096)
        value = backups._decode(raw)
        if set(value) != {"instance_id"}:
            _fail("PROOF_INVALID")
        self.node = backups._uuid(value["instance_id"])

    def binding(self, value, *, source=False):
        expected = source16._profile_sha(self.profile) if source else _digest(list(self.profile.identity))
        if (value["node_id"] != self.node or value["profile_sha256" if source else "owner_profile_sha256"] != expected):
            _fail("IDENTITY_MISMATCH")

    def backup(self, identifier, *, destination, version, schema_version, facts=None, required=False):
        registry = backups._catalog(self.profile) / (identifier + ".json")
        try:
            value, raw, _identity = self.reader.document(registry)
        except FileNotFoundError:
            if required or identifier in self.records:
                _fail("PROOF_MISSING")
            return None
        with backups.controlled_reads(self.reader):
            record, _ = backups._record(registry)
        if (record != value or identifier not in self.records or self.records[identifier] != record
                or record["source_profile"] != backups._profile_json(self.profile)
                or record["backup_id"] != identifier or record["kind"] != "maintenance"
                or record["destination"] != backups._key(destination)):
            _fail("IDENTITY_MISMATCH")
        if record["state"] == "reserved" and not required:
            return None
        if record["state"] not in {"ready", "retiring"}:
            _fail("PROOF_MISSING")
        sidecar, sidecar_raw, _sidecar_identity = self.reader.document(
            destination.with_name(destination.name + backups.SUFFIX))
        descriptor = backups._descriptor(sidecar, record)
        if (record["descriptor"] != descriptor or record["descriptor_sha256"] != _sha(sidecar_raw)
                or descriptor["source_roots"] != backups._roots(self.profile)
                or descriptor["source_version"] != version or descriptor["schema_version"] != schema_version):
            _fail("PROOF_CHANGED")
        actual = {"backup_id": identifier, "descriptor_sha256": _sha(sidecar_raw),
                  "image_sha256": descriptor["database_file"]["sha256"]}
        if facts is not None and actual != facts:
            _fail("PROOF_CHANGED")
        _raw, identity = self.reader.read(destination, maximum=backups.MAX_IMAGE, metadata=False,
            expected_sha=actual["image_sha256"], expected_size=descriptor["database_file"]["byte_size"], capture=False)
        if identity != descriptor["database_file"]:
            _fail("PROOF_CHANGED")
        return actual

    def bundle(self, operation_id, *, complete_required=False):
        if operation_id in self.bundle_facts:
            found = self.bundle_facts[operation_id]
            if complete_required and (found is None or found.get("receipt_sha256") is None):
                _fail("PROOF_MISSING")
            return found
        bundle_id, backup_id = bundles._ids(operation_id)
        operation = self.profile.database.parent / bundles.OPERATIONS
        reservation, raw, _identity = self.reader.document(operation / (operation_id + ".json"), maximum=bundles.MAX_METADATA_BYTES)
        bundles._reservation(reservation, self.profile, operation_id, reservation.get("root_job_id"))
        self.binding(reservation)
        sync_snapshot._version(reservation["knowledge_version"])
        if reservation["knowledge_version"]["library_id"] != reservation["library_id"]:
            _fail("IDENTITY_MISMATCH")
        destination = self.profile.database.with_name(self.profile.database.name + ".bak-preservation17-" + backup_id)
        root = self.profile.database.parent / bundles.DIRECTORY / bundle_id
        names = self.reader.namespace(root)
        if names is None or any(name not in {"certificate.json", "manifest.json", "files"} and not _PART.fullmatch(name) for name in names):
            _fail("PROOF_INVALID")
        receipt_path = operation / (operation_id + ".receipt.json")
        completed = self.reader.present(receipt_path)
        if complete_required and not completed:
            _fail("PROOF_MISSING")
        result = {"reservation_sha256": _sha(raw), "manifest": None, "certificate": None, "receipt_sha256": None}
        if "manifest.json" not in names:
            if completed:
                _fail("PROOF_MISSING")
            self.backup(backup_id, destination=destination, version=reservation["knowledge_version"], schema_version=17)
            self.bundle_facts[operation_id] = result
            return result
        manifest, manifest_raw, _identity = self.reader.document(root / "manifest.json", maximum=bundles.MAX_METADATA_BYTES)
        certificate_raw, _identity = self.reader.read(root / "certificate.json", maximum=bundles.base.MAX_CERTIFICATE_BYTES)
        document = bundles._bundle_certificate(certificate_raw, reservation)
        expected = {"format_version": 1, "bundle_id": bundle_id, "operation_id": operation_id,
            "owner_profile_sha256": reservation["owner_profile_sha256"], "library_id": reservation["library_id"],
            "node_id": reservation["node_id"], "knowledge_version": reservation["knowledge_version"],
            "database_backup": manifest.get("database_backup"), "certificate": {"format_version": 2,
                "sha256": reservation["certificate_sha256"], "byte_size": reservation["certificate_bytes"]},
            "permanent_witnesses": document["permanent_witnesses"], "files": reservation["files"],
            "file_set_sha256": reservation["file_set_sha256"], "created_at": reservation["created_at"]}
        expected["manifest_sha256"] = _digest(expected)
        if set(manifest) != bundles._MANIFEST_FIELDS or manifest != expected:
            _fail("PROOF_INVALID")
        bundles._checkpoint_wrapper(document, manifest)
        facts = manifest["database_backup"]
        if type(facts) is not dict or set(facts) != {"backup_id", "image_sha256", "descriptor_sha256"} or facts["backup_id"] != backup_id:
            _fail("PROOF_INVALID")
        self.backup(backup_id, destination=destination, version=reservation["knowledge_version"],
                    schema_version=17, facts=facts, required=True)
        copies = root / "files"
        copy_names = self.reader.namespace(copies)
        if copy_names != frozenset(bundles._copy_name(row) for row in reservation["files"]):
            _fail("PROOF_MISSING")
        identities = []
        for entry in reservation["files"]:
            name = bundles._copy_name(entry)
            _raw, identity = self.reader.read(copies / name, maximum=bundles.MAX_FILE_BYTES, metadata=False,
                expected_sha=entry["sha256"], expected_size=entry["byte_size"], capture=False)
            identities.append({"name": name, "identity": identity})
        for name in ("certificate.json", "manifest.json"):
            _raw, identity = self.reader.read(root / name, maximum=bundles.MAX_METADATA_BYTES)
            identities.append({"name": name, "identity": identity})
        result.update(manifest=manifest, certificate=document)
        if completed:
            receipt, receipt_raw, _identity = self.reader.document(receipt_path, maximum=bundles.MAX_METADATA_BYTES)
            if (set(receipt) != bundles._RECEIPT_FIELDS or type(receipt["format_version"]) is not int
                    or receipt["format_version"] != 1 or receipt["action"] != "preserve_home17"
                    or receipt["operation_id"] != operation_id or receipt["bundle_id"] != bundle_id
                    or receipt["reservation_sha256"] != _sha(raw) or receipt["manifest_sha256"] != manifest["manifest_sha256"]
                    or receipt["certificate_sha256"] != reservation["certificate_sha256"]
                    or receipt["created_at"] != reservation["created_at"] or receipt["files"] != identities):
                _fail("PROOF_INVALID")
            result["receipt_sha256"] = _sha(receipt_raw)
        self.bundle_facts[operation_id] = result
        return result

    def source(self, source_id, *, public=None, pending_only=False):
        path = source16._source_path(self.profile, source_id)
        intent_path = path.with_name(source_id + ".intent.json")
        intent, intent_raw, _identity = self.reader.document(intent_path, maximum=source16.MAX_DECLARATION_BYTES)
        parser = source17 if intent.get("schema_version") == 17 else source16
        intent = parser._intent(intent_raw)
        self.binding(intent, source=True)
        if intent["source_id"] != source_id:
            _fail("IDENTITY_MISMATCH")
        present = self.reader.present(path)
        if parser is source17:
            companion_path = path.with_name(source_id + ".sql17.json")
            if self.reader.present(companion_path):
                companion_raw, _identity = self.reader.read(companion_path, maximum=source17.MAX_DECLARATION_BYTES)
                companion = source17._companion(companion_raw, intent, _sha(intent_raw))
                if companion_raw != _json(companion):
                    _fail("PROOF_INVALID")
            elif present or intent["backup_id"] in self.records:
                _fail("PROOF_MISSING")
        declaration = None
        if present:
            declaration_raw, _identity = self.reader.read(path, maximum=source16.MAX_DECLARATION_BYTES)
            declaration = parser._declaration(declaration_raw)
            if (declaration_raw != _json(declaration)
                    or any(declaration[key] != intent[key] for key in source16._INTENT_FIELDS - {"action"})):
                _fail("PROOF_CHANGED")
        elif public is not None:
            _fail("PROOF_MISSING")
        if pending_only and declaration is not None:
            # Completion retires only this conditional producer hold. Do not
            # pin its unreferenced backup/catalog: retention may delete them.
            return False
        if public is not None:
            if (type(public) is not dict or set(public) != _PUBLIC_SOURCE or public["source_kind"] != "home_checkpoint"
                    or public["source_id"] != source_id or public["source_version"] != intent["source_version"]
                    or public["source_sha256"] != declaration["image_sha256"]
                    or public["source_binding_sha256"] != _digest(declaration)
                    or public["knowledge_sha256"] != intent["knowledge_sha256"]
                    or type(public["row_counts"]) is not dict or set(public["row_counts"]) != set(source16.COLUMNS)
                    or any(type(n) is not int or not 0 <= n <= source16.MAX_ROWS for n in public["row_counts"].values())):
                _fail("PROOF_INVALID")
        facts = None if declaration is None else {"backup_id": intent["backup_id"],
            "image_sha256": declaration["image_sha256"], "descriptor_sha256": declaration["descriptor_sha256"]}
        destination = self.profile.database.with_name(self.profile.database.name + ".bak-home-knowledge-" + source_id)
        self.backup(intent["backup_id"], destination=destination, version=intent["source_version"],
                    schema_version=intent["schema_version"], facts=facts, required=public is not None)
        return True

    def stored_source(self, source_id, public):
        """A completed restore retains its exact old image, not fresh rights.

        Declaration/intent retirement cannot release a permanently referenced
        image. The original request fixes the v1 UUID/version/image SHA; the
        actual catalog/sidecar/image supplies its observed storage facts. No
        missing historical source provenance is reconstructed here.
        """
        if (type(public) is not dict or set(public) != _PUBLIC_SOURCE or public["source_kind"] != "home_checkpoint"
                or public["source_id"] != source_id or type(public["row_counts"]) is not dict
                or set(public["row_counts"]) != set(source16.COLUMNS)
                or any(type(n) is not int or not 0 <= n <= source16.MAX_ROWS for n in public["row_counts"].values())):
            _fail("PROOF_INVALID")
        sync_snapshot._version(public["source_version"])
        for key in ("source_sha256", "source_binding_sha256", "knowledge_sha256"):
            _hash(public[key])
        identifier = source17._backup_id(source_id)
        record = self.records.get(identifier)
        if record is None or record["state"] not in {"ready", "retiring"}:
            _fail("PROOF_MISSING")
        descriptor = record["descriptor"]
        schema_version = descriptor["schema_version"]
        if type(schema_version) is not int or not 10 <= schema_version <= 17:
            _fail("PROOF_INVALID")
        self.backup(identifier,
            destination=self.profile.database.with_name(self.profile.database.name + ".bak-home-knowledge-" + source_id),
            version=public["source_version"], schema_version=schema_version,
            facts={"backup_id": identifier, "image_sha256": public["source_sha256"],
                   "descriptor_sha256": record["descriptor_sha256"]}, required=True)

    def restore(self, operation_id):
        root = self.profile.database.parent / restore17.DIRECTORY
        request, request_raw, _identity = self.reader.document(root / (operation_id + ".request.json"), maximum=restore17.MAX_METADATA_BYTES)
        if (set(request) != restore17._REQUEST_FIELDS or type(request["format_version"]) is not int
                or request["format_version"] != 2 or request["action"] != "home_knowledge_restore"
                or request["operation_id"] != operation_id or request["request_sha256"] != _digest(request["request"])
                or request["bundle_id"] != bundles._ids(operation_id)[0]):
            _fail("PROOF_INVALID")
        self.binding(request)
        nested, public = request["request"], request["source"]
        if (type(nested) is not dict or set(nested) != {"action", "source_id", "expected_digest"}
                or nested["action"] != "knowledge_restore" or type(public) is not dict):
            _fail("PROOF_INVALID")
        source_id = backups._uuid(nested["source_id"])
        _hash(nested["expected_digest"])
        _hash(request["before_capture_sha256"])
        source_backup = source17._backup_id(source_id)
        self.holds.add(source_backup)
        if set(public) != _PUBLIC_SOURCE:
            _fail("PROOF_INVALID")
        sync_snapshot._version(public["source_version"])
        if public["source_version"]["library_id"] != request["library_id"]:
            _fail("IDENTITY_MISMATCH")
        intent_path = root / (operation_id + ".intent.json")
        if not self.reader.present(intent_path):
            completed = self.bundle_facts.get(operation_id)
            if completed is not None and completed["manifest"] is not None:
                manifest = completed["manifest"]
                if (request["before_capture_sha256"] != manifest["certificate"]["sha256"]
                        or request["library_id"] != manifest["library_id"] or request["node_id"] != manifest["node_id"]):
                    _fail("PROOF_INVALID")
            if self.reader.present(root / (operation_id + ".receipt.json")):
                _fail("PROOF_MISSING")
            self.source(source_id, public=public)
            return
        intent, _raw, _identity = self.reader.document(intent_path, maximum=restore17.MAX_METADATA_BYTES)
        facts = self.bundle(operation_id, complete_required=True)
        manifest = facts["manifest"]
        expected = {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation_id,
            "request_sha256": request["request_sha256"], "reservation_sha256": _sha(request_raw),
            "bundle_id": manifest["bundle_id"], "manifest_sha256": manifest["manifest_sha256"],
            "bundle_receipt_sha256": facts["receipt_sha256"], "certificate_sha256": manifest["certificate"]["sha256"],
            "file_set_sha256": manifest["file_set_sha256"]}
        if (set(intent) != restore17._INTENT_FIELDS or intent != expected
                or request["before_capture_sha256"] != manifest["certificate"]["sha256"]
                or request["library_id"] != manifest["library_id"] or request["node_id"] != manifest["node_id"]):
            _fail("PROOF_INVALID")
        receipt_path = root / (operation_id + ".receipt.json")
        if not self.reader.present(receipt_path):
            self.source(source_id, public=public)
            return
        # Only the actual original permanent completion chain selects this
        # maintenance fallback; a request/intent, caller DTO or absent SQL row
        # cannot masquerade as a completed restore.
        intent_sha = _digest(intent)
        proposal, proposal_raw, _identity = self.reader.document(root / (operation_id + ".proposal.json"), maximum=restore17.MAX_METADATA_BYTES)
        receipt, _raw, _identity = self.reader.document(receipt_path, maximum=restore17.MAX_METADATA_BYTES)
        if (set(proposal) != restore17._PROPOSAL_FIELDS or set(receipt) != restore17._RECEIPT_FIELDS
                or type(proposal["format_version"]) is not int or proposal["format_version"] != 2
                or proposal["action"] != "home_knowledge_restore" or proposal["operation_id"] != operation_id
                or proposal["intent_sha256"] != intent_sha
                or type(receipt["format_version"]) is not int or receipt["format_version"] != 2
                or receipt["action"] != "home_knowledge_restore" or receipt["operation_id"] != operation_id
                or receipt["intent_sha256"] != intent_sha or receipt["proposal_sha256"] != _sha(proposal_raw)
                or receipt["row_sha256"] != proposal["row_sha256"]
                or receipt["committed_at"] != proposal["committed_at"]
                or type(receipt["committed_at"]) is not str or not backups._TIME.fullmatch(receipt["committed_at"])
                or receipt["bundle_receipt_sha256"] != facts["receipt_sha256"]
                or receipt["checkpoint_sha256"] != _digest(bundles._checkpoint_wrapper(facts["certificate"], manifest))):
            _fail("PROOF_INVALID")
        for key in ("row_sha256", "result_sha256", "checkpoint_sha256"):
            _hash(receipt[key])
        self.stored_source(source_id, public)
        self.completed_sources.add(source_id)

    def scan(self):
        root = self.profile.database.parent
        operation_names = self.reader.namespace(root / bundles.OPERATIONS)
        restore_names = self.reader.namespace(root / restore17.DIRECTORY)
        source_names = self.reader.namespace(root / source16.DIRECTORY)
        bundle_names = self.reader.namespace(root / bundles.DIRECTORY)
        operations, bad = _ids(operation_names, (".receipt.json", ".json"))
        restores, bad_restore = _ids(restore_names, (".request.json", ".intent.json", ".proposal.json", ".receipt.json"))
        sources, bad_source = _ids(source_names, (".intent.json", ".sql17.json", ".json"))
        if bad or bad_restore or bad_source:
            self.unknown.add("NAMESPACE_UNPROVEN")
        if operations or restores or sources:
            try:
                self.identity()
            except (ValueError, OSError, KeyError, TypeError) as exc:
                if exc is self.reader.cancelled or isinstance(exc, OperationHoldError) and exc.code.endswith("_LIMIT"):
                    raise
                self.unknown.add("IDENTITY_UNPROVEN")
        expected_bundles = set()
        for operation_id in operations:
            self.reader.operation()
            bundle_id, backup_id = bundles._ids(operation_id)
            expected_bundles.add(bundle_id)
            self.protect(backup_id, lambda identifier=operation_id: self.bundle(identifier), "BUNDLE_PROOF_UNPROVEN")
        if bundle_names is not None and any(name not in expected_bundles and not _PART.fullmatch(name) for name in bundle_names):
            self.unknown.add("BUNDLE_NAMESPACE_UNPROVEN")
        for operation_id in restores:
            self.reader.operation()
            self.protect(bundles._ids(operation_id)[1], lambda identifier=operation_id: self.restore(identifier), "RESTORE_PROOF_UNPROVEN")
        for source_id in sources:
            self.reader.operation()
            if source_id in self.completed_sources:
                # The completed operation already holds the exact image. Its
                # retired fresh-source metadata cannot revive a pending job.
                continue
            identifier = source17._backup_id(source_id)
            was_held = identifier in self.holds
            completed = self.protect(identifier, lambda sid=source_id: self.source(sid, pending_only=True), "SOURCE_PROOF_UNPROVEN")
            if completed is False and not was_held:
                self.holds.remove(identifier)


@contextmanager
def capture_operation_holds_locked(profile, records, check_cancelled=None, limits=None):
    """Borrow EX; preserve exact file-derived original backup dependencies.

    ``unknown`` is a controlled reason set. Its presence must prevent callers
    from interpreting this capture as an exhaustive empty/complete hold graph.
    Full verification is for deletion boundaries outside SQL writers; the short
    method checks identities/closed selections without any complete file SHA.
    """
    profile = lifecycle._profile(profile)
    guard = lifecycle.require_guard(profile, exclusive=True)
    if type(records) is not dict or check_cancelled is not None and not callable(check_cancelled):
        _fail("ARGUMENT_INVALID")
    limits = OperationHoldLimits() if limits is None else limits
    if type(limits) is not OperationHoldLimits:
        _fail("ARGUMENT_INVALID")
    if (any(type(getattr(limits, key)) is not int or getattr(limits, key) < 1 for key in
            ("max_operations", "max_files", "max_metadata_bytes", "max_read_bytes"))
            or type(limits.max_seconds) not in (int, float) or not 0 < limits.max_seconds <= 120
            or limits.max_operations > 4096 or limits.max_files > 100_000
            or limits.max_metadata_bytes > 128 * 1024 * 1024 or limits.max_read_bytes > 8 * 1024 * 1024 * 1024):
        _fail("ARGUMENT_INVALID")
    with ExitStack() as stack:
        reader = _Reader(stack, profile, guard, check_cancelled, limits)
        scanner = _Scanner(reader, records)
        try:
            scanner.scan()
        except (ValueError, OSError, KeyError, TypeError, UnicodeError, RecursionError) as exc:
            if exc is reader.cancelled or isinstance(exc, OperationHoldError) and exc.code.endswith("_LIMIT"):
                raise
            scanner.unknown.add("NAMESPACE_UNPROVEN")
        capture = OperationHoldCapture(frozenset(scanner.holds), frozenset(scanner.unknown), reader)
        capture.verify_source()
        yield capture
        capture.verify_source()
