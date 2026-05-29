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
            "Fire-and-forget: start a command inside the guest VM via the QEMU "
            "guest agent and return immediately with the PVE-assigned pid. "
            "Use this ONLY for long-running/background commands. If you need the "
            "command's output, call exec_blocking instead — never run `sleep` or "
            "poll in a loop to wait for it. `command` is an argv list (e.g. "
            "['ip','-o','-4','addr','show']); for shell features like pipes wrap "
            "it as ['bash','-c','...']. Refused if the VM is not in the "
            "configured ai-redteam pool."
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
        return client.agent(vmid)("exec-status").get(pid=pid)

    @mcp.tool(
        description=(
            "PREFERRED way to run a command in a guest VM and get its output. "
            "Runs the command and waits server-side until it finishes (or the "
            "timeout), then returns the final exec-status dict. Do NOT run "
            "`sleep` or poll yourself — the waiting happens here. `command` is an "
            "argv list (e.g. ['whoami']); for pipes/redirects wrap it as "
            "['bash','-c','...']. On timeout it returns a dict with "
            "{exited: false, timed_out: true, pid} rather than raising, so you "
            "can keep polling that pid with agent_exec_status if needed."
        )
    )
    def exec_blocking(
        vmid: int,
        command: list[str],
        timeout_s: int = 120,
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
            status = client.agent(vmid)("exec-status").get(pid=pid)
            if status.get("exited"):
                return status
            time.sleep(poll_interval_s)
        # Timed out: hand back a structured result (with the pid) instead of
        # raising, so the agent can poll agent_exec_status rather than fall
        # back to fire-and-forget + sleep.
        return {"exited": False, "timed_out": True, "pid": pid, "timeout_s": timeout_s}
