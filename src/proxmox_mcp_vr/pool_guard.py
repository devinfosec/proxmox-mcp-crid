"""Refuse any VM-touching call whose vmid is not in the allowed PVE pool.

The PVE token's ACL (scoped via `pveum aclmod /pool/ai-redteam`) is the
authoritative boundary. This is a fast, in-process pre-check so the agent
gets a clear, structured refusal instead of a 403 from PVE.
"""

from __future__ import annotations

import os
from typing import Optional

from .proxmox_client import ProxmoxClient

ALLOWED_POOL = os.environ.get("PROXMOX_ALLOWED_POOL", "ai-redteam")


class PoolViolation(PermissionError):
    """Raised when a tool is invoked against a vmid outside ALLOWED_POOL."""


_client_singleton: Optional[ProxmoxClient] = None


def bind_client(client: ProxmoxClient) -> None:
    """Set the module-level client used by `require_pool`. Called at server init."""
    global _client_singleton
    _client_singleton = client


def get_client() -> ProxmoxClient:
    if _client_singleton is None:
        raise RuntimeError("pool_guard.bind_client() was never called")
    return _client_singleton


def require_pool(vmid: int) -> None:
    """Raise PoolViolation if `vmid` is not in ALLOWED_POOL.

    Intentionally synchronous: tool handlers are run in FastMCP's threadpool.
    """
    client = get_client()
    try:
        pool = client.pool_for(vmid)
    except LookupError as e:
        raise PoolViolation(str(e)) from e
    if pool != ALLOWED_POOL:
        raise PoolViolation(
            f"VM {vmid} is in pool {pool!r}, not {ALLOWED_POOL!r} — refused"
        )
