"""Regression tests for TUI gateway transport serialization."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from decimal import Decimal
from uuid import uuid4

import pytest

from tui_gateway.transport import dumps_transport_json
from tui_gateway.ws import WSTransport


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
