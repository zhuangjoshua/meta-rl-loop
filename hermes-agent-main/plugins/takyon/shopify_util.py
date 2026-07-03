"""Shopify UC4 rail — connection, plan-fee cost basis, and the shop/update recompose leaf
(modularization plan §2.7 Stage 5, "Shopify slices"; acceptance matrix §4p UC4).

Scope is EXACTLY the plan's stop line: the fixed monthly per-store platform fee — the one Shopify
money surface that belongs in monthly subscription composition. Per-order/fulfillment/catalog rails
are the archetypes project's money shape and are deliberately NOT here.

Three concerns, all fail-closed:

  * CONNECTION (`connect_shopify`) — attach/initiate the Composio Shopify connected account for a
    business through the EXISTING brokered Composio transport (`composio_distribution._request`,
    which rides `safebox.composio_forward` on runtime planes). Token custody lives in Composio;
    COMPOSIO_API_KEY lives in the safebox; ZERO new runtime credential and no `os.environ` reads
    (mirrors the twitter/reddit connected-account pattern).

  * COST BASIS (`read_shop_plan` / `plan_fee_cost_basis`) — fetch the store's current plan via
    Shopify Admin GraphQL through the same broker and map the plan name to its fixed monthly fee
    via `SHOPIFY_PLAN_MONTHLY_FEE_MICROUSD` — the ONE explicit, documented plan_name→fee table.
    Anything not in the table REFUSES (`ShopifyPlanUnmapped`); a price is never guessed. Output is
    a `plan_composition.CostBasis.fixed(...)` — fetched, never typed.

  * WEBHOOK RECOMPOSE (`record_webhook_and_process`) — the DB-side processor for the verified
    `shop/update` webhook (HMAC verification happens BEFORE this, safebox-side — see
    `safebox.verify_shopify_app_webhook`; the shared secret never reaches the runtime plane).
    Mirrors `app_payments.record_webhook_and_process` exactly: global provider-keyed dedup on the
    existing `webhook_events (provider, provider_event_id)` table (provider='shopify'; NO new
    dedup store), row locked `for update`, whole dispatch in ONE transaction. On a verified plan
    change it re-derives each affected composed plan with the new fee and mints the NEXT plan_key
    version through `app_entitlements.upsert_plan_from_composition` — grandfathering intact by
    construction (the live plan_key row is never mutated).

Security posture for the webhook (the route is PUBLIC-facing; subuser security is priority one):

  * Shopify's HMAC covers ONLY the raw body — request headers (X-Shopify-Webhook-Id, -Topic,
    -Shop-Domain) are attacker-controllable on a replayed body. So the DEDUP KEY is derived from
    the HMAC-covered content itself (sha256 of topic + raw body), never from a header id: a
    captured (body, hmac) pair replayed with fresh header ids is `deduplicated=True` forever —
    replays are one effect BY CONSTRUCTION, including the stale plan-flip replay (old "plan B"
    body replayed after the shop moved back to plan A).
  * The shop identity used for business mapping is `myshopify_domain` FROM THE VERIFIED BODY,
    never the header.
  * Unmapped plan names / unknown shop domains record a processed-with-error receipt and change
    NO pricing state (Shopify retrying cannot fix them, so failing the transaction would only
    thrash); an invalid body fails the request outright.

House style (matches app_payments.py): the processor is a pure leaf taking a psycopg connection,
opens its own `conn.transaction()`, raises typed errors on broken preconditions.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from typing import Any, Mapping

from plugins.takyon import app_entitlements, plan_composition

# ── constants ────────────────────────────────────────────────────────────────────

SHOPIFY_TOOLKIT_SLUG = "shopify"

# Composio v3.1 API paths, all called through the in-repo brokered transport
# (`composio_distribution._request` → safebox `/v1/providers/composio/forward`).
# `connected_accounts` (list) and `tools/execute/proxy` are proven in-repo (the twitter/reddit
# lookup and `reddit_proxy_request` use them today). `auth_configs` (list, for OAuth initiation)
# and the POST `connected_accounts` initiate body are NOT verifiable offline — they are pinned
# here as single constants and fail closed with the raw Composio error if the API disagrees;
# they get validated in the live UC4 acceptance run.
COMPOSIO_CONNECTED_ACCOUNTS_PATH = "connected_accounts"
COMPOSIO_AUTH_CONFIGS_PATH = "auth_configs"  # UNVERIFIED-OFFLINE (validated in live acceptance)
COMPOSIO_PROXY_TOOL_PATH = "tools/execute/proxy"  # proven in-repo (reddit_proxy_request)

# Shopify Admin GraphQL — the ONE query this rail issues, plan-fee scope only. The API version is
# a quarterly pin; bump alongside the live acceptance run.
SHOPIFY_ADMIN_API_VERSION = "2026-01"
SHOPIFY_SHOP_PLAN_GRAPHQL_QUERY = (
    "{ shop { myshopifyDomain plan { displayName partnerDevelopment shopifyPlus } } }"
)

# The composition component this rail owns (plan §2.7: archetypes P3 extends the SAME tool/key).
SHOPIFY_STORE_COMPONENT_KEY = "shopify_store"

# Where the connection lives in business state: businesses.metadata_json[SHOPIFY_CONNECTION_METADATA_KEY].
SHOPIFY_CONNECTION_METADATA_KEY = "takyon_shopify"

# The webhook shared secret (the Shopify app's API secret; HMAC key for X-Shopify-Hmac-Sha256).
# Resolved ONLY on the safebox host (safebox.verify_shopify_app_webhook) — never on a runtime plane.
SHOPIFY_WEBHOOK_SECRET_ALIASES = (
    "TAKYON_SHOPIFY_WEBHOOK_SECRET",
    "SHOPIFY_WEBHOOK_SECRET",
    "SHOPIFY_API_SECRET",
)

SHOPIFY_SHOP_UPDATE_TOPIC = "shop/update"

_WEBHOOK_PROVIDER = "shopify"
_MICRO_PER_USD = 1_000_000

# ── the explicit plan_name → monthly fee table (THE documented map; unknown REFUSES) ──────────
#
# Shopify's Admin surfaces expose the subscription tier as a NAME, not a dollar figure, in two
# spellings: the `shop/update` webhook / REST shop object's `plan_name` (snake_case; "professional"
# = the mid "Shopify" tier, "unlimited" = "Advanced" — legacy internal names) and Admin GraphQL's
# `shop.plan.displayName`. Both spellings normalize through `_normalize_plan_name` and resolve in
# this ONE table. Fees are the standard monthly-billing USD prices, expressed in µUSD/month.
#
#   basic     → $39/mo        shopify/professional → $105/mo       advanced/unlimited → $399/mo
#   partner-development / dev-store plans → the EXPLICIT per-connection configured test fee
#     (partner_dev_fee_microusd on the connection record); with no configured fee they REFUSE —
#     a dev store has no public price and we never guess one.
#   Shopify Plus, trials, paused, staff, affiliate → NOT mapped (custom/none pricing) → REFUSE.
SHOPIFY_PLAN_MONTHLY_FEE_MICROUSD: dict[str, int] = {
    "basic": 39 * _MICRO_PER_USD,
    "shopify": 105 * _MICRO_PER_USD,
    "professional": 105 * _MICRO_PER_USD,
    "advanced": 399 * _MICRO_PER_USD,
    "unlimited": 399 * _MICRO_PER_USD,
}

# Plan identifiers that mean "partner development / dev store" across both spellings.
_PARTNER_DEV_PLAN_NAMES = frozenset(
    {
        "partner_test",
        "partner test account",
        "partner development",
        "partner_development",
        "developer preview",
        "development",
        "dev",
    }
)

_SHOP_DOMAIN_RE = re.compile(r"^[a-z0-9][a-z0-9-]*\.myshopify\.com$")

# Public-facing webhook body cap: shop/update payloads are a few KB; anything huge is abuse.
SHOPIFY_WEBHOOK_MAX_BODY_BYTES = 1_048_576


# ── errors ───────────────────────────────────────────────────────────────────────


class ShopifyError(Exception):
    """Base for the Shopify UC4 rail."""


class ShopifyComposioUnconfigured(ShopifyError):
    """COMPOSIO_API_KEY (or the safebox broker that holds it) is unavailable — fail closed."""


class ShopifyConnectionError(ShopifyError):
    """The Composio Shopify connection could not be resolved/initiated (ambiguous accounts,
    missing auth config, provider rejection)."""


class ShopifyPlanUnmapped(ShopifyError):
    """The store's plan name is not in the explicit fee table — a price is never guessed."""


