"""Product plan catalog + sub-user entitlements — Phase 5 (increment b) of mediationplan.md.

Builds on `app_identity` (the sub-user spine). Two concerns, both scoped by `business_slug`:

  * the per-business PLAN CATALOG (`app_plan_policies`) — what a product sells: price, tier,
    included AI budget/action quota, Stripe product/price linkage. This remains operator/product
    metadata first, but runtime surfaces such as the AI gateway may also consume selected plan
    fields (for example included AI budget or model/feature metadata) as hard gates.

  * per-sub-user ENTITLEMENTS (`app_entitlements`) — append-a-row grants of access. A sub-user's
    EFFECTIVE tier is resolved across their grants whose status is active/trialing (highest rank
    wins) and cached onto `app_users.tier`, mirroring the SQLite trunk's `_sync_user_tier`
    (core.py:3545). Granting any access-bearing tier without Stripe evidence is REJECTED — that
    is the money-truth guard ported verbatim from core.py:5314 (a manual paid grant would fake
    billing state).

Postgres port of the SQLite trunk's app_plan_policies / app_entitlements (core.py:3036-3140);
the SQLite product path is the predecessor, retired in Phase 8. The dead `stripe_payment_link_*`
columns are dropped (written, never read). `included_action_quota` remains as plan metadata; the
old subsidy/overage plan switch is removed because plan-funded monthly budget is now authoritative.

House style (matches billing.py / custody.py / policy.py / app_identity.py): pure leaf, takes a
psycopg connection, imports no psycopg, opens its own `conn.transaction()` per mutating op, and
raises typed errors on broken preconditions. An unknown business fails loud through the FK to
businesses(slug). The email→sub-user resolution reuses `app_identity.upsert_app_user`.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass

from plugins.takyon import app_identity, app_profiles

# tier → rank for resolving the effective tier; LOWER wins.
_TIER_RANK = {"owner": 0, "paid": 1, "pro": 1}
_DEFAULT_TIER_RANK = 5
_UNENTITLING_TIERS = {"", "free", "none", app_identity.UNENTITLED_TIER}

_VALID_BILLING_INTERVALS = {"month", "year", "one_time"}
_BILLING_INTERVAL_ALIASES = {
    "monthly": "month",
    "mo": "month",
    "per_month": "month",
    "annual": "year",
    "annually": "year",
    "yearly": "year",
    "yr": "year",
    "per_year": "year",
    "once": "one_time",
    "one-time": "one_time",
    "single": "one_time",
}

# Statuses that actually confer a tier; everything else (cancelled, past_due, …) does not.
_ACTIVE_STATUSES = ("active", "trialing")


def _monthly_plan_price_cap_microusd(price_cents: int) -> int:
    return max(0, int(price_cents) * 10_000)


class EntitlementError(Exception):
    """Base for plan/entitlement errors."""


class InvalidPlan(EntitlementError):
    """A plan field is malformed (bad interval, negative amount, …)."""


class AppUserNotFound(EntitlementError):
    """The referenced sub-user does not exist in this business."""


class FakeBillingRejected(EntitlementError):
    """A grant with no Stripe evidence would fake billing state."""


class InvalidEntitlementTier(EntitlementError):
    """The requested tier is unsupported for this product shape."""


@dataclass(frozen=True)
class PlanPolicy:
    """One row of a business's plan catalog (unique per (business_slug, plan_key))."""

    id: str
    business_slug: str
    plan_key: str
    tier: str
    price_cents: int
    currency: str
    billing_interval: str
    included_ai_budget_microusd: int
    included_action_quota: int
    stripe_product_id: str | None
    stripe_price_id: str | None
    source: str
    notes: str
    metadata: dict


@dataclass(frozen=True)
class Entitlement:
    """One grant of access to a sub-user. The sub-user's effective tier is resolved across all
    of their grants — this single row is not authoritative on its own."""

    id: str
    business_slug: str
    app_user_id: str
    tier: str
    status: str
    source: str
    stripe_customer_id: str | None
    stripe_subscription_id: str | None
    stripe_checkout_session_id: str | None
    plan_key: str | None
    current_period_end: object
    metadata: dict


_PLAN_COLUMNS = (
    "id, business_slug, plan_key, tier, price_cents, currency, billing_interval, "
    "included_ai_budget_microusd, included_action_quota, stripe_product_id, "
    "stripe_price_id, source, notes, metadata"
)
_ENT_COLUMNS = (
    "id, business_slug, app_user_id, tier, status, source, "
    "stripe_customer_id, stripe_subscription_id, stripe_checkout_session_id, "
    "plan_key, current_period_end, metadata"
)


