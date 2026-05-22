# LXC + PVE setup for proxmox-mcp-vr

Run all `pveum` and `pct` commands from the **PVE host shell**, not from
inside the LXC.

## 1. PVE: pool, role, user, token

```bash
# Pool that scopes everything the MCP can touch.
pveum pool add ai-redteam

# Minimum role for the agent. No VM.Config.*, no VM.Allocate, no Sys.*.
pveum role add MCPVRAgent --privs \
  "VM.Audit VM.PowerMgmt VM.Console VM.Monitor \
   VM.Snapshot VM.Snapshot.Rollback \
   Datastore.AllocateSpace"

# Dedicated user for the MCP.
pveum user add mcp-agent@pve --comment "proxmox-mcp-vr service account"

# Grant the role *only* on the pool.
pveum aclmod /pool/ai-redteam -user mcp-agent@pve -role MCPVRAgent

# Token. Capture the printed secret — it's shown once.
pveum user token add mcp-agent@pve mcpvr --privsep 0
```

Put every target VM in the pool:

```bash
pveum pool modify ai-redteam -vms 9001,9002,9003
```

## 2. LXC: create the management container

Unprivileged LXC, no nesting needed, single bridge attached to the
management network (firewalled inbound to operator workstation only).

```bash
pct create 200 local:vztmpl/debian-12-standard_12.2-1_amd64.tar.zst \
  --hostname proxmox-mcp \
  --memory 512 --cores 1 --rootfs local-lvm:8 \
  --net0 name=eth0,bridge=vmbr-mgmt,ip=dhcp \
  --unprivileged 1 --features keyctl=1,nesting=0 \
  --pool ''   # MCP LXC must NOT be in ai-redteam
pct start 200
pct enter 200
```

## 3. Inside the LXC: install

One command:

```bash
curl -fsSL https://raw.githubusercontent.com/devinfosec/proxmox-mcp-vr/main/deploy/install.sh \
  | sudo bash
```

The script does everything: apt deps, `mcp` user, venv, pip install, config
files, bearer-token generation, systemd unit, enable+start. It prompts for
the four PVE fields it can't infer (host, user, token name, token value)
unless you pre-set them as env vars:

```bash
sudo PVE_HOST=pve.lab.lan \
     PVE_USER=mcp-agent@pve \
     PVE_TOKEN_NAME=mcpvr \
     PVE_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
     bash deploy/install.sh
```

Re-runs are safe: config and bearer files are preserved if they already exist.

<details>
<summary>What it does, manually</summary>

```bash
apt-get update
apt-get install -y python3.11 python3.11-venv git pwgen

adduser --system --group --home /opt/proxmox-mcp mcp
install -d -o mcp -g mcp /var/lib/proxmox-mcp

python3.11 -m venv /opt/proxmox-mcp/.venv
/opt/proxmox-mcp/.venv/bin/pip install --upgrade pip
/opt/proxmox-mcp/.venv/bin/pip install \
    git+https://github.com/devinfosec/proxmox-mcp-vr.git@main

install -d -m 750 -o root -g mcp /etc/proxmox-mcp
install -m 640 -o root -g mcp deploy/config.example.toml /etc/proxmox-mcp/config.toml
install -m 640 -o root -g mcp deploy/env.example         /etc/proxmox-mcp/env
# Now edit /etc/proxmox-mcp/config.toml — set host + token_value.

pwgen -s 64 1 > /etc/proxmox-mcp/bearer
chown root:mcp /etc/proxmox-mcp/bearer
chmod 640      /etc/proxmox-mcp/bearer

install -m 644 deploy/proxmox-mcp.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now proxmox-mcp
systemctl status proxmox-mcp
```

</details>

## 4. Verify

```bash
# 401 with no auth:
curl -sS -o /dev/null -w '%{http_code}\n' http://127.0.0.1:8080/sse
# 200 with the bearer:
curl -sS -H "Authorization: Bearer $(cat /etc/proxmox-mcp/bearer)" \
     -N http://127.0.0.1:8080/sse | head -c 200
```

## 5. Rotate the bearer token

```bash
pwgen -s 64 1 > /etc/proxmox-mcp/bearer
systemctl restart proxmox-mcp
# Update Authorization header in ~/.crid/mcp_servers.json on the workstation.
```

## 6. Rotate the PVE token

```bash
pveum user token remove mcp-agent@pve mcpvr
pveum user token add    mcp-agent@pve mcpvr --privsep 0
# Update token_value in /etc/proxmox-mcp/config.toml on the LXC.
systemctl restart proxmox-mcp
```
