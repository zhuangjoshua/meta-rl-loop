"""UC4 compositional-pricing engine — modularization plan §2.7, Stage 5 (the composition slice
that rides BEHIND the already-landed monthly-only slice).

A subuser plan's economics — `price_cents`, `included_ai_budget_microusd`, the
`metadata.features` / `model_allowlist` gates — must stop being freehand numbers the CEO types
and become DERIVED outputs of a set of priced components under a margin policy. "Adding a feature
is adding a `PricedComponent`" (plan §3, UC4): the composer derives the new price/budget/gates
from each component's cost basis, refuses if the derivation would violate the margin floor (with
the exact figures, never a silent clamp), and emits a receipt carrying the full component-level
derivation.

This module is the pure engine. It has ONE money dependency — `agent.usage_pricing`, the single
cost SSOT — which it READS to price a metered AI-allowance ceiling; it never becomes a second
pricing table and never hardcodes a provider $ number. It takes no DB connection and opens no
transaction; the plan-write wiring that persists a composed plan lives in `app_entitlements.py`
(`upsert_plan_from_composition`), which calls `compose_plan` here and stores the derived economics
plus the composition + receipt in the plan row's existing jsonb metadata.

Money model (matches `ai_gateway._user_weekly_budget_microusd` and `usage_pricing.billed_cost`):

  * A metered AI-allowance component carries an ALLOWANCE priced through `usage_pricing`
    (model + provider + monthly token counts). Pricing it yields the REALIZED monthly provider
    COGS ceiling for that allowance. That realized ceiling is the component's contribution to the
    margin invariant. The customer-facing GRANT the plan stores — `included_ai_budget_microusd`,
    the retail budget the gateway meters against — is that realized ceiling marked up via
    `usage_pricing.billed_cost` (so the budget is what the customer may spend, and the COGS is what
    the platform pays to serve it fully consumed). Both derive from the ONE priced ceiling.
  * A fixed external fee (Shopify per-store, App-Store presence, per-seat SaaS) contributes its
    fee µUSD/month as-is. It has no retail markup here — it is a pass-through platform cost that
    the price must cover under margin. It does not add to the AI budget grant.
  * A per_unit (quota-shaped) component contributes `unit_cost_microusd * included_units` µUSD/month
    of COGS — the priced replacement for the dead `included_action_quota`.

Margin invariant (generalizes the physical-goods `list_price ≥ landed_cost × (1+floor)` gate and
REPLACES the 100%-of-price silent clamp): with `total_cogs = Σ monthly COGS ceilings`,

    total_cogs <= price_microusd * (1 - margin_floor)

so `price_microusd >= total_cogs / (1 - margin_floor)`. `compose_plan` derives the minimum price
that satisfies it, rounds UP under the `MarginPolicy` rounding rule, and re-checks — a rounded
price only ever raises margin, never lowers it. There is no silent clamp: a `MarginPolicy` with
`margin_floor >= 1` (impossible margin) or a freehand price below the derived floor REFUSES with
the exact figures (`MarginFloorViolation`).

Monthly-only by construction: every µUSD figure is per-month, there is no interval axis, and the
composer emits nothing that could mint a non-month plan.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import ROUND_CEILING, Decimal
from typing import Literal

from agent import usage_pricing

# ── cost-basis shapes ────────────────────────────────────────────────────────────

CostBasisKind = Literal["metered", "fixed", "per_unit"]

_MICRO = 1_000_000  # µUSD per USD


class CompositionError(Exception):
    """Base for all plan-composition errors."""


class InvalidComponent(CompositionError):
    """A component or cost basis is malformed (bad kind, negative amount, missing allowance)."""


class UnpricedAllowance(CompositionError):
    """A metered allowance references a model with no entry in `usage_pricing` (the ONE cost
    SSOT). Fail closed — an unpriced model can never derive a budget, exactly as the runtime spend
    path refuses an unpriced call (CLAUDE.md money-gate rule)."""


class InvalidMarginPolicy(CompositionError):
    """The margin policy is malformed (floor outside [0,1), unknown rounding rule)."""


class MarginFloorViolation(CompositionError):
    """The composed (or freehand) price does not clear the margin floor over total COGS. Carries
    the exact figures. This is the fail-loud replacement for the old silent 100%-of-price clamp —
    there is no silent adjustment, ever."""


@dataclass(frozen=True)
class MeteredAllowance:
    """A month's worth of AI inference the plan includes, expressed in TOKENS against a specific
    model so it can be priced through `usage_pricing` (never a hardcoded µUSD). The realized
    monthly provider COGS of fully consuming this allowance is the component's COGS ceiling; the
    customer-facing budget grant is that ceiling marked up via `usage_pricing.billed_cost`."""

    model: str
    provider: str | None = None
    base_url: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    request_count: int = 0

    def __post_init__(self) -> None:
        if not str(self.model or "").strip():
            raise InvalidComponent("metered allowance requires a model")
        for name in (
            "input_tokens",
            "output_tokens",
            "cache_read_tokens",
            "cache_write_tokens",
            "request_count",
        ):
            if int(getattr(self, name)) < 0:
                raise InvalidComponent(f"metered allowance {name} must be non-negative")
        if (
            self.input_tokens
            + self.output_tokens
            + self.cache_read_tokens
            + self.cache_write_tokens
            + self.request_count
        ) <= 0:
            raise InvalidComponent(
                "metered allowance must include at least one non-zero token/request count"
            )


@dataclass(frozen=True)
class CostBasis:
    """The monthly cost shape of one component. Discriminated on `kind`:

      * ``metered``  — carries a `MeteredAllowance`; COGS ceiling is priced via `usage_pricing`.
      * ``fixed``    — carries `fee_microusd_month`; the external recurring/per-seat fee, as-is.
      * ``per_unit`` — carries `unit_cost_microusd` × `included_units`; quota-shaped COGS.
    """

    kind: CostBasisKind
    allowance: MeteredAllowance | None = None
    fee_microusd_month: int = 0
    unit_cost_microusd: int = 0
    included_units: int = 0

    def __post_init__(self) -> None:
        if self.kind == "metered":
            if self.allowance is None:
                raise InvalidComponent("metered cost basis requires an allowance")
            if self.fee_microusd_month or self.unit_cost_microusd or self.included_units:
                raise InvalidComponent(
                    "metered cost basis must not set fixed/per_unit fields"
                )
        elif self.kind == "fixed":
            if self.allowance is not None or self.unit_cost_microusd or self.included_units:
                raise InvalidComponent("fixed cost basis takes only fee_microusd_month")
            if int(self.fee_microusd_month) < 0:
                raise InvalidComponent("fixed fee_microusd_month must be non-negative")
        elif self.kind == "per_unit":
            if self.allowance is not None or self.fee_microusd_month:
                raise InvalidComponent(
                    "per_unit cost basis takes only unit_cost_microusd + included_units"
                )
            if int(self.unit_cost_microusd) < 0 or int(self.included_units) < 0:
                raise InvalidComponent(
                    "per_unit unit_cost_microusd and included_units must be non-negative"
                )
        else:
            raise InvalidComponent(f"unknown cost basis kind: {self.kind!r}")

    # constructors mirroring the plan sketch's metered()/fixed()/per_unit()
    @staticmethod
    def metered(allowance: MeteredAllowance) -> "CostBasis":
        return CostBasis(kind="metered", allowance=allowance)

    @staticmethod
    def fixed(fee_microusd_month: int) -> "CostBasis":
        return CostBasis(kind="fixed", fee_microusd_month=int(fee_microusd_month))

    @staticmethod
    def per_unit(unit_cost_microusd: int, included_units: int) -> "CostBasis":
        return CostBasis(
            kind="per_unit",
            unit_cost_microusd=int(unit_cost_microusd),
            included_units=int(included_units),
        )


@dataclass(frozen=True)
class PricedComponent:
    """One priced building block of a plan. `grants` says what it turns on: `features` (dict/list
    folded into `metadata.features`), `model_allowlist` (entries folded into the allowlist gate),
    `rail` (a runtime rail name), `credits` (creative-credit grant metadata). The composer sums
    COGS across components and unions their grants."""

    kind: str  # 'ai_allowance' | 'external_fee' | 'feature_rail' | 'credit_grant' | ...
    key: str
    cost_basis: CostBasis
    grants: dict = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not str(self.key or "").strip():
            raise InvalidComponent("component requires a key")
        if not isinstance(self.grants, dict):
            raise InvalidComponent("component grants must be a dict")


# ── margin policy ────────────────────────────────────────────────────────────────

RoundingRule = Literal["cent", "dollar", "none"]


@dataclass(frozen=True)
class MarginPolicy:
    """The margin floor (fraction of price that must remain after COGS) plus the price-point
    rounding rule. `margin_floor` is in [0, 1): 0.30 means COGS may be at most 70% of price."""

    margin_floor: float = 0.0
    rounding: RoundingRule = "cent"

    def __post_init__(self) -> None:
        floor = float(self.margin_floor)
        if not (0.0 <= floor < 1.0):
            raise InvalidMarginPolicy(
                f"margin_floor must be in [0, 1) (got {floor}); a floor >= 1 leaves no room for COGS"
            )
        if self.rounding not in ("cent", "dollar", "none"):
            raise InvalidMarginPolicy(f"unknown rounding rule: {self.rounding!r}")


@dataclass(frozen=True)
class PlanComposition:
    """A plan expressed as priced components under a margin policy. `floor_price_microusd` is an
    optional operator-chosen price the composer must MEET-OR-EXCEED (the CEO chooses a price point
    within policy; the derived margin floor still governs — the higher of the two wins)."""

    components: tuple[PricedComponent, ...]
    margin_policy: MarginPolicy = field(default_factory=MarginPolicy)
    floor_price_microusd: int = 0

    def __post_init__(self) -> None:
        if not self.components:
            raise InvalidComponent("a plan composition needs at least one component")
        if int(self.floor_price_microusd) < 0:
            raise InvalidComponent("floor_price_microusd must be non-negative")


# ── composed result ────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ComponentDerivation:
    """The priced derivation of one component — carried on the receipt so the price is auditable."""

    key: str
    kind: str
    cost_basis_kind: str
    cogs_microusd_month: int
    detail: str


@dataclass(frozen=True)
class ComposedPlan:
    """The DERIVED economics of a plan. Every number here is computed, never typed:

      * ``price_cents`` — the retail monthly price (cents) that clears the margin floor.
      * ``included_ai_budget_microusd`` — the customer-facing AI budget grant (billed µUSD/month).
      * ``features`` / ``model_allowlist`` — the union of the components' grants (the gateway gates).
      * ``total_cogs_microusd_month`` — Σ component COGS ceilings (the left side of the invariant).
      * ``margin_floor`` / ``derivations`` / ``receipt`` — the audit trail proving the derivation.
    """

    price_cents: int
    price_microusd: int
    included_ai_budget_microusd: int
    total_cogs_microusd_month: int
    margin_floor: float
    features: dict
    model_allowlist: tuple[str, ...]
    credits: dict
    rails: tuple[str, ...]
    derivations: tuple[ComponentDerivation, ...]
    receipt: dict


# ── pricing helpers (usage_pricing is the ONLY cost SSOT) ──────────────────────────


def _price_metered_ceiling_microusd(allowance: MeteredAllowance) -> int:
    """The REALIZED monthly provider COGS of fully consuming `allowance`, priced through
    `agent.usage_pricing` — the single cost SSOT. We build the canonical usage bucket the
    allowance describes and call `estimate_usage_cost`; an unpriced model (no entry in the pricing
    table) fails closed via `UnpricedAllowance`. No provider $ number is hardcoded here."""
    usage = usage_pricing.CanonicalUsage(
        input_tokens=int(allowance.input_tokens),
        output_tokens=int(allowance.output_tokens),
        cache_read_tokens=int(allowance.cache_read_tokens),
        cache_write_tokens=int(allowance.cache_write_tokens),
        request_count=int(allowance.request_count),
    )
    result = usage_pricing.estimate_usage_cost(
        allowance.model,
        usage,
        provider=allowance.provider,
        base_url=allowance.base_url,
    )
    if result.amount_usd is None:
        raise UnpricedAllowance(
            f"metered allowance model {allowance.model!r} (provider={allowance.provider!r}) has no "
            f"price in usage_pricing (status={result.status!r}); an unpriced allowance cannot "
            "derive a budget — fail closed"
        )
    # µUSD, rounded UP so the ceiling never understates realized COGS.
    micro = (Decimal(result.amount_usd) * Decimal(_MICRO)).to_integral_value(
        rounding=ROUND_CEILING
    )
    return int(micro)


def _component_cogs_microusd(component: PricedComponent) -> tuple[int, str]:
    """Return (monthly COGS µUSD, human detail) for one component, priced by its cost basis."""
    basis = component.cost_basis
    if basis.kind == "metered":
        ceiling = _price_metered_ceiling_microusd(basis.allowance)  # type: ignore[arg-type]
        return ceiling, f"metered allowance priced via usage_pricing → {ceiling} µUSD/mo COGS"
    if basis.kind == "fixed":
        fee = int(basis.fee_microusd_month)
        return fee, f"fixed external fee → {fee} µUSD/mo COGS"
    # per_unit
    cogs = int(basis.unit_cost_microusd) * int(basis.included_units)
    return (
        cogs,
        f"per_unit {basis.unit_cost_microusd} µUSD × {basis.included_units} units → {cogs} µUSD/mo COGS",
    )


def _round_price_microusd(minimum_microusd: int, rule: RoundingRule) -> int:
    """Round a minimum price UP to the next price point under `rule`. Rounding up only ever raises
    margin, so the invariant re-check after rounding can never fail because of rounding."""
    if minimum_microusd <= 0:
        return 0
    if rule == "none":
        return int(minimum_microusd)
    step = _MICRO // 100 if rule == "cent" else _MICRO  # cent or whole dollar, in µUSD
    remainder = minimum_microusd % step
    return int(minimum_microusd if remainder == 0 else minimum_microusd + (step - remainder))


# ── the engine ────────────────────────────────────────────────────────────────────


def compose_plan(composition: PlanComposition) -> ComposedPlan:
    """Derive a plan's full economics from its priced components under the margin policy.

    Everything is derived, monthly-only, µUSD/month by construction:

      1. Price each component's monthly COGS ceiling (metered via `usage_pricing`, fixed as-is,
         per_unit as unit_cost×units); sum to `total_cogs`.
      2. Union the components' grants → features, model_allowlist, credits, rails.
      3. Sum the metered components' billed budget → `included_ai_budget_microusd` (the retail AI
         budget the gateway meters; = realized ceiling marked up via `usage_pricing.billed_cost`).
      4. Derive the minimum price that clears the margin floor:
             price >= total_cogs / (1 - margin_floor),
         take the max with any operator `floor_price_microusd`, round UP under the rounding rule,
         and re-check the invariant (rounding only raises margin, so this always holds).
      5. Refuse (`MarginFloorViolation`, with figures) only if the derivation is impossible — e.g.
         COGS > 0 with margin_floor pushing the required price past representability, or a supplied
         freehand floor below the derived floor is validated separately by `assert_price_meets_margin`.

    Raises `UnpricedAllowance` if any metered model is unpriced (fail closed), `InvalidComponent`
    / `InvalidMarginPolicy` on malformed input.
    """
    policy = composition.margin_policy
    floor = Decimal(str(policy.margin_floor))

    derivations: list[ComponentDerivation] = []
    total_cogs = 0
    included_ai_budget = 0
    features: dict = {}
    model_allowlist: list[str] = []
    credits: dict = {}
    rails: list[str] = []

    for component in composition.components:
        cogs, detail = _component_cogs_microusd(component)
        total_cogs += cogs
        derivations.append(
            ComponentDerivation(
                key=component.key,
                kind=component.kind,
                cost_basis_kind=component.cost_basis.kind,
                cogs_microusd_month=cogs,
                detail=detail,
            )
        )
        # The metered components define the customer AI budget grant: realized COGS ceiling marked
        # up to the retail budget the gateway meters against (usage_pricing.billed_cost).
        if component.cost_basis.kind == "metered":
            included_ai_budget += usage_pricing.billed_cost(cogs)
        # Union the grants.
        _fold_grants(component.grants, features, model_allowlist, credits, rails)

    # Minimum price that clears the margin floor: total_cogs / (1 - margin_floor).
    if total_cogs <= 0:
        min_price_microusd = 0
    else:
        denom = Decimal(1) - floor  # policy guarantees 0 < denom <= 1
        min_price_microusd = int(
            (Decimal(total_cogs) / denom).to_integral_value(rounding=ROUND_CEILING)
        )

    target = max(min_price_microusd, int(composition.floor_price_microusd))
    price_microusd = _round_price_microusd(target, policy.rounding)

    # Re-check the invariant on the final (rounded) price. Rounding up only raises margin, so this
    # is belt-and-suspenders — but it guarantees the returned plan is margin-true no matter the rule.
    _assert_margin(price_microusd, total_cogs, policy.margin_floor)

    price_cents = _microusd_to_cents(price_microusd)

    receipt = _build_receipt(
        price_microusd=price_microusd,
        price_cents=price_cents,
        total_cogs=total_cogs,
        included_ai_budget=included_ai_budget,
        policy=policy,
        floor_price_microusd=int(composition.floor_price_microusd),
        min_price_microusd=min_price_microusd,
        derivations=derivations,
    )

    return ComposedPlan(
        price_cents=price_cents,
        price_microusd=price_microusd,
        included_ai_budget_microusd=included_ai_budget,
        total_cogs_microusd_month=total_cogs,
        margin_floor=float(policy.margin_floor),
        features=features,
        model_allowlist=tuple(model_allowlist),
        credits=credits,
        rails=tuple(rails),
        derivations=tuple(derivations),
        receipt=receipt,
    )


def _fold_grants(
    grants: dict,
    features: dict,
    model_allowlist: list[str],
    credits: dict,
    rails: list[str],
) -> None:
    """Union one component's grants into the accumulating plan grants."""
    raw_features = grants.get("features")
    if isinstance(raw_features, dict):
        for name, on in raw_features.items():
            features[str(name)] = bool(on)
    elif isinstance(raw_features, (list, tuple, set)):
        for name in raw_features:
            features[str(name)] = True

    raw_models = grants.get("model_allowlist") or grants.get("models")
    if isinstance(raw_models, (list, tuple, set)):
        for model in raw_models:
            model = str(model)
            if model and model not in model_allowlist:
                model_allowlist.append(model)

    raw_rail = grants.get("rail")
    if isinstance(raw_rail, str) and raw_rail and raw_rail not in rails:
        rails.append(raw_rail)
    raw_rails = grants.get("rails")
    if isinstance(raw_rails, (list, tuple, set)):
        for rail in raw_rails:
            rail = str(rail)
            if rail and rail not in rails:
                rails.append(rail)

    raw_credits = grants.get("credits")
    if isinstance(raw_credits, dict):
        credits.update(raw_credits)


