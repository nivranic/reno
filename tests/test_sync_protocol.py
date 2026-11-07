"""Knowledge transport refuses authority expansion before any SQLite mutation."""
import copy
import json
import sqlite3
import uuid

import pytest

from reno import db, migrations, sync_protocol as protocol


@pytest.fixture
def con(tmp_path):
    path = tmp_path / "knowledge.db"
    db.initialize(path)
    connection = sqlite3.connect(path)
    try:
        yield connection
    finally:
        connection.close()


def media(**changes):
    return {"video_id": "BV1Example", "sha256": "a" * 64, "byte_size": 4096,
            "extension": "mp4", **changes}


def atom(**changes):
    return {"id": "atom_example_001", "video_id": "BV1Example", "claim": "施工前核对基层",
            "conditions_json": '{"基层":["混凝土"],"空间":"卫生间"}',
            "parameters_json": '[{"name":"高度","value":2000,"unit":"mm"}]',
            "confidence": 0.8, **changes}


def decision(**changes):
    return {"event_id": "54e60904-3de3-4110-ae9a-13b278f4df55", "client_id": "local",
            "atom_id": "atom_example_001", "conflict_id": None, "action": "confirm",
            "revised_claim": None, "note": None, "created_at": "2026-09-30T01:02:03.004Z",
            "decided_at": "2026-09-30T01:02:03.004Z", "target_fingerprint": "v1:" + "b" * 64,
            "target_snapshot_json": '{"fingerprint_version":1,"conditions":{"空间":"卫生间"}}',
            "event_schema_version": 1, **changes}


def test_handshake_is_read_only_and_independent_of_node_data(con, tmp_path):
    first = protocol.schema_handshake(con)
    assert first["migration_version"] == migrations.SCHEMA_VERSION
    assert first["protocol_version"] == 3
    assert len(first["core_schema_sha256"]) == 64
    changes = con.total_changes
    original_metadata = con.execute("SELECT * FROM meta ORDER BY key").fetchall()
    assert protocol.validate_handshake(first, protocol.schema_handshake(con)) is None
    assert con.total_changes == changes
    assert con.execute("SELECT * FROM meta ORDER BY key").fetchall() == original_metadata
    other_path = tmp_path / "other" / "knowledge.db"
    db.initialize(other_path)
    with sqlite3.connect(other_path) as other:
        other.execute("UPDATE meta SET value=? WHERE key='library_id'", (str(uuid.uuid4()),))
        other.execute("CREATE TABLE private_future(id TEXT, secret TEXT)")
        other.execute("INSERT INTO private_future VALUES('new','never included')")
        # Operational and personal content/structure do not enter knowledge sync.
        assert protocol.schema_handshake(other) == first


def test_real_columns_and_wire_allowlist_both_change_schema_fingerprint(con, monkeypatch):
    first = protocol.schema_handshake(con)
    con.execute("ALTER TABLE knowledge_atom ADD COLUMN future_private TEXT")
    added_column = protocol.schema_handshake(con)
    assert first["core_schema_sha256"] != added_column["core_schema_sha256"]
    with pytest.raises(protocol.SyncError) as err:
        protocol.validate_handshake(added_column, first)
    assert err.value.code == "INCOMPATIBLE_SYNC_SCHEMA" and err.value.status == 409
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(future_private="not automatically exported")])
    changed = {name: dict(spec) for name, spec in protocol.TABLES.items()}
    changed["knowledge_atom"]["columns"] += ("future_private",)
    monkeypatch.setattr(protocol, "TABLES", changed)
    # Identical SQL with a changed wire contract is also incompatible.
    assert protocol.schema_handshake(con)["core_schema_sha256"] != added_column["core_schema_sha256"]


def test_actual_closed_rules_are_detached_and_are_used_by_fingerprint(con, monkeypatch):
    first = protocol.schema_handshake(con)
    rules = protocol.schema_rules()
    rules["limits"][0] = 0
    rules["integer_columns"].clear()
    assert protocol.schema_handshake(con) == first
    original = protocol.schema_rules
    def changed_rules():
        actual = original()
        return {**actual, "source_control_continuity_version": actual["source_control_continuity_version"] + 1}
    monkeypatch.setattr(protocol, "schema_rules", changed_rules)
    assert protocol.schema_handshake(con)["core_schema_sha256"] != first["core_schema_sha256"]


