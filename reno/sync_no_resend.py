"""Permanent sender decisions, independent of restorable sync-state images.

This module never opens SQLite, reads credentials or performs network I/O.
Writers require their caller's StateStore ownership and lifecycle SH. A durable
catalog reservation precedes each immutable file, so a missing committed file
fails closed instead of making an abandoned publication sendable again.
"""
from __future__ import annotations

from copy import deepcopy
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import os
import re
import time
import uuid

from . import storage_lifecycle as lifecycle, sync_media as media, sync_protocol as protocol
from .sync_protocol import SyncError

WITNESS = "sync-sender-contract.json"
SEAL = "sync-sender-activated.json"
DIRECTORY = "sync-no-resend"
VERSION_DIRECTORY = "v1"
MAX_RECORDS = 4096
MAX_RECORD_BYTES = 64 * 1024
MAX_CATALOG_BYTES = 8 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
DEADLINE_SECONDS = 10.0
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_PART = re.compile(r"\.sync-[0-9a-f]{32}\.part\Z")
_PEER = ("home_library_id", "home_node_id", "target_library_id", "target_node_id")
_CONTRACT = {"format_version", "ledger_id", "owner_profile_sha256", *_PEER}
_REQUEST = {"format_version", "ledger_id", "owner_profile_sha256", *_PEER,
            "operation_id", "metadata", "binding", "payload_sha256", "reason", "created_at"}
_CATALOG = {"format_version", "contract", "publications", "receipts", "references"}


def _fail(code="SYNC_NO_RESEND_INVALID", status=409):
    raise SyncError("Permanent sender evidence could not be verified", code, status) from None


@contextmanager
def _errors():
    try:
        yield
    except SyncError as exc:
        if exc.code.startswith("SYNC_NO_RESEND_"):
            raise
        _fail("SYNC_NO_RESEND_UNSAFE_PATH" if "UNSAFE_PATH" in exc.code else "SYNC_NO_RESEND_INVALID")
    except (OSError, ValueError, TypeError, KeyError, AttributeError, UnicodeError, OverflowError, RecursionError):
        _fail()


def _exact(value, fields):
    if type(value) is not dict or set(value) != set(fields):
        _fail()


def _uuid(value):
    if type(value) is not str or str(uuid.UUID(value)) != value:
        _fail()


def _sha(value):
    if type(value) is not str or not _SHA.fullmatch(value):
        _fail()


def _encoded(value, maximum=MAX_RECORD_BYTES):
    from .sync_state import _bounded
    return _bounded(value, maximum=maximum, nodes=100_000, depth=24)


def _digest(value):
    return hashlib.sha256(_encoded(value)).hexdigest()


def profile_sha256(profile):
    lifecycle._profile(profile)
    return _digest({key: lifecycle._key(getattr(profile, key))
                    for key in ("database", "media", "reports", "checkpoints")})


def validate_contract(value, *, profile=None, binding=None):
    _exact(value, _CONTRACT)
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail()
    _uuid(value["ledger_id"])
    _sha(value["owner_profile_sha256"])
    for key in _PEER:
        _uuid(value[key])
    if profile is not None and value["owner_profile_sha256"] != profile_sha256(profile):
        _fail("SYNC_NO_RESEND_OWNER_MISMATCH")
    if binding is not None and any(value[key] != binding[key] for key in _PEER):
        _fail("SYNC_NO_RESEND_PEER_MISMATCH")
    return deepcopy(value)


def validate_request(value, contract):
    from .sync_state import _metadata, _timestamp
    from .sync_upload_pins import normalize_binding
    _exact(value, _REQUEST)
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail()
    if any(type(value[key]) is not type(contract[key]) or value[key] != contract[key] for key in _CONTRACT):
        _fail("SYNC_NO_RESEND_OWNER_MISMATCH")
    _uuid(value["operation_id"])
    _metadata(value["metadata"])
    _sha(value["payload_sha256"])
    _timestamp(value["created_at"])
    if value["reason"] not in {"user_abandon", "published_receipt"}:
        _fail()
    binding = normalize_binding(value["binding"])
    if (binding["publication_id"] != value["metadata"]["publication_id"]
            or binding["source_node_id"] != value["home_node_id"]
            or binding["source_version"]["library_id"] != value["home_library_id"]
            or binding["recovery_stream"]["library_id"] != value["target_library_id"]):
        _fail("SYNC_NO_RESEND_BINDING_MISMATCH")
    _encoded(value)
    return deepcopy(value)