def _microusd_to_cents(price_microusd: int) -> int:
    """µUSD → whole cents, rounding UP (a fractional-cent price rounds to the next cent so the
    charged price never dips below the margin-true µUSD figure)."""
    if price_microusd <= 0:
        return 0
    return int((Decimal(price_microusd) / Decimal(10_000)).to_integral_value(rounding=ROUND_CEILING))


def _assert_margin(price_microusd: int, total_cogs_microusd: int, margin_floor: float) -> None:
    """The margin invariant, enforced: total_cogs <= price * (1 - margin_floor). Raises
    `MarginFloorViolation` with the exact figures — never a silent clamp."""
    floor = Decimal(str(margin_floor))
    allowed_cogs = (Decimal(price_microusd) * (Decimal(1) - floor)).to_integral_value(
        rounding=ROUND_CEILING
    )
    if Decimal(total_cogs_microusd) > allowed_cogs:
        realized_margin = (
            (Decimal(price_microusd) - Decimal(total_cogs_microusd)) / Decimal(price_microusd)
            if price_microusd > 0
            else Decimal(-1)
        )
        raise MarginFloorViolation(
            "margin floor violated: total monthly COGS "
            f"{total_cogs_microusd} µUSD exceeds the allowed "
            f"{int(allowed_cogs)} µUSD = price {price_microusd} µUSD × (1 - margin_floor="
            f"{margin_floor}). Realized margin would be {realized_margin:.4f} < floor {margin_floor}. "
            f"Raise the price to at least {_min_price_for(total_cogs_microusd, margin_floor)} µUSD "
            "or lower COGS; the price is never silently clamped."
        )


