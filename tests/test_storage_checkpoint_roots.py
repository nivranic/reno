"""Real synthetic archive bytes; original committed images, no real library."""
from contextlib import contextmanager
import hashlib
import itertools
import json
from pathlib import Path
import sqlite3
from types import SimpleNamespace
import uuid

import pytest

from reno import db, storage_artifacts as artifacts, storage_checkpoint_roots as roots
from reno import storage_gc_contract as contract, storage_lifecycle as lifecycle, sync_checkpoints as checkpoints


STAMP = "2026-10-01T00:00:00.000Z"


def insert(con, table, value):
    con.execute(f"INSERT INTO {table}({','.join(value)}) VALUES({','.join('?' for _ in value)})", tuple(value.values()))


@pytest.fixture
def env(tmp_path):
    profile = lifecycle.StorageProfile(tmp_path / "data/reno.db", tmp_path / "media", tmp_path / "reports", tmp_path / "checkpoints")
    db.initialize(profile.database)
    lifecycle.initialize(profile)
    con = sqlite3.connect(profile.database)
    con.row_factory = sqlite3.Row
    library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    with lifecycle.shared(profile):
        root_ids = {ns: artifacts.root_identity(root, profile=profile)["root_id"] for ns, root in (
            ("checkpoint", profile.checkpoints), ("sync_media", profile.media),
            ("local_report", profile.reports), ("sync_report", profile.reports))}
    value = {"con": con, "profile": profile, "library_id": library, "node_id": str(uuid.uuid4()),
             "member_id": "mem_" + "a" * 64, "root_ids": root_ids}
    yield value
    con.close()


