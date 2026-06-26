"""Regression tests for the official Meta Ads MCP/Safebox seam.

The legacy Meta system-user token (META_SYSTEM_USER_ACCESS_TOKEN / META_ACCESS_TOKEN) is still a
provider secret the safebox holds and DENIES /v1/env egress, but production v2 launches must use
Meta's official remote MCP server through a safebox-held META_MCP_OAUTH_TOKEN. ``core._meta_config``
brokers only non-secret readiness/config hints; live launch calls go through the
``/v1/providers/meta/mcp/*`` broker routes, not Composio and not the fourmanifold-server Meta
developer app. The ``/v1/providers/meta/config`` route must still REDACT any token it sees so the
legacy secret never leaves the safebox.
"""
from __future__ import annotations

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from plugins.takyon import composio_distribution, core, creative_gateway, meta_mcp, safebox_app

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
            "has_mcp_oauth_token": True,
            "mcp_endpoint": "https://mcp.facebook.com/ads",
            "composio_connected_account_id": "",
            "composio_user_id": "takyon_prod_operator",
            "composio_alias": "takyon-prod-meta-ads",
        }

    monkeypatch.setattr(core.safebox, "meta_config", fake_meta_config)
    monkeypatch.setattr(
        core.composio_distribution,
        "resolve_metaads_connected_account_id",
        lambda: (_ for _ in ()).throw(AssertionError("Composio must not satisfy Meta v2 config")),
    )

    cfg = core._meta_config(require_token=True)
    # The token NEVER reaches the runtime plane; has_token reflects the safebox holds one.
    assert cfg["token"] == ""
    assert cfg["has_token"] is True
    # The non-secret config the launch plan / preflight need rides through.
    assert cfg["version"] == "v21.0"
    assert cfg["ad_account_id"] == "act_1300104788312342"
    assert cfg["page_id"] == "page_123"
    assert cfg["has_mcp_oauth_token"] is True
    assert cfg["mcp_endpoint"] == "https://mcp.facebook.com/ads"
    assert cfg["composio_connected_account_id"] == ""


