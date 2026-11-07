"""Explicit Home knowledge restore, preserving original operational authority.

Only Home-produced checkpoints are accepted. SQL commits its permanent receipt
before the separate StateStore epoch update; repeating the original UUID only
reconciles that gap. Nothing in this module dispatches a provider, changes a job,
creates source admission, or replaces the original source execution binding.
"""
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
import hashlib
from itertools import chain
import json
import os
import sqlite3
import tempfile
import time
import uuid

from . import config, db, home_recovery_schema as schema, home_recovery_source as sources
from . import migrations, model_scopes, review, source_contract, source_preservation as preservation
from . import storage_backups as backups, storage_lifecycle as lifecycle
from . import sync_checkpoints, sync_media, sync_queue, sync_sources, sync_state, tasks
from .sync_protocol import SyncError, validate_ingest_url
from .sync_recovery import _Pins


FORMAT_VERSION = 1
_ACTIVE_JOBS = frozenset({"pending", "running", "failed"})
_INPUT_TABLES = ("video_asset", "frame", "transcript_segment", "ocr_span", "visual_observation")
_VIDEO_TABLES = (*_INPUT_TABLES, "knowledge_atom", "evidence_ref")
_GLOBALS = ("knowledge_cluster", "conflict_case")
INTENT_DIRECTORY = ".reno-home-knowledge-restores-v1"
_INTENT_KEYS = frozenset({"format_version", "operation_id", "request", "request_sha256", "library_id", "node_id", "profile_sha256", "before_capture_sha256", "source_binding_sha256"})
MAX_REQUEST_CANDIDATES = 4096


class HomeRecoveryError(ValueError):
    def __init__(self, code, status=409):
        self.code = "HOME_RECOVERY_" + code
        self.status = status
        super().__init__(self.code)

    def public(self):
        return {"code": self.code, "detail": "知识恢复条件未满足，请核对原恢复操作。"}


def _fail(code, status=409):
    raise HomeRecoveryError(code, status) from None


def _json(value):
    return schema.canonical_json(value)


def _sha(value):
    return hashlib.sha256(_json(value).encode("utf-8")).hexdigest()


def _uuid(value):
    try:
        if type(value) is not str or str(uuid.UUID(value)) != value:
            _fail("REQUEST_INVALID", 400)
    except (ValueError, AttributeError):
        _fail("REQUEST_INVALID", 400)
    return value


def _digest(value):
    if type(value) is not str or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        _fail("REQUEST_INVALID", 400)
    return value


def _profile(profile):
    if config.is_cloud():
        _fail("ROLE_REQUIRED", 403)
    return lifecycle._profile(profile)


def _existing_state_lock(profile):
    # The explicit checkpoint producer already creates this lock. A preview
    # must not initialize a missing facility or invent a worker/state file.
    with sync_media._Directory(profile.database.parent) as directory:
        try:
            with directory.open_file(sync_state._LOCK):
                pass
        except FileNotFoundError:
            _fail("STATE_LOCK_MISSING")
        directory.verify()


@contextmanager
def _transaction(profile, *, writable, store):
    _existing_state_lock(profile)
    with store.reference_operation():
        con = db.connect(profile.database) if writable else migrations._open(profile.database, readonly=True)
        try:
            if not writable:
                migrations.validate(con)
            con.execute("PRAGMA busy_timeout=0")
            con.execute("BEGIN IMMEDIATE" if writable else "BEGIN")
            yield con
        except sqlite3.OperationalError as exc:
            if "locked" in str(exc).lower() or "busy" in str(exc).lower():
                _fail("DATABASE_BUSY")
            raise
        finally:
            con.rollback()
            con.close()


def _actual_schema(con):
    """Compare effects, rather than accepting an arbitrary trigger's hash."""
    schema.validate(con)
    if con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] != "16":
        _fail("SCHEMA_UNSUPPORTED")
    reference = sqlite3.connect(":memory:")
    try:
        migrations._create_schema(reference, readmit_variant=None)
        schema.install(reference)
        tables = set(preservation.PROJECTIONS_V16) | set(preservation._KNOWLEDGE)
        effects = []
        for table in sorted(tables):
            actual = sorted(tuple(row) for row in con.execute(f'PRAGMA main.foreign_key_list("{table}")'))
            expected = sorted(tuple(row) for row in reference.execute(f'PRAGMA foreign_key_list("{table}")'))
            if actual != expected:
                _fail("FOREIGN_KEY_CONTRACT")
            effects.append([table, actual])
        query = "SELECT name,tbl_name,sql FROM sqlite_master WHERE type='trigger' ORDER BY name"
        normalize = lambda rows: [[r[0], r[1], schema._normalized(r[2])] for r in rows]
        actual_triggers, expected_triggers = normalize(con.execute(query)), normalize(reference.execute(query))
        if actual_triggers != expected_triggers:
            _fail("TRIGGER_CONTRACT")
        effects.append(["triggers", actual_triggers])
        return _sha(effects)
    finally:
        reference.close()


def _state(store):
    try:
        return store.read()
    except SyncError as exc:
        if exc.code != "SYNC_STATE_MISSING":
            raise
        return None


def _rows_equal(before, candidate, tables, *, video_ids=None, atom_ids=None):
    def selected(rows):
        if video_ids is not None:
            rows = [r for r in rows if r.get("video_id") in video_ids]
        if atom_ids is not None:
            rows = [r for r in rows if r.get("id") in atom_ids or r.get("atom_id") in atom_ids]
        return rows
    if any(selected(before[t]) != selected(candidate[t]) for t in tables):
        _fail("UNFINISHED_INPUT_MISMATCH")


def _video_checkpoint(before, candidate, video_id):
    old_steps = [r for r in before["meta"] if r["key"].startswith("step:" + video_id + ":")]
    proposed = [r for r in candidate["meta"] if r["key"].startswith("step:" + video_id + ":")]
    if old_steps != proposed:
        _fail("UNFINISHED_CHECKPOINT_MISMATCH")
    if any(r["key"] == "step:" + video_id + ":atomize" for r in old_steps):
        _rows_equal(before, candidate, ("knowledge_atom", "evidence_ref"), video_ids={video_id})


