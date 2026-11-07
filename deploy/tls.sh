#!/usr/bin/env bash
# Sourced by deploy.sh. Functions use explicit paths so isolated tests can
# supply temporary directories without ever touching host nginx/ACME state.
RENO_NGINX_AVAILABLE=/etc/nginx/sites-available/reno
RENO_NGINX_ENABLED=/etc/nginx/sites-enabled/reno
RENO_ACME_ROOT=/var/lib/reno-acme
RENO_CERT_ROOT=/etc/letsencrypt

reno_tls_firewall() {
  command -v ufw >/dev/null 2>&1 || return 0
  local firewall_status
  firewall_status="$(LC_ALL=C ufw status)" || die "cannot determine UFW status; firewall unchanged"
  grep -q '^Status: active' <<< "$firewall_status" || return 0
  # Do not touch an SSH listener even when SSH uses a custom high port.
  local listeners
  listeners="$(ss -H -ltnp "sport = :$PORT")" || die "cannot verify the SSH listener before changing UFW"
  if grep -Eq '(sshd|dropbear)' <<< "$listeners"; then
    die "application port overlaps an SSH listener; firewall unchanged"
  fi
  # Only this exact application-port deny is relocated, so an older allow
  # cannot precede it. No UFW enable/reset/default or SSH policy changes.
  ufw --force delete deny "$PORT/tcp" >/dev/null 2>&1 || true
  ufw insert 1 deny "$PORT/tcp" >/dev/null
  ufw allow 80/tcp >/dev/null
  ufw allow 443/tcp >/dev/null
}

reno_tls_activate() {
  local phase="$1" candidate
  candidate="$(mktemp "$(dirname "$RENO_NGINX_AVAILABLE")/.reno-nginx.XXXXXX")"
  if ! "$PYTHON" "$SUPPORT" render-nginx --domain "$TLS_DOMAIN" --port "$PORT" \
      --webroot "$RENO_ACME_ROOT" --certificate "$CERTIFICATE" --key "$CERT_KEY" \
      --phase "$phase" > "$candidate"; then
    rm -f -- "$candidate"
    return 1
  fi
  chmod 644 "$candidate"
  mv -f -- "$candidate" "$RENO_NGINX_AVAILABLE"
  [ -L "$RENO_NGINX_ENABLED" ] || ln -s "$RENO_NGINX_AVAILABLE" "$RENO_NGINX_ENABLED"
  nginx -t >/dev/null 2>&1 || return 1
  if systemctl is-active --quiet nginx; then
    systemctl reload nginx
  else
    systemctl start nginx
  fi
}

