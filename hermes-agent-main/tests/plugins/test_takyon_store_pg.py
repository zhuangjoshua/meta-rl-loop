"""Postgres integration test for the TakyonStore connection seam — P8.2a of the SQLite kill
(mediationplan.md > Phase 8 store-seam finding, 2026-05-31).

Proves the operator store runs its SQLite-shaped SQL UNCHANGED on Postgres through the thin
``_PGConn`` translating wrapper — without rewriting a single one of the store's ~150
``conn.execute`` call sites:

  1. Backend default: with a DSN, ``store._connect()`` returns a ``_PGConn`` (psycopg),
     and it does NOT bootstrap schema — the migration runner owns DDL, so the wrapper's
     ``executescript`` (only reachable from the skipped ``_init_db``) fails loud if ever called.
  2. The ``?`` → ``%s`` translation, ``dict_row`` reads-by-name, and the one-atomic-transaction model
     (autocommit=False; the nested ``with conn:`` block in ``commit`` collapses into the single outer
     transaction) all work end-to-end across a multi-op commit that touches the operator tables 0011
     ported 1:1 (workspaces, ledger_entries, events, agent_runs, conversation_threads/_messages) plus
     the idempotency_keys spine — verified by reading the rows straight back through raw psycopg.
  3. ``control_states`` (ON CONFLICT(scope)) round-trips via a global-scope commit, which never touches
     the business CEO-cron path.
  4. Idempotency replay: re-committing the same key is one effect (second call returns the stored
     result, writes no new rows), proving the transaction commits exactly once.
  5. A stale ``TAKYON_DB_BACKEND=sqlite`` env is rejected loudly instead of silently reviving the
     retired local authority path.

P8.3 owner wiring is exercised at the bottom (``test_business_upsert_*`` / ``test_ensure_platform_owner_*``):
the store's ``business.upsert`` is the first write that needs an ``owner_user_id`` (PG
``businesses.owner_user_id`` is NOT NULL, 0001 spine). On Postgres the INSERT now resolves a single
platform owner READ-ONLY (``control_plane.resolve_platform_owner_id``, keyed by
``TAKYON_PLATFORM_OWNER_SUB``) so creating a business never mints/surfaces an API key as a side effect;
an unprovisioned owner blocks with a reason (invariant #8), and the explicit
``control_plane.ensure_platform_owner`` bootstrap is what mints the one-time key + opens the accounts.
The earlier operator-table tests still PRE-SEED an owned business by hand (``_seed_owned_business``)
because they exercise the store's OTHER operations, not creation.

P8.2b Stage A is exercised below (``test_operator_job_enqueue_*`` / ``test_maintenance_gc_*``): the
operator ``job.enqueue`` is NOT leaf delegation — it is a pure STORAGE retarget of the store's
work-request SQL to ``business_work_requests`` (0011's 1:1 text port of the SQLite operator ``jobs``),
ISOLATED from the 0010 ``jobs`` worker-plane execution queue (uuid/jsonb/SKIP-LOCKED). The tests prove
the store's job write/read/GC hit ``business_work_requests`` and never the 0010 queue.

P8.2b Stage B is exercised at the bottom (``test_app_*``): the ``app.*`` writes' PG tables
(0006/0007/0008) diverge from the store's SQLite SQL (jsonb ``metadata``, dropped
``stripe_payment_link_*``, the reserve→settle usage model with a NOT-NULL ``reservation_key``), so on
Postgres the store delegates each ``app.*`` op to its owning Phase-5 leaf (``app_identity`` /
``app_entitlements`` / ``app_usage``) rather than carry a second writer. Those tests drive the real
``store.commit`` path and assert BEHAVIOUR — rows landing through the leaf, the dropped columns gone,
warnings folded exactly once, the atomic budget cap, leaf reservation-key idempotency, and the
anti-fake-billing gate — not the leaves' exact error strings.

Real engine on real Postgres (never mocks). Skips unless psycopg is importable and TAKYON_TEST_PG_DSN
is set (the pg_store_dsn fixture skips on its own when unset).
"""

from __future__ import annotations

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import business_credits  # noqa: E402
from plugins.takyon import control_plane  # noqa: E402
from plugins.takyon import core as takyon_core  # noqa: E402


def _seed_owned_business(dsn: str, slug: str, *, mode: str = "test") -> None:
    """Insert a users row + an owned businesses row directly (the store cannot create one until P8.3
    wires owner_user_id). Uses the SAME dsn the store will open, so they share one database. Every
    enriched businesses column (goal/status/work_focus/budget_json/metadata_json/updated_at) and
    created_at carries a default, so only the four required spine columns are supplied."""
    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id", (f"auth0|{slug}",)
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, %s)",
            (slug, slug.title(), uid, mode),
        )


