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
_DEFAULT_LIST_PAGE_SIZE = 100


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
    # True when access could NOT be authoritatively read (the entitlement-access endpoint 404'd /
    # gave no definitive answer AND there was no active subscription to confirm from). A degraded
    # snapshot must NEVER retire a customer's local access — it is the fail-OPEN-grace signal that
    # distinguishes "OpenMeter says no access" (authoritative, may retire) from "OpenMeter could
    # not tell us" (unreachable/404, preserve last-known-good).
    degraded: bool = False


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
    existing = _customer_by_key(key)
    if existing is None:
        created = _request_json("POST", "/openmeter/customers", payload=payload, expected_status={201})
        return _payload_entity(created)
    customer_id = str(existing.get("id") or "").strip()
    if not customer_id:
        raise OpenMeterAPIError(f"OpenMeter customer lookup for key={key} returned no id")
    updated = _request_json(
        "PUT",
        f"/openmeter/customers/{urllib.parse.quote(customer_id, safe='')}",
        payload=payload,
    )
    entity = _payload_entity(updated)
    return entity if entity else existing


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
        "/openmeter/events",
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
    current = _plan_by_key(vendor_plan_key)
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
        draft = _request_json("POST", "/openmeter/plans", payload=body, expected_status={201})
    else:
        current_id = str(current.get("id") or "").strip()
        if not current_id:
            raise OpenMeterAPIError(f"OpenMeter plan lookup for key={vendor_plan_key} returned no id")
        current_status = str(current.get("status") or "").strip().lower()
        if current_status == "draft":
            draft = _request_json(
                "PUT",
                f"/openmeter/plans/{urllib.parse.quote(current_id, safe='')}",
                payload=_plan_update_body(body),
            )
        else:
            draft = _request_json("POST", "/openmeter/plans", payload=body, expected_status={201})
    draft_entity = _payload_entity(draft)
    published = _request_json(
        "POST",
        f"/openmeter/plans/{urllib.parse.quote(str(draft_entity.get('id') or ''), safe='')}/publish",
    )
    published_entity = _payload_entity(published)
    return _plan_snapshot(published_entity if published_entity else draft_entity)


