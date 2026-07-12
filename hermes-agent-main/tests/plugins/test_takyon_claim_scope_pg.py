"""ClaimScope reservation proofs (modularization Stage 2, UC1) — successor to the Stage-0
``test_takyon_claim_affinity_characterization.py``.

Stage 2 replaced the payload-hint affinity (``preferred_worker_id_prefix`` + LIKE + grace
window) with first-class reservations: indexed columns on ``jobs`` stamped from a
:class:`~plugins.takyon.claim_scope.ClaimScope` at enqueue, a pool registry
(``worker_pools``) with a heartbeated lease, and one predicate in ``jobs.claim_one``.
This file both RE-PINS the five Stage-0 semantic invariants on the new mechanism (so the
cutover is provably behavior-equivalent where it claims to be) and proves the new UC1
guarantees (strict ownership, exclusive pools, spill-not-strand, renewal-on-requeue).

Rig posture (same as its predecessor): direct row inserts, no safebox/billing path, no
sleeps (windows move by back-dating timestamps / lapsing leases). Runs on the plain PG rig
(``TAKYON_TEST_PG_DSN``); skips cleanly without it.

NOTE (plan R1): the full reclaim-while-billing-reservation-open ordering test requires the
billing rail, which the local rig cannot run (known gap, Stage-3 dev-DB work). The billing
reconcile itself is unchanged by Stage 2 (``run_one`` step 3); what IS rig-provable here is
that requeues never touch ``reserved_billing_entry_id`` (test below).
"""

from __future__ import annotations

import os
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import claim_scope as cs  # noqa: E402
from plugins.takyon import jobs  # noqa: E402
from plugins.takyon.claim_scope import ClaimScope  # noqa: E402


def _provision_direct(conn, owner_id: str | None = None) -> str:
    uid = owner_id or str(uuid.uuid4())
    if owner_id is None:
        conn.execute(
            "insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}")
        )
    slug = f"scope-{uuid.uuid4().hex[:8]}"
    conn.execute(
        "insert into businesses (slug, name, goal, status, mode, owner_user_id) "
        "values (%s, %s, 'g', 'active', 'test', %s)",
        (slug, slug, uid),
    )
    return slug


def _mk_owner(conn) -> str:
    uid = str(uuid.uuid4())
    conn.execute(
        "insert into users (id, auth0_sub) values (%s, %s)", (uid, f"auth0|{uuid.uuid4().hex}")
    )
    return uid


def _expire_reservation(conn, job_id: str) -> None:
    conn.execute(
        "update jobs set reservation_expires_at = now() - interval '1 second' where id = %s",
        (job_id,),
    )


def _lapse_pool_lease(conn, pool_id: str) -> None:
    conn.execute(
        "update worker_pools set lease_expires_at = now() - interval '1 second' where pool_id = %s",
        (pool_id,),
    )


def _enq(conn, slug: str, scope: ClaimScope | None, **kw):
    return jobs.enqueue(
        conn,
        slug,
        "ceo_wake",
        idempotency_key=f"k-{uuid.uuid4().hex}",
        payload=kw.pop("payload", {"a": 1}),
        claim_scope=scope,
        **kw,
    )


_A = "pool-a-host-111"
_B = "pool-b-host-222"
_AFTER = ClaimScope(pool_id=_A, fallback="after_lease", lease_seconds=120)


@pytest.fixture(autouse=True)
def _register_claiming_pools(pg_conn):
    """Every actual claimant is a registered release-advertising pool after migration 0086."""
    cs.register_pool(pg_conn, pool_id=_A, lease_seconds=600)
    cs.register_pool(pg_conn, pool_id=_B, lease_seconds=600)


# ── re-pins of the five Stage-0 invariants, on the new mechanism ─────────────────────────


def test_reserved_pool_claims_own_job_in_window(pg_conn):
    slug = _provision_direct(pg_conn)
    j = _enq(pg_conn, slug, _AFTER)
    got = jobs.claim_one(pg_conn, worker_id=f"{_A}-1", kinds=["ceo_wake"], claim_pool_id=_A)
    assert got is not None and got.id == j.id and got.status == "running"


