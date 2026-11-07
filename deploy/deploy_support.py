"""Pure rendering and bounded local deployment helpers; never invoke ACME.

CLI errors are deliberately sanitized: configuration and credentials must not
appear in argv, exception tracebacks or command output.
"""
from __future__ import annotations

import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import tarfile
import time
import urllib.error
import urllib.request

MARKER = "# managed by reno-deploy v1"


def domain_name(value):
    if not isinstance(value, str) or len(value) > 253 or value != value.lower() or "." not in value:
        raise ValueError("TLS domain must be a lowercase public DNS hostname")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", part) for part in value.split(".")):
        raise ValueError("TLS domain is invalid")
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return value
    raise ValueError("TLS domain cannot be an IP address")


def port_number(value):
    if not re.fullmatch(r"[0-9]{1,5}", str(value)) or not 1024 <= int(value) <= 65535:
        raise ValueError("Application port must be between 1024 and 65535")
    return int(value)


def safe_root(value):
    path = Path(value)
    if not path.is_absolute() or ".." in path.parts or len(path.parts) < 3:
        raise ValueError("Deployment needs a dedicated absolute directory")
    protected = {"/usr/local", "/var/lib", "/var/www", "/var/log", "/etc/nginx", "/etc/systemd"}
    if path.as_posix().rstrip("/") in protected:
        raise ValueError("Deployment refuses a shared system directory")
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Deployment directories cannot contain symlinks")
    return path


def nginx_path(value):
    value = str(value).replace("\\", "/")
    if (not value or not (value.startswith("/") or re.match(r"[A-Za-z]:/", value))
            or any(ord(c) < 32 for c in value) or any(c in value for c in ('"', "$", "{", "}", ";"))):
        raise ValueError("Nginx paths must be absolute plain paths")
    return '"' + value + '"'


def render_nginx(*, domain, port, webroot, certificate=None, key=None, phase="redirect"):
    domain, port = domain_name(domain), port_number(port)
    if phase not in {"http", "https", "redirect"}:
        raise ValueError("Unknown TLS phase")
    template = Path(__file__).with_name("nginx-reno.conf").read_text(encoding="utf-8")
    if phase == "http":
        template = template.split("\nserver {\n    listen 443 ssl;", 1)[0]
    elif not certificate or not key:
        raise ValueError("TLS certificate paths are required")
    sync_proxy = f"""proxy_pass http://127.0.0.1:{port};
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $remote_addr;
        proxy_set_header X-Forwarded-Proto $scheme;
        limit_req zone=reno_sync burst=20 nodelay;
        proxy_request_buffering off;
        proxy_buffering off;
        proxy_next_upstream off;
        proxy_connect_timeout 5s;
        proxy_send_timeout 30s;
        proxy_read_timeout 3700s;"""
    replacements = {"DOMAIN": domain, "PORT": str(port), "WEBROOT": nginx_path(webroot),
                    "HTTP_FALLBACK": f"return 301 https://{domain}$request_uri;" if phase == "redirect" else "return 503;",
                    "SYNC_PROXY": sync_proxy}
    if phase != "http":
        replacements.update(CERTIFICATE=nginx_path(certificate), KEY=nginx_path(key))
    for name, value in replacements.items():
        template = template.replace(f"__RENO_{name}__", value)
    if "__RENO_" in template:
        raise ValueError("Incomplete nginx template")
    return template


def _plain_file(path):
    path = Path(path)
    if path.is_symlink():
        raise ValueError("Deployment refuses a symlink file")
    if path.exists() and not stat.S_ISREG(path.stat().st_mode):
        raise ValueError("Deployment requires a regular file")
    return path


def read_config(path):
    path = _plain_file(path)
    value = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    if type(value) is not dict:
        raise ValueError("Configuration must be a JSON object")
    return value