class ShopifyWebhookInvalidSignature(ShopifyError):
    """The X-Shopify-Hmac-Sha256 header does not verify against the raw body."""


class ShopifyWebhookUnconfigured(ShopifyError):
    """No webhook shared secret is provisioned — verification is impossible; never process."""


class ShopifyWebhookInvalidEvent(ShopifyError):
    """The webhook body is not a usable JSON object."""


# ── HMAC verification (pure; the SECRET is resolved safebox-side by the caller) ────────────────


def verify_webhook_hmac(raw_body: str | bytes, hmac_header: str, secret: str) -> None:
    """Verify Shopify's `X-Shopify-Hmac-Sha256` — base64(HMAC-SHA256(secret, raw_body)) — against
    the exact raw body bytes. Returns None on success; raises `ShopifyWebhookInvalidSignature` on
    any mismatch/malformed header and `ShopifyWebhookUnconfigured` on an empty secret. Stripe's
    `t=/v1=` format is not reusable here (different scheme), hence this dedicated verifier."""
    if not str(secret or "").strip():
        raise ShopifyWebhookUnconfigured("shopify webhook secret is not configured")
    presented = str(hmac_header or "").strip()
    if not presented:
        raise ShopifyWebhookInvalidSignature("missing X-Shopify-Hmac-Sha256 header")
    body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    digest = hmac.new(secret.encode("utf-8"), body_bytes, hashlib.sha256).digest()
    expected = base64.b64encode(digest).decode("ascii")
    if not hmac.compare_digest(expected.encode("ascii"), presented.encode("utf-8", errors="replace")):
        raise ShopifyWebhookInvalidSignature("Shopify webhook HMAC verification failed")


