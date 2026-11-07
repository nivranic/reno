#!/usr/bin/env bash
# Ubuntu/Debian deployment. No certificate is requested without DNS + HTTP
# challenge preflight. Secrets are read from files/environment, never argv.
set +x
set -euo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
SUPPORT="$SCRIPT_DIR/deploy_support.py"
REPO_URL="${RENO_DEPLOY_REPO:-https://github.com/nivranic/reno.git}"
BRANCH=main DEST=/opt/reno DATA_DIR="" HOST=127.0.0.1 PORT=8765 ROLE=""
DIST_TGZ="" IMPORT_TGZ="" TLS_DOMAIN="" TLS_EMAIL="" NO_SERVICE=0 SKIP_DEPS=0
PYTHON=python3

say() { printf '[reno-deploy] %s\n' "$*"; }
warn() { printf '[reno-deploy] %s\n' "$*" >&2; }
die() { warn "$*"; exit 1; }
usage() {
  cat <<'EOF'
usage: deploy.sh [options]
  --repo URL           HTTPS/file git remote without embedded credentials
  --branch NAME        branch to track (default main; fast-forward only)
  --dest DIR           dedicated install directory (default /opt/reno)
  --data-dir DIR       dedicated data directory (default DEST/data)
  --host ADDRESS       bind address (default 127.0.0.1; TLS requires loopback)
  --port NUMBER        application port, 1024..65535 (default 8765)
  --role all|cloud|home preserve an existing role; new installs default to all
  --tls-domain DOMAIN  configure nginx + Certbot HTTP-01, then validated HTTPS
  --tls-email EMAIL    optional ACME renewal contact
  --auth-token-file F  ordinary API credential file (or RENO_DEPLOY_AUTH_TOKEN)
  --api-key-file F     provider credential file (or RENO_DEPLOY_API_KEY)
  --dist-tgz FILE      prebuilt dist/ or frontend/dist/; skips Node completely
  --import-data FILE   first-install data/reno.db + media/ only; forbidden on cloud
  --no-service         install/validate only; no service, TLS or health probing
  --skip-deps          prerequisites already installed
  --help
Legacy --auth-token / --api-key arguments are rejected. Never pass secrets on
the command line. Existing credential values are retained when none supplied.
EOF
}
need_value() { [ "$#" -ge 2 ] || die "option requires a value (see --help)"; }
while [ "$#" -gt 0 ]; do
  case "$1" in
    --repo) need_value "$@"; REPO_URL="$2"; shift 2;;
    --branch) need_value "$@"; BRANCH="$2"; shift 2;;
    --dest) need_value "$@"; DEST="$2"; shift 2;;
    --data-dir) need_value "$@"; DATA_DIR="$2"; shift 2;;
    --host) need_value "$@"; HOST="$2"; shift 2;;
    --port) need_value "$@"; PORT="$2"; shift 2;;
    --role) need_value "$@"; ROLE="$2"; shift 2;;
    --tls-domain) need_value "$@"; TLS_DOMAIN="$2"; shift 2;;
    --tls-email) need_value "$@"; TLS_EMAIL="$2"; shift 2;;
    --auth-token-file) need_value "$@"; export RENO_DEPLOY_AUTH_TOKEN_FILE="$2"; shift 2;;
    --api-key-file) need_value "$@"; export RENO_DEPLOY_API_KEY_FILE="$2"; shift 2;;
    --dist-tgz) need_value "$@"; DIST_TGZ="$2"; shift 2;;
    --import-data) need_value "$@"; IMPORT_TGZ="$2"; shift 2;;
    --auth-token|--api-key) die "secret argv options are no longer supported; use environment or credential files";;
    --no-service) NO_SERVICE=1; shift;;
    --skip-deps) SKIP_DEPS=1; shift;;
    --help|-h) usage; exit 0;;
    *) die "unknown option (see --help)";;
  esac
done

