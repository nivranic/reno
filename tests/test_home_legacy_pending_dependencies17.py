"""Actual old16 mapped work/outbox capsules, with physical source seams offline.

The production resolver planner, source prepare/run, budget ledger, original
worker, SourceMap writer, outbox writer and pending capsule CAS remain real.
Only resolver responses, local child media and the existing pipeline results
are synthetic. Negative cases deliberately damage disposable original files;
no positive State, capsule, receipt or execution permission is fabricated.
"""
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import socket
import sqlite3
import subprocess
import sys
import traceback
from types import SimpleNamespace

import httpx
import pytest

from reno import config, llm, report, source_budget, source_resolution, source_resolution_contract
from reno import source_runner, storage_admin, storage_lifecycle as lifecycle
from reno import storage_legacy_pending_state17 as pending, sync_sources, sync_state
from reno import storage_legacy_pending_dependencies17 as dependencies
from reno.budget_contract import DEFER_REASONS
from tests import test_sync_source_legacy_pending_intake17 as actual_pending
from tests.test_storage_legacy_pending_state17 import original_inputs

_REAL_POPEN = subprocess.Popen
LEAF = "https://www.bilibili.com/video/BVfake123"
METADATA = {"id": "BVfake123", "title": "Synthetic offline mapped original", "extractor": "BiliBili",
    "webpage_url": LEAF, "duration": 1, "formats": [
        {"format_id": "offline_video", "url": "https://media.example.test/video.mp4", "protocol": "https",
            "ext": "mp4", "vcodec": "avc1", "acodec": "none", "height": 480},
        {"format_id": "offline_audio", "url": "https://media.example.test/audio.m4a", "protocol": "https",
            "ext": "m4a", "vcodec": "none", "acodec": "mp4a"}]}


def offline_physical_sources(monkeypatch):
    requests, children = [], []

    def forbidden(*args, **kwargs):
        pytest.fail("Unexpected physical network or model call in the isolated mapped producer")

    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket.socket, "connect_ex", forbidden)
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", forbidden)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", forbidden)
    monkeypatch.setattr(llm, "chat", forbidden)
    monkeypatch.setattr(llm, "vision", forbidden)
    monkeypatch.setattr(source_budget, "_cookies", lambda plan, explicit: None)

    def resolution(prepared, *, late_sink=None):
        plan = prepared.plan
        requests.append(plan)
        stamp = datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")
        if plan.phase == "dns":
            return source_resolution_contract.ResolutionResult(plan.request_sha256, "dns", True, "completed",
                addresses=("8.8.8.8",), observed_at=stamp)
        assert plan.phase == "headers" and plan.current_url in {plan.submitted_url, LEAF}
        redirected = plan.current_url == plan.submitted_url
        return source_resolution_contract.ResolutionResult(plan.request_sha256, "headers", True, "completed",
            headers_complete=True, transport_closed=True, status=302 if redirected else 200,
            next_url=LEAF if redirected else None, observed_at=stamp)

    monkeypatch.setattr(source_resolution, "run", resolution)
    script = """import json, os, sys
from pathlib import Path
arguments = json.loads(sys.argv[1])
if '--dump-single-json' in arguments:
    sys.stdout.write(METADATA_TEXT)
elif '--output' in arguments:
    target = Path(arguments[arguments.index('--output') + 1])
    original_part = Path(str(target) + '.part')
    assert original_part.is_file()
    original_part.write_bytes(b'isolated actual source bytes: ' + target.name.encode('ascii'))
    os.rename(original_part, target)
else:
    raise AssertionError('Unexpected physical source plan')
""".replace("METADATA_TEXT", repr(json.dumps(METADATA)))

    def local_child(arguments, **kwargs):
        assert list(arguments[:4]) == [sys.executable, "-I", "-m", "yt_dlp"]
        assert kwargs["shell"] is False and kwargs["stdin"] == subprocess.DEVNULL
        children.append((tuple(arguments), dict(kwargs)))
        # Preserve production Job/process-group, drain and artifact controls;
        # substitute only the executable body with this closed local script.
        return _REAL_POPEN([sys.executable, "-I", "-c", script, json.dumps(list(arguments))], **kwargs)

    # yt-dlp imports subclass the stdlib Popen class. Preserve that actual
    # module/class globally; only this runner receives the local spawn seam.
    runner_process = SimpleNamespace(**vars(subprocess))
    runner_process.Popen = local_child
    monkeypatch.setattr(source_runner, "subprocess", runner_process)
    return requests, children