def test_after_lease_other_pool_blocked_within_window(pg_conn):
    slug = _provision_direct(pg_conn)
    _enq(pg_conn, slug, _AFTER)
    got = jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B)
    assert got is None


def test_after_lease_spills_to_anyone_after_window(pg_conn):
    slug = _provision_direct(pg_conn)
    j = _enq(pg_conn, slug, _AFTER)
    _expire_reservation(pg_conn, j.id)
    got = jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B)
    assert got is not None and got.id == j.id


def test_unreserved_job_claimable_by_any_pool(pg_conn):
    slug = _provision_direct(pg_conn)
    j = _enq(pg_conn, slug, None)
    cs.register_pool(pg_conn, pool_id="whatever", lease_seconds=600)
    got = jobs.claim_one(
        pg_conn, worker_id="any-worker-at-all", kinds=["ceo_wake"], claim_pool_id="whatever"
    )
    assert got is not None and got.id == j.id


def test_poolless_worker_claims_unreserved_but_not_reserved(pg_conn):
    """A worker with NO pool identity (claim_pool_id=None → '') keeps today's behavior for
    unreserved jobs and never matches a reservation."""
    slug = _provision_direct(pg_conn)
    _enq(pg_conn, slug, _AFTER)
    assert jobs.claim_one(pg_conn, worker_id="legacy", kinds=["ceo_wake"]) is None
    j2 = _enq(pg_conn, slug + "x" if False else _provision_direct(pg_conn), None)
    got = jobs.claim_one(pg_conn, worker_id="legacy", kinds=["ceo_wake"])
    assert got is not None and got.id == j2.id


# ── strict ownership + spill-not-strand (the UC1 keystone) ───────────────────────────────


def test_strict_blocked_while_owner_pool_lease_alive(pg_conn):
    owner = _mk_owner(pg_conn)
    slug = _provision_direct(pg_conn, owner)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, lease_seconds=600)
    _enq(pg_conn, slug, ClaimScope(pool_id=_A, fallback="strict"))
    got = jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B)
    assert got is None  # no time escape: strict holds while the owner pool lives


def test_strict_spills_when_owner_pool_lease_lapses(pg_conn):
    owner = _mk_owner(pg_conn)
    slug = _provision_direct(pg_conn, owner)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, lease_seconds=600)
    j = _enq(pg_conn, slug, ClaimScope(pool_id=_A, fallback="strict"))
    _lapse_pool_lease(pg_conn, _A)  # the session died; heartbeats stopped
    got = jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B)
    assert got is not None and got.id == j.id  # SPILLS, never strands


def test_strict_spills_when_owner_pool_decommissioned(pg_conn):
    owner = _mk_owner(pg_conn)
    slug = _provision_direct(pg_conn, owner)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, lease_seconds=600)
    j = _enq(pg_conn, slug, ClaimScope(pool_id=_A, fallback="strict"))
    cs.decommission_pool(pg_conn, _A)
    got = jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B)
    assert got is not None and got.id == j.id


def test_strict_unregistered_pool_counts_as_dead(pg_conn):
    """A strict reservation for a pool that never registered must not strand the job."""
    slug = _provision_direct(pg_conn)
    j = _enq(pg_conn, slug, ClaimScope(pool_id="ghost-pool", fallback="strict"))
    got = jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B)
    assert got is not None and got.id == j.id


