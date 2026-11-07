"""Cloud SQL half of registered source controls, with a sealed writer seam.

Schema17 and its real protocol capability are mandatory for fresh business.
Historical positive receipts only explain their original body; neither caller
JSON nor an existing effect marker can authorize a fresh queue effect. HTTP,
ordinary effect-module integration and Home creation seals belong to callers.
"""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import re
import sqlite3
import threading

from . import config, db, migrations, storage_admin, storage_lifecycle as lifecycle
from . import sync_budget_wait as old_budget, sync_source_control as old_source
from . import sync_protocol as protocol, sync_queue as queue, sync_recovery
from . import sync_source_continuity as external, sync_source_readmit_contract as wire
from . import sync_source_readmit_schema as schema
from .sync_protocol import SyncError


_CURRENT = ContextVar("reno_source_continuity_sql_writer", default=None)
_EFFECT = ContextVar("reno_source_continuity_effect_frame", default=None)
_SEAL = object()
_HISTORICAL = object()


def _fail(code, status=409):
    raise SyncError("Source control conditions could not be verified", "SYNC_SOURCE_READMIT_" + code, status) from None


def _json(value):
    return wire.canonical_json(value)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _profile(profile):
    if not config.is_cloud():
        _fail("ROLE_REQUIRED", 403)
    return lifecycle._profile(profile)


def _runtime(*, capability):
    if migrations.SCHEMA_VERSION != schema.SCHEMA_VERSION:
        _fail("CAPABILITY_REQUIRED" if capability else "SCHEMA_REQUIRED")
    if capability:
        getter = getattr(protocol, "schema_rules", None)
        version = getattr(protocol, "SOURCE_CONTROL_CONTINUITY_VERSION", None)
        if type(version) is not int or version != 1 or not callable(getter):
            _fail("CAPABILITY_REQUIRED")
        rules = getter()
        if type(rules) is not dict or type(rules.get("source_control_continuity_version")) is not int or rules["source_control_continuity_version"] != 1:
            _fail("CAPABILITY_REQUIRED")


def _connection(con, profile):
    if type(con) is not sqlite3.Connection:
        _fail("WRITER_REQUIRED")
    storage_admin.from_connection(con, profile=profile)
    paths = [row[2] for row in con.execute("PRAGMA database_list") if row[1] == "main"]
    if len(paths) != 1 or not paths[0] or lifecycle._key(paths[0]) != lifecycle._key(profile.database):
        _fail("PROFILE_MISMATCH")


def _structure(con, profile):
    _connection(con, profile)
    try:
        version = con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()
        if version is None or version[0] != "17":
            _fail("SCHEMA_REQUIRED")
        migrations.validate(con)
        from . import sync_source_readmit_schema2
        return sync_source_readmit_schema2.detect(con)
    except (migrations.SchemaVersionError, sqlite3.DatabaseError):
        _fail("SCHEMA_REQUIRED")


def _sql_start(statement):
    """Discard SQLite's empty statements, BOM and leading comments."""
    statement = statement.lstrip()
    while statement.startswith((";", "\ufeff", "--", "/*")):
        if statement.startswith((";", "\ufeff")):
            statement = statement[1:]
        elif statement.startswith("--"):
            _, separator, statement = statement.partition("\n")
            if not separator:
                return ""
        else:
            end = statement.find("*/", 2)
            if end < 0:
                return ""
            statement = statement[end + 2:]
        statement = statement.lstrip()
    return statement


def _transaction_boundary(statement):
    statement = _sql_start(statement)
    first = re.match(r"[A-Za-z]+", statement)
    if first is None:
        return False
    verb = first[0].upper()
    if verb in {"BEGIN", "COMMIT", "END"}:
        return True
    if verb != "ROLLBACK":
        return False
    rest = _sql_start(statement[first.end():])
    next_word = re.match(r"[A-Za-z]+", rest)
    if next_word is not None and next_word[0].upper() == "TRANSACTION":
        rest = _sql_start(rest[next_word.end():])
        next_word = re.match(r"[A-Za-z]+", rest)
    return next_word is None or next_word[0].upper() != "TO"