def _direct_business_fk_tables(dsn: str) -> set[str]:
    """Current Postgres tables directly owned by one business via FK to businesses.slug.

    This is intentionally computed from the migrated schema instead of a hardcoded list so the test
    fails when a new Takyon-owned business table is added without the delete preview/result learning
    about it.
    """
    with psycopg.connect(dsn, autocommit=True) as conn:
        rows = conn.execute(
            """
            select distinct tc.table_name
            from information_schema.table_constraints tc
            join information_schema.key_column_usage kcu
              on tc.constraint_name = kcu.constraint_name
             and tc.table_schema = kcu.table_schema
            join information_schema.constraint_column_usage ccu
              on ccu.constraint_name = tc.constraint_name
             and ccu.table_schema = tc.table_schema
            where tc.constraint_type = 'FOREIGN KEY'
              and tc.table_schema = 'public'
              and ccu.table_name = 'businesses'
              and ccu.column_name = 'slug'
            order by tc.table_name
            """
        ).fetchall()
    return {str(row[0]) for row in rows}


@pytest.fixture
def pg_store(pg_store_dsn, tmp_path):
    """A TakyonStore wired to a migrated throwaway Postgres DB."""
    return takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)


def test_default_backend_returns_pg_wrapper_without_bootstrapping(pg_store):
    # The default store backend is the psycopg-backed _PGConn adapter, and it does NOT run schema
    # bootstrap: a ?-parametrized, dict_row read works, and executescript fails loud if ever reached.
    with pg_store._connect() as conn:
        assert isinstance(conn, takyon_core._PGConn)
        row = conn.execute(
            "SELECT count(*) AS n FROM businesses WHERE slug = ?", ("absent",)
        ).fetchone()
        assert row["n"] == 0  # dict_row: addressable by the aliased name, not position
        with pytest.raises(RuntimeError, match="must not run on the Postgres backend"):
            conn.executescript("CREATE TABLE should_not_exist (i int)")
    # Bootstrap really was skipped: the guard table the executescript tried to make is absent.
    with psycopg.connect(pg_store._database_url, autocommit=True) as raw:
        assert raw.execute("select to_regclass('public.should_not_exist')").fetchone()[0] is None


def test_pg_wrapper_accepts_native_percent_s_placeholders(pg_store):
    # Delegated leaf helpers like business_credits already speak native psycopg %s placeholders.
    # The store wrapper must pass those through instead of escaping them into a literal %%s.
    with pg_store._connect() as conn:
        row = conn.execute("SELECT %s::int AS n", (7,)).fetchone()
        assert row["n"] == 7


def test_business_credits_round_trip_through_pg_wrapper(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "creditco", mode="live")
    with pg_store._connect() as conn:
        business_credits.open_business_credit_account(conn, "creditco")
        granted = business_credits.grant_credits(conn, "creditco", 1, "grant-1")
        assert granted.balance_credits == 1
        balances = business_credits.get_business_credit_balances(conn, "creditco")
        assert balances.business_slug == "creditco"
        assert balances.balance_credits == 1
        assert balances.reserved_credits == 0


def test_stale_sqlite_backend_env_is_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_DB_BACKEND", "sqlite")
    with pytest.raises(RuntimeError, match="legacy Takyon SQLite backend has been removed"):
        takyon_core._db_backend()


def test_explicit_postgres_backend_env_is_still_accepted(pg_store_dsn, tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_DB_BACKEND", "postgres")
    store = takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)
    with store._connect() as conn:
        assert isinstance(conn, takyon_core._PGConn)


def test_multi_op_commit_round_trips_operator_tables(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "seamco", mode="test")

    # One atomic commit spanning six operator tables. control.set exercises the scope-keyed
    # control_states UPSERT; conversation.message.record creates a thread + message (exercising
    # ON CONFLICT(business_slug, source, external_id)); agent.record binds a NULL parent_id (proving
    # None → SQL NULL through the wrapper); event.record + every other op also append an events row.
    result = pg_store.commit(
        scope="business:seamco",
        operations=[
            {"action": "workspace.upsert", "business": "seamco", "path": "product/site"},
            {"action": "control.set", "scope": "business:seamco", "state": "paused", "reason": "seam hold"},
            {"action": "agent.record", "business": "seamco", "status": "completed"},
            {"action": "conversation.message.record", "business": "seamco", "source": "x",
             "direction": "inbound", "body": "hello", "thread_external_id": "t-1"},
            {"action": "event.record", "business": "seamco", "scope": "business:seamco",
             "event_type": "seam.test", "payload": {"k": 1}},
        ],
        idempotency_key="seam-multi-1",
        reason="seam round-trip",
        actor="test",
    )
    assert result["success"] is True
    assert len(result["results"]) == 5

    # Read every operator row straight back through raw psycopg (default tuple rows) — bypassing the
    # store entirely — to prove the writes are real AND committed by the outer transaction.
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from workspaces where business_slug='seamco' and path='product/site'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from control_states where scope='business:seamco' and state='paused'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from agent_runs where scope='business:seamco' and status='completed'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from conversation_threads where business_slug='seamco' and external_id='t-1'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from conversation_messages where business_slug='seamco' and direction='inbound'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from events where business_slug='seamco' and event_type='seam.test'"
        ).fetchone()[0] == 1
        # The idempotency spine recorded this commit exactly once.
        assert conn.execute(
            "select count(*) from idempotency_keys where key='seam-multi-1'"
        ).fetchone()[0] == 1


