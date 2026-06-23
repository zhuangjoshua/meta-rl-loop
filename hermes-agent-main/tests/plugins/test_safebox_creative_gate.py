"""AUTHORITATIVE creative-credit gate on the safebox service app (logo / UGC / static-ad).

The creative-credit money gate for the fixed-price creative actions now lives on the safebox, not the
client. The operator (boundary-1 ownership) reserves the action's canonical fixed credits via
``/v1/creative/reserve`` (the safebox validates ownership, resolves the price from the ONE canonical
``core`` table, and reserves on the business's creative-credit ledger), which hands back a creative
capability. The gated provider routes (``/v1/providers/{gemini/logo,openai/images,fal/{path}}``) verify
that capability, resolve the provider key LOCALLY, and forward — returning a KEY-FREE result. The
``/v1/creative/{commit,release}`` routes finalize the ONE reservation.

These tests pin the task's hard contracts:
  (a) the gated creative gate REFUSES when credits are insufficient BEFORE any provider call;
  (b) on success credits are reserved -> committed EXACTLY ONCE on the safebox and the provider result
      is KEY-FREE;
  (c) a bad / missing / wrong-audience capability is refused (401) at the provider route before any key
      is resolved;
  (d) the ungated /v1/proxy/{gemini,openai,fal} routes are GONE.

Hermetic: NO live DB, NO live provider. The safebox DB conn (the owner read) is stubbed, the
``business_credits`` ledger is replaced by an in-memory fake, and the provider callers are
monkeypatched. The point is the gate WIRING + ordering (refuse-before-provider, reserve-once,
key-free), not the ledger SQL (exercised in the business_credits PG suite).
"""
from __future__ import annotations

import contextlib

import pytest
from starlette.testclient import TestClient

from plugins.takyon import safebox, safebox_app
from plugins.takyon.business_credits import (
    CreativeCreditBalances,
    CreativeCreditReservation,
    InsufficientCreativeCredits,
)
from plugins.takyon.safebox_capability import verify_capability

_SIGNING_KEY = "safebox-only-signing-key-not-on-any-client"
_TOKEN = "secret-internal-token"
_REAL_KEY = "sk-REAL-PROVIDER-KEY-CANARY-do-not-leak"


class _OwnerCursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _OwnerConn:
    """Fake safebox conn: the only query the reserve path runs is the owner_user_id lookup."""

    def __init__(self, owner):
        self._owner = owner

    def execute(self, sql, params=None):
        return _OwnerCursor({"owner_user_id": self._owner})


class _FakeCreditLedger:
    """In-memory stand-in for the ``business_credits`` backend the safebox credit adapter calls.

    Records reserve/commit/release so a test can assert reserve-once / commit-once and refuse-before-
    provider. ``balance`` seeds the available credits; a reserve over balance raises the real
    ``InsufficientCreativeCredits`` (the route maps it to 402), exactly like the SQL ledger."""

    def __init__(self, *, balance: int):
        self.balance = int(balance)
        self.reserved: list[tuple[str, int, str]] = []
        self.reserve_metadata: list[dict | None] = []
        self.committed: list[str] = []
        self.commit_metadata: list[dict | None] = []
        self.released: list[str] = []
        self.release_metadata: list[dict | None] = []

    def open_business_credit_account(self, conn, business_slug):
        return None

    def reserve_credits(self, conn, business_slug, credits, reservation_key, *, metadata=None):
        if int(credits) > self.balance:
            raise InsufficientCreativeCredits(
                requested_credits=int(credits), available_credits=self.balance
            )
        self.balance -= int(credits)
        self.reserved.append((str(business_slug), int(credits), str(reservation_key)))
        self.reserve_metadata.append(dict(metadata or {}) if metadata else None)
        return CreativeCreditReservation(key=str(reservation_key), reserved_credits=int(credits))

    def commit_credits(self, conn, reservation_key, *, actual_credits=None, metadata=None):
        self.committed.append(str(reservation_key))
        self.commit_metadata.append(dict(metadata or {}) if metadata else None)
        return CreativeCreditBalances(business_slug="acme", balance_credits=self.balance, reserved_credits=0)

    def release_credits(self, conn, reservation_key, *, metadata=None):
        self.released.append(str(reservation_key))
        self.release_metadata.append(dict(metadata or {}) if metadata else None)
        # Refund the most-recent reserve for the key (test fake; the real ledger derives it).
        for _slug, credits, key in self.reserved:
            if key == str(reservation_key):
                self.balance += credits
                break
        return CreativeCreditBalances(business_slug="acme", balance_credits=self.balance, reserved_credits=0)


