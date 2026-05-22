#!/usr/bin/env bash
# One-shot installer for proxmox-mcp-vr. Run inside the management LXC as root.
#
# Idempotent: safe to re-run. Reads any pre-set env vars (PVE_HOST,
# PVE_USER, PVE_TOKEN_NAME, PVE_TOKEN_VALUE, ALLOWED_POOL, MCP_BIND,
# REPO_URL, REPO_REF); prompts for the ones it can't fill in.
#
# If invoked from a git checkout (pyproject.toml exists two levels up),
# installs from that checkout instead of fetching REPO_URL. This is the
# fast path for development and for repo names that differ from the
# default.
#
# Steps 1 + 2 (pool/role/user/token on the PVE host, LXC creation) are
# NOT in this script — they live in deploy/lxc-setup.md and need root
# on the hypervisor, not the LXC.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/devinfosec/proxmox-mcp-crid/main/deploy/install.sh | bash
#   # or, from a local checkout:
#   PVE_HOST=pve.lab.lan PVE_TOKEN_VALUE=xxx bash deploy/install.sh

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/devinfosec/proxmox-mcp-crid.git}"
REPO_REF="${REPO_REF:-main}"
ALLOWED_POOL="${ALLOWED_POOL:-ai-redteam}"
MCP_BIND="${MCP_BIND:-0.0.0.0:8080}"
PVE_USER_DEFAULT="mcp-agent@pve"
PVE_TOKEN_NAME_DEFAULT="mcpvr"
# PVE_VERIFY_SSL=false -> verify_ssl: false + security.dev_mode: true
# (upstream rejects verify_ssl=false without dev_mode). Defaults to true for
# prod; flip to false for self-signed PVE certs (typical out-of-box install).
PVE_VERIFY_SSL="${PVE_VERIFY_SSL:-true}"
# Set FORCE_REWRITE_CONFIG=1 to overwrite an existing /etc/proxmox-mcp/config.json.
FORCE_REWRITE_CONFIG="${FORCE_REWRITE_CONFIG:-0}"

INSTALL_DIR=/opt/proxmox-mcp
CONFIG_DIR=/etc/proxmox-mcp
STATE_DIR=/var/lib/proxmox-mcp
SERVICE_USER=mcp

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOCAL_CHECKOUT=""
if [[ -f "$SCRIPT_DIR/../pyproject.toml" ]]; then
  LOCAL_CHECKOUT="$(cd "$SCRIPT_DIR/.." && pwd)"
fi

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

# Drop privileges to $SERVICE_USER without needing sudo. `runuser` is part
# of util-linux and present on every modern Debian/Ubuntu base image.
as_service_user() { runuser -u "$SERVICE_USER" -- "$@"; }

[[ $EUID -eq 0 ]] || die "run as root"

# ---------- prompt helper -------------------------------------------------
prompt() {
  # prompt VARNAME "message" ["default"] [silent]
  local var="$1" msg="$2" default="${3:-}" silent="${4:-}"
  local current="${!var:-}"
  if [[ -n $current ]]; then
    log "using \$$var from environment"
    return
  fi
  local input
  if [[ -n $silent ]]; then
    read -r -s -p "$msg: " input; echo
  elif [[ -n $default ]]; then
    read -r -p "$msg [$default]: " input
    input="${input:-$default}"
  else
    read -r -p "$msg: " input
  fi
  [[ -n $input ]] || die "$var is required"
  printf -v "$var" '%s' "$input"
}

# ---------- 1. system packages -------------------------------------------
log "installing system packages"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq \
  python3.11 python3.11-venv python3-pip git pwgen ca-certificates

command -v python3.11 >/dev/null || die "python3.11 not on PATH after install"

# ---------- 2. service user + dirs ---------------------------------------
if ! id "$SERVICE_USER" >/dev/null 2>&1; then
  log "creating system user $SERVICE_USER"
  adduser --system --group --home "$INSTALL_DIR" "$SERVICE_USER"
else
  log "user $SERVICE_USER already exists"