def archive(env, number=0, *, kind="publication", schema=9, owned=True, extra_paths=False, issues=(), unready=False, report=False):
    profile, con = env["profile"], env["con"]
    checkpoint_id, origin_id = str(uuid.uuid4()), str(uuid.uuid4())
    stamp = f"2026-10-01T00:00:{number:02}.000Z"
    version = {"library_id": env["library_id"], "library_epoch": 1, "kb_revision": 0}
    entry = {"checkpoint_id": checkpoint_id, "receiver_version_json": contract.canonical_bytes(version).decode(),
        "format_version": 1 if kind == "publication" else 2, "schema_version": schema,
        "storage_key": checkpoint_id + ".sqlite", "created_at": stamp}
    entry.update({"publication_id": origin_id} if kind == "publication" else {"operation_id": origin_id, "origin_kind": "recovery"})
    content = f"media-{kind}-{number}".encode()
    digest = hashlib.sha256(content).hexdigest()
    video = f"video_{kind}_{number}"
    key = f"sync/{video}/{digest}.mp4"
    media = profile.media / key
    media.parent.mkdir(parents=True, exist_ok=True)
    media.write_bytes(content)
    a = checkpoints._archive_connection()
    try:
        for statement in checkpoints._ddl(schema).values():
            a.execute(statement)
        files = {"video": "media/" + key}
        extra_keys = []
        if extra_paths:
            for role in ("audio", "poster", "frame"):
                payload = (role + str(number)).encode()
                sha = hashlib.sha256(payload).hexdigest()
                extra_key = f"sync/{video}/{sha}.mp4"
                destination = profile.media / extra_key
                destination.write_bytes(payload)
                extra_keys.append(extra_key)
                if role == "frame":
                    insert(a, "frame", {"video_id": video, "frame_id": "f1", "pts_ms": 0, "path": "media/" + extra_key})
                else:
                    files[role] = "media/" + extra_key
        insert(a, "video_asset", {"video_id": video, "status": "processed", "sync_state": "unready" if unready else "ready",
            "content_sha256": digest, "files_json": json.dumps(files)})
        if not unready:
            insert(a, "checkpoint_media", {"video_id": video, "storage_key": key, "sha256": digest,
                                           "byte_size": len(content), "extension": "mp4"})
        report_key = None
        if report:
            body = b"PRIVATE_ARCHIVED_REPORT_BODY"
            report_sha = hashlib.sha256(body).hexdigest()
            report_key = f".sync-versions/prices-{report_sha}.md"
            target = profile.reports / report_key
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(body)
            insert(a, "sync_report", {"name": "prices", "publication_id": origin_id,
                "storage_key": report_key, "sha256": report_sha, "byte_size": len(body), "policy_version": "reports-v3",
                "source_version_json": checkpoints._json(version), "local_version_json": checkpoints._json(version),
                "generated_at": STAMP, "review_events_sha256": "a" * 64})
        manifest = {**entry, "receiver_version": version, "receiver_node_id": env["node_id"],
            "row_counts": {name: a.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in checkpoints.KNOWLEDGE_COLUMNS},
            "issues": list(issues)}
        manifest.pop("receiver_version_json")
        manifest.pop("storage_key")
        insert(a, "checkpoint_manifest", {"slot": 1, "manifest_json": checkpoints._json(manifest)})
        a.commit()
        data = a.serialize()
    finally:
        a.close()
    entry.update(sha256=hashlib.sha256(data).hexdigest(), byte_size=len(data))
    path = profile.checkpoints / entry["storage_key"]
    path.write_bytes(data)
    if kind == "publication":
        insert(con, "sync_publication", {"publication_id": origin_id, "source_library_id": str(uuid.uuid4()),
            "source_node_id": str(uuid.uuid4()), "source_epoch": 1, "source_revision": number,
            "payload_sha256": "b" * 64, "knowledge_sha256": "c" * 64,
            "committed_at": stamp, "local_version_json": checkpoints._json({**version, "kb_revision": number + 1})})
        insert(con, "sync_checkpoint", entry)
    else:
        insert(con, "sync_recovery_operation", {"operation_id": origin_id, "action": "restore",
            "request_sha256": "b" * 64, "request_json": "{}", "result_json": "{}", "created_at": stamp})
        insert(con, "sync_recovery_checkpoint", entry)
    file = None
    if owned:
        # Use the same handle identity as the trusted producer. On Windows,
        # path stat's ctime can be birth time while fstat's ctime is change time.
        with lifecycle.shared(profile):
            file = artifacts._inspect(profile=profile, namespace="checkpoint", root=profile.checkpoints,
                storage_key=entry["storage_key"], sha256=entry["sha256"], byte_size=len(data))
            insert(con, "storage_artifact", {**file, "created_at": stamp, "updated_at": stamp})
    con.commit()
    return {"entry": entry, "path": path, "media": media, "media_key": key, "extra_keys": extra_keys,
            "report_key": report_key, "file": file}


@contextmanager
def image(env):
    con = sqlite3.connect(":memory:")
    try:
        env["con"].backup(con)
        con.execute("PRAGMA query_only=ON")
        con.execute("BEGIN")
        yield con
    finally:
        con.close()


def options(env, **changes):
    return {k: env[k] for k in ("profile", "root_ids", "member_id", "library_id", "node_id")} | changes


def scan(env, **changes):
    with lifecycle.shared(env["profile"]), image(env) as con:
        return roots.scan_image_locked(con, **options(env, **changes))


