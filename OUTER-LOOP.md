# Outer Loop — Semantic Gradient Evolution

The complete standing instructions for the continuous outer loop. Any session
(Codex or Claude) resuming this work reads this file first and follows it
exactly. `progress.md` is the ledger of what has already happened; this file is
the procedure.

## Objective

Run ONE continuous outer loop — no parallel variants — until BOTH:
(1) first-payment ROAS > 3 at the end of 3 consecutive inner loops, trending
~monotonically up across them; (2) we can print a causal,
hidden-information-grounded explanation of why the winning gradient works and
why it generalizes beyond any single world (not luck).

## Inner loop (execution, minority of effort)

One pass = one fresh world, one experiment: generate a new world (new seed,
same Formflow business/platform), then run one blind 8-iteration
`tier_b_experiment.py --market v2` experiment on it — Luna as consumer judge,
Terra as gradient model, Sol-xhigh as designer. Hidden information stays sealed
during the rollout. Every inner loop starts from the identical fixed seed
policy (`sim/seed-policy.md`); the advertising policy evolves only within its
own 8 iterations and is discarded with its world — nothing learned transfers
between worlds except through updates to `semantic-gradient.md`.

## Post-mortem (only after the inner loop completes)

Open the hidden audits and world file. Assess: ROAS trajectory and held-out
delta, pathologies (wasted HOLDs, rung misuse, thesis-evidence mismatch), and
qualitatively whether the gradient's rewrites addressed what the hidden
populations actually blocked on. Note the world's apparent difficulty (size and
reachability of its winnable segments) so score comparisons across worlds can
be read in context.

## Outer loop (majority of effort, but never stall)

Keep a current best gradient (initially v1, the committed
`semantic-gradient.md`). After each inner loop, post-mortem with the hidden
information, then write ONE large, justified update — the next version
(v2, v3, …) — printing the update and its causal justification.

**Before running any version: snapshot its exact text to
`sim/audit/gradient-vN.md` and record its SHA-256 in `progress.md`.** A
version that was never snapshotted must not be run.

Run the next inner loop (fresh world) with the new version.

**Selection rule (improvement ratio):** a version's score is its within-run
improvement ratio — held-out final ROAS divided by held-out initial ROAS on
its own world (`held_out_expected.ratio` in the run's `summary.json`; the
initial is the seed policy's noise-free score on that same world, which makes
the ratio self-normalizing for world difficulty). Compare the new version's
ratio against the current best's recorded ratio. If the new version's ratio is
less than or equal to the current best's, restore `semantic-gradient.md` from
the current best's snapshot in `sim/audit/` (verify the hash matches) and
diagnose why the new version failed before writing the next one. If it is
higher, the new version becomes the current best. Record BOTH numbers per
version in `progress.md` — the ratio (selection metric) and the absolute
held-out final ROAS (the objective's > 3 condition is judged on the absolute
number, not the ratio). Reverted versions stay in the time-series as evidence.
Current ledger under this metric: v1 = 1.671 (1.176 → 1.965), v2 = 3.275
(1.312 → 4.298, current best), v3 = 3.003 (0.212 → 0.636, reverted —
narrowly, on a very hard world).

Diagnose the BIG reason the gradient isn't producing meaningful updates — not
surface tweaks. If context grows long without a gradient update, make one now.
Then immediately generate the next world and launch the next inner loop.

## Edit constraint (applies to the entire run, outer and inner)

The ONLY behavioral file that may ever be modified is
`ad-creative-stack/semantic-gradient.md`. No code, prompt, config, or data
file changes anywhere, at any point: not the simulator, market, adapter,
generator, scheduler, validators, receipts, selection logic,
calibration/variation JSONs, seed policy, tests, or harness — even if a bug or
pathology is discovered mid-loop. If something outside the semantic gradient
appears broken, record it in `learnings.md` and work around it; if
unrecoverable (rerun with the existing cache-purge tool, or regenerate the
world), stop and ask. The only other writes are non-behavioral records —
`progress.md`, `learnings.md`, and `sim/audit/gradient-vN.md` snapshots
(append-only records) — plus artifacts that runs and world generations produce
on their own. This file (`OUTER-LOOP.md`) is operator-owned: the loop reads
it and never edits it.