def _json_dumps(value) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _normalize_plan_key(value: str) -> str:
    """Slugify a plan key the same way the SQLite trunk's `_file_slug` does."""
    raw = str(value or "").strip().lower()
    raw = re.sub(r"[^a-z0-9._-]+", "-", raw).strip("-_.")
    return (raw or "plan")[:96]


def _normalize_billing_interval(value: str) -> str:
    raw = str(value or "month").strip().lower().replace("-", "_")
    return _BILLING_INTERVAL_ALIASES.get(raw, raw)


def _contains_unlimited(value) -> bool:
    if isinstance(value, str):
        return "unlimited" in value.lower()
    if isinstance(value, bool):
        return False
    if isinstance(value, (int, float)):
        return value < 0
    if isinstance(value, dict):
        return any(_contains_unlimited(item) for item in value.values())
    if isinstance(value, list):
        return any(_contains_unlimited(item) for item in value)
    return False


def plan_validation_warnings(plan_key: str, tier: str, quota: int, metadata: dict) -> list[str]:
    """Operator-facing advisory warnings about a plan's coherence. Pure (no DB). Ported from
    core.py:1978 so the upsert can fold them into stored metadata, exactly as the SQLite path
    did — they are advisory only and gate nothing."""
    warnings: list[str] = []
    normalized_key = _normalize_plan_key(plan_key)
    normalized_tier = _normalize_plan_key(tier)
    if (
        normalized_tier
        and normalized_key
        and normalized_tier not in normalized_key
        and normalized_key not in {"plan"}
    ):
        warnings.append(
            "plan_key and entitlement tier differ; this can be valid for billing variants "
            "but should be intentional"
        )
    if _contains_unlimited(metadata) and quota > 0:
        warnings.append(
            "metadata suggests an unlimited entitlement but included_action_quota is finite"
        )
    return warnings


def _plan_from_row(row) -> PlanPolicy:
    return PlanPolicy(
        id=str(row[0]),
        business_slug=str(row[1]),
        plan_key=str(row[2]),
        tier=str(row[3]),
        price_cents=int(row[4]),
        currency=str(row[5]),
        billing_interval=str(row[6]),
        included_ai_budget_microusd=int(row[7]),
        included_action_quota=int(row[8]),
        stripe_product_id=None if row[9] is None else str(row[9]),
        stripe_price_id=None if row[10] is None else str(row[10]),
        source=str(row[11]),
        notes=str(row[12]),
        metadata=row[13] if isinstance(row[13], dict) else {},
    )


def _ent_from_row(row) -> Entitlement:
    return Entitlement(
        id=str(row[0]),
        business_slug=str(row[1]),
        app_user_id=str(row[2]),
        tier=str(row[3]),
        status=str(row[4]),
        source=str(row[5]),
        stripe_customer_id=None if row[6] is None else str(row[6]),
        stripe_subscription_id=None if row[7] is None else str(row[7]),
        stripe_checkout_session_id=None if row[8] is None else str(row[8]),
        plan_key=None if row[9] is None else str(row[9]),
        current_period_end=row[10],
        metadata=row[11] if isinstance(row[11], dict) else {},
    )


# ── plan catalog ─────────────────────────────────────────────────────────────────


