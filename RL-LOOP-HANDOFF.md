# Handoff — Meta-ads RL loop (semantic gradient) — session of 2026-07-15/16

> Update 2026-07-18: the statement below that Tier B was not built is historical.
> The real cached LLM-persona market, complete adaptive runner, schedule controls,
> and paired sweep runner now live in `sim/tier_b_market.py`,
> `sim/tier_b_experiment.py`, `sim/noise_schedule.py`, and
> `sim/tier_b_sweep.py`; see `sim/TIER-B.md`. The original session record is
> otherwise preserved verbatim.

Everything below was built and tested in one long session. All artifacts are on
disk; nothing depends on the old session's memory. Nothing here touched the VPS,
any git remote (none is configured), or real ads/money. All ad/market data is
FABRICATED or simulated.

## What the system is

An RL loop that improves an advertising POLICY (a prose document) using an
improvement operator that is ALSO a prose document, executed by an LLM:

  policy + batch receipts + goal (ROAS)
    -> semantic gradient (one falsifiable THESIS + 6 revisions of the policy,
       smallest to boldest — "doses"; evidence-derived direction, dose = step size)
    -> noise schedule picks the dose (seeded draw; bold early, tiny/no-change
       late; tau = 0.92^iteration, floor 0.05, Gaussian width 0.18 over the
       7-rung ladder [incumbent + 6 doses])
    -> adopted revision becomes the next policy version -> next ad batch.

Operator's key design decisions (argued and settled in-session):
- Zero code in the gradient: the operator IS ad-creative-stack/semantic-gradient.md.
- Doses are INDEPENDENT renderings of one thesis at increasing magnitude
  (explicitly NOT cumulative; rejected).
- Selection is a weighted draw over the boldness ladder (no top-k scoring;
  direction is trusted, schedule sets step size — SGD learning-rate analogy).
- Policies must be STANDALONE text (no thesis/era citations inside policy text).
- Evidence must be FULL-FIDELITY (full policy texts, full ad prompts, full
  settings, per-ad rows; never summarized — operator repeatedly enforced this).
- Everything in a policy is rewritable "intelligence layer" (rails split later).

## Key files (all under /Users/joshuazhuang/Documents/fourmanifold/meta-rl-loop)

THE LOOP (prose, canonical):
- ad-creative-stack/semantic-gradient.md — THE improvement operator. Contains the
  full accumulated rule stack: 3 inputs (goal / current policy / evidence grouped
  by policy version with full texts+prompts+settings+outcomes), one organizing
  thesis that may combine matched-pair, replicated-pattern, and design evidence
  when they support the same mechanism, coverage rule
  (sweep unpriced axis values before refining; unfunded=UNKNOWN), composition
  rule (never compare rates across differently-targeted cells — adopted at ~90%
  confidence), demotion floor (no axis demoted till funded spend expects >=3
  goal events), independent-dose ladder rules.
- ad-creative-stack/loop/ — real-policy lineage demo: policy-v4.md = operator's
  original 918-line POLICY.md (untouched master at repo root: POLICY.md);
  policy-v5..v7.md = materialized versions from 3 hand-run iterations (fake
  receipts) with anchor-applied edits; evidence.md = full-fidelity receipts.
- ad-creative-stack/ also holds: collected copies of takyon-meta-ads-v2 /
  ugc-video-ad / lightreel skills, ad-copy fragments (no ad-copy skill exists
  in this repo — known gap), sim-plan.md.

THE SIM (code = world only; gradient stays prose):
- sim/worldgen.py — sealed-world generator: 10 general decision archetypes
  (peer_proof, skeptic, herd, bargain, novelty, authority, committee, scroller,
  pragmatist, impulse) x sub-segments with weights + hidden dims (proof_pref
  over {benefit,outcome,count,story}, demo_gate, trial_gate, base,
  mismatch_bounce, clickiness, buyiness) + per-audience reach. Worlds are
  single-use: once revealed, spent.
- sim/market.py — market runner: batch spec JSON -> noisy receipts (Beta-
  Binomial sampling; auto-budget-mode concentration quirk; NO-TRUST flags).
  Has structured simulate() + CLI. Receipts leak nothing hidden.
- sim/score.py, sim/driver.py — reveal/oracle scoring; a scripted-doctrine
  driver (built but NOT used for the final experiment — operator rejected it:
  the gradient must be the LLM).
- sim/seed-policy.md — v0 policy for sim runs. sim/agent-protocol.md — the
  exact per-iteration protocol subagents follow (includes sealed-file bans,
  draw-before-adoption command, spec format).