class _Writer:
    def __init__(self, profile, con, guard, observed, *, schema_variant="v1"):
        self.profile, self.con, self.guard, self.observed = profile, con, guard, observed
        self.thread = threading.get_ident()
        self.seal = _SEAL
        self.sql_generation = 0
        self.schema_variant = schema_variant

    def transaction_observed(self, statement):
        # This service creates and owns the connection. The observer preserves
        # its authorizer and records only a generation count, never SQL text.
        if _transaction_boundary(statement):
            self.sql_generation += 1

    def check(self):
        if (_CURRENT.get() is not self or self.seal is not _SEAL or self.thread != threading.get_ident()
                or not self.con.in_transaction or self.sql_generation != 0):
            _fail("WRITER_REQUIRED")
        lifecycle.require_guard(self.profile)
        self.guard._check()
        _connection(self.con, self.profile)


@contextmanager
def writer(*, profile, initialize=False, capability=True, resume_request=None):
    """Own SH -> real external OS lock -> IMMEDIATE, with no network calls.

    The complete external closure and its long SHA reads precede BEGIN. SQL
    consumes only this guard's sealed cache and short commit observations.
    A resume selector repairs only exact request heads, never source authority.
    """
    profile = _profile(profile)
    _runtime(capability=capability)
    if _CURRENT.get() is not None or type(initialize) is not bool or type(capability) is not bool:
        _fail("WRITER_REQUIRED")
    with lifecycle.shared(profile), external.locked(profile, initialize=initialize) as guard:
        con = migrations._open(profile.database)
        token = None
        try:
            schema_variant = _structure(con, profile)
            try:
                observed = guard.observe(resume_request=resume_request)
            except SyncError as exc:
                if not initialize or exc.code != "SYNC_SOURCE_CONTINUITY_MISSING":
                    raise
                # Only explicit activation can complete its original empty,
                # unsealed setup. activate_files still checks every anchor.
                observed = None
            con.execute("PRAGMA busy_timeout=0")
            con.execute("BEGIN IMMEDIATE")
            owner = _Writer(profile, con, guard, observed, schema_variant=schema_variant)
            con.set_trace_callback(owner.transaction_observed)
            token = _CURRENT.set(owner)
            yield con
            owner.check()
            guard.verify_for_commit()
            con.commit()
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                _fail("BUSY")
            raise
        finally:
            if token is not None:
                _CURRENT.reset(token)
            con.set_trace_callback(None)
            con.rollback()
            con.close()


def _owner(con, profile):
    owner = _CURRENT.get()
    if type(owner) is not _Writer or owner.con is not con or owner.profile.identity != profile.identity:
        _fail("WRITER_REQUIRED")
    owner.check()
    return owner


def _lookup(con, table, key, value):
    row = con.execute("SELECT " + ",".join(schema.COLUMNS[table]) + " FROM " + table + " WHERE " + key + "=?", (value,)).fetchone()
    if row is None:
        return None
    try:
        if table in {schema.ADMISSION, schema.DISPOSITION}:
            from . import sync_source_readmit_schema2
            return sync_source_readmit_schema2.validate_record(table, row)
        return schema.validate_record(table, row)
    except schema.SourceReadmitRecordError:
        _fail("RECORD_INVALID")


def _insert(con, table, row):
    if table in {schema.ADMISSION, schema.DISPOSITION}:
        from . import sync_source_readmit_schema2
        row = sync_source_readmit_schema2.validate_record(table, row)
    else:
        row = schema.validate_record(table, row)
    columns = schema.COLUMNS[table]
    con.execute("INSERT INTO " + table + "(" + ",".join(columns) + ") VALUES(" + ",".join("?" for _ in columns) + ")", tuple(row[key] for key in columns))