def closed_process_diagnostic(error, cycle, requests, children):
    """Read actual failure facts; never print URLs, paths, bodies or secrets."""
    chain, seen, current = [], set(), error
    watched = {"sync_agent.py", "source_resolution_budget.py", "source_budget.py", "source_runner.py",
        "source_contract.py", "sync_source_execution.py", "test_home_legacy_pending_dependencies17.py"}
    while current is not None and id(current) not in seen and len(chain) < 8:
        seen.add(id(current))
        item = {"class": type(current).__name__}
        for key in ("code", "reason"):
            value = getattr(current, key, None)
            if (type(value) is str and value.isascii() and len(value) <= 96
                    and value.replace("_", "").isalnum()
                    and (value in DEFER_REASONS if key == "reason" else value == value.upper())):
                item[key] = value
        if type(current) is KeyError and len(current.args) == 1:
            key = current.args[0]
            if type(key) is str and key.isascii() and key.isidentifier() and len(key) <= 64:
                item["key"] = key
        frames = [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
            for frame in traceback.extract_tb(current.__traceback__) if Path(frame.filename).name in watched]
        if frames:
            item["frames"] = frames[-6:]
        chain.append(item)
        current = current.__context__
    with storage_admin.connection(cycle.storage_profile) as connection:
        rows = [dict(row) for row in connection.execute(
            "SELECT o.kind,a.state,a.outcome FROM budget_attempt a JOIN budget_operation o USING(operation_id) "
            "ORDER BY a.dispatched_at,a.ordinal LIMIT 16")]
    phases = ["metadata" if "--dump-single-json" in arguments else
        "video" if arguments[arguments.index("-f") + 1] == "bv*" else "audio"
        for arguments, _kwargs in children]
    specification = importlib.util.find_spec("yt_dlp")
    origin_in_interpreter = (specification is not None and specification.origin is not None
        and Path(specification.origin).resolve().is_relative_to(Path(sys.prefix).resolve()))
    return {"exception_chain": chain, "resolver_adapter_phases": [plan.phase for plan in requests],
        "planned_source_child_phases": phases, "actual_budget_rows": rows,
        "physical_prepare": {"media_root_exists": cycle.storage_profile.media.is_dir(),
            "yt_dlp_available": specification is not None, "yt_dlp_origin_in_interpreter": origin_in_interpreter}}


@pytest.fixture
def mapped_cycle(tmp_path, monkeypatch, request):
    requests, children = offline_physical_sources(monkeypatch)
    original_bind, generated = actual_pending.bind, []

    def bind_and_freeze(cycle):
        proof = original_bind(cycle)
        assert cycle.source["handshake"]["migration_version"] == 16
        assert cycle.store.read()["format_version"] == 3
        assert "source_authority" not in cycle.store.read()
        config.set_local({"budget_enabled": True})
        try:
            claims = cycle.process_claim()
        except Exception as error:
            diagnostic = closed_process_diagnostic(error, cycle, requests, children)
            raise AssertionError("offline mapped process diagnostic: " + json.dumps(diagnostic, sort_keys=True)) from None
        assert len(claims) == 1
        work = cycle.store.read()["work"]
        assert work["phase"] == "judged" and work["source_mapping_generation"] and work["source_mapping_id"]
        mapping = cycle.home_data / "sync-sources" / "v2" / (work["source_mapping_generation"] + ".json")
        assert mapping.is_file()
        document = json.loads(mapping.read_bytes())
        assert document["mapping_generation"] == work["source_mapping_generation"]
        assert document["origin"] == claims[0]["origin"]
        report.run_all(profile=cycle.storage_profile)
        cycle.freeze(claims)
        metadata = cycle.store.read()["pending_publication"]
        outbox = cycle.home_data / sync_state._OUTBOX / (metadata["publication_id"] + ".json")
        assert outbox.is_file()
        raw = outbox.read_bytes()
        assert (len(raw), hashlib.sha256(raw).hexdigest()) == (metadata["byte_size"], metadata["sha256"])
        assert cycle.store.read()["work"] == work and cycle.store.read()["format_version"] == 3
        generated.append((mapping, outbox, deepcopy(work), deepcopy(metadata)))
        return proof

    monkeypatch.setattr(actual_pending, "bind", bind_and_freeze)
    original = actual_pending.real_cycle.__wrapped__(tmp_path, monkeypatch, request)
    value = next(original)
    assert len(generated) == 1
    value.pending_dependency_files = generated[0][:2]
    value.original_mapped_work, value.original_pending_metadata = generated[0][2:]
    value.offline_resolution_requests, value.offline_source_children = requests, children
    try:
        yield value
    finally:
        try:
            next(original)
        except StopIteration:
            pass