def upsert_plan_policy(
    conn,
    business_slug: str,
    plan_key: str,
    *,
    tier: str | None = None,
    price_cents: int = 0,
    currency: str = "usd",
    billing_interval: str = "month",
    included_ai_budget_microusd: int | None = None,
    included_action_quota: int = 0,
    stripe_product_id: str | None = None,
    stripe_price_id: str | None = None,
    source: str = "takyon",
    notes: str = "",
    metadata: dict | None = None,
) -> PlanPolicy:
    """Create or update a plan in the business's catalog, idempotent on (business_slug, plan_key).
    Every field overwrites on conflict EXCEPT `stripe_product_id`/`stripe_price_id`, which are
    COALESCE-preserved (a re-upsert that omits them keeps the prior linkage) — matching the SQLite
    upsert (core.py:5207). Unknown business → ForeignKeyViolation (fail loud)."""
    key = _normalize_plan_key(plan_key)
    tier_value = str(tier or key or "paid").strip()
    if tier_value.lower() in _UNENTITLING_TIERS:
        raise InvalidPlan("free plan tiers are unsupported; unpaid users must have no entitlement")
    price = int(float(price_cents or 0))
    if price < 0:
        raise InvalidPlan("plan price must be non-negative")
    interval = _normalize_billing_interval(billing_interval)
    if interval not in _VALID_BILLING_INTERVALS:
        raise InvalidPlan("billing_interval must be one of: month, year, one_time")
    existing = None
    if included_ai_budget_microusd in {None, ""}:
        existing = get_plan_policy(conn, business_slug, key)
    budget_source = (
        existing.included_ai_budget_microusd
        if existing is not None and included_ai_budget_microusd in {None, ""}
        else included_ai_budget_microusd
    )
    budget = int(float(budget_source or 0))
    if budget < 0:
        raise InvalidPlan("included_ai_budget_microusd must be non-negative")
    if interval == "month":
        cap = _monthly_plan_price_cap_microusd(price)
        if budget > cap:
            raise InvalidPlan("included_ai_budget_microusd must be between 0 and the monthly plan price")
    quota = int(included_action_quota if included_action_quota is not None else 0)
    if quota < 0:
        raise InvalidPlan("included_action_quota must be non-negative")
    meta = dict(metadata or {})
    warnings = plan_validation_warnings(key, tier_value, quota, meta)
    if warnings:
        prior = meta.get("takyon_plan_validation")
        prior = prior if isinstance(prior, dict) else {}
        prior_warnings = prior.get("warnings")
        prior_warnings = prior_warnings if isinstance(prior_warnings, list) else []
        meta = {
            **meta,
            "takyon_plan_validation": {
                **prior,
                "status": "warning",
                "warnings": [*prior_warnings, *warnings],
            },
        }
    with conn.transaction():
        row = conn.execute(
            "insert into app_plan_policies "
            "(business_slug, plan_key, tier, price_cents, currency, billing_interval, "
            " included_ai_budget_microusd, included_action_quota, stripe_product_id, "
            " stripe_price_id, source, notes, metadata) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            "on conflict (business_slug, plan_key) do update set "
            " tier = excluded.tier, "
            " price_cents = excluded.price_cents, "
            " currency = excluded.currency, "
            " billing_interval = excluded.billing_interval, "
            " included_ai_budget_microusd = excluded.included_ai_budget_microusd, "
            " included_action_quota = excluded.included_action_quota, "
            " stripe_product_id = coalesce(excluded.stripe_product_id, app_plan_policies.stripe_product_id), "
            " stripe_price_id = coalesce(excluded.stripe_price_id, app_plan_policies.stripe_price_id), "
            " source = excluded.source, "
            " notes = excluded.notes, "
            " metadata = excluded.metadata, "
            " updated_at = now() "
            f"returning {_PLAN_COLUMNS}",
            (
                business_slug,
                key,
                tier_value,
                price,
                str(currency or "usd").lower(),
                interval,
                budget,
                quota,
                stripe_product_id,
                stripe_price_id,
                str(source or "takyon"),
                str(notes or ""),
                _json_dumps(meta),
            ),
        ).fetchone()
    return _plan_from_row(row)


def get_plan_policy(conn, business_slug: str, plan_key: str) -> PlanPolicy | None:
    """One plan by (business, plan_key), or None. Pure read."""
    row = conn.execute(
        f"select {_PLAN_COLUMNS} from app_plan_policies "
        "where business_slug = %s and plan_key = %s",
        (business_slug, _normalize_plan_key(plan_key)),
    ).fetchone()
    return None if row is None else _plan_from_row(row)


def list_plan_policies(conn, business_slug: str) -> list[PlanPolicy]:
    """A business's whole catalog, cheapest first. Pure read."""
    rows = conn.execute(
        f"select {_PLAN_COLUMNS} from app_plan_policies "
        "where business_slug = %s order by price_cents asc, plan_key asc",
        (business_slug,),
    ).fetchall()
    return [_plan_from_row(r) for r in rows]


# ── entitlements ─────────────────────────────────────────────────────────────────


def _resolve_app_user_id(
    conn, business_slug: str, *, app_user_id: str | None, email: str | None, name: str | None
) -> str:
    if app_user_id:
        exists = conn.execute(
            "select 1 from app_users where business_slug = %s and id = %s",
            (business_slug, app_user_id),
        ).fetchone()
        if exists is None:
            raise AppUserNotFound(str(app_user_id))
        return str(app_user_id)
    if email:
        # Reuses the identity leaf; an unknown business fails loud here through its FK.
        return app_identity.upsert_app_user(conn, business_slug, email, name=name).id
    raise ValueError("grant_entitlement requires app_user_id or email")


