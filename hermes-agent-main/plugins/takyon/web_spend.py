"""Takyon business-budget implementation of the core web spend seam (agent/web_spend_meter.py).

This is the trusted side of the split: the core web tools resolve which provider actually runs and
hand this meter a server-owned `pricing_key`; the meter resolves the business scope from the trusted
session, prices the call from `agent/usage_pricing.py`, and reserves / settles / releases against the
business app_budget (`app_usage`, the same rail the product runtime meters through). Registered on
plugin load (`plugins/takyon/__init__.py`), so every paid web provider call the operator makes inside
a business is gated. Unpriced / unknown pricing and an exhausted/inactive budget FAIL CLOSED.

Nothing the user/model supplies (query, urls, depth) reaches this module — only the resolved provider
name, the `(namespace, op)` pricing key, and server-computed units/usage.
"""
from __future__ import annotations

import logging
import uuid
from decimal import ROUND_HALF_UP, Decimal
from typing import Any, Optional

from agent import web_spend_meter
from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

logger = logging.getLogger(__name__)


def _price_microusd(pricing_key, *, units, usage) -> Optional[int]:
    """Resolve the server-owned cost (microUSD) for a call. ``pricing_key`` is ``(provider, model)``
    for request-priced web providers, or ``(provider, model, base_url)`` for the token-priced
    summarizer (whose route needs provider/base_url to resolve, e.g. openrouter vs anthropic).
    Request-priced calls price off ``units`` (request_count); token-priced calls off a
    CanonicalUsage. Returns ``None`` when the route is unpriced — the caller must fail closed."""
    provider = pricing_key[0]
    model = pricing_key[1]
    base_url = pricing_key[2] if len(pricing_key) > 2 else None
    canonical = usage if usage is not None else CanonicalUsage(request_count=int(units or 0))
    result = estimate_usage_cost(
        str(model),
        canonical,
        provider=(str(provider) if provider else None),
        base_url=(str(base_url) if base_url else None),
    )
    if result.amount_usd is None:
        return None
    return int((Decimal(result.amount_usd) * 1_000_000).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


class _Reservation:
    """Opaque handle threaded back from reserve() to settle()/release()."""

    __slots__ = ("business_slug", "reservation_key", "pricing_key", "provider", "model", "reserved_microusd")

    def __init__(self, business_slug, reservation_key, pricing_key, provider, model, reserved_microusd):
        self.business_slug = business_slug
        self.reservation_key = reservation_key
        self.pricing_key = pricing_key
        self.provider = provider
        self.model = model
        self.reserved_microusd = reserved_microusd


def _operator_remaining_microusd(raw, business: str) -> int:
    """Resolve the business OWNER's remaining operator-rail spend authority (microUSD) — the
    NON-NULL ceiling this CEO/agent web-egress meter gates on.

    This is the OPERATOR money rail (``billing.py``, Takyon-user → platform), NOT the product
    subuser subscription rail: CEO/agent web egress is *operator* spend (it carries no
    ``app_user_id`` and no product subscription), so it must be bounded by the operator's OWN
    billing authority — never by a product-subuser entitlement and never by the
    invariant-9-removed per-business pool. The authority is allowance remaining + topup balance
    (the same two buckets ``billing.reserve`` draws from), converted cents → microUSD (×10_000).

    ALWAYS returns a concrete, non-null ceiling, NEVER unbounded: an unresolvable owner or a
    business with no billing account yields 0, which fails the call closed — the correct posture
    for an unfunded/unknown operator (every real operator is funded by the starter allowance or a
    topup, so a 0 here means "no money authority", not "free")."""
    from . import billing

    row = raw.execute(
        "select owner_user_id from businesses where slug = %s", (business,)
    ).fetchone()
    owner_user_id = str((row[0] if row else "") or "").strip()
    if not owner_user_id:
        return 0
    try:
        balances = billing.get_billing_balances(raw, owner_user_id)
    except billing.NoBillingAccount:
        return 0
    remaining_cents = max(0, int(balances.allowance_remaining_cents)) + max(
        0, int(balances.topup_balance_cents)
    )
    return remaining_cents * 10_000


class BusinessBudgetSpendMeter:
    """Reserve/settle/release a paid web call against the active business AI budget."""

    def reserve(self, *, pricing_key, provider, op, units, usage, purpose) -> Optional[_Reservation]:
        # Lazy import: core is heavy and imports plenty; importing it at module load would risk a
        # cycle. _session_business_slug reads the TRUSTED session scope (not a tool arg).
        from .core import _PGConn, _session_business_slug, _store

        business = _session_business_slug()
        if not business:
            # Global / top-level operator scope: there is no business budget to meter against, so the
            # call proceeds unmetered (the same boundary the product runtime draws — metering is a
            # per-business rail). Metering global operator spend belongs to a future user-budget rail,
            # not here. The fail-closed cases are over-budget / unpriced (below) and the
            # no-meter-in-a-business-session case (enforced by the seam's _meter_required).
            return None
        cost = _price_microusd(pricing_key, units=units, usage=usage)
        if cost is None:
            raise web_spend_meter.SpendBlocked(
                f"{op} via {provider} is unpriced in usage_pricing ({pricing_key}); refusing unpriced spend"
            )
        model = str(pricing_key[1] or op)
        key = f"agent:{op}:{uuid.uuid4().hex}"
        store = _store()
        with store._connect() as conn:
            if not isinstance(conn, _PGConn):
                return None  # SQLite dev runtime has no app_budgets; metering is a Postgres rail
            usage_leaf = store._app_leaves()["usage"]
            with store._leaf_conn(conn) as raw:
                # OPERATOR-RAIL CEILING — the gate invariant 9 left this meter without. The flat
                # per-business pool cap is gone (the budget row opens with a NULL cap), and this
                # CEO/agent web egress carries NO ``app_user_id`` / product subscription, so the
                # per-subuser gate inside ``reserve_usage`` cannot apply either. Without a ceiling
                # here the reserve would hold the estimate with NO money gate at all = unbounded
                # ungated spend. Bound it to the business owner's OWN operator billing authority
                # (``billing.py``, the Takyon-user → platform rail), a real non-null ceiling that is
                # distinct from any product-subuser subscription. Refuse BEFORE holding when the
                # cost exceeds that authority; the authoritative spend reservation still lands on
                # ``app_usage`` below (which additionally enforces any explicit operator pool cap).
                operator_ceiling = _operator_remaining_microusd(raw, business)
                if cost > operator_ceiling:
                    raise web_spend_meter.SpendBlocked(
                        f"operator budget authority exhausted for business {business!r}: "
                        f"{op} via {provider} needs {cost} microusd, operator remaining "
                        f"{operator_ceiling} microusd"
                    )
                try:
                    usage_leaf.reserve_usage(
                        raw,
                        business,
                        estimated_cost_microusd=cost,
                        reservation_key=key,
                        purpose=str(purpose),
                        route="ceo_tool",
                        provider=str(provider),
                        model=model,
                        metadata={"op": op, "pricing_key": [pricing_key[0], pricing_key[1]]},
                    )
                except usage_leaf.AppBudgetExceeded:
                    raise web_spend_meter.SpendBlocked(
                        f"business {business!r} is out of AI budget; {op} blocked"
                    )
                except usage_leaf.AppBudgetInactive:
                    raise web_spend_meter.SpendBlocked(
                        f"business {business!r} AI budget is inactive; {op} blocked"
                    )
        return _Reservation(business, key, pricing_key, str(provider), model, cost)

    def settle(self, handle: Optional[_Reservation], *, units, usage) -> None:
        if handle is None:
            return
        cost = _price_microusd(handle.pricing_key, units=units, usage=usage)
        if not cost:  # actual usage unavailable / zero -> reserved estimate (never undercharge)
            cost = handle.reserved_microusd
        from .core import _PGConn, _store

        store = _store()
        with store._connect() as conn:
            if not isinstance(conn, _PGConn):
                return
            usage_leaf = store._app_leaves()["usage"]
            with store._leaf_conn(conn) as raw:
                usage_leaf.settle_usage(
                    raw,
                    handle.business_slug,
                    handle.reservation_key,
                    actual_cost_microusd=int(cost),
                    provider=handle.provider,
                    model=handle.model,
                )

    def release(self, handle: Optional[_Reservation], *, error) -> None:
        if handle is None:
            return
        from .core import _PGConn, _store

        store = _store()
        with store._connect() as conn:
            if not isinstance(conn, _PGConn):
                return
            usage_leaf = store._app_leaves()["usage"]
            with store._leaf_conn(conn) as raw:
                usage_leaf.release_usage(
                    raw,
                    handle.business_slug,
                    handle.reservation_key,
                    error=(str(error)[:500] if error else None),
                )


def register() -> None:
    """Install the business-budget meter as THE spend implementation for paid web egress."""
    web_spend_meter.register_spend_meter(BusinessBudgetSpendMeter())