def _pair(con, body):
    old_budget._pair_identity(con, body)
    return queue._local_identity(con)


def _actual_activation(con, profile, *, _mode=None):
    if _mode is not None and _mode is not _HISTORICAL:
        _fail("WRITER_REQUIRED")
    library, node = queue._local_identity(con)
    version = db.knowledge_version(con)
    pair = con.execute("SELECT * FROM sync_pair WHERE slot=1").fetchone()
    if pair is None or (pair["target_library_id"], pair["target_node_id"]) != (library, node):
        _fail("PEER_MISMATCH")
    local = protocol.schema_handshake(con)
    source = {"library_id": pair["source_library_id"], "node_id": pair["source_node_id"], "handshake": local}
    queue._pair(con, source)
    result = {"owner_profile_sha256": external.profile_sha256(profile),
        "source_identity": {key: source[key] for key in ("library_id", "node_id")},
        "target_identity": {"library_id": library, "node_id": node}}
    if _mode is not _HISTORICAL:
        result["target_epoch"] = version["library_epoch"]
    return result


def _activation(con, owner, continuity_id, *, _mode=None):
    row = _lookup(con, schema.ACTIVATION, "continuity_id", continuity_id)
    if row is None:
        _fail("CONTINUITY_REQUIRED")
    contract = json.loads(row["contract_json"])
    if any(contract[key] != value for key, value in _actual_activation(con, owner.profile, _mode=_mode).items()):
        _fail("CONTINUITY_REQUIRED")
    observed = owner.guard.view()
    if observed["contract"] != contract or external._digest(contract) != row["contract_sha256"]:
        _fail("CONTINUITY_REQUIRED")
    return row, observed


def activate(*, profile, continuity_id, activated_at, expected_basis_sha256=None):
    """Explicit maintenance only; never retrospectively registers old bodies."""
    external._uuid(continuity_id)
    external._time(activated_at)
    if expected_basis_sha256 is not None:
        external._sha(expected_basis_sha256)
    with writer(profile=profile, initialize=True) as con:
        owner = _owner(con, profile)
        basis = _actual_activation(con, profile)
        if expected_basis_sha256 is not None and _sha(basis) != expected_basis_sha256:
            _fail("CONTROL_CHANGED")
        actual = {"format_version": 1, "continuity_id": continuity_id,
                  **basis, "activated_at": activated_at}
        existing = _lookup(con, schema.ACTIVATION, "continuity_id", continuity_id)
        if existing is not None:
            if json.loads(existing["contract_json"]) != actual or owner.guard.view()["contract"] != actual:
                _fail("OPERATION_CONFLICT")
            return deepcopy(actual)
        observed = owner.observed
        if observed is not None and (observed["contract"] is not None and observed["contract"] != actual
                or any(observed["entries"][kind] for kind in external.KINDS)):
            _fail("CONTINUITY_REQUIRED")
        duplicate = con.execute("SELECT 1 FROM " + schema.ACTIVATION + " WHERE owner_profile_sha256=? AND source_library_id=? AND source_node_id=? AND target_library_id=? AND target_node_id=? AND target_epoch=?",
            (actual["owner_profile_sha256"], actual["source_identity"]["library_id"], actual["source_identity"]["node_id"], actual["target_identity"]["library_id"], actual["target_identity"]["node_id"], actual["target_epoch"])).fetchone()
        if duplicate is not None:
            _fail("OPERATION_CONFLICT")
        owner.guard.activate_files(actual)
        row = {key: actual[key] for key in ("format_version", "continuity_id", "owner_profile_sha256", "target_epoch", "activated_at")}
        row.update(_identities(actual["source_identity"], actual["target_identity"]),
                   contract_json=_json(actual), contract_sha256=_sha(actual))
        _insert(con, schema.ACTIVATION, row)
        return deepcopy(actual)


def _identities(source, target):
    return {prefix + "_" + key: value[key] for prefix, value in (("source", source), ("target", target)) for key in ("library_id", "node_id")}


