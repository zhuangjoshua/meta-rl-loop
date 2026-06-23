"""Dedicated Safebox service app.

This is the service boundary for Safebox when it runs on its own VPS. The
runtime planes talk to it over HTTP; the service itself still uses the local
Safebox authority module as the single backing implementation.
"""

from __future__ import annotations

import hmac
import json
import os
import time
import uuid
from contextlib import contextmanager
from typing import Any

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
    # Product (sub-user) mint: session_token + business. Operator/platform mint: operator_user_id +
    # business. action + max_cost_microusd scope the minted capability. Exactly one identity shape.
    business: str
    action: str
    max_cost_microusd: int
    session_token: str | None = None
    operator_user_id: str | None = None
    audience: str | None = None
    ttl_seconds: int | None = None


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
        png_bytes = creative_gateway._gemini_generate_logo_png(api_key=key, prompt=prompt)
        result = {"image_base64": _b64.b64encode(png_bytes).decode("ascii"), "format": "png"}
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

    @app.get("/v1/env/{key}")
    def read_env_value(key: str, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        return {"value": safebox.read_env_backed_value(key)}

    @app.post("/v1/env/first")
    def first_env_value(body: _FirstEnvBody, authorization: str | None = Header(default=None)) -> dict[str, str]:
        _require_internal_token(authorization)
        return {"value": safebox.first_env_backed_value(*body.keys)}

    @app.post("/v1/env/{key}")
    def save_env_value(key: str, body: _EnvValueBody, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox.save_env_backed_value(key, body.value)
        return {"ok": True}

    @app.delete("/v1/env/{key}")
    def delete_env_value(key: str, authorization: str | None = Header(default=None)) -> dict[str, bool]:
        _require_internal_token(authorization)
        return {"removed": safebox.remove_env_backed_value(key)}

    @app.get("/v1/env/snapshot")
    def env_snapshot(authorization: str | None = Header(default=None)) -> dict[str, dict[str, str]]:
        _require_internal_token(authorization)
        return {"snapshot": safebox.sensitive_env_snapshot()}

    @app.get("/v1/env")
    def env_keys(
        sensitive_only: str = Query(default="1"),
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[str]]:
        _require_internal_token(authorization)
        return {"keys": safebox.list_env_backed_keys(sensitive_only=sensitive_only != "0")}

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

    @app.post("/v1/creative-credits/accounts/open")
    def open_creative_credit_account(
        body: _OpenCreativeCreditAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        safebox._local_open_business_credit_account(None, body.business_slug)
        return {"ok": True}

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
        balances = safebox._local_grant_credits(
            None,
            body.business_slug,
            body.credits,
            body.idempotency_key,
            metadata=body.metadata,
            stripe_ref=body.stripe_ref,
        )
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

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

    @app.post("/v1/creative-credits/reserve")
    def reserve_creative_credits(
        body: _ReserveCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            reservation = safebox._local_reserve_credits(
                None,
                body.business_slug,
                body.credits,
                body.reservation_key,
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
        return {
            "key": reservation.key,
            "reserved_credits": reservation.reserved_credits,
        }

    @app.post("/v1/creative-credits/commit")
    def commit_creative_credits(
        body: _CommitCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            balances = safebox._local_commit_credits(
                None,
                body.reservation_key,
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

    @app.post("/v1/creative-credits/release")
    def release_creative_credits(
        body: _ReleaseCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        try:
            balances = safebox._local_release_credits(
                None,
                body.reservation_key,
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
        # broker an expensive provider call under a cheap action's scope. An action with no mapped
        # audience is unbrokerable -> 400.
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

    # ── Operator/platform provider proxy (internal-token only, platform-billed, key-free) ─────────
    # The TRUSTED operator/platform counterpart to the metered /v1/providers/* business broker above:
    # it resolves the real provider key LOCALLY and forwards, so operator/platform/worker code can call
    # paid providers WITHOUT ever holding a raw key. Mounted from its own module to keep the broker and
    # the proxy in separate, uniform surfaces.
    from .safebox_provider_proxy import register_provider_proxy_routes

    register_provider_proxy_routes(app)

    return app


app = build_safebox_app()
