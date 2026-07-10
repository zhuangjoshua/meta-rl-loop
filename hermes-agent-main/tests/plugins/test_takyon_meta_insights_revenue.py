"""Meta-attributed revenue/ROAS read-path + client-side Purchase pixel (option 1).

Covers the read side (``core._meta_aggregate_insights_rows`` turning Meta's
``action_values``/``actions`` into purchase value / count / ROAS, deduped across the
synonym action_types) and the write side (the starter app shell firing a value-carrying
``Purchase`` pixel event on ``?checkout=success``)."""

from plugins.takyon import core


def _row(**kw):
    base = {"impressions": "1000", "clicks": "50", "spend": "20.00"}
    base.update(kw)
    return base


# ── read side: purchase value / count / roas ────────────────────────────────


def test_purchase_value_and_roas_from_action_values():
    rows = [
        _row(
            spend="25.00",
            action_values=[{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "100.00"}],
            actions=[{"action_type": "offsite_conversion.fb_pixel_purchase", "value": "2"}],
        )
    ]
    totals = core._meta_aggregate_insights_rows(rows)
    assert totals["purchase_value_cents"] == 10000
    assert totals["purchase_value_usd"] == 100.0
    assert totals["purchase_count"] == 2
    # ROAS = revenue / spend = 100 / 25 = 4.0
    assert totals["roas"] == 4.0


def test_link_clicks_and_conversion_rate_from_actions():
    rows = [
        _row(
            spend="25.00",
            action_values=[{"action_type": "purchase", "value": "100.00"}],
            actions=[
                {"action_type": "purchase", "value": "2"},
                {"action_type": "link_click", "value": "40"},
            ],
        )
    ]
    totals = core._meta_aggregate_insights_rows(rows)
    assert totals["link_clicks"] == 40
    # conversion rate from link clicks = 2 / 40 = 5%
    assert totals["link_click_conversion_rate"] == 5.0


def test_no_link_clicks_leaves_conversion_rate_null():
    rows = [_row(spend="10.00", action_values=[], actions=[{"action_type": "purchase", "value": "1"}])]
    totals = core._meta_aggregate_insights_rows(rows)
    assert totals["link_clicks"] == 0
    assert totals["link_click_conversion_rate"] is None


def test_purchase_synonyms_are_counted_once_not_summed():
    # Meta lists the SAME purchase under several synonym action_types with identical value;
    # the aggregator must take ONE, not sum them (which would triple-count).
    rows = [
        _row(
            spend="10.00",
            action_values=[
                {"action_type": "omni_purchase", "value": "50.00"},
                {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "50.00"},
                {"action_type": "purchase", "value": "50.00"},
            ],
            actions=[
                {"action_type": "omni_purchase", "value": "1"},
                {"action_type": "offsite_conversion.fb_pixel_purchase", "value": "1"},
            ],
        )
    ]
    totals = core._meta_aggregate_insights_rows(rows)
    assert totals["purchase_value_cents"] == 5000  # 50.00 once, not 150.00
    assert totals["purchase_count"] == 1
    assert totals["roas"] == 5.0


def test_no_purchase_actions_yields_zero_revenue_and_null_roas_when_no_spend():
    rows = [_row(spend="0", action_values=None, actions=None)]
    totals = core._meta_aggregate_insights_rows(rows)
    assert totals["purchase_value_cents"] == 0
    assert totals["purchase_count"] == 0
    assert totals["roas"] is None  # spend is zero → ROAS undefined, not 0


def test_spend_without_purchases_gives_zero_roas():
    rows = [_row(spend="30.00", action_values=[], actions=[])]
    totals = core._meta_aggregate_insights_rows(rows)
    assert totals["purchase_value_cents"] == 0
    assert totals["roas"] == 0.0


def test_first_action_metric_prefers_canonical_order():
    entries = [
        {"action_type": "purchase", "value": "7"},
        {"action_type": "omni_purchase", "value": "9"},
    ]
    # omni_purchase wins over purchase regardless of list order.
    assert core._meta_first_action_metric(entries, core._META_PURCHASE_ACTION_TYPES) == 9.0
    assert core._meta_first_action_metric(None, core._META_PURCHASE_ACTION_TYPES) is None
    assert core._meta_first_action_metric([], core._META_PURCHASE_ACTION_TYPES) is None


# ── write side: client-side Purchase pixel event (LIVE scaffold, not the dead starter) ──


def _scaffold_hooks_source() -> str:
    from pathlib import Path

    return (
        Path(core.__file__).parent / "subuser_app_kit" / "scaffold" / "src" / "lib" / "hooks.ts"
    ).read_text(encoding="utf-8")


def test_live_scaffold_fires_value_carrying_purchase_on_checkout_success():
    ts = _scaffold_hooks_source()
    # Fires the standard Purchase event with a value + currency (not just PageView).
    assert 'fbq("track", "Purchase"' in ts
    assert "value: Math.round(cents) / 100" in ts
    assert "currency:" in ts
    # Only when the pixel is actually installed, and ONLY on an explicit success return —
    # a checkout=cancel (or bare session_id) return must never mint a Purchase.
    assert 'if (params.get("checkout") !== "success") return;' in ts
    assert "typeof fbq !== \"function\"" in ts
    # Deduped once per session so a refresh cannot double-count.
    assert "tk_meta_purchase_fired" in ts
    # Tagged with the business's own hostname so cross-business purchases on the SHARED
    # pixel stay identifiable per business in Events Manager.
    assert "content_name: String(window.location.hostname" in ts
    # Wired into the one return-from-checkout hook that actually runs.
    assert "fireMetaPurchasePixel(params);" in ts


def test_dead_legacy_starter_carries_no_pixel_code():
    # The _subuser_app_starter_*_js family has zero callers (legacy Next starter); Purchase
    # tracking must live ONLY in the real vite scaffold so nobody mistakes the dead file
    # for the live implementation again.
    js = core._subuser_app_starter_access_page_js()
    assert "fbq" not in js
