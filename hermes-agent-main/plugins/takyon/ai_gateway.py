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

from http.cookies import SimpleCookie
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from .ai_provider import (
    AnthropicPricingUnavailable,
    anthropic_key,
    anthropic_payload,
    anthropic_rates_microusd_per_token,
    anthropic_text,
    call_anthropic,
    microusd_cost,
)
from . import app_entitlements, app_funding, app_identity
from .app_gateway_keys import GatewayPrincipal, resolve_gateway_key
from .app_runtime_constants import APP_SESSION_COOKIE
from .app_usage import (
    AppBudgetExceeded,
    AppBudgetInactive,
    AppUserNotFound,
    ensure_app_budget,
    release_usage,
    reserve_usage,
    settle_usage,
)

_BEARER_PREFIX = "Bearer "
_UNAUTH_HEADERS = {"WWW-Authenticate": "Bearer"}
_APP_SESSION_HEADER = "X-Takyon-App-Session"

# A provider caller is a server-side closure that already holds the shared key. The endpoint only
# ever sees this callable (or None when unconfigured) — never the key itself.
ProviderCaller = Callable[[dict], dict]
_CALLER_UNSET = object()


class GatewayMessageError(Exception):
    """Structured gateway failure suitable for HTTP surfaces and compatibility shims."""

    def __init__(
        self,
        status_code: int,
        detail: Any,
        *,
        headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__(str(detail))
        self.status_code = int(status_code)
        self.detail = detail
        self.headers = headers or {}


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


def _session_token(body: dict, header_value: str | None, cookie_header: str | None) -> str:
    header_token = str(header_value or "").strip()
    if header_token:
        return header_token
    body_token = str(body.get("session_token") or body.get("sessionToken") or "").strip()
    if body_token:
        return body_token
    cookie = SimpleCookie(cookie_header or "")
    morsel = cookie.get(APP_SESSION_COOKIE)
    return "" if morsel is None else str(morsel.value or "").strip()


def _coerce_nonnegative_int(value) -> int:
    if value in {None, ""}:
        return 0
    try:
        return max(0, int(float(value)))
    except (TypeError, ValueError):
        return 0


def _resolve_plan_for_user(conn, business_slug: str, user: app_identity.AppUser):
    entitlement = app_entitlements.get_active_entitlement(conn, business_slug, user.id)
    if entitlement is not None and entitlement.plan_key:
        plan = app_entitlements.get_plan_policy(conn, business_slug, entitlement.plan_key)
        if plan is not None:
            return entitlement, plan
    for plan in app_entitlements.list_plan_policies(conn, business_slug):
        if plan.tier == user.tier:
            return entitlement, plan
    return entitlement, None


def _feature_allowed(plan, feature_name: str) -> bool:
    metadata = plan.metadata if plan is not None and isinstance(plan.metadata, dict) else {}
    features = metadata.get("features")
    if isinstance(features, dict):
        if feature_name in features:
            return bool(features.get(feature_name))
        return True
    if isinstance(features, (list, tuple, set)):
        return feature_name in set(str(item) for item in features)
    return True


def _model_allowed(plan, model: str) -> bool:
    metadata = plan.metadata if plan is not None and isinstance(plan.metadata, dict) else {}
    allowlist = metadata.get("model_allowlist") or metadata.get("models")
    if isinstance(allowlist, (list, tuple, set)) and allowlist:
        return model in {str(item) for item in allowlist}
    return True


def _user_credit_limit_microusd(plan) -> int:
    if plan is None or str(plan.tier or "free") == "free":
        return 0
    return max(0, int(plan.included_ai_budget_microusd))


def _subsidy_cap_microusd(plan) -> int:
    if plan is None:
        return 0
    metadata = plan.metadata if isinstance(plan.metadata, dict) else {}
    explicit = metadata.get("subsidy_cap_microusd")
    if explicit is not None:
        return _coerce_nonnegative_int(explicit)
    if str(plan.tier or "free") == "free" or bool(plan.allow_overage):
        return max(0, int(plan.included_ai_budget_microusd))
    return 0


def broker_message_for_business(
    conn,
    *,
    business_slug: str,
    raw_session_token: str,
    body: dict | None = None,
    caller: ProviderCaller | None | object = _CALLER_UNSET,
    audit_route: str = "internal_ai_gateway",
) -> dict[str, Any]:
    """Run the canonical app-generation broker flow for one business/app session.

    This is the one hardened path that compatibility surfaces should delegate to:
    validate app session -> reserve funding -> reserve app budget -> call shared
    provider key server-side -> settle actual cost. The caller never sees the
    provider key and never gets to bypass the funding/budget rails.
    """
    body = body or {}
    if caller is _CALLER_UNSET:
        caller = get_provider_caller()

    if not raw_session_token:
        raise GatewayMessageError(status_code=401, detail="missing_app_session")
    app_user = app_identity.validate_session(conn, business_slug, raw_session_token)
    if app_user is None:
        raise GatewayMessageError(status_code=401, detail="invalid_app_session")
    requested_app_user_id = body.get("app_user_id") or body.get("appUserId") or None
    if requested_app_user_id and str(requested_app_user_id) != app_user.id:
        raise GatewayMessageError(status_code=403, detail="mismatched_app_user")

    # Invariant #8: no provider key configured -> block with a reason. Checked
    # after auth (so callers cannot probe config) and before reservation.
    if caller is None:
        raise GatewayMessageError(status_code=503, detail="provider_unconfigured")

    try:
        payload, model, estimated_input_tokens = anthropic_payload(body)
    except Exception as exc:
        raise GatewayMessageError(status_code=400, detail=str(exc)) from exc

    estimated_output_tokens = int(payload.get("max_tokens") or 0)
    try:
        estimated_cost = microusd_cost(
            model, estimated_input_tokens, estimated_output_tokens
        )
        rate_source = anthropic_rates_microusd_per_token(model)[2]
    except AnthropicPricingUnavailable as exc:
        raise GatewayMessageError(status_code=400, detail=str(exc)) from exc

    entitlement, plan = _resolve_plan_for_user(conn, business_slug, app_user)
    feature_name = str(body.get("feature") or "ai_generate").strip() or "ai_generate"
    if plan is not None and not _feature_allowed(plan, feature_name):
        raise GatewayMessageError(
            status_code=403,
            detail={"error": "feature_not_in_plan", "feature": feature_name},
        )
    if plan is not None and not _model_allowed(plan, model):
        raise GatewayMessageError(
            status_code=403,
            detail={"error": "model_not_in_plan", "model": model},
        )
    budget = ensure_app_budget(conn, business_slug)
    user_credit_limit = _user_credit_limit_microusd(plan)
    subsidy_cap = _subsidy_cap_microusd(plan)
    plan_key = None
    if entitlement is not None and entitlement.plan_key:
        plan_key = entitlement.plan_key
    elif plan is not None:
        plan_key = plan.plan_key

    reservation_key = uuid.uuid4().hex
    try:
        app_funding.reserve_funding(
            conn,
            business_slug,
            app_user_id=app_user.id,
            reservation_key=reservation_key,
            estimated_cost_microusd=estimated_cost,
            user_credit_limit_microusd=user_credit_limit,
            subsidy_cap_microusd=subsidy_cap,
            period_start=budget.current_period_start,
            plan_key=plan_key,
            metadata={
                "feature": feature_name,
                "cost_rate_source": rate_source,
                "route": audit_route,
            },
        )
    except app_funding.InsufficientAppFunding as exc:
        raise GatewayMessageError(
            status_code=402,
            detail={
                "error": "app_funding_exhausted",
                "requested_microusd": exc.requested_microusd,
                "user_credit_remaining_microusd": exc.user_credit_remaining_microusd,
                "user_subsidy_remaining_microusd": exc.user_subsidy_remaining_microusd,
                "business_subsidy_remaining_microusd": exc.business_subsidy_remaining_microusd,
            },
        ) from exc

    try:
        reserve_usage(
            conn,
            business_slug,
            estimated_cost_microusd=estimated_cost,
            reservation_key=reservation_key,
            app_user_id=app_user.id,
            app_user_tier=app_user.tier,
            purpose=str(body.get("purpose") or "ai_generate"),
            route=audit_route,
            provider="anthropic",
            model=model,
            metadata={"cost_rate_source": rate_source},
        )
    except AppBudgetInactive as exc:
        app_funding.release_funding(
            conn, reservation_key, metadata={"error": "app_budget_inactive"}
        )
        raise GatewayMessageError(
            status_code=402,
            detail={"error": "app_budget_inactive", "status": exc.status},
        ) from exc
    except AppBudgetExceeded as exc:
        app_funding.release_funding(
            conn, reservation_key, metadata={"error": "app_budget_exceeded"}
        )
        raise GatewayMessageError(
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
        app_funding.release_funding(
            conn, reservation_key, metadata={"error": "unknown_app_user"}
        )
        raise GatewayMessageError(status_code=400, detail="unknown_app_user") from exc

    try:
        provider_response = caller(payload)
    except Exception as exc:
        release_usage(conn, business_slug, reservation_key, error=str(exc))
        app_funding.release_funding(conn, reservation_key, metadata={"error": str(exc)})
        raise GatewayMessageError(status_code=502, detail="provider_error") from exc

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
    app_funding.settle_funding(
        conn,
        reservation_key,
        actual_cost_microusd=actual_cost,
        metadata={"provider_request_id": provider_request_id},
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


def build_ai_gateway_router() -> APIRouter:
    """Build the ``/internal/ai-gateway`` router. Call ``app.include_router(...)`` on it and override
    ``get_gateway_conn`` (and, in tests, ``get_provider_caller``) to supply the connection and the
    provider call."""
    router = APIRouter(prefix="/internal/ai-gateway")

    @router.post("/messages")
    def create_message(
        body: dict | None = Body(default=None),
        principal: GatewayPrincipal = Depends(_gateway_principal),
        app_session_token: str | None = Header(default=None, alias=_APP_SESSION_HEADER),
        cookie_header: str | None = Header(default=None, alias="Cookie"),
        conn=Depends(get_gateway_conn),
        caller: ProviderCaller | None = Depends(get_provider_caller),
    ) -> dict[str, Any]:
        """Broker one AI generation for the gateway key's business: reserve → call shared key →
        settle. Returns only the generated text/content/model/usage — never the provider key."""
        body = body or {}
        raw_session_token = _session_token(body, app_session_token, cookie_header)
        try:
            return broker_message_for_business(
                conn,
                business_slug=principal.business_slug,
                raw_session_token=raw_session_token,
                body=body,
                caller=caller,
                audit_route="internal_ai_gateway",
            )
        except GatewayMessageError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=exc.headers or None,
            ) from exc

    return router