def retire(env, value, *, state="planned", unowned=False):
    con, entry = env["con"], value["entry"]
    operation_id = str(uuid.uuid4())
    file = None if unowned else {**value["file"], "intent": "delete_if_unreferenced"}
    selected = contract.checkpoint_descriptor(entry, root_id=env["root_ids"]["checkpoint"],
        archive_item_id=file["artifact_id"] if file else None)
    policy = contract.default_policy()
    request = {"format_version": 1, "action": "storage_gc", "operation_id": operation_id,
        "owner": {"member_id": env["member_id"], "library_id": env["library_id"],
            "database_identity": {"device": "1", "inode": "2"}}, "expected_preview_sha256": "a" * 64,
        "expected_roots_sha256": "b" * 64, "members_sha256": "c" * 64, "policy_sha256": contract.digest(policy),
        "retirements": [selected], "files": [file] if file else []}
    contract.validate_request(request, policy=policy)
    insert(con, "storage_gc_operation", {"operation_id": operation_id, "request_sha256": contract.digest(request),
        "preview_sha256": request["expected_preview_sha256"], "roots_sha256": request["expected_roots_sha256"],
        "policy_json": contract.canonical_bytes(policy).decode(), "request_json": contract.canonical_bytes(request).decode(),
        "state": state, "created_at": STAMP, "updated_at": STAMP})
    if file:
        insert(con, "storage_gc_item", {"operation_id": operation_id, **{k: file[k] for k in contract._ITEM_IDENTITY},
            "state": "blocked", "deleted_bytes": 0, "updated_at": STAMP})
    insert(con, "sync_checkpoint_retirement", {"origin_kind": selected["origin_kind"], "checkpoint_id": entry["checkpoint_id"],
        "operation_id": operation_id, "storage_key": entry["storage_key"], "sha256": entry["sha256"],
        "byte_size": entry["byte_size"], "retired_at": STAMP})
    con.commit()


@pytest.mark.parametrize("schema", [7, 9])
def test_closed_archive_all_files_and_frames_are_roots(env, schema):
    value = archive(env, schema=schema, extra_paths=True)
    result = scan(env)
    assert not result.inputs.blockers, result.inputs.blockers
    assert result.nodes[0].recoverable
    assert {r.storage_key for r in result.inputs.references if r.namespace == "sync_media"} == {
        value["media_key"], *value["extra_keys"]}


def test_recent_n_and_latest_recoverable_are_independent_per_kind(env):
    values = [archive(env, i, kind=kind) for kind in ("publication", "recovery") for i in range(3)]
    result = scan(env, policy=contract.default_policy(keep_publication=1, keep_recovery=2))
    assert not result.inputs.blockers, result.inputs.blockers
    assert {(r["origin_kind"], r["checkpoint_id"]) for r in result.proposed_retirements} == {
        ("publication", values[0]["entry"]["checkpoint_id"]), ("publication", values[1]["entry"]["checkpoint_id"]),
        ("recovery", values[3]["entry"]["checkpoint_id"])}
    assert all(r["archive_disposition"] == "journaled" for r in result.proposed_retirements)
    assert len(result.effective_inputs.references) < len(result.inputs.references)


def test_damaged_latest_n_keeps_an_older_actually_recoverable_point(env):
    old = archive(env, 0)
    archive(env, 1)
    newest = archive(env, 2)
    newest["path"].write_bytes(b"damaged")
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    by_id = {node.checkpoint_id: node for node in result.nodes}
    middle = next(n for n in result.nodes if n.checkpoint_id not in {old["entry"]["checkpoint_id"], newest["entry"]["checkpoint_id"]})
    assert middle.retained and "latest_recoverable" in middle.retention_reasons
    assert by_id[newest["entry"]["checkpoint_id"]].retained
    assert [r["checkpoint_id"] for r in result.proposed_retirements] == [old["entry"]["checkpoint_id"]]


def test_all_broken_blocks_class_retirement_and_does_not_block_local_reports(env):
    for i in range(3):
        archive(env, i)["path"].write_bytes(b"broken")
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    assert not result.proposed_retirements
    assert "NO_RECOVERABLE_CHECKPOINT" in {b.code for b in result.inputs.blockers}
    assert "local_report" not in {b.namespace for b in result.inputs.blockers}


@pytest.mark.parametrize("state", ["planned", "running", "blocked", "completed"])
def test_original_image_retirement_can_remove_journaled_edges_without_archive_bytes(env, state):
    value = archive(env)
    retire(env, value, state=state)
    value["path"].unlink()
    result = scan(env)
    assert not result.inputs.blockers, result.inputs.blockers
    assert not result.inputs.references
    assert result.nodes[0].retirement_status == "proven"
    assert not result.nodes[0].logical_recovery_edge


