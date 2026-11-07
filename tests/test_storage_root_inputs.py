"""Reference parsers use artificial committed databases and no real maintenance."""
import hashlib
from contextlib import closing
import json
import os
import sqlite3
import uuid

import pytest

from reno import db, storage_lifecycle, storage_root_inputs as inputs, sync_upload_pins as pins
from reno import sync_source_readmit_schema as future_schema
from reno.sync_protocol import _json_bytes
from tests.schema_fixtures import released16, assert_current17_runtime


SHA = "a" * 64
OTHER_SHA = "b" * 64
ROOTS = {"sync_media": "1" * 64, "local_report": "2" * 64,
         "sync_report": "2" * 64, "checkpoint": "3" * 64}


@pytest.fixture
def env(tmp_path):
    profile = storage_lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media",
                                               tmp_path / "reports", tmp_path / "checkpoints")
    profile.database.parent.mkdir()
    db.initialize(profile.database)
    con = sqlite3.connect(profile.database)
    library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
    yield con, profile, library
    con.close()


@pytest.fixture
def historical_env(tmp_path, monkeypatch, request):
    """Build only the declared old parser image before opening it as current."""
    from tests.migration_helpers import drop_future_home_recovery
    profile = storage_lifecycle.StorageProfile(tmp_path / "data" / "reno.db", tmp_path / "media",
                                               tmp_path / "reports", tmp_path / "checkpoints")
    profile.database.parent.mkdir()
    with released16(monkeypatch):
        db.initialize(profile.database)
        with closing(sqlite3.connect(profile.database)) as con:
            drop_future_home_recovery(con)
            con.execute("UPDATE meta SET value=? WHERE key='migration_version'", (request.param,))
            con.execute("DROP TABLE sync_upload_pin")
            con.commit()
            names = {row[0].lower() for row in con.execute("SELECT name FROM sqlite_master")}
            assert not names & {name.lower() for name in future_schema.RESERVED_NAMES}
            assert "sync_upload_pin" not in names
    assert_current17_runtime()
    with closing(sqlite3.connect(profile.database)) as con:
        library = con.execute("SELECT value FROM meta WHERE key='library_id'").fetchone()[0]
        yield con, profile, library


def parse(env, **kwargs):
    con, profile, _ = env
    con.commit()
    return inputs.parse_image(con, profile=profile, root_ids=ROOTS, member_id="mem_test", **kwargs)


def reset(con):
    con.set_authorizer(None)


def test_budget_wait_keeps_bound_video_root_without_private_budget_projection(env):
    con, _, _ = env
    con.execute("INSERT INTO sync_queue(url,status,video_id,budget_wait_reason,budget_operation_id) "
                "VALUES('https://b23.tv/private?token=PRIVATE','budget_wait','waiting-video','daily_limit',?)",
                (str(uuid.uuid4()),))
    result = parse(env)
    assert not result.blockers and {item.video_id for item in result.selectors} == {"waiting-video"}
    assert b"PRIVATE" not in result.canonical_bytes()


def asset(con, video="video", files=None, **extra):
    values = {"video_id": video, "files_json": json.dumps(files if files is not None else {}), **extra}
    con.execute(f"INSERT INTO video_asset ({','.join(values)}) VALUES ({','.join('?' for _ in values)})", tuple(values.values()))


def report(con):
    # Synthetic SQL pointer; no file or ownership row is created by a parser.
    con.execute("INSERT INTO sync_report VALUES (?,?,?,?,?,?,?,?,?,?)",
                ("prices", str(uuid.uuid4()), f".sync-versions/prices-{SHA}.md", SHA, 20,
                 "v1", "{}", "{}", "2026-10-01T00:00:00Z", OTHER_SHA))


