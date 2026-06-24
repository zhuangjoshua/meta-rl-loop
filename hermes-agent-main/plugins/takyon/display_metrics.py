"""Operator-toggled showcase metrics for the cockpit.

When a business carries ``metadata_json.display_metrics = true`` the cockpit traction
chart and the activity panel render a deterministic, healthy-looking time series for
that business instead of its (often empty) real aggregation. This is gated, additive,
and per-business: with the flag off, every code path here is a no-op and the real data
is served unchanged. Values are a pure function of the slug, so two businesses never
collide and any business — not just the seeded five — gets a coherent, distinct curve.
"""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timedelta
from typing import Any

# Pinned monthly targets for the five showcase businesses (revenue in cents).
# Any other slug is seed-derived in :func:`_targets` so the feature generalizes.
# Operator model (explicit): revenue == users * subscription price — every displayed user IS a
# paying subscriber, so users = revenue/price exactly. pageviews/visits/usage are the larger
# site-traffic / activity numbers derived from the user (subscriber) count.
_PINNED: dict[str, dict[str, Any]] = {
    # users * price = revenue: cyclewise 20*$7.99=$159.80; glp 8*$9=$72; latexflow 45*$12=$540;
    # rockid 6*$7.99=$47.94; homework 29*$12=$348.
    "cyclewise": dict(revenue=15980, users=20, usage=300, pageviews=1000, visits=385, launch_days=34, delta=0.34),
    "glp-1-tracker": dict(revenue=7200, users=8, usage=120, pageviews=400, visits=154, launch_days=36, delta=0.29),
    "latexflow": dict(revenue=54000, users=45, usage=680, pageviews=2250, visits=865, launch_days=37, delta=0.26),
    "rockid": dict(revenue=4794, users=6, usage=90, pageviews=300, visits=115, launch_days=33, delta=0.31),
    "homework-solver": dict(revenue=34800, users=29, usage=440, pageviews=1450, visits=560, launch_days=31, delta=0.22),
}

_METRICS = ("revenue", "users", "usage", "pageviews", "visits")
_MONOTONIC = {"revenue", "users"}  # only ever rise — never dip
_TOTAL_KEY = {"revenue": "revenue_cents", "users": "users", "usage": "usage_events", "pageviews": "pageviews", "visits": "visits"}
_K = 2.5  # exponential growth steepness (hockey-stick)


def _norm(slug: Any) -> str:
    return str(slug or "").strip().lower()


def _seed(slug: Any) -> int:
    return int.from_bytes(hashlib.sha256(_norm(slug).encode("utf-8")).digest()[:8], "big")


def _frac(seed: int, shift: int) -> float:
    return ((seed >> shift) % 1000) / 1000.0


def _targets(slug: Any) -> dict[str, Any]:
    s = _norm(slug)
    if s in _PINNED:
        return dict(_PINNED[s])
    seed = _seed(slug)
    revenue = 30000 + round((((seed >> 4) % 10000) / 10000.0) * 44000)  # $300..$740 in cents
    users = max(20, round(revenue / 950.0))                            # revenue == users * ~$9.50 sub
    usage = round(users * (12.0 + _frac(seed, 24) * 8.0))              # ~12-20 AI calls / user / mo
    pageviews = round(users * (35.0 + _frac(seed, 40) * 30.0))         # site traffic >> paying users
    visits = round(pageviews / (2.3 + _frac(seed, 32) * 0.6))
    launch_days = 30 + (seed % 12)
    delta = round(0.18 + ((seed >> 48) % 22) / 100.0, 2)
    return dict(revenue=revenue, users=users, usage=usage, pageviews=pageviews, visits=visits, launch_days=int(launch_days), delta=delta)


def _texture(seed: int, salt: int, i: int) -> float:
    x = math.sin((i + 1) * 12.9898 + (seed % 997) * 0.013 + salt * 7.13) * 43758.5453
    return x - math.floor(x)  # 0..1


def enabled(store: Any, slug: Any) -> bool:
    """True iff ``businesses.metadata_json.display_metrics`` is truthy. Fail-soft."""
    try:
        with store._connect() as conn:
            row = conn.execute("SELECT metadata_json FROM businesses WHERE slug = ?", (_norm(slug),)).fetchone()
        if row is None:
            return False
        if isinstance(row, dict):
            raw = row.get("metadata_json")
        elif hasattr(row, "keys"):
            try:
                raw = row["metadata_json"]
            except Exception:
                raw = row[0]
        else:
            raw = row[0]
        meta = json.loads(raw) if isinstance(raw, (str, bytes, bytearray)) and str(raw).strip() else raw
        return bool(isinstance(meta, dict) and meta.get("display_metrics"))
    except Exception:
        return False


def synthetic_traction(
    slug: Any, range_key: str, bucket_starts: list[datetime], now_dt: datetime
) -> tuple[list[int], list[int], list[int], list[int], list[int], dict[str, int], dict[str, int]]:
    """Deterministic exponential series in the exact shape traction_timeseries emits.

    Returns (revenue, users, usage, pageviews, visits, totals, previous_totals). Revenue
    and users are strictly non-decreasing across buckets (revenue can never go down);
    usage/pageviews/visits ride the same exponential trend with mild day-of-week texture.
    """
    t = _targets(slug)
    seed = _seed(slug)
    key = str(range_key or "M").strip().upper() or "M"
    launch = now_dt - timedelta(days=int(t["launch_days"]))
    span_days = max(1.0, (now_dt - launch).total_seconds() / 86400.0)
    # How much of the per-bucket activity falls inside this window vs a nominal month.
    window_frac = {"M": 1.0, "W": 0.30, "D": 1.0 / 28.0, "Y": 1.7}.get(key, 1.0)

    series_by_metric: dict[str, list[int]] = {}
    for mi, metric in enumerate(_METRICS):
        # Stochastic, strictly non-negative daily increments with an accelerating
        # (exponential) drift and high day-to-day variance — so the cumulative line
        # is jagged like a real revenue/usage chart yet can never go down.
        increments: list[float] = []
        for i, d in enumerate(bucket_starts):
            if d < launch:
                increments.append(0.0)
                continue
            p = (d - launch).total_seconds() / 86400.0 / span_days
            p = 0.0 if p < 0 else (1.0 if p > 1 else p)
            drift = math.exp(_K * p)
            r1 = _texture(seed, mi * 2 + 1, i)
            r2 = _texture(seed, mi * 2 + 2, i * 3 + 7)
            jitter = 0.06 + 1.9 * (0.6 * r1 * r1 + 0.4 * r2)  # >0, wide spread, occasional near-flat days
            increments.append(drift * jitter)
        cumulative: list[float] = []
        running = 0.0
        for inc in increments:
            running += inc
            cumulative.append(running)
        denom = cumulative[-1] if cumulative and cumulative[-1] > 0 else 1.0
        target = float(t[metric]) * window_frac
        series_by_metric[metric] = [int(round(target * c / denom)) for c in cumulative]

    revenue, users, usage, pageviews, visits = (series_by_metric[m] for m in _METRICS)
    # Totals = the final cumulative level (== target); the line is the jagged climb to it.
    totals = {
        "revenue_cents": revenue[-1] if revenue else 0,
        "users": users[-1] if users else 0,
        "usage_events": usage[-1] if usage else 0,
        "pageviews": pageviews[-1] if pageviews else 0,
        "visits": visits[-1] if visits else 0,
    }
    d = float(t["delta"])
    previous_totals = {k: int(round(v / (1.0 + d))) for k, v in totals.items()}
    return revenue, users, usage, pageviews, visits, totals, previous_totals