def test_retired_unowned_bytes_remain_held_but_no_longer_promise_restore_dependencies(env):
    value = archive(env, owned=False)
    retire(env, value, unowned=True)
    result = scan(env)
    assert not result.inputs.blockers, result.inputs.blockers
    assert {r.namespace for r in result.inputs.references} == {"checkpoint"}
    assert result.nodes[0].physical_archive_hold and not result.nodes[0].logical_recovery_edge


def test_old_backup_does_not_borrow_current_retirement_or_current_node(env):
    value = archive(env)
    with lifecycle.shared(env["profile"]), image(env) as original:
        retire(env, value)
        old = roots.scan_image_locked(original, **options(env, source_kind="backup", source_id="old", live_retention=False, node_id=None))
    assert old.nodes[0].logical_recovery_edge and not old.nodes[0].recoverable
    assert {r.namespace for r in old.inputs.references} == {"checkpoint", "sync_media"}
    assert not old.inputs.blockers
    assert {"historical_image", "unproven_recovery"} <= set(old.nodes[0].retention_reasons)
    assert not scan(env).inputs.references


def test_unowned_proposal_keeps_physical_hold_in_effective_roots(env):
    old = archive(env, 0, owned=False)
    archive(env, 1)
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    assert result.proposed_retirements[0]["archive_disposition"] == "preserve_unowned"
    assert old["entry"]["storage_key"] in {r.storage_key for r in result.effective_inputs.references}
    assert old["media_key"] not in {r.storage_key for r in result.effective_inputs.references}


def test_context_final_verification_is_independent_of_the_closed_sql_image(env):
    value = archive(env)
    with lifecycle.shared(env["profile"]):
        con = sqlite3.connect(":memory:")
        env["con"].backup(con)
        with roots.capture_image_locked(con, **options(env)) as captured:
            con.close()
            assert captured.verify_source()
        with image(env) as con:
            with pytest.raises(roots.CheckpointRootError) as caught:
                with roots.capture_image_locked(con, **options(env)):
                    value["media"].write_bytes(b"later source mutation")
            assert caught.value.code == "CHECKPOINT_SOURCE_CHANGED" and caught.value.retriable


def test_same_archive_is_decoded_once_but_preserves_both_backup_sources(env, monkeypatch):
    archive(env)
    original, calls = checkpoints._decode, []
    def counted(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)
    monkeypatch.setattr(checkpoints, "_decode", counted)
    with lifecycle.shared(env["profile"]), image(env) as con:
        cache = roots.ArchiveCache()
        with roots.capture_image_locked(con, **options(env, source_kind="backup", source_id="one", live_retention=False, cache=cache)) as a:
            with roots.capture_image_locked(con, **options(env, source_kind="backup", source_id="two", live_retention=False, cache=cache)) as b:
                assert a.nodes[0].source_id != b.nodes[0].source_id
                assert len(calls) == 1


@pytest.mark.parametrize("limits", [roots.CheckpointLimits(max_archives=1), roots.CheckpointLimits(max_rows=2),
    roots.CheckpointLimits(max_hash_bytes=1), roots.CheckpointLimits(max_cell_bytes=16),
    roots.CheckpointLimits(max_depth=1)])
def test_budgets_block_incomplete_graph_instead_of_returning_empty_success(env, limits):
    archive(env, 0)
    archive(env, 1)
    result = scan(env, limits=limits)
    assert result.inputs.blockers and not result.proposed_retirements


def test_unready_files_and_sync_report_dependencies_are_preserved_without_private_body(env):
    value = archive(env, unready=True, extra_paths=True, report=True)
    result = scan(env)
    assert not result.inputs.blockers, result.inputs.blockers
    assert {r.storage_key for r in result.inputs.references} >= {value["media_key"], *value["extra_keys"], value["report_key"]}
    assert b"PRIVATE_ARCHIVED_REPORT_BODY" not in result.inputs.canonical_bytes()