def test_reads_unready_every_field_and_frames_without_registering_ownership(env):
    con, profile, _ = env
    keys = [f"sync/video/{n * 64}.mp4" for n in ("a", "b", "c", "d")]
    asset(con, files={"video": "media/" + keys[0], "audio": str(profile.media / keys[1]),
                      "poster": str(profile.media / keys[2]).replace("/", "\\"), "unused": None}, sync_state="unready")
    con.execute("INSERT INTO frame(video_id,frame_id,path) VALUES (?,?,?)", ("video", "f1", "media/" + keys[3]))
    report(con)
    result = parse(env)
    assert not result.blockers
    assert {r.storage_key for r in result.references if r.namespace == "sync_media"} == set(keys)
    assert len([r for r in result.references if r.namespace == "sync_report"]) == 1
    reset(con)
    assert con.execute("SELECT count(*) FROM storage_artifact").fetchone()[0] == 0


@pytest.mark.parametrize("value", ["outside/sync/video/x.mp4", "media/../media/sync/video/x.mp4", "Q:\\elsewhere\\media\\x.mp4",
                                  {"nested": "media/sync/video/x.mp4"}, ["media/originals/x.mp4"], 42])
def test_unproven_paths_only_block_media_while_reports_survive(env, value):
    con, _, _ = env
    asset(con, files={"auxiliary": value})
    report(con)
    result = parse(env)
    assert {b.namespace for b in result.blockers} == {"sync_media"}
    assert any(r.namespace == "sync_report" for r in result.references)


@pytest.mark.parametrize("raw", ['{"video":', '{"video":null,"video":null}', '{"video":NaN}', '["media/x"]'])
def test_bad_files_json_is_not_an_empty_graph(env, raw):
    con, _, _ = env
    asset(con)
    con.execute("UPDATE video_asset SET files_json=?", (raw,))
    result = parse(env)
    assert result.blockers and {b.namespace for b in result.blockers} == {"sync_media"}


def test_local_media_are_not_gc_candidates(env):
    con, profile, _ = env
    asset(con, files={"video": str(profile.media / "originals" / "v.mp4"), "audio": "media/audio/a.wav"})
    con.execute("INSERT INTO frame(video_id,frame_id,path) VALUES ('video','f1',?)", (str(profile.media / "frames" / "f.jpg"),))
    result = parse(env)
    assert not result.blockers and not result.references


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive physical path contract")
@pytest.mark.parametrize("key", [f"SYNC/video/{SHA}.mp4", f"sync/VIDEO/{SHA}.mp4",
                                f"sync/video/{SHA.upper()}.mp4", f"sync/video/{SHA}.MP4",
                                f"sync./video/{SHA}.mp4", f"sync /video/{SHA}.mp4",
                                f"SyNc... /video/{SHA}.mp4", f"sync/video./{SHA}.mp4"])
@pytest.mark.parametrize("source", ["files", "frame"])
def test_windows_noncanonical_managed_alias_never_becomes_unreferenced_local_file(env, key, source):
    con, _, _ = env
    if source == "files":
        asset(con, files={"video": "media/" + key})
    else:
        con.execute("INSERT INTO frame(video_id,frame_id,path) VALUES ('video','f',?)", ("media/" + key,))
    report(con)
    result = parse(env)
    assert {b.namespace for b in result.blockers} == {"sync_media"}
    assert any(r.namespace == "sync_report" for r in result.references)


def test_canonical_mixed_case_video_identifiers_remain_valid(env):
    key = f"sync/BV1Video/{SHA}.mp4"
    asset(env[0], video="BV1Video", files={"video": "media/" + key})
    result = parse(env)
    assert not result.blockers and result.references[0].storage_key == key


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive physical path contract")
def test_legal_cross_video_case_references_require_physical_candidate_index_matching(env):
    # Neither source names the referenced video. Both target spellings are legal
    # IDs, so the parser must preserve both and must not guess owner key casing.
    # A future candidate index must merge their Windows physical equivalence.
    asset(env[0], video="sourceA", files={"video": f"media/sync/Video/{SHA}.mp4"})
    asset(env[0], video="sourceB", files={"video": f"media/sync/video/{SHA}.mp4"})
    result = parse(env)
    assert not result.blockers and len(result.references) == 2
    assert len({r.storage_key for r in result.references}) == 2
    assert len({os.path.normcase(r.storage_key) for r in result.references}) == 1


