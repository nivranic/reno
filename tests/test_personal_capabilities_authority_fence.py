"""Actual isolated capability projections under the original lifecycle guards.

The forty fixed cases use production current17 setup, original SH/EX/group
guards and connections, and actual mirror capture/installation. No successful
authority, ready attachment, mirror head or receipt is seeded. None from the
finite name fence never becomes a reusable grant. Negative consumer-local
faults retain the real acquired iterator/directory and close it first.

Own throw-node checks walk tb_next by identity only: no tb_frame, code, locals
or globals are inspected. This suite is not native root-cause sampling, live
writer authority, normal-library maintenance, or complete H05 acceptance.
"""
import asyncio
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import os
import sqlite3
from types import SimpleNamespace
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

from app import personal_read_api
from reno import config, db, personal, personal_schema, storage_admin
from reno import personal_attachments as attachments
from reno import personal_authority_legacy_fence as fence
from reno import personal_mirror_contract as facts
from reno import personal_mirror_read as mirror_reader
from reno import personal_mirror_snapshot as capture
from reno import personal_mirror_transfer as transfer
from reno import personal_mirror_transfer_contract as wire
from reno import personal_mirror_transfer_schema as mirror_schema
from reno import storage_lifecycle as lifecycle, sync_media as safe
from reno import sync_protocol, sync_queue


LOCAL_KEYS = frozenset({
    "library_id", "instance_id", "personal_primary_id", "primary_epoch",
    "personal_read", "personal_write", "personal_upload", "personal_integrity_ok",
    "field_checks", "field_checks_read", "field_checks_write", "personal_cloud_read_enabled",
    "personal_read_version", "personal_mode", "personal_reason",
})
WRITE_KEYS = ("personal_write", "personal_upload", "field_checks_write")
REQUIRED_REASON = "个人数据写入权限待确认，暂时仅可读取"
STATE_REASON = "个人数据写入权限暂不可用，请稍后重试"
INCOMPLETE_REASON = "个人附件不完整，保留只读记录；请完成附件恢复后再写入"
BODY = b"%PDF-1.4\nactual isolated capability attachment\n%%EOF\n"
MARKER_BODY = b"unclassified original marker bytes; no successful authority"
MIRROR_NOW = datetime(2026, 10, 3, tzinfo=timezone.utc)


def uid():
    return str(uuid.uuid4())


def envelope(env, changes, *, base=None):
    result = {"operation_id": uid(), "primary_epoch": int(env["meta"]["primary_epoch"]),
              "changes": changes}
    if base is not None:
        result["base_revision"] = base
    return result


