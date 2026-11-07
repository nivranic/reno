"""Owned SH reader of completed Home17 restore provenance.

The factory reads its own genuine readonly17 window and retains independent
original artifact pins after that SQL window closes. It neither creates a
producer receipt nor grants admission, dispatch, retirement or continuity
after an external rollback. The short budget consumer requires that factory's
original transaction-bound token and leaves its final comparison with the
factory. It does not opt ordinary schema16 into17 or connect budget business
reserve/dispatch and fresh Home execution.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import threading
import time

from . import db, home_recovery as legacy, home_recovery17 as restore
from . import budget_writer_witness as budget_writer
from . import home_recovery_checkpoint as checkpoint, home_recovery_schema as schema
from . import source_preservation as base, source_preservation_bundle as bundles
from . import source_preservation_certificate17 as certificate, source_preservation_witness as witness
from . import home_authority_pending_contract17 as pending_contract
from . import storage_backups as backups, storage_lifecycle as lifecycle
from . import storage_root_inputs as inputs, storage_source as physical
from . import storage_home_recovery_dependency17 as files, storage_home_roots as home
from . import storage_source_authority_roots as home_roots
from . import storage_source_continuity_roots as cloud_roots
from . import storage_legacy_pending_dependencies17 as pending_dependencies
from . import sync_source_authority as authority, sync_state

_CURRENT = ContextVar("reno_completed_restore_runtime17", default=())
_HISTORICAL = ContextVar("reno_historical_restore_prefix17", default=())
_HISTORICAL_CONTEXT = ContextVar("reno_historical_restore_prefix17_context", default=None)
_SEAL = object()
MAX_CHAIN = 4096


class RuntimeRestoreError(ValueError):
    def __init__(self, code):
        self.code = "HOME_RUNTIME17_" + code
        super().__init__(self.code)


def _fail(code):
    raise RuntimeRestoreError(code) from None


def _same(found, expected):
    # Typed cells, including integer versus real, are checked separately for
    # actual SQL rows. Canonical file documents use the original byte hashes.
    if found != expected:
        _fail("PROVENANCE_INVALID")


class _RuntimePins(files._ProofPins):
    """Actual files only; bounded mutable catalogs, independent SH ownership."""
    def __init__(self, stack, profile, callback):
        super().__init__(stack, profile, callback)
        # Pure facts from this reader's own already verified original bundle.
        # They bind the original selection without a long-lived dependency
        # child whose source-cache anchor would conflict with legal appends.
        self._copied_pending_dependencies = {}

    def verify_short(self):
        self.check()
        for directory in self.directories:
            directory.verify()
        for directory, name in self.absences:
            self.check()
            try:
                directory.entry_stat(name)
            except FileNotFoundError:
                continue
            _fail("ARTIFACT_CHANGED")
        for directory, names in self.names:
            found = []
            with os.scandir(directory.fd if os.name != "nt" else files.safe._native_path(directory.path)) as entries:
                for item in entries:
                    self.check()
                    if len(found) >= len(names):
                        _fail("ARTIFACT_CHANGED")
                    found.append(item.name)
            if tuple(sorted(found)) != names:
                _fail("ARTIFACT_CHANGED")
        # Catalogs are bounded small mutable records, not whole backup images.
        for directory, name, signature, identity, maximum in self.mutable:
            self.check()
            if physical.file_signature(directory.entry_stat(name)) != signature:
                _fail("ARTIFACT_CHANGED")
            if backups._read(directory.path / name, maximum=maximum, content=False,
                    read_control=self)[1] != identity:
                _fail("ARTIFACT_CHANGED")
        for item in self.files:
            self.check()
            self._identity(item)


def _protection_image(pins, record, profile, *, legacy_row=False):
    identifier = record["protection_backup_id"]
    catalog_path = backups._catalog(profile) / (identifier + ".json")
    catalog, _ = pins.document(catalog_path, backups.MAX_JSON, mutable=True)
    with backups.controlled_reads(pins):
        checked, _ = backups._record(catalog_path)
    _same(catalog, checked)
    expected_id = (str(files.uuid.uuid5(files.uuid.UUID(record["operation_id"]),
        "reno.home.knowledge.restore.protection.v1")) if legacy_row else bundles._ids(record["operation_id"])[1])
    expected_path = profile.database.with_name(profile.database.name + (
        ".bak-home-restore-" + record["operation_id"] if legacy_row else ".bak-preservation17-" + identifier))
    if (catalog["state"] != "ready" or catalog["kind"] != "maintenance"
            or identifier != expected_id or catalog["source_profile"] != backups._profile_json(profile)
            or catalog["destination"] != backups._key(expected_path)):
        _fail("PROTECTION_INVALID")
    descriptor, descriptor_identity = pins.document(expected_path.with_name(expected_path.name + backups.SUFFIX),
        backups.MAX_JSON, expected=record["protection_descriptor_sha256"])
    backups._descriptor(descriptor, catalog)
    _same(descriptor, catalog["descriptor"])
    _same(catalog["descriptor_sha256"], descriptor_identity["sha256"])
    _same(descriptor["source_roots"], backups._roots(profile))
    _same(descriptor["schema_version"], 16 if legacy_row else 17)
    _same(descriptor["source_version"], files.backups._decode(record["before_version_json"].encode()))
    _same(descriptor["database_file"]["sha256"], record["protection_image_sha256"])
    _, image_identity = pins.read(expected_path, backups.MAX_IMAGE,
        expected=record["protection_image_sha256"], capture=False)
    _same(image_identity, descriptor["database_file"])
    for suffix in ("-wal", "-shm", "-journal"):
        raw, _ = pins.read(expected_path.with_name(expected_path.name + suffix), backups.MAX_IMAGE, optional=True)
        if raw is not None:
            _fail("PROTECTION_SIDECAR")
    stream = next(item[2] for item in reversed(pins.files)
        if item[0].path / item[1] == expected_path)
    temporary = pins.stack.enter_context(tempfile.TemporaryDirectory(prefix="reno-runtime17-original-"))
    private = Path(temporary) / "image.sqlite"
    os.lseek(stream.fileno(), 0, os.SEEK_SET)
    digest, count = hashlib.sha256(), 0
    with private.open("xb") as target:
        while chunk := os.read(stream.fileno(), 128 * 1024):
            pins.check()
            count += len(chunk)
            if count > image_identity["byte_size"]:
                _fail("ARTIFACT_CHANGED")
            digest.update(chunk)
            target.write(chunk)
    _same((count, digest.hexdigest()), (image_identity["byte_size"], image_identity["sha256"]))
    os.chmod(private, 0o600)
    # The shared typed SQL scanner resolves node identity beside its image.
    # Copy the actual pinned local identity bytes, never synthesize an ID or
    # infer one from a caller/descriptor. Original row+current owned identity
    # must already agree; this private copy only supports pure SQL hashing.
    identity_raw, _ = pins.read(profile.database.parent / "node-identity.json", 4096)
    _same(witness.identity_admin._node(identity_raw), record["node_id"])
    identity_path = private.parent / "node-identity.json"
    with identity_path.open("xb") as output:
        output.write(identity_raw)
    os.chmod(identity_path, 0o600)
    con = sqlite3.connect(private.as_uri() + "?mode=ro&immutable=1", uri=True)
    pins.stack.callback(con.close)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA query_only=ON")
    con.execute("PRAGMA trusted_schema=OFF")
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, base.MAX_CELL_BYTES)
    con.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH, base.MAX_SCHEMA_BYTES)
    con.execute("BEGIN")
    steps = [0]
    def progress():
        steps[0] += 1000
        pins.check()
        return int(steps[0] > 20_000_000)
    con.set_progress_handler(progress, 1000)
    (legacy._actual_schema if legacy_row else restore._actual_schema)(con)
    return con


def _copied_checkpoint(record, document, copies, profile, protection, pins):
    raw = copies.get(("DATA", sync_state._STATE))
    state = None if raw is None else sync_state._decode(raw)
    if state is not None:
        sync_state._validate(state)
        _same(state["local_epoch"], record["before_library_epoch"])
    home_reader = files._CopiesReader(profile, copies, pins.check, home_kind=True)
    original_home = pending_contract.normalize_summary(document["permanent_witnesses"]["home_authority"])
    pending, _private_images = home_reader.legacy_pending_state(document)
    summary, refs = (home_roots._capture_absence(home_reader,
        format_version=original_home["format_version"], legacy_pending=pending) if state is None
        else home_roots._capture(home_reader, state["binding"],
            format_version=original_home["format_version"], legacy_pending=pending))
    _same(summary, document["permanent_witnesses"]["home_authority"])
    # State5's original references must be in the original copied facility.
    witness._resolve_state(state, type("_HistoricHome", (), {"references": refs})())
    cloud_summary, _ = cloud_roots._capture(files._CopiesReader(profile, copies, pins.check),
        inputs.read_continuity_projection(protection), None)
    _same(cloud_summary, document["permanent_witnesses"]["cloud_continuity"])
    expected = document["checkpoint"]
    if state is None:
        observed = {"state_present": False,
            "absence_sha256": base._sha({"format_version": 1, "state": "absent"}),
            "state_sha256": None, "state_bytes": 0, "canonical_state_sha256": None,
            "state_format_version": None, "source_execution_sha256": None,
            "source_execution_lineage": None, "outboxes": []}
    else:
        core = state.get("source_execution")
        _same((state["binding"]["home_library_id"], state["binding"]["home_node_id"]),
            (record["library_id"], record["node_id"]))
        if core is not None:
            _same(core["root_job_id"], record["root_job_id"])
        outboxes = []
        # Metadata identifies exact original outboxes. The pure validator
        # checks their actual saved bytes, claims, library and publication.
        for item in expected["outboxes"]:
            pins.check()
            outbox_raw = copies.get(("DATA", sync_state._OUTBOX + "/" + item["publication_id"] + ".json"))
            if outbox_raw is None:
                _fail("CHECKPOINT_INCOMPLETE")
            value = home._outbox(sync_state._decode(outbox_raw), item["publication_id"] + ".json",
                {"library_id": record["library_id"], "node_id": record["node_id"]})
            outboxes.append({"publication_id": item["publication_id"], "sha256": files._sha(outbox_raw),
                "byte_size": len(outbox_raw), "semantic_sha256": base._sha(value)})
        references = set()
        if state["pending_publication"] is not None:
            references.add(state["pending_publication"]["publication_id"])
        if state["recovery_attempt"] is not None:
            references.add(state["recovery_attempt"]["publication_id"])
        _same(references, {item["publication_id"] for item in outboxes})
        observed = {"state_present": True, "absence_sha256": None,
            "state_sha256": files._sha(raw), "state_bytes": len(raw), "canonical_state_sha256": base._sha(state),
            "state_format_version": state["format_version"],
            "source_execution_sha256": None if core is None else base._sha(core),
            "source_execution_lineage": None if core is None else {key: core[key] for key in
                ("root_job_id", "continuation_id", "execution_generation")}, "outboxes": outboxes}
        if state["pending_publication"] is not None:
            item = state["pending_publication"]
            matched = next(row for row in outboxes if row["publication_id"] == item["publication_id"])
            _same({k: matched[k] for k in ("publication_id", "sha256", "byte_size")}, item)
    _same(observed, expected)
    projection = None if state is None or state["format_version"] != 5 else sync_state.source_readmit_projection(state)
    _same(certificate._state_authority(observed, {"authority_projection": projection}), document["state_authority"])
    return observed


def _completed_row(pins, record, profile, actual_connection):
    public = {key: record[key] for key in files.old.COLUMNS}
    document, manifest, copies, _, maintenance_sha = files._bundle(pins, public, profile)
    requirements, _dependency_records, dependency_unknown = bundles._copied_pending_dependencies(
        profile, document, copies, entries=manifest["files"], check=pins.check)
    if dependency_unknown:
        _fail("COPIED_PENDING_DEPENDENCY_INCOMPLETE")
    reservation_path = profile.database.parent / bundles.OPERATIONS / (record["operation_id"] + ".json")
    reservation, _ = pins.document(reservation_path, bundles.MAX_METADATA_BYTES)
    bundles._reservation(reservation, profile, record["operation_id"], record["root_job_id"])
    protection = _protection_image(pins, record, profile)
    _same(bundles._sql_digest17(protection), reservation["sql_image_sha256"])
    _, version, preserved = base._sql_capture(protection, profile=profile,
        root_job_id=record["root_job_id"], prospective_schema17=True)
    # Every prior receipt actually preserved by this operation remains exact
    # in the current image. Starting at the first still-visible row alone
    # could silently erase the beginning of a mixed epoch chain.
    previous = protection.execute('SELECT ' + ','.join(schema.COLUMNS) + ' FROM main."' + schema.TABLE
        + '" LIMIT ?', (MAX_CHAIN + 1,)).fetchall()
    if len(previous) > MAX_CHAIN:
        _fail("CHAIN_LIMIT")
    for raw in previous:
        pins.check()
        original = schema.validate_record(raw)
        current = restore._record(actual_connection, original["operation_id"])
        if current is None or restore._row_sha(current) != restore._row_sha(original):
            _fail("CHAIN_HISTORY_CHANGED")
    original_prior = {restore._row_sha(schema.validate_record(raw)) for raw in previous
        if raw["library_id"] == record["library_id"]}
    live_prior = actual_connection.execute('SELECT ' + ','.join(schema.COLUMNS) + ' FROM main."' + schema.TABLE
        + '" WHERE library_id=? AND after_library_epoch<=? LIMIT ?',
        (record["library_id"], record["before_library_epoch"], MAX_CHAIN + 1)).fetchall()
    if len(live_prior) > MAX_CHAIN or original_prior != {restore._row_sha(schema.validate_record(raw))
            for raw in live_prior}:
        # Empty original history is an immutable-image observation too. A
        # later current-only backdated row cannot fill that original absence.
        _fail("CHAIN_HISTORY_CHANGED")
    _same(version, document["knowledge_version"])
    original_checkpoint = _copied_checkpoint(record, document, copies, profile, protection, pins)
    preserved.update(checkpoint=original_checkpoint, state_authority=document["state_authority"],
        permanent_witnesses=document["permanent_witnesses"])
    _same({key: document[key] for key in certificate._PRESERVED_FIELDS}, preserved)
    _same(base._sha(preserved), record["before_operational_sha256"])
    path = profile.database.parent / restore.DIRECTORY
    operation = record["operation_id"]
    request, request_identity = pins.document(path / (operation + ".request.json"), restore.MAX_METADATA_BYTES)
    intent, intent_identity = pins.document(path / (operation + ".intent.json"), restore.MAX_METADATA_BYTES)
    proposal, proposal_identity = pins.document(path / (operation + ".proposal.json"), restore.MAX_METADATA_BYTES)
    receipt, _ = pins.document(path / (operation + ".receipt.json"), restore.MAX_METADATA_BYTES)
    source = files._producer(pins, public, profile, document, manifest, maintenance_sha,
        provenance={operation: record["result_sha256"]})
    _same(request["request"], backups._decode(record["request_json"].encode()))
    _same(request["request_sha256"], record["request_sha256"])
    _same(proposal, restore._proposal_record(operation, intent_identity["sha256"], record))
    _same(receipt, {"format_version": 2, "action": "home_knowledge_restore", "operation_id": operation,
        "intent_sha256": intent_identity["sha256"], "proposal_sha256": proposal_identity["sha256"],
        "row_sha256": restore._row_sha(record), "result_sha256": record["result_sha256"],
        "checkpoint_sha256": record["checkpoint_sha256"], "bundle_receipt_sha256": maintenance_sha,
        "committed_at": record["committed_at"]})
    _same(record["before_version_json"], restore._json(document["knowledge_version"]))
    _same(record["frontier_json"], restore._json(document["frontier"]))
    _same(record["before_operational_sha256"], document["preserved_sha256"])
    # Original source declarations can be legitimately retired after commit.
    # The actual immutable row + original request/receipt bind that source;
    # this reader does not reopen it as a fresh production/admission source.
    _same(source["source_binding_sha256"], record["source_binding_sha256"])
    result = backups._decode(record["result_json"].encode())
    _same(source["knowledge_sha256"], result["knowledge_sha256"])
    source_version = source["source_version"]
    if (type(source_version) is not dict or set(source_version) != {"library_id", "library_epoch", "kb_revision"}
            or source_version["library_id"] != record["library_id"]
            or type(source_version["library_epoch"]) is not int or source_version["library_epoch"] < 1
            or type(source_version["kb_revision"]) is not int or source_version["kb_revision"] < 0):
        _fail("SOURCE_BINDING_INVALID")
    pins._copied_pending_dependencies[record["operation_id"]] = (
        files._sha(files._encoded(document)), manifest["file_set_sha256"],
        frozenset(("DATA", requirement.relative_key) for requirement in requirements))
    return document


def _legacy_row(pins, record, profile):
    """Observe released16 intent/image history, without a trusted17 seal."""
    path = legacy._intent_path(profile, record["operation_id"])
    intent, _ = pins.document(path, schema.MAX_REQUEST_BYTES)
    if type(intent) is not dict or set(intent) != legacy._INTENT_KEYS:
        _fail("LEGACY_HISTORY_INVALID")
    archive = _protection_image(pins, record, profile, legacy_row=True)
    _, version, preserved = base._sql_capture(archive, profile=profile, root_job_id=record["root_job_id"])
    preserved["checkpoint"] = checkpoint.validate(record, schema_version=17).checkpoint
    document = {"format_version": 1, "schema_version": "16", "scope": "local_capture_comparison",
        "continuity": "not_established", "knowledge_version": version,
        "preserved_sha256": base._sha(preserved), **preserved}
    _same(intent, {"format_version": 1, "operation_id": record["operation_id"],
        "request": backups._decode(record["request_json"].encode()), "request_sha256": record["request_sha256"],
        "library_id": record["library_id"], "node_id": record["node_id"],
        "profile_sha256": base._sha(list(profile.identity)), "before_capture_sha256": base._sha(document),
        "source_binding_sha256": record["source_binding_sha256"]})
    _same(version, backups._decode(record["before_version_json"].encode()))
    _same(document["preserved_sha256"], record["before_operational_sha256"])
    _same(preserved["frontier"], backups._decode(record["frontier_json"].encode()))


def _with_held_legacy_pending(summary1, references, *, format_version, child):
    """Keep the actual held child across a sole authority-tail append.

    A reserved parent stays scoped unknown. Its real child may be compared to
    actual committed parent records, but no placeholder or copied DTO promotes
    that parent to a complete/paid witness. This never recaptures the child.
    """
    if type(format_version) is not int or format_version not in {1, 2}:
        _fail("PERMANENT_PREFIX_CHANGED")
    if format_version == 1:
        if child is not None:
            _fail("PERMANENT_PREFIX_CHANGED")
        return deepcopy(summary1)
    if child is None:
        _fail("PERMANENT_PREFIX_CHANGED")
    child.verify_source()
    public = child.public_summary()
    pending = pending_contract._component_summary(public["summary"], summary1["owner_profile_sha256"])
    pending_contract._component_projection(public, {"owner_profile_sha256": summary1["owner_profile_sha256"],
        "legacy_pending_state": pending})
    folders = {"origin": "origins", "control": "controls", "publication": "publications",
        "recovery_intent": "requests", "recovery_receipt": "receipts"}
    actual = {(row["kind"], row["record_id"], row["record_sha256"], row["core_sha256"])
        for row in references if row["state"] == "committed"}
    for ref in public["references"]:
        if (folders[ref["record_kind"]], ref["record_id"], ref["record_sha256"], ref["core_sha256"]) not in actual:
            _fail("PERMANENT_PREFIX_CHANGED")
    if summary1["presence"] == "present":
        summary, _ = home_roots._with_legacy_pending(summary1, references, public)
        return summary
    if summary1["presence"] != "unknown":
        _fail("PERMANENT_PREFIX_CHANGED")
    return {**deepcopy(summary1), "format_version": 2, "legacy_pending_state": pending}


def _held_pending_prefix(original, home_capture):
    """A historical Home1 is uncovered here; never reinterpret it as absence."""
    checked = pending_contract.normalize_summary(original)
    if checked["format_version"] == 1:
        return
    current, child = home_capture.summary, getattr(home_capture, "_legacy_pending", None)
    if (getattr(home_capture, "_format_version", None) != 2 or current.get("format_version") != 2 or child is None):
        _fail("PERMANENT_PREFIX_CHANGED")
    before = checked["legacy_pending_state"]
    after = pending_contract._component_summary(child.summary, checked["owner_profile_sha256"])
    _same(current.get("legacy_pending_state"), after)
    if (before["presence"] == "present" and after["presence"] != "present"
            or any(after[name] < before[name] for name in
                ("file_count", "capsule_count", "marker_count", "applied_count", "total_bytes"))):
        _fail("PERMANENT_PREFIX_CHANGED")
    if before["presence"] == after["presence"] and before["file_count"] == after["file_count"]:
        _same(before, after)


def _current_prefix(document, operation_id, pins, profile, cloud, home_capture):
    latest = {"cloud_continuity": cloud.summary, "home_authority": home_capture.summary}
    for kind, original in document["permanent_witnesses"].items():
        current = latest[kind]
        from .home_creation_recovery17 import ReservedAuthorityCapture17
        if (kind == "home_authority" and type(home_capture) is ReservedAuthorityCapture17
                and original["presence"] == "present"):
            # A separate original-completion factory owns the actual reserved
            # capsule. It is not a healthy paid/current witness and preserves
            # the sole reservation's real physical missing-file observation.
            home_capture.verify_original_prefix(original)
            continue
        if original["presence"] == "present" and (current["presence"] != "present"
                or current["identity"] != original["identity"]
                or current["contract_sha256"] != original["contract_sha256"]
                or current["head_ordinal"] < original["head_ordinal"]
                or current["head_ordinal"] == original["head_ordinal"]
                    and current["head_sha256"] != original["head_sha256"]):
            _fail("PERMANENT_PREFIX_CHANGED")
    reservation, _ = pins.document(profile.database.parent / bundles.OPERATIONS / (operation_id + ".json"),
        bundles.MAX_METADATA_BYTES)
    dependency_facts = pins._copied_pending_dependencies.get(operation_id)
    if dependency_facts is None:
        _fail("COPIED_PENDING_DEPENDENCY_REQUIRED")
    _same(dependency_facts[:2], (files._sha(files._encoded(document)), reservation["file_set_sha256"]))
    dependency_keys = dependency_facts[2]
    original_home = document["permanent_witnesses"]["home_authority"]
    _held_pending_prefix(original_home, home_capture)
    selected = {}
    child = getattr(home_capture, "_legacy_pending", None)
    for capture in (cloud, home_capture) + (() if child is None else (child,)):
        for directory, name, held, before, opened, digest in capture._reader.files:
            root, key, role = bundles._relative_role(profile, directory, name)
            selected[(root, key)] = ({"root_kind": root, "relative_key": key, "role": role,
                "sha256": digest.hex(), "byte_size": before[2]}, before)
    bundles._pending_file_gate(document, reservation["files"])
    for entry, signature in zip(reservation["files"], reservation["source_identities"]):
        key = entry["root_kind"], entry["relative_key"]
        if entry["role"] in pending_dependencies.ROLES and key not in dependency_keys:
            _fail("COPIED_PENDING_DEPENDENCY_REQUIRED")
        if key in dependency_keys:
            if (entry["root_kind"] != "DATA" or entry["role"] not in pending_dependencies.ROLES
                    and entry["role"] != "checkpoint_outbox"):
                _fail("COPIED_PENDING_DEPENDENCY_REQUIRED")
            path = profile.database.parent / entry["relative_key"]
            _, identity = pins.read(path, bundles._file_limit(entry), expected=entry["sha256"], capture=False)
            if identity["byte_size"] != entry["byte_size"]:
                _fail("PERMANENT_PREFIX_CHANGED")
            observed = pins.files[-1]
            selected[key] = (dict(entry), observed[5])
        elif entry["role"] in {"head", "checkpoint_state", "checkpoint_outbox"}:
            continue
        current = selected.get(key)
        if current is None or current[0] != entry or list(current[1]) != signature:
            _fail("PERMANENT_PREFIX_CHANGED")


def _chain(con, selected, version):
    identity = (selected["library_id"], selected["node_id"])
    raw_rows = con.execute('SELECT ' + ','.join(schema.COLUMNS) + ' FROM main."' + schema.TABLE
        + '" WHERE library_id=? ORDER BY before_library_epoch,operation_id LIMIT ?',
        (identity[0], MAX_CHAIN + 1)).fetchall()
    if not raw_rows or len(raw_rows) > MAX_CHAIN:
        _fail("CHAIN_LIMIT")
    rows, previous, matched = [], None, 0
    # Include original older bare history, rather than granting it wrapper17
    # trust or starting the observed chain at the latest convenient wrapper.
    for raw in raw_rows:
        current = schema.validate_record(raw)
        parsed = checkpoint.validate(current, schema_version=17)
        if ((current["library_id"], current["node_id"]) != identity
                or current["after_library_epoch"] > version["library_epoch"]
                or current["after_kb_revision"] > version["kb_revision"]):
            _fail("CHAIN_INVALID")
        if previous is not None and (current["before_library_epoch"] != previous["after_library_epoch"]
                or current["before_kb_revision"] < previous["after_kb_revision"]):
            _fail("CHAIN_INCOMPLETE")
        rows.append((current, parsed))
        matched += int(current["operation_id"] == selected["operation_id"])
        previous = current
    if matched != 1 or previous["after_library_epoch"] != version["library_epoch"]:
        _fail("CHAIN_INCOMPLETE")
    return rows


def _state_effect17(captured, guard):
    """Closed typed effect; no DTO or naked changed signature grants adoption."""
    from . import sync_source_lifecycle17 as effects, sync_source_readmit_local2 as local
    from .sync_source_legacy_intake17 import LegacyBaselineAdmissionCapture17
    from .sync_source_legacy_origin_intake17 import LegacyOriginIntakeCapture17
    from .sync_source_legacy_pending_intake17 import LegacyPendingIntakeCapture17
    if (type(guard) is not effects.StateCASGuard17 or not guard._active or not guard._written
            or effects._CURRENT.get() is not guard or guard.store is not captured._store
            or guard.profile.identity != captured._profile.identity
            or type(guard._control) not in {local.AdmissionReceiptCapture2, local.ControlReceiptCapture2,
                LegacyBaselineAdmissionCapture17, LegacyOriginIntakeCapture17,
                LegacyPendingIntakeCapture17}
            or guard._control.preservation_capture is not captured):
        _fail("STATE_EFFECT_REQUIRED")
    guard._context()
    guard._control._context()
    captured._owner.verify()
    before, after = json.loads(guard._before_json), json.loads(guard._after_json)
    if captured._state["value"] not in (before, after):
        _fail("STATE_EFFECT_REQUIRED")
    _same((before["source_execution"], after["source_execution"]),
          (guard._control.core, guard._control.core))
    if type(guard._control) is LegacyPendingIntakeCapture17:
        _same(guard._control.pending_state_effect(guard), (before, after))
        return before, after
    if type(guard._control) is LegacyOriginIntakeCapture17:
        _same(guard._control.origin_state_effect(guard), (before, after))
        return before, after
    control, result = guard._control.request, guard._control.result
    if type(guard._control) is LegacyBaselineAdmissionCapture17:
        _same(guard._control.baseline_state_effect(guard), (before, after))
    pending = before["source_recovery_pending"]
    if type(guard._control) is local.ControlReceiptCapture2:
        if control["kind"] != "source_dispose" or control["format_version"] != 2:
            _fail("STATE_EFFECT_REQUIRED")
        # This sealed capture owns the real intent, HTTP result and original
        # control. Consume either its pending reconciliation or its positive
        # receipt cache; neither path can change a claim or paid identity.
        expected = deepcopy(before)
        if pending is not None:
            intent = guard._control._intent
            _same(pending, {"format_version": 1, "kind": "source_dispose",
                "request_id": control["request_id"], "request_sha256": intent["request_sha256"],
                "core_sha256": intent["core_sha256"], "intent_sha256": authority.digest(intent)})
            expected["source_recovery_pending"] = None
        elif result["disposition"] != "applied":
            _fail("STATE_EFFECT_REQUIRED")
        original = guard._control.original_control_request
        slot = {"budget_defer": "defer", "budget_resume": "resume", "source_fail": "failure"}[original["kind"]]
        actual = guard._control._closure.records.get(("controls", original["request_id"]))
        if actual is None or before["source_authority"] is None:
            _fail("STATE_EFFECT_REQUIRED")
        _same((actual["request"], actual["core_sha256"], before["source_authority"][slot]),
              (original, authority.digest(guard._control.core), authority.record_ref(actual)))
        target = expected["source_control_pending"] if slot == "failure" else expected["budget_pending"]
        if target is None:
            _fail("STATE_EFFECT_REQUIRED")
        request_key, receipt_key = (("request", "receipt") if slot == "failure" else
                                    (slot + "_request", slot + "_receipt"))
        _same(target[request_key], original)
        if result["disposition"] == "applied":
            positive = guard._control.original_control_receipt
            if positive is None or target[receipt_key] is not None and not sync_state._same_receipt(target[receipt_key], positive):
                _fail("STATE_EFFECT_REQUIRED")
            target[receipt_key] = positive
        elif result["disposition"] == "not_applied":
            if target[receipt_key] is not None or guard._control.original_control_receipt is not None:
                _fail("STATE_EFFECT_REQUIRED")
            if slot == "failure":
                expected["source_control_pending"] = None
            elif slot == "defer":
                if target["resume_request"] is not None:
                    _fail("STATE_EFFECT_REQUIRED")
                expected["budget_pending"] = None
            else:
                # Only the negative resume retires. Keep the original positive
                # parent waiting and do not invent a refund or paid permit.
                target.update(resume_request=None, resume_receipt=None)
            expected["source_authority"][slot] = None
        else:
            _fail("STATE_EFFECT_REQUIRED")
        _same(after, expected)
        return before, after
    _same(control["kind"], "source_readmit")
    if pending is None or pending["request_id"] != control["request_id"]:
        _fail("STATE_EFFECT_REQUIRED")
    original = [entry for entry in before["claims"] if entry["item_id"] == result["item_id"]]
    if len(original) != 1 or original[0]["claim"] != control["expected_claim"]:
        _fail("STATE_EFFECT_REQUIRED")
    expected = deepcopy(before)
    expected.update(source_admission=guard._control.admission_ref(), source_recovery_pending=None)
    selected = {**deepcopy(original[0]), "attempts": result["attempts"],
        "claim": deepcopy(result["claim"]), "lease_until": result["session"]["lease_until"]}
    expected["claims"] = [selected if entry["item_id"] == result["item_id"] else deepcopy(entry)
        for entry in before["claims"]]
    if control["defer_receipt"] is not None:
        if before["budget_pending"] is not None or control["resume_receipt"] is None:
            _fail("STATE_EFFECT_REQUIRED")
        _same((before["source_authority"]["defer"]["record_id"], before["source_authority"]["resume"]["record_id"]),
              (control["defer_receipt"]["request_id"], control["resume_receipt"]["request_id"]))
        expected["source_authority"].update(defer=None, resume=None)
    _same(after, expected)
    return before, after


def _refresh_home_creation(captured, producer, observed, folder, *, _creation_read_scope=None):
    """Renew the private reader across only its sealed producer's append.

    Reread the physical closure and retain every original immutable identity.
    The original capture's final full verification remains registered. A live
    reserved successor remains scoped unknown until its own commit appears.
    """
    original = captured._home._reader
    format_version = captured._home._format_version
    child = captured._home._legacy_pending
    if (format_version == 1 and child is not None or format_version == 2 and child is None):
        _fail("PERMANENT_PREFIX_CHANGED")
    original.check()
    record = producer.record
    identifier = record["record_id"]
    reader = home_roots._Reader(captured._profile, original.stack,
        original.limits, original.callback)
    if _creation_read_scope is not None:
        from . import home_creation_recovery17 as original_creation
        original_creation._register_refresh_reader17(_creation_read_scope, captured, original, reader)
    reader.started, reader.total = original.started, original.total
    reader.payload_started, reader.payload_elapsed = original.payload_started, original.payload_elapsed
    reader.legacy_pending_elapsed = original.legacy_pending_elapsed
    payload = home_roots._read_payload(reader)
    if reader.anchor_errors:
        _fail("PERMANENT_PREFIX_CHANGED")
    _same(payload["contract"], observed["contract"])
    _same(payload["db_head"], observed["head"])
    expected_records = {kind: {key: item["record"] for key, item in values.items()}
        for kind, values in observed["entries"].items()}
    pending_file = None
    if identifier not in payload["records"][folder] and identifier in expected_records[folder]:
        candidate = observed["entries"][folder][identifier]
        _same((candidate["state"], candidate["record"]), ("reserved", record))
        expected_records[folder].pop(identifier)
        # Observe actual absence. The journal is the reserved fact; no file or
        # committed DTO is manufactured to satisfy the ordinary pure reader.
        ledger_path = captured._profile.database.parent / authority.DIRECTORY / authority.VERSION_DIRECTORY
        directory = next(item for item in reader.directories if item.path == ledger_path / folder)
        _same(reader.read(directory, identifier + ".json", optional=True), None)
        pending_file = (directory.path, identifier + ".json")
    _same(payload["records"], expected_records)
    home_roots._validate_publication_payloads(reader, observed["entries"])
    old = original.payload
    for name in ("intent", "seal", "contract"):
        _same(payload[name], old[name])
    _same(payload["journal"][:len(old["journal"])], old["journal"])
    if len(payload["journal"]) < len(old["journal"]):
        _fail("PERMANENT_PREFIX_CHANGED")
    for kind, values in old["records"].items():
        for key, value in values.items():
            _same(payload["records"][kind].get(key), value)
        allowed = {identifier} if kind == folder else set()
        if set(payload["records"][kind]) - set(values) - allowed:
            _fail("PERMANENT_PREFIX_CHANGED")
    ledger = captured._profile.database.parent / authority.DIRECTORY / authority.VERSION_DIRECTORY
    allowed_files = {(ledger / folder, identifier + ".json")}
    for event in payload["journal"][len(old["journal"]):]:
        _same((event["kind"], event["key"], event["record"]), (folder, identifier, record))
        allowed_files.add((ledger / "journal", f"{event['ordinal']:08d}.json"))
    if pending_file is None:
        projection = authority.pure_projection(payload,
            binding=captured._state["value"]["binding"],
            owner_profile_sha256=authority.profile_sha256(captured._profile))
    else:
        # This branch exists only inside the original sealed successor. Every
        # decoded event is a real canonical journal file, and the producer has
        # already proved the exact base -> this sole reserved successor.
        latest = {(event["kind"], event["key"]): event for event in payload["journal"]}
        references = [authority.public_record_projection(kind, latest[(kind, key)]["record"],
            state=latest[(kind, key)]["state"], activation=payload["contract"])
            for kind in authority.KINDS for key in sorted(observed["entries"][kind])]
        projection = {"records": references,
            "owner_profile_sha256": payload["contract"]["owner_profile_sha256"],
            "facility_id": payload["contract"]["facility_id"],
            "binding": captured._home.summary["binding"], "head_sha256": observed["head"]["sha256"]}
    original_files = {(item[0].path, item[1]): item for item in original.files}
    current_files = {(item[0].path, item[1]): item for item in reader.files}
    heads = {(captured._profile.database.parent, authority.DB_HEAD_NAME),
             (captured._profile.checkpoints, authority.CHECKPOINT_HEAD_NAME)}
    for key, previous in original_files.items():
        current = current_files.get(key)
        if current is None:
            _fail("PERMANENT_PREFIX_CHANGED")
        if key not in heads:
            _same(current[3:], previous[3:])
            previous[0].verify()
            if previous[2] is not None:
                _same(physical.file_signature(os.fstat(previous[2].fileno())), previous[4])
    if set(current_files) - set(original_files) - allowed_files:
        _fail("PERMANENT_PREFIX_CHANGED")
    selected_file = (ledger / folder, identifier + ".json")
    _same({(directory.path, name) for directory, name in reader.absences} - {selected_file},
          {(directory.path, name) for directory, name in original.absences} - {selected_file})
    old_names = {directory.path: set(names) for directory, names in original.names}
    new_names = {directory.path: set(names) for directory, names in reader.names}
    _same(set(new_names), set(old_names))
    for directory, names in old_names.items():
        additions = {name for path, name in allowed_files if path == directory}
        if not names <= new_names[directory] or new_names[directory] - names - additions:
            _fail("PERMANENT_PREFIX_CHANGED")
    reader.verify()
    references = tuple(projection["records"])
    reserved = tuple(row for row in references if row["state"] != "committed")
    if any((row["kind"], row["record_id"]) != (folder, identifier) for row in reserved):
        _fail("COMMITTED_RECORD_REQUIRED")
    summary = {"format_version": 1, "kind": "home_authority",
        "presence": "unknown" if reserved else "present", "absence_sha256": None,
        "owner_profile_sha256": projection["owner_profile_sha256"],
        "identity": {"facility_id": projection["facility_id"]}, "binding": projection["binding"],
        "contract_sha256": home_roots._digest(payload["contract"]),
        "head_ordinal": len(payload["journal"]), "head_sha256": projection["head_sha256"],
        "journal_sha256": home_roots._digest(payload["journal"]),
        "record_set_sha256": home_roots._digest(list(references)),
        "reference_set_sha256": home_roots._digest(list(references)),
        "record_count": len(references), "journal_count": len(payload["journal"])}
    with reader.legacy_pending_window():
        summary = _with_held_legacy_pending(summary, references, format_version=format_version, child=child)
    captured._home._reader = reader
    captured._home._summary, captured._home._references = summary, references
    captured._home._unknown = ({"scope": "home_authority", "code": "RESERVED"},) if reserved else ()


class CompletedRestoreCapture17:
    """Live completed-provenance seal; its public reference grants no effect."""
    __slots__ = ("_seal", "_profile", "_store", "_owner", "_guard", "_thread", "_active", "_pins",
        "_state", "_identity", "_checkpoint", "_cloud", "_home", "_records", "_row_hashes", "_version",
        "_sql_projection", "_selected", "_documents", "_schema_sha", "_creation_guard",
        "_home_format_anchor", "_home_pending_anchor")

    def __init__(self, *, seal, profile, store, owner, guard, pins, state, identity, checkpoint_capture,
            cloud, home_capture, records, version, projection, selected, documents, schema_sha):
        if seal is not _SEAL:
            _fail("FACTORY_REQUIRED")
        self._seal, self._profile, self._store = seal, profile, store
        self._owner, self._guard = owner, guard
        self._thread, self._active = threading.get_ident(), True
        self._pins, self._state, self._identity, self._checkpoint = pins, deepcopy(state), identity, checkpoint_capture
        self._cloud, self._home = cloud, home_capture
        self._home_format_anchor, self._home_pending_anchor = home_capture._format_version, home_capture._legacy_pending
        self._records = deepcopy(records)
        self._row_hashes = tuple(restore._row_sha(row) for row, _ in records)
        self._version, self._sql_projection = deepcopy(version), deepcopy(projection)
        self._selected, self._documents, self._schema_sha = deepcopy(selected), deepcopy(documents), schema_sha
        self._creation_guard = None

    def _check(self):
        if (type(self) is not CompletedRestoreCapture17 or self._seal is not _SEAL or not self._active
                or self._thread != threading.get_ident() or not any(value is self for value in _CURRENT.get())):
            _fail("FACTORY_REQUIRED")
        self._owner.verify()
        if lifecycle.require_guard(self._profile) is not self._guard:
            _fail("OWNER_REQUIRED")
        if (self._home._format_version != self._home_format_anchor
                or self._home._legacy_pending is not self._home_pending_anchor):
            _fail("PERMANENT_PREFIX_CHANGED")
        self._pins.check()

    def _verify_short_files(self):
        self._check()
        self._pins.verify_short()
        if witness._state_signature(self._store) != self._state["signature"]:
            # One actual held recovery State CAS may replace the State path.
            # This is a fixed comparison, never a full recapture or mutation
            # of our original restoration facts inside the writer.
            from . import sync_source_lifecycle17 as effects
            guard = effects._CURRENT.get()
            if (not budget_writer.in_write_window() or type(guard) is not effects.StateCASGuard17
                    or guard._writer is None or not guard._written):
                _fail("STATE_CHANGED")
            _state_effect17(self, guard)
            connection, writer = guard._writer
            if budget_writer.require_writer(connection, profile=self._profile) is not writer:
                _fail("WRITER_PERMIT_REQUIRED")
            _same(self._store.read(), json.loads(guard._after_json))
        self._identity.verify_for_commit(None)
        self._checkpoint.verify_for_commit()
        self._cloud.verify_for_commit()
        self._home.verify_for_commit()

    def _advance_state(self, guard):
        """Adopt only this actual held child's sole completed recovery State CAS."""
        if budget_writer.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._check()
        _state_effect17(self, guard)
        observed = guard._advance_for_capture(self)
        if self._state["value"] == observed["after"]:
            witness._verify_state_full(self._store, self._state)
            return
        _same(self._state["value"], observed["before"])
        after = witness._state(self._store)
        _same(after["value"], observed["after"])
        self._checkpoint.verify_full()
        self._pins.verify()
        self._identity.verify_for_commit(None)
        self._cloud.verify_source()
        self._home.verify_source()
        witness._resolve_state(after["value"], self._home)
        # Pending outbox/recovery roots are identical in the derived State.
        # Its already held checkpoint remains the same actual byte closure.
        self._state = after
        self.verify_source()

    def _verify_sql(self, connection):
        """Private fixed comparison; not a writer permit or public consumer."""
        _same(db.knowledge_version(connection), self._version)
        if connection.execute("SELECT value FROM main.meta WHERE key='migration_version'").fetchone()[0] != "17":
            _fail("SCHEMA_REQUIRED")
        _same(inputs.read_continuity_projection(connection), self._sql_projection)
        self._identity.verify_for_commit(connection)
        current = restore._record(connection, self._selected["operation_id"])
        if current is None:
            _fail("CHAIN_INCOMPLETE")
        chain = _chain(connection, current, self._version)
        hashes = tuple(restore._row_sha(row) for row, _ in chain)
        if hashes != self._row_hashes:
            _fail("ROW_CHANGED")
        # This compares immutable restore rows and fixed continuity facts.
        # Budget operations/attempts are intentionally not a whole SQL CAS:
        # reserve and dispatch make valid writes to those ledgers.

    def verify_source(self):
        """Full original SHA/namespace validation, outside any owned writer."""
        if budget_writer.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._check()
        window = witness._WINDOW.get()
        if window is not None and (window._writable is not False or window.generation != 0
                or window.profile.identity != self._profile.identity or window.store is not self._store
                or window.connection.execute("PRAGMA query_only").fetchone()[0] != 1):
            _fail("READ_WINDOW_REQUIRED")
        self._verify_short_files()
        self._pins.verify()
        witness._verify_state_full(self._store, self._state)
        self._checkpoint.verify_full()
        self._cloud.verify_source()
        self._home.verify_source()
        witness._resolve_state(self._state["value"], self._home)
        if window is not None:
            self._verify_sql(window.connection)
            _same(restore._actual_schema(window.connection), self._schema_sha)
        elif self._home.unknown:
            # Ordinary witnesses must continue rejecting an incomplete Home
            # facility. Only our original sealed producer may compare its
            # private held closure during its sole reserved successor.
            self._verify_creation_readonly_sql()
        else:
            # Independent original readonly factory, never a caller SQL DTO.
            with witness.sql_window_locked(profile=self._profile, store=self._store, writable=False) as con:
                con.execute("PRAGMA query_only=ON")
                with witness.capture_locked(con, profile=self._profile, store=self._store) as actual:
                    witness.require_read_capture(actual, connection=con, profile=self._profile)
                    self._verify_sql(con)
                    _same(restore._actual_schema(con), self._schema_sha)

    def _verify_creation_readonly_sql(self):
        if budget_writer.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._check()
        from .sync_source_lifecycle17 import ControlCreationGuard17, PublicationCreationGuard17
        from .sync_source_readmit_local2 import ReadmitIntentProducer2
        from .sync_source_publication_local2 import PublicationCreationGuard2
        producer = self._creation_guard
        if type(producer) not in {ControlCreationGuard17, PublicationCreationGuard17, PublicationCreationGuard2, ReadmitIntentProducer2}:
            _fail("CREATION_PRODUCER_REQUIRED")
        observed = producer._advance_for_capture(self)
        folder = ("controls" if type(producer) is ControlCreationGuard17 else
            "publications" if type(producer) in {PublicationCreationGuard17, PublicationCreationGuard2} else "requests")
        record = producer.record
        _same(observed["incomplete"], [(folder, record["record_id"])])
        _same(observed["entries"][folder][record["record_id"]], {"state": "reserved", "record": record})
        _same(observed["head"], self._home._reader.payload["db_head"])
        # This independently opened factory grants no writer permit. Original
        # State/SH, identity, bundle and live file pins remain held above; the
        # private SQL comparison neither recaptures unknown facts nor exposes
        # a caller connection as a replacement witness.
        with budget_writer.connection(self._profile, write=False) as con:
            con.execute("PRAGMA query_only=ON")
            if not con.in_transaction or con.execute("PRAGMA query_only").fetchone()[0] != 1:
                _fail("READ_WINDOW_REQUIRED")
            self._verify_sql(con)
            _same(restore._actual_schema(con), self._schema_sha)
            self._verify_short_files()
        self._home.verify_source()
        _same(producer._advance_for_capture(self)["head"], observed["head"])

    def _verify_budget_writer(self, connection, writer):
        """Fixed short comparison; never a schema/source/producer acquisition."""
        try:
            budget_writer.check(writer, connection=connection, profile=self._profile)
            if writer.schema_version != 17:
                _fail("SCHEMA_REQUIRED")
            self._verify_short_files()
            self._verify_sql(connection)
            budget_writer.check(writer, connection=connection, profile=self._profile)
        except budget_writer.BudgetWriterWitnessError:
            _fail("WRITER_PERMIT_REQUIRED")

    def verify_for_commit(self, connection, *, writer_permit=None):
        """Consume only the actual budget factory's original closed writer.

        The factory retains a fixed terminal comparison while this capture,
        State ownership and original SH pins remain live. The final check does
        not replay this public method or register another application callback.
        A caller connection/DTO/writable witness cannot supply the token.
        """
        self._check()
        try:
            writer = budget_writer.require_writer(connection, profile=self._profile)
            if writer_permit is not writer:
                _fail("WRITER_PERMIT_REQUIRED")
            self._verify_budget_writer(connection, writer)
            budget_writer.register_commit_check(writer,
                lambda: self._verify_budget_writer(connection, writer),
                connection=connection, profile=self._profile)
        except budget_writer.BudgetWriterWitnessError:
            _fail("WRITER_PERMIT_REQUIRED")

    def _advance_creation(self, producer):
        """Renew this original capture for only its exact active append owner."""
        if budget_writer.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._check()
        from .sync_source_lifecycle17 import ControlCreationGuard17, PublicationCreationGuard17
        from .sync_source_readmit_local2 import ReadmitIntentProducer2
        from .sync_source_publication_local2 import PublicationCreationGuard2
        if (type(producer) not in {ControlCreationGuard17, PublicationCreationGuard17, PublicationCreationGuard2, ReadmitIntentProducer2}
                or producer.profile.identity != self._profile.identity or producer.store is not self._store):
            _fail("CREATION_PRODUCER_REQUIRED")
        if type(producer) is ControlCreationGuard17 and getattr(producer, "_restore_capture", None) is not self:
            _fail("CREATION_PRODUCER_REQUIRED")
        observed = producer._advance_for_capture(self)
        if self._creation_guard is None:
            base_head = (json.loads(producer._base_head_json)
                if type(producer) in {ControlCreationGuard17, PublicationCreationGuard17, PublicationCreationGuard2} else producer._base_head)
            _same(self._home._reader.payload["db_head"], base_head)
        elif self._creation_guard is not producer:
            _fail("CREATION_PRODUCER_REQUIRED")
        folder = ("controls" if type(producer) is ControlCreationGuard17 else
            "publications" if type(producer) in {PublicationCreationGuard17, PublicationCreationGuard2} else "requests")
        if (self._home._reader.payload["db_head"] != observed["head"]
                or self._home.summary["presence"] == "unknown"):
            _refresh_home_creation(self, producer, observed, folder)
        else:
            self._home.verify_source()
        self._creation_guard = producer
        self.verify_source()

    def provenance_ref(self):
        self._check()
        return {"format_version": 1, "producer": "home_knowledge_restore",
            "operation_id": self._selected["operation_id"], "receipt_sha256": self._selected["result_sha256"]}

    def chain_observation(self):
        """Bounded metadata only; bare history is never a trusted17 producer."""
        self._check()
        return tuple({"operation_id": row["operation_id"], "before_epoch": row["before_library_epoch"],
            "after_epoch": row["after_library_epoch"], "trusted17_producer": parsed.wrapper is not None}
            for row, parsed in self._records)

    def preservation_observation(self, *, from_epoch):
        """Return one actual complete preserved epoch suffix, never a DTO seal.

        The latest selected producer supplies the actual certificate/frontier.
        Its original verified bundle contains the older permanent prefix. Each
        intervening restore must be a genuine completed17 producer; older bare
        history before the selected origin epoch remains historical evidence.
        """
        self._check()
        if type(from_epoch) is not int or from_epoch < 1:
            _fail("CHAIN_INVALID")
        current, chain, selected_document, selected_certificate, core_sha = from_epoch, [], None, None, None
        for (row, parsed), document in zip(self._records, self._documents):
            if row["after_library_epoch"] <= from_epoch:
                continue
            if (row["before_library_epoch"] != current or parsed.wrapper is None
                    or document is None or row["after_library_epoch"] <= current):
                _fail("CHAIN_INCOMPLETE")
            document_core = document["state_authority"]["source_core_sha256"]
            if core_sha is None:
                core_sha = document_core
            elif core_sha != document_core:
                _fail("CHAIN_INVALID")
            chain.append({"operation_id": row["operation_id"], "row_sha256": restore._row_sha(row),
                "certificate_sha256": parsed.certificate_sha256,
                "frontier_sha256": base._sha(document["frontier"]),
                "from_epoch": row["before_library_epoch"], "to_epoch": row["after_library_epoch"]})
            current = row["after_library_epoch"]
            if row["operation_id"] == self._selected["operation_id"]:
                selected_document = document
                selected_certificate = parsed.certificate_sha256
        if (not chain or current != self._version["library_epoch"] or selected_document is None
                or self._selected["after_library_epoch"] != current):
            _fail("CHAIN_INCOMPLETE")
        return {"preservation": {"format_version": 1,
            "frontier_sha256": base._sha(selected_document["frontier"]),
            "certificate_sha256": selected_certificate,
            "from_epoch": from_epoch, "to_epoch": current},
            "source_core_sha256": core_sha, "chain": tuple(chain)}


