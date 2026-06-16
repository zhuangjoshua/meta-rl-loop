"""Minimal Umami REST helper for reading published product-site analytics.

Mirrors the self-contained, stdlib-only shape of ``stripe_util.py``. The API
base comes from config (``analytics.umami.api_endpoint``) and the key from the
read-only Safebox env authority (``UMAMI_API_KEY``) — never raw env, never a
faked response. One shared Umami website is used across all businesses; callers
pass the per-business subdomain as ``hostname`` so Umami returns only that
site's slice (isolation is enforced here by the filter, not by Umami).

Cloud contract (https://docs.umami.is): GET {base}/websites/{id}/stats with
header ``x-umami-api-key``; ``startAt``/``endAt`` are unix-millisecond
timestamps; the optional filter is ``hostname``. Cloud base is
``https://api.umami.is/v1``; self-hosted base is ``https://<host>/api``.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from . import safebox


class UmamiError(Exception):
    """A Umami API call that failed or was unconfigured — raised, never faked."""


def umami_configured() -> bool:
    """True when a Umami API key is present (stats reads are possible).

    Never raises — a missing/unavailable secret authority reads as "not
    configured" rather than propagating, so callers can probe safely."""
    try:
        return bool(str(safebox.read_env_backed_value("UMAMI_API_KEY") or "").strip())
    except Exception:
        return False


def umami_request(
    path: str,
    params: dict[str, Any] | None,
    api_endpoint: str,
    *,
    timeout: int = 20,
) -> dict[str, Any]:
    """GET ``{api_endpoint}/{path}`` with the shared Umami API key, dropping
    None-valued params. Returns the parsed JSON object. Raises ``UmamiError`` if
    the key/endpoint is missing (never faked) or the call returns non-2xx."""
    key = str(safebox.read_env_backed_value("UMAMI_API_KEY") or "").strip()
    if not key:
        raise UmamiError("Umami analytics read requires UMAMI_API_KEY")
    base = str(api_endpoint or "").strip().rstrip("/")
    if not base:
        raise UmamiError("Umami analytics read requires analytics.umami.api_endpoint")
    encoded = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
    url = f"{base}/{path.lstrip('/')}"
    if encoded:
        url = f"{url}?{encoded}"
    request = urllib.request.Request(
        url,
        headers={"Accept": "application/json", "x-umami-api-key": key},
        method="GET",
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            raw = response.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else {}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise UmamiError(f"Umami GET {path} failed: {exc.code} {body}") from exc
    except urllib.error.URLError as exc:
        raise UmamiError(f"Umami GET {path} failed: {exc.reason}") from exc


def website_stats(
    website_id: str,
    *,
    start_ms: int,
    end_ms: int,
    api_endpoint: str,
    hostname: str = "",
    timeout: int = 20,
) -> dict[str, int]:
    """Return normalized {pageviews, visitors, visits, bounces, totaltime} for a
    website over [start_ms, end_ms], optionally filtered to one subdomain
    ``hostname``. Each metric is coerced to an int (Umami returns either a bare
    number or a {value, prev} object depending on comparison mode)."""
    if not str(website_id or "").strip():
        raise UmamiError("Umami analytics read requires a website id")
    params: dict[str, Any] = {"startAt": int(start_ms), "endAt": int(end_ms)}
    if hostname:
        params["hostname"] = hostname
    raw = umami_request(f"websites/{website_id}/stats", params, api_endpoint, timeout=timeout)

    def _num(value: Any) -> int:
        if isinstance(value, dict):
            value = value.get("value")
        try:
            return int(value or 0)
        except (TypeError, ValueError):
            return 0

    return {metric: _num(raw.get(metric)) for metric in ("pageviews", "visitors", "visits", "bounces", "totaltime")}


def website_pageviews_series(
    website_id: str,
    *,
    start_ms: int,
    end_ms: int,
    unit: str,
    api_endpoint: str,
    hostname: str = "",
    timezone: str = "UTC",
    timeout: int = 20,
) -> dict[str, list[dict[str, Any]]]:
    """Return Umami pageviews + sessions time series for a website over
    [start_ms, end_ms] at ``unit`` granularity (hour/day/month), optionally
    filtered to one subdomain ``hostname``. Each series is a list of
    {"x": <bucket timestamp string>, "y": <count int>}."""
    if not str(website_id or "").strip():
        raise UmamiError("Umami analytics read requires a website id")
    params: dict[str, Any] = {
        "startAt": int(start_ms),
        "endAt": int(end_ms),
        "unit": str(unit or "day"),
        "timezone": str(timezone or "UTC"),
    }
    if hostname:
        params["hostname"] = hostname
    raw = umami_request(f"websites/{website_id}/pageviews", params, api_endpoint, timeout=timeout)

    def _series(name: str) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for item in raw.get(name) or []:
            if isinstance(item, dict):
                try:
                    count = int(item.get("y") or 0)
                except (TypeError, ValueError):
                    count = 0
                out.append({"x": item.get("x"), "y": count})
        return out

    return {"pageviews": _series("pageviews"), "sessions": _series("sessions")}
