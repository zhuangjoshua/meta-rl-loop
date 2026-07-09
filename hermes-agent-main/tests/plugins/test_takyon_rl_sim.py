"""RL-loop test environment (plugins/takyon/rl_sim.py) — the acceptance assertions.

Encodes the operator-defined per-business process the harness exists to prove:
  1. choose skill  2. run ads/seo  3. next wake: full-cost ROAS (creation + spend; signup
  profit, page visits for seo)  4. append process+metrics to the skill's per-business run
  history (its working-prompt feedback)  5. re-run and check ROAS improves.

Two independently tested feedback levels:
  * ALLOCATION — the REAL loop's injected wake learnings steer the skill choice toward the
    highest-ROAS arm (regret / %-optimal / lesson-rank vs known ground truth).
  * EXECUTION  — the appended skill history steers the variant choice inside one skill, so
    the SAME skill's ROAS improves run-over-run.

Driven by the fast scripted policy (the real-CEO driver is the same seam but costs tokens,
so it is not a CI path) against a synthetic world with known per-channel ROAS. Runs against
a migrated throwaway Postgres (pg_store_dsn); skips unless TAKYON_TEST_PG_DSN is set — same
posture as test_takyon_rl_rails.py. The store is Postgres-only.
"""

from __future__ import annotations

import random

import pytest

pytest.importorskip("psycopg")

from plugins.takyon import rl_sim  # noqa: E402


def _run(dsn, root, *, inject, skill_feedback=True, wakes=60, seed=11, epsilon=0.06):
    return rl_sim.run_simulation(
        dsn=dsn,
        world=rl_sim.default_world(seed, noise=0.1),
        chooser=rl_sim.ScriptedChooser(random.Random(seed), epsilon=epsilon),
        wakes=wakes,
        slug=rl_sim._fresh_slug("rlsimtest"),
        root=root,
        inject=inject,
        skill_feedback=skill_feedback,
    )


class _AlwaysSkill(rl_sim.Chooser):
    """Pins the skill choice so a test can isolate the EXECUTION level; variant choice
    delegates to a ScriptedChooser (which reads the appended history)."""

    def __init__(self, skill: str, seed: int = 5, epsilon: float = 0.0) -> None:
        self.skill = skill
        self._inner = rl_sim.ScriptedChooser(random.Random(seed), epsilon=epsilon)

    def choose(self, injected_text, arms, t):
        return self.skill

    def choose_variant(self, skill, skill_prompt_text, variants, t):
        return self._inner.choose_variant(skill, skill_prompt_text, variants, t)


# --- allocation level: the real loop's lessons steer the skill choice ----------------------

def test_injected_memory_converges_on_the_best_roas_arm(pg_store_dsn, tmp_path):
    """With injection ON the scripted policy must learn — from the loop's own [measured]
    lessons — that seo (ROAS 2.1) beats reddit (1.3) beats meta (0.7), and lock onto seo."""
    rep = _run(pg_store_dsn, tmp_path, inject=True)
    s = rep.summary()
    # The loop's lessons recovered the true ROAS ranking, best arm first.
    assert s["best_arm_identified"], s
    assert s["learned_rank"][0] == "seo", s
    # And the policy exploits it: the tail of the run is overwhelmingly the optimal arm.
    assert s["pct_optimal_last_quartile"] >= 0.7, s
    # seo was pulled more than either ad channel.
    assert s["pulls"]["seo"] > s["pulls"]["reddit"], s
    assert s["pulls"]["seo"] > s["pulls"]["meta"], s


def test_rl_memory_beats_the_memory_off_ablation(pg_store_dsn, tmp_path):
    """The headline: turning the feedback ON measurably lowers regret and raises optimal-
    choice rate vs the identical world with all feedback OFF (a memoryless agent can only
    explore uniformly). This is the number to watch while iterating on the loop."""
    on = _run(pg_store_dsn, tmp_path, inject=True).summary()
    off = _run(pg_store_dsn, tmp_path, inject=False, skill_feedback=False).summary()
    assert on["cumulative_regret_usd"] < off["cumulative_regret_usd"], (on, off)
    assert on["pct_optimal_last_quartile"] > off["pct_optimal_last_quartile"], (on, off)
    assert on["realized_roas"] > off["realized_roas"], (on, off)
    # Memory-off has no basis to prefer, so it stays near uniform (~1/3 optimal), never converging.
    assert off["convergence_wake"] is None, off


