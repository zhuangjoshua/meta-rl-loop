"""Tests for the Umami web-analytics rail: always-on contract, server-side
script injection, the read tool's summary, and the minimal client."""

from __future__ import annotations

import pytest

from plugins.takyon import core, umami_util
from takyon_cli import web_server as ws


# --------------------------------------------------------------------------- #
# Always-on rail: analytics must be present for every product, never a choice  #
# --------------------------------------------------------------------------- #

def test_analytics_is_registered_and_always_on():
    assert "analytics" in core.PRODUCT_RUNTIME_RAILS
    assert "analytics" in core.ALWAYS_ON_RUNTIME_RAILS


def test_contract_block_always_includes_analytics_when_other_rails_selected():
    block = core._runtime_ui_contract_block(
        {"runtime_features": ["auth", "account"], "runtime_api_base": "/api/takyon/apps/demo"}
    )
    assert "Always-on runtime rails" in block
    assert "analytics" in block
    # the always-on note must steer workers away from their own tracker
    assert "tracking" in block.lower()


def test_contract_block_mentions_analytics_even_with_no_selected_rails():
    block = core._runtime_ui_contract_block({})
    assert "analytics" in block


# --------------------------------------------------------------------------- #
# Server-side <head> injection                                                 #
# --------------------------------------------------------------------------- #

_SNIP = '<script defer src="https://cloud.umami.is/script.js" data-website-id="W"></script>'


def test_inject_before_close_head_and_idempotent():
    doc = "<html><head><title>x</title></head><body></body></html>"
    out = ws._inject_head_snippet(doc, _SNIP)
    assert _SNIP in out and out.index(_SNIP) < out.lower().index("</head>")
    assert ws._inject_head_snippet(out, _SNIP) == out  # idempotent


def test_inject_handles_uppercase_head_and_missing_head():
    up = ws._inject_head_snippet("<HTML><HEAD></HEAD><BODY></BODY></HTML>", _SNIP)
    assert _SNIP in up
    nohead = ws._inject_head_snippet("<html><body>hi</body></html>", _SNIP)
    assert _SNIP in nohead


def test_snippet_disabled_returns_empty(monkeypatch):
    ws._UMAMI_SNIPPET_CACHE = None
    monkeypatch.setattr(ws, "load_config", lambda: {"analytics": {"umami": {"enabled": False, "website_id": "W"}}})
    assert ws._umami_analytics_snippet() == ""


def test_snippet_enabled_returns_tag(monkeypatch):
    ws._UMAMI_SNIPPET_CACHE = None
    monkeypatch.setattr(
        ws,
        "load_config",
        lambda: {"analytics": {"umami": {"enabled": True, "website_id": "WID-123", "script_src": "https://u.example/s.js"}}},
    )
    tag = ws._umami_analytics_snippet()
    assert 'data-website-id="WID-123"' in tag and 'src="https://u.example/s.js"' in tag
    ws._UMAMI_SNIPPET_CACHE = None  # don't leak cache to other tests


def test_file_response_injects_html_passes_through_assets(monkeypatch, tmp_path):
    ws._UMAMI_SNIPPET_CACHE = None
    monkeypatch.setattr(
        ws,
        "load_config",
        lambda: {"analytics": {"umami": {"enabled": True, "website_id": "WID", "script_src": "https://u/s.js"}}},
    )
    html_file = tmp_path / "index.html"
    html_file.write_text("<html><head></head><body></body></html>", encoding="utf-8")
    js_file = tmp_path / "app.js"
    js_file.write_text("console.log(1)", encoding="utf-8")
    html_resp = ws._product_site_file_response(html_file)
    assert html_resp.__class__.__name__ == "HTMLResponse"
    assert b"WID" in html_resp.body
    js_resp = ws._product_site_file_response(js_file)
    assert js_resp.__class__.__name__ == "FileResponse"  # assets untouched
    ws._UMAMI_SNIPPET_CACHE = None


# --------------------------------------------------------------------------- #
# Operator-dashboard injection (main app analytics)                            #
# --------------------------------------------------------------------------- #


def _mount_dashboard(monkeypatch, tmp_path, *, litebulb: bool):
    """Build a real FastAPI app whose static mount serves a fake WEB_DIST."""
    from fastapi import FastAPI

    ws._UMAMI_SNIPPET_CACHE = None
    monkeypatch.setattr(ws, "WEB_DIST", tmp_path)
    monkeypatch.setattr(ws, "_DASHBOARD_EMBEDDED_CHAT_ENABLED", litebulb, raising=False)
    monkeypatch.setattr(
        ws,
        "load_config",
        lambda: {
            "analytics": {
                "umami": {
                    "enabled": True,
                    "website_id": "DASH-WID",
                    "script_src": "https://u.example/s.js",
                }
            }
        },
    )

    (tmp_path / "index.html").write_text(
        "<html><head><title>Takyon</title></head><body></body></html>",
        encoding="utf-8",
    )
    if litebulb:
        (tmp_path / "litebulb").mkdir(exist_ok=True)
        (tmp_path / "litebulb" / "litebulb.html").write_text(
            "<html><head><title>Workspace</title></head>"
            '<body><script src="./takyon-adapter.js"></script></body></html>',
            encoding="utf-8",
        )

    app = FastAPI()
    ws.mount_spa(app)
    return ws, app