@pytest.fixture
def mapped_completed(mapped_cycle):
    value = mapped_cycle
    intent, arguments, receipt, _answer = actual_pending.received(value)
    with value.store.reference_operation():
        actual_pending.actual_call(actual_pending.intake.reconcile_original_pending17_locked, **arguments)
    path = actual_pending.capsules.path(value.profile, intent["record"]["record_id"])
    marker = path.with_name(intent["record"]["record_id"] + ".applied.json")
    assert path.is_file() and marker.is_file()
    document = json.loads(path.read_bytes())
    for phase in ("before", "after"):
        state = json.loads(document[phase + "_json"])
        assert state["work"] == value.original_mapped_work
        assert state["pending_publication"] == value.original_pending_metadata
    return value, intent, receipt, path, marker


@contextmanager
def observed_dependencies(value, **kwargs):
    with lifecycle.shared(value.profile), pending.capture_locked(value.profile) as original:
        assert not original.unknown
        with dependencies.capture_locked(value.profile, pending_capture=original, **kwargs) as observed:
            yield original, observed


def original_dependency_files(value):
    return tuple((path.read_bytes(), path.stat().st_mtime_ns) for path in value.pending_dependency_files)


def test_actual_old16_worker_creates_complete_work_map_outbox_and_original_capsule(mapped_completed):
    value, intent, receipt, path, marker = mapped_completed
    assert [plan.phase for plan in value.offline_resolution_requests] == ["dns", "headers", "dns", "headers"]
    children = value.offline_source_children
    assert len(children) == 3
    assert "--dump-single-json" in children[0][0]
    assert children[1][0][children[1][0].index("-f") + 1] == "bv*"
    assert children[2][0][children[2][0].index("-f") + 1] == "ba/bestaudio"
    with storage_admin.connection(value.profile) as connection:
        attempts = [dict(row) for row in connection.execute(
            "SELECT o.kind,a.state,a.outcome,a.download_segment FROM budget_attempt a "
            "JOIN budget_operation o USING(operation_id) ORDER BY a.dispatched_at,a.ordinal")]
    assert len(attempts) == 7
    assert all(row["state"] == "settled" for row in attempts)
    audio = [row for row in attempts if row["kind"] == "download" and row["download_segment"] == "audio"]
    assert len(audio) == 1 and audio[0]["outcome"] == "full_result"
    steps = [row for row in attempts if row is not audio[0]]
    assert len(steps) == 6 and all(row["outcome"] == "source_step_complete" for row in steps)
    assert {row["kind"] for row in attempts} == {"source_resolution", "source_metadata", "download"}
    before, files = original_inputs(value, path, marker), original_dependency_files(value)
    with observed_dependencies(value) as (original, observed):
        assert observed.summary["coverage"] == "complete" and not observed.unknown
        assert observed.summary["selected_count"] == observed.summary["file_count"] == 2
        assert observed.summary["reference_count"] == 4 and observed.summary["absence_count"] == 0
        assert observed.summary["capsule_state_set_sha256"] == original.summary["state_set_sha256"]
        records = {record.role: record for record in observed.records}
        assert set(records) == {dependencies.ROLE_MAPPING_V2, dependencies.ROLE_OUTBOX}
        assert len(observed._reader.files) == 2 and all(len(row) == 6 and row[2] is not None for row in observed._reader.files)
        for directory, name, held, before_identity, opened_identity, digest in observed._reader.files:
            key = (directory.path / name).relative_to(value.profile.database.parent).as_posix()
            record = next(record for record in records.values() if record.relative_key == key)
            assert before_identity == dependencies.source.file_signature(directory.entry_stat(name))
            assert opened_identity == dependencies.source.file_signature(os.fstat(held.fileno()))
            assert before_identity[:4] == opened_identity[:4]
            assert digest.hex() == record.sha256 and before_identity[2] == record.byte_size
        for record in records.values():
            selected = value.profile.database.parent / record.relative_key
            assert selected in value.pending_dependency_files and record.raw == selected.read_bytes()
            assert (record.sha256, record.byte_size) == (hashlib.sha256(record.raw).hexdigest(), len(record.raw))
        states = {(image["request_id"], image["phase"]): json.loads(image["state_json"]) for image in original.state_images}
        for requirement in observed.requirements:
            state = states[requirement.request_id, requirement.phase]
            assert requirement.request_id == intent["record"]["record_id"]
            if requirement.role == dependencies.ROLE_MAPPING_V2:
                assert requirement.expectation["inputs"] == state["work"]["source_mapping_inputs"]
                assert requirement.expectation["mapping_id"] == state["work"]["source_mapping_id"]
                assert requirement.expectation["mapping_generation"] == state["work"]["source_mapping_generation"]
            else:
                assert requirement.expectation["original"] == state["pending_publication"]
            dependencies.validate_dependency(records[requirement.role], requirement)
        public = observed.public_summary()
        assert set(public) == {"summary", "references", "unknown"}
        encoded = json.dumps(public)
        assert "state_json" not in encoded and "expectation_json" not in encoded and "source_mapping_inputs" not in encoded
        assert value.core["binding"]["target_url"] not in encoded
        observed.verify_for_commit()
        observed.verify_source()
    assert original_inputs(value, path, marker) == before
    assert original_dependency_files(value) == files


