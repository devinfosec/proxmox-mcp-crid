"""Shared fixtures: a fake proxmoxer-shaped API that records calls.

`proxmoxer` exposes the PVE REST tree as chained attribute/item access ending
in `.get(...)`, `.post(...)`, `.put(...)`, `.delete(...)`. The fake below
replays that interface without speaking HTTP, so unit tests can assert the
exact request shape each tool produces.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

from proxmox_mcp_vr.pool_guard import bind_client
from proxmox_mcp_vr.proxmox_client import ProxmoxClient


@dataclass
class Call:
    path: tuple
    method: str
    kwargs: dict


@dataclass
class FakeAPI:
    """proxmoxer-shaped recorder. Configure `responses` keyed by (path, method)."""

    responses: dict[tuple[tuple, str], Any] = field(default_factory=dict)
    calls: list[Call] = field(default_factory=list)

    def __call__(self, *args):
        return _Node(self, tuple(str(a) for a in args))

    def __getattr__(self, name: str):
        if name in {"responses", "calls"}:
            raise AttributeError(name)
        return _Node(self, (name,))

    def __getitem__(self, name: str):
        return _Node(self, (str(name),))


@dataclass
class _Node:
    api: FakeAPI
    path: tuple

    def __getattr__(self, name: str):
        if name in {"get", "post", "put", "delete"}:
            return _Method(self.api, self.path, name)
        return _Node(self.api, self.path + (name,))

    def __getitem__(self, name: str):
        return _Node(self.api, self.path + (str(name),))

    def __call__(self, *args):
        return _Node(self.api, self.path + tuple(str(a) for a in args))


@dataclass
class _Method:
    api: FakeAPI
    path: tuple
    method: str

    def __call__(self, **kwargs):
        self.api.calls.append(Call(path=self.path, method=self.method, kwargs=kwargs))
        return self.api.responses.get((self.path, self.method), {})


@pytest.fixture
def fake_api() -> FakeAPI:
    api = FakeAPI()
    # Seed the cluster resources lookup that ProxmoxClient.refresh_index needs.
    # Real PVE often omits the pool field from /cluster/resources, so we
    # replicate that here — pool membership comes from /pools instead.
    api.responses[(("cluster", "resources"), "get")] = [
        {"vmid": 9001, "node": "pve1", "type": "qemu"},
        {"vmid": 9002, "node": "pve1", "type": "qemu"},
        {"vmid": 100,  "node": "pve1", "pool": "production", "type": "qemu"},
        {"vmid": 200,  "node": "pve1", "type": "qemu"},
    ]
    # Pool membership via /pools and /pools/{poolid}
    api.responses[(("pools",), "get")] = [
        {"poolid": "ai-redteam"},
        {"poolid": "production"},
    ]
    api.responses[(("pools", "ai-redteam"), "get")] = {
        "members": [
            {"vmid": 9001, "node": "pve1", "type": "qemu"},
            {"vmid": 9002, "node": "pve1", "type": "qemu"},
        ],
    }
    api.responses[(("pools", "production"), "get")] = {
        "members": [
            {"vmid": 100, "node": "pve1", "type": "qemu"},
        ],
    }
    return api


@pytest.fixture
def client(fake_api: FakeAPI) -> ProxmoxClient:
    c = ProxmoxClient(fake_api)
    c.refresh_index()
    bind_client(c)
    return c


class FakeMCP:
    """Records `@mcp.tool(...)` registrations so tests can call the bare handler."""

    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}
        self.descriptions: dict[str, str] = {}

    def tool(self, description: str = ""):
        def deco(fn):
            self.tools[fn.__name__] = fn
            self.descriptions[fn.__name__] = description
            return fn
        return deco


@pytest.fixture
def mcp() -> FakeMCP:
    return FakeMCP()