def _sync_user_tier(conn, business_slug: str, app_user_id: str) -> str:
    """Resolve the effective tier from active/trialing grants (highest rank) and cache it onto
    app_users.tier. Mirrors the SQLite `_sync_user_tier`. Caller already holds a transaction."""
    status_placeholders = ", ".join(["%s"] * len(_ACTIVE_STATUSES))
    tier_placeholders = ", ".join(["%s"] * len(_UNENTITLING_TIERS))
    rank_case = (
        "case lower(tier) when 'owner' then 0 when 'paid' then 1 when 'pro' then 1 "
        f"else {_DEFAULT_TIER_RANK} end"
    )
    row = conn.execute(
        "select tier from app_entitlements "
        f"where business_slug = %s and app_user_id = %s and status in ({status_placeholders}) "
        f"  and lower(tier) not in ({tier_placeholders}) "
        f"order by {rank_case} asc, updated_at desc limit 1",
        (business_slug, app_user_id, *_ACTIVE_STATUSES, *_UNENTITLING_TIERS),
    ).fetchone()
    tier = str(row[0]) if row is not None else app_identity.UNENTITLED_TIER
    conn.execute(
        "update app_users set tier = %s, updated_at = now() "
        "where business_slug = %s and id = %s",
        (tier, business_slug, app_user_id),
    )
    return tier


def resolve_user_tier(conn, business_slug: str, app_user_id: str) -> str:
    """Recompute and persist a sub-user's effective tier from their entitlements. Idempotent;
    safe to call after any out-of-band status change (e.g. a subscription lapse)."""
    with conn.transaction():
        return _sync_user_tier(conn, business_slug, app_user_id)


def grant_entitlement(
    conn,
    business_slug: str,
    *,
    app_user_id: str | None = None,
    email: str | None = None,
    name: str | None = None,
    tier: str = "",
    status: str = "active",
    source: str = "manual",
    stripe_customer_id: str | None = None,
    stripe_subscription_id: str | None = None,
    stripe_checkout_session_id: str | None = None,
    plan_key: str | None = None,
    current_period_end: object = None,
    metadata: dict | None = None,
) -> tuple[Entitlement, str]:
    """Append an entitlement grant for a sub-user (by id or email) and return
    (Entitlement, effective_tier). Atomic: the grant insert and the app_users.tier resync commit
    together. Any access-bearing grant without payment proof is rejected (FakeBillingRejected).
    Unpaid access is represented by NO entitlement row, not a `free` tier. Supply `email` to
    auto-provision the sub-user; an unknown business fails loud."""
    tier_value = str(tier or "").strip()
    tier_lower = tier_value.lower()
    status_value = str(status or "active")
    source_value = str(source or "manual")
    meta = dict(metadata or {})
    if not tier_value:
        raise InvalidEntitlementTier("tier is required")
    if tier_lower in _UNENTITLING_TIERS:
        raise InvalidEntitlementTier(
            "free entitlements are unsupported; unpaid users must have no entitlement"
        )
    has_stripe_evidence = bool(
        stripe_customer_id or stripe_subscription_id or stripe_checkout_session_id
    )
    if not has_stripe_evidence:
        raise FakeBillingRejected(
            "entitlement would fake billing state; use Stripe/webhook evidence"
        )
    with conn.transaction():
        resolved_id = _resolve_app_user_id(
            conn, business_slug, app_user_id=app_user_id, email=email, name=name
        )
        app_profiles.ensure_profile(
            conn,
            business_slug,
            app_user_id=resolved_id,
            display_name=name,
        )
        row = conn.execute(
            "insert into app_entitlements "
            "(business_slug, app_user_id, tier, status, source, stripe_customer_id, "
            " stripe_subscription_id, stripe_checkout_session_id, plan_key, "
            " current_period_end, metadata) "
            "values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s::jsonb) "
            f"returning {_ENT_COLUMNS}",
            (
                business_slug,
                resolved_id,
                tier_value,
                status_value,
                source_value,
                stripe_customer_id,
                stripe_subscription_id,
                stripe_checkout_session_id,
                plan_key,
                current_period_end,
                _json_dumps(meta),
            ),
        ).fetchone()
        effective = _sync_user_tier(conn, business_slug, resolved_id)
    return _ent_from_row(row), effective


