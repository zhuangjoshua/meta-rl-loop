"""Policy improvement loop — the operator's wake-time RL loop (2026-07-15 design).

This replaces the fixed hold/relaunch rule as the thing that learns. The POLICY —
a versioned params+prose object — is the learned artifact; every wake runs one
improvement step over the live ad portfolio:

  1. A/B — judge the live arms launched under the current policy using their
     measured metrics; those measurements also revise the policy's own A/B
     section (min-spend-before-judgment, arm count) next step.
  2. SEMANTIC GRADIENT — from ALL prior policies and the metrics of every ad
     they produced (profitable and unprofitable), generate K candidate
     policies, RANK THEM SMALLEST -> BOLDEST (distance from the incumbent),
     and select the next policy along that boldness spectrum through a
     decaying noise schedule: hot early wakes take bold swings, cooled late
     wakes take small refinements, and the incumbent competes at boldness 0
     ("no change"). Evidence steers each candidate's DIRECTION; the schedule
     controls STEP SIZE. The old policy is always stored; lineage and content
     hashes make every ad attributable to the exact policy it ran under.
  3. CUT / KEEP / LAUNCH — cut unprofitable arms (their budget returns), keep
     profitable arms running, then fill the freed slots with new arms sampled
     from the NEW policy for the next round of A/B.

The engine is deliberately runtime-agnostic and seedable: the level-1 sim
(`rl_policy_sim.py`) drives it against a synthetic world, and the same
functions can later drive the real rail (arm launch -> business_meta_ad_launch
with total_budget_usd; cut -> business_meta_ad_control pause; policy store ->
per-business metrics/policy/meta/ versions).

No money, no network, stdlib only.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

# ── Policy object ────────────────────────────────────────────────────────────────────────

# Creative attribute axes the policy expresses preferences over. Values mirror the levers
# the real skills expose (creative kind; hook strategy; targeting breadth).
ATTRIBUTE_AXES: dict[str, tuple[str, ...]] = {
    "kind": ("video", "image"),
    "hook": ("pain", "proof", "curiosity"),
    "audience": ("broad", "niche"),
}

DEFAULT_PARAMS: dict[str, Any] = {
    # portfolio judgment
    "hold_roas": 2.5,          # at/above -> keep the arm running
    "cut_roas": 1.0,           # below (after min spend) -> cut, budget returns
    "min_spend_before_judgment": 10.0,  # USD an arm must spend before cut/hold applies
    # portfolio shape
    "max_live_arms": 3,        # mirrors the code cap in meta_ads_v2
    "arm_budget_frac": 1.0 / 3.0,  # slice of the remaining bucket per new arm
    # exploration posture
    "explore_share": 0.34,     # share of new arms sampled uniformly instead of by weight
    # creative preferences (the semantic part): weight per attribute value
    "attr_weights": {axis: {v: 1.0 for v in values} for axis, values in ATTRIBUTE_AXES.items()},
}


@dataclass
class PolicyVersion:
    version: int
    parent: int | None
    params: dict[str, Any]
    note: str            # prose rationale — how this candidate was derived from evidence
    created_wake: int

    @property
    def policy_hash(self) -> str:
        blob = json.dumps({"params": self.params, "note": self.note}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()[:12]

    def to_record(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "parent": self.parent,
            "hash": self.policy_hash,
            "params": self.params,
            "note": self.note,
            "created_wake": self.created_wake,
        }


class PolicyStore:
    """Append-only policy history. Every version is retained; `current` is the live one."""

    def __init__(self, seed_params: Mapping[str, Any] | None = None):
        params = json.loads(json.dumps(dict(seed_params or DEFAULT_PARAMS)))
        self.history: list[PolicyVersion] = [
            PolicyVersion(version=0, parent=None, params=params,
                          note="seed policy (extracted operator doctrine)", created_wake=0)
        ]

    @property
    def current(self) -> PolicyVersion:
        return self.history[-1]

    def adopt(self, candidate: PolicyVersion) -> PolicyVersion:
        # Version numbers are assigned at ADOPTION so lineage is strictly monotonic;
        # candidate numbering at generation time is provisional. Content hashes are the
        # stable identity either way.
        candidate.version = self.history[-1].version + 1
        self.history.append(candidate)
        return candidate

    def records(self) -> list[dict[str, Any]]:
        return [p.to_record() for p in self.history]


# ── Arms ─────────────────────────────────────────────────────────────────────────────────

@dataclass
class Arm:
    """One live ad campaign: the creative attributes it ran with, under which policy."""
    arm_id: str
    attrs: dict[str, str]
    policy_version: int
    policy_hash: str
    launched_wake: int
    spend: float = 0.0
    measured_roas: float | None = None   # latest settled measurement (None until judged)
    status: str = "live"                 # live | cut | held
    history: list[dict[str, Any]] = field(default_factory=list)  # per-wake measurements


# ── Evidence: attribute-level estimates from ALL arms ever run ───────────────────────────

def estimate_attribute_values(
    arms: Sequence[Arm],
) -> dict[str, dict[str, tuple[float, int]]]:
    """Mean measured ROAS and sample count per attribute value, over every arm with a
    measurement — profitable and unprofitable alike (both are evidence)."""
    est: dict[str, dict[str, tuple[float, int]]] = {
        axis: {} for axis in ATTRIBUTE_AXES
    }
    for arm in arms:
        if arm.measured_roas is None:
            continue
        for axis, value in arm.attrs.items():
            mean, n = est[axis].get(value, (0.0, 0))
            est[axis][value] = ((mean * n + arm.measured_roas) / (n + 1), n + 1)
    return est


def boldness(base_params: Mapping[str, Any], cand_params: Mapping[str, Any]) -> float:
    """How large a change a candidate is, relative to the incumbent: summed absolute
    deltas across thresholds, exploration posture, and attribute weights. 0.0 = no
    change. Used only for ORDERING candidates smallest -> boldest."""
    d = 0.0
    for key in ("hold_roas", "cut_roas", "min_spend_before_judgment",
                "arm_budget_frac", "explore_share"):
        d += abs(float(cand_params[key]) - float(base_params[key]))
    for axis, weights in cand_params["attr_weights"].items():
        for value, w in weights.items():
            d += abs(float(w) - float(base_params["attr_weights"][axis].get(value, 1.0)))
    return round(d, 6)


# ── Step 2: semantic gradient — candidates ranked smallest -> boldest, noise-scheduled ───

@dataclass
class NoiseSchedule:
    """Boldness appetite decays over wakes: bold swings early, small refinements late."""
    tau0: float = 1.0
    decay: float = 0.92
    floor: float = 0.05
    width: float = 0.18   # how tightly selection concentrates around the target boldness

    def tau(self, wake: int) -> float:
        return max(self.floor, self.tau0 * (self.decay ** wake))

    def target01(self, wake: int) -> float:
        """Current appetite mapped to [0, 1]: 1 = boldest end of the ranking, 0 = smallest."""
        span = self.tau0 - self.floor
        return (self.tau(wake) - self.floor) / span if span > 0 else 0.0


def generate_candidates(
    store: PolicyStore,
    all_arms: Sequence[Arm],
    *,
    k: int,
    wake: int,
    rng: random.Random,
) -> list[PolicyVersion]:
    """K candidate policies = K STEP SIZES along ONE evidence direction (the semantic
    gradient), like a learning-rate ladder in SGD: the direction is trusted, the
    candidates differ only in how far they step. Candidate i steps at scale (i+1)/k,
    so the set always ranges smallest -> boldest along the same gradient.

    The direction, computed once per wake from ALL arm metrics (profitable and
    unprofitable both count):
      - attribute weights move toward measured winners, confidence-weighted;
      - hold/cut thresholds move toward the observed ROAS median split.
    Cold start (no measurements yet): the direction is a seeded random perturbation —
    there is no gradient before the first evidence, only exploration.
    """
    base = store.current
    est = estimate_attribute_values(all_arms)
    measured = [a for a in all_arms if a.measured_roas is not None]
    next_version = len(store.history)

    # ── one direction per wake ──
    weight_delta: dict[str, dict[str, float]] = {axis: {} for axis in ATTRIBUTE_AXES}
    if measured:
        for axis, values in est.items():
            if not values:
                continue
            best_value, (best_mean, n) = max(values.items(), key=lambda kv: kv[1][0])
            confidence = n / (n + 2)
            weight_delta[axis][best_value] = 1.5 * confidence
        roases = sorted(a.measured_roas for a in measured)
        median = roases[len(roases) // 2]
        hold_target = max(median, 1.2)
        winners = {ax: max(v, key=lambda x: v[x][0]) for ax, v in est.items() if v}
        direction_note = f"gradient toward measured winners {winners}, median ROAS {median:.2f}"
    else:
        axis = rng.choice(list(ATTRIBUTE_AXES))
        value = rng.choice(list(ATTRIBUTE_AXES[axis]))
        weight_delta[axis][value] = 1.5
        hold_target = base.params["hold_roas"]
        median = base.params["cut_roas"] * 2
        direction_note = f"cold start: no measurements yet, random direction {axis}={value}"

    # ── K magnitudes of that direction ──
    candidates: list[PolicyVersion] = []
    for i in range(k):
        scale = (i + 1) / k   # step size: 1/k (tiny) .. 1.0 (bold)
        params = json.loads(json.dumps(base.params))
        for axis, deltas in weight_delta.items():
            for value, d in deltas.items():
                params["attr_weights"][axis][value] = (
                    params["attr_weights"][axis].get(value, 1.0) + scale * d
                )
        blend = 0.6 * scale
        params["hold_roas"] = round((1 - blend) * params["hold_roas"] + blend * hold_target, 3)
        params["cut_roas"] = round(min(params["hold_roas"] - 0.2,
                                       (1 - blend) * params["cut_roas"] + blend * median * 0.5), 3)
        candidates.append(PolicyVersion(
            version=next_version + i, parent=base.version, params=params,
            note=f"step {scale:.2f} · {direction_note}", created_wake=wake,
        ))
    return candidates


def select_policy(
    store: PolicyStore,
    candidates: Sequence[PolicyVersion],
    *,
    wake: int,
    rng: random.Random,
    schedule: NoiseSchedule,
) -> PolicyVersion:
    """Sample one rung of the step-size ladder. The candidates arrive ALREADY ordered
    smallest -> boldest — they are one gradient at increasing step sizes by
    construction, so no distance metric or sort is needed. The incumbent is prepended
    as the zero-step rung ('no change'). The noise schedule picks the position: a hot
    schedule targets the bold end, a cooled one the small end. No score-based top-k —
    evidence already shaped the direction; the schedule only chooses how far to step."""
    ranked = [store.current] + list(candidates)
    n = len(ranked)
    if n == 1:
        return ranked[0]
    target = schedule.target01(wake)
    weights = [math.exp(-((i / (n - 1)) - target) ** 2 / (2 * schedule.width ** 2))
               for i in range(n)]
    z = sum(weights)
    pick = rng.random() * z
    acc = 0.0
    for w, policy in zip(weights, ranked):
        acc += w
        if pick <= acc:
            return policy
    return ranked[-1]


# ── Steps 1 + 3: judge arms, cut/keep, launch new arms under the new policy ──────────────

@dataclass
class ImprovementResult:
    cut: list[Arm]
    kept: list[Arm]
    new_policy: PolicyVersion
    launched: list[Arm]
    candidate_notes: list[str]


def sample_arm_attrs(params: Mapping[str, Any], rng: random.Random) -> dict[str, str]:
    """Sample creative attributes from the policy's weights; with probability
    explore_share, sample the axis uniformly instead (the policy's own A/B knob)."""
    attrs: dict[str, str] = {}
    for axis, values in ATTRIBUTE_AXES.items():
        if rng.random() < params["explore_share"]:
            attrs[axis] = rng.choice(list(values))
            continue
        weights = params["attr_weights"][axis]
        z = sum(weights.get(v, 1.0) for v in values)
        pick = rng.random() * z
        acc = 0.0
        for v in values:
            acc += weights.get(v, 1.0)
            if pick <= acc:
                attrs[axis] = v
                break
    return attrs


def improvement_step(
    store: PolicyStore,
    live_arms: list[Arm],
    all_arms: list[Arm],
    *,
    wake: int,
    rng: random.Random,
    schedule: NoiseSchedule,
    k_candidates: int = 6,
    next_arm_id: Any = None,
) -> ImprovementResult:
    """One wake of the operator's improvement loop, in the operator's three steps."""
    params = store.current.params

    # 1. A/B judgment on the live arms (metrics were settled by the caller before this).
    cut, kept = [], []
    for arm in live_arms:
        judged = (arm.measured_roas is not None
                  and arm.spend >= params["min_spend_before_judgment"])
        if judged and arm.measured_roas < params["cut_roas"]:
            arm.status = "cut"
            cut.append(arm)
        else:
            kept.append(arm)  # profitable and not-yet-judged arms keep running

    # 2. Semantic gradient over policy history + all ad metrics; candidates ranked
    #    smallest -> boldest, selection position set by the noise schedule.
    candidates = generate_candidates(store, all_arms, k=k_candidates, wake=wake, rng=rng)
    chosen = select_policy(store, candidates, wake=wake, rng=rng, schedule=schedule)
    if chosen is not store.current:
        chosen = store.adopt(chosen)

    # 3. Fill freed slots with new arms sampled from the NEW policy.
    new_params = store.current.params
    launched: list[Arm] = []
    slots = max(0, int(new_params["max_live_arms"]) - len(kept))
    for _ in range(slots):
        arm_id = str(next_arm_id() if callable(next_arm_id) else f"arm-w{wake}-{len(launched)}")
        launched.append(Arm(
            arm_id=arm_id,
            attrs=sample_arm_attrs(new_params, rng),
            policy_version=store.current.version,
            policy_hash=store.current.policy_hash,
            launched_wake=wake,
        ))

    return ImprovementResult(
        cut=cut, kept=kept, new_policy=store.current, launched=launched,
        candidate_notes=[c.note for c in candidates],
    )
