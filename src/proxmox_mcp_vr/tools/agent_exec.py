"""Run commands inside a guest VM via the QEMU guest agent.

Three tools:
- agent_exec: fire-and-forget, returns the PVE-assigned pid.
- agent_exec_status: poll a pid for stdout/stderr/exitcode.
- exec_blocking: composite — exec + poll loop until exit or timeout.
"""

from __future__ import annotations

import time
from typing import Any

from ..pool_guard import require_pool
from ..proxmox_client import ProxmoxClient


def register(mcp: Any, client: ProxmoxClient) -> None:
    @mcp.tool(
        description=(
            "Start a command inside the guest VM via QEMU guest agent. "
            "Returns the PVE-assigned pid; use agent_exec_status to read output. "
            "Refused if VM is not in the configured ai-redteam pool."
        )
    )
    def agent_exec(
        vmid: int,
        command: list[str],
        input_data: str | None = None,
    ) -> dict[str, Any]:
        require_pool(vmid)
        body: dict[str, Any] = {"command": command}
        if input_data is not None:
            body["input-data"] = input_data
        return client.agent(vmid).exec.post(**body)

    @mcp.tool(
        description=(
            "Poll a previously-started guest-agent exec by pid. "
            "Returns {exited, exitcode, out-data, err-data, out-truncated, err-truncated}."
        )
    )
    def agent_exec_status(vmid: int, pid: int) -> dict[str, Any]:
        require_pool(vmid)
        return client.agent(vmid)["exec-status"].get(pid=pid)

    @mcp.tool(
        description=(
            "Run a command in the guest and wait for it to finish (or timeout). "
            "Composite of agent_exec + agent_exec_status polling. "
            "Returns the final exec-status dict, or raises TimeoutError."
        )
    )
    def exec_blocking(
        vmid: int,
        command: list[str],
        timeout_s: int = 30,
        input_data: str | None = None,
        poll_interval_s: float = 0.5,
    ) -> dict[str, Any]:
        require_pool(vmid)
        body: dict[str, Any] = {"command": command}
        if input_data is not None:
            body["input-data"] = input_data
        started = client.agent(vmid).exec.post(**body)
        pid = started["pid"]

        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            status = client.agent(vmid)["exec-status"].get(pid=pid)
            if status.get("exited"):
                return status
            time.sleep(poll_interval_s)
        raise TimeoutError(f"exec_blocking: pid {pid} did not exit within {timeout_s}s")
