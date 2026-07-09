"""RL rails (rl-rails-plan.md) — subjective CEO memory loop, on real Postgres.

Runs against a migrated throwaway DB (the pg_store_dsn fixture applies every migration via the
production runner, so this ALSO validates migration 0057). Skips unless TAKYON_TEST_PG_DSN is set.

Covers the build that landed this pass:
  * R5 wake-injection — the floor-1 byte-carrying channel: a planted state-of-mind note provably
    enters the wake user turn (_ceo_cron_prompt). Proof the "learning channel" is not dead.
  * R1 episode rail — a recorded bet shows up in the next wake's injected memory.
  * R4 pulse baseline — record_pulse persists a snapshot so deltas stop baselining against zero.
  * R8 deterministic slice — episodes carrying quantitative metrics_snapshots distill into
    [measured] lessons through a FIXED significance gate (no model autonomy), idempotently
    (one ceo.episode.observed marker per episode), with evidence linking lesson↔episode; the
    compressed learnings block (dedupe + provenance tiers + char budget) is APPENDED to the
    END of the wake prompt.
"""

from __future__ import annotations

import json
import uuid

import pytest

psycopg = pytest.importorskip("psycopg")

from plugins.takyon import core as takyon_core  # noqa: E402
from plugins.takyon.core import TakyonError  # noqa: E402


def _seed_owned_business(dsn: str, slug: str, *, mode: str = "test", goal: str = "") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id", (f"auth0|{slug}",)
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode, goal) values (%s, %s, %s, %s, %s)",
            (slug, slug.title(), uid, mode, goal or ""),
        )


def _seed_app_users(dsn: str, slug: str, count: int, *, prefix: str = "u") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for i in range(count):
            conn.execute(
                "insert into app_users (business_slug, email) values (%s, %s)",
                (slug, f"{prefix}{i}@example.test"),
            )


def _seed_revenue(dsn: str, slug: str, amount_cents: int, *, revenue_type: str = "checkout") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute(
            "insert into app_revenue_events (business_slug, amount_paid_cents, revenue_type) values (%s, %s, %s)",
            (slug, amount_cents, revenue_type),
        )


def _seed_usage_events(dsn: str, slug: str, count: int, *, prefix: str = "rk") -> None:
    with psycopg.connect(dsn, autocommit=True) as conn:
        for i in range(count):
            conn.execute(
                "insert into app_usage_events (business_slug, reservation_key) values (%s, %s)",
                (slug, f"{prefix}-{slug}-{i}"),
            )


def _upsert_reddit_policy(dsn: str, business: str, campaign_slug: str, *,
                          spend_cents: int, status: str = "active") -> None:
    from datetime import datetime, timedelta, timezone

    from plugins.takyon import business_ad_spend

    with psycopg.connect(dsn, autocommit=True) as conn:
        business_ad_spend.upsert_policy(
            conn,
            business_slug=business, channel="reddit", slug=campaign_slug,
            reservation_key=f"resv-{business}-{campaign_slug}",
            reserved_credits=2000, daily_budget_cents=500, total_budget_cents=2000,
            start_at=datetime.now(timezone.utc) - timedelta(days=1),
            end_at=datetime.now(timezone.utc) + timedelta(days=3),
            status=status, last_synced_spend_cents=spend_cents,
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
    _seed_owned_business(pg_store_dsn, "authorco", goal="b2c consumer growth")
    _seed_owned_business(pg_store_dsn, "readerco", goal="b2c consumer habit tracker")
    pg_store.record_learning("authorco", "Pain-first hooks beat feature lists for consumer apps.",
                             tags=["b2c", "consumer"], scope="shared")
    # A shared learning authored by one business surfaces (as a prior) for a DIFFERENT business
    # whose declared tags overlap the lesson's tags.
    prompt = pg_store._ceo_cron_prompt("readerco")
    assert "Pain-first hooks" in prompt and "from similar businesses" in prompt


def test_shared_learning_zero_tag_overlap_is_dropped(pg_store, pg_store_dsn):
    # Both sides declared tags and NONE match: the borrowed lesson is topical noise for this
    # business and must not fill a slot just because slots exist.
    _seed_owned_business(pg_store_dsn, "chessco", goal="chess coaching marketplace")
    _seed_owned_business(pg_store_dsn, "gymco", goal="fitness gym bookings")
    pg_store.record_learning("chessco", "Chess IM endorsements beat generic ads.",
                             tags=["chess", "coaching"], scope="shared")
    assert "Chess IM endorsements" not in pg_store._ceo_cron_prompt("gymco")


def test_shared_learning_not_shown_across_operators(pg_store_dsn, tmp_path):
    # Give both businesses the SAME topical tags so tag-overlap retrieval would surface the
    # lesson — proving the exclusion below happens on the OPERATOR boundary, nothing else.
    _seed_owned_business(pg_store_dsn, "opaco", goal="b2c consumer growth")
    _seed_owned_business(pg_store_dsn, "opbco", goal="b2c consumer growth")
    store_a = takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn, operator_user_id="op-a")
    store_b = takyon_core.TakyonStore(root=tmp_path, database_url=pg_store_dsn, operator_user_id="op-b")
    store_a.record_learning("opaco", "Operator-A secret: niche subreddit X converts.",
                            tags=["b2c"], scope="shared")
    # Positive control: the same lesson DOES surface for another business of the SAME operator...
    _seed_owned_business(pg_store_dsn, "opa2co", goal="b2c consumer growth")
    assert "Operator-A secret" in store_a._ceo_cron_prompt("opa2co")
    # ...and operator B must never see operator A's shared learnings.
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


