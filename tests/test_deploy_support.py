"""Deployment helpers use temporary files and local HTTP only, never real ACME."""
import importlib.util
import io
import json
import os
import stat
from pathlib import Path
import tarfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("reno_deploy_support", ROOT / "deploy/deploy_support.py")
support = importlib.util.module_from_spec(spec)
spec.loader.exec_module(support)


@pytest.mark.parametrize("domain", ["../x", "example.com;return", "Example.com", "example..com", "127.0.0.1",
    "*.example.com", "https://example.com", "example.com\n", "example.com."])
def test_domain_rejection_prevents_nginx_injection(domain):
    with pytest.raises(ValueError):
        support.render_nginx(domain=domain, port=8765, webroot="/var/lib/reno-acme", phase="http")


def test_render_phases_require_tls_before_redirect_and_keep_sync_contract(tmp_path):
    args = dict(domain="reno.example.com", port=18765, webroot=tmp_path / "web root",
                certificate=tmp_path / "cert.pem", key=tmp_path / "key.pem")
    bootstrap = support.render_nginx(**args, phase="http")
    tls = support.render_nginx(**args, phase="https")
    final = support.render_nginx(**args, phase="redirect")
    assert "listen 443" not in bootstrap and "proxy_pass" not in bootstrap and "return 503" in bootstrap
    assert "listen 443 ssl" in tls and "return 301" not in tls
    assert "return 301 https://reno.example.com$request_uri;" in final
    assert final.count("proxy_set_header X-Forwarded-Proto $scheme;") == 6
    assert "client_max_body_size 512m" in final and "client_max_body_size 65m" in final
    assert "location = /api/sync/uploads/prepare" in final and "client_max_body_size 4m" in final
    assert "large_client_header_buffers 4 16k" in final and '"code":"SYNC_BUSY"' in final
    assert "__RENO_" not in final and "access_log off;" in final
    assert "proxy_pass http://127.0.0.1:18765" in final


