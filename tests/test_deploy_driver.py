"""Run the deploy driver with isolated filesystem and stubbed privileged commands."""
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tarfile

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture()
def driver(tmp_path):
    if os.name == "nt":
        pytest.skip("Full POSIX deploy driver is exercised in the isolated Linux harness")
    template, dest = tmp_path / "template", tmp_path / "app"
    template.mkdir()
    (template / "frontend").mkdir()
    (template / "requirements.txt").write_text("")
    archive = tmp_path / "dist.tgz"
    with tarfile.open(archive, "w:gz") as tar:
        item = tarfile.TarInfo("dist/index.html"); item.size = 5
        tar.addfile(item, io.BytesIO(b"index"))
    harness = tmp_path / "driver.sh"
    harness.write_text(r'''#!/usr/bin/env bash
set -euo pipefail
record() { printf '%s\n' "$*" >> "$RENO_DRIVER_ROOT/calls"; }
git() {
  record "git $*"
  if [ "$1" = clone ]; then
    local target="${*: -1}"
    cp -R "$RENO_DRIVER_ROOT/template" "$target"
    mkdir "$target/.git"
    return
  fi
  case "$*" in
    *' status --porcelain') [ "${DIRTY:-0}" = 0 ] || printf ' M tracked.py\n';;
    *' branch --show-current') printf 'main\n';;
    *' rev-list --count '*) printf '%s\n' "${LOCAL_COMMITS:-0}";;
  esac
  return 0
}
python3() {
  record "python3 $1"
  if [[ "$1" == -c && "$2" == *serialize* && "${CHECKPOINT_RUNTIME_FAIL:-0}" == 1 ]]; then
    return 1
  fi
  if [ "$1" = -m ] && [ "$2" = venv ]; then
    mkdir -p "$3/bin"
    cat > "$3/bin/python" <<'PYTHON'
#!/usr/bin/env bash
set -euo pipefail
if [[ "$1" == -c && "$2" == 'from reno '* ]]; then
  printf 'prepare-db\n' >> "$RENO_DRIVER_ROOT/calls"
  exit "${PREPARE_FAIL:-0}"
fi
if [ "${2:-}" = health ]; then
  printf 'health\n' >> "$RENO_DRIVER_ROOT/calls"
  exec "$RENO_REAL_PYTHON" -c 'import json,sys; assert json.load(open(sys.argv[1]))["auth_token"] == "retained-secret"' "$3"
fi
exec "$RENO_REAL_PYTHON" "$@"
PYTHON
    printf '#!/bin/sh\nexit 0\n' > "$3/bin/pip"
    chmod +x "$3/bin/python" "$3/bin/pip"
    return
  fi
  command "$RENO_REAL_PYTHON" "$@"
}
id() { printf '0\n'; }
useradd() { record "useradd $*"; }
chown() { record "chown $*"; }
runuser() { record 'runuser'; shift 3; "$@"; }
systemctl() { record "systemctl $*"; }
mktemp() {
  if [[ "${1:-}" == /etc/systemd/system/* ]]; then command mktemp "$RENO_DRIVER_ROOT/unit.XXXXXX";
  else command mktemp "$@"; fi
}
mv() {
  if [ "${*: -1}" = /etc/systemd/system/reno.service ]; then
    local source="${@: -2:1}"; command mv "$source" "$RENO_DRIVER_ROOT/reno.service"
  else command mv "$@"; fi
}
node() { record 'node version check'; return "${NODE_FAIL:-0}"; }
npm() { record "npm $*"; mkdir -p dist; printf index > dist/index.html; }
export -f record git python3 id useradd chown runuser systemctl mktemp mv node npm
exec /bin/bash "$RENO_REPO/deploy/deploy.sh" "$@"
''', encoding="utf-8")
    def run(extra=(), *, env_changes=None, dist=True, clear_log=True):
        if clear_log and (tmp_path / "calls").exists(): (tmp_path / "calls").unlink()
        env = {key: value for key, value in os.environ.items() if not key.startswith("RENO_DEPLOY_")}
        env.update(RENO_DRIVER_ROOT=str(tmp_path), RENO_REPO=str(ROOT), RENO_REAL_PYTHON=sys.executable,
                   RENO_DEPLOY_AUTH_TOKEN="retained-secret")
        env.update(env_changes or {})
        args = ["--skip-deps", "--dest", str(dest)]
        if dist: args += ["--dist-tgz", str(archive)]
        result = subprocess.run(["/bin/bash", str(harness), *args, *extra], env=env, capture_output=True,
                                text=True, timeout=25)
        calls = (tmp_path / "calls").read_text() if (tmp_path / "calls").exists() else ""
        return result, calls
    return run, tmp_path, dest


