"""Starlette middleware that requires `Authorization: Bearer <token>`.

The token is read once at startup from MCP_BEARER_TOKEN_FILE (mode 0600,
owned by the mcp user). Rotation = edit file + `systemctl restart`.
"""

from __future__ import annotations

import hmac
import os
from pathlib import Path

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import PlainTextResponse


def load_bearer_token() -> str:
    path = os.environ.get("MCP_BEARER_TOKEN_FILE", "/etc/proxmox-mcp/bearer")
    raw = Path(path).read_text().strip()
    if not raw:
        raise RuntimeError(f"bearer token file {path} is empty")
    return raw


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, expected_token: str) -> None:
        super().__init__(app)
        self._expected = expected_token

    async def dispatch(self, request: Request, call_next):
        header = request.headers.get("authorization", "")
        scheme, _, token = header.partition(" ")
        if scheme.lower() != "bearer" or not token:
            return PlainTextResponse("missing bearer token", status_code=401)
        if not hmac.compare_digest(token, self._expected):
            return PlainTextResponse("invalid bearer token", status_code=401)
        return await call_next(request)