def test_two_exclusive_scopes_of_same_owner_never_cross_claim(pg_conn):
    """THE UC1 acceptance pin: two concurrent sessions of the SAME operator each own their
    jobs; neither pool can drain the other's, in either direction, while both live."""
    owner = _mk_owner(pg_conn)
    slug_a = _provision_direct(pg_conn, owner)
    slug_b = _provision_direct(pg_conn, owner)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, lease_seconds=600)
    cs.register_pool(pg_conn, pool_id=_B, owner_user_id=owner, exclusive=True, lease_seconds=600)
    ja = _enq(pg_conn, slug_a, ClaimScope(pool_id=_A, owner_user_id=owner, fallback="strict"))
    jb = _enq(pg_conn, slug_b, ClaimScope(pool_id=_B, owner_user_id=owner, fallback="strict"))

    got_b = jobs.claim_one(
        pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], owner_user_id=owner,
        claim_pool_id=_B, exclusive_pool=True,
    )
    assert got_b is not None and got_b.id == jb.id  # B gets ITS job, never A's

    got_a = jobs.claim_one(
        pg_conn, worker_id=f"{_A}-1", kinds=["ceo_wake"], owner_user_id=owner,
        claim_pool_id=_A, exclusive_pool=True,
    )
    assert got_a is not None and got_a.id == ja.id  # A gets ITS job

    assert (
        jobs.claim_one(
            pg_conn, worker_id=f"{_A}-2", kinds=["ceo_wake"], owner_user_id=owner,
            claim_pool_id=_A, exclusive_pool=True,
        )
        is None
    )  # nothing of B's is visible to A


def test_exclusive_pool_never_claims_unreserved_work(pg_conn):
    owner = _mk_owner(pg_conn)
    slug = _provision_direct(pg_conn, owner)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, lease_seconds=600)
    _enq(pg_conn, slug, None)  # unreserved
    got = jobs.claim_one(
        pg_conn, worker_id=f"{_A}-1", kinds=["ceo_wake"], claim_pool_id=_A, exclusive_pool=True
    )
    assert got is None  # an exclusive session pool does nobody else's work


def test_exclusive_requires_pool_identity(pg_conn):
    with pytest.raises(ValueError):
        jobs.claim_one(pg_conn, worker_id="w", exclusive_pool=True)


# ── enqueue stamping, idempotent replay, renewal ─────────────────────────────────────────


def test_enqueue_stamps_reservation_columns_and_replay_does_not_restamp(pg_conn):
    slug = _provision_direct(pg_conn)
    key = f"k-{uuid.uuid4().hex}"
    j = jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key=key, payload={},
        claim_scope=ClaimScope(pool_id=_A, fallback="after_lease", lease_seconds=120),
    )
    row = pg_conn.execute(
        "select reserved_pool_id, reservation_policy, reservation_lease_seconds,"
        " reservation_expires_at from jobs where id = %s",
        (j.id,),
    ).fetchone()
    assert row[0] == _A and row[1] == "after_lease" and float(row[2]) == 120.0
    assert row[3] is not None

    # Idempotent replay with a DIFFERENT scope: one effect — the original reservation stands.
    j2 = jobs.enqueue(
        pg_conn, slug, "ceo_wake", idempotency_key=key, payload={},
        claim_scope=ClaimScope(pool_id=_B, fallback="strict"),
    )
    assert j2.id == j.id
    row2 = pg_conn.execute(
        "select reserved_pool_id, reservation_policy from jobs where id = %s", (j.id,)
    ).fetchone()
    assert row2[0] == _A and row2[1] == "after_lease"


def test_requeue_renews_after_lease_window_and_leaves_billing_key_alone(pg_conn):
    """f899da41 parity: a healthy local retry renews the first-claim window instead of
    spilling to a sibling; and no requeue path touches reserved_billing_entry_id."""
    slug = _provision_direct(pg_conn)
    j = _enq(pg_conn, slug, _AFTER, max_attempts=3)
    got = jobs.claim_one(pg_conn, worker_id=f"{_A}-1", kinds=["ceo_wake"], claim_pool_id=_A)
    assert got is not None
    pg_conn.execute(
        "update jobs set reservation_expires_at = now() + interval '1 second',"
        " reserved_billing_entry_id = 'sentinel-hold' where id = %s",
        (j.id,),
    )
    assert jobs.fail(pg_conn, j.id, error="boom", retryable=True) == "requeued"
    row = pg_conn.execute(
        "select status, reservation_expires_at > now() + interval '60 second',"
        " reserved_billing_entry_id from jobs where id = %s",
        (j.id,),
    ).fetchone()
    assert row[0] == "queued"
    assert row[1] is True  # renewed to ~now()+120s, not the stale +1s
    assert row[2] == "sentinel-hold"  # billing key untouched by the requeue rail