def add_pin(con, library, *, state="reserved"):
    binding = {"publication_id": str(uuid.uuid4()), "source_node_id": str(uuid.uuid4()),
               "source_version": {"library_id": str(uuid.uuid4()), "library_epoch": 1, "kb_revision": 3},
               "recovery_stream": {"library_id": library, "library_epoch": 1}, "recovery_generation": 0}
    descriptor = {"video_id": "pending", "sha256": SHA, "byte_size": 10, "extension": "mp4"}
    parent = pins._record(binding)
    child = pins._record(binding, descriptor=descriptor)
    child.update(parent_pin_id=parent["pin_id"], state=state)
    if state == "stored":
        child["artifact_id"] = "artifact-test"
    for row in (parent, child):
        con.execute(f"INSERT INTO sync_upload_pin ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    return parent, child, descriptor


@pytest.mark.parametrize("state", ["reserved", "stored"])
def test_pin_without_current_asset_is_a_root_and_validates_parent(env, state):
    con, _, library = env
    parent, child, _ = add_pin(con, library, state=state)
    result = parse(env)
    assert not result.blockers
    assert [r.storage_key for r in result.references] == [f"sync/pending/{SHA}.mp4"]
    reset(con)
    con.execute("UPDATE sync_upload_pin SET source_revision=source_revision+1 WHERE pin_id=?", (parent["pin_id"],))
    assert {b.namespace for b in parse(env).blockers} == {"sync_media"}


def test_legacy_hold_is_permanent_root(env):
    con, _, library = env
    descriptor = {"video_id": "legacy", "sha256": SHA, "byte_size": 10, "extension": "webm"}
    node = str(uuid.uuid4())
    digest = hashlib.sha256(_json_bytes({"source_library_id": library, "source_node_id": node, "descriptor": descriptor})).hexdigest()
    row = {"pin_id": "legacy_" + digest, "kind": "legacy", "source_library_id": library, "source_node_id": node,
           "descriptor_json": json.dumps(descriptor), "intent_sha256": digest, "state": "legacy_hold",
           "created_at": "2020-01-01", "updated_at": "2020-01-01"}
    con.execute(f"INSERT INTO sync_upload_pin ({','.join(row)}) VALUES ({','.join('?' for _ in row)})", tuple(row.values()))
    result = parse(env)
    assert not result.blockers and result.references[0].storage_key == f"sync/legacy/{SHA}.webm"


def test_terminal_abandoned_pin_has_no_root_but_tombstone_must_validate(env):
    con, _, library = env
    parent, child, _ = add_pin(con, library)
    binding = inputs._pin_binding(parent)
    operation = str(uuid.uuid4())
    digest = hashlib.sha256(_json_bytes({"binding": binding, "operation_id": operation})).hexdigest()
    con.execute("UPDATE sync_upload_pin SET state='abandoned'")
    con.execute("UPDATE sync_upload_pin SET abandon_operation_id=?,abandon_request_sha256=? WHERE pin_id=?",
                (operation, digest, parent["pin_id"]))
    result = parse(env)
    assert not result.references and not result.blockers
    reset(con)
    con.execute("UPDATE sync_upload_pin SET abandon_request_sha256=? WHERE pin_id=?", (OTHER_SHA, parent["pin_id"]))
    assert parse(env).blockers


def test_pending_jobs_and_queue_protect_all_video_versions_without_secrets(env):
    con, _, _ = env
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES ('missing','process','running','{\"force\":true}')")
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES (NULL,'judge','pending','{\"threshold\":0.2}')")
    con.execute("INSERT INTO sync_queue(url,status,video_id,lease_token) VALUES ('https://private.invalid/?secret=TOKEN','claimed','queued-video','CREDENTIAL')")
    result = parse(env)
    assert not result.blockers
    assert {s.video_id for s in result.selectors} == {"missing", "queued-video"}
    assert all(s.sha256 is None and s.extension is None for s in result.selectors)
    for encoded in (result.canonical_bytes(), json.dumps(result.public_summary()).encode()):
        assert b"private.invalid" not in encoded and b"TOKEN" not in encoded and b"CREDENTIAL" not in encoded


def test_import_jobs_protect_bound_video_without_exposing_targets_or_local_files(env):
    con, _, _ = env
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES('bound-import','ingest','running',?)",
                (json.dumps({"target": "https://private.invalid/?secret=TOKEN", "source": "url", "process": True}),))
    con.execute("INSERT INTO job(kind,status,payload_json) VALUES('ingest','pending',?)",
                (json.dumps({"target": "C:\\private\\recording.mp4", "source": "file", "process": False}),))
    con.execute("INSERT INTO job(kind,status,payload_json) VALUES('favlist','pending',?)",
                (json.dumps({"url": "https://video.test/list", "limit": 0, "cookies": "C:\\private\\cookies.txt"}),))
    result = parse(env)
    assert not result.blockers and {item.video_id for item in result.selectors} == {"bound-import"}
    assert b"TOKEN" not in result.canonical_bytes() and b"private" not in result.canonical_bytes()


