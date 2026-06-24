"""Dedicated Safebox service app.

This is the service boundary for Safebox when it runs on its own VPS. The
runtime planes talk to it over HTTP; the service itself still uses the local
Safebox authority module as the single backing implementation.
"""

from __future__ import annotations

import base64
import hmac
import json
import os
import re
import time
import uuid
from contextlib import contextmanager
from typing import Any, Iterable

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel

from . import safebox
from .safebox_capability import CapabilityScope, mint_capability, verify_capability
from .safebox_nonce import pg_claim_nonce

_SAFEBOX_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"

# The capability signing key is a SAFEBOX-ONLY secret: it is read from the process env on the
# safebox host and is NEVER written to any client .env (that is the whole point — a client cannot
# mint a token for another tenant nor raise its own ceiling). Read it via os.environ here (this is
# safebox-internal code on the safebox host, not a business runtime), and fail closed if absent so a
# misconfigured host can never broker or mint without a key.
_CAP_SIGNING_KEY_ENV = "TAKYON_CAP_SIGNING_KEY"

# Per-action audience + provider-key aliases + pricing seam. The audience binds a minted token to
# exactly one provider action; mismatched audiences are rejected by verify_capability. Key aliases
# mirror the canonical resolvers (ai_provider.anthropic_key / tavily_key,
# creative_gateway._GEMINI_KEY_ALIASES) so "what key does this action use" lives in ONE place.
_ANTHROPIC_AUDIENCE = "anthropic.messages"
_TAVILY_AUDIENCE = "tavily.search"
_GEMINI_IMAGE_AUDIENCE = "gemini.image"

# ── Operator/platform SESSION capability audience ────────────────────────────────────────────────
# The operator/platform plane (CEO agent + coding worker + platform web_tools) calls Anthropic /
# Tavily through the safebox proxy with the stock SDK and a STATIC key, making MANY streaming calls.
# A single-use-nonce capability cannot cover that. This audience binds a SESSION-scoped operator
# capability: signed, operator+business-bound, with a per-CALL cost CEILING (``max_cost_microusd``)
# and a minutes-to-hours TTL, and — unlike the per-call product/creative capabilities — REUSABLE
# across calls (the proxy verifies it but does NOT claim a nonce, so a reused token is not a replay).
# The safebox meters EACH call against the verified operator's control-plane budget keyed on
# ``scope.takyon_user_id`` (the business owner = the operator). The audience is accepted by the three
# operator proxy routes (``/v1/messages``, ``/v1/proxy/anthropic/messages``, ``/v1/proxy/tavily/{op}``)
# in addition to the per-action audiences those routes already match, so one session token covers both
# Anthropic and Tavily for a run.
_OPERATOR_SESSION_AUDIENCE = "operator.session"

# Default TTL for a session-scoped operator capability (seconds). Minutes-to-hours, NOT the 300s
# per-call TTL — the CEO/worker run streams many calls under one token. Capped so a leaked session
# token still expires within the bound.
_OPERATOR_SESSION_TTL_SECONDS = 3600
_OPERATOR_SESSION_TTL_MAX_SECONDS = 6 * 3600

# ── Creative-credit audiences (logo / UGC video / static ad) ──────────────────────────────────────
# These are the AUTHORITATIVE creative-credit gate audiences. A creative capability is minted by the
# operator (boundary-1 ownership) against ONE creative action, and the safebox reserves the action's
# fixed creative-credit price BEFORE it hands the operator a token. The creative provider routes
# (/v1/providers/{gemini,openai,fal}) then accept a VERIFIED creative capability, resolve the provider
# key LOCALLY, and forward — never returning the key. Unlike the per-CALL usage broker
# (anthropic/tavily/gemini.image), a single creative action makes SEVERAL provider calls (UGC = 1
# OpenAI image + N FAL clips), so the credit gate is reserved/committed ONCE per action via the
# /v1/creative/{reserve,commit,release} routes; the provider routes verify the creative capability but
# do NOT re-reserve (re-reserving per call would multiply-charge the fixed action price). The token is
# therefore NOT single-use: it authorizes every provider call within ONE reserved creative action for
# the life of its short TTL.
_CREATIVE_LOGO_AUDIENCE = "creative.logo"
_CREATIVE_UGC_AUDIENCE = "creative.ugc"
_CREATIVE_STATIC_AD_AUDIENCE = "creative.static_ad"
_CREATIVE_X_PUBLISH_AUDIENCE = "creative.x_publish"
_CREATIVE_REDDIT_PUBLISH_AUDIENCE = "creative.reddit_publish"
_CREATIVE_META_AD_LAUNCH_AUDIENCE = "creative.meta_ad_launch"
_CREATIVE_REDDIT_AD_LAUNCH_AUDIENCE = "creative.reddit_ad_launch"
_CREATIVE_META_AD_MEDIA_SPEND_AUDIENCE = "creative.meta_ad_media_spend"
_CREATIVE_REDDIT_AD_MEDIA_SPEND_AUDIENCE = "creative.reddit_ad_media_spend"

# Creative action (capability `action`, also the mint action) -> its canonical creative-credit cost
# action key in core._CREATIVE_CREDIT_COST_DEFAULTS/_ENVS. The fixed price the client used and the
# price the safebox reserves both resolve from that ONE canonical table (env-override-first), so there
# is no second price table on the safebox.
_CREATIVE_AUDIENCE_CREDIT_ACTION = {
    _CREATIVE_LOGO_AUDIENCE: "logo_generate",
    _CREATIVE_UGC_AUDIENCE: "ugc_ad_generate",
    _CREATIVE_STATIC_AD_AUDIENCE: "static_ad_generate",
    _CREATIVE_X_PUBLISH_AUDIENCE: "x_publish_outreach",
    _CREATIVE_REDDIT_PUBLISH_AUDIENCE: "reddit_publish_outreach",
    _CREATIVE_META_AD_LAUNCH_AUDIENCE: "meta_ad_launch",
    _CREATIVE_REDDIT_AD_LAUNCH_AUDIENCE: "reddit_ad_launch",
    _CREATIVE_META_AD_MEDIA_SPEND_AUDIENCE: "meta_ad_media_spend",
    _CREATIVE_REDDIT_AD_MEDIA_SPEND_AUDIENCE: "reddit_ad_media_spend",
}

# Which creative audiences each gated creative PROVIDER route accepts. A logo capability may only hit
# Gemini; a UGC capability may hit OpenAI (the reference image) AND FAL (the clips); a static-ad
# capability may hit OpenAI. This binds the reserved creative action to exactly the providers that
# action legitimately uses, so a cheap action's token cannot drive an unrelated provider.
_CREATIVE_GEMINI_AUDIENCES = frozenset({_CREATIVE_LOGO_AUDIENCE})
_CREATIVE_OPENAI_AUDIENCES = frozenset({_CREATIVE_UGC_AUDIENCE, _CREATIVE_STATIC_AD_AUDIENCE})
_CREATIVE_FAL_AUDIENCES = frozenset({_CREATIVE_UGC_AUDIENCE})

# Default action -> audience so a token minted for a known provider action is directly brokerable by
# the matching provider route without the caller having to restate the audience. A caller may still
# pass an explicit `audience` to mint for a future/custom action.
_ACTION_AUDIENCE_DEFAULTS = {
    _ANTHROPIC_AUDIENCE: _ANTHROPIC_AUDIENCE,
    _TAVILY_AUDIENCE: _TAVILY_AUDIENCE,
    _GEMINI_IMAGE_AUDIENCE: _GEMINI_IMAGE_AUDIENCE,
}

# Default short TTL for minted capability tokens (seconds). The token is also single-use (nonce) and
# audience-bound, so a leaked token does exactly one {tenant, action, <=cost} thing within this window.
_CAP_TTL_SECONDS = 300


def _normalize_stripe_request(path: str, method: str, params: dict[str, Any] | None) -> tuple[str, str, dict[str, Any]]:
    stripe_path = str(path or "").strip().lstrip("/")
    stripe_method = str(method or "POST").strip().upper()
    if not stripe_path or "?" in stripe_path or "\\" in stripe_path or ".." in stripe_path.split("/"):
        raise HTTPException(status_code=403, detail="stripe_path_not_allowed")
    clean_params = dict(params or {})
    parts = stripe_path.split("/")
    if stripe_method == "POST" and stripe_path in {"products", "prices", "checkout/sessions"}:
        _require_takyon_app_stripe_params(stripe_path, clean_params)
        return stripe_path, stripe_method, clean_params
    if stripe_method == "GET" and len(parts) == 3 and parts[:2] == ["checkout", "sessions"] and parts[2].startswith("cs_"):
        return stripe_path, stripe_method, clean_params
    if len(parts) == 2 and parts[0] == "subscriptions" and parts[1].startswith("sub_"):
        if stripe_method == "GET":
            return stripe_path, stripe_method, clean_params
        if stripe_method == "POST" and set(clean_params) <= {"cancel_at_period_end"}:
            return stripe_path, stripe_method, clean_params
    raise HTTPException(status_code=403, detail="stripe_path_not_allowed")


def _storage_provider(provider: str) -> str:
    value = str(provider or "").strip()
    if value not in {"supabase_s3", "r2"}:
        raise HTTPException(status_code=400, detail="unknown_storage_provider")
    return value


_SAFE_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,95}$")


def _require_safe_slug(value: str, *, detail: str = "unsafe_slug") -> str:
    slug = str(value or "").strip().lower()
    if not _SAFE_SLUG_RE.fullmatch(slug):
        raise HTTPException(status_code=403, detail=detail)
    return slug


def _require_existing_business(slug: str) -> str:
    business = _require_safe_slug(slug)
    with _safebox_db_conn() as conn:
        row = conn.execute("select 1 from businesses where slug = %s", (business,)).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="unknown_business")
    return business


def _storage_business_slug(path: str) -> str:
    raw = str(path or "").strip().strip("/")
    if not raw:
        raise HTTPException(status_code=403, detail="storage_scope_required")
    return _require_existing_business(raw.split("/", 1)[0])


def _domain_business_slug(domain: str) -> str:
    name = str(domain or "").strip().lower().strip(".")
    base = str(
        os.environ.get("PUBLIC_COMPANY_BASE_DOMAIN")
        or safebox.load_env().get("PUBLIC_COMPANY_BASE_DOMAIN")
        or os.environ.get("TAKYON_COMPANY_BASE_DOMAIN")
        or safebox.load_env().get("TAKYON_COMPANY_BASE_DOMAIN")
        or "coscale.app"
    ).strip().lower().strip(".")
    suffix = f".{base}"
    if name == base or not name.endswith(suffix):
        raise HTTPException(status_code=403, detail="domain_not_product_scoped")
    labels = name[: -len(suffix)].split(".")
    if not labels or not labels[-1]:
        raise HTTPException(status_code=403, detail="domain_not_product_scoped")
    return _require_existing_business(labels[-1])


def _metadata_value(params: dict[str, Any], key: str) -> str:
    return str(params.get(f"metadata[{key}]") or params.get(f"metadata[{key.lower()}]") or "").strip()


def _require_takyon_app_stripe_params(path: str, params: dict[str, Any]) -> str:
    business_name = _metadata_value(params, "business")
    if not business_name:
        raise HTTPException(status_code=403, detail="stripe_scope_required")
    business = _require_existing_business(business_name)
    if _metadata_value(params, "source") != "takyon_app":
        raise HTTPException(status_code=403, detail="stripe_scope_required")
    if path in {"products", "prices"} and not _metadata_value(params, "plan_key"):
        raise HTTPException(status_code=403, detail="stripe_plan_scope_required")
    if path == "checkout/sessions":
        if not _metadata_value(params, "plan_key") or not _metadata_value(params, "checkout_intent_id"):
            raise HTTPException(status_code=403, detail="stripe_checkout_scope_required")
        for url_key in ("success_url", "cancel_url"):
            url = str(params.get(url_key) or "").strip()
            if not url.startswith("https://") or any(ch.isspace() for ch in url):
                raise HTTPException(status_code=403, detail="stripe_redirect_not_allowed")
    return business


def _require_takyon_app_stripe_object(payload: dict[str, Any], *, require_source: bool = False) -> str:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    business = str(metadata.get("business") or "").strip()
    if not business:
        raise HTTPException(status_code=403, detail="stripe_scope_required")
    if require_source and str(metadata.get("source") or "").strip() != "takyon_app":
        raise HTTPException(status_code=403, detail="stripe_scope_required")
    return _require_existing_business(business)


def _require_magic_link_email(body: "_PostmarkSendBody") -> None:
    subject = str(body.subject or "")
    text = str(body.text_body or "")
    html = str(body.html_body or "")
    if not subject.startswith("Sign in to "):
        raise HTTPException(status_code=403, detail="postmark_scope_required")
    if "This link expires in 15 minutes and can be used once." not in text:
        raise HTTPException(status_code=403, detail="postmark_scope_required")
    for candidate in re.findall(r"https?://[^\s\"'<>]+", "\n".join([text, html])):
        if not candidate.startswith("https://"):
            raise HTTPException(status_code=403, detail="postmark_link_not_allowed")
        host = candidate.split("://", 1)[1].split("/", 1)[0].split(":", 1)[0].lower()
        if not (
            host == "app.fourmanifold.com"
            or host.endswith(".coscale.app")
            or host.endswith(".fourmanifold.com")
        ):
            raise HTTPException(status_code=403, detail="postmark_link_not_allowed")