# --- RL observability CLI layer (read projections + review write, all from events) ----------

def test_rl_lessons_effective_status_from_reviews(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "statco")
    a = pg_store.record_learning("statco", "lesson A", scope="business")["event_id"]
    b = pg_store.record_learning("statco", "lesson B", scope="shared", tags=["x"])["event_id"]
    L = {l["id"]: l for l in pg_store.rl_lessons("statco")["lessons"]}
    assert L[a]["status"] == "candidate" and L[b]["status"] == "candidate"
    assert L[a]["human_reviewed"] is False
    pg_store.rl_review_lesson(a, "approve")
    pg_store.rl_review_lesson(b, "reject", reason="noise")
    L = {l["id"]: l for l in pg_store.rl_lessons("statco")["lessons"]}
    assert L[a]["status"] == "proven" and L[a]["human_reviewed"] is True
    assert L[b]["status"] == "retired"


def test_reject_removes_lesson_from_injection(pg_store, pg_store_dsn):
    # the operator's reject must actually remove the lesson from what the CEO is shown.
    _seed_owned_business(pg_store_dsn, "rejco")
    lid = pg_store.record_learning("rejco", "Bad lesson spam everyone", scope="business")["event_id"]
    assert "Bad lesson spam everyone" in pg_store._ceo_cron_prompt("rejco")
    pg_store.rl_review_lesson(lid, "reject", reason="harmful")
    assert "Bad lesson spam everyone" not in pg_store._ceo_cron_prompt("rejco")


def test_rl_review_unknown_lesson_raises(pg_store):
    with pytest.raises(TakyonError):
        pg_store.rl_review_lesson("00000000-0000-0000-0000-000000000000", "approve")


def test_rl_why_reconstructs_bet_and_context(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "whyco")
    pg_store.set_identity("whyco", "B2C habit tracker")
    pg_store.open_state_of_mind("whyco", "left off testing reddit")
    ep = pg_store.record_episode("whyco", "try a pricing test", channel="pricing")["episode_id"]
    w = pg_store.rl_why(ep)
    assert w["bet"]["hypothesis"] == "try a pricing test" and w["bet"]["channel"] == "pricing"
    assert w["context_before"]["identity"] == "B2C habit tracker"
    assert w["context_before"]["state_of_mind"] == "left off testing reddit"
    assert w["settled"] is False and w["outcome"] is None  # truthful: not invented
    assert w["observed"] is False and w["observation"] is None  # distiller has not judged it yet


def test_rl_status_counts_are_real(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "cntco")
    pg_store.record_episode("cntco", "bet one")
    pg_store.record_learning("cntco", "lesson one", scope="business")
    s = pg_store.rl_status("cntco")
    assert s["episodes_opened"] == 1 and s["episodes_settled"] == 0 and s["lessons_total"] == 1


