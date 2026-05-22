"""Bearer auth middleware: missing/wrong header returns 401, valid token passes."""

from __future__ import annotations

from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from proxmox_mcp_vr.bearer_auth import BearerAuthMiddleware


def _app(token: str) -> Starlette:
    async def ok(_request):
        return PlainTextResponse("ok")

    app = Starlette(routes=[Route("/sse", endpoint=ok)])
    app.add_middleware(BearerAuthMiddleware, expected_token=token)
    return app


def test_missing_authorization_header_returns_401():
    client = TestClient(_app("secret"))
    r = client.get("/sse")
    assert r.status_code == 401


def test_wrong_token_returns_401():
    client = TestClient(_app("secret"))
    r = client.get("/sse", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 401


def test_valid_token_passes_through():
    client = TestClient(_app("secret"))
    r = client.get("/sse", headers={"Authorization": "Bearer secret"})
    assert r.status_code == 200
    assert r.text == "ok"


def test_wrong_scheme_returns_401():
    client = TestClient(_app("secret"))
    r = client.get("/sse", headers={"Authorization": "Basic secret"})
    assert r.status_code == 401
