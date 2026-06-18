"""Authoritative money-safety suite — INVARIANT 6.

GOAL_RULES.md §3 invariant 6:
    "No paid capability without a money gate — fail-closed default; a new
     spendful tool with no reservation envelope fails the check."

This module asserts the *pricing/billing fail-closed default* that makes
invariant 6 hold: an unpriced/unknown provider op is refused, and a spendful
path with no reservation envelope cannot spend. It overlaps invariant 1 (the
reserve→settle/release envelope) but is deliberately scoped to the FAIL-CLOSED
DEFAULT — the property that the *absence* of pricing/registration data is
treated as "refuse", never as "free" or "run anyway".

Three real, server-owned fail-closed seams are asserted here, every symbol
confirmed by reading source under hermes-agent-main/:

  1. agent/usage_pricing.py
       - estimate_usage_cost(...) -> CostResult(status="unknown") for an
         unpriced model+route (the upstream signal callers refuse on).
       - has_known_pricing(unknown_model) is False.
       - PricingEntry.request_cost exists for per-request (Tavily-style) pricing
         and the Tavily ("tavily","search") entry is priced (a priced op exists,
         so the fail-closed branch is the *absence* of an entry, not a blanket
         refuse).

  2. agent/web_search_registry.py
       - provider_billing(<unknown provider>) == ("unknown", None): an unknown
         provider is NOT classified "free"; it falls into the non-free branch
         that the egress point routes through the spend meter.

  3. agent/web_spend_meter.py
       - reserve_paid_call(...) raises SpendBlocked when a paid call happens in a
         business session (meter required) with NO meter registered: a spendful
         path with no reservation envelope fails closed.
       - tools/web_tools.py gates egress on `billing_mode == "free"` — so paid
         AND unknown both take the reserve path (source-structure assertion).

All assertions are no-credential / no-network: they import the real symbols and
exercise deterministic behavior, or read source and assert structure. None need
the Postgres rig.
"""

from __future__ import annotations

import inspect
from decimal import Decimal
from pathlib import Path

import pytest

# ── Real imports (confirmed importable from the repo) ────────────────────────
from agent.usage_pricing import (
    CanonicalUsage,
    CostResult,
    PricingEntry,
    estimate_usage_cost,
    has_known_pricing,
    _OFFICIAL_DOCS_PRICING,
)
from agent.web_search_registry import provider_billing, _PROVIDER_BILLING
from agent import web_spend_meter
from agent.web_spend_meter import SpendBlocked, reserve_paid_call


# A model name that is intentionally absent from every pricing source. Using a
# non-openrouter/non-anthropic/non-openai provider so resolution can't synthesize
# a route, and a nonsense model so no official-docs snapshot matches. This is an
# invariant test (unknown => refuse), NOT a catalog snapshot.
_UNPRICED_MODEL = "totally-made-up-model-xyz-9999"
_UNKNOWN_PROVIDER = "totally-made-up-provider-xyz-9999"


# ─────────────────────────────────────────────────────────────────────────────
# Seam 1: usage_pricing fail-closed default (the prime invariant-6 check)
# ─────────────────────────────────────────────────────────────────────────────


def test_has_known_pricing_false_for_unknown_model():
    """An unpriced/unknown model+provider has NO known pricing.

    `has_known_pricing` is the cheap gate callers use to decide whether a model
    is usable; for an unknown model with an unknown provider it MUST be False so
    spendful callers refuse before egress (no heuristic-family fallback).
    """
    assert has_known_pricing(_UNPRICED_MODEL, provider=_UNKNOWN_PROVIDER) is False


def test_estimate_usage_cost_status_unknown_for_unpriced_route():
    """estimate_usage_cost returns status='unknown' (and amount None) when the
    route is unpriced — the upstream signal that the spend must be REFUSED, not
    billed at $0."""
    usage = CanonicalUsage(input_tokens=1000, output_tokens=1000)
    result = estimate_usage_cost(_UNPRICED_MODEL, usage, provider=_UNKNOWN_PROVIDER)
    assert isinstance(result, CostResult)
    assert result.status == "unknown"
    # Unknown pricing must NOT masquerade as a concrete (e.g. zero) cost.
    assert result.amount_usd is None


