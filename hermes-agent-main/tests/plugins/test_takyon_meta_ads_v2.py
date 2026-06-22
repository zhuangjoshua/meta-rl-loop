"""
Focused tests for the Meta Ads v2 net-new tools (evaluate + insights dedup).

Per skills/takyon HANDOFF: stdlib + pytest + unittest.mock only; no live network. The
v2 launch/control tests from the drop-in are deferred to the launch/control MCP swap —
the existing v1 handlers still own those tool names and use a different internal path
(Composio/Graph), so they cannot be tested against the v2 gateway shape yet.
"""

from unittest.mock import patch
import json
import sys
import types


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


# --- config: Safebox-backed Meta system token is the primary auth rail --------
def test_meta_config_prefers_safebox_system_user_token(monkeypatch):
    from plugins.takyon import core

    values = {
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

    def should_not_need_composio():
        raise AssertionError("Composio should not be consulted when the Meta system token exists")

    monkeypatch.setattr(core.composio_distribution, "resolve_metaads_connected_account_id", should_not_need_composio)

    cfg = core._meta_config(require_token=True)

    assert cfg["token"] == "system-token"
    assert cfg["version"] == "v23.0"
    assert cfg["ad_account_id"] == "1300104788312342"
    assert cfg["composio_connected_account_id"] == ""


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
