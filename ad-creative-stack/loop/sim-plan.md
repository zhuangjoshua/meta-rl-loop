# Sim environment plan — simulated users for fast RL iteration

GOAL: replace my hand-fabricated batch results with a real simulated market —
a fixed population of simulated users who react to the ACTUAL ad text and
settings — so loop iterations are fast, reproducible, and not authored by the
same brain that runs the gradient.

## Why this is the missing piece

Today the evidence is written by me, which means the gradient is learning from
a world I invent — the loop's mechanics get tested, but its judgment doesn't.
A sim market makes outcomes a function of (ad text, settings) → (hidden user
preferences), computed by something OTHER than the gradient. The gradient then
has to genuinely discover what the population wants.

## Components (four, small)

1. **Subpopulation bank** — `sim/subpops.json` (+ `sim/subpops-hidden.json`,
   sealed). Each sim user represents a whole subpopulation with a weight, and
   subpopulations are GENERAL decision archetypes, not niche demographics —
   reusable across any business (peer-proof deciders, skeptical verifiers,
   herd followers, bargain hunters, novelty seekers, authority deferrers,
   committee buyers, indifferent scrollers...). Each is a point on ~6
   universal hidden dimensions: proof style that moves them, demo-gating,
   price sensitivity, exposures needed before acting, ad/page mismatch
   tolerance, baseline responsiveness. Product/niche flavor stays OUT of the
   personas — the ads and landing page carry it. World-seeds vary only the
   weights and trait intensities, so worlds are comparable and the bank never
   needs rebuilding per business. VISIBILITY RULE: the gradient sees only what
   a real advertiser could see — platform audience names and rough size
   estimates, plus noisy receipts. Archetypes and sub-segments are entirely
   truth-side constructs: their existence, weights, dimension values, deltas,
   and reached-by tags are ALL sealed. Every level of the hierarchy has its
   own hidden logic the loop must reconstruct from receipts alone; at reveal,
   each thesis is graded discovered / missed / hallucinated against the sealed
   file.
   Each archetype lists 2–4 CONSTITUENT SUB-SEGMENTS (weights splitting the
   parent's): each sub-segment inherits the parent's hidden dimensions with
   small stored deltas, plus REACHED-BY tags (which audiences/placements can
   expose it). Judging stays at archetype level (30 judgments/era); sub-segment
   deltas are applied arithmetically; the exposure model maps each campaign's
   audience settings onto sub-segment reachability — which is what makes
   settings-level theses (broad vs interest, placement choices) discoverable
   truths instead of authored outcomes.

2. **Exposure model** — who sees which ad. A simple stand-in for Meta's
   optimizer: each campaign type samples personas biased by its optimization
   goal (the Clicks campaign finds clicky personas, the Sales campaign finds
   buyers — imperfectly, with noise). Budget mechanism quirks included: the
   Meta-allocated twin concentrates budget unevenly, reproducing the anomaly
   class we've been parking.

3. **Response model** — the heart. For each (subpopulation, ad) pair — NOT
   per individual — the model outputs RATE DISTRIBUTIONS: mean and variance
   for each funnel step (click rate, page-load rate, sign-up rate, demo rate,
   purchase rate). One judgment covers a whole segment: 10 subpops x 3
   creatives = 30 judgments per era instead of 60 users x 21 ads = 1,260.
   The market runner then converts distributions to receipts in two steps:
   expected counts = population weight x exposures x rate; then SAMPLE the
   realized counts from the distribution (Beta-Binomial: mean+variance set the
   Beta, exposures set the Binomial). Sampling is kept deliberately — the
   receipts must still carry finite-sample noise, or the loop's noise
   discipline (min-spend gates, maturity flags, thin-evidence sizing) never
   gets tested. Two fidelity tiers:
   - **Tier A (scripted, free, instant):** responses computed from persona
     traits vs ad FEATURES (proof style, named-story presence, claim type,
     demonstration vs testimony — extracted by simple tagging). Seeded,
     deterministic, thousands of runs/hour. Good for: loop mechanics, noise
     schedule tuning, thesis-procedure testing.
   - **Tier B (LLM-judged, paid, minutes):** an LLM reads the ACTUAL script +
     landing page text on behalf of a whole segment: "This subpopulation is
     solo consultants drowning in intake email [hidden traits attached]. For
     this ad, estimate the segment's click rate and sign-up rate — mean and
     spread." Judging distributions per segment instead of decisions per
     individual is what makes Tier B affordable: ~30 judgments per era,
     batchable into 2-3 prompts. The words themselves are judged, not
     hand-tagged features — the real test of a SEMANTIC gradient.

4. **Receipts adapter** — emits era blocks in the EXACT evidence.md format we
   already use (full prompts, settings, per-ad rows with maturity flags, era
   totals). The loop upstream doesn't change at all: gradient reads
   evidence.md, produces thesis + doses, noise schedule draws, policy
   materializes, next batch runs against the sim.

## What stays honest

- **Sealed truth:** persona traits hidden from the gradient; revealed only at
  post-run scoring ("did the loop converge on what the population actually
  wanted?").
- **Seeded worlds:** same seed → same personas → same responses (Tier A fully;
  Tier B via cached judgments). Different seeds → different truths, so the
  loop can't memorize.
- **Oracle scoring:** with subpopulation weights and true rate means known,
  the best possible ad/config is a closed-form sum (weight x rate x value) —
  exact regret per era, no Monte Carlo needed. Convergence = does the policy's
  chosen creative/config approach the analytic optimum, and how fast.
- **Limits stated:** a sim validates the LOOP (does it find what a world
  rewards?), not the MARKET (is this world like reality?). Tier B judgments
  are an LLM's taste, not human buyers. Directional only; real ads remain the
  final rung.

## Build steps

1. Subpopulation generator (LLM writes ~10 weighted segments from a spec;
   hidden traits sampled with a seed; saved sealed).                              ~1 session
2. Tier A market: exposure + scripted response + receipts adapter, wired to
   run one era end-to-end from a policy file.          ~1 session
3. Run 6–10 full wake cycles at Tier A; tune noise schedule and thesis
   procedure against oracle regret.                    fast, same day
4. Tier B upgrade: LLM-persona judging with response caching; rerun the same
   seeds; compare Tier A vs B divergence.              ~1 session + API cost
5. (optional) Plug into the existing rl_sim/rl_wake_rig harness family so the
   sim market becomes a driver there too.

## Decision needed from operator

- Tier B model calls: run through my session (no setup, I judge as personas in
  batches) vs. scripted API calls with your key (faster, reproducible caching).
- World count: how many seeds constitutes "the loop works" (suggest 5 worlds ×
  8 eras).
