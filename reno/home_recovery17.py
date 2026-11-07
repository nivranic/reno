"""Actual Home17 knowledge restore and permanent receipt-first reconciliation.

Global migration/capability remains closed. This explicit producer operates only
on an already genuine17 image, accepts existing Home10-16 source declarations,
preserves all operational/paid facts, and never dispatches a provider. Neither
a comparison certificate nor a caller DTO can substitute the original bundle,
source, permanent intent/proposal and committed SQL receipt.
"""
from __future__ import annotations

from contextlib import contextmanager, ExitStack
from contextvars import ContextVar
from copy import deepcopy
from functools import lru_cache
import hashlib
import json
import sqlite3
import threading

from . import db, home_recovery as old, home_recovery_schema as schema
from . import home_recovery_source as sources, home_recovery_checkpoint as checkpoint
from . import migrations, source_preservation as base, source_preservation_certificate17 as certificate
from . import source_preservation_bundle as bundles, source_preservation_witness as witness
from . import storage_backups as backups, storage_lifecycle as lifecycle, sync_media as safe
from . import storage_root_inputs as root_inputs, storage_source_continuity_roots as cloud_roots
from . import storage_source_authority_roots as home_roots
from . import sync_queue, sync_state, sync_source_authority as authority, sync_source_readmit_schema as readmit, tasks
from .sync_recovery import _Pins

DIRECTORY = ".reno-home-knowledge-restores-v2"
MAX_METADATA_BYTES = 2 * 1024 * 1024
_REQUEST_FIELDS = frozenset({"format_version", "action", "operation_id", "request", "request_sha256",
    "library_id", "node_id", "owner_profile_sha256", "source", "before_capture_sha256", "bundle_id"})
_INTENT_FIELDS = frozenset({"format_version", "action", "operation_id", "request_sha256", "reservation_sha256",
    "bundle_id", "manifest_sha256", "bundle_receipt_sha256", "certificate_sha256", "file_set_sha256"})
_PROPOSAL_FIELDS = frozenset({"format_version", "action", "operation_id", "intent_sha256", "row_sha256", "committed_at"})
_RECEIPT_FIELDS = frozenset({"format_version", "action", "operation_id", "intent_sha256", "proposal_sha256",
    "row_sha256", "result_sha256", "checkpoint_sha256", "bundle_receipt_sha256", "committed_at"})
_PRODUCERS = ContextVar("reno_home17_restore_producers", default=())
_SEAL = object()


def _fail(code):
    old._fail(code)


def _json(value):
    return schema.canonical_json(value)


def _sha(value):
    return old._sha(value)


def _row_sha(record):
    return base._row_sha(schema.TABLE, schema.COLUMNS, record, [0, 0])


def _short(pins):
    for item in pins.files:
        pins._identity(item)


@contextmanager
def _owner(profile, store):
    old._existing_state_lock(profile)
    with store.exclusive(), store._guard, lifecycle.exclusive(profile, timeout=1):
        yield


def _expected_schema_effects(variant):
    """Immutable reference facts only; every actual database is read again."""
    if type(variant) is not str or variant not in {"v1", "admission2", "admission2_dispose2"}:
        _fail("SCHEMA_UNSUPPORTED")
    # Admission is outside the cache: equal/subclass keys cannot borrow a warm
    # template without this exact-type, closed-variant check.
    return _expected_schema_effects_cached(variant)


@lru_cache(maxsize=3)
def _expected_schema_effects_cached(variant):
    """Cache tuples of declarations, never a connection or validation result."""
    if type(variant) is not str or variant not in {"v1", "admission2", "admission2_dispose2"}:
        _fail("SCHEMA_UNSUPPORTED")
    reference = sqlite3.connect(":memory:")
    try:
        migrations._create_schema(reference, readmit_variant=variant)
        foreign_keys = tuple((table, tuple(sorted(tuple(row) for row in
            reference.execute(f'PRAGMA foreign_key_list("{table}")'))))
            for table in sorted(set(base.PROJECTIONS_V17) | set(base._KNOWLEDGE)))
        query = "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        triggers = tuple((row[0], row[1], schema._normalized(row[2]))
                         for row in reference.execute(query))
        return foreign_keys, triggers
    finally:
        reference.close()