def test_single_arm_per_period_produces_a_measured_seo_lesson(pg_store_dsn, tmp_path):
    """Sanity on the plumbing: a few forced seo periods must mint a real [measured] lesson that
    names seo and a positive revenue delta — i.e. the world's outcome flowed through the REAL
    _episode_metrics_snapshot -> distill path, not a reimplementation."""
    rep = rl_sim.run_simulation(
        dsn=pg_store_dsn, world=rl_sim.default_world(3, noise=0.05),
        chooser=_AlwaysSkill("seo"), wakes=6, slug=rl_sim._fresh_slug("rlsimseo"), root=tmp_path,
    )
    measured = [l for l in rep.lessons if str(l.get("source") or "").startswith("auto:")]
    assert measured, rep.lessons
    assert any("seo" in str(l.get("claim") or "") and "revenue" in str(l.get("claim") or "")
               for l in measured), measured
    # Every distilled lesson carries before/after/delta evidence linking it to its episode.
    assert all(l.get("evidence") for l in measured), measured


# --- execution level: the appended skill history improves the SAME skill's ROAS ------------
# (process steps 4+5: append process+metrics to the skill prompt, re-run, ROAS improves)

def test_skill_history_feedback_improves_roas_run_over_run(pg_store_dsn, tmp_path):
    """Pin the skill to seo, OPT INTO execution variants (the controllable improvement lever),
    and let ONLY the appended run history guide the choice — the wake-learnings channel is
    explicitly OFF (inject=False), so this is the operator's skill-scoped loop in isolation:
    late runs must lock onto the best variant and out-ROAS the early (exploring) runs —
    'append feedback -> re-run -> ROAS improves', measured."""
    rep = rl_sim.run_simulation(
        dsn=pg_store_dsn,
        world=rl_sim.default_world(5, noise=0.05, variants=rl_sim._DEFAULT_VARIANTS),
        chooser=_AlwaysSkill("seo", seed=5, epsilon=0.0), wakes=24,
        slug=rl_sim._fresh_slug("rlsimexec"), root=tmp_path,
        inject=False, skill_feedback=True,
    )
    imp = rep.skill_improvement()["seo"]
    assert imp["runs"] == 24, imp
    assert imp["improved"], imp
    assert imp["best_variant_share_late"] >= 0.7, imp


def test_without_skill_history_the_same_skill_never_improves(pg_store_dsn, tmp_path):
    """Ablation for the execution level: with the history feedback OFF, every run looks like
    a cold start (untried variants cycle forever), so late ROAS shows no lock-on — and the
    feedback-ON run beats it. The improvement is attributable to the appended history."""
    on = rl_sim.run_simulation(
        dsn=pg_store_dsn,
        world=rl_sim.default_world(5, noise=0.05, variants=rl_sim._DEFAULT_VARIANTS),
        chooser=_AlwaysSkill("seo", seed=5, epsilon=0.0), wakes=24,
        slug=rl_sim._fresh_slug("rlsimexecon"), root=tmp_path,
        inject=False, skill_feedback=True,
    ).skill_improvement()["seo"]
    off = rl_sim.run_simulation(
        dsn=pg_store_dsn,
        world=rl_sim.default_world(5, noise=0.05, variants=rl_sim._DEFAULT_VARIANTS),
        chooser=_AlwaysSkill("seo", seed=5, epsilon=0.0), wakes=24,
        slug=rl_sim._fresh_slug("rlsimexecoff"), root=tmp_path,
        inject=False, skill_feedback=False,
    ).skill_improvement()["seo"]
    assert on["late_roas"] > off["late_roas"], (on, off)
    assert on["best_variant_share_late"] > off["best_variant_share_late"], (on, off)


