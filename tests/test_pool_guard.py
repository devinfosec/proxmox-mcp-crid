"""Pool guard: the single most important security invariant.

If these fail, agent_exec / file_write / sendkey could be aimed at neighbour
VMs (production, infra) — which is exactly the failure mode the spec exists
to prevent.
"""

from __future__ import annotations

import pytest

from proxmox_mcp_vr.pool_guard import PoolViolation, require_pool


def test_require_pool_allows_vm_in_ai_redteam(client):
    require_pool(9001)  # should not raise


def test_agent_exec_refuses_vm_outside_ai_redteam_pool(client, mcp):
    """Headline assertion: agent_exec on a vmid in the 'production' pool refuses."""
    from proxmox_mcp_vr.tools.agent_exec import register

    register(mcp, client)

    with pytest.raises(PoolViolation) as exc_info:
        mcp.tools["agent_exec"](vmid=100, command=["whoami"])

    assert "100" in str(exc_info.value)
    assert "ai-redteam" in str(exc_info.value)


def test_require_pool_refuses_vm_in_empty_pool(client):
    with pytest.raises(PoolViolation):
        require_pool(200)


def test_require_pool_refuses_unknown_vmid(client):
    with pytest.raises(PoolViolation):
        require_pool(99999)


def test_every_added_tool_calls_require_pool(client, mcp, monkeypatch):
    """Defense-in-depth: every handler we register must invoke require_pool first."""
    from proxmox_mcp_vr.tools import register_all

    seen_vmids: list[int] = []

    def spy(vmid):
        seen_vmids.append(vmid)
        # Don't actually check the pool — we want to confirm the call happened.

    monkeypatch.setattr("proxmox_mcp_vr.pool_guard.require_pool", spy)
    # The tool modules import `require_pool` at import time; patch them too.
    import proxmox_mcp_vr.tools.agent_console as agent_console
    import proxmox_mcp_vr.tools.agent_exec as agent_exec
    import proxmox_mcp_vr.tools.agent_files as agent_files
    import proxmox_mcp_vr.tools.snapshots as snapshots
    import proxmox_mcp_vr.tools.vm_power as vm_power
    for mod in (agent_exec, agent_files, agent_console, vm_power, snapshots):
        monkeypatch.setattr(mod, "require_pool", spy)

    register_all(mcp, client)

    invocations = [
        ("agent_exec",          {"vmid": 9001, "command": ["true"]}),
        ("agent_exec_status",   {"vmid": 9001, "pid": 1}),
        ("agent_file_read",     {"vmid": 9001, "file": "/etc/os-release"}),
        ("agent_file_write",    {"vmid": 9001, "file": "/tmp/x", "content": "y"}),
        ("agent_ping",          {"vmid": 9001}),
        ("sendkey",             {"vmid": 9001, "key": "ret"}),
        ("vncproxy",            {"vmid": 9001}),
        ("vm_suspend",          {"vmid": 9001}),
        ("vm_resume",           {"vmid": 9001}),
        ("snapshot_config_read",{"vmid": 9001, "name": "pre-detonate"}),
    ]
    for name, kwargs in invocations:
        seen_vmids.clear()
        mcp.tools[name](**kwargs)
        assert seen_vmids == [9001], (
            f"{name} did not call require_pool(vmid) as its first action "
            f"(saw {seen_vmids})"
        )