@pytest.mark.parametrize("kind,payload", [("unexpected", "{}"), ("process", "broken-json"), ("process", '{"path":"secret"}')])
def test_unknown_or_bad_pending_job_blocks_possible_namespaces(env, kind, payload):
    con, _, _ = env
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES ('video',?,'pending',?)", (kind, payload))
    assert {b.namespace for b in parse(env).blockers} == set(inputs.NAMESPACES)


@pytest.mark.parametrize("status", ["pending", "running"])
def test_personal_mirror_jobs_accept_only_the_original_closed_selector_without_video_roots(env, status):
    con, _, _ = env
    operation = str(uuid.uuid4())
    request_sha = "c" * 64
    payload = {"operation_id": operation, "request_sha256": request_sha}
    con.execute("INSERT INTO job(kind,status,payload_json) VALUES('personal_mirror',?,?)",
                (status, json.dumps(payload)))
    asset(con, files={"video": f"media/sync/video/{SHA}.mp4"})
    report(con)
    result = parse(env)
    # The sender's original request and .files tree have permanent physical
    # protection in storage_roots; the task must not invent a video selector.
    assert not result.blockers and not result.selectors
    assert {item.namespace for item in result.references} == {"sync_media", "sync_report"}
    for encoded in (result.canonical_bytes(), json.dumps(result.public_summary()).encode()):
        assert operation.encode() not in encoded and request_sha.encode() not in encoded


@pytest.mark.parametrize("kind,video_id,payload", [
    ("personal_mirror", None, "broken-json"),
    ("personal_mirror", None, "null"),
    ("personal_mirror", None, json.dumps({"operation_id": "not-a-uuid", "request_sha256": SHA})),
    ("personal_mirror", None, json.dumps({"operation_id": str(uuid.uuid4()), "request_sha256": SHA.upper()})),
    ("personal_mirror", None, json.dumps({"operation_id": str(uuid.uuid4()), "request_sha256": "a" * 63})),
    ("personal_mirror", None, json.dumps({"operation_id": str(uuid.uuid4())})),
    ("personal_mirror", None, json.dumps({"operation_id": str(uuid.uuid4()), "request_sha256": SHA,
                                        "snapshot_id": "PRIVATE_EXTRA"})),
    ("personal_mirror", "unexpected-video", json.dumps({"operation_id": str(uuid.uuid4()), "request_sha256": SHA})),
    ("personal_mirror_future", None, json.dumps({"operation_id": str(uuid.uuid4()), "request_sha256": SHA}))
])
def test_malformed_or_unknown_personal_mirror_jobs_still_block_every_namespace(env, kind, video_id, payload):
    con, _, _ = env
    con.execute("INSERT INTO job(video_id,kind,status,payload_json) VALUES(?,?,'pending',?)",
                (video_id, kind, payload))
    asset(con, files={"video": f"media/sync/video/{SHA}.mp4"})
    report(con)
    result = parse(env)
    assert {blocker.namespace for blocker in result.blockers} == set(inputs.NAMESPACES)
    assert not result.selectors
    assert {item.namespace for item in result.references} == {"sync_media", "sync_report"}
    assert b"PRIVATE_EXTRA" not in result.canonical_bytes()