def test_operator_dashboard_spa_index_includes_umami(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    web_server, app = _mount_dashboard(monkeypatch, tmp_path, litebulb=False)
    try:
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert 'data-website-id="DASH-WID"' in resp.text
        assert 'src="https://u.example/s.js"' in resp.text
    finally:
        web_server._UMAMI_SNIPPET_CACHE = None


def test_operator_dashboard_litebulb_workspace_includes_umami(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    web_server, app = _mount_dashboard(monkeypatch, tmp_path, litebulb=True)
    try:
        client = TestClient(app)
        resp = client.get("/chat")
        assert resp.status_code == 200
        assert 'data-website-id="DASH-WID"' in resp.text
    finally:
        web_server._UMAMI_SNIPPET_CACHE = None


def test_operator_dashboard_omits_umami_when_disabled(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    web_server, app = _mount_dashboard(monkeypatch, tmp_path, litebulb=False)
    web_server._UMAMI_SNIPPET_CACHE = None
    monkeypatch.setattr(
        web_server,
        "load_config",
        lambda: {"analytics": {"umami": {"enabled": False, "website_id": "DASH-WID"}}},
    )
    try:
        client = TestClient(app)
        resp = client.get("/")
        assert resp.status_code == 200
        assert "DASH-WID" not in resp.text  # no faked tracking when disabled
    finally:
        web_server._UMAMI_SNIPPET_CACHE = None


# --------------------------------------------------------------------------- #
# Minimal Umami client                                                         #
# --------------------------------------------------------------------------- #

def test_umami_configured_is_safe_without_authority():
    # Must never raise even when the secret authority is unavailable.
    assert umami_util.umami_configured() is False


def test_umami_configured_true_on_remote_authority(monkeypatch):
    # A runtime plane (operator/sub-user) reports configured — the account-scoped key lives on the
    # safebox and the broker resolves it; the read itself fail-softs if the safebox lacks it.
    monkeypatch.setattr(umami_util.safebox, "_use_remote_authority", lambda: True)
    assert umami_util.umami_configured() is True


def test_umami_request_brokers_on_remote_authority(monkeypatch):
    # On a remote-authority plane the read goes through the safebox broker; the key is NEVER resolved
    # locally and Umami is NEVER called directly from the runtime.
    captured: dict[str, object] = {}
    monkeypatch.setattr(umami_util.safebox, "_use_remote_authority", lambda: True)

    def fake_forward(*, path, params=None, timeout=20.0):
        captured.update({"path": path, "params": params, "timeout": timeout})
        return {"visitors": 4, "visits": 10}

    monkeypatch.setattr(umami_util.safebox, "umami_forward", fake_forward)
    monkeypatch.setattr(
        umami_util.safebox,
        "read_env_backed_value",
        lambda *a, **k: pytest.fail("remote plane must broker, not resolve the key locally"),
    )
    monkeypatch.setattr(
        umami_util.urllib.request,
        "urlopen",
        lambda *a, **k: pytest.fail("remote plane must broker, not call Umami directly"),
    )
    out = umami_util.umami_request("websites/WID/stats", {"hostname": "x.example"}, "https://api.umami.is/v1")
    assert out == {"visitors": 4, "visits": 10}
    assert captured["path"] == "websites/WID/stats" and captured["params"] == {"hostname": "x.example"}


def test_umami_request_broker_failure_raises_umami_error(monkeypatch):
    monkeypatch.setattr(umami_util.safebox, "_use_remote_authority", lambda: True)

    def boom(**_kwargs):
        raise RuntimeError("safebox 502")

    monkeypatch.setattr(umami_util.safebox, "umami_forward", boom)
    with pytest.raises(umami_util.UmamiError):
        umami_util.umami_request("websites/WID/stats", {}, "https://api.umami.is/v1")


def test_umami_request_uses_local_key_when_not_remote(monkeypatch):
    # On the safebox host / standalone the key is resolved locally and Umami is called directly — the
    # broker is not used (this is the path the broker ROUTE itself runs on the safebox host).
    monkeypatch.setattr(umami_util.safebox, "_use_remote_authority", lambda: False)
    monkeypatch.setattr(
        umami_util.safebox, "umami_forward", lambda **k: pytest.fail("local path must not broker")
    )
    monkeypatch.setattr(umami_util.safebox, "read_env_backed_value", lambda key: "local-key")

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def read(self):
            return b'{"visitors": 7}'

    captured: dict[str, object] = {}

    def fake_urlopen(request, timeout=0):
        captured["headers"] = {k.lower(): v for k, v in request.header_items()}
        return DummyResponse()

    monkeypatch.setattr(umami_util.urllib.request, "urlopen", fake_urlopen)
    out = umami_util.umami_request("websites/WID/stats", {"hostname": "x"}, "https://api.umami.is/v1")
    assert out == {"visitors": 7}
    assert captured["headers"]["x-umami-api-key"] == "local-key"


def test_umami_request_sends_browser_user_agent(monkeypatch):
    captured: dict[str, object] = {}

    class DummyResponse:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

        def read(self):
            return b"{}"

    def fake_urlopen(request, timeout=0):
        captured["url"] = request.full_url
        captured["timeout"] = timeout
        captured["headers"] = {key.lower(): value for key, value in request.header_items()}
        return DummyResponse()

    monkeypatch.setattr(umami_util.safebox, "read_env_backed_value", lambda key: "secret-key")
    monkeypatch.setattr(umami_util.urllib.request, "urlopen", fake_urlopen)
    out = umami_util.umami_request("websites/wid/stats", {"hostname": "demo.example"}, "https://api.umami.is/v1")
    assert out == {}
    assert captured["url"] == "https://api.umami.is/v1/websites/wid/stats?hostname=demo.example"
    headers = captured["headers"]
    assert headers["user-agent"] == umami_util._UMAMI_USER_AGENT
    assert headers["x-umami-api-key"] == "secret-key"


def test_website_stats_normalizes_numbers_and_value_objects(monkeypatch):
    monkeypatch.setattr(
        umami_util,
        "umami_request",
        lambda *a, **k: {"pageviews": {"value": 42}, "visitors": 17, "visits": {"value": 20}, "bounces": 3, "totaltime": 999},
    )
    stats = umami_util.website_stats("wid", start_ms=0, end_ms=1, api_endpoint="https://api.umami.is/v1", hostname="x.example")
    assert stats == {"pageviews": 42, "visitors": 17, "visits": 20, "bounces": 3, "totaltime": 999}


def test_website_pageviews_series_normalizes_xy(monkeypatch):
    captured = {}

    def fake_request(path, params, api_endpoint, **k):
        captured["path"] = path
        captured["params"] = params
        return {
            "pageviews": [{"x": "2026-06-10 00:00:00", "y": "5"}, {"x": "2026-06-11 00:00:00", "y": 8}],
            "sessions": [{"x": "2026-06-10 00:00:00", "y": 3}],
        }

    monkeypatch.setattr(umami_util, "umami_request", fake_request)
    out = umami_util.website_pageviews_series(
        "wid", start_ms=0, end_ms=10, unit="day", api_endpoint="https://api.umami.is/v1", hostname="x.example"
    )
    assert captured["path"] == "websites/wid/stats".replace("stats", "pageviews")
    assert captured["params"]["unit"] == "day" and captured["params"]["hostname"] == "x.example"
    assert out["pageviews"] == [{"x": "2026-06-10 00:00:00", "y": 5}, {"x": "2026-06-11 00:00:00", "y": 8}]
    assert out["sessions"] == [{"x": "2026-06-10 00:00:00", "y": 3}]


# --------------------------------------------------------------------------- #
# Business analytics summary (best-effort, never raises, never faked)          #
# --------------------------------------------------------------------------- #

def test_summary_disabled_returns_not_configured_without_network(monkeypatch):
    monkeypatch.setattr(core, "_analytics_umami_config", lambda: {"enabled": False})
    out = core._business_analytics_summary("demo")
    assert out == {"configured": False, "reason": "analytics disabled"}


def test_core_analytics_config_uses_effective_load_config(monkeypatch):
    from takyon_cli import config as takyon_config

    monkeypatch.setattr(
        takyon_config,
        "load_config",
        lambda: {
            "analytics": {
                "umami": {
                    "enabled": True,
                    "website_id": "WID",
                    "script_src": "https://cloud.umami.is/script.js",
                    "api_endpoint": "https://api.umami.is/v1",
                }
            }
        },
    )
    assert core._analytics_umami_config() == {
        "enabled": True,
        "website_id": "WID",
        "script_src": "https://cloud.umami.is/script.js",
        "api_endpoint": "https://api.umami.is/v1",
    }


def test_summary_enabled_without_key_reports_missing_key(monkeypatch):
    monkeypatch.setattr(
        core,
        "_analytics_umami_config",
        lambda: {"enabled": True, "website_id": "WID", "api_endpoint": "https://api.umami.is/v1"},
    )
    monkeypatch.setattr(umami_util, "umami_configured", lambda: False)
    out = core._business_analytics_summary("demo")
    assert out["configured"] is False and "UMAMI_API_KEY" in out["reason"]


def test_summary_ok_path_returns_stats(monkeypatch):
    core._ANALYTICS_SUMMARY_CACHE.clear()
    monkeypatch.setattr(
        core,
        "_analytics_umami_config",
        lambda: {"enabled": True, "website_id": "WID", "api_endpoint": "https://api.umami.is/v1", "stats_cache_seconds": 0},
    )
    monkeypatch.setattr(umami_util, "umami_configured", lambda: True)
    monkeypatch.setattr(
        umami_util,
        "website_stats",
        lambda *a, **k: {"pageviews": 5, "visitors": 4, "visits": 4, "bounces": 1, "totaltime": 60},
    )
    out = core._business_analytics_summary("demo", days=30)
    assert out["configured"] is True and out["ok"] is True
    assert out["window_days"] == 30 and out["stats"]["visitors"] == 4