def _min_price_for(total_cogs_microusd: int, margin_floor: float) -> int:
    floor = Decimal(str(margin_floor))
    if total_cogs_microusd <= 0:
        return 0
    return int((Decimal(total_cogs_microusd) / (Decimal(1) - floor)).to_integral_value(rounding=ROUND_CEILING))


def assert_price_meets_margin(
    price_cents: int,
    included_ai_budget_microusd: int,
    *,
    margin_floor: float = 0.0,
    extra_cogs_microusd_month: int = 0,
) -> int:
    """TRANSITIONAL freehand-write gate. A plan written with a raw `price_cents` + raw
    `included_ai_budget_microusd` (no composition) STILL validates against the SAME margin
    invariant — one path, no silent second rail. The freehand budget is a customer-facing (billed)
    figure, so its realized-COGS contribution is de-marked-up: the realized provider cost of a
    billed allowance is `billed / (1 + markup)`, i.e. the inverse of `usage_pricing.billed_cost`.
    Any `extra_cogs_microusd_month` (external fees not modeled as components) adds on top.

    Returns the total realized monthly COGS it validated against; raises `MarginFloorViolation`
    (with figures) if the price does not clear the floor. Callers pass `margin_floor=0.0` to get
    exactly the historical 100%-of-price cap behavior, now fail-loud instead of clamping.
    """
    if int(price_cents) < 0:
        raise InvalidComponent("price_cents must be non-negative")
    price_microusd = int(price_cents) * 10_000
    realized_ai_cogs = _realized_from_billed(int(included_ai_budget_microusd))
    total_cogs = realized_ai_cogs + max(0, int(extra_cogs_microusd_month))
    _assert_margin(price_microusd, total_cogs, margin_floor)
    return total_cogs


