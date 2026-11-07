# -*- coding: utf-8 -*-
"""Multi-process robustness: two REAL uvicorn server processes sharing one
data dir (RENO_DATA_DIR), with imports that perform REAL yt-dlp downloads
from a local HTTP file server. Verifies completed queue results, content dedup,
the real cross-process download lock, and reads during a blocked download."""
import concurrent.futures
import errno
import hashlib
import http.server
import json
import os
import queue
import socket
import sqlite3
import subprocess
import sys
import threading
import time
import traceback
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

import pytest

from tests.windows_owned_job import OwnedProcess, owned_drain_proved, owned_original_process, stop_owned

REPO = Path(__file__).resolve().parents[1]
N_PROCESSES = 2


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _post(port: int, path: str, payload: dict, timeout: int = 180):
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}{path}", data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", "replace")
        raise AssertionError(f"HTTP {e.code} on {path}: {body[:300]}") from None


def _get(port: int, path: str, timeout: float = 60):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=timeout) as r:
        return r.status, r.read()


def _wait_task(cluster, task_id, *, timeout=180):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        for port in cluster["ports"]:
            status, raw = _get(port, f"/api/tasks/{task_id}", timeout=3)
            assert status == 200
            last = json.loads(raw)
            if last["status"] in {"done", "failed"}:
                assert last["status"] == "done", last
                assert last["payload"]["process"] is False
                return last["result"]
        time.sleep(.1)
    raise AssertionError(f"Original task {task_id} did not finish: {last}")


def _queue_import(cluster, index, target):
    return _post(cluster["ports"][index % N_PROCESSES], "/api/tasks", {
        "kind": "ingest", "payload": {"target": target, "process": False}})


def _assert_one_content(cluster, sha, video_id):
    path = cluster["data_dir"] / "reno.db"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as con:
        rows = con.execute("SELECT video_id,files_json FROM video_asset WHERE content_sha256=?", (sha,)).fetchall()
        assert len(rows) == 1 and rows[0][0] == video_id
        assert con.execute("SELECT COUNT(*) FROM job WHERE kind='process'").fetchone()[0] == 0
    video = Path(json.loads(rows[0][1])["video"])
    if not video.is_absolute():
        video = cluster["media_root"] / video
    assert video.resolve().is_relative_to(cluster["media_root"].resolve())
    assert hashlib.sha256(video.read_bytes()).hexdigest() == sha


def _server_log_tails(logs):
    """Only artificial fixture instances write here; retain actionable errors."""
    sections = []
    for port, path, stream in logs:
        flush_error = None
        try:
            stream.flush()
        except (OSError, ValueError) as exc:
            flush_error = exc
        try:
            tail = "\n".join(path.read_text(encoding="utf-8", errors="replace").splitlines()[-40:])
        except OSError as exc:
            tail = f"log unavailable: {exc}"
        sections.append(f"server {port}, log {path}:\n{tail or '(empty log)'}"
                        + (f"\nlog flush failed: {flush_error!r}" if flush_error is not None else ""))
    return "\n\n".join(sections)


def _stop_owned_server(owner):
    # Root poll/exit is never the complete Windows process-tree observation.
    return stop_owned(owner, timeout=10)


def _cleanup_stage(errors, stage, action):
    try:
        action()
    except BaseException as error:
        error.add_note("multiprocess cleanup stage: " + stage)
        errors.append(error)


def _report_cleanup_errors(errors, setup_or_helper_primary):
    if not errors:
        return
    if setup_or_helper_primary is not None:
        # Keep the original active setup/helper exception object and traceback.
        # A pytest yield fixture does not receive the test-body exception here.
        for error in errors:
            setup_or_helper_primary.add_note("Additional owned cleanup failure:\n" +
                "".join(traceback.format_exception(error)))
        return
    raise BaseExceptionGroup("Owned multiprocess cleanup failures", errors)


def _assert_server_port_closed(port):
    # Observation only. No PID/port discovery can enlarge process authority.
    with socket.socket() as probe:
        probe.settimeout(1)
        assert probe.connect_ex(("127.0.0.1", port)) != 0, "Owned uvicorn survived fixture teardown"

