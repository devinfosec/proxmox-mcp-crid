"""agent_exec / agent_exec_status / exec_blocking request-shape tests."""

from __future__ import annotations

import pytest

from proxmox_mcp_vr.tools.agent_exec import register


def test_agent_exec_posts_to_correct_endpoint(client, fake_api, mcp):
    register(mcp, client)
    fake_api.responses[(("nodes", "pve1", "qemu", "9001", "agent", "exec"), "post")] = {
        "pid": 4242
    }

    result = mcp.tools["agent_exec"](vmid=9001, command=["uname", "-a"])

    assert result == {"pid": 4242}
    call = fake_api.calls[-1]
    assert call.path == ("nodes", "pve1", "qemu", "9001", "agent", "exec")
    assert call.method == "post"
    assert call.kwargs == {"command": ["uname", "-a"]}


def test_agent_exec_passes_input_data_when_given(client, fake_api, mcp):
    register(mcp, client)
    mcp.tools["agent_exec"](vmid=9001, command=["cat"], input_data="hello\n")
    call = fake_api.calls[-1]
    assert call.kwargs == {"command": ["cat"], "input-data": "hello\n"}


def test_agent_exec_status_uses_exec_status_endpoint(client, fake_api, mcp):
    register(mcp, client)
    fake_api.responses[
        (("nodes", "pve1", "qemu", "9001", "agent", "exec-status"), "get")
    ] = {"exited": 1, "exitcode": 0, "out-data": "done\n"}

    result = mcp.tools["agent_exec_status"](vmid=9001, pid=4242)

    assert result["out-data"] == "done\n"
    call = fake_api.calls[-1]
    assert call.path == ("nodes", "pve1", "qemu", "9001", "agent", "exec-status")
    assert call.method == "get"
    assert call.kwargs == {"pid": 4242}


def test_exec_blocking_polls_until_exited(client, fake_api, mcp):
    register(mcp, client)
    fake_api.responses[(("nodes", "pve1", "qemu", "9001", "agent", "exec"), "post")] = {
        "pid": 7
    }

    statuses = iter(
        [
            {"exited": 0},
            {"exited": 0},
            {"exited": 1, "exitcode": 0, "out-data": "ok\n"},
        ]
    )

    original = fake_api.responses

    class _DynResponses(dict):
        def get(self, key, default=None):
            if key == (
                ("nodes", "pve1", "qemu", "9001", "agent", "exec-status"),
                "get",
            ):
                return next(statuses)
            return original.get(key, default)

    fake_api.responses = _DynResponses(original)

    result = mcp.tools["exec_blocking"](
        vmid=9001, command=["sleep", "0"], timeout_s=5, poll_interval_s=0.01
    )
    assert result["exitcode"] == 0
    assert result["out-data"] == "ok\n"


def test_exec_blocking_raises_on_timeout(client, fake_api, mcp):
    register(mcp, client)
    fake_api.responses[(("nodes", "pve1", "qemu", "9001", "agent", "exec"), "post")] = {
        "pid": 7
    }
    fake_api.responses[
        (("nodes", "pve1", "qemu", "9001", "agent", "exec-status"), "get")
    ] = {"exited": 0}

    with pytest.raises(TimeoutError):
        mcp.tools["exec_blocking"](
            vmid=9001, command=["sleep", "999"], timeout_s=0, poll_interval_s=0.01
        )