def test_schema_fingerprint_ignores_physical_column_order_and_index_name(con):
    before = protocol.schema_handshake(con)
    con.execute("DROP INDEX idx_decision_event")
    con.execute("CREATE UNIQUE INDEX different_local_name ON user_decision(event_id)")
    con.execute("ALTER TABLE frame RENAME TO old_frame")
    con.execute("CREATE TABLE frame(path TEXT,dhash TEXT,origin TEXT,pts_ms INTEGER,"
                "video_id TEXT,frame_id TEXT NOT NULL,PRIMARY KEY(video_id,frame_id))")
    con.execute("DROP TABLE old_frame")
    assert protocol.schema_handshake(con) == before


@pytest.mark.parametrize("damage", ["wrong_pk", "wrong_type", "missing", "partial_event", "missing_content_unique"])
def test_schema_rejects_structurally_unsafe_databases(con, damage):
    if damage == "wrong_pk":
        con.execute("DROP TABLE frame")
        con.execute("CREATE TABLE frame(frame_id TEXT PRIMARY KEY,video_id TEXT,pts_ms INTEGER,"
                    "origin TEXT,dhash TEXT,path TEXT)")
    elif damage == "wrong_type":
        con.execute("DROP TABLE frame")
        con.execute("CREATE TABLE frame(frame_id TEXT,video_id TEXT,pts_ms TEXT,"
                    "origin TEXT,dhash TEXT,path TEXT,PRIMARY KEY(video_id,frame_id))")
    elif damage == "missing":
        con.execute("DROP TABLE frame")
    elif damage == "partial_event":
        con.execute("DROP INDEX idx_decision_event")
        con.execute("CREATE UNIQUE INDEX misleading ON user_decision(event_id) WHERE action='confirm'")
    else:
        # Preserve columns/PK while dropping the independent content identity.
        sql = con.execute("SELECT sql FROM sqlite_master WHERE name='video_asset'").fetchone()[0]
        con.execute("DROP TABLE video_asset")
        con.execute(sql.replace("TEXT UNIQUE", "TEXT"))
    with pytest.raises(protocol.SyncError) as err:
        protocol.schema_handshake(con)
    assert err.value.code == "INCOMPATIBLE_SYNC_SCHEMA" and err.value.status == 409


@pytest.mark.parametrize("version", [None, "0", "04", "999", "bad"])
def test_handshake_never_initializes_or_migrates(con, version):
    con.execute("DELETE FROM meta WHERE key='migration_version'")
    if version is not None:
        con.execute("INSERT INTO meta VALUES('migration_version',?)", (version,))
    before = con.total_changes
    with pytest.raises(protocol.SyncError):
        protocol.schema_handshake(con)
    assert con.total_changes == before


@pytest.mark.parametrize("changes", [
    {"protocol_version": 1}, {"migration_version": 3}, {"core_schema_sha256": "f" * 64},
    {"protocol_version": True}, {"migration_version": "4"}, {"core_schema_sha256": "F" * 64},
    {"extra": "ignored?"},
])
def test_handshake_rejects_version_or_shape_mismatch(con, changes):
    local = protocol.schema_handshake(con)
    with pytest.raises(protocol.SyncError):
        protocol.validate_handshake({**local, **changes}, local)


def test_sql_primary_keys_are_explicit_and_server_ids_are_not_wire_keys(con):
    for table, spec in protocol.TABLES.items():
        columns = con.execute(f'PRAGMA table_info("{table}")').fetchall()
        actual = tuple(row[1] for row in sorted(columns, key=lambda row: row[5]) if row[5])
        assert actual == spec["primary_key"]
    assert protocol.TABLES["frame"]["primary_key"] == ("video_id", "frame_id")
    assert protocol.TABLES["evidence_ref"]["server_allocated"] == ("id",)
    assert protocol.TABLES["user_decision"]["logical_key"] == ("event_id",)
    assert "decision_id" not in protocol.TABLES["user_decision"]["columns"]


