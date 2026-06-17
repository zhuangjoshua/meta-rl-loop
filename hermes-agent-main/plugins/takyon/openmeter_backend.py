"""Thin OpenMeter adapter for Takyon's product-app billing mirror.

Takyon keeps the runtime authority boundary (session validation, `unentitled`, AI gateway
blocking) and uses this module only for the billing/entitlement plane behind it. The adapter is
deliberately narrow:

* one boolean OpenMeter feature per business for "has paid access"
* one OpenMeter customer per Takyon app user
* one OpenMeter plan per Takyon local plan_key, versioned by OpenMeter
* customer access is projected BACK into local `app_entitlements` by the caller

This first cut mirrors recurring access plans only. One-time app plans stay on the local Takyon
rail until we move that product shape onto a vendor-backed contract model too.
"""

from __future__ import annotations

import hashlib
import json
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from typing import Any

from . import safebox

_OPENMETER_URL_KEYS = (
    "TAKYON_OPENMETER_URL",
    "OPENMETER_URL",
    "OPENMETER_API_URL",
)
_OPENMETER_TOKEN_KEYS = (
    "OPENMETER_API_TOKEN",
    "TAKYON_OPENMETER_API_TOKEN",
)
_SUBSCRIPTION_STATUSES = ("active", "scheduled")


class OpenMeterError(Exception):
    """Base error for OpenMeter mirror failures."""


class OpenMeterConfigurationError(OpenMeterError):
    """OpenMeter is disabled or cannot mirror the requested Takyon shape."""


class OpenMeterAPIError(OpenMeterError):
    """OpenMeter returned a concrete HTTP/API failure."""


@dataclass(frozen=True)
class OpenMeterPlanSnapshot:
    id: str
    key: str
    version: int
    status: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OpenMeterAccessSnapshot:
    customer_key: str
    feature_key: str
    has_access: bool
    tier: str | None
    takyon_plan_key: str | None
    openmeter_plan_key: str | None
    plan_version: int | None
    subscription_id: str | None
    current_period_end: object
    metadata: dict[str, Any]
    raw_access: dict[str, Any]
    raw_subscription: dict[str, Any] | None


def enabled() -> bool:
    return bool(_base_url())


def customer_key_for(business_slug: str, app_user_id: str) -> str:
    return _key("tk", business_slug, "customer", app_user_id, max_len=128)


def access_feature_key_for(business_slug: str) -> str:
    return _key("tk", business_slug, "app", "access", max_len=64)


def plan_key_for(business_slug: str, plan_key: str) -> str:
    return _key("tk", business_slug, "plan", plan_key, max_len=64)


def billing_cadence_for(interval: str) -> str:
    raw = str(interval or "").strip().lower()
    if raw == "month":
        return "P1M"
    if raw == "year":
        return "P1Y"
    raise OpenMeterConfigurationError(
        "OpenMeter mirror currently supports recurring app plans only"
    )


def sync_customer(
    *,
    business_slug: str,
    app_user_id: str,
    email: str,
    name: str | None,
) -> dict[str, Any]:
    _require_enabled()
    key = customer_key_for(business_slug, app_user_id)
    subject_key = key
    payload = {
        "key": key,
        "name": str(name or email or key).strip() or key,
        "primaryEmail": str(email or "").strip() or None,
        "usageAttribution": {"subjectKeys": [subject_key]},
        "metadata": {
            "takyon_business_slug": str(business_slug or ""),
            "takyon_app_user_id": str(app_user_id or ""),
            "takyon_email": str(email or ""),
            "takyon_subject_key": subject_key,
        },
    }
    existing = _request_json(
        "GET",
        f"/api/v1/customers/{urllib.parse.quote(key, safe='')}",
        allow_status={404},
    )
    if existing is None:
        created = _request_json("POST", "/api/v1/customers", payload=payload, expected_status={201})
        return created if isinstance(created, dict) else {}
    updated = _request_json(
        "PUT",
        f"/api/v1/customers/{urllib.parse.quote(key, safe='')}",
        payload=payload,
    )
    return updated if isinstance(updated, dict) else {}


_USAGE_METER_SLUG = "tk_ai_cost_microusd"
_USAGE_EVENT_TYPE = "tk_ai_usage"


def usage_event_subject_for(business_slug: str) -> str:
    """OpenMeter subject key for a business's exact-cost meter (one stream per business)."""
    return _key("tk", business_slug, "usage", max_len=128)


