"""Real startup enrollment on disposable trees, with no model/cloud requests."""
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
import uuid

import pytest
from fastapi.testclient import TestClient

from reno import config, db, storage_admin, storage_lifecycle as lifecycle, storage_members as members


def _concurrent_initializer(paths, barrier, output):
    """Real processes and OS locks; the hook changes scheduling, not admission."""
    profile = lifecycle.StorageProfile(*(Path(path) for path in paths))
    original = members._group
    @contextmanager
    def synchronized_group(profiles, exclusive):
        if exclusive:
            barrier.wait(timeout=20)
        with original(profiles, exclusive) as protected:
            yield protected
    members._group = synchronized_group
    try:
        result = members.initialize_member(profile)
        output.put({"ok": True, "unchanged": result["unchanged"]})
    except BaseException as exc:
        output.put({"ok": False, "code": getattr(exc, "code", type(exc).__name__)})


@pytest.fixture
def instance(tmp_path, monkeypatch):
    root = tmp_path / "instance"
    root.mkdir()
    local = root / "config.test.json"
    local.write_text(json.dumps({"node_role": "cloud", "web_ui": "classic", "auth_token": "", "sync_token": "",
                                 "zhipu_api_key": ""}), encoding="utf-8")
    monkeypatch.setenv("RENO_CONFIG_FILE", str(local))
    monkeypatch.setenv("RENO_DATA_DIR", str(root / "data"))
    for name, path in {"DATA": root / "data", "MEDIA": root / "media", "ORIG": root / "media/originals",
                       "FRAMES": root / "media/frames", "AUDIO": root / "media/audio", "FRAME_CACHE": root / "frames_cache"}.items():
        monkeypatch.setattr(config, name, path)
    monkeypatch.setattr(config, "_cache", None)
    return root, storage_admin.profile()


def registries(profile):
    return {role: json.loads((getattr(profile, role) / members.REGISTRY).read_text(encoding="utf-8")) for role in members.ROLES}


def own_files(root):
    return {str(p.relative_to(root)): (p.read_bytes(), p.stat().st_mtime_ns) for p in root.rglob("*") if p.is_file()}


def complete(profiles):
    declarations = [{"root": path, "databases": [str(p.database) for p in profiles if path in members._root_map((p,))]}
                    for path in members._root_map(profiles)]
    preview = members.membership_preview(profiles, declarations=declarations)
    assert not preview["blocked"], preview
    members.enroll(profiles, declarations=declarations, expected_digest=preview["digest"], operation_id=str(uuid.uuid4()))


@contextmanager
def cli_server(root):
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = listener.getsockname()[1]
    env = {key: value for key, value in os.environ.items() if not key.startswith("RENO_")}
    env.update(RENO_QA_ROOT=str(root), RENO_CONFIG_FILE=str(root / "config.test.json"),
               RENO_DATA_DIR=str(root / "data"), RENO_LLM_TESTS="0")
    process = subprocess.Popen([sys.executable, "-m", "reno", "serve", "--host", "127.0.0.1", "--port", str(port)],
        cwd=Path(__file__).resolve().parents[1], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        deadline = time.monotonic() + 20
        while True:
            if process.poll() is not None:
                pytest.fail("CLI server failed to start: " + process.communicate()[0])
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/health", timeout=1) as response:
                    assert response.status == 200
                    break
            except (OSError, urllib.error.URLError):
                if time.monotonic() >= deadline:
                    pytest.fail("CLI server did not become healthy")
                time.sleep(0.05)
        yield
    finally:
        if process.poll() is None:
            process.terminate()
        try:
            process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=10)


def test_direct_asgi_new_tree_all_three_roots_fresh_and_requests_do_not_setup(instance, monkeypatch):
    from app.main import create_app
    root, profile = instance
    app = create_app()
    assert not profile.database.parent.exists() and not profile.media.exists()
    with TestClient(app) as client:
        assert client.get("/api/health").status_code == 200
        assert {r["completeness"]["kind"] for r in registries(profile).values()} == {"fresh"}
        def unexpected(*_args, **_kwargs):
            raise AssertionError("Ordinary requests must never perform startup setup")
        monkeypatch.setattr(storage_admin, "setup", unexpected)
        assert client.get("/api/reports").status_code == 200


def test_actual_cli_new_tree_preserves_fresh_evidence_and_second_start_is_idempotent(instance):
    root, profile = instance
    with cli_server(root):
        first = registries(profile)
        assert {r["completeness"]["kind"] for r in first.values()} == {"fresh"}
    # Take metadata only; an OS-terminated web process may leave SQLite's own
    # WAL/SHM, which the next SQLite connection legitimately recovers.
    owned = {p: p.read_bytes() for role in members.ROLES for p in getattr(profile, role).glob(".reno-storage-members-*.json")}
    with cli_server(root):
        assert registries(profile) == first
        assert {p: p.read_bytes() for p in owned} == owned