@pytest.fixture
def ledger(monkeypatch):
    fake = _FakeCreditLedger(balance=10)
    monkeypatch.setattr(safebox, "_creative_credit_backend", lambda: fake)
    return fake


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    monkeypatch.setenv(safebox_app._CAP_SIGNING_KEY_ENV, _SIGNING_KEY)

    @contextlib.contextmanager
    def _fake_conn():
        yield _OwnerConn("owner_A")

    # The safebox app and the safebox credit client both open the same fake conn.
    monkeypatch.setattr(safebox_app, "_safebox_db_conn", _fake_conn)
    # The credit adapter opens conn=None against the ledger conn ctx; stub it so the fake backend never
    # touches Postgres.
    @contextlib.contextmanager
    def _fake_credit_conn(conn=None):
        yield _OwnerConn("owner_A")

    monkeypatch.setattr(safebox, "_creative_credit_conn", _fake_credit_conn)
    return TestClient(safebox_app.build_safebox_app())


def _auth():
    return {"Authorization": f"Bearer {_TOKEN}"}


def _reserve(client, *, action="creative.logo", operator="owner_A", units=None, key="rk-1"):
    body = {
        "business": "acme",
        "operator_user_id": operator,
        "action": action,
        "reservation_key": key,
    }
    if units is not None:
        body["units"] = units
    return client.post("/v1/creative/reserve", headers=_auth(), json=body)


# ── (a) refuse on insufficient credits BEFORE any provider call ───────────────────────────────────
def test_reserve_refuses_insufficient_credits_before_provider(client, ledger, monkeypatch):
    ledger.balance = 1  # logo costs 2 -> insufficient
    # If a provider is ever resolved/called, fail — the refusal must precede it.
    from plugins.takyon import creative_gateway

    monkeypatch.setattr(
        creative_gateway,
        "_gemini_generate_logo_png",
        lambda **k: pytest.fail("provider called despite insufficient credits"),
    )
    resp = _reserve(client)
    assert resp.status_code == 402, resp.text
    detail = resp.json()["detail"]
    assert detail["requested_credits"] == 2
    assert detail["available_credits"] == 1
    assert ledger.reserved == []  # never reserved
    assert ledger.committed == []


# ── (b) success: reserve -> commit exactly once, provider result key-free ─────────────────────────
def test_logo_reserve_then_provider_then_commit_is_key_free_and_once(client, ledger, monkeypatch):
    from plugins.takyon import creative_gateway

    captured = {}

    def _fake_gen(*, api_key, prompt):
        captured["api_key"] = api_key
        captured["prompt"] = prompt
        return b"\x89PNG\r\n\x1a\nFAKELOGO"

    monkeypatch.setattr(creative_gateway, "_resolve_gemini_image_key", lambda: _REAL_KEY)
    monkeypatch.setattr(creative_gateway, "_gemini_generate_logo_png", _fake_gen)

    # 1. Reserve -> creative capability (credits reserved EXACTLY once).
    resp = _reserve(client)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    token = data["token"]
    assert data["audience"] == safebox_app._CREATIVE_LOGO_AUDIENCE
    assert data["credits"] == 2
    assert ledger.reserved == [("acme", 2, "rk-1")]

    # The capability verifies to the AUTHORITATIVE owner-derived scope.
    scope, _nonce, _exp = verify_capability(
        token,
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._CREATIVE_LOGO_AUDIENCE,
        now=0,
    )
    assert scope.takyon_user_id == "owner_A"
    assert scope.business_slug == "acme"
    assert scope.app_user_id is None  # operator action, no sub-user
    assert scope.action == "creative.logo"
    assert scope.max_cost_microusd == 2  # the credit ceiling rides this field for the credit rail

    # 2. Present the capability to the gated logo route -> KEY-FREE result; key resolved LOCALLY.
    presp = client.post(
        "/v1/providers/gemini/logo",
        headers=_auth(),
        json={"token": token, "payload": {"prompt": "a clean icon"}},
    )
    assert presp.status_code == 200, presp.text
    pdata = presp.json()
    assert pdata["format"] == "png"
    assert pdata["image_base64"]
    assert _REAL_KEY not in presp.text  # KEY-FREE
    assert captured["api_key"] == _REAL_KEY  # resolved locally on the safebox
    assert captured["prompt"] == "a clean icon"

    # 3. Commit the ONE reservation (exactly once); no double-charge.
    cresp = client.post("/v1/creative/commit", headers=_auth(), json={"reservation_key": "rk-1"})
    assert cresp.status_code == 200, cresp.text
    assert ledger.committed == ["rk-1"]
    assert ledger.released == []
    assert ledger.reserved == [("acme", 2, "rk-1")]  # still exactly one reserve