def normalize_response(request, response):
    """Only exact remote evidence can close a local authorized request."""
    from .sync_state import _timestamp
    from .sync_snapshot import _version
    from .sync_upload_pins import normalize_binding
    if type(response) is not dict:
        _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
    if "binding" not in response or normalize_binding(response["binding"]) != request["binding"]:
        _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
    if response.get("state") == "abandoned":
        _exact(response, {"binding", "operation_id", "state"})
        if response["operation_id"] != request["operation_id"] or response["binding"] != request["binding"]:
            _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
    elif response.get("state") == "published":
        _exact(response, {"binding", "payload_sha256", "state", "receipt"})
        if response["binding"] != request["binding"] or response["payload_sha256"] != request["payload_sha256"]:
            _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
        receipt = response["receipt"]
        _exact(receipt, {"publication_id", "source_version", "local_version", "committed_at", "replayed"})
        _version(receipt["source_version"])
        _version(receipt["local_version"])
        _timestamp(receipt["committed_at"])
        stream = request["binding"]["recovery_stream"]
        if (type(receipt["replayed"]) is not bool
                or receipt["publication_id"] != request["metadata"]["publication_id"]
                or receipt["source_version"] != request["binding"]["source_version"]
                or any(receipt["local_version"][key] != stream[key] for key in ("library_id", "library_epoch"))):
            _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
    else:
        _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
    result = deepcopy(response)
    if result["state"] == "published":
        # Replay presentation is not a different durable terminal identity.
        result["receipt"]["replayed"] = False
    _encoded(result)
    return result


def _validate_catalog(value, *, profile=None, binding=None, check=None):
    _exact(value, _CATALOG)
    if type(value["format_version"]) is not int or value["format_version"] != 1:
        _fail()
    contract = validate_contract(value["contract"], profile=profile, binding=binding)
    count = 0
    operations = set()
    for kind in ("publications", "receipts", "references"):
        entries = value[kind]
        if type(entries) is not dict:
            _fail()
        count += len(entries)
        if count > MAX_RECORDS:
            _fail("SYNC_NO_RESEND_COUNT_LIMIT")
        for key, entry in entries.items():
            if check is not None:
                check()
            _uuid(key)
            _exact(entry, {"sha256", "record", "state"})
            if entry["state"] not in {"reserved", "committed"}:
                _fail()
            _sha(entry["sha256"])
            if _digest(entry["record"]) != entry["sha256"]:
                _fail()
            if kind == "publications":
                request = validate_request(entry["record"], contract)
                if request["metadata"]["publication_id"] != key or request["operation_id"] in operations:
                    _fail()
                operations.add(request["operation_id"])
            elif kind == "receipts":
                _exact(entry["record"], {"format_version", "request_sha256", "operation_id", "publication_id", "response"})
                terminal = entry["record"]
                original = value["publications"].get(key)
                if (type(terminal["format_version"]) is not int or terminal["format_version"] != 1 or original is None
                        or original["state"] != "committed"
                        or terminal["publication_id"] != key
                        or terminal["operation_id"] != original["record"]["operation_id"]
                        or terminal["request_sha256"] != original["sha256"]
                        or normalize_response(original["record"], terminal["response"]) != terminal["response"]):
                    _fail("SYNC_NO_RESEND_RECEIPT_MISMATCH")
            else:
                from .sync_local_retirement import validate_request as validate_reference
                reference = validate_reference(entry["record"], contract)
                if reference["operation_id"] != key or key in operations:
                    _fail("SYNC_NO_RESEND_OPERATION_CONFLICT")
                operations.add(key)
    _encoded(value, MAX_CATALOG_BYTES)
    return contract