def test_missing_report_keeps_known_reference_and_blocks_recovery_not_unrelated_reports(env):
    value = archive(env, report=True)
    (env["profile"].reports / value["report_key"]).unlink()
    result = scan(env)
    assert not result.nodes[0].recoverable and result.nodes[0].retained
    assert value["report_key"] in {r.storage_key for r in result.inputs.references}
    assert {b.namespace for b in result.inputs.blockers} == {"checkpoint"}


@pytest.mark.parametrize("state", ["held", "resume_pending", "running"])
def test_current_restore_protection_and_held_target_override_retention(env, state):
    target = archive(env, 0)
    archive(env, 1)
    protection = archive(env, 0, kind="recovery")
    archive(env, 1, kind="recovery")
    con = env["con"]
    before = {"library_id": env["library_id"], "library_epoch": 1, "kb_revision": 0}
    after = {**before, "library_epoch": 2}
    request = {"action": "restore", "expected_version": before, "expected_control_revision": 0,
               "checkpoint_id": target["entry"]["checkpoint_id"], "checkpoint_kind": "publication"}
    control = {"generation": 1, "revision": 1, "state": "held", "source_floor": None,
               "stream": {"library_id": env["library_id"], "library_epoch": 2}}
    operation_id = protection["entry"]["operation_id"]
    result = {"operation_id": operation_id, "action": "restore", "checkpoint_id": request["checkpoint_id"],
        "checkpoint_kind": "publication", "before_version": before, "version": after, "control": control,
        "protection_checkpoint_id": protection["entry"]["checkpoint_id"], "committed_at": protection["entry"]["created_at"],
        "replayed": False}
    encoded = contract.canonical_bytes(request).decode()
    con.execute("UPDATE sync_recovery_operation SET request_sha256=?,request_json=?,result_json=? WHERE operation_id=?",
                (hashlib.sha256(encoded.encode()).hexdigest(), encoded, checkpoints._json(result), operation_id))
    con.execute("UPDATE meta SET value='2' WHERE key='library_epoch'")
    insert(con, "sync_recovery_control", {"slot": 1, "state": state, "generation": 1, "revision": 2,
        "source_floor_json": None, "restore_operation_id": operation_id, "updated_at": STAMP})
    con.commit()
    graph = scan(env, policy=contract.default_policy(keep_publication=1, keep_recovery=1))
    assert not graph.inputs.blockers, graph.inputs.blockers
    by_id = {n.checkpoint_id: n for n in graph.nodes}
    assert "active_recovery" in by_id[protection["entry"]["checkpoint_id"]].retention_reasons
    assert ("active_recovery" in by_id[target["entry"]["checkpoint_id"]].retention_reasons) == (state != "running")


@pytest.mark.parametrize("damage", ["missing", "view"])
def test_required_checkpoint_table_cannot_be_absent_or_a_view_in_current_image(env, damage):
    archive(env)
    con = env["con"]
    con.execute("ALTER TABLE sync_checkpoint RENAME TO saved_checkpoint")
    if damage == "view":
        con.execute("CREATE VIEW sync_checkpoint AS SELECT * FROM saved_checkpoint")
    con.commit()
    graph = scan(env)
    assert graph.inputs.blockers and not graph.proposed_retirements


@pytest.mark.parametrize("failure_type", [OSError, FileNotFoundError, ValueError])
def test_caller_cancellation_keeps_exact_exception_through_sql_budgets(env, failure_type):
    archive(env)
    cancellation = failure_type("synthetic original cancellation")
    def cancel():
        raise cancellation
    with pytest.raises(failure_type) as caught:
        scan(env, check_cancelled=cancel)
    assert caught.value is cancellation


