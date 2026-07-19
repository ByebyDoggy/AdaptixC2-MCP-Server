"""
AdaptixC2 MCP — Authentication Middleware for SSE/HTTP transport.

Provides a pure-ASGI middleware that validates Bearer tokens on every HTTP
request before they reach the MCP SSE or message handlers.

Usage (in server.py):
    from utils.auth import AuthMiddleware
    app = AuthMiddleware(mcp.sse_app(), api_key="secret")
"""
from __future__ import annotations

import json
import logging
from typing import Any

log = logging.getLogger("auth")


class AuthMiddleware:
    """ASGI middleware that enforces Bearer token authentication.

    If *api_key* is empty (falsy) the middleware is a no-op pass-through —
    a warning is logged on startup but requests proceed unauthenticated.
    """

    def __init__(self, app: Any, api_key: str) -> None:
        self.app = app
        self.api_key = api_key.strip()
        self._body_401 = json.dumps({
            "error": "Unauthorized",
            "message": "Provide a valid API key via the Authorization: Bearer <key> header.",
        }).encode("utf-8")

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if not self.api_key or scope["type"] != "http":
            # No auth required, or not an HTTP request (e.g. websocket / lifespan)
            await self.app(scope, receive, send)
            return

        headers = dict(scope.get("headers", []))
        auth_value = headers.get(b"authorization", b"").decode()
        expected = f"Bearer {self.api_key}"

        if auth_value != expected:
            log.warning("auth.rejected: path=%s", scope.get("path", ""))
            await send({
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"cache-control", b"no-store"),
                ],
            })
            await send({
                "type": "http.response.body",
                "body": self._body_401,
            })
            return

        await self.app(scope, receive, send)