- sim/world-1..5/ — hand-run worlds (I was the gradient), all REVEALED/spent.
- sim/world-6..25/ — the 20-world parallel experiment (subagent gradients),
  all run and revealed. Per world: llm-policy.md (final policy), llm-lineage.md
  (theses/draws), llm-evidence.md (receipts), llm-results.jsonl, specs.
- sim/baselines.json, sim/experiment-results.json — frozen-baseline runs and
  aggregated curves.

DASHBOARDS (claude.ai artifacts, republish same file path to update):
- Policy-loop dashboard (v4->v7 lineage, verbatim policies+prompts, sim world-1
  section): scratchpad file policy-loop.html,
  https://claude.ai/code/artifact/c5eb4aa8-354c-48c2-943d-b946fffeeb3f
- 20-world ROAS graph: scratchpad roas-experiment.html,
  https://claude.ai/code/artifact/054fd6b9-66d5-4720-bae5-5a84a30e0aaf
  (scratchpad = session temp dir; the HTML is regenerable from
  sim/experiment-results.json if lost)

ALSO IN REPO (earlier session work, unrelated to the loop core):
- hermes-agent-main/plugins/takyon/meta_ads_v2.py — MODIFIED (uncommitted):
  one-live-campaign guard relaxed to cap of 3 (_MAX_LIVE_META_CAMPAIGNS) +
  optional total_budget_usd launch arg for portfolio budget slices; tests
  updated and passing (tests/plugins/test_takyon_meta_ads_v2.py, 36/36).
- hermes-agent-main/plugins/takyon/rl_policy_loop.py / rl_policy_sim.py — the
  earlier NUMERIC engine + sim (NoiseSchedule lives here; the sim draws
  reproduce it inline). tests/plugins/test_rl_policy_loop.py 10/10.
- hermes-agent-main/.venv exists (uv-built) with pytest/psycopg/fastapi.

## Headline results (worlds are fabricated; loop is validated vs WORLDS not Meta)

20-world experiment (LLM gradient, $200/batch, 8 iterations, frozen baseline):
- mean ROAS by step: loop .008 .073 .073 .094 .189 .116 .095 .080
                     base .014 .022 .074 .066 .073 .030 .073 .022
- halves 0.062 -> 0.120 (2x); revenue $2,900 vs $1,479; wins 14 / ties 2 / losses 4
  (losses = near-dead worlds; world-11 agent correctly declared it unbuyable).
- Agents invented novel axes (story subject-vertical, CTA intent, concreteness),
  unwound their own falsified beliefs, self-imposed pooled-evidence windows.

Hand-run worlds taught (each lesson now a rule in semantic-gradient.md):
- w1 ($20 batches): starved goal layer -> proxy drift (sign-up chasing pulled
  off the true optimum). Fix: readable budget (~$200 => ~1-3 buys/batch).
- w2: noise-sweep hallucination adopted at bold dose (calm-style, no true
  effect) + self-confirming concentration (niche). Fix: replication before
  doctrine; per-dollar judgment.
- w3: coverage failure — best copy family (story) never tested in 8 iters.
  Fix: coverage ledger/sweep rule.
- w4: funded-absence vs unlucky-silence confusion (demo wrongly demoted).
  Fix: >=3-expected-events demotion floor.
- w5: composition blindness — pooled signup->purchase rates across differently-
  targeted cells misdiagnosed "offer broken" (belief-level error; contained by
  guards). Fix adopted: composition rule. PROPOSED BUT NOT ADOPTED (operator
  chose calibrated restraint): judge-world-by-best-cell (needs winner's-curse
  shrinkage), evidence-floor-with-sunset. Validation idea: engineered world
  with healthy buy-layer + tempting funnel-cliff shape.

## Known gaps / possible next steps (none committed)
- Real-world wiring: loop -> business_meta_ad_launch (paused-mode = real Meta
  objects, zero spend), policy files per business, hashes in receipts. The
  meta_ads_v2.py cap change supports 3-arm portfolios with total_budget_usd.
- Rules 2-3 from w5 pending the engineered-world test.
- Sim Tier B (LLM-judged subpopulation rate distributions) never built; Tier A
  feature-tag model is what ran.
- Process note: draws MUST run in their own call BEFORE writing adoption text
  (repeated pre-write errors are logged in world lineages as corrections).

## Operator communication rules (SAVED in project memory, auto-loads:
memory/communication-style.md) — no invented codes/letters, concrete before
abstract, gloss all jargon inline, one idea per sentence in key statements,
numbers always carry meaning, full fidelity in artifacts (never compress
evidence), label my framings as mine. The operator audits intake fidelity and
catches compression — do not summarize evidence files.