def _foreign_closure(con, candidate):
    """Preserved FK rows must remain closed over the proposed knowledge."""
    for table, columns in preservation.PROJECTIONS_V16.items():
        foreign = {}
        for fk in con.execute(f'PRAGMA main.foreign_key_list("{table}")'):
            if fk[2] in candidate and fk[2] != "meta":
                foreign.setdefault((fk[0], fk[2]), []).append((fk[1], fk[3], fk[4]))
        for (_, target), fields in foreign.items():
            fields.sort()
            local, remote = [f[1] for f in fields], [f[2] for f in fields]
            if any(c not in columns for c in local) or any(c not in sources.COLUMNS[target] for c in remote):
                _fail("FOREIGN_KEY_CONTRACT")
            targets = {tuple(row[c] for c in remote) for row in candidate[target]}
            names = ",".join('"' + c + '"' for c in local)
            for row in con.execute(f'SELECT {names} FROM main."{table}"'):
                values = tuple(row)
                if all(v is not None for v in values) and values not in targets:
                    _fail("PRESERVED_REFERENCE_MISSING")


def _manual_records(con):
    records = {}
    for row in con.execute("SELECT key,value FROM meta WHERE substr(key,1,27)='budget:manual:execution:v1:'"):
        try:
            record = model_scopes._load_record(row[1])
            if (row[0] != "budget:manual:execution:v1:" + record["continuation_id"]
                    or record["library_id"] != db.knowledge_version(con)["library_id"]):
                _fail("UNFINISHED_CONSUMER_UNKNOWN")
            records[(record["continuation_id"], record["execution_generation"])] = record
        except (ValueError, TypeError):
            _fail("UNFINISHED_CONSUMER_UNKNOWN")
    return records


def _consumer_closure(before, candidate, record):
    if record["state"] == "completed":
        return
    consumer, selection, done = record["consumer"], set(record["selection"]), set(record["completed_items"])
    assets = {r["video_id"] for r in candidate["video_asset"]}
    if consumer in {"atomize", "rerun_atomize"}:
        if not selection <= assets:
            _fail("UNFINISHED_INPUT_MISSING")
        _rows_equal(before, candidate, _INPUT_TABLES, video_ids=selection)
        _rows_equal(before, candidate, ("knowledge_atom", "evidence_ref"), video_ids=done)
    elif consumer == "backfill_v2":
        atoms = {r["id"] for r in candidate["knowledge_atom"]}
        if not selection <= atoms:
            _fail("UNFINISHED_INPUT_MISSING")
        _rows_equal(before, candidate, ("knowledge_atom", "evidence_ref"), atom_ids=selection)
    elif consumer == "vlm":
        frames = {r["frame_id"] for r in candidate["frame"]}
        if not selection <= frames:
            _fail("UNFINISHED_INPUT_MISSING")
        # The frame selection has no video scope in this durable record;
        # ambiguous duplicate frame IDs cannot supply a compatibility proof.
        if sum(r["frame_id"] in selection for r in candidate["frame"]) != len(selection):
            _fail("UNFINISHED_CONSUMER_UNKNOWN")
        _rows_equal(before, candidate, ("video_asset", "frame", "visual_observation"))
    elif consumer in {"judge", "summary", "ask"}:
        _rows_equal(before, candidate, (*_VIDEO_TABLES, *_GLOBALS))
    else:
        _fail("UNFINISHED_CONSUMER_UNKNOWN")


def _model_witness(con, operation, record, before, candidate):
    """Accept an actual consumer-owned frozen checkpoint, never a bare SHA."""
    if record is None or record["consumer"] != "backfill_v2":
        _fail("UNFINISHED_INPUT_OPAQUE")
    prefix = "backfill-v2:batch:"
    key = operation["suboperation_key"]
    if not key.startswith(prefix) or not key[len(prefix):].isdigit():
        _fail("UNFINISHED_INPUT_OPAQUE")
    number = int(key[len(prefix):])
    # This is the existing backfill consumer's durable contract. Its witness
    # is written before budget reserve/dispatch and updated with child commits.
    from scripts import backfill_atoms_v2 as backfill
    ids = record["selection"][number * backfill.BATCH:(number + 1) * backfill.BATCH]
    if not ids:
        _fail("UNFINISHED_INPUT_MISSING")
    columns = ("id", "category", "subject", "claim", "reason", "risk_if_ignored", "polarity", "conditions_json", "evidence_nature", "dimension", "exceptions")
    def fixed(rows):
        found = {r["id"]: r for r in rows["knowledge_atom"]}
        if any(i not in found for i in ids):
            _fail("UNFINISHED_INPUT_MISSING")
        return [{c: found[i][c] for c in columns} for i in ids]
    current, proposed = fixed(before), fixed(candidate)
    expected = {"format_version": 1, "consumer": "backfill_v2", "library_id": operation["library_id"],
        "continuation_id": operation["continuation_id"], "execution_generation": operation["execution_generation"],
        "suboperation_key": key, "selection_sha256": _sha(ids), "input_sha256": _sha(current), "current_sha256": _sha(current)}
    witness_key = f"budget:consumer:backfill-input:v1:{operation['continuation_id']}:{operation['execution_generation']}:{number}"
    try:
        actual = backfill._witness_record(con, witness_key, expected)
    except ValueError:
        _fail("UNFINISHED_INPUT_OPAQUE")
    if actual is None:
        _fail("UNFINISHED_INPUT_OPAQUE")
    if actual["current_sha256"] != _sha(current) or actual["current_sha256"] != _sha(proposed):
        _fail("UNFINISHED_INPUT_MISMATCH")
    return _sha(actual)


