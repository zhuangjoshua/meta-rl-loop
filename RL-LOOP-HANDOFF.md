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

## 2026-07-17 update — qualitative audit + gradient patches

After the 20-world run, all worlds were opened against the oracle
(sim/score.py) and four auditors compared each agent's funded configs and
final beliefs to ground truth. Full reports: sim/audit/qualitative-audit-
2026-07-17.md. Headline: oracle-attainable ≈ $6,200; loop $2,900 (47%),
baseline $1,479 (24%). Dominant systematic failure: the demo creative axis
(oracle-best in 19/20 worlds) was dropped via thin-slice misreads ($13 cells
expecting ~0.1 buys read as "demo is neutral") or never crossed with the
winning angle (story+demo never authored in w11/15/18/21/22/25). The
handoff's earlier claim that w11 was "correctly declared unbuyable" is
REFUTED by the oracle ($176/buy available; the agent's outcome-anchor false
belief from iteration-1 signup data was the actual cause). Also: the >=3-
events demotion floor lived only in sim/agent-protocol.md, not the gradient
doc, and was evaded by rewording ("neutral"/"a wash").

Three rules were adopted into ad-creative-stack/semantic-gradient.md (and
mirrored in sim/agent-protocol.md reminders); pre-patch control copy at
sim/audit/semantic-gradient-prepatch-2026-07-17.md:
1. Readable-evidence floor — a cell prices nothing unless funded spend
   expects >=1 goal event; thinner zeros are arithmetic, not evidence.
2. Interaction coverage — when a value becomes incumbent-best, its crosses
   with other axes' incumbent bests are unpriced and owed a readable test.
3. Demotion floor binds the act, not the wording — any reduced/ended funding
   is a demotion, licensed only at >=3 expected goal events; below, UNKNOWN
   keeps its slot.
4. Drought escalation (adopted later same day at operator direction) — a
   goal-event drought must fund unpriced incumbent-best crosses before any
   thesis may blame the destination/offer; declaring a market dead requires
   that exhausted ledger. The w11 fix.
Still gated on further evidence: provisional-belief sunset (the w5 sunset
rule) and the signup-evidence dose cap.

VALIDATION RUN COMPLETED 2026-07-17 (results: sim/audit/validation-results.json;
worlds 26-35 random + world-50 engineered proxy-trap; 22 agents, patched vs
pre-patch gradient, paired seeds): patched $2,320 vs control $2,030 (+$290,
+14%); oracle capture 50% vs 44%. Pairs 5W-6L for patched but asymmetric —
losses small ($29-87, exploration tax), wins large (+$87-203) concentrated
where the audited failures bite: w30 drought recovery +$145, w34 control
scored $0 while patched escaped +$58, and the engineered trap w50 +$203
(control anchored on the outcome signup-bait exactly as predicted; patched
found story+demo by it2 and required demo everywhere). Patched arms kept
demo alive in ~9/11 worlds vs ~4/11 for control (oracle wanted demo=True in
all 11). No false dead-declarations in patched arms; no w25-style frozen
diffusion observed. NEW PATHOLOGY FOUND (w26 -$87, partially w33): hero-cross
fixation — an agent chased an unpriced cross against a RECEDING readable
floor (each zero lowers the observed rate, raising the dollar floor),
burning 5 zero batches. Agents that priced crosses CUMULATIVELY with stable
targets (w27, w30, w50) avoided it and won. AMENDMENT ADOPTED later same
day (operator-approved): readable-floor arithmetic is FIXED not live — the
floor is computed once at test start from the then-observed rate and
recorded; spend accumulates across batches; stop-loss parks a config still
silent at its recorded floor or after 2-3 full-budget batches as UNPRICED
(resumable, no budget priority, no further consecutive batches); a worked
example is now in the doc. Four-rule pre-amendment doc archived at
sim/audit/semantic-gradient-v2-fourrules-2026-07-17.md. Verdict: the four
rules + amendment stay. SECOND EDIT same day: the >=3-event demotion floor
now explicitly uses the same fixed arithmetic (threshold computed and
recorded when the question opens; later zeros never raise it) — adopted as
a consistency extension of the proven world-26 fix. WATCH ITEM (registered,
not adopted — no observed casualty yet): pin the cross-servicing order in
the interaction-coverage rule (one owed cross at a time, boldest-untested
first); adopt only if a future run shows queue starvation or simultaneous
cross servicing.