@contextmanager
def capture_completed_locked(*, profile, store, operation_id, expected_result_sha256=None):
    """Capture actual completed provenance under caller's original State+SH.

    Only profile, actual StateStore and original UUID select the facts. No
    caller connection, facts, certificate, file path or SQL row is accepted.
    The initial genuine readonly witness closes before the sealed capture is
    yielded; its own independent pins survive across later SQL windows.
    """
    if budget_writer.in_write_window():
        _fail("READ_WINDOW_REQUIRED")
    profile = lifecycle._profile(profile)
    operation_id = legacy._uuid(operation_id)
    if expected_result_sha256 is not None:
        legacy._digest(expected_result_sha256)
    if (type(store) is not sync_state.StateStore or store.profile is None
            or store.profile.identity != profile.identity or witness._WINDOW.get() is not None
            or _CURRENT.get()):
        _fail("FACTORY_REQUIRED")
    owner = authority._Ownership(store)
    owner.verify()
    guard = lifecycle.require_guard(profile)
    started = time.monotonic()
    def check():
        owner.verify()
        if lifecycle.require_guard(profile) is not guard:
            _fail("OWNER_REQUIRED")
        if store.check_cancelled is not None:
            store.check_cancelled()
        if time.monotonic() - started > 120:
            _fail("CAPTURE_LIMIT")
    with ExitStack() as stack:
        pins = _RuntimePins(stack, profile, check)
        with witness.sql_window_locked(profile=profile, store=store, writable=False) as con:
            con.execute("PRAGMA query_only=ON")
            with witness.capture_locked(con, profile=profile, store=store) as actual:
                witness.require_read_capture(actual, connection=con, profile=profile)
                schema_sha = restore._actual_schema(con)
                selected = restore._record(con, operation_id)
                if selected is None:
                    _fail("OPERATION_MISSING")
                if checkpoint.validate(selected, schema_version=17).wrapper is None:
                    _fail("TRUSTED17_PRODUCER_REQUIRED")
                if expected_result_sha256 is not None and selected["result_sha256"] != expected_result_sha256:
                    _fail("RESULT_MISMATCH")
                _same(actual.identity(), {"library_id": selected["library_id"], "node_id": selected["node_id"]})
                version, state = db.knowledge_version(con), witness._state(store)
                if state["value"] is not None and state["value"]["local_epoch"] != version["library_epoch"]:
                    _fail("STATE_EPOCH_GAP")
                projection = inputs.read_continuity_projection(con)
                cloud = stack.enter_context(cloud_roots.capture_locked(profile, sql_projection=projection,
                    limits=cloud_roots.CaptureLimits(deadline_seconds=120), check_cancelled=check))
                home_capture = stack.enter_context(home_roots.capture_absence_locked(profile,
                    format_version=2, limits=home_roots.CaptureLimits(deadline_seconds=120), check_cancelled=check)
                    if state["value"] is None else home_roots.capture_locked(profile, binding=state["value"]["binding"],
                        format_version=2, limits=home_roots.CaptureLimits(deadline_seconds=120), check_cancelled=check))
                if any(value.unknown or value.summary["presence"] not in {"present", "absent"}
                        for value in (cloud, home_capture)):
                    _fail("CURRENT_FACILITY_INCOMPLETE")
                _same({"cloud_continuity": cloud.summary, "home_authority": home_capture.summary}, actual.capsules())
                witness._resolve_state(state["value"], home_capture)
                identity = witness._IdentityCapture(store, con, stack)
                checkpoint_capture = witness._CheckpointCache(store, state, stack)
                chain = _chain(con, selected, version)
                documents = []
                for row, parsed in chain:
                    check()
                    if parsed.wrapper is None:
                        _legacy_row(pins, row, profile)
                        documents.append(None)
                    else:
                        original_document = _completed_row(pins, row, profile, con)
                        _current_prefix(original_document, row["operation_id"], pins, profile, cloud, home_capture)
                        documents.append(original_document)
                actual.verify_source()
                pins.verify()
                captured = CompletedRestoreCapture17(seal=_SEAL, profile=profile, store=store, owner=owner,
                    guard=guard, pins=pins, state=state, identity=identity, checkpoint_capture=checkpoint_capture,
                    cloud=cloud, home_capture=home_capture, records=chain, version=version, projection=projection,
                    selected=selected, documents=documents, schema_sha=schema_sha)
        token = _CURRENT.set((*_CURRENT.get(), captured))
        try:
            captured.verify_source()
            yield captured
            captured.verify_source()
        finally:
            captured._active = False
            _CURRENT.reset(token)