def test_skill_history_file_carries_the_entire_process_and_metrics(pg_store_dsn, tmp_path):
    """Process step 4 verbatim: the per-business file metrics/roas/<skill>.md exists in the
    REAL business filesystem and each entry carries the process narrative + all metrics —
    creation cost, spend, conversions, profit, total cost, ROAS (and page visits for seo).
    Default (variantless) mode: the run IS the skill, so no variant noise in the entries."""
    slug = rl_sim._fresh_slug("rlsimhist")
    rl_sim.run_simulation(
        dsn=pg_store_dsn, world=rl_sim.default_world(9, noise=0.05),
        chooser=_AlwaysSkill("seo"), wakes=3, slug=slug, root=tmp_path,
    )
    store = rl_sim.TakyonStore(root=tmp_path, database_url=pg_store_dsn,
                               operator_user_id="rlsim-op")
    text = rl_sim._read_skill_history(store, slug, "seo")
    assert text, "skill history file missing"
    assert text.count("- run ") == 3, text
    for token in ("process:", "created the seo deliverable for $", "page_visits",
                  "signups", "profit $", "total cost $", "ROAS "):
        assert token in text, (token, text)
    assert "variant=" not in text, text  # no execution lever -> no variant noise


# --- backtest (replay) level ----------------------------------------------------------------

def test_replay_backtest_drives_the_same_loop_and_scoreboard(pg_store_dsn, tmp_path):
    """BACKTEST path: a hand-authored per-period per-channel ROAS table (the shape you'd build
    from real attributed campaign data) replays through the SAME loop + scoreboard. reddit is
    the standing best arm; the loop must identify it and allocate there — proving ReplayWorld
    drops into the synthetic-world seam unchanged."""
    # reddit best every period; a couple of noisy periods where meta spikes (exploration bait).
    table = []
    for t in range(40):
        table.append({
            "meta": 2.0 if t in (5, 12) else 0.6,
            "reddit": 1.8,
            "seo": 1.0,
        })
    world = rl_sim.ReplayWorld(table, budget_cents=1000, rng=random.Random(1))
    rep = rl_sim.run_simulation(
        dsn=pg_store_dsn, world=world,
        chooser=rl_sim.ScriptedChooser(random.Random(1), epsilon=0.05),
        wakes=len(table), slug=rl_sim._fresh_slug("rlsimreplay"), root=tmp_path,
    )
    s = rep.summary()
    assert s["best_arm_identified"], s
    assert s["learned_rank"][0] == "reddit", s
    assert s["pulls"]["reddit"] > s["pulls"]["seo"], s


def test_load_replay_table_csv_and_json_agree(tmp_path):
    """Pure unit — no DB. The CSV (period,channel,roas) and JSON record loaders densify to the
    same contiguous per-period table."""
    import json as _json

    csv_path = tmp_path / "t.csv"
    csv_path.write_text("period,channel,roas\n0,reddit,1.3\n0,seo,2.1\n1,reddit,1.4\n1,seo,2.0\n")
    json_path = tmp_path / "t.json"
    json_path.write_text(_json.dumps([
        {"period": 0, "channel": "reddit", "roas": 1.3}, {"period": 0, "channel": "seo", "roas": 2.1},
        {"period": 1, "channel": "reddit", "roas": 1.4}, {"period": 1, "channel": "seo", "roas": 2.0},
    ]))
    assert rl_sim.load_replay_table(csv_path) == rl_sim.load_replay_table(json_path)
    assert rl_sim.load_replay_table(csv_path)[0] == {"reddit": 1.3, "seo": 2.1}


# --- Meta-pixel attributed revenue feeding CEO learning --------------------------------------

