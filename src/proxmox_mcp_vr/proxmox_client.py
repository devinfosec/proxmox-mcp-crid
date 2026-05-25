"""Thin async-friendly wrapper over the synchronous proxmoxer client.

ProxmoxMCP-Plus owns the connection object; we borrow it via the upstream
server instance and add a couple of helpers (pool lookup, agent endpoints
that the upstream doesn't expose).

`proxmoxer` is synchronous. Tool handlers exposed via FastMCP can be
sync — FastMCP runs them in a threadpool. We keep helpers sync too and
let the caller decide. The `require_pool` helper in pool_guard.py is
declared `async` to match the spec but internally just calls the sync
client.
"""

from __future__ import annotations

import logging
from typing import Any

log = logging.getLogger(__name__)


class ProxmoxClient:
    """Stateful holder for the proxmoxer connection + a vmid→(node, pool) cache."""

    def __init__(self, api: Any) -> None:
        # `api` is a `proxmoxer.ProxmoxAPI` instance.
        self.api = api
        self._index: dict[int, dict[str, str]] = {}

    def refresh_index(self) -> None:
        """Populate vmid → {node, pool, type} from /cluster/resources + /pools.

        The ``/cluster/resources?type=vm`` endpoint does not reliably
        include the ``pool`` field for VMs that belong to a pool.  We
        supplement it by querying each pool's member list and writing the
        pool name back onto matching vmids.
        """
        resources = self.api.cluster.resources.get(type="vm")
        new_index: dict[int, dict[str, str]] = {}
        for r in resources:
            vmid = r.get("vmid")
            if vmid is None:
                continue
            new_index[int(vmid)] = {
                "node": r.get("node", ""),
                "pool": r.get("pool", ""),
                "type": r.get("type", ""),
            }

        # Enrich with authoritative pool membership from /pools.
        try:
            pools = self.api.pools.get()
            for pool_entry in pools:
                pool_name = pool_entry.get("poolid", "")
                if not pool_name:
                    continue
                try:
                    pool_detail = self.api.pools(pool_name).get()
                    for member in pool_detail.get("members", []):
                        mid = member.get("vmid")
                        if mid is not None and int(mid) in new_index:
                            new_index[int(mid)]["pool"] = pool_name
                except Exception:
                    log.debug("failed to query pool %r members", pool_name)
        except Exception:
            log.debug("failed to list pools; relying on cluster/resources pool field")

        self._index = new_index

    def lookup(self, vmid: int) -> dict[str, str]:
        if vmid not in self._index:
            self.refresh_index()
        if vmid not in self._index:
            raise LookupError(f"VM {vmid} not found in cluster resources")
        return self._index[vmid]

    def node_for(self, vmid: int) -> str:
        return self.lookup(vmid)["node"]

    def pool_for(self, vmid: int) -> str:
        return self.lookup(vmid)["pool"]

    # ----- agent endpoints (proxmoxer surfaces these as attribute chains) -----

    def agent(self, vmid: int) -> Any:
        node = self.node_for(vmid)
        return self.api.nodes(node).qemu(vmid).agent

    def status(self, vmid: int) -> Any:
        node = self.node_for(vmid)
        return self.api.nodes(node).qemu(vmid).status

    def snapshot(self, vmid: int, name: str) -> Any:
        node = self.node_for(vmid)
        return self.api.nodes(node).qemu(vmid).snapshot(name)

    def monitor(self, vmid: int) -> Any:
        node = self.node_for(vmid)
        return self.api.nodes(node).qemu(vmid).monitor

    def sendkey(self, vmid: int) -> Any:
        node = self.node_for(vmid)
        return self.api.nodes(node).qemu(vmid).sendkey

    def vncproxy(self, vmid: int) -> Any:
        node = self.node_for(vmid)
        return self.api.nodes(node).qemu(vmid).vncproxy