def test_old_roots_become_unknown_keep_existing_bytes_and_allow_normal_use(instance):
    from app.main import create_app
    root, profile = instance
    db.initialize(profile.database)
    preserved = {}
    for role in members.ROLES:
        directory = getattr(profile, role)
        directory.mkdir()
        path = directory / "legacy-content.bin"
        path.write_bytes(("existing " + role).encode())
        preserved[path] = path.read_bytes()
    database_before = profile.database.read_bytes()
    with TestClient(create_app()) as client:
        assert client.get("/api/reports").status_code == 200
        assert {r["completeness"]["kind"] for r in registries(profile).values()} == {"unknown"}
        with storage_admin.connection(profile) as con:
            members.require_enrolled(profile)
            con.execute("INSERT INTO meta VALUES('ordinary-write-probe','allowed')")
            assert con.execute("SELECT value FROM meta WHERE key='ordinary-write-probe'").fetchone()[0] == "allowed"
            con.rollback()
    assert {path: path.read_bytes() for path in preserved} == preserved
    assert profile.database.read_bytes() == database_before
    before = own_files(root)
    with TestClient(create_app()) as client:
        assert client.get("/api/health").status_code == 200
    assert own_files(root) == before


def test_foreign_member_database_and_unique_root_offline_does_not_block_own_startup(instance):
    from app.main import create_app
    root, a = instance
    db.initialize(a.database)
    storage_admin.setup(a)
    b = lifecycle.StorageProfile(root / "foreign/data/reno.db", a.media, root / "foreign/reports", root / "foreign/checkpoints")
    db.initialize(b.database)
    storage_admin.initialize(b)
    complete([a, b])
    b.database.rename(b.database.with_name("offline.db"))
    b.reports.rename(b.reports.with_name("offline-reports"))
    before = registries(a)
    assert storage_admin.setup(a)["unchanged"]
    with TestClient(create_app()) as client:
        assert client.get("/api/reports").status_code == 200
    assert registries(a) == before
    assert members.membership_preview([a])["blocked"]  # GC still needs the full group.


@pytest.mark.parametrize("damage", ["missing_witness", "partial_registry"])
def test_local_partial_enrollment_refuses_direct_startup_without_repair(instance, damage):
    from app.main import create_app
    root, profile = instance
    db.initialize(profile.database)
    storage_admin.setup(profile)
    value = registries(profile)["reports"]
    if damage == "missing_witness":
        (profile.reports / (members.COMMIT_PREFIX + value["operation_id"] + ".json")).unlink()
    else:
        value["generation"] += 1
        (profile.reports / members.REGISTRY).write_bytes(members._json(value))
    before = own_files(root)
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_ENROLLMENT_REQUIRED"):
        with TestClient(create_app()):
            pytest.fail("Incomplete member must not accept requests")
    assert own_files(root) == before


def test_new_member_cannot_join_shared_root_through_startup_or_create_other_roots(instance):
    root, a = instance
    db.initialize(a.database)
    storage_admin.setup(a)
    b = lifecycle.StorageProfile(root / "new/data/reno.db", a.media, root / "new/reports", root / "new/checkpoints")
    db.initialize(b.database)
    before = registries(a)
    with pytest.raises(members.MembershipError, match="MEMBERSHIP_ENROLLMENT_REQUIRED"):
        storage_admin.setup(b)
    assert not b.reports.exists() and not b.checkpoints.exists()
    assert registries(a) == before


def test_setup_requires_current_schema_without_creating_any_roots(instance):
    _, profile = instance
    with pytest.raises(db.SchemaVersionError):
        storage_admin.setup(profile)
    assert not profile.database.parent.exists() and not profile.media.exists()


def test_two_processes_with_initially_absent_registry_accept_only_complete_winner(instance):
    root, profile = instance
    db.initialize(profile.database)
    context = multiprocessing.get_context("spawn")
    barrier, output = context.Barrier(2), context.Queue()
    paths = tuple(str(getattr(profile, key)) for key in members.FIELDS)
    processes = [context.Process(target=_concurrent_initializer, args=(paths, barrier, output)) for _ in range(2)]
    try:
        for process in processes:
            process.start()
        outcomes = [output.get(timeout=30) for _ in processes]
        for process in processes:
            process.join(timeout=10)
            assert process.exitcode == 0
        assert all(item["ok"] for item in outcomes), outcomes
        assert sorted(item["unchanged"] for item in outcomes) == [False, True]
        recorded = registries(profile)
        assert {row["generation"] for row in recorded.values()} == {1}
        assert len({row["operation_id"] for row in recorded.values()}) == 1
        before = own_files(root)
        assert storage_admin.setup(profile)["unchanged"]
        assert own_files(root) == before
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(timeout=10)
        output.close()