def _resolution_requests(con, operation, url, core, profile):
    from . import source_resolution_contract as codec
    from . import source_resolution_budget as resolver
    from types import SimpleNamespace
    plan = codec.ResolutionPlan("dns", url, url, operation["input_sha256"], operation["max_attempts"])
    items = con.execute("SELECT attempt_id,ordinal,request_sha256,state,result_text,result_storage_state,outcome,settlement_sha256,initial_reservation_json,dispatched_at FROM budget_attempt WHERE operation_id=? ORDER BY ordinal", (operation["operation_id"],)).fetchall()
    hops, initial = [], None
    for position, item in enumerate(items):
        snapshot = json.loads(item["initial_reservation_json"])
        if (snapshot.get("resolver_contract_version") != 1 or snapshot.get("resolver_max_attempts") != operation["max_attempts"]
                or snapshot.get("resolver_phase") != plan.phase or item["request_sha256"] != plan.request_sha256):
            _fail("ORIGINAL_REQUEST_MISMATCH")
        if item["state"] in {"reserved", "dispatching", "indeterminate"}:
            if position != len(items) - 1:
                _fail("ORIGINAL_REQUEST_MISMATCH")
            break
        if item["state"] == "released":
            continue
        if item["result_storage_state"] != "stored" or item["result_text"] is None:
            _fail("UNFINISHED_INPUT_OPAQUE")
        args = dict(request_sha256=item["request_sha256"], input_sha256=operation["input_sha256"], phase=plan.phase, max_attempts=operation["max_attempts"])
        if codec.decode_plan(item["result_text"], **args) != plan:
            _fail("ORIGINAL_REQUEST_MISMATCH")
        result = codec.decode_envelope(item["result_text"], **args)
        if item["outcome"] == "resolution_retry" and result.retryable:
            continue
        if not result.successful:
            if item["outcome"] != "terminal_error" or position != len(items) - 1:
                _fail("ORIGINAL_REQUEST_MISMATCH")
            break
        if item["outcome"] != "source_step_complete":
            _fail("ORIGINAL_REQUEST_MISMATCH")
        common = dict(hop_index=plan.hop_index, generation=plan.generation, visited_urls=plan.visited_urls,
            refresh_attempt_id=plan.refresh_attempt_id, refresh_settlement_sha256=plan.refresh_settlement_sha256)
        if plan.phase == "dns":
            plan = codec.ResolutionPlan("headers", url, plan.current_url, operation["input_sha256"], operation["max_attempts"],
                dns_attempt_id=item["attempt_id"], dns_settlement_sha256=item["settlement_sha256"], peer_ip=result.addresses[0], **common)
        elif result.next_url is None:
            hops.append({"url": plan.current_url, "peer_ip": plan.peer_ip, "status": result.status, "next_url": None})
            completed = resolver.CompletedResolution(codec.canonical_json(sync_sources._validate_resolution({
                "resolution_version": 1, "submitted_url": url, "resolved_url": plan.current_url,
                "resolved_at": result.observed_at, "hops": hops})), item["attempt_id"],
                item["request_sha256"], item["settlement_sha256"], plan.generation,
                plan.refresh_attempt_id, plan.refresh_settlement_sha256)
            if plan.generation == 1 and initial is not None:
                resolver._same_resource(initial, completed)
            if position == len(items) - 1:
                break
            if plan.generation != 0 or initial is not None:
                _fail("ORIGINAL_REQUEST_MISMATCH")
            initial, following = completed, items[position + 1]
            rights = [(initial.attempt_id, initial.settlement_sha256)]
            rights.extend((r[0], r[1]) for r in con.execute("SELECT a.attempt_id,a.settlement_sha256 FROM budget_attempt a JOIN budget_operation o USING(operation_id) WHERE o.root_job_id=? AND o.continuation_id=? AND o.execution_generation=? AND o.kind='download' AND a.download_segment='video' AND a.state='settled' AND a.outcome='source_retry' AND a.result_storage_state='stored'",
                (operation["root_job_id"], operation["continuation_id"], operation["execution_generation"])))
            options = [codec.ResolutionPlan("dns", url, url, operation["input_sha256"], operation["max_attempts"],
                generation=1, previous_attempt_id=initial.attempt_id, previous_settlement_sha256=initial.settlement_sha256,
                refresh_attempt_id=right[0], refresh_settlement_sha256=right[1]) for right in rights]
            matching = [p for p in options if p.request_sha256 == following["request_sha256"]]
            if len(matching) != 1:
                _fail("ORIGINAL_REQUEST_MISMATCH")
            plan = matching[0]
            # This object is only a reader for the existing ledger validator.
            # It lacks HomeExecutionBinding's seal/check and cannot dispatch.
            reader = SimpleNamespace(profile=profile, core=core,
                namespace=_sha({k: core[k] for k in ("binding", "item_id", "submitted_url_sha256")}))
            instant = following["dispatched_at"] or json.loads(following["initial_reservation_json"])["admitted_at"]
            from . import budget
            resolver._authorize(reader, resolver.RefreshAuthorization(plan.refresh_attempt_id, plan.refresh_settlement_sha256),
                initial, connection=con, historical_at=budget._datetime(instant))
            hops = []
        else:
            hops.append({"url": plan.current_url, "peer_ip": plan.peer_ip, "status": result.status, "next_url": result.next_url})
            plan = codec.ResolutionPlan("dns", url, result.next_url, operation["input_sha256"], operation["max_attempts"],
                hop_index=plan.hop_index + 1, visited_urls=(*plan.visited_urls, plan.current_url),
                previous_attempt_id=item["attempt_id"], previous_settlement_sha256=item["settlement_sha256"],
                generation=plan.generation, refresh_attempt_id=plan.refresh_attempt_id,
                refresh_settlement_sha256=plan.refresh_settlement_sha256)