def _original(con, body):
    body = wire._old_request(body)
    budget = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (body["request_id"],)).fetchone()
    source = con.execute("SELECT * FROM sync_queue_source_receipt WHERE request_id=?", (body["request_id"],)).fetchone()
    if budget is not None and source is not None or body["kind"] == "source_fail" and budget is not None or body["kind"] != "source_fail" and source is not None:
        _fail("OPERATION_CONFLICT")
    found = source if body["kind"] == "source_fail" else budget
    if found is None:
        return None
    try:
        _, result = old_source._receipt(found, _json(body)) if source is not None else old_budget._receipt(found, _json(body), con=con)
    except ValueError:
        _fail("ORIGINAL_RECEIPT_INVALID")
    return result


def _parent(con, body):
    if body["kind"] != "budget_resume":
        return None
    row = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (body["defer_request_id"],)).fetchone()
    if row is None or row["action"] != "budget_defer":
        _fail("DEFER_REQUIRED")
    try:
        original, result = old_budget._receipt(row, con=con)
    except ValueError:
        _fail("ORIGINAL_RECEIPT_INVALID")
    if any(original[key][name] != body[key][name] for key in ("source", "target") for name in ("library_id", "node_id")):
        _fail("PEER_MISMATCH")
    return result


def legacy_receipt(*, profile, original_request):
    """Read an exact genuine pre17 positive without continuity activation.

    This private consistent SQL read grants no leaf/effect permission and does
    not open, create or repair external facilities. A missing old receipt is
    None, never proof of not_applied. New17 bodies never use this fallback.
    Same-UUID new control facts force the full continuity path instead.
    """
    profile = _profile(profile)
    original = wire._old_request(original_request)
    if original["source"]["handshake"]["migration_version"] >= 17:
        return None
    _runtime(capability=False)
    if _CURRENT.get() is not None or _EFFECT.get() is not None:
        _fail("WRITER_REQUIRED")
    with lifecycle.shared(profile):
        con = migrations._open(profile.database, readonly=True)
        try:
            con.execute("PRAGMA query_only=ON")
            con.execute("PRAGMA busy_timeout=0")
            con.execute("BEGIN")
            _structure(con, profile)
            _pair(con, original)

            def old_uuid(request_id):
                for table in (schema.REGISTRATION, schema.DISPOSITION):
                    if con.execute("SELECT 1 FROM " + table + " WHERE original_request_id=? LIMIT 1", (request_id,)).fetchone():
                        _fail("OPERATION_CONFLICT")

            old_uuid(original["request_id"])
            receipt = _original(con, original)
            if receipt is None:
                return None
            if original["kind"] == "budget_resume":
                old_uuid(original["defer_request_id"])
                parent = _parent(con, original)
                row = con.execute("SELECT * FROM sync_queue_budget_receipt WHERE request_id=?", (original["defer_request_id"],)).fetchone()
                parent_body, parent_receipt = old_budget._receipt(row, con=con)
                if (parent_body["source"]["handshake"]["migration_version"] >= 17
                        or parent_receipt != parent):
                    _fail("OPERATION_CONFLICT")
            return {**receipt, "replayed": True}
        except sqlite3.OperationalError as exc:
            if getattr(exc, "sqlite_errorcode", 0) & 0xFF in (sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED):
                _fail("BUSY")
            raise
        finally:
            con.rollback()
            con.close()


def _external_positive(owner, original, *, core_sha256=None, item_id=None):
    """A SQL replay cannot hide an incompatible permanent file fact.

    Historical controls without a registration remain exact positive replays.
    Once their UUID appears in the external closure its original body and any
    negative outcome survive an older SQL restoration, even when the caller
    omits the registration or the restored SQL lacks a disposition row.
    """
    observed = owner.guard.view()
    key = original["request_id"]
    expected = {"original_request_id": key, "original_request_sha256": _sha(original),
                "original_kind": original["kind"]}
    if core_sha256 is not None:
        expected["core_sha256"] = core_sha256
    if item_id is not None:
        expected["item_id"] = item_id
    for kind in external.KINDS:
        entry = observed["entries"][kind].get(key)
        if entry is not None and (kind == "tombstones"
                or any(entry["record"].get(name) != value for name, value in expected.items())):
            _fail("OPERATION_CONFLICT")


