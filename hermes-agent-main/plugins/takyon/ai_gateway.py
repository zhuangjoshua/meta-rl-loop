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

This is the Postgres successor to the old SQLite ``/generate`` route, with two
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
import logging
import uuid
from typing import Any, Callable

from fastapi import APIRouter, Body, Depends, Header, HTTPException

from agent.usage_pricing import usage_markup_bps

from .ai_provider import (
    AnthropicPricingUnavailable,
    TavilyPricingUnavailable,
    anthropic_key,
    anthropic_payload,
    anthropic_rates_microusd_per_token,
    anthropic_text,
    billed_microusd_cost,
    call_anthropic,
    call_tavily,
    tavily_key,
    tavily_request_microusd,
)
from . import app_entitlements, app_identity
from .app_gateway_keys import GatewayPrincipal, resolve_gateway_key
from .app_runtime_constants import APP_SESSION_COOKIE
from .app_usage import (
    AppBudgetExceeded,
    AppBudgetInactive,
    AppUserBudgetExceeded,
    AppUserNotFound,
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

logger = logging.getLogger(__name__)


def _settle_or_hold(
    conn,
    business_slug: str,
    reservation_key: str,
    *,
    actual_cost_microusd: int,
    **settle_kwargs,
) -> bool:
    """Finalize a reservation after the provider was ALREADY PAID. On a settle failure we must NOT
    ``release`` — the provider was paid, so releasing would forget real spend and undercharge. The
    reservation already holds the (>= actual) cost, so we keep the hold, log, and report
    non-fatally; the caller still returns the paid-for result instead of 500-ing into a retry that
    would mint a fresh ``reservation_key`` and double-spend (the proxy conn is autocommit, so the
    reserve is already committed and cannot be rolled back). A reconciliation pass can finalize the
    held row later. Returns True iff the row reached ``completed``."""
    try:
        settle_usage(
            conn,
            business_slug,
            reservation_key,
            actual_cost_microusd=actual_cost_microusd,
            **settle_kwargs,
        )
        return True
    except Exception:
        logger.exception(
            "settle_usage failed after provider was paid; holding reservation %s at its reserved cost",
            reservation_key,
        )
        return False


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


# A search caller is a server-side closure that already holds the shared Tavily key. The endpoint
# only ever sees this callable (or None when unconfigured) — never the key itself, exactly like
# ProviderCaller above.
SearchCaller = Callable[[dict], dict]


def get_search_caller() -> SearchCaller | None:
    """Resolve the SHARED platform Tavily key server-side and bind it into a caller closure.

    Returns None when no key is configured — the search broker then BLOCKS (503), never calling
    keyless. The key lives ONLY inside the returned closure; it is never returned to a caller and
    is not an argument the app supplies. Tests override this seam with a canned searcher, so no real
    key or network is needed to exercise the gate."""
    key = tavily_key()
    if not key:
        return None

    def _call(req: dict) -> dict:
        return call_tavily(str(req.get("endpoint") or "search"), req.get("payload") or {}, key)

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


def _require_active_entitlement(entitlement) -> None:
    if entitlement is None:
        raise GatewayMessageError(
            status_code=402,
            detail={"error": "subscription_required"},
        )


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


# Invariant 9 (GOAL_RULES §3): the $0.50 per-user free-tier FLOOR IS REMOVED. There is no free
# allowance — budget is plan-derived-or-0. This name is retained only as a 0-valued back-compat
# shim (value 0 == "no floor"); it is NOT referenced by `_user_weekly_budget_microusd` and must
# never be reintroduced as a positive fallback. The per-user limit is the active PAID subscription's
# `included_ai_budget_microusd` pro-rated to the weekly window; no plan ⇒ 0 ⇒ reserve refuses (402).
_DEFAULT_USER_MONTHLY_BUDGET_MICROUSD = 0


# Pro-rate the MONTHLY plan allowance onto the WEEKLY usage window. Operator decision (2026-06-20):
# the per-customer AI budget resets WEEKLY (matching the /account "resets weekly" copy + migration
# 0035's ISO-week window), so the spendable amount per weekly window is the monthly
# included_ai_budget pro-rated to a week (× 7/30). Cumulative spend over a ~30-day month then equals
# the monthly allowance — fixing the ~4.3× overspend a monthly allowance over a weekly window caused.
_USAGE_WINDOW_DAYS = 7
_PLAN_FUNDING_PERIOD_DAYS = 30


def _user_weekly_budget_microusd(plan) -> int:
    """THE canonical per-user AI-allowance resolver for the WEEKLY usage window (GOAL_RULES §3 gap #4:
    "centralize per-user-limit resolution ... unify to plan-derived-or-0"). This is the per-subuser
    gate that stops ONE subuser from draining the business and, post-invariant-9, the ONLY budget gate
    (there is no per-business pool cap anymore). ``app_actions._plan_derived_user_limit_microusd``
    delegates HERE so the gateway and action reserve paths share one rule.

    Plan-derived-or-0 with NO free-tier floor: budget comes ONLY from the active PAID subscription's
    ``included_ai_budget_microusd`` (the ``y`` term of x+y+z) — a MONTHLY figure capped at the monthly
    plan price. Because the usage window is the ISO WEEK, the spendable per-window amount is that
    monthly allowance pro-rated to a week (× 7/30), so a full month totals the monthly allowance. No
    plan ⇒ 0 (reserve refuses → 402). A free / unentitled tier ⇒ 0 (only a paid subscription funds)."""
    if plan is None:
        return 0
    if str(getattr(plan, "tier", "") or "").strip().lower() in {"", "free", "none", "unentitled"}:
        return 0
    monthly = max(0, int(plan.included_ai_budget_microusd))
    return monthly * _USAGE_WINDOW_DAYS // _PLAN_FUNDING_PERIOD_DAYS


def broker_provider_call(
    conn,
    business_slug: str,
    *,
    app_user,
    plan,
    provider: str,
    model: str,
    estimated_cost_microusd: int,
    purpose: str,
    audit_route: str,
    do_call,
    actual_cost,
    reserve_metadata: dict | None = None,
    provider_error_detail: str = "provider_error",
):
    """THE single metered envelope every paid product-provider call routes through — Anthropic,
    Tavily, and any future provider. Reserve the SERVER-computed estimate against the business budget
    under the atomic row lock (the ONE gate that can refuse spend), then call the provider, then
    settle the actual cost — or release the hold on a provider error. A provider that reaches this
    function cannot skip the reservation, so adding a new provider is "price it fail-closed in
    ``usage_pricing`` + call this", never "remember to wire the budget rails." ``do_call()`` performs
    the provider request and returns its raw response; ``actual_cost(raw)`` returns
    ``(actual_cost_microusd, settle_kwargs)``. Returns ``(raw, reservation_key, actual_cost, settled)``."""
    reservation_key = uuid.uuid4().hex
    try:
        reserve_usage(
            conn,
            business_slug,
            estimated_cost_microusd=estimated_cost_microusd,
            reservation_key=reservation_key,
            app_user_id=app_user.id,
            # The gate enforces this over the current (weekly) usage window; the value is the
            # monthly plan allowance pro-rated to the week (see _user_weekly_budget_microusd).
            user_monthly_limit_microusd=_user_weekly_budget_microusd(plan),
            app_user_tier=app_user.tier,
            purpose=purpose,
            route=audit_route,
            provider=provider,
            model=model,
            metadata=reserve_metadata or {},
        )
    except (AppBudgetInactive, AppBudgetExceeded, AppUserBudgetExceeded, AppUserNotFound) as exc:
        raise _gateway_reservation_error(exc) from exc

    try:
        raw = do_call()
    except Exception as exc:
        release_usage(conn, business_slug, reservation_key, error=str(exc))
        raise GatewayMessageError(status_code=502, detail=provider_error_detail) from exc

    cost, settle_kwargs = actual_cost(raw)
    settled = _settle_or_hold(
        conn,
        business_slug,
        reservation_key,
        actual_cost_microusd=cost,
        provider=provider,
        model=model,
        **settle_kwargs,
    )
    return raw, reservation_key, cost, settled


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
    validate app session -> reserve usage under the current plan + app budget ->
    call shared provider key server-side -> settle actual cost. The caller never
    sees the provider key and never gets to bypass the budget rails.
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
        # The usage rail reserves the BILLED estimate (provider cost + usage markup); the realized
        # provider estimate is recorded alongside it for money-truth.
        estimated_realized_cost, estimated_cost = billed_microusd_cost(
            model, estimated_input_tokens, estimated_output_tokens
        )
        rate_source = anthropic_rates_microusd_per_token(model)[2]
    except AnthropicPricingUnavailable as exc:
        raise GatewayMessageError(status_code=400, detail=str(exc)) from exc

    entitlement, plan = _resolve_plan_for_user(conn, business_slug, app_user)
    _require_active_entitlement(entitlement)
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
    def _anthropic_actual_cost(raw):
        # Anthropic reports cached prompt tokens in separate buckets and EXCLUDES them from
        # input_tokens. Bill them at their real cache rates instead of dropping them (which would
        # undercharge true provider cost on every cached call). The usage rail settles the BILLED
        # amount (realized provider cost + usage markup); realized is recorded in metadata for
        # money-truth so a settled row carries both numbers.
        usage = raw.get("usage") or {}
        in_tok = int(usage.get("input_tokens") or estimated_input_tokens)
        out_tok = int(usage.get("output_tokens") or 0)
        cr = int(usage.get("cache_read_input_tokens") or 0)
        cw = int(usage.get("cache_creation_input_tokens") or 0)
        realized_cost, billed = billed_microusd_cost(
            model, in_tok, out_tok, cache_read_tokens=cr, cache_write_tokens=cw
        )
        return billed, {
            "input_tokens": in_tok,
            "output_tokens": out_tok,
            "provider_request_id": str(raw.get("id") or ""),
            "metadata": {
                "cache_read_input_tokens": cr,
                "cache_creation_input_tokens": cw,
                "realized_cost_microusd": realized_cost,
                "billed_cost_microusd": billed,
            },
        }

    provider_response, _reservation_key, actual_cost, _settled = broker_provider_call(
        conn,
        business_slug,
        app_user=app_user,
        plan=plan,
        provider="anthropic",
        model=model,
        estimated_cost_microusd=estimated_cost,
        purpose=str(body.get("purpose") or "ai_generate"),
        audit_route=audit_route,
        reserve_metadata={
            "cost_rate_source": rate_source,
            "usage_markup_bps": usage_markup_bps(),
            "estimated_realized_cost_microusd": estimated_realized_cost,
            "estimated_billed_cost_microusd": estimated_cost,
        },
        do_call=lambda: caller(payload),
        actual_cost=_anthropic_actual_cost,
    )

    usage = provider_response.get("usage") or {}
    input_tokens = int(usage.get("input_tokens") or estimated_input_tokens)
    output_tokens = int(usage.get("output_tokens") or 0)

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


def _gateway_reservation_error(exc: Exception) -> GatewayMessageError:
    """Map an ``app_usage`` reservation failure to the structured gateway error. Shared by the
    message and search brokers so the two budget gates report a tenant's cap state identically."""
    if isinstance(exc, AppBudgetInactive):
        return GatewayMessageError(
            status_code=402,
            detail={"error": "app_budget_inactive", "status": exc.status},
        )
    if isinstance(exc, AppBudgetExceeded):
        return GatewayMessageError(
            status_code=402,
            detail={
                "error": "app_budget_exceeded",
                "hard_limit_microusd": exc.hard_limit_microusd,
                "committed_microusd": exc.committed_microusd,
                "requested_microusd": exc.requested_microusd,
                "remaining_microusd": exc.remaining_microusd,
            },
        )
    if isinstance(exc, AppUserBudgetExceeded):
        return GatewayMessageError(
            status_code=402,
            detail={
                "error": "app_user_budget_exceeded",
                "app_user_id": exc.app_user_id,
                "user_monthly_limit_microusd": exc.user_monthly_limit_microusd,
                "committed_microusd": exc.committed_microusd,
                "requested_microusd": exc.requested_microusd,
                "remaining_microusd": exc.remaining_microusd,
            },
        )
    if isinstance(exc, AppUserNotFound):
        return GatewayMessageError(status_code=400, detail="unknown_app_user")
    raise exc


def _normalize_search_results(raw: dict | None) -> list[dict[str, Any]]:
    """Map a Tavily /search response to a key-free result list (title/url/content/position)."""
    out: list[dict[str, Any]] = []
    for i, item in enumerate((raw or {}).get("results") or []):
        if not isinstance(item, dict):
            continue
        out.append(
            {
                "title": str(item.get("title") or ""),
                "url": str(item.get("url") or ""),
                "content": str(item.get("content") or ""),
                "position": i + 1,
            }
        )
    return out


def _normalize_extract_results(raw: dict | None) -> list[dict[str, Any]]:
    """Map a Tavily /extract response to a key-free document list (url/title/content)."""
    out: list[dict[str, Any]] = []
    for item in (raw or {}).get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        text = str(item.get("raw_content") or item.get("content") or "")
        out.append({"url": url, "title": str(item.get("title") or ""), "content": text})
    return out


_TAVILY_MAX_RESULTS = 10
_TAVILY_MAX_EXTRACT_URLS = 20


def broker_search_for_business(
    conn,
    *,
    business_slug: str,
    raw_session_token: str,
    body: dict | None = None,
    searcher: SearchCaller | None | object = _CALLER_UNSET,
    audit_route: str = "internal_search_gateway",
) -> dict[str, Any]:
    """Metered web-search broker — the Tavily sibling of ``broker_message_for_business``.

    Validate the app session → price the request FAIL-CLOSED from ``usage_pricing`` → reserve the
    cost against the business app budget (THE gate, atomic under the budget row lock) → call the
    SHARED Tavily key server-side → settle the fixed per-request cost (or release on failure). The
    caller never sees the Tavily key and cannot bypass the budget. This is the path that turns
    product-runtime web search from an ungated operator-billed money leak into metered app usage.
    """
    body = body or {}
    if searcher is _CALLER_UNSET:
        searcher = get_search_caller()

    if not raw_session_token:
        raise GatewayMessageError(status_code=401, detail="missing_app_session")
    app_user = app_identity.validate_session(conn, business_slug, raw_session_token)
    if app_user is None:
        raise GatewayMessageError(status_code=401, detail="invalid_app_session")
    requested_app_user_id = body.get("app_user_id") or body.get("appUserId") or None
    if requested_app_user_id and str(requested_app_user_id) != app_user.id:
        raise GatewayMessageError(status_code=403, detail="mismatched_app_user")

    # Invariant #8 (search): no Tavily key configured -> block. Checked after auth (callers cannot
    # probe config) and before any reservation.
    if searcher is None:
        raise GatewayMessageError(status_code=503, detail="search_unconfigured")

    operation = str(body.get("operation") or "search").strip().lower()
    if operation not in {"search", "extract"}:
        raise GatewayMessageError(status_code=400, detail="operation must be 'search' or 'extract'")

    if operation == "search":
        query = str(body.get("query") or "").strip()
        if not query:
            raise GatewayMessageError(status_code=400, detail="query is required")
        advanced = (
            str(body.get("depth") or body.get("search_depth") or "basic").strip().lower()
            == "advanced"
        )
        try:
            max_results = max(1, min(int(body.get("max_results") or 5), _TAVILY_MAX_RESULTS))
        except (TypeError, ValueError):
            max_results = 5
        pricing_op = "search_advanced" if advanced else "search"
        units = 1
        endpoint = "search"
        provider_payload = {
            "query": query,
            "max_results": max_results,
            "search_depth": "advanced" if advanced else "basic",
            "include_raw_content": False,
            "include_images": False,
        }
    else:  # extract
        raw_urls = body.get("urls") or body.get("url") or []
        if isinstance(raw_urls, str):
            raw_urls = [raw_urls]
        urls = [str(u).strip() for u in raw_urls if str(u).strip()][:_TAVILY_MAX_EXTRACT_URLS]
        if not urls:
            raise GatewayMessageError(status_code=400, detail="urls is required for extract")
        pricing_op = "extract"
        units = (len(urls) + 4) // 5  # Tavily bills 1 credit per 5 URLs
        endpoint = "extract"
        provider_payload = {"urls": urls, "include_images": False}

    # Server-side, fail-closed price. An unpriced operation is refused BEFORE any reservation or
    # provider call — a new search depth can never spend budget unpriced.
    try:
        cost = tavily_request_microusd(pricing_op, units=units)
    except TavilyPricingUnavailable as exc:
        raise GatewayMessageError(status_code=400, detail=str(exc)) from exc

    entitlement, plan = _resolve_plan_for_user(conn, business_slug, app_user)
    _require_active_entitlement(entitlement)
    feature_name = str(body.get("feature") or "web_search").strip() or "web_search"
    if plan is not None and not _feature_allowed(plan, feature_name):
        raise GatewayMessageError(
            status_code=403,
            detail={"error": "feature_not_in_plan", "feature": feature_name},
        )

    # Fixed per-request price: estimate == actual, so the held amount IS the truth on settle.
    raw, _reservation_key, _actual_cost, settled = broker_provider_call(
        conn,
        business_slug,
        app_user=app_user,
        plan=plan,
        provider="tavily",
        model=pricing_op,
        estimated_cost_microusd=cost,
        purpose=feature_name,
        audit_route=audit_route,
        reserve_metadata={"operation": operation, "units": units},
        do_call=lambda: searcher({"endpoint": endpoint, "payload": provider_payload}),
        actual_cost=lambda _raw: (cost, {"metadata": {"operation": operation, "units": units}}),
        provider_error_detail="search_provider_error",
    )

    results = (
        _normalize_search_results(raw)
        if operation == "search"
        else _normalize_extract_results(raw)
    )
    return {
        "success": True,
        "operation": operation,
        "results": results,
        "usage": {
            "provider": "tavily",
            "operation": pricing_op,
            "units": units,
            "cost_microusd": cost,
            "settled": settled,
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

    @router.post("/search")
    def web_search(
        body: dict | None = Body(default=None),
        principal: GatewayPrincipal = Depends(_gateway_principal),
        app_session_token: str | None = Header(default=None, alias=_APP_SESSION_HEADER),
        cookie_header: str | None = Header(default=None, alias="Cookie"),
        conn=Depends(get_gateway_conn),
        searcher: SearchCaller | None = Depends(get_search_caller),
    ) -> dict[str, Any]:
        """Broker one metered web search/extract for the gateway key's business: price → reserve →
        call the shared Tavily key → settle. Returns only normalized results + usage — never the
        provider key. Same auth/budget rails as ``/messages``; this is the metered alternative to a
        product app reaching Tavily ungated."""
        body = body or {}
        raw_session_token = _session_token(body, app_session_token, cookie_header)
        try:
            return broker_search_for_business(
                conn,
                business_slug=principal.business_slug,
                raw_session_token=raw_session_token,
                body=body,
                searcher=searcher,
                audit_route="internal_search_gateway",
            )
        except GatewayMessageError as exc:
            raise HTTPException(
                status_code=exc.status_code,
                detail=exc.detail,
                headers=exc.headers or None,
            ) from exc

    return router
