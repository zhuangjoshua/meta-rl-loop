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
`sim/audit/gradient-e3-vN.md` (epoch-prefixed) and record its SHA-256 in `progress.md`.** A
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
(Historical example of the metric, epoch 1: v1 = 1.671, v2 = 3.275, v3 =
3.003 — closed history; see the Version bootstrap below for the live epoch's
state.)

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
`progress.md`, `learnings.md`, and `sim/audit/gradient-e3-vN.md` snapshots
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

Closed-epoch history (context only, never selection input): epoch 1's v1
learned the market's #1 creative proof element blind but never touched
targeting/budget in 8 iterations; its v2 (Purchase-Intent Concentration,
forced allocation decisions) was the first version to clear ROAS 3
(4.2982 on `formflow-outer-6`). Epoch 2 (old seed, paused 2026-07-23) reached
its own v3; its versions are archived in `sim/audit/epoch2/`. Engine caveats that shape
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

Version bootstrap (EPOCH 3, new seed policy — operator reset, 2026-07-23
afternoon): this epoch runs on the NEW seed policy — the merged
production-shaped policy (three ownership sections, dormant PROD-ONLY blocks,
sequenced coverage plan) now installed at `sim/seed-policy.md`
(`4eb908330147`). The prior seed is kept for revert at
`sim/seed-policy-original.md` (also in git history). Both seeds carry a
byte-identical batch-1 anchor slate, so start scores remain comparable across
the seed change; the policy prose from batch 2 onward differs, which is why
the seed swap is an epoch boundary.

The gradient restarts from v1 (`1eb5bb38a139`, the committed file — snapshot
`sim/audit/gradient-v1.md`, the shared root of all epochs). Prior epochs are
closed history in `progress.md`; their gradient texts are archived at
`sim/audit/epoch1/` (the overnight epoch) and `sim/audit/epoch2/` (the paused
epoch). THIS epoch snapshots its versions as `sim/audit/gradient-e3-vN.md`
(numbered from e3-v2, since v1 is the shared root; a naming note at the end
of `progress.md` maps this epoch's early ledger labels v5-v8 onto
e3-v2..e3-v5). Epoch 3 begins by running v1 on a fresh
world; that run's improvement ratio becomes v1's score and the epoch's
opening bar; no prior-epoch score is ever used for selection. World seed 19
was generated during the pause and may serve as this epoch's first world if
its generation completed and validated; otherwise continue from seed 20
(seeds 1-19 are used or excluded per `progress.md`).

Where things live: the editable gradient is
`ad-creative-stack/semantic-gradient.md`; version snapshots in
`sim/audit/gradient-e3-vN.md` for this epoch. Results: newest dir under `sim/runs/tier-b/` —
`summary.json` (`held_out_expected.final_roas` is the score;
`loop.roas_by_iteration` the trajectory), `lineage.json` (theses/rungs),
`evidence.json` (ads + receipts). Hidden info (post-mortem only): the world's
`population-model.json` and `sim/human-variation-core-v1.json`. Expect ~5 min
per world generation and ~20–30 min per inner run; both make live `codex` CLI
calls. If a run dies on "invalid microprofile reactions," that's the known
judge flake — rerun it; the loop self-recovers via cache purge. Prior baseline
run: `sim/runs/tier-b/world-71-default-seed-1-20260723T023502Z/`.
