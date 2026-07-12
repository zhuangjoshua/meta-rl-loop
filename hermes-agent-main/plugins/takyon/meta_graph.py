"""Meta Graph Marketing API leg for takyon-meta-ads-v2.

This module is the *Graph* half of the proven hybrid launch flow. It owns the
two jobs that the official Meta Ads MCP cannot do for an app that is still in
development mode: uploading the generated creative bytes (video / image) and
running ad-object lifecycle (status + budget) once the objects exist.

Token discipline (mirrors meta_mcp.py exactly):
    This module NEVER resolves a token. Callers resolve the SYSTEM-USER token
    on the authority/safebox plane (core.safebox.first_env_backed_value(
    "META_SYSTEM_USER_ACCESS_TOKEN")) and pass it in. There are no env reads,
    no hardcoded secrets, and no hardcoded ids anywhere in this file. The token
    we receive is a normal Graph Marketing API bearer; it is REJECTED by the
    MCP and is disjoint from META_MCP_OAUTH_TOKEN (proven live this session).

Everything is bytes-first multipart (proven to work): the runtime materializes
the creative locally, the launch handler reads the bytes, and we POST them to
Meta with httpx ``files=``. We never round-trip through a signed/public URL.
"""

from __future__ import annotations

import json
import hashlib
import hmac
import threading
import time
from typing import Any, Mapping

# Graph host for object/management calls and image uploads. Large media (video)
# is uploaded against the dedicated video host, which Meta documents for
# /advideos. Both are reachable from the same egress plane that already talks to
# the AI creative providers + mcp.facebook.com.
_GRAPH_HOST = "graph.facebook.com"
_GRAPH_VIDEO_HOST = "graph-video.facebook.com"

# Polling cadence for asynchronous video processing. Meta accepts the upload
# immediately but the creative cannot reference the video until status is
# "ready"; we poll GET /<video_id>?fields=status until then (or timeout).
_VIDEO_POLL_INTERVAL_S = 3.0

# Shared, keep-alive httpx client reused across all Graph calls (including the
# upload_video poll loop). Building one client per request tore down a fresh
# connection pool + TLS handshake every time; a single module-level client
# amortizes that across the upload+poll hot path. Created lazily so a missing
# httpx dependency still surfaces only when a Graph call is actually made
# (preserving prior behavior).
_CLIENT_LOCK = threading.Lock()
_CLIENT: Any = None


def _client() -> Any:
    """Return the shared, lazily-initialized keep-alive httpx.Client."""
    global _CLIENT
    if _CLIENT is None:
        with _CLIENT_LOCK:
            if _CLIENT is None:
                try:
                    import httpx
                except Exception as exc:  # pragma: no cover - dependency missing
                    raise MetaGraphError(
                        "Meta Graph calls require the httpx package"
                    ) from exc
                _CLIENT = httpx.Client()
    return _CLIENT


def account_path(ad_account_id: str) -> str:
    """Normalize an ad account id to Meta's ``act_<digits>`` edge path.

    Accepts either a bare numeric id ("1234567890") or an already-prefixed
    "act_1234567890" and always returns the prefixed form. Raises if there are
    no digits to work with so callers fail loudly instead of POSTing to a bad
    edge.
    """
    raw = str(ad_account_id or "").strip()
    if raw.startswith("act_"):
        raw = raw[len("act_"):]
    # An ad account id is purely numeric; reject anything else so we never POST
    # to a malformed edge.
    if not raw or not raw.isdigit():
        raise MetaGraphError(f"invalid ad_account_id: {ad_account_id!r}")
    return f"act_{raw}"


class MetaGraphError(RuntimeError):
    """Raised for any Meta Graph Marketing API failure.

    The message carries the parsed Meta error envelope (code / subcode /
    error_user_title / error_user_msg / blame_field_specs) so a launch handler
    can write a diagnosable receipt. Meta's bare ``message`` for code 100 is
    intentionally generic; the actionable detail lives in the secondary fields,
    so we always fold those in here while the raw error is still in hand.
    """