def webhook_dedup_event_id(topic: str, raw_body: str | bytes) -> str:
    """The provider-keyed dedup id for one verified delivery, derived from HMAC-COVERED content
    (topic + body), never from replayable headers (see module docstring, security posture)."""
    body_bytes = raw_body.encode("utf-8") if isinstance(raw_body, str) else bytes(raw_body)
    hasher = hashlib.sha256()
    hasher.update(str(topic or "").strip().lower().encode("utf-8"))
    hasher.update(b"\n")
    hasher.update(body_bytes)
    return f"shopify_evt_{hasher.hexdigest()}"


# ── plan-name → fee (fail-closed) ──────────────────────────────────────────────────


def _normalize_plan_name(raw: Any) -> str:
    return str(raw or "").strip().lower().replace("-", " ").replace("_", " ").strip()


def is_partner_dev_plan(plan_name: Any) -> bool:
    normalized = _normalize_plan_name(plan_name).replace(" ", "_")
    return (
        _normalize_plan_name(plan_name) in {n.replace("_", " ") for n in _PARTNER_DEV_PLAN_NAMES}
        or normalized in _PARTNER_DEV_PLAN_NAMES
    )


def plan_fee_microusd(plan_name: Any, *, partner_dev_fee_microusd: int | None = None) -> int:
    """Resolve a Shopify plan identifier (webhook `plan_name` or GraphQL `plan.displayName`) to
    its fixed monthly fee in µUSD via the explicit table. Partner-development/dev-store plans
    resolve to the EXPLICIT configured test fee recorded on the connection (never a guess); every
    other unknown name raises `ShopifyPlanUnmapped` with the exact name — fail closed."""
    normalized = _normalize_plan_name(plan_name)
    if not normalized:
        raise ShopifyPlanUnmapped("shopify plan name is empty; cannot derive a fee")
    if is_partner_dev_plan(normalized):
        if partner_dev_fee_microusd is None:
            raise ShopifyPlanUnmapped(
                f"shopify plan {str(plan_name)!r} is a partner-development/dev-store plan with no "
                "public price; set an explicit partner_dev_fee_microusd on the connection "
                "(business_connect_shopify) — a fee is never guessed"
            )
        fee = int(partner_dev_fee_microusd)
        if fee < 0:
            raise ShopifyPlanUnmapped("partner_dev_fee_microusd must be non-negative")
        return fee
    fee = SHOPIFY_PLAN_MONTHLY_FEE_MICROUSD.get(normalized.replace(" ", "_")) or (
        SHOPIFY_PLAN_MONTHLY_FEE_MICROUSD.get(normalized)
    )
    if fee is None:
        raise ShopifyPlanUnmapped(
            f"shopify plan {str(plan_name)!r} is not in the explicit plan_name→fee table "
            f"({sorted(SHOPIFY_PLAN_MONTHLY_FEE_MICROUSD)} + partner-development); refusing to "
            "guess a fee"
        )
    return int(fee)


def plan_fee_cost_basis(
    plan_name: Any, *, partner_dev_fee_microusd: int | None = None
) -> "plan_composition.CostBasis":
    """The `shopify_store` component's cost basis: a fixed monthly external fee — FETCHED (from the
    store's plan) and MAPPED (through the explicit table), never typed."""
    return plan_composition.CostBasis.fixed(
        plan_fee_microusd(plan_name, partner_dev_fee_microusd=partner_dev_fee_microusd)
    )


def normalize_shop_domain(raw: Any) -> str:
    """Normalize + validate a `*.myshopify.com` domain (lowercase, strict charset). Raises
    `ShopifyError` on anything else — the connection record never stores a freeform host."""
    domain = str(raw or "").strip().lower()
    domain = domain.removeprefix("https://").removeprefix("http://").rstrip("/")
    if not _SHOP_DOMAIN_RE.fullmatch(domain):
        raise ShopifyError(
            f"invalid shop domain {str(raw)!r}: expected <store>.myshopify.com"
        )
    return domain


