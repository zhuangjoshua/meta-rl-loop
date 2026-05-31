"""Internal AI Gateway — the server-side broker that lets a generated app spend on AI without ever
holding the platform provider key.

mediationplan.md (Phase 5 / Runtime Cutover): "Gateway resolves business → policy → reserves
billing → calls the shared provider key → settles. Generated apps never hold provider keys." This
router is that broker. A generated app (or a business's app runtime) presents its own ``tkg_``
gateway key as ``Authorization: Bearer``; the gateway resolves it to a ``business_slug`` — and to
NOTHING else (never another tenant, never the provider key) — meters the spend against that
business's product budget through THE ONE gate (``app_usage`` reserve→settle/release), and only then
calls the SHARED platform provider key server-side. The provider key is resolved HERE and bound into
a caller closure; it is never an argument the app supplies and never appears in any response.

This is the Postgres successor to the SQLite ``/generate`` route (``app_api.py``), with two
deliberate hardenings over it:
  * Spend is gated by the atomic reserve-under-row-lock (``app_usage.reserve_usage``), not the old
    read-then-act budget mirror that N concurrent calls could all slip past.
  * The cost estimate the cap is checked against is computed SERVER-SIDE from the request payload —
    a caller can no longer under-declare ``estimated_cost_microusd`` to duck the cap.

House style mirrors ``control_api.py``: a ``build_*_router()`` factory plus ``get_gateway_conn`` /
``get_provider_caller`` dependency seams the host app overrides. The router is standalone and
strategy-free; mounting it against real Postgres + the real provider key is the deliberate step in
``runtime_app.py``. Invariant #8: when no provider key is configured the gateway BLOCKS (503 with a
reason) — it never calls keyless and never fabricates a completion.
"""

from __future__ import annotations

import uuid
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from .ai_provider import (
    anthropic_key,
    anthropic_payload,
    anthropic_rates_microusd_per_token,
    anthropic_text,
    call_anthropic,
    microusd_cost,
)
from .app_gateway_keys import GatewayPrincipal, resolve_gateway_key
from .app_usage import (
    AppBudgetExceeded,
    AppBudgetInactive,
    AppUserNotFound,
    release_usage,
    reserve_usage,
    settle_usage,
)

_BEARER_PREFIX = "Bearer "
_UNAUTH_HEADERS = {"WWW-Authenticate": "Bearer"}

# A provider caller is a server-side closure that already holds the shared key. The endpoint only
# ever sees this callable (or None when unconfigured) — never the key itself.
ProviderCaller = Callable[[dict], dict]


def get_gateway_conn():
    """Dependency seam for the per-request gateway DB connection. Unconfigured by default — the host
    app MUST override it (``app.dependency_overrides[get_gateway_conn] = ...``), exactly like
    ``control_api.get_control_conn``. Keeps the router free of any connect/pool strategy."""
    raise RuntimeError("ai-gateway connection not configured")


def get_provider_caller() -> ProviderCaller | None:
    """Resolve the SHARED platform provider key server-side and bind it into a caller closure.

    Returns None when no key is configured — the endpoint then BLOCKS (503) per invariant #8, never
    calling keyless and never fabricating a completion. The key lives ONLY inside the returned
    closure; it is never returned to a caller and is not an argument the app supplies. Tests override
    this seam with a canned caller (and can leave it as the real default to exercise the blocked path
    in a key-less environment), so no real key or network is needed to test the gate."""
    key = anthropic_key()
    if not key:
        return None

    def _call(payload: dict) -> dict:
        return call_anthropic(payload, key)

    return _call


def _gateway_principal(
    authorization: str | None = Header(default=None),
    conn=Depends(get_gateway_conn),
) -> GatewayPrincipal:
    """Resolve the presented ``tkg_`` gateway key to its business, or refuse with one
    undifferentiated 401. Malformed, unknown, and revoked all look identical from outside — the
    boundary never reveals which, nor whether any business exists."""
    if not authorization or not authorization.startswith(_BEARER_PREFIX):
        raise HTTPException(
            status_code=401, detail="missing_bearer_token", headers=_UNAUTH_HEADERS
        )
    raw = authorization[len(_BEARER_PREFIX) :].strip()
    principal = resolve_gateway_key(conn, raw)
    if principal is None:
        raise HTTPException(
            status_code=401, detail="invalid_gateway_key", headers=_UNAUTH_HEADERS
        )
    return principal