def _actual_schema(con):
    """Exact prospective17 effects, without installing anything on live SQL."""
    base._schema(con, prospective_schema17=True)
    if con.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0] != "17":
        _fail("SCHEMA_UNSUPPORTED")
    from . import sync_source_readmit_schema2
    variant = sync_source_readmit_schema2.detect(con)
    expected_foreign_keys, expected_triggers = _expected_schema_effects(variant)
    effects = []
    for table, expected in expected_foreign_keys:
        actual = sorted(tuple(row) for row in con.execute(f'PRAGMA main.foreign_key_list("{table}")'))
        if tuple(actual) != expected:
            _fail("FOREIGN_KEY_CONTRACT")
        effects.append([table, actual])
    query = "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
    actual = [[row[0], row[1], schema._normalized(row[2])] for row in con.execute(query)]
    if tuple(tuple(row) for row in actual) != expected_triggers:
        _fail("TRIGGER_CONTRACT")
    effects.append(["triggers", actual])
    return _sha(effects)


def _foreign_closure17(con, candidate):
    # Existing16 semantic checks remain intact; additionally close preserved
    # references from every new table over the approved candidate knowledge.
    for table, columns in readmit.COLUMNS.items():
        grouped = {}
        for fk in con.execute(f'PRAGMA main.foreign_key_list("{table}")'):
            if fk[2] in candidate and fk[2] != "meta":
                grouped.setdefault((fk[0], fk[2]), []).append((fk[1], fk[3], fk[4]))
        for (_, target), fields in grouped.items():
            fields.sort()
            local, remote = [f[1] for f in fields], [f[2] for f in fields]
            if any(c not in columns for c in local) or any(c not in sources.COLUMNS[target] for c in remote):
                _fail("FOREIGN_KEY_CONTRACT")
            allowed = {tuple(row[c] for c in remote) for row in candidate[target]}
            names = ",".join('"' + c + '"' for c in local)
            for row in con.execute(f'SELECT {names} FROM main."{table}"'):
                if all(v is not None for v in row) and tuple(row) not in allowed:
                    _fail("PRESERVED_REFERENCE_MISSING")


def _capture(con, store, source, pins, profile, actual):
    effects = _actual_schema(con)
    state = old._state(store)
    version = db.knowledge_version(con)
    if state is not None and state["local_epoch"] != version["library_epoch"]:
        _fail("STATE_EPOCH_GAP")
    core = None if state is None else state.get("source_execution")
    before = certificate.capture(con, profile=profile, store=store,
        root_job_id=None if core is None else core["root_job_id"], witness=actual)
    semantic = old._semantic_closure(con, store, state, source.rows, pins, profile)
    _foreign_closure17(con, source.rows)
    document = {"format_version": 1, "action": "knowledge_restore", "source": source.public(),
        "before_version": version, "before_capture_sha256": before.sha256,
        "effects_sha256": effects, "semantic_closure": semantic}
    return before, {**document, "digest": _sha(document)}


def preview(*, profile, source_id):
    profile, source_id = old._profile(profile), old._uuid(source_id)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    old._existing_state_lock(profile)
    with store.reference_operation():
        with witness.sql_window_locked(profile=profile, store=store, writable=False) as con, ExitStack() as stack:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=profile, store=store) as actual:
                from . import home_recovery_source_dispatch as source_dispatch
                with source_dispatch.open_source(con, source_id, profile=profile, actual_witness=actual) as source:
                    pins = _Pins(stack)
                    _, result = _capture(con, store, source, pins, profile, actual)
                    source.verify()
                    pins.verify()
                    return result


def _directory(profile, stack, *, create=False):
    return stack.enter_context(safe._Directory(profile.database.parent / DIRECTORY,
        base=profile.database.parent, create=create))


