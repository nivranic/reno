"""Explicit preservation of original personal facts and mirror raw history.

Independent private packages do not grant restoration, dispatch or primary
authority. No generic backup, artifact namespace or retention contract changes.
All producers hold the original profile EX; original source observers must
successfully exit before a durable source-verified fact is published.
"""
from __future__ import annotations

from contextlib import ExitStack
from datetime import datetime, timezone
import hashlib
import os
from pathlib import Path
import stat
import time
import uuid

from . import personal_preservation_contract as contract
from . import personal_preservation_files as files
from . import personal_preservation_source as source
from . import storage_lifecycle as lifecycle, sync_media as safe

MAX_JSON = 128 * 1024 * 1024


class PersonalPreservationError(ValueError):
    def __init__(self, code, *, retriable=False):
        super().__init__("Personal preservation could not be completed safely")
        self.code, self.retriable = code, retriable


def _fail(code, *, retriable=False):
    raise PersonalPreservationError("PERSONAL_PRESERVATION_" + code,
                                    retriable=retriable)


def _stamp():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _checkpoint(phase):
    """A phase boundary for deterministic crash tests; no production callback."""


class _Budget:
    def __init__(self, limits, check):
        self.limits, self.check = limits, check or (lambda: None)
        self.deadline = time.monotonic() + limits["max_seconds"]

    def __call__(self):
        self.check()
        if time.monotonic() >= self.deadline:
            _fail("DEADLINE_LIMIT", retriable=True)


def _enter_directory(stack, parent, name, *, create=False):
    if create:
        files.mkdir_child(parent, name)
    directory = stack.enter_context(safe._Directory(parent.path / name, parent=parent))
    files.require_private(directory.path, directory=True)
    return directory


def _fact(stack, directory, name, validator, *, required=True, check=None, **kwargs):
    try:
        held = stack.enter_context(files.FileHold(directory.path / name,
            maximum=MAX_JSON, check_cancelled=check))
    except FileNotFoundError:
        if required:
            _fail("FACT_MISSING")
        return None, None
    files.require_private(held.path, directory=False)
    value = validator(contract.decode(held.raw), **kwargs)
    if contract.canonical_bytes(value) != held.raw:
        _fail("FACT_NONCANONICAL")
    stack.__dict__.setdefault("_preservation_facts", []).append(held)
    return value, held


def _verify_facts(stack):
    for held in stack.__dict__.get("_preservation_facts", ()):
        held.verify()


def _publish_fact(stack, directory, name, value, validator, *, check=None, **kwargs):
    value = validator(value, **kwargs)
    raw = contract.canonical_bytes(value)
    if len(raw) > MAX_JSON:
        _fail("FACT_TOO_LARGE")
    files.publish(directory, name, raw)
    result, held = _fact(stack, directory, name, validator, check=check, **kwargs)
    if result != value:
        _fail("FACT_CHANGED")
    return result, held


def _prefix(operation_id):
    # Pure request validation also checks this, before any source reads.
    try:
        canonical = str(uuid.UUID(operation_id))
    except (ValueError, TypeError, AttributeError):
        _fail("OPERATION_INVALID")
    if canonical != operation_id:
        _fail("OPERATION_INVALID")
    return operation_id


def _profile(profile):
    return {name: lifecycle._key(getattr(profile, name)) for name in
            ("database", "media", "reports", "checkpoints")}


def _request(profile, operation_id, limits, data, bundles):
    bundle_id = str(uuid.uuid5(uuid.UUID(operation_id),
                             "reno.personal.preservation.bundle.v1"))
    target = bundles.path / bundle_id
    # Never allow the package to become a selected live source root.
    for root in (profile.database, profile.media, profile.reports, profile.checkpoints):
        root_key, target_key = Path(lifecycle._key(root)), Path(lifecycle._key(target))
        if root_key == target_key or root_key.is_relative_to(target_key) or target_key.is_relative_to(root_key):
            _fail("TARGET_SOURCE_OVERLAP")
    return contract.validate_request({"format_version": 1,
        "kind": "personal_preservation_request", "operation_id": operation_id,
        "bundle_id": bundle_id, "source_profile": _profile(profile),
        "source_root_identity": files.directory_identity(data),
        "target_parent_identity": files.directory_identity(bundles),
        "target": lifecycle._key(target),
        "scope": "personal-facts-and-mirror-raw-v1", "limits": limits})


