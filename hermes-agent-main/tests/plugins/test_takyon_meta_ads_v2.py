"""
Focused tests for the Meta Ads v2 net-new tools (evaluate + insights dedup).

Per skills/takyon HANDOFF: stdlib + pytest + unittest.mock only; no live network. Launch/control
transport is now covered by the Safebox official-MCP seam tests; this file keeps focused v2
dedup/evaluate/config regressions.
"""

from unittest.mock import patch
import json
import sys
import types

import pytest


# --- insights: append-only, dedup by (level, object_id, date) ----------------
def test_insights_aggregator_dedups_resync_by_window():
    """Two syncs of the same (level,object_id,window) collapse to the latest by created_at."""
    from plugins.takyon import core
    rows = [
        {"level": "ad", "object_id": "9", "totals": {"date_start": "2026-06-20", "date_stop": "2026-06-20"},
         "created_at": "2026-06-20T01:00:00Z", "totals_spend_cents": 100},
        {"level": "ad", "object_id": "9", "totals": {"date_start": "2026-06-20", "date_stop": "2026-06-20"},
         "created_at": "2026-06-20T05:00:00Z", "totals_spend_cents": 140},
    ]
    deduped = core._meta_insights_dedup(rows)
    assert len(deduped) == 1
    assert deduped[0]["created_at"] == "2026-06-20T05:00:00Z"


def test_insights_dedup_keeps_distinct_windows():
    """Different (object_id) or (date window) rows are kept separately."""
    from plugins.takyon import core
    rows = [
        {"level": "ad", "object_id": "9", "totals": {"date_start": "2026-06-20", "date_stop": "2026-06-20"},
         "created_at": "2026-06-20T01:00:00Z"},
        {"level": "ad", "object_id": "9", "totals": {"date_start": "2026-06-21", "date_stop": "2026-06-21"},
         "created_at": "2026-06-21T01:00:00Z"},
        {"level": "ad", "object_id": "10", "totals": {"date_start": "2026-06-20", "date_stop": "2026-06-20"},
         "created_at": "2026-06-20T01:00:00Z"},
    ]
    assert len(core._meta_insights_dedup(rows)) == 3


# --- evaluate: learning guard + default window -------------------------------
def test_evaluate_learning_phase_returns_wait():
    from plugins.takyon import core
    verdict = json.dumps({"verdict": "learning", "recommended_action": "wait"})
    with patch.object(core, "_call_creative_runtime_gateway", return_value=verdict):
        out = json.loads(core.handle_business_meta_ad_evaluate(
            {"business": "acme", "level": "adset", "object_id": "7", "idempotency_key": "k4"}))
    assert out["verdict"] == "learning" and out["recommended_action"] == "wait"


def test_evaluate_defaults_window_last_7d():
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway", return_value="{}") as gw:
        core.handle_business_meta_ad_evaluate(
            {"business": "acme", "level": "ad", "object_id": "7", "idempotency_key": "k5"})
    assert gw.call_args.args[1]["window"] == "last_7d"


# --- config: official Meta Ads MCP is the primary v2 launch rail -------------
def test_meta_config_requires_official_meta_mcp_oauth(monkeypatch):
    from plugins.takyon import core

    values = {
        "META_MCP_OAUTH_TOKEN": "official-meta-mcp-oauth",
        "META_SYSTEM_USER_ACCESS_TOKEN": "system-token",
        "META_GRAPH_VERSION": "23.0",
        "META_AD_ACCOUNT_ID": "1300104788312342",
    }

    def first_env_backed_value(*keys):
        for key in keys:
            if key in values:
                return values[key]
        return ""

    monkeypatch.setattr(core.safebox, "first_env_backed_value", first_env_backed_value)
    monkeypatch.setattr(
        core.composio_distribution,
        "resolve_metaads_connected_account_id",
        lambda: (_ for _ in ()).throw(AssertionError("Composio must not satisfy Meta v2 config")),
    )

    cfg = core._meta_config(require_token=True)

    assert cfg["token"] == "system-token"
    assert cfg["has_mcp_oauth_token"] is True
    assert cfg["mcp_endpoint"] == "https://mcp.facebook.com/ads"
    assert cfg["version"] == "v23.0"
    assert cfg["ad_account_id"] == "1300104788312342"
    assert cfg["composio_connected_account_id"] == ""


def test_meta_config_rejects_composio_without_official_mcp_oauth(monkeypatch):
    from plugins.takyon import core

    values = {
        "META_SYSTEM_USER_ACCESS_TOKEN": "system-token",
        "META_AD_ACCOUNT_ID": "1300104788312342",
    }

    def first_env_backed_value(*keys):
        for key in keys:
            if key in values:
                return values[key]
        return ""

    monkeypatch.setattr(core.safebox, "first_env_backed_value", first_env_backed_value)
    monkeypatch.setattr(
        core.composio_distribution,
        "resolve_metaads_connected_account_id",
        lambda: "conn_metaads_123",
    )

    with pytest.raises(core.TakyonError, match="META_MCP_OAUTH_TOKEN"):
        core._meta_config(require_token=True)


