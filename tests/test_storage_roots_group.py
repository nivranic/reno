"""Complete-member previews on isolated real schema10 libraries only."""
from contextlib import contextmanager
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid

import pytest

from reno import db, storage_artifacts as artifacts, storage_gc_contract as contract
from reno import storage_lifecycle as lifecycle, storage_members as members
from reno import storage_roots as roots, storage_backups as backups
from tests.test_storage_checkpoint_roots import archive


@pytest.fixture
def libraries(tmp_path):
    opened = []
    def make(name="a", *, media=None, reports=None, checkpoints=None, database=None):
        base = tmp_path / name
        profile = lifecycle.StorageProfile(database or base / "data/reno.db", media or base / "media",
                                           reports or base / "reports", checkpoints or base / "checkpoints")
        db.initialize(profile.database)
        lifecycle.initialize(profile)
        con = sqlite3.connect(profile.database)
        con.row_factory = sqlite3.Row
        opened.append(con)
        library_id = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        node_id = str(uuid.uuid4())
        (profile.database.parent / "node-identity.json").write_text(json.dumps({"instance_id": node_id}), encoding="utf-8")
        with lifecycle.shared(profile):
            root_ids = {ns: artifacts.root_identity(getattr(profile, roots._ROOT_ROLE[ns]), profile=profile)["root_id"]
                        for ns in roots.NAMESPACES}
        return {"profile": profile, "con": con, "library_id": library_id, "node_id": node_id,
                "member_id": "mem_" + "a" * 64, "root_ids": root_ids}
    yield make
    for con in opened:
        con.close()


def enroll(*environments, complete=True):
    profiles = [env["profile"] for env in environments]
    declarations = [{"root": root, "databases": [str(p.database) for p in profiles if root in members._root_map([p])]}
                    for root in members._root_map(profiles)] if complete else []
    preview = members.membership_preview(profiles, declarations=declarations)
    assert not preview["blocked"], preview
    members.enroll(profiles, expected_digest=preview["digest"], declarations=declarations, operation_id=str(uuid.uuid4()))
    for env in environments:
        with lifecycle.shared(env["profile"]):
            env["member_id"] = members.require_enrolled(env["profile"])["member_id"]


def registered(env, namespace="sync_media", content=b"unused media", *, video="video", key=None):
    profile = env["profile"]
    sha = hashlib.sha256(content).hexdigest()
    if key is None:
        key = {"sync_media": f"sync/{video}/{sha}.mp4", "local_report": f".versions/prices-{uuid.uuid4().hex}.md",
               "sync_report": f".sync-versions/prices-{sha}.md", "checkpoint": str(uuid.uuid4()) + ".sqlite"}[namespace]
    root = getattr(profile, roots._ROOT_ROLE[namespace])
    path = root / key
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    with lifecycle.shared(profile):
        env["con"].execute("BEGIN IMMEDIATE")
        value = artifacts.register(env["con"], profile=profile, namespace=namespace, root=root, storage_key=key,
                                   sha256=sha, byte_size=len(content))
    env["con"].commit()
    return path, value


@contextmanager
def captured(*envs, **kwargs):
    profiles = [env["profile"] for env in envs]
    with members._group(profiles, False) as (expanded, _, _):
        with roots.capture_locked(expanded, owner_profile=profiles[0], **kwargs) as capture:
            yield capture


def scan(*envs, **kwargs):
    with captured(*envs, **kwargs) as capture:
        return capture.scan


def statuses(result):
    return {file["artifact_id"]: file["status"] for file in result.files}


def test_actual_unreferenced_candidates_and_unknown_files_are_separate(libraries):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    unknown = path.with_name("b" * 64 + ".mp4")
    unknown.write_bytes(b"unregistered, preserve")
    before = path.read_bytes(), unknown.read_bytes(), env["con"].total_changes
    result = scan(env)
    assert not result.effective_inputs.blockers
    assert result.eligible_artifacts == ( {key: item[key] for key in roots._ARTIFACT_FIELDS}, )
    public = roots.public_preview(result)
    assert public["candidate_bytes"] == len(before[0])
    assert next(n for n in public["namespaces"] if n["namespace"] == "sync_media")["unknown_count"] == 1
    assert before == (path.read_bytes(), unknown.read_bytes(), env["con"].total_changes)


