# proxmox-mcp-vr

A Proxmox VE MCP server scoped to **vulnerability research / reverse
engineering / exploit-dev** workflows. Designed to run as the entry point of
an unprivileged LXC on a Proxmox host, and be consumed by [CRID](https://github.com/devinfosec/CRID)
(an IDE-side AI agent framework) over SSE.

It is a thin wrapper around [ProxmoxMCP-Plus](https://github.com/RekklesNA/ProxmoxMCP-Plus):
imports the upstream `ProxmoxMCPServer`, lets it register its 41-tool surface
on a FastMCP instance, then post-init:

1. **Pops destructive tools** (`create_vm`, `delete_vm`, `clone_vm`,
   `create_container`, `delete_container`, `delete_snapshot`, network/hardware
   reconfig, backup mutation, etc.) from the FastMCP `_tool_manager`.
2. **Wraps remaining VM-touching tools with a pool guard** that refuses any
   `vmid` not in the configured PVE resource pool (default: `ai-redteam`).
3. **Registers additional in-guest tools**: `agent_exec`, `agent_exec_status`,
   `exec_blocking`, `agent_file_read`, `agent_file_write`, `agent_ping`,
   `vm_check_readiness`, `sendkey`, `vncproxy`, `vm_suspend`, `vm_resume`,
   `snapshot_config_read`.
4. **Wraps the SSE transport with a bearer-token middleware** that rejects
   any inbound request lacking a matching `Authorization: Bearer …` header.

The Proxmox API token never leaves the LXC. CRID authenticates to the MCP
with a separate bearer token (rotated independently).

## Wrapper vs fork — decision

**Wrapper.** ProxmoxMCP-Plus's `ProxmoxMCPServer` uses FastMCP's
`@mcp.tool()` decorator path inside its plugin layer (`builtin_tool_plugins.py`).
After `super().__init__(config_path)` the registry is reachable at
`self.mcp._tool_manager._tools` (a dict keyed by tool name); we filter and
augment it without forking. Upstream upgrades are `pip install -U
git+…@<tag>`. If FastMCP changes the private `_tool_manager` API we revisit;
that's a small surface to track.

## Scope (what this MCP does NOT do)

- No VM/LXC create, delete, clone, migrate.
- No PVE host mutation, no node-level commands.
- No network/firewall/bridge config.
- No hardware reconfig (`vm_config_set` for disk/CPU/mem/PCI/boot).
- No backup mutation.
- No storage-level create/delete.

The PVE token is also bound to a custom role (`MCPVRAgent` —
`VM.Audit VM.PowerMgmt VM.Console VM.Monitor VM.Snapshot
VM.Snapshot.Rollback Datastore.AllocateSpace`) scoped to the `ai-redteam`
pool, so denied tools would 403 at the API layer even if a client tried to
call them. Client-side denylisting is defense-in-depth, not the security
boundary.

## Install (LXC)

Two steps. PVE-side provisioning (pool/role/user/token, LXC creation) lives
in [`deploy/lxc-setup.md`](deploy/lxc-setup.md) — those need root on the
hypervisor, not the LXC. Once you have a PVE token and an empty LXC, drop
into the LXC and run:

```bash
curl -fsSL https://raw.githubusercontent.com/devinfosec/proxmox-mcp-crid/main/deploy/install.sh \
  | bash
```

The script is idempotent: installs `python3.11`, creates the `mcp` user +
venv, pip-installs this package, prompts for the four PVE fields it can't
infer (host, user, token name, token value), generates a bearer token,
installs the systemd unit, starts it, and prints the smoke-test commands.

Non-interactive (e.g. from an automation harness):

```bash
PVE_HOST=pve.lab.lan \
PVE_USER=mcp-agent@pve \
PVE_TOKEN_NAME=mcpvr \
PVE_TOKEN_VALUE=xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx \
bash deploy/install.sh
```

## Configure CRID

In `~/.crid/mcp_servers.json` on the operator workstation:

```json
{
  "proxmox": {
    "transport": "sse",
    "url": "http://10.0.20.5:8080/sse",
    "headers": {
      "Authorization": "Bearer <contents-of-/etc/proxmox-mcp/bearer>"
    },
    "denylist": [
      "create_vm", "delete_vm", "clone_vm",
      "create_container", "delete_container",
      "delete_snapshot", "delete_iso", "delete_backup", "restore_backup",
      "update_container_ssh_keys"
    ],
    "tier_overrides": {
      "agent_file_write": "T1",
      "sendkey": "T1",
      "rollback_snapshot": "T1",
      "vm_resume": "T0",
      "vm_suspend": "T0"
    }
  }
}
```

(Denylist is redundant on the wire — the server also enforces it — but CRID
honours it as a hint and skips the tool from prompt context.)

## Develop

```bash
git clone https://github.com/devinfosec/proxmox-mcp-vr
cd proxmox-mcp-vr
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'
pytest
```

Tests mock the PVE HTTP surface with `respx` / `pytest-httpx`; no real
Proxmox required.

## Out of scope (v0)

- mTLS (bearer is fine for v0; mTLS is a follow-up)
- Multi-pool / multi-tenant
- LXC `agent_exec` equivalent (no qemu-guest-agent in LXC; use `pct exec`
  out-of-band if needed)
- Approval gating — handled by CRID via `tier_overrides`, not in this server