def _realized_from_billed(billed_microusd: int) -> int:
    """Invert `usage_pricing.billed_cost`: recover the realized provider COGS from a billed
    (retail, marked-up) µUSD figure. `billed = ceil(realized * (1 + bps/10000))`, so
    `realized = billed / (1 + bps/10000)`, rounded UP to stay conservative (never understate COGS)."""
    if billed_microusd <= 0:
        return 0
    bps = Decimal(usage_pricing.usage_markup_bps())
    factor = (Decimal(10_000) + bps) / Decimal(10_000)  # >= 1
    realized = (Decimal(billed_microusd) / factor).to_integral_value(rounding=ROUND_CEILING)
    return int(realized)


# ── serialization (round-trip; used to store a composition on the plan row so a webhook-driven
# recompose can re-derive it with an updated component cost basis — UC4 leg 2) ────────────────────


def composition_to_dict(composition: PlanComposition) -> dict:
    """Serialize a `PlanComposition` to a deterministic JSON-safe dict. `composition_from_dict`
    round-trips it exactly; validation on rebuild rides the frozen dataclasses' __post_init__, so a
    tampered/malformed stored composition fails closed instead of composing garbage."""
    components = []
    for component in composition.components:
        basis = component.cost_basis
        basis_data: dict = {"kind": basis.kind}
        if basis.kind == "metered":
            allowance = basis.allowance
            basis_data["allowance"] = {
                "model": allowance.model,
                "provider": allowance.provider,
                "base_url": allowance.base_url,
                "input_tokens": int(allowance.input_tokens),
                "output_tokens": int(allowance.output_tokens),
                "cache_read_tokens": int(allowance.cache_read_tokens),
                "cache_write_tokens": int(allowance.cache_write_tokens),
                "request_count": int(allowance.request_count),
            }
        elif basis.kind == "fixed":
            basis_data["fee_microusd_month"] = int(basis.fee_microusd_month)
        else:  # per_unit
            basis_data["unit_cost_microusd"] = int(basis.unit_cost_microusd)
            basis_data["included_units"] = int(basis.included_units)
        components.append(
            {
                "kind": component.kind,
                "key": component.key,
                "cost_basis": basis_data,
                "grants": dict(component.grants),
            }
        )
    return {
        "components": components,
        "margin_policy": {
            "margin_floor": float(composition.margin_policy.margin_floor),
            "rounding": composition.margin_policy.rounding,
        },
        "floor_price_microusd": int(composition.floor_price_microusd),
    }