def test_requeue_stale_renews_after_lease_window(pg_conn):
    slug = _provision_direct(pg_conn)
    j = _enq(pg_conn, slug, _AFTER, max_attempts=3)
    got = jobs.claim_one(pg_conn, worker_id=f"{_A}-1", kinds=["ceo_wake"], claim_pool_id=_A)
    assert got is not None
    pg_conn.execute(
        "update jobs set locked_at = now() - interval '2 hours',"
        " reservation_expires_at = now() - interval '1 hour' where id = %s",
        (j.id,),
    )
    assert jobs.requeue_stale(pg_conn, older_than_seconds=900) >= 1
    row = pg_conn.execute(
        "select status, reservation_expires_at > now() from jobs where id = %s", (j.id,)
    ).fetchone()
    assert row[0] == "queued" and row[1] is True


# ── pool registry lifecycle ──────────────────────────────────────────────────────────────


def test_pool_registry_lifecycle(pg_conn):
    owner = _mk_owner(pg_conn)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, concurrency=4)
    pool = cs.get_pool(pg_conn, _A)
    assert pool is not None and pool["status"] == "active" and pool["exclusive"] is True
    assert cs.heartbeat_pool(pg_conn, _A) is True
    cs.begin_drain(pg_conn, _A)
    assert cs.get_pool(pg_conn, _A)["status"] == "draining"
    # draining still owns strict reservations (liveness = lease, not status) — claim blocked:
    slug = _provision_direct(pg_conn, owner)
    _enq(pg_conn, slug, ClaimScope(pool_id=_A, fallback="strict"))
    assert (
        jobs.claim_one(pg_conn, worker_id=f"{_B}-1", kinds=["ceo_wake"], claim_pool_id=_B) is None
    )
    cs.decommission_pool(pg_conn, _A)
    assert cs.get_pool(pg_conn, _A)["status"] == "decommissioned"
    assert cs.heartbeat_pool(pg_conn, _A) is False  # a dead pool must not resurrect via heartbeat
    _lapse = None
    cs.register_pool(pg_conn, pool_id=_B, owner_user_id=owner, lease_seconds=600)
    _lapse_pool_lease(pg_conn, _B)
    assert cs.reap_lost_pools(pg_conn) >= 1
    assert cs.get_pool(pg_conn, _B)["status"] == "lost"


def test_claim_one_param_alignment_with_all_gates_active(pg_conn, monkeypatch):
    """Every optional SQL gate at once (kinds + min_queue_age + owner + exclusive): pins the
    positional-parameter alignment of the concatenated claim SQL."""
    owner = _mk_owner(pg_conn)
    slug = _provision_direct(pg_conn, owner)
    cs.register_pool(pg_conn, pool_id=_A, owner_user_id=owner, exclusive=True, lease_seconds=600)
    j = _enq(pg_conn, slug, ClaimScope(pool_id=_A, owner_user_id=owner, fallback="strict"))
    pg_conn.execute(
        "update jobs set created_at = created_at - interval '60 second' where id = %s", (j.id,)
    )
    monkeypatch.setenv("TAKYON_WORKER_MIN_QUEUE_AGE_SECONDS", "5")
    got = jobs.claim_one(
        pg_conn, worker_id=f"{_A}-1", kinds=["ceo_wake"], owner_user_id=owner,
        claim_pool_id=_A, exclusive_pool=True,
    )
    assert got is not None and got.id == j.id
    # And the same shape with a WRONG owner claims nothing (owner param really lands).
    j2 = _enq(pg_conn, _provision_direct(pg_conn, owner), ClaimScope(pool_id=_A, owner_user_id=owner, fallback="strict"))
    pg_conn.execute(
        "update jobs set created_at = created_at - interval '60 second' where id = %s", (j2.id,)
    )
    assert (
        jobs.claim_one(
            pg_conn, worker_id=f"{_A}-2", kinds=["ceo_wake"], owner_user_id=str(uuid.uuid4()),
            claim_pool_id=_A, exclusive_pool=True,
        )
        is None
    )