def _source_input(con, operation, job, state, before, candidate, pins, profile):
    """Reconstruct the original logical source from immutable task/core facts."""
    core = None if state is None else state.get("source_execution")
    is_home = (core is not None and operation["root_job_id"] == core["root_job_id"] and
        (operation["continuation_id"], operation["execution_generation"]) == (core["continuation_id"], core["execution_generation"]))
    if is_home:
        entries = [e for e in state["claims"] if e["item_id"] == core["item_id"]]
        if len(entries) != 1:
            _fail("ORIGINAL_INPUT_MISSING")
        url = validate_ingest_url(entries[0]["url"])
        if hashlib.sha256(url.encode()).hexdigest() != core["submitted_url_sha256"]:
            _fail("ORIGINAL_INPUT_MISMATCH")
        namespace = _sha({k: core[k] for k in ("binding", "item_id", "submitted_url_sha256")})
        prefix = "home:" + namespace + ":"
    else:
        if job is None or job["kind"] not in {"ingest", "favlist"}:
            _fail("UNFINISHED_INPUT_OPAQUE")
        payload = json.loads(job["payload_json"])
        url = payload.get("target") if job["kind"] == "ingest" else payload.get("url")
        if type(url) is not str:
            _fail("ORIGINAL_INPUT_MISSING")
        prefix = ""
    kind = operation["kind"]
    if kind == "source_resolution":
        if not is_home:
            _fail("UNFINISHED_INPUT_OPAQUE")
        from .source_resolution_contract import RESOLVER_CONTRACT
        digest = _sha({"format_version": 1, "kind": kind, "resolver_contract": RESOLVER_CONTRACT,
            "namespace": namespace, "submitted_url_sha256": core["submitted_url_sha256"]})
        key = prefix + "resolution:v1"
        if core["resolution_input_sha256"] != digest:
            _fail("ORIGINAL_INPUT_MISMATCH")
        _resolution_requests(con, operation, url, core, profile)
    elif kind in {"source_metadata", "source_enumeration"}:
        if kind == "source_enumeration" and (is_home or job["kind"] != "favlist"):
            _fail("ORIGINAL_INPUT_MISMATCH")
        digest = _sha({"kind": kind, "source": url, "runner_contract": "yt_dlp_v1"})
        key = prefix + ("ingest:metadata:v1" if kind == "source_metadata" else "favlist:enumeration:v1")
    elif kind == "download":
        logical = _sha({"kind": "source_metadata", "source": url, "runner_contract": "yt_dlp_v1"})
        metadata = con.execute("SELECT operation_id,input_sha256 FROM budget_operation WHERE continuation_id=? AND execution_generation=? AND root_job_id IS ? AND kind='source_metadata' AND suboperation_key=? AND input_sha256=?",
            (operation["continuation_id"], operation["execution_generation"], operation["root_job_id"], prefix + "ingest:metadata:v1", logical)).fetchall()
        if len(metadata) != 1:
            _fail("ORIGINAL_INPUT_MISSING")
        genesis = None
        for item in con.execute("SELECT request_sha256,result_text FROM budget_attempt WHERE operation_id=? AND state='settled' AND outcome='source_step_complete' AND result_storage_state='stored' ORDER BY ordinal", (metadata[0][0],)):
            decoded = source_contract.decode_envelope(item[1], request_sha256=item[0], input_sha256=logical, kind="source_metadata", segment=None)
            if decoded.successful:
                genesis = decoded.metadata_json
                break
        if genesis is None:
            _fail("ORIGINAL_INPUT_MISSING")
        digest = _sha({"kind": kind, "source": url, "runner_contract": "yt_dlp_v1", "genesis_metadata_sha256": hashlib.sha256(genesis.encode()).hexdigest()})
        video_id = json.loads(genesis)["id"]
        key = prefix + "ingest:media:v1:" + video_id
        for item in con.execute("SELECT request_sha256,result_text,download_segment FROM budget_attempt WHERE operation_id=? AND state='settled' AND outcome='source_step_complete' AND result_storage_state='stored' ORDER BY ordinal", (operation["operation_id"],)):
            decoded = source_contract.decode_envelope(item[1], request_sha256=item[0], input_sha256=digest, kind="download", segment=item[2])
            if decoded.successful:
                artifact = decoded.artifact
                # A retained paid file can exist before SQL installs its asset.
                # If SQL already consumed it, preserve that checkpoint in source.
                current = con.execute("SELECT video_id FROM video_asset WHERE video_id=? AND " + ("content_sha256" if artifact.segment == "video" else "audio_sha256") + "=?", (video_id, artifact.sha256)).fetchone()
                if current is not None and not any(r["video_id"] == video_id and r["content_sha256" if artifact.segment == "video" else "audio_sha256"] == artifact.sha256 for r in candidate["video_asset"]):
                    _fail("UNFINISHED_CHECKPOINT_MISMATCH")
                if current is not None:
                    _video_checkpoint(before, candidate, video_id)
    else:
        _fail("UNFINISHED_INPUT_OPAQUE")
    if operation["suboperation_key"] != key or operation["input_sha256"] != digest:
        _fail("ORIGINAL_INPUT_MISMATCH")
    if kind != "source_resolution":
        physical_urls = {url} if not is_home else set()
        if is_home:
            work = state["work"]
            if work is not None and work.get("source_mapping_inputs") is not None:
                physical_urls.add(work["source_mapping_inputs"]["resolution"]["resolved_url"])
            from . import source_resolution_contract as codec
            for resolver in con.execute("SELECT operation_id,input_sha256,max_attempts FROM budget_operation WHERE root_job_id=? AND continuation_id=? AND execution_generation=? AND kind='source_resolution' AND suboperation_key=?",
                    (core["root_job_id"], core["continuation_id"], core["execution_generation"], prefix + "resolution:v1")):
                for item in con.execute("SELECT request_sha256,result_text FROM budget_attempt WHERE operation_id=? AND state='settled' AND outcome='source_step_complete' AND result_storage_state='stored' ORDER BY ordinal", (resolver[0],)):
                    args = dict(request_sha256=item[0], input_sha256=resolver[1], phase="headers", max_attempts=resolver[2])
                    envelope = json.loads(item[1])
                    if envelope.get("plan", {}).get("phase") != "headers":
                        continue
                    plan = codec.decode_plan(item[1], **args)
                    result = codec.decode_envelope(item[1], **args)
                    if result.successful and result.next_url is None:
                        physical_urls.add(plan.current_url)
        metadata_rows = con.execute("SELECT a.request_sha256,a.result_text,o.input_sha256 FROM budget_attempt a JOIN budget_operation o USING(operation_id) WHERE o.root_job_id IS ? AND o.continuation_id=? AND o.execution_generation=? AND o.kind='source_metadata' AND o.suboperation_key=? AND a.state='settled' AND a.outcome='source_step_complete' AND a.result_storage_state='stored' ORDER BY a.ordinal",
            (operation["root_job_id"], operation["continuation_id"], operation["execution_generation"], prefix + "ingest:metadata:v1")).fetchall()
        metadata_values = []
        for item in metadata_rows:
            decoded = source_contract.decode_envelope(item[1], request_sha256=item[0], input_sha256=item[2], kind="source_metadata", segment=None)
            if decoded.successful:
                metadata_values.append(decoded.metadata_json)
        refresh_rights = []
        if kind in {"source_metadata", "download"} and metadata_values:
            first = metadata_values[0]
            vid = json.loads(first)["id"]
            expected_input = _sha({"kind": "download", "source": url, "runner_contract": "yt_dlp_v1", "genesis_metadata_sha256": hashlib.sha256(first.encode()).hexdigest()})
            for failure in con.execute("SELECT a.attempt_id,a.settlement_sha256,a.request_sha256,a.result_text,o.input_sha256 FROM budget_attempt a JOIN budget_operation o USING(operation_id) WHERE o.root_job_id IS ? AND o.continuation_id=? AND o.execution_generation=? AND o.kind='download' AND o.suboperation_key=? AND o.input_sha256=? AND a.state='settled' AND a.outcome='source_retry' AND a.download_segment='video' AND a.result_storage_state='stored' ORDER BY a.ordinal",
                    (operation["root_job_id"], operation["continuation_id"], operation["execution_generation"], prefix + "ingest:media:v1:" + vid, expected_input)):
                source_contract.validate_retry_envelope(failure[3], request_sha256=failure[2], input_sha256=failure[4], kind="download", segment="video")
                refresh_rights.append((failure[0], failure[1]))
        attempts = con.execute("SELECT request_sha256,initial_reservation_json,state,result_text,result_storage_state,download_segment FROM budget_attempt WHERE operation_id=? ORDER BY ordinal", (operation["operation_id"],)).fetchall()
        for item in attempts:
            snapshot = json.loads(item["initial_reservation_json"])
            if snapshot.get("source_contract_version") != 1:
                _fail("UNFINISHED_INPUT_OPAQUE")
            if kind == "download":
                segment = item["download_segment"]
                filename = "video.mp4" if segment == "video" else "audio.m4a"
                relpath = f"originals/{video_id}/source-{hashlib.sha256(genesis.encode()).hexdigest()}/{filename}"
                options = (source_contract.SourcePlan("download", u, segment=segment, info_json=genesis, artifact_relpath=relpath) for u in sorted(physical_urls))
            else:
                options = (source_contract.SourcePlan(kind, u) for u in sorted(physical_urls))
            if kind == "source_metadata":
                options = chain(options, (source_contract.SourcePlan(kind, u, phase="refresh", refresh_attempt_id=right[0], refresh_settlement_sha256=right[1]) for u in sorted(physical_urls) for right in refresh_rights))
            elif kind == "download":
                options = chain(options, (source_contract.SourcePlan("download", u, segment=item["download_segment"], info_json=info,
                    artifact_relpath=f"originals/{video_id}/source-{hashlib.sha256(info.encode()).hexdigest()}/{filename}",
                    phase="refresh", refresh_attempt_id=right[0], refresh_settlement_sha256=right[1])
                    for u in sorted(physical_urls) for right in refresh_rights for info in metadata_values
                    if json.loads(info)["id"] == video_id))
            found = False
            for position, plan in enumerate(options):
                if position >= MAX_REQUEST_CANDIDATES:
                    _fail("LIMIT")
                if plan.request_sha256 == item["request_sha256"]:
                    found = True
                    break
            if not found:
                _fail("ORIGINAL_REQUEST_MISMATCH")
            if item["state"] == "settled" and item["result_storage_state"] == "stored":
                decoded = source_contract.decode_envelope(item["result_text"], request_sha256=item["request_sha256"], input_sha256=digest, kind=kind, segment=item["download_segment"])
                if kind == "download" and decoded.successful:
                    artifact = decoded.artifact
                    _, identity = sources._pin_file(pins, profile.media / artifact.relpath, backups.MAX_IMAGE, base=profile.media)
                    if (identity["sha256"] != artifact.sha256 or identity["byte_size"] != artifact.size_bytes
                            or (int(identity["device"]), int(identity["inode"]), int(identity["mtime_ns"])) != (artifact.device, artifact.inode, artifact.mtime_ns)):
                        _fail("UNFINISHED_CHECKPOINT_MISMATCH")
    return digest


