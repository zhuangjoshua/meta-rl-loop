"""Tests for the DataForSEO-backed keyword modes of business_seo_query_data.

These cover the swap of the keyword backend from the (free) Google Ads Keyword Planner
API to DataForSEO: the per-request price gate, the normalizer that keeps the tool result
contract stable, geo/language resolution with legacy fallbacks, and fail-closed behavior
when the safebox creds are absent.
"""

from __future__ import annotations

import json
from decimal import Decimal

import pytest

from agent.usage_pricing import CanonicalUsage, estimate_usage_cost
from plugins.takyon import core as takyon_core
from plugins.takyon.core import (
    TakyonError,
    _seo_dataforseo_keyword_ideas,
    _seo_dataforseo_keyword_metrics,
    _seo_dataforseo_language_code,
    _seo_dataforseo_location_code,
    _seo_normalize_dataforseo_item,
    _seo_resolve_dataforseo_auth,
    handle_business_seo_query_data,
)


# ── secrets: both halves must be safebox-sensitive (resolvable through the gate) ──
def test_dataforseo_credentials_are_safebox_sensitive():
    from plugins.takyon import safebox as takyon_safebox

    # PASSWORD matches the _PASSWORD suffix; LOGIN is explicitly allowlisted (no
    # sensitive suffix), so the safebox would otherwise refuse to serve it.
    assert takyon_safebox.is_sensitive_env_key("DATAFORSEO_LOGIN")
    assert takyon_safebox.is_sensitive_env_key("DATAFORSEO_PASSWORD")


# ── pricing: the load-bearing gate value ──────────────────────────────────
@pytest.mark.parametrize("op", ["search_volume", "keywords_for_keywords", "keywords_for_site"])
def test_dataforseo_ops_are_priced(op):
    result = estimate_usage_cost(op, CanonicalUsage(request_count=1), provider="dataforseo")
    assert result.amount_usd == Decimal("0.075")


def test_unpriced_dataforseo_op_resolves_to_no_price():
    # An op with no pricing entry must resolve to no price so the metered caller fails closed
    # (the spend seam refuses an unpriced call rather than spending budget).
    result = estimate_usage_cost("not_a_real_op", CanonicalUsage(request_count=1), provider="dataforseo")
    assert result.amount_usd is None


# ── normalizer: DataForSEO row → stable tool result contract ──────────────
def test_normalizer_maps_fields_to_stable_shape():
    item = {
        "keyword": "running shoes",
        "search_volume": 12000,
        "competition": "HIGH",
        "competition_index": 88,
        "low_top_of_page_bid": 0.42,
        "high_top_of_page_bid": 1.5,
        "cpc": 0.93,
        "monthly_searches": [
            {"year": 2026, "month": 1, "search_volume": 10000},
            {"year": 2026, "month": 12, "search_volume": 14000},
        ],
    }
    row = _seo_normalize_dataforseo_item(item)
    assert row["keyword"] == "running shoes"
    assert row["avg_monthly_searches"] == 12000
    assert row["competition"] == "HIGH"
    assert row["competition_index"] == 88
    # dollar bids → integer micros (the previous Google Ads field contract)
    assert row["low_top_of_page_bid_micros"] == 420000
    assert row["high_top_of_page_bid_micros"] == 1500000
    assert row["cpc"] == 0.93
    # integer month → the protobuf-style enum name the old backend emitted
    assert row["monthly_search_volumes"][0] == {"year": 2026, "month": "JANUARY", "monthly_searches": 10000}
    assert row["monthly_search_volumes"][1]["month"] == "DECEMBER"
    # no DataForSEO equivalent → empty, never fabricated
    assert row["close_variants"] == []


def test_normalizer_tolerates_missing_optional_fields():
    row = _seo_normalize_dataforseo_item({"keyword": "x"})
    assert row["avg_monthly_searches"] is None
    assert row["low_top_of_page_bid_micros"] is None
    assert row["monthly_search_volumes"] == []
    assert row["close_variants"] == []


# ── geo / language resolution (with legacy Google Ads fallbacks) ──────────
def test_location_code_resolution():
    assert _seo_dataforseo_location_code({"location_code": 2826}) == 2826
    assert _seo_dataforseo_location_code({"geo_target_ids": ["2840"]}) == 2840  # legacy fallback
    assert _seo_dataforseo_location_code({}) == 2840  # default United States


def test_language_code_resolution():
    assert _seo_dataforseo_language_code({"language_code": "es"}) == "es"
    assert _seo_dataforseo_language_code({"language_id": "1000"}) == "en"  # legacy id → code
    assert _seo_dataforseo_language_code({}) == "en"  # default English


# ── fail-closed: missing safebox creds ────────────────────────────────────
def test_auth_fails_closed_when_creds_missing(monkeypatch):
    monkeypatch.setattr(takyon_core, "_seo_resolve_sensitive_env", lambda name: "")
    with pytest.raises(TakyonError) as exc:
        _seo_resolve_dataforseo_auth()
    assert "dataforseo_unconfigured" in str(exc.value)


def test_auth_requires_both_login_and_password(monkeypatch):
    # Login present but password missing must still fail closed.
    monkeypatch.setattr(
        takyon_core,
        "_seo_resolve_sensitive_env",
        lambda name: "user" if name == "DATAFORSEO_LOGIN" else "",
    )
    with pytest.raises(TakyonError):
        _seo_resolve_dataforseo_auth()