def test_merge_preserves_credentials_and_role_without_putting_them_in_output(tmp_path, monkeypatch):
    path = tmp_path / "config.local.json"
    path.write_text(json.dumps({"auth_token": "keep-private", "node_role": "cloud", "custom": 4}), encoding="utf-8")
    for name in ("RENO_DEPLOY_AUTH_TOKEN", "RENO_DEPLOY_AUTH_TOKEN_FILE", "RENO_DEPLOY_API_KEY", "RENO_DEPLOY_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)
    assert support.merge_config(path, "", tls=True) == "cloud"
    value = json.loads(path.read_text(encoding="utf-8"))
    assert value["auth_token"] == "keep-private" and value["custom"] == 4
    assert value["trusted_proxy_ips"] == ["127.0.0.1", "::1"]
    assert not list(tmp_path.glob(".reno-deploy-*"))


def test_credentials_come_from_files_and_invalid_input_keeps_old_config(tmp_path, monkeypatch):
    path, secret = tmp_path / "config.local.json", tmp_path / "credential"
    path.write_text('{"auth_token":"old"}', encoding="utf-8")
    secret.write_text("replacement\n", encoding="utf-8")
    monkeypatch.setenv("RENO_DEPLOY_AUTH_TOKEN_FILE", str(secret))
    support.merge_config(path, "home")
    before = path.read_bytes()
    assert json.loads(before)["auth_token"] == "replacement"
    secret.write_text("invalid\nheader", encoding="utf-8")
    with pytest.raises(ValueError):
        support.merge_config(path, "cloud")
    assert path.read_bytes() == before


def test_symlink_config_is_never_followed(tmp_path):
    target, link = tmp_path / "target", tmp_path / "config.local.json"
    target.write_text("do not change", encoding="utf-8")
    try:
        link.symlink_to(target)
    except OSError:
        pytest.skip("Creating file symlinks requires permission on this platform")
    with pytest.raises(ValueError):
        support.merge_config(link, "cloud")
    assert target.read_text() == "do not change"


def test_health_uses_retained_token_and_refuses_redirect_exfiltration(tmp_path):
    path = tmp_path / "config.local.json"
    path.write_text('{"auth_token":"kept-secret"}', encoding="utf-8")
    observed = []
    class Target(BaseHTTPRequestHandler):
        def do_GET(self):
            observed.append("leak")
            self.send_response(200); self.end_headers()
        def log_message(self, *args): pass
    target = ThreadingHTTPServer(("127.0.0.1", 0), Target)
    class Source(BaseHTTPRequestHandler):
        redirect = False
        def do_GET(self):
            observed.append(self.headers.get("X-Reno-Token"))
            self.send_response(302 if self.redirect else 200)
            self.send_header("Location", f"http://127.0.0.1:{target.server_port}/steal")
            self.end_headers()
        def log_message(self, *args): pass
    source = ThreadingHTTPServer(("127.0.0.1", 0), Source)
    threads = [threading.Thread(target=server.serve_forever, daemon=True) for server in (source, target)]
    for thread in threads: thread.start()
    try:
        support.health_check(path, source.server_port, attempts=1)
        Source.redirect = True
        with pytest.raises(ValueError):
            support.health_check(path, source.server_port, attempts=1)
        assert observed == ["kept-secret", "kept-secret"]
    finally:
        for server in (source, target): server.shutdown(); server.server_close()
        for thread in threads: thread.join(timeout=2)


def archive(path, files, *, link=None):
    with tarfile.open(path, "w:gz") as tar:
        for name, content in files.items():
            info = tarfile.TarInfo(name); info.size = len(content)
            tar.addfile(info, io.BytesIO(content))
        if link:
            info = tarfile.TarInfo(link); info.type = tarfile.SYMTYPE; info.linkname = "../../private"
            tar.addfile(info)


@pytest.mark.parametrize("name,link", [("../escape", None), ("/absolute", None), ("dist/x", "dist/link")])
def test_archive_traversal_and_links_fail_before_install(tmp_path, name, link):
    tgz = tmp_path / "bad.tgz"
    archive(tgz, {"dist/index.html": b"new", name: b"bad"}, link=link)
    destination = tmp_path / "app"
    with pytest.raises(ValueError):
        support.extract_archive(tgz, kind="dist", destination=destination)
    assert not (destination / "frontend/dist/index.html").exists()


def test_dist_replacement_and_data_whitelist_are_real_file_operations(tmp_path):
    tgz, destination = tmp_path / "dist.tgz", tmp_path / "app"
    (destination / "frontend/dist").mkdir(parents=True)
    (destination / "frontend/dist/index.html").write_text("old")
    archive(tgz, {"frontend/dist/index.html": b"new", "frontend/dist/assets/app.js": b"code"})
    support.extract_archive(tgz, kind="dist", destination=destination)
    assert (destination / "frontend/dist/index.html").read_bytes() == b"new"
    data = tmp_path / "state/data"
    archive(tgz, {"data/reno.db": b"fake-db", "data/dy_cookies.txt": b"never-copy",
                  "data/node-id": b"never-copy", "media/video.mp4": b"media"})
    support.extract_archive(tgz, kind="data", destination=destination, data_dir=data)
    assert (data / "reno.db").read_bytes() == b"fake-db"
    assert not (data / "dy_cookies.txt").exists() and not (data / "node-id").exists()
    with pytest.raises(ValueError):
        support.extract_archive(tgz, kind="data", destination=destination, data_dir=data)


def test_an_unrelated_nginx_domain_cannot_be_shadowed():
    with pytest.raises(ValueError):
        support.check_sites("# configuration file /etc/nginx/sites-enabled/other:\nserver_name reno.example.com;", "reno.example.com", ["/etc/nginx/sites-enabled/reno"])


@pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() != 0, reason="Requires isolated Linux root-owned fixture")
def test_atomic_upgrade_retains_existing_service_owner(tmp_path, monkeypatch):
    path = tmp_path / "config.local.json"
    path.write_text('{"auth_token":"keep"}')
    os.chown(path, 65534, 65534)
    for key in ("RENO_DEPLOY_AUTH_TOKEN_FILE", "RENO_DEPLOY_API_KEY_FILE"):
        monkeypatch.delenv(key, raising=False)
    support.merge_config(path, "cloud")
    info = path.stat()
    assert info.st_uid == 65534 and info.st_gid == 65534
    assert stat.S_IMODE(info.st_mode) == 0o600
