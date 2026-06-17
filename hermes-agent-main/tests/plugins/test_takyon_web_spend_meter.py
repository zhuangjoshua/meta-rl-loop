"""Web spend boundary — wiring proof (no Postgres).

These cover the cutover Codex specified: the spend boundary lives in tools/web_tools.py at the
RESOLVED provider, a free backend never meters, a paid backend reserves-before-egress and
settles-on-success / releases-on-failure, a zero-spend early return never reserves, and the
summarizer LLM is metered as its own spend. A recording spy stands in for the budget so the tests
are deterministic and exercise the real web_tools code paths. The real-budget movement is proven
separately in test_takyon_web_spend_pg.py.
"""
from __future__ import annotations

import asyncio

import pytest

from agent import web_spend_meter
from agent.web_search_provider import WebSearchProvider
from agent.web_search_registry import provider_billing, register_provider, _reset_for_tests
from plugins.takyon import web_spend
import tools.web_tools as web_tools


# ── spy meter + fake provider ────────────────────────────────────────────────
class _SpyMeter:
    def __init__(self):
        self.calls = []

    def reserve(self, *, pricing_key, provider, op, units, usage, purpose):
        self.calls.append(("reserve", op, provider, units))
        return {"op": op}

    def settle(self, handle, *, units, usage):
        self.calls.append(("settle", handle["op"]))

    def release(self, handle, *, error):
        self.calls.append(("release", handle["op"]))

    def kinds(self):
        return [c[0] for c in self.calls]


class _FakeProvider(WebSearchProvider):
    def __init__(self, name, *, raises=False):
        self._name = name
        self._raises = raises

    @property
    def name(self) -> str:
        return self._name

    def is_available(self) -> bool:
        return True

    def supports_search(self) -> bool:
        return True

    def supports_extract(self) -> bool:
        return True

    def supports_crawl(self) -> bool:
        return True

    def search(self, query, limit=5):
        if self._raises:
            raise RuntimeError("provider boom")
        return {"success": True, "data": {"web": [{"title": "t", "url": "https://x", "description": "d", "position": 1}]}}

    def extract(self, urls, **kwargs):
        if self._raises:
            raise RuntimeError("provider boom")
        return [{"url": u, "title": "t", "content": "c"} for u in urls]


@pytest.fixture
def spy(monkeypatch):
    """Register a recording spy as THE spend meter; restore real (none) afterwards."""
    s = _SpyMeter()
    web_spend_meter.register_spend_meter(s)
    yield s
    web_spend_meter.register_spend_meter(None)


@pytest.fixture
def reg(monkeypatch):
    """Isolated registry; restored by clearing afterward."""
    _reset_for_tests()
    yield
    _reset_for_tests()


def _use_backend(monkeypatch, name):
    monkeypatch.setattr(web_tools, "_get_search_backend", lambda: name)
    monkeypatch.setattr(web_tools, "_get_extract_backend", lambda: name)


# ── provider_billing classification (server-owned) ───────────────────────────
def test_provider_billing_classifies_paid_free_and_unknown():
    for paid in ("tavily", "firecrawl", "exa", "parallel"):
        assert provider_billing(paid)[0] == "paid"
    for free in ("searxng", "brave-free", "ddgs"):
        assert provider_billing(free) == ("free", None)
    assert provider_billing("made-up-backend") == ("unknown", None)  # fails closed downstream


# ── seam contract ────────────────────────────────────────────────────────────
def test_seam_paid_call_with_no_meter_in_business_session_fails_closed(monkeypatch):
    web_spend_meter.register_spend_meter(None)
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", "acme")
    with pytest.raises(web_spend_meter.SpendBlocked):
        web_spend_meter.reserve_paid_call(
            pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="x"
        )


def test_seam_no_meter_no_business_session_allows(monkeypatch):
    web_spend_meter.register_spend_meter(None)
    monkeypatch.delenv("TAKYON_SESSION_BUSINESS_SLUG", raising=False)
    handle = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="x"
    )
    assert handle is None
    # settle/release on a None handle are no-ops (no crash)
    web_spend_meter.settle_paid_call(None, units=1)
    web_spend_meter.release_paid_call(None, error="e")


def test_seam_routes_to_registered_meter(spy):
    handle = web_spend_meter.reserve_paid_call(
        pricing_key=("tavily", "search"), provider="tavily", op="web_search", units=1, purpose="x"
    )
    web_spend_meter.settle_paid_call(handle, units=1)
    assert spy.kinds() == ["reserve", "settle"]


