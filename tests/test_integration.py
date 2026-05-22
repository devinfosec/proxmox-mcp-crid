"""End-to-end tests against the real upstream ProxmoxMCPServer + real FastMCP.

These are the tests that would have caught:
- the .api vs .proxmox attribute bug (wrong attr on ProxmoxManager)
- the TOML vs JSON config format bug (upstream uses json.load)
- any drift in FastMCP's `_tool_manager._tools` or `sse_app()` private API

Skipped automatically if proxmox-mcp-plus isn't installed (e.g. on a
contributor's machine without network access during `pip install`).
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

pytest.importorskip("proxmox_mcp", reason="proxmox-mcp-plus not installed")


@pytest.fixture
def fake_proxmox_api(monkeypatch):
    """Replace proxmoxer.ProxmoxAPI everywhere upstream might bind it."""
    instance = MagicMock(name="ProxmoxAPIInstance")
    instance.cluster.resources.get.return_value = [
        {"vmid": 9001, "node": "pve1", "pool": "ai-redteam", "type": "qemu"},
    ]
    fake = MagicMock(name="ProxmoxAPI", return_value=instance)
    monkeypatch.setattr("proxmoxer.ProxmoxAPI", fake)
    # Upstream does `from proxmoxer import ProxmoxAPI` so the name is
    # re-bound inside proxmox_mcp.core.proxmox at import time.
    monkeypatch.setattr("proxmox_mcp.core.proxmox.ProxmoxAPI", fake, raising=False)
    return instance


@pytest.fixture
def upstream_env(monkeypatch, tmp_path, fake_proxmox_api):
    """Valid-shape config.json + bearer file + env vars."""
    cfg = {
        "proxmox": {
            "host": "fake.example",
            "port": 8006,
            "verify_ssl": False,
            "service": "PVE",
        },
        "auth": {
            "user": "u@pve",
            "token_name": "n",
            "token_value": "v",
        },
        "logging": {"level": "WARNING"},
        "mcp": {"host": "127.0.0.1", "port": 0, "transport": "SSE"},
        "jobs": {"sqlite_path": str(tmp_path / "jobs.sqlite3")},
        "security": {"dev_mode": True},
    }
    cfg_path = tmp_path / "config.json"
    cfg_path.write_text(json.dumps(cfg))

    bearer_path = tmp_path / "bearer"
    bearer_path.write_text("test-bearer-token-12345")

    monkeypatch.setenv("PROXMOX_MCP_CONFIG", str(cfg_path))
    monkeypatch.setenv("MCP_BEARER_TOKEN_FILE", str(bearer_path))
    monkeypatch.setenv("PROXMOX_ALLOWED_POOL", "ai-redteam")

    # Reset pool_guard module singleton between tests.
    from proxmox_mcp_vr import pool_guard
    pool_guard._client_singleton = None

    return cfg_path, bearer_path


def test_build_server_filters_denylist_and_registers_added_tools(upstream_env):
    """Real FastMCP, real upstream constructor — the bug-catcher."""
    from proxmox_mcp_vr.denylist import DENYLISTED_TOOLS
    from proxmox_mcp_vr.server import build_server

    upstream, mcp, client = build_server()

    tools = mcp._tool_manager._tools

    for name in DENYLISTED_TOOLS:
        assert name not in tools, f"denylisted tool {name!r} still registered"

    added = {
        "agent_exec", "agent_exec_status", "exec_blocking",
        "agent_file_read", "agent_file_write", "agent_ping",
        "sendkey", "vncproxy", "vm_suspend", "vm_resume",
        "vm_check_readiness", "snapshot_config_read",
    }
    for name in added:
        assert name in tools, f"added tool {name!r} not registered"

    # An upstream allowlisted tool we want to keep.
    assert "get_vms" in tools, "upstream get_vms unexpectedly missing"


def test_sse_app_rejects_requests_without_bearer(upstream_env):
    """The FastMCP SSE app + our middleware must 401 unauthenticated calls."""
    from starlette.testclient import TestClient

    from proxmox_mcp_vr.bearer_auth import BearerAuthMiddleware, load_bearer_token
    from proxmox_mcp_vr.server import build_server

    _, mcp, _ = build_server()

    # FastMCP private API — if upstream renames sse_app(), this fails fast.
    sse_app = mcp.sse_app()
    sse_app.add_middleware(BearerAuthMiddleware, expected_token=load_bearer_token())

    client = TestClient(sse_app)

    assert client.get("/sse").status_code == 401
    assert client.get("/sse", headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_pool_guarded_upstream_tool_refuses_out_of_pool_vm(upstream_env, fake_proxmox_api):
    """Wrapping the upstream tool registry actually inserts the pool guard."""
    from proxmox_mcp_vr.pool_guard import PoolViolation
    from proxmox_mcp_vr.server import build_server

    # Make cluster/resources also return a VM in another pool so we can target it.
    fake_proxmox_api.cluster.resources.get.return_value = [
        {"vmid": 9001, "node": "pve1", "pool": "ai-redteam", "type": "qemu"},
        {"vmid": 100, "node": "pve1", "pool": "production", "type": "qemu"},
    ]

    _, mcp, _ = build_server()
    wrapped = mcp._tool_manager._tools["start_vm"].fn

    with pytest.raises(PoolViolation):
        wrapped(vmid=100, node="pve1")
