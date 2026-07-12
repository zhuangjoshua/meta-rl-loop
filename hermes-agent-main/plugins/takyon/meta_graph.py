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


def ensure_custom_conversion(
    token: str,
    ad_account_id: str,
    *,
    name: str,
    rule: str,
    custom_event_type: str,
    event_source_id: str = "",
    version: str = "v21.0",
) -> dict:
    """Create (or return) a URL-rule custom conversion for per-business attribution.

    The platform shares ONE pixel across businesses; per-business attribution is
    achieved by a URL-rule custom conversion on the account. ``rule`` is Meta's
    JSON rule string (e.g. a URL CONTAINS match for the business's domain/slug).
    If a conversion with the same name already exists Meta returns an error
    whose body carries the existing id; we surface that id so callers stay
    idempotent instead of erroring on re-run.
    """
    acct = account_path(ad_account_id)
    rule_value = rule if isinstance(rule, str) else json.dumps(rule)
    params = {
        "name": name,
        "rule": rule_value,
        "custom_event_type": custom_event_type,
    }
    # Meta REQUIRES the event source (the pixel this conversion listens to): the API
    # refuses creation without it — "(#100) The parameter event_source_id is required".
    if str(event_source_id or "").strip():
        params["event_source_id"] = str(event_source_id).strip()
    try:
        return _graph(
            "POST",
            f"{acct}/customconversions",
            params,
            token=token,
            version=version,
        )
    except MetaGraphError as exc:
        # Meta error 2650 / "already exists" carries the existing conversion id
        # in error_data; if we can recover it, treat the call as idempotent.
        existing = _existing_conversion_id_from_error(exc)
        if existing:
            return {"id": existing, "existed": True}
        raise


def _existing_conversion_id_from_error(exc: MetaGraphError) -> str:
    """Best-effort: pull an existing-object id out of a duplicate-name error.

    Meta embeds the existing id in error_data for some duplicate errors; the
    detail is already folded into the exception string, so scan it for a
    numeric id. Returns "" when nothing is recoverable (caller re-raises).
    """
    text = str(exc)
    if "already" not in text.lower() and "exists" not in text.lower():
        return ""
    # Grab the longest run of digits as a heuristic conversion id.
    best = ""
    current = ""
    for ch in text:
        if ch.isdigit():
            current += ch
        else:
            if len(current) > len(best):
                best = current
            current = ""
    if len(current) > len(best):
        best = current
    return best if len(best) >= 6 else ""
