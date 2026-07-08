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


# ── write side: client-side Purchase pixel event ────────────────────────────


def test_starter_app_shell_fires_value_carrying_purchase_on_checkout_success():
    js = core._subuser_app_starter_access_page_js()
    # Fires the standard Purchase event with a value + currency (not just PageView).
    assert 'fbq("track", "Purchase"' in js
    assert "value: Math.round(valueCents) / 100" in js
    assert "currency:" in js
    # Only when the pixel is actually installed, and only on the success state.
    assert "typeof window.fbq !== \"function\"" in js
    assert 'checkoutState !== "success"' in js
    # Deduped once per session so a refresh cannot double-count.
    assert "tk_meta_purchase_fired" in js
    # Tagged with the business's own hostname so cross-business purchases on the SHARED
    # pixel stay identifiable per business in Events Manager.
    assert "content_name: String(window.location.hostname" in js
