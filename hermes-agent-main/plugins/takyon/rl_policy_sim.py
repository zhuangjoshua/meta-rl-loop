"""Level-1 sim for the OPERATOR'S policy improvement loop (rl_policy_loop.py).

Synthetic Meta world with hidden ground truth: each creative attribute value carries a
sealed ROAS multiplier (drawn per seed), true arm ROAS is the product across axes, and
MEASURED ROAS is the truth corrupted by spend-scaled noise (small spend = noisy reads —
the statistical-power reality). No network, no money, no Postgres.

Two drivers on identical seeds/worlds:
  policy-loop — the operator's loop: 3 concurrent A/B arms, per-wake semantic gradient
                over stored policies, noise-scheduled selection, cut/keep/launch.
  baseline    — the current production doctrine: ONE campaign, hold at ROAS >= 2.5,
                else one changed-approach successor (never repeat a failed combo).

Usage:
    python -m plugins.takyon.rl_policy_sim --wakes 40 --seeds 5
    python -m plugins.takyon.rl_policy_sim --wakes 40 --seeds 5 --dump out.json
"""

from __future__ import annotations

import argparse
import json
import math
import random
import statistics
import sys
from pathlib import Path
from typing import Any

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:  # pragma: no cover - direct-script convenience
    sys.path.insert(0, str(_REPO_ROOT))

from plugins.takyon.rl_policy_loop import (  # noqa: E402
    ATTRIBUTE_AXES, Arm, NoiseSchedule, PolicyStore, improvement_step,
)

DAILY_SPEND_PER_ARM = 5.0     # USD/day per live arm (the default rail budget)
WAKE_DAYS = 1.0               # one wake per simulated day
NOISE_SIGMA0 = 0.9            # lognormal sigma at 1 USD of spend; shrinks ~ 1/sqrt(spend)


# ── Hidden world ─────────────────────────────────────────────────────────────────────────

class World:
    """Sealed truth: one ROAS multiplier per attribute value; base ROAS below break-even
    so only good attribute combinations are profitable."""

    def __init__(self, seed: int):
        rng = random.Random(seed * 7919 + 13)
        self.base = 1.1
        self.mults: dict[str, dict[str, float]] = {}
        for axis, values in ATTRIBUTE_AXES.items():
            # one clear winner, one dud, middles in between — shuffled per seed
            spread = [1.45, 0.75] + [1.0] * (len(values) - 2)
            rng.shuffle(spread)
            self.mults[axis] = dict(zip(values, spread))

    def true_roas(self, attrs: dict[str, str]) -> float:
        r = self.base
        for axis, value in attrs.items():
            r *= self.mults[axis][value]
        return r

    def best_attrs(self) -> dict[str, str]:
        return {axis: max(vals, key=lambda v: vals[v]) for axis, vals in self.mults.items()}

    def measure(self, attrs: dict[str, str], spend: float, rng: random.Random) -> float:
        sigma = NOISE_SIGMA0 / math.sqrt(max(spend, 1.0))
        return self.true_roas(attrs) * rng.lognormvariate(0.0, sigma)


# ── The operator's policy loop, one run ──────────────────────────────────────────────────

def run_policy_loop(world: World, *, wakes: int, seed: int, k: int = 6,
                    trace: bool = False, decay: float = 0.92, width: float = 0.18) -> dict[str, Any]:
    rng = random.Random(seed)
    store = PolicyStore()
    schedule = NoiseSchedule(decay=decay, width=width)
    live: list[Arm] = []
    graveyard: list[Arm] = []
    counter = iter(range(10 ** 6))
    roas_by_wake: list[float] = []
    spend_total = profit_total = 0.0

    def _attrs(a: Arm) -> str:
        return "/".join(a.attrs[axis] for axis in sorted(a.attrs))

    for wake in range(wakes):
        # metrics pull: every live arm accrues spend and settles a fresh measurement
        for arm in live:
            arm.spend += DAILY_SPEND_PER_ARM * WAKE_DAYS
            arm.measured_roas = world.measure(arm.attrs, arm.spend, rng)
            arm.history.append({"wake": wake, "spend": arm.spend, "roas": arm.measured_roas})

        prior_version = store.current.version
        result = improvement_step(
            store, live, live + graveyard, wake=wake, rng=rng, schedule=schedule,
            k_candidates=k, next_arm_id=lambda: f"arm-{next(counter)}",
        )
        graveyard.extend(result.cut)
        live = result.kept + result.launched

        if trace:
            print(f"\n── wake {wake} · policy v{prior_version} ─────────────────────────")
            for a in result.kept + result.cut:
                if a.measured_roas is not None:
                    verdict = "CUT " if a in result.cut else ("hold" if a.measured_roas >= store.current.params["hold_roas"] else "run ")
                    print(f"  measure  {a.arm_id:<10} {_attrs(a):<24} spend ${a.spend:>5.0f}"
                          f"  measured ROAS {a.measured_roas:5.2f}  true {world.true_roas(a.attrs):4.2f}  -> {verdict}"
                          f"  [policy v{a.policy_version}@{a.policy_hash}]")
            picked = "kept incumbent" if store.current.version == prior_version else \
                f"ADOPTED v{store.current.version} ({store.current.note})"
            print(f"  gradient {len(result.candidate_notes)} candidates -> {picked}")
            for a in result.launched:
                print(f"  launch   {a.arm_id:<10} {_attrs(a):<24} under v{a.policy_version}@{a.policy_hash}")

        # realized economics this wake (true ROAS earns on the spend of arms that ran)
        wake_spend = sum(DAILY_SPEND_PER_ARM * WAKE_DAYS for a in result.kept if a.launched_wake < wake)
        wake_return = sum(world.true_roas(a.attrs) * DAILY_SPEND_PER_ARM * WAKE_DAYS
                          for a in result.kept if a.launched_wake < wake)
        spend_total += wake_spend
        profit_total += wake_return - wake_spend
        roas_by_wake.append((wake_return / wake_spend) if wake_spend else 0.0)

    best = world.best_attrs()
    late_arms = [a for a in live if a.measured_roas is not None]
    best_share = (sum(1 for a in late_arms if a.attrs == best) / len(late_arms)) if late_arms else 0.0
    return {
        "driver": "policy-loop",
        "roas_by_wake": roas_by_wake,
        "spend": spend_total,
        "profit": profit_total,
        "policy_versions": len(store.history),
        "final_policy": store.current.to_record(),
        "best_attr_share_final": best_share,
        "arms_run": len(graveyard) + len(live),
    }