def test_global_control_set_round_trips(pg_store, pg_store_dsn):
    # control_states via a GLOBAL-scope commit: no business, so the CEO-cron sync path (which imports
    # cron.jobs) is never entered — this isolates the INSERT ... ON CONFLICT(scope) on Postgres.
    result = pg_store.commit(
        scope="global",
        operations=[{"action": "control.set", "scope": "global", "state": "paused", "reason": "hold"}],
        idempotency_key="seam-control-1",
        reason="seam control",
        actor="test",
    )
    assert result["success"] is True
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select state from control_states where scope='global'"
        ).fetchone()[0] == "paused"

    # And the store's own read path returns the operator rows by name (global read = businesses +
    # control_states), proving reads round-trip through dict_row too.
    read = pg_store.read(scope="global", query="businesses")
    assert read["success"] is True
    assert any(c["scope"] == "global" and c["state"] == "paused" for c in read["controls"])


def test_commit_idempotency_replay_is_one_effect(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "idemco", mode="test")
    ops = [{"action": "workspace.upsert", "business": "idemco", "path": "product/site"}]
    first = pg_store.commit(
        scope="business:idemco", operations=ops, idempotency_key="idem-1", reason="r", actor="test"
    )
    second = pg_store.commit(
        scope="business:idemco", operations=ops, idempotency_key="idem-1", reason="r", actor="test"
    )
    assert first["success"] is True
    # Replay returns the STORED original result verbatim (commit() does this when it finds the prior
    # idempotency_keys row), not a re-application — a genuine re-apply would PK-violate on the
    # no-ON-CONFLICT idempotency_keys INSERT, so the only way `second` succeeds is the replay path.
    assert second == first
    # And the gate truly swallowed the replay: exactly one key row and one workspace row.
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from idempotency_keys where key='idem-1'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from workspaces where business_slug='idemco' and path='product/site'"
        ).fetchone()[0] == 1


# ── P8.2b Stage A: operator-jobs storage retarget to business_work_requests ─────────────────────


def test_operator_job_enqueue_isolates_to_business_work_requests(pg_store, pg_store_dsn):
    # The operator store's job.enqueue is a *work-request record*, not a worker-plane queue entry. On
    # Postgres _work_requests_table() resolves it to business_work_requests (0011's 1:1 text port of
    # the SQLite operator `jobs`) — NOT the 0010 `jobs` execution queue. Prove the row lands there with
    # the store's exact text shape, and the 0010 queue stays empty (isolation is the whole point of the
    # P8.1 ISOLATE decision).
    import json

    _seed_owned_business(pg_store_dsn, "jobco", mode="test")
    result = pg_store.commit(
        scope="business:jobco",
        operations=[{
            "action": "job.enqueue", "business": "jobco", "kind": "research",
            "status": "queued", "payload": {"topic": "seam"},
        }],
        idempotency_key="stageA-enqueue-1",
        reason="stage A enqueue",
        actor="test",
    )
    assert result["success"] is True

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute(
            "select scope, business_slug, kind, status, payload_json "
            "from business_work_requests where business_slug = %s",
            ("jobco",),
        ).fetchone()
        assert row is not None
        scope, business_slug, kind, status, payload_json = row
        assert (scope, business_slug, kind, status) == ("business:jobco", "jobco", "research", "queued")
        assert json.loads(payload_json)["topic"] == "seam"  # text column, store's json.dumps round-trips
        # Isolation: the 0010 worker-plane execution queue is never touched by the operator store.
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0

    # The store's own read path returns it under the logical "jobs" key. query="jobs" deliberately
    # avoids query="summary"/"app", which would hit the not-yet-delegated (Stage B) app_* reads.
    read = pg_store.read(scope="business:jobco", query="jobs")
    assert read["success"] is True
    assert [j["kind"] for j in read["jobs"]] == ["research"]


def test_operator_job_enqueue_can_mirror_live_x_publish_into_worker_queue(pg_store, pg_store_dsn):
    import json

    _seed_owned_business(pg_store_dsn, "xco", mode="live")
    result = pg_store.commit(
        scope="business:xco",
        operations=[{
            "action": "job.enqueue",
            "business": "xco",
            "kind": "x.publish_outreach",
            "status": "pending",
            "worker_queue": True,
            "worker_max_attempts": 1,
            "payload": {"provider": "x", "channel": "x", "body": "hello from takyon"},
        }],
        idempotency_key="x-mirror-enqueue-1",
        reason="mirror live x publish",
        actor="test",
    )
    assert result["success"] is True
    enqueue = result["results"][0]
    assert enqueue["job"]
    assert enqueue["worker_job"]

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        work_request = conn.execute(
            "select status, payload_json from business_work_requests where id = %s",
            (enqueue["job"],),
        ).fetchone()
        assert work_request is not None
        assert work_request[0] == "pending"
        assert json.loads(work_request[1])["work_request_id"] == enqueue["job"]

        worker_row = conn.execute(
            "select kind, status, max_attempts, payload from jobs where id = %s",
            (enqueue["worker_job"],),
        ).fetchone()
        assert worker_row is not None
        assert worker_row[0] == "x.publish_outreach"
        assert worker_row[1] == "queued"
        assert worker_row[2] == 1
        assert worker_row[3]["work_request_id"] == enqueue["job"]


