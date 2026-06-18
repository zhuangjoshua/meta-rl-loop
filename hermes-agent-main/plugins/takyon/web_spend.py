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
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
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


def _microusd_to_cents_ceiling(microusd: int) -> int:
    """Convert a microUSD magnitude to whole CENTS, rounding UP. The operator billing rail
    (``billing.py``) is denominated in cents; web spend is priced in microUSD. The HOLD must never
    under-charge the authority, so the estimate is rounded toward +infinity — a sub-cent web cost
    still reserves at least 1 cent, so a flood of sub-cent calls cannot stay forever free against
    the cumulative ceiling. Settles re-clamp to the held cents (never over-charge the reservation)."""
    return int((Decimal(int(microusd)) / Decimal(10_000)).quantize(Decimal("1"), rounding=ROUND_CEILING))


class _Reservation:
    """Opaque handle threaded back from reserve() to settle()/release().

    Carries BOTH money rails so settle/release can finalize each consistently:
    - ``business_slug`` / ``reservation_key`` finalize the per-business ``app_usage`` audit row.
    - ``owner_user_id`` / ``billing_reserved_cents`` finalize the operator billing hold
      (``billing.py``) — the authority gate that actually decrements the operator ceiling.
      ``owner_user_id`` is empty / ``billing_reserved_cents`` is 0 when no billing hold was taken
      (a zero-cost call, or — defensively — a missing billing account), so settle/release skip it."""

    __slots__ = (
        "business_slug",
        "reservation_key",
        "pricing_key",
        "provider",
        "model",
        "reserved_microusd",
        "owner_user_id",
        "billing_reserved_cents",
    )

    def __init__(
        self,
        business_slug,
        reservation_key,
        pricing_key,
        provider,
        model,
        reserved_microusd,
        owner_user_id="",
        billing_reserved_cents=0,
    ):
        self.business_slug = business_slug
        self.reservation_key = reservation_key
        self.pricing_key = pricing_key
        self.provider = provider
        self.model = model
        self.reserved_microusd = reserved_microusd
        self.owner_user_id = owner_user_id
        self.billing_reserved_cents = billing_reserved_cents


def _resolve_owner_user_id(raw, business: str) -> str:
    """Resolve the business OWNER's Takyon-user id — the identity whose OPERATOR billing authority
    (``billing.py``, Takyon-user → platform rail) bounds this CEO/agent web egress.

    CEO/agent web egress is *operator* spend: it carries NO ``app_user_id`` and no product
    subscription, so it must be bounded by the operator's OWN billing authority — never by a
    product-subuser entitlement and never by the invariant-9-removed per-business pool. Returns the
    owner uuid as a string, or empty when the business has no owner row (which fails the call
    closed at the reserve, the correct posture for an unknown operator)."""
    row = raw.execute(
        "select owner_user_id from businesses where slug = %s", (business,)
    ).fetchone()
    return str((row[0] if row else "") or "").strip()


