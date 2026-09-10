"""MCP HTTP 入口的 Bearer 鉴权中间件。

选择纯 ASGI 实现而非 fastmcp 的 auth provider，原因：
- FastMCP 4.x 的 `http_app(middleware=[...])` 接受任意 ASGI 中间件，接口稳定；
- 不绑定某个 provider 类的版本/命名（4.0 里 `StaticTokenVerifier` 位置与 2.x 不同）；
- 纯 ASGI 可直接单测，无需启动服务。
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Optional

logger = logging.getLogger("bigqmt.mcp.auth")


def _header(scope: Any, name: bytes) -> Optional[str]:
    for key, value in scope.get("headers") or []:
        if key == name:
            return value.decode("latin-1")
    return None


class BearerAuthMiddleware:
    """校验 `Authorization: Bearer <token>`，失败返回 401。"""

    #: 允许不带 Authorization 的路径前缀（健康检查/探活）
    open_paths = ("/health",)

    def __init__(self, app: Callable, token: str) -> None:
        if not token:
            raise ValueError("BearerAuthMiddleware 需要非空 token")
        self.app = app
        self.token = token
        self.expected = f"Bearer {token}"

    async def __call__(self, scope: Any, receive: Callable, send: Callable) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path") or "/"
        if any(path.startswith(prefix) for prefix in self.open_paths):
            await self.app(scope, receive, send)
            return

        if _header(scope, b"authorization") == self.expected:
            await self.app(scope, receive, send)
            return

        logger.warning("拒绝未授权 MCP 请求: %s %s", scope.get("method"), path)
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send(
            {
                "type": "http.response.body",
                "body": b'{"error":"Unauthorized","hint":"Authorization: Bearer <token>"}',
            }
        )


__all__ = ["BearerAuthMiddleware"]