def test_maintenance_gc_prunes_business_work_requests_not_jobs_queue(pg_store, pg_store_dsn):
    # The GC query dict keys the work-request store by its physical name (business_work_requests on PG),
    # and that same key drives DELETE FROM {table}; a confirmed gc prunes completed records there while
    # the 0010 jobs queue stays untouched. Exercises the GC site's key-doubles-as-table-name path.
    _seed_owned_business(pg_store_dsn, "gcco", mode="test")
    pg_store.commit(
        scope="business:gcco",
        operations=[{"action": "job.enqueue", "business": "gcco", "kind": "research", "status": "completed"}],
        idempotency_key="stageA-gc-enqueue",
        reason="seed gc",
        actor="test",
    )
    # older_than_days has a 7-day floor (core.py _gc), so backdate the record past the cutoff. created_at
    # is a text column on PG (0011), and ISO-8601 strings compare lexicographically like the engine assumes.
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        conn.execute(
            "update business_work_requests set created_at = %s where business_slug = %s",
            ("2000-01-01T00:00:00+00:00", "gcco"),
        )
    result = pg_store.commit(
        scope="global",
        operations=[{"action": "maintenance.gc", "confirm": True, "older_than_days": 7}],
        idempotency_key="stageA-gc-run",
        reason="gc",
        actor="test",
    )
    assert result["success"] is True
    gc = result["results"][0]
    # The receipt keys the pruned set by the physical table actually touched — truthful on PG.
    assert gc["deleted"]["business_work_requests"] == 1

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from business_work_requests where business_slug = 'gcco'"
        ).fetchone()[0] == 0
        assert conn.execute("select count(*) from jobs").fetchone()[0] == 0


# ── P8.2b Stage B: app.* writes delegate to the Phase-5 leaves on Postgres ───────────────────────


def _commit_one(store, slug, op, key):
    """Drive ONE app.* op through the real store.commit path (the seam under test) for `slug`.
    Single-op commits keep each leaf delegation isolated and let a distinct idempotency_key per call
    bypass the store's own idempotency spine — which matters where we want to reach the LEAF twice
    (its reservation_key idempotency is a different gate from the store's key)."""
    return store.commit(
        scope=f"business:{slug}",
        operations=[op],
        idempotency_key=key,
        reason="stage B",
        actor="test",
    )


def test_app_budget_set_delegates_to_app_usage_and_preserves_status(pg_store, pg_store_dsn):
    # app.budget.set delegates to app_usage.set_app_budget (the canonical app_budgets writer). The
    # store hoists `status = op.status or current.status or "active"` BEFORE the leaf call, so an
    # update that omits status must keep the prior one — prove the hoist by pausing then re-capping.
    _seed_owned_business(pg_store_dsn, "budgco", mode="test")
    first = _commit_one(
        pg_store, "budgco",
        {"action": "app.budget.set", "business": "budgco", "hard_limit_microusd": 9_000_000, "status": "paused"},
        "stageB-budget-1",
    )
    assert first["success"] is True
    # Second cap with NO status field — the leaf must receive the preserved "paused".
    second = _commit_one(
        pg_store, "budgco",
        {"action": "app.budget.set", "business": "budgco", "hard_limit_microusd": 3_000_000},
        "stageB-budget-2",
    )
    assert second["success"] is True
    assert second["results"][0]["hard_limit_microusd"] == 3_000_000

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute(
            "select hard_limit_microusd, status from app_budgets where business_slug = %s", ("budgco",)
        ).fetchone()
        assert int(row[0]) == 3_000_000      # the leaf wrote the new cap
        assert row[1] == "paused"            # ...and the omitted status was preserved, not reset to active


def test_app_plan_upsert_delegates_drops_payment_link_cols_and_folds_warnings_once(pg_store, pg_store_dsn):
    # app.plan.upsert delegates to app_entitlements.upsert_plan_policy. Three things the delegation
    # exists to get right: (1) the leaf owns plan_key normalization, so the receipt reflects the
    # PERSISTED key ("Pro Plan" -> "pro-plan"); (2) migration 0006 dropped the dead
    # stripe_payment_link_* columns the SQLite INSERT still lists, so they must be ABSENT on PG;
    # (3) the leaf folds plan-validation warnings into metadata itself — the store passes RAW
    # metadata so the warning lands EXACTLY ONCE, not doubled.
    _seed_owned_business(pg_store_dsn, "planco", mode="test")
    result = _commit_one(
        pg_store, "planco",
        {"action": "app.plan.upsert", "business": "planco", "plan_key": "Pro Plan",
         "tier": "enterprise", "price_cents": 2000},  # tier != plan_key triggers one advisory warning
        "stageB-plan-1",
    )
    assert result["success"] is True
    assert result["results"][0]["plan_key"] == "pro-plan"  # receipt = leaf-normalized persisted key

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        cols = {
            r[0] for r in conn.execute(
                "select column_name from information_schema.columns where table_name = 'app_plan_policies'"
            ).fetchall()
        }
        # The divergence the delegation is built for: the dead payment-link columns are gone on PG.
        assert "stripe_payment_link_id" not in cols
        assert "stripe_payment_link_url" not in cols
        assert {"plan_key", "tier", "price_cents", "stripe_product_id", "stripe_price_id", "metadata"} <= cols

        row = conn.execute(
            "select tier, price_cents, metadata from app_plan_policies "
            "where business_slug = %s and plan_key = %s",
            ("planco", "pro-plan"),
        ).fetchone()
        assert row is not None
        assert (row[0], int(row[1])) == ("enterprise", 2000)
        # jsonb metadata decodes to a dict; the advisory warning was folded ONCE (store didn't also fold).
        warnings = row[2]["takyon_plan_validation"]["warnings"]
        assert len(warnings) == 1