@pytest.mark.parametrize("phase", ["preflight", "final_check"])
def test_facility_initialization_waits_for_same_profile_enrollment_winner(instance, monkeypatch, phase):
    _, profile = instance
    db.initialize(profile.database)
    lifecycle.initialize(profile)  # Existing facilities, no membership yet.
    winner_held, finish_winner = threading.Event(), threading.Event()
    loser_preflight, loser_contended = threading.Event(), threading.Event()
    role = threading.local()
    original_apply, original_preflight, original_attempt = members._apply, lifecycle._preflight, lifecycle._FileLock.attempt
    def apply(candidates, operation_id):
        if getattr(role, "value", None) == "winner":
            winner_held.set()  # Real initialize_member currently owns group EX.
            assert finish_winner.wait(10)
        return original_apply(candidates, operation_id)
    def preflight(*args, **kwargs):
        result = original_preflight(*args, **kwargs)
        if phase == "final_check" and getattr(role, "value", None) == "loser":
            loser_preflight.set()
            assert winner_held.wait(10)
        return result
    def attempt(lock, exclusive):
        acquired = original_attempt(lock, exclusive)
        if not acquired and not exclusive and getattr(role, "value", None) == "loser":
            loser_contended.set()
        return acquired
    def initialize(name):
        role.value = name
        return storage_admin.setup(profile)
    monkeypatch.setattr(members, "_apply", apply)
    monkeypatch.setattr(lifecycle, "_preflight", preflight)
    monkeypatch.setattr(lifecycle._FileLock, "attempt", attempt)
    with ThreadPoolExecutor(max_workers=2) as pool:
        try:
            if phase == "final_check":
                loser = pool.submit(initialize, "loser")
                assert loser_preflight.wait(10)
            winner = pool.submit(initialize, "winner")
            assert winner_held.wait(10)
            if phase == "preflight":
                loser = pool.submit(initialize, "loser")
            assert loser_contended.wait(10), "The actual OS SH lock must meet the winner's EX lock"
            with pytest.raises(lifecycle.StorageLifecycleError) as error:
                storage_admin.status(profile)
            assert error.value.code == "STORAGE_BUSY"  # status(timeout=0) still does not wait.
        finally:
            finish_winner.set()
        assert winner.result(timeout=10)["unchanged"] is False
        assert loser.result(timeout=10)["unchanged"] is True
    assert {row["generation"] for row in registries(profile).values()} == {1}


@pytest.mark.parametrize("phase", ["preflight", "final_check"])
@pytest.mark.parametrize("exception_type", [OSError, FileNotFoundError])
def test_initialization_wait_preserves_cancellation_identity_and_releases_handles(instance, monkeypatch, phase, exception_type):
    root, profile = instance
    lifecycle.initialize(profile)
    before = own_files(root)
    start_holder, held, release, contended = (threading.Event() for _ in range(4))
    cancelled = exception_type("synthetic business cancellation must retain identity")
    original_preflight, original_attempt = lifecycle._preflight, lifecycle._FileLock.attempt
    def hold_exclusive():
        assert start_holder.wait(10)
        with lifecycle.exclusive(profile):
            held.set()
            assert release.wait(10)
    def preflight(*args, **kwargs):
        result = original_preflight(*args, **kwargs)
        if phase == "final_check":
            start_holder.set()
            assert held.wait(10)
        return result
    def attempt(lock, exclusive):
        acquired = original_attempt(lock, exclusive)
        if not exclusive and not acquired:
            contended.set()
        return acquired
    def check():
        if contended.is_set():
            raise cancelled
    monkeypatch.setattr(lifecycle, "_preflight", preflight)
    monkeypatch.setattr(lifecycle._FileLock, "attempt", attempt)
    with ThreadPoolExecutor(max_workers=1) as pool:
        holder = pool.submit(hold_exclusive)
        try:
            if phase == "preflight":
                start_holder.set()
                assert held.wait(10)
            with pytest.raises(exception_type) as caught:
                lifecycle.initialize(profile, timeout=2, check_cancelled=check)
            assert caught.value is cancelled
            assert contended.is_set()  # The cancellation followed a real failed OS SH attempt.
        finally:
            release.set()
        holder.result(timeout=10)
    with lifecycle.exclusive(profile, timeout=0):
        pass
    assert own_files(root) == before