def composition_from_dict(data: dict) -> PlanComposition:
    """Rebuild a `PlanComposition` from `composition_to_dict` output. Raises `InvalidComponent` /
    `InvalidMarginPolicy` (fail closed) on anything malformed — never composes a guessed shape."""
    if not isinstance(data, dict):
        raise InvalidComponent("composition data must be an object")
    raw_components = data.get("components")
    if not isinstance(raw_components, (list, tuple)) or not raw_components:
        raise InvalidComponent("composition data needs a non-empty components list")
    components: list[PricedComponent] = []
    for raw in raw_components:
        if not isinstance(raw, dict):
            raise InvalidComponent("each component must be an object")
        basis_data = raw.get("cost_basis")
        if not isinstance(basis_data, dict):
            raise InvalidComponent("each component needs a cost_basis object")
        kind = str(basis_data.get("kind") or "")
        if kind == "metered":
            allowance_data = basis_data.get("allowance")
            if not isinstance(allowance_data, dict):
                raise InvalidComponent("metered cost basis needs an allowance object")
            allowance = MeteredAllowance(
                model=str(allowance_data.get("model") or ""),
                provider=allowance_data.get("provider"),
                base_url=allowance_data.get("base_url"),
                input_tokens=int(allowance_data.get("input_tokens") or 0),
                output_tokens=int(allowance_data.get("output_tokens") or 0),
                cache_read_tokens=int(allowance_data.get("cache_read_tokens") or 0),
                cache_write_tokens=int(allowance_data.get("cache_write_tokens") or 0),
                request_count=int(allowance_data.get("request_count") or 0),
            )
            basis = CostBasis.metered(allowance)
        elif kind == "fixed":
            basis = CostBasis.fixed(int(basis_data.get("fee_microusd_month") or 0))
        elif kind == "per_unit":
            basis = CostBasis.per_unit(
                int(basis_data.get("unit_cost_microusd") or 0),
                int(basis_data.get("included_units") or 0),
            )
        else:
            raise InvalidComponent(f"unknown cost basis kind: {kind!r}")
        grants = raw.get("grants")
        components.append(
            PricedComponent(
                kind=str(raw.get("kind") or ""),
                key=str(raw.get("key") or ""),
                cost_basis=basis,
                grants=dict(grants) if isinstance(grants, dict) else {},
            )
        )
    policy_data = data.get("margin_policy")
    policy_data = policy_data if isinstance(policy_data, dict) else {}
    policy = MarginPolicy(
        margin_floor=float(policy_data.get("margin_floor") or 0.0),
        rounding=str(policy_data.get("rounding") or "cent"),  # type: ignore[arg-type]
    )
    return PlanComposition(
        components=tuple(components),
        margin_policy=policy,
        floor_price_microusd=int(data.get("floor_price_microusd") or 0),
    )


