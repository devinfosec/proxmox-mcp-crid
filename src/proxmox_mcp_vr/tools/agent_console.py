"""Fallback console controls when the guest agent is unavailable."""

from __future__ import annotations

from typing import Any

from ..pool_guard import require_pool
from ..proxmox_client import ProxmoxClient


def register(mcp: Any, client: ProxmoxClient) -> None:
    @mcp.tool(
        description=(
            "Send a single keystroke to the VM's console (qm sendkey). "
            "Use when the guest agent is dead. `key` follows PVE's keysym "
            "convention (e.g. 'ret', 'esc', 'ctrl-c', 'a')."
        )
    )
    def sendkey(vmid: int, key: str) -> dict[str, Any]:
        require_pool(vmid)
        return client.sendkey(vmid).put(key=key)

    @mcp.tool(
        description=(
            "Open a VNC proxy session for the VM. Returns {ticket, port, user, "
            "upid}. CRID renders this as a clickable URL for the operator."
        )
    )
    def vncproxy(vmid: int) -> dict[str, Any]:
        require_pool(vmid)
        return client.api.nodes(client.node_for(vmid)).qemu(vmid).vncproxy.post()