def _read(directory, name, pins, *, maximum=MAX_METADATA_BYTES):
    raw, _ = sources._pin_file(pins, directory.path / name, maximum, capture=True,
                              base=directory.base)
    value = backups._decode(raw)
    if backups._json(value) != raw:
        _fail("RECEIPT_INVALID")
    return value, backups._sha(raw)


def _write(directory, name, value):
    raw = backups._json(value)
    if len(raw) > MAX_METADATA_BYTES:
        _fail("RECEIPT_LIMIT")
    directory.verify()
    sync_state._write(directory, name, raw, immutable=True)
    return backups._sha(raw)


def _persist(directory, name, value, pins):
    try:
        existing, sha = _read(directory, name, pins)
    except FileNotFoundError:
        _write(directory, name, value)
        existing, sha = _read(directory, name, pins)
    if existing != value:
        _fail("OPERATION_CONFLICT")
    return sha


def _record(con, operation_id):
    row = con.execute('SELECT ' + ",".join(schema.COLUMNS) + ' FROM main."' + schema.TABLE + '" WHERE operation_id=?',
                      (operation_id,)).fetchone()
    if row is None:
        return None
    record = schema.validate_record(row)
    checkpoint.validate(record, schema_version=17)
    return record


def _request_record(operation_id, request, before, source, profile):
    doc = before.public()
    bundle_id, _ = bundles._ids(operation_id)
    return {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation_id,
        "request": request, "request_sha256": _sha(request), "library_id": doc["library_id"], "node_id": doc["node_id"],
        "owner_profile_sha256": _sha(list(profile.identity)), "source": source.public(),
        "before_capture_sha256": before.sha256, "bundle_id": bundle_id}


def _intent_record(operation_id, request_sha, reservation_sha, bundle):
    facts = bundle.facts()
    manifest = facts["manifest"]
    return {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation_id,
        "request_sha256": request_sha, "reservation_sha256": reservation_sha, "bundle_id": manifest["bundle_id"],
        "manifest_sha256": manifest["manifest_sha256"], "bundle_receipt_sha256": facts["receipt_sha256"],
        "certificate_sha256": manifest["certificate"]["sha256"], "file_set_sha256": manifest["file_set_sha256"]}


def _proposal_record(operation_id, intent_sha, record):
    return {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation_id,
        "intent_sha256": intent_sha, "row_sha256": _row_sha(record), "committed_at": record["committed_at"]}


def _receipt_record(record, intent_sha, proposal_sha, bundle):
    return {"format_version": 2, "action": "home_knowledge_restore", "operation_id": record["operation_id"],
        "intent_sha256": intent_sha, "proposal_sha256": proposal_sha, "row_sha256": _row_sha(record),
        "result_sha256": record["result_sha256"], "checkpoint_sha256": record["checkpoint_sha256"],
        "bundle_receipt_sha256": bundle.facts()["receipt_sha256"], "committed_at": record["committed_at"]}


