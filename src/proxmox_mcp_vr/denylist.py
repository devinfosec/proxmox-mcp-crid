"""Tool names that must NOT be exposed to the agent.

The PVE token's role (`MCPVRAgent`) is the authoritative boundary; these
names are stripped from the FastMCP registry as defense in depth so the
LLM never even sees them.
"""

DENYLISTED_TOOLS: frozenset[str] = frozenset(
    {
        # VM lifecycle mutation
        "create_vm",
        "delete_vm",
        "clone_vm",
        # LXC lifecycle mutation
        "create_container",
        "delete_container",
        "update_container_ssh_keys",
        "update_container_resources",
        # Snapshot destruction
        "delete_snapshot",
        # ISO/template/backup mutation
        "download_iso",
        "delete_iso",
        "create_backup",
        "restore_backup",
        "delete_backup",
    }
)