# ── Composio transport (brokered; zero raw provider key on runtime planes) ─────────────────────


def default_composio_user_id() -> str:
    """The Composio entity user id, mirroring the channel defaults in `composio_distribution`
    (COMPOSIO_USER_ID is a non-sensitive entity label, resolved through the same `_env_value`
    helper the channels use — never a provider secret)."""
    try:
        from . import composio_distribution
    except ImportError:  # pragma: no cover - alternate load path
        from plugins.takyon import composio_distribution
    return composio_distribution._env_value("COMPOSIO_USER_ID") or "takyon_prod_operator"


def _composio_request(method: str, path: str, **kwargs: Any) -> dict[str, Any]:
    """One brokered Composio call. Maps a missing/undeliverable COMPOSIO_API_KEY — local path
    (`missing COMPOSIO_API_KEY`), brokered path (the safebox 502s the same message), or an
    unreachable safebox authority — to the fail-closed `ShopifyComposioUnconfigured` so callers
    surface a clear *_unconfigured error before any state change."""
    try:
        from . import composio_distribution, safebox
    except ImportError:  # pragma: no cover - alternate load path
        from plugins.takyon import composio_distribution, safebox
    try:
        return composio_distribution._request(method, path, **kwargs)
    except composio_distribution.ComposioDistributionError as exc:
        message = str(exc)
        if "COMPOSIO_API_KEY" in message or "composio_unconfigured" in message:
            raise ShopifyComposioUnconfigured(
                f"shopify_composio_unconfigured: {message}"
            ) from exc
        raise ShopifyConnectionError(message) from exc
    except safebox.SafeboxAuthorityUnavailable as exc:
        raise ShopifyComposioUnconfigured(f"shopify_composio_unconfigured: {exc}") from exc
    except safebox.RemoteSafeboxError as exc:
        message = str(exc)
        if "COMPOSIO_API_KEY" in message or exc.status_code in {401, 403, 503}:
            raise ShopifyComposioUnconfigured(
                f"shopify_composio_unconfigured: {message}"
            ) from exc
        raise ShopifyConnectionError(message) from exc


def _active_shopify_accounts(user_id: str) -> list[dict[str, Any]]:
    payload = _composio_request(
        "GET",
        COMPOSIO_CONNECTED_ACCOUNTS_PATH,
        params=[
            ("toolkit_slugs", SHOPIFY_TOOLKIT_SLUG),
            ("statuses", "ACTIVE"),
            ("user_ids", user_id),
        ],
        timeout=30.0,
    )
    items = payload.get("items")
    if not isinstance(items, list):
        raise ShopifyConnectionError("connected account lookup returned an unexpected payload")
    return [
        item
        for item in items
        if isinstance(item, dict)
        and str(((item.get("toolkit") or {}).get("slug") or "")).strip() == SHOPIFY_TOOLKIT_SLUG
        and str(item.get("status") or "").strip().upper() == "ACTIVE"
    ]


def _account_mentions_domain(item: Mapping[str, Any], shop_domain: str) -> bool:
    """Best-effort match of a connected-account row to a shop domain (the store subdomain rides in
    the connection params/metadata; exact field name varies by auth scheme, so match on the
    serialized row). Used only to DISAMBIGUATE among multiple ACTIVE accounts, never to authorize."""
    subdomain = shop_domain.removesuffix(".myshopify.com")
    try:
        blob = json.dumps(item, ensure_ascii=False).lower()
    except (TypeError, ValueError):
        return False
    return shop_domain in blob or f'"{subdomain}"' in blob


def _resolve_auth_config_id(explicit: str | None) -> str:
    if str(explicit or "").strip():
        return str(explicit).strip()
    payload = _composio_request(
        "GET",
        COMPOSIO_AUTH_CONFIGS_PATH,
        params=[("toolkit_slug", SHOPIFY_TOOLKIT_SLUG)],
        timeout=30.0,
    )
    items = payload.get("items")
    items = [item for item in items if isinstance(item, dict)] if isinstance(items, list) else []
    if not items:
        raise ShopifyConnectionError(
            "no Composio Shopify auth config exists; create the Shopify app auth config in "
            "Composio once (one-time human setup), or pass auth_config_id explicitly"
        )
    if len(items) > 1:
        ids = [str(item.get("id") or "") for item in items]
        raise ShopifyConnectionError(
            f"multiple Composio Shopify auth configs found ({ids}); pass auth_config_id explicitly"
        )
    auth_config_id = str(items[0].get("id") or "").strip()
    if not auth_config_id:
        raise ShopifyConnectionError("Composio Shopify auth config has no id")
    return auth_config_id