def _verify_transition(con, before, record, bundle, *, profile, store, actual):
    document = certificate._certificate(before)
    parsed = checkpoint.validate(record, schema_version=17)
    if parsed.wrapper != bundle.checkpoint_wrapper():
        _fail("TRANSITION_INVALID")
    after = certificate.capture(con, profile=profile, store=store,
        root_job_id=document["frontier"]["root_job_id"], witness=actual)
    current = after.public()
    if (record["before_version_json"] != _json(document["knowledge_version"])
            or record["after_version_json"] != _json(current["knowledge_version"])
            or record["frontier_json"] != _json(document["frontier"])
            or record["library_id"] != document["library_id"] or record["node_id"] != document["node_id"]
            or record["before_operational_sha256"] != document["preserved_sha256"]):
        _fail("TRANSITION_INVALID")
    order, _, _, projections = base._schema(con, prospective_schema17=True)
    columns = projections[schema.TABLE]
    digest = hashlib.sha256(base._encoded([base.FORMAT_VERSION, schema.TABLE, list(columns)]))
    scan, count, matched = [0, 0], 0, 0
    for row in base._rows(con, schema.TABLE, order, scan, projections):
        checkpoint.validate(row, schema_version=17)
        if row["operation_id"] == record["operation_id"]:
            if row != record:
                _fail("TRANSITION_INVALID")
            matched += 1
        else:
            digest.update(bytes.fromhex(base._row_sha(schema.TABLE, columns, row, scan)))
            count += 1
    if matched != 1:
        _fail("TRANSITION_INVALID")
    normalized = {key: current[key] for key in certificate._PRESERVED_FIELDS}
    replacement = {"name": schema.TABLE, "rows": count, "hashed_bytes": scan[1], "sha256": digest.hexdigest()}
    normalized["tables"] = [replacement if row["name"] == schema.TABLE else row for row in normalized["tables"]]
    if base._sha(normalized) != document["preserved_sha256"]:
        _fail("PRESERVATION_CHANGED")
    bundle.verify_for_commit()
    return after


class RestoreProducerCapture:
    """Actual committed row + permanently bound files; no source admission."""
    __slots__ = ("_seal", "_profile", "_store", "_owner", "_guard", "_thread", "_active", "_record",
                 "_pins", "_directory", "_bundle", "_intent_sha", "_proposal_sha", "_receipt_sha")

    def __init__(self, *, seal, profile, store, record, pins, directory, bundle, intent_sha, proposal_sha):
        if seal is not _SEAL:
            _fail("PRODUCER_REQUIRED")
        self._seal, self._profile, self._store, self._record = seal, profile, store, deepcopy(record)
        self._owner, self._guard = authority._Ownership(store), lifecycle.require_guard(profile, exclusive=True)
        self._thread, self._active = threading.get_ident(), True
        self._pins, self._directory, self._bundle = pins, directory, bundle
        self._intent_sha, self._proposal_sha, self._receipt_sha = intent_sha, proposal_sha, None

    def verify_for_commit(self):
        if (self._seal is not _SEAL or not self._active or self._thread != threading.get_ident()
                or not any(value is self for value in _PRODUCERS.get())):
            _fail("PRODUCER_REQUIRED")
        self._owner.verify()
        if lifecycle.require_guard(self._profile, exclusive=True) is not self._guard:
            _fail("PRODUCER_REQUIRED")
        _short(self._pins)
        self._bundle.verify_for_commit()

    def verify_source(self):
        self.verify_for_commit()
        current = witness._WINDOW.get()
        if current is not None and current.connection.execute("PRAGMA query_only").fetchone()[0] != 1:
            _fail("PRODUCER_READ_WINDOW_REQUIRED")
        self._pins.verify()
        self._bundle.verify_source()

    def facts(self):
        self.verify_for_commit()
        if self._receipt_sha is None:
            _fail("PRODUCER_RECEIPT_MISSING")
        return {"producer": "home_knowledge_restore", "operation_id": self._record["operation_id"],
            "receipt_sha256": self._record["result_sha256"], "permanent_receipt_sha256": self._receipt_sha,
            "record": deepcopy(self._record), "certificate": self._bundle.before().public()}