def _live(con, request, now):
    _runtime(capability=True)
    current, _ = queue._clock(now)
    session, _ = queue._session(con, request["source"], request["lease"], current)
    control = sync_recovery.assert_recovery_fence(con, request["recovery_generation"], request["recovery_stream"])
    if request["control_revision"] != control["revision"]:
        _fail("CONTROL_CHANGED")
    return session, current


def _registered(con, owner, row, *, _mode=None):
    activation, observed = _activation(con, owner, row["continuity_id"], _mode=_mode)
    request, result = json.loads(row["request_json"]), json.loads(row["result_json"])
    if request["continuity"] != {"format_version": 1, "continuity_id": activation["continuity_id"],
            "contract_sha256": activation["contract_sha256"], "target_epoch": activation["target_epoch"]}:
        _fail("CONTINUITY_REQUIRED")
    entry = observed["entries"]["registrations"].get(row["original_request_id"])
    if entry is None or entry["state"] != "committed" or entry["record"] != result:
        _fail("CONTINUITY_REQUIRED")
    return observed


def _historical_registered(con, owner, row):
    """Read one actual exact registration, with no fresh epoch permission."""
    actual = _lookup(con, schema.REGISTRATION, "request_id", row["request_id"])
    if actual is None or actual != row:
        _fail("CONTINUITY_REQUIRED")
    return _registered(con, owner, actual, _mode=_HISTORICAL)


def _control_record(request, result, *, parent=None):
    row = {key: result[key] for key in ("request_id", "format_version", "continuity_id", "core_sha256", "item_id",
        "original_request_id", "original_request_sha256", "original_kind", "registration_sha256", "committed_at")}
    row.update(_identities(request["source"], request["target"]))
    for name, value in (("request", request), ("result", result), ("original_request", request["original_request"])):
        row[name + "_json"], row[name + "_sha256"] = _json(value), _sha(value)
    if request["kind"] == "source_dispose":
        row.update(disposition=result["disposition"], disposition_sha256=wire.disposition_sha256(result),
                   defer_receipt_json=None if parent is None else _json(parent),
                   defer_receipt_sha256=None if parent is None else _sha(parent))
    return row


