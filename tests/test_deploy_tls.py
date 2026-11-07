"""Execute real Bash TLS control flow with harmless in-process command stubs."""
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]
BASH = shutil.which("bash") if os.name != "nt" else None


@pytest.fixture()
def harness(tmp_path):
    if not BASH:
        pytest.skip("Behavioral deployment tests need Bash")
    if os.name == "nt":
        pytest.skip("TLS transaction uses POSIX symlinks; execute this suite in the isolated Linux harness")
    script = tmp_path / "tls-harness.sh"
    script.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
to_path() { if command -v cygpath >/dev/null; then cygpath -u "$1"; else printf '%s' "$1"; fi; }
ROOT="$(to_path "$RENO_TEST_ROOT")"
BASE="$(to_path "$RENO_TEST_BASE")"
PYTHON="$RENO_TEST_PYTHON"
SUPPORT="$ROOT/deploy/deploy_support.py"
PORT=18765 TLS_DOMAIN=reno.example.com TLS_EMAIL=""
say() { :; }
warn() { printf '%s\n' "$*" >&2; }
die() { warn "$*"; exit 1; }
. "$ROOT/deploy/tls.sh"
RENO_NGINX_AVAILABLE="$BASE/nginx/available/reno"
RENO_NGINX_ENABLED="$BASE/nginx/enabled/reno"
RENO_ACME_ROOT="$BASE/acme"
RENO_CERT_ROOT="$BASE/certbot"
mkdir -p "$BASE/nginx/available" "$BASE/nginx/enabled"
record() { printf '%s\n' "$*" >> "$BASE/calls"; }
nginx() {
  record "nginx $*"
  if [ "$1" = -T ]; then
    [ "${CASE:-}" != conflict ] || printf '# configuration file /etc/nginx/sites-enabled/other:\nserver_name reno.example.com;\n'
  fi
  if [ "$1" = -t ] && [ -f "$RENO_NGINX_AVAILABLE" ]; then
    if grep -q 'return 301' "$RENO_NGINX_AVAILABLE"; then record stage:redirect
    elif grep -q 'listen 443 ssl' "$RENO_NGINX_AVAILABLE"; then record stage:https
    else record stage:http; fi
    if [ "${CASE:-}" = nginx_fail ] && grep -q 'listen 443 ssl' "$RENO_NGINX_AVAILABLE"; then return 1; fi
  fi
  return 0
}
systemctl() { record "systemctl $*"; if [ "$1" = is-active ] && [ "${CASE:-}" = inactive ]; then return 3; fi; return 0; }
dig() { record "dig $*"; [ "$4" != A ] || printf '8.8.8.8\n'; }
ss() { record "ss $*"; [ "${CASE:-}" != ssh ] || printf 'LISTEN users:(("sshd",pid=1))\n'; }
ufw() { record "ufw $*"; [ "$1" != status ] || printf 'Status: %s\n' "${FIREWALL:-inactive}"; }
curl() {
  record "curl $*"
  local url="${*: -1}"
  if [ "${CASE:-}" = http_fail ] && [[ "$url" == http:* ]]; then printf wrong; return; fi
  if [ "${CASE:-}" = https_fail ] && [[ "$url" == https:* ]]; then return 22; fi
  printf '%s' "${url##*/}"
}
certbot() {
  record "certbot $*"
  [ "${CASE:-}" != cert_fail ] || return 1
  mkdir -p "$RENO_CERT_ROOT/live/reno-$TLS_DOMAIN"
  printf certificate > "$RENO_CERT_ROOT/live/reno-$TLS_DOMAIN/fullchain.pem"
  printf key > "$RENO_CERT_ROOT/live/reno-$TLS_DOMAIN/privkey.pem"
}
# DNS subprocesses cannot see unexported functions; use an executable stub.
mkdir -p "$BASE/bin"
printf '#!/usr/bin/env bash\n[ "$4" != A ] || printf "8.8.8.8\\n"\n' > "$BASE/bin/dig"
chmod +x "$BASE/bin/dig"
export PATH="$BASE/bin:$PATH"
if [ "${OLD:-}" = yes ]; then
  printf '# managed by reno-deploy v1\nserver_name reno.example.com;\n# OLD_VALID_CONFIGURATION\n' > "$RENO_NGINX_AVAILABLE"
  ln -s "$RENO_NGINX_AVAILABLE" "$RENO_NGINX_ENABLED"
