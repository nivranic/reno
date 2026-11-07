"""Real nginx and TLS against an isolated cloud process; no ACME or public ports.

NGINX_TEST_BINARY may select an unpacked nginx executable. Without nginx these
integration tests skip explicitly; CI installs it. Only generated test trust is
injected, and the production client's certificate verification remains enabled.
"""
from contextlib import contextmanager
import http.client
import json
import os
from pathlib import Path
import shutil
import socket
import ssl
import subprocess
import sys
import sysconfig
import time

import httpx
import pytest

from deploy.deploy_support import render_nginx
from reno.sync_client import SyncClient, SyncClientError


REPO = Path(__file__).resolve().parents[1]
SYNC_TOKEN = "synthetic-nginx-sync-token"
BROWSER_TOKEN = "synthetic-nginx-browser-token"


def _binary(variable, name):
    value = os.environ.get(variable) or shutil.which(name)
    if not value:
        pytest.skip(f"Real {name} executable is required for proxy integration")
    return value


def _port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _run(arguments, **options):
    result = subprocess.run(arguments, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, encoding="utf-8", timeout=30,
                            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **options)
    assert result.returncode == 0, f"Isolated proxy setup failed: {result.stdout[-4000:]}"
    return result


@contextmanager
def _process(arguments, log_path, **options):
    with log_path.open("wb") as log:
        process = subprocess.Popen(arguments, stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0, **options)
        try:
            yield process
        finally:
            if process.poll() is None:
                process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


def _ready(process, url, **kwargs):
    deadline = time.monotonic() + 20
    with httpx.Client(trust_env=False, timeout=1, **kwargs) as client:
        while time.monotonic() < deadline:
            assert process.poll() is None, "Isolated proxy/backend exited; inspect its QA log"
            try:
                if client.get(url).status_code == 200:
                    return
            except httpx.RequestError:
                pass
            time.sleep(0.05)
    raise AssertionError("Isolated proxy/backend did not become healthy")