def test_rl_policy_is_the_injected_text(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "polco")
    pg_store.set_identity("polco", "thesis: streaks drive retention")
    pg_store.record_learning("polco", "ship weekly digests", scope="business")
    p = pg_store.rl_policy("polco")
    assert p["identity"] == "thesis: streaks drive retention"
    assert "ship weekly digests" in p["active_intra_learnings"]
    # IS the policy, not a description: exactly the prepended memory + the appended learnings.
    assert p["injected_memory"] == pg_store._assemble_wake_memory("polco")
    assert p["injected_learnings"] == pg_store._assemble_wake_learnings("polco")
    assert p["injected_text"] == p["injected_memory"] + "\n\n" + p["injected_learnings"]
    assert "ship weekly digests" in p["injected_learnings"]


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


# --- R8 deterministic slice: metrics→lessons distillation (fixed gate, no model autonomy) ----
#
# Every episode records a quantitative metrics_snapshot at open time (R1). The distiller
# measures before→now deltas once the episode matures and KEEPS the episode as a lesson iff a
# fixed significance threshold is crossed — then the lesson rides the compressed block APPENDED
# to the END of the wake prompt. These tests backtest the whole chain on real Postgres:
# episode → distill (significance / idempotency / dry-run / age gates) → lesson (evidence,
# source) → end-of-prompt injection (position, markers, dedupe, char budget, human review).


def _flat(snap):
    return takyon_core.TakyonStore._flatten_metrics_snapshot(snap)


def test_flatten_metrics_snapshot_shapes():
    # product counters + campaign sums + x totals; non-numeric keys dropped; spend not
    # double-counted when both cents and usd are present.
    snap = {
        "captured_at": "2026-07-04T00:00:00+00:00",
        "users": 7, "revenue_cents": 1500, "usage_events": 42,
        "campaigns": [
            {"slug": "c1", "status": "active", "spend_cents": 620, "impressions": 2412, "clicks": 38, "spend_usd": 6.20},
            {"slug": "c2", "status": "paused", "spend_usd": 1.50, "impressions": 100},
        ],
        "x": {"views": 900, "likes": 12},
    }
    flat = _flat(snap)
    assert flat["users"] == 7 and flat["revenue_cents"] == 1500 and flat["usage_events"] == 42
    assert flat["impressions"] == 2512 and flat["clicks"] == 38
    assert flat["spend_cents"] == 620 + 150  # c1 counted once from cents; c2 folded from usd
    assert flat["views"] == 900 and flat["likes"] == 12
    assert "captured_at" not in flat
    assert _flat(None) == {} and _flat({"captured_at": "x"}) == {}


def test_significant_metric_moves_thresholds():
    moves = takyon_core.TakyonStore._significant_metric_moves(
        {"users": 2, "revenue_cents": 100, "usage_events": -25, "clicks": 24, "spend_cents": 499})
    # users below threshold, clicks below, spend below; revenue at threshold and a usage DROP
    # (absolute value) both count.
    assert moves == {"revenue_cents": 100.0, "usage_events": -25.0}
    assert takyon_core.TakyonStore._significant_metric_moves({}) == {}


def test_compose_measured_claim_positive_and_spend_only():
    positive = takyon_core.TakyonStore._compose_measured_claim(
        "post pain-first thread", {"users": 5.0, "revenue_cents": 1000.0},
        window_hours=18.0, channel="reddit")
    assert positive.startswith("Measured: 'post pain-first thread' on reddit -> ")
    assert "+$10.00 revenue" in positive and "+5 users" in positive and "in 18h" in positive
    spend_only = takyon_core.TakyonStore._compose_measured_claim(
        "boost with ads", {"spend_cents": 1800.0}, window_hours=30.0, channel="meta")
    assert "no significant movement despite $18.00 ad spend" in spend_only


