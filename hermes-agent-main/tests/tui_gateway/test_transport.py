"""Regression tests for TUI gateway transport serialization."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from tui_gateway.transport import dumps_transport_json
from tui_gateway.ws import WSTransport, handle_ws


def test_dumps_transport_json_handles_postgres_scalars():
    payload = {
        "user_id": uuid4(),
        "created_at": datetime(2026, 5, 31, 12, 34, 56, tzinfo=timezone.utc),
        "reserved_cents": Decimal("1000"),
    }

    encoded = dumps_transport_json(payload)
    decoded = __import__("json").loads(encoded)

    assert isinstance(decoded["user_id"], str)
    assert decoded["created_at"] == "2026-05-31T12:34:56+00:00"
    assert decoded["reserved_cents"] == 1000


def test_dumps_transport_json_still_rejects_unknown_objects():
    class Unknown:
        pass

    with pytest.raises(TypeError):
        dumps_transport_json({"bad": Unknown()})


@pytest.mark.asyncio
async def test_ws_transport_write_async_handles_uuid_payload():
    sent: list[str] = []

    class FakeWS:
        async def send_text(self, text: str) -> None:
            sent.append(text)

    transport = WSTransport(FakeWS(), asyncio.get_running_loop())

    ok = await transport.write_async({"jsonrpc": "2.0", "result": {"user_id": uuid4()}})

    assert ok is True
    assert len(sent) == 1
    assert isinstance(__import__("json").loads(sent[0])["result"]["user_id"], str)


@pytest.mark.asyncio
async def test_handle_ws_returns_parse_error_for_bad_json(monkeypatch):
    import tui_gateway.ws as ws_module

    sent: list[dict] = []

    class FakeDisconnect(Exception):
        pass

    class FakeWS:
        def __init__(self) -> None:
            self._reads = 0

        async def accept(self) -> None:
            return None

        async def receive_text(self) -> str:
            if self._reads == 0:
                self._reads += 1
                return "{"
            raise FakeDisconnect()

        async def send_text(self, text: str) -> None:
            sent.append(__import__("json").loads(text))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(ws_module, "_WebSocketDisconnect", FakeDisconnect)

    await handle_ws(FakeWS())

    assert sent[0]["method"] == "event"
    assert sent[0]["params"]["type"] == "gateway.ready"
    assert sent[1]["error"]["code"] == -32700
    assert sent[1]["error"]["message"] == "parse error"


@pytest.mark.asyncio
async def test_handle_ws_supports_preaccepted_socket(monkeypatch):
    import tui_gateway.ws as ws_module

    sent: list[dict] = []

    class FakeDisconnect(Exception):
        pass

    class FakeWS:
        def __init__(self) -> None:
            self.accept_calls = 0

        async def accept(self) -> None:
            self.accept_calls += 1

        async def receive_text(self) -> str:
            raise FakeDisconnect()

        async def send_text(self, text: str) -> None:
            sent.append(__import__("json").loads(text))

        async def close(self) -> None:
            return None

    monkeypatch.setattr(ws_module, "_WebSocketDisconnect", FakeDisconnect)

    fake_ws = FakeWS()
    await handle_ws(fake_ws, preaccepted=True)

    assert fake_ws.accept_calls == 0
    assert sent[0]["method"] == "event"
    assert sent[0]["params"]["type"] == "gateway.ready"