def _format_meta_error(method: str, path: str, error: Mapping[str, Any], fallback: str) -> str:
    """Build a single diagnosable string from a Meta error envelope."""
    code = str(error.get("code") or "").strip()
    subcode = str(error.get("error_subcode") or "").strip()
    message = str(error.get("message") or "").strip()
    user_title = str(error.get("error_user_title") or "").strip()
    user_msg = str(error.get("error_user_msg") or "").strip()
    error_data = error.get("error_data")
    blame_field = ""
    if isinstance(error_data, Mapping):
        blame_field = str(error_data.get("blame_field_specs") or "").strip()

    # Deduplicate while preserving order (message and user_msg are often equal).
    detail_parts = [p for p in (message, user_title, user_msg, blame_field) if p]
    seen: set[str] = set()
    detail = "; ".join(p for p in detail_parts if not (p in seen or seen.add(p)))
    if not detail:
        detail = fallback

    code_suffix = ""
    if code:
        code_suffix = f" (code {code}" + (f"/{subcode}" if subcode else "") + ")"
    return f"Meta Graph {method} /{path} failed{code_suffix}" + (f": {detail}" if detail else "")


def _graph(
    method: str,
    path: str,
    data_or_files: Mapping[str, Any] | None,
    *,
    token: str,
    version: str = "v21.0",
    host: str = _GRAPH_HOST,
    files: Mapping[str, Any] | None = None,
    timeout: float = 180.0,
) -> dict[str, Any]:
    """Single httpx round-trip against the Graph Marketing API.

    ``data_or_files`` is the form payload (GET -> query params, others -> form
    fields). ``files`` is an optional httpx multipart mapping for bytes-first
    uploads (e.g. {"source": ("ad.mp4", raw, "video/mp4")}). The access_token is
    injected as a form/query field, never logged. Any HTTP >=400 or any response
    carrying an ``error`` envelope is raised as MetaGraphError.
    """
    client = _client()

    bearer = str(token or "").strip()
    if not bearer:
        # We never resolve tokens here; an empty token is a caller bug.
        raise MetaGraphError("Meta Graph call requires a resolved system-user access token")

    clean = {k: v for k, v in dict(data_or_files or {}).items() if v is not None}
    clean["access_token"] = bearer

    url = f"https://{host}/{version}/{path}"
    request_kwargs: dict[str, Any] = {"timeout": float(timeout)}
    if str(method).upper() == "GET":
        request_kwargs["params"] = clean
    else:
        request_kwargs["data"] = clean
        if files:
            request_kwargs["files"] = dict(files)

    try:
        resp = client.request(str(method).upper(), url, **request_kwargs)
    except Exception as exc:
        raise MetaGraphError(f"Meta Graph {method} /{path} failed: {exc}") from exc

    # Meta always answers JSON for the Marketing API; tolerate a non-JSON body
    # (e.g. an HTML gateway error) by surfacing the raw text.
    try:
        data = resp.json()
    except Exception:
        data = None

    if resp.status_code >= 400 or (isinstance(data, Mapping) and data.get("error")):
        error = data.get("error") if isinstance(data, Mapping) else None
        if not isinstance(error, Mapping):
            error = {}
        raise MetaGraphError(
            _format_meta_error(str(method).upper(), path, error, getattr(resp, "text", "") or "")
        )

    if isinstance(data, Mapping):
        return dict(data)
    if isinstance(data, list):
        return {"data": data}
    return {}


def upload_video(
    token: str,
    ad_account_id: str,
    video_bytes: bytes,
    *,
    name: str,
    version: str = "v21.0",
    poll: bool = True,
    timeout: float = 180.0,
) -> str:
    """Upload raw video bytes to an ad account and return the video id.

    Bytes-first multipart: POST act_<id>/advideos with the file in the
    ``source`` field (proven). The upload returns immediately; Meta then
    processes the video asynchronously. When ``poll`` is set we GET
    /<video_id>?fields=status until status.video_status == "ready" (the state a
    creative requires), or raise on timeout / an error status.

    NOTE: a video creative ALSO needs a thumbnail (image_hash or image_url).
    That is the launch handler's responsibility — it stages/uploads a thumbnail
    separately. This function only produces the video_id.
    """
    if not video_bytes:
        raise MetaGraphError("upload_video received empty video bytes")
    acct = account_path(ad_account_id)

    result = _graph(
        "POST",
        f"{acct}/advideos",
        {"name": name},
        token=token,
        version=version,
        host=_GRAPH_VIDEO_HOST,
        files={"source": (f"{name or 'ad'}.mp4", video_bytes, "video/mp4")},
        timeout=timeout,
    )
    video_id = str(result.get("id") or "").strip()
    if not video_id:
        raise MetaGraphError(f"Meta video upload returned no id (name={name!r})")

    if not poll:
        return video_id

    # Poll for readiness. ``timeout`` is reused as the overall poll budget.
    deadline = time.monotonic() + float(timeout)
    while True:
        status_doc = _graph(
            "GET",
            video_id,
            {"fields": "status"},
            token=token,
            version=version,
            host=_GRAPH_HOST,
            timeout=min(60.0, float(timeout)),
        )
        status = status_doc.get("status")
        phase = ""
        if isinstance(status, Mapping):
            phase = str(status.get("video_status") or status.get("status") or "").strip().lower()
        if phase == "ready":
            return video_id
        if phase == "error":
            raise MetaGraphError(f"Meta video {video_id} processing failed (status=error)")
        if time.monotonic() >= deadline:
            # Return the id rather than discarding a successful upload; the
            # caller writes a repairable receipt and can re-poll later.
            raise MetaGraphError(
                f"Meta video {video_id} not ready after {timeout:.0f}s (last status={phase or 'unknown'})"
            )
        time.sleep(_VIDEO_POLL_INTERVAL_S)