def test_distill_keeps_significant_episode_as_measured_lesson(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "sigco")
    out = pg_store.record_episode("sigco", "launch referral loop", channel="referral")
    episode_id = out["episode_id"]
    # the measured world moves AFTER the bet: +5 users (threshold 3), +$10.00 (threshold $1)
    _seed_app_users(pg_store_dsn, "sigco", 5)
    _seed_revenue(pg_store_dsn, "sigco", 1000)
    result = pg_store.distill_episode_lessons("sigco")
    assert result["distilled"] == 1 and result["errors"] == 0
    assert result["lessons"][0]["episode_id"] == episode_id
    lessons = pg_store.rl_lessons("sigco")["lessons"]
    auto = [l for l in lessons if l["source"] == "auto:metrics"]
    assert len(auto) == 1
    assert auto[0]["status"] == "candidate"
    assert auto[0]["claim"].startswith("Measured: 'launch referral loop'")
    assert "+5 users" in auto[0]["claim"] and "+$10.00 revenue" in auto[0]["claim"]
    # evidence carries the exact numbers + the episode id (lesson↔episode linkage).
    ev = auto[0]["evidence"][0]
    assert ev["episode_id"] == episode_id
    assert ev["deltas"]["users"] == 5 and ev["deltas"]["revenue_cents"] == 1000
    assert ev["before"]["users"] == 0 and ev["after"]["users"] == 5
    # idempotent: the episode was observed exactly once; a re-run mints nothing new.
    again = pg_store.distill_episode_lessons("sigco")
    assert again["checked"] == 0 and again["distilled"] == 0
    assert len([l for l in pg_store.rl_lessons("sigco")["lessons"] if l["source"] == "auto:metrics"]) == 1
    status = pg_store.rl_status("sigco")
    assert status["episodes_observed"] == 1 and status["lessons_auto_distilled"] == 1
    assert status["episodes_settled"] == 0  # observation is NOT settlement
    # rl_why surfaces the deterministic observation (before/after/deltas) without settling.
    why = pg_store.rl_why(episode_id)
    assert why["observed"] is True and why["observation"]["significant"] is True
    assert why["observation"]["deltas"]["users"] == 5
    assert why["settled"] is False
    # evidence is FROZEN at observation: movement after the judgment never rewrites it.
    _seed_app_users(pg_store_dsn, "sigco", 50, prefix="post-observation")
    pg_store.distill_episode_lessons("sigco")
    frozen = [l for l in pg_store.rl_lessons("sigco")["lessons"] if l["source"] == "auto:metrics"][0]
    assert frozen["evidence"][0]["deltas"]["users"] == 5


def test_distill_insignificant_episode_writes_marker_but_no_lesson(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "flatco")
    pg_store.record_episode("flatco", "tweak headline copy", channel="site")
    _seed_app_users(pg_store_dsn, "flatco", 2)  # +2 users: below the 3-user threshold
    result = pg_store.distill_episode_lessons("flatco")
    assert result["insignificant"] == 1 and result["distilled"] == 0
    assert pg_store.rl_lessons("flatco")["lessons"] == []
    status = pg_store.rl_status("flatco")
    assert status["episodes_observed"] == 1 and status["lessons_total"] == 0
    # judged exactly once: later unrelated growth can never be re-attributed to this old bet.
    _seed_app_users(pg_store_dsn, "flatco", 50, prefix="later")
    assert pg_store.distill_episode_lessons("flatco")["checked"] == 0
    assert pg_store.rl_lessons("flatco")["lessons"] == []


def test_distill_min_age_gate_keeps_fresh_episodes_pending(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "youngco")
    pg_store.record_episode("youngco", "try a pricing test", channel="pricing")
    result = pg_store.distill_episode_lessons("youngco")  # default min age: 12h
    assert result["pending"] == 1 and result["distilled"] == 0
    assert pg_store.rl_status("youngco")["episodes_observed"] == 0  # not judged, still pending


def test_distill_stale_episode_is_marked_never_misattributed(pg_store, pg_store_dsn, monkeypatch):
    from datetime import datetime, timedelta, timezone
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "oldco")
    pg_store.record_episode("oldco", "ancient bet", channel="reddit")
    _seed_app_users(pg_store_dsn, "oldco", 25)  # big movement, but the window is too old
    future = datetime.now(timezone.utc) + timedelta(days=30)
    result = pg_store.distill_episode_lessons("oldco", now=future)
    assert result["stale"] == 1 and result["distilled"] == 0
    assert pg_store.rl_lessons("oldco")["lessons"] == []
    assert pg_store.rl_status("oldco")["episodes_observed"] == 1


def test_distill_dry_run_writes_nothing(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "dryco")
    pg_store.record_episode("dryco", "dry run bet", channel="referral")
    _seed_app_users(pg_store_dsn, "dryco", 5)
    preview = pg_store.distill_episode_lessons("dryco", dry_run=True)
    assert preview["dry_run"] is True and preview["distilled"] == 1
    assert pg_store.rl_lessons("dryco")["lessons"] == []
    assert pg_store.rl_status("dryco")["episodes_observed"] == 0
    # the dry run consumed nothing: the real pass still distills the episode.
    real = pg_store.distill_episode_lessons("dryco")
    assert real["distilled"] == 1
    assert len(pg_store.rl_lessons("dryco")["lessons"]) == 1