@pytest.mark.parametrize("table", [
    "renovation_project", "decision_card", "source_record", "source_attachment", "personal_revision",
    "personal_evidence_ref", "mutation_receipt", "checklist_instance", "checklist_item", "checklist_check",
    "processing_run", "job", "task_request", "worker_lease", "sync_queue", "atom_fts", "config",
    'knowledge_atom; DROP TABLE video_asset; --', "sqlite_master",
])
def test_authority_boundary_rejects_every_nonknowledge_table_even_empty(table):
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows(table, [])


@pytest.mark.parametrize("table,row", [
    ("knowledge_atom", atom(status="verified")),
    ("knowledge_atom", atom(primary_key="video_id")),
    ("knowledge_atom", atom(future_column="private")),
    ("video_asset", {"video_id": "v1", "files_json": '{"video":"C:\\private\\clip.mp4"}'}),
    ("video_asset", {"video_id": "v1", "sync_state": "ready"}),
    ("conflict_case", {"conflict_id": "c1", "status": "decided:accept_a"}),
    ("evidence_ref", {"id": 7, "video_id": "v1", "atom_id": "a1"}),
    ("user_decision", decision(decision_id=123)),
])
def test_forbidden_columns_never_become_bound_sql_values(table, row):
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows(table, [row])


def test_incoming_video_scope_cannot_be_changed_or_smuggled_into_global_tables():
    first = atom()
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [first, atom(id="a2", video_id="other")], video_id="BV1Example")
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [{"id": "a1"}], video_id="BV1Example")
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("conflict_case", [], video_id="BV1Example")
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("user_decision", [decision()], video_id="BV1Example")
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("transcript_segment", [{"id": "asr_1", "video_id": None}])


@pytest.mark.parametrize("key", [
    "library_id", "library_epoch", "kb_revision", "migration_version", "default_project_id",
    "personal_primary_id", "primary_epoch", "last_judge", "backfill:last_id", "sync_cursor",
    "step:v1:unknown", "step:v1:atomize:extra", "step:../v1:atomize", "step:v2:atomize",
])
def test_meta_cannot_overwrite_identity_operations_or_other_video_steps(key):
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("meta", [{"key": key, "value": "done"}], video_id="v1")
    assert protocol.validate_rows("meta", [{"key": "step:v1:atomize", "value": "done"}], video_id="v1")


@pytest.mark.parametrize("value", [
    "../v1", "v1/v2", "v1\\v2", "C:\\video", "/etc/passwd", "a:b", ".", "..", "a%2fb",
    "v1\x00", "v1\n", "　v1", "-option", "视频1", "a" * 129, "CON", "lpt1", "AUX", "", None, True,
])
def test_video_id_is_safe_on_windows_and_posix(value):
    with pytest.raises(protocol.SyncError):
        protocol.validate_video_id(value)


@pytest.mark.parametrize("url", [
    "https://www.bilibili.com/video/BV1Example?p=1", "https://bilibili.com/video/BV1Example",
    "https://b23.tv/abc123", "https://v.douyin.com/abc123/", "https://www.douyin.com/video/1234567890",
    "https://live.bilibili.com/123", "http://www.bilibili.com:80/video/123",
    "https://www.douyin.com:443/video/123", "HTTPS://WWW.BILIBILI.COM/video/123",
])
def test_supported_public_platform_urls_are_preserved(url):
    assert protocol.validate_ingest_url(url) == url