def test_app_customer_upsert_delegates_to_app_identity_forcing_active(pg_store, pg_store_dsn):
    # app.customer.upsert delegates to app_identity.upsert_app_user. The leaf is intentionally
    # narrower (forces status='active'); prove the returned app_user_id is the persisted row id and
    # the row is active.
    _seed_owned_business(pg_store_dsn, "custco", mode="test")
    result = _commit_one(
        pg_store, "custco",
        {"action": "app.customer.upsert", "business": "custco", "email": "Buyer@Example.com", "name": "Buyer"},
        "stageB-cust-1",
    )
    assert result["success"] is True
    app_user_id = result["results"][0]["app_user_id"]

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute(
            "select id::text, email, name, status from app_users where business_slug = %s", ("custco",)
        ).fetchone()
        assert row is not None
        assert row[0] == app_user_id            # receipt id == persisted id
        assert row[1] == "buyer@example.com"    # leaf normalized the email
        assert (row[2], row[3]) == ("Buyer", "active")
        prow = conn.execute(
            "select id::text, display_name from app_user_profiles where business_slug = %s and id = %s",
            ("custco", app_user_id),
        ).fetchone()
        assert prow == (app_user_id, "Buyer")


def test_app_entitlement_upsert_email_autoprovisions_and_syncs_tier(pg_store, pg_store_dsn):
    # app.entitlement.upsert delegates to app_entitlements.grant_entitlement, which auto-provisions
    # the sub-user from email (no recursive customer.upsert needed on PG) and atomically resyncs
    # app_users.tier. A non-billing source ("internal") legitimately grants a paid tier.
    _seed_owned_business(pg_store_dsn, "entco", mode="test")
    result = _commit_one(
        pg_store, "entco",
        {"action": "app.entitlement.upsert", "business": "entco", "email": "vip@example.com",
         "tier": "pro", "source": "internal"},
        "stageB-ent-1",
    )
    assert result["success"] is True
    receipt = result["results"][0]
    assert receipt["tier"] == "pro"
    user_id = receipt["app_user_id"]

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        # The sub-user was auto-created by the leaf and its effective tier synced to the grant.
        urow = conn.execute(
            "select id::text, tier from app_users where business_slug = %s and email = %s",
            ("entco", "vip@example.com"),
        ).fetchone()
        assert urow is not None
        assert urow[0] == user_id
        assert urow[1] == "pro"                 # _sync_user_tier bumped it inside the same transaction
        erow = conn.execute(
            "select tier, status, source from app_entitlements where business_slug = %s and app_user_id = %s",
            ("entco", user_id),
        ).fetchone()
        assert erow == ("pro", "active", "internal")
        prow = conn.execute(
            "select id::text from app_user_profiles where business_slug = %s and id = %s",
            ("entco", user_id),
        ).fetchone()
        assert prow == (user_id,)


def test_app_entitlement_upsert_manual_paid_without_evidence_is_rejected(pg_store, pg_store_dsn):
    # The anti-fake-billing rail survives delegation: a non-free grant with no Stripe evidence
    # is refused by the leaf (FakeBillingRejected), surfaced to the
    # store as TakyonError, which propagates out of commit() and rolls the whole transaction back —
    # so neither the entitlement NOR the auto-provisioned user is left behind.
    _seed_owned_business(pg_store_dsn, "fakeco", mode="test")
    with pytest.raises(takyon_core.TakyonError):
        _commit_one(
            pg_store, "fakeco",
            {"action": "app.entitlement.upsert", "business": "fakeco", "email": "fraud@example.com",
             "tier": "pro", "source": "manual"},
            "stageB-fake-1",
        )
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from app_entitlements where business_slug = %s", ("fakeco",)
        ).fetchone()[0] == 0
        # The gate fires before any write, and the outer commit rolled back: no orphan sub-user either.
        assert conn.execute(
            "select count(*) from app_users where business_slug = %s", ("fakeco",)
        ).fetchone()[0] == 0