def ingest_usage_event(
    *,
    business_slug: str,
    reservation_key: str,
    actual_cost_microusd: int,
    route: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    app_user_id: str | None = None,
    occurred_at: str | None = None,
) -> bool:
    """Mirror ONE settled usage event into OpenMeter for exact-cost aggregation (GOAL_RULES
    §4: BUY the cost-aggregation/time-bucketing engine, do NOT hand-roll a time-series store).

    `app_usage.py` stays the authoritative event source and ledger; this is a fire-and-forget
    mirror that is idempotent on the reservation_key (OpenMeter dedupes on the CloudEvent id),
    so a replayed settle never double-counts. Returns True if the event was accepted, False if
    OpenMeter is disabled (the caller treats this as a no-op and never lets it affect the
    ledger). Cost is carried in micro-USD as the meter value so OpenMeter can sum exact cost
    per business per time window without us building a custom cost store.
    """
    if not enabled():
        return False
    subject = usage_event_subject_for(business_slug)
    event_id = _key("ev", business_slug, reservation_key, max_len=200)
    event: dict[str, Any] = {
        "id": event_id,
        "source": "takyon-app-usage",
        "type": _USAGE_EVENT_TYPE,
        "subject": subject,
        "data": {
            "value": str(max(0, int(actual_cost_microusd or 0))),
            "business_slug": str(business_slug or ""),
            "reservation_key": str(reservation_key or ""),
            "route": str(route or ""),
            "provider": str(provider or ""),
            "model": str(model or ""),
            "app_user_id": str(app_user_id or ""),
        },
    }
    if occurred_at:
        event["time"] = str(occurred_at)
    # CloudEvents batch ingest; OpenMeter accepts a single event object too.
    _request_json(
        "POST",
        "/api/v1/events",
        payload=event,
        expected_status={200, 201, 204},
    )
    return True


def sync_access_plan(policy: Any) -> OpenMeterPlanSnapshot:
    _require_enabled()
    cadence = billing_cadence_for(getattr(policy, "billing_interval", ""))
    business_slug = str(getattr(policy, "business_slug", "") or "").strip()
    local_plan_key = str(getattr(policy, "plan_key", "") or "").strip()
    feature_key = access_feature_key_for(business_slug)
    vendor_plan_key = plan_key_for(business_slug, local_plan_key)
    _ensure_feature(feature_key, f"{business_slug} app access")
    desired_metadata = _plan_metadata(policy, feature_key)
    current = _request_json(
        "GET",
        f"/api/v1/plans/{urllib.parse.quote(vendor_plan_key, safe='')}",
        allow_status={404},
    )
    if isinstance(current, dict):
        current_meta = current.get("metadata") if isinstance(current.get("metadata"), dict) else {}
        if (
            str(current.get("status") or "").strip().lower() == "active"
            and str(current_meta.get("takyon_fingerprint") or "")
            == str(desired_metadata.get("takyon_fingerprint") or "")
        ):
            return _plan_snapshot(current)
    body = _plan_create_body(policy, feature_key, cadence, desired_metadata)
    if current is None:
        draft = _request_json("POST", "/api/v1/plans", payload=body, expected_status={201})
    else:
        current_status = str(current.get("status") or "").strip().lower()
        if current_status == "draft":
            draft = _request_json(
                "PUT",
                f"/api/v1/plans/{urllib.parse.quote(str(current.get('id') or ''), safe='')}",
                payload=_plan_update_body(body),
            )
        else:
            next_draft = _request_json(
                "POST",
                f"/api/v1/plans/{urllib.parse.quote(str(current.get('id') or current.get('key') or ''), safe='')}/next",
                expected_status={201},
            )
            draft = _request_json(
                "PUT",
                f"/api/v1/plans/{urllib.parse.quote(str((next_draft or {}).get('id') or ''), safe='')}",
                payload=_plan_update_body(body),
            )
    published = _request_json(
        "POST",
        f"/api/v1/plans/{urllib.parse.quote(str((draft or {}).get('id') or ''), safe='')}/publish",
    )
    return _plan_snapshot(published if isinstance(published, dict) else draft)


def current_subscription(
    *,
    business_slug: str,
    app_user_id: str,
) -> dict[str, Any] | None:
    _require_enabled()
    key = customer_key_for(business_slug, app_user_id)
    payload = _request_json(
        "GET",
        f"/api/v1/customers/{urllib.parse.quote(key, safe='')}/subscriptions",
        query={"status": list(_SUBSCRIPTION_STATUSES), "pageSize": 10, "page": 1},
        allow_status={404},
    )
    if not isinstance(payload, dict):
        return None
    items = payload.get("items")
    if not isinstance(items, list):
        return None
    active = [
        item
        for item in items
        if isinstance(item, dict)
        and str(item.get("status") or "").strip().lower() in _SUBSCRIPTION_STATUSES
    ]
    if not active:
        return None
    active.sort(
        key=lambda item: str(
            item.get("activeFrom")
            or item.get("updatedAt")
            or item.get("createdAt")
            or ""
        ),
        reverse=True,
    )
    return active[0]