@pytest.mark.parametrize("url", [
    "file:///C:/video.mp4", "C:\\Users\\me\\video.mp4", "//bilibili.com/video/1",
    "ftp://bilibili.com/video/1", "https://127.0.0.1/", "http://[::1]/", "https://localhost/",
    "https://bilibili.com.evil.test/", "https://evilbilibili.com/", "https://b23.tv.evil.test/",
    "https://sub.b23.tv/", "https://bilibi1i.com/", "https://bilibili。com/",
    "https://user:pass@bilibili.com/", "https://@bilibili.com/", "https://bilibili.com@evil.test/",
    "https://evil.test\\@bilibili.com/", "https://bilibili.com:22/", "http://bilibili.com:443/",
    "https://bilibili.com:99999/", "https://bilibili.com:/", "https://bilibili.com./",
    "https://%62ilibili.com/", "https://bilibili.com%2fevil.test/", "https://bilibili.com/#http://127.0.0.1",
    "\nhttps://bilibili.com/", "https://bili\tbili.com/", "https://bilibili.com/a\x7fb",
    "https://bilibili.com/%0d%0aHost:evil.test", "https://bilibili.com/%255c@evil.test",
    "https://bilibili.com/%252525250d", "https://bilibili.com/%" + "25" * 20 + "0d",
    "https://bilibili.com/\u00a0", "https://bilibili.com/" + "x" * 4096, None,
])
def test_url_parser_ambiguities_and_private_destinations_are_rejected(url):
    with pytest.raises(protocol.SyncError):
        protocol.validate_ingest_url(url)


@pytest.mark.parametrize("table,row", [
    ("video_asset", {"video_id": "v1", "source_url": "C:\\Users\\me\\clip.mp4"}),
    ("video_asset", {"video_id": "v1", "source_url": "media/originals/v1/video.mp4"}),
    ("frame", {"frame_id": "f1", "video_id": "v1", "path": "media/frames/v1/001.jpg"}),
    ("frame", {"frame_id": "f1", "video_id": "v1", "path": ""}),
])
def test_local_paths_cannot_leak_through_source_or_frame_fields(table, row):
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows(table, [row], video_id="v1")


def test_valid_closure_rows_and_legacy_events_keep_values_without_mutating_inputs():
    batch = [atom()]
    before = copy.deepcopy(batch)
    assert protocol.validate_rows("knowledge_atom", batch, video_id="BV1Example") == batch
    assert batch == before
    returned = protocol.validate_rows("knowledge_atom", batch)
    returned[0]["claim"] = "changed copy"
    assert batch == before
    assert protocol.validate_rows("frame", [{"frame_id": "f1", "video_id": "v1", "path": None}])
    assert protocol.validate_rows("video_asset", [{"video_id": "v1", "source_url": None}])
    assert protocol.validate_rows("user_decision", [decision()])
    legacy = decision(event_schema_version=0, decided_at=None, target_fingerprint=None,
                      target_snapshot_json='{"binding_status":"legacy_unbound","atom_id":"a1"}')
    assert protocol.validate_rows("user_decision", [legacy]) == [legacy]


@pytest.mark.parametrize("changes", [
    {"byte_size": 0}, {"byte_size": -1}, {"byte_size": True}, {"byte_size": 512 * 1024 * 1024 + 1},
    {"byte_size": "4096"}, {"byte_size": 1.5}, {"byte_size": float("inf")},
    {"extension": ".mp4"}, {"extension": "MP4"}, {"extension": "../../mp4"}, {"extension": "exe"},
    {"sha256": "A" * 64}, {"sha256": "a" * 63}, {"sha256": "g" * 64},
    {"path": "C:\\private\\clip.mp4"}, {"video_id": "../v1"},
])
def test_media_upload_descriptor_cannot_choose_paths_or_bypass_resource_limits(changes):
    with pytest.raises(protocol.SyncError):
        protocol.validate_media_descriptor(media(**changes))


def test_media_descriptor_exact_fields_and_boundary():
    value = media(byte_size=protocol.MAX_MEDIA_BYTES)
    assert protocol.validate_media_descriptor(value) == value
    assert protocol.validate_media_descriptor(value) is not value
    value.pop("sha256")
    with pytest.raises(protocol.SyncError):
        protocol.validate_media_descriptor(value)