def test_flatten_folds_pixel_attributed_purchase_value():
    """Pure unit — no DB. The pixel rail's receipt totals (purchase_value_usd/purchase_count
    from Meta action_values) flatten into attributed_revenue_cents/purchases and cross the
    significance gate — the wiring that makes pixel ROAS feed CEO learning."""
    store_cls = rl_sim.takyon_core.TakyonStore
    snap = {"captured_at": "t", "users": 1, "campaigns": [{
        "slug": "meta-sim", "status": "active", "spend_cents": 1000,
        "impressions": 900, "clicks": 20,
        "purchase_value_usd": 8.4, "purchase_count": 2,
    }]}
    flat = store_cls._flatten_metrics_snapshot(snap)
    assert flat["attributed_revenue_cents"] == pytest.approx(840.0)
    assert flat["purchases"] == pytest.approx(2.0)
    moves = store_cls._significant_metric_moves(
        {"attributed_revenue_cents": 840.0, "purchases": 2.0})
    assert "attributed_revenue_cents" in moves and "purchases" in moves
    # ...and a receipt without the pixel fields (reddit today) flattens exactly as before.
    legacy = store_cls._flatten_metrics_snapshot(
        {"campaigns": [{"slug": "r", "spend_cents": 1000, "impressions": 900, "clicks": 20}]})
    assert "attributed_revenue_cents" not in legacy and "purchases" not in legacy


def test_pixel_attributed_revenue_reaches_distilled_lessons(pg_store_dsn, tmp_path):
    """End to end through the REAL rail: forced meta runs write pixel-shaped receipts
    (purchase_value_usd) -> episode snapshot harvests them -> the distiller mints lessons
    whose claims carry the channel-attributed revenue delta."""
    rep = rl_sim.run_simulation(
        dsn=pg_store_dsn, world=rl_sim.default_world(4, noise=0.05),
        chooser=_AlwaysSkill("meta"), wakes=6, slug=rl_sim._fresh_slug("rlsimpix"),
        root=tmp_path,
    )
    measured = [l for l in rep.lessons if str(l.get("source") or "").startswith("auto:")]
    assert measured, rep.lessons
    assert any("attributed revenue" in str(l.get("claim") or "") for l in measured), measured
    # The scripted chooser's revenue parse still reads the new claim shape.
    est = rl_sim.ScriptedChooser(random.Random(0))._estimates(rep.injected_final, ("meta",))
    assert est.get("meta", 0) > 0, rep.injected_final


def test_filter_world_skills_restricts_arms():
    """Pure unit — no DB. --skills narrows the world; unknown names fail loudly."""
    w = rl_sim.filter_world_skills(rl_sim.default_world(1), ["meta"])
    assert w.arms() == ("meta",)
    with pytest.raises(SystemExit):
        rl_sim.filter_world_skills(rl_sim.default_world(1), ["tiktok"])


# --- ground-truth guards ----------------------------------------------------------------------

def test_default_world_has_a_clear_unique_optimum():
    """Pure unit — no DB. Guards the harness's ground truth: seo is the strict ROAS optimum,
    and by default each skill runs ONE way (the run IS the skill — no execution lever). With
    the OPT-IN variants, seo's WORST variant still beats every other skill's BEST variant, so
    'converge on the best skill' and 'improve within the skill' stay separately measurable."""
    w = rl_sim.default_world()
    assert w.best_arm(0) == "seo"
    ranked = sorted(w.arms(), key=lambda a: w.expected_roas(a, 0), reverse=True)
    assert ranked == ["seo", "reddit", "meta"]
    for skill in w.arms():
        assert w.variants(skill) == ("default",), skill  # variantless by default
    wv = rl_sim.default_world(variants=rl_sim._DEFAULT_VARIANTS)
    seo_worst = min(wv.expected_roas("seo", 0, variant=v) for v in wv.variants("seo"))
    for other in ("reddit", "meta"):
        other_best = wv.expected_roas(other, 0, variant=wv.best_variant(other))
        assert seo_worst > other_best, (seo_worst, other, other_best)


def test_receipt_roas_is_full_cost():
    """Pure unit — process step 3: ROAS divides profit by creation + spend, not spend alone."""
    r = rl_sim.Receipt(skill="meta", variant="ugc-video", spend_cents=1000, creation_cents=200,
                       impressions=0, clicks=0, signups=6, profit_cents=2400, page_visits=0)
    assert r.total_cost_cents == 1200
    assert abs(r.roas - 2.0) < 1e-9