fi
if [ "${CASE:-}" = unrelated ]; then printf unrelated > "$RENO_NGINX_ENABLED"; fi
reno_configure_tls
''', encoding="utf-8", newline="\n")
    def run(case="", old=False, firewall="inactive"):
        env = {**os.environ, "RENO_TEST_ROOT": ROOT.as_posix(), "RENO_TEST_BASE": tmp_path.as_posix(),
               "RENO_TEST_PYTHON": Path(sys.executable).as_posix(), "CASE": case, "OLD": "yes" if old else "",
               "FIREWALL": firewall, "PYTHONUTF8": "1"}
        result = subprocess.run([BASH, "--noprofile", "--norc", script.as_posix()], env=env,
                                capture_output=True, text=True, timeout=30)
        calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
        return result, calls
    return run, tmp_path


def test_first_tls_setup_orders_preflight_certificate_https_then_redirect(harness):
    run, root = harness
    result, calls = run(firewall="active")
    assert result.returncode == 0, result.stderr
    assert calls.index("http://reno.example.com/.well-known/") < calls.index("certbot certonly")
    assert calls.index("certbot certonly") < calls.index("stage:https") < calls.index("stage:redirect")
    assert "--resolve reno.example.com:443:127.0.0.1" in calls
    assert "ufw insert 1 deny 18765/tcp" in calls and "ufw allow 443/tcp" in calls
    assert "ufw enable" not in calls and "ufw reset" not in calls
    assert "systemctl enable --now certbot.timer" in calls
    assert "return 301" in (root / "nginx/available/reno").read_text()


@pytest.mark.parametrize("failure", ["http_fail", "cert_fail", "https_fail", "nginx_fail"])
def test_failures_restore_old_site_and_never_enable_redirect(harness, failure):
    run, root = harness
    result, calls = run(failure, old=True)
    assert result.returncode != 0
    assert (root / "nginx/available/reno").read_text() == "# managed by reno-deploy v1\nserver_name reno.example.com;\n# OLD_VALID_CONFIGURATION\n"
    assert "stage:redirect" not in calls
    if failure == "http_fail": assert "certbot certonly" not in calls


def test_first_install_failure_removes_only_its_own_site(harness):
    run, root = harness
    other = root / "nginx/enabled/other"
    other.parent.mkdir(parents=True)
    other.write_text("unrelated site")
    result, _ = run("cert_fail")
    assert result.returncode != 0 and not (root / "nginx/available/reno").exists()
    assert not (root / "nginx/enabled/reno").exists() and other.read_text() == "unrelated site"


@pytest.mark.parametrize("failure", ["conflict", "unrelated", "ssh"])
def test_unrelated_sites_or_ssh_overlap_stop_before_acme(harness, failure):
    run, _ = harness
    result, calls = run(failure, firewall="active")
    assert result.returncode != 0 and "certbot certonly" not in calls
    if failure == "ssh": assert "ufw insert" not in calls


def test_repeat_tls_deployment_reuses_managed_site_without_http_downgrade(harness):
    run, root = harness
    assert run()[0].returncode == 0
    (root / "calls").write_text("")
    result, calls = run()
    assert result.returncode == 0, result.stderr
    assert "stage:http\n" not in calls and "--keep-until-expiring" in calls


def test_existing_managed_site_can_recover_a_stopped_nginx(harness):
    run, _ = harness
    result, calls = run("inactive", old=True)
    assert result.returncode == 0, result.stderr
    assert "systemctl start nginx" in calls