def test_deadline_is_a_controlled_incomplete_graph_not_an_uncaught_failure(env, monkeypatch):
    archive(env)
    clock = itertools.count(step=31)
    monkeypatch.setattr(roots, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    graph = scan(env)
    assert graph.inputs.blockers and not graph.proposed_retirements


def test_final_verification_deadline_raises_controlled_limit_error(env, monkeypatch):
    archive(env)
    with lifecycle.shared(env["profile"]), image(env) as con:
        with roots.capture_image_locked(con, **options(env)) as captured:
            with monkeypatch.context() as final_clock:
                clock = itertools.count(step=31)
                final_clock.setattr(roots, "time", SimpleNamespace(monotonic=lambda: next(clock)))
                with pytest.raises(roots.CheckpointRootError) as caught:
                    captured.verify_source()
                assert caught.value.code == "CHECKPOINT_DEADLINE_LIMIT"


def test_optional_decode_hook_exception_closes_private_connection_and_is_preserved(env, monkeypatch):
    value = archive(env)
    observed, original = [], checkpoints._archive_connection
    def connection():
        con = original()
        observed.append(con)
        return con
    monkeypatch.setattr(checkpoints, "_archive_connection", connection)
    cancellation = OSError("cancel before untrusted SQL")
    def configure(_con):
        raise cancellation
    with pytest.raises(OSError) as caught:
        checkpoints._decode(value["path"].read_bytes(), value["entry"], configure_read=configure)
    assert caught.value is cancellation
    with pytest.raises(sqlite3.ProgrammingError):
        observed[0].execute("SELECT 1")


def test_equal_created_times_use_checkpoint_id_as_deterministic_tie_breaker(env):
    first, second = archive(env, 0), archive(env, 0)
    graph = scan(env, policy=contract.default_policy(keep_publication=1))
    identifiers = sorted([first["entry"]["checkpoint_id"], second["entry"]["checkpoint_id"]])
    assert [d["checkpoint_id"] for d in graph.proposed_retirements] == identifiers[:1]
    assert "latest_n" in next(n.retention_reasons for n in graph.nodes if n.checkpoint_id == identifiers[-1])


@pytest.mark.parametrize("damage", ["unknown_table", "duplicate_manifest_key", "boolean_manifest_integer"])
def test_closed_archive_schema_and_unambiguous_manifest_are_required(env, damage):
    value = archive(env)
    with sqlite3.connect(value["path"]) as archive_con:
        if damage == "unknown_table":
            archive_con.execute("CREATE TABLE unsupported_child_checkpoint(reference TEXT)")
        else:
            raw = archive_con.execute("SELECT manifest_json FROM checkpoint_manifest").fetchone()[0]
            if damage == "duplicate_manifest_key":
                changed = raw.replace('"issues":[]', '"issues":[],"issues":[]')
            else:
                changed = raw.replace('"format_version":1', '"format_version":true')
            assert changed != raw
            archive_con.execute("UPDATE checkpoint_manifest SET manifest_json=?", (changed,))
    data = value["path"].read_bytes()
    sha = hashlib.sha256(data).hexdigest()
    con = env["con"]
    con.execute("UPDATE sync_checkpoint SET sha256=?,byte_size=? WHERE checkpoint_id=?",
                (sha, len(data), value["entry"]["checkpoint_id"]))
    con.execute("UPDATE storage_artifact SET sha256=?,byte_size=? WHERE artifact_id=?",
                (sha, len(data), value["file"]["artifact_id"]))
    con.commit()
    graph = scan(env, policy=contract.default_policy(keep_publication=1))
    assert not graph.nodes[0].valid and not graph.proposed_retirements
    assert {b.namespace for b in graph.inputs.blockers} == {"checkpoint", "sync_media", "sync_report"}


def test_wrong_physical_root_identity_is_rejected_before_archive_reads(env):
    archive(env)
    wrong = {**env["root_ids"], "sync_media": "0" * 64}
    with pytest.raises(roots.CheckpointRootError) as caught:
        scan(env, root_ids=wrong)
    assert caught.value.code == "CHECKPOINT_ROOT_BINDING_MISMATCH"


def test_no_proposals_means_base_and_effective_inputs_are_identical(env):
    archive(env)
    graph = scan(env)
    assert not graph.proposed_retirements
    assert graph.inputs.digest == graph.effective_inputs.digest