class BusinessBudgetSpendMeter:
    """Reserve/settle/release a paid web call against the active business AI budget."""

    def reserve(self, *, pricing_key, provider, op, units, usage, purpose) -> Optional[_Reservation]:
        # Lazy import: core is heavy and imports plenty; importing it at module load would risk a
        # cycle. _session_business_slug reads the TRUSTED session scope (not a tool arg).
        from . import billing
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
                # CUMULATIVE OPERATOR-AUTHORITY GATE — the hole the red-team proved. invariant 9
                # removed the per-business pool cap (the budget row opens with a NULL cap), and this
                # CEO/agent web egress carries NO ``app_user_id`` / product subscription, so the
                # per-subuser gate inside ``reserve_usage`` cannot apply either. The old fix only
                # READ the operator ceiling and compared this single call against it — but it never
                # DEBITED that ceiling, so the held estimate never decremented the authority and N
                # sequential reserves each saw the full balance (120%+ of authority could be held at
                # once). The fix: take a REAL hold on the operator billing rail (``billing.py``,
                # Takyon-user → platform), denominated in cents. ``billing.reserve`` locks the single
                # billing_accounts row FOR UPDATE, draws allowance-first-then-topup, and raises
                # InsufficientBalance when the two buckets can no longer cover the estimate — so a
                # second reserve sees the DECREMENTED remaining and fails closed. THIS is the money
                # gate; the ``app_usage`` reserve below stays as the per-business audit row and the
                # carrier of any explicit operator pool cap. Both holds share the SAME reservation
                # key for idempotency, and both finalize together in settle/release.
                owner_user_id = _resolve_owner_user_id(raw, business)
                if not owner_user_id:
                    raise web_spend_meter.SpendBlocked(
                        f"no operator owner resolvable for business {business!r}; "
                        f"refusing ungated {op} via {provider}"
                    )
                billing_reserved_cents = 0
                if cost > 0:
                    estimate_cents = _microusd_to_cents_ceiling(cost)
                    try:
                        # idempotency_key == reservation_key: a replay of the SAME key returns the
                        # same split without holding twice (billing.reserve is idempotent on it).
                        resv = billing.reserve(
                            raw,
                            owner_user_id,
                            estimate_cents,
                            key,
                            business_slug=business,
                            job_id=f"web_egress:{op}",
                        )
                    except billing.NoBillingAccount:
                        # Every real operator is funded by the starter allowance or a topup; no
                        # account means "no money authority", which must fail closed (not "free").
                        raise web_spend_meter.SpendBlocked(
                            f"operator {owner_user_id} has no billing account; "
                            f"refusing ungated {op} via {provider}"
                        )
                    except billing.InsufficientBalance as exc:
                        # The cumulative ceiling — outstanding holds + settled spend already consume
                        # the authority, so this call cannot be covered. THIS is what stops the loop.
                        raise web_spend_meter.SpendBlocked(
                            f"operator budget authority exhausted for business {business!r}: "
                            f"{op} via {provider} needs {estimate_cents} cents, operator has "
                            f"allowance {exc.allowance_available_cents} + topup "
                            f"{exc.topup_available_cents} cents"
                        )
                    billing_reserved_cents = int(resv.total_cents)
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
                    # The per-business pool cap (if an operator set one) refused — release the
                    # billing hold we just took so it does not leak, then fail closed.
                    if billing_reserved_cents:
                        billing.refund(raw, key)
                    raise web_spend_meter.SpendBlocked(
                        f"business {business!r} is out of AI budget; {op} blocked"
                    )
                except usage_leaf.AppBudgetInactive:
                    if billing_reserved_cents:
                        billing.refund(raw, key)
                    raise web_spend_meter.SpendBlocked(
                        f"business {business!r} AI budget is inactive; {op} blocked"
                    )
        return _Reservation(
            business, key, pricing_key, str(provider), model, cost,
            owner_user_id=owner_user_id, billing_reserved_cents=billing_reserved_cents,
        )

    def settle(self, handle: Optional[_Reservation], *, units, usage) -> None:
        if handle is None:
            return
        cost = _price_microusd(handle.pricing_key, units=units, usage=usage)
        if not cost:  # actual usage unavailable / zero -> reserved estimate (never undercharge)
            cost = handle.reserved_microusd
        from . import billing
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
                # Finalize the operator billing hold: held → spent. ``billing.settle`` asserts
                # actual ≤ reserved (it is custody of real money), so clamp the actual cents to the
                # held cents — the held estimate was rounded UP, so the true cost can only be ≤ it
                # for request-priced calls; for the token-priced summarizer a real overage stays
                # capped at the reservation (the conservative over-hold is reconcilable, an
                # over-charge of the reservation is not). Idempotent: a replayed settle is a no-op.
                if handle.owner_user_id and handle.billing_reserved_cents:
                    actual_cents = min(
                        _microusd_to_cents_ceiling(int(cost)), int(handle.billing_reserved_cents)
                    )
                    billing.settle(raw, handle.reservation_key, actual_cents)

    def release(self, handle: Optional[_Reservation], *, error) -> None:
        if handle is None:
            return
        from . import billing
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
                # Return the whole operator billing hold to the authority (no spend recorded).
                # Idempotent: a replayed/already-finalized refund is a no-op.
                if handle.owner_user_id and handle.billing_reserved_cents:
                    billing.refund(raw, handle.reservation_key)


def register() -> None:
    """Install the business-budget meter as THE spend implementation for paid web egress."""
    web_spend_meter.register_spend_meter(BusinessBudgetSpendMeter())