def _work_closure(con, store, state, before, candidate, pins, profile):
    if state is None:
        return None
    work, core = state["work"], state.get("source_execution")
    if core is not None:
        row = con.execute("SELECT kind,continuation_id,execution_generation FROM job WHERE job_id=?", (core["root_job_id"],)).fetchone()
        if (row is None or row["kind"] != "sync" or (row["continuation_id"], row["execution_generation"]) !=
                (core["continuation_id"], core["execution_generation"])):
            _fail("ORIGINAL_CORE_MISMATCH")
        entries = [e for e in state["claims"] if e["item_id"] == core["item_id"]]
        if len(entries) != 1 or hashlib.sha256(validate_ingest_url(entries[0]["url"]).encode()).hexdigest() != core["submitted_url_sha256"]:
            _fail("ORIGINAL_INPUT_MISSING")
    if work is None:
        return None
    store.assert_work_reusable(work)
    assets = {r["video_id"]: r for r in candidate["video_asset"]}
    asset = assets.get(work["video_id"])
    if asset is None:
        _fail("UNFINISHED_INPUT_MISSING")
    _rows_equal(before, candidate, _INPUT_TABLES, video_ids={work["video_id"]})
    _video_checkpoint(before, candidate, work["video_id"])
    if work["phase"] in {"processed", "judged", "reported"}:
        _rows_equal(before, candidate, ("knowledge_atom", "evidence_ref"), video_ids={work["video_id"]})
    if work["phase"] in {"judged", "reported"}:
        _rows_equal(before, candidate, _GLOBALS)
    mapping = sync_sources.SourceMap(profile.database.parent, profile=profile,
        source_library_id=state["binding"]["home_library_id"], source_node_id=state["binding"]["home_node_id"])
    mapping_id, generation = work.get("source_mapping_id"), work.get("source_mapping_generation")
    inputs = work.get("source_mapping_inputs")
    entry = next((e for e in state["claims"] if e["item_id"] == work["item_id"]), None)
    if entry is None:
        _fail("ORIGINAL_INPUT_MISSING")
    if inputs is not None:
        sync_sources.validate_resource_identity(inputs["resolution"]["resolved_url"], inputs["extracted_video_id"])
        if (inputs["resolution"]["submitted_url"] != entry["url"] or inputs["media_sha256"] != asset["content_sha256"]):
            _fail("SOURCE_MAP_MISMATCH")
    if mapping_id is not None:
        origin = mapping.get(mapping_id, mapping_generation=generation)
        path = (mapping.v2_root if generation is not None else mapping.root) / ((generation or mapping_id) + ".json")
        raw, _ = sources._pin_file(pins, path, sync_sources.MAX_RECORD_BYTES, base=profile.database.parent, capture=True)
        pinned = sync_sources.validate_source_record(json.loads(raw), mapping_id=mapping_id,
            mapping_generation=generation, source_library_id=state["binding"]["home_library_id"],
            source_node_id=state["binding"]["home_node_id"])
        if (pinned != origin or origin["video_id"] != asset["video_id"] or origin["media_sha256"] != asset["content_sha256"]
                or origin["submitted_url"] != entry["url"] or inputs is not None and (
                    json.loads(raw)["resolution"] != inputs["resolution"] or any(
                    origin[k] != inputs[k] for k in ("extracted_video_id", "media_sha256", "recorded_at")))):
            _fail("SOURCE_MAP_MISMATCH")
        return _sha(json.loads(raw))
    if inputs is None or generation is None:
        _fail("SOURCE_MAP_MISSING")
    return _sha(inputs)