def current_subscription(
    *,
    business_slug: str,
    app_user_id: str,
) -> dict[str, Any] | None:
    _require_enabled()
    key = customer_key_for(business_slug, app_user_id)
    customer = _customer_by_key(key)
    if customer is None:
        return None
    customer_id = str(customer.get("id") or "").strip()
    if not customer_id:
        return None
    # NOTE: do not send filter[status] as a repeated query param. The
    # Kong/OpenMeter gateway rejects multi-value filter[status]
    # (urlencode(..., doseq=True) emits filter[status]=active&filter[status]=scheduled)
    # with a 400 "repeated query parameter not allowed". Fetch the customer's
    # subscriptions unfiltered and narrow to _SUBSCRIPTION_STATUSES in Python below.
    payload = _request_json(
        "GET",
        "/openmeter/subscriptions",
        query={
            "page[size]": _DEFAULT_LIST_PAGE_SIZE,
            "page[number]": 1,
        },
        allow_status={404},
    )
    items = _list_payload_items(payload)
    active = [
        item
        for item in items
        if isinstance(item, dict)
        and _subscription_matches_customer(item, customer_id=customer_id, customer_key=key)
        and str(item.get("status") or "").strip().lower() in _SUBSCRIPTION_STATUSES
    ]
    if not active:
        return None
    active.sort(
        key=lambda item: str(
            item.get("active_from")
            or item.get("activeFrom")
            or item.get("updated_at")
            or item.get("updatedAt")
            or item.get("created_at")
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
    customer = _customer_by_key(key)
    if customer is None:
        return None
    customer_id = str(customer.get("id") or "").strip()
    if not customer_id:
        return None
    payload = _request_json(
        "GET",
        f"/openmeter/customers/{urllib.parse.quote(customer_id, safe='')}/billing",
        allow_status={404},
    )
    entity = _payload_entity(payload)
    return entity if entity else None


def upsert_customer_stripe_data(
    *,
    business_slug: str,
    app_user_id: str,
    stripe_customer_id: str,
    stripe_default_payment_method_id: str | None = None,
) -> dict[str, Any]:
    _require_enabled()
    key = customer_key_for(business_slug, app_user_id)
    customer = _customer_by_key(key)
    if customer is None:
        raise OpenMeterAPIError(f"OpenMeter customer not found for key={key}")
    customer_id = str(customer.get("id") or "").strip()
    if not customer_id:
        raise OpenMeterAPIError(f"OpenMeter customer lookup for key={key} returned no id")
    # PUT /customers/{id}/billing expects the Stripe binding under app_data.stripe with snake_case
    # `customer_id` (verified against the live Kong-fronted OpenMeter: the older
    # {type, stripe_customer_id} shape 400s with "app_data.stripe required"; this shape advances
    # to the real Stripe-customer validation). OpenMeter validates the customer exists in the
    # connected Stripe account, so the id must be a real Stripe customer (from the product checkout).
    stripe_data: dict[str, Any] = {"customer_id": str(stripe_customer_id or "").strip()}
    if stripe_default_payment_method_id:
        stripe_data["default_payment_method_id"] = str(stripe_default_payment_method_id).strip()
    payload = {"app_data": {"stripe": stripe_data}}
    updated = _request_json(
        "PUT",
        f"/openmeter/customers/{urllib.parse.quote(customer_id, safe='')}/billing",
        payload=payload,
    )
    return _payload_entity(updated)


def ensure_subscription(
    *,
    business_slug: str,
    app_user_id: str,
    plan: OpenMeterPlanSnapshot,
    stripe_subscription_id: str | None = None,
) -> dict[str, Any]:
    _require_enabled()
    customer_key = customer_key_for(business_slug, app_user_id)
    current = current_subscription(business_slug=business_slug, app_user_id=app_user_id)
    if current is not None:
        current_plan = current.get("plan") if isinstance(current.get("plan"), dict) else {}
        current_plan_key = str(current_plan.get("key") or "").strip()
        if current_plan_key == plan.key:
            # Same plan already active: idempotent no-op. The plan key/version is the ONLY churn
            # driver — carrying stripe_subscription_id in metadata never makes a same-plan sub look
            # "different", so a webhook/subscription.updated storm can't cause cancel/recreate.
            return current
        cancel_subscription(
            business_slug=business_slug,
            app_user_id=app_user_id,
            timing="immediate",
        )
    metadata = {"takyon_customer_key": customer_key}
    if stripe_subscription_id:
        metadata["takyon_stripe_subscription_id"] = str(stripe_subscription_id).strip()
    create = {
        "customer": {"key": customer_key},
        "plan": {"key": plan.key, "version": int(plan.version)},
        "timing": "immediate",
        "name": f"{business_slug} {plan.key}",
        "metadata": metadata,
    }
    created = _request_json(
        "POST",
        "/openmeter/subscriptions",
        payload=create,
        expected_status={201},
    )
    return _payload_entity(created)


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
        f"/openmeter/subscriptions/{urllib.parse.quote(subscription_id, safe='')}/cancel",
        payload={"timing": str(timing or "immediate")},
    )
    entity = _payload_entity(cancelled)
    return entity if entity else current


def project_customer_access(
    *,
    business_slug: str,
    app_user_id: str,
) -> OpenMeterAccessSnapshot:
    _require_enabled()
    customer_key = customer_key_for(business_slug, app_user_id)
    customer = _customer_by_key(customer_key)
    customer_id = str((customer or {}).get("id") or "").strip()
    feature_key = access_feature_key_for(business_slug)
    raw_access = (
        _request_json(
            "GET",
            f"/openmeter/customers/{urllib.parse.quote(customer_id, safe='')}/entitlement-access",
            allow_status={404},
        )
        if customer_id
        else None
    )
    current = current_subscription(business_slug=business_slug, app_user_id=app_user_id)
    entitlement = _entitlement_payload(raw_access, feature_key)
    # Fail-OPEN-grace inputs. An active OpenMeter subscription confers access even if the
    # entitlement-access read came back empty. `raw_access is None` means the entitlement-access
    # endpoint 404'd (urllib soft-miss via allow_status), which is NOT an authoritative negative.
    subscription_active = bool(
        isinstance(current, dict)
        and str(current.get("status") or "").strip().lower() in _SUBSCRIPTION_STATUSES
    )
    has_access = _entitlement_has_access(entitlement) or subscription_active
    # A read is AUTHORITATIVE only when it carries an EXPLICIT access decision (the resolved
    # entitlement payload actually contains has_access/hasAccess/access) OR an active subscription
    # confirms access. Everything else -- a 404 (raw_access None), a 200 empty/non-dict body ({}),
    # or a 200 envelope that lacks THIS feature's entitlement (the live Kong-misroute case) -- CANNOT
    # prove no-access, so it is degraded and must preserve last-known-good rather than retire.
    # (Keying degraded on `raw_access is None` alone missed every non-404 degraded 200.)
    authoritative = subscription_active or _entitlement_access_is_authoritative(entitlement)
    degraded = not authoritative
    plan_ref = current.get("plan") if isinstance(current, dict) and isinstance(current.get("plan"), dict) else {}
    plan_payload = None
    plan_id = str(plan_ref.get("id") or "").strip()
    if plan_id:
        plan_payload = _request_json(
            "GET",
            f"/openmeter/plans/{urllib.parse.quote(plan_id, safe='')}",
            allow_status={404},
        )
        plan_payload = _payload_entity(plan_payload)
    plan_metadata = (
        plan_payload.get("metadata")
        if isinstance(plan_payload, dict) and isinstance(plan_payload.get("metadata"), dict)
        else {}
    )
    return OpenMeterAccessSnapshot(
        customer_key=customer_key,
        feature_key=feature_key,
        has_access=has_access,
        degraded=degraded,
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
            (current or {}).get("active_to")
            or (current or {}).get("activeTo")
            or (current or {}).get("current_period_end")
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
            "openmeter_has_access": "true" if _entitlement_has_access(entitlement) else "false",
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
        _request_json("POST", "/openmeter/features", payload=payload, expected_status={201})
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
    # OpenMeter is a downstream ACCESS/USAGE MIRROR, never a second charger. The product's own Stripe
    # Checkout is the sole money rail; if the OpenMeter rate card carried the real price, OpenMeter's
    # Stripe billing app would issue a SECOND recurring invoice to the same customer (double-billing).
    # So the rate card is $0 — it confers the boolean access entitlement only. The real Takyon price
    # is still recorded in the plan metadata (takyon_price_cents) for reference.
    amount = 0
    name = str(getattr(policy, "plan_key", "plan") or "plan").replace("-", " ").strip().title()
    body = {
        "key": plan_key_for(str(getattr(policy, "business_slug", "") or ""), str(getattr(policy, "plan_key", "") or "")),
        "name": name or "Plan",
        "description": str(getattr(policy, "notes", "") or "") or None,
        "currency": str(getattr(policy, "currency", "usd") or "usd").upper(),
        "billing_cadence": cadence,
        "metadata": metadata,
        "phases": [
            {
                "key": "default",
                "name": "Default",
                "rate_cards": [
                    {
                        "name": f"{name or 'Plan'} Access",
                        "description": str(getattr(policy, "notes", "") or "")
                        or "Takyon paid app access",
                        "key": feature_key,
                        "feature_key": feature_key,
                        "entitlement_template": {"type": "boolean"},
                        "price": {
                            "amount": str(amount),
                            "type": "flat",
                            "payment_term": "in_advance",
                        },
                        "billing_cadence": cadence,
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
        "billing_cadence": body.get("billing_cadence"),
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


def _payload_entity(payload: dict[str, Any] | list[Any] | None) -> dict[str, Any]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, dict):
            return data
        return payload
    return {}


def _list_payload_items(payload: dict[str, Any] | list[Any] | None) -> list[dict[str, Any]]:
    if isinstance(payload, dict):
        data = payload.get("data")
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        items = payload.get("items")
        if isinstance(items, list):
            return [item for item in items if isinstance(item, dict)]
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def _customer_by_key(key: str) -> dict[str, Any] | None:
    payload = _request_json(
        "GET",
        "/openmeter/customers",
        query={"filter[key]": key},
    )
    for item in _list_payload_items(payload):
        if str(item.get("key") or "").strip() == key:
            return item
    return None


def _plan_by_key(key: str) -> dict[str, Any] | None:
    payload = _request_json(
        "GET",
        "/openmeter/plans",
        query={"filter[key]": key},
    )
    for item in _list_payload_items(payload):
        if str(item.get("key") or "").strip() == key:
            return item
    return None


def _subscription_matches_customer(
    payload: dict[str, Any],
    *,
    customer_id: str,
    customer_key: str,
) -> bool:
    customer = payload.get("customer") if isinstance(payload.get("customer"), dict) else {}
    ids = {
        str(customer.get("id") or "").strip(),
        str(payload.get("customer_id") or "").strip(),
        str(payload.get("customerId") or "").strip(),
    }
    keys = {
        str(customer.get("key") or "").strip(),
        str(payload.get("customer_key") or "").strip(),
        str(payload.get("customerKey") or "").strip(),
    }
    return (customer_id and customer_id in ids) or (customer_key and customer_key in keys)


def _entitlement_has_access(payload: dict[str, Any]) -> bool:
    if not isinstance(payload, dict):
        return False
    for name in ("has_access", "hasAccess", "access"):
        value = payload.get(name)
        if value is not None:
            return bool(value)
    return False


def _entitlement_access_is_authoritative(payload: dict[str, Any]) -> bool:
    """True iff the entitlement payload carries an EXPLICIT access decision (one of
    has_access/hasAccess/access is present, regardless of value). An empty / feature-missing payload
    cannot prove no-access, so it is NOT authoritative — the fail-open grace must preserve on it."""
    if not isinstance(payload, dict):
        return False
    return any(payload.get(name) is not None for name in ("has_access", "hasAccess", "access"))


def _entitlement_payload(raw_access: dict[str, Any] | list[Any] | None, feature_key: str) -> dict[str, Any]:
    if isinstance(raw_access, dict):
        entitlements = raw_access.get("entitlements")
        if isinstance(entitlements, dict):
            direct = entitlements.get(feature_key)
            if isinstance(direct, dict):
                return direct
        for key in ("data", "items", "features", "entitlement_access"):
            collection = raw_access.get(key)
            if isinstance(collection, dict):
                direct = collection.get(feature_key)
                if isinstance(direct, dict):
                    return direct
            if isinstance(collection, list):
                for item in collection:
                    if isinstance(item, dict) and _payload_feature_key(item) == feature_key:
                        return item
        if _payload_feature_key(raw_access) == feature_key:
            return raw_access
    elif isinstance(raw_access, list):
        for item in raw_access:
            if isinstance(item, dict) and _payload_feature_key(item) == feature_key:
                return item
    return {}


def _payload_feature_key(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    feature = payload.get("feature") if isinstance(payload.get("feature"), dict) else {}
    return str(
        payload.get("feature_key")
        or payload.get("featureKey")
        or feature.get("key")
        or payload.get("key")
        or ""
    ).strip()


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