def build_ai_gateway_router() -> APIRouter:
    """Build the ``/internal/ai-gateway`` router. Call ``app.include_router(...)`` on it and override
    ``get_gateway_conn`` (and, in tests, ``get_provider_caller``) to supply the connection and the
    provider call."""
    router = APIRouter(prefix="/internal/ai-gateway")

    @router.post("/messages")
    def create_message(
        body: dict | None = Body(default=None),
        principal: GatewayPrincipal = Depends(_gateway_principal),
        conn=Depends(get_gateway_conn),
        caller: ProviderCaller | None = Depends(get_provider_caller),
    ) -> dict[str, Any]:
        """Broker one AI generation for the gateway key's business: reserve → call shared key →
        settle. Returns only the generated text/content/model/usage — never the provider key."""
        body = body or {}
        business_slug = principal.business_slug

        # Invariant #8: no provider key configured → block with a reason. Checked AFTER auth (so an
        # unauthenticated caller can't probe provider configuration) and BEFORE reserving (so a
        # config gap never churns the budget).
        if caller is None:
            raise HTTPException(status_code=503, detail="provider_unconfigured")

        # Build the provider payload from the (untrusted) request body. Bad/empty body → 400.
        try:
            payload, model, estimated_input_tokens = anthropic_payload(body)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        # Cost estimate is computed SERVER-SIDE (the caller cannot declare its own) and is what the
        # budget cap is checked against at reserve.
        estimated_output_tokens = int(payload.get("max_tokens") or 0)
        estimated_cost = microusd_cost(model, estimated_input_tokens, estimated_output_tokens)

        app_user_id = body.get("app_user_id") or body.get("appUserId") or None
        app_user_tier = body.get("app_user_tier") or body.get("appUserTier") or None
        rate_source = anthropic_rates_microusd_per_token(model)[2]

        # THE gate: hold the estimate atomically under the budget row lock. A FRESH reservation_key
        # per request — an internal reserve↔settle correlation id, not a client retry key — so there
        # is no replay path that could call the provider twice against one settle.
        reservation_key = uuid.uuid4().hex
        try:
            reserve_usage(
                conn,
                business_slug,
                estimated_cost_microusd=estimated_cost,
                reservation_key=reservation_key,
                app_user_id=app_user_id,
                app_user_tier=app_user_tier,
                purpose=str(body.get("purpose") or "ai_generate"),
                route="internal_ai_gateway",
                provider="anthropic",
                model=model,
                metadata={"cost_rate_source": rate_source},
            )
        except AppBudgetInactive as exc:
            raise HTTPException(
                status_code=402,
                detail={"error": "app_budget_inactive", "status": exc.status},
            ) from exc
        except AppBudgetExceeded as exc:
            raise HTTPException(
                status_code=402,
                detail={
                    "error": "app_budget_exceeded",
                    "hard_limit_microusd": exc.hard_limit_microusd,
                    "committed_microusd": exc.committed_microusd,
                    "requested_microusd": exc.requested_microusd,
                    "remaining_microusd": exc.remaining_microusd,
                },
            ) from exc
        except AppUserNotFound as exc:
            raise HTTPException(status_code=400, detail="unknown_app_user") from exc

        # Reservation held. Call the shared provider key server-side. On ANY failure release the hold
        # (no spend recorded) and surface 502. On success settle at the TRUE provider cost — settle
        # never re-checks the cap, because the money is already spent and recording truth is
        # mandatory (mediationplan invariant #8).
        try:
            provider_response = caller(payload)
        except Exception as exc:
            release_usage(conn, business_slug, reservation_key, error=str(exc))
            raise HTTPException(status_code=502, detail="provider_error") from exc

        usage = provider_response.get("usage") or {}
        input_tokens = int(usage.get("input_tokens") or estimated_input_tokens)
        output_tokens = int(usage.get("output_tokens") or 0)
        actual_cost = microusd_cost(model, input_tokens, output_tokens)
        provider_request_id = str(provider_response.get("id") or "")

        settle_usage(
            conn,
            business_slug,
            reservation_key,
            actual_cost_microusd=actual_cost,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            provider_request_id=provider_request_id,
            provider="anthropic",
            model=model,
        )

        return {
            "success": True,
            "text": anthropic_text(provider_response),
            "content": provider_response.get("content") or [],
            "model": model,
            "usage": {
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "estimated_cost_microusd": estimated_cost,
                "actual_cost_microusd": actual_cost,
            },
        }

    return router