def atomic_write(path, content, mode=0o600):
    path = _plain_file(path)
    safe_root(path.parent)
    previous = path.stat() if path.exists() else None
    fd, temporary = tempfile.mkstemp(prefix=".reno-deploy-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            os.fchmod(stream.fileno(), mode) if hasattr(os, "fchmod") else os.chmod(temporary, mode)
            if previous is not None and hasattr(os, "fchown"):
                # --no-service maintenance must retain the existing service owner.
                os.fchown(stream.fileno(), previous.st_uid, previous.st_gid)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        _plain_file(path)
        os.replace(temporary, path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _secret(name, file_name):
    path = os.environ.get(file_name)
    value = (_plain_file(path).read_text(encoding="utf-8").rstrip("\r\n")
             if path else os.environ.get(name, ""))
    if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError("Deployment credential has an invalid format")
    return value


def merge_config(path, role, *, tls=False):
    cfg = read_config(path)
    token = _secret("RENO_DEPLOY_AUTH_TOKEN", "RENO_DEPLOY_AUTH_TOKEN_FILE")
    key = _secret("RENO_DEPLOY_API_KEY", "RENO_DEPLOY_API_KEY_FILE")
    if token:
        cfg["auth_token"] = token
    if key:
        cfg["zhipu_api_key"] = key
    role = role or cfg.get("node_role", "all")
    if role not in ("cloud", "home", "all"):
        raise ValueError("Node role is invalid")
    cfg["node_role"] = role
    if tls:
        if not isinstance(cfg.get("auth_token"), str) or not cfg["auth_token"]:
            raise ValueError("TLS public hosting requires an ordinary API access token")
        # Only the local nginx hop may assert the incoming HTTPS scheme.
        cfg["trusted_proxy_ips"] = ["127.0.0.1", "::1"]
    atomic_write(path, (json.dumps(cfg, ensure_ascii=False, indent=2) + "\n").encode())
    return role


def extract_archive(archive, *, kind, destination, data_dir=None):
    """Extract only approved regular files; archive links/devices never run as root."""
    destination = safe_root(destination)
    data_dir = safe_root(data_dir) if data_dir else None
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if len(members) > 100_000 or sum(max(m.size, 0) for m in members) > 64 * 1024**3:
            raise ValueError("Archive exceeds deployment limits")
        files = {}
        for member in members:
            raw = member.name.replace("\\", "/")
            if raw.startswith("/") or any(p in {".."} or ":" in p for p in raw.split("/")):
                raise ValueError("Archive contains an unsafe pathname")
            name = "/".join(p for p in raw.split("/") if p not in ("", "."))
            if not name:
                continue
            if member.isdir():
                continue
            if not member.isfile() or name in files:
                raise ValueError("Archive contains a link, special file or repeated pathname")
            files[name] = member
        if kind == "dist":
            roots = [name[:-len("index.html")] for name in files if name in {"dist/index.html", "frontend/dist/index.html"}]
            if len(roots) != 1:
                raise ValueError("Frontend archive needs exactly one dist/index.html")
            prefix = roots[0]
            parent = destination / "frontend"
            parent.mkdir(parents=True, exist_ok=True)
            safe_root(parent)
            stage = Path(tempfile.mkdtemp(prefix=".reno-dist-", dir=parent))
            old = parent / (".reno-old-dist-" + stage.name.split("-")[-1])
            target = parent / "dist"
            safe_root(target)
            try:
                for name, member in files.items():
                    if not name.startswith(prefix):
                        continue
                    path = stage / name[len(prefix):]
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, path.open("xb") as dst:
                        shutil.copyfileobj(src, dst)
                if target.exists():
                    os.replace(target, old)
                try:
                    os.replace(stage, target)
                except BaseException:
                    if old.exists():
                        os.replace(old, target)
                    raise
                if old.exists():
                    shutil.rmtree(old)
            finally:
                if stage.exists():
                    shutil.rmtree(stage)
        elif kind == "data" and data_dir is not None:
            if "data/reno.db" not in files:
                raise ValueError("Data archive contains no data/reno.db")
            target = _plain_file(data_dir / "reno.db")
            if target.exists():
                raise ValueError("Import cannot overwrite an existing database")
            data_dir.mkdir(parents=True, exist_ok=True)
            # Preserve the historical whitelist: no cookies, node IDs, private
            # state/outboxes, config files or database backups are imported.
            selected = [(target, files["data/reno.db"])]
            selected.extend((data_dir.parent / name, member) for name, member in files.items() if name.startswith("media/"))
            for path, _ in selected:
                safe_root(path.parent)
                if path.exists() or path.is_symlink():
                    raise ValueError("Import destination already contains media")
            created = []
            try:
                for path, member in selected:
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with tar.extractfile(member) as src, path.open("xb") as dst:
                        created.append(path)
                        shutil.copyfileobj(src, dst)
            except BaseException:
                for path in reversed(created):
                    path.unlink(missing_ok=True)
                raise
        else:
            raise ValueError("Unknown archive operation")


def dns_addresses(domain):
    domain_name(domain)
    addresses = []
    for record in ("A", "AAAA"):
        result = subprocess.run(["dig", "+time=3", "+tries=1", "+short", record, domain],
                                check=True, capture_output=True, text=True, timeout=8)
        for line in result.stdout.splitlines():
            try:
                address = ipaddress.ip_address(line.strip())
            except ValueError:
                continue # CNAME names are accompanied by their resolved addresses.
            if not address.is_global or address.is_multicast or address.is_reserved:
                raise ValueError("TLS DNS must resolve exclusively to public addresses")
            if str(address) not in addresses:
                addresses.append(str(address))
    if not addresses or len(addresses) > 32:
        raise ValueError("TLS DNS has no bounded public destination")
    return addresses


def check_sites(text, domain, managed):
    """nginx -T names each source file; do not shadow another site's exact name."""
    allowed = {str(Path(path)).replace("\\", "/") for path in managed}
    current = None
    for line in text.splitlines():
        if line.startswith("# configuration file ") and line.endswith(":"):
            current = line[len("# configuration file "):-1].replace("\\", "/")
        for match in re.finditer(r"\bserver_name\s+([^;]+);", line.split("#", 1)[0]):
            if domain in match[1].split() and current not in allowed:
                raise ValueError("Domain is already configured by another nginx site")


def health_check(config_path, port, *, attempts=30):
    token = read_config(config_path).get("auth_token", "")
    if not isinstance(token, str) or any(ord(c) < 33 or ord(c) > 126 for c in token):
        raise ValueError("Effective API credential is invalid")
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            return None
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(f"http://127.0.0.1:{port_number(port)}/api/videos",
                                     headers={"X-Reno-Token": token} if token else {})
    for index in range(attempts):
        try:
            with opener.open(request, timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            pass
        if index + 1 < attempts:
            time.sleep(1)
    raise ValueError("Application health check failed; inspect the service locally")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("render-nginx")
    render.add_argument("--domain", required=True)
    render.add_argument("--port", required=True)
    render.add_argument("--webroot", required=True)
    render.add_argument("--certificate")
    render.add_argument("--key")
    render.add_argument("--phase", choices=("http", "https", "redirect"), default="redirect")
    merge = commands.add_parser("merge-config")
    merge.add_argument("path")
    merge.add_argument("--role", default="")
    merge.add_argument("--tls", action="store_true")
    health = commands.add_parser("health")
    health.add_argument("path")
    health.add_argument("--port", required=True)
    domain = commands.add_parser("validate-domain")
    domain.add_argument("value")
    roots = commands.add_parser("validate-root")
    roots.add_argument("value", nargs="+")
    dns = commands.add_parser("dns-addresses")
    dns.add_argument("domain")
    archive = commands.add_parser("extract")
    archive.add_argument("archive")
    archive.add_argument("--kind", choices=("dist", "data"), required=True)
    archive.add_argument("--destination", required=True)
    archive.add_argument("--data-dir")
    sites = commands.add_parser("check-sites")
    sites.add_argument("--domain", required=True)
    sites.add_argument("--managed", nargs="+", required=True)
    role = commands.add_parser("config-role")
    role.add_argument("path")
    args = parser.parse_args()
    try:
        if args.command == "render-nginx":
            print(render_nginx(domain=args.domain, port=args.port, webroot=args.webroot,
                               certificate=args.certificate, key=args.key, phase=args.phase), end="")
        elif args.command == "merge-config":
            print(merge_config(args.path, args.role, tls=args.tls))
        elif args.command == "health":
            health_check(args.path, args.port)
        elif args.command == "validate-domain":
            domain_name(args.value)
        elif args.command == "validate-root":
            for value in args.value:
                safe_root(value)
        elif args.command == "dns-addresses":
            print("\n".join(dns_addresses(args.domain)))
        elif args.command == "extract":
            extract_archive(args.archive, kind=args.kind, destination=args.destination, data_dir=args.data_dir)
        elif args.command == "check-sites":
            domain_name(args.domain)
            check_sites(sys.stdin.read(8 * 1024 * 1024), args.domain, args.managed)
        elif args.command == "config-role":
            role = read_config(args.path).get("node_role", "all")
            if role not in ("cloud", "home", "all"):
                raise ValueError("Node role is invalid")
            print(role)
    except (OSError, ValueError, TypeError, tarfile.TarError, subprocess.SubprocessError):
        print("Deployment validation failed; check configuration, paths and service health locally.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