def upload_image(
    token: str,
    ad_account_id: str,
    image_bytes: bytes,
    *,
    name: str,
    version: str = "v21.0",
    timeout: float = 180.0,
) -> dict:
    """Upload raw image bytes to an ad account and return {'hash', 'url'}.

    Bytes-first multipart: POST act_<id>/adimages with the file in the
    ``filename`` field (Meta's documented field name for this edge). The
    response nests results under ``images`` keyed by filename; we flatten it to
    the hash + url a creative needs.
    """
    if not image_bytes:
        raise MetaGraphError("upload_image received empty image bytes")
    acct = account_path(ad_account_id)

    upload_name = name or "ad"
    result = _graph(
        "POST",
        f"{acct}/adimages",
        {"name": upload_name},
        token=token,
        version=version,
        host=_GRAPH_HOST,
        files={"filename": (f"{upload_name}.png", image_bytes, "image/png")},
        timeout=timeout,
    )

    # Meta returns {"images": {"<filename>": {"hash": ..., "url": ...}}}; older
    # shapes put hash/url at the top level. Handle both.
    images = result.get("images") if isinstance(result.get("images"), Mapping) else {}
    first = next(iter(images.values()), {}) if images else {}
    if isinstance(first, Mapping) and first:
        image_hash = str(first.get("hash") or result.get("hash") or "").strip()
        image_url = str(first.get("url") or result.get("url") or "").strip()
    else:
        image_hash = str(result.get("hash") or result.get("image_hash") or "").strip()
        image_url = str(result.get("url") or result.get("image_url") or "").strip()

    if not image_hash:
        raise MetaGraphError(f"Meta image upload returned no image hash (name={name!r})")
    return {"hash": image_hash, "url": image_url or None}


def set_status(
    token: str,
    object_id: str,
    status: str,
    *,
    version: str = "v21.0",
) -> dict:
    """Set an ad object's effective status (campaign / ad set / ad).

    Used for activation (PAUSED -> ACTIVE on live launches, in
    campaign->adset->ad order) and for pause/activate control actions. Meta
    accepts {"status": "ACTIVE"|"PAUSED"} on POST /<object_id>.
    """
    obj = str(object_id or "").strip()
    if not obj:
        raise MetaGraphError("set_status requires an object_id")
    desired = str(status or "").strip().upper()
    if desired not in {"ACTIVE", "PAUSED", "ARCHIVED", "DELETED"}:
        raise MetaGraphError(f"unsupported status {status!r} (expected ACTIVE/PAUSED)")
    return _graph(
        "POST",
        obj,
        {"status": desired},
        token=token,
        version=version,
    )


def update_daily_budget(
    token: str,
    object_id: str,
    daily_budget_cents: int,
    *,
    version: str = "v21.0",
) -> dict:
    """Update an ad set's (or campaign's, for CBO) daily budget in minor units.

    Meta budgets are integer cents in the account currency. Callers pass the
    object id whose daily_budget they own (ad set for ad-set budgeting).
    """
    obj = str(object_id or "").strip()
    if not obj:
        raise MetaGraphError("update_daily_budget requires an object_id")
    try:
        cents = int(daily_budget_cents)
    except (TypeError, ValueError) as exc:
        raise MetaGraphError(f"invalid daily_budget_cents: {daily_budget_cents!r}") from exc
    if cents <= 0:
        raise MetaGraphError("daily_budget_cents must be a positive integer (minor units)")
    return _graph(
        "POST",
        obj,
        {"daily_budget": cents},
        token=token,
        version=version,
    )