def register(*, profile, request, now=None):
    request = wire.normalize_register_request(request)
    with writer(profile=profile, capability=False, resume_request=("registrations", request["request_id"], _sha(request))) as con:
        owner = _owner(con, profile)
        _pair(con, request)
        existing = _lookup(con, schema.REGISTRATION, "request_id", request["request_id"])
        if existing is not None:
            if existing["request_json"] != _json(request):
                _fail("OPERATION_CONFLICT")
            _historical_registered(con, owner, existing)
            return {**json.loads(existing["result_json"]), "replayed": True}
        if _lookup(con, schema.REGISTRATION, "original_request_id", request["original_request"]["request_id"]) is not None:
            _fail("OPERATION_CONFLICT")
        if _original(con, request["original_request"]) is not None:
            _fail("ALREADY_APPLIED")
        if request["original_request"]["source"]["handshake"]["migration_version"] != 17:
            _fail("CAPABILITY_REQUIRED")
        observed = owner.guard.view()
        original = request["original_request"]
        permanent = observed["entries"]["registrations"].get(original["request_id"])
        if permanent is not None:
            # The external reservation/result preceded SQL. Its exact original
            # UUID/body can complete that gap without minting a new timestamp
            # or relying on a now-expired original session/recovery fence.
            try:
                result = wire.normalize_register_result(request, permanent["record"])
            except ValueError:
                _fail("OPERATION_CONFLICT")
            _activation(con, owner, request["continuity"]["continuity_id"], _mode=_HISTORICAL)
            owner.guard.register(request, result)
            _insert(con, schema.REGISTRATION, _control_record(request, result))
            return {**result, "replayed": True}
        _activation(con, owner, request["continuity"]["continuity_id"])
        session, current = _live(con, request, now)
        if original["kind"] == "budget_resume":
            parent = _parent(con, original)
            if parent["item_id"] != request["item_id"]:
                _fail("ORIGINAL_RECEIPT_INVALID")
        else:
            claim = queue._claim_record(con, session, original["claim"], current)
            if claim["status"] != "claimed" or claim["item_id"] != request["item_id"]:
                _fail("OUTCOME_MISMATCH")
        _, stamp = queue._clock(now)
        result = {"format_version": 1, "request_id": request["request_id"], "request_sha256": _sha(request),
            "original_request_id": original["request_id"], "original_request_sha256": _sha(original),
            "original_kind": original["kind"], "core_sha256": request["core_sha256"], "item_id": request["item_id"],
            "continuity_id": request["continuity"]["continuity_id"], "registration_sha256": "0" * 64,
            "committed_at": stamp, "replayed": False}
        result["registration_sha256"] = wire.registration_sha256(result)
        owner.guard.register(request, result)
        _insert(con, schema.REGISTRATION, _control_record(request, result))
        return result


def dispose(*, profile, request, now=None):
    request = wire.normalize_dispose_request(request)
    if request.get("format_version", 1) != 1:
        _fail("CAPABILITY_REQUIRED")
    return _dispose(profile=profile, request=request, now=now, version=1)