def select_root(monkeypatch, root, *, role="home", mirror_enabled=True):
    data = root / "data"
    data.mkdir(parents=True, exist_ok=True)
    local = root / "config.synthetic.json"
    local.write_text(json.dumps({"node_role": role, "personal_features_enabled": True,
        "field_checks_enabled": True, "personal_cloud_read_enabled": False,
        "personal_mirror_enabled": mirror_enabled, "auth_token": "", "ask_rate_per_min": 0}),
        encoding="utf-8")
    monkeypatch.setenv("RENO_DATA_DIR", str(data))
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    for key in ("RENO_NODE_ROLE", "RENO_PERSONAL_FEATURES_ENABLED", "RENO_FIELD_CHECKS_ENABLED",
                "RENO_PERSONAL_CLOUD_READ_ENABLED", "RENO_PERSONAL_MIRROR_ENABLED"):
        monkeypatch.delenv(key, raising=False)
    for name, path in {"DATA": data, "MEDIA": root / "media", "ORIG": root / "media/originals",
        "FRAMES": root / "media/frames", "AUDIO": root / "media/audio",
        "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    return data


def original_meta(con):
    return dict(con.execute("SELECT key,value FROM meta WHERE key IN (?,?,?,?)", facts.META_KEYS))


@pytest.fixture
def library(tmp_path, monkeypatch):
    root = tmp_path / "actual-capabilities-home"
    data = select_root(monkeypatch, root)
    profile = lifecycle.StorageProfile(data / "reno.db", root / "media",
                                       data / "generated", data / "sync-checkpoints")
    assert db.initialize(profile.database).version == 17
    lifecycle.initialize(profile)
    with storage_admin.connection(profile) as con:
        meta = original_meta(con)
        assert con.execute("SELECT value FROM meta WHERE key='migration_version'").fetchone()[0] == "17"
    node = personal_schema.node_identity(data)
    assert node and node == meta["personal_primary_id"]
    env = {"root": root, "data": data, "path": profile.database, "profile": profile,
           "meta": meta, "node": node, "project_id": meta["default_project_id"]}
    source_envelope = envelope(env, {"source_type": "manual_note", "title": "绍兴未定材料原始核对记录"})
    created, status = personal.create_source(env["project_id"], source_envelope)
    assert status == 201
    env.update(source_id=created["source"]["source_id"], source_envelope=source_envelope)
    return env


def local_oracle(env, *, meta=None, node=None, enabled=True, field=True, intact=True,
                 reason=None, writable=None):
    # Expected fields come from actual producer metadata/node, never subject cap.
    metadata = env["meta"] if meta is None else meta
    instance = env["node"] if node is None else node
    primary = bool(instance and instance == metadata.get("personal_primary_id"))
    initialized = bool(metadata.get("default_project_id") and metadata.get("primary_epoch"))
    readable = enabled and initialized
    old_write = readable and primary and intact
    writable = old_write if writable is None else writable
    if reason is None:
        reason = ("个人装修功能尚未启用" if not enabled else
                  "个人数据主实例尚未初始化" if not initialized else
                  "本节点是只读副本" if not primary else
                  INCOMPLETE_REASON if not intact else "")
    return {"library_id": metadata.get("library_id"), "instance_id": instance,
        "personal_primary_id": metadata.get("personal_primary_id"),
        "primary_epoch": int(metadata.get("primary_epoch") or 0),
        "personal_read": readable, "personal_write": writable, "personal_upload": writable,
        "personal_integrity_ok": intact, "field_checks": readable and field,
        "field_checks_read": readable and field, "field_checks_write": writable and field,
        "personal_cloud_read_enabled": False, "personal_read_version": None,
        "personal_mode": "local" if readable else "disabled", "personal_reason": reason}


def assert_local(actual, expected):
    assert type(actual) is dict and frozenset(actual) == LOCAL_KEYS
    assert actual == expected
    for key in ("personal_read", *WRITE_KEYS, "personal_integrity_ok", "field_checks",
                "field_checks_read", "personal_cloud_read_enabled"):
        assert type(actual[key]) is bool and actual[key] is expected[key]
    assert type(actual["primary_epoch"]) is int
    assert actual["personal_read_version"] is None


def sql_state(env, *, extra_tables=()):
    with storage_admin.connection(env["profile"]) as con:
        return {name: [tuple(row) for row in con.execute('SELECT * FROM main."' + name + '" ORDER BY rowid')]
                for name in ("meta", *facts.TABLE_NAMES, *extra_tables)}


def immutable_state(env):
    # SQLite connection sidecars are not claimed immutable file effects here.
    database_names = {env["path"].name + suffix for suffix in ("", "-wal", "-shm", "-journal")}
    return {str(path.relative_to(env["root"])): path.read_bytes()
            for path in env["root"].rglob("*") if path.is_file() and path.name not in database_names}


def snapshot(env, *, extra_tables=()):
    return sql_state(env, extra_tables=extra_tables), immutable_state(env)


def assert_unchanged(env, before, *, extra_tables=()):
    assert sql_state(env, extra_tables=extra_tables) == before[0]
    assert immutable_state(env) == before[1]


def assert_released(env):
    assert lifecycle.current_guard() is None
    with lifecycle.exclusive(env["profile"], timeout=0) as held:
        assert lifecycle.require_guard(env["profile"], exclusive=True) is held


def marker(env, kind="file", name=".reno-personal-authority-unknown"):
    path = env["data"] / name
    if kind == "file":
        path.write_bytes(MARKER_BODY)
    else:
        path.mkdir()
        if kind == "partial":
            (path / "unclassified.part").write_bytes(b"actual unfinished original")
    # Windows may normalize a trailing dot/space request. Assert the actual name
    # observed on disk is prefixed; do not manufacture a successful name view.
    with os.scandir(env["data"]) as entries:
        actual_names = [entry.name for entry in entries
                        if entry.name.rstrip(" .").casefold().startswith(".reno-personal-authority")]
    assert actual_names
    return path


class FenceSpy:
    """Wrap and call the actual finite fence; never return a forged success."""
    def __init__(self, original, *, after_first=None):
        self.original, self.after_first = original, after_first
        self.calls = []

    def __call__(self, profile, *args, **kwargs):
        call = {"profile": profile, "guard": lifecycle.current_guard(), "returned": False,
                "error": None}
        self.calls.append(call)
        try:
            result = self.original(profile, *args, **kwargs)
        except BaseException as error:
            call["error"] = error
            raise
        call["returned"] = True
        assert result is None
        if len(self.calls) == 1 and self.after_first is not None:
            self.after_first()
        return result


def install_spy(patch, *, after_first=None):
    spy = FenceSpy(fence.assert_legacy_write_allowed_locked, after_first=after_first)
    patch.setattr(fence, "assert_legacy_write_allowed_locked", spy)
    return spy


def install_new_call_sentinels(patch):
    calls = []
    def forbidden_from_connection(*args, **kwargs):
        calls.append("from_connection")
        raise AssertionError("A previously false or cloud cap called the new profile interface")
    def forbidden_fence(*args, **kwargs):
        calls.append("fence")
        raise AssertionError("A previously false or cloud cap added a name census")
    patch.setattr(storage_admin, "from_connection", forbidden_from_connection)
    patch.setattr(fence, "assert_legacy_write_allowed_locked", forbidden_fence)
    return calls


def own_throw(failure, own_nodes):
    try:
        raise failure
    except BaseException:
        own_nodes.append(BaseException.__dict__["__traceback__"].__get__(failure))
        raise


def assert_own_throw_survives(failure, own_nodes):
    assert len(own_nodes) == 1 and own_nodes[0] is not None
    current = BaseException.__dict__["__traceback__"].__get__(failure)
    for _ in range(4096):
        if current is own_nodes[0]:
            return
        if current is None:
            break
        current = current.tb_next
    raise AssertionError("Original own throw node was lost")


class ActualScans:
    """Consumer-local negative seams over actual acquired/closed scandir owners."""
    def __init__(self, original, *, names=None, next_error=None, own_nodes=None,
                 close_error=None, after_close=None, acquire_error=None):
        self.original = original
        self.names, self.next_error = names, next_error
        self.own_nodes = [] if own_nodes is None else own_nodes
        self.close_error, self.after_close, self.acquire_error = close_error, after_close, acquire_error
        self.calls = self.closed = 0
        self.firsts = []

    def __call__(self, target):
        actual = self.original(target)
        index = self.calls
        self.calls += 1
        if self.acquire_error is not None:
            # Explicit injected failure occurs only after the real owner closes.
            actual.close()
            self.closed += 1
            raise self.acquire_error
        owner = self
        class Context:
            def __enter__(self):
                entries = actual.__enter__()
                self.entries = iter(entries) if owner.names is None else iter(
                    SimpleNamespace(name=name) for name in owner.names(index))
                return self

            def __iter__(self):
                return self

            def __next__(self):
                if owner.next_error is not None:
                    own_throw(owner.next_error, owner.own_nodes)
                return next(self.entries)

            def __exit__(self, kind, error, traceback):
                owner.firsts.append(error)
                result = actual.__exit__(kind, error, traceback)
                owner.closed += 1
                if owner.after_close is not None:
                    owner.after_close(index)
                if owner.close_error is not None:
                    raise owner.close_error
                return result
        return Context()


def substitute_scans(patch, scans):
    patch.setattr(fence, "os", SimpleNamespace(name=os.name, scandir=scans))


def substitute_normal_directory_close(patch, failure, closed):
    original_factory = safe._Directory
    def factory(*args, **kwargs):
        actual = original_factory(*args, **kwargs)
        class Context:
            def __enter__(self):
                return actual.__enter__()
            def __exit__(self, kind, error, traceback):
                actual.__exit__(kind, error, traceback)
                closed.append((kind, error))
                raise failure
        return Context()
    patch.setattr(fence, "safe", SimpleNamespace(_Directory=factory, _native_path=safe._native_path))


def actual_copy(env, root):
    data = root / "data"
    data.mkdir(parents=True)
    path = data / "reno.db"
    with storage_admin.connection(env["profile"]) as source, closing(sqlite3.connect(path)) as target:
        source.backup(target)
    profile = lifecycle.StorageProfile(path, root / "media", data / "generated", data / "sync-checkpoints")
    lifecycle.initialize(profile)
    with storage_admin.connection(profile) as con:
        meta = original_meta(con)
    assert meta == env["meta"]
    return {"root": root, "data": data, "path": path, "profile": profile,
            "meta": meta, "node": env["node"], "project_id": env["project_id"]}


def actual_upload(env):
    from app.personal_api import router
    app = FastAPI()
    app.include_router(router)
    with TestClient(app) as client:
        response = client.post(f"/api/sources/{env['source_id']}/attachments",
            headers={"X-Operation-Id": uid(), "X-Primary-Epoch": env["meta"]["primary_epoch"]},
            files={"file": ("original.pdf", BODY, "application/pdf")})
    assert response.status_code in (200, 201), response.text
    attachment = response.json()["attachment"]
    with storage_admin.connection(env["profile"]) as con:
        row = con.execute("SELECT * FROM source_attachment WHERE attachment_id=?",
                          (attachment["attachment_id"],)).fetchone()
        assert row["state"] == "ready" and row["byte_size"] == len(BODY)
        assert row["sha256"] == hashlib.sha256(BODY).hexdigest()
        storage_key = row["storage_key"]
    path = attachments.resolve_storage(storage_key)
    assert path.read_bytes() == BODY
    return path, attachment


def actual_mirror(env, tmp_path, monkeypatch):
    with storage_admin.connection(env["profile"]) as con:
        source = {"library_id": env["meta"]["library_id"], "node_id": env["node"],
                  "handshake": sync_protocol.schema_handshake(con)}
    epoch = int(env["meta"]["primary_epoch"])
    captured = capture.capture_snapshot(snapshot_id=uid(), expected_library_id=source["library_id"],
        expected_primary_id=source["node_id"], expected_epoch=epoch).snapshot
    root = tmp_path / "actual-capabilities-cloud"
    data = select_root(monkeypatch, root, role="cloud")
    profile = lifecycle.StorageProfile(data / "reno.db", root / "media",
                                       data / "generated", data / "sync-checkpoints")
    assert db.initialize(profile.database).version == 17
    lifecycle.initialize(profile)
    with storage_admin.connection(profile) as con:
        con.execute("BEGIN IMMEDIATE")
        mirror_schema.install(con, profile=profile)
        sync_queue.bind_peer(con, source, now=MIRROR_NOW)
        lease = sync_queue.heartbeat(con, source, uid(), now=MIRROR_NOW)["lease"]
        con.commit()
        receiver = {"library_id": db.knowledge_version(con)["library_id"],
                    "node_id": personal_schema.node_identity(data)}
    prepared = {"format_version": 1, "kind": "personal_mirror_prepare", "operation_id": uid(),
        "snapshot_id": captured.snapshot_id, "snapshot_sha256": captured.snapshot_sha256,
        "snapshot_json": captured.snapshot_json,
        "source": {"library_id": source["library_id"], "node_id": source["node_id"],
                   "personal_primary_id": source["node_id"], "primary_epoch": epoch},
        "receiver": receiver, "previous": None, "expected_revision": 0}
    selected = {"format_version": 1, "kind": "personal_mirror_commit", "operation_id": prepared["operation_id"],
                "prepare_sha256": wire.prepare_sha256(prepared)}
    assert wire.attachment_manifest(prepared) == []
    arguments = {"profile": profile, "source": source, "lease": lease, "now": MIRROR_NOW}
    with lifecycle.shared(profile):
        transfer.prepare_mirror_locked(**arguments, prepare=prepared)
        committed = transfer.commit_mirror_locked(**arguments, commit=selected)
    config.set_local({"personal_cloud_read_enabled": True})
    return {"root": root, "data": data, "path": profile.database, "profile": profile,
            "prepared": prepared, "committed": committed, "receiver": receiver, "source": source}


def actual_client():
    from app.api import router
    app = FastAPI()
    app.include_router(router)
    return TestClient(app)


@pytest.mark.parametrize("window", ["shared", "exclusive"], ids=["CAP-N01", "CAP-N02"])
def test_actual_no_marker_preserves_every_original_capability(library, monkeypatch, window):
    env = library
    before = snapshot(env)
    with getattr(lifecycle, window)(env["profile"]) as held, closing(db.connect(env["path"])) as con:
        assert lifecycle.require_guard(env["profile"]) is held
        assert storage_admin.from_connection(con).identity == env["profile"].identity
        with monkeypatch.context() as patch:
            spy = install_spy(patch)
            actual = personal.capabilities(con)
        assert_local(actual, local_oracle(env))
        assert len(spy.calls) == 1 and spy.calls[0]["returned"]
        assert spy.calls[0]["guard"] is held
        assert spy.calls[0]["profile"].identity == env["profile"].identity
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("kind,name", [
    ("empty", ".reno-personal-authority-empty"),
    ("file", ".reno-personal-authority-malformed"),
    ("partial", ".reno-personal-authority-partial"),
    ("empty", ".RENO-PERSONAL-AUTHORITY-UPPER"),
    ("file", ".reno-personal-authority-trailing-alias. "),
], ids=["CAP-M01", "CAP-M02", "CAP-M03", "CAP-M04", "CAP-M05"])
def test_every_actual_prefixed_object_only_downgrades_write_fields(library, monkeypatch, kind, name):
    env = library
    marker(env, kind, name)
    before = snapshot(env)
    with storage_admin.connection(env["profile"]) as con:
        held = lifecycle.require_guard(env["profile"])
        with monkeypatch.context() as patch:
            spy = install_spy(patch)
            actual = personal.capabilities(con)
            assert not personal.can_write(con)
            assert not personal.can_write(con, upload=True)
            assert not personal.can_write(con, field=True)
            assert personal.can_read(con)
        assert_local(actual, local_oracle(env, writable=False, reason=REQUIRED_REASON))
        assert len(spy.calls) == 5
        assert all(call["guard"] is held and not call["returned"]
                   and type(call["error"]) is fence.LegacyAuthorityFenceError
                   and call["error"].code == "PERSONAL_AUTHORITY_REQUIRED" for call in spy.calls)
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("state", ["bare-idle", "bare-transaction", "foreign-guard", "ambiguous"],
                         ids=["CAP-G01", "CAP-G02", "CAP-G03", "CAP-G06"])
def test_real_connection_and_profile_preconditions_fail_closed(library, tmp_path, monkeypatch, state):
    env = library
    other = None
    variant = None
    variant_env = None
    if state == "foreign-guard":
        other = actual_copy(env, tmp_path / "actual-foreign")
    elif state == "ambiguous":
        roots = tmp_path / "actual-same-db-other-roots"
        variant = lifecycle.StorageProfile(env["path"], roots / "media", roots / "reports", roots / "checkpoints")
        lifecycle.initialize(variant)
        variant_env = {**env, "root": roots, "profile": variant}
    before = snapshot(env)
    other_before = snapshot(other) if other is not None else None
    variant_before = snapshot(variant_env) if variant_env is not None else None
    exact_codes = {"bare-transaction": "STORAGE_GUARD_REQUIRED", "foreign-guard": "STORAGE_PROFILE_MISMATCH",
                   "ambiguous": "STORAGE_PROFILE_AMBIGUOUS"}

    def inspect(con):
        if state == "bare-idle":
            assert not con.in_transaction and lifecycle.current_guard() is None
            assert storage_admin.from_connection(con).identity == env["profile"].identity
        else:
            with pytest.raises(lifecycle.StorageLifecycleError) as caught:
                storage_admin.from_connection(con)
            assert caught.value.code == exact_codes[state]
        with monkeypatch.context() as patch:
            spy = install_spy(patch)
            actual = personal.capabilities(con)
        assert_local(actual, local_oracle(env, writable=False, reason=STATE_REASON))
        if state == "bare-idle":
            assert len(spy.calls) == 1 and spy.calls[0]["guard"] is None
            assert not spy.calls[0]["returned"]
            assert spy.calls[0]["error"].code == "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
        else:
            assert spy.calls == []
        if state == "bare-transaction":
            assert con.in_transaction

    if state in {"bare-idle", "bare-transaction"}:
        with closing(db.connect(env["path"])) as con:
            if state == "bare-transaction":
                con.execute("BEGIN")
            inspect(con)
    elif state == "foreign-guard":
        with lifecycle.shared(other["profile"]), closing(db.connect(env["path"])) as con:
            inspect(con)
    else:
        with lifecycle.shared_many((env["profile"], variant)), closing(db.connect(env["path"])) as con:
            inspect(con)
    assert_unchanged(env, before)
    assert_released(env)
    if other is not None:
        assert_unchanged(other, other_before)
        assert_released(other)
    if variant_env is not None:
        assert_unchanged(variant_env, variant_before)
        assert_released(variant_env)


@pytest.mark.parametrize("location", ["configuration-root", "actual-second-db-root"], ids=["CAP-G04", "CAP-G05"])
def test_actual_main_database_selects_second_member_and_its_names(library, tmp_path, monkeypatch, location):
    env = library
    second = actual_copy(env, tmp_path / "actual-second-group-member")
    marker(env if location == "configuration-root" else second)
    before, second_before = snapshot(env), snapshot(second)
    with lifecycle.shared_many((env["profile"], second["profile"])) as held:
        with closing(db.connect(second["path"])) as con:
            assert held.profiles[0].identity == env["profile"].identity
            assert len(held.profiles) == 2
            assert storage_admin.from_connection(con) is held.profiles[1]
            with monkeypatch.context() as patch:
                spy = install_spy(patch)
                actual = personal.capabilities(con)
            assert len(spy.calls) == 1
            assert spy.calls[0]["guard"] is held and spy.calls[0]["profile"] is held.profiles[1]
            assert actual["instance_id"] == env["node"] == actual["personal_primary_id"]
            if location == "configuration-root":
                assert spy.calls[0]["returned"]
                assert_local(actual, local_oracle(env))
            else:
                assert spy.calls[0]["error"].code == "PERSONAL_AUTHORITY_REQUIRED"
                assert_local(actual, local_oracle(env, writable=False, reason=REQUIRED_REASON))
    assert_unchanged(env, before)
    assert_unchanged(second, second_before)
    assert_released(env)
    assert_released(second)


@pytest.mark.parametrize("fault", ["scan-io", "directory-close", "non-text-name", "actual-name-drift"],
                         ids=["CAP-U01", "CAP-U02", "CAP-U03", "CAP-U04"])
def test_actual_guard_observation_faults_downgrade_only_writes(library, monkeypatch, fault):
    env = library
    before = snapshot(env)
    closed = []
    addition = env["data"] / "ordinary-explicit-name-change.bin"
    added = b"explicit noncooperating actor added this original file"
    scans = None
    with storage_admin.connection(env["profile"]) as con:
        with monkeypatch.context() as patch:
            if fault == "scan-io":
                scans = ActualScans(os.scandir, acquire_error=OSError("synthetic acquired census interface failure"))
                substitute_scans(patch, scans)
            elif fault == "directory-close":
                substitute_normal_directory_close(patch, OSError("synthetic normal directory close"), closed)
            elif fault == "non-text-name":
                scans = ActualScans(os.scandir, names=lambda index: [None])
                substitute_scans(patch, scans)
            else:
                def after_close(index):
                    if index == 0:
                        addition.write_bytes(added)
                scans = ActualScans(os.scandir, after_close=after_close)
                substitute_scans(patch, scans)
            actual = personal.capabilities(con)
        assert_local(actual, local_oracle(env, writable=False, reason=STATE_REASON))
    if scans is not None:
        assert scans.calls == scans.closed and scans.closed > 0
    if fault == "directory-close":
        assert closed == [(None, None)]
    if fault == "actual-name-drift":
        assert sql_state(env) == before[0]
        assert immutable_state(env) == {**before[1], str(addition.relative_to(env["root"])): added}
    else:
        assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("case_id", ["CAP-U05"], ids=["CAP-U05"])
def test_first_actual_required_survives_secondary_scan_close_fault(library, monkeypatch, case_id):
    # Fixed ID CAP-U05; actual marker drives REQUIRED before explicit cleanup IO.
    env = library
    marker(env)
    before = snapshot(env)
    scans = ActualScans(os.scandir, close_error=OSError("synthetic secondary iterator close"))
    with storage_admin.connection(env["profile"]) as con:
        with monkeypatch.context() as patch:
            substitute_scans(patch, scans)
            actual = personal.capabilities(con)
        assert_local(actual, local_oracle(env, writable=False, reason=REQUIRED_REASON))
    assert scans.calls == scans.closed == 1
    assert len(scans.firsts) == 1 and type(scans.firsts[0]) is fence.LegacyAuthorityFenceError
    assert scans.firsts[0].code == "PERSONAL_AUTHORITY_REQUIRED"
    assert "PERSONAL_AUTHORITY_FENCE_CLEANUP_FAILED" in scans.firsts[0].__notes__
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("case_id", ["CAP-F01"], ids=["CAP-F01"])
def test_whole_typed_storage_class_downgrades_writes_without_code_whitelist(library, monkeypatch, case_id):
    env = library
    before = snapshot(env)
    class DerivedStorageFailure(lifecycle.StorageLifecycleError):
        pass
    failures = [lifecycle.StorageLifecycleError("STORAGE_INVALID_PROFILE", 400),
                lifecycle.StorageLifecycleError("SYNTHETIC_UNKNOWN_LIFECYCLE_CODE", 418),
                DerivedStorageFailure("SYNTHETIC_DERIVED_LIFECYCLE_CODE", 451)]
    original = storage_admin.from_connection
    observations = []
    with storage_admin.connection(env["profile"]) as con:
        held = lifecycle.require_guard(env["profile"])
        for failure in failures:
            original_code, original_status = failure.code, failure.status
            own_nodes = []
            def unavailable(actual_con):
                resolved = original(actual_con)
                assert actual_con is con and lifecycle.require_guard(resolved) is held
                observations.append((failure, failure.code, failure.status, resolved.identity))
                own_throw(failure, own_nodes)
            with monkeypatch.context() as patch:
                spy = install_spy(patch)
                patch.setattr(storage_admin, "from_connection", unavailable)
                actual = personal.capabilities(con)
            assert_local(actual, local_oracle(env, writable=False, reason=STATE_REASON))
            assert spy.calls == []
            assert failure.code == original_code and failure.status == original_status
            assert_own_throw_survives(failure, own_nodes)
    assert [value[0] for value in observations] == failures
    assert all(value[0] is failure and value[1:3] == (failure.code, failure.status)
               and value[3] == env["profile"].identity for value, failure in zip(observations, failures))
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("failure_kind", ["unknown-fence-code", "interface-permission", "interface-value"],
                         ids=["CAP-F02", "CAP-F03", "CAP-F04"])
def test_unclassified_errors_from_new_interfaces_propagate_original_object(library, monkeypatch, failure_kind):
    env = library
    before = snapshot(env)
    failure = (fence.LegacyAuthorityFenceError("SYNTHETIC_UNKNOWN_FENCE_CODE") if failure_kind == "unknown-fence-code"
               else PermissionError("synthetic from_connection permission failure") if failure_kind == "interface-permission"
               else ValueError("synthetic from_connection ordinary value failure"))
    own_nodes, called = [], []
    original = storage_admin.from_connection
    with storage_admin.connection(env["profile"]) as con:
        held = lifecycle.require_guard(env["profile"])
        def from_connection(actual_con):
            resolved = original(actual_con)
            assert actual_con is con and lifecycle.require_guard(resolved) is held
            called.append(("from_connection", resolved.identity))
            own_throw(failure, own_nodes)
        def unknown_fence(resolved):
            assert lifecycle.require_guard(resolved) is held
            called.append(("fence", resolved.identity))
            own_throw(failure, own_nodes)
        with monkeypatch.context() as patch:
            if failure_kind == "unknown-fence-code":
                patch.setattr(fence, "assert_legacy_write_allowed_locked", unknown_fence)
            else:
                patch.setattr(storage_admin, "from_connection", from_connection)
            with pytest.raises(type(failure)) as caught:
                personal.capabilities(con)
        assert caught.value is failure
        assert_own_throw_survives(failure, own_nodes)
        assert called == [("fence" if failure_kind == "unknown-fence-code" else "from_connection",
                           env["profile"].identity)]
        if failure_kind == "unknown-fence-code":
            assert failure.code == "SYNTHETIC_UNKNOWN_FENCE_CODE"
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("control", ["keyboard", "exit", "cancel"], ids=["CAP-F05", "CAP-F06", "CAP-F07"])
def test_original_control_and_own_throw_node_survive_actual_close_then_fault(library, monkeypatch, control):
    env = library
    before = snapshot(env)
    failure = (KeyboardInterrupt("synthetic original capability control") if control == "keyboard"
               else SystemExit(93) if control == "exit"
               else asyncio.CancelledError("synthetic original capability cancellation"))
    own_nodes = []
    scans = ActualScans(os.scandir, next_error=failure, own_nodes=own_nodes,
                        close_error=OSError("synthetic secondary close failure"))
    with storage_admin.connection(env["profile"]) as con:
        held = lifecycle.require_guard(env["profile"])
        with monkeypatch.context() as patch:
            substitute_scans(patch, scans)
            with pytest.raises(type(failure)) as caught:
                personal.capabilities(con)
        assert caught.value is failure
        assert_own_throw_survives(failure, own_nodes)
        assert scans.calls == scans.closed == 1 and scans.firsts == [failure]
        assert lifecycle.require_guard(env["profile"]) is held
        assert "PERSONAL_AUTHORITY_FENCE_CLEANUP_FAILED" in failure.__notes__
        if control == "exit":
            assert failure.code == 93
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("state", ["disabled", "uninitialized", "replica", "missing-attachment", "same-length-corruption"],
                         ids=["CAP-P01", "CAP-P02", "CAP-P03", "CAP-P04", "CAP-P05"])
def test_original_false_capability_performs_no_new_profile_or_name_observation(library, tmp_path, monkeypatch, state):
    env = library
    original_source_before = snapshot(env) if state == "replica" else None
    expected_arguments = {}
    if state == "disabled":
        config.set_local({"personal_features_enabled": False})
        expected_arguments["enabled"] = False
    elif state == "uninitialized":
        with storage_admin.connection(env["profile"]) as con:
            con.execute("UPDATE meta SET value='' WHERE key='default_project_id'")
            con.commit()
            damaged_metadata = original_meta(con)
        assert damaged_metadata == {**env["meta"], "default_project_id": ""}
        expected_arguments["meta"] = damaged_metadata
    elif state == "replica":
        original = env
        env = actual_copy(original, tmp_path / "actual-capability-replica")
        replica_node = personal_schema.node_identity(env["data"], create=True)
        assert replica_node and replica_node != original["node"]
        env["node"] = replica_node
        selected_data = select_root(monkeypatch, env["root"])
        assert selected_data == env["data"]
        assert env["meta"]["personal_primary_id"] == original["node"]
        expected_arguments["node"] = replica_node
    else:
        path, attachment = actual_upload(env)
        expected_arguments["intact"] = False
        if state == "missing-attachment":
            path.unlink()
            assert not path.exists()
        else:
            original_stat = path.stat()
            corrupt = bytes([BODY[0] ^ 1]) + BODY[1:]
            path.write_bytes(corrupt)
            os.utime(path, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns + 1_000_000_000))
            assert path.stat().st_size == attachment["byte_size"] == len(BODY)
            assert hashlib.sha256(path.read_bytes()).hexdigest() != attachment["sha256"]
    before = snapshot(env)
    with storage_admin.connection(env["profile"]) as con:
        with monkeypatch.context() as patch:
            calls = install_new_call_sentinels(patch)
            actual = personal.capabilities(con)
            assert not personal.can_write(con)
            assert not personal.can_write(con, upload=True, field=True)
        assert calls == []
        assert_local(actual, local_oracle(env, **expected_arguments))
        assert all(actual[key] is False for key in WRITE_KEYS)
        if state not in {"disabled", "uninitialized"}:
            assert actual["personal_read"] is True
    assert_unchanged(env, before)
    assert_released(env)
    if original_source_before is not None:
        assert_unchanged(library, original_source_before)
        assert_released(library)