def test_app_usage_record_delegates_completed_with_leaf_reservation_key_idempotency(pg_store, pg_store_dsn):
    # app.usage.record delegates to app_usage.record_completed_usage. Two proofs: (1) the receipt's
    # usage_event is the PERSISTED event id, not the op id the store passes as reservation_key (the
    # PG model generates its own row id); (2) the LEAF's reservation_key idempotency collapses a
    # duplicate even under a DIFFERENT store idempotency_key (so the store's spine isn't what swallows
    # it) — re-recording the same reservation_key returns the same event and writes no second row.
    _seed_owned_business(pg_store_dsn, "useco", mode="test")
    first = _commit_one(
        pg_store, "useco",
        {"action": "app.usage.record", "business": "useco", "id": "res-1", "actual_cost_microusd": 1000},
        "stageB-usage-A",
    )
    assert first["success"] is True
    event_id = first["results"][0]["usage_event"]
    assert event_id != "res-1"  # leaf-generated row id, distinct from the reservation_key

    # Same reservation_key (op id), DIFFERENT store key — reaches the leaf, which returns the prior event.
    second = _commit_one(
        pg_store, "useco",
        {"action": "app.usage.record", "business": "useco", "id": "res-1", "actual_cost_microusd": 1000},
        "stageB-usage-B",
    )
    assert second["success"] is True
    assert second["results"][0]["usage_event"] == event_id  # leaf idempotency, not the store's

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        rows = conn.execute(
            "select id::text, status, actual_cost_microusd from app_usage_events "
            "where business_slug = %s and reservation_key = %s",
            ("useco", "res-1"),
        ).fetchall()
        assert len(rows) == 1                       # the duplicate was swallowed by the leaf
        assert rows[0][0] == event_id
        assert (rows[0][1], int(rows[0][2])) == ("completed", 1000)


def test_app_usage_record_budget_cap_is_enforced_atomically(pg_store, pg_store_dsn):
    # The authoritative budget cap survives delegation: record_completed_usage row-locks the budget
    # and checks committed + this amount against the cap. With a 1000-microusd cap, a 900 record fits;
    # a following 200 record would make committed spend 1100 > 1000 and is refused (AppBudgetExceeded
    # -> TakyonError), leaving exactly the one accepted row. This is invariant #8: the cap is enforced,
    # not raced or silently exceeded.
    _seed_owned_business(pg_store_dsn, "capco", mode="test")
    assert _commit_one(
        pg_store, "capco",
        {"action": "app.budget.set", "business": "capco", "hard_limit_microusd": 1000},
        "stageB-cap-budget",
    )["success"] is True
    assert _commit_one(
        pg_store, "capco",
        {"action": "app.usage.record", "business": "capco", "id": "cap-r1", "actual_cost_microusd": 900},
        "stageB-cap-u1",
    )["success"] is True
    with pytest.raises(takyon_core.TakyonError):
        _commit_one(
            pg_store, "capco",
            {"action": "app.usage.record", "business": "capco", "id": "cap-r2", "actual_cost_microusd": 200},
            "stageB-cap-u2",
        )

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        rows = conn.execute(
            "select actual_cost_microusd from app_usage_events where business_slug = %s", ("capco",)
        ).fetchall()
        assert len(rows) == 1               # only the accepted spend survived
        assert int(rows[0][0]) == 900


def test_app_reads_round_trip_after_delegated_writes(pg_store, pg_store_dsn):
    # After a full set of delegated app.* writes, the store's own read path (query="app", which calls
    # _app_summary) and calculate_pulse must both round-trip the Postgres rows back through dict_row +
    # the backend-agnostic metadata-column select. This is the read-side proof for Stage B.
    _seed_owned_business(pg_store_dsn, "rtco", mode="test")
    for op, key in [
        ({"action": "app.budget.set", "business": "rtco", "hard_limit_microusd": 9_000_000}, "rt-budget"),
        ({"action": "app.plan.upsert", "business": "rtco", "plan_key": "pro", "tier": "pro", "price_cents": 2000}, "rt-plan"),
        ({"action": "app.customer.upsert", "business": "rtco", "email": "rt@example.com", "name": "RT"}, "rt-cust"),
        ({"action": "app.entitlement.upsert", "business": "rtco", "email": "rt@example.com", "tier": "pro", "source": "internal"}, "rt-ent"),
        ({"action": "app.usage.record", "business": "rtco", "id": "rt-u1", "actual_cost_microusd": 1500}, "rt-usage"),
    ]:
        assert _commit_one(pg_store, "rtco", op, key)["success"] is True

    read = pg_store.read(scope="business:rtco", query="app")
    assert read["success"] is True
    app = read["app"]
    assert int(app["budget"]["hard_limit_microusd"]) == 9_000_000
    assert any(p["plan_key"] == "pro" for p in app["plans"])
    assert any(c["email"] == "rt@example.com" for c in app["customers"])
    assert any(e["tier"] == "pro" for e in app["entitlements"])
    assert app["usage_this_period"]["events"] == 1
    assert int(app["usage_this_period"]["actual_cost_microusd"]) == 1500

    pulse = pg_store.calculate_pulse("rtco")
    assert pulse["success"] is True
    assert pulse["business"] == "rtco"


# --------------------------------------------------------------------------- P8.3 owner wiring