def test_distill_spend_without_movement_mints_negative_lesson(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "burnco")
    pg_store.record_episode("burnco", "scale winning ad set", channel="reddit")
    # After the bet: $9.00 of ad spend landed, nothing else moved. Patch only the AFTER
    # snapshot (the stored episode already carries the real before snapshot).
    real_snapshot = takyon_core._episode_metrics_snapshot

    def after_with_spend(store, conn, slug, channel):
        snap = real_snapshot(store, conn, slug, channel)
        snap["campaigns"] = [{"slug": "cmp", "status": "active", "spend_cents": 900,
                              "impressions": 40, "clicks": 1}]
        return snap

    monkeypatch.setattr(takyon_core, "_episode_metrics_snapshot", after_with_spend)
    result = pg_store.distill_episode_lessons("burnco")
    assert result["distilled"] == 1
    claim = pg_store.rl_lessons("burnco")["lessons"][0]["claim"]
    assert "no significant movement despite $9.00 ad spend" in claim


def test_distill_legacy_episode_without_snapshot_marked_no_baseline(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "legacyco")
    # A pre-metrics episode (no metrics_snapshot key at all), written directly to events.
    with pg_store._connect() as conn:
        pg_store._record_event(
            conn, scope="business:legacyco", business_slug="legacyco",
            event_type="ceo.episode.opened",
            payload={"episode_id": "legacy-1", "hypothesis": "old bet", "opened_at": takyon_core._now()},
        )
    result = pg_store.distill_episode_lessons("legacyco")
    assert result["no_baseline"] == 1 and result["distilled"] == 0
    assert pg_store.rl_status("legacyco")["episodes_observed"] == 1


def test_measured_lesson_is_appended_to_the_end_of_the_wake_prompt(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "endco")
    pg_store.set_identity("endco", "B2C referral-led growth co")
    pg_store.record_episode("endco", "launch referral loop", channel="referral")
    _seed_app_users(pg_store_dsn, "endco", 5)
    assert pg_store.distill_episode_lessons("endco")["distilled"] == 1
    prompt = pg_store._ceo_cron_prompt("endco")
    # the learnings block is APPENDED: it is the prompt's tail, after every wake instruction.
    assert prompt.rstrip().endswith("== end learnings ==")
    claim_pos = prompt.index("Measured: 'launch referral loop'")
    assert claim_pos > prompt.index("CEO wakeup for business:endco")
    assert claim_pos > prompt.index("do not start additional work beyond the capped tasks")
    assert "[measured]" in prompt
    # while the memory block stays PREPENDED at the top.
    assert prompt.index("== Your memory") == 0
    assert "Who you are: B2C referral-led growth co" in prompt


def test_learnings_block_dedupes_near_duplicates(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "dupco")
    for n in (30, 42, 55):
        pg_store.record_learning("dupco", f"Reddit ads got {n} clicks this week", scope="business")
    prompt = pg_store._ceo_cron_prompt("dupco")
    assert prompt.count("Reddit ads got") == 1  # collapsed to one line
    assert "(x3)" in prompt  # ...that admits how often it recurred