class HistoricalRestorePrefixCapture17:
    """Original verified restore prefix facts; never a current paid permit."""
    __slots__ = ("_seal", "_active", "_thread", "_current", "_records", "_documents", "_selected", "_context_token")

    def __init__(self, *, current, records, documents, selected, seal):
        if seal is not _SEAL or type(current) is not CompletedRestoreCapture17:
            _fail("FACTORY_REQUIRED")
        self._seal, self._active, self._thread = seal, True, threading.get_ident()
        self._current, self._records, self._documents = current, tuple(records), tuple(documents)
        self._selected = deepcopy(selected)
        self._context_token = None

    def _check(self):
        if (type(self) is not HistoricalRestorePrefixCapture17 or self._seal is not _SEAL or not self._active
                or self._thread != threading.get_ident() or not any(value is self for value in _HISTORICAL.get())
                or _HISTORICAL_CONTEXT.get() is not self):
            _fail("FACTORY_REQUIRED")
        try:
            _HISTORICAL_CONTEXT.reset(self._context_token)
        except (ValueError, RuntimeError):
            _fail("FACTORY_REQUIRED")
        self._context_token = _HISTORICAL_CONTEXT.set(self)
        self._current._check()
        # The current factory already pinned and fully validated every original
        # wrapper/protection/image. This old selection must be its exact prefix;
        # newer rows cannot replace or complete a missing historical producer.
        _same(tuple(self._current._records[:len(self._records)]), self._records)
        _same(tuple(self._current._documents[:len(self._documents)]), self._documents)
        if not self._records or self._records[-1][0] != self._selected:
            _fail("CHAIN_INCOMPLETE")

    def provenance_ref(self):
        self._check()
        return {"format_version": 1, "producer": "home_knowledge_restore",
            "operation_id": self._selected["operation_id"], "receipt_sha256": self._selected["result_sha256"]}

    def preservation_observation(self, *, from_epoch):
        self._check()
        if type(from_epoch) is not int or from_epoch < 1:
            _fail("CHAIN_INVALID")
        current, chain, core_sha, selected_document, selected_certificate = from_epoch, [], None, None, None
        for (row, parsed), document in zip(self._records, self._documents):
            if row["after_library_epoch"] <= from_epoch:
                continue
            if (row["before_library_epoch"] != current or parsed.wrapper is None or document is None
                    or row["after_library_epoch"] <= current):
                _fail("CHAIN_INCOMPLETE")
            actual_core = document["state_authority"]["source_core_sha256"]
            if core_sha is None:
                core_sha = actual_core
            elif actual_core != core_sha:
                _fail("CHAIN_INVALID")
            chain.append({"operation_id": row["operation_id"], "row_sha256": restore._row_sha(row),
                "certificate_sha256": parsed.certificate_sha256, "frontier_sha256": base._sha(document["frontier"]),
                "from_epoch": row["before_library_epoch"], "to_epoch": row["after_library_epoch"]})
            current = row["after_library_epoch"]
            if row["operation_id"] == self._selected["operation_id"]:
                selected_document, selected_certificate = document, parsed.certificate_sha256
        if (not chain or current != self._selected["after_library_epoch"] or selected_document is None):
            _fail("CHAIN_INCOMPLETE")
        return {"preservation": {"format_version": 1,
            "frontier_sha256": base._sha(selected_document["frontier"]),
            "certificate_sha256": selected_certificate, "from_epoch": from_epoch, "to_epoch": current},
            "source_core_sha256": core_sha, "chain": tuple(chain)}

    def verify_source(self):
        if budget_writer.in_write_window():
            _fail("READ_WINDOW_REQUIRED")
        self._check()
        self._current.verify_source()

    def _fixed(self, connection, writer):
        self._check()
        if budget_writer.require_writer(connection, profile=self._current._profile) is not writer:
            _fail("WRITER_PERMIT_REQUIRED")
        self._current._verify_budget_writer(connection, writer)

    def verify_for_commit(self, connection, *, writer_permit=None):
        self._fixed(connection, writer_permit)
        self._current.verify_for_commit(connection, writer_permit=writer_permit)
        budget_writer.register_commit_check(writer_permit, lambda: self._fixed(connection, writer_permit),
            connection=connection, profile=self._current._profile)