def _cap_signing_key() -> bytes:
    """The safebox-only capability signing key as bytes, or "" if unconfigured.

    Read from the process env on the safebox host only. Callers MUST fail closed on b"" — an
    unconfigured signing key must never mean "minting/broker disabled but proceed anyway"."""
    return str(os.environ.get(_CAP_SIGNING_KEY_ENV) or "").strip().encode("utf-8")


@contextmanager
def _safebox_db_conn():
    """Open the SAFEBOX-OWNED Postgres connection (same recipe as safebox._creative_credit_conn).

    The usage-ledger STEP-A SECURITY DEFINER functions are writable only by the safebox role, so the
    ledger adapter below runs them on THIS connection on the safebox host."""
    from .runtime_app import resolve_database_url
    import psycopg

    raw_conn = psycopg.connect(
        resolve_database_url(),
        autocommit=True,
        prepare_threshold=None,
    )
    try:
        yield raw_conn
    finally:
        raw_conn.close()


class _UsageLedgerAdapter:
    """Ledger the broker reserves/settles/releases against, keyed on the AUTHORITATIVE scope.

    Each method opens the safebox's own DB connection and calls the STEP-A SECURITY DEFINER usage
    functions (``safebox_usage_reserve`` / ``safebox_usage_settle`` / ``safebox_usage_release``).
    Those functions are the single money gate on the safebox host; this adapter is only the thin
    bind from the verified {business_slug, app_user_id, action} scope to those calls. Reserve raises
    (fail-closed) on insufficient funds, which the broker turns into a release-free refusal. The
    reservation handle returned by reserve is passed straight back to settle/release — the broker
    never inspects it."""

    def __init__(self, *, provider: str):
        self._provider = str(provider or "")

    def reserve(self, scope: CapabilityScope, estimate_microusd: int):
        from . import app_entitlements, app_usage
        from .ai_gateway import _user_weekly_budget_microusd

        key = str(uuid.uuid4())
        with _safebox_db_conn() as conn:
            # A PRODUCT (sub-user) scope ALWAYS gets a concrete per-user limit so the 0037 gate is
            # actually enforced (it only enforces when the limit is not null). On any entitlement/plan
            # miss the plan-derived limit is 0 ⇒ reserve refuses (402), never None ⇒ "no cap". Only an
            # OPERATOR scope (app_user_id is None) gets None (no per-user cap on operator spend).
            limit = None
            tier = None
            if scope.app_user_id:
                plan = None
                ent = app_entitlements.get_active_entitlement(conn, scope.business_slug, scope.app_user_id)
                if ent is not None:
                    tier = getattr(ent, "tier", None)
                    plan = (
                        app_entitlements.get_plan_policy(conn, scope.business_slug, ent.plan_key)
                        if getattr(ent, "plan_key", None)
                        else None
                    )
                limit = _user_weekly_budget_microusd(plan)
            app_usage.reserve_usage(
                conn,
                scope.business_slug,
                estimated_cost_microusd=int(estimate_microusd),
                reservation_key=key,
                app_user_id=scope.app_user_id,
                user_monthly_limit_microusd=limit,
                app_user_tier=tier,
                provider=self._provider,
                metadata={"via": "safebox_broker", "action": scope.action},
            )
        return {"business_slug": scope.business_slug, "reservation_key": key}

    def settle(self, reservation, actual_microusd: int) -> None:
        from . import app_usage

        with _safebox_db_conn() as conn:
            app_usage.settle_usage(
                conn,
                reservation["business_slug"],
                reservation["reservation_key"],
                actual_cost_microusd=int(actual_microusd),
                provider=self._provider,
            )

    def release(self, reservation) -> None:
        from . import app_usage

        with _safebox_db_conn() as conn:
            app_usage.release_usage(
                conn,
                reservation["business_slug"],
                reservation["reservation_key"],
                error="broker_release",
            )


def _microusd_to_cents_ceiling(microusd: int) -> int:
    """Convert a microUSD magnitude to whole CENTS, rounding UP. The operator control-plane billing
    rail (``billing.py``) is denominated in cents; provider spend is priced in microUSD. The HOLD must
    never under-charge the authority, so the estimate is rounded toward +infinity — a sub-cent provider
    call still reserves at least 1 cent, so a flood of sub-cent operator calls cannot stay forever free
    against the cumulative ceiling. Settles re-clamp to the held cents (never over-charge the
    reservation). This mirrors ``web_spend._microusd_to_cents_ceiling`` (the same operator rail)."""
    from decimal import ROUND_CEILING, Decimal

    return int((Decimal(int(max(0, microusd))) / Decimal(10_000)).quantize(Decimal("1"), rounding=ROUND_CEILING))


class _OperatorBudgetAdapter:
    """Operator control-plane money rail the OPERATOR proxy routes reserve/settle/release against.

    The operator/platform plane (CEO agent, coding worker, platform web_tools) calls Anthropic / Tavily
    through the safebox proxy. That spend is OPERATOR spend — it carries NO product ``app_user_id`` and
    no product subscription, so it must be bounded by the OPERATOR's own control-plane billing authority
    (``billing.py``, the Takyon-user -> platform rail), NOT the per-business product usage rail and NOT a
    product entitlement. The authority is keyed on the verified ``scope.takyon_user_id`` — the business
    owner resolved by ``authorize_operator_call`` / the session-token mint, i.e. the operator's own
    Takyon-user id.

    This mirrors the reserve/settle/release shape ``web_spend.py`` uses for ungated operator web egress:
    convert the microUSD estimate to cents (ceiling), take a REAL hold on ``billing.reserve`` (which
    locks the single ``billing_accounts`` row FOR UPDATE, draws the operator allowance, and raises
    ``InsufficientBalance`` when the allowance can no longer cover the estimate — so the gate is
    cumulative and fails CLOSED), then ``billing.settle`` the clamped actual on success / ``billing.refund``
    the whole hold on failure. ``billing.reserve`` is idempotent on its reservation_key, so the broker can
    pass the same key safely. All of this runs INSIDE the safebox process on the safebox's own DB
    connection, so the gate is AUTHORITATIVE on the safebox — no client may reserve/settle the operator
    rail.

    The reservation handle carries the operator user id + reservation key + held cents so settle/release
    finalize the SAME hold. The proxy passes the handle straight back; it never inspects it."""

    def reserve(self, scope: "CapabilityScope", estimate_microusd: int):
        from . import billing

        operator_user_id = str(getattr(scope, "takyon_user_id", "") or "").strip()
        if not operator_user_id:
            # No operator identity on a verified operator scope is a fail-closed condition: an operator
            # call with no billing authority must be refused, never run free.
            raise BrokerLedgerError("operator_identity_missing")
        estimate_cents = _microusd_to_cents_ceiling(int(estimate_microusd))
        key = str(uuid.uuid4())
        with _safebox_db_conn() as conn:
            if estimate_cents <= 0:
                # A zero-cost call (e.g. a 0 ceiling free action) still anchors a reservation_key so
                # settle/release are well-defined and idempotent; billing.reserve writes a zero anchor.
                billing.reserve(
                    conn,
                    operator_user_id,
                    0,
                    key,
                    business_slug=(scope.business_slug or None),
                    job_id=f"operator_proxy:{scope.action}",
                )
                return {"operator_user_id": operator_user_id, "reservation_key": key, "reserved_cents": 0}
            try:
                resv = billing.reserve(
                    conn,
                    operator_user_id,
                    estimate_cents,
                    key,
                    business_slug=(scope.business_slug or None),
                    job_id=f"operator_proxy:{scope.action}",
                )
            except billing.NoBillingAccount as exc:
                # Every real operator is funded by the subscription/starter allowance; no account means
                # "no money authority", which must fail CLOSED (not "free").
                raise BrokerLedgerError("operator_no_billing_account") from exc
            except billing.InsufficientBalance as exc:
                # Cumulative ceiling: outstanding holds + settled spend already consume the authority, so
                # this call cannot be covered. THIS is the money gate that refuses an out-of-budget
                # operator BEFORE any provider key is resolved or any provider is called.
                raise OperatorBudgetExceeded(
                    estimate_cents=int(exc.estimate_cents),
                    allowance_available_cents=int(exc.allowance_available_cents),
                ) from exc
        return {
            "operator_user_id": operator_user_id,
            "reservation_key": key,
            "reserved_cents": int(resv.total_cents),
        }

    def settle(self, reservation, actual_microusd: int) -> None:
        from . import billing

        reserved_cents = int(reservation.get("reserved_cents") or 0)
        if reserved_cents <= 0:
            # Zero anchor: settle at 0 to finalize the hold (held -> spent, nothing to charge).
            with _safebox_db_conn() as conn:
                billing.settle(conn, reservation["reservation_key"], 0)
            return
        # billing.settle asserts actual <= reserved (it is custody of real money). The held estimate was
        # rounded UP, so clamp the realized cents to the held cents — never over-charge the reservation.
        actual_cents = min(_microusd_to_cents_ceiling(int(actual_microusd)), reserved_cents)
        with _safebox_db_conn() as conn:
            billing.settle(conn, reservation["reservation_key"], actual_cents)

    def release(self, reservation) -> None:
        from . import billing

        with _safebox_db_conn() as conn:
            # Return the whole operator billing hold to the authority (no spend recorded). Idempotent.
            billing.refund(conn, reservation["reservation_key"])


class OperatorBudgetExceeded(Exception):
    """The operator's control-plane allowance can no longer cover the estimate (cumulative gate).

    Carries the exact cents figures so the proxy can build a precise 402 / SSE error without leaking
    anything else. Raised by ``_OperatorBudgetAdapter.reserve`` BEFORE any provider key resolution or
    upstream call."""

    def __init__(self, *, estimate_cents: int, allowance_available_cents: int) -> None:
        self.estimate_cents = int(estimate_cents)
        self.allowance_available_cents = int(allowance_available_cents)
        super().__init__(
            f"operator_budget_exceeded: need {estimate_cents} cents, "
            f"allowance {allowance_available_cents} cents"
        )


def _creative_credit_price(audience: str, *, units: int = 1) -> int:
    """The fixed creative-credit price for a creative audience, resolved from the ONE canonical table
    in ``core`` (``_CREATIVE_CREDIT_COST_DEFAULTS`` + env override ``_CREATIVE_CREDIT_COST_ENVS``). The
    safebox imports core's resolver instead of duplicating a price table, so the price the client used
    and the price the safebox reserves can never diverge. Unknown audience -> ValueError (fail closed)."""
    action = _CREATIVE_AUDIENCE_CREDIT_ACTION.get(str(audience or ""))
    if not action:
        raise ValueError(f"no creative credit action for audience {audience!r}")
    from . import core

    return int(core._creative_credit_total_cost(action, units=max(1, int(units or 1))))