## Recording

`progress.md`: per-world results, world-difficulty/sampling differences,
one-line description of each gradient version with its SHA-256.
`learnings.md`: insights, hypotheses, and full why-it-failed/why-it-succeeded
explanations — what was tried, why it failed or succeeded, not just scores.
Sync learnings to Notion (MCP) hourly when a Notion connection is available;
otherwise `learnings.md` is the sole log.

## Known context

Gradient v1 baseline: 1.02 held-out (0.87 mean) on
`formflow-live-seed1-2026-07-22`; v1 learned the market's #1 creative proof
element blind but never touched targeting/budget allocation. Overnight
2026-07-23: v2 (Purchase-Intent Concentration) scored 4.2982 held-out on
`formflow-outer-6` and is current best; v3 reverted (0.64 on `formflow-outer-10`);
v4 (Orthogonal Reach Triage) is authored but untested — world generation for
it repeatedly failed on architect/auditor timeouts. Engine caveats that shape
diagnosis: audiences reset each iteration (no fatigue/depletion), the judge
invalid-response purge lives in the adapter, and ROAS is first-month-payment
only. All work stays local — no GitHub pushes, especially not
`tier-b-audit-fixes`.

## Discipline

Default state is an active experiment. After each world: extract evidence →
update gradient → launch the next world. No polished audits mid-sequence, no
re-reading code, no status narration, no parallel loops. Stop only on the
objective, a true user blocker, or explicit interruption.

## Operational reference

Repo: `~/Documents/fourmanifold/meta-rl-loop`, branch `realistic-sim`, run
everything from repo root.

Generate a world (increment the seed each pass; run dirs must not already
exist; raise `--timeout` above the 1200s default if architect/auditor calls
are timing out):

```bash
python3 sim/population_generator.py --business-spec sim/business-spec-formflow.json --platform sim/world-71/platform.json --seed <N> --run-name formflow-outer-<N>
```

Inner loop on it:

```bash
python3 sim/tier_b_experiment.py 71 --landing-page sim/formflow-landing-page.md --market v2 --population-model sim/generated-populations/formflow-outer-<N>/population-model.json --judge-model gpt-5.6-luna --agent-model gpt-5.6-sol --agent-reasoning-effort xhigh --gradient-model gpt-5.6-terra --timeout 1800
```

Hash a gradient version (first 12 hex characters are what `progress.md` records):

```bash
shasum -a 256 ad-creative-stack/semantic-gradient.md
```

Version bootstrap (operator reset, 2026-07-23): the loop restarts from v1.
The working-tree file IS v1 (`1eb5bb38a139`, identical to the committed file;
recoverable anytime with `git restore`). Overnight versions v2 and v3 have no
surviving text (v2's 4.2982 run and v3's revert happened in a lost session;
their scores remain in `progress.md` as history of a closed epoch). v4
(`56c107e49de3`, never tested) is preserved at `sim/audit/gradient-v4.md` as
reference material — its ideas may inform future versions, but it holds no
selection status. The restart epoch begins by running v1 itself on a fresh world: that run's
improvement ratio becomes v1's score and the epoch's opening bar (prior-epoch
scores, including v1's old 1.671, are history and are never used for
selection). The first new version authored after v1's post-mortem is judged
against that fresh bar. The next world seed is 14 (seeds 1-13 are used or
excluded per `progress.md`).

Where things live: the editable gradient is
`ad-creative-stack/semantic-gradient.md`; version snapshots in
`sim/audit/gradient-vN.md`. Results: newest dir under `sim/runs/tier-b/` —
`summary.json` (`held_out_expected.final_roas` is the score;
`loop.roas_by_iteration` the trajectory), `lineage.json` (theses/rungs),
`evidence.json` (ads + receipts). Hidden info (post-mortem only): the world's
`population-model.json` and `sim/human-variation-core-v1.json`. Expect ~5 min
per world generation and ~20–30 min per inner run; both make live `codex` CLI
calls. If a run dies on "invalid microprofile reactions," that's the known
judge flake — rerun it; the loop self-recovers via cache purge. Prior baseline
run: `sim/runs/tier-b/world-71-default-seed-1-20260723T023502Z/`.