def test_estimate_usage_cost_unknown_is_not_zero_dollar_pass():
    """Guard the dangerous failure mode explicitly: an unknown route must never
    return a priced/estimated/included status with a real amount. A $0 'pass'
    here would let unpriced spend through ungated."""
    usage = CanonicalUsage(input_tokens=500)
    result = estimate_usage_cost(_UNPRICED_MODEL, usage, provider=_UNKNOWN_PROVIDER)
    assert result.status not in {"estimated", "actual", "included"}
    assert result.amount_usd is None


def test_pricing_entry_has_request_cost_field_for_per_request_providers():
    """PricingEntry exposes `request_cost` (per-request USD), the field per-request
    providers (Tavily search/extract) price on. Without this field the per-request
    fail-closed pricing path could not exist."""
    field_names = {f for f in PricingEntry.__dataclass_fields__}
    assert "request_cost" in field_names
    # A bare PricingEntry defaults request_cost to None (unpriced => refused
    # upstream), proving "absence of a price" is the default, not a free pass.
    assert PricingEntry().request_cost is None


def test_priced_per_request_op_exists_so_refusal_is_about_absence():
    """The Tavily ("tavily","search") per-request op IS priced via request_cost.

    This anchors the fail-closed semantics: the system refuses an op because its
    `(namespace, op)` entry is ABSENT — not because per-request pricing is
    universally unavailable. Asserts the relationship (entry => positive
    request_cost), not a specific dollar value, so it is not a catalog snapshot.
    """
    entry = _OFFICIAL_DOCS_PRICING.get(("tavily", "search"))
    assert entry is not None, "expected a priced Tavily per-request search op"
    assert isinstance(entry.request_cost, Decimal)
    assert entry.request_cost > 0


def test_absent_per_request_op_has_no_pricing_entry():
    """A made-up per-request (namespace, op) is simply absent from the priced
    table — there is no synthetic fallback price. Absence is the refuse signal
    the spend meter relies on (unpriced => refused before egress)."""
    assert (_UNKNOWN_PROVIDER, "search") not in _OFFICIAL_DOCS_PRICING


# ─────────────────────────────────────────────────────────────────────────────
# Seam 2: web provider billing classification fails closed to "paid"
# ─────────────────────────────────────────────────────────────────────────────


def test_provider_billing_unknown_provider_is_not_free():
    """An unknown web provider classifies as ("unknown", None) — crucially NOT
    "free". The egress point only runs un-metered when billing_mode == "free";
    "unknown" therefore routes through the spend meter (fail closed). A newly
    added paid backend forgotten in the billing map can never leak ungated spend.
    """
    mode, namespace = provider_billing(_UNKNOWN_PROVIDER)
    assert mode == "unknown"
    assert mode != "free"
    assert namespace is None


def test_provider_billing_handles_non_string_fail_closed():
    """Defensive: a non-string provider name also classifies unknown (never free),
    so a malformed/attacker-shaped value can't bypass the gate."""
    mode, namespace = provider_billing(None)  # type: ignore[arg-type]
    assert mode == "unknown"
    assert namespace is None


def test_known_paid_providers_are_not_classified_free():
    """Every provider the billing map marks paid stays out of the un-metered
    'free' branch. Relationship assertion over the real map (not a name snapshot):
    a 'paid' entry must never be 'free' and must carry a pricing namespace."""
    paid = {n for n, (mode, _) in _PROVIDER_BILLING.items() if mode == "paid"}
    assert paid, "expected at least one paid web provider in the billing map"
    for name in paid:
        mode, namespace = provider_billing(name)
        assert mode == "paid"
        assert mode != "free"
        assert namespace, f"paid provider {name!r} must carry a pricing namespace"


