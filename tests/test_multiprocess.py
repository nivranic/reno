# -*- coding: utf-8 -*-
"""Multi-process robustness: two REAL uvicorn server processes sharing one
data dir (RENO_DATA_DIR), with imports that perform REAL yt-dlp downloads
from a local HTTP file server. Verifies the cross-process dedup claim, the
download file lock, and read availability during import storms."""
import concurrent.futures
import hashlib
import http.server
import json
import os
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from functools import partial
from pathlib import Path

import pytest

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


def _get(port: int, path: str):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=60) as r:
        return r.status, r.read()


@pytest.fixture(scope="module")
def cluster(tmp_path_factory):
    """Two uvicorn processes on one RENO_DATA_DIR + a local file HTTP origin."""
    data_dir = tmp_path_factory.mktemp("mp-data")
    media_root = data_dir.parent
    mp4 = media_root / "seed.mp4"

    import imageio_ffmpeg
    subprocess.run(
        [imageio_ffmpeg.get_ffmpeg_exe(), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=duration=1.2:size=320x240:rate=10",
         "-pix_fmt", "yuv420p", str(mp4)],
        check=True, capture_output=True)

    con = __import__("sqlite3").connect(data_dir / "reno.db")
    con.executescript(
        (REPO / "reno" / "schema" / "schema.sql").read_text(encoding="utf-8"))
    con.commit()
    con.close()

    origin_port = _free_port()
    handler = partial(http.server.SimpleHTTPRequestHandler, directory=str(media_root))
    origin = http.server.ThreadingHTTPServer(("127.0.0.1", origin_port), handler)
    threading.Thread(target=origin.serve_forever, daemon=True).start()

    env = {**os.environ,
           "RENO_DATA_DIR": str(data_dir),
           "RENO_VLM_ENABLED": "false"}
    ports = [_free_port() for _ in range(N_PROCESSES)]
    procs = [
        subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app.main:app",
             "--host", "127.0.0.1", "--port", str(p)],
            cwd=str(REPO), env=env,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for p in ports
    ]
    deadline = time.time() + 60
    for p in ports:
        while True:
            try:
                _get(p, "/api/health")
                break
            except urllib.error.HTTPError:
                break
            except Exception:
                if time.time() > deadline:
                    raise RuntimeError(f"server {p} did not come up")
                time.sleep(0.5)

    yield {"ports": ports, "origin_port": origin_port, "mp4": mp4,
           "media_root": media_root, "data_dir": data_dir}

    for pr in procs:
        pr.terminate()
    origin.shutdown()


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
            port = cluster["ports"][i % N_PROCESSES]
            return _post(port, "/api/import", {"url": origin})

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(hit, range(4)))
        statuses = [body["status"] for _, body in results]
        assert statuses == ["duplicate"] * 4, results

    def test_fresh_content_imported_exactly_once_across_processes(self, cluster):
        """New content: 4 imports across 2 processes -> exactly one
        'imported' (cross-process claim arbitration)."""
        (cluster["media_root"] / "seed2.mp4").write_bytes(
            cluster["mp4"].read_bytes()[:-1] + b"\x00")
        origin = f"http://127.0.0.1:{cluster['origin_port']}/seed2.mp4"

        def hit(i):
            port = cluster["ports"][i % N_PROCESSES]
            return _post(port, "/api/import", {"url": origin})

        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:
            results = list(ex.map(hit, range(4)))
        statuses = [body["status"] for _, body in results]
        assert statuses.count("imported") == 1, results
        assert statuses.count("duplicate") == 3, results

    def test_reads_available_during_import_storm(self, cluster):
        """Reads stay served on both processes while an import runs."""
        origin = f"http://127.0.0.1:{cluster['origin_port']}/seed.mp4"
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as ex:
            f_import = ex.submit(_post, cluster["ports"][0], "/api/import",
                                 {"url": f"{origin}?r=storm"})
            f_reads = [ex.submit(_get, cluster["ports"][i % N_PROCESSES],
                                 "/api/videos") for i in range(5)]
            status, _ = f_import.result()
            read_statuses = [f.result()[0] for f in f_reads]
        assert status == 200
        assert all(s == 200 for s in read_statuses)