def validate_frozen_evidence(contract_raw, seal_raw, catalog_raw, records, *, profile=None,
                             owner_profile_sha256=None, home_identity=None, state_contract=None,
                             allow_incomplete=False):
    """Validate one caller-captured directory closure without live file reads.

    ``records`` maps each publications/receipts/references directory to its
    complete ordinary JSON filename -> bytes mapping. Directory identities,
    unsafe companions and enumeration closure are the capture caller's proof.
    """
    from .sync_state import _decode
    started, size = time.monotonic(), 0
    def check():
        if time.monotonic() - started > DEADLINE_SECONDS:
            _fail("SYNC_NO_RESEND_DEADLINE_LIMIT")
    def decode(raw, maximum):
        nonlocal size
        check()
        if type(raw) is not bytes or len(raw) > maximum:
            _fail("SYNC_NO_RESEND_BYTES_LIMIT")
        size += len(raw)
        if size > MAX_TOTAL_BYTES:
            _fail("SYNC_NO_RESEND_BYTES_LIMIT")
        value = _decode(raw)
        if _encoded(value, maximum) != raw:
            _fail("SYNC_NO_RESEND_RECORD_MISMATCH")
        check()
        return value
    with _errors():
        _exact(records, {"publications", "receipts", "references"})
        if all(raw is None for raw in (contract_raw, seal_raw, catalog_raw)) and not any(records.values()):
            if state_contract is not None:
                _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
            return {"contract": None, "catalog": None, "incomplete": []}
        if any(raw is None for raw in (contract_raw, seal_raw, catalog_raw)):
            _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
        contract = validate_contract(decode(contract_raw, MAX_RECORD_BYTES), profile=profile)
        if owner_profile_sha256 is not None and contract["owner_profile_sha256"] != owner_profile_sha256:
            _fail("SYNC_NO_RESEND_OWNER_MISMATCH")
        if home_identity is not None:
            _exact(home_identity, {"library_id", "node_id"})
            if contract["home_library_id"] != home_identity["library_id"] or contract["home_node_id"] != home_identity["node_id"]:
                _fail("SYNC_NO_RESEND_OWNER_MISMATCH")
        if state_contract is not None:
            _exact(state_contract, {"version", "ledger_id"})
            if type(state_contract["version"]) is not int or state_contract != {"version": 1, "ledger_id": contract["ledger_id"]}:
                _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        if decode(seal_raw, MAX_RECORD_BYTES) != contract:
            _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        catalog = decode(catalog_raw, MAX_CATALOG_BYTES)
        if _validate_catalog(catalog, profile=profile, check=check) != contract:
            _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        incomplete, count = [], 0
        for kind in ("publications", "receipts", "references"):
            if type(records[kind]) is not dict:
                _fail()
            expected = {key + ".json" for key in catalog[kind]}
            if set(records[kind]) - expected:
                _fail("SYNC_NO_RESEND_UNEXPECTED_FILE")
            for key, entry in catalog[kind].items():
                check()
                count += 1
                if count > MAX_RECORDS:
                    _fail("SYNC_NO_RESEND_COUNT_LIMIT")
                raw = records[kind].get(key + ".json")
                if raw is None:
                    incomplete.append((kind, key))
                    continue
                if decode(raw, MAX_RECORD_BYTES) != entry["record"] or hashlib.sha256(raw).hexdigest() != entry["sha256"]:
                    _fail("SYNC_NO_RESEND_RECORD_MISMATCH")
                if entry["state"] != "committed":
                    incomplete.append((kind, key))
        if incomplete and not allow_incomplete:
            _fail("SYNC_NO_RESEND_RECORD_MISSING")
        return {"contract": contract, "catalog": catalog, "incomplete": incomplete}


def _read(directory, name, maximum, *, consume=None):
    from .sync_state import _read_bytes, _decode
    raw = _read_bytes(directory, name, maximum, observation=True)
    if consume is not None:
        consume(len(raw))
    value = _decode(raw)
    if _encoded(value, maximum) != raw:
        _fail("SYNC_NO_RESEND_RECORD_MISMATCH")
    return value


