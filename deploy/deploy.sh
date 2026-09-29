#!/usr/bin/env bash
# reno one-click cloud deploy (Ubuntu / Debian first-class; other distros: see docs/deploy.md)
#
# What it does (idempotent - re-run the same command to update):
#   1. system deps: python3-venv git curl gnupg (+ Node 22 repo when building the SPA on the server)
#   2. clone/update the repo, create .venv, pip install -r requirements.txt
#   3. frontend: build on server (npm ci && npm run build) OR unpack a prebuilt dist tarball
#   4. write config.local.json (auth_token / zhipu_api_key only; other keys preserved)
#   5. optional: import a data tarball (data/ + media/) produced on the dev machine
#   6. install systemd service "reno" (dedicated user, RENO_DATA_DIR, restart on failure)
#   7. health check /api/videos and print a summary
#
# Quick start (defaults: /opt/reno, port 8765, public 0.0.0.0):
#   sudo bash deploy/deploy.sh --auth-token SECRET --api-key ZHIPU_KEY
# Offline frontend (no Node needed on the server):
#   sudo bash deploy/deploy.sh --dist-tgz reno-dist.tgz --auth-token SECRET
# Full story: docs/deploy.md
set -euo pipefail

REPO_URL="${RENO_DEPLOY_REPO:-https://github.com/nivranic/reno.git}"
BRANCH="main"
DEST="/opt/reno"
DATA_DIR="/opt/reno/data"          # becomes RENO_DATA_DIR (db); media lands beside it
HOST="0.0.0.0"
PORT="8765"
ROLE="all"                         # all=full node | cloud=serving only (no pipeline,
                                   # imports enqueue, glm-5.3* refused - sync-plan §1)
DIST_TGZ=""
IMPORT_TGZ=""
AUTH_TOKEN="${RENO_DEPLOY_AUTH_TOKEN:-}"
API_KEY="${RENO_DEPLOY_API_KEY:-}"
NO_SERVICE=0
SKIP_DEPS=0

say()  { printf '\033[1;32m[reno-deploy]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[reno-deploy]\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31m[reno-deploy]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'EOF'
usage: deploy.sh [options]
  --repo URL          git remote (https://... or file:///...); default github.com/nivranic/reno
  --branch NAME       branch to track (default main)
  --dest DIR          install root (default /opt/reno)
  --data-dir DIR      RENO_DATA_DIR - SQLite lives here, media/ is created beside it
  --host / --port     bind address (default 0.0.0.0:8765)
  --role all|cloud|home  node_role config (cloud: serving only, imports queue
                      for the home sync agent, coding-plan models refused)
  --dist-tgz FILE     prebuilt frontend/dist tarball (skip Node entirely on the server)
  --import-data FILE  tarball containing data/ and/or media/ from the dev machine
  --auth-token STR    shared token for the /api gate (empty disables the gate)
  --api-key STR       zhipu API key (needed for /ask + processing pipeline)
  --no-service        install only, skip systemd (tests / WSL)
  --skip-deps         assume python3-venv/git/curl already present
  -h | --help
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --repo) REPO_URL="$2"; shift 2 ;;
    --branch) BRANCH="$2"; shift 2 ;;
    --dest) DEST="$2"; shift 2 ;;
    --data-dir) DATA_DIR="$2"; shift 2 ;;
    --host) HOST="$2"; shift 2 ;;
    --port) PORT="$2"; shift 2 ;;
    --role) ROLE="$2"; shift 2 ;;
    --dist-tgz) DIST_TGZ="$2"; shift 2 ;;
    --import-data) IMPORT_TGZ="$2"; shift 2 ;;
    --auth-token) AUTH_TOKEN="$2"; shift 2 ;;
    --api-key) API_KEY="$2"; shift 2 ;;
    --no-service) NO_SERVICE=1; shift ;;
    --skip-deps) SKIP_DEPS=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "unknown option: $1 (see --help)" ;;
  esac
done

# ---------------------------------------------------- input validation (fail closed)
# Everything below ends up in shell commands or the systemd unit, so only
# conservative character classes are accepted - no whitespace, quotes or
# shell/unit metacharacters, ever.
case "$PORT" in ''|*[!0-9]*) die "--port must be numeric (got: $PORT)" ;; esac
is_abs_path() { [[ "$1" =~ ^/[A-Za-z0-9/._-]*$ && "$1" != *..* ]]; }
is_simple()   { [[ "$1" =~ ^[A-Za-z0-9._:@%+=/-]*$ ]]; }
is_abs_path "$DEST"      || die "--dest must be an absolute path without spaces/metachars (got: $DEST)"
is_abs_path "$DATA_DIR"  || die "--data-dir must be an absolute path without spaces/metachars (got: $DATA_DIR)"
[[ "$HOST"   =~ ^[A-Za-z0-9.:-]+$ ]] || die "--host: only hostname/IP characters allowed (got: $HOST)"
case "$ROLE" in all|cloud|home) ;; *) die "--role must be all|cloud|home (got: $ROLE)" ;; esac
is_simple "$REPO_URL"    || die "--repo: unexpected characters in URL (got: $REPO_URL)"
[[ "$BRANCH" =~ ^[A-Za-z0-9._/-]+$ ]] || die "--branch: invalid branch name (got: $BRANCH)"
is_simple "$AUTH_TOKEN"  || die "--auth-token: only [A-Za-z0-9._:@%+=-] allowed"
is_simple "$API_KEY"     || die "--api-key: only [A-Za-z0-9._:@%+=-] allowed"