def test_release_frees_the_one_reservation(client, ledger, monkeypatch):
    resp = _reserve(client)
    assert resp.status_code == 200
    rresp = client.post("/v1/creative/release", headers=_auth(), json={"reservation_key": "rk-1"})
    assert rresp.status_code == 200
    assert ledger.released == ["rk-1"]
    assert ledger.committed == []


# ── (c) provider route refuses bad / missing / wrong-audience capability before any key ───────────
def test_provider_route_missing_capability_is_401(client, ledger, monkeypatch):
    from plugins.takyon import creative_gateway

    monkeypatch.setattr(
        creative_gateway,
        "_gemini_generate_logo_png",
        lambda **k: pytest.fail("provider called without a capability"),
    )
    resp = client.post(
        "/v1/providers/gemini/logo", headers=_auth(), json={"payload": {"prompt": "x"}}
    )
    assert resp.status_code == 401
    assert resp.json()["detail"] == "missing_capability"


def test_provider_route_garbage_capability_is_401(client, ledger, monkeypatch):
    from plugins.takyon import creative_gateway

    monkeypatch.setattr(
        creative_gateway,
        "_gemini_generate_logo_png",
        lambda **k: pytest.fail("provider called with a garbage capability"),
    )
    resp = client.post(
        "/v1/providers/gemini/logo",
        headers=_auth(),
        json={"token": "not-a-real-token", "payload": {"prompt": "x"}},
    )
    assert resp.status_code == 401
    assert str(resp.json()["detail"]).startswith("capability_invalid")


def test_provider_route_wrong_audience_capability_is_401(client, ledger, monkeypatch):
    # A capability minted for the LOGO action cannot drive the OpenAI route (whose audiences are
    # creative.ugc / creative.static_ad). Wrong-audience -> 401, before any key resolution.
    from plugins.takyon import creative_gateway

    resp = _reserve(client, action="creative.logo", key="rk-logo")
    assert resp.status_code == 200
    token = resp.json()["token"]
    monkeypatch.setattr(
        safebox_app, "_openai_image_key", lambda: pytest.fail("openai key resolved for wrong audience")
    )
    presp = client.post(
        "/v1/providers/openai/images",
        headers=_auth(),
        json={"token": token, "payload": {"prompt": "x"}},
    )
    assert presp.status_code == 401
    assert str(presp.json()["detail"]).startswith("capability_invalid")


# ── ownership boundary: a non-owner operator is refused (403) before any reserve ──────────────────
def test_reserve_refuses_non_owner_before_reserve(client, ledger):
    resp = _reserve(client, operator="someone_else")
    assert resp.status_code == 403
    assert resp.json()["detail"] == "not_business_owner"
    assert ledger.reserved == []


# ── units scaling: static-ad price scales with units, from the ONE canonical table ────────────────
def test_static_ad_reserve_scales_with_units(client, ledger):
    resp = _reserve(client, action="creative.static_ad", units=3, key="rk-static")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["audience"] == safebox_app._CREATIVE_STATIC_AD_AUDIENCE
    assert data["credits"] == 6  # static_ad_generate = 2 credits * 3 units (canonical core table)
    assert ledger.reserved == [("acme", 6, "rk-static")]