DATA_DIR="${DATA_DIR:-$DEST/data}"
[[ "$DEST" =~ ^/[A-Za-z0-9/._-]+$ && "$DATA_DIR" =~ ^/[A-Za-z0-9/._-]+$ ]] || die "install/data paths need plain absolute directory names"
[[ "$PORT" =~ ^[0-9]{1,5}$ ]] && ((10#$PORT >= 1024 && 10#$PORT <= 65535)) || die "port must be 1024..65535"
PORT="$((10#$PORT))"
[[ "$HOST" =~ ^[A-Za-z0-9.:-]+$ ]] || die "invalid bind address"
case "$ROLE" in ''|all|cloud|home) ;; *) die "role must be all, cloud or home";; esac
[[ "$BRANCH" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]*$ && "$BRANCH" != *..* ]] || die "invalid branch name"
[[ "$REPO_URL" =~ ^(https://|file:///)[A-Za-z0-9./_=-]+$ ]] || die "repo must be HTTPS/file without embedded credentials"
[ -z "$TLS_EMAIL" ] || [[ "$TLS_EMAIL" =~ ^[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+$ ]] || die "invalid ACME email"
command -v python3 >/dev/null || die "python3 >= 3.11 must be available to validate this deployment"
check_python() {
  "$1" -c 'import sys, sqlite3; sys.exit(1) if sys.version_info < (3,11) else None; c=sqlite3.connect(":memory:"); c.execute("CREATE TABLE runtime_probe(value)"); c.execute("INSERT INTO runtime_probe VALUES(1)"); c.commit(); c.deserialize(c.serialize()); assert c.execute("SELECT value FROM runtime_probe").fetchone()[0] == 1; c.close()' >/dev/null 2>&1 || die "python3 >= 3.11 with SQLite serialize/deserialize required"
}
check_python python3
python3 "$SUPPORT" validate-root "$DEST" "$DATA_DIR" "$(dirname "$DATA_DIR")/media" "$(dirname "$DATA_DIR")/frames_cache"
ROLE="${ROLE:-$(python3 "$SUPPORT" config-role "$DEST/config.local.json")}"
if [ -n "$TLS_DOMAIN" ]; then
  python3 "$SUPPORT" validate-domain "$TLS_DOMAIN"
  [ "$NO_SERVICE" -eq 0 ] || die "--tls-domain requires service management; cannot combine with --no-service"
  [ "$HOST" = "127.0.0.1" ] || die "TLS hosting requires --host 127.0.0.1"
fi
if [ -n "$IMPORT_TGZ" ]; then
  [ "$ROLE" != cloud ] || die "cloud nodes need an independent new library; seed via paired sync, not a home database copy"
  [ -f "$IMPORT_TGZ" ] || die "import archive is unavailable"
  [ ! -e "$DATA_DIR/reno.db" ] && [ ! -L "$DATA_DIR/reno.db" ] || die "database already exists; use explicit backed-up maintenance"
fi
[ -z "$DIST_TGZ" ] || [ -f "$DIST_TGZ" ] || die "frontend archive is unavailable"
if [ "$NO_SERVICE" -eq 0 ] || [ "$SKIP_DEPS" -eq 0 ]; then
  [ "$(id -u)" -eq 0 ] || die "system package/service changes require root"
fi

if [ "$SKIP_DEPS" -eq 0 ]; then
  # Refuse an unknown distribution instead of assuming apt implies Ubuntu.
  . /etc/os-release
  case "$ID" in ubuntu|debian) ;; *) die "automatic packages support Debian/Ubuntu only; provision dependencies explicitly";; esac
  export DEBIAN_FRONTEND=noninteractive
  apt-get update -qq
  apt-get install -y -qq python3 python3-venv git curl ca-certificates gnupg >/dev/null
  if [ -n "$TLS_DOMAIN" ]; then
    apt-get install -y -qq nginx certbot bind9-dnsutils iproute2 >/dev/null
  fi
fi

if [ -d "$DEST/.git" ]; then
  # A previous run makes this dedicated checkout reno-owned. Trust only the
  # validated destination for these commands, never change global Git policy.
  checkout_git() { git -c "safe.directory=$DEST" -C "$DEST" "$@"; }
  [ -z "$(checkout_git status --porcelain)" ] || die "checkout has local changes; review before deployment"
  checkout_git fetch --quiet origin "$BRANCH"
  [ "$(checkout_git branch --show-current)" = "$BRANCH" ] || die "checkout branch differs; switch explicitly before deployment"
  [ "$(checkout_git rev-list --count "origin/$BRANCH..HEAD")" = 0 ] || die "checkout has local commits; deployment will not discard them"
  checkout_git merge --quiet --ff-only "origin/$BRANCH"
else
  mkdir -p "$(dirname "$DEST")"
  git clone --quiet --branch "$BRANCH" "$REPO_URL" "$DEST"
fi
say "installing application dependencies"
python3 -m venv "$DEST/.venv"
PYTHON="$DEST/.venv/bin/python"
check_python "$PYTHON"
"$DEST/.venv/bin/pip" install -q --upgrade pip
"$DEST/.venv/bin/pip" install -q -r "$DEST/requirements.txt"
if [ -n "$DIST_TGZ" ]; then
  "$PYTHON" "$SUPPORT" extract "$DIST_TGZ" --kind dist --destination "$DEST"
else
  node_ok() { command -v node >/dev/null && node -e 'const [a,b]=process.versions.node.split(".").map(Number);process.exit(a===20?+(b<19):a===22?+(b<12):+(a<24))'; }
  if ! node_ok && [ "$SKIP_DEPS" -eq 0 ]; then
    install -d -m 0755 /etc/apt/keyrings
    curl --fail --silent --show-error --connect-timeout 10 --max-time 60 https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key \
      | gpg --dearmor --yes -o /etc/apt/keyrings/nodesource.gpg
    chmod 644 /etc/apt/keyrings/nodesource.gpg
    printf '%s\n' 'deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_22.x nodistro main' > /etc/apt/sources.list.d/nodesource.list
    apt-get update -qq
    apt-get install -y -qq nodejs >/dev/null
  fi
  node_ok || die "build requires Node 20.19+, 22.12+ or supported newer LTS; use --dist-tgz otherwise"
  (cd "$DEST/frontend" && npm ci --silent && npm run build --silent)
fi
[ -f "$DEST/frontend/dist/index.html" ] || die "frontend output is missing"

export RENO_DEPLOY_AUTH_TOKEN RENO_DEPLOY_API_KEY
config_args=()
[ -z "$TLS_DOMAIN" ] || config_args+=(--tls)
ROLE="$("$PYTHON" "$SUPPORT" merge-config "$DEST/config.local.json" --role "$ROLE" "${config_args[@]}")"
DATA_BASE="$(dirname "$DATA_DIR")"
mkdir -p "$DATA_DIR" "$DATA_BASE/media" "$DATA_BASE/frames_cache"
if [ -n "$IMPORT_TGZ" ]; then
  "$PYTHON" "$SUPPORT" extract "$IMPORT_TGZ" --kind data --destination "$DEST" --data-dir "$DATA_DIR"
fi

prepare_db='from reno import config,db; db.initialize() if not config.db_path().exists() else None; db.connect().close()'
if [ "$NO_SERVICE" -eq 1 ]; then
  (cd "$DEST" && RENO_DATA_DIR="$DATA_DIR" RENO_CONFIG_FILE="$DEST/config.local.json" "$PYTHON" -c "$prepare_db")
  say "installed and database validated; --no-service skips service start, TLS and health requests"
  say "start from $DEST with RENO_DATA_DIR=$DATA_DIR and serve --host $HOST --port $PORT"
  exit 0
fi
id -u reno >/dev/null 2>&1 || useradd --system --home "$DEST" --shell /usr/sbin/nologin reno
# Never recurse into DATA_BASE: /srv or /home may contain unrelated applications.
chown -R reno:reno "$DEST" "$DATA_DIR" "$DATA_BASE/media" "$DATA_BASE/frames_cache"
(cd "$DEST" && runuser -u reno -- env RENO_DATA_DIR="$DATA_DIR" RENO_CONFIG_FILE="$DEST/config.local.json" "$PYTHON" -c "$prepare_db") \
  || die "database validation failed; stop writers and run the explicit backed-up migrate-db workflow"
unit="$(mktemp /etc/systemd/system/.reno-service.XXXXXX)"
cat > "$unit" <<UNIT
[Unit]
Description=reno renovation knowledge base server
After=network.target

[Service]
User=reno
Group=reno
WorkingDirectory=$DEST
Environment=RENO_DATA_DIR=$DATA_DIR
Environment=RENO_CONFIG_FILE=$DEST/config.local.json
ExecStart=$PYTHON -m reno serve --host $HOST --port $PORT
Restart=on-failure
RestartSec=3
TimeoutStopSec=3700

[Install]
WantedBy=multi-user.target
UNIT
chmod 644 "$unit"
mv -f "$unit" /etc/systemd/system/reno.service
systemctl daemon-reload
systemctl enable reno >/dev/null
# enable --now is a no-op for an already-running unit: always actually restart.
systemctl restart reno
systemctl is-active --quiet reno || die "reno.service did not become active"
"$PYTHON" "$SUPPORT" health "$DEST/config.local.json" --port "$PORT"
if [ -n "$TLS_DOMAIN" ]; then
  . "$SCRIPT_DIR/tls.sh"
  reno_configure_tls
fi
say "done: role=$ROLE, application=$HOST:$PORT, install=$DEST, data=$DATA_DIR"
[ -z "$TLS_DOMAIN" ] || say "validated HTTPS endpoint: https://$TLS_DOMAIN/"
say "credentials preserved in private configuration; inspect service locally with journalctl -u reno"