def _semantic_closure(con, store, state, candidate, pins, profile):
    before = sources._read_knowledge(con)
    assets = {r["video_id"] for r in candidate["video_asset"]}
    job_columns = preservation.PROJECTIONS_V16["job"]
    jobs = {r["job_id"]: dict(r) for r in con.execute("SELECT " + ",".join(job_columns) + " FROM job")}
    def lineage_key(job):
        return (job["continuation_id"], job["execution_generation"]) if job["continuation_id"] is not None else ("legacy", job["job_id"])
    latest_jobs = {}
    for job in sorted(jobs.values(), key=lambda j: j["job_id"]):
        latest_jobs[lineage_key(job)] = job
    # Even a historical task's video association is preserved, never nulled
    # by REPLACE/CASCADE or silently repointed to another restored asset.
    if any(j["video_id"] is not None and j["video_id"] not in assets for j in jobs.values()):
        _fail("JOB_INPUT_MISSING")
    for job in latest_jobs.values():
        if job["status"] in _ACTIVE_JOBS and job["kind"] == "process":
            video_id = job["video_id"]
            _rows_equal(before, candidate, _INPUT_TABLES, video_ids={video_id})
            _video_checkpoint(before, candidate, video_id)
    records = _manual_records(con)
    for record in records.values():
        _consumer_closure(before, candidate, record)
    consumers = []
    for raw in con.execute("SELECT " + ",".join(preservation.PROJECTIONS_V16["budget_operation"]) + " FROM budget_operation ORDER BY operation_id"):
        operation = dict(raw)
        lineage = (operation["continuation_id"], operation["execution_generation"])
        job = jobs.get(operation["root_job_id"])
        consumer_job = latest_jobs.get(lineage) if job is not None else None
        manual = records.get(lineage) if operation["root_job_id"] is None else None
        active = (consumer_job is not None and consumer_job["status"] in _ACTIVE_JOBS) or (
                  manual is not None and manual["state"] == "running")
        if not active:
            if (job is None and manual is None and operation["state"] != "completed"):
                _fail("UNFINISHED_CONSUMER_UNKNOWN")
            continue
        if operation["kind"] == "model":
            proof_sha = _model_witness(con, operation, manual, before, candidate)
        else:
            proof_sha = _source_input(con, operation, job, state, before, candidate, pins, profile)
        consumers.append({"operation_id": operation["operation_id"], "input_proof_sha256": proof_sha})
    _foreign_closure(con, candidate)
    mapping_sha = _work_closure(con, store, state, before, candidate, pins, profile)
    return {"format_version": FORMAT_VERSION, "before_knowledge_sha256": _sha(before),
        "candidate_knowledge_sha256": _sha(candidate), "job_video_refs": sum(j["video_id"] is not None for j in jobs.values()),
        "running_manual_consumers": sum(r["state"] == "running" for r in records.values()),
        "compatible_source_operations": consumers, "source_map_sha256": mapping_sha}


def _capture(con, store, source, pins, profile):
    effects = _actual_schema(con)
    state = _state(store)
    version = db.knowledge_version(con)
    if state is not None and state["local_epoch"] != version["library_epoch"]:
        _fail("STATE_EPOCH_GAP")
    core = None if state is None else state.get("source_execution")
    before = preservation.capture(con, profile=profile, store=store, root_job_id=None if core is None else core["root_job_id"])
    semantic = _semantic_closure(con, store, state, source.rows, pins, profile)
    document = {"format_version": FORMAT_VERSION, "action": "knowledge_restore", "source": source.public(),
        "before_version": version, "before_capture_sha256": before.sha256, "effects_sha256": effects,
        "semantic_closure": semantic}
    return before, {**document, "digest": _sha(document)}


def preview(*, profile, source_id):
    """Pure preview using existing facilities, with no task/worker creation."""
    profile, source_id = _profile(profile), _uuid(source_id)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with _transaction(profile, writable=False, store=store) as con, ExitStack() as stack:
        pins = _Pins(stack)
        with sources.open_source(con, source_id, profile=profile) as source:
            _, result = _capture(con, store, source, pins, profile)
            pins.verify()
            source.verify()
            lifecycle.require_guard(profile).verify()
            return result


def _sql_digest(con):
    """Bounded typed fixed-projection digest, including private SQLite BLOBs."""
    order, _, version, _ = preservation._schema(con)
    if version != "16":
        _fail("SCHEMA_UNSUPPORTED")
    projections = {**preservation.PROJECTIONS_V16,
        **{name: tuple(cols.split()) for name, cols in preservation._KNOWLEDGE.items()}}
    scan, result = [0, 0], []
    for table in sorted(projections):
        columns = projections[table]
        names = ",".join('"' + c + '"' for c in columns)
        ordering = ",".join('"' + c + '"' for c in order[table])
        rows, digest = 0, hashlib.sha256(_json([table, list(columns)]).encode())
        lengths = ",".join('length(CAST("' + c + '" AS BLOB))' for c in columns)
        for sizes in con.execute(f'SELECT {lengths} FROM main."{table}" LIMIT ?', (preservation.MAX_ROWS + 1,)):
            scan[0] += 1
            if scan[0] > preservation.MAX_ROWS or any(s is not None and s > preservation.MAX_CELL_BYTES for s in sizes):
                _fail("LIMIT")
        for raw in con.execute(f'SELECT {names} FROM main."{table}" ORDER BY {ordering}'):
            row = dict(zip(columns, raw))
            digest.update(bytes.fromhex(preservation._row_sha(table, columns, row, scan)))
            rows += 1
        result.append([table, rows, digest.hexdigest()])
    return _sha(result)


def _intent_path(profile, operation_id):
    return profile.database.parent / INTENT_DIRECTORY / (operation_id + ".intent.json")


def _read_intent(profile, operation_id, request):
    path = _intent_path(profile, operation_id)
    try:
        raw, _ = backups._read(path, maximum=schema.MAX_REQUEST_BYTES)
    except FileNotFoundError:
        return None
    value = backups._decode(raw)
    if (type(value) is not dict or set(value) != _INTENT_KEYS or type(value["format_version"]) is not int
            or value["format_version"] != 1 or value["operation_id"] != operation_id
            or _json(value).encode() != raw or value["request"] != request or value["request_sha256"] != _sha(request)
            or value["profile_sha256"] != _sha(list(profile.identity))):
        _fail("OPERATION_CONFLICT")
    for name in ("library_id", "node_id"):
        _uuid(value[name])
    for name in ("before_capture_sha256", "source_binding_sha256"):
        _digest(value[name])
    return value


def _permanent_intent(profile, operation_id, request, before, source, pins):
    document = before.public()
    expected = {"format_version": 1, "operation_id": operation_id, "request": request,
        "request_sha256": _sha(request), "library_id": document["library_id"], "node_id": document["node_id"],
        "profile_sha256": document["profile_sha256"], "before_capture_sha256": before.sha256,
        "source_binding_sha256": source.declaration_sha256}
    path = _intent_path(profile, operation_id)
    actual = _read_intent(profile, operation_id, request)
    if actual is None:
        backup_id = str(uuid.uuid5(uuid.UUID(operation_id), "reno.home.knowledge.restore.protection.v1"))
        try:
            backups._read(backups._catalog(profile) / (backup_id + ".json"))
        except FileNotFoundError:
            pass
        else:
            _fail("PROTECTION_INTENT_MISSING")
        with sync_media._Directory(path.parent, base=profile.database.parent, create=True):
            backups._write_json(path, expected, replace=False)
    elif actual != expected:
        _fail("OPERATION_CONFLICT")
    raw, _ = sources._pin_file(pins, path, schema.MAX_REQUEST_BYTES, capture=True, base=profile.database.parent)
    if raw != _json(expected).encode():
        _fail("OPERATION_CONFLICT")