def test_worker_and_channel_spend_actions_reserve_on_safebox_gate(client, ledger):
    ledger.balance = 100
    resp = client.post(
        "/v1/creative/reserve",
        headers=_auth(),
        json={
            "business": "acme",
            "operator_user_id": "owner_A",
            "action": "creative.x_publish",
            "reservation_key": "rk-x",
            "metadata": {"budget_bucket": "x"},
        },
    )
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["audience"] == safebox_app._CREATIVE_X_PUBLISH_AUDIENCE
    assert data["credits"] == 1
    assert ledger.reserved[-1] == ("acme", 1, "rk-x")
    assert ledger.reserve_metadata[-1]["budget_bucket"] == "x"
    assert ledger.reserve_metadata[-1]["audience"] == safebox_app._CREATIVE_X_PUBLISH_AUDIENCE

    scope, _nonce, _exp = verify_capability(
        data["token"],
        signing_key=_SIGNING_KEY.encode("utf-8"),
        expected_audience=safebox_app._CREATIVE_X_PUBLISH_AUDIENCE,
        now=0,
    )
    assert scope.action == "creative.x_publish"
    assert scope.business_slug == "acme"
    assert scope.takyon_user_id == "owner_A"

    media = _reserve(client, action="creative.meta_ad_media_spend", units=42, key="rk-media")
    assert media.status_code == 200, media.text
    assert media.json()["audience"] == safebox_app._CREATIVE_META_AD_MEDIA_SPEND_AUDIENCE
    assert media.json()["credits"] == 42
    assert ledger.reserved[-1] == ("acme", 42, "rk-media")


def test_reserve_unmappable_action_is_400(client, ledger):
    resp = _reserve(client, action="anthropic.messages")  # a real action, but not a CREATIVE one
    assert resp.status_code == 400
    assert resp.json()["detail"] == "unmappable_creative_action"
    assert ledger.reserved == []


# ── gated OpenAI / FAL routes: verify capability -> key LOCAL -> forward KEY-FREE ──────────────────
class _FakeResponse:
    def __init__(self, status_code, payload):
        self.status_code = int(status_code)
        import json as _json

        self.text = _json.dumps(payload)


class _FakeHttpxClient:
    sent: list[dict] = []
    response = None

    def __init__(self, *a, **k):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def post(self, url, *, headers=None, json=None):
        _FakeHttpxClient.sent.append({"url": url, "headers": dict(headers or {}), "json": json})
        return _FakeHttpxClient.response


def test_openai_gate_resolves_key_local_and_forwards_key_free(client, ledger, monkeypatch):
    import httpx

    _FakeHttpxClient.sent = []
    _FakeHttpxClient.response = _FakeResponse(200, {"data": [{"b64_json": "AAAA"}]})
    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(safebox_app, "_openai_image_key", lambda: _REAL_KEY)

    # A UGC capability legitimately drives the OpenAI route (the reference image).
    resp = _reserve(client, action="creative.ugc", key="rk-ugc")
    assert resp.status_code == 200
    token = resp.json()["token"]
    presp = client.post(
        "/v1/providers/openai/images",
        headers=_auth(),
        json={"token": token, "payload": {"prompt": "p", "model": "gpt-image-1"}},
    )
    assert presp.status_code == 200, presp.text
    assert presp.json()["data"][0]["b64_json"] == "AAAA"
    assert _REAL_KEY not in presp.text  # KEY-FREE
    sent = _FakeHttpxClient.sent[-1]
    assert sent["url"] == safebox_app._OPENAI_IMAGES_URL
    assert sent["headers"]["Authorization"] == f"Bearer {_REAL_KEY}"  # key injected ONLY upstream


