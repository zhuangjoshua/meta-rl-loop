"""Unit tests for the Tavily web provider authority boundary.

Covers ``plugins.web.tavily.provider._tavily_request`` — the single chokepoint
for Tavily search/extract/crawl. On a runtime plane it must NOT read the raw
``TAVILY_API_KEY``. This generic provider does not carry a business-owner identity
and therefore cannot mint an operator.session capability; it must fail closed
instead of sending the shared Safebox transport token to the operator proxy. No
network; stdlib + pytest + monkeypatch.

Contract:
  (a) When ``safebox._use_remote_authority()`` is True (runtime plane), the call
      raises ``SafeboxAuthorityUnavailable`` before proxy/network/key access.
  (b) When it is False (safebox host / local dev), the direct httpx path resolves
      the key through the safebox/local config helper and injects it into the body.
"""

import pytest

from plugins.takyon import safebox as _safebox
from plugins.web.tavily import provider as tavily


# ─── (a) runtime plane → fail closed, no raw key ──────────────────────────────


def test_tavily_request_remote_plane_fails_closed_without_operator_capability(monkeypatch):
    """Remote authority with no caller-bound operator.session capability refuses before proxy/network."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(
        _safebox,
        "proxy_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("proxy used without capability")),
    )

    # No TAVILY_API_KEY in the env at all — proves the runtime plane never needs it.
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    def _boom_post(*a, **k):
        raise AssertionError("direct httpx.post on the runtime plane")

    # If the direct path were taken it would import httpx and call .post — guard it.
    import httpx

    monkeypatch.setattr(httpx, "post", _boom_post)

    with pytest.raises(_safebox.SafeboxAuthorityUnavailable, match="operator.session capability"):
        tavily._tavily_request("search", {"query": "hi", "max_results": 5})


def test_tavily_request_remote_plane_ignores_raw_key_env(monkeypatch):
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setenv("TAVILY_API_KEY", "tvly-env-must-not-unlock-runtime")

    def _local_key_used(*_args, **_kwargs):
        raise AssertionError("runtime plane tried to resolve local Tavily key")

    monkeypatch.setattr(tavily, "_local_tavily_api_key", _local_key_used)

    with pytest.raises(_safebox.SafeboxAuthorityUnavailable, match="operator.session capability"):
        tavily._tavily_request("search", {"query": "hi", "max_results": 5})

    assert tavily.TavilyWebSearchProvider().is_available() is False


def test_tavily_request_remote_plane_refuses_extract_too(monkeypatch):
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(
        _safebox,
        "proxy_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("proxy used without capability")),
    )
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    with pytest.raises(_safebox.SafeboxAuthorityUnavailable):
        tavily._tavily_request("/Extract/", {"urls": ["https://x.com"]})


def test_tavily_search_provider_surfaces_remote_authority_refusal(monkeypatch):
    """End-to-end through the provider: remote runtime refuses rather than falling back to raw key."""
    monkeypatch.setattr(_safebox, "_use_remote_authority", lambda: True)
    monkeypatch.setattr(
        _safebox,
        "proxy_request",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("proxy used without capability")),
    )
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)

    result = tavily.TavilyWebSearchProvider().search("query", limit=3)
    assert result["success"] is False
    assert "operator.session capability" in result["error"]


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
