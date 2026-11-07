"""Read-only continuity probes and explicit local activation maintenance.

The probe creates no database, StateStore, writer lock or external facility.
Activation is local-only, checks the current paired identity/profile/epoch in
the actual service writer, and does not register historical control bodies.
"""
from __future__ import annotations

import json
import sqlite3

from . import db, storage_lifecycle as lifecycle
from . import sync_source_continuity_service as service, sync_source_readmit_schema as schema
from . import sync_source_readmit_schema2 as schema2
from . import sync_source_continuity_status_contract as contract
from . import storage_source_continuity_roots as roots
from . import storage_readonly as readonly, storage_source as source, sync_media as media
from .sync_protocol import SyncError


def _projection(con):
    result = {}
    count = 0
    for table, columns in roots.SQL_COLUMNS.items():
        fields = ",".join('"' + key + '"' for key in columns)
        sizes = ",".join('length(CAST("' + key + '" AS BLOB))' for key in columns)
        for row in con.execute('SELECT ' + sizes + ' FROM main."' + table + '" LIMIT ?',
                               (roots.CaptureLimits.max_sql_rows + 1,)):
            count += 1
            if count > roots.CaptureLimits.max_sql_rows or any(size is not None and size > contract.MAX_BYTES for size in row):
                service._fail("RECORD_INVALID")
        result[table] = [dict(row) for row in con.execute('SELECT ' + fields + ' FROM main."' + table + '" ORDER BY 1')]
    return roots.normalize_sql(result)


def _authorizer(action, first, second, sql_schema, _origin):
    if action in {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_TRANSACTION}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_READ:
        columns = roots.SQL_COLUMNS.get(first, ())
        if sql_schema == "main" and (second in columns
                or first == "meta" and second in {"key", "value"}
                or first == "sync_pair" and second in {"slot", "source_library_id", "source_node_id",
                    "target_library_id", "target_node_id", "protocol_version", "schema_fingerprint", "paired_at"}):
            return sqlite3.SQLITE_OK
        if first in {"sqlite_master", "sqlite_temp_master"} and sql_schema in {"main", "temp"}:
            return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_PRAGMA and first in {"table_info", "table_xinfo", "index_list", "index_info",
                                                    "index_xinfo", "foreign_key_list"}:
        return sqlite3.SQLITE_OK
    if action == sqlite3.SQLITE_FUNCTION and second in {"length", "lower", "count", "like"}:
        return sqlite3.SQLITE_OK
    return sqlite3.SQLITE_DENY


def _node(raw):
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                service._fail("PEER_MISMATCH")
            value[key] = item
        return value
    try:
        value = json.loads(raw, object_pairs_hook=unique)
        contract.wire._exact(value, ("instance_id",))
        service.queue._uuid(value["instance_id"], "node_id")
        return value["instance_id"]
    except (ValueError, TypeError, KeyError):
        service._fail("PEER_MISMATCH")


def _read_basis(con, profile, node):
    version = db.knowledge_version(con)
    pair = con.execute("SELECT slot,source_library_id,source_node_id,target_library_id,target_node_id,"
                       "protocol_version,schema_fingerprint,paired_at FROM main.sync_pair WHERE slot=1").fetchone()
    if pair is None:
        raise SyncError("Explicit peer binding is required", "SYNC_NOT_PAIRED", 409)
    handshake = service.protocol.schema_handshake(con)
    if (pair["protocol_version"] != handshake["protocol_version"]
            or pair["schema_fingerprint"] != handshake["core_schema_sha256"]):
        raise SyncError("Stored peer binding needs explicit maintenance", "INCOMPATIBLE_SYNC_SCHEMA", 409)
    if (pair["target_library_id"], pair["target_node_id"]) != (version["library_id"], node):
        service._fail("PEER_MISMATCH")
    actual = {"owner_profile_sha256": service.external.profile_sha256(profile),
        "source_identity": {key: pair["source_" + key] for key in ("library_id", "node_id")},
        "target_identity": {"library_id": version["library_id"], "node_id": node},
        "target_epoch": version["library_epoch"]}
    return actual, handshake