DATA_BASE="$(dirname "$DATA_DIR")"   # media/ + frames_cache/ are created beside the data dir

# ---------------------------------------------------------------- 1. system deps
if [ "$SKIP_DEPS" -eq 0 ]; then
  command -v apt-get >/dev/null || die "apt-get not found - this script targets Ubuntu/Debian; install python3-venv git node(>=20.19) manually and re-run with --skip-deps"
  [ "$(id -u)" -eq 0 ] || die "run as root (sudo) so apt/systemd steps work"
  say "installing system deps (python3-venv git curl gnupg)..."
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv git curl gnupg >/dev/null
fi

python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)' \
  || die "python3 >= 3.10 required (found $(python3 -V 2>&1))"

# ---------------------------------------------------------------- 2. code + venv
if [ -d "$DEST/.git" ]; then
  say "updating existing checkout at $DEST (branch $BRANCH)..."
  git -C "$DEST" fetch --quiet origin "$BRANCH"
  git -C "$DEST" status --porcelain | grep -q . && die "local changes in $DEST - review with 'git -C $DEST status' and commit/stash them (do NOT rm -rf the install root: the data dir and media live inside it)"
  git -C "$DEST" checkout --quiet "$BRANCH"
  git -C "$DEST" reset --quiet --hard "origin/$BRANCH"
else
  say "cloning $REPO_URL ($BRANCH) into $DEST..."
  mkdir -p "$(dirname "$DEST")"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$DEST"
fi

say "creating venv + pip install (2-4 min first time)..."
python3 -m venv "$DEST/.venv"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"

# ---------------------------------------------------------------- 3. frontend
if [ -n "$DIST_TGZ" ]; then
  [ -f "$DIST_TGZ" ] || die "--dist-tgz file not found: $DIST_TGZ"
  say "unpacking prebuilt frontend from $DIST_TGZ..."
  TMPD="$(mktemp -d)"
  tar -xzf "$DIST_TGZ" -C "$TMPD"
  # accept dist/, ./dist/ or frontend/dist/ layouts
  SRCD="$(cd "$TMPD" && find . -maxdepth 3 -type d -name dist | head -1)"
  [ -n "$SRCD" ] && [ -f "$TMPD/$SRCD/index.html" ] || die "tarball has no dist/index.html - build it with: cd frontend && npm ci && npm run build && tar -czf reno-dist.tgz dist"
  rm -rf "$DEST/frontend/dist"
  cp -a "$TMPD/$SRCD" "$DEST/frontend/dist"
  rm -rf "$TMPD"
else
  NODE_OK=0
  if command -v node >/dev/null; then
    NODE_MAJOR="$(node -v | sed 's/^v//' | cut -d. -f1)"
    [ "$NODE_MAJOR" -ge 20 ] && NODE_OK=1
  fi
  if [ "$NODE_OK" -eq 0 ] && [ "$SKIP_DEPS" -eq 0 ]; then
    say "adding NodeSource node_22.x apt repo (gpg-verified) and installing nodejs..."
    say "(CN network slow? build the SPA locally and pass --dist-tgz instead)"
    install -d -m 0755 /etc/apt/keyrings
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
    chmod 644 /etc/apt/keyrings/nodesource.gpg
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main" \
      > /etc/apt/sources.list.d/nodesource.list
    apt-get update -qq
    apt-get install -y -qq nodejs >/dev/null
    NODE_OK=1
  fi
  [ "$NODE_OK" -eq 1 ] || die "node >= 20.19 required to build the SPA (or pass --dist-tgz with a prebuilt bundle)"
  say "building frontend (npm ci + vite build, ~1-2 min)..."
  (cd "$DEST/frontend" && npm ci --silent && npm run build --silent)
fi
[ -f "$DEST/frontend/dist/index.html" ] || die "frontend/dist/index.html missing after build/unpack step"

# ---------------------------------------------------------------- 4. config.local.json
PROMPTED=0
if [ -z "$AUTH_TOKEN" ] && [ -t 0 ]; then
  PROMPTED=1
  read -r -p "auth_token for the /api gate (empty = gate off - only safe behind localhost/nginx): " AUTH_TOKEN
fi
if [ -z "$API_KEY" ] && [ -t 0 ]; then
  PROMPTED=1
  read -r -s -p "zhipu API key (empty = /ask disabled until you edit config.local.json): " API_KEY
  printf '\n'
fi
[ "$PROMPTED" -eq 1 ] && warn "prompted values are NOT persisted for re-runs; export RENO_DEPLOY_AUTH_TOKEN / RENO_DEPLOY_API_KEY or pass flags in automation"

