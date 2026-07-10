"""Streaming ASGI request-body limits applied before framework body parsing."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any


class _RequestBodyTooLarge(Exception):
    pass


class RequestBodyLimitMiddleware:
    """Reject declared or streamed bodies above the route's finite cap.

    Content-Length is only an early rejection hint: every ``http.request`` chunk is counted too,
    so chunked bodies and false small Content-Length values cannot bypass the limit.
    """

    def __init__(
        self,
        app,
        *,
        limit_resolver: Callable[[dict[str, Any]], int],
        require_content_length: Callable[[dict[str, Any]], bool] | None = None,
    ) -> None:
        self.app = app
        self.limit_resolver = limit_resolver
        self.require_content_length = require_content_length or (lambda _scope: False)

    @staticmethod
    def _headers(scope: dict[str, Any]) -> dict[bytes, list[bytes]]:
        values: dict[bytes, list[bytes]] = {}
        for key, value in scope.get("headers") or ():
            values.setdefault(bytes(key).lower(), []).append(bytes(value).strip())
        return values

    @staticmethod
    async def _respond(send, status: int, detail: str) -> None:
        body = (f'{{"detail":"{detail}"}}').encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status,
            "headers": [
                (b"content-type", b"application/json"),
                (b"content-length", str(len(body)).encode("ascii")),
                (b"cache-control", b"no-store"),
            ],
        })
        await send({"type": "http.response.body", "body": body, "more_body": False})

    async def __call__(self, scope, receive, send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        limit = int(self.limit_resolver(scope))
        if limit <= 0:
            await self._respond(send, 413, "request_body_too_large")
            return
        headers = self._headers(scope)
        lengths = headers.get(b"content-length", [])
        if len(lengths) > 1 and len(set(lengths)) != 1:
            await self._respond(send, 400, "invalid_content_length")
            return
        declared: int | None = None
        if lengths:
            try:
                declared = int(lengths[0].decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                await self._respond(send, 400, "invalid_content_length")
                return
            if declared < 0:
                await self._respond(send, 400, "invalid_content_length")
                return
            if declared > limit:
                await self._respond(send, 413, "request_body_too_large")
                return
        elif self.require_content_length(scope) and b"transfer-encoding" not in headers:
            await self._respond(send, 411, "content_length_required")
            return

        received = 0
        response_started = False

        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _RequestBodyTooLarge
            return message

        async def tracked_send(message):
            nonlocal response_started
            if message.get("type") == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, bounded_receive, tracked_send)
        except _RequestBodyTooLarge:
            if not response_started:
                await self._respond(send, 413, "request_body_too_large")
            else:
                raise


def request_method_may_have_body(scope: dict[str, Any]) -> bool:
    return str(scope.get("method") or "").upper() in {"POST", "PUT", "PATCH"}