@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """Two uvicorn processes on one RENO_DATA_DIR + a local file HTTP origin."""
    media_root = tmp_path_factory.mktemp("mp-root")
    data_dir = media_root / "data"
    data_dir.mkdir()
    config_file = media_root / "config.test.json"
    config_file.write_text(json.dumps({"zhipu_api_key": "", "auth_token": "",
                                       "sync_token": "", "vlm_enabled": False,
                                       "budget_enabled": False,
                                       "ytdlp_extra_args": ["--ignore-config"]}), encoding="utf-8")
    guard_root = media_root / "python-guard"
    guard_root.mkdir()
    (guard_root / "sitecustomize.py").write_text("""import socket
_allowed = {"127.0.0.1", "::1", "localhost"}
_resolve, _connect, _connect_ex = socket.getaddrinfo, socket.socket.connect, socket.socket.connect_ex
def _check(host):
    if isinstance(host, bytes): host = host.decode("ascii")
    if host not in _allowed: raise OSError("SYNTHETIC_NON_LOOPBACK_NETWORK_REFUSED")
def _getaddrinfo(host, *args, **kwargs):
    _check(host)
    return _resolve(host, *args, **kwargs)
def _socket_connect(sock, address):
    if isinstance(address, tuple): _check(address[0])
    return _connect(sock, address)
def _socket_connect_ex(sock, address):
    if isinstance(address, tuple): _check(address[0])
    return _connect_ex(sock, address)
socket.getaddrinfo = _getaddrinfo
socket.socket.connect, socket.socket.connect_ex = _socket_connect, _socket_connect_ex
""", encoding="utf-8")
    mp4 = media_root / "seed.mp4"

    import imageio_ffmpeg
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1.2:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(mp4)],
        check=True, capture_output=True)

    from reno import db
    db.initialize(data_dir / "reno.db")

    origin_port = _free_port()
    download_entered, release_download = threading.Event(), threading.Event()

    class OriginHandler(http.server.SimpleHTTPRequestHandler):
        def do_GET(self):
            if self.path.split("?", 1)[0] == "/storm.mp4":
                download_entered.set()
                if not release_download.wait(30):
                    self.send_error(503, "Synthetic download barrier timed out")
                    return
            super().do_GET()

    handler = partial(OriginHandler, directory=str(media_root))
    origin = http.server.ThreadingHTTPServer(("127.0.0.1", origin_port), handler)
    origin_thread = threading.Thread(target=origin.serve_forever, daemon=True)
    procs, owners, logs, ports, log_owners = [], [], [], [], {}
    try:
        origin_thread.start()
        env = {**{k: v for k, v in os.environ.items() if not k.startswith("RENO_")},
               "RENO_QA_ROOT": str(media_root),
               "RENO_CONFIG_FILE": str(config_file),
               "RENO_DATA_DIR": str(data_dir),
               "RENO_LLM_TESTS": "0", "PYTHONUNBUFFERED": "1",
               "PYTHONDONTWRITEBYTECODE": "1", "PYTHONPATH": str(guard_root),
               "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost"}
        ports = [_free_port() for _ in range(N_PROCESSES)]
        try:
            for p in ports:
                log_path = media_root / f"server-{p}.log"
                stream = log_path.open("wb")
                logs.append((p, log_path, stream))
                owner = OwnedProcess()
                owners.append(owner)  # Retain ownership before any child exists.
                log_owners[id(stream)] = owner
                procs.append(owner.launch(
                    [sys.executable, "-m", "uvicorn", "app.main:app",
                     "--host", "127.0.0.1", "--port", str(p)],
                    cwd=str(REPO), env=env, stdout=stream, stderr=subprocess.STDOUT))
            deadline = time.monotonic() + 60
            for p, process in zip(ports, procs):
                last_error = "no health response"
                while True:
                    if process.poll() is not None:
                        raise RuntimeError(f"server {p} exited with code {process.returncode} before healthy")
                    if time.monotonic() >= deadline:
                        raise RuntimeError(f"server {p} did not become healthy: {last_error}")
                    try:
                        status, _ = _get(p, "/api/health", timeout=1)
                        if status == 200:
                            break
                        last_error = f"HTTP {status}"
                    except urllib.error.HTTPError as exc:
                        last_error = f"HTTP {exc.code}"
                    except (OSError, urllib.error.URLError) as exc:
                        last_error = str(exc)
                    time.sleep(0.5)
        except BaseException as exc:
            try:
                exc.add_note(_server_log_tails(logs))
            except BaseException as diagnostic_error:
                exc.add_note("Original server log diagnostic failed: " + repr(diagnostic_error))
            raise

        # Both real lifespans must admit the same completed enrollment. The
        # losing initializer cannot create a second generation or overwrite it.
        from reno import storage_members
        roots = (media_root / "media", data_dir / "generated", data_dir / "sync-checkpoints")
        membership = [json.loads((root / storage_members.REGISTRY).read_text(encoding="utf-8")) for root in roots]
        assert {record["generation"] for record in membership} == {1}
        assert len({record["operation_id"] for record in membership}) == 1

        yield {"ports": ports, "origin_port": origin_port, "mp4": mp4,
               "media_root": media_root, "data_dir": data_dir,
               "config_file": config_file, "process_env": env,
               "download_entered": download_entered, "release_download": release_download}
    finally:
        # All attempts continue independently. The final report preserves native
        # stop errors even when a configured port still accepts a connection.
        primary = sys.exception()
        cleanup_errors = []
        _cleanup_stage(cleanup_errors, "release actual download barrier", release_download.set)
        for index, owner in enumerate(owners):
            _cleanup_stage(cleanup_errors, f"owned uvicorn {index}", lambda owner=owner: _stop_owned_server(owner))
        for port, _path, stream in logs:
            owner = log_owners.get(id(stream))
            if owner is None or owned_drain_proved(owner):
                _cleanup_stage(cleanup_errors, f"log close {port}", stream.close)
            else:
                # The helper retains passed streams with its unproven native
                # owner. Do not close logs before Job0 + original-handle exit.
                error = RuntimeError(f"Log retained for unproven owned uvicorn {port}")
                error.add_note("original owned process stop has not proved complete drain")
                cleanup_errors.append(error)
        if origin_thread.is_alive():
            _cleanup_stage(cleanup_errors, "HTTP origin shutdown", origin.shutdown)
        _cleanup_stage(cleanup_errors, "HTTP origin server_close", origin.server_close)
        if origin_thread.ident is not None:
            _cleanup_stage(cleanup_errors, "HTTP origin join", lambda: origin_thread.join(timeout=5))
        def assert_origin_closed():
            assert not origin_thread.is_alive()
        _cleanup_stage(cleanup_errors, "HTTP origin not alive", assert_origin_closed)
        for port in ports[:len(owners)]:
            _cleanup_stage(cleanup_errors, f"configured port closed {port}",
                           lambda port=port: _assert_server_port_closed(port))
        _report_cleanup_errors(cleanup_errors, primary)
        if primary is None:
            assert not cleanup_errors, cleanup_errors

