"""Entry point: build the upstream ProxmoxMCPServer, filter its tool registry,
add our in-guest tools, wrap pool-guarded tools, then serve SSE behind a
bearer-auth middleware.

The wrapper path:
1. `ProxmoxMCPServer(config_path)` constructs FastMCP and registers all 41
   upstream tools via its plugin layer.
2. We pop denylisted tool names from `mcp._tool_manager._tools` (FastMCP
   private API — fragile, watched in tests).
3. We wrap every remaining VM-touching tool with `require_pool(vmid)`.
4. We register the in-guest agent tools on the same FastMCP.
5. We mount the FastMCP SSE app under bearer-auth middleware and serve with
   uvicorn.
"""

from __future__ import annotations

import inspect
import logging
import os
from functools import wraps
from typing import Any

import uvicorn

from .bearer_auth import BearerAuthMiddleware, load_bearer_token
from .denylist import DENYLISTED_TOOLS
from .pool_guard import ALLOWED_POOL, PoolViolation, bind_client, require_pool
from .proxmox_client import ProxmoxClient
from .tools import register_all

log = logging.getLogger("proxmox_mcp_vr")

# Upstream tool names that we wrap with require_pool(vmid). These all accept
# `vmid` as their first kwarg per ProxmoxMCP-Plus's signatures.
_POOL_GUARDED_UPSTREAM = frozenset(
    {
        "get_vms",
        "execute_vm_command",
        "start_vm",
        "stop_vm",
        "shutdown_vm",
        "reset_vm",
        "list_snapshots",
        "create_snapshot",
        "rollback_snapshot",
        "get_container_config",
        "get_container_ip",
        "start_container",
        "stop_container",
        "restart_container",
        "execute_container_command",
    }
)


def _wrap_with_pool_guard(fn):
    """Wrap a FastMCP-registered handler so `require_pool(vmid)` runs first.

    FastMCP stores handlers as plain callables and re-derives schema from the
    signature; we preserve the signature with @wraps.
    """
    sig = inspect.signature(fn)

    @wraps(fn)
    def wrapped(*args, **kwargs):
        bound = sig.bind_partial(*args, **kwargs)
        vmid = bound.arguments.get("vmid")
        if vmid is not None:
            require_pool(int(vmid))
        return fn(*args, **kwargs)

    return wrapped


def _filter_and_wrap(mcp: Any) -> None:
    """Pop denylisted tools, wrap pool-guarded ones, in place on FastMCP."""
    tool_manager = mcp._tool_manager  # noqa: SLF001 — FastMCP internal
    tools = tool_manager._tools  # noqa: SLF001

    for name in list(tools.keys()):
        if name in DENYLISTED_TOOLS:
            del tools[name]
            log.info("denylist: removed upstream tool %r", name)
            continue
        if name in _POOL_GUARDED_UPSTREAM:
            tool_obj = tools[name]
            # FastMCP's Tool object stores the callable at `.fn`.
            if hasattr(tool_obj, "fn"):
                tool_obj.fn = _wrap_with_pool_guard(tool_obj.fn)
                log.info("pool-guard: wrapped upstream tool %r", name)


def build_server():
    """Construct upstream ProxmoxMCPServer, filter, augment, return (server, mcp, client)."""
    # Imported lazily so tests can run without proxmox-mcp-plus installed.
    from proxmox_mcp.server import ProxmoxMCPServer  # type: ignore

    config_path = os.environ.get("PROXMOX_MCP_CONFIG")
    if not config_path:
        raise RuntimeError("PROXMOX_MCP_CONFIG must point at the upstream config file")

    upstream = ProxmoxMCPServer(config_path)
    mcp = upstream.mcp

    pve_api = upstream.proxmox_manager.proxmox if hasattr(
        upstream, "proxmox_manager"
    ) else upstream.proxmox
    client = ProxmoxClient(pve_api)
    client.refresh_index()
    bind_client(client)

    _filter_and_wrap(mcp)
    register_all(mcp, client)

    return upstream, mcp, client


def main() -> None:
    logging.basicConfig(
        level=os.environ.get("LOG_LEVEL", "info").upper(),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    log.info("starting proxmox-mcp-vr; allowed pool=%s", ALLOWED_POOL)

    _, mcp, _ = build_server()

    bearer = load_bearer_token()
    app = mcp.sse_app()
    app.add_middleware(BearerAuthMiddleware, expected_token=bearer)

    bind = os.environ.get("MCP_BIND", "0.0.0.0:8080")
    host, _, port = bind.partition(":")

    # Translate the spec's PoolViolation into something MCP clients can reason
    # about. FastMCP catches handler exceptions and returns them as MCP errors;
    # nothing else to wire here.
    _ = PoolViolation  # exported for tests

    uvicorn.run(app, host=host or "0.0.0.0", port=int(port or "8080"))


if __name__ == "__main__":
    main()