TEST 3 COMPLETED 2026-07-18 (results: sim/audit/test3-v2-vs-v3-results.json;
worlds 51-57, v3 amended vs v2 four-rule, paired seeds, all Fable after the
Opus arms hit the 7:50pm session cap and were dropped): v3 $1,566 vs v2
$1,421 (+$145, +10%); v3 pairs 3W-2T-2L. Wins where the amendment bites
(w57 +174 clean demo-cross verdict; w54 +87; w51 +58 — v2 stuck re-running
an unresolved head-to-head below readable spend); losses in rich worlds
where v2 banked early (w53 -145, w55 -29). KEY QUALITATIVE FINDING (w55): the
v2 arm hit the receding-floor spiral LIVE (it5-8 all $0), named it at it7
("caps cross accrual below the receding readable floor"), and at it8
reconstructed the amendment itself ("freeze the cross floor at push-start")
— too late to score. So the failure the amendment fixes reproduces on fresh
worlds and under the old rules the model only sometimes self-rescues, always
too late. NO v3 arm entered the spiral; every v3 lineage shows recorded
floors + stop-loss + park firing as written (e.g. w55-v3 "floor $600
recorded... at stop-loss boundary... later zeros never raise them"). No new
v3 pathology (no over-eager parking, no never-pricing). Verdict: v3 amendment
CONFIRMED, stays. Opus-arm model-generality test still owed (cap-blocked). Still gated: provisional-belief sunset (evidence now three
worlds — w11, w18, w50-control — strong candidate), signup-dose cap,
progressive exploration tax (test in an engineered easy world first).
(fresh worlds, patched vs pre-patch gradient, prediction registered: patched
arm ends demo-ON in most worlds and lands closer to oracle $/buy) is designed
but NOT yet run.

## 2026-07-20 update — Tier B first run + audit + fix 1

Tier B (LLM-judged market; Codex merge, PR #1) ran its first full experiment:
10 fresh worlds (60-69), 8 iterations, agent gpt-5.6-terra, judge
gpt-5.6-luna, default schedule. Results: realized loop $15,138 vs frozen
baseline $14,645 (+3.4%, 5W/5L); held-out expected ROAS 0.86->1.03 (+20%,
7/10 improved); 4 worlds ran profitably (ROAS>1, a first). Run data:
sim/runs/tier-b/ (gitignored) + backup sim/audit/tier-b-keep/. Dashboard
artifact: https://claude.ai/code/artifact/5d623b6d-fa9c-41f3-96b9-f0536c6c8538
Qualitative audit (two auditors, all 10 worlds, ground-truth checks):
sim/audit/tier-b-qualitative-audit-2026-07-20.md. Five findings: (1)
RULE-FIXATION dominant — 50-88% of iterations were floor/ledger bookkeeping,
not advertising (w64: zero creative decisions in 8 iters, policy regressed);
(2) ads never rewritten (copy frozen from it1 in 5 worlds); (3) story angle
never tested in any world (coverage applied at cell granularity, not the
declared angle vocabulary — w3 lesson recurring one level up); (4) w61's
+$812 was a false discovery (lucky cell, flat held-out); (5) default
schedule churns good incumbents (mean rung 4.06, keep 2x in 80 draws).
FIX 1 ADOPTED (operator-approved): "Verdicts, not design (the Tier B
world-64 lesson)" added to semantic-gradient.md — floors/ledgers govern
verdicts never batch design; every batch maximizes the goal under current
beliefs; a bookkeeping-only thesis is not adoptable. Pre-fix doc archived:
sim/audit/semantic-gradient-v3-merged-2026-07-20.md. FIX 2 ALSO ADOPTED
(operator-approved, same day): "Menu before grid (the Tier B story lesson)"
— declared-vocabulary values (angle families, formats) each get one funded
cheapest-viable test before cell-level coverage may consume the experiment
slot; a floor on breadth, not exhaustiveness. FIX 3 ALSO ADOPTED
(operator-approved, same day): "Replication before doctrine (the world-61
lesson; restores the world-2 rule)" — a single result sets a LEAN never
DOCTRINE; no config absorbs a major budget share until it produces in TWO
independent batches at comparable per-dollar rates; binds the budget share,
not the wording. WORLD-70 SMOKE TEST (fixes 1-3 active, fresh world,
gpt-5.6): loop $1,798 vs frozen $1,508 (+19%), held-out 1.16->1.48 (+28%),
aggregate ROAS 1.12 (profitable). Fix 1 PASS (0/8 bookkeeping theses, was
50-88%); fix 3 PASS (lean/replication vocabulary throughout, no single-batch
doctrine, doses 3-4 only); fix 2 BLOCKED for a good reason — the agent
refused to author a story ad because the landing page supplies no verified
customer facts (fabricating a testimonial violates the honesty
non-negotiable; final policy states this explicitly). Root cause = fixture
gap, not rule failure; pending remedy: add a verified-testimonial section to
sim/formflow-landing-page.md so story is honestly authorable. FIX 4 ADOPTED
after the smoke test (operator-approved): "Creative is policy (the Tier B
frozen-copy lesson)" — ad copy is rewritable policy surface; creative leans
expressed as revised/new executions run champion/challenger (champion
unchanged, accumulating ledger + replication eligibility; variants alongside)
so creative iteration never destroys evidence accumulation. Copy had stayed
byte-identical across all 11 Tier B worlds including w70. LANDING-PAGE
PATCH DONE (same day): sim/formflow-landing-page.md now has a "What
customers say" section (Maya Reyes / Daniel Okafor / Priya Shah) so story
ads are honestly authorable from verified in-world facts — unblocks fix 2's
story sweep. NOTE: page text feeds judge prompts, so runs after this patch
re-judge against the new page (cache keys change; cross-comparisons with
worlds 60-70 carry that difference; paired arms within a new run are clean).
STILL PROPOSED, not adopted:
(b) the default-vs-conservative schedule comparison run
(pre-built in tier_b_sweep.py --schedules) — which now doubles as the
10-world validation of fixes 1-4. Also fixed in code: llm_client.py uses
max_completion_tokens (GPT-5.6), no temperature; tier_b_experiment.py has
repair-retry on validation failures; tier_b_sweep.py survives world
failures. GPT-5.6 API model ids: gpt-5.6-terra, gpt-5.6-luna (keys via
TIER_B_JUDGE_API_KEY / TIER_B_AGENT_API_KEY in the operator's terminal, not
in this repo; SSL needs SSL_CERT_FILE=certifi bundle on this Mac).

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