@contextmanager
def _protection(con, operation_id, profile):
    identifier = str(uuid.uuid5(uuid.UUID(operation_id), "reno.home.knowledge.restore.protection.v1"))
    destination = profile.database.with_name(profile.database.name + ".bak-home-restore-" + operation_id)
    before_sql = _sql_digest(con)
    db.snapshot(con, destination, profile=profile, kind="maintenance", backup_id=identifier)
    registry = backups._catalog(profile) / (identifier + ".json")
    with ExitStack() as stack:
        pins = _Pins(stack)
        record_raw, _ = sources._pin_file(pins, registry, backups.MAX_JSON, capture=True)
        record = backups._decode(record_raw)
        descriptor, descriptor_sha = backups._verify_ready(record, profile)
        _, sidecar_identity = sources._pin_file(pins, destination.with_name(destination.name + backups.SUFFIX), backups.MAX_JSON)
        if sidecar_identity["sha256"] != descriptor_sha:
            _fail("PROTECTION_IMAGE_MISMATCH")
        sources._pin_file(pins, destination, backups.MAX_IMAGE, expected=descriptor["database_file"])
        origin = pins.files[-1][2]
        # SQLite receives a private copy of the pinned image, never a writable
        # path to the registered recovery/protection artifact itself.
        temporary = stack.enter_context(tempfile.TemporaryDirectory(prefix="reno-home-restore-"))
        from pathlib import Path
        image = Path(temporary) / "image.sqlite"
        os.lseek(origin.fileno(), 0, os.SEEK_SET)
        copied, started = 0, time.monotonic()
        with image.open("xb") as target:
            for chunk in iter(lambda: os.read(origin.fileno(), 128 * 1024), b""):
                copied += len(chunk)
                if copied > descriptor["database_file"]["byte_size"] or time.monotonic() - started > sources.MAX_SECONDS:
                    _fail("PROTECTION_IMAGE_MISMATCH")
                target.write(chunk)
        if copied != descriptor["database_file"]["byte_size"]:
            _fail("PROTECTION_IMAGE_MISMATCH")
        archive = sqlite3.connect(image.as_uri() + "?mode=ro&immutable=1", uri=True)
        stack.callback(archive.close)
        archive.row_factory = sqlite3.Row
        archive.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, preservation.MAX_CELL_BYTES)
        if _sql_digest(archive) != before_sql:
            _fail("PROTECTION_IMAGE_MISMATCH")
        pins.verify()
        yield {"protection_backup_id": identifier, "protection_descriptor_sha256": descriptor_sha,
               "protection_image_sha256": descriptor["database_file"]["sha256"]}, pins
        pins.verify()


def _write_knowledge(con, rows):
    for table in ("evidence_ref", "knowledge_atom", "visual_observation", "ocr_span", "transcript_segment", "frame", "conflict_case", "knowledge_cluster"):
        con.execute(f'DELETE FROM main."{table}"')
    candidates = {r["video_id"] for r in rows["video_asset"]}
    for row in con.execute("SELECT video_id FROM video_asset").fetchall():
        if row[0] not in candidates:
            con.execute("DELETE FROM video_asset WHERE video_id=?", (row[0],))
    columns = sources.COLUMNS["video_asset"]
    fields = ",".join('"' + c + '"' for c in columns)
    updates = ",".join('"' + c + '"=excluded."' + c + '"' for c in columns if c != "video_id")
    for row in rows["video_asset"]:
        con.execute(f'INSERT INTO video_asset({fields}) VALUES({",".join("?" for _ in columns)}) ON CONFLICT(video_id) DO UPDATE SET {updates}', tuple(row[c] for c in columns))
    for table in ("frame", "transcript_segment", "ocr_span", "visual_observation", "knowledge_atom", "evidence_ref", "knowledge_cluster", "conflict_case"):
        columns = sources.COLUMNS[table]
        names = ",".join('"' + c + '"' for c in columns)
        con.executemany(f'INSERT INTO main."{table}"({names}) VALUES({",".join("?" for _ in columns)})',
                        [tuple(row[c] for c in columns) for row in rows[table]])
    con.execute("DELETE FROM meta WHERE substr(key,1,5)='step:'")
    con.executemany("INSERT INTO meta(key,value) VALUES(?,?)", [(r["key"], r["value"]) for r in rows["meta"]])
    review.replay(con)
    con.execute("DELETE FROM atom_fts")
    db.fts_sync_atoms(con, [r["id"] for r in rows["knowledge_atom"]])


def _request(source_id, expected_digest):
    return {"action": "knowledge_restore", "source_id": _uuid(source_id), "expected_digest": _digest(expected_digest)}


def _record(con, operation_id):
    schema.validate(con)
    row = con.execute('SELECT ' + ",".join(schema.COLUMNS) + ' FROM ' + schema.TABLE + ' WHERE operation_id=?', (operation_id,)).fetchone()
    return None if row is None else schema.validate_record(row)


def _validate_business(record):
    request, result = json.loads(record["request_json"]), json.loads(record["result_json"])
    if (request != _request(record["source_id"], request.get("expected_digest")) or record["source_kind"] != "home_checkpoint"
            or result != {"format_version": 1, "action": "knowledge_restore", "operation_id": record["operation_id"],
                "source_id": record["source_id"], "before_version": json.loads(record["before_version_json"]),
                "after_version": json.loads(record["after_version_json"]), "knowledge_sha256": result.get("knowledge_sha256"),
                "protection_backup_id": record["protection_backup_id"]}):
        _fail("RECEIPT_INVALID")
    _digest(result["knowledge_sha256"])
    return result