@pytest.mark.parametrize("status", ["ready", "pending"])
def test_all_current_file_fields_and_frames_hold_registered_files(libraries, status):
    env = libraries()
    enroll(env)
    items = [registered(env, content=role.encode()) for role in ("video", "audio", "poster", "frame")]
    files = {role: "media/" + item["storage_key"] for role, (_, item) in zip(("video", "audio", "poster"), items)}
    env["con"].execute("INSERT INTO video_asset(video_id,files_json,sync_state) VALUES('video',?,?)", (json.dumps(files), status))
    env["con"].execute("INSERT INTO frame(video_id,frame_id,path) VALUES('video','f',?)", ("media/" + items[-1][1]["storage_key"],))
    env["con"].commit()
    result = scan(env)
    assert set(statuses(result).values()) == {"referenced"}
    assert not result.eligible_artifacts


def test_foreign_member_reference_holds_owner_file_primary_only(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    _, item = registered(a)
    _, foreign = registered(b, "local_report")
    b["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('b',?)", (json.dumps({"video": "media/" + item["storage_key"]}),))
    b["con"].commit()
    result = scan(a)
    assert len(result.members_binding["members"]) == 2
    assert statuses(result)[item["artifact_id"]] == "referenced"
    assert foreign["artifact_id"] not in statuses(result)


def test_unreadable_shared_member_blocks_media_but_independent_report_is_candidate(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    _, media = registered(a)
    _, report = registered(a, "local_report")
    b["con"].close()
    b["profile"].database.unlink()
    result = scan(a)
    assert statuses(result)[media["artifact_id"]] == "blocked"
    assert statuses(result)[report["artifact_id"]] == "candidate"
    assert result.eligible_artifacts[0]["artifact_id"] == report["artifact_id"]


def test_unknown_membership_has_no_delete_candidates(libraries):
    env = libraries()
    enroll(env, complete=False)
    _, item = registered(env)
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] == "blocked"
    assert "membership_unknown" in {b.code for b in result.effective_inputs.blockers}


def test_nested_or_incomplete_guard_cannot_claim_group_scan(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    with lifecycle.shared(a["profile"]):
        with pytest.raises(roots.RootError, match="ROOTS_GROUP_MUST_BE_ACQUIRED_FIRST"):
            roots.preview([a["profile"]])
        with pytest.raises((roots.RootError, lifecycle.StorageLifecycleError, members.MembershipError)):
            roots.scan_locked([a["profile"]])


def test_heartbeat_changes_raw_image_without_expiring_semantic_preview(libraries):
    env = libraries()
    enroll(env)
    registered(env)
    env["con"].execute("INSERT INTO job(kind,status,payload_json) VALUES('judge','pending','{}')")
    env["con"].commit()
    before = scan(env)
    env["con"].execute("UPDATE job SET lease_until='display1',updated_at='display2'")
    env["con"].commit()
    after = scan(env)
    assert (before.roots_sha256, before.preview_sha256) == (after.roots_sha256, after.preview_sha256)


def test_wal_recreation_from_only_heartbeat_keeps_semantic_digest(libraries):
    env = libraries()
    enroll(env)
    registered(env)
    env["con"].execute("INSERT INTO job(kind,status,payload_json) VALUES('judge','pending','{}')")
    env["con"].commit()
    before = scan(env)
    env["con"].close()
    con = sqlite3.connect(env["profile"].database)
    try:
        con.execute("UPDATE job SET lease_until='new-only-heartbeat',updated_at='new-display'")
        con.commit()
    finally:
        con.close()
    after = scan(env)
    assert before.preview_sha256 == after.preview_sha256


def test_pending_job_protects_every_registered_version(libraries):
    env = libraries()
    enroll(env)
    for content in (b"version1", b"version2"):
        registered(env, video="pending", content=content)
    env["con"].execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES('pending','process','running','{}')")
    env["con"].commit()
    assert set(statuses(scan(env)).values()) == {"referenced"}


def test_home_source_mapping_holds_all_extensions_without_reading_private_urls(libraries):
    from tests.test_storage_home_roots import source_map, state, write
    env = libraries()
    enroll(env)
    _, item = registered(env, video="BV1Test")
    registered(env, video="BV1Test", key=item["storage_key"].removesuffix(".mp4") + ".webm")
    home_env = (env["profile"], {"library_id": env["library_id"], "node_id": env["node_id"]})
    source_map(home_env, video="BV1Test", sha=item["sha256"])
    value = state(home_env)
    write(home_env, "sync-state.json", value)
    before = scan(env)
    assert set(statuses(before).values()) == {"referenced"}
    value["status"]["updated_at"] = "2026-10-01T01:00:00Z"
    write(home_env, "sync-state.json", value)
    after = scan(env)
    assert before.preview_sha256 == after.preview_sha256
    assert "PRIVATE_SIGNATURE" not in json.dumps(roots.public_preview(after))


def test_legacy_upload_pin_keeps_file_without_current_asset(libraries):
    from reno.sync_protocol import _json_bytes
    env = libraries()
    enroll(env)
    _, item = registered(env, video="legacy")
    descriptor = {"video_id": "legacy", "sha256": item["sha256"], "byte_size": item["byte_size"], "extension": "mp4"}
    identity = {"source_library_id": env["library_id"], "source_node_id": env["node_id"], "descriptor": descriptor}
    digest = hashlib.sha256(_json_bytes(identity)).hexdigest()
    row = {"pin_id": "legacy_" + digest, "kind": "legacy", "source_library_id": env["library_id"],
           "source_node_id": env["node_id"], "descriptor_json": json.dumps(descriptor), "intent_sha256": digest,
           "state": "legacy_hold", "created_at": "2026-10-01T00:00:00Z", "updated_at": "2026-10-01T00:00:00Z"}
    env["con"].execute(f"INSERT INTO sync_upload_pin({','.join(row)}) VALUES({','.join('?' for _ in row)})", tuple(row.values()))
    env["con"].commit()
    assert statuses(scan(env))[item["artifact_id"]] == "referenced"


def test_policy_subset_reports_other_files_as_out_of_policy(libraries):
    env = libraries()
    enroll(env)
    _, media = registered(env)
    _, report = registered(env, "local_report")
    result = scan(env, policy=contract.default_policy(namespaces=["local_report"]))
    assert statuses(result)[media["artifact_id"]] == "out_of_policy"
    assert statuses(result)[report["artifact_id"]] == "candidate"


def test_member_join_between_discovery_and_lock_restarts_entire_group(libraries, monkeypatch):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a)
    original, joined = members._discover, [False]
    def discover(profiles):
        result = original(profiles)
        if not joined[0]:
            joined[0] = True
            enroll(a, b)
        return result
    monkeypatch.setattr(members, "_discover", discover)
    result = roots.preview([a["profile"]])
    assert joined[0] and result["member_count"] == 2


@pytest.mark.skipif(os.name != "nt", reason="Windows case-equivalent physical paths")
def test_windows_case_alias_reference_holds_single_physical_owner_file(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    _, item = registered(a, video="CaseVideo")
    alias = item["storage_key"].replace("CaseVideo", "casevideo")
    b["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('casevideo',?)", (json.dumps({"video": "media/" + alias}),))
    b["con"].commit()
    assert statuses(scan(a))[item["artifact_id"]] == "referenced"


@pytest.mark.parametrize("change", ["reference", "node", "manifest", "inventory", "policy", "artifact"])
def test_reference_identity_inventory_policy_and_registry_changes_expire_digest(libraries, change):
    env = libraries()
    enroll(env)
    path, item = registered(env)
    before = scan(env)
    options = {}
    if change == "reference":
        env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('v',?)", (json.dumps({"video": "media/" + item["storage_key"]}),))
    elif change == "node":
        (env["profile"].database.parent / "node-identity.json").write_text(json.dumps({"instance_id": str(uuid.uuid4())}), encoding="utf-8")
    elif change == "manifest":
        (env["profile"].reports / "prices.manifest.json").write_text('{"PRIVATE":"SECRET"}', encoding="utf-8")
    elif change == "inventory":
        path.with_name("c" * 64 + ".mp4").write_bytes(b"unknown")
    elif change == "policy":
        options["policy"] = contract.default_policy(keep_publication=2)
    else:
        env["con"].execute("UPDATE storage_artifact SET file_mtime_ns='123'")
    env["con"].commit()
    after = scan(env, **options)
    assert before.roots_sha256 != after.roots_sha256
    assert before.preview_sha256 != after.preview_sha256
    assert "SECRET" not in json.dumps(roots.public_preview(after))


def test_source_mutation_during_capture_invalidates_whole_round(libraries):
    env = libraries()
    enroll(env)
    with pytest.raises(Exception) as raised:
        with captured(env):
            env["con"].execute("UPDATE meta SET value='1' WHERE key='kb_revision'")
            env["con"].commit()
    assert getattr(raised.value, "retriable", False)


def test_preview_has_bounded_whole_round_retry(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    original = roots.scan_locked
    calls = []
    def unstable(*args, **kwargs):
        calls.append(True)
        if len(calls) == 1:
            raise roots.RootError("ROOTS_SOURCE_CHANGED", retriable=True)
        return original(*args, **kwargs)
    monkeypatch.setattr(roots, "scan_locked", unstable)
    assert roots.preview([env["profile"]])["member_count"] == 1
    assert len(calls) == 2
    monkeypatch.setattr(roots, "scan_locked", lambda *a, **kw: (_ for _ in ()).throw(roots.RootError("ROOTS_SOURCE_CHANGED", retriable=True)))
    with pytest.raises(roots.RootError, match="ROOTS_SOURCE_CHANGED"):
        roots.preview([env["profile"]])


@pytest.mark.parametrize("kind", [OSError, FileNotFoundError, RuntimeError, ValueError])
@pytest.mark.parametrize("stage", ["start", "hash", "finish"])
def test_cancellation_keeps_exact_original_exception(libraries, monkeypatch, kind, stage):
    env = libraries()
    enroll(env)
    registered(env)
    failure = kind("PRIVATE_CANCEL_MESSAGE")
    armed = [stage == "start"]
    def cancelled():
        if armed[0]:
            raise failure
    if stage == "hash":
        original = roots.artifacts._inspect
        def inspect(**kwargs):
            armed[0] = True
            return original(**kwargs)
        monkeypatch.setattr(roots.artifacts, "_inspect", inspect)
    with pytest.raises(kind) as raised:
        with captured(env, check_cancelled=cancelled):
            armed[0] = True
    assert raised.value is failure


@pytest.mark.parametrize("field,value", [("max_rows", 1), ("max_metadata_bytes", 1), ("max_snapshot_bytes", 1),
                                         ("max_inventory_entries", 1), ("max_candidate_hash_bytes", 1)])
def test_global_limits_never_create_false_empty_candidates(libraries, field, value):
    env = libraries()
    enroll(env)
    registered(env)
    try:
        result = scan(env, limits=replace(roots.RootLimits(), **{field: value}))
    except roots.RootError:
        return
    assert not result.eligible_artifacts
    assert result.effective_inputs.blockers or all(f["status"] != "candidate" for f in result.files)


def test_group_projection_budget_applies_to_parser_results(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    original = roots.inputs.parse_image
    def many(*args, **kwargs):
        result = original(*args, **kwargs)
        ref = roots.inputs.Reference("sync_media", env["root_ids"]["sync_media"], "sync/v/" + "a" * 64 + ".mp4",
                                     env["member_id"], "live", "opaque", "opaque")
        return replace(result, references=(ref,) * 120)
    monkeypatch.setattr(roots.inputs, "parse_image", many)
    with pytest.raises(roots.RootError, match="ROOTS_ROWS_LIMIT"):
        scan(env, limits=replace(roots.RootLimits(), max_rows=100))


def test_manifest_pointer_protects_report_and_invalid_manifest_only_blocks_reports(libraries):
    env = libraries()
    enroll(env)
    _, report = registered(env, "local_report")
    _, media = registered(env)
    manifest = env["profile"].reports / "prices.manifest.json"
    manifest.write_text(json.dumps({"report_type": "prices", "status": "latest", "content_file": report["storage_key"],
                                    "content_sha256": report["sha256"]}), encoding="utf-8")
    before = scan(env)
    assert statuses(before)[report["artifact_id"]] == "referenced"
    assert statuses(before)[media["artifact_id"]] == "candidate"
    manifest.write_bytes(b"PRIVATE_INVALID_MANIFEST")
    after = scan(env)
    assert statuses(after)[report["artifact_id"]] == "blocked"
    assert statuses(after)[media["artifact_id"]] == "candidate"


def test_old_backup_keeps_media_after_live_reference_is_removed(libraries):
    env = libraries()
    enroll(env)
    _, media = registered(env)
    _, report = registered(env, "local_report")
    env["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('v',?)", (json.dumps({"video": "media/" + media["storage_key"]}),))
    env["con"].commit()
    destination = env["profile"].database.with_name("reno.db.bak-legacy")
    backup = sqlite3.connect(destination)
    try:
        env["con"].backup(backup)
    finally:
        backup.close()
    env["con"].execute("DELETE FROM video_asset")
    env["con"].commit()
    result = scan(env)
    assert statuses(result)[media["artifact_id"]] == "referenced"
    assert statuses(result)[report["artifact_id"]] == "blocked"
    assert "BACKUP_LEGACY_MANIFEST_UNKNOWN" in {b.code for b in result.effective_inputs.blockers}


def test_conflicting_registration_from_another_owner_excludes_same_physical_file(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    _, item = registered(a)
    with lifecycle.shared(b["profile"]):
        b["con"].execute("BEGIN IMMEDIATE")
        artifacts.register(b["con"], profile=b["profile"], namespace="sync_media", root=b["profile"].media,
                           storage_key=item["storage_key"], sha256=item["sha256"], byte_size=item["byte_size"])
    b["con"].execute("UPDATE storage_artifact SET byte_size=byte_size+1")
    b["con"].commit()
    result = scan(a)
    assert statuses(result)[item["artifact_id"]] == "conflict"
    assert not result.eligible_artifacts


def test_post_hash_replacement_cannot_be_adopted_as_inventory_baseline(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    path, _ = registered(env)
    original = roots.artifacts._inspect
    def changed(**kwargs):
        value = original(**kwargs)
        path.write_bytes(b"changed after SHA")
        return value
    monkeypatch.setattr(roots.artifacts, "_inspect", changed)
    with pytest.raises(roots.RootError, match="ROOTS_SOURCE_CHANGED"):
        scan(env)


def test_group_scan_never_stats_source_shm_or_changes_source_files(libraries, tmp_path, monkeypatch):
    from tests.test_storage_members import files
    env = libraries()
    enroll(env)
    registered(env)
    before = files(tmp_path)
    forbidden = env["profile"].database.name + "-shm"
    original_stat, original_lstat = os.stat, os.lstat
    def checked(original):
        def call(path, *args, **kwargs):
            if not isinstance(path, int) and Path(path).name == forbidden:
                pytest.fail("Group scan must not stat source SHM")
            return original(path, *args, **kwargs)
        return call
    with monkeypatch.context() as mp:
        mp.setattr(os, "stat", checked(original_stat))
        mp.setattr(os, "lstat", checked(original_lstat))
        result = scan(env)
        assert result.eligible_artifacts
    assert files(tmp_path) == before


def test_retiring_primary_archive_keeps_another_members_dependency_edge(libraries):
    a = libraries("a")
    b = libraries("b", media=a["profile"].media)
    enroll(a, b)
    old = archive(a, 0)
    archive(a, 1)
    with lifecycle.shared(a["profile"]):
        a["con"].execute("BEGIN IMMEDIATE")
        item = artifacts.register(a["con"], profile=a["profile"], namespace="sync_media", root=a["profile"].media,
                                  storage_key=old["media_key"], sha256=hashlib.sha256(old["media"].read_bytes()).hexdigest(),
                                  byte_size=old["media"].stat().st_size)
    a["con"].commit()
    b["con"].execute("INSERT INTO video_asset(video_id,files_json) VALUES('foreign',?)", (json.dumps({"video": "media/" + old["media_key"]}),))
    b["con"].commit()
    result = scan(a, policy=contract.default_policy(keep_publication=1))
    assert [r["checkpoint_id"] for r in result.proposed_retirements] == [old["entry"]["checkpoint_id"]]
    assert statuses(result)[item["artifact_id"]] == "referenced"


def test_corrupt_owned_row_does_not_echo_private_key(libraries):
    env = libraries()
    enroll(env)
    _, item = registered(env)
    env["con"].execute("UPDATE storage_artifact SET storage_key='PRIVATE_URL_AND_TOKEN'")
    env["con"].commit()
    result = roots.public_preview(scan(env))
    assert not result["files"] and result["candidate_bytes"] == 0
    assert "PRIVATE_URL_AND_TOKEN" not in json.dumps(result)


def test_primary_checkpoint_retirement_releases_dependencies_with_latest_retained(libraries):
    env = libraries()
    enroll(env)
    old = archive(env, 0)
    newer = archive(env, 1)
    for value in (old, newer):
        with lifecycle.shared(env["profile"]):
            if not env["con"].in_transaction:
                env["con"].execute("BEGIN IMMEDIATE")
            artifacts.register(env["con"], profile=env["profile"], namespace="sync_media", root=env["profile"].media,
                               storage_key=value["media_key"], sha256=hashlib.sha256(value["media"].read_bytes()).hexdigest(),
                               byte_size=value["media"].stat().st_size)
    env["con"].commit()
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    assert [r["checkpoint_id"] for r in result.proposed_retirements] == [old["entry"]["checkpoint_id"]], result.effective_inputs.blockers
    assert old["media_key"] in {f["storage_key"] for f in result.eligible_artifacts}
    assert newer["media_key"] not in {f["storage_key"] for f in result.eligible_artifacts}


def test_historical_backup_node_unknown_allows_live_retirement_and_holds_bytes(libraries):
    env = libraries()
    enroll(env)
    older, latest = archive(env, 0), archive(env, 1)
    backups.create(env["con"], env["profile"].database.with_name("reno.db.bak-judge-node-unknown"),
                   profile=env["profile"], kind="judge")
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    assert [item["checkpoint_id"] for item in result.proposed_retirements] == [older["entry"]["checkpoint_id"]]
    assert "CHECKPOINT_NODE_BINDING_UNPROVEN" not in {item.code for item in result.effective_inputs.blockers}
    assert statuses(result)[older["file"]["artifact_id"]] == "referenced"
    assert {older["media_key"], latest["media_key"]} <= {item.storage_key for item in result.effective_inputs.references}
    assert not result.eligible_artifacts


def test_missing_live_node_still_blocks_archive_retirement(libraries):
    env = libraries()
    enroll(env)
    older, latest = archive(env, 0), archive(env, 1)
    (env["profile"].database.parent / "node-identity.json").unlink()
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    assert not result.proposed_retirements
    assert "CHECKPOINT_NODE_BINDING_UNPROVEN" in {item.code for item in result.effective_inputs.blockers}
    assert {older["media_key"], latest["media_key"]} <= {item.storage_key for item in result.effective_inputs.references}


def test_late_checkpoint_blocker_restores_transitive_media_holds(libraries, monkeypatch):
    env = libraries()
    enroll(env)
    old, latest = archive(env, 0), archive(env, 1)
    original = roots._inventory
    def blocked(profile, namespace, *args):
        value = original(profile, namespace, *args)
        return (*value[:2], "ROOTS_INVENTORY_UNAVAILABLE") if namespace == "checkpoint" else value
    monkeypatch.setattr(roots, "_inventory", blocked)
    result = scan(env, policy=contract.default_policy(keep_publication=1))
    assert not result.proposed_retirements
    assert {old["media_key"], latest["media_key"]} <= {r.storage_key for r in result.effective_inputs.references}


def test_changed_missing_and_hardlinked_candidates_are_excluded(libraries):
    env = libraries()
    enroll(env)
    changed, first = registered(env, content=b"original")
    missing, second = registered(env, content=b"missing")
    linked, third = registered(env, content=b"hardlink")
    changed.write_bytes(b"tampered")
    missing.unlink()
    os.link(linked, linked.with_name("external-link"))
    result = scan(env)
    assert statuses(result)[first["artifact_id"]] == "changed"
    assert statuses(result)[second["artifact_id"]] == "missing"
    assert statuses(result)[third["artifact_id"]] == "changed"
    assert not result.eligible_artifacts


def test_registered_special_database_path_is_preserved_and_heartbeat_ignored(libraries, tmp_path):
    # A custom DB may itself have a grammatically legal checkpoint name.
    checkpoint_root = tmp_path / "special-checkpoints"
    env = libraries(database=checkpoint_root / (str(uuid.uuid4()) + ".sqlite"), checkpoints=checkpoint_root)
    enroll(env)
    special = env["profile"].database
    # A forged or old compatible registration must never authorize collecting
    # this special file. Do not acquire a media-style deny-write DB handle.
    info, root_info = special.stat(), checkpoint_root.stat()
    item = {"namespace": "checkpoint", **artifacts._root_record(root_info.st_dev, root_info.st_ino),
            "storage_key": special.name, "sha256": hashlib.sha256(special.read_bytes()).hexdigest(), "byte_size": info.st_size,
            "file_device": str(info.st_dev), "file_inode": str(info.st_ino), "file_mtime_ns": str(info.st_mtime_ns),
            "file_ctime_ns": str(info.st_ctime_ns), "created_at": "2026-10-01T00:00:00.000Z", "updated_at": "2026-10-01T00:00:00.000Z"}
    item["artifact_id"] = "art_" + contract.digest({key: item[key] for key in ("namespace", "root_id", "storage_key")})
    env["con"].execute(f"INSERT INTO storage_artifact({','.join(item)}) VALUES({','.join('?' for _ in item)})", tuple(item.values()))
    env["con"].execute("INSERT INTO job(kind,status,payload_json) VALUES('judge','pending','{}')")
    env["con"].commit()
    before = scan(env)
    assert statuses(before)[item["artifact_id"]] == "protected"
    env["con"].execute("UPDATE job SET lease_until='irrelevant-heartbeat',updated_at='display'")
    env["con"].commit()
    after = scan(env)
    assert before.preview_sha256 == after.preview_sha256


def test_registered_external_backup_is_special_even_with_checkpoint_shape(libraries):
    env = libraries()
    producer = libraries("b", media=env["profile"].media)
    enroll(env, producer)
    destination = env["profile"].checkpoints / (str(uuid.uuid4()) + ".sqlite")
    backups.create(producer["con"], destination, profile=producer["profile"], kind="manual")
    with lifecycle.shared(env["profile"]):
        env["con"].execute("BEGIN IMMEDIATE")
        item = artifacts.register(env["con"], profile=env["profile"], namespace="checkpoint", root=env["profile"].checkpoints,
                                  storage_key=destination.name, sha256=hashlib.sha256(destination.read_bytes()).hexdigest(),
                                  byte_size=destination.stat().st_size)
    env["con"].commit()
    result = scan(env)
    assert statuses(result)[item["artifact_id"]] == "protected"
    assert item["artifact_id"] not in {f["artifact_id"] for f in result.eligible_artifacts}
