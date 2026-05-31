"""Postgres integration test for the Internal AI Gateway (``plugins/takyon/ai_gateway.py``) — the
server-side broker a generated app uses to spend on AI without ever holding the provider key.

Proves the wiring end-to-end against real Postgres through the SAME ``build_runtime_app`` mount
production uses: a ``tkg_`` gateway key resolves to its business, the spend is gated by the ONE
``app_usage`` reserve→settle/release path (the ledger actually moves), the provider key is resolved
behind a seam and never appears in the response, an over-cap or paused budget is refused with 402,
and — invariant #8 — a missing provider key blocks (503) with nothing spent. The provider HTTPS call
is stubbed through the ``get_provider_caller`` seam so no real key or network is needed; everything
else (auth, budget, ledger) is the real engine on real Postgres.

Skips unless psycopg + fastapi are importable and TAKYON_TEST_PG_DSN is set.
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")
pytest.importorskip("fastapi")

from fastapi.testclient import TestClient  # noqa: E402
from psycopg.conninfo import make_conninfo  # noqa: E402

from plugins.takyon.ai_gateway import get_provider_caller  # noqa: E402
from plugins.takyon.app_gateway_keys import mint_gateway_key  # noqa: E402
from plugins.takyon.app_usage import (  # noqa: E402
    get_usage_summary,
    list_usage_events,
    set_app_budget,
)
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402
from plugins.takyon.runtime_app import build_runtime_app  # noqa: E402

_GENERATE_BODY = {"messages": [{"role": "user", "content": "Hello gateway"}]}


def _auth(raw: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {raw}"}


def _provision_business(conn) -> tuple[str, str]:
    """Provision a user, create a business they own, mint that business one gateway key. Returns
    ``(business_slug, raw_gateway_key)``."""
    uid, _created, _raw = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", uid),
    )
    raw, _record = mint_gateway_key(conn, slug)
    return slug, raw


def _canned_caller():
    """A provider-caller dependency override: returns a deterministic Anthropic-shaped response and
    never touches the network or any key."""

    def _call(_payload: dict) -> dict:
        return {
            "id": "msg_canned_001",
            "content": [{"type": "text", "text": "canned reply"}],
            "usage": {"input_tokens": 100, "output_tokens": 20},
        }

    return _call


def _raising_caller():
    def _call(_payload: dict) -> dict:
        raise RuntimeError("anthropic boom")

    return _call


def _none_caller():
    # Mirrors the real default in a key-less environment: no provider configured.
    return None


@pytest.fixture
def gateway_client(pg_conn):
    """Build the production host app pointed at the throwaway DB (the real mount), returning a
    factory that installs a provider-caller override and hands back a TestClient. The DB seam is the
    production per-request connection; only the provider call is stubbed."""
    url = make_conninfo(os.environ["TAKYON_TEST_PG_DSN"], dbname=pg_conn.info.dbname)
    app = build_runtime_app(database_url=url)

    def _make(caller_factory=None) -> TestClient:
        if caller_factory is not None:
            app.dependency_overrides[get_provider_caller] = caller_factory
        return TestClient(app)

    return _make


def test_gateway_resolves_reserves_and_settles(gateway_client, pg_conn):
    slug, raw = _provision_business(pg_conn)
    client = gateway_client(_canned_caller)

    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth(raw))
    assert resp.status_code == 200
    payload = resp.json()

    # Exactly the deliberately-exposed projection — nothing else. This is the never-leak-key guard:
    # the response is built only from the provider response + computed costs, so no key, no
    # business slug, no internal id can appear.
    assert set(payload) == {"success", "text", "content", "model", "usage"}
    assert payload["success"] is True
    assert payload["text"] == "canned reply"
    assert payload["content"] == [{"type": "text", "text": "canned reply"}]
    assert payload["model"] == "claude-sonnet-4-6"
    assert set(payload["usage"]) == {
        "input_tokens",
        "output_tokens",
        "estimated_cost_microusd",
        "actual_cost_microusd",
    }
    assert payload["usage"]["input_tokens"] == 100
    assert payload["usage"]["output_tokens"] == 20
    # sonnet default rates 3/15 microUSD per token: 3*100 + 15*20 = 600.
    assert payload["usage"]["actual_cost_microusd"] == 600
    assert isinstance(payload["usage"]["estimated_cost_microusd"], int)
    assert payload["usage"]["estimated_cost_microusd"] > 0

    # The ledger actually moved: one completed event recording the TRUE provider spend.
    events = list_usage_events(pg_conn, slug)
    assert len(events) == 1
    event = events[0]
    assert event.status == "completed"
    assert event.actual_cost_microusd == 600
    assert event.input_tokens == 100
    assert event.output_tokens == 20
    assert event.provider == "anthropic"
    assert event.model == "claude-sonnet-4-6"
    assert event.provider_request_id == "msg_canned_001"
    assert event.route == "internal_ai_gateway"

    # committed spend == the settled actual (completed rows count actuals).
    summary = get_usage_summary(pg_conn, slug)
    assert summary["committed_microusd"] == 600


def test_gateway_provider_key_never_in_response(gateway_client, pg_conn):
    # Even with a caller that closes over a secret-looking key, that secret can never surface: the
    # endpoint builds the response only from the provider response, never from the closure's key.
    secret = "sk-ant-SUPERSECRET-do-not-leak"

    def _caller_with_secret():
        def _call(_payload: dict) -> dict:
            assert secret  # the key lives here, in the closure — and only here
            return {
                "id": "msg_x",
                "content": [{"type": "text", "text": "ok"}],
                "usage": {"input_tokens": 1, "output_tokens": 1},
            }

        return _call

    slug, raw = _provision_business(pg_conn)
    client = gateway_client(_caller_with_secret)
    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth(raw))
    assert resp.status_code == 200
    assert secret not in resp.text


def test_gateway_blocks_when_provider_unconfigured(gateway_client, pg_conn):
    # Invariant #8: a valid key but no provider configured → 503 blocked, and NOTHING reserved.
    slug, raw = _provision_business(pg_conn)
    client = gateway_client(_none_caller)

    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth(raw))
    assert resp.status_code == 503
    assert resp.json()["detail"] == "provider_unconfigured"

    # The block happens before the gate, so the ledger never moved.
    assert list_usage_events(pg_conn, slug) == []
    assert get_usage_summary(pg_conn, slug)["committed_microusd"] == 0


def test_provider_caller_default_blocks_when_unconfigured():
    # The REAL default seam (no override): in the hermetic test env no provider key is configured,
    # so it resolves to None — which is what drives the 503 above. Proves the default itself blocks
    # rather than fabricating a caller. Needs no DB.
    assert get_provider_caller() is None


def test_gateway_budget_exceeded_is_402(gateway_client, pg_conn):
    slug, raw = _provision_business(pg_conn)
    set_app_budget(pg_conn, slug, hard_limit_microusd=0)  # active, but zero headroom
    client = gateway_client(_canned_caller)

    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth(raw))
    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["error"] == "app_budget_exceeded"
    assert detail["hard_limit_microusd"] == 0
    assert detail["remaining_microusd"] == 0

    # Reserve refused before inserting, and the provider was never called.
    assert list_usage_events(pg_conn, slug) == []


def test_gateway_budget_inactive_is_402(gateway_client, pg_conn):
    slug, raw = _provision_business(pg_conn)
    set_app_budget(pg_conn, slug, hard_limit_microusd=10_000_000, status="paused")
    client = gateway_client(_canned_caller)

    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth(raw))
    assert resp.status_code == 402
    detail = resp.json()["detail"]
    assert detail["error"] == "app_budget_inactive"
    assert detail["status"] == "paused"
    assert list_usage_events(pg_conn, slug) == []


def test_gateway_provider_error_releases_and_502(gateway_client, pg_conn):
    slug, raw = _provision_business(pg_conn)
    client = gateway_client(_raising_caller)

    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth(raw))
    assert resp.status_code == 502
    assert resp.json()["detail"] == "provider_error"

    # The hold was released on failure: the event is recorded failed with zero spend, and committed
    # spend drops back to zero so the failed call doesn't permanently consume budget.
    events = list_usage_events(pg_conn, slug)
    assert len(events) == 1
    assert events[0].status == "failed"
    assert events[0].actual_cost_microusd == 0
    assert "anthropic boom" in (events[0].error or "")
    assert get_usage_summary(pg_conn, slug)["committed_microusd"] == 0


def test_gateway_bad_body_is_400(gateway_client, pg_conn):
    slug, raw = _provision_business(pg_conn)
    client = gateway_client(_canned_caller)

    resp = client.post("/internal/ai-gateway/messages", json={}, headers=_auth(raw))
    assert resp.status_code == 400
    assert "prompt or messages" in resp.json()["detail"]
    # Nothing reserved for an unbuildable request.
    assert list_usage_events(pg_conn, slug) == []


def test_gateway_unknown_app_user_is_400(gateway_client, pg_conn):
    slug, raw = _provision_business(pg_conn)
    client = gateway_client(_canned_caller)
    body = dict(_GENERATE_BODY, app_user_id=str(uuid.uuid4()))

    resp = client.post("/internal/ai-gateway/messages", json=body, headers=_auth(raw))
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unknown_app_user"
    assert list_usage_events(pg_conn, slug) == []


def test_gateway_missing_bearer_is_401(gateway_client, pg_conn):
    client = gateway_client(_canned_caller)
    resp = client.post("/internal/ai-gateway/messages", json=_GENERATE_BODY)
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_bearer_token"


def test_gateway_unknown_wellformed_key_is_401(gateway_client, pg_conn):
    # Structurally valid tkg_ key that was never minted → one undifferentiated 401.
    client = gateway_client(_canned_caller)
    resp = client.post(
        "/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth("tkg_" + "a" * 43)
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_gateway_key"


def test_gateway_per_user_key_is_rejected_as_401(gateway_client, pg_conn):
    # A per-USER tk_ key is in a disjoint keyspace from gateway tkg_ keys, so it can never
    # cross-resolve at the gateway. is_well_formed rejects it → 401.
    client = gateway_client(_canned_caller)
    resp = client.post(
        "/internal/ai-gateway/messages", json=_GENERATE_BODY, headers=_auth("tk_" + "a" * 43)
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "invalid_gateway_key"