def connect_shopify(
    *,
    shop_domain: str,
    user_id: str,
    connected_account_id: str | None = None,
    auth_config_id: str | None = None,
    callback_url: str | None = None,
) -> dict[str, Any]:
    """Attach (adopt an ACTIVE account) or INITIATE the Composio Shopify connection for one store.

    Resolution order mirrors `composio_distribution._resolve_connected_account_id`:
      1. explicit `connected_account_id` wins;
      2. exactly one ACTIVE shopify account for `user_id` → adopt it; several → adopt the single
         one whose row references `shop_domain`, else refuse (ambiguous — pass the id explicitly);
      3. none → initiate a new connected account against the toolkit's auth config; the returned
         `redirect_url` is the operator's one-time OAuth step, custody stays in Composio.

    Returns {connected_account_id, status ('active'|'initiated'), redirect_url?}. Fail-closed:
    `ShopifyComposioUnconfigured` when the broker/key is unavailable, `ShopifyConnectionError`
    when Composio rejects the initiate call (e.g. an offline-unverifiable path shape)."""
    domain = normalize_shop_domain(shop_domain)
    explicit = str(connected_account_id or "").strip()
    if explicit:
        return {"connected_account_id": explicit, "status": "active", "source": "explicit"}

    active = _active_shopify_accounts(user_id)
    if len(active) == 1:
        return {
            "connected_account_id": str(active[0].get("id") or "").strip(),
            "status": "active",
            "source": "adopted_single_active",
        }
    if len(active) > 1:
        matching = [item for item in active if _account_mentions_domain(item, domain)]
        if len(matching) == 1:
            return {
                "connected_account_id": str(matching[0].get("id") or "").strip(),
                "status": "active",
                "source": "adopted_domain_match",
            }
        ids = [str(item.get("id") or "") for item in active]
        raise ShopifyConnectionError(
            f"multiple ACTIVE Composio shopify connected accounts for user_id={user_id} ({ids}) "
            f"and none/many match {domain}; pass connected_account_id explicitly"
        )

    # None active → initiate. Body shape follows Composio v3.1 connected-account creation
    # (UNVERIFIED-OFFLINE; single constant path above — a rejection surfaces the raw Composio
    # error and is validated in the live acceptance run).
    resolved_auth_config = _resolve_auth_config_id(auth_config_id)
    connection: dict[str, Any] = {"user_id": user_id}
    if str(callback_url or "").strip():
        connection["callback_url"] = str(callback_url).strip()
    payload = _composio_request(
        "POST",
        COMPOSIO_CONNECTED_ACCOUNTS_PATH,
        json_body={
            "auth_config": {"id": resolved_auth_config},
            "connection": connection,
        },
        timeout=60.0,
    )
    account_id = str(payload.get("id") or (payload.get("connected_account") or {}).get("id") or "").strip()
    redirect_url = str(
        payload.get("redirect_url")
        or payload.get("redirectUrl")
        or (payload.get("connection_data") or {}).get("redirect_url")
        or ""
    ).strip()
    if not account_id:
        raise ShopifyConnectionError(
            f"Composio connected-account initiation returned no id (payload keys: {sorted(payload)})"
        )
    result: dict[str, Any] = {
        "connected_account_id": account_id,
        "status": "initiated",
        "auth_config_id": resolved_auth_config,
        "source": "initiated",
    }
    if redirect_url:
        result["redirect_url"] = redirect_url
    return result


# ── the cost-basis reader (Admin GraphQL through the broker) ───────────────────────────────────


def _extract_shop_object(payload: Any) -> dict[str, Any] | None:
    """Find the GraphQL `shop` object in the Composio proxy envelope, whatever the nesting (the
    proxy wraps the upstream JSON in `data`, sometimes twice). Fail closed on absence."""
    seen = 0
    stack = [payload]
    while stack and seen < 200:
        seen += 1
        current = stack.pop()
        if isinstance(current, Mapping):
            shop = current.get("shop")
            if isinstance(shop, Mapping) and "plan" in shop:
                return dict(shop)
            stack.extend(current.values())
        elif isinstance(current, (list, tuple)):
            stack.extend(current)
    return None