def test_meta_launch_plan_defaults_to_paused_for_v2():
    from plugins.takyon import core

    plan = core._meta_launch_plan(
        {
            "business": "clipbook",
            "asset_kind": "image",
            "ad_image_path": "product/static-ads/demo/creative.png",
            "campaign": {"name": "Demo Campaign"},
            "adset": {"daily_budget_usd": 1.0},
            "ad": {"link": "https://example.com", "page_id": "page_123"},
            "idempotency_key": "demo",
        },
        {"ad_account_id": "act_123", "page_id": "page_123"},
    )

    assert plan["activate"] is False


def test_meta_launch_plan_accepts_drop_in_flat_v2_fields():
    from plugins.takyon import core

    plan = core._meta_launch_plan(
        {
            "business": "clipbook",
            "mode": "live",
            "asset_kind": "image",
            "ad_image_path": "product/static-ads/demo/creative.png",
            "slug": "demo-meta",
            "copy": {
                "message": "Try Clipbook",
                "headline": "Clean homework help",
                "description": "Instant walkthroughs",
                "call_to_action_type": "LEARN_MORE",
            },
            "objective": "OUTCOME_TRAFFIC",
            "optimization_goal": "LINK_CLICKS",
            "billing_event": "IMPRESSIONS",
            "budget_mode": "CBO",
            "budget_kind": "daily",
            "budget_amount_cents": 100,
            "targeting": {"geo_locations": {"countries": ["US"]}},
            "destination_type": "WEBSITE",
            "link": "https://example.com/clipbook",
            "page_id": "page_123",
            "idempotency_key": "demo",
        },
        {"ad_account_id": "act_123", "page_id": "page_123"},
    )

    assert plan["budget_mode"] == "CBO"
    assert plan["budget_kind"] == "daily"
    assert plan["daily_budget_cents"] == 100
    assert plan["destination_type"] == "WEBSITE"
    assert plan["message"] == "Try Clipbook"
    assert plan["headline"] == "Clean homework help"
    assert plan["call_to_action"] == "LEARN_MORE"
    assert plan["activate"] is False


def test_meta_launch_requested_mode_matches_drop_in_contract():
    from plugins.takyon import core

    assert core._meta_launch_requested_mode({"preflight": True}) == "preflight"
    assert core._meta_launch_requested_mode({"mode": "test"}) == "test"
    assert core._meta_launch_requested_mode({"mode": "live"}) == "launch"


def test_meta_mcp_create_args_uses_default_cbo_budget():
    from plugins.takyon import creative_gateway

    plan = {
        "campaign_name": "Demo Campaign",
        "objective": "OUTCOME_TRAFFIC",
        "budget_mode": "CBO",
        "budget_kind": "daily",
        "bid_strategy": "LOWEST_COST_WITHOUT_CAP",
        "daily_budget_cents": 100,
        "adset_name": "Demo Ad Set",
        "billing_event": "IMPRESSIONS",
        "optimization_goal": "LINK_CLICKS",
        "destination_type": "WEBSITE",
        "targeting": {"geo_locations": {"countries": ["US"]}},
        "ad_name": "Demo Ad",
        "page_id": "page_123",
        "link": "https://example.com",
        "message": "Try it",
        "call_to_action": "LEARN_MORE",
    }

    args = creative_gateway._meta_mcp_create_args(plan, ad_account_id="act_123")

    assert args["campaign"]["campaign_daily_budget"] == 100
    assert args["campaign"]["bid_strategy"] == "LOWEST_COST_WITHOUT_CAP"
    assert "daily_budget" not in args["adset"]
    assert args["adset"]["destination_type"] == "WEBSITE"


def test_meta_graph_direct_token_adds_access_token(monkeypatch):
    from plugins.takyon import core

    captured = {}

    class Response:
        status_code = 200
        text = "{}"

        def json(self):
            return {"id": "me"}

    def request(method, url, **kwargs):
        captured.update({"method": method, "url": url, "kwargs": kwargs})
        return Response()

    monkeypatch.setitem(sys.modules, "httpx", types.SimpleNamespace(request=request))

    result = core._meta_graph(
        "GET",
        "me",
        {"fields": "id,name"},
        {"token": "system-token", "version": "v23.0"},
    )

    assert result == {"id": "me"}
    assert captured["url"] == "https://graph.facebook.com/v23.0/me"
    assert captured["kwargs"]["params"]["access_token"] == "system-token"
    assert captured["kwargs"]["params"]["fields"] == "id,name"