def _check_names(directory, allowed, *, maximum, check=None):
    count = 0
    with os.scandir(directory.fd if os.name != "nt" else media._native_path(directory.path)) as entries:
        for entry in entries:
            if check is not None:
                check()
            count += 1
            if count > maximum:
                _fail("SYNC_NO_RESEND_COUNT_LIMIT")
            if entry.name not in allowed and not _PART.fullmatch(entry.name):
                _fail("SYNC_NO_RESEND_UNEXPECTED_FILE")
            info = directory.entry_stat(entry.name)
            media._safe_stat(info, directory=entry.name in {VERSION_DIRECTORY, "publications", "receipts", "references"})
    directory.verify()


def observe(profile, *, state=None, allow_incomplete=False):
    """Pure bounded observation; absent virgin legacy storage is compatible.

    A restored v1 state does not hide an existing independent contract. The
    supported restore operation must preserve these permanent files. Deleting
    every contract anchor and then restoring a pre-activation image is not a
    supported recovery and cannot be detected from that old image alone.
    """
    lifecycle._profile(profile)
    started, size = time.monotonic(), 0
    def check():
        if time.monotonic() - started > DEADLINE_SECONDS:
            _fail("SYNC_NO_RESEND_DEADLINE_LIMIT")
    def consume(amount):
        nonlocal size
        size += amount
        check()
        if size > MAX_TOTAL_BYTES:
            _fail("SYNC_NO_RESEND_BYTES_LIMIT")
    binding = None if state is None else state["binding"]
    with _errors(), media._Directory(profile.database.parent) as root:
        try:
            contract = _read(root, WITNESS, MAX_RECORD_BYTES, consume=consume)
        except FileNotFoundError:
            try:
                root.entry_stat(DIRECTORY)
            except FileNotFoundError:
                try:
                    root.entry_stat(SEAL)
                except FileNotFoundError:
                    pass
                else:
                    _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
                if state is not None and state["format_version"] >= 2:
                    _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
                return {"contract": None, "catalog": None, "incomplete": []}
            _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
        contract = validate_contract(contract, profile=profile, binding=binding)
        try:
            seal = _read(root, SEAL, MAX_RECORD_BYTES, consume=consume)
        except FileNotFoundError:
            _fail("SYNC_NO_RESEND_ACTIVATION_INCOMPLETE")
        if seal != contract:
            _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        if state is not None and state["format_version"] >= 2:
            if state["sender_contract"] != {"version": 1, "ledger_id": contract["ledger_id"]}:
                _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        with media._Directory(root.path / DIRECTORY, parent=root) as ledger:
            _check_names(ledger, {VERSION_DIRECTORY}, maximum=2, check=check)
            with media._Directory(ledger.path / VERSION_DIRECTORY, parent=ledger) as version:
                _check_names(version, {"catalog.json", "publications", "receipts", "references"}, maximum=16, check=check)
                catalog = _read(version, "catalog.json", MAX_CATALOG_BYTES, consume=consume)
                if _validate_catalog(catalog, profile=profile, binding=binding, check=check) != contract:
                    _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
                incomplete = []
                for kind in ("publications", "receipts", "references"):
                    with media._Directory(version.path / kind, parent=version) as records:
                        _check_names(records, {key + ".json" for key in catalog[kind]}, maximum=MAX_RECORDS + 32, check=check)
                        for key, entry in catalog[kind].items():
                            check()
                            try:
                                actual = _read(records, key + ".json", MAX_RECORD_BYTES, consume=consume)
                            except FileNotFoundError:
                                incomplete.append((kind, key))
                                continue
                            if actual != entry["record"]:
                                _fail("SYNC_NO_RESEND_RECORD_MISMATCH")
                            if entry["state"] != "committed":
                                incomplete.append((kind, key))
                        records.verify()
                version.verify()
                root.verify()
        if incomplete and not allow_incomplete:
            _fail("SYNC_NO_RESEND_RECORD_MISSING")
        return {"contract": contract, "catalog": catalog, "incomplete": incomplete}