reno_configure_tls() (
  set -euo pipefail
  local old_file=0 old_link=0 committed=0 backup challenge addresses ip resolved content
  local CERTIFICATE="$RENO_CERT_ROOT/live/reno-$TLS_DOMAIN/fullchain.pem"
  local CERT_KEY="$RENO_CERT_ROOT/live/reno-$TLS_DOMAIN/privkey.pem"
  local hook="$RENO_CERT_ROOT/renewal-hooks/deploy/reno-nginx"
  local challenge_dir="$RENO_ACME_ROOT/.well-known/acme-challenge"
  for command in nginx certbot curl dig ss systemctl; do
    command -v "$command" >/dev/null || die "TLS requires nginx, certbot, curl, dig, ss and systemctl"
  done
  [ ! -L "$RENO_NGINX_AVAILABLE" ] || die "managed nginx file must not be a symlink"
  if [ -e "$RENO_NGINX_AVAILABLE" ]; then
    [ -f "$RENO_NGINX_AVAILABLE" ] && grep -qx '# managed by reno-deploy v1' "$RENO_NGINX_AVAILABLE" \
      || die "refusing to replace an nginx file not owned by reno-deploy"
    grep -Fq "server_name $TLS_DOMAIN;" "$RENO_NGINX_AVAILABLE" \
      || die "managed domain changed; use an explicit domain migration"
    old_file=1
  fi
  if [ -e "$RENO_NGINX_ENABLED" ] || [ -L "$RENO_NGINX_ENABLED" ]; then
    [ -L "$RENO_NGINX_ENABLED" ] && [ "$(readlink "$RENO_NGINX_ENABLED")" = "$RENO_NGINX_AVAILABLE" ] \
      || die "refusing to replace an unrelated enabled nginx site"
    old_link=1
  fi
  [ ! -L "$hook" ] || die "renewal hook cannot be a symlink"
  if [ -e "$hook" ]; then
    grep -qx '# managed by reno-deploy v1' "$hook" || die "renewal hook belongs to another administrator"
  fi
  "$PYTHON" "$SUPPORT" validate-root "$(dirname "$RENO_NGINX_AVAILABLE")" \
      "$(dirname "$RENO_NGINX_ENABLED")" "$challenge_dir" "$RENO_CERT_ROOT/live" "$(dirname "$hook")"
  nginx -t >/dev/null 2>&1 || die "existing nginx configuration is invalid; no site was changed"
  nginx -T 2>/dev/null | "$PYTHON" "$SUPPORT" check-sites --domain "$TLS_DOMAIN" \
      --managed "$RENO_NGINX_AVAILABLE" "$RENO_NGINX_ENABLED"
  # Validate every DNS destination before ACME. The random HTTP marker below
  # proves each advertised address reaches this vhost, including NAT hosts.
  addresses="$("$PYTHON" "$SUPPORT" dns-addresses "$TLS_DOMAIN")"
  backup="$(mktemp -d)"
  if [ "$old_file" -eq 1 ]; then cp -p -- "$RENO_NGINX_AVAILABLE" "$backup/site"; fi
  challenge="reno-$("$PYTHON" -c 'import secrets; print(secrets.token_hex(24))')"
  cleanup() {
    local result=$?
    trap - EXIT INT TERM
    rm -f -- "$challenge_dir/$challenge"
    if [ "$committed" -ne 1 ]; then
      if [ "$old_file" -eq 1 ]; then
        cp -p -- "$backup/site" "$RENO_NGINX_AVAILABLE"
      else
        rm -f -- "$RENO_NGINX_AVAILABLE"
      fi
      [ "$old_link" -eq 1 ] || rm -f -- "$RENO_NGINX_ENABLED"
      if nginx -t >/dev/null 2>&1; then
        systemctl reload nginx >/dev/null 2>&1 || true
      fi
      warn "TLS setup failed; restored the previous nginx site. Existing certificates were preserved."
    fi
    rm -rf -- "$backup"
    exit "$result"
  }
  trap cleanup EXIT
  trap 'exit 130' INT
  trap 'exit 143' TERM
  install -d -m 0755 "$challenge_dir"
  printf '%s' "$challenge" > "$challenge_dir/$challenge"
  chmod 644 "$challenge_dir/$challenge"
  reno_tls_firewall
  if [ "$old_file" -eq 0 ]; then
    reno_tls_activate http
  else
    [ "$old_link" -eq 1 ] || ln -s "$RENO_NGINX_AVAILABLE" "$RENO_NGINX_ENABLED"
    nginx -t >/dev/null 2>&1
    if systemctl is-active --quiet nginx; then systemctl reload nginx; else systemctl start nginx; fi
  fi
  while IFS= read -r ip; do
    resolved="$ip"
    [[ "$ip" != *:* ]] || resolved="[$ip]"
    content="$(curl --noproxy '*' --fail --silent --show-error --connect-timeout 5 --max-time 15 \
      --resolve "$TLS_DOMAIN:80:$resolved" "http://$TLS_DOMAIN/.well-known/acme-challenge/$challenge")"
    [ "$content" = "$challenge" ] || die "DNS/HTTP ACME preflight did not reach this server"
  done <<< "$addresses"
  local account_args=(--register-unsafely-without-email)
  [ -z "${TLS_EMAIL:-}" ] || account_args=(--email "$TLS_EMAIL")
  # certonly never rewrites nginx. Keep existing live certificates on failure;
  # unchanged, not-near-expiry certificates are reused on repeated deployment.
  certbot certonly --non-interactive --agree-tos --keep-until-expiring \
    --cert-name "reno-$TLS_DOMAIN" --webroot -w "$RENO_ACME_ROOT" -d "$TLS_DOMAIN" \
    "${account_args[@]}" >/dev/null 2>&1 || die "certificate issuance failed; inspect Certbot locally"
  [ -s "$CERTIFICATE" ] && [ -s "$CERT_KEY" ] || die "Certbot did not provide the requested certificate"
  reno_tls_activate https
  # No -k: validate trust chain and the actual domain before enabling redirect.
  content="$(curl --noproxy '*' --fail --silent --show-error --connect-timeout 5 --max-time 15 \
    --resolve "$TLS_DOMAIN:443:127.0.0.1" "https://$TLS_DOMAIN/.well-known/acme-challenge/$challenge")"
  [ "$content" = "$challenge" ] || die "local HTTPS verification failed"
  content="$(curl --noproxy '*' --fail --silent --show-error --connect-timeout 5 --max-time 15 \
    "https://$TLS_DOMAIN/.well-known/acme-challenge/$challenge")"
  [ "$content" = "$challenge" ] || die "public HTTPS verification failed"
  reno_tls_activate redirect
  install -d -m 0755 "$(dirname "$hook")"
  local staged_hook
  staged_hook="$(mktemp "$(dirname "$hook")/.reno-hook.XXXXXX")"
  printf '%s\n' '#!/bin/sh' '# managed by reno-deploy v1' 'set -eu' 'nginx -t && systemctl reload nginx' > "$staged_hook"
  chmod 755 "$staged_hook"
  mv -f -- "$staged_hook" "$hook"
  systemctl enable nginx >/dev/null
  systemctl enable --now certbot.timer >/dev/null
  committed=1
)