class TestMultiProcess:
    def test_cross_process_duplicate_import_preseeded(self, cluster):
        """content_sha256 pre-seeded: 4 concurrent imports across 2 processes
        (real yt-dlp downloads from the local origin) all report duplicate."""
        sha = hashlib.sha256(cluster["mp4"].read_bytes()).hexdigest()
        con = __import__("sqlite3").connect(cluster["data_dir"] / "reno.db")
        con.execute(
            "INSERT INTO video_asset (video_id, duration_ms, content_sha256,"
            " files_json, status) VALUES (?,?,?,?,?)",
            ("BVpreseeded", 1200, sha,
             json.dumps({"video": str(cluster["mp4"])}), "processed"))
        con.commit()
        con.close()

        origin = f"http://127.0.0.1:{cluster['origin_port']}/seed.mp4"

        def hit(i):
            return _queue_import(cluster, i, f"{origin}?case=preseeded&claim={i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(hit, range(4)))
        assert all(status == 202 for status, _ in results), results
        task_ids = [body["task_id"] for _, body in results]
        assert len(set(task_ids)) == 4
        completed = [_wait_task(cluster, task_id) for task_id in task_ids]
        statuses = [body["status"] for body in completed]
        assert statuses == ["duplicate"] * 4, results
        assert {body["video_id"] for body in completed} == {"BVpreseeded"}
        _assert_one_content(cluster, sha, "BVpreseeded")
        with sqlite3.connect((cluster["data_dir"] / "reno.db").as_uri() + "?mode=ro", uri=True) as con:
            assert con.execute("SELECT status,duration_ms,files_json FROM video_asset WHERE video_id='BVpreseeded'").fetchone() == (
                "processed", 1200, json.dumps({"video": str(cluster["mp4"])}))

    def test_fresh_content_imported_exactly_once_across_processes(self, cluster):
        """New content: 4 imports across 2 processes -> exactly one
        'imported' from the real cross-process single-writer queue."""
        original = cluster["mp4"].read_bytes()
        fresh = cluster["media_root"] / "seed2.mp4"
        fresh.write_bytes(original[:-1] + bytes([original[-1] ^ 1]))
        sha = hashlib.sha256(fresh.read_bytes()).hexdigest()
        assert sha != hashlib.sha256(original).hexdigest()
        origin = f"http://127.0.0.1:{cluster['origin_port']}/seed2.mp4"

        def hit(i):
            return _queue_import(cluster, i, f"{origin}?case=fresh&claim={i}")

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(hit, range(4)))
        assert all(status == 202 for status, _ in results), results
        task_ids = [body["task_id"] for _, body in results]
        assert len(set(task_ids)) == 4
        completed = [_wait_task(cluster, task_id) for task_id in task_ids]
        statuses = [body["status"] for body in completed]
        assert statuses.count("imported") == 1, results
        assert statuses.count("duplicate") == 3, results
        video_ids = {body["video_id"] for body in completed}
        assert len(video_ids) == 1
        _assert_one_content(cluster, sha, video_ids.pop())

    def test_reads_available_during_import_storm(self, cluster):
        """Both servers read while the actual origin request is held open."""
        original = cluster["mp4"].read_bytes()
        storm = cluster["media_root"] / "storm.mp4"
        storm.write_bytes(original[:-1] + bytes([original[-1] ^ 2]))
        origin = f"http://127.0.0.1:{cluster['origin_port']}/storm.mp4?r=storm"
        status, receipt = _queue_import(cluster, 0, origin)
        assert status == 202
        try:
            assert cluster["download_entered"].wait(20), "Worker never requested the actual origin"
            _, raw = _get(cluster["ports"][1], f"/api/tasks/{receipt['task_id']}")
            assert json.loads(raw)["status"] == "running"
            with concurrent.futures.ThreadPoolExecutor(max_workers=5) as ex:
                reads = list(ex.map(lambda i: _get(cluster["ports"][i % N_PROCESSES], "/api/videos", timeout=3), range(5)))
            assert all(status == 200 for status, _ in reads)
            assert not cluster["release_download"].is_set()
        finally:
            cluster["release_download"].set()
        result = _wait_task(cluster, receipt["task_id"])
        assert result["status"] == "imported"
        _assert_one_content(cluster, hashlib.sha256(storm.read_bytes()).hexdigest(), result["video_id"])

    def test_real_download_lock_excludes_another_process_until_release(self, cluster):
        """The queue serializes jobs; separately prove the actual OS lock."""
        helper = REPO / "tests" / "multiprocess_ingest_lock.py"
        processes, owners, readers, events, runtime_pids = [], [], [], {}, {}
        try:
            for role in ("holder", "contender"):
                owner = OwnedProcess()
                owners.append(owner)
                process = owner.launch([sys.executable, "-u", "-B", str(helper), role,
                    str(cluster["media_root"]), str(cluster["config_file"]), str(cluster["data_dir"])],
                    cwd=REPO, env=cluster["process_env"], stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
                processes.append(process)
                messages = queue.Queue()

                def read(stream=process.stdout, destination=messages):
                    for line in stream:
                        destination.put(json.loads(line))

                reader = threading.Thread(target=read, daemon=True)
                reader.start()
                readers.append(reader)
                events[role] = messages
                event = messages.get(timeout=10)
                assert process.poll() is None
                assert event["event"] == ("held" if role == "holder" else "contended")
                # A Windows venv redirector is the Popen child; the interpreter
                # inherits its pipes and is its direct child. Other platforms
                # execute the interpreter directly. Bind all later events to
                # this same owned interpreter rather than the redirector PID.
                assert type(event["pid"]) is int and event["pid"] > 0
                if event["pid"] != process.pid:
                    assert os.name == "nt" and event["parent_pid"] == process.pid
                assert event["pid"] not in runtime_pids.values()
                runtime_pids[role] = event["pid"]
                assert event["source_sha256"] == hashlib.sha256(helper.read_bytes()).hexdigest()
                if role == "holder":
                    original_identity = event["identity"]
                else:
                    assert event["identity"] == original_identity
            lock_path = cluster["media_root"] / "lock-fixture" / ".lock-owned-contention"
            with lock_path.open("a+b") as probe:
                assert [str(os.fstat(probe.fileno()).st_dev), str(os.fstat(probe.fileno()).st_ino)] == original_identity
                with pytest.raises(OSError) as caught:
                    if os.name == "nt":
                        import msvcrt
                        msvcrt.locking(probe.fileno(), msvcrt.LK_NBLCK, 1)
                    else:
                        import fcntl
                        fcntl.flock(probe.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                assert caught.value.errno in {errno.EACCES, errno.EAGAIN, errno.EDEADLK} or getattr(caught.value, "winerror", None) == 33
            processes[0].stdin.write("release\n")
            processes[0].stdin.flush()
            held = events["contender"].get(timeout=10)
            assert held["event"] == "held" and held["pid"] == runtime_pids["contender"]
            assert held["identity"] == original_identity
            processes[1].stdin.write("release\n")
            processes[1].stdin.flush()
            for index, (role, process) in enumerate(zip(("holder", "contender"), processes)):
                original_status = process.wait(timeout=10)
                if original_status != 0:
                    # A root error may leave its descendant holding stderr.
                    # Prove bounded owned Job drain/EOF before the unchanged
                    # original assertion evaluates its diagnostic .read().
                    try:
                        stop_owned(owners[index], timeout=10)
                    except BaseException as error:
                        error.add_note(f"Original native-lock root exit status: {original_status}")
                        raise
                assert process.wait(timeout=10) == 0, process.stderr.read()
                closed = events[role].get(timeout=3)
                assert closed["event"] == "closed" and closed["pid"] == runtime_pids[role]
                assert closed["descriptor_closed"] is True
                assert closed["network_attempts"] == closed["sql_attempts"] == 0
        finally:
            primary = sys.exception()
            cleanup_errors = []
            for index, owner in enumerate(owners):
                _cleanup_stage(cleanup_errors, f"owned native-lock helper {index}",
                               lambda owner=owner: stop_owned(owner, timeout=10))
            for index, reader in enumerate(readers):
                _cleanup_stage(cleanup_errors, f"native-lock reader join {index}",
                               lambda reader=reader: reader.join(timeout=3))
                if reader.is_alive():
                    cleanup_errors.append(RuntimeError(f"Native-lock reader {index} did not stop"))
                    owners[index]._retain_unproven()
            for index, owner in enumerate(owners):
                process = owned_original_process(owner)
                if process is None:
                    continue
                reader_alive = index < len(readers) and readers[index].is_alive()
                if owned_drain_proved(owner) and not reader_alive:
                    for name in ("stdin", "stdout", "stderr"):
                        stream = getattr(process, name)
                        if stream is not None:
                            _cleanup_stage(cleanup_errors, f"native-lock pipe close {index}/{name}", stream.close)
                else:
                    owner._retain_unproven()
                    cleanup_errors.append(RuntimeError(f"Native-lock pipes retained for unproven owner/reader {index}"))
            _report_cleanup_errors(cleanup_errors, primary)