def get_custom_conversion(token: str, conversion_id: str, *, version: str = "v21.0") -> dict:
    """Read a custom conversion back from Meta — the trust step. Creation (or duplicate
    recovery) only proves an id exists; this read proves WHAT that id actually is."""
    return _graph(
        "GET",
        str(conversion_id),
        {"fields": "id,name,custom_event_type,rule,pixel"},
        token=token,
        version=version,
    )


def find_custom_conversion_id_by_name(
    token: str, ad_account_id: str, name: str, *, version: str = "v21.0"
) -> str:
    """Deterministic duplicate recovery: list the account's custom conversions and match the
    EXACT name. Replaces the removed error-text digit-scan heuristic — an attribution id must
    come from a Meta read, never from guessing numbers out of an error string."""
    acct = account_path(ad_account_id)
    payload = _graph(
        "GET",
        f"{acct}/customconversions",
        {"fields": "id,name", "limit": 500},
        token=token,
        version=version,
    )
    for entry in payload.get("data") or []:
        if isinstance(entry, dict) and str(entry.get("name") or "") == str(name):
            return str(entry.get("id") or "")
    return ""


def _normalized_rule(rule_value: object) -> str:
    """Canonical JSON form for rule comparison (Meta may reserialize key order/whitespace)."""
    try:
        return json.dumps(json.loads(str(rule_value)), sort_keys=True, separators=(",", ":"))
    except Exception:
        return str(rule_value or "").strip()