def test_ensure_platform_owner_idempotent_mints_key_once(pg_store_dsn, monkeypatch):
    # The explicit bootstrap seam (the serving-flip startup) is idempotent: it provisions the single
    # platform owner FULLY (mint THE one API key + open billing + custody, one txn) on the first call
    # and returns the one-time raw key; later calls return the same user with raw_key=None and never
    # mint a second active key (0001's one-active partial unique index). No half-made owner is observable.
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|operator-boot")
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        uid1, raw1 = control_plane.ensure_platform_owner(conn)
        uid2, raw2 = control_plane.ensure_platform_owner(conn)
        assert uid1 == uid2
        assert raw1 and raw1.startswith("tk_") and raw2 is None
        assert conn.execute(
            "select count(*) from user_api_keys where user_id = %s and revoked_at is null", (uid1,)
        ).fetchone()[0] == 1
        # Both money accounts opened in the same provisioning txn (identity never exists without ledgers).
        assert conn.execute("select count(*) from billing_accounts where user_id = %s", (uid1,)).fetchone()[0] == 1
        assert conn.execute("select count(*) from custody_accounts where user_id = %s", (uid1,)).fetchone()[0] == 1


def test_business_upsert_lands_owned_business_with_resolved_platform_owner(pg_store, pg_store_dsn, monkeypatch):
    # business.upsert is the FIRST store write needing owner_user_id on PG. The store resolves the single
    # platform owner READ-ONLY (no key minted in the commit path); the bootstrap below is what mints it.
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|operator-create")
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        owner_id, raw_key = control_plane.ensure_platform_owner(conn)
    assert raw_key and raw_key.startswith("tk_")  # one-time key surfaced ONLY by the bootstrap

    result = pg_store.commit(
        scope="business:ownedco",
        operations=[{"action": "business.upsert", "business": "ownedco", "name": "Owned Co",
                     "goal": "ship", "mode": "test"}],
        idempotency_key="p83-create-1", reason="p8.3", actor="test",
    )
    assert result["success"] is True
    assert result["results"][0]["business"] == "ownedco"

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute(
            "select owner_user_id, name, goal, mode, status from businesses where slug = %s", ("ownedco",)
        ).fetchone()
        assert row is not None
        assert str(row[0]) == owner_id  # owned by the resolved platform owner (NOT a fabricated owner)
        assert row[1] == "Owned Co" and row[2] == "ship" and row[3] == "test" and row[4] == "active"
        # The unification payoff: the owner's opaque API key now lists the shell-created business.
        principal = control_plane.resolve_api_key(conn, raw_key)
        assert principal is not None and "ownedco" in principal.business_slugs


def test_business_upsert_blocks_when_platform_owner_unprovisioned(pg_store, pg_store_dsn, monkeypatch):
    # Invariant #8: no NULL/fake owner. With the configured platform sub NOT provisioned, business.upsert
    # blocks with an actionable reason AND leaves no half-created business row (the commit rolls back).
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|never-seeded")
    with pytest.raises(takyon_core.TakyonError, match="platform owner is not provisioned"):
        pg_store.commit(
            scope="business:orphanco",
            operations=[{"action": "business.upsert", "business": "orphanco", "name": "Orphan Co"}],
            idempotency_key="p83-orphan-1", reason="p8.3", actor="test",
        )
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from businesses where slug = %s", ("orphanco",)
        ).fetchone()[0] == 0


def test_business_upsert_update_path_preserves_owner_on_postgres(pg_store, pg_store_dsn, monkeypatch):
    # The existing-business UPDATE branch never touches owner_user_id, so it runs unchanged on PG and
    # must preserve the original owner while updating the mutable fields (name/goal/mode).
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|operator-update")
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        owner_id, _ = control_plane.ensure_platform_owner(conn)
    pg_store.commit(
        scope="business:upco",
        operations=[{"action": "business.upsert", "business": "upco", "name": "Up Co", "goal": "g1", "mode": "test"}],
        idempotency_key="p83-up-1", reason="p8.3", actor="test",
    )
    pg_store.commit(
        scope="business:upco",
        operations=[{"action": "business.upsert", "business": "upco", "name": "Up Co v2", "goal": "g2", "mode": "live"}],
        idempotency_key="p83-up-2", reason="p8.3", actor="test",
    )
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute(
            "select owner_user_id, name, goal, mode from businesses where slug = %s", ("upco",)
        ).fetchone()
    assert str(row[0]) == owner_id  # owner untouched by the update
    assert row[1] == "Up Co v2" and row[2] == "g2" and row[3] == "live"


