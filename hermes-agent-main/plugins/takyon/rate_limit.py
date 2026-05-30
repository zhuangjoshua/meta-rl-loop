"""Per-user fixed-window rate limiter for the control-plane boundary (Phase 3).

The opaque API key is the entire per-user surface (mediationplan.md), so abuse
control lives at the same grain: one counter per (user, time-window). A request is
counted into the window it lands in; once a user crosses `limit` within a
`window_seconds` window, further requests in that window are refused until it rolls.

Fixed window, not a token bucket, on purpose: the whole decision is ONE atomic SQL
statement — an upsert that increments and returns the new count in a single round
trip — so concurrent requests from the same user can never race past the cap (no
read-then-write gap to oversell). The window boundary is epoch-aligned in the
DATABASE (`floor(epoch/w)*w`), so every app process and every connection agrees on
which window "now" belongs to without trusting any per-process clock; the only
app-side clock use is the advisory Retry-After hint, where a second of skew is
harmless.

Backend-agnostic, same house style as billing.py / custody.py: takes a psycopg
connection, opens its own `conn.transaction()` per op (correct whether the
connection is autocommit or not), imports no psycopg, reads no global config.
"""

from __future__ import annotations

import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta


@dataclass(frozen=True)
class RateLimitResult:
    """Outcome of one `check_rate_limit` call. `count` is this user's request total
    in the current window AFTER counting this request; `allowed` is `count <= limit`.
    `retry_after_seconds` is how long until the window rolls (for a 429 Retry-After
    header) — only meaningful when `allowed` is False."""

    allowed: bool
    limit: int
    remaining: int
    count: int
    reset_at: datetime
    retry_after_seconds: int
    window_seconds: int


def check_rate_limit(
    conn, user_id: str, *, limit: int, window_seconds: int
) -> RateLimitResult:
    """Count one request for `user_id` and report whether it is within `limit` for the
    current `window_seconds` window. Atomic: a single upsert increments the counter and
    returns the new value, so parallel requests cannot oversell the cap. The window is
    aligned to wall-clock epoch boundaries in the database, so all callers share one
    notion of the current window regardless of process or connection."""
    if limit <= 0:
        raise ValueError("limit must be > 0")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")
    with conn.transaction():
        row = conn.execute(
            "insert into api_rate_limits (user_id, window_start, request_count) "
            "values (%s, to_timestamp(floor(extract(epoch from now()) / %s) * %s), 1) "
            "on conflict (user_id, window_start) "
            "do update set request_count = api_rate_limits.request_count + 1 "
            "returning request_count, window_start",
            (user_id, window_seconds, window_seconds),
        ).fetchone()
    count = int(row[0])
    window_start: datetime = row[1]
    reset_at = window_start + timedelta(seconds=window_seconds)
    allowed = count <= limit
    remaining = max(0, limit - count)
    retry_after_seconds = max(0, math.ceil(reset_at.timestamp() - time.time()))
    return RateLimitResult(
        allowed=allowed,
        limit=limit,
        remaining=remaining,
        count=count,
        reset_at=reset_at,
        retry_after_seconds=retry_after_seconds,
        window_seconds=window_seconds,
    )


def prune_rate_limits(conn, *, older_than_seconds: int) -> int:
    """Delete rate-limit rows whose window ended more than `older_than_seconds` ago.
    Housekeeping only — expired windows never affect a decision (the counter for a new
    window starts fresh under a different window_start key), so pruning is purely to
    keep the table small. Returns the number of rows removed."""
    if older_than_seconds < 0:
        raise ValueError("older_than_seconds must be >= 0")
    with conn.transaction():
        cur = conn.execute(
            "delete from api_rate_limits "
            "where window_start < now() - make_interval(secs => %s)",
            (older_than_seconds,),
        )
        return cur.rowcount