@pytest.fixture(scope="module")
def proxy_runtime(tmp_path_factory):
    nginx = _binary("NGINX_TEST_BINARY", "nginx")
    openssl = _binary("OPENSSL_TEST_BINARY", "openssl")
    root = tmp_path_factory.mktemp("nginx-real-tls")
    assert not root.is_relative_to(REPO) and not REPO.is_relative_to(root)
    webroot = root / "webroot"
    challenge = webroot / ".well-known/acme-challenge/qa-probe"
    challenge.parent.mkdir(parents=True)
    challenge.write_text("isolated-tls-probe", encoding="ascii")
    certificate, key = root / "certificate.pem", root / "key.pem"
    cert_config = root / "openssl.cnf"
    cert_config.write_text("""[req]
distinguished_name=dn
x509_extensions=ext
prompt=no
[dn]
CN=reno-sync.example.test
[ext]
subjectAltName=DNS:reno-sync.example.test,IP:127.0.0.1
basicConstraints=critical,CA:TRUE
keyUsage=critical,digitalSignature,keyEncipherment,keyCertSign
""", encoding="ascii")
    _run([openssl, "req", "-x509", "-newkey", "rsa:2048", "-nodes", "-days", "1",
          "-config", str(cert_config), "-keyout", str(key), "-out", str(certificate)])
    ports = set()
    while len(ports) < 3:
        ports.add(_port())
    backend_port, http_port, https_port = sorted(ports)
    config_path = root / "cloud.json"
    config_path.write_text(json.dumps({"node_role": "cloud", "auth_token": BROWSER_TOKEN,
        "sync_token": SYNC_TOKEN, "trusted_proxy_ips": ["127.0.0.1", "::1"],
        "zhipu_api_key": "", "sync_allow_insecure_test_transport": False,
        "web_ui": "classic"}), encoding="utf-8")
    environment = {k: v for k, v in os.environ.items() if not k.startswith("RENO_")}
    environment.update(RENO_QA_ROOT=str(root), RENO_DATA_DIR=str(root / "data"),
                       RENO_CONFIG_FILE=str(config_path), RENO_LLM_TESTS="0", PYTHONDONTWRITEBYTECODE="1")
    python = sys.executable
    if os.name == "nt":
        # A Windows venv redirector spawns another interpreter; terminating only
        # that wrapper can orphan the actual server. Own the real process.
        python = getattr(sys, "_base_executable", sys.executable)
        environment["PYTHONPATH"] = os.pathsep.join((str(REPO), sysconfig.get_path("purelib")))
        environment["PYTHONNOUSERSITE"] = "1"
    _run([python, "-m", "reno", "init-db"], cwd=REPO, env=environment)
    (root / "logs").mkdir()
    prefix = root.as_posix() + "/"
    main = root / "nginx.conf"
    for phase in ("http", "https", "redirect"):
        rendered = render_nginx(domain="reno-sync.example.test", port=backend_port,
            webroot=webroot, certificate=certificate, key=key, phase=phase)
        # Change only listeners for isolation; all production locations, headers,
        # limits and timeouts remain unchanged.
        rendered = (rendered.replace("listen 80;", f"listen 127.0.0.1:{http_port};")
                    .replace("listen [::]:80;", "")
                    .replace("listen 443 ssl;", f"listen 127.0.0.1:{https_port} ssl;")
                    .replace("listen [::]:443 ssl;", ""))
        main.write_text("daemon off;\nmaster_process off;\nworker_processes 1;\n"
            f'pid "{prefix}nginx.pid";\nerror_log "{prefix}nginx-error.log" notice;\n'
            "events { worker_connections 128; }\nhttp {\n"
            f'client_body_temp_path "{prefix}body";\nproxy_temp_path "{prefix}proxy";\n'
            f'fastcgi_temp_path "{prefix}fastcgi";\nuwsgi_temp_path "{prefix}uwsgi";\n'
            f'scgi_temp_path "{prefix}scgi";\n{rendered}\n}}\n', encoding="utf-8")
        _run([nginx, "-t", "-p", prefix, "-c", main.as_posix()], cwd=root)
    # Final phase is redirect, used below after separately syntax-checking the
    # pre-certificate HTTP phase and pre-redirect HTTPS phase.
    context = ssl.create_default_context(cafile=str(certificate))
    with _process([python, "-m", "reno", "serve", "--host", "127.0.0.1", "--port", str(backend_port)],
                  root / "backend.log", cwd=REPO, env=environment) as backend:
        _ready(backend, f"http://127.0.0.1:{backend_port}/api/health", headers={"X-Reno-Token": BROWSER_TOKEN})
        with _process([nginx, "-p", prefix, "-c", main.as_posix()], root / "proxy.log", cwd=root) as proxy:
            _ready(proxy, f"https://127.0.0.1:{https_port}/.well-known/acme-challenge/qa-probe", verify=context)
            yield {"base": f"https://127.0.0.1:{https_port}", "http": f"http://127.0.0.1:{http_port}",
                   "backend_port": backend_port, "https_port": https_port, "context": context,
                   "certificate": certificate, "root": root}


def test_tls_client_verifies_certificate_and_nginx_forwards_https(proxy_runtime, monkeypatch):
    from reno import sync_client_transport
    runtime = proxy_runtime
    with pytest.raises(SyncClientError) as error:
        SyncClient(runtime["base"], token_provider=lambda: SYNC_TOKEN).handshake()
    assert error.value.code == "SYNC_CLIENT_TLS"
    monkeypatch.setattr(sync_client_transport.certifi, "where", lambda: str(runtime["certificate"]))
    identity = SyncClient(runtime["base"], token_provider=lambda: SYNC_TOKEN).handshake()
    assert identity["library_id"] and identity["node_id"]
    with httpx.Client(verify=runtime["context"], trust_env=False) as client:
        # The proxy must replace a visitor's false scheme assertion with the
        # actual TLS scheme rather than trusting the supplied header.
        response = client.get(runtime["base"] + "/api/sync/handshake",
            headers={"X-Sync-Token": SYNC_TOKEN, "X-Forwarded-Proto": "http"})
        assert response.status_code == 200
        response = client.get(runtime["base"] + "/api/sync/handshake", headers={"X-Reno-Token": BROWSER_TOKEN})
        assert response.status_code == 401
        response = client.get(runtime["base"] + "/api/videos", headers={"X-Sync-Token": SYNC_TOKEN})
        assert response.status_code == 401