@pytest.mark.parametrize("state", ["disabled", "actually-installed"], ids=["CAP-C01", "CAP-C02"])
def test_cloud_capability_never_calls_local_profile_or_fence(library, tmp_path, monkeypatch, state):
    source = library
    source_before = snapshot(source)
    if state == "disabled":
        config.set_local({"node_role": "cloud", "personal_cloud_read_enabled": False})
        env = source
        before = snapshot(env)
        with storage_admin.connection(env["profile"]) as con:
            with monkeypatch.context() as patch:
                calls = install_new_call_sentinels(patch)
                actual = personal.capabilities(con)
                assert not personal.can_write(con, 1, upload=True, field=True)
            assert calls == []
        expected = {"library_id": None, "instance_id": None, "personal_primary_id": None,
            "primary_epoch": 0, "personal_read": False, "personal_write": False, "personal_upload": False,
            "personal_integrity_ok": False, "field_checks": False, "field_checks_read": False,
            "field_checks_write": False, "personal_cloud_read_enabled": False,
            "personal_mode": "disabled", "personal_read_version": None,
            "personal_reason": "云端个人镜像读取尚未启用"}
        assert_local(actual, expected)
        assert_unchanged(env, before)
        assert_released(env)
    else:
        env = actual_mirror(source, tmp_path, monkeypatch)
        before = snapshot(env, extra_tables=mirror_schema.TABLES)
        # The independently produced original reader projection supplies its
        # owned read version; fixed identities/descriptors are bound below to
        # actual prepared/committed producer results, not subject cap output.
        with mirror_reader.open_installed_read(env["profile"]) as con:
            observed = mirror_reader.capabilities_for(con)
            assert observed is not None
            assert observed["library_id"] == env["receiver"]["library_id"]
            assert observed["instance_id"] == env["receiver"]["node_id"]
            assert observed["personal_primary_id"] == source["node"]
            assert observed["primary_epoch"] == int(source["meta"]["primary_epoch"])
            version = observed["personal_read_version"]
            assert type(version) is str and len(version) == 64 and set(version) <= set("0123456789abcdef")
            mirror = observed["personal_mirror"]
            assert mirror == {"source_library_id": source["meta"]["library_id"], "source_node_id": source["node"],
                "source_primary_id": source["node"], "source_primary_epoch": int(source["meta"]["primary_epoch"]),
                "installed_revision": env["committed"]["installed_revision"],
                "committed_at": env["committed"]["committed_at"],
                "snapshot_id": env["prepared"]["snapshot_id"], "snapshot_sha256": env["prepared"]["snapshot_sha256"]}
            with monkeypatch.context() as patch:
                calls = install_new_call_sentinels(patch)
                actual = personal.capabilities(con)
                assert not personal.can_write(con, observed["primary_epoch"], upload=True, field=True)
            assert calls == []
            expected = {"library_id": env["receiver"]["library_id"], "instance_id": env["receiver"]["node_id"],
                "personal_primary_id": source["node"], "primary_epoch": int(source["meta"]["primary_epoch"]),
                "personal_read": True, "personal_write": False, "personal_upload": False,
                "personal_integrity_ok": True, "field_checks": True, "field_checks_read": True,
                "field_checks_write": False, "personal_cloud_read_enabled": True,
                "personal_mode": "mirror", "personal_read_version": version,
                "personal_reason": "云端个人镜像仅供只读，请在家庭主实例修改", "personal_mirror": mirror}
            assert type(actual) is dict and frozenset(actual) == LOCAL_KEYS | {"personal_mirror"}
            assert actual == expected
            for key in ("personal_read", *WRITE_KEYS, "personal_integrity_ok", "field_checks",
                        "field_checks_read", "personal_cloud_read_enabled"):
                assert type(actual[key]) is bool and actual[key] is expected[key]
            assert type(actual["primary_epoch"]) is int
            assert all(actual[key] is False for key in WRITE_KEYS)
        assert_unchanged(env, before, extra_tables=mirror_schema.TABLES)
        assert_unchanged(source, source_before)
        assert_released(env)
        assert_released(source)