def _build_receipt(
    *,
    price_microusd: int,
    price_cents: int,
    total_cogs: int,
    included_ai_budget: int,
    policy: MarginPolicy,
    floor_price_microusd: int,
    min_price_microusd: int,
    derivations: list[ComponentDerivation],
) -> dict:
    """The full component-level derivation receipt (plan §2.7: "base + shopify_store ($X/mo COGS)
    → price $19→$29"). Deterministic and JSON-serializable so the plan-write can store it verbatim
    in the plan row's jsonb metadata."""
    lines = [f"{d.key} ({d.cost_basis_kind}): {d.detail}" for d in derivations]
    margin_realized = (
        (price_microusd - total_cogs) / price_microusd if price_microusd > 0 else 0.0
    )
    return {
        "engine": "plan_composition.compose_plan",
        "monthly_only": True,
        "components": [
            {
                "key": d.key,
                "kind": d.kind,
                "cost_basis_kind": d.cost_basis_kind,
                "cogs_microusd_month": d.cogs_microusd_month,
                "detail": d.detail,
            }
            for d in derivations
        ],
        "total_cogs_microusd_month": total_cogs,
        "included_ai_budget_microusd": included_ai_budget,
        "margin_floor": float(policy.margin_floor),
        "rounding": policy.rounding,
        "operator_floor_price_microusd": floor_price_microusd,
        "margin_min_price_microusd": min_price_microusd,
        "price_microusd": price_microusd,
        "price_cents": price_cents,
        "realized_margin": round(float(margin_realized), 6),
        "derivation_lines": lines,
    }
