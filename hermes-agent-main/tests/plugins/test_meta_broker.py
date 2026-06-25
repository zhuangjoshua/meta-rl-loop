"""Regression tests for the Meta Ads safebox broker seam.

The Meta system-user token (META_SYSTEM_USER_ACCESS_TOKEN / META_ACCESS_TOKEN) and the Meta ad-account
/ page / graph-version config are provider secrets the safebox holds and DENIES /v1/env egress, so a
runtime plane (operator/dashboard/sub-user) cannot resolve them. ``core._meta_config`` and
``core._meta_graph`` must broker through the safebox when on a runtime plane
(``_use_remote_authority()`` True) and run the existing direct path only on the safebox host. The
``/v1/providers/meta/config`` route must REDACT the token so it never leaves the safebox.
"""
from __future__ import annotations

import base64

import pytest
from starlette.testclient import TestClient

from plugins.takyon import core, safebox_app

_TOKEN = "secret-internal-token"


def _boom(*_a, **_k):
    raise AssertionError("must not be called on this plane")


# ── core._meta_config brokering ──────────────────────────────────────────────────────────────────


def test_meta_config_brokers_through_safebox_when_remote(monkeypatch):
    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    # The runtime plane must NOT read the secret env value; it brokers the non-secret config.
    monkeypatch.setattr(core, "_meta_env_value", _boom)

    def fake_meta_config():
        return {
            "token": "",  # safebox redacts the token before it leaves
            "has_token": True,
            "version": "v21.0",
            "ad_account_id": "act_1300104788312342",
            "page_id": "page_123",
            "composio_connected_account_id": "",
            "composio_user_id": "takyon_prod_operator",
            "composio_alias": "takyon-prod-meta-ads",
        }

    monkeypatch.setattr(core.safebox, "meta_config", fake_meta_config)

    cfg = core._meta_config(require_token=True)
    # The token NEVER reaches the runtime plane; has_token reflects the safebox holds one.
    assert cfg["token"] == ""
    assert cfg["has_token"] is True
    # The non-secret config the launch plan / preflight need rides through.
    assert cfg["version"] == "v21.0"
    assert cfg["ad_account_id"] == "act_1300104788312342"
    assert cfg["page_id"] == "page_123"


def test_meta_config_direct_when_local_on_safebox_host(monkeypatch):
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: False)
    # On the safebox host the broker client must NOT be used (the token resolves locally).
    monkeypatch.setattr(core.safebox, "meta_config", _boom)
    monkeypatch.setattr(core, "load_takyon_env", lambda: None)

    values = {
        "META_GRAPH_VERSION": "v21.0",
        "META_SYSTEM_USER_ACCESS_TOKEN": "local-system-user-token",
        "META_AD_ACCOUNT_ID": "act_local",
        "META_PAGE_ID": "page_local",
    }

    def fake_env_value(*keys, allow_env_fallback=True):
        for key in keys:
            if values.get(key):
                return values[key]
        return ""

    monkeypatch.setattr(core, "_meta_env_value", fake_env_value)

    cfg = core._meta_config(require_token=True)
    # The locally-resolved token is present in the cfg (this process IS the authority).
    assert cfg["token"] == "local-system-user-token"
    assert cfg["ad_account_id"] == "act_local"
    assert cfg["page_id"] == "page_local"


# ── core._meta_graph brokering ───────────────────────────────────────────────────────────────────


def test_meta_graph_brokers_through_safebox_when_remote(monkeypatch):
    captured = {}

    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)

    def fake_graph_forward(*, method, path, params=None, host="graph.facebook.com", timeout=60.0):
        captured["call"] = (method, path, params, host, timeout)
        return {"id": "me-123", "name": "fourmanifold-server"}

    monkeypatch.setattr(core.safebox, "meta_graph_forward", fake_graph_forward)

    # The empty runtime cfg (token="") is ignored — the safebox re-resolves the real token.
    out = core._meta_graph(
        "GET", "me", {"fields": "id,name"}, {"token": "", "version": "v21.0"}, timeout=30
    )

    assert out == {"id": "me-123", "name": "fourmanifold-server"}
    method, path, params, host, timeout = captured["call"]
    assert method == "GET" and path == "me"
    assert params == {"fields": "id,name"}
    assert host == "graph.facebook.com"
    assert timeout == 30.0


def test_meta_graph_direct_when_local_on_safebox_host(monkeypatch):
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: False)
    # On the safebox host the broker client must NOT be used (would recurse).
    monkeypatch.setattr(core.safebox, "meta_graph_forward", _boom)

    class _Resp:
        status_code = 200
        text = ""

        def json(self):
            return {"id": "me-direct"}

    class _HX:
        def request(self, method, url, **kwargs):
            # The locally-resolved token rides the outbound request, never the broker.
            payload = kwargs.get("params") or kwargs.get("data") or {}
            assert payload.get("access_token") == "local-token"
            return _Resp()

    monkeypatch.setitem(__import__("sys").modules, "httpx", _HX())

    out = core._meta_graph(
        "GET", "me", {"fields": "id"}, {"token": "local-token", "version": "v21.0"}
    )
    assert out == {"id": "me-direct"}


# ── media upload helpers under brokered system-token mode ────────────────────────────────────────