# ─────────────────────────────────────────────────────────────────────────────
# Seam 3: spend meter — a spendful path with NO reservation envelope fails closed
# ─────────────────────────────────────────────────────────────────────────────


@pytest.fixture
def _no_meter_business_session(monkeypatch):
    """Simulate a business session (meter REQUIRED) with NO meter registered.

    The presence of TAKYON_SESSION_BUSINESS_SLUG is the trusted signal that money
    must be metered (web_spend_meter._meter_required). We clear the registered
    meter and assert the reservation refuses. Restores the prior meter after.
    """
    prior = web_spend_meter.get_spend_meter()
    web_spend_meter.register_spend_meter(None)
    monkeypatch.setenv("TAKYON_SESSION_BUSINESS_SLUG", "evil-co")
    try:
        yield
    finally:
        web_spend_meter.register_spend_meter(prior)


def test_reserve_paid_call_fails_closed_without_meter_in_business_scope(
    _no_meter_business_session,
):
    """A paid call inside a business scope with no spend meter registered raises
    SpendBlocked BEFORE any provider egress — a spendful path with no reservation
    envelope fails the check. This is the core of invariant 6's fail-closed default.
    """
    with pytest.raises(SpendBlocked):
        reserve_paid_call(
            pricing_key=("tavily", "search"),
            provider="tavily",
            op="web_search",
            units=1,
            purpose="authoritative_inv6_probe",
        )


def test_reserve_paid_call_no_billing_context_returns_none(monkeypatch):
    """Outside any business scope (no slug) and with no meter, reserve returns None
    (allow) — the non-Takyon / global-scope unmetered design path. This documents
    that the fail-closed refusal is *scoped to business money*, so the SpendBlocked
    above is a real gate, not an unconditional raise."""
    prior = web_spend_meter.get_spend_meter()
    web_spend_meter.register_spend_meter(None)
    monkeypatch.delenv("TAKYON_SESSION_BUSINESS_SLUG", raising=False)
    try:
        handle = reserve_paid_call(
            pricing_key=("tavily", "search"),
            provider="tavily",
            op="web_search",
            units=1,
            purpose="authoritative_inv6_probe",
        )
        assert handle is None
    finally:
        web_spend_meter.register_spend_meter(prior)


# ─────────────────────────────────────────────────────────────────────────────
# Source-structure assertion: the egress point gates on "free", so unknown==paid
# ─────────────────────────────────────────────────────────────────────────────


def _web_tools_source() -> str:
    import tools.web_tools as web_tools

    return Path(inspect.getfile(web_tools)).read_text(encoding="utf-8")


def test_egress_gates_on_free_so_unknown_takes_reserve_path():
    """At the egress point in tools/web_tools.py the ONLY un-metered branch is
    `billing_mode == "free"`. Everything else (paid AND unknown) falls into the
    reserve_paid_call envelope. This proves an unknown/forgotten provider can't
    bypass the money gate.

    Structural assertion (the runtime path is exercised end-to-end elsewhere):
    confirm the gate compares against "free" and that reserve_paid_call /
    SpendBlocked are wired into the same module.
    """
    src = _web_tools_source()
    assert 'billing_mode == "free"' in src
    assert "reserve_paid_call" in src
    assert "SpendBlocked" in src
    # The fail-closed default must NOT be the inverse (gate on "paid" / "unknown"
    # while letting everything else through) — that would let a forgotten paid
    # backend run un-metered.
    assert 'billing_mode == "paid"' not in src
    assert 'billing_mode == "unknown"' not in src


def test_provider_billing_is_server_owned_in_web_tools():
    """web_tools resolves the provider server-side then asks provider_billing —
    the classification is never taken from a tool result or caller input. Confirm
    the server-owned classifier is the symbol imported and used."""
    src = _web_tools_source()
    assert "from agent.web_search_registry import" in src
    assert "provider_billing" in src
