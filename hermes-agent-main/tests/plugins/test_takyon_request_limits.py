from __future__ import annotations

import asyncio

from plugins.takyon.request_limits import RequestBodyLimitMiddleware


async def _consume_app(scope, receive, send):
    while True:
        message = await receive()
        if message["type"] != "http.request" or not message.get("more_body"):
            break
    await send({"type": "http.response.start", "status": 200, "headers": []})
    await send({"type": "http.response.body", "body": b"ok", "more_body": False})


def _run(*, headers=(), chunks=(), limit=8, require_length=False):
    messages = iter(chunks)
    sent = []

    async def receive():
        return next(messages)

    async def send(message):
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(
        _consume_app,
        limit_resolver=lambda _scope: limit,
        require_content_length=lambda _scope: require_length,
    )
    scope = {
        "type": "http", "method": "POST", "path": "/upload", "headers": list(headers),
    }
    asyncio.run(middleware(scope, receive, send))
    return sent


def _status(messages) -> int:
    return next(message["status"] for message in messages if message["type"] == "http.response.start")


def test_declared_oversize_rejected_before_app_reads_body():
    sent = _run(headers=[(b"content-length", b"9")], chunks=(), limit=8)
    assert _status(sent) == 413


def test_chunked_body_is_counted_and_cannot_bypass_limit():
    sent = _run(
        headers=[(b"transfer-encoding", b"chunked")],
        chunks=[
            {"type": "http.request", "body": b"12345", "more_body": True},
            {"type": "http.request", "body": b"6789", "more_body": False},
        ],
        limit=8,
        require_length=True,
    )
    assert _status(sent) == 413


def test_false_small_content_length_does_not_bypass_stream_cap():
    sent = _run(
        headers=[(b"content-length", b"1")],
        chunks=[{"type": "http.request", "body": b"123456789", "more_body": False}],
        limit=8,
    )
    assert _status(sent) == 413


def test_missing_content_length_rejected_where_route_requires_it():
    sent = _run(headers=(), chunks=(), limit=8, require_length=True)
    assert _status(sent) == 411


def test_bounded_body_reaches_app():
    sent = _run(
        headers=[(b"content-length", b"8")],
        chunks=[{"type": "http.request", "body": b"12345678", "more_body": False}],
        limit=8,
    )
    assert _status(sent) == 200