def list_entitlements(
    conn, business_slug: str, *, app_user_id: str | None = None
) -> list[Entitlement]:
    """Entitlement grants for a business, newest first; scoped to one sub-user if given. Read."""
    if app_user_id is not None:
        rows = conn.execute(
            f"select {_ENT_COLUMNS} from app_entitlements "
            "where business_slug = %s and app_user_id = %s order by updated_at desc",
            (business_slug, app_user_id),
        ).fetchall()
    else:
        rows = conn.execute(
            f"select {_ENT_COLUMNS} from app_entitlements "
            "where business_slug = %s order by updated_at desc",
            (business_slug,),
        ).fetchall()
    return [_ent_from_row(r) for r in rows]


def get_active_entitlement(conn, business_slug: str, app_user_id: str) -> Entitlement | None:
    """The entitlement currently conferring access to this sub-user, or None.

    Mirrors the effective-tier resolution order: only active/trialing grants count, lower tier
    rank wins, and the newest row breaks ties. This gives runtime callers the exact grant whose
    plan_key/metadata should be treated as authoritative right now."""
    status_placeholders = ", ".join(["%s"] * len(_ACTIVE_STATUSES))
    tier_placeholders = ", ".join(["%s"] * len(_UNENTITLING_TIERS))
    rank_case = (
        "case lower(tier) when 'owner' then 0 when 'paid' then 1 when 'pro' then 1 "
        f"else {_DEFAULT_TIER_RANK} end"
    )
    row = conn.execute(
        f"select {_ENT_COLUMNS} from app_entitlements "
        f"where business_slug = %s and app_user_id = %s and status in ({status_placeholders}) "
        f"  and lower(tier) not in ({tier_placeholders}) "
        f"order by {rank_case} asc, updated_at desc limit 1",
        (business_slug, app_user_id, *_ACTIVE_STATUSES, *_UNENTITLING_TIERS),
    ).fetchone()
    return None if row is None else _ent_from_row(row)


def set_subscription_status(
    conn,
    stripe_subscription_id: str,
    *,
    status: str,
    stripe_customer_id: str | None = None,
    current_period_end: object = None,
    metadata: dict | None = None,
) -> list[dict]:
    """Apply a subscription-lifecycle status to every stripe-sourced entitlement carrying this
    subscription id, then resync each affected sub-user's effective tier. Mirrors the SQLite
    `_process_subscription_event` (core.py:6929): a lapse/cancel flips status so the grant no
    longer confers a tier (only active/trialing do — see `_ACTIVE_STATUSES`).

    `status` is the already-mapped ENTITLEMENT status (active/cancelled/past_due); the
    Stripe-status interpretation lives in `app_payments`, keeping this leaf free of Stripe
    vocabulary. COALESCE preserves the existing customer id / period end when not supplied, and
    the `metadata` patch is merged onto the row's jsonb (`||`). Returns one
    {business_slug, app_user_id, tier} dict per affected sub-user — empty when the subscription
    is unknown here (a webhook for a subscription this business never recorded is a no-op, not an
    error)."""
    status_value = str(status or "")
    if not status_value:
        raise ValueError("status is required")
    patch = _json_dumps(dict(metadata or {}))
    with conn.transaction():
        targets = conn.execute(
            "select distinct business_slug, app_user_id from app_entitlements "
            "where source = 'stripe' and stripe_subscription_id = %s",
            (stripe_subscription_id,),
        ).fetchall()
        updated: list[dict] = []
        for business_slug, app_user_id in targets:
            conn.execute(
                "update app_entitlements set status = %s, "
                "stripe_customer_id = coalesce(%s, stripe_customer_id), "
                "current_period_end = coalesce(%s, current_period_end), "
                "metadata = metadata || %s::jsonb, updated_at = now() "
                "where source = 'stripe' and stripe_subscription_id = %s "
                "and business_slug = %s and app_user_id = %s",
                (
                    status_value,
                    stripe_customer_id,
                    current_period_end,
                    patch,
                    stripe_subscription_id,
                    business_slug,
                    app_user_id,
                ),
            )
            tier = _sync_user_tier(conn, business_slug, app_user_id)
            updated.append(
                {
                    "business_slug": business_slug,
                    "app_user_id": str(app_user_id),
                    "tier": tier,
                }
            )
    return updated