def test_release_fence_allows_same_release_pool_and_rejects_mismatch(pg_conn):
    owner = _mk_owner(pg_conn)
    slug = _provision_direct(pg_conn, owner)
    release_a = pg_conn.execute("select takyon_get_worker_active_release()").fetchone()[0]
    release_b = "b" * 40
    cs.register_pool(
        pg_conn,
        pool_id=_A,
        owner_user_id=owner,
        release_sha=release_a,
        lease_seconds=600,
    )
    job = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_wake",
        idempotency_key=f"release-{uuid.uuid4().hex}",
        required_release_sha=release_a,
    )

    with pytest.raises(RuntimeError, match="does not match this process release"):
        jobs.claim_one(
            pg_conn,
            worker_id="wrong-release",
            claim_pool_id=_A,
            worker_release_sha=release_b,
        )
    claimed = jobs.claim_one(
        pg_conn,
        worker_id="right-release",
        claim_pool_id=_A,
        worker_release_sha=release_a,
    )
    assert claimed is not None and claimed.id == job.id
    assert claimed.required_release_sha == release_a
    assert claimed.claimed_release_sha == release_a
    assert claimed.claimed_pool_id == _A


def test_release_fence_keeps_different_business_claims_parallel(pg_conn):
    slug_a = _provision_direct(pg_conn)
    slug_b = _provision_direct(pg_conn)
    job_a = _enq(pg_conn, slug_a, ClaimScope(pool_id=_A, fallback="strict"))
    job_b = _enq(pg_conn, slug_b, ClaimScope(pool_id=_B, fallback="strict"))
    dsn = psycopg.conninfo.make_conninfo(
        os.environ["TAKYON_TEST_PG_DSN"],
        dbname=pg_conn.info.dbname,
    )

    with (
        psycopg.connect(dsn, autocommit=False) as first,
        psycopg.connect(dsn, autocommit=False) as second,
    ):
        first.execute("set local statement_timeout = '2s'")
        second.execute("set local statement_timeout = '2s'")
        claimed_a = jobs.claim_one(
            first,
            worker_id=f"{_A}-parallel",
            claim_pool_id=_A,
            exclusive_pool=True,
        )
        # first retains its transaction-level release-fence share lock while this claim proceeds.
        claimed_b = jobs.claim_one(
            second,
            worker_id=f"{_B}-parallel",
            claim_pool_id=_B,
            exclusive_pool=True,
        )
        assert claimed_a is not None and claimed_a.id == job_a.id
        assert claimed_b is not None and claimed_b.id == job_b.id


def test_database_trigger_rejects_pre_fence_worker_claim(pg_conn):
    slug = _provision_direct(pg_conn)
    release = pg_conn.execute("select takyon_get_worker_active_release()").fetchone()[0]
    job = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_wake",
        idempotency_key=f"old-worker-{uuid.uuid4().hex}",
        required_release_sha=release,
    )

    with pytest.raises(psycopg.errors.CheckViolation, match="release claim mismatch"):
        # Exact queued->running UPDATE used by code predating migration 0086: it cannot supply the
        # required release evidence, so the database—not cooperative filtering—refuses the claim.
        pg_conn.execute(
            "update jobs set status='running', locked_by='stale-worker', locked_at=now() "
            "where id=%s",
            (job.id,),
        )
    assert jobs.get_job(pg_conn, job.id).status == "queued"


def test_active_release_fence_rejects_mismatched_enqueue(pg_conn):
    slug = _provision_direct(pg_conn)
    active = pg_conn.execute("select takyon_get_worker_active_release()").fetchone()[0]
    mismatch = "d" * 40 if active != "d" * 40 else "c" * 40

    with pytest.raises(RuntimeError, match="does not match this process release"):
        jobs.enqueue(
            pg_conn,
            slug,
            "ceo_wake",
            idempotency_key=f"mismatch-{uuid.uuid4().hex}",
            required_release_sha=mismatch,
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="is not the active release"):
        pg_conn.execute(
            "insert into jobs (business_slug, kind, idempotency_key, payload, required_release_sha) "
            "values (%s, 'ceo_wake', %s, '{}'::jsonb, %s)",
            (slug, f"raw-mismatch-{uuid.uuid4().hex}", mismatch),
        )


