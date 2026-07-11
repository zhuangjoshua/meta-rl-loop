"""CreativeProviderSpec registry — the business-lane creative/media provider seam
(modularization plan §6b item 2), with the money gate made STRUCTURAL.

WHY THIS EXISTS (and why it is NOT the clean Hermes registries)
================================================================
Hermes already ships clean provider registries (``agent/image_gen_registry.py``,
``agent/video_gen_registry.py``). Those are the OPERATOR-agent tool lane and are
deliberately NOT credit-gated. The Takyon *business* lane hardcodes providers today
precisely because its calls must run inside the Takyon money envelope
(reserve -> safebox authority route -> commit/release). So this registry REUSES the
Hermes *shape* (a per-(capability, provider) descriptor + a catalog dispatch) but
NEVER the Hermes call path. Every business-lane provider call goes through the money
envelope in ``gated_creative_call`` — the same split ``web_spend.py`` proves safe:
the registry dispatches, the envelope injects the Takyon meter into the seam.

THE STRUCTURAL INVARIANT (operator's priority-one constraint)
=============================================================
This turns CLAUDE.md's prose rule "no ungated paid capability" into a TYPE-LEVEL
invariant that is STRICTLY STRONGER than today's hand-written reserve:

  * A ``CreativeProviderSpec`` REQUIRES a ``money_gate`` (a ``CreditActionGate`` for
    fixed-price creative work, or a ``UsageRailGate`` for consumption-priced calls).
    A spec constructed without one RAISES in ``__post_init__`` — it is
    UNCONSTRUCTABLE. There is no "forgot to reserve" path.
  * The spec exposes NO raw ``generate()``. The ONLY business-lane invocation path is
    ``gated_creative_call(spec, ...)``, which reserves against ``spec.money_gate``,
    resolves the provider key SERVER-SIDE via the safebox authority route (never
    ``os.environ``), calls ``spec.safebox_route``, and commits on success / releases
    on failure. A missing key fails closed (``*_unconfigured`` / 503) and the reserve
    is released — never a raw-key fallback, never an uncharged provider call.
  * ``usage_pricing`` stays the ONE price source of truth: the spec holds the LOOKUP
    KEY (``pricing_key``), never a price. Receipt-model and priced-model can no longer
    diverge because both derive from ``spec.model`` / ``spec.pricing_key``.
  * ``_API_ENV_ALIASES`` creative rows and the paid-provider denylist are derived FROM
    the registry (``creative_provider_alias_rows`` / ``creative_provider_denylist_names``),
    so adding a provider extends the secret boundary with no second list to maintain.

This module is import-light on purpose: it imports only stdlib + ``safebox`` at module
load. ``core``/``usage_pricing`` are imported lazily inside functions so this stays a
leaf the safebox host and the runtime plane can both import without a cycle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

from . import safebox

__all__ = [
    "MoneyGate",
    "CreditActionGate",
    "UsageRailGate",
    "CreativeProviderSpec",
    "CREATIVE_PROVIDER_REGISTRY",
    "get_creative_provider_spec",
    "gated_creative_call",
    "creative_provider_alias_rows",
    "creative_provider_denylist_names",
    "resolve_priced_model_cost_usd",
    "MissingMoneyGate",
    "CreativeProviderUnpriced",
]


class MissingMoneyGate(TypeError):
    """A ``CreativeProviderSpec`` was constructed without a ``money_gate``.

    This is the structural invariant: an ungated paid creative capability cannot even
    be described, let alone registered or called."""


class CreativeProviderUnpriced(RuntimeError):
    """A spec's ``pricing_key`` is not priced in ``agent/usage_pricing.py``.

    Fail-closed: an unpriced paid model may never be callable — the receipt could only
    fabricate a cost, and the money gate could not settle truthfully."""


# ── Money gate (the required, structural field) ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class MoneyGate:
    """Base for the required money gate. Never construct this directly — a spec must
    bind a concrete ``CreditActionGate`` or ``UsageRailGate``."""

    kind: str = "abstract"


@dataclass(frozen=True, slots=True)
class CreditActionGate(MoneyGate):
    """Fixed-price creative/ad work: a business-scoped creative-CREDIT action.

    ``credit_action`` is the action key in ``core._CREATIVE_CREDIT_COST_DEFAULTS`` /
    ``_CREATIVE_CREDIT_ACTION_AUDIENCES`` (e.g. ``"logo_generate"``). The envelope
    reserves/commits/releases through the AUTHORITATIVE safebox creative-credit gate
    (``safebox.creative_reserve`` -> ``creative_provider_call`` -> ``creative_commit`` /
    ``creative_release``) — the same path the logo/UGC/static-ad renders already use."""

    kind: str = field(default="credit_action", init=False)
    credit_action: str = ""

    def __post_init__(self) -> None:
        if not str(self.credit_action or "").strip():
            raise MissingMoneyGate("CreditActionGate requires a non-empty credit_action")


@dataclass(frozen=True, slots=True)
class UsageRailGate(MoneyGate):
    """Consumption-priced creative call: metered through the usage rail, priced from
    ``agent/usage_pricing.py`` (reserve -> settle -> release). ``meter_op`` names the
    metering op; the exact cost is resolved from the spec's ``pricing_key`` — never a
    price literal here (``usage_pricing`` stays the one SSOT)."""

    kind: str = field(default="usage_rail", init=False)
    meter_op: str = ""

    def __post_init__(self) -> None:
        if not str(self.meter_op or "").strip():
            raise MissingMoneyGate("UsageRailGate requires a non-empty meter_op")


# ── The spec: one per (capability, provider) ──────────────────────────────────────


@dataclass(frozen=True, slots=True)
class CreativeProviderSpec:
    """One business-lane creative/media provider binding.

    Fields:
      * ``canonical_id`` — stable "<capability>:<provider>" id (registry key).
      * ``capability``   — 'image' | 'video' | ... (the creative capability class).
      * ``provider``     — the ``_API_ENV_ALIASES`` provider name (e.g. 'gemini').
      * ``model``        — the model actually rendered. Authoritative for the receipt.
      * ``pricing_key``  — ``(provider, model)`` LOOKUP KEY into ``usage_pricing``;
                           the spec holds the KEY, never a price. ``pricing_provider``
                           may differ from the alias ``provider`` when the pricing table
                           keys the model under a different vendor name (e.g. the
                           Gemini image models are priced under ``"google"``).
      * ``key_aliases``  — safebox env aliases resolved SERVER-SIDE via
                           ``safebox.first_env_backed_value`` (never ``os.environ``).
      * ``safebox_route``— ``(provider, path)`` for ``safebox.creative_provider_call``
                           (the gated route that resolves the key locally and forwards).
      * ``money_gate``   — REQUIRED. Without it the spec is UNCONSTRUCTABLE.

    A spec without ``money_gate`` raises ``MissingMoneyGate`` in ``__post_init__``."""

    canonical_id: str
    capability: str
    provider: str
    model: str
    pricing_key: tuple[str, str]
    key_aliases: tuple[str, ...]
    safebox_route: tuple[str, str]
    money_gate: MoneyGate | None = None

    def __post_init__(self) -> None:
        # THE structural invariant: an ungated paid capability cannot be described.
        if self.money_gate is None or not isinstance(self.money_gate, (CreditActionGate, UsageRailGate)):
            raise MissingMoneyGate(
                f"CreativeProviderSpec {self.canonical_id!r} has no money_gate; "
                "a paid creative capability must bind a CreditActionGate (fixed-price) "
                "or a UsageRailGate (consumption-priced). No ungated paid capability."
            )
        for name, value in (
            ("canonical_id", self.canonical_id),
            ("capability", self.capability),
            ("provider", self.provider),
            ("model", self.model),
        ):
            if not str(value or "").strip():
                raise ValueError(f"CreativeProviderSpec.{name} is required")
        if not self.key_aliases:
            raise ValueError(
                f"CreativeProviderSpec {self.canonical_id!r} needs key_aliases so the key "
                "resolves through the safebox authority route (never os.environ)"
            )
        if len(self.pricing_key) != 2 or not all(str(p).strip() for p in self.pricing_key):
            raise ValueError(
                f"CreativeProviderSpec {self.canonical_id!r} needs a (provider, model) pricing_key"
            )
        if len(self.safebox_route) != 2 or not str(self.safebox_route[0]).strip():
            raise ValueError(
                f"CreativeProviderSpec {self.canonical_id!r} needs a (provider, path) safebox_route"
            )

    # No raw generate(). The ONLY invocation path is gated_creative_call(spec, ...).

    @property
    def pricing_provider(self) -> str:
        return str(self.pricing_key[0])

    @property
    def priced_model(self) -> str:
        return str(self.pricing_key[1])


# ── The registry: specs for what exists TODAY ─────────────────────────────────────
#
# Gemini brand-logo image (logo / UGC-reference / static-ad ancestor render).
# Byte-faithful to current code, EXCEPT the receipt/priced-model divergence is fixed:
#   * The RENDER model is authoritative — ``creative_gateway._GEMINI_IMAGE_MODEL`` =
#     'gemini-3.1-flash-image' is what ``genai.Client().models.generate_content(model=...)``
#     actually calls (creative_gateway.py:374-378 via the gated safebox route). The old
#     ``core._LOGO_IMAGE_MODEL`` = 'gemini-2.5-flash-image' stamped on the receipt is
#     therefore FACTUALLY WRONG (plan §6b item 2's live truthfulness bug). This spec
#     binds the RENDER model, so receipt-model == priced-model == spec.model by
#     construction, and core's logo receipt now reads model/provider from this spec.
#   * Pricing is keyed under vendor "google" in usage_pricing (both flash-image models
#     are $0.039/image there). ``provider='gemini'`` is the ``_API_ENV_ALIASES`` /
#     safebox-route name; ``pricing_key[0]='google'`` is the usage_pricing vendor name.
#   * Money gate = the fixed creative-credit action 'logo_generate' (2 credits), so the
#     envelope routes through the SAME authoritative safebox creative-credit gate the
#     logo path uses today.
_GEMINI_LOGO_IMAGE = CreativeProviderSpec(
    canonical_id="image:gemini-logo",
    capability="image",
    provider="gemini",
    model="gemini-3.1-flash-image",
    pricing_key=("google", "gemini-3.1-flash-image"),
    key_aliases=("TAKYON_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    safebox_route=("gemini", "logo"),
    money_gate=CreditActionGate(credit_action="logo_generate"),
)

# Gemini site imagery. This is deliberately separate from logo generation: site assets are
# published into product/site and are scoped by their own capability so an image token cannot be
# replayed against the logo or usage-metered product-image routes.
_GEMINI_SITE_IMAGE = CreativeProviderSpec(
    canonical_id="image:gemini-site",
    capability="image",
    provider="gemini",
    model="gemini-3.1-flash-image",
    pricing_key=("google", "gemini-3.1-flash-image"),
    key_aliases=("TAKYON_GEMINI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY"),
    safebox_route=("gemini", "site-image"),
    money_gate=CreditActionGate(credit_action="site_image_generate"),
)

CREATIVE_PROVIDER_REGISTRY: dict[str, CreativeProviderSpec] = {
    _GEMINI_LOGO_IMAGE.canonical_id: _GEMINI_LOGO_IMAGE,
    _GEMINI_SITE_IMAGE.canonical_id: _GEMINI_SITE_IMAGE,
}


def get_creative_provider_spec(canonical_id: str) -> CreativeProviderSpec:
    spec = CREATIVE_PROVIDER_REGISTRY.get(str(canonical_id or "").strip())
    if spec is None:
        raise KeyError(f"unknown creative provider spec: {canonical_id!r}")
    return spec


# ── Derivations FROM the registry (secret boundary + price truthfulness) ──────────


def creative_provider_alias_rows() -> dict[str, tuple[str, ...]]:
    """The ``_API_ENV_ALIASES`` rows the creative registry OWNS, keyed by provider.

    ``core._API_ENV_ALIASES`` must stay consistent with these (a test pins it). Deriving
    the alias rows from the specs means adding a provider extends the secret boundary
    (and the denylist that builds from it) with no second list to hand-sync."""
    rows: dict[str, tuple[str, ...]] = {}
    for spec in CREATIVE_PROVIDER_REGISTRY.values():
        existing = rows.get(spec.provider)
        if existing is None:
            rows[spec.provider] = tuple(spec.key_aliases)
        else:
            # Same provider across capabilities: union preserving order.
            merged = list(existing)
            for alias in spec.key_aliases:
                if alias not in merged:
                    merged.append(alias)
            rows[spec.provider] = tuple(merged)
    return rows


def creative_provider_denylist_names() -> frozenset[str]:
    """Paid-provider env-key names the creative registry contributes to the safebox
    ``/v1/env`` egress denylist. Every alias is a raw paid-provider credential a runtime
    plane must never fetch — the safebox resolves it locally in the authority route."""
    names: set[str] = set()
    for spec in CREATIVE_PROVIDER_REGISTRY.values():
        names.update(spec.key_aliases)
    return frozenset(names)


def resolve_priced_model_cost_usd(spec: CreativeProviderSpec, *, units: int = 1) -> float:
    """Resolve the EXACT per-request provider cost (USD) for the spec's priced model
    from ``agent/usage_pricing.py`` — the ONE price SSOT. Fail-closed: an unpriced model
    raises ``CreativeProviderUnpriced`` so no unpriced paid model is ever callable and no
    receipt can fabricate a cost."""
    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

    result = estimate_usage_cost(
        spec.priced_model,
        CanonicalUsage(request_count=max(1, int(units or 1))),
        provider=spec.pricing_provider,
    )
    if result.amount_usd is None:
        raise CreativeProviderUnpriced(
            f"{spec.pricing_provider}/{spec.priced_model} is not priced in usage_pricing; "
            f"creative provider {spec.canonical_id!r} is refused (no unpriced provider spend)"
        )
    return float(result.amount_usd)


# ── The ONLY business-lane invocation path ────────────────────────────────────────


def gated_creative_call(
    spec: CreativeProviderSpec,
    *,
    business: str,
    operator_user_id: str,
    reservation_key: str,
    payload: Mapping[str, Any],
    units: int = 1,
    metadata: Mapping[str, Any] | None = None,
    on_result: Callable[[dict[str, Any]], Any] | None = None,
) -> dict[str, Any]:
    """Reserve -> (key resolved server-side) provider call -> commit/release, for a
    ``CreditActionGate`` spec. This is the ONLY sanctioned business-lane path to a
    creative provider; ``CreativeProviderSpec`` exposes no raw ``generate()``.

    Order (STRICTLY STRONGER than today's hand-written reserve, and matched to the
    authoritative safebox path the logo render already uses):

      1. Fail-closed price check: the priced model must be priced in usage_pricing, or
         we refuse BEFORE any reserve (``CreativeProviderUnpriced``).
      2. RESERVE against ``spec.money_gate`` on the safebox (mints the creative
         capability). This happens BEFORE the key is resolved — the key never touches
         this plane; it is resolved SERVER-SIDE inside the gated route in step 3.
      3. Call ``spec.safebox_route`` via ``safebox.creative_provider_call`` presenting
         the capability. The safebox verifies it, resolves the key LOCALLY from
         ``spec.key_aliases`` (``first_env_backed_value``), and forwards. A missing key
         fails closed there (``*_unconfigured`` / refusal) and propagates.
      4. On success COMMIT; on ANY failure RELEASE the reservation. Never a raw-key
         fallback, never an uncharged provider call, never a charged-but-failed call.

    Returns ``{"success", "status", "provider", "model", "provider_cost_usd",
    "credits_charged", "balance_credits", "reserved_credits", "result"}``. The
    ``model``/``provider`` in the result are the spec's (render == priced == receipt).
    """
    from . import core

    gate = spec.money_gate
    if not isinstance(gate, CreditActionGate):
        # UsageRailGate specs are metered through the usage rail, not the creative-credit
        # safebox reserve. No such spec exists in the registry today; when one is added,
        # wire its usage-rail envelope here (reserve/settle/release via app_usage) rather
        # than the creative-credit gate. Fail closed rather than silently mis-charge.
        raise NotImplementedError(
            f"gated_creative_call for gate kind {gate.kind!r} is not wired yet; "
            "add the usage-rail envelope before registering a UsageRailGate spec"
        )

    business = str(business or "").strip()
    operator_user_id = str(operator_user_id or "").strip()
    reservation_key = str(reservation_key or "").strip()
    if not business or not operator_user_id or not reservation_key:
        raise ValueError("gated_creative_call requires business, operator_user_id, reservation_key")

    # (1) Fail-closed price check BEFORE any reserve — an unpriced model may never spend.
    provider_cost_usd = resolve_priced_model_cost_usd(spec, units=units)
    requested_credits = core._creative_credit_total_cost(gate.credit_action, units=units)
    audience = core._creative_credit_action_audience(gate.credit_action) or gate.credit_action

    # (2) RESERVE first (mints the creative capability). The key is NOT resolved here.
    reservation = safebox.creative_reserve(
        business=business,
        operator_user_id=operator_user_id,
        action=audience,
        reservation_key=reservation_key,
        units=max(1, int(units or 1)),
        metadata=(dict(metadata) if metadata else None),
    )
    capability_token = str((reservation or {}).get("token") or "")
    if not capability_token:
        # Reserve returned no capability — release defensively and fail closed.
        try:
            safebox.creative_release(reservation_key=reservation_key)
        except Exception:
            pass
        raise RuntimeError("creative_capability_unavailable")

    finalized = False
    try:
        # (3) Provider call — the key is resolved SERVER-SIDE inside this gated route
        # from spec.key_aliases; a missing key fails closed there and raises.
        provider_name, route_path = spec.safebox_route
        result = safebox.creative_provider_call(
            str(provider_name), str(route_path), dict(payload or {}), token=capability_token
        )
        processed: Any = None
        if on_result is not None:
            processed = on_result(result)

        # (4a) COMMIT on success.
        balances = safebox.creative_commit(reservation_key=reservation_key)
        finalized = True
        return {
            "success": True,
            "status": "created",
            "provider": spec.provider,
            "model": spec.model,  # render == priced == receipt, by construction
            "pricing_key": list(spec.pricing_key),
            "provider_cost_usd": provider_cost_usd,
            "credits_charged": requested_credits,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
            "result": result,
            "processed": processed,
        }
    except Exception:
        # (4b) RELEASE on ANY failure (missing key / provider error / post-process).
        if not finalized:
            try:
                safebox.creative_release(reservation_key=reservation_key)
            except Exception:
                pass
        raise
