"""Real-Postgres regression for the SDK SessionStore mirror callback budget."""

from __future__ import annotations

import json
import os
import socket
import time
import uuid

import pytest

pytest.importorskip("psycopg")

from plugins.takyon.claude_sdk_runtime import ScopedToolBridge, ToolBridgeScope  # noqa: E402
from plugins.takyon.claude_sdk_sessions import (  # noqa: E402
    MAX_APPEND_ENTRIES,
    PostgresClaudeSdkSessionStore,
)


class _CountingConnection:
    """Keep the real connection while making client/server round trips observable."""

    def __init__(self, conn) -> None:
        self._conn = conn
        self.execute_calls: list[str] = []

    def __enter__(self):
        return self

    def __exit__(self, *_exc) -> None:
        return None

    def transaction(self):
        return self._conn.transaction()

    def execute(self, sql, params=()):
        self.execute_calls.append(" ".join(str(sql).split()).lower())
        return self._conn.execute(sql, params)


def _bridge_client(bridge: ScopedToolBridge):
    client = socket.socket(fileno=os.dup(bridge.child_fd))
    return client, client.makefile("r", encoding="utf-8"), client.makefile(
        "w", encoding="utf-8"
    )


def _request(writer, reader, payload: dict) -> dict:
    writer.write(json.dumps(payload, separators=(",", ":")) + "\n")
    writer.flush()
    return json.loads(reader.readline())


def test_near_limit_session_mirror_append_has_constant_postgres_round_trips(
    pg_conn,
) -> None:
    """The eager SDK callback must not turn 2,000 entries into 2,000 DB RTTs."""

    owner_id = pg_conn.execute(
        "insert into users (auth0_sub) values (%s) returning id",
        (f"auth0|sdk-mirror-{uuid.uuid4().hex}",),
    ).fetchone()[0]
    slug = f"sdk-mirror-{uuid.uuid4().hex[:12]}"
    pg_conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "SDK mirror regression", owner_id),
    )

    counted = _CountingConnection(pg_conn)
    store = PostgresClaudeSdkSessionStore(
        operator_user_id=str(owner_id),
        business_slug=slug,
        connection_factory=lambda: counted,
    )
    session_id = str(uuid.uuid4())
    bridge = ScopedToolBridge(
        tool_definitions=[],
        scope=ToolBridgeScope(
            operator_user_id=str(owner_id),
            business=slug,
            session_id=session_id,
            session_project_key=store.project_key,
        ),
        session_store=store,
    ).start()
    client, reader, writer = _bridge_client(bridge)
    entries = [
        {
            "type": "assistant",
            "uuid": f"sdk-entry-{index}",
            "message": f"durable mirror entry {index}",
        }
        for index in range(MAX_APPEND_ENTRIES)
    ]

    started = time.monotonic()
    try:
        append_response = _request(
            writer,
            reader,
            {
                "id": "near-limit-append",
                "type": "session_append",
                "key": {
                    "projectKey": store.project_key,
                    "sessionId": session_id,
                },
                "entries": entries,
            },
        )
        append_elapsed = time.monotonic() - started
        append_calls = list(counted.execute_calls)

        load_response = _request(
            writer,
            reader,
            {
                "id": "near-limit-load",
                "type": "session_load",
                "key": {
                    "projectKey": store.project_key,
                    "sessionId": session_id,
                },
            },
        )
        mirror_elapsed = time.monotonic() - started
    finally:
        writer.close()
        reader.close()
        client.close()
        bridge.close()

    # First access includes the bounded retention scan (two calls), ownership
    # proof, advisory lock, and exactly one set-based INSERT. The old per-entry
    # INSERT path made 2,004 calls here and crossed the SDK's 60-second seam on
    # a production pooler.
    assert len(append_calls) == 5
    assert sum(
        call.startswith("insert into public.agent_sdk_session_entries")
        for call in append_calls
    ) == 1
    assert append_response == {
        "id": "near-limit-append",
        "ok": True,
        "result": {"appended": True},
    }
    assert load_response["ok"] is True
    assert len(load_response["result"]) == MAX_APPEND_ENTRIES
    assert load_response["result"][0] == entries[0]
    assert load_response["result"][-1] == entries[-1]
    assert len(counted.execute_calls) - len(append_calls) == 3
    assert append_elapsed < 60
    assert mirror_elapsed < 60