fi
install -d -o "$SERVICE_USER" -g "$SERVICE_USER" "$INSTALL_DIR" "$STATE_DIR"
install -d -m 750 -o root -g "$SERVICE_USER" "$CONFIG_DIR"

# ---------- 3. venv + package --------------------------------------------
if [[ ! -x "$INSTALL_DIR/.venv/bin/python" ]]; then
  log "creating venv at $INSTALL_DIR/.venv"
  as_service_user python3.11 -m venv "$INSTALL_DIR/.venv"
fi

log "upgrading pip"
as_service_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade pip

if [[ -n $LOCAL_CHECKOUT ]]; then
  # The checkout may live under a 0700 home (e.g. /root); mcp can't read it
  # directly. Stage a copy to a /tmp path mcp owns, install from there.
  STAGE="$(mktemp -d)"
  log "staging $LOCAL_CHECKOUT to $STAGE for unprivileged install"
  cp -a "$LOCAL_CHECKOUT/." "$STAGE/"
  rm -rf "$STAGE/.venv" "$STAGE/.git" "$STAGE"/*.egg-info "$STAGE/.pytest_cache" \
         "$STAGE/.ruff_cache"
  chown -R "$SERVICE_USER:$SERVICE_USER" "$STAGE"

  log "installing proxmox-mcp-vr from $STAGE"
  as_service_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade "$STAGE"
  rm -rf "$STAGE"
else
  log "installing proxmox-mcp-vr from $REPO_URL @ $REPO_REF"
  as_service_user "$INSTALL_DIR/.venv/bin/pip" install --upgrade \
    "git+${REPO_URL}@${REPO_REF}"
fi

# ---------- 4. config files ----------------------------------------------
prompt PVE_HOST       "Proxmox host (FQDN or IP)"
prompt PVE_USER       "Proxmox user@realm"          "$PVE_USER_DEFAULT"
prompt PVE_TOKEN_NAME "Proxmox API token name"      "$PVE_TOKEN_NAME_DEFAULT"
prompt PVE_TOKEN_VALUE "Proxmox API token value"    ""                 silent

CONFIG_JSON="$CONFIG_DIR/config.json"

# Legacy: earlier installer versions wrote TOML. Upstream loads JSON.
if [[ -f "$CONFIG_DIR/config.toml" ]]; then
  warn "removing legacy $CONFIG_DIR/config.toml (upstream loads JSON, not TOML)"
  rm -f "$CONFIG_DIR/config.toml"
fi

if [[ -f $CONFIG_JSON && $FORCE_REWRITE_CONFIG != "1" ]]; then
  log "preserving existing $CONFIG_JSON (re-run with FORCE_REWRITE_CONFIG=1 to rewrite)"
else
  [[ -f $CONFIG_JSON ]] && log "FORCE_REWRITE_CONFIG=1 — overwriting $CONFIG_JSON"
  log "writing $CONFIG_JSON (verify_ssl=$PVE_VERIFY_SSL)"
  umask 027

  if [[ $PVE_VERIFY_SSL == "false" ]]; then
    SECURITY_BLOCK=',
  "security": {
    "dev_mode": true
  }'
  else
    SECURITY_BLOCK=""
  fi

  cat > "$CONFIG_JSON" <<EOF
{
  "proxmox": {
    "host": "$PVE_HOST",
    "port": 8006,
    "verify_ssl": $PVE_VERIFY_SSL,
    "service": "PVE"
  },
  "auth": {
    "user": "$PVE_USER",
    "token_name": "$PVE_TOKEN_NAME",
    "token_value": "$PVE_TOKEN_VALUE"
  },
  "logging": {
    "level": "INFO"
  },
  "mcp": {
    "host": "${MCP_BIND%%:*}",
    "port": ${MCP_BIND##*:},
    "transport": "SSE"
  },
  "jobs": {
    "sqlite_path": "$STATE_DIR/jobs.sqlite3"
  }${SECURITY_BLOCK}
}
EOF
  chown root:"$SERVICE_USER" "$CONFIG_JSON"
  chmod 640 "$CONFIG_JSON"
fi

ENV_FILE="$CONFIG_DIR/env"
log "writing $ENV_FILE"
cat > "$ENV_FILE" <<EOF
PROXMOX_MCP_CONFIG=$CONFIG_JSON
PROXMOX_ALLOWED_POOL=$ALLOWED_POOL
MCP_BEARER_TOKEN_FILE=$CONFIG_DIR/bearer
MCP_BIND=$MCP_BIND
LOG_LEVEL=info
EOF
chown root:"$SERVICE_USER" "$ENV_FILE"
chmod 640 "$ENV_FILE"

BEARER_FILE="$CONFIG_DIR/bearer"
if [[ -s $BEARER_FILE ]]; then
  log "preserving existing bearer token at $BEARER_FILE"
else
  log "generating bearer token at $BEARER_FILE"
  pwgen -s 64 1 > "$BEARER_FILE"
  chown root:"$SERVICE_USER" "$BEARER_FILE"
  chmod 640 "$BEARER_FILE"
fi

# ---------- 5. systemd ---------------------------------------------------
# The unit file isn't shipped in the wheel; copy from local checkout or fetch.
if [[ -f "$LOCAL_CHECKOUT/deploy/proxmox-mcp.service" ]]; then
  log "installing systemd unit from local checkout"
  install -m 644 "$LOCAL_CHECKOUT/deploy/proxmox-mcp.service" \
    /etc/systemd/system/proxmox-mcp.service
else
  TMP_UNIT="$(mktemp)"
  trap 'rm -f "$TMP_UNIT"' EXIT
  log "fetching systemd unit from $REPO_URL"
  curl -fsSL "${REPO_URL%.git}/raw/${REPO_REF}/deploy/proxmox-mcp.service" -o "$TMP_UNIT"
  install -m 644 "$TMP_UNIT" /etc/systemd/system/proxmox-mcp.service
fi

systemctl daemon-reload
systemctl enable proxmox-mcp >/dev/null
systemctl restart proxmox-mcp

# ---------- 6. verify ----------------------------------------------------
# Poll the bind port instead of just `is-active` — startup is async and the
# is-active check used to pass at t=1s before the upstream PVE handshake at
# t=4s, masking real failures behind a "service running" message.
PORT="${MCP_BIND##*:}"
HEALTH_TIMEOUT="${HEALTH_TIMEOUT:-20}"

log "waiting up to ${HEALTH_TIMEOUT}s for proxmox-mcp to bind :$PORT"
bound=0
for _ in $(seq 1 "$HEALTH_TIMEOUT"); do
  if ss -ltn "sport = :$PORT" 2>/dev/null | awk 'NR>1 {print}' | grep -q .; then
    bound=1; break
  fi
  if ! systemctl is-active --quiet proxmox-mcp; then
    break  # service already dead, no point polling further
  fi
  sleep 1
done

if [[ $bound -ne 1 ]]; then
  warn "proxmox-mcp did not bind :$PORT within ${HEALTH_TIMEOUT}s. Logs:"
  journalctl -u proxmox-mcp -n 60 --no-pager >&2
  die "service not healthy"
fi

# Even with the port bound, do an end-to-end auth check: a request without
# the bearer should 401. If anything else happens (5xx, hang, wrong port),
# the install isn't actually usable.
log "smoke test: unauthenticated /sse should 401"
http_code=$(curl -sS -o /dev/null -m 5 -w '%{http_code}' "http://127.0.0.1:$PORT/sse" || echo "000")
if [[ $http_code != "401" ]]; then
  warn "smoke test returned $http_code (expected 401). Logs:"
  journalctl -u proxmox-mcp -n 60 --no-pager >&2
  die "service responded but not as expected"
fi

log "done."
cat <<EOF

  proxmox-mcp-vr is running on $MCP_BIND
  pool scope:    $ALLOWED_POOL
  bearer token:  $(cat "$BEARER_FILE")

  Authenticated smoke test (from this LXC):
    curl -sS -H "Authorization: Bearer \$(cat $BEARER_FILE)" -N \\
         http://127.0.0.1:$PORT/sse | head -c 200

  Add to ~/.crid/mcp_servers.json on the operator workstation; see README.

EOF
