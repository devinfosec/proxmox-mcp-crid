"""Snapshot inspection. Mutation (create/delete/rollback) is handled by upstream tools."""

from __future__ import annotations

from typing import Any

from ..pool_guard import require_pool
from ..proxmox_client import ProxmoxClient


def register(mcp: Any, client: ProxmoxClient) -> None:
    @mcp.tool(
        description=(
            "Inspect a snapshot's config: was RAM captured (vmstate)? Just disk? "
            "Returns the snapshot config dict from PVE."
        )
    )
    def snapshot_config_read(vmid: int, name: str) -> dict[str, Any]:
        require_pool(vmid)
        return client.snapshot(vmid, name).config.get()