def test_meta_image_upload_brokers_bytes_when_remote(monkeypatch, tmp_path):
    captured = {}
    image_path = tmp_path / "creative.png"
    image_path.write_bytes(b"fake image bytes")

    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(core.composio_distribution, "upload_file_descriptor", _boom)
    monkeypatch.setattr(core.composio_distribution, "metaads_execute_tool", _boom)

    def fake_graph_forward(*, method, path, params=None, host="graph.facebook.com", timeout=60.0):
        captured["call"] = (method, path, params, host, timeout)
        return {"images": {"creative.png": {"hash": "hash-123", "url": "https://example.com/creative.png"}}}

    monkeypatch.setattr(core.safebox, "meta_graph_forward", fake_graph_forward)

    result = core._meta_upload_adimage(
        image_path,
        {"token": "", "version": "v23.0", "ad_account_id": "act_123"},
    )

    assert result == {"hash": "hash-123", "url": "https://example.com/creative.png"}
    method, path, params, host, timeout = captured["call"]
    assert method == "POST"
    assert path == "act_123/adimages"
    assert params["name"] == "creative.png"
    assert params["bytes"] == base64.b64encode(b"fake image bytes").decode("ascii")
    assert host == "graph.facebook.com"
    assert timeout == 180.0


def test_meta_video_upload_brokers_signed_file_url_when_remote(monkeypatch, tmp_path):
    captured = {}
    video_path = tmp_path / "ad.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(core.composio_distribution, "metaads_proxy_request", _boom)
    monkeypatch.setattr(
        core,
        "_business_file_presigned_get_url",
        lambda business, rel, **_kwargs: f"https://assets.example/{business}/{rel}",
    )

    def fake_graph_forward(*, method, path, params=None, host="graph.facebook.com", timeout=60.0):
        captured["call"] = (method, path, params, host, timeout)
        return {"id": "video-123"}

    monkeypatch.setattr(core.safebox, "meta_graph_forward", fake_graph_forward)

    result = core._meta_upload_advideo(
        video_path,
        {"token": "", "version": "v23.0", "ad_account_id": "123"},
        name="Demo video",
        business="homework-one",
        video_rel="product/ugc-ads/demo/ad.mp4",
    )

    assert result == "video-123"
    method, path, params, host, timeout = captured["call"]
    assert method == "POST"
    assert path == "act_123/advideos"
    assert params == {
        "name": "Demo video",
        "file_url": "https://assets.example/homework-one/product/ugc-ads/demo/ad.mp4",
    }
    assert host == "graph-video.facebook.com"
    assert timeout == 180.0


# ── live budget floor ───────────────────────────────────────────────────────────────────────────


def test_meta_spend_schedule_allows_one_dollar_live_floor(monkeypatch):
    monkeypatch.delenv("TAKYON_META_MIN_LIVE_BUDGET_USD", raising=False)

    schedule = core._derive_ad_spend_schedule(
        channel="meta",
        reserved_credits=100,
        requested_daily_budget_usd=1.0,
    )

    assert schedule["daily_budget_cents"] == 100
    assert schedule["total_budget_cents"] == 100


def test_meta_spend_schedule_rejects_below_one_dollar_live_floor(monkeypatch):
    monkeypatch.delenv("TAKYON_META_MIN_LIVE_BUDGET_USD", raising=False)

    with pytest.raises(core.TakyonError, match="at least 1.00 USD"):
        core._derive_ad_spend_schedule(
            channel="meta",
            reserved_credits=99,
            requested_daily_budget_usd=0.99,
        )


def test_reddit_spend_schedule_keeps_five_dollar_live_floor(monkeypatch):
    monkeypatch.delenv("TAKYON_REDDIT_MIN_LIVE_BUDGET_USD", raising=False)

    with pytest.raises(core.TakyonError, match="at least 5.00 USD"):
        core._derive_ad_spend_schedule(
            channel="reddit",
            reserved_credits=499,
            requested_daily_budget_usd=4.99,
        )


# ── /v1/providers/meta/config route token redaction ──────────────────────────────────────────────


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv(safebox_app._SAFEBOX_TOKEN_ENV, _TOKEN)
    return TestClient(safebox_app.build_safebox_app())


def _auth():
    return {"Authorization": f"Bearer {_TOKEN}"}


def test_meta_config_route_redacts_token(client, monkeypatch):
    # On the safebox host the route resolves a real token locally; the response must REDACT it.
    monkeypatch.setattr(
        core,
        "_meta_config",
        lambda *, require_token=True: {
            "token": "real-system-user-token-that-must-not-egress",
            "version": "v21.0",
            "ad_account_id": "act_1300104788312342",
            "page_id": "page_123",
            "composio_connected_account_id": "",
            "composio_user_id": "takyon_prod_operator",
            "composio_alias": "takyon-prod-meta-ads",
        },
    )

    resp = client.post("/v1/providers/meta/config", headers=_auth())
    assert resp.status_code == 200, resp.text
    data = resp.json()
    # The token VALUE never leaves the safebox: redacted to "" with a has_token bool instead.
    assert data["token"] == ""
    assert data["has_token"] is True
    assert "real-system-user-token-that-must-not-egress" not in resp.text
    # The non-secret config the runtime needs is present.
    assert data["version"] == "v21.0"
    assert data["ad_account_id"] == "act_1300104788312342"
    assert data["page_id"] == "page_123"


def test_meta_config_route_requires_internal_token(client):
    # Internal-only route: a missing bearer is rejected before any work.
    resp = client.post("/v1/providers/meta/config")
    assert resp.status_code == 401


def test_meta_graph_route_is_registered(client):
    paths = {route.path for route in safebox_app.build_safebox_app().routes}
    assert "/v1/providers/meta/config" in paths
    assert "/v1/providers/meta/graph" in paths