@pytest.mark.parametrize("replacement", ["CREATE VIEW frame AS SELECT 'v' AS video_id, 'f' AS frame_id, 'secret' AS path",
                                        "CREATE VIRTUAL TABLE frame USING fts5(video_id,frame_id,path)",
                                        "CREATE TABLE frame(video_id TEXT,frame_id TEXT,path TEXT GENERATED ALWAYS AS (video_id))"])
def test_view_virtual_and_generated_columns_are_rejected_before_read(env, replacement):
    con, _, _ = env
    con.execute("DROP TABLE frame")
    con.execute(replacement)
    report(con)
    result = parse(env)
    assert {b.namespace for b in result.blockers} == {"sync_media"}
    assert any(r.namespace == "sync_report" for r in result.references)


@pytest.mark.parametrize("limits", [inputs.InputLimits(max_rows=2), inputs.InputLimits(max_vm_steps=1),
                                    inputs.InputLimits(max_total_bytes=30), inputs.InputLimits(max_cell_bytes=20),
                                    inputs.InputLimits(deadline_seconds=1e-12), inputs.InputLimits(max_objects=1)])
def test_exhausted_budgets_never_report_an_unblocked_empty_graph(env, limits):
    asset(env[0], files={"video": "media/" + f"sync/video/{SHA}.mp4"})
    assert parse(env, limits=limits).blockers


def test_unknown_schema_blocks_all_and_no_writes_allowed_after_parse(env):
    con, _, _ = env
    con.execute("UPDATE meta SET value='999' WHERE key='migration_version'")
    result = parse(env)
    assert {b.namespace for b in result.blockers} == set(inputs.NAMESPACES)
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("CREATE TABLE illicit(x)")
    with pytest.raises(sqlite3.DatabaseError):
        con.execute("ATTACH ':memory:' AS illicit")


@pytest.mark.parametrize("historical_env", ["7", "8", "9"], indirect=True)
def test_supported_historical_images_without_operational_pin_table(historical_env):
    con, _, _ = historical_env
    asset(con, files={"video": f"media/sync/video/{SHA}.mp4"})
    result = parse(historical_env)
    assert not result.blockers and len(result.references) == 1


def test_sql_functions_in_a_view_are_never_executed(env):
    con, _, _ = env
    calls = []
    con.create_function("leak", 0, lambda: calls.append("called") or "secret")
    con.execute("DROP TABLE frame")
    con.execute("CREATE VIEW frame AS SELECT 'video' AS video_id,'f' AS frame_id,leak() AS path")
    result = parse(env)
    assert result.blockers and not calls


def test_json_depth_and_cell_limits_are_scoped_and_do_not_hide_other_sources(env):
    con, _, _ = env
    asset(con, files={"x": {"y": {"z": "private"}}})
    report(con)
    result = parse(env, limits=inputs.InputLimits(max_json_depth=2))
    assert {b.namespace for b in result.blockers} == {"sync_media"}
    assert any(r.namespace == "sync_report" for r in result.references)


def test_column_type_corruption_is_scoped(env):
    con, _, _ = env
    con.execute("DROP TABLE frame")
    con.execute("CREATE TABLE frame(video_id TEXT,frame_id TEXT,path BLOB)")
    assert {b.namespace for b in parse(env).blockers} == {"sync_media"}