@contextmanager
def resolve_producer_locked(con, *, profile, store, operation_id, actual_witness,
                            expected_result_sha256=None, allow_publish=False):
    """Read the original full committed SQL row, not a caller receipt DTO."""
    if type(allow_publish) is not bool or type(con) is not sqlite3.Connection:
        _fail("PRODUCER_REQUIRED")
    witness.require_capture(actual_witness, connection=con, profile=profile, store=store)
    if con.execute("PRAGMA query_only").fetchone()[0] != 1:
        _fail("PRODUCER_READ_WINDOW_REQUIRED")
    _actual_schema(con)
    record = _record(con, old._uuid(operation_id))
    if record is None:
        _fail("OPERATION_MISSING")
    parsed = checkpoint.validate(record, schema_version=17)
    if parsed.wrapper is None or expected_result_sha256 is not None and record["result_sha256"] != expected_result_sha256:
        _fail("PRODUCER_RECEIPT_INVALID")
    identity = actual_witness.identity()
    if identity != {"library_id": record["library_id"], "node_id": record["node_id"]}:
        _fail("REPLAY_IDENTITY_MISMATCH")
    with ExitStack() as stack:
        pins = _Pins(stack)
        directory = _directory(profile, stack)
        reservation, reservation_sha = _read(directory, operation_id + ".request.json", pins)
        intent, intent_sha = _read(directory, operation_id + ".intent.json", pins)
        proposal, proposal_sha = _read(directory, operation_id + ".proposal.json", pins)
        if (set(reservation) != _REQUEST_FIELDS or set(intent) != _INTENT_FIELDS or set(proposal) != _PROPOSAL_FIELDS
                or proposal != _proposal_record(operation_id, intent_sha, record)):
            _fail("PRODUCER_RECEIPT_INVALID")
        bundle = stack.enter_context(bundles.open_completed_locked(con, profile=profile, store=store,
            operation_id=operation_id, root_job_id=record["root_job_id"], actual_witness=actual_witness,
            expected_manifest_sha256=parsed.wrapper["bundle"]["manifest_sha256"],
            expected_receipt_sha256=intent["bundle_receipt_sha256"]))
        before = bundle.before().public()
        source = reservation["source"]
        if (reservation != {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation_id,
                "request": json.loads(record["request_json"]), "request_sha256": record["request_sha256"],
                "library_id": record["library_id"], "node_id": record["node_id"],
                "owner_profile_sha256": _sha(list(profile.identity)), "source": source,
                "before_capture_sha256": bundle.before().sha256, "bundle_id": parsed.wrapper["bundle"]["bundle_id"]}
                or intent != _intent_record(operation_id, record["request_sha256"], reservation_sha, bundle)
                or parsed.wrapper != bundle.checkpoint_wrapper()
                or source["source_id"] != record["source_id"] or source["source_kind"] != "home_checkpoint"
                or source["source_sha256"] != record["source_sha256"]
                or source["source_binding_sha256"] != record["source_binding_sha256"]
                or record["before_version_json"] != _json(before["knowledge_version"])
                or record["frontier_json"] != _json(before["frontier"])
                or record["before_operational_sha256"] != before["preserved_sha256"]):
            _fail("PRODUCER_RECEIPT_INVALID")
        backup = bundle.facts()["manifest"]["database_backup"]
        if backup != {"backup_id": record["protection_backup_id"], "image_sha256": record["protection_image_sha256"],
                      "descriptor_sha256": record["protection_descriptor_sha256"]}:
            _fail("PRODUCER_RECEIPT_INVALID")
        capture = RestoreProducerCapture(seal=_SEAL, profile=profile, store=store, record=record, pins=pins,
            directory=directory, bundle=bundle, intent_sha=intent_sha, proposal_sha=proposal_sha)
        token = _PRODUCERS.set((*_PRODUCERS.get(), capture))
        try:
            expected = _receipt_record(record, intent_sha, proposal_sha, bundle)
            try:
                actual, capture._receipt_sha = _read(directory, operation_id + ".receipt.json", pins)
            except FileNotFoundError:
                if not allow_publish:
                    _fail("PRODUCER_RECEIPT_MISSING")
            else:
                if set(actual) != _RECEIPT_FIELDS or actual != expected:
                    _fail("PRODUCER_RECEIPT_INVALID")
            capture.verify_source()
            yield capture
            capture.verify_source()
        finally:
            capture._active = False
            _PRODUCERS.reset(token)


def _publish_receipt(con, store, operation_id, capture):
    capture.verify_source()
    record = _record(con, operation_id)
    if record != capture._record:
        _fail("PRODUCER_RECEIPT_INVALID")
    if capture._receipt_sha is None:
        expected = _receipt_record(record, capture._intent_sha, capture._proposal_sha, capture._bundle)
        capture._receipt_sha = _persist(capture._directory, operation_id + ".receipt.json", expected, capture._pins)
    capture.verify_source()
    return capture.facts()