def test_plain_http_never_proxies_credentials_and_untrusted_header_cannot_bypass(proxy_runtime):
    runtime = proxy_runtime
    with httpx.Client(trust_env=False, follow_redirects=False) as client:
        response = client.get(runtime["http"] + "/api/sync/handshake",
                              headers={"X-Sync-Token": SYNC_TOKEN, "X-Forwarded-Proto": "https"})
        assert response.status_code == 301
        assert response.headers["location"] == "https://reno-sync.example.test/api/sync/handshake"
        probe = client.get(runtime["http"] + "/.well-known/acme-challenge/qa-probe")
        assert probe.status_code == 200 and probe.text == "isolated-tls-probe"
    # Only 127.0.0.1 and ::1 are trusted. A different loopback source emulates
    # a direct non-proxy peer without opening any external listener.
    connection = http.client.HTTPConnection("127.0.0.1", runtime["backend_port"], timeout=5,
                                            source_address=("127.0.0.2", 0))
    try:
        connection.request("GET", "/api/sync/handshake",
            headers={"X-Sync-Token": SYNC_TOKEN, "X-Forwarded-Proto": "https", "X-Forwarded-For": "127.0.0.1"})
        response = connection.getresponse()
        assert response.status == 403
        assert json.loads(response.read())["code"] == "SYNC_HTTPS_REQUIRED"
    finally:
        connection.close()


@pytest.mark.parametrize("endpoint,declared_size", [("media", 512 * 1024 * 1024 + 1),
    ("push", 65 * 1024 * 1024 + 1), ("uploads/prepare", 4 * 1024 * 1024 + 1),
    ("heartbeat", 64 * 1024 + 1)])
def test_proxy_rejects_oversized_bodies_before_reading_them(proxy_runtime, endpoint, declared_size):
    runtime = proxy_runtime
    connection = http.client.HTTPSConnection("127.0.0.1", runtime["https_port"],
                                             timeout=5, context=runtime["context"])
    try:
        connection.putrequest("POST", "/api/sync/" + endpoint)
        connection.putheader("X-Sync-Token", SYNC_TOKEN)
        connection.putheader("Content-Length", str(declared_size))
        connection.putheader("Content-Type", "application/octet-stream" if endpoint == "media" else "application/json")
        connection.putheader("Expect", "100-continue")
        connection.endheaders()
        response = connection.getresponse()
        assert response.status == 413
        assert json.loads(response.read())["code"] == "INVALID_SYNC_REQUEST"
    finally:
        connection.close()


def test_complete_upload_declaration_can_exceed_control_endpoint_limit(proxy_runtime):
    with httpx.Client(verify=proxy_runtime["context"], trust_env=False) as client:
        response = client.post(proxy_runtime["base"] + "/api/sync/uploads/prepare",
            json={"padding": "x" * (70 * 1024)}, headers={"X-Sync-Token": SYNC_TOKEN})
    # A complete descriptor collection can exceed the old generic 64 KiB
    # limit. Invalid shape must reach the application, rather than proxy 413.
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SYNC_REQUEST"


def test_metadata_header_reaches_application_at_its_documented_limit(proxy_runtime):
    with httpx.Client(verify=proxy_runtime["context"], trust_env=False) as client:
        response = client.post(proxy_runtime["base"] + "/api/sync/media", content=b"",
            headers={"X-Sync-Token": SYNC_TOKEN, "X-Sync-Media": "A" * 8192,
                     "Content-Type": "application/octet-stream"})
    # Invalid JSON metadata is rejected by the application, not nginx's default
    # 8k single-header buffer (which cannot hold this value plus its name).
    assert response.status_code == 400
    assert response.json()["code"] == "INVALID_SYNC_REQUEST"


def test_proxy_rate_limit_has_retryable_json_contract(proxy_runtime):
    with httpx.Client(verify=proxy_runtime["context"], trust_env=False) as client:
        responses = [client.get(proxy_runtime["base"] + "/api/sync/handshake",
                               headers={"X-Sync-Token": SYNC_TOKEN}) for _ in range(25)]
    limited = [response for response in responses if response.status_code == 429]
    assert limited, "Actual nginx burst limit must reject excess requests"
    assert all(response.json()["code"] == "SYNC_BUSY" and response.headers["retry-after"] == "60"
               for response in limited)
