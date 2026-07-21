# Runbook — running and improving the semantic-gradient RL loop

Self-contained operator's manual. A fresh session (human or agent) should be
able to run simulations, read results, audit failures, and improve the system
from this document alone. History and settled design arguments live in
RL-LOOP-HANDOFF.md; this file is the HOW.

## 1. What this system is

An RL loop that improves an advertising POLICY, where both the policy and the
improvement operator are prose documents executed by LLMs:

  policy (prose) -> design a $200 ad batch -> market returns receipts
    -> receipts appended to full-fidelity evidence
    -> SEMANTIC GRADIENT reads goal + policy + all evidence, produces ONE
       falsifiable thesis rendered as SIX independent policy revisions
       ("rungs"), smallest change to boldest
    -> a seeded noise schedule draws which rung (or the incumbent) is adopted
    -> adopted rung becomes the next policy version -> repeat (usually x8).

The improvement operator IS `ad-creative-stack/semantic-gradient.md`. It is
the product of this project. Everything else is test rig.

The gradient doc carries an accumulated rule stack, every rule earned from an
audited failure and named for it (e.g. "Replication before doctrine — the
world-61 lesson"). Read the doc before running anything; it is ~330 lines and
is injected verbatim into every gradient call.

## 2. The two simulators

TIER A (free, fast, coarse) — `sim/worldgen.py`, `sim/market.py`,
`sim/score.py`, protocol `sim/agent-protocol.md`. Ads are feature TAGS
(proof angle, demo on/off); the market scores tags against hidden per-world
numbers. Run by giving Claude subagents the agent protocol. Use for cheap
rule A/B tests at scale (10-20 worlds, paired arms). `sim/score.py <w>`
reveals a world's ground truth + oracle AFTER a run (revealing spends the
world — never reuse a revealed world for a sealed run).

TIER B (realistic, costs real API money) — `sim/tier_b_experiment.py`,
`sim/tier_b_market.py`, `sim/llm_client.py`, `sim/noise_schedule.py`,
`sim/tier_b_sweep.py`, spec `sim/TIER-B.md`. Ads are REAL COPY. Two isolated
LLM roles: a policy agent designs 3 complete ads + runs the gradient; a
hidden judge plays 10 sealed buyer personas reacting to the actual ad text
and the landing page (`sim/formflow-landing-page.md` — the fake product's
site, read by both sides). Code then samples the judge's rate distributions
through a funnel and returns aggregate receipts. Use for validating rules
where language matters.

Each Tier B run also produces, automatically:
- a FROZEN BASELINE: the run's own iteration-1 design replayed unchanged —
  "what would no learning have earned";
- a HELD-OUT EVALUATION: the judge scores initial vs final policy designs in
  EXPECTATION — the clean measure of policy quality, stripped of dice.
Realized revenue is noisy (purchases are rare events); held-out is the honest
read. When they disagree (w61: +$812 realized, +0.066 held-out) trust
held-out and suspect sampling luck.

## 3. Tier B prerequisites

In the terminal that will run experiments (keys NEVER go in files, chat, or
this repo):

    cd <repo root>
    export TIER_B_JUDGE_API_KEY="sk-..."       # OpenAI key
    export TIER_B_AGENT_API_KEY="$TIER_B_JUDGE_API_KEY"
    export SSL_CERT_FILE=$(python3.12 -c "import certifi;print(certifi.where())")

Models (OpenAI GPT-5.6 family, API ids): judge `gpt-5.6-luna` (cheap tier,
~9 calls/iteration), agent `gpt-5.6-terra` (balanced tier, 2 calls/iteration,
most of the cost). Cost ~$2.50-3.50 per 8-iteration world; ~15-20 min/world.
The judge caches persona-x-ad judgments in `sim/cache/` — reruns are cheaper.
NOTE: editing the landing page changes judge cache keys; results before/after
such an edit are not strictly comparable (paired arms within one run are).

## 4. Running

Generate fresh sealed worlds (never reuse revealed/spent ones; worlds 1-70
are spent as of 2026-07-20 — start at 71):

    python3.12 sim/worldgen.py 71 $((71*991+17))