def customer_stripe_data(
    *,
    business_slug: str,
    app_user_id: str,
) -> dict[str, Any] | None:
    _require_enabled()
    key = customer_key_for(business_slug, app_user_id)
    payload = _request_json(
        "GET",
        f"/api/v1/customers/{urllib.parse.quote(key, safe='')}/stripe",
        allow_status={404},
    )
    return payload if isinstance(payload, dict) else None


def upsert_customer_stripe_data(
    *,
    business_slug: str,
    app_user_id: str,
    stripe_customer_id: str,
    stripe_default_payment_method_id: str | None = None,
) -> dict[str, Any]:
    _require_enabled()
    key = customer_key_for(business_slug, app_user_id)
    payload = {
        "type": "stripe",
        "stripeCustomerId": str(stripe_customer_id or "").strip(),
    }
    if stripe_default_payment_method_id:
        payload["stripeDefaultPaymentMethodId"] = str(
            stripe_default_payment_method_id
        ).strip()
    updated = _request_json(
        "PUT",
        f"/api/v1/customers/{urllib.parse.quote(key, safe='')}/stripe",
        payload=payload,
    )
    return updated if isinstance(updated, dict) else {}


def ensure_subscription(
    *,
    business_slug: str,
    app_user_id: str,
    plan: OpenMeterPlanSnapshot,
) -> dict[str, Any]:
    _require_enabled()
    customer_key = customer_key_for(business_slug, app_user_id)
    current = current_subscription(business_slug=business_slug, app_user_id=app_user_id)
    if current is not None:
        current_plan = current.get("plan") if isinstance(current.get("plan"), dict) else {}
        current_plan_key = str(current_plan.get("key") or "").strip()
        if current_plan_key == plan.key:
            return current
        cancel_subscription(
            business_slug=business_slug,
            app_user_id=app_user_id,
            timing="immediate",
        )
    create = {
        "customerKey": customer_key,
        "plan": {"key": plan.key, "version": int(plan.version)},
        "timing": "immediate",
        "name": f"{business_slug} {plan.key}",
        "metadata": {"takyon_customer_key": customer_key},
    }
    created = _request_json(
        "POST",
        "/api/v1/subscriptions",
        payload=create,
        expected_status={201},
    )
    return created if isinstance(created, dict) else {}


def cancel_subscription(
    *,
    business_slug: str,
    app_user_id: str,
    timing: str = "immediate",
) -> dict[str, Any] | None:
    _require_enabled()
    current = current_subscription(business_slug=business_slug, app_user_id=app_user_id)
    if current is None:
        return None
    subscription_id = str(current.get("id") or "").strip()
    if not subscription_id:
        return current
    cancelled = _request_json(
        "POST",
        f"/api/v1/subscriptions/{urllib.parse.quote(subscription_id, safe='')}/cancel",
        payload={"timing": str(timing or "immediate")},
    )
    return cancelled if isinstance(cancelled, dict) else current


