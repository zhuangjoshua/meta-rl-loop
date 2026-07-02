"""Characterization of ``jobs.claim_one`` preferred-worker AFFINITY (modularization Stage 0 → Stage 2).

Stage 2 replaces the current stringly-typed affinity (a ``preferred_worker_id_prefix`` payload hint
matched by SQL ``LIKE`` inside a time-boxed grace window) with a first-class ``ClaimScope``
reservation. This test pins the EXACT current behavior so the Stage-2 rewrite can be proven
behavior-equivalent (for the in-window/exact-prefix/expiry cases) rather than silently changing when
a job leaks to a sibling worker.

Deliberately NARROW and self-contained: it provisions a user + business with DIRECT row inserts
(no ``provision_user_on_first_login`` → safebox billing path), so it runs on the plain Postgres rig
(``TAKYON_TEST_PG_DSN`` only) with NO safebox authority. That path is a known local-rig gap for the
billing-dependent jobs suite; affinity does not need it. Time-window expiry is simulated by
back-dating ``created_at``/``updated_at`` (the claim SQL keys on ``greatest(created_at, updated_at)``
per commit f899da41), so no test sleeps.

Pinned invariants (verified against jobs.claim_one on 2026-07-02):
  1. A hint-bearing job is claimable by a worker whose id has the prefix (``LIKE prefix%``).
  2. It is NOT claimable by a non-matching worker WHILE the grace window is open.
  3. It IS claimable by ANY worker once the window (``preferred_worker_claim_seconds``) has elapsed.
  4. A worker whose id equals the prefix with the trailing ``-`` stripped (the "base prefix", added
     in commit 6bc61762 so concurrency>1 ``{id}-N`` threads still match) is treated as matching.
  5. A job with NO hint is claimable by any worker immediately.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import jobs  # noqa: E402


def _provision_direct(conn) -> str:
    """Insert a user + a business they own via direct SQL (superuser conn, no billing/safebox path).
    Returns the business slug. This is the affinity-only shortcut around _provision_business."""
    uid = str(uuid.uuid4())
    conn.execute("insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}"))
    slug = f"aff-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'g', 'active', 'test', %s)",
        (slug, slug, uid),
    )
    return slug


def _backdate(conn, job_id: str, seconds: int) -> None:
    """Move a job's created_at AND updated_at back by ``seconds`` to simulate window elapse."""
    conn.execute(
        "update jobs set created_at = created_at - make_interval(secs => %s), "
        "updated_at = updated_at - make_interval(secs => %s) where id = %s",
        (seconds, seconds, job_id),
    )


_PREFIX = "mac-operator-host-123-"
_HINT = {"preferred_worker_id_prefix": _PREFIX, "preferred_worker_claim_seconds": 120}


def test_matching_prefix_worker_claims_in_window(pg_conn):
    slug = _provision_direct(pg_conn)
    j = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key=f"k-{uuid.uuid4().hex}", payload=dict(_HINT))
    got = jobs.claim_one(pg_conn, worker_id=f"{_PREFIX}1", kinds=["ceo_wake"])
    assert got is not None and got.id == j.id
    assert got.status == "running"


def test_nonmatching_worker_blocked_within_window(pg_conn):
    slug = _provision_direct(pg_conn)
    jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key=f"k-{uuid.uuid4().hex}", payload=dict(_HINT))
    # A sibling worker on a DIFFERENT machine must NOT be able to claim while the window is open.
    got = jobs.claim_one(pg_conn, worker_id="mac-operator-other-999-1", kinds=["ceo_wake"])
    assert got is None


def test_nonmatching_worker_claims_after_window_elapses(pg_conn):
    slug = _provision_direct(pg_conn)
    j = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key=f"k-{uuid.uuid4().hex}", payload=dict(_HINT))
    _backdate(pg_conn, j.id, 200)  # window is 120s; 200s ago => elapsed
    got = jobs.claim_one(pg_conn, worker_id="mac-operator-other-999-1", kinds=["ceo_wake"])
    assert got is not None and got.id == j.id  # affinity has lapsed; anyone may claim


def test_base_prefix_exact_match_is_treated_as_matching(pg_conn):
    slug = _provision_direct(pg_conn)
    j = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key=f"k-{uuid.uuid4().hex}", payload=dict(_HINT))
    # Worker id == prefix with the trailing '-' stripped (commit 6bc61762): still matches, in window.
    got = jobs.claim_one(pg_conn, worker_id=_PREFIX.rstrip("-"), kinds=["ceo_wake"])
    assert got is not None and got.id == j.id


def test_unhinted_job_claimable_by_any_worker(pg_conn):
    slug = _provision_direct(pg_conn)
    j = jobs.enqueue(pg_conn, slug, "ceo_wake", idempotency_key=f"k-{uuid.uuid4().hex}", payload={"a": 1})
    got = jobs.claim_one(pg_conn, worker_id="any-worker-at-all", kinds=["ceo_wake"])
    assert got is not None and got.id == j.id