def test_existing_read_transaction_remains_open_and_semantic_digest_is_stable(env):
    con, profile, _ = env
    asset(con, files={"video": f"media/sync/video/{SHA}.mp4"}, title="PRIVATE BODY", source_url="https://secret.invalid")
    con.commit()
    con.execute("BEGIN")
    first = inputs.parse_image(con, profile=profile, root_ids=ROOTS, member_id="mem_test")
    assert con.in_transaction
    reset(con)
    con.rollback()
    con.execute("UPDATE video_asset SET title='CHANGED PRIVATE BODY'")
    second = parse(env)
    assert first.digest == second.digest
    assert b"PRIVATE" not in first.canonical_bytes()


@pytest.mark.parametrize("name", inputs.REPORT_NAMES)
def test_each_manifest_pure_parser_keeps_pointer_and_absence(env, name):
    _, profile, _ = env
    args = dict(name=name, profile=profile, root_ids=ROOTS, member_id="mem_test")
    key = f".versions/{name}-{'a' * 32}.md"
    raw = json.dumps({"report_type": name, "status": "latest", "content_file": key, "content_sha256": SHA}).encode()
    result = inputs.parse_manifest(raw, **args)
    assert not result.blockers and result.references[0].storage_key == key
    absent = inputs.parse_manifest(None, **args)
    assert not absent.references and not absent.blockers and absent.source_bindings[0].state == "absent"
    damaged = inputs.parse_manifest(b'{"report_type":', **args)
    assert {b.namespace for b in damaged.blockers} == {"local_report", "sync_report"}
    assert inputs.parse_manifest(None, unreadable=True, **args).blockers


def test_manifest_unknown_payload_and_namespace_do_not_leak(env):
    _, profile, _ = env
    raw = json.dumps({"report_type": "summary", "content_file": "https://secret.invalid/?api_key=TOKEN", "content_sha256": SHA}).encode()
    result = inputs.parse_manifest(raw, name="summary", profile=profile, root_ids=ROOTS, member_id="mem_test")
    assert result.blockers and not result.references
    assert b"TOKEN" not in result.canonical_bytes()
    assert "secret.invalid" not in json.dumps(result.public_summary())


def test_synced_and_generating_manifest_variants(env):
    _, profile, _ = env
    kwargs = dict(name="summary", profile=profile, root_ids=ROOTS, member_id="mem_test")
    generated = inputs.parse_manifest(b'{"report_type":"summary","status":"generating","policy_version":"v1"}', **kwargs)
    assert not generated.references and not generated.blockers
    raw = json.dumps({"report_type": "summary", "synced": True, "content_file": f".sync-versions/summary-{SHA}.md",
                      "content_sha256": SHA, "byte_size": 8}).encode()
    synced = inputs.parse_manifest(raw, **kwargs)
    assert not synced.blockers and synced.references[0].namespace == "sync_report"


def test_root_namespaces_do_not_merge_identical_relative_keys_across_profiles(env):
    asset(env[0], files={"video": f"media/sync/video/{SHA}.mp4"})
    first = parse(env)
    reset(env[0])
    changed = {**ROOTS, "sync_media": "4" * 64}
    second = inputs.parse_image(env[0], profile=env[1], root_ids=changed, member_id="mem_test")
    assert first.references[0].storage_key == second.references[0].storage_key
    assert first.references[0].root_id != second.references[0].root_id
    assert first.digest != second.digest


def test_knowledge_archive_cannot_be_misreported_as_complete_live_projection(env):
    result = parse(env, source_kind="checkpoint")
    assert not result.references and {b.code for b in result.blockers} == {"INPUT_IMAGE_KIND_UNSUPPORTED"}
    assert {b.namespace for b in result.blockers} == set(inputs.NAMESPACES)


def test_canonical_rejects_python_objects_and_nonfinite_values():
    assert inputs.canonical_bytes({"b": 1, "a": [True, None]}) == b'{"a":[true,null],"b":1}'
    for value in ({"secret": object()}, {1: "bad"}, float("nan")):
        with pytest.raises(ValueError):
            inputs.canonical_bytes(value)