def test_handler_keyword_historical_fails_closed_without_creds(monkeypatch):
    # In a business scope (so the scope guard passes) but with no creds → fails closed.
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "testco")
    monkeypatch.setattr(takyon_core, "_seo_resolve_sensitive_env", lambda name: "")
    out = json.loads(handle_business_seo_query_data({"mode": "keyword-historical", "keywords": ["a"]}))
    assert out["success"] is False
    assert "dataforseo_unconfigured" in out["error"]


def test_keyword_modes_refuse_outside_business_scope(monkeypatch):
    # Paid keyword modes must refuse (no ungated spend) when there is no business scope,
    # even when creds are present. The free gsc modes are unaffected.
    monkeypatch.setattr(takyon_core, "_session_business_slug", lambda: "")
    monkeypatch.setattr(takyon_core, "_seo_resolve_sensitive_env", lambda name: "present")
    for mode_args in (
        {"mode": "keyword-historical", "keywords": ["a"]},
        {"mode": "keyword-ideas", "keywords": ["a"]},
    ):
        out = json.loads(handle_business_seo_query_data(mode_args))
        assert out["success"] is False
        assert "business scope" in out["error"]


# ── the paid path: reserve → settle on success, reserve → release on failure ──
def _install_fake_meter(events):
    from agent import web_spend_meter

    class _FakeMeter:
        def reserve(self, *, pricing_key, provider, op, units, usage, purpose):
            events.append(("reserve", pricing_key, op, units))
            return object()  # opaque non-None inner handle

        def settle(self, handle, *, units, usage):
            events.append(("settle", units))

        def release(self, handle, *, error):
            events.append(("release", error))

    web_spend_meter.register_spend_meter(_FakeMeter())
    return web_spend_meter


def test_metered_call_reserves_then_settles_on_success(monkeypatch):
    events: list = []
    meter_mod = _install_fake_meter(events)
    try:
        monkeypatch.setattr(
            takyon_core,
            "_seo_call_dataforseo",
            lambda path, payload, *, login, password: [{"keyword": "k"}],
        )
        rows = takyon_core._seo_metered_dataforseo(
            "search_volume", "/v3/x", {"keywords": ["k"]}, login="L", password="P"
        )
    finally:
        meter_mod.register_spend_meter(None)
    assert rows == [{"keyword": "k"}]
    assert [e[0] for e in events] == ["reserve", "settle"]  # reserved, settled, NOT released
    assert events[0][1] == ("dataforseo", "search_volume")  # correct pricing_key
    assert events[0][3] == 1  # flat per-request → units=1


def test_metered_call_releases_on_failure(monkeypatch):
    events: list = []
    meter_mod = _install_fake_meter(events)

    def _boom(path, payload, *, login, password):
        raise TakyonError("dataforseo boom")

    try:
        monkeypatch.setattr(takyon_core, "_seo_call_dataforseo", _boom)
        with pytest.raises(TakyonError):
            takyon_core._seo_metered_dataforseo("search_volume", "/v3/x", {}, login="L", password="P")
    finally:
        meter_mod.register_spend_meter(None)
    assert [e[0] for e in events] == ["reserve", "release"]  # reserved, released, NOT settled


# ── endpoint selection: keyword seed vs page/site seed ────────────────────
def _stub_metered(monkeypatch, captured):
    def _fake(op, path, payload, *, login, password):
        captured["op"] = op
        captured["path"] = path
        captured["payload"] = payload
        captured["login"] = login
        return [{"keyword": "k", "search_volume": 5}]

    monkeypatch.setattr(takyon_core, "_seo_metered_dataforseo", _fake)


def test_keyword_ideas_keyword_seed_uses_keywords_for_keywords(monkeypatch):
    captured: dict = {}
    _stub_metered(monkeypatch, captured)
    rows = _seo_dataforseo_keyword_ideas(
        keywords=[f"k{i}" for i in range(30)],  # >20 seeds → capped by the endpoint limit
        page_url=None,
        location_code=2840,
        language_code="en",
        include_adult_keywords=False,
        limit=100,
        login="L",
        password="P",
    )
    assert captured["op"] == "keywords_for_keywords"
    assert captured["path"].endswith("/keywords_for_keywords/live")
    assert len(captured["payload"]["keywords"]) == 20
    assert rows and rows[0]["avg_monthly_searches"] == 5


def test_keyword_ideas_page_seed_uses_keywords_for_site(monkeypatch):
    captured: dict = {}
    _stub_metered(monkeypatch, captured)
    _seo_dataforseo_keyword_ideas(
        keywords=[],
        page_url="https://example.com/pricing",
        location_code=2840,
        language_code="en",
        include_adult_keywords=False,
        limit=100,
        login="L",
        password="P",
    )
    assert captured["op"] == "keywords_for_site"
    assert captured["path"].endswith("/keywords_for_site/live")
    assert captured["payload"]["target"] == "https://example.com/pricing"


def test_keyword_metrics_uses_search_volume(monkeypatch):
    captured: dict = {}
    _stub_metered(monkeypatch, captured)
    _seo_dataforseo_keyword_metrics(
        keywords=["running shoes"],
        location_code=2840,
        language_code="en",
        login="L",
        password="P",
    )
    assert captured["op"] == "search_volume"
    assert captured["path"].endswith("/search_volume/live")
    assert captured["payload"]["keywords"] == ["running shoes"]
    assert captured["payload"]["location_code"] == 2840
    assert captured["payload"]["language_code"] == "en"
