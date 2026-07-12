"""Meta-attributed revenue/ROAS read-path + server-only purchase signal.

Covers the read side (``core._meta_aggregate_insights_rows`` turning Meta's
``action_values``/``actions`` into purchase value / count / ROAS, deduped across the
synonym action_types) and the browser-side invariant that product customers cannot emit
the private CAPI event used for financial attribution."""

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
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
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
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
    assert totals["link_clicks"] == 40
    # conversion rate from link clicks = 2 / 40 = 5%
    assert totals["link_click_conversion_rate"] == 5.0


def test_custom_conversion_boundary_excludes_other_businesses_purchases():
    # SHARED-PIXEL isolation: a click-through that bought on a DIFFERENT business's site
    # arrives as a generic purchase action. With the business's own custom-conversion
    # boundary, ONLY offsite_conversion.custom.<id> counts — the generic purchase is
    # someone else's revenue and must not pollute this ROAS.
    rows = [
        _row(
            spend="20.00",
            action_values=[
                {"action_type": "purchase", "value": "500.00"},          # other business's sale
                {"action_type": "omni_purchase", "value": "500.00"},     # same sale, synonym
                {"action_type": "offsite_conversion.custom.777", "value": "40.00"},  # OURS
            ],
            actions=[
                {"action_type": "purchase", "value": "9"},
                {"action_type": "offsite_conversion.custom.777", "value": "2"},
            ],
        )
    ]
    totals = core._meta_aggregate_insights_rows(
        rows, purchase_action_types=("offsite_conversion.custom.777",))
    assert totals["purchase_value_usd"] == 40.0   # not 500
    assert totals["purchase_count"] == 2          # not 9
    assert totals["roas"] == 2.0                  # 40/20, ours only
    # An EXPLICIT generic boundary still counts generically (the constant remains for
    # callers that deliberately choose it) — there is no implicit default anymore.
    generic = core._meta_aggregate_insights_rows(
        rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
    assert generic["purchase_value_usd"] == 500.0


def test_unavailable_attribution_yields_none_not_zero():
    # Attribution is a financial boundary: when it cannot be resolved, purchases/revenue/
    # ROAS/conversion must be UNAVAILABLE (None) — never zero, and never silently generic.
    rows = [
        _row(
            spend="20.00",
            action_values=[{"action_type": "purchase", "value": "500.00"}],
            actions=[
                {"action_type": "purchase", "value": "9"},
                {"action_type": "link_click", "value": "40"},
            ],
        )
    ]
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=None)
    # Delivery metrics still aggregate…
    assert totals["spend_usd"] == 20.0
    assert totals["clicks"] == 50
    assert totals["link_clicks"] == 40
    # …but the financial fields are unavailable, not zero.
    assert totals["purchase_count"] is None
    assert totals["purchase_value_usd"] is None
    assert totals["roas"] is None
    assert totals["link_click_conversion_rate"] is None


def test_no_link_clicks_leaves_conversion_rate_null():
    rows = [_row(spend="10.00", action_values=[], actions=[{"action_type": "purchase", "value": "1"}])]
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
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
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
    assert totals["purchase_value_cents"] == 5000  # 50.00 once, not 150.00
    assert totals["purchase_count"] == 1
    assert totals["roas"] == 5.0


def test_no_purchase_actions_yields_zero_revenue_and_null_roas_when_no_spend():
    rows = [_row(spend="0", action_values=None, actions=None)]
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
    assert totals["purchase_value_cents"] == 0
    assert totals["purchase_count"] == 0
    assert totals["roas"] is None  # spend is zero → ROAS undefined, not 0


def test_spend_without_purchases_gives_zero_roas():
    rows = [_row(spend="30.00", action_values=[], actions=[])]
    totals = core._meta_aggregate_insights_rows(rows, purchase_action_types=core._META_PURCHASE_ACTION_TYPES)
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


# ── write side: browser must never mint the financial Purchase signal ──


def _scaffold_hooks_source() -> str:
    from pathlib import Path

    return (
        Path(core.__file__).parent / "subuser_app_kit" / "scaffold" / "src" / "lib" / "hooks.ts"
    ).read_text(encoding="utf-8")


def test_live_scaffold_does_not_emit_purchase_from_browser_checkout_query():
    ts = _scaffold_hooks_source()
    assert 'fbq("track", "Purchase"' not in ts
    assert "fireMetaPurchasePixel" not in ts
    assert "server-only conversion signal" in ts


def test_app_checkout_stamps_only_server_owned_meta_capi_metadata():
    import inspect

    source = inspect.getsource(core.handle_business_create_app_checkout)
    assert 'params["metadata[takyon_meta_capi]"] = "1"' in source
    assert 'params["metadata[takyon_meta_pixel_id]"] = meta_pixel_id' in source
    assert 'params["metadata[takyon_meta_site_host]"] = meta_site_host.lower()' in source
    assert "checkout_metadata" not in source[source.index("params: dict[str, Any]") :]


def test_dead_legacy_starter_carries_no_pixel_code():
    # The _subuser_app_starter_*_js family has zero callers (legacy Next starter); Purchase
    # tracking must live ONLY in the real vite scaffold so nobody mistakes the dead file
    # for the live implementation again.
    js = core._subuser_app_starter_access_page_js()
    assert "fbq" not in js