def _dispose(*, profile, request, now, version):
    """Shared original SQL effect; explicit version owners select the contract."""
    if type(version) is not int or version not in (1, 2):
        _fail("CAPABILITY_REQUIRED")
    request = wire.normalize_dispose_request(request)
    if request.get("format_version", 1) != version:
        _fail("CAPABILITY_REQUIRED")
    with writer(profile=profile, capability=False, resume_request=("tombstones", request["request_id"], _sha(request))) as con:
        owner = _owner(con, profile)
        if version == 2 and owner.schema_variant != "admission2_dispose2":
            _fail("SCHEMA_REQUIRED")
        _pair(con, request)
        existing = _lookup(con, schema.DISPOSITION, "request_id", request["request_id"])
        original_receipt = _original(con, request["original_request"])
        if original_receipt is not None:
            _external_positive(owner, request["original_request"],
                core_sha256=request["original"]["core_sha256"], item_id=request["original"]["item_id"])
        if existing is not None:
            if existing["request_json"] != _json(request) or existing["disposition"] == "not_applied" and original_receipt is not None:
                _fail("OPERATION_CONFLICT")
            if existing["disposition"] == "not_applied":
                registered = _lookup(con, schema.REGISTRATION, "original_request_id", existing["original_request_id"])
                if registered is None:
                    _fail("CONTINUITY_REQUIRED")
                observed = _historical_registered(con, owner, registered)
                entry = observed["entries"]["tombstones"].get(existing["original_request_id"])
                if entry is None or entry["state"] != "committed" or entry["record"] != json.loads(existing["result_json"]):
                    _fail("CONTINUITY_REQUIRED")
            return {**json.loads(existing["result_json"]), "replayed": True}
        if version == 2:
            from .sync_source_readmit_sql2 import _dispose_runtime
            # Only the actual permanent record for this original body can
            # finish its SQL gap without fresh capability or epoch authority.
            saved_negative = owner.guard.view()["entries"]["tombstones"].get(
                request["original_request"]["request_id"])
            if saved_negative is not None:
                try:
                    wire.normalize_dispose_result(request, saved_negative["record"])
                except ValueError:
                    _fail("OPERATION_CONFLICT")
            else:
                _dispose_runtime()
            supplied = request["continuity"]
            actual, _ = _activation(con, owner, supplied["continuity_id"],
                _mode=_HISTORICAL if original_receipt is not None or saved_negative is not None else None)
            if supplied != {"format_version": 1, "continuity_id": actual["continuity_id"],
                    "contract_sha256": actual["contract_sha256"], "target_epoch": actual["target_epoch"]}:
                _fail("CONTINUITY_REQUIRED")
        if _lookup(con, schema.DISPOSITION, "original_request_id", request["original_request"]["request_id"]) is not None:
            _fail("OPERATION_CONFLICT")
        parent = _parent(con, request["original_request"]) if original_receipt is not None else None
        registration = request["registration"]
        observed, registered = None, None
        if registration is not None:
            registered = _lookup(con, schema.REGISTRATION, "original_request_id", registration["original_request_id"])
            if registered is None or json.loads(registered["result_json"]) != registration or registered["original_request_json"] != _json(request["original_request"]):
                _fail("CONTINUITY_REQUIRED")
            previous = owner.guard.view()["entries"]["tombstones"].get(registered["original_request_id"])
            if original_receipt is not None or previous is not None:
                if previous is not None:
                    try:
                        wire.normalize_dispose_result(request, previous["record"])
                    except ValueError:
                        _fail("OPERATION_CONFLICT")
                observed = _historical_registered(con, owner, registered)
            else:
                observed = _registered(con, owner, registered)
        if original_receipt is None:
            if registered is None:
                _fail("CONTINUITY_REQUIRED")
            key = registered["original_request_id"]
            if key in observed["entries"]["effects"]:
                _fail("EFFECT_UNKNOWN")
            previous = observed["entries"]["tombstones"].get(key)
            if previous is not None:
                result = previous["record"]
                if result["request_id"] != request["request_id"] or result["request_sha256"] != _sha(request):
                    _fail("OPERATION_CONFLICT")
            else:
                _live(con, request, now)
                result = _disposition_result(request, None, now)
            owner.guard.commit_not_applied(request, result)
        else:
            if observed is not None and registered["original_request_id"] in observed["entries"]["tombstones"]:
                _fail("OPERATION_CONFLICT")
            result = _disposition_result(request, original_receipt, now)
        result = wire.normalize_dispose_result(request, result, defer_receipt=parent)
        _insert(con, schema.DISPOSITION, _control_record(request, result, parent=parent))
        return result


def _disposition_result(request, receipt, now):
    original, registration = request["original_request"], request["registration"]
    _, stamp = queue._clock(now)
    return {"format_version": request.get("format_version", 1), "request_id": request["request_id"], "request_sha256": _sha(request),
        "original_request_id": original["request_id"], "original_request_sha256": _sha(original),
        "original_kind": original["kind"], "core_sha256": request["original"]["core_sha256"],
        "item_id": request["original"]["item_id"], "disposition": "applied" if receipt is not None else "not_applied",
        "original_receipt": receipt, "continuity_id": None if registration is None else registration["continuity_id"],
        "registration_sha256": None if registration is None else registration["registration_sha256"],
        "committed_at": stamp, "replayed": False}


class EffectPermit:
    """A result bound to one actual writer; it is unusable outside that owner."""
    def __init__(self, owner, receipt):
        self._owner, self._seal, self.receipt = owner, _SEAL, deepcopy(receipt)

    def check(self, con, *, profile):
        if self._seal is not _SEAL or _owner(con, profile) is not self._owner:
            _fail("WRITER_REQUIRED")


class _EffectFrame:
    __slots__ = ("owner", "original_sha256", "receipt", "historical", "thread", "seal", "active")

    def __init__(self, owner, original):
        self.owner, self.original_sha256 = owner, _sha(original)
        self.receipt = deepcopy(_original(owner.con, original))
        self.historical = self.receipt is not None
        self.thread, self.seal, self.active = threading.get_ident(), _SEAL, True