def test_learnings_block_respects_char_budget_many_lessons(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_LESSONS_CHAR_BUDGET", "600")
    _seed_owned_business(pg_store_dsn, "packco")
    for i in range(30):
        pg_store.record_learning(
            "packco", f"Distinct lesson number {i:02d}: audience segment {i:02d} responded to angle {i:02d}.",
            scope="business")
    prompt = pg_store._ceo_cron_prompt("packco")
    block = prompt[prompt.index("== Learnings"):]
    # the packed lines fit the budget (block adds only headers/frame around them)
    assert len(block) < 600 + 300
    assert "Distinct lesson number 29" in block   # newest packed first
    assert "Distinct lesson number 00" not in block  # oldest crowded out, not the budget blown


def test_approved_lesson_outranks_newer_candidates_under_budget(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_LESSONS_CHAR_BUDGET", "600")
    _seed_owned_business(pg_store_dsn, "provenco")
    old = pg_store.record_learning("provenco", "The ONE proven play: found-money onboarding email.",
                                   scope="business")["event_id"]
    pg_store.rl_review_lesson(old, "approve")
    for i in range(30):
        pg_store.record_learning(
            "provenco", f"Newer unreviewed narrative lesson {i:02d} that would otherwise crowd.",
            scope="business")
    prompt = pg_store._ceo_cron_prompt("provenco")
    # approve is no longer cosmetic: the proven lesson survives 30 newer candidates.
    assert "[proven] The ONE proven play" in prompt


def test_rejecting_a_measured_lesson_removes_it_from_the_prompt(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "vetoco")
    pg_store.record_episode("vetoco", "launch referral loop", channel="referral")
    _seed_app_users(pg_store_dsn, "vetoco", 5)
    pg_store.distill_episode_lessons("vetoco")
    lesson = [l for l in pg_store.rl_lessons("vetoco")["lessons"] if l["source"] == "auto:metrics"][0]
    assert "Measured:" in pg_store._ceo_cron_prompt("vetoco")
    pg_store.rl_review_lesson(lesson["id"], "reject", reason="misattributed")
    assert "Measured:" not in pg_store._ceo_cron_prompt("vetoco")


def test_distill_nets_refunds_in_revenue_delta(pg_store, pg_store_dsn, monkeypatch):
    # Reversal rows (refunds/chargebacks) are stored with a POSITIVE amount_paid_cents but
    # revenue_type='reversal'; the snapshot must NET them (like get_revenue_summary), or a
    # refund inside the window would mint a fake positive-revenue lesson.
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "refundco")
    _seed_revenue(pg_store_dsn, "refundco", 3000)  # $30 lifetime before the bet
    pg_store.record_episode("refundco", "raise price to $19", channel="pricing")
    _seed_revenue(pg_store_dsn, "refundco", 1000, revenue_type="reversal")  # $10 refund lands
    result = pg_store.distill_episode_lessons("refundco")
    assert result["distilled"] == 1
    lesson = pg_store.rl_lessons("refundco")["lessons"][0]
    ev = lesson["evidence"][0]
    assert ev["before"]["revenue_cents"] == 3000
    assert ev["after"]["revenue_cents"] == 2000       # 3000 - 1000 refund
    assert ev["deltas"]["revenue_cents"] == -1000     # a measured DROP, not a fake gain
    assert "-$10.00 revenue" in lesson["claim"]


def test_distill_usage_events_delta(pg_store, pg_store_dsn, monkeypatch):
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "useco")
    _seed_usage_events(pg_store_dsn, "useco", 5, prefix="base")   # baseline 5 lifetime events
    pg_store.record_episode("useco", "ship onboarding checklist", channel="product")
    _seed_usage_events(pg_store_dsn, "useco", 25, prefix="win")   # +25 in the window (>= 20)
    result = pg_store.distill_episode_lessons("useco")
    assert result["distilled"] == 1
    lesson = pg_store.rl_lessons("useco")["lessons"][0]
    ev = lesson["evidence"][0]
    assert ev["before"]["usage_events"] == 5 and ev["after"]["usage_events"] == 30
    assert ev["deltas"] == {"usage_events": 25}
    assert "+25 usage events" in lesson["claim"]


def test_distill_campaign_delivery_deltas_survive_completion(pg_store, pg_store_dsn, monkeypatch):
    # Full reddit-campaign chain: policy row + insights receipts before AND after, with the
    # campaign COMPLETING inside the window — the after snapshot must still see it (a live-only
    # status set would drop it and mint phantom negative deltas).
    import os as _os

    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "adco")
    _upsert_reddit_policy(pg_store_dsn, "adco", "camp-1", spend_cents=200, status="active")
    receipts = pg_store._resolve_business_file("adco", "metrics/reddit-ads/camp-1/syncs", sync=False)
    receipts.mkdir(parents=True, exist_ok=True)
    (receipts / "t1.json").write_text(json.dumps(
        {"totals": {"impressions": 1000, "clicks": 10, "spend_usd": 2.0}}))
    old = (receipts / "t1.json").stat().st_mtime - 60
    _os.utime(receipts / "t1.json", (old, old))  # ensure t2 is strictly the latest receipt
    pg_store.record_episode("adco", "scale the winning ad set", channel="reddit")
    # window: +4,000 impressions, +40 clicks, +$6.00 spend; campaign settles at its cap.
    _upsert_reddit_policy(pg_store_dsn, "adco", "camp-1", spend_cents=800, status="completed")
    (receipts / "t2.json").write_text(json.dumps(
        {"totals": {"impressions": 5000, "clicks": 50, "spend_usd": 8.0}}))
    result = pg_store.distill_episode_lessons("adco")
    assert result["distilled"] == 1 and result["errors"] == 0
    lesson = [l for l in pg_store.rl_lessons("adco")["lessons"] if l["source"] == "auto:metrics"][0]
    ev = lesson["evidence"][0]
    assert ev["before"]["impressions"] == 1000 and ev["after"]["impressions"] == 5000
    assert ev["before"]["spend_cents"] == 200 and ev["after"]["spend_cents"] == 800
    assert ev["deltas"] == {"clicks": 40, "impressions": 4000, "spend_cents": 600}
    assert "+4000 impressions" in lesson["claim"] and "+40 clicks" in lesson["claim"]
    assert "(spend $6.00)" in lesson["claim"]