def test_fal_gate_resolves_key_local_and_forwards_key_free(client, ledger, monkeypatch):
    import httpx

    _FakeHttpxClient.sent = []
    _FakeHttpxClient.response = _FakeResponse(200, {"video": {"url": "https://x/v.mp4"}})
    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(safebox_app, "_fal_key", lambda: _REAL_KEY)

    resp = _reserve(client, action="creative.ugc", key="rk-ugc2")
    assert resp.status_code == 200
    token = resp.json()["token"]
    presp = client.post(
        "/v1/providers/fal/fal-ai/kling-video/v3/pro/image-to-video",
        headers=_auth(),
        json={"token": token, "payload": {"prompt": "p"}},
    )
    assert presp.status_code == 200, presp.text
    assert presp.json()["video"]["url"] == "https://x/v.mp4"
    assert _REAL_KEY not in presp.text  # KEY-FREE
    sent = _FakeHttpxClient.sent[-1]
    assert sent["url"] == "https://fal.run/fal-ai/kling-video/v3/pro/image-to-video"
    assert sent["headers"]["Authorization"] == f"Key {_REAL_KEY}"


def test_openai_gate_unconfigured_is_503_before_upstream(client, ledger, monkeypatch):
    import httpx

    _FakeHttpxClient.sent = []
    monkeypatch.setattr(httpx, "Client", _FakeHttpxClient)
    monkeypatch.setattr(safebox_app, "_openai_image_key", lambda: "")  # unconfigured
    resp = _reserve(client, action="creative.static_ad", key="rk-s")
    token = resp.json()["token"]
    presp = client.post(
        "/v1/providers/openai/images",
        headers=_auth(),
        json={"token": token, "payload": {"prompt": "p"}},
    )
    assert presp.status_code == 503
    assert presp.json()["detail"] == "openai_unconfigured"
    assert _FakeHttpxClient.sent == []  # never reached upstream


# ── (c) NO client-side credit gate survives for logo / UGC / static-ad ────────────────────────────
def test_creative_gateway_handlers_use_safebox_gate_not_client_reserve():
    """The logo / UGC / static-ad handlers must reserve/commit/release THROUGH the safebox gate
    (``safebox.creative_reserve`` / ``creative_commit`` / ``creative_release``) and must NOT call the
    old client-side ``core._reserve_creative_credits`` for these three provider-keyed actions. Channel
    publish / launch helpers may still call ``core._reserve_creative_credits``, but that helper now
    delegates to the safebox creative gate on production planes."""
    import re
    from pathlib import Path

    src = Path(safebox_app.__file__).resolve().parent / "creative_gateway.py"
    text = src.read_text(encoding="utf-8")

    def _slice(start_marker: str, end_marker: str) -> str:
        start = text.index(start_marker)
        end = text.index(end_marker, start)
        return text[start:end]

    logo = _slice("def logo_render(", "def _create_reddit_structured_post(")
    ugc = _slice("def ugc_render(", "@router.post(\"/static-render\")")
    static = _slice("def static_render(", "@router.post(\"/meta-launch\")")

    for name, handler in (("logo", logo), ("ugc", ugc), ("static", static)):
        # No client-side reserve/commit/release of creative credits in these handlers.
        assert "core._reserve_creative_credits(" not in handler, name
        assert "core._commit_creative_credits(" not in handler, name
        assert "core._release_creative_credits(" not in handler, name
        # The safebox gate IS used.
        assert "safebox.creative_reserve(" in handler, name
        assert re.search(r"safebox\.creative_(commit|release)\(", handler), name


# ── (d) ungated proxy routes are GONE ─────────────────────────────────────────────────────────────
def test_ungated_creative_proxy_routes_are_deleted_and_unreachable(client):
    app = safebox_app.build_safebox_app()
    paths = {getattr(route, "path", None) for route in app.routes}
    assert "/v1/proxy/gemini/image" not in paths
    assert "/v1/proxy/openai/images" not in paths
    assert "/v1/proxy/fal/{path:path}" not in paths
    for route in ("/v1/proxy/gemini/image", "/v1/proxy/openai/images", "/v1/proxy/fal/fal-ai/x"):
        resp = client.post(route, headers=_auth(), json={"prompt": "x"})
        assert resp.status_code == 404, (route, resp.status_code)