def read_shop_plan(*, shop_domain: str, connected_account_id: str) -> dict[str, Any]:
    """Fetch the store's current plan via Shopify Admin GraphQL THROUGH the Composio broker's
    generic raw-HTTP proxy (`tools/execute/proxy` — the exact rail `reddit_proxy_request` uses;
    Composio signs with the connected account's credentials, so no token ever reaches this plane).

    Returns {plan_name, partner_development, shopify_plus, shop_domain}. Fail-closed on a missing
    connection, a proxy error, or an unparseable response — never a guessed plan."""
    domain = normalize_shop_domain(shop_domain)
    account = str(connected_account_id or "").strip()
    if not account:
        raise ShopifyConnectionError("read_shop_plan requires a connected_account_id")
    payload = _composio_request(
        "POST",
        COMPOSIO_PROXY_TOOL_PATH,
        json_body={
            "connected_account_id": account,
            "endpoint": f"https://{domain}/admin/api/{SHOPIFY_ADMIN_API_VERSION}/graphql.json",
            "method": "POST",
            "body": {"query": SHOPIFY_SHOP_PLAN_GRAPHQL_QUERY},
        },
        timeout=60.0,
    )
    if isinstance(payload, Mapping) and payload.get("successful") is False:
        raise ShopifyConnectionError(
            f"Composio shopify proxy returned successful=false: {payload.get('error')!r}"
        )
    shop = _extract_shop_object(payload)
    if not shop:
        raise ShopifyConnectionError(
            "shopify plan read returned no shop.plan object; refusing to guess a plan"
        )
    plan = shop.get("plan") if isinstance(shop.get("plan"), Mapping) else {}
    display_name = str(plan.get("displayName") or plan.get("display_name") or "").strip()
    if not display_name:
        raise ShopifyConnectionError("shopify plan read returned no plan displayName")
    return {
        "plan_name": display_name,
        "partner_development": bool(plan.get("partnerDevelopment")),
        "shopify_plus": bool(plan.get("shopifyPlus")),
        "shop_domain": str(shop.get("myshopifyDomain") or domain),
    }


def read_shop_plan_cost_basis(
    *,
    shop_domain: str,
    connected_account_id: str,
    partner_dev_fee_microusd: int | None = None,
) -> tuple["plan_composition.CostBasis", dict[str, Any]]:
    """The full cost-basis read: live plan from the real store (through the broker) → the explicit
    fee map → a fixed-monthly `CostBasis`. Returns (cost_basis, plan_info) so callers can record
    the observed plan alongside the derived fee."""
    plan_info = read_shop_plan(shop_domain=shop_domain, connected_account_id=connected_account_id)
    effective_partner_fee = partner_dev_fee_microusd
    if plan_info.get("partner_development") and effective_partner_fee is None:
        # GraphQL says it IS a dev store even if the display name is unrecognized — same refusal.
        raise ShopifyPlanUnmapped(
            f"shopify plan {plan_info.get('plan_name')!r} is a partner-development store with no "
            "configured partner_dev_fee_microusd; a fee is never guessed"
        )
    basis = plan_fee_cost_basis(
        plan_info["plan_name"], partner_dev_fee_microusd=effective_partner_fee
    )
    return basis, plan_info


# ── connection state (businesses.metadata_json; read side shared with the webhook leaf) ─────────


def _json_dumps(value: Any) -> str:
    return json.dumps(value or {}, ensure_ascii=False, sort_keys=True)


def _metadata_dict(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str) and raw.strip():
        try:
            parsed = json.loads(raw)
        except (TypeError, ValueError):
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def connection_from_metadata(metadata: Any) -> dict[str, Any]:
    """The business's Shopify connection record (or {}) from a businesses.metadata_json value."""
    record = _metadata_dict(metadata).get(SHOPIFY_CONNECTION_METADATA_KEY)
    return dict(record) if isinstance(record, dict) else {}


def find_businesses_by_shop_domain(conn, shop_domain: str) -> list[tuple[str, dict[str, Any]]]:
    """Map a VERIFIED myshopify domain to every (business_slug, connection_record) that recorded
    it, via the canonical connection records in businesses.metadata_json. Prefilters on the
    metadata key, confirms by exact JSON field match (never a substring accept), deterministic
    slug order. Empty when no business owns it."""
    domain = normalize_shop_domain(shop_domain)
    rows = conn.execute(
        "select slug, metadata_json from businesses "
        "where metadata_json is not null and metadata_json like %s "
        "order by slug",
        (f"%{SHOPIFY_CONNECTION_METADATA_KEY}%",),
    ).fetchall()
    matches: list[tuple[str, dict[str, Any]]] = []
    for row in rows or []:
        slug = str(row[0])
        record = connection_from_metadata(row[1])
        if str(record.get("shop_domain") or "").strip().lower() == domain:
            matches.append((slug, record))
    return matches


# ── plan_key versioning ─────────────────────────────────────────────────────────────


_PLAN_VERSION_RE = re.compile(r"^(?P<base>.+)-v(?P<version>\d+)$")


def plan_key_family(plan_key: str) -> tuple[str, int]:
    """('starter-v3') → ('starter', 3); ('starter') → ('starter', 1)."""
    key = str(plan_key or "").strip()
    match = _PLAN_VERSION_RE.match(key)
    if match:
        return match.group("base"), int(match.group("version"))
    return key, 1