def _chain(con, record, version, identity):
    records, current = [], record
    while True:
        parsed = checkpoint.validate(current, schema_version=17)
        if ((current["library_id"], current["node_id"]) != identity
                or current["after_library_epoch"] > version["library_epoch"]
                or current["after_kb_revision"] > version["kb_revision"]):
            _fail("REPLAY_IDENTITY_MISMATCH")
        records.append((current, parsed))
        if current["after_library_epoch"] == version["library_epoch"]:
            return records
        rows = con.execute('SELECT ' + ",".join(schema.COLUMNS) + ' FROM main."' + schema.TABLE +
            '" WHERE library_id=? AND before_library_epoch=?', (identity[0], current["after_library_epoch"])).fetchall()
        if len(rows) != 1:
            _fail("EPOCH_CHAIN_MISSING")
        later = schema.validate_record(rows[0])
        if later["before_kb_revision"] < current["after_kb_revision"]:
            _fail("EPOCH_CHAIN_MISSING")
        current = later


@contextmanager
def _live_state_references(profile, store, state, version):
    """Retain actual current facilities while checking/updating State epoch.

    Historic bundle copies cannot stand in for State5's current live authority
    references. This is a distinct readonly observation with no immutable-State
    checkpoint callback; the only approved State change is checked by caller.
    """
    with ExitStack() as stack:
        con = migrations._open(profile.database, readonly=True)
        stack.callback(con.close)
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        _actual_schema(con)
        if db.knowledge_version(con) != version:
            _fail("STATE_CHANGED")
        identity = witness._IdentityCapture(store, con, stack)
        cloud = stack.enter_context(cloud_roots.capture_locked(profile,
            sql_projection=root_inputs.read_continuity_projection(con), check_cancelled=store.check_cancelled))
        home = stack.enter_context(home_roots.capture_absence_locked(profile, format_version=2,
            check_cancelled=store.check_cancelled)
            if state is None else home_roots.capture_locked(profile, binding=state["binding"],
                                                          format_version=2, check_cancelled=store.check_cancelled))
        if any(item.unknown or item.summary["presence"] not in {"absent", "present"} for item in (cloud, home)):
            _fail("STATE_AUTHORITY_INCOMPLETE")
        witness._resolve_state(state, home)
        cloud.verify_source()
        home.verify_source()
        identity.verify_for_commit(con)
        yield
        identity.verify_for_commit(con)
        cloud.verify_source()
        home.verify_source()
        con.rollback()
        if db.knowledge_version(con) != version:
            _fail("STATE_CHANGED")