say "merging config.local.json (only provided keys; existing keys preserved)..."
[ "$ROLE" = "cloud" ] && say "node_role=cloud: pipeline disabled; imports will queue for the home sync agent"
"$DEST/.venv/bin/python" - "$DEST/config.local.json" "$AUTH_TOKEN" "$API_KEY" "$ROLE" <<'PY'
import json, sys, pathlib
path, token, key, role = (pathlib.Path(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4])
cfg = {}
if path.exists():
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, ValueError):
        sys.exit("config.local.json is not valid JSON - fix or remove it and re-run")
if token:
    cfg["auth_token"] = token
if key:
    cfg["zhipu_api_key"] = key
cfg["node_role"] = role
tmp = path.with_suffix(".json.tmp")
tmp.write_text(json.dumps(cfg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
tmp.replace(path)
PY
chmod 600 "$DEST/config.local.json"

# ---------------------------------------------------------------- 5. data
mkdir -p "$DATA_DIR"
if [ -n "$IMPORT_TGZ" ]; then
  [ -f "$IMPORT_TGZ" ] || die "--import-data file not found: $IMPORT_TGZ"
  say "importing data tarball into $DATA_BASE (overwrites data/, media/)..."
  TMPD="$(mktemp -d)"
  tar -xzf "$IMPORT_TGZ" -C "$TMPD"
  FOUND_DB=0
  for d in data media; do
    SRC="$(cd "$TMPD" && find . -maxdepth 2 -type d -name "$d" | head -1)"
    [ -n "$SRC" ] || continue
    if [ "$d" = "data" ]; then
      # whitelist ONLY the db: the dev data/ dir also holds dy_cookies.txt
      # (platform login session!) and reno.db.bak-* snapshots - those must
      # never reach a public server (sync-plan §3.4)
      [ -f "$TMPD/$SRC/reno.db" ] || die "tarball contains no data/reno.db - nothing imported"
      FOUND_DB=1
      mkdir -p "$DATA_BASE/$d"
      cp -a "$TMPD/$SRC/reno.db" "$DATA_BASE/$d/reno.db"
      continue
    fi
    mkdir -p "$DATA_BASE/$d"
    cp -a "$TMPD/$SRC/." "$DATA_BASE/$d/"
  done
  rm -rf "$TMPD"
  [ "$FOUND_DB" -eq 1 ] || die "tarball contains no data/reno.db - nothing imported"
fi

# ---------------------------------------------------------------- 6. systemd
if [ "$NO_SERVICE" -eq 1 ]; then
  warn "--no-service: skipping systemd; run manually with:"
  warn "  RENO_DATA_DIR=$DATA_DIR $DEST/.venv/bin/python -m reno serve --host $HOST --port $PORT"
else
  id -u reno >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin reno
  chown -R reno:reno "$DEST" "$DATA_BASE"
  say "installing systemd unit reno.service..."
  cat > /etc/systemd/system/reno.service <<UNIT
[Unit]
Description=reno renovation knowledge base server
After=network.target

[Service]
User=reno
Group=reno
WorkingDirectory=$DEST
Environment=RENO_DATA_DIR=$DATA_DIR
ExecStart=$DEST/.venv/bin/python -m reno serve --host $HOST --port $PORT
Restart=on-failure
RestartSec=3

[Install]
WantedBy=multi-user.target
UNIT
  systemctl daemon-reload
  systemctl enable --now reno >/dev/null 2>&1 || systemctl restart reno
fi

# ---------------------------------------------------------------- 7. health check
say "health check (up to 30s)..."
CURL_ARGS=(-fsS -o /dev/null -w '%{http_code}')
[ -n "$AUTH_TOKEN" ] && CURL_ARGS=(-H "X-Reno-Token: $AUTH_TOKEN" "${CURL_ARGS[@]}")
CODE=""
for _ in $(seq 1 30); do
  CODE="$(curl "${CURL_ARGS[@]}" "http://127.0.0.1:$PORT/api/videos" 2>/dev/null || true)"
  [ "$CODE" = "200" ] && break
  sleep 1
done
if [ "$NO_SERVICE" -eq 0 ]; then
  [ "$CODE" = "200" ] || { journalctl -u reno -n 30 --no-pager || true; die "health check failed (last code: ${CODE:-none})"; }
fi

say "done. summary:"
cat <<SUMMARY
  install root : $DEST
  data dir     : $DATA_DIR   (db here; media/ + frames_cache/ beside it)
  node role    : $ROLE$( [ "$ROLE" = "cloud" ] && echo " (pipeline off; imports queue for the home agent)" )
  listen       : $HOST:$PORT   http://<server-ip>:$PORT/
  /api gate    : $( [ -n "$AUTH_TOKEN" ] && echo "ON (X-Reno-Token header / ?token=)" || echo "OFF - set auth_token before exposing publicly!" )
  service      : $( [ "$NO_SERVICE" -eq 0 ] && echo "systemd reno.service (auto-start on boot, restart on failure)" || echo "not installed (--no-service)" )
  update       : re-run this same command (pulls, reinstalls, rebuilds, restarts)
  logs         : journalctl -u reno -f
SUMMARY
