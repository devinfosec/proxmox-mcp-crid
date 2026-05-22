"""Denylist + filter behavior."""

from __future__ import annotations

from proxmox_mcp_vr.denylist import DENYLISTED_TOOLS
from proxmox_mcp_vr.server import _POOL_GUARDED_UPSTREAM, _filter_and_wrap


class _FakeTool:
    def __init__(self, name, fn):
        self.name = name
        self.fn = fn


class _FakeToolManager:
    def __init__(self, tools):
        self._tools = tools


class _FakeMCP:
    def __init__(self, tools):
        self._tool_manager = _FakeToolManager(tools)


def test_filter_removes_every_denylisted_name():
    tools = {name: _FakeTool(name, lambda **kw: None) for name in DENYLISTED_TOOLS}
    tools["start_vm"] = _FakeTool("start_vm", lambda vmid: None)
    mcp = _FakeMCP(tools)

    _filter_and_wrap(mcp)

    for name in DENYLISTED_TOOLS:
        assert name not in mcp._tool_manager._tools, f"{name} not removed"
    assert "start_vm" in mcp._tool_manager._tools


def test_filter_wraps_pool_guarded_upstream_tools(monkeypatch):
    seen = []

    def spy(vmid):
        seen.append(vmid)

    monkeypatch.setattr("proxmox_mcp_vr.server.require_pool", spy)

    def real_start_vm(vmid):
        return f"started {vmid}"

    mcp = _FakeMCP({"start_vm": _FakeTool("start_vm", real_start_vm)})
    _filter_and_wrap(mcp)

    wrapped = mcp._tool_manager._tools["start_vm"].fn
    result = wrapped(vmid=9001)

    assert seen == [9001]
    assert result == "started 9001"


def test_pool_guarded_set_has_no_denylist_overlap():
    """A pool-guarded tool can't simultaneously be denylisted."""
    assert _POOL_GUARDED_UPSTREAM.isdisjoint(DENYLISTED_TOOLS)