@pytest.mark.parametrize("case_id", ["CAP-W01"], ids=["CAP-W01"])
def test_field_switch_and_epoch_map_original_can_write_fields(library, monkeypatch, case_id):
    env = library
    config.set_local({"field_checks_enabled": False})
    before = snapshot(env)
    epoch = int(env["meta"]["primary_epoch"])
    selections = [(None, {}, True), (None, {"upload": True}, True),
                  (None, {"field": True}, False), (None, {"upload": True, "field": True}, False),
                  (epoch, {}, True), (epoch + 1, {}, False), (epoch + 1, {"upload": True}, False)]
    with storage_admin.connection(env["profile"]) as con:
        held = lifecycle.require_guard(env["profile"])
        with monkeypatch.context() as patch:
            spy = install_spy(patch)
            actual = personal.capabilities(con)
            for supplied_epoch, flags, expected in selections:
                assert personal.can_write(con, supplied_epoch, **flags) is expected
        assert_local(actual, local_oracle(env, field=False))
        assert actual["personal_write"] is True and actual["personal_upload"] is True
        assert actual["field_checks"] is actual["field_checks_read"] is actual["field_checks_write"] is False
        assert len(spy.calls) == 1 + len(selections)
        assert all(call["returned"] and call["guard"] is held
                   and call["profile"].identity == env["profile"].identity for call in spy.calls)
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("refusal", ["later-marker", "later-observation-io"], ids=["CAP-W02", "CAP-W05"])
def test_previous_none_capability_cannot_replace_independent_writer_fence(library, monkeypatch, refusal):
    env = library
    initial = snapshot(env)
    with storage_admin.connection(env["profile"]) as con:
        with monkeypatch.context() as patch:
            cap_spy = install_spy(patch)
            previous_cap = personal.capabilities(con)
        assert_local(previous_cap, local_oracle(env))
        assert len(cap_spy.calls) == 1 and cap_spy.calls[0]["returned"]
    assert_unchanged(env, initial)
    if refusal == "later-marker":
        marker(env)
    before = snapshot(env)
    applied = []
    scans = None
    def callback(con, changes, base, operation_id):
        applied.append(operation_id)
        con.execute("UPDATE renovation_project SET name='must not write through old capability'")
        return {"saved": True}
    with monkeypatch.context() as patch:
        writer_spy = install_spy(patch)
        if refusal == "later-observation-io":
            scans = ActualScans(os.scandir, acquire_error=OSError("synthetic later writer census interface"))
            substitute_scans(patch, scans)
        with pytest.raises(personal.PersonalError) as caught:
            if refusal == "later-marker":
                personal.mutate("synthetic:capability-independent-writer", envelope(env, {}, base=0), callback)
            else:
                with storage_admin.connection(env["profile"]) as con:
                    personal.require_write(con, int(env["meta"]["primary_epoch"]))
    expected_code = "PERSONAL_AUTHORITY_REQUIRED" if refusal == "later-marker" else "PERSONAL_AUTHORITY_STATE_UNAVAILABLE"
    assert caught.value.code == expected_code and caught.value.status == 503
    assert str(caught.value) == (REQUIRED_REASON if refusal == "later-marker" else STATE_REASON)
    assert applied == []
    assert len(writer_spy.calls) == 1 and not writer_spy.calls[0]["returned"]
    assert writer_spy.calls[0]["error"].code == expected_code
    if scans is not None:
        assert scans.calls == scans.closed == 1
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("case_id", ["CAP-W03"], ids=["CAP-W03"])
def test_original_writer_fence_precedes_capability_and_epoch(library, monkeypatch, case_id):
    env = library
    marker(env)
    before = snapshot(env)
    capability_calls = []
    def forbidden_capability(*args, **kwargs):
        capability_calls.append(True)
        raise AssertionError("Independent writer fence reached capability inspection")
    with storage_admin.connection(env["profile"]) as con:
        with monkeypatch.context() as patch:
            spy = install_spy(patch)
            patch.setattr(personal, "capabilities", forbidden_capability)
            with pytest.raises(personal.PersonalError) as caught:
                personal.require_write(con, int(env["meta"]["primary_epoch"]) + 1, upload=True, field=True)
        assert caught.value.code == "PERSONAL_AUTHORITY_REQUIRED" and caught.value.status == 503
        assert str(caught.value) == REQUIRED_REASON
        assert capability_calls == []
        assert len(spy.calls) == 1 and spy.calls[0]["error"].code == "PERSONAL_AUTHORITY_REQUIRED"
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("case_id", ["CAP-W04"], ids=["CAP-W04"])
def test_original_epoch_rejection_runs_writer_and_capability_fences(library, monkeypatch, case_id):
    env = library
    before = snapshot(env)
    epoch = int(env["meta"]["primary_epoch"])
    with storage_admin.connection(env["profile"]) as con:
        held = lifecycle.require_guard(env["profile"])
        with monkeypatch.context() as patch:
            spy = install_spy(patch)
            with pytest.raises(personal.PersonalError) as caught:
                personal.require_write(con, epoch + 1)
        assert caught.value.code == "PRIMARY_CHANGED" and caught.value.status == 409
        assert caught.value.meta == {"primary_epoch": epoch}
        assert len(spy.calls) == 2
        assert all(call["returned"] and call["guard"] is held for call in spy.calls)
    assert_unchanged(env, before)
    assert_released(env)


