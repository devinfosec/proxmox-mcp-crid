"""Additional MCP tools registered on top of the upstream ProxmoxMCP-Plus surface.

Each submodule exposes `register(mcp, client)` which calls `@mcp.tool()` on
its handlers. All VM-touching handlers call `pool_guard.require_pool(vmid)`
as the first statement.
"""

from . import agent_console, agent_exec, agent_files, snapshots, vm_power


def register_all(mcp, client) -> None:
    agent_exec.register(mcp, client)
    agent_files.register(mcp, client)
    agent_console.register(mcp, client)
    vm_power.register(mcp, client)
    snapshots.register(mcp, client)
