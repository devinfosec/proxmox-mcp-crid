"""Read/write files inside guest VMs via the QEMU guest agent."""

from __future__ import annotations

from typing import Any

from ..pool_guard import require_pool
from ..proxmox_client import ProxmoxClient


def register(mcp: Any, client: ProxmoxClient) -> None:
    @mcp.tool(
        description=(
            "Read a file from inside the guest VM via QEMU guest agent. "
            "PVE caps the response at 16 MiB. Returns {content, truncated}; "
            "content is base64 if the file is binary."
        )
    )
    def agent_file_read(vmid: int, file: str) -> dict[str, Any]:
        require_pool(vmid)
        return client.agent(vmid)("file-read").get(file=file)

    @mcp.tool(
        description=(
            "Write a file inside the guest VM via QEMU guest agent. "
            "Marked T1 (operator-approval-required) in the CRID tier overrides. "
            "Refused if VM is not in the configured ai-redteam pool."
        )
    )
    def agent_file_write(vmid: int, file: str, content: str) -> dict[str, Any]:
        require_pool(vmid)
        return client.agent(vmid)("file-write").post(file=file, content=content)

    @mcp.tool(
        description=(
            "Ping qemu-guest-agent inside the VM. Returns {} on success; "
            "raises if the agent is not running."
        )
    )
    def agent_ping(vmid: int) -> dict[str, Any]:
        require_pool(vmid)
        return client.agent(vmid).ping.post()