def _reconcile(con, store, record):
    """An old receipt can repair only its original actual State epoch gap."""
    result = _validate_business(record)
    library, node = sync_queue._local_identity(con)
    version = db.knowledge_version(con)
    if (library, node) != (record["library_id"], record["node_id"]) or version["library_epoch"] < record["after_library_epoch"]:
        _fail("REPLAY_IDENTITY_MISMATCH")
    checkpoint = json.loads(record["checkpoint_json"])
    state = _state(store)
    if state is not None and (state["binding"]["home_library_id"], state["binding"]["home_node_id"]) != (library, node):
        _fail("REPLAY_IDENTITY_MISMATCH")
    if state is not None and state["local_epoch"] > version["library_epoch"]:
        _fail("STATE_CHANGED")
    if not checkpoint["state_present"]:
        if state is not None:
            # A state created later cannot be reconstructed as the old absence.
            if state["local_epoch"] < record["after_library_epoch"]:
                _fail("STATE_CHANGED")
        return result
    if state is None:
        _fail("STATE_CHANGED")
    if state["local_epoch"] >= record["after_library_epoch"]:
        return result
    if state["local_epoch"] != record["before_library_epoch"] or _sha(state) != checkpoint["canonical_state_sha256"]:
        _fail("STATE_CHANGED")
    # If later restores committed while a crash gap remained, require their
    # exact append-only epoch chain. Never infer a new core/admission from it.
    target = record["after_library_epoch"]
    normalized = {k: v for k, v in state.items() if k != "local_epoch"}
    while target < version["library_epoch"]:
        later = con.execute('SELECT ' + ",".join(schema.COLUMNS) + ' FROM ' + schema.TABLE + ' WHERE library_id=? AND before_library_epoch=?', (library, target)).fetchall()
        if len(later) != 1:
            _fail("EPOCH_CHAIN_MISSING")
        next_record = schema.validate_record(later[0])
        _validate_business(next_record)
        next_checkpoint = json.loads(next_record["checkpoint_json"])
        expected_state = {**normalized, "local_epoch": target}
        if next_checkpoint["canonical_state_sha256"] != _sha(expected_state):
            _fail("STATE_CHANGED")
        target = next_record["after_library_epoch"]
    before = dict(state)
    store.update({"local_epoch": target})
    actual = store.read()
    if actual != {**before, "local_epoch": target}:
        _fail("STATE_CHANGED")
    return result


def _build_record(operation_id, source, before, after, protection, request, knowledge_sha256):
    original, checkpoint = before.public(), before.public()["checkpoint"]
    lineage = checkpoint["source_execution_lineage"]
    result = {"format_version": 1, "action": "knowledge_restore", "operation_id": operation_id,
        "source_id": source.declaration["source_id"], "before_version": original["knowledge_version"],
        "after_version": after, "knowledge_sha256": knowledge_sha256,
        "protection_backup_id": protection["protection_backup_id"]}
    record = {"operation_id": operation_id, "format_version": 1, "action": "knowledge_restore",
        "library_id": original["library_id"], "node_id": original["node_id"], "source_kind": "home_checkpoint",
        "source_id": source.declaration["source_id"], "source_sha256": source.declaration["image_sha256"],
        "source_binding_sha256": source.declaration_sha256, **protection,
        "before_library_epoch": original["knowledge_version"]["library_epoch"],
        "before_kb_revision": original["knowledge_version"]["kb_revision"],
        "after_library_epoch": after["library_epoch"], "after_kb_revision": after["kb_revision"],
        "original_core_sha256": checkpoint["source_execution_sha256"],
        "root_job_id": None if lineage is None else lineage["root_job_id"],
        "continuation_id": None if lineage is None else lineage["continuation_id"],
        "execution_generation": None if lineage is None else lineage["execution_generation"],
        "before_operational_sha256": original["preserved_sha256"], "after_operational_sha256": original["preserved_sha256"],
        "committed_at": datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")}
    values = {"before_version": original["knowledge_version"], "after_version": after,
        "frontier": original["frontier"], "checkpoint": checkpoint, "request": request, "result": result}
    for name, value in values.items():
        record[name + "_json"] = _json(value)
        record[name + "_sha256"] = _sha(value)
    return schema.validate_record(record)


def apply(*, profile, source_id, expected_digest, operation_id):
    """Explicit CAS apply; repeating an identical UUID never restores twice."""
    profile, operation_id = _profile(profile), _uuid(operation_id)
    request = _request(source_id, expected_digest)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        with _transaction(profile, writable=True, store=store) as con:
            existing = _record(con, operation_id)
            if existing is not None:
                if existing["request_json"] != _json(request):
                    _fail("OPERATION_CONFLICT")
                return {**_reconcile(con, store, existing), "replayed": True}
            _read_intent(profile, operation_id, request)
            with ExitStack() as stack, sources.open_source(con, source_id, profile=profile) as source:
                semantic_pins = _Pins(stack)
                before, observed = _capture(con, store, source, semantic_pins, profile)
                if observed["digest"] != expected_digest:
                    _fail("CAS_MISMATCH")
                _permanent_intent(profile, operation_id, request, before, source, semantic_pins)
                version = before.public()["knowledge_version"]
                if version["library_epoch"] >= schema.MAX_INTEGER or version["kb_revision"] >= schema.MAX_INTEGER:
                    _fail("VERSION_EXHAUSTED")
                with _protection(con, operation_id, profile) as (protection, protection_pins):
                    _write_knowledge(con, source.rows)
                    after = db.knowledge_version(con)
                    revision = max(after["kb_revision"], version["kb_revision"] + 1, source.declaration["source_version"]["kb_revision"] + 1)
                    if revision > schema.MAX_INTEGER:
                        _fail("VERSION_EXHAUSTED")
                    con.execute("UPDATE meta SET value=? WHERE key='library_epoch'", (str(version["library_epoch"] + 1),))
                    con.execute("UPDATE meta SET value=? WHERE key='kb_revision'", (str(revision),))
                    preservation.verify_unchanged(con, before, profile=profile, store=store)
                    actual_rows = sources._read_knowledge(con)
                    record = _build_record(operation_id, source, before, db.knowledge_version(con), protection, request, _sha(actual_rows))
                    con.execute('INSERT INTO ' + schema.TABLE + '(' + ",".join(schema.COLUMNS) + ') VALUES(' + ",".join("?" for _ in schema.COLUMNS) + ')', tuple(record[c] for c in schema.COLUMNS))
                    preservation.verify_restore_committed_transition(con, before, record, profile=profile, store=store)
                    _actual_schema(con)
                    if con.execute("PRAGMA foreign_key_check").fetchone():
                        _fail("FOREIGN_KEY_INVALID")
                    semantic_pins.verify()
                    source.verify()
                    protection_pins.verify()
                    lifecycle.require_guard(profile).verify()
                    con.commit()
                # Only SQL is committed above. Faults here deliberately leave
                # the permanent receipt for the exact original UUID's resume.
                return {**_reconcile(con, store, record), "replayed": False}


def resume(*, profile, operation_id):
    """Resume the saved operation body without reopening a retired source."""
    profile, operation_id = _profile(profile), _uuid(operation_id)
    store = sync_state.StateStore(profile.database.parent, profile=profile)
    with tasks.process_lock(profile=profile) as acquired:
        if not acquired:
            _fail("WORKER_BUSY")
        with _transaction(profile, writable=True, store=store) as con:
            record = _record(con, operation_id)
            if record is None:
                _fail("OPERATION_MISSING", 404)
            return {**_reconcile(con, store, record), "replayed": True}