@pytest.mark.parametrize("change", ["none", "marker-between-two-capabilities"], ids=["CAP-A01", "CAP-A02"])
def test_actual_http_capability_has_two_independent_fences_and_private_projection(library, monkeypatch, change):
    env = library
    before = snapshot(env)
    added_path = env["data"] / ".reno-personal-authority-between-two-capabilities"
    def add_marker_after_first():
        assert lifecycle.require_guard(env["profile"])
        marker(env, "file", added_path.name)
    with monkeypatch.context() as patch:
        spy = install_spy(patch, after_first=add_marker_after_first if change != "none" else None)
        with actual_client() as client:
            response = client.get("/api/capabilities")
    assert response.status_code == 200, response.text
    assert response.headers["cache-control"] == "private, no-store"
    assert personal_read_api.VERSION_HEADER not in response.headers
    actual = response.json()
    assert frozenset(actual) == LOCAL_KEYS | {"node_role", "processing", "model_write", "tasks",
                                             "task_reason", "library_epoch", "kb_revision"}
    expected = local_oracle(env, writable=False, reason=REQUIRED_REASON) if change != "none" else local_oracle(env)
    assert_local({key: actual[key] for key in LOCAL_KEYS}, expected)
    assert actual["node_role"] == "home" and actual["processing"] is True
    assert actual["model_write"] is True and actual["tasks"] is True and actual["task_reason"] == ""
    original_metadata = {row[0]: row[1] for row in before[0]["meta"]}
    assert type(actual["library_epoch"]) is int and actual["library_epoch"] == int(original_metadata["library_epoch"])
    assert type(actual["kb_revision"]) is int and actual["kb_revision"] == int(original_metadata["kb_revision"])
    assert len(spy.calls) == 2
    assert spy.calls[0]["returned"] and spy.calls[0]["guard"] is spy.calls[1]["guard"]
    assert spy.calls[0]["guard"] is not None
    assert all(call["profile"].identity == env["profile"].identity for call in spy.calls)
    if change == "none":
        assert spy.calls[1]["returned"]
        assert_unchanged(env, before)
    else:
        assert not spy.calls[1]["returned"] and spy.calls[1]["error"].code == "PERSONAL_AUTHORITY_REQUIRED"
        assert sql_state(env) == before[0]
        assert immutable_state(env) == {**before[1], str(added_path.relative_to(env["root"])): MARKER_BODY}
    assert_released(env)


