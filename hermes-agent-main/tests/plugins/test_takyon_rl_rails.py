"""RL rails (rl-rails-plan.md) — subjective CEO memory loop, on real Postgres.

Runs against a migrated throwaway DB (the pg_store_dsn fixture applies every migration via the
production runner, so this ALSO validates migration 0057). Skips unless TAKYON_TEST_PG_DSN is set.

Covers the build that landed this pass:
  * R5 wake-injection — the floor-1 byte-carrying channel: a planted state-of-mind note provably
    enters the wake user turn (_ceo_cron_prompt). Proof the "learning channel" is not dead.
  * R1 episode rail — a recorded bet shows up in the next wake's injected memory.
  * R4 pulse baseline — record_pulse persists a snapshot so deltas stop baselining against zero.
"""

from __future__ import annotations

import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import core as takyon_core  # noqa: E402
from plugins.takyon.core import TakyonError  # noqa: E402


def _seed_owned_business(dsn: str, slug: str, *, mode: str = "test") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id", (f"auth0|{slug}",)
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode) values (%s, %s, %s, %s)",
            (slug, slug.title(), uid, mode),
        )


@pytest.fixture
def pg_store(pg_store_dsn, tmp_path):
    return takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn)


def test_migration_0057_creates_operator_plane_tables(pg_store_dsn):
    # The fixture already ran every migration including 0057; assert the tables exist and that the
    # subuser role has no privilege on them (subuser-security invariant).
    with psycopg.connect(pg_store_dsn, autocommit=True) as conn:
        for t in ("ceo_episode", "ceo_trace", "ceo_identity", "ceo_state_of_mind",
                  "business_ad_spend_entries", "twin_cohort"):
            exists = conn.execute(
                "select to_regclass(%s) is not null", (f"public.{t}",)
            ).fetchone()[0]
            assert exists, f"{t} missing — 0057 did not apply"
            if conn.execute("select 1 from pg_roles where rolname='takyon_app_runtime'").fetchone():
                privs = conn.execute(
                    "select count(*) from information_schema.role_table_grants "
                    "where grantee='takyon_app_runtime' and table_name=%s", (t,)
                ).fetchone()[0]
                assert privs == 0, f"subuser role has grants on {t} — boundary weakened"


def test_wake_memory_empty_without_events(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "emptyco")
    assert pg_store._assemble_wake_memory("emptyco") == ""


def test_state_of_mind_canary_enters_wake_context(pg_store, pg_store_dsn):
    # floor-1: prove the injection channel carries bytes into the cold wake user turn.
    _seed_owned_business(pg_store_dsn, "canaryco")
    canary = f"CANARY-{uuid.uuid4().hex[:8]}: resume the reddit pain-first test, check replies"
    pg_store.open_state_of_mind("canaryco", canary)
    prompt = pg_store._ceo_cron_prompt("canaryco")
    assert canary in prompt
    assert "Where you left off last wake" in prompt


def test_recorded_episode_shows_in_next_wake(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "betco")
    out = pg_store.record_episode("betco", "ship $19/mo pricing test", channel="pricing")
    assert out["success"] and out["episode_id"]
    prompt = pg_store._ceo_cron_prompt("betco")
    assert "ship $19/mo pricing test" in prompt
    assert "in flight" in prompt


def test_record_pulse_creates_real_baseline(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "pulseco")
    first = pg_store.record_pulse("pulseco")
    assert first["was_first_pulse"] is True
    second = pg_store.record_pulse("pulseco")
    assert second["was_first_pulse"] is False


def test_state_of_mind_requires_note(pg_store):
    with pytest.raises(TakyonError):
        pg_store.open_state_of_mind("anyco", "   ")


# --- R7 learnings: intra (this business, always) vs inter (cross-business, top-k) -----------

def test_identity_surfaces_in_wake(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "identco")
    pg_store.set_identity("identco", "You are the CEO of Identco, a B2C habit tracker. Thesis: streaks drive retention.")
    prompt = pg_store._ceo_cron_prompt("identco")
    assert "Who you are" in prompt and "habit tracker" in prompt


def test_intra_learning_isolated_to_its_business(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "intraco")
    _seed_owned_business(pg_store_dsn, "otherco")
    pg_store.record_learning("intraco", "Cold DMs convert 3x better than top-of-funnel posts here.", scope="business")
    assert "Cold DMs convert" in pg_store._ceo_cron_prompt("intraco")
    # intra is scoped to its own business — must NOT leak into another business's wake.
    assert "Cold DMs convert" not in pg_store._ceo_cron_prompt("otherco")