def test_business_delete_detaches_billing_and_custody_history_on_postgres(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "deleteco", mode="test")
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        owner_id = conn.execute(
            "select owner_user_id from businesses where slug = %s", ("deleteco",)
        ).fetchone()[0]
        conn.execute(
            "insert into billing_accounts (user_id, allowance_included_cents, allowance_used_cents) "
            "values (%s, 1000, 100)",
            (owner_id,),
        )
        conn.execute(
            "insert into billing_entries (user_id, business_slug, bucket, kind, amount_cents, "
            "balance_after_cents, idempotency_key) values (%s, %s, 'allowance', 'reserve', 100, 100, %s)",
            (owner_id, "deleteco", "deleteco-billing-1"),
        )
        conn.execute(
            "insert into custody_entries (user_id, business_slug, kind, gross_cents, fee_cents, "
            "net_cents, idempotency_key) values (%s, %s, 'accrual', 500, 50, 450, %s)",
            (owner_id, "deleteco", "deleteco-custody-1"),
        )

    result = pg_store.commit(
        scope="global",
        operations=[{"action": "business.delete", "business": "deleteco", "confirm": True, "delete_domains": False}],
        idempotency_key="pg-deleteco-1",
        reason="test",
        actor="test",
    )

    assert result["success"] is True
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from businesses where slug = %s", ("deleteco",)
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from billing_entries where business_slug = %s", ("deleteco",)
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from billing_entries where idempotency_key = %s and business_slug is null",
            ("deleteco-billing-1",),
        ).fetchone()[0] == 1
        assert conn.execute(
            "select count(*) from custody_entries where business_slug = %s", ("deleteco",)
        ).fetchone()[0] == 0
        assert conn.execute(
            "select count(*) from custody_entries where idempotency_key = %s and business_slug is null",
            ("deleteco-custody-1",),
        ).fetchone()[0] == 1


def test_business_delete_reports_all_current_business_owned_postgres_tables(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "reportco", mode="test")

    fk_tables = _direct_business_fk_tables(pg_store_dsn)
    preview = pg_store.commit(
        scope="global",
        operations=[{"action": "business.delete", "business": "reportco", "delete_domains": False}],
        idempotency_key="pg-delete-preview-reportco-1",
        reason="test",
        actor="test",
    )["results"][0]

    candidate_keys = set(preview["database"]["candidates"])
    assert fk_tables <= candidate_keys
    assert {
        "app_execution_policies",
        "app_gateway_keys",
        "app_user_profiles",
        "business_creative_credit_accounts",
        "business_creative_credit_entries",
        "jobs",
        "wake_schedules",
    } <= candidate_keys
    assert {
        "business_work_requests",
        "ledger_entries",
        "events",
        "agent_runs",
        "control_states",
        "billing_entries",
        "custody_entries",
    } <= candidate_keys

    deletion = pg_store.commit(
        scope="global",
        operations=[{"action": "business.delete", "business": "reportco", "confirm": True, "delete_domains": False}],
        idempotency_key="pg-delete-reportco-1",
        reason="test",
        actor="test",
    )["results"][0]

    deleted_keys = set(deletion["database"]["deleted"])
    assert (fk_tables - {"billing_entries", "custody_entries"}) <= deleted_keys
    assert {
        "business_work_requests",
        "ledger_entries",
        "events",
        "agent_runs",
        "control_states",
        "billing_entries_detached",
        "custody_entries_detached",
    } <= deleted_keys


# --------------------------------------------------------------------------- P8.4 serving flip


def test_seed_platform_owner_via_store_is_idempotent_and_enables_create(pg_store, pg_store_dsn, monkeypatch):
    # The serving-flip startup seed the shell + dashboard both call. It must mint the one-time key
    # exactly ONCE over the store's OWN connection seam (reusing _connect + _leaf_conn, not a second
    # connection strategy), be idempotent on re-call, open both money accounts, and leave the owner
    # resolvable so the very next business.upsert lands an owned business — the whole point of the seed
    # (business.upsert resolves the owner read-only and would otherwise block, invariant #8).
    monkeypatch.setenv("TAKYON_PLATFORM_OWNER_SUB", "auth0|serving-flip")
    uid1, raw1 = pg_store.seed_platform_owner()
    assert uid1 and raw1 and raw1.startswith("tk_")
    uid2, raw2 = pg_store.seed_platform_owner()
    assert uid2 == uid1 and raw2 is None  # idempotent: no second active key minted

    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        assert conn.execute(
            "select count(*) from user_api_keys where user_id = %s and revoked_at is null", (uid1,)
        ).fetchone()[0] == 1
        assert conn.execute("select count(*) from billing_accounts where user_id = %s", (uid1,)).fetchone()[0] == 1
        assert conn.execute("select count(*) from custody_accounts where user_id = %s", (uid1,)).fetchone()[0] == 1

    # The payoff: create now succeeds and the row is owned by the seeded owner (no fabricated owner).
    result = pg_store.commit(
        scope="business:flipco",
        operations=[{"action": "business.upsert", "business": "flipco", "name": "Flip Co",
                     "goal": "g", "mode": "test"}],
        idempotency_key="p84-create-1", reason="p8.4", actor="test",
    )
    assert result["success"] is True
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        row = conn.execute("select owner_user_id from businesses where slug = %s", ("flipco",)).fetchone()
        assert row is not None and str(row[0]) == uid1


def test_seed_platform_owner_rejects_stale_sqlite_backend_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TAKYON_DB_BACKEND", "sqlite")
    store = takyon_core.TakyonStore(root=tmp_path)
    with pytest.raises(RuntimeError, match="legacy Takyon SQLite backend has been removed"):
        store.seed_platform_owner()
