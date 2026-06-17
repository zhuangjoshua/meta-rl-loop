"""Channel-bucket credit stamping + per-channel usage read — grouped E2E (Postgres).

Covers the "Allocate credits from channel to appropriate channel" and "User channels credit tracking"
cards: a creative-credit ledger entry stamped with channel metadata must be routed to the correct
per-channel bucket ("x" / "meta" / "reddit"), and the per-channel usage read must report each
channel's used/reserved credits independently. Drives the REAL ``business_credits`` ledger and the
REAL ``core`` bucket-routing helpers against a throwaway Postgres database (no store-level
``DATABASE_URL`` resolution, so it is independent of the operator-store DSN harness).

Skips unless psycopg is importable and TAKYON_TEST_PG_DSN is set (see tests/conftest.py).
"""
from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import business_credits, core  # noqa: E402
from plugins.takyon.control_plane import provision_user_on_first_login  # noqa: E402


def _business(conn) -> str:
    owner_id, _c, _r = provision_user_on_first_login(conn, f"auth0|{uuid.uuid4().hex}")
    slug = f"biz-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, owner_user_id) values (%s, %s, %s)",
        (slug, "Acme", owner_id),
    )
    return slug


def _usage(pg_conn, slug):
    """Per-channel usage as the dashboard/CEO read it, via the REAL core router. The reader speaks
    sqlite-style ``?`` placeholders, so wrap the raw psycopg conn in the store's _PGConn adapter."""
    return core._creative_credit_channel_usage_from_conn(core._PGConn(pg_conn), slug)


# ── bucket normalization (card "Allocate credits", crit 1) ─────────────────────────


def test_bucket_normalization_routes_known_aliases():
    norm = core._normalize_creative_credit_bucket
    assert norm("x") == "x"
    assert norm("twitter") == "x"
    assert norm("X-Ads") == "x"
    assert norm("meta") == "meta"
    assert norm("facebook") == "meta"
    assert norm("instagram") == "meta"
    assert norm("reddit") == "reddit"
    # Unknown / empty inputs route to the unbucketed "" bucket, never a wrong channel.
    assert norm("linkedin") == ""
    assert norm("") == ""
    assert norm(None) == ""


def test_bucket_from_metadata_prefers_explicit_then_channel():
    from_meta = core._creative_credit_bucket_from_metadata
    assert from_meta({"budget_bucket": "x"}) == "x"
    assert from_meta({"channel": "twitter"}) == "x"
    assert from_meta({"ad_metadata": {"channel": "meta"}}) == "meta"
    assert from_meta({"provider": "reddit"}) == "reddit"
    assert from_meta({"channel": "linkedin"}) == ""  # unknown channel ⇒ unbucketed


# ── stamping + per-channel read (cards "Allocate credits" + "User channels tracking") ──


def test_reserve_for_x_stamps_only_x_bucket(pg_conn):
    """A reservation tagged for X shows up in the 'x' channel's reserved_credits and NOWHERE else."""
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, f"grant:{slug}")
    business_credits.reserve_credits(
        pg_conn, slug, 3, f"x-res:{uuid.uuid4().hex}", metadata={"budget_bucket": "x"}
    )
    usage, unbucketed = _usage(pg_conn, slug)
    assert usage["x"]["reserved_credits"] == 3
    assert usage["x"]["used_credits"] == 0
    assert usage["meta"] == {"used_credits": 0, "reserved_credits": 0}
    assert usage["reddit"] == {"used_credits": 0, "reserved_credits": 0}
    assert unbucketed == 0


def test_commit_for_meta_decrements_only_meta_used(pg_conn):
    """After commit, only the channel the action targeted (meta) shows used_credits; reserved clears
    and other channels stay at zero."""
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, f"grant:{slug}")
    key = f"meta-res:{uuid.uuid4().hex}"
    business_credits.reserve_credits(pg_conn, slug, 2, key, metadata={"channel": "meta"})
    business_credits.commit_credits(pg_conn, key, actual_credits=2, metadata={"channel": "meta"})
    usage, unbucketed = _usage(pg_conn, slug)
    assert usage["meta"]["used_credits"] == 2
    assert usage["meta"]["reserved_credits"] == 0  # reservation finalized, no double-count
    assert usage["x"]["used_credits"] == 0 and usage["reddit"]["used_credits"] == 0
    assert unbucketed == 0


def test_three_channels_track_independently(pg_conn):
    """The headline card behavior: X / meta / reddit spends route to three separate buckets and the
    per-channel read reports each one correctly and independently."""
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 20, f"grant:{slug}")
    # X: commit 2 (used). meta: open reserve 3. reddit: commit 1 (used).
    xk = f"x:{uuid.uuid4().hex}"
    business_credits.reserve_credits(pg_conn, slug, 2, xk, metadata={"channel": "x"})
    business_credits.commit_credits(pg_conn, xk, actual_credits=2, metadata={"channel": "x"})
    business_credits.reserve_credits(
        pg_conn, slug, 3, f"m:{uuid.uuid4().hex}", metadata={"channel": "meta"}
    )
    rk = f"r:{uuid.uuid4().hex}"
    business_credits.reserve_credits(pg_conn, slug, 1, rk, metadata={"channel": "reddit"})
    business_credits.commit_credits(pg_conn, rk, actual_credits=1, metadata={"channel": "reddit"})

    usage, unbucketed = _usage(pg_conn, slug)
    assert usage["x"]["used_credits"] == 2 and usage["x"]["reserved_credits"] == 0
    assert usage["meta"]["reserved_credits"] == 3 and usage["meta"]["used_credits"] == 0
    assert usage["reddit"]["used_credits"] == 1 and usage["reddit"]["reserved_credits"] == 0
    assert unbucketed == 0


def test_unknown_channel_spend_is_unbucketed_not_misrouted(pg_conn):
    """A spend with no recognizable channel is counted as unbucketed — never silently charged to one
    of the real channel buckets (which would corrupt that channel's accounting)."""
    slug = _business(pg_conn)
    business_credits.grant_credits(pg_conn, slug, 10, f"grant:{slug}")
    key = f"u:{uuid.uuid4().hex}"
    business_credits.reserve_credits(pg_conn, slug, 4, key, metadata={"channel": "linkedin"})
    business_credits.commit_credits(pg_conn, key, actual_credits=4, metadata={"channel": "linkedin"})
    usage, unbucketed = _usage(pg_conn, slug)
    assert unbucketed == 4
    for bucket in ("x", "meta", "reddit"):
        assert usage[bucket] == {"used_credits": 0, "reserved_credits": 0}