class _CreditLedgerAdapter:
    """Creative-credit ledger the creative gate reserves/commits/releases against, keyed on the
    AUTHORITATIVE verified scope's ``business_slug`` and the creative action's FIXED credit price.

    This mirrors ``_UsageLedgerAdapter`` but backs the creative-credit rail (``business_credits``)
    instead of the per-call usage rail: reserve -> commit on success / release on failure. It opens the
    safebox's own DB connection and runs the append-only credit ledger there on the safebox host, so the
    creative-credit gate is AUTHORITATIVE on the safebox (no client may reserve/commit credits). The
    fixed price comes from ``_creative_credit_price`` (the canonical per-action table), NOT a client
    value, so a client cannot under-reserve. Reserve raises ``safebox.InsufficientCreativeCredits`` (the
    route maps it to a clean 402) BEFORE any provider key is resolved or any provider is called."""

    def __init__(self, *, audience: str):
        self._audience = str(audience or "")

    def reserve(
        self,
        scope: "CapabilityScope",
        *,
        reservation_key: str,
        units: int = 1,
        metadata: dict[str, Any] | None = None,
    ):
        from . import safebox

        credits = _creative_credit_price(self._audience, units=units)
        reserve_metadata = {
            **(metadata if isinstance(metadata, dict) else {}),
            "via": "safebox_creative_gate",
            "audience": self._audience,
            "action": scope.action,
            "units": int(max(1, units or 1)),
        }
        with _safebox_db_conn() as conn:
            reservation = safebox._local_reserve_credits(
                conn,
                scope.business_slug,
                credits,
                reservation_key,
                metadata=reserve_metadata,
            )
        return {
            "business_slug": scope.business_slug,
            "reservation_key": reservation.key,
            "reserved_credits": int(reservation.reserved_credits),
            "credits": credits,
        }

    def commit(
        self,
        *,
        reservation_key: str,
        actual_credits: int | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        from . import safebox

        commit_metadata = {
            **(metadata if isinstance(metadata, dict) else {}),
            "via": "safebox_creative_gate",
            "audience": self._audience,
        }
        with _safebox_db_conn() as conn:
            return safebox._local_commit_credits(
                conn,
                reservation_key,
                actual_credits=actual_credits,
                metadata=commit_metadata,
            )

    def release(
        self,
        *,
        reservation_key: str,
        metadata: dict[str, Any] | None = None,
    ):
        from . import safebox

        release_metadata = {
            **(metadata if isinstance(metadata, dict) else {}),
            "via": "safebox_creative_gate",
            "audience": self._audience,
        }
        with _safebox_db_conn() as conn:
            return safebox._local_release_credits(
                conn,
                reservation_key,
                metadata=release_metadata,
            )


class BrokerLedgerError(Exception):
    """The safebox usage ledger refused a reserve/settle/release (e.g. SECURITY DEFINER fn failed)."""


class _PgNonceStore:
    """Single-use nonce store backed by the safebox-owned ``safebox_used_nonces`` table.

    The broker calls ``.claim(nonce, expires_at, now=...)``; we delegate to the authoritative
    ``pg_claim_nonce`` (INSERT ... ON CONFLICT DO NOTHING) on the safebox's own connection so a
    replayed token is rejected exactly once. ``now`` is accepted for interface parity (the row's
    own ``expires_at`` plus the periodic sweep bound the set)."""

    def claim(self, nonce: str, expires_at: int, *, now: int) -> bool:
        with _safebox_db_conn() as conn:
            return pg_claim_nonce(conn, nonce, int(expires_at))


class _EnvValueBody(BaseModel):
    value: str


class _FirstEnvBody(BaseModel):
    keys: list[str]


class _ComposioForwardBody(BaseModel):
    method: str = "GET"
    path: str = ""
    json_body: dict[str, Any] | None = None
    params: list[Any] | None = None
    timeout: float = 60.0


class _MetaGraphBody(BaseModel):
    method: str
    path: str
    params: dict[str, Any] = {}
    host: str = "graph.facebook.com"
    timeout: float = 60.0


class _RegisterUserKeyBody(BaseModel):
    user_id: str
    raw_key: str
    key_id: str
    created_at: str | None = None


class _ResolveUserKeyBody(BaseModel):
    raw_key: str


class _RevokeUserKeyBody(BaseModel):
    key_id: str
    revoked_at: str | None = None


class _RevokeUserKeysForUserBody(BaseModel):
    user_id: str
    revoked_at: str | None = None


class _RestoreUserKeysBody(BaseModel):
    key_ids: list[str]


class _OpenCreativeCreditAccountBody(BaseModel):
    business_slug: str


class _BusinessBootstrapCreditsBody(BaseModel):
    business_slug: str
    operator_user_id: str


class _OpenBillingAccountBody(BaseModel):
    user_id: str
    allowance_included_cents: int | None = None


class _StarterAllowanceBody(BaseModel):
    session_token: str | None = None
    user_id: str | None = None


class _OperatorSubscriptionSyncBody(BaseModel):
    user_id: str
    refresh_live: bool | None = True


class _OperatorPayoutStateBody(BaseModel):
    user_id: str
    refresh_live: bool | None = True


class _OperatorBillingPortalBody(BaseModel):
    user_id: str
    return_url: str


class _OperatorSubscriptionCheckoutBody(BaseModel):
    user_id: str
    plan_id: str
    success_url: str
    cancel_url: str


class _OperatorPayoutConnectBody(BaseModel):
    user_id: str
    return_url: str
    refresh_url: str


class _StripeRequestBody(BaseModel):
    path: str
    params: dict[str, Any] | None = None
    method: str | None = "POST"


class _PostmarkSendBody(BaseModel):
    to_email: str
    subject: str
    text_body: str
    html_body: str | None = None
    message_stream: str | None = None


class _ProductEdgeRouteBody(BaseModel):
    slug: str


class _VercelDomainDeleteBody(BaseModel):
    domain: str


class _StoragePutBody(BaseModel):
    provider: str
    key: str
    data_b64: str
    digest: str


class _StorageKeyBody(BaseModel):
    provider: str
    key: str


class _StorageListBody(BaseModel):
    provider: str
    prefix: str


class _OpenCustodyAccountBody(BaseModel):
    user_id: str
    currency: str | None = "usd"


class _GrantCreativeCreditsBody(BaseModel):
    business_slug: str
    credits: int
    idempotency_key: str
    metadata: dict[str, Any] | None = None
    stripe_ref: str | None = None


class _CreativeCreditCheckoutBody(BaseModel):
    user_id: str
    business_slug: str
    credits: int | None = None
    pack_id: str | None = None
    success_url: str
    cancel_url: str


class _ReconcileCreativeCreditCheckoutBody(BaseModel):
    session_id: str
    business_slug: str | None = None


class _ReserveCreativeCreditsBody(BaseModel):
    business_slug: str
    credits: int
    reservation_key: str
    metadata: dict[str, Any] | None = None


class _CommitCreativeCreditsBody(BaseModel):
    reservation_key: str
    actual_credits: int | None = None
    metadata: dict[str, Any] | None = None


class _ReleaseCreativeCreditsBody(BaseModel):
    reservation_key: str
    metadata: dict[str, Any] | None = None


class _StripeBillingWebhookVerifyBody(BaseModel):
    raw_body: str
    signature: str


class _StripeAppWebhookVerifyBody(BaseModel):
    raw_body: str
    signature: str


class _AppCheckoutReconcileBody(BaseModel):
    session_id: str
    business_slug: str | None = None
    app_user_id: str | None = None
    customer_email: str | None = None


class _Auth0LoginStateBody(BaseModel):
    state: str
    nonce: str
    return_to: str = "/"
    issued_at: int | None = None


class _Auth0CallbackBody(BaseModel):
    code: str
    state: str
    state_token: str
    nonce_token: str
    redirect_uri: str
    now: int | None = None
    state_max_age_seconds: int = 10 * 60
    session_max_age_seconds: int = 12 * 60 * 60


class _Auth0SessionVerifyBody(BaseModel):
    session_token: str
    now: int | None = None


class _ProviderCallBody(BaseModel):
    # Either a pre-minted capability token, OR (session_token + business + action) for the safebox to
    # mint-then-broker in one call. The provider payload is the provider-specific request body.
    token: str | None = None
    session_token: str | None = None
    business: str | None = None
    action: str | None = None
    payload: dict[str, Any] | None = None
    estimate_microusd: int


class _MintTokenBody(BaseModel):
    # Product (sub-user) mint only: session_token + business. Operator/platform sessions go through
    # /v1/operator/session-token, and creative capabilities go through /v1/creative/reserve after a
    # safebox-side credit reserve. action + max_cost_microusd scope the minted capability.
    business: str
    action: str
    max_cost_microusd: int
    session_token: str | None = None
    operator_user_id: str | None = None
    audience: str | None = None
    ttl_seconds: int | None = None


class _OperatorSessionTokenBody(BaseModel):
    # Mint a SESSION-scoped operator capability (audience = operator.session) for the operator/platform
    # plane. The operator MUST own the business (boundary 1, validated via authorize_operator_call).
    # ``max_cost_microusd`` is the per-CALL ceiling the proxy enforces on every metered call under this
    # token; ``ttl_seconds`` is the session lifetime (minutes-to-hours, capped). The token is REUSABLE
    # across calls — the proxy verifies it but does NOT claim a nonce.
    business: str
    operator_user_id: str
    max_cost_microusd: int
    ttl_seconds: int | None = None


class _CreativeReserveBody(BaseModel):
    # Operator-only creative-credit reserve. The operator MUST own the business (boundary 1). action is
    # one of the creative audiences (creative.logo / creative.ugc / creative.static_ad); units scales
    # the fixed per-action price (static-ad = N creatives). The safebox reserves the canonical fixed
    # price on the business's creative-credit ledger and returns a creative capability the client
    # presents to the gated provider routes.
    business: str
    operator_user_id: str
    action: str
    reservation_key: str
    units: int | None = None
    ttl_seconds: int | None = None
    metadata: dict[str, Any] | None = None


class _CreativeFinalizeBody(BaseModel):
    # Commit (settle the reserved credits, optionally refunding reserved-actual) or release (free the
    # whole reservation) keyed on the reservation_key the reserve route used.
    reservation_key: str
    actual_credits: int | None = None
    metadata: dict[str, Any] | None = None


class _CreativeProviderCallBody(BaseModel):
    # A VERIFIED creative capability token (minted by /v1/creative/reserve) + the provider payload. The
    # gate already reserved the action's fixed credits, so this route only resolves the key + forwards.
    # ``token`` is Optional so a missing/empty token surfaces as a clean 401 ``missing_capability`` from
    # the route's own check rather than a 422 validation error.
    token: str | None = None
    payload: dict[str, Any] | None = None


def _allow_tokenless() -> bool:
    """Explicit insecure override for LOCAL TEST RIGS ONLY (the hermetic pytest env scrubs *_TOKEN
    vars, so a local rig's safebox must run tokenless). Same opt-out idiom as
    TAKYON_ALLOW_POSTGRES_OUTSIDE_VPS; never set this on a deployed Safebox host."""
    return str(os.environ.get("TAKYON_SAFEBOX_ALLOW_TOKENLESS") or "").strip().lower() in {"1", "true", "yes", "on"}


def _require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = str(os.environ.get(_SAFEBOX_TOKEN_ENV) or "").strip()
    if not expected:
        if _allow_tokenless():
            return
        # Fail closed: an unconfigured token must never mean "auth disabled" — Safebox safety must
        # not silently degrade to firewall/VPC correctness. Provision TAKYON_SAFEBOX_TOKEN (the
        # service unit loads $TAKYON_HOME/.env) on both the Safebox host and every client plane.
        raise HTTPException(status_code=401, detail="safebox token not configured")
    presented = str(authorization or "").strip()
    want = f"Bearer {expected}"
    if not hmac.compare_digest(presented.encode(), want.encode()):
        raise HTTPException(status_code=401, detail="unauthorized")


def _provider_key_denylist() -> frozenset[str]:
    """Canonical set of PAID-PROVIDER key names the /v1/env HTTP routes must REFUSE to vend
    (GOAL_RULES §1 step 4). Sourced from ``core.provider_key_denylist`` (built from the single
    ``core._API_ENV_ALIASES`` map minus infra providers) so there is no second hand-maintained list.
    Imported lazily, matching the existing in-route ``from . import core`` pattern. Fails CLOSED: if
    the canonical source can't be loaded, deny the known provider-key names below so a load error can
    never silently re-open raw-key vending."""
    try:
        from . import core

        return core.provider_key_denylist()
    except Exception:
        # Conservative fallback mirror of the canonical denylist — never widen vending on error.
        return frozenset(
            {
                "ANTHROPIC_API_KEY", "ANTHROPIC_TOKEN", "CLAUDE_CODE_OAUTH_TOKEN",
                "OPENAI_API_KEY", "OPENAI_KEY",
                "AZURE_OPENAI_API_KEY", "AZURE_OPENAI_KEY",
                "TAVILY_API_KEY",
                "GEMINI_API_KEY", "TAKYON_GEMINI_API_KEY", "GOOGLE_API_KEY",
                "FAL_KEY", "FAL_API_KEY",
                "REPLICATE_API_TOKEN",
                "COMPOSIO_API_KEY",
                "FIRECRAWL_API_KEY", "OPENROUTER_API_KEY", "PARALLEL_API_KEY", "XAI_API_KEY",
                "DATAFORSEO_LOGIN", "DATAFORSEO_PASSWORD",
                "META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN", "META_CAPI_TOKEN",
            }
        )


def _is_denied_provider_key(name: str) -> bool:
    return str(name or "").strip() in _provider_key_denylist()


def _refuse_provider_key(name: str) -> None:
    """Reject a /v1/env read for a PAID-PROVIDER key — a runtime plane must call the safebox broker,
    not pull the raw key over HTTP. 404 (indistinguishable from an absent key; never echoes the
    value)."""
    if _is_denied_provider_key(name):
        raise HTTPException(status_code=404, detail="provider_key_not_vended")


# ── /v1/env egress is an ALLOWLIST, and the safebox's own authority secrets are categorically out ───
# (authority principle / GOAL_RULES §1). A denylist leaks anything you forget to list — which is how the
# HMAC signing key + master token were vending (G1). The read gate is now deny-by-default
# (``_env_egress_allowed``); the write/delete gate hard-refuses the self-authority secrets so they can
# never be overwritten or removed over HTTP either.
_SAFEBOX_SELF_AUTHORITY_FALLBACK: frozenset[str] = frozenset(
    {"TAKYON_CAP_SIGNING_KEY", "TAKYON_SAFEBOX_TOKEN"}
)


def _self_authority_secret_names() -> frozenset[str]:
    try:
        from . import core

        return core.safebox_self_authority_secret_names()
    except Exception:
        return _SAFEBOX_SELF_AUTHORITY_FALLBACK


def _is_sensitive_env_name(name: str) -> bool:
    try:
        return bool(safebox.is_sensitive_env_key(name))
    except Exception:
        n = str(name or "").strip()
        return bool(n) and (
            n == "DATABASE_URL"
            or n.endswith((
                "_KEY", "_TOKEN", "_SECRET", "_PASSWORD",
                "_SECRET_ACCESS_KEY", "_WEBHOOK_SECRET", "_CLIENT_SECRET", "_ACCESS_KEY_ID",
            ))
        )


def _refuse_env_write(name: str) -> None:
    """No runtime plane writes env over HTTP — secrets are provisioned out-of-band on the safebox host.
    So POST/DELETE /v1/env refuse ANY sensitive key (provider key, self-authority/verification secret,
    or any *_KEY/_TOKEN/_SECRET/_PASSWORD), in any case — closing the DATABASE_URL clobber/DoS, the
    provider-key swap (keys the safebox's own proxies resolve locally), and the lowercase-500 vector.
    403."""
    n = str(name or "").strip()
    if not n:
        raise HTTPException(status_code=403, detail="env_write_forbidden")
    for cand in {n, n.upper()}:
        if (
            cand in _self_authority_secret_names()
            or _is_denied_provider_key(cand)
            or _is_sensitive_env_name(cand)
        ):
            raise HTTPException(status_code=403, detail="env_write_forbidden")


def _env_egress_allowed(name: str) -> bool:
    """/v1/env READ allowlist (deny-by-default). Delegates to ``core.env_egress_allowed``; on a core
    import failure, fails closed for the self-authority secrets + paid-provider keys while still serving
    the known infra names/prefixes so a transient error can't black out the runtime's DB/Stripe/Auth0
    fetches."""
    try:
        from . import core

        return core.env_egress_allowed(name)
    except Exception:
        # Fail closed: a core import hiccup must not re-open egress. Deny self-authority + provider keys,
        # admit only the few critical infra names to bootstrap (exact only, no prefixes).
        n = str(name or "").strip()
        if not n or n in _SAFEBOX_SELF_AUTHORITY_FALLBACK or _is_denied_provider_key(n):
            return False
        return n in {
            "DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING",
        }


_RUNTIME_DATABASE_EGRESS_NAMES: frozenset[str] = frozenset(
    {"DATABASE_URL", "POSTGRES_URL", "POSTGRES_PRISMA_URL", "POSTGRES_URL_NON_POOLING"}
)
_RUNTIME_DATABASE_URL_ENV = "TAKYON_RUNTIME_DATABASE_URL"


def _env_egress_value(name: str) -> str:
    """Resolve an allowlisted value for runtime-plane egress.

    The safebox itself keeps its owner DATABASE_URL locally so provider proxies, webhook processors, and
    money gates can still run with authority. Runtime planes must receive the least-privilege database
    DSN after the G3 cutover, so database aliases egress as TAKYON_RUNTIME_DATABASE_URL when configured.
    """
    n = str(name or "").strip()
    if n in _RUNTIME_DATABASE_EGRESS_NAMES:
        runtime_database_url = str(
            os.environ.get(_RUNTIME_DATABASE_URL_ENV)
            or safebox.load_env().get(_RUNTIME_DATABASE_URL_ENV)
            or ""
        ).strip()
        if runtime_database_url:
            return runtime_database_url
    try:
        return safebox.read_env_backed_value(n)
    except KeyError:
        return str(os.environ.get(n) or safebox.load_env().get(n) or "").strip()


def _first_env_egress_value(names: Iterable[str]) -> str:
    allowed = [str(name or "").strip() for name in names if _env_egress_allowed(str(name or "").strip())]
    if not allowed:
        raise HTTPException(status_code=404, detail="not_vendable")
    for name in allowed:
        value = _env_egress_value(name)
        if value:
            return value
    return ""


def _anthropic_key_resolver(_scope: CapabilityScope) -> str:
    """Resolve the SHARED Anthropic key LOCALLY on the safebox (never returned to a caller)."""
    from . import ai_provider

    return ai_provider.anthropic_key()


def _anthropic_provider_caller(payload: dict[str, Any]):
    """Build the (scope, key) -> (key_free_result, actual_microusd) caller for Anthropic Messages.

    Prices the realized response from the canonical pricing table; cached tokens bill at their own
    rates. The returned result is the raw provider JSON (key-free)."""
    from . import ai_provider

    built_payload, model, estimated_input_tokens = ai_provider.anthropic_payload(payload or {})

    def _call(_scope: CapabilityScope, key: str):
        raw = ai_provider.call_anthropic(built_payload, key)
        usage = raw.get("usage") or {}
        in_tok = int(usage.get("input_tokens") or estimated_input_tokens)
        out_tok = int(usage.get("output_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        # Settle the BILLED amount (realized provider cost + usage markup), matching the usage rail's
        # pricing contract, the local ai_gateway settle, and the server-side reserve estimate — NOT the
        # bare realized cost, which would silently drop the markup and under-charge every brokered call.
        _realized, billed = ai_provider.billed_microusd_cost(
            model, in_tok, out_tok, cache_read_tokens=cache_read, cache_write_tokens=cache_write
        )
        return raw, int(billed)

    return _call


def _tavily_key_resolver(_scope: CapabilityScope) -> str:
    """Resolve the SHARED Tavily key LOCALLY on the safebox (never returned to a caller)."""
    from . import ai_provider

    return ai_provider.tavily_key()


def _tavily_provider_caller(payload: dict[str, Any]):
    """Build the (scope, key) -> (key_free_result, actual_microusd) caller for Tavily search/extract.

    Tavily is a per-REQUEST provider: cost comes from ``tavily_request_microusd`` (fail-closed for any
    unpriced operation). The endpoint/operation are taken from the payload."""
    from . import ai_provider

    body = dict(payload or {})
    endpoint = str(body.pop("endpoint", None) or body.get("operation") or "search").strip("/").lower()
    operation = str(body.pop("operation", None) or endpoint).strip().lower()
    units = max(1, int(body.pop("units", 1) or 1))

    def _call(_scope: CapabilityScope, key: str):
        actual_microusd = ai_provider.tavily_request_microusd(operation, units=units)
        raw = ai_provider.call_tavily(endpoint, body, key)
        return raw, int(actual_microusd)

    return _call


def _gemini_image_key_resolver(_scope: CapabilityScope) -> str:
    """Resolve the SHARED Gemini image key LOCALLY on the safebox (never returned to a caller)."""
    from . import creative_gateway

    return creative_gateway._resolve_gemini_image_key()


def _gemini_image_provider_caller(payload: dict[str, Any]):
    """Build the (scope, key) -> (key_free_result, actual_microusd) caller for Gemini image gen.

    Gemini image is a per-REQUEST provider; cost is the canonical request price for the image model
    (fail-closed if unpriced). The image bytes are returned base64-encoded so the result is JSON-safe
    and KEY-FREE."""
    import base64 as _b64
    from decimal import ROUND_CEILING, Decimal

    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

    from . import creative_gateway

    prompt = str((payload or {}).get("prompt") or "").strip()

    def _call(_scope: CapabilityScope, key: str):
        priced = estimate_usage_cost(
            creative_gateway._GEMINI_IMAGE_MODEL,
            CanonicalUsage(request_count=1),
            provider="gemini",
        )
        if priced.amount_usd is None:
            # Fail closed: an unpriced image action may never spend (mirrors the Anthropic/Tavily
            # fail-closed pricing contract).
            raise BrokerLedgerError("gemini_image_pricing_unavailable")
        actual_microusd = int(
            (priced.amount_usd * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING)
        )
        # Secret boundary: the safebox makes ONLY the keyed provider call and returns the RAW image
        # bytes. The alpha-key / PNG post-process is a pure pixel transform (no secret) and runs on
        # the runtime plane after the broker returns — never here (the safebox has no numpy).
        raw_bytes = creative_gateway._gemini_generate_image_raw(api_key=key, prompt=prompt)
        result = {"image_base64": _b64.b64encode(raw_bytes).decode("ascii"), "format": "raw"}
        return result, int(actual_microusd)

    return _call


def _anthropic_estimate(payload: dict[str, Any]):
    """Build the SERVER-side estimate closure ``(scope) -> int`` for an Anthropic Messages call.

    The estimate mirrors the provider caller's own pricing source: the billed cost of the canonical
    payload's estimated input tokens + the requested max_tokens (the worst-case output), so a client
    cannot pass a tiny ``estimate_microusd`` to duck the per-user cap and then run an expensive call.
    Fail-closed: an unpriced model raises ``BrokerLedgerError`` before any reserve."""
    from . import ai_provider

    _built, model, estimated_input_tokens = ai_provider.anthropic_payload(payload or {})
    max_tokens = int((_built or {}).get("max_tokens") or 0)

    def _estimate(_scope: CapabilityScope) -> int:
        try:
            _realized, billed = ai_provider.billed_microusd_cost(
                model, int(estimated_input_tokens), int(max_tokens)
            )
        except ai_provider.AnthropicPricingUnavailable as exc:
            raise BrokerLedgerError("anthropic_pricing_unavailable") from exc
        return int(billed)

    return _estimate


def _tavily_estimate(payload: dict[str, Any]):
    """Build the SERVER-side estimate closure for a Tavily search/extract call: the EXACT per-request
    price for the resolved operation/units (the same figure the provider caller settles). Fail-closed:
    an unpriced operation raises ``BrokerLedgerError`` before any reserve."""
    from . import ai_provider

    body = dict(payload or {})
    endpoint = str(body.get("endpoint") or body.get("operation") or "search").strip("/").lower()
    operation = str(body.get("operation") or endpoint).strip().lower()
    units = max(1, int(body.get("units") or 1))

    def _estimate(_scope: CapabilityScope) -> int:
        try:
            return int(ai_provider.tavily_request_microusd(operation, units=units))
        except ai_provider.TavilyPricingUnavailable as exc:
            raise BrokerLedgerError("tavily_pricing_unavailable") from exc

    return _estimate


def _gemini_image_estimate(payload: dict[str, Any]):
    """Build the SERVER-side estimate closure for a Gemini image call: the EXACT canonical request
    price for the image model (the same figure the provider caller settles). Fail-closed: an unpriced
    image action raises ``BrokerLedgerError`` before any reserve."""
    from decimal import ROUND_CEILING, Decimal

    from agent.usage_pricing import CanonicalUsage, estimate_usage_cost

    from . import creative_gateway

    def _estimate(_scope: CapabilityScope) -> int:
        priced = estimate_usage_cost(
            creative_gateway._GEMINI_IMAGE_MODEL,
            CanonicalUsage(request_count=1),
            provider="gemini",
        )
        if priced.amount_usd is None:
            raise BrokerLedgerError("gemini_image_pricing_unavailable")
        return int((priced.amount_usd * Decimal("1000000")).to_integral_value(rounding=ROUND_CEILING))

    return _estimate


# ── Creative-credit provider routes (logo / UGC / static-ad) ──────────────────────────────────────
# Upstream provider hosts for the gated creative forwards. Kept here on the safebox (never in the
# business runtime) because only the safebox holds the key and forwards. Mirrors the constants that
# used to live in safebox_provider_proxy.py before the ungated routes were deleted.
_OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
_FAL_BASE_URL = "https://fal.run"
# Long-running FAL models (Kling video i2v) routinely generate for >3 min, which exceeds the
# synchronous fal.run gateway/timeout and 502s. Those models MUST go through the FAL queue API
# (submit -> poll status -> fetch result): each HTTP hop stays short while the generation runs
# server-side. See _forward_fal_queue / _creative_fal_caller.
_FAL_QUEUE_BASE_URL = "https://queue.fal.run"
_CREATIVE_UPSTREAM_TIMEOUT_S = 180.0
# Max wall-clock the safebox waits for a queued FAL render to COMPLETE, and the poll cadence. The
# total budget stays below the runtime subprocess's proxy timeout (pipeline.py _PROXY_TIMEOUT_S) so
# the subprocess hears a clean result/refusal rather than its own transport timeout.
_FAL_QUEUE_TOTAL_BUDGET_S = 840.0
_FAL_QUEUE_POLL_INTERVAL_S = 4.0


def _openai_image_key() -> str:
    """The SHARED OpenAI key, resolved LOCALLY on the safebox via the canonical alias
    (``core._API_ENV_ALIASES['openai']`` = ``OPENAI_API_KEY``). Returns "" when unconfigured so the
    creative route can fail closed with a clear ``openai_unconfigured`` before any upstream call."""
    from . import safebox

    try:
        return str(safebox.first_env_backed_value("OPENAI_API_KEY") or "").strip()
    except Exception:
        return ""


def _fal_key() -> str:
    """The SHARED FAL key, resolved LOCALLY on the safebox via the canonical aliases
    (``core._API_ENV_ALIASES['fal']`` = ``FAL_KEY`` / ``FAL_API_KEY``)."""
    from . import safebox

    try:
        return str(safebox.first_env_backed_value("FAL_KEY", "FAL_API_KEY") or "").strip()
    except Exception:
        return ""


def _forward_json_post(url: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """POST ``payload`` to ``url`` with the LOCALLY-injected auth ``headers`` and return the parsed JSON
    response. The provider key lives only in ``headers`` (the outbound request); it never appears in the
    returned body. Raises ``BrokerLedgerError`` on transport/HTTP failure (the route maps it to a clean
    502/503) and NEVER echoes the request auth header or the raw upstream body verbatim."""
    import httpx

    try:
        with httpx.Client(timeout=_CREATIVE_UPSTREAM_TIMEOUT_S) as client:
            resp = client.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise BrokerLedgerError("provider_unreachable") from exc
    text = resp.text
    if resp.status_code >= 400:
        # Sanitized: the truncated body is the upstream RESPONSE (no request key) — never the auth header.
        raise BrokerLedgerError(f"provider_http_{int(resp.status_code)}")
    try:
        return json.loads(text) if text.strip() else {}
    except (ValueError, TypeError):
        return {}


def _forward_fal_queue(path: str, *, headers: dict[str, str], payload: dict[str, Any]) -> dict[str, Any]:
    """Submit a FAL request to the QUEUE API and block until it completes, returning the KEY-FREE
    result JSON.

    Long-running models (Kling video i2v) exceed the synchronous ``fal.run`` gateway, so we submit to
    ``queue.fal.run/<path>``, poll the returned ``status_url`` until terminal, then GET the
    ``response_url``. Every individual HTTP hop is short (well under ``_CREATIVE_UPSTREAM_TIMEOUT_S``);
    the wait is the bounded poll loop. The provider key lives only in ``headers`` and never appears in
    the returned body. Same failure contract as ``_forward_json_post``: ``BrokerLedgerError`` on
    transport/HTTP/queue failure (the route maps it to a clean 502/503)."""
    import time

    import httpx

    submit_url = f"{_FAL_QUEUE_BASE_URL}/{path}"
    try:
        with httpx.Client(timeout=_CREATIVE_UPSTREAM_TIMEOUT_S) as client:
            resp = client.post(submit_url, headers=headers, json=payload)
            if resp.status_code >= 400:
                raise BrokerLedgerError(f"provider_http_{int(resp.status_code)}")
            submit = json.loads(resp.text) if resp.text.strip() else {}
            status_url = str(submit.get("status_url") or "").strip()
            response_url = str(submit.get("response_url") or "").strip()
            if not response_url:
                # A submit with neither a response_url nor a status_url is not a queue response we can
                # follow — surface it rather than hang.
                raise BrokerLedgerError("provider_queue_no_response_url")

            deadline = time.monotonic() + _FAL_QUEUE_TOTAL_BUDGET_S
            while True:
                if time.monotonic() > deadline:
                    raise BrokerLedgerError("provider_queue_timeout")
                time.sleep(_FAL_QUEUE_POLL_INTERVAL_S)
                poll = client.get(status_url or response_url, headers=headers)
                if poll.status_code >= 400:
                    raise BrokerLedgerError(f"provider_http_{int(poll.status_code)}")
                state = json.loads(poll.text) if poll.text.strip() else {}
                if not status_url:
                    # No status_url to track: a 200 on the response_url itself means the result is ready.
                    return state
                status = str(state.get("status") or "").upper()
                if status in {"COMPLETED", "OK", "SUCCESS"}:
                    break
                if status in {"FAILED", "ERROR", "CANCELED", "CANCELLED"}:
                    raise BrokerLedgerError("provider_queue_failed")
                # IN_QUEUE / IN_PROGRESS -> keep polling.

            result = client.get(response_url, headers=headers)
            if result.status_code >= 400:
                raise BrokerLedgerError(f"provider_http_{int(result.status_code)}")
            try:
                return json.loads(result.text) if result.text.strip() else {}
            except (ValueError, TypeError):
                return {}
    except httpx.HTTPError as exc:
        raise BrokerLedgerError("provider_unreachable") from exc


def _creative_gemini_caller(payload: dict[str, Any]):
    """Resolve the Gemini image key LOCALLY and render a logo PNG; return a KEY-FREE base64 result. The
    creative-credit gate already reserved the action's fixed price (the reserve route), so this caller
    does NOT meter — it only resolves the key and forwards."""
    import base64 as _b64

    from . import creative_gateway

    prompt = str((payload or {}).get("prompt") or "").strip()
    if not prompt:
        raise ValueError("missing_prompt")

    def _call(_scope: "CapabilityScope"):
        key = creative_gateway._resolve_gemini_image_key()
        if not key:
            raise BrokerLedgerError("gemini_unconfigured")
        # Secret boundary: keyed provider call only; return RAW bytes. The runtime alpha-keys after
        # the broker returns (no numpy/PIL build on the secret host).
        raw_bytes = creative_gateway._gemini_generate_image_raw(api_key=key, prompt=prompt)
        return {"image_base64": _b64.b64encode(raw_bytes).decode("ascii"), "format": "raw"}

    return _call


def _creative_openai_images_caller(payload: dict[str, Any]):
    """Resolve the OpenAI key LOCALLY and forward an images/generations request; return the KEY-FREE
    upstream JSON. Key-free: the key is injected ONLY into the outbound Authorization header."""
    body = dict(payload or {})

    def _call(_scope: "CapabilityScope"):
        key = _openai_image_key()
        if not key:
            raise BrokerLedgerError("openai_unconfigured")
        headers = {"Authorization": f"Bearer {key}", "content-type": "application/json"}
        return _forward_json_post(_OPENAI_IMAGES_URL, headers=headers, payload=body)

    return _call


def _creative_fal_caller(fal_path: str):
    """Build the FAL forwarder for a given FAL ``path`` (e.g. ``fal-ai/kling-video/...``). Resolves the
    FAL key LOCALLY and forwards to ``https://fal.run/<path>``; returns the KEY-FREE upstream JSON."""
    path = str(fal_path or "").strip().strip("/")

    def _build(payload: dict[str, Any]):
        body = dict(payload or {})

        def _call(_scope: "CapabilityScope"):
            if not path:
                raise ValueError("missing_fal_path")
            key = _fal_key()
            if not key:
                raise BrokerLedgerError("fal_unconfigured")
            headers = {"Authorization": f"Key {key}", "content-type": "application/json"}
            # Route through the FAL QUEUE API: Kling video renders run for minutes and 502 on the
            # synchronous fal.run endpoint. _forward_fal_queue submits, polls, and fetches the result
            # with short per-hop HTTP calls, returning the same KEY-FREE provider JSON.
            return _forward_fal_queue(path, headers=headers, payload=body)

        return _call

    return _build


def _creative_provider_route(
    body: "_CreativeProviderCallBody",
    *,
    allowed_audiences: "frozenset[str]",
    caller_builder,
) -> dict[str, Any]:
    """Shared body for the gated creative PROVIDER routes (gemini/openai/fal).

    The creative-credit gate is reserved ONCE per action via ``/v1/creative/reserve`` (which hands the
    operator a creative capability). This route therefore only VERIFIES that capability (signature +
    one of ``allowed_audiences`` + not-expired -> the AUTHORITATIVE scope), then resolves the provider
    key LOCALLY and forwards, returning a KEY-FREE result. It does NOT reserve/commit per call (that
    would multiply-charge the fixed action price) and the token is NOT single-use (one action makes
    several provider calls). Fails closed: a bad/expired/wrong-audience token is 401; an unconfigured
    key / unreachable provider is 503/502 BEFORE leaking anything."""
    from .safebox_capability import CapabilityError, verify_capability

    signing_key = _cap_signing_key()
    if not signing_key:
        raise HTTPException(status_code=503, detail="capability_signing_unconfigured")

    token = str(body.token or "").strip()
    if not token:
        raise HTTPException(status_code=401, detail="missing_capability")

    now = int(time.time())
    scope = None
    last_exc: CapabilityError | None = None
    for audience in allowed_audiences:
        try:
            scope, _nonce, _exp = verify_capability(
                token, signing_key=signing_key, expected_audience=audience, now=now
            )
            break
        except CapabilityError as exc:
            last_exc = exc
            scope = None
    if scope is None:
        raise HTTPException(
            status_code=401, detail=f"capability_invalid: {last_exc}" if last_exc else "capability_invalid"
        )

    try:
        provider_caller = caller_builder(body.payload or {})
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_provider_payload") from exc

    try:
        return provider_caller(scope)
    except BrokerLedgerError as exc:
        message = str(exc)
        if message.endswith("_unconfigured"):
            raise HTTPException(status_code=503, detail=message) from exc
        raise HTTPException(status_code=502, detail="provider_error") from exc
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_provider_payload") from exc
    except RuntimeError as exc:
        # A provider/SDK failure (e.g. the Gemini SDK) — never leak the upstream body/key.
        raise HTTPException(status_code=502, detail="provider_error") from exc


def _broker_provider_route(
    body: "_ProviderCallBody",
    *,
    audience: str,
    provider: str,
    key_resolver,
    caller_builder,
    estimate_builder,
) -> dict[str, Any]:
    """Shared body for the three provider routes: resolve/mint the token, then hand the whole brokered
    call to ``safebox_broker.handle_provider_request`` so verify -> ceiling -> reserve -> single-use
    -> key-local -> settle/release all happen INSIDE the safebox process. The reserve is gated on
    ``max(server_estimate, client_estimate)`` (``estimate_builder`` mirrors the provider's own pricing
    source) so a client cannot pass a tiny estimate to duck the cap. Returns the KEY-FREE result."""
    from . import app_usage, safebox_broker
    from .safebox_capability import CapabilityError

    signing_key = _cap_signing_key()
    if not signing_key:
        raise HTTPException(status_code=503, detail="capability_signing_unconfigured")

    now = int(time.time())
    token = str(body.token or "").strip()
    if not token:
        # No pre-minted token: mint one here from the supplied identity, then broker it. The
        # entitlement/ceiling decision and the provider invocation must be the SAME action, so the
        # supplied action MUST map to THIS route's audience before we mint — otherwise a caller could
        # mint a cheap action and broker an expensive provider under it.
        inline_action = str(body.action or "").strip()
        if _ACTION_AUDIENCE_DEFAULTS.get(inline_action) != audience:
            raise HTTPException(status_code=400, detail="action_audience_mismatch")
        token = _mint_capability_token(
            business=str(body.business or ""),
            action=inline_action,
            max_cost_microusd=int(body.estimate_microusd),
            session_token=body.session_token,
            operator_user_id=None,
            audience=audience,
            ttl_seconds=_CAP_TTL_SECONDS,
            now=now,
        )

    ledger = _UsageLedgerAdapter(provider=provider)

    # The builders parse/validate the provider payload (e.g. anthropic_payload rejects an empty/bad
    # messages body), so they can raise on a malformed request. Guard them in their OWN narrow try so a
    # bad payload surfaces as a clean 400 — never a 500, and never shadowing the broker handler chain
    # below (a pre-minted-token call skips the inline-mint that would otherwise 400 first).
    try:
        provider_caller = caller_builder(body.payload or {})
        estimate_fn = estimate_builder(body.payload or {})
    except (ValueError, KeyError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="invalid_provider_payload") from exc

    try:
        return safebox_broker.handle_provider_request(
            token=token,
            signing_key=signing_key,
            audience=audience,
            now=now,
            nonce_store=_PgNonceStore(),
            ledger=ledger,
            key_resolver=key_resolver,
            provider_caller=provider_caller,
            estimate_microusd=int(body.estimate_microusd),
            estimate_fn=estimate_fn,
        )
    except CapabilityError as exc:
        raise HTTPException(status_code=401, detail=f"capability_invalid: {exc}") from exc
    except safebox_broker.BrokerError as exc:
        raise HTTPException(status_code=402, detail=str(exc)) from exc
    except (app_usage.AppBudgetInactive, app_usage.AppBudgetExceeded, app_usage.AppUserBudgetExceeded) as exc:
        # The ONE money gate refused inside the broker reserve — business budget inactive/exhausted or
        # the per-user weekly cap. Surface a clean 402 (out-of-funds), never a 500; the structured class
        # name + message lets the client map it back to its canonical budget shape.
        raise HTTPException(
            status_code=402, detail={"error": type(exc).__name__, "detail": str(exc)}
        ) from exc
    except app_usage.AppUserNotFound as exc:
        raise HTTPException(status_code=400, detail="unknown_app_user") from exc
    except (RuntimeError, BrokerLedgerError) as exc:
        # A provider/ledger failure: never leak the upstream provider body. A fail-closed
        # *_unconfigured (missing key) or *_pricing_unavailable (unpriced action) is a 503 with its
        # own clear code; anything else is a generic 502.
        message = str(exc)
        if message.endswith("_unconfigured") or message.endswith("_pricing_unavailable"):
            raise HTTPException(status_code=503, detail=message) from exc
        raise HTTPException(status_code=502, detail="provider_error") from exc


def _mint_capability_token(
    *,
    business: str,
    action: str,
    max_cost_microusd: int,
    session_token: str | None,
    operator_user_id: str | None,
    audience: str,
    ttl_seconds: int,
    now: int,
) -> str:
    """Validate identity (boundary 2 + 1 for product, boundary 1 for operator) then mint a signed
    capability for the AUTHORITATIVE scope. Raises HTTPException on bad identity / unconfigured key."""
    from .safebox_authz import (
        AuthzError,
        authorize_operator_call,
        authorize_product_call,
    )

    signing_key = _cap_signing_key()
    if not signing_key:
        raise HTTPException(status_code=503, detail="capability_signing_unconfigured")

    business = str(business or "").strip()
    action = str(action or "").strip()
    if not business or not action:
        raise HTTPException(status_code=400, detail="missing_identity")

    has_session = bool(str(session_token or "").strip())
    has_operator = bool(str(operator_user_id or "").strip())
    if has_session == has_operator:
        # Exactly one identity shape: a product (sub-user) session OR an operator user, never both,
        # never neither.
        raise HTTPException(status_code=400, detail="ambiguous_identity")

    try:
        with _safebox_db_conn() as conn:
            if has_session:
                scope = authorize_product_call(
                    conn,
                    business_slug=business,
                    session_token=str(session_token or ""),
                    action=action,
                    max_cost_microusd=int(max_cost_microusd),
                )
            else:
                scope = authorize_operator_call(
                    conn,
                    business_slug=business,
                    operator_user_id=str(operator_user_id or ""),
                    action=action,
                    max_cost_microusd=int(max_cost_microusd),
                )
    except AuthzError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc

    return mint_capability(
        scope,
        signing_key=signing_key,
        audience=audience,
        nonce=str(uuid.uuid4()),
        issued_at=int(now),
        ttl_seconds=int(ttl_seconds),
    )


def build_safebox_app() -> FastAPI:
    app = FastAPI(title="Takyon Safebox")

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/env/snapshot")
    def env_snapshot(authorization: str | None = Header(default=None)) -> dict[str, dict[str, str]]:
        _require_internal_token(authorization)
        # Allowlist the bulk snapshot too — the runtime planes get only the infra secrets they need;
        # provider keys, the signing key, and the master token are never present in the snapshot.
        snapshot = {
            name: _env_egress_value(name)
            for name in safebox.sensitive_env_snapshot()
            if _env_egress_allowed(name)
        }
        return {"snapshot": snapshot}

    @app.get("/v1/env/{key}")
    def read_env_value(key: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        # Egress is a deny-by-default ALLOWLIST of infra secrets. The self-authority secrets (signing
        # key, master token) and every paid-provider key are NOT on it, so they 404 here (no value,
        # indistinguishable from absent) — closing the G1 leak structurally rather than by denylist.
        if not _env_egress_allowed(key):
            raise HTTPException(status_code=404, detail="not_vendable")
        return {"value": _env_egress_value(key)}

    @app.post("/v1/env/first")
    def first_env_value(body: _FirstEnvBody, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        # Keep only allowlisted infra names, then resolve the first present value. A request for only
        # non-allowlisted keys (provider keys, the signing key, the master token, …) refuses (404).
        return {"value": _first_env_egress_value(body.keys or [])}

    @app.post("/v1/env/{key}")
    def save_env_value(key: str, body: _EnvValueBody, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        _refuse_env_write(key)
        safebox.save_env_backed_value(key, body.value)
        return {"ok": True}

    @app.delete("/v1/env/{key}")
    def delete_env_value(key: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        _refuse_env_write(key)
        return {"removed": safebox.remove_env_backed_value(key)}

    @app.get("/v1/env")
    def env_keys(
        sensitive_only: str = Query(default="1"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[str]]:
        _require_internal_token(authorization)
        # Names only (no values), and only the allowlisted infra names — a client never even sees the
        # provider keys, the signing key, or the master token advertised as vendable through this route.
        keys = [
            name
            for name in safebox.list_env_backed_keys(sensitive_only=sensitive_only != "0")
            if _env_egress_allowed(name)
        ]
        return {"keys": keys}

    @app.post("/v1/auth0/login-state")
    def auth0_login_state(
        body: _Auth0LoginStateBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            return safebox.auth0_login_state(
                state=body.state,
                nonce=body.nonce,
                return_to=body.return_to,
                issued_at=body.issued_at,
            )
        except safebox.Auth0AuthorityUnconfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except safebox.Auth0AuthorityRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.post("/v1/auth0/callback")
    def auth0_callback(
        body: _Auth0CallbackBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            return safebox.auth0_exchange_callback(
                code=body.code,
                state=body.state,
                state_token=body.state_token,
                nonce_token=body.nonce_token,
                redirect_uri=body.redirect_uri,
                now=body.now,
                state_max_age_seconds=body.state_max_age_seconds,
                session_max_age_seconds=body.session_max_age_seconds,
            )
        except safebox.Auth0AuthorityUnconfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        except safebox.Auth0AuthorityRejected as exc:
            raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    @app.post("/v1/auth0/session/verify")
    def auth0_session_verify(
        body: _Auth0SessionVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            user = safebox.auth0_verify_session(
                session_token=body.session_token,
                now=body.now,
            )
        except safebox.Auth0AuthorityUnconfigured as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        if not user:
            return {"authenticated": False}
        return {"authenticated": True, "user": user}

    @app.post("/v1/user-api-keys/register")
    def register_user_key(
        body: _RegisterUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return {
            "record": safebox.register_user_api_key(
                body.user_id,
                body.raw_key,
                key_id=body.key_id,
                created_at=body.created_at,
            )
        }

    @app.post("/v1/user-api-keys/resolve")
    def resolve_user_key(
        body: _ResolveUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return {"record": safebox.resolve_user_api_key(body.raw_key)}

    @app.post("/v1/user-api-keys/revoke")
    def revoke_user_key(
        body: _RevokeUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        return {"revoked": safebox.revoke_user_api_key(body.key_id, revoked_at=body.revoked_at)}

    @app.post("/v1/user-api-keys/revoke-for-user")
    def revoke_user_keys_for_user(
        body: _RevokeUserKeysForUserBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[str]]:
        _require_internal_token(authorization)
        return {
            "revoked_ids": safebox.revoke_user_api_keys_for_user(
                body.user_id,
                revoked_at=body.revoked_at,
            )
        }

    @app.post("/v1/user-api-keys/restore")
    def restore_user_keys(
        body: _RestoreUserKeysBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox.restore_user_api_keys(body.key_ids)
        return {"ok": True}

    @app.delete("/v1/user-api-keys/{key_id}")
    def delete_user_key(key_id: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        return {"deleted": safebox.delete_user_api_key(key_id)}

    @app.post("/v1/billing/accounts/open")
    def open_billing_account(
        body: _OpenBillingAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        # Account-open is allowed only as a zero-balance provisioning primitive. Any non-zero amount
        # is a grant and must go through starter/subscription/webhook policy.
        if int(body.allowance_included_cents or 0) != 0:
            raise HTTPException(status_code=400, detail="billing_open_must_not_mint_allowance")
        safebox._local_open_billing_account(None, body.user_id, allowance_included_cents=0)
        return {"ok": True}

    @app.post("/v1/billing/starter-allowance")
    def grant_starter_allowance(
        body: _StarterAllowanceBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        user = safebox.auth0_verify_session(session_token=str(body.session_token or ""))
        if not isinstance(user, dict):
            raise HTTPException(status_code=403, detail="starter_session_required")
        auth0_sub = str(user.get("sub") or "").strip()
        if not auth0_sub:
            raise HTTPException(status_code=403, detail="starter_session_required")
        with _safebox_db_conn() as conn:
            row = conn.execute("select id from users where auth0_sub = %s", (auth0_sub,)).fetchone()
        if row is None:
            raise HTTPException(status_code=404, detail="starter_user_not_found")
        user_id = str(row[0])
        requested_user = str(body.user_id or "").strip()
        if requested_user and requested_user != user_id:
            raise HTTPException(status_code=403, detail="starter_user_mismatch")
        included = safebox._local_grant_starter_allowance(
            None,
            user_id,
            idempotency_subject=f"auth0:{auth0_sub}",
        )
        return {"ok": True, "user_id": user_id, "included_cents": int(included)}

    @app.post("/v1/billing/operator-subscription/sync")
    def sync_operator_subscription_allowance(
        body: _OperatorSubscriptionSyncBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from .control_api import sync_operator_subscription_allowance as _sync

        with _safebox_db_conn() as conn:
            state = _sync(conn, body.user_id, refresh_live=bool(body.refresh_live))
        return safebox._operator_subscription_state_payload(state)

    @app.post("/v1/operator/payouts/state")
    def operator_payout_state(
        body: _OperatorPayoutStateBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            return safebox.get_operator_payout_state(
                body.user_id,
                refresh_live=bool(body.refresh_live),
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="user_not_found") from exc
        except Exception as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="payout_state_unconfigured") from exc
            raise HTTPException(status_code=502, detail=message) from exc

    @app.post("/v1/operator/billing/portal")
    def operator_billing_portal(
        body: _OperatorBillingPortalBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            session = safebox.create_operator_billing_portal(
                body.user_id,
                return_url=body.return_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="user_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="billing_portal_unconfigured") from exc
            raise HTTPException(status_code=502, detail=message) from exc
        return {
            "portal_url": session.get("url"),
            "customer_id": session.get("customer"),
        }

    @app.post("/v1/operator/billing/subscription/checkout")
    def operator_subscription_checkout(
        body: _OperatorSubscriptionCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            return safebox.create_operator_subscription_checkout(
                body.user_id,
                plan_id=body.plan_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="unknown_operator_plan") from exc
        except ValueError as exc:
            if "operator_email_unavailable" in str(exc):
                raise HTTPException(status_code=409, detail="operator_email_unavailable") from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="operator_subscription_unconfigured") from exc
            raise HTTPException(status_code=502, detail=message) from exc

    @app.post("/v1/operator/payouts/connect")
    def operator_payout_connect(
        body: _OperatorPayoutConnectBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            return safebox.create_operator_payout_connect(
                body.user_id,
                return_url=body.return_url,
                refresh_url=body.refresh_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="user_not_found") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="payout_connect_unconfigured") from exc
            raise HTTPException(status_code=502, detail=message) from exc

    @app.post("/v1/stripe/request")
    def stripe_request(
        body: _StripeRequestBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        path, method, params = _normalize_stripe_request(body.path, body.method or "POST", body.params)
        try:
            if path == "checkout/sessions" and method == "POST":
                price_id = str(params.get("line_items[0][price]") or "").strip()
                if price_id:
                    price = safebox.stripe_request(f"prices/{price_id}", {}, method="GET")
                    business = _require_takyon_app_stripe_object(price, require_source=True)
                    if business != _metadata_value(params, "business"):
                        raise HTTPException(status_code=403, detail="stripe_price_scope_mismatch")
            if path.startswith("subscriptions/"):
                subscription = safebox.stripe_request(path, {}, method="GET")
                _require_takyon_app_stripe_object(subscription)
                if method == "GET":
                    return subscription
            result = safebox.stripe_request(path, params, method=method)
            if method == "GET" and path.startswith("checkout/sessions/"):
                _require_takyon_app_stripe_object(result, require_source=True)
            return result
        except Exception as exc:
            if isinstance(exc, HTTPException):
                raise
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="stripe_unconfigured") from exc
            raise HTTPException(status_code=502, detail="stripe_error") from exc

    @app.post("/v1/postmark/send")
    def postmark_send(
        body: _PostmarkSendBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        if "@" not in str(body.to_email or ""):
            raise HTTPException(status_code=400, detail="invalid_recipient")
        if not str(body.subject or "").strip() or not str(body.text_body or "").strip():
            raise HTTPException(status_code=400, detail="missing_email_body")
        _require_magic_link_email(body)
        try:
            return safebox.send_postmark_email(
                to_email=body.to_email,
                subject=body.subject,
                text_body=body.text_body,
                html_body=body.html_body,
                message_stream=body.message_stream,
            )
        except Exception as exc:
            message = str(exc)
            if "postmark_unconfigured" in message or "POSTMARK_SERVER_TOKEN" in message:
                raise HTTPException(status_code=503, detail="postmark_unconfigured") from exc
            raise HTTPException(status_code=502, detail="postmark_error") from exc

    @app.post("/v1/cloudflare/product-edge-route")
    def cloudflare_product_edge_route(
        body: _ProductEdgeRouteBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            slug = _require_existing_business(body.slug)
            return safebox.ensure_product_edge_route(slug)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            message = str(exc)
            if "CLOUDFLARE_API_TOKEN" in message:
                raise HTTPException(status_code=503, detail="cloudflare_unconfigured") from exc
            raise HTTPException(status_code=502, detail="cloudflare_error") from exc

    @app.post("/v1/vercel/domain/delete")
    def vercel_domain_delete(
        body: _VercelDomainDeleteBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            _domain_business_slug(body.domain)
            return safebox.delete_vercel_project_domain(body.domain)
        except HTTPException:
            raise
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            message = str(exc)
            if "vercel_token_unconfigured" in message or "VERCEL_TOKEN" in message:
                raise HTTPException(status_code=503, detail="vercel_unconfigured") from exc
            if "vercel_project_unconfigured" in message:
                raise HTTPException(status_code=503, detail="vercel_project_unconfigured") from exc
            raise HTTPException(status_code=502, detail="vercel_error") from exc

    @app.post("/v1/storage/put")
    def storage_put(
        body: _StoragePutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        _storage_business_slug(body.key)
        try:
            data = base64.b64decode(str(body.data_b64 or ""), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_base64") from exc
        try:
            return safebox.storage_put(provider, body.key, data, digest=body.digest)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/composio/forward")
    def provider_composio_forward(
        body: _ComposioForwardBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # COMPOSIO_API_KEY is a provider secret held here and denied /v1/env egress; runtime planes
        # broker their Composio calls through this route. On the safebox host _use_remote_authority()
        # is False, so composio_distribution._request resolves the key LOCALLY and calls Composio
        # directly, returning the key-free upstream JSON. Gated by the internal token (transport
        # reachability); the per-action money gate lives upstream in the distribution skill/tool.
        _require_internal_token(authorization)
        from . import composio_distribution as _cd

        params = None
        if body.params:
            params = [
                (str(p[0]), p[1])
                for p in body.params
                if isinstance(p, (list, tuple)) and len(p) == 2
            ]
        try:
            return _cd._request(
                body.method,
                body.path,
                json_body=body.json_body,
                params=params,
                timeout=body.timeout,
            )
        except _cd.ComposioDistributionError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/meta/config")
    def provider_meta_config(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # The Meta system-user token is a provider secret held here and DENIED /v1/env egress, so a
        # runtime plane cannot resolve it. This returns the NON-SECRET Meta config (graph version,
        # ad account id, page id, composio_* hints) plus a has_token bool; the token VALUE is redacted
        # ("") and never leaves the safebox. On the safebox host _use_remote_authority() is False, so
        # core._meta_config resolves the token LOCALLY and succeeds. Gated by the internal token
        # (transport reachability); the per-action money gate lives upstream in the meta-ads handlers.
        _require_internal_token(authorization)
        from . import core as _core

        try:
            cfg = dict(_core._meta_config(require_token=True))
        except _core.TakyonError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        cfg["has_token"] = bool(cfg.get("token"))
        cfg["token"] = ""
        return cfg

    @app.post("/v1/providers/meta/graph")
    def provider_meta_graph(
        body: _MetaGraphBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Broker one Meta Graph API call: the runtime plane forwards method/path/params here and the
        # safebox re-resolves the real system-user token LOCALLY before calling Graph, returning the
        # key-free upstream JSON. The token never leaves the safebox. Gated by the internal token; the
        # per-action money gate lives upstream in the meta-ads handlers.
        _require_internal_token(authorization)
        from . import core as _core

        try:
            cfg = _core._meta_config(require_token=True)
            return _core._meta_graph(
                body.method,
                body.path,
                dict(body.params or {}),
                cfg,
                host=body.host,
                timeout=int(body.timeout),
            )
        except _core.TakyonError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/storage/get")
    def storage_get(
        body: _StorageKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        _storage_business_slug(body.key)
        try:
            data = safebox.storage_get(provider, body.key)
        except Exception as exc:
            if type(exc).__name__ == "ObjectNotFound":
                raise HTTPException(status_code=404, detail="object_not_found") from exc
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"provider": provider, "key": body.key, "data_b64": base64.b64encode(data).decode("ascii")}

    @app.post("/v1/storage/delete")
    def storage_delete(
        body: _StorageKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        _storage_business_slug(body.key)
        try:
            return safebox.storage_delete(provider, body.key)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/storage/list-digests")
    def storage_list_digests(
        body: _StorageListBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        try:
            _storage_business_slug(body.prefix)
        except HTTPException as exc:
            if exc.detail == "unknown_business":
                return {"provider": provider, "prefix": body.prefix, "digests": {}}
            raise
        try:
            return {"provider": provider, "prefix": body.prefix, "digests": safebox.storage_list_digests(provider, body.prefix)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/storage/list-sizes")
    def storage_list_sizes(
        body: _StorageListBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        try:
            _storage_business_slug(body.prefix)
        except HTTPException as exc:
            if exc.detail == "unknown_business":
                return {"provider": provider, "prefix": body.prefix, "sizes": {}}
            raise
        try:
            return {"provider": provider, "prefix": body.prefix, "sizes": safebox.storage_list_object_sizes(provider, body.prefix)}
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/billing/webhook/process")
    def process_billing_webhook(
        body: _StripeBillingWebhookVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            event = safebox.verify_stripe_billing_webhook(body.raw_body, body.signature)
        except safebox.StripeBillingWebhookUnconfigured as exc:
            raise HTTPException(status_code=503, detail="billing_webhook_unconfigured") from exc
        except safebox.StripeBillingWebhookInvalidSignature as exc:
            raise HTTPException(status_code=400, detail="invalid_signature") from exc
        from .control_api import process_billing_webhook_event

        with _safebox_db_conn() as conn:
            return process_billing_webhook_event(conn, event)

    @app.post("/v1/custody/accounts/open")
    def open_custody_account(
        body: _OpenCustodyAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox._local_open_custody_account(None, body.user_id, currency=body.currency or "usd")
        return {"ok": True}

    @app.post("/v1/creative-credits/accounts/open")
    def open_creative_credit_account(
        body: _OpenCreativeCreditAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox._local_open_business_credit_account(None, body.business_slug)
        return {"ok": True}

    @app.post("/v1/creative-credits/bootstrap-starter")
    def grant_business_bootstrap_credits(
        body: _BusinessBootstrapCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            balances = safebox._local_grant_business_bootstrap_credits(
                None,
                body.business_slug,
                body.operator_user_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except PermissionError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return {
            "ok": True,
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
            "credited_credits": safebox.business_bootstrap_free_credits(),
        }

    @app.get("/v1/creative-credits/{business_slug}")
    def get_creative_credit_balances(
        business_slug: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        balances = safebox._local_get_business_credit_balances(None, business_slug)
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @app.post("/v1/creative-credits/checkout")
    def create_creative_credit_checkout(
        body: _CreativeCreditCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import stripe_util
        from .control_api import create_creative_credit_checkout_session

        try:
            session, charge = create_creative_credit_checkout_session(
                body.user_id,
                body.business_slug,
                credits=body.credits,
                pack_id=body.pack_id,
                success_url=body.success_url,
                cancel_url=body.cancel_url,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="unknown_credit_pack") from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except stripe_util.StripeError as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message or "creative_credit_checkout_unconfigured" in message:
                raise HTTPException(
                    status_code=503, detail="creative_credit_checkout_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {message}") from exc
        return {
            "checkout_url": session.get("url"),
            "session_id": session.get("id"),
            "business_slug": body.business_slug,
            "pack_id": charge.get("pack_id"),
            "credits": charge["credits"],
            "amount_cents": charge["amount_cents"],
            "price_cents_per_credit": charge.get("price_cents_per_credit"),
        }

    @app.post("/v1/creative-credits/reconcile")
    def reconcile_creative_credit_checkout(
        body: _ReconcileCreativeCreditCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import stripe_util

        try:
            return safebox.reconcile_creative_credit_checkout(
                None,
                session_id=body.session_id,
                expected_business_slug=body.business_slug,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except RuntimeError as exc:
            if str(exc) == "creative_credit_checkout_unpaid":
                raise HTTPException(status_code=409, detail=str(exc)) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except stripe_util.StripeError as exc:
            message = str(exc)
            if "STRIPE_SECRET_KEY" in message or "creative_credit_reconcile_unconfigured" in message:
                raise HTTPException(
                    status_code=503, detail="creative_credit_reconcile_unconfigured"
                ) from exc
            raise HTTPException(status_code=502, detail=f"stripe_error: {message}") from exc

    @app.post("/v1/creative-credits/grant")
    def grant_creative_credits(
        body: _GrantCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        raise HTTPException(
            status_code=403,
            detail="creative_credit_grant_requires_verified_checkout_or_webhook",
        )

    @app.post("/v1/stripe/billing-webhook/verify")
    def verify_stripe_billing_webhook(
        body: _StripeBillingWebhookVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import stripe_util

        secret = safebox.read_env_backed_value("STRIPE_BILLING_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=503, detail="billing_webhook_unconfigured")
        try:
            stripe_util.verify_stripe_signature(body.raw_body, body.signature, secret)
        except stripe_util.StripeError as exc:
            raise HTTPException(status_code=400, detail="invalid_signature") from exc
        event = json.loads(body.raw_body)
        return {"event": event if isinstance(event, dict) else {}}

    @app.post("/v1/stripe/app-webhook/verify")
    def verify_stripe_app_webhook(
        body: _StripeAppWebhookVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Sub-user (flow-B) product app webhook verify — the secret-side analogue of the flow-A
        # billing-webhook verify above. STRIPE_WEBHOOK_SECRET is read LOCALLY on the safebox and the
        # signature is verified here; the parsed event is returned (NEVER the secret) so the runtime
        # plane can reconcile entitlements without ever holding the signing key.
        _require_internal_token(authorization)
        from . import stripe_util

        secret = safebox.read_env_backed_value("STRIPE_WEBHOOK_SECRET")
        if not secret:
            raise HTTPException(status_code=503, detail="app_webhook_unconfigured")
        try:
            stripe_util.verify_stripe_signature(body.raw_body, body.signature, secret)
        except stripe_util.StripeError as exc:
            raise HTTPException(status_code=400, detail="invalid_signature") from exc
        event = json.loads(body.raw_body)
        return {"event": event if isinstance(event, dict) else {}}

    @app.post("/v1/stripe/app-webhook/process")
    def process_stripe_app_webhook(
        body: _StripeAppWebhookVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Signature verification and entitlement/revenue/custody processing happen together on the
        # safebox, so custody accrual is tied to a genuine signed Stripe event instead of a
        # shared-token caller-supplied amount.
        _require_internal_token(authorization)
        try:
            event = safebox.verify_stripe_app_webhook(body.raw_body, body.signature)
        except safebox.StripeAppWebhookUnconfigured as exc:
            raise HTTPException(status_code=503, detail="app_webhook_unconfigured") from exc
        except safebox.StripeAppWebhookInvalidSignature as exc:
            raise HTTPException(status_code=400, detail="invalid_signature") from exc
        from . import app_payments

        with _safebox_db_conn() as conn:
            return app_payments.record_webhook_and_process(conn, event)

    @app.post("/v1/stripe/app-checkout/reconcile")
    def reconcile_stripe_app_checkout(
        body: _AppCheckoutReconcileBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Recovery path for a completed hosted Checkout session when the webhook has not arrived yet.
        # The runtime can request reconciliation by session id, but the safebox retrieves the Stripe
        # object locally, verifies it is a Takyon app checkout, and performs entitlement/revenue/custody
        # processing on the safebox DB role. The shared transport token never gets custody authority.
        _require_internal_token(authorization)
        from . import app_payments, stripe_util

        session_id = str(body.session_id or "").strip()
        if not session_id or not session_id.startswith("cs_"):
            raise HTTPException(status_code=400, detail="invalid_checkout_session")
        try:
            session = safebox.stripe_request(f"checkout/sessions/{session_id}", {}, method="GET")
        except stripe_util.StripeError as exc:
            message = str(exc)
            if " failed: 404" in message:
                raise HTTPException(status_code=404, detail="unknown_checkout_session") from exc
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="stripe_unconfigured") from exc
            raise HTTPException(status_code=502, detail="stripe_error") from exc
        if not isinstance(session, dict) or not session:
            raise HTTPException(status_code=404, detail="unknown_checkout_session")
        business = _require_takyon_app_stripe_object(session, require_source=True)
        expected_business = str(body.business_slug or "").strip()
        if expected_business and _require_safe_slug(expected_business) != business:
            raise HTTPException(status_code=403, detail="checkout_business_mismatch")
        if str(session.get("status") or "").strip().lower() != "complete":
            raise HTTPException(status_code=409, detail="checkout_session_not_complete")
        if str(session.get("payment_status") or "").strip().lower() not in {"paid", "no_payment_required"}:
            raise HTTPException(status_code=409, detail="checkout_session_unpaid")

        expected_user = str(body.app_user_id or "").strip()
        expected_email = str(body.customer_email or "").strip().lower()
        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        intent_id = str(metadata.get("checkout_intent_id") or "").strip()
        client_reference_id = str(session.get("client_reference_id") or "").strip()
        with _safebox_db_conn() as conn:
            intent = None
            if intent_id:
                intent = conn.execute(
                    "select business_slug, app_user_id, customer_email "
                    "from app_checkout_intents where id = %s",
                    (intent_id,),
                ).fetchone()
            if intent is None and client_reference_id:
                intent = conn.execute(
                    "select business_slug, app_user_id, customer_email "
                    "from app_checkout_intents where client_reference_id = %s",
                    (client_reference_id,),
                ).fetchone()
            if intent is None:
                raise HTTPException(status_code=404, detail="missing_checkout_intent")
            intent_business = str(intent[0] or "").strip()
            intent_user = str(intent[1] or "").strip()
            intent_email = str(intent[2] or "").strip().lower()
            if intent_business != business:
                raise HTTPException(status_code=403, detail="checkout_intent_business_mismatch")
            if expected_user and intent_user and intent_user != expected_user:
                raise HTTPException(status_code=403, detail="checkout_user_mismatch")
            if expected_email and intent_email and intent_email != expected_email:
                raise HTTPException(status_code=403, detail="checkout_email_mismatch")
            checkout_result = app_payments.reconcile_checkout_session(
                conn,
                session,
                provider_event_id=f"checkout.session.reconcile:{session_id}",
                event_created=session.get("created"),
            )
            subscription_result = None
            subscription_id = str(session.get("subscription") or "").strip()
            if subscription_id:
                try:
                    subscription = safebox.stripe_request(
                        f"subscriptions/{subscription_id}", {}, method="GET"
                    )
                except stripe_util.StripeError:
                    subscription = {}
                if isinstance(subscription, dict) and subscription:
                    subscription_result = app_payments.reconcile_subscription(conn, subscription)
        return {
            "ok": True,
            "session_id": session_id,
            "business_slug": business,
            "processed": checkout_result,
            "subscription": subscription_result,
        }

    @app.post("/v1/creative-credits/reserve")
    def reserve_creative_credits(
        body: _ReserveCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        raise HTTPException(status_code=403, detail="creative_credit_spend_requires_creative_gate")

    @app.post("/v1/creative-credits/commit")
    def commit_creative_credits(
        body: _CommitCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        raise HTTPException(status_code=403, detail="creative_credit_spend_requires_creative_gate")

    @app.post("/v1/creative-credits/release")
    def release_creative_credits(
        body: _ReleaseCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        raise HTTPException(status_code=403, detail="creative_credit_spend_requires_creative_gate")

    # ── Capability mint + action-shaped broker routes (Phase 2 cutover prep) ──────────────────────
    # These are ADDITIVE alongside /v1/env/*; the env egress routes stay live until Codex STEP E
    # deletes them. Each provider route brokers the call entirely inside the safebox: the capability
    # token is verified (authoritative scope), its nonce claimed once, usage reserved on the validated
    # {business, app_user} via the STEP-A SECURITY DEFINER ledger, the provider key resolved LOCALLY,
    # the provider called, and the cost settled — the caller never sees the key.

    @app.post("/v1/token/mint")
    def mint_token(
        body: _MintTokenBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _require_internal_token(authorization)
        # Audience is derived SOLELY from the action map. We IGNORE body.audience: the
        # entitlement/ceiling decision and the provider invocation must be the SAME action, so a
        # caller must never be able to mint action="ping" but audience="anthropic.messages" and then
        # broker an expensive provider call under a cheap action's scope. This endpoint mints only
        # product/sub-user single-use capabilities. Operator and creative capabilities have their own
        # safebox gates because they carry identity authority / fixed-credit reserve authority.
        if str(body.operator_user_id or "").strip():
            raise HTTPException(status_code=403, detail="operator_capabilities_use_session_route")
        if not str(body.session_token or "").strip():
            raise HTTPException(status_code=403, detail="product_session_token_required")
        audience = _ACTION_AUDIENCE_DEFAULTS.get(str(body.action or "").strip())
        if not audience:
            raise HTTPException(status_code=400, detail="unmappable_action")
        ttl_seconds = int(body.ttl_seconds or _CAP_TTL_SECONDS)
        if ttl_seconds <= 0:
            raise HTTPException(status_code=400, detail="ttl_must_be_positive")
        token = _mint_capability_token(
            business=body.business,
            action=body.action,
            max_cost_microusd=int(body.max_cost_microusd),
            session_token=body.session_token,
            operator_user_id=body.operator_user_id,
            audience=audience,
            ttl_seconds=ttl_seconds,
            now=int(time.time()),
        )
        return {"token": token, "audience": audience}

    @app.post("/v1/operator/session-token")
    def operator_session_token(
        body: _OperatorSessionTokenBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Mint a SESSION-scoped operator capability (audience = operator.session) for one CEO/worker
        run. Validates operator ownership of the business (boundary 1 via ``authorize_operator_call``),
        binds the per-CALL cost ceiling, and issues a REUSABLE, TTL-bounded capability the operator plane
        presents on every Anthropic / Tavily proxy call. The signing key lives ONLY on the safebox, so
        the operator host cannot forge or widen scope. Internal-token only.

        Distinct from ``/v1/token/mint``: that mints a SINGLE-USE (nonce-claimed) per-action capability
        for the metered ``/v1/providers/*`` business broker; this mints a long-lived, reusable session
        token for the operator PROXY routes, which meter EACH call against the operator's control-plane
        budget without claiming a nonce."""
        _require_internal_token(authorization)
        ttl_seconds = int(body.ttl_seconds or _OPERATOR_SESSION_TTL_SECONDS)
        if ttl_seconds <= 0:
            raise HTTPException(status_code=400, detail="ttl_must_be_positive")
        # Clamp the session TTL so a leaked token still expires within the hard bound.
        ttl_seconds = min(ttl_seconds, _OPERATOR_SESSION_TTL_MAX_SECONDS)
        token = _mint_capability_token(
            business=body.business,
            action=_OPERATOR_SESSION_AUDIENCE,
            max_cost_microusd=int(body.max_cost_microusd),
            session_token=None,
            operator_user_id=body.operator_user_id,
            audience=_OPERATOR_SESSION_AUDIENCE,
            ttl_seconds=ttl_seconds,
            now=int(time.time()),
        )
        return {
            "token": token,
            "audience": _OPERATOR_SESSION_AUDIENCE,
            "ttl_seconds": ttl_seconds,
            "max_cost_microusd": int(body.max_cost_microusd),
        }

    @app.post("/v1/providers/anthropic/messages")
    def provider_anthropic_messages(
        body: _ProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _broker_provider_route(
            body,
            audience=_ANTHROPIC_AUDIENCE,
            provider="anthropic",
            key_resolver=_anthropic_key_resolver,
            caller_builder=_anthropic_provider_caller,
            estimate_builder=_anthropic_estimate,
        )

    @app.post("/v1/providers/tavily/search")
    def provider_tavily_search(
        body: _ProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _broker_provider_route(
            body,
            audience=_TAVILY_AUDIENCE,
            provider="tavily",
            key_resolver=_tavily_key_resolver,
            caller_builder=_tavily_provider_caller,
            estimate_builder=_tavily_estimate,
        )

    @app.post("/v1/providers/gemini/image")
    def provider_gemini_image(
        body: _ProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _broker_provider_route(
            body,
            audience=_GEMINI_IMAGE_AUDIENCE,
            provider="gemini",
            key_resolver=_gemini_image_key_resolver,
            caller_builder=_gemini_image_provider_caller,
            estimate_builder=_gemini_image_estimate,
        )

    # ── Creative-credit gate: AUTHORITATIVE reserve/commit/release (operator-owned) ───────────────
    # These three routes are the ONE money gate for the fixed-price creative actions (logo / UGC /
    # static ad). The operator (boundary-1 ownership) reserves the action's canonical fixed credit
    # price on the business's creative-credit ledger ON THE SAFEBOX; reserve hands back a creative
    # capability the client presents to the gated provider routes. No client may reserve/commit credits
    # itself, and the provider routes never re-charge — so there is exactly one authoritative gate per
    # action and no double-charge.

    @app.post("/v1/creative/reserve")
    def creative_reserve(
        body: _CreativeReserveBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import safebox

        action = str(body.action or "").strip()
        audience = action if action in _CREATIVE_AUDIENCE_CREDIT_ACTION else ""
        if not audience:
            raise HTTPException(status_code=400, detail="unmappable_creative_action")
        reservation_key = str(body.reservation_key or "").strip()
        if not reservation_key:
            raise HTTPException(status_code=400, detail="reservation_key_required")
        units = int(body.units or 1)
        ttl_seconds = int(body.ttl_seconds or _CAP_TTL_SECONDS)
        if ttl_seconds <= 0:
            raise HTTPException(status_code=400, detail="ttl_must_be_positive")

        signing_key = _cap_signing_key()
        if not signing_key:
            raise HTTPException(status_code=503, detail="capability_signing_unconfigured")

        # Boundary 1: validate the operator OWNS the business and derive the AUTHORITATIVE scope. The
        # fixed credit price is the ceiling (max_cost_microusd carries the credit count for this rail).
        try:
            credits = _creative_credit_price(audience, units=units)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="unmappable_creative_action") from exc

        from .safebox_authz import AuthzError, authorize_operator_call

        try:
            with _safebox_db_conn() as conn:
                scope = authorize_operator_call(
                    conn,
                    business_slug=str(body.business or ""),
                    operator_user_id=str(body.operator_user_id or ""),
                    action=action,
                    max_cost_microusd=credits,
                )
        except AuthzError as exc:
            raise HTTPException(status_code=403, detail=str(exc)) from exc

        # Reserve the fixed credits on the verified business BEFORE handing back a token. Insufficient
        # credits -> 402 here, before any token mint / provider key / provider call.
        ledger = _CreditLedgerAdapter(audience=audience)
        try:
            reservation = ledger.reserve(
                scope,
                reservation_key=reservation_key,
                units=units,
                metadata=body.metadata,
            )
        except safebox.InsufficientCreativeCredits as exc:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": str(exc),
                    "requested_credits": exc.requested_credits,
                    "available_credits": exc.available_credits,
                },
            ) from exc

        token = mint_capability(
            scope,
            signing_key=signing_key,
            audience=audience,
            nonce=str(uuid.uuid4()),
            issued_at=int(time.time()),
            ttl_seconds=ttl_seconds,
        )
        return {
            "token": token,
            "audience": audience,
            "reservation_key": reservation["reservation_key"],
            "reserved_credits": reservation["reserved_credits"],
            "credits": reservation["credits"],
        }

    @app.post("/v1/creative/commit")
    def creative_commit(
        body: _CreativeFinalizeBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import safebox

        reservation_key = str(body.reservation_key or "").strip()
        if not reservation_key:
            raise HTTPException(status_code=400, detail="reservation_key_required")
        try:
            balances = _CreditLedgerAdapter(audience="").commit(
                reservation_key=reservation_key,
                actual_credits=body.actual_credits,
                metadata=body.metadata,
            )
        except safebox.UnknownCreativeCreditReservation as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_creative_credit_reservation", "reservation_key": str(exc)},
            ) from exc
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @app.post("/v1/creative/release")
    def creative_release(
        body: _CreativeFinalizeBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import safebox

        reservation_key = str(body.reservation_key or "").strip()
        if not reservation_key:
            raise HTTPException(status_code=400, detail="reservation_key_required")
        try:
            balances = _CreditLedgerAdapter(audience="").release(
                reservation_key=reservation_key,
                metadata=body.metadata,
            )
        except safebox.UnknownCreativeCreditReservation as exc:
            raise HTTPException(
                status_code=404,
                detail={"error": "unknown_creative_credit_reservation", "reservation_key": str(exc)},
            ) from exc
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    # ── Gated creative PROVIDER routes (verify creative capability -> key-local -> forward) ────────
    # Each route requires a creative capability (minted by /v1/creative/reserve, audience-bound to one
    # of allowed_audiences), resolves the provider key LOCALLY, forwards, and returns a KEY-FREE result.
    # They do NOT reserve/commit credits (the reserve route already did, once per action) and the token
    # is NOT single-use, so one reserved action can drive its several provider calls. These REPLACE the
    # deleted ungated /v1/proxy/{gemini,openai,fal} routes.

    @app.post("/v1/providers/gemini/logo")
    def provider_gemini_logo(
        body: _CreativeProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _creative_provider_route(
            body,
            allowed_audiences=_CREATIVE_GEMINI_AUDIENCES,
            caller_builder=_creative_gemini_caller,
        )

    @app.post("/v1/providers/openai/images")
    def provider_openai_images(
        body: _CreativeProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _creative_provider_route(
            body,
            allowed_audiences=_CREATIVE_OPENAI_AUDIENCES,
            caller_builder=_creative_openai_images_caller,
        )

    @app.post("/v1/providers/fal/{fal_path:path}")
    def provider_fal(
        fal_path: str,
        body: _CreativeProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _creative_provider_route(
            body,
            allowed_audiences=_CREATIVE_FAL_AUDIENCES,
            caller_builder=_creative_fal_caller(fal_path),
        )

    # ── Operator/platform provider proxy (internal-token only, platform-billed, key-free) ─────────
    # The TRUSTED operator/platform counterpart to the metered /v1/providers/* business broker above:
    # it resolves the real provider key LOCALLY and forwards, so operator/platform/worker code can call
    # paid providers WITHOUT ever holding a raw key. Mounted from its own module to keep the broker and
    # the proxy in separate, uniform surfaces.
    from .safebox_provider_proxy import register_provider_proxy_routes

    register_provider_proxy_routes(app)

    return app


app = build_safebox_app()
