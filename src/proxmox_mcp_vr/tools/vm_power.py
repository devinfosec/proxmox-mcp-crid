"""Suspend / resume / readiness — useful for freezing a VM mid-execution."""

from __future__ import annotations

from typing import Any

from ..pool_guard import require_pool
from ..proxmox_client import ProxmoxClient


def register(mcp: Any, client: ProxmoxClient) -> None:
    @mcp.tool(
        description=(
            "Suspend the VM (freeze in-memory state). Set todisk=True for a "
            "persistent suspend that survives host reboots. Useful for "
            "capturing a target mid-execution."
        )
    )
    def vm_suspend(vmid: int, todisk: bool = False) -> dict[str, Any]:
        require_pool(vmid)
        params: dict[str, Any] = {}
        if todisk:
            params["todisk"] = 1
        return client.status(vmid).suspend.post(**params)

    @mcp.tool(description="Resume a previously-suspended VM.")
    def vm_resume(vmid: int) -> dict[str, Any]:
        require_pool(vmid)
        return client.status(vmid).resume.post()

    @mcp.tool(
        description=(
            "Pre-flight check before driving the guest: returns "
            "{running: bool, agent_alive: bool, agent_version: str | None}. "
            "Combines status/current with agent/ping + agent/info."
        )
    )
    def vm_check_readiness(vmid: int) -> dict[str, Any]:
        require_pool(vmid)
        current = client.status(vmid).current.get()
        running = current.get("status") == "running"

        agent_alive = False
        agent_version: str | None = None
        if running:
            try:
                client.agent(vmid).ping.post()
                agent_alive = True
                try:
                    info = client.agent(vmid).info.get()
                    agent_version = info.get("version")
                except Exception:
                    pass
            except Exception:
                agent_alive = False

        return {
            "running": running,
            "agent_alive": agent_alive,
            "agent_version": agent_version,
        }