def next_plan_key_version(existing_plan_keys: list[str], plan_key: str) -> str:
    """The next unminted version key in `plan_key`'s family, given the business's catalog."""
    base, _ = plan_key_family(plan_key)
    highest = 0
    for key in existing_plan_keys:
        family, version = plan_key_family(key)
        if family == base:
            highest = max(highest, version)
    return f"{base}-v{max(highest, 1) + 1}"


# ── webhook processor (DB leaf; runs AFTER safebox-side HMAC verification) ─────────────────────


def record_webhook_and_process(conn, *, topic: str, raw_body: str) -> dict[str, Any]:
    """THE Shopify webhook entry (DB side; the safebox verifies the HMAC first and only then calls
    this with the verified raw body). Provider-keyed dedup on the EXISTING
    `webhook_events (provider, provider_event_id)` unique key with provider='shopify' — no new
    store, no migration for dedup. The event id is content-derived (see `webhook_dedup_event_id`),
    the row is locked `for update`, and the whole dispatch runs in ONE transaction, mirroring
    `app_payments.record_webhook_and_process` byte-for-byte in shape.

    Returns {provider_event_id, type, deduplicated, processed}."""
    body = str(raw_body or "")
    if len(body.encode("utf-8")) > SHOPIFY_WEBHOOK_MAX_BODY_BYTES:
        raise ShopifyWebhookInvalidEvent("webhook body exceeds the size cap")
    try:
        event = json.loads(body)
    except (TypeError, ValueError) as exc:
        raise ShopifyWebhookInvalidEvent(f"webhook body is not valid JSON: {exc}") from exc
    if not isinstance(event, dict):
        raise ShopifyWebhookInvalidEvent("webhook body must be a JSON object")
    topic_value = str(topic or "").strip().lower()
    event_id = webhook_dedup_event_id(topic_value, body)

    with conn.transaction():
        conn.execute(
            "insert into webhook_events (provider, provider_event_id, payload) "
            "values (%s, %s, %s::jsonb) "
            "on conflict (provider, provider_event_id) do nothing",
            (_WEBHOOK_PROVIDER, event_id, _json_dumps({"topic": topic_value, "body": event})),
        )
        locked = conn.execute(
            "select processed_at from webhook_events "
            "where provider = %s and provider_event_id = %s for update",
            (_WEBHOOK_PROVIDER, event_id),
        ).fetchone()
        if locked is not None and locked[0] is not None:
            return {
                "provider_event_id": event_id,
                "type": topic_value,
                "deduplicated": True,
                "processed": None,
            }
        if topic_value == SHOPIFY_SHOP_UPDATE_TOPIC:
            processed = _process_shop_update(conn, event, event_id)
        else:
            processed = {"recorded": False, "ignored": topic_value}
        conn.execute(
            "update webhook_events set processed_at = now(), error = null "
            "where provider = %s and provider_event_id = %s",
            (_WEBHOOK_PROVIDER, event_id),
        )
    return {
        "provider_event_id": event_id,
        "type": topic_value,
        "deduplicated": False,
        "processed": processed,
    }


def _plan_rows_with_shopify_component(conn, business_slug: str) -> list[Any]:
    return app_entitlements.list_plan_policies(conn, business_slug)


def _stored_composition(plan) -> dict[str, Any] | None:
    meta = plan.metadata if isinstance(plan.metadata, dict) else {}
    record = meta.get(app_entitlements._COMPOSITION_METADATA_KEY)
    if not isinstance(record, dict):
        return None
    composition = record.get("composition")
    return composition if isinstance(composition, dict) else None


def _composition_shopify_fee(composition_data: dict[str, Any]) -> int | None:
    for raw in composition_data.get("components") or []:
        if not isinstance(raw, dict):
            continue
        if str(raw.get("key") or "") != SHOPIFY_STORE_COMPONENT_KEY:
            continue
        basis = raw.get("cost_basis")
        if isinstance(basis, dict) and str(basis.get("kind") or "") == "fixed":
            return int(basis.get("fee_microusd_month") or 0)
    return None


def _recomposed_with_fee(
    composition_data: dict[str, Any], new_fee_microusd: int
) -> "plan_composition.PlanComposition":
    """Rebuild the stored composition with the `shopify_store` component's fixed fee swapped to the
    freshly observed value — everything else byte-identical. Malformed stored data fails closed via
    `composition_from_dict`."""
    updated = json.loads(json.dumps(composition_data))  # deep copy, JSON-safe by construction
    for raw in updated.get("components") or []:
        if isinstance(raw, dict) and str(raw.get("key") or "") == SHOPIFY_STORE_COMPONENT_KEY:
            raw["cost_basis"] = {"kind": "fixed", "fee_microusd_month": int(new_fee_microusd)}
    return plan_composition.composition_from_dict(updated)