def contract_active(profile, *, state=None):
    """Detect permanent activation even after restoring a legacy state image.

    Evidence incompleteness cannot waive cache protection. This small
    observation need not enumerate records or block a heartbeat on their SHA.
    """
    lifecycle._profile(profile)
    with _errors(), media._Directory(profile.database.parent) as root:
        try:
            value = _read(root, WITNESS, MAX_RECORD_BYTES)
        except FileNotFoundError:
            for name in (DIRECTORY, SEAL):
                try:
                    root.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
            if state is not None and state["format_version"] >= 2:
                _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
            return False
        validate_contract(value, profile=profile, binding=None if state is None else state["binding"])
        return True


def ensure_contract(profile, binding, *, required_ledger_id=None):
    """Explicit activation, called only under StateStore ownership and SH."""
    from .sync_state import _write
    lifecycle.require_guard(profile)
    with _errors(), media._Directory(profile.database.parent) as root:
        try:
            contract = _read(root, WITNESS, MAX_RECORD_BYTES)
        except FileNotFoundError:
            for name in (DIRECTORY, SEAL):
                try:
                    root.entry_stat(name)
                except FileNotFoundError:
                    continue
                _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
            if required_ledger_id is not None:
                _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
            contract = {"format_version": 1, "ledger_id": str(uuid.uuid4()),
                        "owner_profile_sha256": profile_sha256(profile), **{key: binding[key] for key in _PEER}}
            # Immutable activation reservation precedes directory creation.
            # All retries keep its original UUID, profile and peer.
            _write(root, WITNESS, _encoded(contract), immutable=True)
        validate_contract(contract, profile=profile, binding=binding)
        if required_ledger_id is not None and contract["ledger_id"] != required_ledger_id:
            _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
        try:
            seal = _read(root, SEAL, MAX_RECORD_BYTES)
        except FileNotFoundError:
            if required_ledger_id is not None:
                _fail("SYNC_NO_RESEND_ACTIVATION_INCOMPLETE")
        else:
            if seal != contract:
                _fail("SYNC_NO_RESEND_CONTRACT_MISMATCH")
            return observe(profile, state={"format_version": 1, "binding": binding})["contract"]
        catalog = {"format_version": 1, "contract": contract, "publications": {}, "receipts": {}, "references": {}}
        with media._Directory(root.path / DIRECTORY, parent=root, create=True) as ledger:
            _check_names(ledger, {VERSION_DIRECTORY}, maximum=2)
            with media._Directory(ledger.path / VERSION_DIRECTORY, parent=ledger, create=True) as version:
                _check_names(version, {"catalog.json", "publications", "receipts", "references"}, maximum=16)
                for kind in ("publications", "receipts", "references"):
                    with media._Directory(version.path / kind, parent=version, create=True) as records:
                        # Only this provably empty activation can resume. Never
                        # reconstruct an empty catalog over existing evidence.
                        _check_names(records, set(), maximum=32)
                try:
                    existing = _read(version, "catalog.json", MAX_CATALOG_BYTES)
                except FileNotFoundError:
                    _write(version, "catalog.json", _encoded(catalog, MAX_CATALOG_BYTES), immutable=True)
                else:
                    if existing != catalog:
                        _fail("SYNC_NO_RESEND_ACTIVATION_INCOMPLETE")
                if os.name != "nt":
                    os.fsync(version.fd)
            if os.name != "nt":
                os.fsync(ledger.fd)
        _write(root, SEAL, _encoded(contract), immutable=True)
    return contract