@pytest.mark.parametrize("changes", [
    {"confidence": float("nan")}, {"confidence": float("inf")}, {"confidence": -float("inf")},
    {"confidence": 2**63}, {"confidence": 10**100}, {"confidence": 10**1000},
    {"confidence": True}, {"confidence": {"value": 0.8}}, {"claim": ["nested"]},
    {"conditions_json": {"空间": "卫生间"}}, {"conditions_json": "not-json"},
    {"conditions_json": '["wrong root"]'}, {"conditions_json": '{"a": NaN}'},
    {"conditions_json": '{"a": Infinity}'}, {"conditions_json": '{"a": 1e999}'},
    {"conditions_json": '{"a":{"b":-Infinity}}'}, {"conditions_json": '{"a":1,"a":2}'},
    {"conditions_json": '{"a":"\\ud800"}'}, {"conditions_json": '{"a":"\\u0000"}'},
    {"parameters_json": '[{"v":{"deep":NaN}}]'},
    {"claim": "\ud800"}, {"claim": "hello\x00world"},
])
def test_non_sql_scalars_and_malicious_nested_json_are_rejected(changes):
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(**changes)])


def test_deep_json_and_oversized_json_cannot_exhaust_normal_row_processing():
    nested = '{"a":' * (protocol.MAX_JSON_DEPTH + 2) + '0' + '}' * (protocol.MAX_JSON_DEPTH + 2)
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(conditions_json=nested)])
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(parameters_json="[" * 1100 + "0" + "]" * 1100)])
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(parameters_json=json.dumps([0] * (protocol.MAX_JSON_NODES + 1)))])
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(claim="中" * (protocol.MAX_CELL_BYTES // 3 + 1))])
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom()] * (protocol.MAX_ROWS + 1))


def test_batch_limit_is_enforced_across_individually_valid_rows(monkeypatch):
    monkeypatch.setattr(protocol, "MAX_BATCH_BYTES", 700)
    rows = [atom(id=f"a{i}", claim="文字" * 45) for i in range(4)]
    assert protocol.validate_rows("knowledge_atom", rows[:1])
    with pytest.raises(protocol.SyncError) as err:
        protocol.validate_rows("knowledge_atom", rows)
    assert err.value.status == 413


def test_duplicate_logical_keys_rejected_but_composite_key_ids_may_repeat_across_videos():
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("knowledge_atom", [atom(), atom()])
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("user_decision", [decision(), decision(note="changed")])
    rows = [{"id": "asr_1", "video_id": "v1"}, {"id": "asr_1", "video_id": "v2"}]
    assert protocol.validate_rows("transcript_segment", rows) == rows


@pytest.mark.parametrize("row", [
    {"id": "a1", "video_id": "v1", "idx": True},
    {"id": "a1", "video_id": "v1", "idx": 2**63},
    {"id": "a1", "video_id": "v1", "start_ms": -1},
    {"id": "a1", "video_id": "v1", "start_ms": 20, "end_ms": 10},
])
def test_integer_times_remain_bindable_and_ordered(row):
    with pytest.raises(protocol.SyncError):
        protocol.validate_rows("transcript_segment", [row])


def test_protocol_error_is_usable_without_exposing_bad_values():
    secret = "https://sensitive:unique-test-value-abc@bilibili.com/"
    with pytest.raises(protocol.SyncError) as err:
        protocol.validate_ingest_url(secret)
    assert "unique-test-value-abc" not in str(err.value)
    assert err.value.code == "INVALID_SYNC_REQUEST" and err.value.status == 400


def test_accepted_numeric_types_bind_to_real_sqlite_columns(con):
    values = (0, 1, 2**63 - 1, 0.875, 1e100, 1.7976931348623157e308)
    for i, value in enumerate(values):
        row = protocol.validate_rows("knowledge_atom", [atom(id=f"atom_{i}", confidence=value)])[0]
        con.execute("INSERT INTO knowledge_atom(id,video_id,confidence) VALUES(?,?,?)",
                    (row["id"], row["video_id"], row["confidence"]))
    stored = con.execute("SELECT confidence FROM knowledge_atom ORDER BY id").fetchall()
    assert len(stored) == len(values) and all(row[0] is not None for row in stored)