@pytest.mark.parametrize("selected", ["mapping", "outbox"])
def test_actual_capsule_missing_selected_dependency_remains_scoped_unknown(mapped_completed, selected):
    value, intent, _receipt, path, marker = mapped_completed
    before = original_inputs(value, path, marker)
    target = value.pending_dependency_files[0 if selected == "mapping" else 1]
    missing_key = target.relative_to(value.profile.database.parent).as_posix()
    target.unlink()  # Explicit negative deletion in the isolated original selection.
    with observed_dependencies(value) as (_original, observed):
        assert observed.summary["coverage"] == "unknown"
        assert observed.summary["selected_count"] == 2 and observed.summary["file_count"] == 1
        unknown = observed.public_summary()["unknown"]
        missing = [row for row in unknown if row["relative_key"] == missing_key]
        assert {row["phase"] for row in missing} == {"before", "after"}
        assert all(row["request_id"] == intent["record"]["record_id"] and row["code"] == "DEPENDENCY_MISSING" for row in missing)
        assert all(record.relative_key != missing_key for record in observed.records)
        observed.verify_source()
    assert original_inputs(value, path, marker) == before
    assert not target.exists()


@pytest.mark.parametrize("selected", ["mapping", "outbox"])
def test_actual_capsule_changed_selected_raw_never_proves_original_dependency(mapped_completed, selected):
    value, intent, _receipt, path, marker = mapped_completed
    before = original_inputs(value, path, marker)
    target = value.pending_dependency_files[0 if selected == "mapping" else 1]
    original = json.loads(target.read_bytes())
    if selected == "mapping":
        original["origin"]["media_sha256"] = "f" * 64
        code = "MAPPING_UNPROVEN"
    else:
        original["snapshot"]["publication_id"] = "00000000-0000-4000-8000-000000000001"
        code = "OUTBOX_UNPROVEN"
    target.write_bytes(dependencies._json(original))  # Explicit negative raw corruption.
    with observed_dependencies(value) as (_original, observed):
        assert observed.summary["coverage"] == "unknown" and observed.summary["file_count"] == 2
        changed = [row for row in observed.unknown if row["relative_key"] == target.relative_to(value.profile.database.parent).as_posix()]
        assert {row["phase"] for row in changed} == {"before", "after"}
        assert all(row["request_id"] == intent["record"]["record_id"] and row["code"] == code for row in changed)
        observed.verify_source()
    assert original_inputs(value, path, marker) == before


