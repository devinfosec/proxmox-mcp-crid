#!/usr/bin/env bash
# One-shot installer for proxmox-mcp-vr. Run inside the management LXC as root.
#
# Idempotent: safe to re-run. Reads any pre-set env vars (PVE_HOST,
# PVE_USER, PVE_TOKEN_NAME, PVE_TOKEN_VALUE, ALLOWED_POOL, MCP_BIND,
# REPO_URL, REPO_REF); prompts for the ones it can't fill in.
#
# Steps 1 + 2 (pool/role/user/token on the PVE host, LXC creation) are
# NOT in this script — they live in deploy/lxc-setup.md and need root
# on the hypervisor, not the LXC.
#
# Usage:
#   curl -fsSL https://raw.githubusercontent.com/devinfosec/proxmox-mcp-vr/main/deploy/install.sh | sudo bash
#   # or:
#   sudo PVE_HOST=pve.lab.lan PVE_TOKEN_VALUE=xxx bash deploy/install.sh

set -euo pipefail

REPO_URL="${REPO_URL:-https://github.com/devinfosec/proxmox-mcp-vr.git}"
REPO_REF="${REPO_REF:-main}"
ALLOWED_POOL="${ALLOWED_POOL:-ai-redteam}"
MCP_BIND="${MCP_BIND:-0.0.0.0:8080}"
PVE_USER_DEFAULT="mcp-agent@pve"
PVE_TOKEN_NAME_DEFAULT="mcpvr"

INSTALL_DIR=/opt/proxmox-mcp
CONFIG_DIR=/etc/proxmox-mcp
STATE_DIR=/var/lib/proxmox-mcp
SERVICE_USER=mcp

log() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die() { printf '\033[1;31mxx\033[0m %s\n' "$*" >&2; exit 1; }

[[ $EUID -eq 0 ]] || die "run as root (try: sudo $0)"

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
  sudo -u "$SERVICE_USER" python3.11 -m venv "$INSTALL_DIR/.venv"
fi

log "installing proxmox-mcp-vr from $REPO_URL@$REPO_REF"
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade pip
sudo -u "$SERVICE_USER" "$INSTALL_DIR/.venv/bin/pip" install --quiet --upgrade \
  "git+${REPO_URL}@${REPO_REF}"

# ---------- 4. config files ----------------------------------------------
prompt PVE_HOST       "Proxmox host (FQDN or IP)"
prompt PVE_USER       "Proxmox user@realm"          "$PVE_USER_DEFAULT"
prompt PVE_TOKEN_NAME "Proxmox API token name"      "$PVE_TOKEN_NAME_DEFAULT"
prompt PVE_TOKEN_VALUE "Proxmox API token value"    ""                 silent

CONFIG_TOML="$CONFIG_DIR/config.toml"
if [[ -f $CONFIG_TOML ]]; then
  log "preserving existing $CONFIG_TOML (back it up + re-run if you want a rewrite)"
else
  log "writing $CONFIG_TOML"
  umask 027
  cat > "$CONFIG_TOML" <<EOF
[proxmox]
host = "$PVE_HOST"
port = 8006
verify_ssl = true
service = "PVE"

[auth]
user = "$PVE_USER"
token_name = "$PVE_TOKEN_NAME"
token_value = "$PVE_TOKEN_VALUE"

[logging]
level = "INFO"

[mcp]
host = "${MCP_BIND%%:*}"
port = ${MCP_BIND##*:}
transport = "SSE"
EOF
  chown root:"$SERVICE_USER" "$CONFIG_TOML"
  chmod 640 "$CONFIG_TOML"
fi

ENV_FILE="$CONFIG_DIR/env"
log "writing $ENV_FILE"
cat > "$ENV_FILE" <<EOF
PROXMOX_MCP_CONFIG=$CONFIG_TOML
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
UNIT_SRC="$INSTALL_DIR/.venv/lib/python3.11/site-packages/proxmox_mcp_vr"
# The package doesn't ship the unit file inside the wheel; fetch it from the
# git checkout the same way pip got the package.
TMP_UNIT="$(mktemp)"
trap 'rm -f "$TMP_UNIT"' EXIT
log "fetching systemd unit"
curl -fsSL "${REPO_URL%.git}/raw/${REPO_REF}/deploy/proxmox-mcp.service" -o "$TMP_UNIT"
install -m 644 "$TMP_UNIT" /etc/systemd/system/proxmox-mcp.service

systemctl daemon-reload
systemctl enable proxmox-mcp >/dev/null
systemctl restart proxmox-mcp

# ---------- 6. verify ----------------------------------------------------
sleep 1
if ! systemctl is-active --quiet proxmox-mcp; then
  warn "proxmox-mcp failed to start. Logs:"
  journalctl -u proxmox-mcp -n 40 --no-pager >&2
  die "service not running"
fi

log "done."
cat <<EOF

  proxmox-mcp-vr is running on $MCP_BIND
  pool scope:    $ALLOWED_POOL
  bearer token:  $(cat "$BEARER_FILE")

  Smoke test (from this LXC):
    curl -sS -o /dev/null -w '%{http_code}\\n' http://127.0.0.1:${MCP_BIND##*:}/sse
    # -> 401 (auth required, good)
    curl -sS -H "Authorization: Bearer \$(cat $BEARER_FILE)" -N \\
         http://127.0.0.1:${MCP_BIND##*:}/sse | head -c 200
    # -> SSE stream opens, good

  Add to ~/.crid/mcp_servers.json on the operator workstation; see README.

EOF