def _reconcile(profile, store, operation_id):
    """Authenticate every original step before either epoch early return."""
    with store.reference_operation(), ExitStack() as stack:
        with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            _actual_schema(con)
            record = _record(con, operation_id)
            if record is None:
                _fail("OPERATION_MISSING")
            identity = sync_queue._local_identity(con)
            version = db.knowledge_version(con)
            records = _chain(con, record, version, identity)
        # Long original artifact checks happen outside any SQL writer. Each
        # actual readonly witness has its own bounded file-observation window.
        captures = []
        for current, parsed in records:
            if parsed.wrapper is None:
                continue
            with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
                con.execute("PRAGMA query_only=ON")
                with witness.capture_locked(con, profile=profile, store=store) as actual:
                    if _record(con, current["operation_id"]) != current or db.knowledge_version(con) != version:
                        _fail("STATE_CHANGED")
                    capture = stack.enter_context(resolve_producer_locked(con, profile=profile, store=store,
                        operation_id=current["operation_id"], actual_witness=actual,
                        expected_result_sha256=current["result_sha256"], allow_publish=True))
                    _publish_receipt(con, store, current["operation_id"], capture)
                    captures.append(capture)
        state = old._state(store)
        original = records[0][1].checkpoint
        result = old._validate_business(record)
        with _live_state_references(profile, store, state, version):
            if state is not None and ((state["binding"]["home_library_id"], state["binding"]["home_node_id"]) != identity
                                     or state["local_epoch"] > version["library_epoch"]):
                _fail("STATE_CHANGED")
            if not original["state_present"]:
                if state is not None and state["local_epoch"] < record["after_library_epoch"]:
                    _fail("STATE_CHANGED")
            elif state is None:
                _fail("STATE_CHANGED")
            else:
                normalized = {k: v for k, v in state.items() if k != "local_epoch"}
                # Even already-reconciled State must not bypass a malformed
                # later wrapper or a broken mixed epoch chain.
                if state["local_epoch"] < record["after_library_epoch"]:
                    if state["local_epoch"] != record["before_library_epoch"] or _sha(state) != original["canonical_state_sha256"]:
                        _fail("STATE_CHANGED")
                    for step, parsed in records:
                        expected = {**normalized, "local_epoch": step["before_library_epoch"]}
                        if parsed.checkpoint["canonical_state_sha256"] != _sha(expected):
                            _fail("STATE_CHANGED")
                    for capture in captures:
                        capture.verify_source()
                    before = deepcopy(state)
                    store.update({"local_epoch": version["library_epoch"]})
                    if store.read() != {**before, "local_epoch": version["library_epoch"]}:
                        _fail("STATE_CHANGED")
        for capture in captures:
            capture.verify_source()
        return result