def test_actual_mapping_inputs_cannot_be_replaced_by_partial_or_missing_copied_selection(mapped_completed):
    value, _intent, _receipt, path, marker = mapped_completed
    before, files = original_inputs(value, path, marker), original_dependency_files(value)
    with observed_dependencies(value) as (_original, observed):
        record = next(row for row in observed.records if row.role == dependencies.ROLE_MAPPING_V2)
        requirement = next(row for row in observed.requirements if row.role == dependencies.ROLE_MAPPING_V2)
        with pytest.raises(dependencies.PendingDependencyError) as missing:
            dependencies.validate_dependency(None, requirement)
        assert missing.value.code == "LEGACY_PENDING_DEPENDENCY17_RECORD_INVALID"
        damaged = requirement.expectation
        damaged["inputs"]["media_sha256"] = "f" * 64
        with pytest.raises(dependencies.PendingDependencyError) as changed:
            # Deliberate detached negative expectation; never used as an
            # actual capture or a positive physical selection capability.
            dependencies.validate_dependency(record, replace(requirement,
                expectation_json=dependencies._json(damaged).decode("utf-8")))
        assert changed.value.code == "LEGACY_PENDING_DEPENDENCY17_MAPPING_UNPROVEN"
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == files


def test_actual_valid_v1_map_sibling_cannot_replace_original_v2_capsule_selection(mapped_completed):
    value, _intent, _receipt, path, marker = mapped_completed
    before, files = original_inputs(value, path, marker), original_dependency_files(value)
    work = value.original_mapped_work
    inputs = work["source_mapping_inputs"]
    with lifecycle.shared(value.profile):
        with storage_admin.connection(value.profile) as connection:
            asset = dict(connection.execute("SELECT * FROM video_asset WHERE video_id=?", (work["video_id"],)).fetchone())
        mapping = sync_sources.SourceMap(value.profile.database.parent, profile=value.profile,
            source_library_id=value.source["library_id"], source_node_id=value.source["node_id"])
        # Actual public v1 file producer creates an unreferenced sibling; no
        # State/capsule field is edited to invent a legacy selection positive.
        origin = mapping.record(inputs["resolution"], extracted_video_id=inputs["extracted_video_id"], asset=asset)
    legacy_path = mapping.root / (origin["mapping_id"] + ".json")
    assert legacy_path.is_file()
    legacy_key = legacy_path.relative_to(value.profile.database.parent).as_posix()
    keys = [legacy_key, *(selected.relative_to(value.profile.database.parent).as_posix()
        for selected in value.pending_dependency_files)]
    assert {dependencies.role_for_key(key) for key in keys} == {
        dependencies.ROLE_MAPPING_V1, dependencies.ROLE_MAPPING_V2, dependencies.ROLE_OUTBOX}
    with observed_dependencies(value) as (_original, observed):
        assert observed.summary["coverage"] == "complete" and observed.summary["file_count"] == 2
        assert all(record.relative_key != legacy_key for record in observed.records)
        assert all(requirement.relative_key != legacy_key for requirement in observed.requirements)
        observed.verify_source()
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == files


def test_actual_dependency_capture_cannot_substitute_live_state_sql_or_new_file_writers(mapped_completed, monkeypatch):
    value, _intent, _receipt, path, marker = mapped_completed
    before, files = original_inputs(value, path, marker), original_dependency_files(value)
    with monkeypatch.context() as scoped:
        def forbidden(*args, **kwargs):
            pytest.fail("Dependency capture consulted live State/SQL or a file producer")
        scoped.setattr(sqlite3, "connect", forbidden)
        scoped.setattr(sync_state.StateStore, "read", forbidden)
        scoped.setattr(sync_state.StateStore, "read_observation", forbidden)
        scoped.setattr(sync_state, "_write", forbidden)
        scoped.setattr(sync_sources.SourceMap, "record", forbidden)
        with observed_dependencies(value) as (_original, observed):
            assert observed.summary["coverage"] == "complete" and observed.summary["file_count"] == 2
            observed.verify_for_commit()
            observed.verify_source()
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == files


@pytest.mark.parametrize("limits,code", [
    (dependencies.CaptureLimits(max_files=1), "FILE_COUNT_LIMIT"),
    (dependencies.CaptureLimits(max_total_bytes=1), "TOTAL_BYTES_LIMIT"),
])
def test_actual_dependency_count_and_byte_limits_stop_before_any_original_changes(mapped_completed, limits, code):
    value, _intent, _receipt, path, marker = mapped_completed
    before, files = original_inputs(value, path, marker), original_dependency_files(value)
    with pytest.raises(dependencies.PendingDependencyError) as raised:
        with observed_dependencies(value, limits=limits):
            pytest.fail("The actual two-file selection exceeded its explicit capture budget")
    assert raised.value.code == "LEGACY_PENDING_DEPENDENCY17_" + code
    assert original_inputs(value, path, marker) == before and original_dependency_files(value) == files