# ── Baseline: current production doctrine (one campaign, hold at 2.5) ────────────────────

def run_baseline(world: World, *, wakes: int, seed: int) -> dict[str, Any]:
    rng = random.Random(seed)
    hold = 2.5
    failed: set[tuple] = set()
    attrs = {axis: rng.choice(list(vals)) for axis, vals in ATTRIBUTE_AXES.items()}
    spend_on_current = 0.0
    roas_by_wake: list[float] = []
    spend_total = profit_total = 0.0
    relaunches = 0

    for wake in range(wakes):
        spend = DAILY_SPEND_PER_ARM * WAKE_DAYS
        spend_on_current += spend
        measured = world.measure(attrs, spend_on_current, rng)
        true = world.true_roas(attrs)
        spend_total += spend
        profit_total += true * spend - spend
        roas_by_wake.append(true)
        if measured < hold:
            failed.add(tuple(sorted(attrs.items())))
            for _ in range(50):  # one changed-approach successor, never repeat a failure
                candidate = {axis: rng.choice(list(vals)) for axis, vals in ATTRIBUTE_AXES.items()}
                if tuple(sorted(candidate.items())) not in failed:
                    attrs = candidate
                    break
            spend_on_current = 0.0
            relaunches += 1

    return {
        "driver": "baseline",
        "roas_by_wake": roas_by_wake,
        "spend": spend_total,
        "profit": profit_total,
        "relaunches": relaunches,
    }


# ── Report ───────────────────────────────────────────────────────────────────────────────

def _quartile_means(series: list[float]) -> tuple[float, float]:
    q = max(1, len(series) // 4)
    return statistics.fmean(series[:q]), statistics.fmean(series[-q:])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--wakes", type=int, default=40)
    ap.add_argument("--seeds", type=int, default=5)
    ap.add_argument("--k", type=int, default=6, help="candidate policies per wake")
    ap.add_argument("--dump", type=str, default="", help="write full per-seed results JSON here")
    ap.add_argument("--trace", action="store_true", help="narrate every wake (best with --seeds 1)")
    ap.add_argument("--decay", type=float, default=0.92, help="noise schedule decay per wake")
    ap.add_argument("--width", type=float, default=0.18, help="selection concentration around target boldness")
    args = ap.parse_args(argv)

    runs = []
    for s in range(args.seeds):
        world = World(seed=s)
        if args.trace:
            print(f"\n=== seed {s} · hidden best combo: {world.best_attrs()} ===")
        pl = run_policy_loop(world, wakes=args.wakes, seed=s, k=args.k, trace=args.trace,
                             decay=args.decay, width=args.width)
        bl = run_baseline(world, wakes=args.wakes, seed=s)
        runs.append({"seed": s, "best_attrs": world.best_attrs(), "policy_loop": pl, "baseline": bl})

    def agg(driver: str, key: str) -> float:
        return statistics.fmean(r[driver][key] for r in runs)

    pl_early, pl_late = zip(*(_quartile_means(r["policy_loop"]["roas_by_wake"]) for r in runs))
    bl_early, bl_late = zip(*(_quartile_means(r["baseline"]["roas_by_wake"]) for r in runs))

    print(f"── operator policy loop vs baseline · {args.seeds} seeds × {args.wakes} wakes ──")
    print(f"  {'':24}{'policy-loop':>14}{'baseline':>14}")
    print(f"  {'ROAS early-quartile':24}{statistics.fmean(pl_early):>14.2f}{statistics.fmean(bl_early):>14.2f}")
    print(f"  {'ROAS late-quartile':24}{statistics.fmean(pl_late):>14.2f}{statistics.fmean(bl_late):>14.2f}")
    print(f"  {'profit (mean/seed)':24}{agg('policy_loop', 'profit'):>14.2f}{agg('baseline', 'profit'):>14.2f}")
    print(f"  {'spend (mean/seed)':24}{agg('policy_loop', 'spend'):>14.2f}{agg('baseline', 'spend'):>14.2f}")
    print(f"  policy versions adopted (mean)  {agg('policy_loop', 'policy_versions'):.1f}")
    print(f"  best-attr share of final arms   {agg('policy_loop', 'best_attr_share_final'):.0%}")
    improve = statistics.fmean(pl_late) - statistics.fmean(pl_early)
    print(f"  policy-loop ROAS improvement early->late: {improve:+.2f}")

    if args.dump:
        Path(args.dump).write_text(json.dumps(runs, indent=2, default=str))
        print(f"  full results -> {args.dump}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