def apply(*, profile, source_id, expected_digest, operation_id):
    profile, operation_id = old._profile(profile), old._uuid(operation_id)
    request = old._request(source_id, expected_digest)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        with _owner(profile, store), ExitStack() as stack:
            # Existing original SQL receipts always win over source reopening.
            with store.reference_operation():
                with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
                    _actual_schema(con)
                    existing = _record(con, operation_id)
                    if existing is not None and existing["request_json"] != _json(request):
                        _fail("OPERATION_CONFLICT")
            if existing is not None:
                return {**_reconcile(profile, store, operation_id), "replayed": True}
            pins = _Pins(stack)
            with store.reference_operation():
                with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
                    con.execute("PRAGMA query_only=ON")
                    with witness.capture_locked(con, profile=profile, store=store) as actual:
                        from . import home_recovery_source_dispatch as source_dispatch
                        source = stack.enter_context(source_dispatch.open_source(
                            con, source_id, profile=profile, actual_witness=actual))
                        before, observed = _capture(con, store, source, pins, profile, actual)
                        if observed["digest"] != expected_digest:
                            _fail("CAS_MISMATCH")
                        directory = _directory(profile, stack, create=True)
                        reservation = _request_record(operation_id, request, before, source, profile)
                        reservation_sha = _persist(directory, operation_id + ".request.json", reservation, pins)
                        source.verify()
                        pins.verify()
            root = before.public()["frontier"]["root_job_id"]
            # Bundle owns a distinct readonly producer window. No old SQL cap
            # survives its transaction boundaries.
            bundles.create(profile=profile, store=store, operation_id=operation_id, root_job_id=root)
            with store.reference_operation(), ExitStack() as bundle_stack:
                with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
                    con.execute("PRAGMA query_only=ON")
                    with witness.capture_locked(con, profile=profile, store=store) as actual:
                        bundle = bundle_stack.enter_context(bundles.open_completed_locked(con, profile=profile, store=store,
                            operation_id=operation_id, root_job_id=root, actual_witness=actual))
                        if bundle.before().sha256 != before.sha256:
                            _fail("CAS_MISMATCH")
                        intent = _intent_record(operation_id, _sha(request), reservation_sha, bundle)
                        intent_sha = _persist(directory, operation_id + ".intent.json", intent, pins)
                        try:
                            proposed, _ = _read(directory, operation_id + ".proposal.json", pins, maximum=16 * 1024)
                        except FileNotFoundError:
                            proposed = None
                        source.verify()
                        pins.verify()
                with witness.sql_window_locked(profile=profile, store=store, writable=True) as con:
                    with witness.capture_locked(con, profile=profile, store=store) as actual:
                        _actual_schema(con)
                        actual_before = certificate.capture(con, profile=profile, store=store, root_job_id=root, witness=actual)
                        if actual_before.sha256 != before.sha256:
                            _fail("CAS_MISMATCH")
                        bundle.verify_for_commit()
                        _short(pins)
                        _short(source._pins)
                        version = before.public()["knowledge_version"]
                        if version["library_epoch"] >= schema.MAX_INTEGER or version["kb_revision"] >= schema.MAX_INTEGER:
                            _fail("VERSION_EXHAUSTED")
                        old._write_knowledge(con, source.rows)
                        revision = max(db.knowledge_version(con)["kb_revision"], version["kb_revision"] + 1,
                                       source.declaration["source_version"]["kb_revision"] + 1)
                        if revision > schema.MAX_INTEGER:
                            _fail("VERSION_EXHAUSTED")
                        con.execute("UPDATE main.meta SET value=? WHERE key='library_epoch'", (str(version["library_epoch"] + 1),))
                        con.execute("UPDATE main.meta SET value=? WHERE key='kb_revision'", (str(revision),))
                        certificate.verify_unchanged(con, before, profile=profile, store=store, witness=actual)
                        facts = bundle.facts()["manifest"]["database_backup"]
                        protection = {"protection_backup_id": facts["backup_id"],
                            "protection_descriptor_sha256": facts["descriptor_sha256"], "protection_image_sha256": facts["image_sha256"]}
                        record = old._build_record(operation_id, source, before, db.knowledge_version(con),
                                                   protection, request, _sha(sources._read_knowledge(con)))
                        record["checkpoint_json"] = _json(bundle.checkpoint_wrapper())
                        record["checkpoint_sha256"] = _sha(bundle.checkpoint_wrapper())
                        if proposed is not None:
                            record["committed_at"] = proposed["committed_at"]
                        checkpoint.validate(record, schema_version=17)
                        con.execute('INSERT INTO main."' + schema.TABLE + '"(' + ",".join(schema.COLUMNS) +
                            ') VALUES(' + ",".join("?" for _ in schema.COLUMNS) + ')', tuple(record[col] for col in schema.COLUMNS))
                        _verify_transition(con, before, record, bundle, profile=profile, store=store, actual=actual)
                        # Small immutable proposal records the actual verified
                        # one-row transition before COMMIT. No source copy or
                        # full artifact SHA is performed in this writer.
                        proposal = _proposal_record(operation_id, intent_sha, record)
                        if proposed is None:
                            _write(directory, operation_id + ".proposal.json", proposal)
                        elif proposed != proposal:
                            _fail("OPERATION_CONFLICT")
                        # The bounded scalar proposal commits only the exact
                        # typed row SHA and original timestamp. Never write a
                        # potentially8MiB frontier again while holding SQL.
                        published, _ = _read(directory, operation_id + ".proposal.json", pins, maximum=16 * 1024)
                        if published != proposal:
                            _fail("PRODUCER_RECEIPT_INVALID")
                        _verify_transition(con, before, record, bundle, profile=profile, store=store, actual=actual)
                        _short(pins)
                        _short(source._pins)
                        bundle.verify_for_commit()
                        _actual_schema(con)
                # This point follows actual SQL COMMIT and the full original
                # witness exit checks. The separate State epoch comes later.
                source.verify()
                pins.verify()
            # Release the old BundleCapture/reference frame before resolving
            # committed rows with the original permanent producer lifecycle.
            stack.close()
            return {**_reconcile(profile, store, operation_id), "replayed": False}


def resume(*, profile, operation_id):
    profile, operation_id = old._profile(profile), old._uuid(operation_id)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        with _owner(profile, store):
            return {**_reconcile(profile, store, operation_id), "replayed": True}