def project_customer_access(
    *,
    business_slug: str,
    app_user_id: str,
) -> OpenMeterAccessSnapshot:
    _require_enabled()
    customer_key = customer_key_for(business_slug, app_user_id)
    feature_key = access_feature_key_for(business_slug)
    raw_access = _request_json(
        "GET",
        f"/api/v1/customers/{urllib.parse.quote(customer_key, safe='')}/access",
        allow_status={404},
    )
    current = current_subscription(business_slug=business_slug, app_user_id=app_user_id)
    entitlement = {}
    if isinstance(raw_access, dict):
        entitlements = raw_access.get("entitlements")
        if isinstance(entitlements, dict):
            maybe = entitlements.get(feature_key)
            entitlement = maybe if isinstance(maybe, dict) else {}
    plan_ref = current.get("plan") if isinstance(current, dict) and isinstance(current.get("plan"), dict) else {}
    plan_payload = None
    plan_id = str(plan_ref.get("id") or "").strip()
    if plan_id:
        plan_payload = _request_json(
            "GET",
            f"/api/v1/plans/{urllib.parse.quote(plan_id, safe='')}",
            allow_status={404},
        )
    plan_metadata = (
        plan_payload.get("metadata")
        if isinstance(plan_payload, dict) and isinstance(plan_payload.get("metadata"), dict)
        else {}
    )
    return OpenMeterAccessSnapshot(
        customer_key=customer_key,
        feature_key=feature_key,
        has_access=bool(entitlement.get("hasAccess")),
        tier=(
            str(plan_metadata.get("takyon_tier") or "").strip() or None
        ),
        takyon_plan_key=(
            str(plan_metadata.get("takyon_plan_key") or "").strip() or None
        ),
        openmeter_plan_key=(
            str(plan_ref.get("key") or "").strip()
            or (str(plan_payload.get("key") or "").strip() if isinstance(plan_payload, dict) else "")
            or None
        ),
        plan_version=_maybe_int(
            plan_ref.get("version")
            if isinstance(plan_ref, dict)
            else (plan_payload or {}).get("version")
        ),
        subscription_id=str(current.get("id") or "").strip() or None if isinstance(current, dict) else None,
        current_period_end=(
            (current or {}).get("activeTo")
            or (current or {}).get("currentPeriodEnd")
            or (current or {}).get("endsAt")
        )
        if isinstance(current, dict)
        else None,
        metadata={
            "authority": "openmeter",
            "openmeter_customer_key": customer_key,
            "openmeter_feature_key": feature_key,
            "openmeter_subscription_id": str((current or {}).get("id") or "") if isinstance(current, dict) else "",
            "openmeter_plan_id": str(plan_id or ""),
            "openmeter_plan_key": str(plan_ref.get("key") or "") if isinstance(plan_ref, dict) else "",
            "openmeter_plan_version": str(
                plan_ref.get("version") if isinstance(plan_ref, dict) and plan_ref.get("version") is not None else ""
            ),
            "openmeter_has_access": "true" if bool(entitlement.get("hasAccess")) else "false",
        },
        raw_access=raw_access if isinstance(raw_access, dict) else {},
        raw_subscription=current if isinstance(current, dict) else None,
    )


def _base_url() -> str:
    return (
        str(safebox.first_env_backed_value(*_OPENMETER_URL_KEYS) or "")
        .strip()
        .rstrip("/")
    )


def _api_token() -> str:
    return str(safebox.first_env_backed_value(*_OPENMETER_TOKEN_KEYS) or "").strip()


def _require_enabled() -> None:
    if enabled():
        return
    raise OpenMeterConfigurationError(
        "OpenMeter mirror is disabled; configure TAKYON_OPENMETER_URL"
    )


def _request_json(
    method: str,
    path: str,
    *,
    payload: dict[str, Any] | None = None,
    query: dict[str, Any] | None = None,
    allow_status: set[int] | None = None,
    expected_status: set[int] | None = None,
) -> dict[str, Any] | list[Any] | None:
    allow = set(allow_status or set())
    expected = set(expected_status or {200})
    encoded_query = urllib.parse.urlencode(query or {}, doseq=True)
    url = f"{_base_url()}{path}"
    if encoded_query:
        url = f"{url}?{encoded_query}"
    data = None
    headers = {"Accept": "application/json"}
    token = _api_token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            status = int(getattr(response, "status", 200) or 200)
            raw = response.read().decode("utf-8", errors="replace")
        if status not in expected and status not in allow:
            raise OpenMeterAPIError(f"OpenMeter {method.upper()} {path} returned {status}: {raw}")
        if not raw.strip():
            return {}
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, (dict, list)) else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        if exc.code in allow:
            return None
        raise OpenMeterAPIError(
            f"OpenMeter {method.upper()} {path} failed: {exc.code} {body}"
        ) from exc


def _ensure_feature(feature_key: str, name: str) -> None:
    payload = {"key": feature_key, "name": name}
    try:
        _request_json("POST", "/api/v1/features", payload=payload, expected_status={201})
    except OpenMeterAPIError as exc:
        if "409" in str(exc) or "already exists" in str(exc).lower():
            return
        raise