def _read(*, profile, request=None):
    profile = service._profile(profile)
    service._runtime(capability=True)
    if request is not None:
        request = contract.normalize_request(request)
    with lifecycle.shared(profile), media._Directory(profile.database.parent) as directory:
        with source.source_reader(directory, "node-identity.json") as identity, readonly.committed_image_locked(profile) as image:
            raw = identity.read(4097)
            if len(raw) > 4096:
                service._fail("PEER_MISMATCH")
            node = _node(raw)
            con = image.connection
            con.row_factory = sqlite3.Row
            con.set_authorizer(_authorizer)
            version = con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()
            if version is None or version[0] != "17":
                service._fail("SCHEMA_REQUIRED")
            schema2.validate_historical(con)
            actual, handshake = _read_basis(con, profile, node)
            local_request = {"kind": "source_continuity", "source": {
                **actual["source_identity"], "handshake": handshake},
                "target": actual["target_identity"]}
            if request is None:
                request = contract.normalize_request(local_request)
            if any(contract.wire._peer(request[side]) != actual[side + "_identity"] for side in ("source", "target")):
                service._fail("PEER_MISMATCH")
            service.protocol.validate_handshake(request["source"]["handshake"], handshake)
            projection = _projection(con)
            with roots.capture_locked(profile, sql_projection=projection) as capture:
                summary = capture.summary
                state, continuity, head = "unknown", None, None
                if summary["presence"] == "absent":
                    state = "absent"
                elif summary["presence"] == "present":
                    row = projection[schema.ACTIVATION][0]
                    state = "current" if row["target_epoch"] == actual["target_epoch"] else "historical"
                    continuity = {"format_version": 1, "continuity_id": row["continuity_id"],
                        "contract_sha256": row["contract_sha256"], "target_epoch": row["target_epoch"]}
                    head = {"ordinal": summary["head_ordinal"], "sha256": summary["head_sha256"]}
                result = contract.normalize_result(request, {"format_version": 1, "kind": "source_continuity",
                    "source_identity": actual["source_identity"], "target_identity": actual["target_identity"],
                    "handshake": local_request["source"]["handshake"], "receiver_version": db.knowledge_version(con),
                    "state": state, "continuity": continuity, "head": head})
                capture.verify_source()
                image.verify_source()
                lifecycle.require_guard(profile).verify()
                return result, service._sha(actual)


def _read_checked(*, profile, request=None):
    try:
        return _read(profile=profile, request=request)
    except schema.SourceReadmitSchemaError:
        service._fail("SCHEMA_REQUIRED")
    except roots.ContinuityRootError as exc:
        service._fail("BUSY" if exc.retriable else "RECORD_INVALID")
    except (readonly.ReadonlyImageError, source.SourceReadError) as exc:
        service._fail("BUSY" if getattr(exc, "retriable", False) or exc.code == "SOURCE_CHANGED" else "RECORD_INVALID")


def status(*, profile, request):
    """Authenticated paired observation, with no automatic initialization."""
    try:
        request = contract.normalize_request(request)
    except contract.wire.SourceReadmitContractError:
        raise SyncError("Continuity status could not be verified", "INVALID_SYNC_REQUEST", 400) from None
    return _read_checked(profile=profile, request=request)[0]


def preview(*, profile):
    result, basis = _read_checked(profile=profile)
    return {**result, "activation_basis_sha256": basis}


def activate(*, profile, continuity_id, activated_at, expected_basis_sha256):
    # Both the original UUID and timestamp are caller-visible retry inputs.
    # An uncertain result must reuse them, rather than generate a second setup.
    contract.wire._sha(expected_basis_sha256)
    return service.activate(profile=profile, continuity_id=continuity_id, activated_at=activated_at,
        expected_basis_sha256=expected_basis_sha256)
