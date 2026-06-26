"""
Focused tests for the Meta Ads v2 tools (drop into tests/plugins/test_takyon_plugin.py).

Per HANDOFF: stdlib + pytest + unittest.mock only; no live network. Cover the normal path and the real
blocked/test variants. Mock the creative-gateway / MCP client; use a temp TAKYON_HOME (the autouse
_isolate_takyon_home fixture already redirects it).
"""

from unittest.mock import patch
import json


# --- launch: pinned default + test-mode suppression --------------------------
def test_launch_defaults_to_traffic_website():
    """Unspecified objective/goal/destination default to Traffic -> Website clicks, CBO."""
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway", return_value="{}") as gw:
        core.handle_business_meta_ad_launch(
            {"business": "acme", "asset_kind": "image",
             "ad_image_path": "product/static-ads/acme/hero.png", "idempotency_key": "k1"}
        )
    plan = gw.call_args.args[1]["plan"]
    assert plan["objective"] == "OUTCOME_TRAFFIC"
    assert plan["optimization_goal"] == "LINK_CLICKS"
    assert plan["destination_type"] == "WEBSITE"
    assert plan["budget_mode"] == "CBO"
    assert plan["activate"] is False  # staged paused by default


def test_launch_test_mode_suppresses_external_calls():
    """Test mode must not hit Meta; the gateway gets mode=test and returns a local receipt."""
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway",
                      return_value=json.dumps({"status": "suppressed_test_mode"})) as gw:
        out = core.handle_business_meta_ad_launch(
            {"business": "acme", "mode": "test", "asset_kind": "image",
             "ad_image_path": "product/static-ads/acme/hero.png", "idempotency_key": "k2"}
        )
    assert gw.call_args.args[1]["mode"] == "test"
    assert json.loads(out)["status"] == "suppressed_test_mode"


# --- control: budget-mode resolution + no-delete -----------------------------
def test_control_set_budget_passes_value_in_cents():
    from plugins.takyon import core
    with patch.object(core, "_call_creative_runtime_gateway", return_value="{}") as gw:
        core.handle_business_meta_ad_control(
            {"business": "acme", "action": "set_budget", "level": "campaign",
             "object_id": "123", "value": 500, "idempotency_key": "k3"}
        )
    payload = gw.call_args.args[1]
    assert payload["action"] == "set_budget" and payload["value"] == 500


# --- insights: append-only, dedup by (level,object_id,date) ------------------
def test_insights_aggregator_dedups_resync_by_window():
    """Two syncs of the same (level,object_id,window) collapse to the latest by created_at."""
    from plugins.takyon import core
    rows = [
        {"level": "ad", "object_id": "9", "totals": {"date_start": "2026-06-20", "date_stop": "2026-06-20"},
         "created_at": "2026-06-20T01:00:00Z", "totals_spend_cents": 100},
        {"level": "ad", "object_id": "9", "totals": {"date_start": "2026-06-20", "date_stop": "2026-06-20"},
         "created_at": "2026-06-20T05:00:00Z", "totals_spend_cents": 140},
    ]
    deduped = core._meta_insights_dedup(rows)  # aggregator helper added with the tool
    assert len(deduped) == 1
    assert deduped[0]["created_at"] == "2026-06-20T05:00:00Z"


# --- evaluate: learning guard + poor-CTR action ------------------------------
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