def test_distill_x_deltas_from_summary_totals(pg_store, pg_store_dsn, monkeypatch):
    # Full X chain: summary.json totals before and after, raw X API names mapped, every X
    # metric family's delta computed against its own baseline.
    monkeypatch.setenv("TAKYON_RL_DISTILL_MIN_AGE_HOURS", "0")
    _seed_owned_business(pg_store_dsn, "xco")
    summary_abs = pg_store._resolve_business_file("xco", "metrics/x/summary.json", sync=False)
    summary_abs.parent.mkdir(parents=True, exist_ok=True)

    def write_summary(imp, likes, replies, retweets, quotes, clicks):
        summary_abs.write_text(json.dumps({"totals": {
            "public_metrics": {"impression_count": imp, "like_count": likes,
                               "reply_count": replies, "retweet_count": retweets,
                               "quote_count": quotes},
            "non_public_metrics": {"url_link_clicks": clicks},
        }}))

    write_summary(4000, 40, 2, 3, 1, 5)
    pg_store.record_episode("xco", "post pain-first thread", channel="x")
    write_summary(9100, 95, 9, 10, 4, 31)
    result = pg_store.distill_episode_lessons("xco")
    assert result["distilled"] == 1
    lesson = pg_store.rl_lessons("xco")["lessons"][0]
    ev = lesson["evidence"][0]
    assert ev["before"]["impressions"] == 4000 and ev["after"]["impressions"] == 9100
    assert ev["before"]["reposts"] == 4 and ev["after"]["reposts"] == 14  # retweets + quotes
    # every family crosses its threshold: +5100 imp, +55 likes, +7 replies, +10 reposts, +26 clicks
    assert ev["deltas"] == {"clicks": 26, "impressions": 5100, "likes": 55,
                            "replies": 7, "reposts": 10}
    assert "+5100 impressions" in lesson["claim"] and "+55 likes" in lesson["claim"]


def test_distill_after_sample_taken_at_first_matured_evaluation(pg_store, pg_store_dsn):
    # The "after" read happens at the FIRST evaluation past the maturity gate — not before,
    # and never again after. Uses the real default 12h gate with injected clocks.
    from datetime import timedelta

    _seed_owned_business(pg_store_dsn, "gateco")
    ep = pg_store.record_episode("gateco", "try referral rewards", channel="referral")["episode_id"]
    opened_at = takyon_core._parse_iso_datetime(pg_store.rl_why(ep)["bet"]["opened_at"])
    _seed_app_users(pg_store_dsn, "gateco", 5)
    early = pg_store.distill_episode_lessons("gateco", now=opened_at + timedelta(hours=11, minutes=59))
    assert early["pending"] == 1 and early["distilled"] == 0
    assert pg_store.rl_status("gateco")["episodes_observed"] == 0  # not sampled early
    judged = pg_store.distill_episode_lessons("gateco", now=opened_at + timedelta(hours=12, minutes=1))
    assert judged["distilled"] == 1
    ev = pg_store.rl_lessons("gateco")["lessons"][0]["evidence"][0]
    assert 12.0 <= ev["window_hours"] <= 12.05  # window = open -> first matured evaluation


def test_record_learning_evidence_roundtrip(pg_store, pg_store_dsn):
    _seed_owned_business(pg_store_dsn, "evco")
    pg_store.record_learning("evco", "DM-first outreach beats cold posts",
                             evidence=["episode:abc123", "metrics/x/syncs/2026-07-01.json"],
                             scope="business")
    lesson = pg_store.rl_lessons("evco")["lessons"][0]
    assert lesson["evidence"] == ["episode:abc123", "metrics/x/syncs/2026-07-01.json"]
    # evidence-backed model lessons render with the [measured] provenance marker too.
    assert "[measured] DM-first outreach beats cold posts" in pg_store._ceo_cron_prompt("evco")
