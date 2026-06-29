"""Unit tests for the Tavily web provider authority boundary.

Covers ``plugins.web.tavily.provider._tavily_request`` — the single chokepoint
for Tavily search/extract/crawl. On a runtime plane it must NOT read the raw
``TAVILY_API_KEY`` or use the shared Safebox transport token as spend authority.
Search/extract must mint a caller-bound ``operator.session`` capability and call
the Safebox proxy. No network; stdlib + pytest + monkeypatch.

Contract:
  (a) When ``safebox._use_remote_authority()`` is True (runtime plane), the call
      mints a caller-bound operator.session capability and uses the Safebox proxy,
      never a raw key.
  (b) When it is False (safebox host / local dev), the direct httpx path resolves
      the key through the safebox/local config helper and injects it into the body.
"""

import pytest

from plugins.takyon import safebox as _safebox
from plugins.web.tavily import provider as tavily


# ─── (a) runtime plane → proxy with operator.session, no raw key ──────────────


def test_tavily_request_remote_plane_uses_operator_session_proxy(monkeypatch):
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(tavily, "_operator_session_token", lambda _safebox: "cap-operator-session")

    # No TAVILY_API_KEY in the env at all — proves the runtime plane never needs it.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    def _boom_post(*a, **k):
        raise AssertionError("direct httpx.post on the runtime plane")

    # If the direct path were taken it would import httpx and call .post — guard it.
    import httpx

    monkeypatch.setattr(httpx, "post", _boom_post)

    captured = {}

    def _proxy(provider, path, payload, *, token, **_kwargs):
        captured.update({"provider": provider, "path": path, "payload": payload, "token": token})
        return {"results": []}

    monkeypatch.setattr(_safebox, "proxy_request", _proxy)

    result = tavily._tavily_request("search", {"query": "hi", "max_results": 5})

    assert result == {"results": []}
    assert captured == {
        "provider": "tavily",
        "path": "search",
        "payload": {"query": "hi", "max_results": 5},
        "token": "cap-operator-session",
    }


def test_tavily_request_remote_plane_ignores_raw_key_env(monkeypatch):
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env-must-not-unlock-runtime")
    monkeypatch.setattr(tavily, "_operator_session_token", lambda _safebox: "cap")
    monkeypatch.setattr(_safebox, "proxy_request", lambda *a, **k: {"results": []})

    def _local_key_used(*_args, **_kwargs):
        raise AssertionError("runtime plane tried to resolve local Tavily key")

    monkeypatch.setattr(tavily, "_local_tavily_api_key", _local_key_used)

    assert tavily._tavily_request("search", {"query": "hi", "max_results": 5}) == {"results": []}

    monkeypatch.setattr(_safebox, "provider_proxy_base_url", lambda: "http://safebox")
    assert tavily.TavilyWebSearchProvider().is_available() is True
    assert tavily.TavilyWebSearchProvider().authority_gates_spend() is True


def test_tavily_request_remote_plane_proxies_extract_too(monkeypatch):
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(tavily, "_operator_session_token", lambda _safebox: "cap")
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    captured = {}

    def _proxy(provider, path, payload, *, token, **_kwargs):
        captured.update({"provider": provider, "path": path, "payload": payload, "token": token})
        return {"results": [{"url": "https://x.com"}]}

    monkeypatch.setattr(_safebox, "proxy_request", _proxy)

    assert tavily._tavily_request("/Extract/", {"urls": ["https://x.com"]})["results"][0]["url"] == "https://x.com"
    assert captured["provider"] == "tavily"
    assert captured["path"] == "extract"
    assert captured["token"] == "cap"


def test_tavily_remote_plane_does_not_advertise_crawl(monkeypatch):
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    assert tavily.TavilyWebSearchProvider().supports_crawl() is False


def test_tavily_search_provider_surfaces_missing_operator_capability(monkeypatch):
    """End-to-end through the provider: remote runtime refuses if it cannot mint the operator token."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(
        tavily,
        "_operator_session_token",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            _safebox.SafeboxAuthorityUnavailable("operator.session capability missing")
        ),
    )
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = tavily.TavilyWebSearchProvider().search("query", limit=3)
    assert result["success"] is False
    assert "operator.session capability" in result["error"]


def test_web_tools_skip_outer_meter_for_authority_gated_tavily(monkeypatch):
    """Safebox-proxied Tavily gates money inside the proxy; web_tools must not double-reserve."""
    from tools import web_tools
    from agent import web_search_registry as reg

    class _AuthorityGatedTavily:
        name = "tavily"
        display_name = "Tavily"

        def supports_search(self):
            return True

        def authority_gates_spend(self):
            return True

        def search(self, query, limit=5):
            return {"success": True, "data": {"web": [{"title": query, "url": "https://example.com"}]}}

    monkeypatch.setattr(web_tools, "_get_search_backend", lambda: "tavily")
    monkeypatch.setattr(reg, "get_provider", lambda name: _AuthorityGatedTavily())

    def _must_not_reserve(**_kwargs):
        raise AssertionError("outer web_spend meter should not run for authority-gated provider")

    monkeypatch.setattr(web_tools, "reserve_paid_call", _must_not_reserve)

    result = web_tools.web_search_tool("query", limit=1)
    assert '"success": true' in result


# ─── (b) local / safebox authority → direct httpx path unchanged ──────────────


def test_tavily_request_direct_path_when_not_remote(monkeypatch):
    """No remote authority -> the direct httpx path runs: the local Safebox helper
    resolves the key, it is injected into the body, and the proxy is NEVER touched."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: False)
    monkeypatch.setattr(
        _safebox,
        "proxy_request",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("proxy used on the local/safebox plane")
        ),
    )
    monkeypatch.setattr(_safebox, "first_env_backed_value", lambda *names: "tvly-local-key")

    captured = {}

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"results": []}

    def _fake_post(url, json=None, headers=None, timeout=None):
        captured["url"] = url
        captured["json"] = json
        captured["headers"] = headers
        return _Resp()

    import httpx

    monkeypatch.setattr(httpx, "post", _fake_post)

    tavily._tavily_request("search", {"query": "hi"})

    assert "api.tavily.com/search" in captured["url"]
    assert captured["json"]["api_key"] == "tvly-local-key"
    assert captured["json"]["query"] == "hi"


def test_tavily_request_direct_path_raises_without_key(monkeypatch):
    """Local plane with no key → ValueError (the existing fail-closed behavior)."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: False)
    monkeypatch.setattr(_safebox, "first_env_backed_value", lambda *names: "")

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        tavily._tavily_request("search", {"query": "hi"})
