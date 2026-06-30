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