def test_meta_config_requires_official_mcp_oauth_when_local_on_safebox_host(monkeypatch):
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: False)
    # On the safebox host the config may see the legacy token, but v2 launch still requires official MCP.
    monkeypatch.setattr(core.safebox, "meta_config", _boom)
    monkeypatch.setattr(core, "load_takyon_env", lambda: None)
    monkeypatch.setattr(
        core.composio_distribution,
        "resolve_metaads_connected_account_id",
        lambda: (_ for _ in ()).throw(AssertionError("Composio must not satisfy Meta v2 config")),
    )

    values = {
        "META_MCP_OAUTH_TOKEN": "official-meta-mcp-token",
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
    assert cfg["token"] == "local-system-user-token"
    assert cfg["has_mcp_oauth_token"] is True
    assert cfg["ad_account_id"] == "act_local"
    assert cfg["page_id"] == "page_local"
    assert cfg["composio_connected_account_id"] == ""


# ── core._meta_graph brokering ───────────────────────────────────────────────────────────────────


def test_meta_graph_rejects_composio_metaads_config(monkeypatch):
    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(core.safebox, "meta_graph_forward", _boom)

    with pytest.raises(core.TakyonError, match="Composio Meta Ads is disabled"):
        core._meta_graph(
            "GET",
            "me",
            {"fields": "id,name"},
            {"token": "", "version": "v21.0", "composio_connected_account_id": "conn_metaads_123"},
            timeout=30,
        )


def test_meta_graph_requires_mcp_connection_when_remote(monkeypatch):
    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(core.safebox, "meta_graph_forward", _boom)

    with pytest.raises(core.TakyonError, match="official Meta MCP broker"):
        core._meta_graph("POST", "act_123/campaigns", {"name": "demo"}, {"token": "", "version": "v21.0"})


def test_metaads_proxy_request_is_disabled():
    with pytest.raises(
        composio_distribution.ComposioDistributionError,
        match="disabled",
    ):
        composio_distribution.metaads_proxy_request(
            method="POST",
            endpoint="https://graph.facebook.com/v23.0/act_123/adcreatives",
            connected_account_id="conn_metaads_123",
            body={"name": "Demo creative"},
        )


def test_meta_mcp_auth_error_detects_taskgroup_wrapped_401():
    class _Response:
        status_code = 401

    class _HTTPError(Exception):
        response = _Response()

    wrapped = ExceptionGroup("streamable http task group", [_HTTPError("unauthorized")])

    assert meta_mcp._auth_error(wrapped) is True


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


# ── official MCP launch payload helpers ──────────────────────────────────────────────────────────


def test_meta_mcp_create_args_use_public_image_url_not_upload_hash():
    plan = {
        "campaign_name": "Homework Solver Traffic",
        "objective": "OUTCOME_TRAFFIC",
        "daily_budget_cents": 100,
        "campaign_start_time": None,
        "campaign_end_time": None,
        "adset_name": "Homework Solver Ad Set",
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LINK_CLICKS",
        "adset_start_time": None,
        "adset_end_time": None,
        "targeting": {"geo_locations": {"countries": ["US"]}},
        "ad_name": "Homework Solver Ad",
        "page_id": "1181033165085863",
        "link": "https://homework-solver.coscale.app/",
        "message": "Get homework help faster.",
        "headline": "Homework help",
        "description": "Study smarter.",
        "call_to_action": "LEARN_MORE",
    }

    args = creative_gateway._meta_mcp_create_args(
        plan,
        ad_account_id="act_1300104788312342",
        image_url="https://homework-solver.coscale.app/_takyon/assets/ad/creative.png",
    )

    assert args["campaign"]["campaign_daily_budget"] == 100
    assert args["campaign"]["status"] == "PAUSED"
    assert args["adset"]["destination_type"] == "WEBSITE"
    assert args["creative"]["image_url"].startswith("https://homework-solver.coscale.app/")
    assert "image_hash" not in args["creative"]
    assert args["creative"]["call_to_action_type"] == "LEARN_MORE"
    assert args["ad"]["status"] == "PAUSED"


def test_meta_launch_missing_provider_ids_flags_stale_live_receipt():
    receipt = {
        "success": True,
        "mode": "live",
        "launch_mode": "auto_post",
        "status": "created_paused",
        "ids": {
            "image_hash": "hash-only",
            "campaign_id": "6992099099828",
            "creative_id": "",
            "adset_id": "",
            "ad_id": "",
        },
    }

    assert core._meta_launch_missing_provider_ids(receipt) == ["creative_id", "adset_id", "ad_id"]


def test_meta_launch_missing_provider_ids_ignores_test_and_manual_receipts():
    complete_missing = {"ids": {"campaign_id": "campaign-only"}}

    assert core._meta_launch_missing_provider_ids({"mode": "test", **complete_missing}) == []
    assert core._meta_launch_missing_provider_ids(
        {"mode": "live", "launch_mode": "manual_handoff", **complete_missing}
    ) == []


def test_meta_launch_repair_ids_skip_existing_campaign(monkeypatch, tmp_path):
    app = FastAPI()
    app.include_router(creative_gateway.build_creative_gateway_router())
    app.dependency_overrides[creative_gateway._require_internal_session] = lambda: None
    app.dependency_overrides[creative_gateway.get_control_conn] = lambda: object()

    business_root = tmp_path / "businesses" / "homework-solver"
    image_path = business_root / "product" / "static-ads" / "homework-solver" / "relatability-02.png"
    image_path.parent.mkdir(parents=True, exist_ok=True)
    image_path.write_bytes(b"fake png bytes")

    class FakeStore:
        def _resolve_business_file(self, business, rel):
            assert business == "homework-solver"
            return business_root / rel

    calls: list[str] = []

    monkeypatch.setattr(
        core,
        "_meta_config",
        lambda *, require_token=True: {
            "ad_account_id": "1300104788312342",
            "page_id": "1181033165085863",
            "mcp_endpoint": "https://mcp.facebook.com/ads",
        },
    )
    monkeypatch.setattr(core, "_store", lambda: FakeStore())
    monkeypatch.setattr(
        core,
        "_creative_credit_total_cost",
        lambda action: 1 if action == "meta_ad_launch" else 0,
    )
    monkeypatch.setattr(
        core,
        "_reserve_creative_credits",
        lambda *a, **k: {"budget_bucket": "meta"},
    )
    monkeypatch.setattr(
        core,
        "_commit_creative_credits",
        lambda *a, **k: {
            "balance_credits": 10,
            "reserved_credits": 0,
            "channel_budget": {},
        },
    )
    monkeypatch.setattr(
        core,
        "_stage_business_public_asset",
        lambda *a, **k: {"public_url": "https://homework-solver.coscale.app/_takyon/assets/ad.png"},
    )

    def fake_mcp(tool, payload, timeout=60.0):
        calls.append(tool)
        if tool == "ads_create_creative":
            return {"id": "creative-2"}
        if tool == "ads_create_campaign":
            raise AssertionError("existing campaign must be reused during repair")
        if tool == "ads_create_ad_set":
            assert payload["campaign_id"] == "6992099099828"
            return {"id": "adset-2"}
        if tool == "ads_create_ad":
            assert payload["adset_id"] == "adset-2"
            assert payload["creative_id"] == "creative-2"
            return {"id": "ad-2"}
        raise AssertionError(f"unexpected MCP tool: {tool}")

    monkeypatch.setattr(creative_gateway, "_meta_mcp_call", fake_mcp)

    resp = TestClient(app).post(
        "/internal/creative-gateway/meta-launch",
        json={
            "business": "homework-solver",
            "idempotency_key": "repair-homework-meta",
            "asset_kind": "image",
            "ad_image_path": "product/static-ads/homework-solver/relatability-02.png",
            "slug": "homework-solver-traffic-v1",
            "campaign": {"name": "Homework Solver Traffic", "objective": "OUTCOME_TRAFFIC"},
            "adset": {
                "name": "Students 13-24 US Broad",
                "daily_budget_usd": 1.0,
                "optimization_goal": "LINK_CLICKS",
                "targeting": {"age_min": 13, "age_max": 24, "geo_locations": {"countries": ["US"]}},
            },
            "ad": {
                "name": "Homework Solver Ad",
                "message": "No one to ask at midnight?",
                "link": "https://homework-solver.coscale.app/",
                "page_id": "1181033165085863",
            },
            "repair_ids": {"campaign_id": "6992099099828"},
        },
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is True
    assert data["ids"]["campaign_id"] == "6992099099828"
    assert data["ids"]["creative_id"] == "creative-2"
    assert data["ids"]["adset_id"] == "adset-2"
    assert data["ids"]["ad_id"] == "ad-2"
    assert calls == ["ads_create_creative", "ads_create_ad_set", "ads_create_ad"]


def test_meta_launch_preflight_missing_oauth_returns_structured_block(monkeypatch):
    app = FastAPI()
    app.include_router(creative_gateway.build_creative_gateway_router())
    app.dependency_overrides[creative_gateway._require_internal_session] = lambda: None
    app.dependency_overrides[creative_gateway.get_control_conn] = lambda: object()

    monkeypatch.setattr(
        core,
        "_meta_config",
        lambda *, require_token=True: (_ for _ in ()).throw(
            core.TakyonError(
                "Meta v2 action requires official Meta Ads MCP OAuth. Configure "
                "META_MCP_OAUTH_TOKEN on the safebox; Composio Meta Ads is not a valid v2 launch fallback."
            )
        ),
    )

    resp = TestClient(app).post(
        "/internal/creative-gateway/meta-launch",
        json={"business": "homework-solver", "mode": "preflight", "preflight": True},
    )

    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["success"] is False
    assert data["read_only"] is True
    assert data["status"] == "blocked_meta_mcp_oauth_required"
    assert data["provider"] == "official_meta_mcp"
    assert data["credits_charged"] == 0
    assert data["ids"] is None
    assert "META_MCP_OAUTH_TOKEN" in data["error"]


# ── legacy media upload helpers (not the Meta v2 launch transport) ───────────────────────────────


def test_meta_image_upload_rejects_composio_mcp_config(monkeypatch, tmp_path):
    image_path = tmp_path / "creative.png"
    image_path.write_bytes(b"fake image bytes")

    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(core.safebox, "meta_graph_forward", _boom)

    with pytest.raises(core.TakyonError, match="Composio Meta Ads image upload is disabled"):
        core._meta_upload_adimage(
            image_path,
            {
                "token": "",
                "version": "v23.0",
                "ad_account_id": "act_123",
                "composio_connected_account_id": "conn_metaads_123",
            },
        )


def test_meta_video_upload_rejects_composio_mcp_config(monkeypatch, tmp_path):
    video_path = tmp_path / "ad.mp4"
    video_path.write_bytes(b"fake video bytes")

    monkeypatch.setattr(core.safebox, "_remote_enabled", lambda: True)
    monkeypatch.setattr(core.safebox, "_local_authority_enabled", lambda: False)
    monkeypatch.setattr(core.safebox, "meta_graph_forward", _boom)

    with pytest.raises(core.TakyonError, match="Composio Meta Ads video upload is disabled"):
        core._meta_upload_advideo(
            video_path,
            {
                "token": "",
                "version": "v23.0",
                "ad_account_id": "123",
                "composio_connected_account_id": "conn_metaads_123",
            },
            name="Demo video",
            business="homework-one",
            video_rel="product/ugc-ads/demo/ad.mp4",
        )


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


def test_meta_media_spend_keeps_gateway_setup_credit_available():
    assert core._ad_channel_live_media_spend_credits("meta", 101, setup_credits=1) == 100

    with pytest.raises(core.TakyonError, match="fully consumed"):
        core._ad_channel_live_media_spend_credits("meta", 1, setup_credits=1)


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
            "has_mcp_oauth_token": True,
            "mcp_endpoint": "https://mcp.facebook.com/ads",
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
    assert data["has_mcp_oauth_token"] is True
    assert data["mcp_endpoint"] == "https://mcp.facebook.com/ads"


def test_meta_config_route_requires_internal_token(client):
    # Internal-only route: a missing bearer is rejected before any work.
    resp = client.post("/v1/providers/meta/config")
    assert resp.status_code == 401


def test_meta_mcp_call_route_brokers_key_free_tool_result(client, monkeypatch):
    captured = {}

    def fake_meta_mcp_call(*, tool_name, arguments=None, timeout=60.0):
        captured["call"] = (tool_name, arguments, timeout)
        return {"id": "campaign-123"}

    monkeypatch.setattr(safebox_app.safebox, "meta_mcp_call", fake_meta_mcp_call)

    resp = client.post(
        "/v1/providers/meta/mcp/call",
        headers=_auth(),
        json={
            "tool_name": "ads_create_campaign",
            "arguments": {"ad_account_id": "act_123", "name": "Demo"},
            "timeout": 12.0,
        },
    )

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"id": "campaign-123"}
    assert captured["call"] == (
        "ads_create_campaign",
        {"ad_account_id": "act_123", "name": "Demo"},
        12.0,
    )


def test_meta_graph_route_is_registered(client):
    paths = {route.path for route in safebox_app.build_safebox_app().routes}
    assert "/v1/providers/meta/config" in paths
    assert "/v1/providers/meta/mcp/call" in paths
    assert "/v1/providers/meta/mcp/tools" in paths
    assert "/v1/providers/meta/graph" in paths
