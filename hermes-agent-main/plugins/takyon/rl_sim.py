"""Offline RL-loop test environment — synthetic ground-truth channel world.

This is an OFFLINE EVAL HARNESS. It is not registered as a tool, not imported by any
runtime path, and spends no money / calls no paid provider. Its job is to let us iterate
on the Takyon RL loop and *measure* whether accumulated feedback improves ROAS.

THE PER-BUSINESS PROCESS (operator-defined, 2026-07-07) — each simulated wake runs it:

  1. CHOOSE SKILL     — the OPERATOR names the ONE skill under test (--skills meta|reddit|seo).
                        The agent does NOT select skills in this environment — skill selection
                        is the production wake's job and is out of scope here. Listing several
                        skills is an explicit opt-in for allocation experiments only.
  2. RUN ADS/SEO      — execute the skill: pay the CREATION cost, run the spend. (By
                        default the run IS the skill — no execution choice. --variants
                        optionally adds creative/content-type variants with hidden quality
                        multipliers, a controllable lever for measuring within-skill
                        improvement with the scripted driver.)
  3. NEXT WAKE: ROAS  — calculate cost of creating AND running the ad, profit from
                        ad -> signup conversion (page visits for SEO), ROAS = profit /
                        (creation + spend).
  4. APPEND TO SKILL  — append the ENTIRE process + all metrics to the skill's per-
                        business run history, which is injected into the skill's working
                        prompt on its next run. (Production shape: SKILL.md stays static
                        and shared; the per-business history lives in the business
                        filesystem at metrics/roas/<skill>.md and is appended into the
                        skill's prompt when it runs for that business.)
  5. RE-RUN           — the skill runs again with that feedback; measure whether its
                        ROAS improves run-over-run, and keep appending.

So the harness measures BOTH optimization levels:
  * ALLOCATION  — does the wake choose the highest-ROAS skill over time? (regret,
    %-optimal, convergence — driven by the real episodes->distill->wake-learnings rail)
  * EXECUTION   — within one skill, does the appended run history make the NEXT run
    better (pick the higher-converting variant)? (per-skill early->late ROAS, best-
    variant share)

It exercises the REAL loop, unmodified: record_episode -> synthetic outcome written
where the store reads it -> distill_episode_lessons -> _assemble_wake_learnings; plus
the per-business skill-history feedback file for the execution level.

Requires a migrated Postgres, exactly like tests/plugins/test_takyon_rl_rails.py:
    export TAKYON_TEST_PG_DSN=postgres://...        # a throwaway migrated DB
    python -m plugins.takyon.rl_sim --skills meta --wakes 40      # THE canonical run: one skill
    python -m plugins.takyon.rl_sim --skills seo --wakes 40 --driver anthropic  # real-LLM
    python -m plugins.takyon.rl_sim --replay history.csv          # backtest a ROAS table
    python -m plugins.takyon.rl_sim --skills meta,reddit,seo --wakes 150  # allocation opt-in

Meta-pixel revenue path: meta receipts carry purchase_value_usd/purchase_count (the pixel
rail's attributed revenue), which the episode snapshot harvests and the distiller turns
into [attributed revenue] lesson deltas; reddit receipts deliberately lack them (no
purchase attribution on that channel today), so the environment tests both shapes.

The store is Postgres-only (`_connect` -> `_connect_postgres`); there is no SQLite path.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import re
import sys
import tempfile
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

try:  # in-package import (python -m plugins.takyon.rl_sim)
    from . import business_ad_spend
    from . import core as takyon_core
except ImportError:  # pragma: no cover - alternate load path
    from plugins.takyon import business_ad_spend  # type: ignore
    from plugins.takyon import core as takyon_core  # type: ignore

TakyonStore = takyon_core.TakyonStore

# meta/reddit are ad buckets the episode metrics snapshot recognizes (spend + delivery);
# seo is organic — its return proxy is page visits (process step 3). Stable order for
# deterministic cold-start.
AD_BUCKETS = ("meta", "reddit")
ARMS: tuple[str, ...] = ("meta", "reddit", "seo")

# Margin per converted signup (cents). Profit = signups x margin — "profit from ad ->
# signup conversion" (process step 3). One constant so ground-truth ROAS stays exact.
# MUST be small relative to a run's expected profit: profit quantizes to whole signups, so a
# coarse margin flattens variant-level ROAS differences into identical rounded values (observed
# at 400: every meta variant rounded to 2 signups = $8.00, making the 0.9x and 1.15x variants
# literally indistinguishable and the execution-learning signal unmeasurable at a $10 budget).
SIGNUP_MARGIN_CENTS = 100


# --------------------------------------------------------------------------- clock

class _Clock:
    """A controllable UTC clock. We monkeypatch ``core._now`` to it so every episode /
    event / lesson written by the store is stamped in SIMULATED time, and we pass the same
    time into ``distill_episode_lessons(now=...)`` — that is what ages an episode past the
    12h distill gate in milliseconds of wall-clock."""

    def __init__(self, start: datetime) -> None:
        self.t = start

    def now_iso(self) -> str:
        return self.t.isoformat()

    def advance(self, **kw: Any) -> None:
        self.t += timedelta(**kw)


@contextmanager
def _patched_clock(clock: _Clock):
    original = takyon_core._now
    takyon_core._now = clock.now_iso  # type: ignore[assignment]
    try:
        yield
    finally:
        takyon_core._now = original  # type: ignore[assignment]


# ----------------------------------------------------------------------------- receipt

@dataclass
class Receipt:
    """Everything one skill run produced — the raw material for process step 3's ROAS:
    cost of creating (creation_cents) AND running (spend_cents) the ad, the signup
    conversions and their profit, and page visits (the SEO return proxy)."""

    skill: str
    variant: str
    spend_cents: int
    creation_cents: int
    impressions: int
    clicks: int
    signups: int
    profit_cents: int
    page_visits: int

    @property
    def total_cost_cents(self) -> int:
        return self.spend_cents + self.creation_cents

    @property
    def roas(self) -> float:
        return (self.profit_cents / self.total_cost_cents) if self.total_cost_cents else 0.0


# --------------------------------------------------------------------- ground truth

@dataclass
class ChannelTruth:
    """Hidden truth for one channel/skill. ``roas`` is the expected profit per $1 of TOTAL
    cost (creation + spend) at execution quality 1.0; ``variants`` are the skill's execution
    choices (creative/angle/content type), each a hidden multiplier on that ROAS — the thing
    the skill can LEARN from its appended run history (process step 5). ``noise`` is the
    std-dev of the per-run ROAS draw; ``drift`` shifts the mean per wake (non-stationarity).
    ``kind``: 'ads' pays budget as spend + a fixed creative creation cost; 'organic' (seo)
    pays the whole budget as content creation, zero ad spend."""

    name: str
    roas: float
    noise: float = 0.12
    drift: float = 0.0
    kind: str = "ads"
    creation_cost_cents: int = 200
    variants: dict[str, float] = field(default_factory=lambda: {"default": 1.0})

    def expected_roas(self, t: int, *, mult: float = 1.0) -> float:
        return max(0.0, (self.roas + self.drift * t) * mult)

    def best_variant(self) -> str:
        return max(self.variants, key=lambda v: self.variants[v])


@dataclass
class World:
    """The synthetic reality. One skill runs per period (temporal isolation — what makes
    business-wide profit attributable to that period's skill); each period commits the SAME
    operator budget, so per-dollar ROAS ranking is exactly comparable across skills."""

    channels: dict[str, ChannelTruth]
    budget_cents: int = 1000  # $10 committed per wake
    rng: random.Random = field(default_factory=random.Random)

    def arms(self) -> tuple[str, ...]:
        return tuple(self.channels)

    def variants(self, skill: str) -> tuple[str, ...]:
        return tuple(self.channels[skill].variants)

    def best_variant(self, skill: str) -> str:
        return self.channels[skill].best_variant()

    def expected_roas(self, name: str, t: int, *, variant: str | None = None) -> float:
        ch = self.channels[name]
        mult = ch.variants.get(variant, 1.0) if variant else 1.0
        return ch.expected_roas(t, mult=mult)

    def best_arm(self, t: int) -> str:
        # The optimum a perfect agent could reach: best skill AT its best variant.
        return max(self.channels,
                   key=lambda n: self.expected_roas(n, t, variant=self.best_variant(n)))

    def run(self, skill: str, variant: str, t: int) -> Receipt:
        """Execute one skill run and return its receipt. Profit quantizes to whole signups
        (signups x SIGNUP_MARGIN_CENTS) so the conversion arithmetic in the history entry is
        exact, not narrative."""
        ch = self.channels[skill]
        mult = ch.variants.get(variant, 1.0)
        roas_sample = max(0.0, self.rng.gauss(ch.expected_roas(t, mult=mult), ch.noise))
        if ch.kind == "ads":
            spend, creation = self.budget_cents, ch.creation_cost_cents
        else:  # organic: the whole budget IS content creation; no ad spend
            spend, creation = 0, self.budget_cents + ch.creation_cost_cents
        total = spend + creation
        signups = max(0, int(round(roas_sample * total / SIGNUP_MARGIN_CENTS)))
        profit = signups * SIGNUP_MARGIN_CENTS
        if ch.kind == "ads":
            clicks = int(spend / 50 * self.rng.uniform(0.8, 1.2))  # ~50c CPC
            impressions = int(clicks / self.rng.uniform(0.01, 0.03))
            visits = clicks
        else:
            clicks = impressions = 0
            visits = int(signups * 50 * self.rng.uniform(0.85, 1.15))  # ~2% visit->signup
        return Receipt(skill=skill, variant=variant, spend_cents=spend, creation_cents=creation,
                       impressions=impressions, clicks=clicks, signups=signups,
                       profit_cents=profit, page_visits=visits)


# OPT-IN execution variants: give each skill a hidden quality spread (worst 0.9x -> best
# 1.15x) so within-skill improvement has a controllable, measurable lever. NOT the default —
# by default a run is just the skill (no execution choice); enable via --variants or
# default_world(variants=_DEFAULT_VARIANTS) when the experiment needs an improvement lever.
_DEFAULT_VARIANTS: dict[str, dict[str, float]] = {
    "meta": {"static-image": 0.9, "carousel": 1.0, "ugc-video": 1.15},
    "reddit": {"meme": 0.9, "howto": 1.0, "pain-first": 1.15},
    "seo": {"glossary": 0.9, "howto-guide": 1.0, "comparison-page": 1.15},
}


def default_world(seed: int = 7, *, budget_cents: int = 1000, noise: float = 0.12,
                  drift: float = 0.0, roas: dict[str, float] | None = None,
                  variants: dict[str, dict[str, float]] | None = None) -> World:
    """A clear-gap default: seo (2.1) > reddit (1.3) > meta (0.7). One obvious optimum so a
    working loop must converge on seo; regret and %-optimal are unambiguous. By default each
    skill runs ONE way (no execution lever) — running the skill IS the action, and ROAS
    tracks the channel's true rate. Pass ``variants`` (e.g. _DEFAULT_VARIANTS) to give skills
    an execution choice the run-history feedback can measurably improve."""
    roas = roas or {"meta": 0.7, "reddit": 1.3, "seo": 2.1}
    channels: dict[str, ChannelTruth] = {}
    for name, r in roas.items():
        kind = "ads" if name in AD_BUCKETS else "organic"
        channels[name] = ChannelTruth(
            name, r, noise=noise, drift=drift, kind=kind,
            creation_cost_cents=200 if kind == "ads" else 0,
            variants=dict((variants or {}).get(name) or {"default": 1.0}),
        )
    return World(channels=channels, budget_cents=budget_cents, rng=random.Random(seed))


# ----------------------------------------------------------------------- replay world

@dataclass
class ReplayWorld:
    """BACKTEST world: replay a real (or hand-authored) per-period, per-channel ROAS table
    through the SAME loop + scoreboard as the synthetic world (identical interface).

    ``roas_table[t][channel]`` is the channel's OBSERVED ROAS in period t. The backtest
    applies a FIXED hypothetical budget to whichever skill the loop picks and looks up that
    channel's ROAS — the sound counterfactual for an ALLOCATION policy. Honest limits:

      * FULL INFORMATION required — every arm's ROAS present for every period, because the
        loop may pick any arm. History that ran ONE channel per period has no ground truth
        for the un-run arms.
      * The table itself is the hard part: real spend+revenue -> per-channel ROAS means
        ATTRIBUTING revenue to a channel. The backtest is only as sound as that attribution.
      * Replayed ROAS is an observed aggregate, so there is no per-variant execution level
        here — one 'replay' variant; the backtest evaluates allocation only.

    ROAS is deterministic (the table already embodies real noise), so no rng on outcomes."""

    roas_table: list[dict[str, float]]
    budget_cents: int = 1000
    rng: random.Random = field(default_factory=random.Random)

    def arms(self) -> tuple[str, ...]:
        seen: list[str] = []
        for row in self.roas_table:
            for channel in row:
                if channel not in seen:
                    seen.append(channel)
        return tuple(seen)

    def variants(self, skill: str) -> tuple[str, ...]:
        return ("replay",)

    def best_variant(self, skill: str) -> str:
        return "replay"

    def expected_roas(self, name: str, t: int, *, variant: str | None = None) -> float:
        row = self.roas_table[min(t, len(self.roas_table) - 1)]
        return float(row.get(name, 0.0))

    def best_arm(self, t: int) -> str:
        return max(self.arms(), key=lambda a: self.expected_roas(a, t))

    def run(self, skill: str, variant: str, t: int) -> Receipt:
        roas = self.expected_roas(skill, t)
        spend = self.budget_cents if skill in AD_BUCKETS else 0
        creation = 0 if skill in AD_BUCKETS else self.budget_cents
        total = spend + creation
        signups = max(0, int(round(roas * total / SIGNUP_MARGIN_CENTS)))
        profit = signups * SIGNUP_MARGIN_CENTS
        clicks = int(spend / 50 * self.rng.uniform(0.8, 1.2)) if spend else 0
        impressions = int(clicks / 0.02) if clicks else 0
        visits = clicks if spend else signups * 50
        return Receipt(skill=skill, variant=variant, spend_cents=spend, creation_cents=creation,
                       impressions=impressions, clicks=clicks, signups=signups,
                       profit_cents=profit, page_visits=visits)


def load_replay_table(path: str | Path) -> list[dict[str, float]]:
    """Load a per-period per-channel ROAS table from CSV (columns: period,channel,roas) or JSON
    (a list of {"period","channel","roas"} records). Periods are densified into a contiguous,
    time-ordered list of {channel: roas} rows."""
    import csv

    p = Path(path)
    if p.suffix.lower() == ".json":
        records = json.loads(p.read_text(encoding="utf-8"))
    else:
        with p.open(encoding="utf-8", newline="") as fh:
            records = [dict(row) for row in csv.DictReader(fh)]
    if not records:
        raise ValueError(f"replay table {p} is empty")
    periods = sorted({int(r["period"]) for r in records})
    index = {period: i for i, period in enumerate(periods)}
    table: list[dict[str, float]] = [{} for _ in periods]
    for r in records:
        table[index[int(r["period"])]][str(r["channel"])] = float(r["roas"])
    return table


# ------------------------------------------------- the skill prompt (process steps 4+5)

_SKILL_GUIDANCE: dict[str, str] = {
    "meta": ("Run one Meta ads flight: create ONE ad creative (this costs creative credits), "
             "launch it against the audience, and let it deliver for the period."),
    "reddit": ("Run one Reddit ads flight: create ONE promoted-post creative (this costs "
               "creative credits), launch it in the target subreddits for the period."),
    "seo": ("Produce ONE piece of SEO content for the period (the budget is the content "
            "production cost); success shows up as increased page visits that convert to "
            "signups."),
}


def _skill_history_relpath(skill: str) -> str:
    return f"metrics/roas/{skill}.md"


def _read_skill_history(store: Any, slug: str, skill: str) -> str:
    """The per-business run history for one skill — the feedback that gets appended into the
    skill's working prompt (process step 4). Lives in the REAL business filesystem so the
    production shape is identical: SKILL.md stays static/shared, this file is per-business."""
    try:
        path = store._resolve_business_file(slug, _skill_history_relpath(skill), sync=False)
        return path.read_text(encoding="utf-8") if path.exists() else ""
    except Exception:
        return ""


def _append_skill_history(store: Any, slug: str, receipt: Receipt, *, run_idx: int,
                          period_hours: int, observed_at: str) -> None:
    """Process steps 3+4: upon next wake, compute full-cost ROAS from the receipt and append
    the ENTIRE process + all metrics to the skill's per-business history file. The entry is
    deliberately regex-parsable (variant=..., ROAS ...) so a policy — scripted or LLM — can
    consume its own feedback."""
    path = store._resolve_business_file(slug, _skill_history_relpath(receipt.skill), sync=False)
    path.parent.mkdir(parents=True, exist_ok=True)
    header = "" if path.exists() else (
        f"# {receipt.skill} run history (per-business skill feedback)\n\n"
        "Appended each wake after the run's ROAS is measured. ROAS = profit from signup "
        "conversions / (creation cost + ad spend).\n\n"
    )
    return_line = (f"page_visits {receipt.page_visits} -> signups {receipt.signups}"
                   if receipt.skill not in AD_BUCKETS else
                   f"clicks {receipt.clicks} -> signups {receipt.signups}")
    # 'default' means the run had no execution choice (the run IS the skill) — keep the
    # entry free of variant noise then; variant runs stay regex-parsable (variant=..., ROAS).
    if receipt.variant and receipt.variant != "default":
        head = (f"- run {run_idx} | variant={receipt.variant} | "
                f"process: chose variant '{receipt.variant}', created it for ")
    else:
        head = f"- run {run_idx} | process: created the {receipt.skill} deliverable for "
    entry = (
        head
        + f"${receipt.creation_cents / 100:.2f}, ran {receipt.skill} with "
        f"${receipt.spend_cents / 100:.2f} spend over {period_hours}h, measured at next wake "
        f"({observed_at}) | metrics: impressions {receipt.impressions}, {return_line}, "
        f"profit ${receipt.profit_cents / 100:.2f}, total cost "
        f"${receipt.total_cost_cents / 100:.2f} | ROAS {receipt.roas:.2f}\n"
    )
    with path.open("a", encoding="utf-8") as fh:
        fh.write(header + entry)


def skill_prompt(store: Any, slug: str, skill: str, variants: Sequence[str],
                 *, include_history: bool = True) -> str:
    """The skill's per-business working prompt: static guidance (the SKILL.md role) + the
    appended run-history feedback. This exact text is what the execution policy reads —
    scripted or real LLM."""
    history = _read_skill_history(store, slug, skill) if include_history else ""
    variant_line = (f"Execution variants available: {', '.join(variants)}.\n"
                    if len(variants) > 1 else "")
    return (
        f"== Skill: {skill} growth (working prompt for business:{slug}) ==\n"
        f"{_SKILL_GUIDANCE.get(skill, 'Run one growth action for the period.')}\n"
        + variant_line
        + "Goal: maximize ROAS = profit from signup conversions / (creation cost + spend).\n"
        "== Run history for this business (appended feedback — newest last) ==\n"
        + (history or "(no prior runs)\n")
    )


# ------------------------------------------------------------------------- choosers

class Chooser:
    """The two decisions of the per-business process: which skill to run this wake (step 1,
    from the wake's injected learnings) and which execution variant to run it with (step 2,
    from the skill's working prompt with appended history)."""

    def choose(self, injected_text: str, arms: Sequence[str], t: int) -> str:  # pragma: no cover
        raise NotImplementedError

    def choose_variant(self, skill: str, skill_prompt_text: str,
                       variants: Sequence[str], t: int) -> str:
        return variants[0]


# The measured-lesson claim shape is stable (core._compose_measured_claim), so we can read a
# per-arm revenue signal straight out of the injected wake text — the exact same bytes the
# real CEO would see. This keeps the scripted policy HONEST: its only memory is the loop's.
# The dollar figure may be "attributed revenue" (Meta-pixel action_values) or plain
# business-wide "revenue" depending on the channel's attribution rail — take the first
# dollar delta before either wording on the claim line.
_REV_RE = "on {arm} ->[^\\n]*?\\+\\$([0-9]+(?:\\.[0-9]+)?)[^\\n]*?revenue"
_NOMOVE_RE = "on {arm} ->\\s*no significant movement"
# Per-variant ROAS lines in the skill history file (step 4's parseable feedback).
_VARIANT_ROAS_RE = re.compile(r"variant=(\S+).*?ROAS ([0-9]+(?:\.[0-9]+)?)")


@dataclass
class ScriptedChooser(Chooser):
    """Stateless policy: re-derives ALL its estimates from the injected/appended text every
    wake (the whole point — the loop's feedback is its only memory). Skill choice: cold-start
    each untried arm once, then ε-greedy on the lesson-text revenue signal. Variant choice:
    cold-start each untried variant once, then ε-greedy on the AVERAGE ROAS per variant
    parsed from the skill's run history."""

    rng: random.Random
    epsilon: float = 0.08

    def _estimates(self, text: str, arms: Sequence[str]) -> dict[str, float]:
        est: dict[str, float] = {}
        for a in arms:
            m = re.search(_REV_RE.format(arm=re.escape(a)), text)
            if m:
                est[a] = float(m.group(1))
            elif re.search(_NOMOVE_RE.format(arm=re.escape(a)), text):
                est[a] = 0.0  # spent, nothing moved: a real (negative) signal, not "untried"
        return est

    def choose(self, injected_text: str, arms: Sequence[str], t: int) -> str:
        est = self._estimates(injected_text or "", arms)
        unseen = [a for a in arms if a not in est]
        if unseen:
            return unseen[t % len(unseen)]  # deterministic cold-start over untried arms
        if self.rng.random() < self.epsilon:
            return self.rng.choice(list(arms))
        return max(arms, key=lambda a: est.get(a, -1.0))

    def choose_variant(self, skill: str, skill_prompt_text: str,
                       variants: Sequence[str], t: int) -> str:
        samples: dict[str, list[float]] = {}
        for m in _VARIANT_ROAS_RE.finditer(skill_prompt_text or ""):
            if m.group(1) in variants:
                samples.setdefault(m.group(1), []).append(float(m.group(2)))
        unseen = [v for v in variants if v not in samples]
        if unseen:
            return unseen[t % len(unseen)]
        if self.rng.random() < self.epsilon:
            return self.rng.choice(list(variants))
        return max(variants, key=lambda v: sum(samples[v]) / len(samples[v]))


@dataclass
class CallableChooser(Chooser):
    """Wraps ``decide(injected_text, arms, t) -> skill`` and (optionally)
    ``decide_variant(skill, skill_prompt_text, variants, t) -> variant`` — the real-CEO seam."""

    decide: Callable[[str, Sequence[str], int], str]
    decide_variant: Callable[[str, str, Sequence[str], int], str] | None = None

    @staticmethod
    def _match(pick: str, options: Sequence[str], t: int) -> str:
        pick = str(pick or "").strip().lower()
        for o in options:
            if o.lower() in pick:  # tolerate "reddit ads" / "SEO." etc.
                return o
        return options[t % len(options)]

    def choose(self, injected_text: str, arms: Sequence[str], t: int) -> str:
        if len(arms) == 1:  # operator pinned the skill: no choice, don't spend an LLM call
            return arms[0]
        return self._match(self.decide(injected_text, arms, t), arms, t)

    def choose_variant(self, skill: str, skill_prompt_text: str,
                       variants: Sequence[str], t: int) -> str:
        if len(variants) == 1:  # no execution choice: don't spend an LLM call on it
            return variants[0]
        if self.decide_variant is None:
            return variants[t % len(variants)]
        return self._match(self.decide_variant(skill, skill_prompt_text, variants, t),
                           variants, t)


def make_anthropic_chooser(model: str = "claude-opus-4-8") -> CallableChooser:
    """A real-LLM acceptance driver: two constrained one-shots per wake — one reads the SAME
    injected wake learnings the CEO sees and names a skill (process step 1), one reads the
    skill's working prompt with its appended run history and names a variant (step 2). Needs
    the ``anthropic`` SDK and a key in the environment. Slow, costs tokens; use it to
    accept, not to sweep."""
    import anthropic  # lazy: only the acceptance path needs it

    client = anthropic.Anthropic()

    def _one_word(system: str, user: str) -> str:
        msg = client.messages.create(model=model, max_tokens=12, system=system,
                                     messages=[{"role": "user", "content": user}])
        return "".join(getattr(b, "text", "") for b in msg.content)

    def decide(injected: str, arms: Sequence[str], t: int) -> str:
        system = (
            "You are the autonomous CEO of a business. Each wake you invest a FIXED, equal "
            "budget into exactly ONE growth skill to maximize return on ad spend (ROAS). "
            "Base your choice ONLY on your accumulated learnings below. If a skill is "
            "untried, it may be worth exploring. Reply with EXACTLY one word, one of: "
            + " | ".join(arms) + "."
        )
        user = (injected.strip() or "(no learnings recorded yet)") + (
            f"\n\nSkills available: {', '.join(arms)}. Which ONE do you run this wake?"
        )
        return _one_word(system, user)

    def decide_variant(skill: str, prompt_text: str, variants: Sequence[str], t: int) -> str:
        system = (
            f"You are executing the {skill} growth skill. Your run history (appended "
            "feedback) is below. Pick the execution variant most likely to maximize ROAS; "
            "explore untried variants when the history is thin. Reply with EXACTLY one of: "
            + " | ".join(variants) + "."
        )
        return _one_word(system, prompt_text)

    return CallableChooser(decide, decide_variant)


# ----------------------------------------------------------------- world -> store IO

@dataclass
class _ArmState:
    spend_cents: int = 0
    impressions: int = 0
    clicks: int = 0
    profit_cents: int = 0
    purchases: int = 0


def seed_business(dsn: str, slug: str, *, goal: str = "Maximize ROAS across meta/reddit/seo") -> None:
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        uid = conn.execute(
            "insert into users (auth0_sub) values (%s) returning id", (f"auth0|{slug}",)
        ).fetchone()[0]
        conn.execute(
            "insert into businesses (slug, name, owner_user_id, mode, goal) values (%s,%s,%s,%s,%s)",
            (slug, slug.replace("-", " ").title(), uid, "live", goal),
        )


def _apply_outcome(store: Any, dsn: str, slug: str, receipt: Receipt,
                   state: dict[str, _ArmState], seq: int) -> None:
    """Write the run's outcome exactly where the real episode metrics snapshot reads it:
    signup conversions as app_users rows; their margin-net profit as an app_revenue_events
    row (attributed to this skill by temporal isolation); for ad skills, cumulative spend on
    the ad-spend policy + a cumulative insights-sync receipt. Page visits have no store
    counter today (a known snapshot gap — production would read GSC/analytics); they live in
    the receipt + skill history file. No store internals are bypassed on the READ side —
    the loop measures its own deltas."""
    import psycopg

    with psycopg.connect(dsn, autocommit=True) as conn:
        for i in range(receipt.signups):
            conn.execute(
                "insert into app_users (business_slug, email) values (%s,%s)",
                (slug, f"sim-{slug}-{seq}-{i}@example.test"),
            )
        if receipt.profit_cents:
            conn.execute(
                "insert into app_revenue_events (business_slug, amount_paid_cents, revenue_type) "
                "values (%s,%s,%s)", (slug, int(receipt.profit_cents), "checkout"),
            )
        if receipt.skill in AD_BUCKETS:
            st = state[receipt.skill]
            st.spend_cents += receipt.spend_cents
            st.impressions += receipt.impressions
            st.clicks += receipt.clicks
            st.profit_cents += receipt.profit_cents
            st.purchases += receipt.signups
            camp = f"{receipt.skill}-sim"
            business_ad_spend.upsert_policy(
                conn, business_slug=slug, channel=receipt.skill, slug=camp,
                reservation_key=f"resv-{slug}-{receipt.skill}",
                reserved_credits=10_000_000, daily_budget_cents=max(1, receipt.spend_cents),
                total_budget_cents=10_000_000,
                start_at=datetime.now(timezone.utc) - timedelta(days=1),
                end_at=datetime.now(timezone.utc) + timedelta(days=365),
                status="active", last_synced_spend_cents=st.spend_cents,
            )
            syncs = store._resolve_business_file(
                slug, f"metrics/{receipt.skill}-ads/{camp}/syncs", sync=False)
            syncs.mkdir(parents=True, exist_ok=True)
            totals: dict[str, Any] = {
                "impressions": st.impressions, "clicks": st.clicks,
                "spend_usd": st.spend_cents / 100.0,
            }
            if receipt.skill == "meta":
                # Mirror the Meta-pixel revenue rail: the insights sync aggregates
                # action_values onto the receipt as purchase_value_usd / purchase_count.
                # Reddit has no purchase attribution today, so its receipts stay without —
                # the environment reproduces that asymmetry so the loop is tested on both.
                totals["purchase_value_usd"] = st.profit_cents / 100.0
                totals["purchase_count"] = st.purchases
            (syncs / f"{seq:06d}.json").write_text(
                json.dumps({"totals": totals}), encoding="utf-8",
            )


# --------------------------------------------------------------------------- run + score

@dataclass
class WakeRecord:
    t: int
    arm: str
    variant: str
    spend_cents: int
    creation_cents: int
    profit_cents: int
    signups: int
    page_visits: int
    exp_roas_chosen: float
    exp_roas_best: float
    best_arm: str

    @property
    def total_cost_cents(self) -> int:
        return self.spend_cents + self.creation_cents

    @property
    def realized_roas(self) -> float:
        return (self.profit_cents / self.total_cost_cents) if self.total_cost_cents else 0.0

    @property
    def optimal(self) -> bool:
        return self.arm == self.best_arm

    @property
    def roas_gap(self) -> float:
        return max(0.0, self.exp_roas_best - self.exp_roas_chosen)


def run_simulation(*, dsn: str, world: Any, chooser: Chooser, wakes: int, slug: str,
                   root: Path, operator_user_id: str = "rlsim-op", inject: bool = True,
                   skill_feedback: bool = True, period_hours: int = 24,
                   start: datetime | None = None, identity: str | None = None) -> "SimReport":
    """Drive ``wakes`` periods of the per-business process and score the outcome. Two
    independently ablatable feedback channels:
      * ``inject``          — the REAL loop's wake-learnings injection (skill choice, step 1)
      * ``skill_feedback``  — the appended per-skill run history (execution, steps 4+5)
    Turning one off hands the corresponding decision an empty text — the memoryless
    baseline for that level."""
    store = TakyonStore(root=root, database_url=dsn, operator_user_id=operator_user_id)
    seed_business(dsn, slug)
    clock = _Clock(start or datetime(2026, 1, 1, tzinfo=timezone.utc))
    state = {a: _ArmState() for a in AD_BUCKETS}
    records: list[WakeRecord] = []
    runs_per_skill: dict[str, int] = {}
    with _patched_clock(clock):
        if identity:
            store.set_identity(slug, identity)
        for t in range(wakes):
            # 1. choose skill — from the real loop's injected learnings
            injected = store.rl_policy(slug)["injected_learnings"] if inject else ""
            arm = chooser.choose(injected, world.arms(), t)
            # 2. run the skill — variant chosen from its working prompt (+ appended history)
            variants = world.variants(arm)
            sp = skill_prompt(store, slug, arm, variants, include_history=skill_feedback)
            variant = chooser.choose_variant(arm, sp, variants, t)
            store.record_episode(slug, f"push {arm} to grow roas", channel=arm,
                                 action_kind=variant)
            receipt = world.run(arm, variant, t)
            _apply_outcome(store, dsn, slug, receipt, state, t)
            # 3+4. upon next wake: compute full-cost ROAS and append process+metrics to the
            #      skill's per-business history (the skill-prompt feedback for its next run)
            clock.advance(hours=period_hours)
            runs_per_skill[arm] = runs_per_skill.get(arm, 0) + 1
            _append_skill_history(store, slug, receipt, run_idx=runs_per_skill[arm],
                                  period_hours=period_hours, observed_at=clock.now_iso())
            # ...and the real deterministic distill keeps feeding the wake-learnings rail
            store.distill_episode_lessons(slug, now=clock.t)
            records.append(WakeRecord(
                t=t, arm=arm, variant=variant, spend_cents=receipt.spend_cents,
                creation_cents=receipt.creation_cents, profit_cents=receipt.profit_cents,
                signups=receipt.signups, page_visits=receipt.page_visits,
                exp_roas_chosen=world.expected_roas(arm, t, variant=variant),
                exp_roas_best=world.expected_roas(
                    world.best_arm(t), t, variant=world.best_variant(world.best_arm(t))),
                best_arm=world.best_arm(t),
            ))
            # 5. loop — the next wake re-runs with the appended feedback
    lessons = store.rl_lessons(slug, limit=10_000)["lessons"]
    injected_final = store.rl_policy(slug)["injected_learnings"]
    return SimReport(slug=slug, records=records, world=world, lessons=lessons,
                     injected_final=injected_final, inject=inject,
                     skill_feedback=skill_feedback)


@dataclass
class SimReport:
    slug: str
    records: list[WakeRecord]
    world: Any
    lessons: list[dict[str, Any]]
    injected_final: str
    inject: bool = True
    skill_feedback: bool = True
    CONVERGE_FRACTION: float = 0.8  # tail must be >=80% optimal to count as "locked on"

    # --- headline metrics -------------------------------------------------------------
    def _learned_ranking(self) -> list[tuple[str, float]]:
        """What the loop's OWN lessons believe each arm's latest measured revenue is — parsed
        from the injected learnings the CEO would read. This is the loop's estimate of the
        ranking; compare it to ground truth to score lesson correctness."""
        est: dict[str, float] = {}
        for a in self.world.arms():
            m = re.search(_REV_RE.format(arm=re.escape(a)), self.injected_final)
            if m:
                est[a] = float(m.group(1))
            elif re.search(_NOMOVE_RE.format(arm=re.escape(a)), self.injected_final):
                est[a] = 0.0
        return sorted(est.items(), key=lambda kv: kv[1], reverse=True)

    def skill_improvement(self) -> dict[str, dict[str, Any]]:
        """Process step 5's question, per skill: did ROAS improve run-over-run? Compares the
        realized full-cost ROAS of the skill's EARLY runs (first half) vs LATE runs (second
        half), and how often the late runs used the world's best variant. Skills with <4
        runs are reported but not judged."""
        out: dict[str, dict[str, Any]] = {}
        for skill in self.world.arms():
            runs = [r for r in self.records if r.arm == skill]
            entry: dict[str, Any] = {"runs": len(runs)}
            if len(runs) >= 4:
                half = len(runs) // 2
                early = runs[:half]
                late = runs[half:]
                entry["early_roas"] = round(sum(r.realized_roas for r in early) / len(early), 4)
                entry["late_roas"] = round(sum(r.realized_roas for r in late) / len(late), 4)
                entry["improved"] = entry["late_roas"] > entry["early_roas"]
                if len(self.world.variants(skill)) > 1:  # only meaningful with a lever
                    best_v = self.world.best_variant(skill)
                    entry["best_variant"] = best_v
                    entry["best_variant_share_late"] = round(
                        sum(1 for r in late if r.variant == best_v) / len(late), 4)
            out[skill] = entry
        return out

    def summary(self) -> dict[str, Any]:
        n = len(self.records) or 1
        total_cost = sum(r.total_cost_cents for r in self.records) / 100.0
        total_profit = sum(r.profit_cents for r in self.records) / 100.0
        pct_optimal = sum(r.optimal for r in self.records) / n
        cum_regret = sum(r.roas_gap * (r.total_cost_cents / 100.0) for r in self.records)
        # oracle: always the best skill at its best variant, at each t
        oracle_profit = sum(r.exp_roas_best * (r.total_cost_cents / 100.0) for r in self.records)
        lastq = self.records[-max(1, n // 4):]
        # Convergence = the earliest wake from which the policy stays optimal at least
        # CONVERGE_FRACTION of the time over a SUSTAINED tail (>= 10% of the run, min 3
        # wakes). A fraction (not a pure suffix) so ε-greedy's occasional exploration pull
        # doesn't hide a policy that has clearly locked on; the tail length stops a uniform
        # explorer from "converging" on a lucky final wake. None => never locked on.
        conv = None
        min_tail = max(3, n // 10)
        for k in range(len(self.records)):
            tail = self.records[k:]
            if len(tail) < min_tail:
                break
            if sum(r.optimal for r in tail) / len(tail) >= self.CONVERGE_FRACTION:
                conv = k
                break
        pulls = {a: sum(1 for r in self.records if r.arm == a) for a in self.world.arms()}
        truth_rank = [name for name, _ in sorted(
            ((a, self.world.expected_roas(a, n - 1)) for a in self.world.arms()),
            key=lambda kv: kv[1], reverse=True)]
        learned = self._learned_ranking()
        learned_rank = [a for a, _ in learned]
        return {
            "inject": self.inject,
            "skill_feedback": self.skill_feedback,
            "wakes": n,
            "realized_roas": round(total_profit / total_cost, 4) if total_cost else 0.0,
            "oracle_roas": round(oracle_profit / total_cost, 4) if total_cost else 0.0,
            "efficiency_vs_oracle": round(total_profit / oracle_profit, 4) if oracle_profit else 0.0,
            "pct_optimal": round(pct_optimal, 4),
            "pct_optimal_last_quartile": round(sum(r.optimal for r in lastq) / len(lastq), 4),
            "cumulative_regret_usd": round(cum_regret, 2),
            "avg_regret_per_wake_usd": round(cum_regret / n, 4),
            "convergence_wake": conv,
            "total_profit_usd": round(total_profit, 2),
            "total_cost_usd": round(total_cost, 2),
            "total_signups": sum(r.signups for r in self.records),
            "pulls": pulls,
            "ground_truth_rank": truth_rank,
            "learned_rank": learned_rank,
            "learned_rank_correct": learned_rank == truth_rank,
            "best_arm_identified": bool(learned_rank) and learned_rank[0] == truth_rank[0],
            "lessons_total": len(self.lessons),
            "skill_improvement": self.skill_improvement(),
        }

    def render(self) -> str:
        s = self.summary()
        feedback = (f"wake-learnings {'ON' if self.inject else 'OFF'} · "
                    f"skill-history {'ON' if self.skill_feedback else 'OFF'}")
        lines = [
            f"── RL-loop simulation · business={self.slug} · {feedback} ──",
            f"  wakes                 {s['wakes']}",
            f"  realized ROAS         {s['realized_roas']}   (oracle {s['oracle_roas']}, "
            f"efficiency {s['efficiency_vs_oracle']:.0%})",
            f"  % optimal skill       {s['pct_optimal']:.0%}   (last quartile "
            f"{s['pct_optimal_last_quartile']:.0%})",
            f"  cumulative regret     ${s['cumulative_regret_usd']}   "
            f"(${s['avg_regret_per_wake_usd']}/wake)",
            f"  convergence wake      {s['convergence_wake']}",
            f"  profit / cost         ${s['total_profit_usd']} / ${s['total_cost_usd']}   "
            f"({s['total_signups']} signups)",
            f"  skill pulls           {s['pulls']}",
            f"  ground-truth ROAS rank {s['ground_truth_rank']}",
            f"  loop-learned rank      {s['learned_rank']}  "
            f"(correct={s['learned_rank_correct']}, best-arm={s['best_arm_identified']})",
            f"  lessons in store      {s['lessons_total']}",
            "  per-skill run-over-run ROAS (process step 5):",
        ]
        for skill, imp in s["skill_improvement"].items():
            if "late_roas" in imp:
                line = (
                    f"    {skill:<7} runs {imp['runs']:>3} · ROAS {imp['early_roas']:.2f}"
                    f" -> {imp['late_roas']:.2f} ({'improved' if imp['improved'] else 'flat/worse'})"
                )
                if "best_variant_share_late" in imp:
                    line += f" · best-variant share late {imp['best_variant_share_late']:.0%}"
                lines.append(line)
            else:
                lines.append(f"    {skill:<7} runs {imp['runs']:>3} · too few runs to judge")
        return "\n".join(lines)


# --------------------------------------------------------------------------- CLI

def _parse_roas(spec: str | None) -> dict[str, float] | None:
    if not spec:
        return None
    out: dict[str, float] = {}
    for part in spec.split(","):
        k, _, v = part.partition("=")
        out[k.strip().lower()] = float(v)
    return out


def _fresh_slug(prefix: str) -> str:
    import uuid

    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def filter_world_skills(world: Any, skills: Sequence[str]) -> Any:
    """Restrict the world to a subset of skills (the --skills flag). A single skill turns the
    run into a pure EXECUTION-level test (variant learning within that skill); several skills
    keep the allocation level too. Fails loudly on unknown names."""
    wanted = [s.strip().lower() for s in skills if s.strip()]
    if not wanted:
        return world
    if isinstance(world, ReplayWorld):
        unknown = [s for s in wanted if s not in world.arms()]
        if unknown:
            raise SystemExit(f"--skills: {unknown} not in replay table (has {list(world.arms())})")
        world.roas_table = [{k: v for k, v in row.items() if k in wanted}
                            for row in world.roas_table]
        return world
    unknown = [s for s in wanted if s not in world.channels]
    if unknown:
        raise SystemExit(f"--skills: unknown {unknown} (available: {list(world.channels)})")
    world.channels = {k: v for k, v in world.channels.items() if k in wanted}
    return world


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Offline RL-loop test environment (per-business ROAS process).")
    ap.add_argument("--dsn", default=os.environ.get("TAKYON_TEST_PG_DSN", ""),
                    help="migrated Postgres DSN (default: $TAKYON_TEST_PG_DSN)")
    ap.add_argument("--wakes", type=int, default=150)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--budget-cents", type=int, default=1000)
    ap.add_argument("--noise", type=float, default=0.12)
    ap.add_argument("--drift", type=float, default=0.0)
    ap.add_argument("--epsilon", type=float, default=0.08)
    ap.add_argument("--roas", default="", help='override, e.g. "meta=0.7,reddit=1.3,seo=2.1"')
    ap.add_argument("--variants", action="store_true",
                    help="OPT-IN: give each skill its built-in execution variants (creative/"
                         "content types with hidden quality multipliers) so within-skill "
                         "improvement is measurable with the scripted driver. Default: the "
                         "run IS the skill — no execution choice; ROAS tracks the channel's "
                         "true rate.")
    ap.add_argument("--skills", default="",
                    help="REQUIRED (unless --replay): the ONE skill under test, e.g. 'meta'. "
                         "The operator picks the skill; the agent never selects it here. A "
                         "single skill disables the wake-learnings channel so any ROAS "
                         "improvement is attributable solely to the skill's appended run "
                         "history. Listing several (e.g. 'meta,reddit,seo') is an explicit "
                         "opt-in for allocation experiments.")
    ap.add_argument("--replay", default="",
                    help="BACKTEST: path to a per-period per-channel ROAS table (CSV period,channel,roas "
                         "or JSON). Replaces the synthetic world; --wakes is set to the table length.")
    ap.add_argument("--period-hours", type=int, default=24)
    ap.add_argument("--driver", choices=("scripted", "anthropic"), default="scripted")
    ap.add_argument("--model", default="claude-opus-4-8")
    ap.add_argument("--no-ablation", action="store_true",
                    help="skip the feedback-OFF baseline run (scripted driver only)")
    ap.add_argument("--slug-prefix", default="rlsim")
    args = ap.parse_args(argv)

    if not args.dsn:
        print("error: no DSN. Set TAKYON_TEST_PG_DSN or pass --dsn (a migrated throwaway Postgres).",
              file=sys.stderr)
        return 2
    if not args.skills.strip() and not args.replay:
        # One skill at a time is the testing contract: the operator picks the skill under
        # test; the agent's only learned decisions here are within-skill (execution).
        print("error: name the skill under test, e.g. --skills meta (one of: "
              + ", ".join(ARMS) + "). Multi-skill allocation runs are an explicit opt-in: "
              "--skills meta,reddit,seo.", file=sys.stderr)
        return 2

    root = Path(tempfile.mkdtemp(prefix="rlsim-"))
    roas = _parse_roas(args.roas)
    replay_table = load_replay_table(args.replay) if args.replay else None
    wakes = len(replay_table) if replay_table is not None else args.wakes

    def build_world() -> Any:
        if replay_table is not None:
            world: Any = ReplayWorld(replay_table, budget_cents=args.budget_cents,
                                     rng=random.Random(args.seed))
        else:
            world = default_world(args.seed, budget_cents=args.budget_cents, noise=args.noise,
                                  drift=args.drift, roas=roas,
                                  variants=_DEFAULT_VARIANTS if args.variants else None)
        return filter_world_skills(world, args.skills.split(","))

    # A single-skill run is a PURE skill-level test (the operator's process: feedback goes
    # only to the skill, via its appended run history). The wake-learnings channel exists to
    # steer the CHOICE between skills, so with one skill it is disabled outright — any ROAS
    # improvement is then attributable solely to the skill-prompt feedback.
    wake_inject = len(build_world().arms()) > 1

    if args.driver == "anthropic":
        chooser: Chooser = make_anthropic_chooser(args.model)
        report = run_simulation(dsn=args.dsn, world=build_world(), chooser=chooser,
                                wakes=wakes, slug=_fresh_slug(args.slug_prefix), root=root,
                                inject=wake_inject)
        print(report.render())
        return 0

    # scripted: run the feedback channels ON, and (default) the all-OFF ablation for contrast.
    on_slug = _fresh_slug(args.slug_prefix)
    on = run_simulation(dsn=args.dsn, world=build_world(),
                        chooser=ScriptedChooser(random.Random(args.seed), epsilon=args.epsilon),
                        wakes=wakes, slug=on_slug, root=root,
                        inject=wake_inject, skill_feedback=True)
    print(on.render())
    for skill in build_world().arms():
        history = root / "businesses" / on_slug / _skill_history_relpath(skill)
        if history.exists():
            print(f"  feedback file          {history}")
    if not args.no_ablation:
        off = run_simulation(dsn=args.dsn, world=build_world(),
                            chooser=ScriptedChooser(random.Random(args.seed), epsilon=args.epsilon),
                            wakes=wakes, slug=_fresh_slug(args.slug_prefix), root=root,
                            inject=False, skill_feedback=False)
        print()
        print(off.render())
        d_regret = off.summary()["cumulative_regret_usd"] - on.summary()["cumulative_regret_usd"]
        d_opt = on.summary()["pct_optimal_last_quartile"] - off.summary()["pct_optimal_last_quartile"]
        d_roas = on.summary()["realized_roas"] - off.summary()["realized_roas"]
        print()
        print(f"── ablation delta (all feedback ON − OFF) ──")
        print(f"  regret avoided by feedback    ${round(d_regret, 2)}")
        print(f"  optimal-skill lift (last q)   {d_opt:+.0%}")
        print(f"  realized ROAS lift            {d_roas:+.2f}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