def test_unsupported_python_sqlite_is_rejected_before_checkout_or_data_mutation(driver):
    run, root, dest = driver
    result, calls = run(["--no-service"], env_changes={"CHECKPOINT_RUNTIME_FAIL": "1"})
    assert result.returncode != 0
    assert "python3 >= 3.11 with SQLite serialize/deserialize required" in result.stderr
    assert not dest.exists()
    assert "git " not in calls and "prepare-db" not in calls
    assert "systemctl" not in calls and "chown" not in calls


def test_no_service_is_install_only_without_health_sleep_or_privileged_commands(driver):
    run, root, dest = driver
    result, calls = run(["--no-service", "--role", "cloud"])
    assert result.returncode == 0, result.stderr
    assert (dest / "frontend/dist/index.html").read_text() == "index"
    assert "prepare-db" in calls
    assert not any(value in calls for value in ("systemctl", "health", "chown", "node version"))
    assert not (root / "reno.service").exists()


def test_upgrade_restarts_and_uses_final_preserved_token_and_role(driver):
    run, root, dest = driver
    first, _ = run(["--role", "cloud"])
    assert first.returncode == 0, first.stderr
    second, calls = run(env_changes={"RENO_DEPLOY_AUTH_TOKEN": ""})
    assert second.returncode == 0, second.stderr
    assert "systemctl enable reno" in calls and "systemctl restart reno" in calls
    assert "systemctl enable --now reno" not in calls
    assert "health" in calls and "retained-secret" not in calls + first.stdout + second.stdout + second.stderr
    config = json.loads((dest / "config.local.json").read_text())
    assert config["node_role"] == "cloud" and config["auth_token"] == "retained-secret"
    assert f"safe.directory={dest}" in calls
    assert f"chown -R reno:reno {dest} {dest}/data {dest}/media {dest}/frames_cache" in calls
    assert "--host 127.0.0.1 --port 8765" in (root / "reno.service").read_text()


def test_schema_failure_cannot_restart_or_health_probe(driver):
    run, _, _ = driver
    result, calls = run(env_changes={"PREPARE_FAIL": "1"})
    assert result.returncode != 0 and "prepare-db" in calls
    assert "systemctl restart" not in calls and "health" not in calls


@pytest.mark.parametrize("extra", [["--tls-domain", "bad;domain"], ["--dest", "/"], ["--data-dir", "/data"],
    ["--port", "22"], ["--port", "99999"], ["--tls-domain", "reno.example.com", "--no-service"],
    ["--auth-token", "do-not-echo-this"]])
def test_unsafe_arguments_fail_before_git_or_any_deployment_effect(driver, extra):
    run, _, _ = driver
    result, calls = run(extra)
    assert result.returncode != 0
    assert "git " not in calls and "systemctl" not in calls
    assert "do-not-echo-this" not in result.stdout + result.stderr


def test_old_node_is_rejected_and_prebuilt_bundle_bypasses_it(driver):
    run, _, _ = driver
    bad, calls = run(["--no-service"], dist=False, env_changes={"NODE_FAIL": "1"})
    assert bad.returncode != 0 and "node version check" in calls and "npm " not in calls
    good, calls = run(["--no-service"], env_changes={"NODE_FAIL": "1"})
    assert good.returncode == 0, good.stderr
    assert "node version" not in calls


@pytest.mark.parametrize("env_changes", [{"DIRTY": "1"}, {"LOCAL_COMMITS": "2"}])
def test_upgrade_preserves_dirty_work_or_unpublished_commits(driver, env_changes):
    run, _, _ = driver
    assert run(["--no-service"])[0].returncode == 0
    result, calls = run(["--no-service"], env_changes=env_changes)
    assert result.returncode != 0 and "merge --quiet --ff-only" not in calls


def test_cloud_never_imports_home_database_even_into_empty_install(driver):
    run, root, dest = driver
    result, calls = run(["--role", "cloud", "--import-data", str(root / "dist.tgz")])
    assert result.returncode != 0 and "git " not in calls
    assert not dest.exists()


@pytest.mark.skipif(os.name == "nt" or getattr(os, "geteuid", lambda: 1)() != 0, reason="Needs a temporary Linux checkout with different owner")
def test_scoped_git_trust_works_for_service_owned_checkout(tmp_path):
    checkout = tmp_path / "checkout"
    subprocess.run(["git", "init", "-q", str(checkout)], check=True)
    # Only this test's temporary empty repository changes ownership.
    for root, dirs, files in os.walk(checkout):
        for entry in dirs + files: os.chown(Path(root) / entry, 65534, 65534)
    os.chown(checkout, 65534, 65534)
    result = subprocess.run(["git", "-c", f"safe.directory={checkout}", "-C", str(checkout), "status", "--porcelain"],
                            capture_output=True, text=True)
    assert result.returncode == 0 and result.stdout == ""