def test_shared_learning_surfaces_cross_business(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "authorco")
    _seed_owned_business(pg_store_dsn, "readerco")
    pg_store.record_learning("authorco", "Pain-first hooks beat feature lists for consumer apps.",
                             tags=["b2c", "consumer"], scope="shared")
    # A shared learning authored by one business surfaces (as a prior) for a DIFFERENT business.
    prompt = pg_store._ceo_cron_prompt("readerco")
    assert "Pain-first hooks" in prompt and "from similar businesses" in prompt


def test_shared_learning_not_shown_across_operators(pg_store_dsn, tmp_path):
    _seed_owned_business(pg_store_dsn, "opaco")
    _seed_owned_business(pg_store_dsn, "opbco")
    store_a = takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn, operator_user_id="op-a")
    store_b = takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn, operator_user_id="op-b")
    store_a.record_learning("opaco", "Operator-A secret: niche subreddit X converts.",
                            tags=["b2c"], scope="shared")
    # Operator B must never see operator A's shared learnings.
    assert "Operator-A secret" not in store_b._ceo_cron_prompt("opbco")


# --- Subuser-security gate (floor 5): live adversarial arm. DO NOT MAKE SUBUSER LESS SECURE -----

def test_subuser_role_denied_direct_writes_to_money_identity_and_ceo_tables(pg_conn):
    """Positively attempt denied writes AS takyon_app_runtime and assert DENIED. Covers the money +
    identity tables (the existing boundary) AND the new RL ceo_* tables / ad-spend rollup (0057).
    A regression that granted the subuser role write on any of these would FAIL here loudly."""
    if not pg_conn.execute("select 1 from pg_roles where rolname='takyon_app_runtime'").fetchone():
        pytest.skip("takyon_app_runtime role absent on this DB")
    must_be_denied = [
        # existing subuser/money/identity boundary
        "app_revenue_events", "app_usage_events", "app_entitlements",
        # new RL operator-plane tables (0057) — subuser must have NO write
        "ceo_episode", "ceo_identity", "ceo_state_of_mind", "ceo_trace",
        "business_ad_spend_entries", "twin_cohort",
    ]
    for tbl in must_be_denied:
        if not pg_conn.execute("select to_regclass(%s) is not null", (f"public.{tbl}",)).fetchone()[0]:
            continue  # table not in this schema build; skip
        pg_conn.execute("set role takyon_app_runtime")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                pg_conn.execute(f'insert into public."{tbl}" default values')
        finally:
            pg_conn.execute("reset role")


def test_subuser_role_denied_writing_0058_attribution_fingerprint_columns(pg_conn):
    """0058 adds attribution_json / card_fingerprint to subuser/money tables. The subuser role
    must remain unable to write them — they are populated only by the privileged ports."""
    if not pg_conn.execute("select 1 from pg_roles where rolname='takyon_app_runtime'").fetchone():
        pytest.skip("takyon_app_runtime role absent on this DB")
    if not pg_conn.execute("select to_regclass('public.app_users') is not null").fetchone()[0]:
        pytest.skip("app_users not in this schema build")
    # app_users.attribution_json — subuser has no table UPDATE, so a direct write is denied.
    pg_conn.execute("set role takyon_app_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("update public.app_users set attribution_json = '{}'::jsonb")
    finally:
        pg_conn.execute("reset role")
    # app_revenue_events is fully denied to the subuser role; the new fingerprint column too.
    pg_conn.execute("set role takyon_app_runtime")
    try:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            pg_conn.execute("update public.app_revenue_events set card_fingerprint = 'x'")
    finally:
        pg_conn.execute("reset role")


def test_subuser_role_cannot_set_rls_bypass_guc(pg_conn):
    """takyon_app_runtime must not be able to flip the RLS-bypass GUC on itself."""
    if not pg_conn.execute("select 1 from pg_roles where rolname='takyon_app_runtime'").fetchone():
        pytest.skip("takyon_app_runtime role absent on this DB")
    pg_conn.execute("set role takyon_app_runtime")
    try:
        # The bypass is gated behind a SECURITY DEFINER function / superuser GUC, never a plain SET
        # the app role can issue. Attempting to grant itself bypass must not succeed silently.
        with pytest.raises(psycopg.errors.Error):
            pg_conn.execute("alter role takyon_app_runtime bypassrls")
    finally:
        pg_conn.execute("reset role")