@contextmanager
def _effect_frame(owner, original_request):
    if _EFFECT.get() is not None:
        _fail("EFFECT_REQUIRED")
    frame = _EffectFrame(owner, wire._old_request(original_request))
    token = _EFFECT.set(frame)
    try:
        yield
    finally:
        frame.active = False
        _EFFECT.reset(token)


def require_effect(con, original_request):
    """Validate one leaf call inside the exact sealed before_effect lifetime.

    Returns only an actual exact positive receipt for replay, or None for the
    already admitted fresh frame. Caller JSON, a retained permit and an active
    writer by themselves grant no effect authority. A historical frame whose
    SQL receipt disappears cannot fall through into fresh application.
    """
    owner = _CURRENT.get()
    if type(owner) is not _Writer or owner.con is not con:
        _fail("WRITER_REQUIRED")
    owner.check()
    frame = _EFFECT.get()
    if (type(frame) is not _EffectFrame or frame.seal is not _SEAL or not frame.active
            or frame.owner is not owner or frame.thread != threading.get_ident()):
        _fail("EFFECT_REQUIRED")
    original = wire._old_request(original_request)
    if _sha(original) != frame.original_sha256:
        _fail("EFFECT_BODY_MISMATCH")
    receipt = _original(con, original)
    if frame.historical and receipt != frame.receipt:
        _fail("EFFECT_RECEIPT_REQUIRED")
    if receipt is not None:
        _external_positive(owner, original, core_sha256=original.get("core_sha256"), item_id=receipt["item_id"])
        return {**receipt, "replayed": True}
    return None


@contextmanager
def before_effect(con, *, profile, original_request, now=None):
    """Pin the original proof and publish begin before the caller's SQL effect.

    An existing marker without an exact SQL positive is UNKNOWN. Fresh success
    must write that exact original receipt in this same writer before return.
    """
    owner = _owner(con, profile)
    if _EFFECT.get() is not None:
        _fail("EFFECT_REQUIRED")
    # Keep the effect and its receipt atomic even if a caller handles the
    # controlled error inside the larger writer and commits unrelated work.
    try:
        with queue._operation(con):
            with _before_effect(con, profile=profile, original_request=original_request, now=now) as permit:
                with _effect_frame(owner, original_request):
                    yield permit
    except sqlite3.OperationalError:
        if owner.sql_generation != 0:
            _fail("WRITER_REQUIRED")
        raise


@contextmanager
def _before_effect(con, *, profile, original_request, now=None):
    owner = _owner(con, profile)
    original = wire._old_request(original_request)
    _pair(con, original)
    receipt = _original(con, original)
    disposed = _lookup(con, schema.DISPOSITION, "original_request_id", original["request_id"])
    if disposed is not None and disposed["disposition"] == "not_applied":
        _fail("OUTCOME_MISMATCH")
    if receipt is not None:
        _external_positive(owner, original, core_sha256=original.get("core_sha256"), item_id=receipt["item_id"])
        permit = EffectPermit(owner, {**receipt, "replayed": True})
        yield permit
        permit.check(con, profile=profile)
        return
    _runtime(capability=True)
    row = _lookup(con, schema.REGISTRATION, "original_request_id", original["request_id"])
    if row is None or row["original_request_json"] != _json(original):
        _fail("CONTINUITY_REQUIRED")
    observed = _registered(con, owner, row)
    if original["request_id"] in observed["entries"]["tombstones"]:
        _fail("OUTCOME_MISMATCH")
    if original["request_id"] in observed["entries"]["effects"]:
        _fail("EFFECT_UNKNOWN")
    request = json.loads(row["request_json"])
    _live(con, request, now)
    _, stamp = queue._clock(now)
    owner.guard.begin_effect(json.loads(row["result_json"]), begun_at=stamp)
    permit = EffectPermit(owner, None)
    yield permit
    permit.check(con, profile=profile)
    if _original(con, original) is None:
        _fail("EFFECT_RECEIPT_REQUIRED")