def test_release_triggers_ignore_temp_table_shadows(pg_conn):
    slug = _provision_direct(pg_conn)
    active = pg_conn.execute("select takyon_get_worker_active_release()").fetchone()[0]
    mismatch = "d" * 40 if active != "d" * 40 else "c" * 40
    job = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_wake",
        idempotency_key=f"shadow-claim-{uuid.uuid4().hex}",
        required_release_sha=active,
    )

    pg_conn.execute(
        "create temporary table worker_release_fence "
        "(singleton boolean, active_release_sha text)"
    )
    pg_conn.execute(
        "insert into pg_temp.worker_release_fence values (true, %s)",
        (mismatch,),
    )
    pg_conn.execute(
        "create temporary table worker_pools "
        "(pool_id text, release_sha text, status text, lease_expires_at timestamptz)"
    )
    pg_conn.execute(
        "insert into pg_temp.worker_pools values "
        "('shadow-pool', %s, 'active', now() + interval '1 hour')",
        (active,),
    )
    pg_conn.execute("set search_path = pg_temp, public")

    with pytest.raises(psycopg.errors.CheckViolation, match="is not the active release"):
        pg_conn.execute(
            "insert into public.jobs "
            "(business_slug, kind, idempotency_key, payload, required_release_sha) "
            "values (%s, 'ceo_wake', %s, '{}'::jsonb, %s)",
            (slug, f"shadow-insert-{uuid.uuid4().hex}", mismatch),
        )
    with pytest.raises(psycopg.errors.CheckViolation, match="is not live on required release"):
        pg_conn.execute(
            "update public.jobs set status='running', locked_by='shadow-worker', locked_at=now(), "
            "claimed_release_sha=%s, claimed_pool_id='shadow-pool' where id=%s",
            (active, job.id),
        )

    configs = pg_conn.execute(
        "select p.proname, p.prosecdef, p.proconfig "
        "from pg_catalog.pg_proc p "
        "join pg_catalog.pg_namespace n on n.oid = p.pronamespace "
        "where n.nspname = 'public' and p.proname = any(%s)",
        (
            [
                "takyon_enforce_job_release_insert",
                "takyon_enforce_job_release_claim",
                "takyon_enforce_worker_pool_release",
            ],
        ),
    ).fetchall()
    assert len(configs) == 3
    assert all(
        security_definer and "search_path=pg_catalog, public, pg_temp" in (settings or [])
        for _name, security_definer, settings in configs
    )


def test_first_cutover_activation_and_restore_repin_only_untouched_jobs(pg_conn):
    slug = _provision_direct(pg_conn)
    current = pg_conn.execute("select takyon_get_worker_active_release()").fetchone()[0]
    restored = pg_conn.execute(
        "select takyon_restore_worker_legacy_release(%s)",
        (current,),
    ).fetchone()[0]
    assert restored["active_release_sha"] == cs.UNPINNED_RELEASE_SHA
    legacy_job_id = pg_conn.execute(
        "insert into jobs (business_slug, kind, idempotency_key, payload) "
        "values (%s, 'ceo_wake', %s, '{}'::jsonb) returning id",
        (slug, f"legacy-{uuid.uuid4().hex}"),
    ).fetchone()[0]
    target = "a" * 40
    activated = pg_conn.execute(
        "select takyon_activate_worker_release(%s)",
        (target,),
    ).fetchone()[0]
    assert activated["previous_release_sha"] == cs.UNPINNED_RELEASE_SHA
    assert activated["active_release_sha"] == target
    assert activated["repinned_jobs"] == 1
    assert activated["drained_pools"] >= 0
    assert pg_conn.execute(
        "select required_release_sha from jobs where id=%s",
        (legacy_job_id,),
    ).fetchone()[0] == target

    rolled_back = pg_conn.execute(
        "select takyon_restore_worker_legacy_release(%s)",
        (target,),
    ).fetchone()[0]
    assert rolled_back["repinned_jobs"] == 1
    assert pg_conn.execute(
        "select required_release_sha from jobs where id=%s",
        (legacy_job_id,),
    ).fetchone()[0] == cs.UNPINNED_RELEASE_SHA