# ── server-owned pricing ─────────────────────────────────────────────────────
def test_price_microusd_priced_and_unpriced():
    # tavily search is $0.008 = 8000 microUSD
    assert web_spend._price_microusd(("tavily", "search"), units=1, usage=None) == 8000
    assert web_spend._price_microusd(("tavily", "extract"), units=3, usage=None) == 24000
    # unpriced (provider/op absent from usage_pricing) -> None -> caller fails closed
    assert web_spend._price_microusd(("nope", "search"), units=1, usage=None) is None


# ── web_tools spend boundary (Codex matrix) ──────────────────────────────────
def test_web_search_paid_success_reserves_then_settles(spy, reg, monkeypatch):
    register_provider(_FakeProvider("tavily"))
    _use_backend(monkeypatch, "tavily")
    out = web_tools.web_search_tool("hello", limit=3)
    assert '"success": true' in out.lower()
    assert spy.kinds() == ["reserve", "settle"]  # paid call that ran -> settled


def test_web_search_paid_failure_reserves_then_releases(spy, reg, monkeypatch):
    register_provider(_FakeProvider("tavily", raises=True))
    _use_backend(monkeypatch, "tavily")
    out = web_tools.web_search_tool("hello", limit=3)
    assert "error" in out.lower()
    assert spy.kinds() == ["reserve", "release"]  # provider raised -> hold released, no charge


def test_web_search_free_backend_does_not_meter(spy, reg, monkeypatch):
    register_provider(_FakeProvider("searxng"))
    _use_backend(monkeypatch, "searxng")
    out = web_tools.web_search_tool("hello", limit=3)
    assert '"success": true' in out.lower()
    assert spy.calls == []  # free backend spends nothing -> never metered


def test_web_extract_blocked_before_provider_does_not_meter(spy, reg, monkeypatch):
    register_provider(_FakeProvider("tavily"))
    _use_backend(monkeypatch, "tavily")
    # All-private URLs are SSRF-filtered to an empty safe set BEFORE the provider call / reservation.
    out = asyncio.run(
        web_tools.web_extract_tool(["http://169.254.169.254/latest/meta-data/"], use_llm_processing=False)
    )
    assert isinstance(out, str)
    assert spy.calls == []  # nothing reached the provider -> nothing reserved


def test_web_extract_paid_success_settles_per_url(spy, reg, monkeypatch):
    register_provider(_FakeProvider("tavily"))
    _use_backend(monkeypatch, "tavily")
    monkeypatch.setattr(web_tools, "is_safe_url", lambda u: True)  # avoid DNS in hermetic CI
    out = asyncio.run(
        web_tools.web_extract_tool(["https://a.example", "https://b.example"], use_llm_processing=False)
    )
    assert isinstance(out, str)
    assert spy.calls and spy.calls[0][0] == "reserve" and spy.calls[0][3] == 2  # units == #safe URLs
    assert spy.kinds() == ["reserve", "settle"]


# ── summarizer LLM as its own spend (invariant #8) ───────────────────────────
class _FakeUsage:
    input_tokens = 1200
    output_tokens = 800


class _FakeResp:
    usage = _FakeUsage()


def _patch_summarizer(monkeypatch, *, priced: bool):
    monkeypatch.setattr(web_tools, "has_known_pricing", lambda *a, **k: priced)
    monkeypatch.setattr(
        web_tools, "_resolve_web_extract_auxiliary", lambda model=None: (object(), "claude-haiku-4-5", {})
    )

    async def _fake_call(**kwargs):
        return _FakeResp()

    monkeypatch.setattr(web_tools, "async_call_llm", _fake_call)
    monkeypatch.setattr(web_tools, "extract_content_or_reasoning", lambda r: "SUMMARY")


def test_summarizer_paid_model_reserves_and_settles(spy, monkeypatch):
    _patch_summarizer(monkeypatch, priced=True)
    out = asyncio.run(web_tools._call_summarizer_llm("long content", "ctx", None, max_tokens=500))
    assert out == "SUMMARY"
    assert spy.calls and spy.calls[0][0] == "reserve" and spy.calls[0][1] == "web_summarize"
    assert spy.kinds() == ["reserve", "settle"]  # second truthful spend for extract+summarize


def test_summarizer_unpriced_model_is_not_metered(spy, monkeypatch):
    # An unpriced aux model is an operator-trusted free/local backend — summarize without charge.
    _patch_summarizer(monkeypatch, priced=False)
    out = asyncio.run(web_tools._call_summarizer_llm("long content", "ctx", None, max_tokens=500))
    assert out == "SUMMARY"
    assert spy.calls == []