def _state(catalog, request, phase, *, selection=None, verified=None,
           manifest=None, receipt=None, failure=None):
    value = {"format_version": 1, "kind": "personal_preservation_state",
        "operation_id": request["operation_id"], "request_sha256": contract.digest(request),
        "phase": phase, "selection_sha256": contract.digest(selection) if selection else None,
        "source_verified_sha256": contract.digest(verified) if verified else None,
        "manifest_sha256": contract.digest(manifest) if manifest else None,
        "receipt_sha256": contract.digest(receipt) if receipt else None,
        "updated_at": _stamp(), "failure": failure}
    contract.validate_state(value, request=request, selection=selection,
        source_verified=verified, manifest=manifest, receipt=receipt)
    files.publish(catalog, request["operation_id"] + ".state.json",
                  contract.canonical_bytes(value), replace=True)


def _copy_digest_set(records):
    return [{"entry_id": record["entry_id"], "sha256": contract.digest(record)}
            for record in records]


def _load_copies(stack, target, copy_index, selection, request, check, *, repair=False):
    records_directory = _enter_directory(stack, target, "copy-records")
    attempts_directory = _enter_directory(stack, target, "attempts")
    records, copies = [], {}
    selected_ids = {entry["entry_id"] for entry in selection["files"]}
    for directory in (records_directory, copy_index):
        for name in directory.names():
            if name.startswith(".preservation-write-") and name.endswith(".tmp"):
                stack.enter_context(files.FileHold(directory.path / name,
                    maximum=MAX_JSON, check_cancelled=check))
                continue
            if not name.endswith(".json") or name[:-5] not in selected_ids:
                _fail("COPY_UNDECLARED")
    for entry in selection["files"]:
        check()
        entry_id = entry["entry_id"]
        record, record_held = _fact(stack, records_directory, entry_id + ".json",
            lambda value: value, required=False, check=check)
        anchor, anchor_held = _fact(stack, copy_index, entry_id + ".json",
            lambda value: value, required=False, check=check)
        if record is not None and anchor is None:
            _fail("FIRST_COPY_IDENTITY_MISSING")
        if anchor is None:
            continue
        if record is not None and (record != anchor or record_held.raw != anchor_held.raw):
            _fail("FIRST_COPY_RECORD_CHANGED")
        attempt_name = anchor.get("attempt_id", "") + "-" + entry_id + ".json"
        attempt, _held = _fact(stack, attempts_directory, attempt_name,
            contract.validate_copy_attempt, check=check, selection=selection, request=request)
        record = contract.validate_copy_record(anchor, selection=selection,
                                               attempt=attempt, request=request)
        held = stack.enter_context(files.FileHold(target.path / record["target_relative_key"],
            maximum=request["limits"]["max_bytes"], check_cancelled=check, collect=False))
        files.require_private(held.path, directory=False)
        if (held.sha256 != entry["sha256"] or held.path_identity != record["copy_path_identity"]
                or held.handle_identity != record["copy_handle_identity"]
                or held.directory_identity != record["target_parent_identity"]):
            _fail("COPY_CHANGED")
        if record_held is None:
            if not repair:
                _fail("COPY_RECORD_MISSING")
            # The independently published first identity is the only permitted
            # reconstruction. The original payload remains the same inode.
            record, record_held = _publish_fact(stack, records_directory, entry_id + ".json",
                record, contract.validate_copy_record, selection=selection,
                attempt=attempt, request=request, check=check)
        anchor_held.verify()
        records.append(record)
        copies[entry_id] = held
    return records, copies, records_directory, attempts_directory