def _publish(profile, kind, key, record):
    from .sync_state import _write
    lifecycle.require_guard(profile)
    observed = observe(profile, allow_incomplete=True)
    if observed["contract"] is None:
        _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
    catalog = observed["catalog"]
    if observed["incomplete"] and any(item != (kind, key) for item in observed["incomplete"]):
        _fail("SYNC_NO_RESEND_RECORD_MISSING")
    entry = {"sha256": _digest(record), "record": deepcopy(record), "state": "reserved"}
    existing = catalog[kind].get(key)
    if existing is not None and any(existing[field] != entry[field] for field in ("sha256", "record")):
        _fail("SYNC_NO_RESEND_OPERATION_CONFLICT")
    catalog[kind][key] = existing or entry
    _validate_catalog(catalog, profile=profile)
    with _errors(), media._Directory(profile.database.parent) as root:
        with media._Directory(root.path / DIRECTORY, parent=root) as ledger:
            with media._Directory(ledger.path / VERSION_DIRECTORY, parent=ledger) as version:
                # Durable reservation first: interruption never fails open.
                if existing is None:
                    _write(version, "catalog.json", _encoded(catalog, MAX_CATALOG_BYTES))
                with media._Directory(version.path / kind, parent=version) as records:
                    try:
                        actual = _read(records, key + ".json", MAX_RECORD_BYTES)
                    except FileNotFoundError:
                        _write(records, key + ".json", _encoded(record), immutable=True)
                    else:
                        if actual != record:
                            _fail("SYNC_NO_RESEND_OPERATION_CONFLICT")
                # The immutable evidence file is durable before the committed
                # phase. Reserved operations remain blocked until exact resume.
                if catalog[kind][key]["state"] != "committed":
                    catalog[kind][key]["state"] = "committed"
                    _write(version, "catalog.json", _encoded(catalog, MAX_CATALOG_BYTES))
    return deepcopy(record)


def make_request(profile, state, metadata, envelope, operation_id, *, reason="user_abandon"):
    from .sync_upload_pins import normalize_binding
    from .sync_transfer import publication_payload_sha256
    _uuid(operation_id)
    contract = observe(profile, state=state)["contract"]
    if contract is None:
        _fail("SYNC_NO_RESEND_CONTRACT_MISSING")
    if envelope["recovery_stream"] is None:
        _fail("SYNC_NO_RESEND_BINDING_UNPROVEN")
    binding = normalize_binding({"publication_id": metadata["publication_id"],
        "source_node_id": state["binding"]["home_node_id"], "source_version": envelope["snapshot"]["version"],
        "recovery_generation": envelope["recovery_generation"], "recovery_stream": envelope["recovery_stream"]})
    value = {**contract, "operation_id": operation_id, "metadata": deepcopy(metadata), "binding": binding,
             "payload_sha256": publication_payload_sha256(envelope["snapshot"], envelope["claims"],
                recovery_generation=envelope["recovery_generation"], recovery_stream=envelope["recovery_stream"]),
             "reason": reason, "created_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")}
    return validate_request(value, contract)


def commit_request(profile, request):
    contract = observe(profile, allow_incomplete=True)["contract"]
    validate_request(request, contract)
    return _publish(profile, "publications", request["metadata"]["publication_id"], request)


def request_for_operation(profile, operation_id, *, state=None, allow_incomplete=False):
    _uuid(operation_id)
    observed = observe(profile, state=state, allow_incomplete=allow_incomplete)
    if observed["catalog"] is not None:
        for item in observed["catalog"]["publications"].values():
            if item["record"]["operation_id"] == operation_id:
                return deepcopy(item["record"])
    _fail("SYNC_NO_RESEND_OPERATION_MISSING")


def commit_receipt(profile, operation_id, response, *, state=None):
    request = request_for_operation(profile, operation_id, state=state, allow_incomplete=True)
    value = {"format_version": 1, "request_sha256": _digest(request), "operation_id": operation_id,
             "publication_id": request["metadata"]["publication_id"], "response": normalize_response(request, response)}
    return _publish(profile, "receipts", value["publication_id"], value)


def assert_sendable(profile, publication_id, *, state=None):
    _uuid(publication_id)
    observed = observe(profile, state=state)
    if observed["catalog"] is not None and publication_id in observed["catalog"]["publications"]:
        _fail("SYNC_NO_RESEND_FORBIDDEN")


def pending_requests(profile, *, state=None, allow_incomplete=False):
    observed = observe(profile, state=state, allow_incomplete=allow_incomplete)
    if allow_incomplete and any(kind == "references" for kind, _ in observed["incomplete"]):
        _fail("SYNC_NO_RESEND_RECORD_MISSING")
    if observed["catalog"] is None:
        return []
    return [deepcopy(item["record"]) for key, item in sorted(observed["catalog"]["publications"].items())
            if key not in observed["catalog"]["receipts"] or allow_incomplete and (
                ("receipts", key) in observed["incomplete"]
                or state is not None and state["pending_publication"] == item["record"]["metadata"])]
