"""Dedicated Safebox service app.

This is the service boundary for Safebox when it runs on its own VPS. The
runtime planes talk to it over HTTP; the service itself still uses the local
Safebox authority module as the single backing implementation.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import ipaddress
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from contextlib import contextmanager
from typing import Any, Iterable

from fastapi import FastAPI, Header, HTTPException, Query, Request
from pydantic import BaseModel

from . import safebox
from .request_limits import RequestBodyLimitMiddleware, request_method_may_have_body
from .safebox_capability import CapabilityScope, mint_capability, verify_capability
from .safebox_nonce import pg_claim_nonce

_SAFEBOX_TOKEN_ENV = "TAKYON_SAFEBOX_TOKEN"
_CLOUDFLARE_AIG_TOKEN_ENV = "CLOUDFLARE_AIG_TOKEN"
_CLOUDFLARE_AIG_ACCOUNT_ID_ENV = "CLOUDFLARE_AIG_ACCOUNT_ID"
_CLOUDFLARE_AIG_GATEWAY_ID_ENV = "CLOUDFLARE_AIG_GATEWAY_ID"
_CLOUDFLARE_AIG_GATEWAY_ID_DEFAULT = "takyon-subuser"
_CLOUDFLARE_AIG_BASE = "https://gateway.ai.cloudflare.com/v1"

# The capability signing key is a SAFEBOX-ONLY secret: it is read from the process env on the
# safebox host and is NEVER written to any client .env (that is the whole point — a client cannot
# mint a token for another tenant nor raise its own ceiling). Read it via os.environ here (this is
# safebox-internal code on the safebox host, not a business runtime), and fail closed if absent so a
# misconfigured host can never broker or mint without a key.
_CAP_SIGNING_KEY_ENV = "TAKYON_CAP_SIGNING_KEY"
_OPERATOR_CLIENTS_ENV = "TAKYON_SAFEBOX_OPERATOR_CLIENTS"
_OPERATOR_TOKEN_ENV = "TAKYON_SAFEBOX_OPERATOR_TOKEN"
_OPERATOR_TOKEN_HEADER = "x-takyon-operator-token"

# Per-action audience + provider-key aliases + pricing seam. The audience binds a minted token to
# exactly one provider action; mismatched audiences are rejected by verify_capability. Key aliases
# mirror the canonical resolvers (ai_provider.anthropic_key / tavily_key,
# creative_gateway._GEMINI_KEY_ALIASES) so "what key does this action use" lives in ONE place.
_ANTHROPIC_AUDIENCE = "anthropic.messages"
_OPENAI_AUDIENCE = "openai.messages"
_TAVILY_AUDIENCE = "tavily.search"
_GEMINI_IMAGE_AUDIENCE = "gemini.image"
_EGRESS_AUDIENCE = "connection.egress"
_POSTMARK_SEND_AUDIENCE = "postmark.send"

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
_CREATIVE_SITE_IMAGE_AUDIENCE = "creative.site_image"
_CREATIVE_X_PUBLISH_AUDIENCE = "creative.x_publish"
_CREATIVE_REDDIT_PUBLISH_AUDIENCE = "creative.reddit_publish"
_CREATIVE_META_AD_LAUNCH_AUDIENCE = "creative.meta_ad_launch"
_CREATIVE_REDDIT_AD_LAUNCH_AUDIENCE = "creative.reddit_ad_launch"
_CREATIVE_META_AD_MEDIA_SPEND_AUDIENCE = "creative.meta_ad_media_spend"
_CREATIVE_REDDIT_AD_MEDIA_SPEND_AUDIENCE = "creative.reddit_ad_media_spend"
# App Store rail: a mobile release reserves/commits credits through the creative gate like any paid
# creative action. It has NO provider route (the EAS build uses operator-rail custody, not a
# safebox-vended key), so it appears only in the credit-action map below, never in the provider
# audience sets.
_CREATIVE_MOBILE_RELEASE_AUDIENCE = "creative.mobile_release"

# Creative action (capability `action`, also the mint action) -> its canonical creative-credit cost
# action key in core._CREATIVE_CREDIT_COST_DEFAULTS/_ENVS. The fixed price the client used and the
# price the safebox reserves both resolve from that ONE canonical table (env-override-first), so there
# is no second price table on the safebox.
_CREATIVE_AUDIENCE_CREDIT_ACTION = {
    _CREATIVE_LOGO_AUDIENCE: "logo_generate",
    _CREATIVE_UGC_AUDIENCE: "ugc_ad_generate",
    _CREATIVE_STATIC_AD_AUDIENCE: "static_ad_generate",
    _CREATIVE_SITE_IMAGE_AUDIENCE: "site_image_generate",
    _CREATIVE_X_PUBLISH_AUDIENCE: "x_publish_outreach",
    _CREATIVE_REDDIT_PUBLISH_AUDIENCE: "reddit_publish_outreach",
    _CREATIVE_META_AD_LAUNCH_AUDIENCE: "meta_ad_launch",
    _CREATIVE_REDDIT_AD_LAUNCH_AUDIENCE: "reddit_ad_launch",
    _CREATIVE_META_AD_MEDIA_SPEND_AUDIENCE: "meta_ad_media_spend",
    _CREATIVE_REDDIT_AD_MEDIA_SPEND_AUDIENCE: "reddit_ad_media_spend",
    _CREATIVE_MOBILE_RELEASE_AUDIENCE: "mobile_release",
}

# Which creative audiences each gated creative PROVIDER route accepts. A logo capability may only hit
# Gemini; a UGC capability may hit OpenAI (the reference image) AND FAL (the clips); a static-ad
# capability may hit OpenAI. This binds the reserved creative action to exactly the providers that
# action legitimately uses, so a cheap action's token cannot drive an unrelated provider.
_CREATIVE_GEMINI_AUDIENCES = frozenset({_CREATIVE_LOGO_AUDIENCE, _CREATIVE_SITE_IMAGE_AUDIENCE})
_CREATIVE_OPENAI_AUDIENCES = frozenset(
    {_CREATIVE_UGC_AUDIENCE, _CREATIVE_STATIC_AD_AUDIENCE}
)
_CREATIVE_FAL_AUDIENCES = frozenset({_CREATIVE_UGC_AUDIENCE})

# Default action -> audience so a token minted for a known provider action is directly brokerable by
# the matching provider route without the caller having to restate the audience. A caller may still
# pass an explicit `audience` to mint for a future/custom action.
_ACTION_AUDIENCE_DEFAULTS = {
    _ANTHROPIC_AUDIENCE: _ANTHROPIC_AUDIENCE,
    _OPENAI_AUDIENCE: _OPENAI_AUDIENCE,
    _TAVILY_AUDIENCE: _TAVILY_AUDIENCE,
    _GEMINI_IMAGE_AUDIENCE: _GEMINI_IMAGE_AUDIENCE,
    _EGRESS_AUDIENCE: _EGRESS_AUDIENCE,
}

# Default short TTL for minted capability tokens (seconds). The token is also single-use (nonce) and
# audience-bound, so a leaked token does exactly one {tenant, action, <=cost} thing within this window.
_CAP_TTL_SECONDS = 300
_SAFEBOX_BODY_DEFAULT_LIMIT = 8 * 1024 * 1024
_SAFEBOX_BODY_LARGE_PROVIDER_LIMIT = 32 * 1024 * 1024
_SAFEBOX_BODY_STRIPE_LIMIT = 512 * 1024
_SAFEBOX_BODY_CONNECTION_LIMIT = 128 * 1024


def _safebox_body_limit(scope: dict[str, Any]) -> int:
    path = str(scope.get("path") or "")
    if path == "/v1/stripe/request" or path == "/v1/billing/webhook/process":
        return _SAFEBOX_BODY_STRIPE_LIMIT
    if path in {"/v1/connections/deposit", "/v1/connections/rebind"}:
        return _SAFEBOX_BODY_CONNECTION_LIMIT
    if path.startswith("/v1/providers/") or path == "/v1/storage/put":
        return _SAFEBOX_BODY_LARGE_PROVIDER_LIMIT
    return _SAFEBOX_BODY_DEFAULT_LIMIT


def _normalize_stripe_request(path: str, method: str, params: dict[str, Any] | None) -> tuple[str, str, dict[str, Any]]:
    stripe_path = str(path or "").strip().lstrip("/")
    stripe_method = str(method or "POST").strip().upper()
    if not stripe_path or "?" in stripe_path or "\\" in stripe_path or ".." in stripe_path.split("/"):
        raise HTTPException(status_code=403, detail="stripe_path_not_allowed")
    clean_params = dict(params or {})
    if stripe_method == "POST" and stripe_path in {"products", "prices", "checkout/sessions"}:
        _require_takyon_app_stripe_params(stripe_path, clean_params)
        return stripe_path, stripe_method, clean_params
    raise HTTPException(status_code=403, detail="stripe_path_not_allowed")


_STRIPE_CATALOG_MUTATION_PATHS = frozenset({"products", "prices"})


def _stripe_checkout_disabled() -> bool:
    raw = os.environ.get("TAKYON_STRIPE_CHECKOUT_DISABLED")
    if raw is None:
        raw = safebox.load_env().get("TAKYON_STRIPE_CHECKOUT_DISABLED")
    normalized = str(raw or "").strip().lower()
    if not normalized:
        # A production live-key deploy must not become a money-moving activation merely because
        # the pause flag was omitted. The operator explicitly writes 0 only after the durable DB
        # cutover and webhook proofs have completed; dev/test keeps its existing open default.
        return str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live"
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live"


def _require_stripe_checkout_enabled() -> None:
    if _stripe_checkout_disabled():
        raise HTTPException(status_code=503, detail="stripe_checkout_paused")


def _specialized_checkout_disabled(name: str) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        raw = safebox.load_env().get(name)
    normalized = str(raw or "").strip().lower()
    if not normalized:
        return str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live"
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live"


def _require_operator_checkout_enabled() -> None:
    if _specialized_checkout_disabled("TAKYON_STRIPE_OPERATOR_CHECKOUT_DISABLED"):
        raise HTTPException(status_code=503, detail="stripe_operator_checkout_paused")


def _require_creative_checkout_enabled() -> None:
    if _specialized_checkout_disabled("TAKYON_STRIPE_CREATIVE_CHECKOUT_DISABLED"):
        raise HTTPException(status_code=503, detail="stripe_creative_checkout_paused")


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


def _storage_business_slug(path: str, *, require_existing: bool = True) -> str:
    raw = str(path or "").strip().strip("/")
    if not raw:
        raise HTTPException(status_code=403, detail="storage_scope_required")
    business = _require_safe_slug(raw.split("/", 1)[0])
    if require_existing:
        return _require_existing_business(business)
    return business


def _require_safe_media_id(value: str) -> str:
    media_id = str(value or "").strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}", media_id):
        raise HTTPException(status_code=403, detail="unsafe_media_id")
    return media_id


def _app_media_storage_key(business: str, media_id: str) -> str:
    return f"media/{_require_safe_slug(business)}/{_require_safe_media_id(media_id)}"


def _company_base_domain() -> str:
    """The allowed product-host base domain for THIS environment (UC3 environment seam).

    Resolution is exactly the ``PUBLIC_COMPANY_BASE_DOMAIN`` / ``TAKYON_COMPANY_BASE_DOMAIN``
    config this safebox host already honors for product-host scoping (``_domain_business_slug``):
    process env first, then the safebox env store. Unset everywhere -> ``coscale.app``, so prod
    behavior is byte-identical. Only an environment that EXPLICITLY declares its base domain
    (``environments/dev.yaml`` ``domains.company_base`` -> the dev safebox's env) shifts which
    product hosts are acceptable — and only to that single declared base; every other host keeps
    failing closed."""
    return str(
        os.environ.get("PUBLIC_COMPANY_BASE_DOMAIN")
        or safebox.load_env().get("PUBLIC_COMPANY_BASE_DOMAIN")
        or os.environ.get("TAKYON_COMPANY_BASE_DOMAIN")
        or safebox.load_env().get("TAKYON_COMPANY_BASE_DOMAIN")
        or "coscale.app"
    ).strip().lower().strip(".")


def _domain_business_slug(domain: str) -> str:
    name = str(domain or "").strip().lower().strip(".")
    base = _company_base_domain()
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
        if any(str(key).startswith("line_items[") for key in params):
            raise HTTPException(status_code=403, detail="stripe_checkout_pricing_client_forbidden")
        forbidden_prefixes = (
            "branding_settings[",
            "discounts[",
            "automatic_tax[",
            "subscription_data[billing_mode]",
        )
        if any(str(key).startswith(forbidden_prefixes) for key in params) or any(
            key in params for key in ("allow_promotion_codes", "subscription_data[trial_period_days]")
        ):
            raise HTTPException(status_code=403, detail="stripe_checkout_presentation_client_forbidden")
        for url_key in ("success_url", "cancel_url"):
            _require_app_checkout_redirect_url(str(params.get(url_key) or ""), business=business)
    return business


def _require_app_checkout_redirect_url(raw_url: str, *, business: str) -> None:
    url = str(raw_url or "").strip()
    if not url or any(ch.isspace() for ch in url):
        raise HTTPException(status_code=403, detail="stripe_redirect_not_allowed")
    parsed = urllib.parse.urlsplit(url)
    host = str(parsed.hostname or "").strip().lower()
    # Env-aware product host: <slug>.<declared company base>, defaulting to <slug>.coscale.app
    # (prod byte-identical when nothing is declared — see _company_base_domain).
    expected_host = f"{_require_safe_slug(business)}.{_company_base_domain()}"
    path = str(parsed.path or "")
    if (
        parsed.scheme != "https"
        or parsed.username
        or parsed.password
        or host != expected_host
        or (path != "/app" and not path.startswith("/app/"))
    ):
        raise HTTPException(status_code=403, detail="stripe_redirect_not_allowed")


def _db_row_value(row: Any, index: int, key: str) -> Any:
    if row is None:
        return None
    if isinstance(row, dict):
        return row.get(key)
    try:
        return row[key]
    except Exception:
        try:
            return row[index]
        except Exception:
            return None


def _stripe_key_livemode() -> bool:
    from . import stripe_util

    return stripe_util.stripe_key_livemode()


def _stripe_account_snapshot() -> tuple[str, bool]:
    account = safebox.stripe_request("account", {}, method="GET")
    if not isinstance(account, dict):
        raise HTTPException(status_code=502, detail="stripe_account_invalid")
    account_id = str(account.get("id") or "").strip()
    if not account_id.startswith("acct_") or account.get("object") not in {None, "account"}:
        raise HTTPException(status_code=502, detail="stripe_account_invalid")
    configured_id = str(os.environ.get("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()
    if configured_id and configured_id != account_id:
        raise HTTPException(status_code=503, detail="stripe_account_mismatch")
    return account_id, _stripe_key_livemode()


def _plan_stripe_metadata(plan: dict[str, Any], *, account_id: str) -> dict[str, str]:
    from . import app_entitlements

    business = str(plan.get("business_slug") or "").strip()
    return {
        "business": business,
        "business_id": business,
        "plan_key": str(plan.get("plan_key") or "").strip(),
        "source": "takyon_app",
        "economics_version": app_entitlements.plan_economics_version_from_mapping(plan),
        "tier": str(plan.get("tier") or ""),
        "currency": str(plan.get("currency") or "usd").lower(),
        "price_cents": str(int(plan.get("price_cents") or 0)),
        "billing_interval": str(plan.get("billing_interval") or "month"),
        "included_ai_budget_microusd": str(int(plan.get("included_ai_budget_microusd") or 0)),
        "included_action_quota": str(int(plan.get("included_action_quota") or 0)),
        "takyon_stripe_account_id": account_id,
    }


_CHECKOUT_BRANDING_SCHEMA = "takyon.stripe.checkout_branding.v1"
_CHECKOUT_BRANDING_ALLOWED_KEYS = frozenset(
    {
        "branding_settings[background_color]",
        "branding_settings[border_style]",
        "branding_settings[button_color]",
        "branding_settings[display_name]",
        "branding_settings[logo][type]",
        "branding_settings[logo][url]",
        "line_items[0][price_data][product_data][images][0]",
    }
)
_CHECKOUT_BRANDING_COLOR_RE = re.compile(r"^#[0-9a-f]{6}$")


def _allowlisted_checkout_branding_params(value: Any, *, business: str) -> dict[str, str]:
    """Validate and forward a precompiled operator-owned snapshot; never derive branding here."""
    if not isinstance(value, dict) or value.get("schema") != _CHECKOUT_BRANDING_SCHEMA:
        return {}
    if not str(value.get("source_build_id") or "").strip():
        return {}
    params = value.get("params")
    if not isinstance(params, dict) or not set(map(str, params)).issubset(
        _CHECKOUT_BRANDING_ALLOWED_KEYS
    ):
        return {}
    normalized = {str(key): str(raw) for key, raw in params.items() if isinstance(raw, str)}
    if len(normalized) != len(params):
        return {}

    display_name = normalized.get("branding_settings[display_name]", "")
    if not display_name or len(display_name) > 100 or any(ord(ch) < 32 for ch in display_name):
        return {}
    for key in ("branding_settings[background_color]", "branding_settings[button_color]"):
        if key in normalized and not _CHECKOUT_BRANDING_COLOR_RE.fullmatch(normalized[key]):
            return {}
    if normalized.get("branding_settings[border_style]") not in {
        None,
        "pill",
        "rectangular",
        "rounded",
    }:
        return {}

    logo_type = normalized.get("branding_settings[logo][type]")
    logo_url = normalized.get("branding_settings[logo][url]")
    product_image = normalized.get(
        "line_items[0][price_data][product_data][images][0]"
    )
    if any(item is not None for item in (logo_type, logo_url, product_image)):
        if logo_type != "url" or not logo_url or product_image != logo_url:
            return {}
        parsed = urllib.parse.urlsplit(logo_url)
        try:
            parsed_port = parsed.port
        except ValueError:
            return {}
        if (
            parsed.scheme != "https"
            or parsed.username
            or parsed.password
            or parsed_port not in {None, 443}
            or str(parsed.hostname or "").lower()
            != f"{_require_safe_slug(business)}.{_company_base_domain()}"
            or parsed.path != "/brand-logo.png"
            or parsed.query
            or parsed.fragment
        ):
            return {}
    return normalized


def _expected_stripe_livemode(plan: dict[str, Any]) -> bool:
    if str(plan.get("business_mode") or "").strip().lower() != "live":
        raise HTTPException(status_code=409, detail="stripe_business_not_live")
    # The prod-shaped dev twin intentionally uses Stripe's test universe. Production units leave
    # TAKYON_ENV unset or set it to prod, so a live business there requires a live key/object.
    return str(os.environ.get("TAKYON_ENV") or "prod").strip().lower() not in {"dev", "test"}


def _require_exact_stripe_metadata(payload: dict[str, Any], expected: dict[str, str]) -> None:
    if not isinstance(payload, dict):
        raise HTTPException(status_code=502, detail="stripe_catalog_invalid")
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    if any(str(metadata.get(key) or "") != value for key, value in expected.items()):
        raise HTTPException(status_code=403, detail="stripe_catalog_metadata_mismatch")


def _stripe_object_id(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        return str(value.get("id") or "").strip()
    return ""


def _require_app_subscription_proof(
    subscription: Any,
    subscription_id: str,
    *,
    business: str,
    detail: str,
) -> dict[str, Any]:
    expected_livemode = (
        str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live"
    )
    metadata = (
        subscription.get("metadata")
        if isinstance(subscription, dict)
        and isinstance(subscription.get("metadata"), dict)
        else {}
    )
    expected_account_id = str(os.getenv("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()
    if (
        not isinstance(subscription, dict)
        or str(subscription.get("id") or "") != subscription_id
        or subscription.get("object") not in {None, "subscription"}
        or subscription.get("livemode") is not expected_livemode
        or metadata.get("source") != "takyon_app"
        or str(metadata.get("business") or "").strip() != business
        or (
            expected_livemode
            and (
                not expected_account_id
                or metadata.get("takyon_stripe_account_id") != expected_account_id
            )
        )
    ):
        raise HTTPException(status_code=503, detail=detail)
    return subscription


def _stripe_payment_subscription_binding(
    payment_intent_id: str, charge_id: str
) -> tuple[str, str] | None:
    if not payment_intent_id and not charge_id:
        return None
    with _safebox_db_conn() as conn:
        rows = conn.execute(
            "select distinct r.business_slug, "
            "coalesce(nullif(r.metadata->>'stripe_subscription_id', ''), "
            "s.stripe_subscription_id) "
            "from app_revenue_events r "
            "left join app_checkout_sessions s on "
            "s.stripe_checkout_session_id = r.stripe_checkout_session_id "
            "where r.revenue_type in ('checkout', 'subscription_renewal') and "
            "((%s <> '' and coalesce(r.metadata->'stripe_payment_intent_ids', "
            "'[]'::jsonb) ? %s) or "
            "(%s <> '' and coalesce(r.metadata->'stripe_charge_ids', "
            "'[]'::jsonb) ? %s))",
            (payment_intent_id, payment_intent_id, charge_id, charge_id),
        ).fetchall()
    bindings = {
        (str(row[0] or "").strip(), str(row[1] or "").strip())
        for row in rows
        if str(row[0] or "").strip() and str(row[1] or "").strip()
    }
    return next(iter(bindings)) if len(bindings) == 1 else None


def _stripe_invoice_with_all_payments(invoice_id: str) -> dict[str, Any]:
    """Retrieve one Invoice plus the complete paginated InvoicePayment mapping."""
    invoice_ref = str(invoice_id or "").strip()
    if not invoice_ref:
        return {}
    invoice = safebox.stripe_request(f"invoices/{invoice_ref}", {}, method="GET")
    if not isinstance(invoice, dict) or str(invoice.get("id") or "") != invoice_ref:
        return {}
    payments: list[dict[str, Any]] = []
    starting_after = ""
    for _ in range(100):
        params: dict[str, Any] = {"invoice": invoice_ref, "limit": 100}
        if starting_after:
            params["starting_after"] = starting_after
        page = safebox.stripe_request("invoice_payments", params, method="GET")
        rows = page.get("data") if isinstance(page, dict) else None
        if not isinstance(rows, list):
            return {}
        typed_rows = [row for row in rows if isinstance(row, dict)]
        payments.extend(typed_rows)
        if not bool(page.get("has_more")):
            break
        next_cursor = str((typed_rows[-1] if typed_rows else {}).get("id") or "")
        if not next_cursor or next_cursor == starting_after:
            return {}
        starting_after = next_cursor
    else:
        return {}
    invoice["payments"] = {
        "object": "list",
        "url": "/v1/invoice_payments",
        "has_more": False,
        "data": payments,
    }
    return invoice


def _validate_checkout_catalog(
    plan: dict[str, Any], *, price: dict[str, Any], product: dict[str, Any],
    account_id: str, key_livemode: bool,
) -> None:
    expected_live = _expected_stripe_livemode(plan)
    if key_livemode is not expected_live:
        raise HTTPException(status_code=409, detail="stripe_mode_mismatch")
    expected_price_id = str(plan.get("stripe_price_id") or "").strip()
    expected_product_id = str(plan.get("stripe_product_id") or "").strip()
    product_id = _stripe_object_id(price.get("product"))
    recurring = price.get("recurring") if isinstance(price.get("recurring"), dict) else {}
    if (
        price.get("object") != "price"
        or str(price.get("id") or "") != expected_price_id
        or price.get("active") is not True
        or price.get("livemode") is not expected_live
        or str(price.get("type") or "") != "recurring"
        or str(price.get("currency") or "").lower() != str(plan.get("currency") or "usd").lower()
        or int(price.get("unit_amount") or -1) != int(plan.get("price_cents") or 0)
        or str(recurring.get("interval") or "") != str(plan.get("billing_interval") or "month")
        or int(recurring.get("interval_count") or 1) != 1
        or not expected_product_id
        or product_id != expected_product_id
    ):
        raise HTTPException(status_code=403, detail="stripe_price_economics_mismatch")
    if (
        product.get("object") != "product"
        or str(product.get("id") or "") != expected_product_id
        or product.get("active") is not True
        or product.get("livemode") is not expected_live
    ):
        raise HTTPException(status_code=403, detail="stripe_product_scope_mismatch")
    expected_metadata = _plan_stripe_metadata(plan, account_id=account_id)
    _require_exact_stripe_metadata(price, expected_metadata)
    _require_exact_stripe_metadata(product, expected_metadata)


def _load_catalog_plan(business: str, plan_key: str) -> dict[str, Any]:
    with _safebox_db_conn() as conn:
        row = conn.execute(
            """
            select p.stripe_price_id, p.stripe_product_id, p.tier, p.price_cents,
                   p.currency, p.billing_interval, p.included_ai_budget_microusd,
                   p.included_action_quota, p.metadata, p.saleable, b.mode
            from app_plan_policies p
            join businesses b on b.slug = p.business_slug
            where p.business_slug = %s and p.plan_key = %s
            limit 1
            """,
            (business, plan_key),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=403, detail="stripe_plan_scope_required")
    plan = {
        "business_slug": business,
        "plan_key": plan_key,
        "stripe_price_id": str(_db_row_value(row, 0, "stripe_price_id") or "").strip(),
        "stripe_product_id": str(_db_row_value(row, 1, "stripe_product_id") or "").strip(),
        "tier": str(_db_row_value(row, 2, "tier") or ""),
        "price_cents": int(_db_row_value(row, 3, "price_cents") or 0),
        "currency": str(_db_row_value(row, 4, "currency") or "usd"),
        "billing_interval": str(_db_row_value(row, 5, "billing_interval") or "month"),
        "included_ai_budget_microusd": int(
            _db_row_value(row, 6, "included_ai_budget_microusd") or 0
        ),
        "included_action_quota": int(_db_row_value(row, 7, "included_action_quota") or 0),
        "metadata": _db_row_value(row, 8, "metadata")
        if isinstance(_db_row_value(row, 8, "metadata"), dict) else {},
        "saleable": bool(_db_row_value(row, 9, "saleable")),
        "business_mode": str(_db_row_value(row, 10, "mode") or ""),
    }
    from . import app_entitlements
    if not app_entitlements.plan_is_saleable(plan):
        raise HTTPException(status_code=409, detail="stripe_plan_not_saleable")
    return plan


def _prepare_catalog_mutation(path: str, params: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    business = _require_safe_slug(_metadata_value(params, "business"))
    plan_key = _require_safe_slug(_metadata_value(params, "plan_key"))
    plan = _load_catalog_plan(business, plan_key)
    account_id, key_livemode = _stripe_account_snapshot()
    expected_live = _expected_stripe_livemode(plan)
    if key_livemode is not expected_live:
        raise HTTPException(status_code=409, detail="stripe_mode_mismatch")
    expected_metadata = _plan_stripe_metadata(plan, account_id=account_id)
    for key, value in expected_metadata.items():
        if key == "takyon_stripe_account_id":
            params[f"metadata[{key}]"] = value
        elif _metadata_value(params, key) != value:
            raise HTTPException(status_code=403, detail="stripe_catalog_metadata_mismatch")
    if path == "prices":
        if (
            int(params.get("unit_amount") or -1) != int(plan["price_cents"])
            or str(params.get("currency") or "").lower() != str(plan["currency"]).lower()
            or str(params.get("recurring[interval]") or "") != str(plan["billing_interval"])
        ):
            raise HTTPException(status_code=403, detail="stripe_price_economics_mismatch")
        product_id = str(params.get("product") or "").strip()
        if not product_id:
            raise HTTPException(status_code=403, detail="stripe_product_scope_mismatch")
        product = safebox.stripe_request(f"products/{product_id}", {}, method="GET")
        if (
            product.get("object") != "product"
            or str(product.get("id") or "") != product_id
            or product.get("active") is not True
            or product.get("livemode") is not expected_live
        ):
            raise HTTPException(status_code=403, detail="stripe_product_scope_mismatch")
        _require_exact_stripe_metadata(product, expected_metadata)
    return plan, expected_live


def _claim_app_checkout_intent_authority(params: dict[str, Any]) -> dict[str, Any]:
    """Checkout creation authority is the recorded app checkout intent, not the bearer token.

    The product runtime creates the intent through the app DB plane after validating the customer's
    app session. Safebox refuses to create a Stripe Checkout Session unless that intent and plan exist
    and match the submitted scope. The caller supplies no pricing fields: Safebox atomically claims
    the still-unused intent and derives the exact monthly price from app_plan_policies.
    """
    business = _require_safe_slug(_metadata_value(params, "business"))
    plan_key = _require_safe_slug(_metadata_value(params, "plan_key"))
    intent_id = str(_metadata_value(params, "checkout_intent_id") or "").strip()
    if not intent_id:
        raise HTTPException(status_code=403, detail="stripe_checkout_scope_required")
    if str(params.get("mode") or "subscription").strip() != "subscription":
        raise HTTPException(status_code=403, detail="stripe_checkout_mode_not_allowed")
    if any(
        str(key).startswith(
            (
                "line_items[",
                "branding_settings[",
                "discounts[",
                "automatic_tax[",
                "subscription_data[billing_mode]",
            )
        )
        for key in params
    ) or any(key in params for key in ("allow_promotion_codes", "subscription_data[trial_period_days]")):
        raise HTTPException(status_code=403, detail="stripe_checkout_pricing_client_forbidden")
    submitted_email = str(params.get("customer_email") or "").strip().lower()
    submitted_reference = str(params.get("client_reference_id") or "").strip()
    account_id, key_livemode = _stripe_account_snapshot()
    expected_livemode = _expected_stripe_livemode({"business_mode": "live"})
    if key_livemode is not expected_livemode:
        raise HTTPException(status_code=409, detail="stripe_mode_mismatch")
    live_target_account_id: str | None = None
    if expected_livemode:
        configured_account_id = str(os.getenv("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()
        if not configured_account_id or configured_account_id != account_id:
            raise HTTPException(
                status_code=503, detail="stripe_live_account_binding_required"
            )
        live_target_account_id = configured_account_id
    with _safebox_db_conn() as conn:
        row = conn.execute(
            """
            select * from takyon_safebox_claim_app_checkout_intent(
                %s::uuid, %s, %s, %s, %s, %s
            )
            """,
            (
                intent_id,
                business,
                plan_key,
                submitted_email,
                submitted_reference,
                live_target_account_id,
            ),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=409, detail="stripe_checkout_intent_not_open")
    intent_email = str(_db_row_value(row, 2, "customer_email") or "").strip().lower()
    client_reference_id = str(
        _db_row_value(row, 3, "client_reference_id") or ""
    ).strip()
    price_cents = int(_db_row_value(row, 4, "price_cents") or 0)
    currency = str(_db_row_value(row, 5, "currency") or "").strip().lower()
    interval = str(_db_row_value(row, 6, "billing_interval") or "").strip().lower()
    tier = str(_db_row_value(row, 7, "tier") or "").strip()
    included_ai_budget_microusd = int(
        _db_row_value(row, 8, "included_ai_budget_microusd") or 0
    )
    included_action_quota = int(_db_row_value(row, 9, "included_action_quota") or 0)
    plan_metadata = _db_row_value(row, 10, "plan_metadata")
    plan_metadata = plan_metadata if isinstance(plan_metadata, dict) else {}
    business_mode = str(_db_row_value(row, 11, "business_mode") or "").strip().lower()
    checkout_branding = _db_row_value(row, 12, "checkout_branding")
    if price_cents <= 0 or currency != "usd" or interval != "month":
        raise HTTPException(status_code=409, detail="stripe_checkout_plan_not_billable")
    plan = {
        "business_slug": business,
        "plan_key": plan_key,
        "tier": tier,
        "price_cents": price_cents,
        "currency": currency,
        "billing_interval": interval,
        "included_ai_budget_microusd": included_ai_budget_microusd,
        "included_action_quota": included_action_quota,
        "metadata": plan_metadata,
        "saleable": True,
        "business_mode": business_mode,
    }
    if _expected_stripe_livemode(plan) is not expected_livemode:
        raise HTTPException(status_code=409, detail="stripe_mode_mismatch")
    binding = _plan_stripe_metadata(plan, account_id=account_id)
    authoritative: dict[str, Any] = _allowlisted_checkout_branding_params(
        checkout_branding,
        business=business,
    )
    if any(str(key).startswith("branding_settings[") for key in authoritative):
        # Checkout branding requires the Clover API version. Preserve the pre-Clover
        # subscription behavior explicitly instead of accepting its new flexible default.
        authoritative["subscription_data[billing_mode][type]"] = "classic"
    authoritative.update({
        "mode": "subscription",
        "line_items[0][quantity]": 1,
        "line_items[0][price_data][currency]": currency,
        "line_items[0][price_data][unit_amount]": price_cents,
        "line_items[0][price_data][recurring][interval]": "month",
        "line_items[0][price_data][product_data][name]": f"{business} {plan_key}",
        "success_url": str(params.get("success_url") or ""),
        "cancel_url": str(params.get("cancel_url") or ""),
        "client_reference_id": client_reference_id,
        "metadata[business]": business,
        "metadata[plan_key]": plan_key,
        "metadata[checkout_intent_id]": intent_id,
        "metadata[source]": "takyon_app",
        "subscription_data[metadata][checkout_intent_id]": intent_id,
    })
    for key, value in binding.items():
        authoritative[f"line_items[0][price_data][product_data][metadata][{key}]"] = value
        authoritative[f"metadata[{key}]"] = value
        authoritative[f"subscription_data[metadata][{key}]"] = value
    if intent_email:
        authoritative["customer_email"] = intent_email
    return {
        "intent_id": str(_db_row_value(row, 0, "id") or intent_id),
        "params": authoritative,
        "expected_metadata": binding,
        "expected_livemode": expected_livemode,
        "client_reference_id": client_reference_id,
    }


def _release_app_checkout_intent_claim(intent_id: str) -> None:
    """Make a failed Stripe attempt retriable with the same intent-bound idempotency key."""
    with _safebox_db_conn() as conn:
        conn.execute(
            "select takyon_safebox_release_app_checkout_intent(%s::uuid)",
            (str(intent_id),),
        )


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
    from .runtime_app import assert_takyon_pg_role, configure_takyon_pg_session, resolve_database_url
    import psycopg

    raw_conn = psycopg.connect(
        resolve_database_url(plane="safebox"),
        autocommit=True,
        prepare_threshold=None,
    )
    try:
        configure_takyon_pg_session(raw_conn, bypass=True)
        assert_takyon_pg_role(raw_conn, "safebox")
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

    def __init__(self, *, provider: str, purpose: str = "product_usage", route: str = "app"):
        self._provider = str(provider or "")
        self._purpose = str(purpose or "product_usage")
        self._route = str(route or "app")

    def reserve(self, scope: CapabilityScope, estimate_microusd: int):
        from . import app_entitlements, app_usage
        from .ai_gateway import _user_monthly_budget_microusd

        key = str(uuid.uuid4())
        with _safebox_db_conn() as conn:
            # A PRODUCT (sub-user) scope ALWAYS gets a concrete per-user limit so the 0037/0063 gate
            # is actually enforced (it only enforces when the limit is not null). On any entitlement/
            # plan miss the plan-derived limit is 0 ⇒ reserve refuses (402), never None ⇒ "no cap".
            # Only an OPERATOR scope (app_user_id is None) gets None (no per-user cap on operator
            # spend). The limit is the FULL monthly allowance; the gate anchors the matching
            # entitlement-monthly window itself (migration 0063).
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
                limit = _user_monthly_budget_microusd(plan)
            app_usage.reserve_usage(
                conn,
                scope.business_slug,
                estimated_cost_microusd=int(estimate_microusd),
                reservation_key=key,
                app_user_id=scope.app_user_id,
                user_monthly_limit_microusd=limit,
                app_user_tier=tier,
                provider=self._provider,
                purpose=self._purpose,
                route=self._route,
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


def _operator_creative_gate_disabled() -> bool:
    """Operator god-mode creative-credit bypass — **ON BY DEFAULT** (operator ruling 2026-07-09).
    The creative gate never REFUSES an operator-plane action for insufficient credits: the shortfall
    is auto-granted (ledgered + bypass-tagged) and the reserve retried, so every action still
    reserves/settles with real cost metadata. Only REFUSAL changes, never metering. Set
    ``TAKYON_OPERATOR_CREATIVE_GATE_DISABLED=0`` (or false/no/off) to restore hard gating. Subusers
    stay gated regardless: the creative routes require the internal token + operator client, so the
    app/customer plane can never reach the bypass."""
    return str(os.getenv("TAKYON_OPERATOR_CREATIVE_GATE_DISABLED", "1")).strip().lower() not in {
        "0", "false", "no", "off",
    }


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


class _UmamiForwardBody(BaseModel):
    path: str = ""
    params: dict[str, Any] | None = None
    timeout: float = 20.0


# The exact READ-ONLY per-website stats routes umami_util calls. Anything else (bare website list,
# writes/deletes, website-management) is refused so a runtime plane can only READ one site's stats,
# never enumerate or mutate the shared Umami account.
_UMAMI_FORWARD_PATH_RE = re.compile(r"^websites/[A-Za-z0-9_-]+/(?:stats|pageviews)$")


class _GscTokenBody(BaseModel):
    site_url: str


class _GscVerifyBody(BaseModel):
    site_url: str
    submit_sitemap: bool = True


class _OpenMeterRequestBody(BaseModel):
    method: str = "GET"
    path: str = ""
    payload: dict[str, Any] | None = None
    query: dict[str, Any] | None = None
    allow_status: list[int] = []
    expected_status: list[int] = []
    timeout: float = 20.0


class _MetaGraphBody(BaseModel):
    method: str
    path: str
    params: dict[str, Any] = {}
    # Compatibility only: the broker route below accepts only graph.facebook.com.
    host: str = "graph.facebook.com"
    timeout: float = 60.0


class _MetaGraphUploadVideoBody(BaseModel):
    ad_account_id: str
    name: str = ""
    data_b64: str
    poll: bool = True
    timeout: float = 180.0


class _MetaGraphUploadImageBody(BaseModel):
    ad_account_id: str
    name: str = ""
    data_b64: str
    timeout: float = 180.0


class _MetaEnsureCustomConversionBody(BaseModel):
    ad_account_id: str
    name: str
    rule: str
    custom_event_type: str
    event_source_id: str = ""  # the pixel the conversion listens to; Meta requires it
    timeout: float = 60.0


class _MetaMCPCallBody(BaseModel):
    tool_name: str
    arguments: dict[str, Any] = {}
    timeout: float = 60.0


class _MetaMCPToolsBody(BaseModel):
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


class _BillingReserveBody(BaseModel):
    user_id: str
    estimate_cents: int
    reservation_key: str
    business_slug: str | None = None
    job_id: str | None = None


class _BillingSettleBody(BaseModel):
    reservation_key: str
    actual_cents: int


class _BillingRefundBody(BaseModel):
    reservation_key: str


class _BillingBalancesBody(BaseModel):
    user_id: str


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


class _AppMediaPutBody(BaseModel):
    provider: str
    business: str
    session_token: str
    media_id: str
    data_b64: str
    digest: str


class _AppMediaKeyBody(BaseModel):
    provider: str
    business: str
    session_token: str
    media_id: str


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


class _ShopifyAppWebhookBody(BaseModel):
    raw_body: str
    hmac_sha256: str
    topic: str = ""


class _AppCheckoutReconcileBody(BaseModel):
    session_id: str
    business_slug: str | None = None
    app_user_id: str | None = None
    customer_email: str | None = None


class _AppChargeReversalReconcileBody(BaseModel):
    charge_id: str
    business_slug: str | None = None
    checkout_session_id: str | None = None


class _AppSubscriptionCancelBody(BaseModel):
    business_slug: str
    app_user_id: str
    session_token: str | None = None
    cancel_at_period_end: bool = True


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


class _ConnectionDepositBody(BaseModel):
    # Operator-plane credential deposit for an APPROVED provider connection (delta 6). The secret is
    # AEAD-sealed server-side and never returned; only the fingerprint comes back.
    business: str
    connection_slug: str
    secret: str


class _ConnectionRebindBody(BaseModel):
    # Operator-plane, plaintext-free reactivation of an existing sealed credential. This route is
    # useful only after a fresh approval for the exact canonical connection scope.
    business: str
    connection_slug: str


class _OperatorSessionTokenBody(BaseModel):
    # Mint a SESSION-scoped operator capability (audience = operator.session) for the operator/platform
    # plane. Business-scoped runs prove boundary 1 by owning the business; root-scope runs (before a
    # business exists) may present a verified dashboard Auth0 session so the safebox can derive the
    # REAL Takyon user, otherwise the root path falls back only to an ACTIVE Takyon user on this
    # operator-only rail. ``max_cost_microusd`` is the per-CALL ceiling the proxy enforces on every
    # metered call under this token; ``ttl_seconds`` is the session lifetime (minutes-to-hours,
    # capped). The token is REUSABLE across calls — the proxy verifies it but does NOT claim a nonce.
    business: str | None = None
    operator_user_id: str
    max_cost_microusd: int
    session_token: str | None = None
    ttl_seconds: int | None = None


class _StoreEasBuildCredentialsBody(BaseModel):
    # Operator-plane mint of the per-build store-signing bundle (App Store rail, host-independent
    # builder lane). `business` names the owning business (bundle id is DERIVED server-side from it
    # — never caller-supplied); `capabilities` is the ASC capabilityType list the client derived
    # from app.json, validated against the known set before any Apple call.
    business: str
    capabilities: list[str] | None = None


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


# ── per-replica node transport tokens (plan Stage 4b hardening) ─────────────────────────────────
# The shared TAKYON_SAFEBOX_TOKEN stays valid (non-split hosts keep working unchanged), but each
# replica of a split plane can be enrolled with its OWN revocable transport token. The Safebox host
# never stores those token VALUES — only their sha256 digests, in a json file the environment
# provisioner writes on enroll and prunes on revoke. The file is re-read when its mtime changes, so
# a revocation takes effect on the NEXT request with no service restart. A missing/unreadable/
# malformed file simply contributes zero accepted tokens — fail closed, never open.
_NODE_TOKENS_PATH_ENV = "TAKYON_SAFEBOX_NODE_TOKENS_PATH"
_NODE_TOKENS_FILENAME = "node_tokens.json"
_node_tokens_cache: dict[str, Any] = {"path": None, "stat": None, "hashes": frozenset()}


def _node_tokens_path() -> str:
    explicit = str(os.environ.get(_NODE_TOKENS_PATH_ENV) or "").strip()
    if explicit:
        return explicit
    try:
        from takyon_constants import get_takyon_home

        return os.path.join(str(get_takyon_home()), "safebox", _NODE_TOKENS_FILENAME)
    except Exception:
        return ""


def _parse_node_token_hashes(raw: str) -> frozenset[str]:
    try:
        data = json.loads(raw)
    except Exception:
        return frozenset()
    nodes = data.get("nodes") if isinstance(data, dict) else None
    hashes: set[str] = set()
    for entry in (nodes or {}).values() if isinstance(nodes, dict) else ():
        digest = str((entry or {}).get("token_sha256") or "").strip().lower() if isinstance(entry, dict) else ""
        if re.fullmatch(r"[0-9a-f]{64}", digest):
            hashes.add(digest)
    return frozenset(hashes)


def _node_token_hashes() -> frozenset[str]:
    """Currently-enrolled node token digests, mtime-cached. Empty set on any failure."""
    path = _node_tokens_path()
    if not path:
        return frozenset()
    try:
        st = os.stat(path)
        stat_key = (st.st_mtime_ns, st.st_size)
    except OSError:
        return frozenset()
    cache = _node_tokens_cache
    if cache["path"] == path and cache["stat"] == stat_key:
        return cache["hashes"]
    try:
        with open(path, "r", encoding="utf-8") as fh:
            hashes = _parse_node_token_hashes(fh.read())
    except OSError:
        return frozenset()
    cache["path"], cache["stat"], cache["hashes"] = path, stat_key, hashes
    return hashes


def _require_internal_token(authorization: str | None = Header(default=None)) -> None:
    expected = str(os.environ.get(_SAFEBOX_TOKEN_ENV) or "").strip()
    node_hashes = _node_token_hashes()
    if not expected and not node_hashes:
        if _allow_tokenless():
            return
        # Fail closed: an unconfigured token must never mean "auth disabled" — Safebox safety must
        # not silently degrade to firewall/VPC correctness. Provision TAKYON_SAFEBOX_TOKEN (the
        # service unit loads $TAKYON_HOME/.env) on both the Safebox host and every client plane.
        raise HTTPException(status_code=401, detail="safebox token not configured")
    presented = str(authorization or "").strip()
    if expected:
        want = f"Bearer {expected}"
        if hmac.compare_digest(presented.encode(), want.encode()):
            return
    if node_hashes and presented.startswith("Bearer "):
        digest = hashlib.sha256(presented[len("Bearer "):].strip().encode()).hexdigest()
        # Scan EVERY enrolled digest (no early exit) with a constant-time compare per entry, so
        # acceptance timing does not leak which node matched.
        matched = False
        for enrolled in node_hashes:
            if hmac.compare_digest(digest.encode(), enrolled.encode()):
                matched = True
        if matched:
            return
    raise HTTPException(status_code=401, detail="unauthorized")


def _client_host(request: Request) -> str:
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "").strip()


def _client_allowed_by_entry(host: str, entry: str) -> bool:
    candidate = str(entry or "").strip()
    if not candidate:
        return False
    if hmac.compare_digest(host.encode(), candidate.encode()):
        return True
    try:
        return ipaddress.ip_address(host) in ipaddress.ip_network(candidate, strict=False)
    except ValueError:
        return False


def _require_operator_client(request: Request) -> None:
    """Restrict operator/infrastructure-only Safebox routes to exact trusted service clients.

    ``TAKYON_SAFEBOX_TOKEN`` is transport reachability and is shared by multiple planes during cutover.
    It must not be enough to mint operator capabilities or touch private workspace/storage surfaces.
    The Safebox host therefore needs a route-specific operator token AND an explicit client allowlist for
    these routes, normally the operator VPS private address plus local test clients.
    """
    expected_operator_token = str(os.environ.get(_OPERATOR_TOKEN_ENV) or "").strip()
    if not expected_operator_token:
        raise HTTPException(status_code=503, detail="operator_token_not_configured")
    presented_operator_token = str(request.headers.get(_OPERATOR_TOKEN_HEADER) or "").strip()
    if not hmac.compare_digest(
        presented_operator_token.encode(),
        expected_operator_token.encode(),
    ):
        raise HTTPException(status_code=401, detail="operator_unauthorized")

    raw = str(os.environ.get(_OPERATOR_CLIENTS_ENV) or "").strip()
    if not raw:
        raise HTTPException(status_code=503, detail="operator_client_allowlist_unconfigured")
    host = _client_host(request)
    if not host:
        raise HTTPException(status_code=403, detail="operator_client_unavailable")
    allowed = [part.strip() for part in raw.replace(",", " ").split() if part.strip()]
    if not any(_client_allowed_by_entry(host, part) for part in allowed):
        raise HTTPException(status_code=403, detail="operator_client_not_allowed")


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
                "META_MCP_OAUTH_TOKEN",
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
    {"TAKYON_CAP_SIGNING_KEY", "TAKYON_SAFEBOX_OPERATOR_TOKEN", "TAKYON_SAFEBOX_TOKEN"}
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
            or n in {
                "POSTGRES_URL",
                "POSTGRES_PRISMA_URL",
                "POSTGRES_URL_NON_POOLING",
                "TAKYON_OPERATOR_DATABASE_URL",
                "TAKYON_APP_DATABASE_URL",
                "TAKYON_SAFEBOX_DATABASE_URL",
                "TAKYON_MIGRATION_DATABASE_URL",
                "MIGRATION_DATABASE_URL",
            }
            or n.endswith("_DATABASE_URL")
            or n.endswith((
                "_KEY", "_TOKEN", "_SECRET", "_PASSWORD",
                "_SECRET_ACCESS_KEY", "_WEBHOOK_SECRET", "_CLIENT_SECRET", "_ACCESS_KEY_ID",
            ))
        )


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
        return False


_RUNTIME_DATABASE_EGRESS_NAMES: frozenset[str] = frozenset()


def _env_egress_value(name: str) -> str:
    """Resolve an allowlisted value for runtime-plane egress.

    DB DSNs are not vendable through this route. Each runtime process gets its own least-privilege DSN
    from local service env/Doppler/systemd; the shared Safebox transport token is not DB authority.
    """
    n = str(name or "").strip()
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


def _authorize_app_media_session(*, business: str, session_token: str) -> str:
    from . import app_identity

    business = _require_safe_slug(business)
    token = str(session_token or "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="product_session_token_required")
    with _safebox_db_conn() as conn:
        user = app_identity.validate_session(conn, business, token)
    if user is None:
        raise HTTPException(status_code=403, detail="invalid_session")
    app_user_id = str(getattr(user, "id", "") or "").strip()
    if not app_user_id:
        raise HTTPException(status_code=403, detail="invalid_session")
    return app_user_id


def _authorize_app_media_row(*, business: str, session_token: str, media_id: str) -> str:
    from . import app_identity

    business = _require_safe_slug(business)
    media_id = _require_safe_media_id(media_id)
    token = str(session_token or "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="product_session_token_required")
    with _safebox_db_conn() as conn:
        user = app_identity.validate_session(conn, business, token)
        if user is None:
            raise HTTPException(status_code=403, detail="invalid_session")
        app_user_id = str(getattr(user, "id", "") or "").strip()
        if not app_user_id:
            raise HTTPException(status_code=403, detail="invalid_session")
        row = conn.execute(
            """
            select app_user_id, storage_key
              from app_media
             where business_slug = %s
               and media_id = %s
             limit 1
            """,
            (business, media_id),
        ).fetchone()
    if row is None or str(_db_row_value(row, 0, "app_user_id") or "") != app_user_id:
        raise HTTPException(status_code=404, detail="media_not_found")
    key = str(_db_row_value(row, 1, "storage_key") or "").strip()
    expected = _app_media_storage_key(business, media_id)
    if key != expected:
        raise HTTPException(status_code=403, detail="app_media_storage_scope_mismatch")
    return expected


def _cloudflare_aig_config() -> tuple[str, str, str] | None:
    """Return Cloudflare AI Gateway config, or None when the backstop is intentionally disabled.

    Account ID is the opt-in switch. If the account is configured but auth is missing, fail closed so
    the sub-user provider path cannot silently bypass the configured gateway.
    """
    account_id = str(
        os.environ.get(_CLOUDFLARE_AIG_ACCOUNT_ID_ENV)
        or os.environ.get("CLOUDFLARE_ACCOUNT_ID")
        or ""
    ).strip()
    if not account_id:
        return None
    gateway_id = str(
        os.environ.get(_CLOUDFLARE_AIG_GATEWAY_ID_ENV)
        or _CLOUDFLARE_AIG_GATEWAY_ID_DEFAULT
    ).strip()
    if not gateway_id:
        raise BrokerLedgerError("cloudflare_aig_unconfigured")
    token = str(safebox.read_env_backed_value(_CLOUDFLARE_AIG_TOKEN_ENV) or "").strip()
    if not token:
        raise BrokerLedgerError("cloudflare_aig_unconfigured")
    return account_id, gateway_id, token


def _cloudflare_aig_metadata(scope: CapabilityScope, *, provider: str, model: str) -> str:
    metadata = {
        "app_user_id": str(scope.app_user_id or ""),
        "business_slug": str(scope.business_slug or ""),
        "provider": str(provider or ""),
        "action": str(scope.action or ""),
        "model": str(model or ""),
    }
    return json.dumps(metadata, separators=(",", ":"))


def _cloudflare_aig_anthropic_messages(
    payload: dict[str, Any],
    *,
    api_key: str,
    scope: CapabilityScope,
    model: str,
) -> dict[str, Any] | None:
    config = _cloudflare_aig_config()
    if config is None:
        return None
    account_id, gateway_id, token = config
    account = urllib.parse.quote(account_id, safe="")
    gateway = urllib.parse.quote(gateway_id, safe="")
    request = urllib.request.Request(
        f"{_CLOUDFLARE_AIG_BASE}/{account}/{gateway}/anthropic/v1/messages",
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
            "x-api-key": api_key,
            "cf-aig-authorization": f"Bearer {token}",
            "cf-aig-metadata": _cloudflare_aig_metadata(scope, provider="anthropic", model=model),
            "cf-aig-collect-log-payload": "false",
            "User-Agent": "Takyon-Safebox/1.0",
        },
        method="POST",
    )
    try:
        timeout = int(os.environ.get("TAKYON_APP_ANTHROPIC_TIMEOUT_SECONDS") or 60)
    except ValueError:
        timeout = 60
    timeout = max(5, min(300, timeout))
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"Cloudflare AI Gateway Anthropic returned {exc.code}: {body[:500]}") from exc


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

    def _call(scope: CapabilityScope, key: str):
        raw = _cloudflare_aig_anthropic_messages(
            built_payload,
            api_key=key,
            scope=scope,
            model=model,
        )
        if raw is None:
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


def _deepseek_key_resolver(_scope: CapabilityScope) -> str:
    """Resolve the SHARED DeepSeek key LOCALLY on the safebox (never returned to a caller)."""
    from . import safebox

    try:
        return str(safebox.first_env_backed_value("DEEPSEEK_API_KEY") or "").strip()
    except Exception:  # noqa: BLE001 — resolver returns empty; the broker fails closed on it
        return ""


def _deepseek_provider_caller(payload: dict[str, Any]):
    """Anthropic-wire caller pointed at DeepSeek's anthropic-compatible Messages endpoint.

    Mirrors ``_anthropic_provider_caller`` (same payload parse, same billed-cost settle from the
    canonical pricing table — deepseek models price via their own entries) with the URL + key swap
    the operator proxy lane already does (``safebox_provider_proxy``). Direct call, no Cloudflare
    AIG hop — AIG is provider-configured for Anthropic only."""
    from . import ai_provider

    built_payload, model, estimated_input_tokens = ai_provider.anthropic_payload(payload or {})

    def _call(scope: CapabilityScope, key: str):
        raw = ai_provider.call_anthropic(
            built_payload, key, url=ai_provider.DEEPSEEK_ANTHROPIC_MESSAGES_URL
        )
        usage = raw.get("usage") or {}
        in_tok = int(usage.get("input_tokens") or estimated_input_tokens)
        out_tok = int(usage.get("output_tokens") or 0)
        cache_read = int(usage.get("cache_read_input_tokens") or 0)
        cache_write = int(usage.get("cache_creation_input_tokens") or 0)
        _realized, billed = ai_provider.billed_microusd_cost(
            model, in_tok, out_tok, cache_read_tokens=cache_read, cache_write_tokens=cache_write
        )
        return raw, int(billed)

    return _call


def _openai_key_resolver(_scope: CapabilityScope) -> str:
    from . import ai_provider

    return ai_provider.openai_key()


def _openai_provider_caller(payload: dict[str, Any]):
    from . import ai_provider

    built_payload, model, estimated_input_tokens = ai_provider.openai_payload(payload or {})

    def _call(_scope: CapabilityScope, key: str):
        raw = ai_provider.call_openai(built_payload, key)
        usage = ai_provider.openai_usage(
            raw,
            model=model,
            estimated_input_tokens=estimated_input_tokens,
        )
        return raw, int(usage["billed_cost_microusd"])

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
    endpoint, operation = ai_provider.normalize_tavily_endpoint_operation(endpoint, operation)
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
        image_kwargs = {"api_key": key, "prompt": prompt}
        if str((payload or {}).get("aspect_ratio") or "").strip():
            image_kwargs["aspect_ratio"] = str(payload["aspect_ratio"]).strip()
        if str((payload or {}).get("image_size") or "").strip():
            image_kwargs["image_size"] = str(payload["image_size"]).strip()
        raw_bytes = creative_gateway._gemini_generate_image_raw(**image_kwargs)
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


def _openai_estimate(payload: dict[str, Any]):
    from . import ai_provider

    built_payload, model, estimated_input_tokens = ai_provider.openai_payload(payload or {})
    max_tokens = int((built_payload or {}).get("max_completion_tokens") or 0)

    def _estimate(_scope: CapabilityScope) -> int:
        try:
            _realized, billed = ai_provider.openai_billed_microusd_cost(
                model,
                int(estimated_input_tokens),
                int(max_tokens),
            )
        except ai_provider.OpenAIPricingUnavailable as exc:
            raise BrokerLedgerError("openai_pricing_unavailable") from exc
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
    endpoint, operation = ai_provider.normalize_tavily_endpoint_operation(endpoint, operation)
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


def _postmark_send_price_microusd() -> int:
    from . import app_email

    return int(app_email._send_price_microusd())


def _postmark_key_resolver(_scope: CapabilityScope) -> str:
    return str(safebox.first_env_backed_value("POSTMARK_SERVER_TOKEN") or "").strip()


def _postmark_from_email() -> str:
    return str(
        os.environ.get("POSTMARK_FROM_EMAIL")
        or safebox.load_env().get("POSTMARK_FROM_EMAIL")
        or ""
    ).strip()


def _postmark_estimate(_payload: dict[str, Any]):
    def _estimate(_scope: CapabilityScope) -> int:
        return _postmark_send_price_microusd()

    return _estimate


def _postmark_provider_caller(payload: dict[str, Any]):
    body = dict(payload or {})
    to_email = str(body.get("to_email") or "").strip()
    subject = str(body.get("subject") or "")
    text_body = str(body.get("text_body") or "")
    html_body = body.get("html_body")
    message_stream = str(body.get("message_stream") or "").strip()
    if "@" not in to_email:
        raise ValueError("invalid_recipient")
    if not subject.strip() or not text_body.strip():
        raise ValueError("missing_email_body")

    def _call(_scope: CapabilityScope, key: str):
        from_email = _postmark_from_email()
        if not key or not from_email:
            raise BrokerLedgerError("postmark_unconfigured")
        postmark_payload: dict[str, Any] = {
            "From": from_email,
            "To": to_email,
            "Subject": subject,
            "TextBody": text_body,
        }
        if html_body:
            postmark_payload["HtmlBody"] = str(html_body)
        stream = message_stream or str(
            os.environ.get("TAKYON_APP_EMAIL_MESSAGE_STREAM")
            or safebox.load_env().get("TAKYON_APP_EMAIL_MESSAGE_STREAM")
            or ""
        ).strip()
        if stream:
            postmark_payload["MessageStream"] = stream
        req = urllib.request.Request(
            "https://api.postmarkapp.com/email",
            data=json.dumps(postmark_payload).encode("utf-8"),
            headers={
                "X-Postmark-Server-Token": key,
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as response:
                response_body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
            raise BrokerLedgerError(f"provider_http_{int(exc.code)}:{detail}") from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise BrokerLedgerError("provider_unreachable") from exc
        return (
            {
                "message_id": response_body.get("MessageID"),
                "provider": "postmark",
                "status": "sent",
            },
            _postmark_send_price_microusd(),
        )

    return _call


def _postmark_authorize_service_send(
    *,
    business: str,
    session_token: str,
    recipient_app_user_id: str,
) -> dict[str, str]:
    from . import app_email, app_identity

    business = _require_safe_slug(business)
    token = str(session_token or "").strip()
    recipient = str(recipient_app_user_id or "").strip()
    if not token:
        raise HTTPException(status_code=403, detail="product_session_token_required")
    if not recipient:
        raise HTTPException(status_code=400, detail="recipient_app_user_id_required")

    with _safebox_db_conn() as conn:
        service_user = app_identity.validate_session(conn, business, token)
        if service_user is None:
            raise HTTPException(status_code=403, detail="invalid_session")
        service_user_id = str(service_user.id or "").strip()
        service_email = str(service_user.email or "").strip()
        if not app_email.is_service_email(service_email):
            raise HTTPException(status_code=403, detail="service_session_required")
        owner_row = conn.execute(
            "select owner_user_id from businesses where slug = %s",
            (business,),
        ).fetchone()
        if owner_row is None:
            raise HTTPException(status_code=403, detail="unknown_business")
        owner_user_id = str(_db_row_value(owner_row, 0, "owner_user_id") or "").strip()
        if not owner_user_id:
            raise HTTPException(status_code=403, detail="business_owner_missing")
        recipient_row = conn.execute(
            """
            select r.id::text, r.email::text, r.tier::text
              from app_users r
             where r.business_slug = %s
               and r.id::text = %s
               and r.status = 'active'
               and lower(r.email::text) not like '%.takyon.invalid'
             limit 1
            """,
            (business, recipient),
        ).fetchone()
        if recipient_row is None:
            raise HTTPException(status_code=403, detail="recipient_not_authorized")
        count_row = conn.execute(
            """
            select count(*)::bigint
              from app_usage_events
             where business_slug = %s
               and purpose = 'email_send'
               and created_at >= date_trunc('day', now() at time zone 'utc')
            """,
            (business,),
        ).fetchone()

    sends_today = int(_db_row_value(count_row, 0, "count") or 0)
    if sends_today >= app_email._daily_send_cap():
        raise HTTPException(status_code=403, detail="email_daily_cap_exceeded")
    return {
        "owner_user_id": owner_user_id,
        "service_app_user_id": service_user_id,
        "recipient_app_user_id": str(_db_row_value(recipient_row, 0, "id") or recipient),
        "recipient_email": str(_db_row_value(recipient_row, 1, "email") or ""),
        "recipient_tier": str(_db_row_value(recipient_row, 2, "tier") or ""),
    }


# ── Creative-credit provider routes (logo / UGC / static-ad) ──────────────────────────────────────
# Upstream provider hosts for the gated creative forwards. Kept here on the safebox (never in the
# business runtime) because only the safebox holds the key and forwards. Mirrors the constants that
# used to live in safebox_provider_proxy.py before the ungated routes were deleted.
_OPENAI_IMAGES_URL = "https://api.openai.com/v1/images/generations"
_FAL_BASE_URL = "https://fal.run"
# Long-running FAL models (Kling video i2v) routinely generate for >3 min, which exceeds the
# synchronous fal.run gateway/timeout and 502s. Those models MUST go through the FAL queue API
# (submit -> poll status -> fetch result): each HTTP hop stays short while the generation runs
# server-side. See _forward_fal_queue / _creative_fal_kling_image_to_video_caller.
_FAL_QUEUE_BASE_URL = "https://queue.fal.run"
_FAL_KLING_IMAGE_TO_VIDEO_PATH = "fal-ai/kling-video/v3/pro/image-to-video"
_CREATIVE_UPSTREAM_TIMEOUT_S = 180.0
# Max wall-clock the safebox waits for a queued FAL render to COMPLETE, and the poll cadence. The
# total budget stays below the runtime subprocess's proxy timeout (pipeline.py _PROXY_TIMEOUT_S) so
# the subprocess hears a clean result/refusal rather than its own transport timeout.
_FAL_QUEUE_TOTAL_BUDGET_S = 840.0
_FAL_QUEUE_POLL_INTERVAL_S = 4.0
_FAL_QUEUE_ALLOWED_HOSTS = frozenset({"queue.fal.run"})


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


def _require_fal_queue_url(raw_url: str, *, label: str) -> str:
    """Allow FAL queue follow-up requests only to fixed HTTPS queue.fal.run URLs."""
    url = str(raw_url or "").strip()
    if not url:
        raise BrokerLedgerError(f"provider_queue_missing_{label}")
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.scheme != "https"
        or parsed.hostname not in _FAL_QUEUE_ALLOWED_HOSTS
        or parsed.username
        or parsed.password
        or parsed.port is not None
        or not parsed.path.startswith("/")
        or parsed.fragment
    ):
        raise BrokerLedgerError("provider_queue_invalid_url")
    return urllib.parse.urlunsplit(parsed)


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
            status_url_raw = str(submit.get("status_url") or "").strip()
            response_url_raw = str(submit.get("response_url") or "").strip()
            if not response_url_raw:
                # A submit with neither a response_url nor a status_url is not a queue response we can
                # follow — surface it rather than hang.
                raise BrokerLedgerError("provider_queue_no_response_url")
            status_url = (
                _require_fal_queue_url(status_url_raw, label="status_url") if status_url_raw else ""
            )
            response_url = _require_fal_queue_url(response_url_raw, label="response_url")

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
        image_kwargs = {"api_key": key, "prompt": prompt}
        if str((payload or {}).get("aspect_ratio") or "").strip():
            image_kwargs["aspect_ratio"] = str(payload["aspect_ratio"]).strip()
        if str((payload or {}).get("image_size") or "").strip():
            image_kwargs["image_size"] = str(payload["image_size"]).strip()
        raw_bytes = creative_gateway._gemini_generate_image_raw(**image_kwargs)
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


def _creative_fal_kling_image_to_video_caller(payload: dict[str, Any]):
    """Resolve the FAL key LOCALLY and call the one UGC video model Takyon exposes through this gate.

    This is deliberately not a generic FAL path proxy. Product creative code may call only the named
    Kling image-to-video route; adding another FAL model means adding a new explicit route/audience
    contract, not passing a caller-chosen provider path through Safebox."""
    body = dict(payload or {})

    def _call(_scope: "CapabilityScope"):
        key = _fal_key()
        if not key:
            raise BrokerLedgerError("fal_unconfigured")
        headers = {"Authorization": f"Key {key}", "content-type": "application/json"}
        # Route through the FAL QUEUE API: Kling video renders run for minutes and 502 on the
        # synchronous fal.run endpoint. _forward_fal_queue submits, polls, and fetches the result
        # with short per-hop HTTP calls, returning the same KEY-FREE provider JSON.
        return _forward_fal_queue(_FAL_KLING_IMAGE_TO_VIDEO_PATH, headers=headers, payload=body)

    return _call


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


def _exact_approved_connection_binding(
    conn,
    *,
    business: str,
    connection_slug: str,
) -> dict[str, Any]:
    """Lock and prove one connection against its exact current canonical approval."""
    from . import egress_gateway, money_shape

    row = conn.execute(
        "select id, connection_slug, provider_kind, allowed_host, allowed_path_prefix, "
        "allowed_methods, placement, scope, approval_id, secret_ciphertext, secret_nonce, "
        "secret_fingerprint from provider_connections "
        "where business_slug = %s and connection_slug = %s for update",
        (business, connection_slug),
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="connection_unknown")
    try:
        scope_snapshot = egress_gateway.normalize_connection_scope(
            provider_kind=str(row[2] or ""),
            allowed_host=str(row[3] or ""),
            allowed_path_prefix=row[4],
            allowed_methods=row[5],
            placement=row[6] if isinstance(row[6], dict) else {},
            scope=str(row[7] or "business"),
        )
        approval_payload = egress_gateway.connection_approval_payload(
            str(row[1] or ""), scope_snapshot
        )
        scope_digest = egress_gateway.connection_scope_digest(scope_snapshot)
        approval_digest = money_shape.payload_digest(approval_payload)
    except egress_gateway.EgressError as exc:
        raise HTTPException(status_code=403, detail={"error": exc.code}) from exc
    approval = conn.execute(
        "select status from operator_approvals where business_slug = %s "
        "and action_kind = 'provider_connection_grant' and status = 'approved' "
        "and id = %s and payload_digest = %s "
        "and (expires_at is null or expires_at > now()) for update",
        (business, row[8], approval_digest),
    ).fetchone()
    if approval is None:
        raise HTTPException(status_code=403, detail="connection_not_approved")
    return {
        "connection_id": str(row[0]),
        "scope_digest": scope_digest,
        "secret_ciphertext": None if row[9] is None else bytes(row[9]),
        "secret_nonce": None if row[10] is None else bytes(row[10]),
        "secret_fingerprint": None if row[11] is None else str(row[11]),
    }


def build_safebox_app() -> FastAPI:
    app = FastAPI(title="Takyon Safebox")
    app.add_middleware(
        RequestBodyLimitMiddleware,
        limit_resolver=_safebox_body_limit,
        require_content_length=request_method_may_have_body,
    )

    @app.get("/healthz")
    def healthz() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/v1/stripe/account-proof")
    def stripe_account_proof(
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Non-secret proof that this running Safebox resolves the bound Stripe account."""
        _require_internal_token(authorization)
        account_id, livemode = _stripe_account_snapshot()
        return {"account_id": account_id, "livemode": livemode}

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
    def save_env_value(
        key: str,
        body: _EnvValueBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        raise HTTPException(status_code=403, detail="env_write_forbidden")

    @app.delete("/v1/env/{key}")
    def delete_env_value(
        key: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        raise HTTPException(status_code=403, detail="env_write_forbidden")

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
        request: Request,
        body: _Auth0LoginStateBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _Auth0CallbackBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _Auth0SessionVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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

    @app.post("/v1/connections/deposit")
    def deposit_connection_secret(
        request: Request,
        body: _ConnectionDepositBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Deposit the plaintext credential for an operator-APPROVED provider connection. Operator-
        plane only (the human/dashboard POSTs the secret directly): _require_internal_token +
        _require_operator_client, so the secret never enters the CEO model context or the business
        runtime. Verifies an APPROVED operator_approvals row exists for this connection, AEAD-seals
        the secret with the safebox-only seal key, writes the ciphertext columns (the only role that
        can), and flips the connection to 'active'. Idempotent: re-depositing rotates the secret."""
        from . import egress_gateway

        _require_internal_token(authorization)
        _require_operator_client(request)
        business = str(body.business or "").strip()
        connection_slug = str(body.connection_slug or "").strip()
        secret = str(body.secret or "")
        if not business or not connection_slug or not secret:
            raise HTTPException(status_code=400, detail="missing_deposit_fields")
        # Seal BEFORE opening the conn (fail fast on an unconfigured key, no partial state).
        try:
            ct, nonce, fp = egress_gateway.seal_secret(secret)
        except egress_gateway.EgressError as exc:
            raise HTTPException(status_code=exc.status, detail={"error": exc.code}) from exc
        with _safebox_db_conn() as conn:
            # ALL statements in ONE transaction: on the Supabase transaction pooler (:6543) separate
            # autocommit statements can land on different backends where the RLS-bypass GUC is not
            # carried, so the SELECT could see the row and the UPDATE could silently touch 0 rows
            # (the documented probe gotcha). One transaction pins them to one backend.
            with conn.transaction():
                # Pin the RLS-bypass GUC to THIS backend for the whole transaction (:6543 probe
                # gotcha — a session-level SET is not reliably carried across pooled backends).
                conn.execute("select set_config('takyon.rls_bypass', '1', true)")
                binding = _exact_approved_connection_binding(
                    conn,
                    business=business,
                    connection_slug=connection_slug,
                )
                updated = conn.execute(
                    "update provider_connections set secret_ciphertext = %s, secret_nonce = %s, "
                    "secret_fingerprint = %s, approved_scope_digest = %s, status = 'active', "
                    "updated_at = now() where id = %s "
                    "returning id",
                    (
                        ct,
                        nonce,
                        fp,
                        binding["scope_digest"],
                        binding["connection_id"],
                    ),
                ).fetchone()
                if updated is None:
                    raise HTTPException(status_code=500, detail="deposit_write_failed")
        return {"business": business, "connection_slug": connection_slug, "status": "active", "fingerprint": fp}

    @app.post("/v1/connections/rebind")
    def rebind_connection_secret(
        request: Request,
        body: _ConnectionRebindBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Reactivate intact sealed credential material after exact canonical-scope reapproval.

        This never accepts or returns plaintext. Historical approvals that do not digest to the
        current canonical payload remain unusable; an operator must first approve that exact scope.
        """
        from . import egress_gateway

        _require_internal_token(authorization)
        _require_operator_client(request)
        business = str(body.business or "").strip()
        connection_slug = str(body.connection_slug or "").strip()
        if not business or not connection_slug:
            raise HTTPException(status_code=400, detail="missing_rebind_fields")
        with _safebox_db_conn() as conn:
            with conn.transaction():
                conn.execute("select set_config('takyon.rls_bypass', '1', true)")
                binding = _exact_approved_connection_binding(
                    conn,
                    business=business,
                    connection_slug=connection_slug,
                )
                try:
                    egress_gateway.verify_sealed_secret(
                        binding["secret_ciphertext"],
                        binding["secret_nonce"],
                        binding["secret_fingerprint"],
                    )
                except egress_gateway.EgressError as exc:
                    raise HTTPException(
                        status_code=exc.status,
                        detail={"error": exc.code},
                    ) from exc
                updated = conn.execute(
                    "update provider_connections set approved_scope_digest = %s, "
                    "status = 'active', updated_at = now() where id = %s returning id",
                    (binding["scope_digest"], binding["connection_id"]),
                ).fetchone()
                if updated is None:
                    raise HTTPException(status_code=500, detail="rebind_write_failed")
        return {
            "business": business,
            "connection_slug": connection_slug,
            "status": "active",
            "fingerprint": binding["secret_fingerprint"],
        }

    @app.post("/v1/user-api-keys/register")
    def register_user_key(
        request: Request,
        body: _RegisterUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _ResolveUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        return {"record": safebox.resolve_user_api_key(body.raw_key)}

    @app.post("/v1/user-api-keys/revoke")
    def revoke_user_key(
        request: Request,
        body: _RevokeUserKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        return {"revoked": safebox.revoke_user_api_key(body.key_id, revoked_at=body.revoked_at)}

    @app.post("/v1/user-api-keys/revoke-for-user")
    def revoke_user_keys_for_user(
        request: Request,
        body: _RevokeUserKeysForUserBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, list[str]]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        return {
            "revoked_ids": safebox.revoke_user_api_keys_for_user(
                body.user_id,
                revoked_at=body.revoked_at,
            )
        }

    @app.post("/v1/user-api-keys/restore")
    def restore_user_keys(
        request: Request,
        body: _RestoreUserKeysBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        safebox.restore_user_api_keys(body.key_ids)
        return {"ok": True}

    @app.delete("/v1/user-api-keys/{key_id}")
    def delete_user_key(
        request: Request,
        key_id: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        return {"deleted": safebox.delete_user_api_key(key_id)}

    @app.post("/v1/billing/accounts/open")
    def open_billing_account(
        request: Request,
        body: _OpenBillingAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        # Account-open is allowed only as a zero-balance provisioning primitive. Any non-zero amount
        # is a grant and must go through starter/subscription/webhook policy.
        if int(body.allowance_included_cents or 0) != 0:
            raise HTTPException(status_code=400, detail="billing_open_must_not_mint_allowance")
        safebox._local_open_billing_account(None, body.user_id, allowance_included_cents=0)
        return {"ok": True}

    @app.post("/v1/billing/reserve")
    def reserve_billing(
        request: Request,
        body: _BillingReserveBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import billing

        try:
            with _safebox_db_conn() as conn:
                res = billing.reserve(
                    conn,
                    body.user_id,
                    int(body.estimate_cents or 0),
                    body.reservation_key,
                    business_slug=body.business_slug or None,
                    job_id=body.job_id or None,
                )
        except billing.InsufficientBalance as exc:
            raise HTTPException(
                status_code=402,
                detail={
                    "code": "insufficient_balance",
                    "estimate_cents": int(exc.estimate_cents),
                    "allowance_available_cents": int(exc.allowance_available_cents),
                },
            ) from exc
        except billing.NoBillingAccount as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "no_billing_account", "user_id": body.user_id},
            ) from exc
        return {"reservation_key": res.key, "allowance_cents": int(res.allowance_cents)}

    @app.post("/v1/billing/settle")
    def settle_billing(
        request: Request,
        body: _BillingSettleBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import billing

        try:
            with _safebox_db_conn() as conn:
                billing.settle(conn, body.reservation_key, int(body.actual_cents or 0))
        except billing.UnknownReservation as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_reservation", "reservation_key": body.reservation_key},
            ) from exc
        return {"ok": True}

    @app.post("/v1/billing/refund")
    def refund_billing(
        request: Request,
        body: _BillingRefundBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import billing

        try:
            with _safebox_db_conn() as conn:
                billing.refund(conn, body.reservation_key)
        except billing.UnknownReservation as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "unknown_reservation", "reservation_key": body.reservation_key},
            ) from exc
        return {"ok": True}

    @app.post("/v1/billing/balances")
    def billing_balances(
        request: Request,
        body: _BillingBalancesBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import billing

        try:
            with _safebox_db_conn() as conn:
                balances = billing.get_billing_balances(conn, body.user_id)
        except billing.NoBillingAccount as exc:
            raise HTTPException(
                status_code=404,
                detail={"code": "no_billing_account", "user_id": body.user_id},
            ) from exc
        return {
            "user_id": balances.user_id,
            "allowance_included_cents": int(balances.allowance_included_cents),
            "allowance_used_cents": int(balances.allowance_used_cents),
            "allowance_remaining_cents": int(balances.allowance_remaining_cents),
            "reserved_cents": int(balances.reserved_cents),
            "allowance_period_start": balances.allowance_period_start.isoformat()
            if hasattr(balances.allowance_period_start, "isoformat")
            else balances.allowance_period_start,
            "allowance_resets_at": balances.allowance_resets_at.isoformat()
            if hasattr(balances.allowance_resets_at, "isoformat")
            else balances.allowance_resets_at,
        }

    @app.post("/v1/billing/starter-allowance")
    def grant_starter_allowance(
        request: Request,
        body: _StarterAllowanceBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _OperatorSubscriptionSyncBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from .control_api import sync_operator_subscription_allowance as _sync

        with _safebox_db_conn() as conn:
            state = _sync(conn, body.user_id, refresh_live=bool(body.refresh_live))
        return safebox._operator_subscription_state_payload(state)

    @app.post("/v1/operator/payouts/state")
    def operator_payout_state(
        request: Request,
        body: _OperatorPayoutStateBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _OperatorBillingPortalBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _OperatorSubscriptionCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        _require_stripe_checkout_enabled()
        _require_operator_checkout_enabled()
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
        request: Request,
        body: _OperatorPayoutConnectBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _StripeRequestBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        path, method, params = _normalize_stripe_request(body.path, body.method or "POST", body.params)
        catalog_plan: dict[str, Any] | None = None
        expected_livemode: bool | None = None
        if method == "POST" and path in _STRIPE_CATALOG_MUTATION_PATHS:
            _require_operator_client(request)
        try:
            if method == "POST" and path in _STRIPE_CATALOG_MUTATION_PATHS:
                catalog_plan, expected_livemode = _prepare_catalog_mutation(path, params)
            if path == "checkout/sessions" and method == "POST":
                _require_stripe_checkout_enabled()
                checkout = _claim_app_checkout_intent_authority(params)
                params = checkout["params"]
                idempotency_key = f"takyon-app-checkout-{checkout['intent_id']}"
                try:
                    result = safebox.stripe_request(
                        path,
                        params,
                        method=method,
                        idempotency_key=idempotency_key,
                    )
                    metadata = (
                        result.get("metadata")
                        if isinstance(result, dict) and isinstance(result.get("metadata"), dict)
                        else {}
                    )
                    expected_metadata = dict(checkout["expected_metadata"])
                    expected_metadata["checkout_intent_id"] = checkout["intent_id"]
                    session_id = str(result.get("id") or "") if isinstance(result, dict) else ""
                    session_url = str(result.get("url") or "") if isinstance(result, dict) else ""
                    parsed_url = urllib.parse.urlsplit(session_url)
                    expected_prefix = "cs_live_" if checkout["expected_livemode"] else "cs_test_"
                    if (
                        not isinstance(result, dict)
                        or result.get("object") != "checkout.session"
                        or result.get("livemode") is not checkout["expected_livemode"]
                        or result.get("mode") != "subscription"
                        or not session_id.startswith(expected_prefix)
                        or parsed_url.scheme != "https"
                        or parsed_url.hostname != "checkout.stripe.com"
                        or str(result.get("client_reference_id") or "")
                        != checkout["client_reference_id"]
                        or any(
                            str(metadata.get(key) or "") != str(value)
                            for key, value in expected_metadata.items()
                        )
                    ):
                        raise HTTPException(status_code=502, detail="stripe_checkout_create_mismatch")
                except Exception:
                    _release_app_checkout_intent_claim(checkout["intent_id"])
                    raise
            else:
                result = safebox.stripe_request(path, params, method=method)
            if catalog_plan is not None and expected_livemode is not None:
                account_id = str(params.get("metadata[takyon_stripe_account_id]") or "")
                _require_exact_stripe_metadata(
                    result, _plan_stripe_metadata(catalog_plan, account_id=account_id)
                )
                expected_object = "product" if path == "products" else "price"
                if (
                    result.get("object") != expected_object
                    or result.get("active") is not True
                    or result.get("livemode") is not expected_livemode
                ):
                    raise HTTPException(status_code=502, detail="stripe_catalog_create_mismatch")
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
        request: Request,
        body: _PostmarkSendBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _ProductEdgeRouteBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _VercelDomainDeleteBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _StoragePutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        provider = _storage_provider(body.provider)
        _storage_business_slug(body.key, require_existing=False)
        try:
            data = base64.b64decode(str(body.data_b64 or ""), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_base64") from exc
        try:
            return safebox.storage_put(provider, body.key, data, digest=body.digest)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/app-media/put")
    def app_media_put(
        body: _AppMediaPutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        business = _require_safe_slug(body.business)
        media_id = _require_safe_media_id(body.media_id)
        _authorize_app_media_session(business=business, session_token=body.session_token)
        try:
            data = base64.b64decode(str(body.data_b64 or ""), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_base64") from exc
        try:
            safebox.storage_put(provider, _app_media_storage_key(business, media_id), data, digest=body.digest)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"provider": provider, "business": business, "media_id": media_id, "stored": True}

    @app.post("/v1/app-media/get")
    def app_media_get(
        body: _AppMediaKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        business = _require_safe_slug(body.business)
        media_id = _require_safe_media_id(body.media_id)
        key = _authorize_app_media_row(
            business=business,
            session_token=body.session_token,
            media_id=media_id,
        )
        try:
            data = safebox.storage_get(provider, key)
        except Exception as exc:
            if type(exc).__name__ == "ObjectNotFound":
                raise HTTPException(status_code=404, detail="object_not_found") from exc
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {
            "provider": provider,
            "business": business,
            "media_id": media_id,
            "data_b64": base64.b64encode(data).decode("ascii"),
        }

    @app.post("/v1/app-media/delete")
    def app_media_delete(
        body: _AppMediaKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        provider = _storage_provider(body.provider)
        business = _require_safe_slug(body.business)
        media_id = _require_safe_media_id(body.media_id)
        key = _authorize_app_media_row(
            business=business,
            session_token=body.session_token,
            media_id=media_id,
        )
        try:
            safebox.storage_delete(provider, key)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"provider": provider, "business": business, "media_id": media_id, "deleted": True}

    @app.post("/v1/providers/composio/forward")
    def provider_composio_forward(
        request: Request,
        body: _ComposioForwardBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # COMPOSIO_API_KEY is a provider secret held here and denied /v1/env egress; runtime planes
        # broker their Composio calls through this route. On the safebox host _use_remote_authority()
        # is False, so composio_distribution._request resolves the key LOCALLY and calls Composio
        # directly, returning the key-free upstream JSON. Gated by the internal token (transport
        # reachability); the per-action money gate lives upstream in the distribution skill/tool.
        _require_internal_token(authorization)
        _require_operator_client(request)
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

    @app.post("/v1/analytics/umami/forward")
    def analytics_umami_forward(
        request: Request,
        body: _UmamiForwardBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # UMAMI_API_KEY is account-scoped (it reads every business's analytics and can manage the
        # shared Umami account), so it is denied /v1/env egress and resolved only here. Runtime planes
        # broker their READ-ONLY stats reads through this route: on the safebox host
        # umami_util.umami_request resolves the key LOCALLY, calls Umami with the safebox's OWN
        # configured endpoint, and returns the key-free upstream JSON. Operator-plane only (internal
        # token + operator client gate), like the GSC/Composio brokers. The path is allowlisted to the
        # read-only stats routes and the caller never supplies the upstream URL — a compromised runtime
        # can neither mutate the account nor redirect the key.
        _require_internal_token(authorization)
        _require_operator_client(request)
        path = str(body.path or "").strip().lstrip("/")
        if not _UMAMI_FORWARD_PATH_RE.match(path):
            raise HTTPException(status_code=400, detail="umami_path_not_allowed")
        from . import core as _core, umami_util as _uu

        try:
            cfg = _core._analytics_umami_config() or {}
        except Exception:
            cfg = {}
        api_endpoint = str(cfg.get("api_endpoint") or "https://api.umami.is/v1").strip()
        timeout = min(60.0, max(5.0, float(body.timeout or 20.0)))
        try:
            return _uu.umami_request(path, dict(body.params or {}), api_endpoint, timeout=timeout)
        except _uu.UmamiError as exc:
            msg = str(exc)
            if "requires UMAMI_API_KEY" in msg:
                raise HTTPException(status_code=404, detail="umami_unconfigured") from exc
            raise HTTPException(status_code=502, detail=msg[:300]) from exc

    def _gsc_credentials():
        sa_json = str(safebox.first_env_backed_value("TAKYON_GSC_SERVICE_ACCOUNT_KEY") or "").strip()
        if not sa_json:
            raise HTTPException(status_code=404, detail="gsc_unconfigured")
        try:
            sa_info = json.loads(sa_json)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"gsc_key_invalid_json: {exc}") from exc
        try:
            from google.oauth2 import service_account as _gsc_service_account  # type: ignore
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"gsc_client_missing: {exc}") from exc
        scopes = (
            "https://www.googleapis.com/auth/siteverification",
            "https://www.googleapis.com/auth/webmasters",
        )
        return _gsc_service_account.Credentials.from_service_account_info(sa_info, scopes=list(scopes))

    def _gsc_build(api: str, version: str):
        try:
            from googleapiclient import discovery as _gsc_discovery  # type: ignore
        except Exception as exc:
            raise HTTPException(status_code=500, detail=f"gsc_client_missing: {exc}") from exc
        return _gsc_discovery.build(
            api,
            version,
            credentials=_gsc_credentials(),
            cache_discovery=False,
        )

    @app.post("/v1/gsc/verification-token")
    def gsc_verification_token(
        request: Request,
        body: _GscTokenBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, str]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        site_url = str(body.site_url or "").strip()
        if not site_url:
            raise HTTPException(status_code=400, detail="site_url_required")
        service = _gsc_build("siteVerification", "v1")
        resp = (
            service.webResource()
            .getToken(
                body={
                    "verificationMethod": "META",
                    "site": {"type": "SITE", "identifier": site_url},
                }
            )
            .execute()
        )
        token = str((resp or {}).get("token") or "").strip()
        if not token:
            raise HTTPException(status_code=502, detail="gsc_empty_token")
        return {"verification_token": token}

    @app.post("/v1/store/asc/account-health")
    def store_asc_account_health(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """App Store rail account-health probe (readmodular §4.1). Resolves the ASC .p8 + identifiers
        from custody (never egressed), mints a short-TTL ES256 JWT, and probes Apple — returning ONLY
        the health receipt (ok | agreement_blocked | auth_error | error | unreachable). The key never
        leaves the safebox. Operator-plane only (internal token + operator client gate)."""
        _require_internal_token(authorization)
        _require_operator_client(request)
        pem = str(
            safebox.first_env_backed_value(
                "TAKYON_APP_STORE_CONNECT_PRIVATE_KEY", "APP_STORE_CONNECT_PRIVATE_KEY"
            )
            or ""
        ).strip()
        if not pem:
            raise HTTPException(status_code=404, detail="asc_unconfigured")
        key_id = str(safebox.first_env_backed_value("APP_STORE_CONNECT_KEY_ID") or "").strip()
        issuer_id = str(safebox.first_env_backed_value("APP_STORE_CONNECT_ISSUER_ID") or "").strip()
        if not key_id or not issuer_id:
            raise HTTPException(status_code=404, detail="asc_identifiers_unconfigured")
        try:
            from plugins.takyon import asc as _asc
        except Exception as exc:  # pragma: no cover - deploy coherence
            raise HTTPException(status_code=500, detail=f"asc_leaf_missing: {exc}") from exc
        receipt = _asc.probe_account_health(key_id, issuer_id, pem)
        # Return ONLY the receipt; the key/JWT never egress.
        return {
            "state": receipt.get("state"),
            "status_code": receipt.get("status_code"),
            "detail": receipt.get("detail"),
            "checked_at": receipt.get("checked_at"),
        }

    @app.post("/v1/store/eas/build-credentials")
    def store_eas_build_credentials(
        request: Request,
        body: _StoreEasBuildCredentialsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Mint the per-build store-signing bundle — the host-independent builder lane (App Store
        rail). The safebox performs the ASC provisioning SERVER-SIDE with the custodied .p8 (ensure
        the business's DERIVED bundle id, sync capabilities via POST /v1/bundleIdCapabilities —
        eas-cli's own sync does not persist under ASC-key auth — and (re)mint the App Store
        provisioning profile bound to the reused team distribution cert). Only the ephemeral signing
        material the eas-cli child needs egresses: expo_token, team p12 (base64) + password, the
        pre-minted profile (base64), and identifiers. The .p8 NEVER leaves this host. No npm/expo
        tooling executes here — Apple API calls + packaging only. Operator-plane only."""
        _require_internal_token(authorization)
        _require_operator_client(request)
        business = _require_safe_slug(str(body.business or ""), detail="unsafe_business")
        try:
            from plugins.takyon import store_builder as _sb
        except Exception as exc:  # pragma: no cover - deploy coherence
            raise HTTPException(status_code=500, detail=f"store_builder_leaf_missing: {exc}") from exc
        pem = str(
            safebox.first_env_backed_value(
                "TAKYON_APP_STORE_CONNECT_PRIVATE_KEY", "APP_STORE_CONNECT_PRIVATE_KEY"
            )
            or ""
        ).strip()
        key_id = str(safebox.first_env_backed_value("APP_STORE_CONNECT_KEY_ID") or "").strip()
        issuer_id = str(safebox.first_env_backed_value("APP_STORE_CONNECT_ISSUER_ID") or "").strip()
        team_id = str(safebox.first_env_backed_value("APPLE_TEAM_ID") or "").strip()
        expo_token = str(
            safebox.first_env_backed_value("TAKYON_EXPO_TOKEN", "EXPO_TOKEN") or ""
        ).strip()
        expo_owner = str(safebox.first_env_backed_value("EXPO_OWNER") or "coscale").strip() or "coscale"
        dist_cert_id = str(safebox.first_env_backed_value("APP_STORE_DIST_CERT_ID") or "").strip()
        dist_p12_b64 = str(safebox.first_env_backed_value("APP_STORE_DIST_P12_B64") or "").strip()
        dist_p12_password = str(safebox.first_env_backed_value("APP_STORE_DIST_P12_PASSWORD") or "")
        missing = [
            name
            for name, value in (
                ("APP_STORE_CONNECT_PRIVATE_KEY", pem),
                ("APP_STORE_CONNECT_KEY_ID", key_id),
                ("APP_STORE_CONNECT_ISSUER_ID", issuer_id),
                ("APPLE_TEAM_ID", team_id),
                ("EXPO_TOKEN", expo_token),
                ("APP_STORE_DIST_CERT_ID", dist_cert_id),
                ("APP_STORE_DIST_P12_B64", dist_p12_b64),
                ("APP_STORE_DIST_P12_PASSWORD", dist_p12_password),
            )
            if not value
        ]
        if missing:
            # Fail-closed BEFORE any Apple call; the missing names are the operator's exact fix.
            raise HTTPException(
                status_code=404,
                detail=f"eas_build_credentials_unconfigured:{','.join(missing)}",
            )
        # Capabilities are DATA validated against the known ASC set — never free-form strings.
        known_capabilities = {capability for _, capability in _sb._CAPABILITY_MAP}
        capabilities: list[str] = []
        for raw_capability in list(body.capabilities or []):
            capability = str(raw_capability or "").strip().upper()
            if not capability:
                continue
            if capability not in known_capabilities:
                raise HTTPException(status_code=400, detail=f"unknown_capability:{capability}")
            capabilities.append(capability)
        # The bundle id is DERIVED server-side from the business slug (the same hard
        # business-isolation rail the builder enforces) — a caller can never provision/sign an
        # identity it does not own.
        bundle_id = _sb.expected_bundle_identifier(business)
        creds = _sb.StoreBuilderCreds(
            key_id=key_id,
            issuer_id=issuer_id,
            team_id=team_id,
            private_key_pem=pem,
            expo_token=expo_token,
            expo_owner=expo_owner,
            dist_cert_id=dist_cert_id,
            dist_p12_path="",
            dist_p12_password=dist_p12_password,
        )
        try:
            bundle_resource = _sb._ensure_bundle_id(creds, bundle_id, f"takyon {business}")
            _sb._ensure_capabilities(creds, bundle_resource, capabilities)
            profile_bytes = _sb._ensure_store_profile(
                creds,
                bundle_resource_id=bundle_resource,
                profile_name=f"takyon {business} appstore",
            )
        except Exception as exc:
            raise HTTPException(
                status_code=502, detail=f"asc_provisioning_failed: {str(exc)[:300]}"
            ) from exc
        return {
            "business": business,
            "bundle_identifier": bundle_id,
            "team_id": team_id,
            "expo_owner": expo_owner,
            "expo_token": expo_token,
            "dist_cert_id": dist_cert_id,
            "dist_p12_b64": dist_p12_b64,
            "dist_p12_password": dist_p12_password,
            "profile_b64": base64.b64encode(profile_bytes).decode("ascii"),
            "minted_at": int(time.time()),
        }

    @app.post("/v1/gsc/verify")
    def gsc_verify(
        request: Request,
        body: _GscVerifyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        site_url = str(body.site_url or "").strip()
        if not site_url:
            raise HTTPException(status_code=400, detail="site_url_required")
        site_verification = _gsc_build("siteVerification", "v1")
        search_console = _gsc_build("searchconsole", "v1")
        verify_resp = (
            site_verification.webResource()
            .insert(
                verificationMethod="META",
                body={"site": {"type": "SITE", "identifier": site_url}},
            )
            .execute()
        )
        search_console.sites().add(siteUrl=site_url).execute()
        sitemap_url = ""
        if body.submit_sitemap:
            normalized = site_url if site_url.endswith("/") else f"{site_url}/"
            sitemap_url = urllib.parse.urljoin(normalized, "sitemap.xml")
            search_console.sitemaps().submit(siteUrl=site_url, feedpath=sitemap_url).execute()
        return {
            "verified_resource": (verify_resp or {}).get("id") if isinstance(verify_resp, dict) else None,
            "sitemap_url": sitemap_url,
        }

    @app.post("/v1/gsc/add-property")
    def gsc_add_property(
        request: Request,
        body: _GscTokenBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        site_url = str(body.site_url or "").strip()
        if not site_url:
            raise HTTPException(status_code=400, detail="site_url_required")
        if not site_url.endswith("/"):
            site_url = f"{site_url}/"
        service = _gsc_build("webmasters", "v3")
        sites_resp = service.sites().list().execute()
        entries = sites_resp.get("siteEntry", []) if isinstance(sites_resp, dict) else []
        existing_urls = {
            str(entry.get("siteUrl") or "").strip()
            for entry in entries
            if isinstance(entry, dict)
        }
        if site_url in existing_urls:
            return {"success": True, "site_url": site_url, "already_existed": True}
        service.sites().add(siteUrl=site_url).execute()
        return {"success": True, "site_url": site_url, "already_existed": False}

    @app.post("/v1/openmeter/request")
    def openmeter_request(
        request: Request,
        body: _OpenMeterRequestBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        base_url = str(
            safebox.first_env_backed_value("TAKYON_OPENMETER_URL", "OPENMETER_URL", "OPENMETER_API_URL")
            or ""
        ).strip().rstrip("/")
        if not base_url:
            raise HTTPException(status_code=404, detail="openmeter_unconfigured")
        token = str(
            safebox.first_env_backed_value("OPENMETER_API_TOKEN", "TAKYON_OPENMETER_API_TOKEN") or ""
        ).strip()
        raw_path = str(body.path or "").strip()
        parsed_path = urllib.parse.urlparse(raw_path)
        if parsed_path.scheme or parsed_path.netloc or not raw_path.startswith("/"):
            raise HTTPException(status_code=400, detail="openmeter_path_must_be_relative")
        query = urllib.parse.urlencode(body.query or {}, doseq=True)
        url = f"{base_url}{raw_path}"
        if query:
            url = f"{url}?{query}"
        expected = set(int(x) for x in (body.expected_status or [200]))
        allow = set(int(x) for x in (body.allow_status or []))
        headers = {"Accept": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        data = None
        if body.payload is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body.payload).encode("utf-8")
        req = urllib.request.Request(url, data=data, headers=headers, method=str(body.method or "GET").upper())
        try:
            with urllib.request.urlopen(req, timeout=float(body.timeout or 20.0)) as resp:
                status = int(getattr(resp, "status", 200) or 200)
                raw = resp.read().decode("utf-8", errors="replace")
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode("utf-8", errors="replace")
            if exc.code in allow:
                return {"status": exc.code, "body": None}
            raise HTTPException(status_code=502, detail=f"OpenMeter {body.method.upper()} {raw_path} failed: {exc.code} {raw}") from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"OpenMeter {body.method.upper()} {raw_path} failed: {exc}") from exc
        if status not in expected and status not in allow:
            raise HTTPException(status_code=502, detail=f"OpenMeter {body.method.upper()} {raw_path} returned {status}: {raw}")
        if not raw.strip():
            parsed: Any = {}
        else:
            try:
                parsed = json.loads(raw)
            except Exception as exc:
                raise HTTPException(status_code=502, detail=f"OpenMeter {body.method.upper()} {raw_path} returned invalid JSON") from exc
        return {"status": status, "body": parsed if isinstance(parsed, (dict, list)) else {}}

    @app.post("/v1/providers/meta/config")
    def provider_meta_config(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # The official Meta MCP OAuth token and legacy system-user token are provider secrets held
        # here and DENIED /v1/env egress, so a runtime plane cannot resolve them. This returns only
        # NON-SECRET readiness/config hints. Live v2 launches use the /meta/mcp/* broker routes.
        # Gated by the internal token (transport reachability); the per-action money gate lives
        # upstream in the meta-ads handlers.
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import meta_mcp as _meta_mcp

        version = str(safebox.first_env_backed_value("META_GRAPH_VERSION") or "v23.0").strip().lstrip("/")
        if not version:
            version = "v23.0"
        elif not version.startswith("v"):
            version = f"v{version}"
        token = str(
            safebox.first_env_backed_value("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN") or ""
        ).strip()
        mcp_token = str(safebox.first_env_backed_value(*_meta_mcp.META_MCP_TOKEN_ALIASES) or "").strip()
        endpoint = str(
            safebox.first_env_backed_value(*_meta_mcp.META_MCP_ENDPOINT_ALIASES)
            or _meta_mcp.DEFAULT_META_MCP_ENDPOINT
        ).strip() or _meta_mcp.DEFAULT_META_MCP_ENDPOINT
        return {
            "token": "",
            "has_token": bool(token),
            "has_mcp_oauth_token": bool(mcp_token),
            "mcp_endpoint": endpoint,
            "version": version,
            "ad_account_id": str(safebox.first_env_backed_value("META_AD_ACCOUNT_ID") or "").strip(),
            "page_id": str(safebox.first_env_backed_value("META_PAGE_ID") or "").strip(),
            "instagram_user_id": str(safebox.first_env_backed_value("META_INSTAGRAM_ID") or "").strip(),
            "composio_connected_account_id": "",
            "composio_user_id": "",
            "composio_alias": "",
        }

    @app.post("/v1/providers/meta/mcp/tools")
    def provider_meta_mcp_tools(
        request: Request,
        body: _MetaMCPToolsBody | None = None,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Official Meta Ads MCP tool discovery. The OAuth token is resolved locally by safebox.py and
        # never leaves this host; callers receive only the key-free MCP tool schemas.
        _require_internal_token(authorization)
        _require_operator_client(request)
        try:
            return safebox.meta_mcp_list_tools(timeout=(body.timeout if body else 60.0))
        except safebox.RemoteSafeboxError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.payload.get("detail") or str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/meta/mcp/call")
    def provider_meta_mcp_call(
        request: Request,
        body: _MetaMCPCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Official Meta Ads MCP action broker. This is the v2 launch/control/read transport; it does
        # not use the legacy Meta developer app, and it does not use Composio.
        _require_internal_token(authorization)
        _require_operator_client(request)
        try:
            return safebox.meta_mcp_call(
                tool_name=body.tool_name,
                arguments=dict(body.arguments or {}),
                timeout=body.timeout,
            )
        except safebox.RemoteSafeboxError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.payload.get("detail") or str(exc)) from exc
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/meta/graph")
    def provider_meta_graph(
        request: Request,
        body: _MetaGraphBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Legacy compatibility route. Production launch/control/read calls use official Meta MCP;
        # this remains internal-only for Graph shims/diagnostics and still never egresses the token.
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import meta_graph as _meta_graph

        version = str(safebox.first_env_backed_value("META_GRAPH_VERSION") or "v23.0").strip().lstrip("/")
        if not version:
            version = "v23.0"
        elif not version.startswith("v"):
            version = f"v{version}"
        token = str(
            safebox.first_env_backed_value("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN") or ""
        ).strip()
        if not token:
            raise HTTPException(status_code=502, detail="META_SYSTEM_USER_ACCESS_TOKEN is not configured")
        host = str(body.host or _meta_graph._GRAPH_HOST).strip().lower()
        if host != _meta_graph._GRAPH_HOST:
            raise HTTPException(status_code=400, detail="meta_graph_host_not_allowed")
        try:
            return _meta_graph._graph(
                body.method,
                str(body.path or "").lstrip("/"),
                dict(body.params or {}),
                token=token,
                version=version,
                host=_meta_graph._GRAPH_HOST,
                timeout=float(body.timeout),
            )
        except _meta_graph.MetaGraphError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/meta/graph/upload-video")
    def provider_meta_graph_upload_video(
        request: Request,
        body: _MetaGraphUploadVideoBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import meta_graph as _meta_graph

        version = str(safebox.first_env_backed_value("META_GRAPH_VERSION") or "v23.0").strip().lstrip("/")
        if not version:
            version = "v23.0"
        elif not version.startswith("v"):
            version = f"v{version}"
        token = str(
            safebox.first_env_backed_value("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN") or ""
        ).strip()
        if not token:
            raise HTTPException(status_code=502, detail="META_SYSTEM_USER_ACCESS_TOKEN is not configured")
        try:
            raw = base64.b64decode(str(body.data_b64 or ""), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_base64") from exc
        try:
            video_id = _meta_graph.upload_video(
                token,
                str(body.ad_account_id or ""),
                raw,
                name=str(body.name or ""),
                version=version,
                poll=bool(body.poll),
                timeout=float(body.timeout),
            )
            return {"video_id": video_id}
        except _meta_graph.MetaGraphError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/meta/graph/upload-image")
    def provider_meta_graph_upload_image(
        request: Request,
        body: _MetaGraphUploadImageBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import meta_graph as _meta_graph

        version = str(safebox.first_env_backed_value("META_GRAPH_VERSION") or "v23.0").strip().lstrip("/")
        if not version:
            version = "v23.0"
        elif not version.startswith("v"):
            version = f"v{version}"
        token = str(
            safebox.first_env_backed_value("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN") or ""
        ).strip()
        if not token:
            raise HTTPException(status_code=502, detail="META_SYSTEM_USER_ACCESS_TOKEN is not configured")
        try:
            raw = base64.b64decode(str(body.data_b64 or ""), validate=True)
        except Exception as exc:
            raise HTTPException(status_code=400, detail="invalid_base64") from exc
        try:
            return _meta_graph.upload_image(
                token,
                str(body.ad_account_id or ""),
                raw,
                name=str(body.name or ""),
                version=version,
                timeout=float(body.timeout),
            )
        except _meta_graph.MetaGraphError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/providers/meta/graph/ensure-custom-conversion")
    def provider_meta_graph_ensure_custom_conversion(
        request: Request,
        body: _MetaEnsureCustomConversionBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        from . import meta_graph as _meta_graph

        version = str(safebox.first_env_backed_value("META_GRAPH_VERSION") or "v23.0").strip().lstrip("/")
        if not version:
            version = "v23.0"
        elif not version.startswith("v"):
            version = f"v{version}"
        token = str(
            safebox.first_env_backed_value("META_SYSTEM_USER_ACCESS_TOKEN", "META_ACCESS_TOKEN") or ""
        ).strip()
        if not token:
            raise HTTPException(status_code=502, detail="META_SYSTEM_USER_ACCESS_TOKEN is not configured")
        if not str(body.event_source_id or "").strip():
            raise HTTPException(status_code=400, detail="event_source_id_required")
        try:
            return _meta_graph.ensure_custom_conversion(
                token,
                str(body.ad_account_id or ""),
                name=str(body.name or ""),
                rule=str(body.rule or ""),
                custom_event_type=str(body.custom_event_type or ""),
                event_source_id=str(body.event_source_id or ""),
                version=version,
            )
        except _meta_graph.MetaGraphError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/storage/get")
    def storage_get(
        request: Request,
        body: _StorageKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        provider = _storage_provider(body.provider)
        _storage_business_slug(body.key, require_existing=False)
        try:
            data = safebox.storage_get(provider, body.key)
        except Exception as exc:
            if type(exc).__name__ == "ObjectNotFound":
                raise HTTPException(status_code=404, detail="object_not_found") from exc
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"provider": provider, "key": body.key, "data_b64": base64.b64encode(data).decode("ascii")}

    @app.post("/v1/storage/delete")
    def storage_delete(
        request: Request,
        body: _StorageKeyBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        provider = _storage_provider(body.provider)
        _storage_business_slug(body.key, require_existing=False)
        try:
            return safebox.storage_delete(provider, body.key)
        except Exception as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

    @app.post("/v1/storage/list-digests")
    def storage_list_digests(
        request: Request,
        body: _StorageListBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _StorageListBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
            detail = "invalid_livemode" if str(exc) == "invalid_livemode" else "invalid_signature"
            raise HTTPException(status_code=400, detail=detail) from exc
        from .control_api import process_billing_webhook_event

        with _safebox_db_conn() as conn:
            return process_billing_webhook_event(conn, event)

    @app.post("/v1/custody/accounts/open")
    def open_custody_account(
        request: Request,
        body: _OpenCustodyAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        safebox._local_open_custody_account(None, body.user_id, currency=body.currency or "usd")
        return {"ok": True}

    @app.post("/v1/creative-credits/accounts/open")
    def open_creative_credit_account(
        request: Request,
        body: _OpenCreativeCreditAccountBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, bool]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        safebox._local_open_business_credit_account(None, body.business_slug)
        return {"ok": True}

    @app.post("/v1/creative-credits/bootstrap-starter")
    def grant_business_bootstrap_credits(
        request: Request,
        body: _BusinessBootstrapCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        business_slug: str,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        balances = safebox._local_get_business_credit_balances(None, business_slug)
        return {
            "business_slug": balances.business_slug,
            "balance_credits": balances.balance_credits,
            "reserved_credits": balances.reserved_credits,
        }

    @app.post("/v1/creative-credits/checkout")
    def create_creative_credit_checkout(
        request: Request,
        body: _CreativeCreditCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
        _require_stripe_checkout_enabled()
        _require_creative_checkout_enabled()
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
        request: Request,
        body: _ReconcileCreativeCreditCheckoutBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _GrantCreativeCreditsBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        try:
            event = safebox.verify_stripe_billing_webhook(body.raw_body, body.signature)
        except safebox.StripeBillingWebhookUnconfigured as exc:
            raise HTTPException(status_code=503, detail="billing_webhook_unconfigured") from exc
        except safebox.StripeBillingWebhookInvalidSignature as exc:
            detail = "invalid_livemode" if str(exc) == "invalid_livemode" else "invalid_signature"
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"event": event}

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
        try:
            event = safebox.verify_stripe_app_webhook(body.raw_body, body.signature)
        except safebox.StripeAppWebhookUnconfigured as exc:
            raise HTTPException(status_code=503, detail="app_webhook_unconfigured") from exc
        except safebox.StripeAppWebhookInvalidSignature as exc:
            detail = "invalid_livemode" if str(exc) == "invalid_livemode" else "invalid_signature"
            raise HTTPException(status_code=400, detail=detail) from exc
        return {"event": event}

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
            detail = "invalid_livemode" if str(exc) == "invalid_livemode" else "invalid_signature"
            raise HTTPException(status_code=400, detail=detail) from exc
        from . import app_payments

        event_type = str(event.get("type") or "")
        obj = (event.get("data") or {}).get("object") or {}
        checkout_subscription: dict[str, Any] | None = None
        if str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live":
            proof_path = ""
            proof_id = ""
            if event_type == "checkout.session.completed":
                proof_id = str(obj.get("id") or "")
                proof_path = f"checkout/sessions/{proof_id}"
            elif event_type in {
                "customer.subscription.created",
                "customer.subscription.updated",
                "customer.subscription.deleted",
            }:
                proof_id = str(obj.get("id") or "")
                proof_path = f"subscriptions/{proof_id}"
            elif event_type in {"invoice.paid", "invoice.payment_failed"}:
                proof_id = str(obj.get("id") or "")
                proof_path = f"invoices/{proof_id}"
            elif event_type == "charge.refunded":
                proof_id = str(obj.get("id") or "")
                proof_path = f"charges/{proof_id}"
            elif event_type in {
                "charge.dispute.created",
                "charge.dispute.updated",
                "charge.dispute.closed",
                "charge.dispute.funds_withdrawn",
                "charge.dispute.funds_reinstated",
            }:
                proof_id = str(obj.get("id") or "")
                proof_path = f"disputes/{proof_id}"
            if proof_path and proof_id:
                try:
                    proof = (
                        _stripe_invoice_with_all_payments(proof_id)
                        if event_type in {"invoice.paid", "invoice.payment_failed"}
                        else safebox.stripe_request(proof_path, {}, method="GET")
                    )
                except Exception as exc:
                    raise HTTPException(
                        status_code=503, detail="stripe_account_object_proof_pending"
                    ) from exc
                if (
                    not isinstance(proof, dict)
                    or str(proof.get("id") or "") != proof_id
                    or proof.get("livemode") is not True
                ):
                    raise HTTPException(
                        status_code=503, detail="stripe_account_object_proof_pending"
                    )
                if event_type in {
                    "charge.dispute.created",
                    "charge.dispute.updated",
                    "charge.dispute.closed",
                    "charge.dispute.funds_withdrawn",
                    "charge.dispute.funds_reinstated",
                }:
                    obj.clear()
                    obj.update(proof)
                    charge_id = _stripe_object_id(obj.get("charge"))
                    if not charge_id:
                        raise HTTPException(
                            status_code=503, detail="stripe_account_object_proof_pending"
                        )
                    try:
                        charge = safebox.stripe_request(
                            f"charges/{charge_id}", {}, method="GET"
                        )
                    except Exception as exc:
                        raise HTTPException(
                            status_code=503, detail="stripe_account_object_proof_pending"
                        ) from exc
                    if (
                        not isinstance(charge, dict)
                        or str(charge.get("id") or "") != charge_id
                        or charge.get("livemode") is not True
                    ):
                        raise HTTPException(
                            status_code=503, detail="stripe_account_object_proof_pending"
                        )
                    charge_gross_cents = charge.get("amount")
                    charge_refunded_cents = charge.get("amount_refunded")
                    if (
                        not isinstance(charge_gross_cents, int)
                        or isinstance(charge_gross_cents, bool)
                        or not isinstance(charge_refunded_cents, int)
                        or isinstance(charge_refunded_cents, bool)
                        or charge_gross_cents < 0
                        or charge_refunded_cents < 0
                        or charge_refunded_cents > charge_gross_cents
                    ):
                        raise HTTPException(
                            status_code=503, detail="stripe_account_object_proof_pending"
                        )
                    obj.update(
                        {
                            key: charge.get(key)
                            for key in (
                                "currency",
                                "customer",
                                "invoice",
                                "metadata",
                                "payment_intent",
                            )
                        }
                    )
                    obj["_takyon_charge_gross_cents"] = charge_gross_cents
                    obj["_takyon_charge_amount_refunded_cents"] = (
                        charge_refunded_cents
                    )
                    if str(obj.get("status") or "").strip().lower() in {
                        "won",
                        "warning_closed",
                    }:
                        payment_intent_id = _stripe_object_id(
                            charge.get("payment_intent")
                        )
                        binding = _stripe_payment_subscription_binding(
                            payment_intent_id, charge_id
                        )
                        if binding is None:
                            raise HTTPException(
                                status_code=503,
                                detail="stripe_account_object_proof_pending",
                            )
                        bound_business, subscription_id = binding
                        try:
                            subscription = safebox.stripe_request(
                                f"subscriptions/{subscription_id}", {}, method="GET"
                            )
                        except Exception as exc:
                            raise HTTPException(
                                status_code=503,
                                detail="stripe_account_object_proof_pending",
                            ) from exc
                        obj["_takyon_subscription"] = _require_app_subscription_proof(
                            subscription,
                            subscription_id,
                            business=bound_business,
                            detail="stripe_account_object_proof_pending",
                        )
                else:
                    # Webhook delivery can be out of order. Reconcile the current Stripe object
                    # fetched under the configured live account, not the stale event snapshot.
                    obj.clear()
                    obj.update(proof)
                    if event_type in {"invoice.paid", "invoice.payment_failed"}:
                        subscription_id = app_payments._invoice_subscription_id(obj)
                        if subscription_id:
                            try:
                                subscription = safebox.stripe_request(
                                    f"subscriptions/{subscription_id}", {}, method="GET"
                                )
                            except Exception as exc:
                                raise HTTPException(
                                    status_code=503,
                                    detail="stripe_account_object_proof_pending",
                                ) from exc
                            if (
                                not isinstance(subscription, dict)
                                or str(subscription.get("id") or "")
                                != subscription_id
                                or subscription.get("livemode") is not True
                            ):
                                raise HTTPException(
                                    status_code=503,
                                    detail="stripe_account_object_proof_pending",
                                )
                            obj["_takyon_subscription"] = subscription
                    if event_type == "checkout.session.completed":
                        invoice_id = _stripe_object_id(obj.get("invoice"))
                        if invoice_id:
                            try:
                                invoice = _stripe_invoice_with_all_payments(invoice_id)
                            except Exception as exc:
                                raise HTTPException(
                                    status_code=503,
                                    detail="stripe_account_object_proof_pending",
                                ) from exc
                            if (
                                not invoice
                                or invoice.get("livemode") is not True
                            ):
                                raise HTTPException(
                                    status_code=503,
                                    detail="stripe_account_object_proof_pending",
                                )
                            obj["_takyon_invoice"] = invoice

        checkout_metadata = (
            obj.get("metadata") if isinstance(obj.get("metadata"), dict) else {}
        )
        if (
            event_type == "checkout.session.completed"
            and checkout_metadata.get("source") == "takyon_app"
        ):
            checkout_business = str(checkout_metadata.get("business") or "").strip()
            subscription_id = _stripe_object_id(obj.get("subscription"))
            if not checkout_business or not subscription_id.startswith("sub_"):
                raise HTTPException(
                    status_code=503, detail="stripe_subscription_reconcile_pending"
                )
            try:
                subscription = safebox.stripe_request(
                    f"subscriptions/{subscription_id}", {}, method="GET"
                )
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="stripe_subscription_reconcile_pending"
                ) from exc
            checkout_subscription = _require_app_subscription_proof(
                subscription,
                subscription_id,
                business=checkout_business,
                detail="stripe_subscription_reconcile_pending",
            )

        with _safebox_db_conn() as conn:
            try:
                if checkout_subscription is None:
                    result = app_payments.record_webhook_and_process(conn, event)
                else:
                    with conn.transaction():
                        result = app_payments.record_webhook_and_process(conn, event)
                        subscription_result = app_payments.reconcile_subscription(
                            conn, checkout_subscription
                        )
                        if not bool(subscription_result.get("recorded")):
                            raise app_payments.RetryableWebhookEvent(
                                "stripe_subscription_reconcile_pending"
                            )
                        result["subscription"] = subscription_result
            except app_payments.RetryableWebhookEvent as exc:
                raise HTTPException(
                    status_code=503, detail="stripe_event_dependency_pending"
                ) from exc
            return result

    @app.post("/v1/shopify/app-webhook/process")
    def process_shopify_app_webhook(
        body: _ShopifyAppWebhookBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # UC4 Shopify shop/update rail — the Shopify analogue of the Stripe app-webhook process
        # route above: HMAC verification (X-Shopify-Hmac-Sha256 over the raw body, shared secret
        # read LOCALLY on the safebox, never vended) and the pricing-affecting recompose run
        # TOGETHER on the safebox, so a plan_key version is only ever minted from a genuinely
        # signed shop/update. Dedup (provider='shopify', content-derived event id) happens inside
        # record_webhook_and_process BEFORE any state change. Fail-closed: missing secret → 503,
        # bad HMAC → 401; an unverified body is never parsed into effects.
        _require_internal_token(authorization)
        try:
            safebox.verify_shopify_app_webhook(body.raw_body, body.hmac_sha256)
        except safebox.ShopifyAppWebhookUnconfigured as exc:
            raise HTTPException(status_code=503, detail="shopify_webhook_unconfigured") from exc
        except safebox.ShopifyAppWebhookInvalidSignature as exc:
            raise HTTPException(status_code=401, detail="invalid_signature") from exc
        from . import shopify_util

        try:
            with _safebox_db_conn() as conn:
                return shopify_util.record_webhook_and_process(
                    conn, topic=body.topic, raw_body=body.raw_body
                )
        except shopify_util.ShopifyWebhookInvalidEvent as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

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
        expected_business = str(body.business_slug or "").strip()
        expected_user = str(body.app_user_id or "").strip()
        expected_email = str(body.customer_email or "").strip().lower()
        if not expected_business or (not expected_user and not expected_email):
            raise HTTPException(status_code=403, detail="checkout_context_required")
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
        if expected_business and _require_safe_slug(expected_business) != business:
            raise HTTPException(status_code=403, detail="checkout_business_mismatch")
        if str(session.get("status") or "").strip().lower() != "complete":
            raise HTTPException(status_code=409, detail="checkout_session_not_complete")
        if str(session.get("payment_status") or "").strip().lower() != "paid":
            raise HTTPException(status_code=409, detail="checkout_session_unpaid")

        metadata = session.get("metadata") if isinstance(session.get("metadata"), dict) else {}
        intent_id = str(metadata.get("checkout_intent_id") or "").strip()
        client_reference_id = str(session.get("client_reference_id") or "").strip()
        invoice_id = _stripe_object_id(session.get("invoice"))
        if invoice_id:
            try:
                invoice = _stripe_invoice_with_all_payments(invoice_id)
            except Exception as exc:
                raise HTTPException(
                    status_code=503, detail="stripe_invoice_payment_evidence_pending"
                ) from exc
            if not invoice:
                raise HTTPException(
                    status_code=503, detail="stripe_invoice_payment_evidence_pending"
                )
            session["_takyon_invoice"] = invoice
        subscription_id = _stripe_object_id(session.get("subscription"))
        if not subscription_id:
            raise HTTPException(
                status_code=503, detail="stripe_subscription_reconcile_pending"
            )
        try:
            subscription = safebox.stripe_request(
                f"subscriptions/{subscription_id}", {}, method="GET"
            )
        except stripe_util.StripeError as exc:
            raise HTTPException(
                status_code=503, detail="stripe_subscription_reconcile_pending"
            ) from exc
        expected_livemode = (
            str(os.getenv("TAKYON_STRIPE_MODE") or "test").strip().lower() == "live"
        )
        if (
            not isinstance(subscription, dict)
            or str(subscription.get("id") or "") != subscription_id
            or subscription.get("object") not in {None, "subscription"}
            or subscription.get("livemode") is not expected_livemode
        ):
            raise HTTPException(
                status_code=503, detail="stripe_subscription_reconcile_pending"
            )
        subscription_metadata = (
            subscription.get("metadata")
            if isinstance(subscription.get("metadata"), dict)
            else {}
        )
        expected_account_id = str(os.getenv("TAKYON_STRIPE_ACCOUNT_ID") or "").strip()
        if (
            subscription_metadata.get("source") != "takyon_app"
            or str(subscription_metadata.get("business") or "").strip() != business
            or not expected_account_id
            or subscription_metadata.get("takyon_stripe_account_id") != expected_account_id
        ):
            raise HTTPException(
                status_code=503, detail="stripe_subscription_reconcile_pending"
            )
        with _safebox_db_conn() as conn:
            with conn.transaction():
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
                    raise HTTPException(
                        status_code=403, detail="checkout_intent_business_mismatch"
                    )
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
                subscription_result = app_payments.reconcile_subscription(
                    conn, subscription
                )
                if not bool(subscription_result.get("recorded")):
                    raise HTTPException(
                        status_code=503, detail="stripe_subscription_reconcile_pending"
                    )
        return {
            "ok": True,
            "session_id": session_id,
            "business_slug": business,
            "processed": checkout_result,
            "subscription": subscription_result,
        }

    @app.post("/v1/stripe/app-reversal/reconcile")
    def reconcile_stripe_app_reversal(
        body: _AppChargeReversalReconcileBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        # Recovery path for a real Stripe charge refund when the webhook has not arrived yet.
        # The safebox reads the charge from Stripe, verifies it belongs to the expected app checkout,
        # and then delegates to the same reversal processor used by signed webhooks.
        _require_internal_token(authorization)
        from . import app_payments, stripe_util

        charge_id = str(body.charge_id or "").strip()
        if not charge_id or not charge_id.startswith("ch_"):
            raise HTTPException(status_code=400, detail="invalid_charge")
        expected_business = str(body.business_slug or "").strip()
        expected_session = str(body.checkout_session_id or "").strip()
        if not expected_business and not expected_session:
            raise HTTPException(status_code=403, detail="reversal_context_required")
        try:
            charge = safebox.stripe_request(f"charges/{charge_id}", {}, method="GET")
        except stripe_util.StripeError as exc:
            message = str(exc)
            if " failed: 404" in message:
                raise HTTPException(status_code=404, detail="unknown_charge") from exc
            if "STRIPE_SECRET_KEY" in message:
                raise HTTPException(status_code=503, detail="stripe_unconfigured") from exc
            raise HTTPException(status_code=502, detail="stripe_error") from exc
        if not isinstance(charge, dict) or not charge:
            raise HTTPException(status_code=404, detail="unknown_charge")
        amount_refunded = int(charge.get("amount_refunded") or 0)
        if amount_refunded <= 0:
            raise HTTPException(status_code=409, detail="charge_not_refunded")
        try:
            refunds = safebox.stripe_request(
                "refunds", {"charge": charge_id, "limit": 1}, method="GET"
            )
        except stripe_util.StripeError as exc:
            raise HTTPException(status_code=502, detail="stripe_refund_evidence_error") from exc
        refund_rows = refunds.get("data") if isinstance(refunds, dict) else None
        latest_refund = refund_rows[0] if isinstance(refund_rows, list) and refund_rows else None
        if (
            not isinstance(latest_refund, dict)
            or _stripe_object_id(latest_refund.get("charge")) != charge_id
            or int(latest_refund.get("created") or 0) <= 0
        ):
            raise HTTPException(status_code=503, detail="stripe_refund_evidence_pending")

        payment_intent_id = str(charge.get("payment_intent") or "").strip()
        customer_id = str(charge.get("customer") or "").strip()
        with _safebox_db_conn() as conn:
            row = None
            if payment_intent_id:
                row = conn.execute(
                    "select business_slug, stripe_checkout_session_id "
                    "from app_checkout_sessions where stripe_payment_intent_id = %s limit 1",
                    (payment_intent_id,),
                ).fetchone()
            if row is None and customer_id:
                row = conn.execute(
                    "select business_slug, stripe_checkout_session_id "
                    "from app_checkout_sessions where stripe_customer_id = %s "
                    "order by created_at desc limit 1",
                    (customer_id,),
                ).fetchone()
            if row is None:
                raise HTTPException(status_code=404, detail="unknown_payment")
            business = str(row[0] or "").strip()
            session_id = str(row[1] or "").strip()
            if expected_business and _require_safe_slug(expected_business) != business:
                raise HTTPException(status_code=403, detail="reversal_business_mismatch")
            if expected_session and expected_session != session_id:
                raise HTTPException(status_code=403, detail="reversal_checkout_session_mismatch")
            event = {
                "id": (
                    "charge.refunded.reconcile:"
                    f"{str(latest_refund.get('id') or charge_id)}:{amount_refunded}"
                ),
                "type": "charge.refunded",
                "created": latest_refund.get("created"),
                "data": {"object": charge},
            }
            processed = app_payments.record_webhook_and_process(conn, event)
        return {
            "ok": True,
            "charge_id": charge_id,
            "business_slug": business,
            "checkout_session_id": session_id,
            "deduplicated": bool(processed.get("deduplicated"))
            or not bool((processed.get("processed") or {}).get("reversal_recorded")),
            "processed": processed,
        }

    @app.post("/v1/stripe/app-subscription/cancel")
    def cancel_stripe_app_subscription(
        body: _AppSubscriptionCancelBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import app_identity, app_payments, stripe_util

        business = _require_safe_slug(body.business_slug)
        app_user_id = str(body.app_user_id or "").strip()
        if not app_user_id:
            raise HTTPException(status_code=400, detail="app_user_id_required")
        session_token = str(body.session_token or "").strip()
        if not session_token:
            raise HTTPException(status_code=403, detail="app_session_required")
        with _safebox_db_conn() as conn:
            session_user = app_identity.validate_session(conn, business, session_token)
            if session_user is None:
                raise HTTPException(status_code=403, detail="app_session_invalid")
            if str(session_user.id) != app_user_id:
                raise HTTPException(status_code=403, detail="app_session_user_mismatch")
            try:
                return app_payments.cancel_subscription(
                    conn,
                    business,
                    app_user_id=app_user_id,
                    cancel_at_period_end=bool(body.cancel_at_period_end),
                    subscription_updater=lambda subscription_id, should_cancel_at_period_end: safebox.stripe_request(
                        f"subscriptions/{subscription_id}",
                        {"cancel_at_period_end": "true" if should_cancel_at_period_end else "false"},
                    ),
                )
            except app_payments.CancelableSubscriptionNotFound as exc:
                raise HTTPException(status_code=404, detail="no_cancelable_subscription") from exc
            except stripe_util.StripeError as exc:
                message = str(exc)
                if "STRIPE_SECRET_KEY" in message:
                    raise HTTPException(status_code=503, detail="stripe_unconfigured") from exc
                raise HTTPException(status_code=502, detail="stripe_error") from exc

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

    # ── Capability mint + action-shaped broker routes ─────────────────────────────────────────────
    # These are the authority path. The remaining /v1/env egress routes are read-only public-config
    # compatibility, not provider/money/secret authority. Each provider route brokers the call entirely
    # inside the safebox: the capability token is verified (authoritative scope), its nonce claimed
    # once, usage reserved on the validated {business, app_user} via the SECURITY DEFINER ledger, the
    # provider key resolved LOCALLY, the provider called, and the cost settled — the caller never sees
    # the key.

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
        request: Request,
        body: _OperatorSessionTokenBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        """Mint a SESSION-scoped operator capability (audience = operator.session) for one CEO/worker
        run. Business-scoped runs validate operator ownership of the business (boundary 1 via
        ``authorize_operator_call``); root-scope runs either validate a dashboard Auth0 session and
        derive the REAL Takyon user id from that verified session, or fall back only to an ACTIVE
        Takyon user on the operator-only rail when no verified dashboard session exists.
        The token binds the per-CALL cost ceiling and is REUSABLE + TTL-bounded, so the operator plane
        presents it on every Anthropic / Tavily proxy call without ever seeing the raw provider key. The
        signing key lives ONLY on the safebox, so the operator host cannot forge or widen scope.
        Internal-token only.

        Distinct from ``/v1/token/mint``: that mints a SINGLE-USE (nonce-claimed) per-action capability
        for the metered ``/v1/providers/*`` business broker; this mints a long-lived, reusable session
        token for the operator PROXY routes, which meter EACH call against the operator's control-plane
        budget without claiming a nonce."""
        _require_internal_token(authorization)
        _require_operator_client(request)
        ttl_seconds = int(body.ttl_seconds or _OPERATOR_SESSION_TTL_SECONDS)
        if ttl_seconds <= 0:
            raise HTTPException(status_code=400, detail="ttl_must_be_positive")
        # Clamp the session TTL so a leaked token still expires within the hard bound.
        ttl_seconds = min(ttl_seconds, _OPERATOR_SESSION_TTL_MAX_SECONDS)
        business = str(body.business or "").strip()
        if business:
            token = _mint_capability_token(
                business=business,
                action=_OPERATOR_SESSION_AUDIENCE,
                max_cost_microusd=int(body.max_cost_microusd),
                session_token=None,
                operator_user_id=body.operator_user_id,
                audience=_OPERATOR_SESSION_AUDIENCE,
                ttl_seconds=ttl_seconds,
                now=int(time.time()),
            )
        else:
            requested_user_id = str(body.operator_user_id or "").strip()
            resolved_user_id = ""
            session_user = safebox.auth0_verify_session(
                session_token=str(body.session_token or ""),
            )
            if isinstance(session_user, dict):
                auth0_sub = str(session_user.get("sub") or "").strip()
                if not auth0_sub:
                    raise HTTPException(status_code=403, detail="operator_root_session_required")
                with _safebox_db_conn() as conn:
                    row = conn.execute(
                        "select id from users where auth0_sub = %s",
                        (auth0_sub,),
                    ).fetchone()
                if row is None:
                    raise HTTPException(status_code=404, detail="operator_user_not_found")
                resolved_user_id = str(_db_row_value(row, 0, "id") or "").strip()
                if not requested_user_id or requested_user_id != resolved_user_id:
                    raise HTTPException(status_code=403, detail="operator_user_mismatch")
            else:
                from . import control_plane

                with _safebox_db_conn() as conn:
                    principal = control_plane.resolve_user_principal(
                        conn,
                        requested_user_id,
                        key_id="operator-root-local",
                    )
                if principal is None:
                    raise HTTPException(status_code=403, detail="operator_root_session_required")
                resolved_user_id = str(getattr(principal, "user_id", "") or "").strip()
            signing_key = _cap_signing_key()
            if not signing_key:
                raise HTTPException(status_code=503, detail="capability_signing_unconfigured")
            token = mint_capability(
                CapabilityScope(
                    takyon_user_id=resolved_user_id,
                    business_slug="",
                    app_user_id=None,
                    action=_OPERATOR_SESSION_AUDIENCE,
                    max_cost_microusd=int(body.max_cost_microusd),
                ),
                signing_key=signing_key,
                audience=_OPERATOR_SESSION_AUDIENCE,
                nonce=str(uuid.uuid4()),
                issued_at=int(time.time()),
                ttl_seconds=ttl_seconds,
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
        # DeepSeek models ride the SAME anthropic wire (audience/action unchanged: the client
        # brokers them as anthropic.messages) but need the key + upstream URL swap the operator
        # proxy lane already does — without this, a deepseek-* model was sent to the REAL
        # Anthropic API with the Anthropic key and failed provider_error on every call.
        from . import ai_provider as _ai

        if _ai._is_deepseek_model(_ai.anthropic_model(body.payload or {})):
            return _broker_provider_route(
                body,
                audience=_ANTHROPIC_AUDIENCE,
                provider="deepseek",
                key_resolver=_deepseek_key_resolver,
                caller_builder=_deepseek_provider_caller,
                estimate_builder=_anthropic_estimate,
            )
        return _broker_provider_route(
            body,
            audience=_ANTHROPIC_AUDIENCE,
            provider="anthropic",
            key_resolver=_anthropic_key_resolver,
            caller_builder=_anthropic_provider_caller,
            estimate_builder=_anthropic_estimate,
        )

    @app.post("/v1/providers/openai/messages")
    def provider_openai_messages(
        body: _ProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _broker_provider_route(
            body,
            audience=_OPENAI_AUDIENCE,
            provider="openai",
            key_resolver=_openai_key_resolver,
            caller_builder=_openai_provider_caller,
            estimate_builder=_openai_estimate,
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

    @app.post("/v1/providers/postmark/send")
    def provider_postmark_send(
        body: _ProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        from . import app_usage, safebox_broker
        from .safebox_capability import CapabilityError, CapabilityScope

        signing_key = _cap_signing_key()
        if not signing_key:
            raise HTTPException(status_code=503, detail="capability_signing_unconfigured")
        if str(body.token or "").strip():
            raise HTTPException(status_code=403, detail="postmark_requires_service_session")
        if str(body.action or "").strip() != _POSTMARK_SEND_AUDIENCE:
            raise HTTPException(status_code=400, detail="action_audience_mismatch")

        payload = dict(body.payload or {})
        recipient_ref = str(payload.get("recipient_app_user_id") or "").strip()
        try:
            subject = str(payload.get("subject") or "")
            text_body = str(payload.get("text_body") or "")
            if not subject.strip() or not text_body.strip():
                raise ValueError("missing_email_body")
            resolved = _postmark_authorize_service_send(
                business=str(body.business or ""),
                session_token=str(body.session_token or ""),
                recipient_app_user_id=recipient_ref,
            )
        except HTTPException:
            raise
        except (ValueError, KeyError, TypeError) as exc:
            raise HTTPException(status_code=400, detail="invalid_provider_payload") from exc

        now = int(time.time())
        token = mint_capability(
            CapabilityScope(
                takyon_user_id=resolved["owner_user_id"],
                business_slug=_require_safe_slug(str(body.business or "")),
                app_user_id=resolved["recipient_app_user_id"],
                action=_POSTMARK_SEND_AUDIENCE,
                max_cost_microusd=int(body.estimate_microusd),
            ),
            signing_key=signing_key,
            audience=_POSTMARK_SEND_AUDIENCE,
            nonce=str(uuid.uuid4()),
            issued_at=now,
            ttl_seconds=_CAP_TTL_SECONDS,
        )
        provider_payload = {
            "to_email": resolved["recipient_email"],
            "subject": subject,
            "text_body": text_body,
            "html_body": payload.get("html_body"),
            "message_stream": payload.get("message_stream"),
        }
        try:
            return safebox_broker.handle_provider_request(
                token=token,
                signing_key=signing_key,
                audience=_POSTMARK_SEND_AUDIENCE,
                now=now,
                nonce_store=_PgNonceStore(),
                ledger=_UsageLedgerAdapter(provider="postmark", purpose="email_send", route="email"),
                key_resolver=_postmark_key_resolver,
                provider_caller=_postmark_provider_caller(provider_payload),
                estimate_microusd=int(body.estimate_microusd),
                estimate_fn=_postmark_estimate(provider_payload),
            )
        except CapabilityError as exc:
            raise HTTPException(status_code=401, detail=f"capability_invalid: {exc}") from exc
        except safebox_broker.BrokerError as exc:
            if str(exc).endswith("_unconfigured"):
                raise HTTPException(status_code=503, detail=str(exc)) from exc
            raise HTTPException(status_code=402, detail=str(exc)) from exc
        except (app_usage.AppBudgetInactive, app_usage.AppBudgetExceeded, app_usage.AppUserBudgetExceeded) as exc:
            raise HTTPException(
                status_code=402, detail={"error": type(exc).__name__, "detail": str(exc)}
            ) from exc
        except app_usage.AppUserNotFound as exc:
            raise HTTPException(status_code=400, detail="unknown_app_user") from exc
        except BrokerLedgerError as exc:
            message = str(exc)
            if message.endswith("_unconfigured") or message.endswith("_pricing_unavailable"):
                raise HTTPException(status_code=503, detail=message) from exc
            raise HTTPException(status_code=502, detail="provider_error") from exc
        except Exception as exc:
            message = str(exc)
            if message.endswith("_unconfigured"):
                raise HTTPException(status_code=503, detail=message) from exc
            raise HTTPException(status_code=502, detail="provider_error") from exc

    # ── Creative-credit gate: AUTHORITATIVE reserve/commit/release (operator-owned) ───────────────
    # These three routes are the ONE money gate for the fixed-price creative actions (logo / UGC /
    # static ad). The operator (boundary-1 ownership) reserves the action's canonical fixed credit
    # price on the business's creative-credit ledger ON THE SAFEBOX; reserve hands back a creative
    # capability the client presents to the gated provider routes. No client may reserve/commit credits
    # itself, and the provider routes never re-charge — so there is exactly one authoritative gate per
    # action and no double-charge.

    @app.post("/v1/creative/reserve")
    def creative_reserve(
        request: Request,
        body: _CreativeReserveBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
            if not _operator_creative_gate_disabled():
                raise HTTPException(
                    status_code=402,
                    detail={
                        "error": str(exc),
                        "requested_credits": exc.requested_credits,
                        "available_credits": exc.available_credits,
                    },
                ) from exc
            # Operator god-mode (TAKYON_OPERATOR_CREATIVE_GATE_DISABLED on the safebox host):
            # never REFUSE an operator-plane creative action for insufficient credits. Grant exactly
            # the shortfall as a ledgered, bypass-tagged top-up and retry the reserve once — the
            # reservation/settle flow still runs and still records real cost metadata, so nothing is
            # unmetered; only the refusal disappears. This route is unreachable from the subuser/app
            # plane (_require_internal_token + _require_operator_client), so customers stay gated.
            shortfall = max(1, int(exc.requested_credits or 0) - int(exc.available_credits or 0))
            with _safebox_db_conn() as conn:
                safebox._local_grant_credits(
                    conn,
                    scope.business_slug,
                    shortfall,
                    f"operator-creative-gate-bypass:{reservation_key}",
                    metadata={
                        "reason": "operator_creative_gate_disabled",
                        "action": action,
                        "audience": audience,
                        "shortfall_credits": shortfall,
                    },
                )
            reservation = ledger.reserve(
                scope,
                reservation_key=reservation_key,
                units=units,
                metadata=body.metadata,
            )

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
        request: Request,
        body: _CreativeFinalizeBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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
        request: Request,
        body: _CreativeFinalizeBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        _require_operator_client(request)
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

    @app.post("/v1/providers/gemini/site-image")
    def provider_gemini_site_image(
        body: _CreativeProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _creative_provider_route(
            body,
            allowed_audiences=frozenset({_CREATIVE_SITE_IMAGE_AUDIENCE}),
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

    @app.post("/v1/providers/fal/kling-image-to-video")
    def provider_fal_kling_image_to_video(
        body: _CreativeProviderCallBody,
        authorization: str | None = Header(default=None),
    ) -> dict[str, Any]:
        _require_internal_token(authorization)
        return _creative_provider_route(
            body,
            allowed_audiences=_CREATIVE_FAL_AUDIENCES,
            caller_builder=_creative_fal_kling_image_to_video_caller,
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