def _plan_create_body(
    policy: Any,
    feature_key: str,
    cadence: str,
    metadata: dict[str, str],
) -> dict[str, Any]:
    amount = max(0, int(getattr(policy, "price_cents", 0) or 0))
    name = str(getattr(policy, "plan_key", "plan") or "plan").replace("-", " ").strip().title()
    body = {
        "key": plan_key_for(str(getattr(policy, "business_slug", "") or ""), str(getattr(policy, "plan_key", "") or "")),
        "name": name or "Plan",
        "description": str(getattr(policy, "notes", "") or "") or None,
        "currency": str(getattr(policy, "currency", "usd") or "usd").upper(),
        "billingCadence": cadence,
        "metadata": metadata,
        "phases": [
            {
                "key": "default",
                "name": "Default",
                "rateCards": [
                    {
                        "name": f"{name or 'Plan'} Access",
                        "description": str(getattr(policy, "notes", "") or "")
                        or "Takyon paid app access",
                        "key": feature_key,
                        "featureKey": feature_key,
                        "entitlementTemplate": {"type": "boolean"},
                        "price": {
                            "amount": str(amount),
                            "type": "flat",
                            "paymentTerm": "in_advance",
                        },
                        "billingCadence": cadence,
                        "type": "flat",
                    }
                ],
            }
        ],
    }
    return body


def _plan_update_body(body: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": body.get("name"),
        "description": body.get("description"),
        "metadata": body.get("metadata"),
        "billingCadence": body.get("billingCadence"),
        "phases": body.get("phases") or [],
    }


def _plan_metadata(policy: Any, feature_key: str) -> dict[str, str]:
    local_metadata = getattr(policy, "metadata", {}) or {}
    serialised_local = (
        json.dumps(local_metadata, ensure_ascii=False, sort_keys=True)
        if isinstance(local_metadata, dict)
        else json.dumps({"value": local_metadata}, ensure_ascii=False, sort_keys=True)
    )
    fingerprint_source = {
        "business_slug": str(getattr(policy, "business_slug", "") or ""),
        "plan_key": str(getattr(policy, "plan_key", "") or ""),
        "tier": str(getattr(policy, "tier", "") or ""),
        "price_cents": int(getattr(policy, "price_cents", 0) or 0),
        "currency": str(getattr(policy, "currency", "usd") or "usd").lower(),
        "billing_interval": str(getattr(policy, "billing_interval", "") or ""),
        "included_ai_budget_microusd": int(
            getattr(policy, "included_ai_budget_microusd", 0) or 0
        ),
        "included_action_quota": int(getattr(policy, "included_action_quota", 0) or 0),
        "notes": str(getattr(policy, "notes", "") or ""),
        "metadata_json": serialised_local,
        "feature_key": feature_key,
    }
    fingerprint = hashlib.sha1(
        json.dumps(fingerprint_source, sort_keys=True).encode("utf-8")
    ).hexdigest()
    return {
        "takyon_business_slug": str(getattr(policy, "business_slug", "") or ""),
        "takyon_plan_key": str(getattr(policy, "plan_key", "") or ""),
        "takyon_tier": str(getattr(policy, "tier", "") or ""),
        "takyon_price_cents": str(int(getattr(policy, "price_cents", 0) or 0)),
        "takyon_currency": str(getattr(policy, "currency", "usd") or "usd").lower(),
        "takyon_billing_interval": str(getattr(policy, "billing_interval", "") or ""),
        "takyon_included_ai_budget_microusd": str(
            int(getattr(policy, "included_ai_budget_microusd", 0) or 0)
        ),
        "takyon_included_action_quota": str(
            int(getattr(policy, "included_action_quota", 0) or 0)
        ),
        "takyon_feature_key": feature_key,
        "takyon_plan_metadata_json": serialised_local,
        "takyon_fingerprint": fingerprint,
    }


def _plan_snapshot(payload: dict[str, Any] | None) -> OpenMeterPlanSnapshot:
    raw = payload if isinstance(payload, dict) else {}
    metadata = raw.get("metadata") if isinstance(raw.get("metadata"), dict) else {}
    return OpenMeterPlanSnapshot(
        id=str(raw.get("id") or ""),
        key=str(raw.get("key") or ""),
        version=int(raw.get("version") or 0),
        status=str(raw.get("status") or ""),
        metadata=metadata,
    )


def _key(*parts: str, max_len: int) -> str:
    normalized_parts = []
    for part in parts:
        raw = re.sub(r"[^a-z0-9]+", "_", str(part or "").strip().lower()).strip("_")
        if raw:
            normalized_parts.append(raw)
    base = "_".join(normalized_parts) or "takyon"
    if len(base) <= max_len:
        return base
    digest = hashlib.sha1(base.encode("utf-8")).hexdigest()[:10]
    head = base[: max(1, max_len - 11)].rstrip("_")
    return f"{head}_{digest}"


def _maybe_int(value: Any) -> int | None:
    try:
        return int(value) if value not in {None, ""} else None
    except (TypeError, ValueError):
        return None