def purchase_custom_conversion_rule(event_name: str, site_hostname: str) -> str:
    """Build the strict server-event purchase rule used for shared-pixel isolation.

    The event name is a Safebox-derived, per-business value sent only through CAPI; it is
    never emitted by the browser.  The hostname clause is defense in depth and prevents a
    valid event for one business from being reused against another business's URL.
    """
    event = str(event_name or "").strip()
    host = str(site_hostname or "").strip().lower().rstrip(".")
    if not event:
        raise ValueError("purchase custom conversion requires event_name")
    if not host or "/" in host or ":" in host:
        raise ValueError("purchase custom conversion requires a hostname")
    return json.dumps(
        {
            "and": [
                {"event": {"eq": event}},
                {"url": {"i_contains": f"{host}/app"}},
            ]
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def derive_purchase_event_name(signing_key: bytes, business: str) -> str:
    """Derive a stable, unguessable CAPI-only event name for one business."""
    key = bytes(signing_key or b"")
    slug = str(business or "").strip().lower()
    if len(key) < 32 or not slug:
        raise ValueError("purchase event derivation requires a signing key and business")
    digest = hmac.new(key, f"meta-purchase-v1:{slug}".encode(), hashlib.sha256).hexdigest()
    return f"TakyonPurchase_{digest[:32]}"


def send_purchase_conversion_event(
    token: str,
    pixel_id: str,
    *,
    event_name: str,
    event_time: int,
    event_id: str,
    event_source_url: str,
    user_data: Mapping[str, Any],
    value: float,
    currency: str,
    version: str = "v21.0",
) -> dict[str, Any]:
    """Send one Stripe-proven purchase through Meta CAPI.

    The public browser pixel never emits ``event_name``.  The Safebox calls this function
    only after Stripe signature and live-account object verification, so product subusers
    cannot manufacture the event that the custom conversion counts.
    """
    pixel = str(pixel_id or "").strip()
    name = str(event_name or "").strip()
    source_url = str(event_source_url or "").strip()
    eid = str(event_id or "").strip()
    if not pixel.isdigit() or not name or not source_url or not eid:
        raise ValueError("CAPI purchase requires pixel_id, event_name, event_id, and source URL")
    event = {
        "event_name": name,
        "event_time": int(event_time),
        "event_id": eid,
        "action_source": "website",
        "event_source_url": source_url,
        "user_data": dict(user_data or {}),
        "custom_data": {
            "value": round(float(value), 2),
            "currency": str(currency or "USD").strip().upper() or "USD",
        },
    }
    result = _graph(
        "POST",
        f"{pixel}/events",
        {"data": json.dumps([event], separators=(",", ":"))},
        token=token,
        version=version,
        timeout=60.0,
    )
    if int(result.get("events_received") or 0) != 1:
        raise MetaGraphError("Meta CAPI did not acknowledge exactly one purchase event")
    return result


def verify_custom_conversion(
    token: str,
    conversion_id: str,
    *,
    expected_rule: str,
    expected_event_type: str,
    expected_pixel_id: str,
    version: str = "v21.0",
) -> dict:
    """Read the conversion from Meta and require id, pixel, event type, and EXACT rule to
    match what we intended. A same-named conversion with a different rule/pixel/type must
    never be accepted as the purchase-attribution boundary. Raises MetaGraphError on any
    mismatch; returns the read payload on success."""
    payload = get_custom_conversion(token, conversion_id, version=version)
    problems: list[str] = []
    if str(payload.get("id") or "") != str(conversion_id):
        problems.append(f"id {payload.get('id')!r} != {conversion_id!r}")
    got_type = str(payload.get("custom_event_type") or "").strip().upper()
    if got_type != str(expected_event_type or "").strip().upper():
        problems.append(f"custom_event_type {got_type!r} != {expected_event_type!r}")
    pixel_field = payload.get("pixel")
    got_pixel = str(
        (pixel_field.get("id") if isinstance(pixel_field, dict) else pixel_field) or ""
    ).strip()
    if got_pixel != str(expected_pixel_id or "").strip():
        problems.append(f"pixel {got_pixel!r} != {expected_pixel_id!r}")
    if _normalized_rule(payload.get("rule")) != _normalized_rule(expected_rule):
        problems.append(f"rule {payload.get('rule')!r} != {expected_rule!r}")
    if problems:
        raise MetaGraphError(
            f"custom conversion {conversion_id} failed read-back verification "
            f"({'; '.join(problems)}) — refusing to certify it as the purchase-attribution boundary"
        )
    return payload


def ensure_custom_conversion(
    token: str,
    ad_account_id: str,
    *,
    name: str,
    rule: str,
    custom_event_type: str,
    event_source_id: str,
    version: str = "v21.0",
) -> dict:
    """Create (or deterministically recover) a URL-rule custom conversion and VERIFY it.

    The platform shares ONE pixel across businesses; per-business attribution is achieved by
    a URL-rule custom conversion anchored to that pixel. Flow:
      1. POST create.
      2. On a duplicate-name error, recover the existing id by LISTING the account's
         conversions and matching the exact name (no error-text guessing).
      3. READ the conversion back and verify id, pixel, event type, and exact rule.
    Only a verified conversion is returned; the result carries ``verified: True`` plus the
    verified fields so callers can gate the canonical attribution record on them."""
    acct = account_path(ad_account_id)
    rule_value = rule if isinstance(rule, str) else json.dumps(rule)
    # Meta REQUIRES the event source (the pixel this conversion listens to): the API
    # refuses creation without it — "(#100) The parameter event_source_id is required".
    # Required at every layer of this chain so a missing pixel fails loudly at the edge.
    if not str(event_source_id or "").strip():
        raise ValueError("ensure_custom_conversion requires event_source_id (the pixel id)")
    pixel_id = str(event_source_id).strip()
    existed = False
    try:
        created = _graph(
            "POST",
            f"{acct}/customconversions",
            {
                "name": name,
                "rule": rule_value,
                "custom_event_type": custom_event_type,
                "event_source_id": pixel_id,
            },
            token=token,
            version=version,
        )
        conversion_id = str(created.get("id") or "").strip()
    except MetaGraphError as exc:
        text = str(exc).lower()
        if "already" not in text and "exists" not in text and "duplicate" not in text:
            raise
        conversion_id = find_custom_conversion_id_by_name(
            token, ad_account_id, name, version=version
        )
        if not conversion_id:
            raise
        existed = True
    if not conversion_id:
        raise MetaGraphError("custom conversion create returned no id")
    verified = verify_custom_conversion(
        token,
        conversion_id,
        expected_rule=rule_value,
        expected_event_type=custom_event_type,
        expected_pixel_id=pixel_id,
        version=version,
    )
    return {
        "id": conversion_id,
        "existed": existed,
        "verified": True,
        "custom_event_type": str(verified.get("custom_event_type") or "").strip().upper(),
        "pixel_id": pixel_id,
        "rule": str(verified.get("rule") or rule_value),
        "name": str(verified.get("name") or name),
    }