@pytest.mark.parametrize("case_id", ["CAP-A03"], ids=["CAP-A03"])
def test_second_actual_read_result_capability_control_cannot_return_first_success(library, monkeypatch, case_id):
    env = library
    before = snapshot(env)
    failure = KeyboardInterrupt("synthetic second original capability interface control")
    own_nodes, calls = [], []
    original = storage_admin.from_connection
    def second_interface_control(con):
        resolved = original(con)
        held = lifecycle.require_guard(resolved)
        calls.append((con, resolved.identity, held))
        assert con.in_transaction and con.execute("PRAGMA query_only").fetchone()[0] == 1
        if len(calls) == 2:
            own_throw(failure, own_nodes)
        assert len(calls) == 1
        return resolved
    with monkeypatch.context() as patch:
        spy = install_spy(patch)
        patch.setattr(storage_admin, "from_connection", second_interface_control)
        with pytest.raises(KeyboardInterrupt) as caught:
            personal_read_api.read_result(lambda con: personal.capabilities(con), gate=False)
    assert caught.value is failure
    assert_own_throw_survives(failure, own_nodes)
    assert len(calls) == 2 and calls[0][0] is calls[1][0] and calls[0][2] is calls[1][2]
    assert all(value[1] == env["profile"].identity for value in calls)
    assert len(spy.calls) == 1 and spy.calls[0]["returned"]
    with pytest.raises(sqlite3.ProgrammingError):
        calls[0][0].execute("SELECT 1")
    assert_unchanged(env, before)
    assert_released(env)