def _process_shop_update(conn, shop: dict[str, Any], event_id: str) -> dict[str, Any]:
    """The verified shop/update dispatch: VERIFIED-BODY domain → every business that recorded the
    shop (deterministic order) → new fee via the explicit map → recompose each affected composed
    plan family → mint the NEXT plan_key version (grandfather preserved; the live row is never
    touched). Unknown domain / unmapped plan record a processed-with-error outcome and change
    NOTHING."""
    domain_raw = str(shop.get("myshopify_domain") or shop.get("myshopifyDomain") or "").strip()
    if not domain_raw:
        return {"recorded": False, "error": "shop_update_missing_myshopify_domain"}
    try:
        domain = normalize_shop_domain(domain_raw)
    except ShopifyError:
        return {"recorded": False, "error": "shop_update_invalid_myshopify_domain"}

    matches = find_businesses_by_shop_domain(conn, domain)
    if not matches:
        # No business owns this shop — nothing to recompose. Generic outcome; never reflect
        # internals to the (public) caller beyond this.
        return {"recorded": False, "error": "unknown_shop_domain"}

    plan_name = str(shop.get("plan_name") or shop.get("plan_display_name") or "").strip()
    if not plan_name:
        return {"recorded": False, "error": "shop_update_missing_plan_name"}

    outcomes = [
        _recompose_business_for_shop(conn, business_slug, connection, domain, plan_name, event_id)
        for business_slug, connection in matches
    ]
    if len(outcomes) == 1:
        return outcomes[0]
    return {
        "recorded": any(o.get("recorded") for o in outcomes),
        "shop_domain": domain,
        "plan_name": plan_name,
        "businesses": outcomes,
    }


def _recompose_business_for_shop(
    conn, business_slug: str, connection: dict[str, Any], domain: str, plan_name: str, event_id: str
) -> dict[str, Any]:
    partner_dev_fee = connection.get("partner_dev_fee_microusd")
    partner_dev_fee = int(partner_dev_fee) if partner_dev_fee is not None else None
    try:
        new_fee = plan_fee_microusd(plan_name, partner_dev_fee_microusd=partner_dev_fee)
    except ShopifyPlanUnmapped as exc:
        return {
            "recorded": False,
            "business_slug": business_slug,
            "plan_name": plan_name,
            "error": f"shopify_plan_unmapped: {exc}",
        }

    recomposed: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    plans = _plan_rows_with_shopify_component(conn, business_slug)
    all_keys = [plan.plan_key for plan in plans]
    # Only the LATEST version of each family is a live candidate — earlier versions are
    # grandfathered rows recompose must never touch.
    latest_by_family: dict[str, Any] = {}
    for plan in plans:
        family, version = plan_key_family(plan.plan_key)
        current = latest_by_family.get(family)
        if current is None or plan_key_family(current.plan_key)[1] < version:
            latest_by_family[family] = plan

    for plan in latest_by_family.values():
        composition_data = _stored_composition(plan)
        if composition_data is None:
            continue
        current_fee = _composition_shopify_fee(composition_data)
        if current_fee is None:
            continue  # composed plan without a shopify component — not ours to touch
        if int(current_fee) == int(new_fee):
            skipped.append({"plan_key": plan.plan_key, "unchanged_fee_microusd": int(new_fee)})
            continue
        composition = _recomposed_with_fee(composition_data, new_fee)
        new_key = next_plan_key_version(all_keys, plan.plan_key)
        minted = app_entitlements.upsert_plan_from_composition(
            conn,
            business_slug,
            new_key,
            composition,
            tier=plan.tier,
            currency=plan.currency,
            # Labels a money-shape refusal truthfully (the gate itself never relaxes by kind).
            money_shape_task_kind="shopify_webhook",
            source="takyon_shopify_webhook",
            notes=(
                f"Recomposed from {plan.plan_key}: shopify_store fee "
                f"{int(current_fee)} → {int(new_fee)} µUSD/mo (shop/update {event_id})"
            ),
            metadata={
                "takyon_shopify_recompose": {
                    "from_plan_key": plan.plan_key,
                    "provider_event_id": event_id,
                    "shop_domain": domain,
                    "shopify_plan_name": plan_name,
                    "old_fee_microusd_month": int(current_fee),
                    "new_fee_microusd_month": int(new_fee),
                }
            },
        )
        all_keys.append(minted.plan_key)
        recomposed.append(
            {
                "from_plan_key": plan.plan_key,
                "plan_key": minted.plan_key,
                "price_cents": minted.price_cents,
                "old_fee_microusd_month": int(current_fee),
                "new_fee_microusd_month": int(new_fee),
            }
        )

    return {
        "recorded": True,
        "business_slug": business_slug,
        "shop_domain": domain,
        "plan_name": plan_name,
        "fee_microusd_month": int(new_fee),
        "recomposed": recomposed,
        "unchanged": skipped,
    }