@contextmanager
def capture_historical_prefix_locked(*, profile, store, operation_id, expected_result_sha256, to_epoch):
    """Own today's complete actual chain, then select only the original prefix."""
    if budget_writer.in_write_window() or _HISTORICAL.get():
        _fail("FACTORY_REQUIRED")
    profile = lifecycle._profile(profile)
    operation_id = legacy._uuid(operation_id)
    legacy._digest(expected_result_sha256)
    if (type(to_epoch) is not int or to_epoch < 1 or type(store) is not sync_state.StateStore
            or store.profile is None or store.profile.identity != profile.identity):
        _fail("FACTORY_REQUIRED")
    authority._Ownership(store).verify()
    with budget_writer.connection(profile) as con:
        version = db.knowledge_version(con)
        rows = con.execute("SELECT operation_id,result_sha256 FROM " + schema.TABLE
            + " WHERE library_id=? AND after_library_epoch=?",
            (version["library_id"], version["library_epoch"])).fetchall()
    if len(rows) != 1:
        _fail("CHAIN_INCOMPLETE")
    with capture_completed_locked(profile=profile, store=store, operation_id=rows[0][0],
            expected_result_sha256=rows[0][1]) as current:
        found = [(index, row) for index, (row, parsed) in enumerate(current._records)
            if row["operation_id"] == operation_id]
        if len(found) != 1:
            _fail("CHAIN_INCOMPLETE")
        index, selected = found[0]
        _same((selected["result_sha256"], selected["after_library_epoch"]),
              (expected_result_sha256, to_epoch))
        if current._records[index][1].wrapper is None or current._documents[index] is None:
            _fail("CHAIN_INCOMPLETE")
        captured = HistoricalRestorePrefixCapture17(current=current, records=current._records[:index + 1],
            documents=current._documents[:index + 1], selected=selected, seal=_SEAL)
        token = _HISTORICAL.set((*_HISTORICAL.get(), captured))
        captured._context_token = _HISTORICAL_CONTEXT.set(captured)
        try:
            captured.verify_source()
            yield captured
            captured.verify_source()
        finally:
            captured._active = False
            _HISTORICAL_CONTEXT.reset(captured._context_token)
            _HISTORICAL.reset(token)