def test_release_activation_repins_untouched_work_and_drains_old_pools(pg_conn):
    slug = _provision_direct(pg_conn)
    previous = pg_conn.execute("select takyon_get_worker_active_release()").fetchone()[0]
    cs.register_pool(pg_conn, pool_id=_A, release_sha=previous, lease_seconds=600)
    cs.register_pool(pg_conn, pool_id=_B, release_sha=previous, lease_seconds=600)
    manual_pool = "pool-manual-drain"
    target_pool = "pool-target-release"
    cs.register_pool(pg_conn, pool_id=manual_pool, release_sha=previous, lease_seconds=600)
    cs.begin_drain(pg_conn, manual_pool)
    _lapse_pool_lease(pg_conn, _B)
    job = jobs.enqueue(
        pg_conn,
        slug,
        "ceo_wake",
        idempotency_key=f"upgrade-{uuid.uuid4().hex}",
    )
    target = "a" * 40 if previous != "a" * 40 else "b" * 40

    receipt = pg_conn.execute(
        "select takyon_activate_worker_release(%s)",
        (target,),
    ).fetchone()[0]

    assert receipt["previous_release_sha"] == previous
    assert receipt["repinned_jobs"] >= 1
    assert pg_conn.execute(
        "select required_release_sha from jobs where id=%s",
        (job.id,),
    ).fetchone()[0] == target
    assert pg_conn.execute(
        "select status from public.worker_pools where pool_id=%s",
        (_A,),
    ).fetchone()[0] == "draining"
    assert pg_conn.execute(
        "select release_fence_drained_by_sha from public.worker_pools where pool_id=%s",
        (_A,),
    ).fetchone()[0] == target
    assert pg_conn.execute(
        "select release_fence_drained_by_sha from public.worker_pools where pool_id=%s",
        (manual_pool,),
    ).fetchone()[0] is None
    with pytest.raises(psycopg.errors.CheckViolation, match="is not the active release"):
        cs.register_pool(pg_conn, pool_id=_A, release_sha=previous, lease_seconds=600)
    pg_conn.execute(
        "insert into public.worker_pools "
        "(pool_id, hostname, status, release_sha, lease_expires_at) "
        "values (%s, 'target-host', 'active', %s, now() + interval '10 minutes')",
        (target_pool, target),
    )

    restored = pg_conn.execute(
        "select takyon_restore_worker_release(%s, %s)",
        (target, previous),
    ).fetchone()[0]
    assert restored["active_release_sha"] == previous
    assert restored["drained_pools"] == 1
    assert restored["reactivated_pools"] == 1
    assert pg_conn.execute(
        "select required_release_sha from jobs where id=%s",
        (job.id,),
    ).fetchone()[0] == previous
    pool_states = {
        pool_id: (status, marker)
        for pool_id, status, marker in pg_conn.execute(
            "select pool_id, status, release_fence_drained_by_sha "
            "from public.worker_pools where pool_id = any(%s)",
            ([_A, _B, manual_pool, target_pool],),
        ).fetchall()
    }
    assert pool_states[_A] == ("active", None)
    assert pool_states[_B] == ("draining", None)
    assert pool_states[manual_pool] == ("draining", None)
    assert pool_states[target_pool] == ("draining", previous)
    claimed = jobs.claim_one(
        pg_conn,
        worker_id=f"{_A}-restored",
        claim_pool_id=_A,
        worker_release_sha=previous,
    )
    assert claimed is not None and claimed.id == job.id