Single world, 8 iterations:

    python3.12 sim/tier_b_experiment.py 71 \
      --landing-page sim/formflow-landing-page.md \
      --iterations 8 \
      --judge-provider openai --judge-model gpt-5.6-luna \
      --agent-provider openai --agent-model gpt-5.6-terra

Multi-world sweep (worlds run in parallel; a failed world is reported and the
rest continue):

    python3.12 sim/tier_b_sweep.py \
      --worlds 71,72,73,74,75 \
      --schedules default \
      --iterations 8 --parallel-worlds 3 \
      --landing-page sim/formflow-landing-page.md \
      --judge-provider openai --judge-model gpt-5.6-luna \
      --agent-provider openai --agent-model gpt-5.6-terra

Schedules (`sim/noise_schedule.py --iterations 8` prints rung probabilities):
`default` is aggressive (mean adopted rung ~4, <1% keep at iter 8) — measured
to churn good incumbents; `conservative` cools hard; also `wide`,
`persistent`, `greedy-small`. `--schedules default,conservative` runs paired
arms per world. Draws are seeded by (world, run seed, iteration) so identical
configs reproduce identical selections.

Outputs land in `sim/runs/tier-b/world-N-<schedule>-seed-S-<stamp>/`:
- `summary.json` — loop vs frozen-baseline revenue/ROAS by iteration,
  held-out initial/final expected ROAS, selected rungs, token/call stats
- `lineage.json` — per iteration: thesis, mechanism, evidence basis,
  confidence, breadth, falsifier, all 6 rungs, draw, adoption
- `evidence.json` — full history the agent saw (policies, ads, receipts)
- `specs/iteration-N.json` — the actual ads (headline/message/visual/CTA)
  and campaigns bought each batch
- `current-policy.md`, `initial-expected.json`, `final-expected.json`
Sweeps also write `sweep-<stamp>.json` with aggregate + failures.
`sim/runs/` is GITIGNORED — copy anything worth keeping into `sim/audit/`
(pattern: `sim/audit/tier-b-keep/`).

Sanity tests (no API needed): `PYTHONPATH=. pytest -q sim/test_tier_b.py`
(pytest lives in `hermes-agent-main/.venv/bin/pytest`).

## 5. The improvement method (this is the real engine)

Progress comes from an audit loop, not from clever guessing:

1. REGISTER predictions before any run (what the current rules should make
   agents do, stated falsifiably — e.g. "a story ad appears in the first two
   batches", "zero bookkeeping-only theses").
2. RUN (paired arms when comparing rule versions: same worlds, same seeds,
   old doc vs new doc — world quality varies far more than rule effects, so
   only paired designs have signal).
3. AUDIT: read every lineage against the predictions and the revealed ground
   truth. Classify each world's primary mode. Known taxonomy so far:
   FOUND-AND-HELD, EXPLORATION-TAX, SAMPLING-LUCK, FALSE-DISCOVERY (hot cell
   crowned on one draw), RULE-FIXATION (bookkeeping displaced advertising),
   NEVER-TESTED (best option not on the board), CHURN (good incumbent
   rewritten late). Write the audit to `sim/audit/` with verbatim quotes.
4. PROPOSE rules only for failures DEMONSTRATED in multiple worlds; state
   each with calibrated confidence. Adoption bar: demonstrated-failure-or-
   defer. Register weak candidates as watch items instead.
5. ARCHIVE the current gradient doc to `sim/audit/` (it becomes the control
   arm), EDIT the canonical doc, mirror in `sim/agent-protocol.md` only if
   Tier A will run, update RL-LOOP-HANDOFF.md, run the unit tests.
6. VALIDATE with a fresh paired run. If the new doc doesn't win, the rule
   comes back out.

Hard-earned meta-lessons (all three observed repeatedly — design rules
accordingly):
- LLM executors follow rule LETTER, not intent: rules get evaded by rewording
  ("neutral" instead of "demote"), turned into runaway arithmetic (the
  receding floor), or worshipped until they displace the goal (rule-fixation).
  State intent explicitly; bind acts and budget shares, not vocabulary; give
  worked numeric examples for any load-bearing arithmetic.
- Oracles diagnose, receipts govern: sealed ground truth and held-out scores
  are for auditing only; every RULE must run on receipts a real ad account
  would produce (that is why the luck-filter is replication, not expectation).
- Doc bloat is a real cost: every added rule dilutes attention on the rest
  (rule-fixation was partly a salience failure). Budget ~330 lines; prefer
  consolidation over addition.

## 6. Current state (2026-07-20)

Gradient doc rule stack: original discipline (one thesis, composition rule,
evidence sizing, 6 independent rungs) + Tier A audit rules (readable-evidence
floor with FIXED arithmetic + stop-loss + worked example; interaction
coverage; act-not-word demotion floor; drought escalation) + Tier B audit
rules (menu before grid; replication before doctrine; verdicts-not-design;
creative-is-policy with champion/challenger). All archived versions for
control arms are in `sim/audit/semantic-gradient-*.md`.

Validation status: Tier A rules validated twice (paired runs: +14% then the
amendment +10%). Tier B fixes smoke-tested once (world 70: fixes 1+3 clearly
working, 0/8 bookkeeping theses, profitable run +19% realized +28% held-out;
fix 2 unblocked by the landing-page testimonial patch; fix 4 untested).

THE NEXT PLANNED RUN (not yet executed): 10 fresh worlds (71+), paired arms
`--schedules default,conservative`, current doc vs archived
`sim/audit/semantic-gradient-v3-merged-2026-07-20.md` (pre-fix control).
Registered predictions: bookkeeping theses ~0 (was 50-88%); a story ad in
every world's first two batches; no single-batch doctrine; fresh creative
variants appear (fix 4's test); conservative arms hold gains better than
default. Watch items: token-variant gaming of fix 4; letter-evasion of
replication via "strong lean" wording.

Git: main = merged Tier B (PR #1); branch `tier-b-audit-fixes` = the four
fixes + rig patch + audits. Merge to main only after the validation run.

## 7. Pitfalls

- Session/usage caps kill parallel agent fleets mid-run; sub-runs die at
  iteration 3-6. Check completed `summary.json` before rerunning anything —
  finished worlds stay finished; clear partial dirs before a rerun.
- OpenAI 429s: keep `--parallel-worlds` ~3 on a fresh key; raise gradually.
- macOS SSL: without SSL_CERT_FILE=certifi bundle, every call fails with
  CERTIFICATE_VERIFY_FAILED.
- GPT-5.6 rejects `max_tokens` and custom temperature (runner already
  patched; remember if writing new callers).
- The runner fails closed on malformed agent output; a repair-retry (3
  attempts, error fed back — which also busts the response cache) is built
  in. If a world still fails, the sweep continues without it.
- Worlds are single-use: revealing (score.py, reading subpops-hidden.json,
  or publishing an audit that quotes ground truth) spends them forever.
- Never paste API keys into chat or commit them; the operator exports them
  in their own terminal. Rotate any key that leaks.
- Tier A results (worlds 1-57) and Tier B results are not comparable to each
  other, and Tier B results before/after a landing-page edit are not
  strictly comparable either.

## 8. File map

    ad-creative-stack/semantic-gradient.md   THE improvement operator (edit here)
    ad-creative-stack/loop/                  real-policy lineage demo (v4->v7)
    POLICY.md                                operator's original master policy (do not touch)
    RL-LOOP-HANDOFF.md                       full history, results, settled design decisions
    RUNBOOK.md                               this file
    sim/TIER-B.md                            Tier B design spec (isolation, backends, schedules)
    sim/tier_b_experiment.py / tier_b_sweep.py / tier_b_market.py
    sim/llm_client.py / noise_schedule.py    runner internals
    sim/formflow-landing-page.md             the fake product's site (agent+judge both read it)
    sim/seed-policy.md                       v0 policy every world starts from
    sim/worldgen.py / market.py / score.py   Tier A world generator / market / reveal oracle
    sim/agent-protocol.md                    Tier A subagent protocol (mirror of doc rules)
    sim/world-N/                             sealed worlds (subpops-hidden.json = ground truth, SEALED)
    sim/runs/tier-b/                         Tier B outputs (gitignored)
    sim/cache/                               judge response cache (gitignored)
    sim/audit/                               audits, archived doc versions, kept results (tracked)