def _copy_one(stack, target, payload, copy_index, record_directory, attempts_directory,
              selection, request, entry, original, attempt_id, check):
    entry_id = entry["entry_id"]
    attempt_directory = _enter_directory(stack, payload, attempt_id, create=True)
    attempt = {"format_version": 1, "kind": "personal_preservation_copy_attempt",
        "operation_id": request["operation_id"], "selection_sha256": contract.digest(selection),
        "entry_id": entry_id, "attempt_id": attempt_id,
        "target_relative_key": f"payload/{attempt_id}/{entry_id}.raw",
        "target_parent_identity": files.directory_identity(attempt_directory),
        "sha256": entry["sha256"], "byte_size": entry["byte_size"]}
    attempt, _held = _publish_fact(stack, attempts_directory,
        attempt_id + "-" + entry_id + ".json", attempt, contract.validate_copy_attempt,
        selection=selection, request=request, check=check)
    _checkpoint("copy_attempt")
    check()
    original.verify()
    original.stream.seek(0)
    with attempt_directory.open_file(entry_id + ".raw", write=True, create=True) as stream:
        if os.name == "nt":
            files._windows_acl(attempt_directory.path / (entry_id + ".raw"),
                               directory=False, initialize=True)
        digest, written = hashlib.sha256(), 0
        while True:
            check()
            chunk = original.stream.read(128 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > entry["byte_size"]:
                _fail("SOURCE_CHANGED", retriable=True)
            stream.write(chunk)
            digest.update(chunk)
        stream.flush()
        os.fsync(stream.fileno())
        if written != entry["byte_size"] or digest.hexdigest() != entry["sha256"]:
            _fail("SOURCE_CHANGED", retriable=True)
    if os.name != "nt":
        os.fsync(attempt_directory.fd)
    attempt_directory.verify()
    original.verify()
    _checkpoint("copy")
    copied = stack.enter_context(files.FileHold(target.path / attempt["target_relative_key"],
        maximum=request["limits"]["max_bytes"], check_cancelled=check, collect=False))
    record = {**attempt, "kind": "personal_preservation_copy_record",
              "copy_path_identity": copied.path_identity,
              "copy_handle_identity": copied.handle_identity}
    record, _anchor_held = _publish_fact(stack, copy_index, entry_id + ".json", record,
        contract.validate_copy_record, selection=selection, attempt=attempt, request=request, check=check)
    _checkpoint("first_copy_record")
    record, _held = _publish_fact(stack, record_directory, entry_id + ".json", record,
        contract.validate_copy_record, selection=selection, attempt=attempt, request=request, check=check)
    _checkpoint("copy_record")
    copied.verify()
    return record, copied


def _check_payload_paths(stack, target, selection, request, check):
    """Unrecorded attempts remain evidence, never adopted as successful copies."""
    attempts = _enter_directory(stack, target, "attempts")
    for name in target.names():
        if name in {"payload", "copy-records", "attempts", "manifest.json"}:
            continue
        if name.startswith(".preservation-write-") and name.endswith(".tmp"):
            stack.enter_context(files.FileHold(target.path / name, maximum=MAX_JSON, check_cancelled=check))
            continue
        _fail("PACKAGE_UNDECLARED")
    allowed = set()
    for name in attempts.names():
        check()
        if name.startswith(".preservation-write-") and name.endswith(".tmp"):
            # Atomic publication may leave private raw evidence. It has no role.
            stack.enter_context(files.FileHold(attempts.path / name,
                maximum=MAX_JSON, check_cancelled=check))
            continue
        attempt, _held = _fact(stack, attempts, name, contract.validate_copy_attempt,
            selection=selection, request=request, check=check)
        if name != attempt["attempt_id"] + "-" + attempt["entry_id"] + ".json":
            _fail("ATTEMPT_INVALID")
        allowed.add(attempt["target_relative_key"])
    payload = _enter_directory(stack, target, "payload")
    for name in payload.names():
        check()
        child = _enter_directory(stack, payload, name)
        for filename in child.names():
            if f"payload/{name}/{filename}" not in allowed:
                _fail("COPY_UNDECLARED")
            info = files.identity(child.entry_stat(filename))
            if info["byte_size"] > request["limits"]["max_bytes"]:
                _fail("BYTE_LIMIT")
    return allowed


def _verify_copies(records, copies, selection, check):
    if [r["entry_id"] for r in records] != [e["entry_id"] for e in selection["files"]]:
        _fail("COPY_INCOMPLETE")
    for held in copies.values():
        check()
        files.require_private(held.path, directory=False)
        held.verify()
    source.verify_sql_payload(selection, copies, check_cancelled=check)
    for held in copies.values():
        held.verify()


def _seal_layout(stack, target, check):
    """Retain final package directory names until all readers have finished."""
    observations = []
    def walk(directory):
        check()
        names = tuple(sorted(directory.names()))
        observations.append((directory, files.directory_identity(directory), names))
        for name in names:
            info = directory.entry_stat(name)
            if stat.S_ISDIR(info.st_mode):
                walk(_enter_directory(stack, directory, name))
            else:
                files.identity(info)
    walk(target)
    def finish(exc_type, _exc, _tb):
        if exc_type is None:
            for directory, identity, names in observations:
                check()
                directory.verify()
                if (files.directory_identity(directory) != identity
                        or tuple(sorted(directory.names())) != names):
                    _fail("PACKAGE_CHANGED", retriable=True)
        return False
    stack.push(finish)


def _coverage(selection):
    return {"personal_original_facts": "complete", "sender_raw_history": "complete",
        "receiver_original_facts": "complete", "unknowns_present": bool(selection["unknowns"]),
        "knowledge_restore_dependencies": "unknown", "runtime_restore_authority": False,
        "primary_handoff_authority": False}


def _public(request, selection, phase):
    return {"operation_id": request["operation_id"], "phase": phase,
        "file_count": len(selection["files"]) if selection else 0,
        "byte_size": sum(entry["byte_size"] for entry in selection["files"]) if selection else 0}


def _record_failure(stack, catalog, request, phase, attempt_id, exc,
                    selection, verified, manifest, receipt):
    code = getattr(exc, "code", None)
    if (type(code) is not str or not code.startswith("PERSONAL_PRESERVATION_")
            or not all(c.isupper() or c.isdigit() or c == "_" for c in code)):
        code = "PERSONAL_PRESERVATION_CAPTURE_FAILED"
    failure = {"code": code, "phase": phase, "attempt_id": attempt_id,
        "original_digests": {"request_sha256": contract.digest(request),
            "selection_sha256": contract.digest(selection) if selection else None,
            "source_verified_sha256": contract.digest(verified) if verified else None,
            "manifest_sha256": contract.digest(manifest) if manifest else None,
            "receipt_sha256": contract.digest(receipt) if receipt else None}}
    directory = _enter_directory(stack, catalog, request["operation_id"] + ".failures", create=True)
    files.publish(directory, attempt_id + ".json", contract.canonical_bytes(failure))
    _state(catalog, request, "failed", selection=selection, verified=verified,
           manifest=manifest, receipt=receipt, failure=failure)


def _run_locked(profile, *, operation_id, limits=None, expected_request_sha256=None,
                resume=False, check_cancelled=None, readonly=False):
    lifecycle.require_guard(profile, exclusive=not readonly)
    operation_id = _prefix(operation_id)
    requested_limits = contract.validate_limits(dict(contract.DEFAULT_LIMITS) if limits is None else limits)
    # A plain argument check precedes filesystem creation, but the actual
    # request still binds original parent identities obtained under EX.
    budget = _Budget(requested_limits, check_cancelled)
    with ExitStack() as stack:
        data = stack.enter_context(safe._Directory(profile.database.parent, base=profile.database.parent))
        catalog = _enter_directory(stack, data, contract.CATALOG, create=not readonly and not resume)
        bundles = _enter_directory(stack, data, contract.DIRECTORY, create=not readonly and not resume)
        request, request_held = _fact(stack, catalog, operation_id + ".request.json",
            contract.validate_request, required=resume or readonly, check=budget)
        is_new = request is None
        if is_new:
            request = _request(profile, operation_id, requested_limits, data, bundles)
            try:
                bundles.entry_stat(request["bundle_id"])
            except FileNotFoundError:
                pass
            else:
                _fail("TARGET_ALREADY_EXISTS")
            request, request_held = _publish_fact(stack, catalog, operation_id + ".request.json",
                request, contract.validate_request, check=budget)
        else:
            if (request["source_profile"] != _profile(profile)
                    or request["source_root_identity"] != files.directory_identity(data)
                    or request["target_parent_identity"] != files.directory_identity(bundles)):
                _fail("OPERATION_CONFLICT")
            if not resume and not readonly and request["limits"] != requested_limits:
                _fail("OPERATION_CONFLICT")
            budget = _Budget(request["limits"], check_cancelled)
        if expected_request_sha256 is not None and request_held.sha256 != expected_request_sha256:
            _fail("OPERATION_CONFLICT")
        if resume and expected_request_sha256 is None:
            _fail("REQUEST_SHA_REQUIRED")
        attempt_id = str(uuid.uuid4())
        selection = verified = manifest = receipt = None
        phase = "reserved"
        try:
            if is_new:
                _state(catalog, request, phase)
                _checkpoint("reserve")
            selection, selection_held = _fact(stack, catalog, operation_id + ".selection.json",
                contract.validate_selection, required=False, check=budget, request=request)
            if selection is None and not is_new:
                _fail("CAPTURE_INCOMPLETE")
            if selection is None and readonly:
                _fail("CAPTURE_INCOMPLETE")
            if selection is not None:
                verified, _held = _fact(stack, catalog, operation_id + ".source-verified.json",
                    lambda value: value, required=False, check=budget)
            target = _enter_directory(stack, bundles, request["bundle_id"], create=not readonly)
            for name in ("payload", "copy-records", "attempts"):
                if not readonly:
                    _enter_directory(stack, target, name, create=True)
            copy_index = _enter_directory(stack, catalog, operation_id + ".copy-index", create=is_new)
            if selection is None or verified is None:
                if readonly:
                    _fail("SOURCE_UNVERIFIED")
                # A repeat capture is only a comparison against the ORIGINAL
                # fixed selection. It can never become a replacement selection.
                with source.capture_locked(profile, operation_id=operation_id,
                        request_sha256=request_held.sha256, limits=request["limits"],
                        check_cancelled=budget) as captured:
                    if selection is None:
                        selection, selection_held = _publish_fact(stack, catalog,
                            operation_id + ".selection.json", captured.selection,
                            contract.validate_selection, request=request, check=budget)
                    elif captured.selection != selection:
                        _fail("SOURCE_CHANGED", retriable=True)
                    phase = "selected"
                    _state(catalog, request, phase, selection=selection)
                    _checkpoint("selection")
                    records, copies, record_dir, attempt_dir = _load_copies(stack, target,
                        copy_index, selection, request, budget, repair=True)
                    payload = _enter_directory(stack, target, "payload")
                    for entry in selection["files"]:
                        if entry["entry_id"] in copies:
                            continue
                        record, held = _copy_one(stack, target, payload, copy_index, record_dir, attempt_dir,
                            selection, request, entry, captured.files[entry["entry_id"]], attempt_id, budget)
                        records.append(record)
                        copies[entry["entry_id"]] = held
                    records.sort(key=lambda item: item["entry_id"])
                    _check_payload_paths(stack, target, selection, request, budget)
                    _verify_copies(records, copies, selection, budget)
                    _verify_facts(stack)
                    captured.verify()
                    _checkpoint("source_check")
                # This is intentionally OUTSIDE all original source contexts.
                # Their successful exits, including their final checks, must
                # precede the only durable original-source completion fact.
                lifecycle.require_guard(profile, exclusive=True).verify()
                _verify_copies(records, copies, selection, budget)
                _verify_facts(stack)
                verified = {"format_version": 1, "kind": "personal_preservation_source_verified",
                    "operation_id": operation_id, "request_sha256": request_held.sha256,
                    "selection_sha256": selection_held.sha256, "attempt_id": attempt_id,
                    "sql_fingerprint_sha256": contract.digest(selection["sql"]["source_fingerprint"]),
                    "copy_record_sha256s": _copy_digest_set(records), "completed_at": _stamp()}
                verified, _held = _publish_fact(stack, catalog, operation_id + ".source-verified.json",
                    verified, contract.validate_source_verified, selection=selection,
                    copy_records=records, request=request, check=budget)
                phase = "source_verified"
                _state(catalog, request, phase, selection=selection, verified=verified)
                _checkpoint("source_verified")
            else:
                records, copies, _record_dir, _attempt_dir = _load_copies(stack, target,
                    copy_index, selection, request, budget, repair=not readonly)
                verified = contract.validate_source_verified(verified, selection=selection,
                    copy_records=records, request=request)
                _check_payload_paths(stack, target, selection, request, budget)
                _verify_copies(records, copies, selection, budget)
                phase = "source_verified"
            manifest, _held = _fact(stack, target, "manifest.json", contract.validate_manifest,
                required=False, check=budget, selection=selection, source_verified=verified,
                copy_records=records, request=request)
            if manifest is None:
                if readonly:
                    _fail("MANIFEST_MISSING")
                manifest = {"format_version": 1, "kind": "personal_preservation_manifest",
                    "operation_id": operation_id, "bundle_id": request["bundle_id"],
                    "request_sha256": request_held.sha256, "selection_sha256": selection_held.sha256,
                    "source_verified_sha256": contract.digest(verified),
                    "source_observation": selection["source_observation"], "sql": selection["sql"],
                    "files": [{"source": entry, "copy": record} for entry, record in
                              zip(selection["files"], records)],
                    "copy_record_sha256s": _copy_digest_set(records),
                    "requirements": selection["requirements"], "coverage": _coverage(selection),
                    "created_at": _stamp()}
                manifest, _held = _publish_fact(stack, target, "manifest.json", manifest,
                    contract.validate_manifest, selection=selection, source_verified=verified,
                    copy_records=records, request=request, check=budget)
            phase = "manifest"
            if not readonly:
                _state(catalog, request, phase, selection=selection, verified=verified, manifest=manifest)
                _checkpoint("manifest")
            receipt, _held = _fact(stack, catalog, operation_id + ".receipt.json", contract.validate_receipt,
                required=False, check=budget, manifest=manifest, selection=selection,
                source_verified=verified, request=request)
            if receipt is None:
                if readonly:
                    _fail("RECEIPT_MISSING")
                receipt = {"format_version": 1, "kind": "personal_preservation_receipt",
                    "operation_id": operation_id, "request_sha256": request_held.sha256,
                    "selection_sha256": selection_held.sha256, "manifest_sha256": contract.digest(manifest),
                    "source_verified_sha256": contract.digest(verified),
                    "target_directory_identity": files.directory_identity(target), "completed_at": _stamp()}
                receipt, _held = _publish_fact(stack, catalog, operation_id + ".receipt.json", receipt,
                    contract.validate_receipt, manifest=manifest, selection=selection,
                    source_verified=verified, request=request, check=budget)
                _checkpoint("receipt")
            if receipt["target_directory_identity"] != files.directory_identity(target):
                _fail("TARGET_CHANGED")
            _verify_copies(records, copies, selection, budget)
            _verify_facts(stack)
            request_held.verify()
            if not readonly:
                _state(catalog, request, "ready", selection=selection, verified=verified,
                       manifest=manifest, receipt=receipt)
                _checkpoint("ready")
            _check_payload_paths(stack, target, selection, request, budget)
            _seal_layout(stack, target, budget)
            result = _public(request, selection, "ready")
        except BaseException as primary:
            if not readonly:
                try:
                    _record_failure(stack, catalog, request, phase, attempt_id, primary,
                                    selection, verified, manifest, receipt)
                except BaseException:
                    primary.add_note("PERSONAL_PRESERVATION_FAILURE_RECORD_UNAVAILABLE")
            raise
        # All package readers and original guards finish before the caller can
        # receive this result. State alone has no completion authority.
    return result


def create_locked(profile, *, operation_id, limits=None, check_cancelled=None):
    return _run_locked(profile, operation_id=operation_id, limits=limits,
                       check_cancelled=check_cancelled)


def resume_locked(profile, *, operation_id, expected_request_sha256, check_cancelled=None):
    return _run_locked(profile, operation_id=operation_id,
        expected_request_sha256=expected_request_sha256, resume=True, check_cancelled=check_cancelled)


def verify_ready_locked(profile, *, operation_id, expected_request_sha256=None, check_cancelled=None):
    """Read a historical package without requiring today's SQL to match it."""
    return _run_locked(profile, operation_id=operation_id, readonly=True,
        expected_request_sha256=expected_request_sha256, check_cancelled=check_cancelled)


def create(profile, *, operation_id, limits=None, check_cancelled=None):
    with lifecycle.exclusive(profile, check_cancelled=check_cancelled):
        return create_locked(profile, operation_id=operation_id, limits=limits,
                             check_cancelled=check_cancelled)


def resume(profile, *, operation_id, expected_request_sha256, check_cancelled=None):
    with lifecycle.exclusive(profile, check_cancelled=check_cancelled):
        return resume_locked(profile, operation_id=operation_id,
            expected_request_sha256=expected_request_sha256, check_cancelled=check_cancelled)
