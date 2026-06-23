"""Unit tests for the safebox-proxy cutover of the Tavily web provider.

Covers ``plugins.web.tavily.provider._tavily_request`` — the single chokepoint
for Tavily search/extract/crawl. On a runtime plane it must NOT read the raw
``TAVILY_API_KEY``; it must broker the call through the safebox provider proxy.
No network; stdlib + pytest + monkeypatch.

Contract:
  (a) When ``safebox._use_remote_authority()`` is True (runtime plane), the call
      goes through ``safebox.proxy_request("tavily", op, payload)`` and the raw
      ``TAVILY_API_KEY`` is NEVER read / no direct httpx.post is made. The proxy
      result (raw Tavily JSON) flows back unchanged through the normalizers.
  (b) When it is False (safebox host / local dev), the existing direct httpx
      path is used unchanged: key read from env, injected into the body.
"""

import pytest

from plugins.takyon import safebox as _safebox
from plugins.web.tavily import provider as tavily


# ─── (a) runtime plane → proxy, no raw key ────────────────────────────────────


def test_tavily_request_uses_proxy_on_runtime_plane(monkeypatch):
    """Remote authority → proxy_request(provider, op, payload); the raw key is
    never read and no httpx.post is made. The op is passed through verbatim and
    the api_key is NOT injected into the payload (the safebox injects it)."""
    proxy_calls = []
    raw_json = {"results": [{"title": "R", "url": "https://r.com", "content": "d"}]}

    def _fake_proxy(provider, path, payload, **kwargs):
        proxy_calls.append((provider, path, dict(payload)))
        return raw_json

    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(_safebox, "proxy_request", _fake_proxy)

    # No TAVILY_API_KEY in the env at all — proves the runtime plane never needs it.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    def _boom_post(*a, **k):
        raise AssertionError("direct httpx.post on the runtime plane")

    # If the direct path were taken it would import httpx and call .post — guard it.
    import httpx

    monkeypatch.setattr(httpx, "post", _boom_post)

    out = tavily._tavily_request("search", {"query": "hi", "max_results": 5})

    assert out is raw_json
    assert proxy_calls == [("tavily", "search", {"query": "hi", "max_results": 5})]
    # api_key must NOT be injected into the proxied payload.
    assert "api_key" not in proxy_calls[0][2]


def test_tavily_request_proxy_op_lowercased(monkeypatch):
    """The op passed to the proxy is the normalized (stripped/lowercased) name."""
    seen = {}

    def _fake_proxy(provider, path, payload, **kwargs):
        seen["op"] = path
        return {"results": []}

    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(_safebox, "proxy_request", _fake_proxy)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    tavily._tavily_request("/Extract/", {"urls": ["https://x.com"]})
    assert seen["op"] == "extract"


def test_tavily_search_provider_uses_proxy_result(monkeypatch):
    """End-to-end through the provider: a search on the runtime plane returns the
    normalized shape, driven by the proxy result with no raw key read."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(
        _safebox,
        "proxy_request",
        lambda *a, **k: {
            "results": [{"title": "Doc", "url": "https://d.com", "content": "snippet"}]
        },
    )
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = tavily.TavilyWebSearchProvider().search("query", limit=3)
    assert result["success"] is True
    web = result["data"]["web"]
    assert len(web) == 1
    assert web[0]["title"] == "Doc"
    assert web[0]["url"] == "https://d.com"
    assert web[0]["description"] == "snippet"


# ─── (b) local / safebox authority → direct httpx path unchanged ──────────────


def test_tavily_request_direct_path_when_not_remote(monkeypatch):
    """No remote authority → the existing direct httpx path runs: the env key is
    read and injected into the request body; the proxy is NEVER touched."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: False)
    monkeypatch.setattr(
        _safebox,
        "proxy_request",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("proxy used on the local/safebox plane")
        ),
    )
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-local-key")

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
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(ValueError, match="TAVILY_API_KEY"):
        tavily._tavily_request("search", {"query": "hi"})
