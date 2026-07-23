# Simulator corrections

## Adaptive hidden population generation

- Added `sim/population_generator.py`, which generates a product-relevant market once from a
  business specification and public delivery controls, never from candidate ads.
- Added an independent architect/auditor pass, strict ten-relationship coverage, share
  normalization, eligibility invariants, business fingerprints, and fail-closed validation using
  the same loader as the executable market.
- Added full run provenance: exact input snapshots, full prompts, raw and normalized drafts,
  audit output, normalization report, validation report, event log, model identities and call
  statistics.
- Added `sim/business-spec-formflow.json` as the input example and
  `sim/human-variation-core-v1.json` as product-independent cross-cutting human behavior.
- Made role-specific factual preferences optional because the generated child situations now own
  product-relevant priorities, evidence, objections, alternatives, and positive matches.
- Added focused generator tests and `sim/ADAPTIVE-POPULATIONS.md` with generation, inspection,
  replay, and cross-product usage instructions.
- Added two-to-four frozen decision strata per child so product need, fit, authority, budget,
  switching cost, implementation capacity, and expected experience are statistical mixtures
  derived from the subpopulation rather than one product-state guess from the ad judge.
- Added multi-world generation with independent seeds, per-world audits, a world-set manifest, and
  an append-only world-set event log. Different worlds vary hidden composition while the business
  and public platform remain fixed.

Status: the hierarchical population-market v2 and its full-information policy
oracle are implemented alongside the original Tier-B harness. The older sections
below remain the audit backlog for features not yet migrated.

## Implemented population-market v2

- Added `sim/population-model-v2.json`: ten parent populations with frozen
  world-71 shares and 41 inherited child situations. Children are coherent buyer
  contexts, not arbitrary personality-score vectors.
- Added a universal human-attention constitution and an executable monotone funnel:
  exposed -> noticed -> stopped scrolling -> meaningful view -> click -> landing
  visit -> card-required trial -> activation -> purchase.
- Ads now require a continuous second-by-second timeline. The judge may credit only
  content plausibly seen before abandonment, preventing long-copy information cheats.
- Meta delivery deterministically allocates exact child/ad exposures from parent
  share, child share, frozen reach, objective affinity, CPM, campaign budget, and
  ad allocation before AI judgment.
- Exactly ten hidden AI evaluations score a policy batch: one per parent, containing
  all inherited children and their exact exposures. Counts are fail-closed unless
  complete, exposure-preserving, and funnel-monotone.
- Learner receipts contain aggregate ad and funnel counts only. Population ids,
  child ids, explanations, blockers, and latent delivery rows remain hidden.
- Added `sim/full_info_policy_v2.py`: a two-generation full-information search over
  complete three-ad policies with real timeline creatives, delivery, and 10%-70%
  portfolio allocation constraints. It uses the hidden audit only for oracle
  refinement and reports first-payment ROAS separately from a mechanical six-month
  gross-revenue multiple.
- Added invariant tests in `sim/test_population_market_v2.py` for hierarchy shares,
  mandatory timelines, exactly ten evaluations, hidden receipts, and monotone counts.

## Implemented market-role population v3

- Replaced impulse, skeptic, and similar response styles as population roots. The ten
  v3 parents are mutually exclusive role/workflow/authority market states; behavioral
  styles exist only inside their concrete situations.
- Added an explicit 71% general-adult out-of-market prior plus adjacent businesses,
  low-volume providers, qualified consultants, agencies, practices, incumbents,
  unauthorized champions, and procurement-controlled teams.
- Marked structurally ineligible children with `purchase_eligibility: none`; the
  harness enforces exactly zero purchases regardless of copy quality.
- Made audience presence and objective affinity declared delivery assumptions. For
  the frozen v3 sales-broad cell, 51% of impressions remain explicit nonbuyers; a
  sales objective enriches qualified roles without turning broad traffic into them.
- Replaced LLM-generated counts and aggregate conditional-rate ceilings with the
  persistent random-utility choice model described below.
- Normalized harmless judge exposure/id transcription mistakes back to the
  deterministic delivery record without altering substantive behavioral counts.

## Implemented cross-cutting human variation v4

- Added twelve explicit microprofiles inside every role child. They cover silent
  two-second scanning, short-ad intolerance, anti-AI-copy sensitivity, mechanism
  inspection, price-first evaluation, peer-story preference, testimonial distrust,
  mobile visual limits, detailed research, interruption, technical evaluation, and
  passive viewing.
- Every child's exposure is partitioned by the same declared microprofile shares.
  A profile inherits the child's actual role, need and authority; it cannot become
  a replacement market segment or manufacture eligibility.
- Added role-specific factual preferences and hard product questions for all ten
  populations. These distinguish, for example, agency seat limits, bookkeeping
  security requirements, incumbent migration cost, consultant integrations, and
  unauthorized internal advocacy.

## Implemented subscription economics v1

- Removed the misleading assumption that every acquired payer contributes exactly
  six payments.
- Kept first-payment ROAS as a directly defined cash metric and added month-1,
  month-3, month-6, month-12, and modeled-lifetime cohort-revenue ROAS.
- Subscription ROAS is now generated from explicit monthly paid-subscriber churn
  scenarios. The bundled 20%, 10%, and 5% scenarios are labeled assumptions, not
  Formflow data or industry benchmarks.
- Refunds, failed payments, discounts, expansion, contribution margin, and
  reactivation remain excluded until measured inputs are supplied.
- Added a configurable operating anchor: month-6 cohort revenue ROAS, month-6
  contribution ROAS, and contribution CAC-payback month. The bundled 85% margin
  and payback grades are explicitly illustrative internal assumptions.

## Implemented persistent-person choice model v1

- The population judge no longer returns funnel counts, probabilities, or numeric
  propensities. It returns qualitative fixed product-person states and ad-perception
  signals; deterministic code produces the funnel.
- Need, product fit, authority, budget, switching cost, implementation capacity and
  product experience are assessed separately from every ad and cannot be improved by
  copy. Ads affect attention, comprehension, relevance, credibility, trial motivation
  and expectation matching.
- Paid conversion depends on both fixed product experience and ad-created expectations.
  Structurally ineligible people remain unable to purchase.
- Delivery uses explicit audience sizes to create persistent parent/child pools.
  Repeated impressions revisit those pools, frequency and fatigue accumulate, and
  purchase hazards are deduplicated across the policy's ads so one person buys at most
  once.
- Removed all active conversion-rate ceilings and ad-lift bounds. The versioned
  `sim/choice-calibration-v1.json` contains explicit cold-traffic utility assumptions,
  not maxima; logistic random utility and audience occupancy produce rates.
- Hidden audits now contain product assessments, ad assessments, persistence groups,
  exact calibration inputs, unique reach and mean frequency. Learner receipts still
  contain aggregates only.

## 1. Replace the degenerate fixed-action oracle

RESOLVED: `sim/tier_b_full_info.py` (the legacy oracle with the hard-coded
`$199.98 / $0.01 / $0.01` allocation) is deleted. `sim/full_info_policy_v2.py`
is the full-information policy benchmark; it produces complete three-ad
policies on the adaptive market. Still open from this section:

- Evaluate policies across multiple worlds and multiple periods, not one
  stationary world and one cached judge surface.
- Report a single-cell diagnostic ceiling separately if one is ever needed;
  no such oracle exists any longer.

## 2. Prevent single-cell collapse

- Add frequency, fatigue, audience saturation, and diminishing marginal reach.
- Make CPM respond to objective, competition, audience saturation, and creative
  quality instead of remaining a fixed audience constant.
- Replace equal per-ad budget splitting with a delivery optimizer that learns
  from predicted outcomes and retains explicit exploration.
- Replace random `auto` audience allocation with an optimizer whose state and
  uncertainty are recorded.
- Add configurable portfolio constraints for benchmark runs: minimum readable
  spend, maximum share per creative, and champion/challenger roles.

## 3. Make buyer policies executable rather than suggestive prose

- Keep buyer policies completely opaque to the learner. Opacity is an
  information boundary, not permission to leave behavior underspecified.
- Replace each single archetype with a hierarchical population:
  `world -> persona family -> strict subpopulation -> sampled individuals`.
- Give every persona family multiple substantively different subpopulations.
  For example, `skeptic` must distinguish at least: urgent/high-fit buyers who
  need mechanism proof; high-need buyers blocked by security or credibility;
  researchers without purchase authority; low-need comparison shoppers; and
  price-qualified buyers blocked by switching cost.
- Parameterize each subpopulation with distributions for:
  - problem severity and current-solution pain;
  - product/category fit and required capabilities;
  - awareness, channel intent, urgency, and time horizon;
  - budget, willingness to pay, price sensitivity, and expected customer value;
  - decision authority, procurement burden, and switching cost;
  - prior trust, brand familiarity, claim skepticism, and evidence thresholds;
  - tolerance for card requirements, setup effort, risk, and delayed value;
  - attention, proof preferences, demonstration requirements, and CTA friction;
  - exposure history, learning, fatigue, and retargeting state.
- Give every individual deterministic purchase gates. A purchase is impossible
  unless need, product fit, affordability, authority, offer tolerance, and
  evidence sufficiency all clear their respective thresholds. An ad can change
  beliefs or reveal fit; it cannot manufacture need, budget, authority, or a
  missing product capability.
- Define sequential latent state transitions:
  `unaware -> aware -> interested -> evaluating -> qualified -> activated ->
  purchased -> retained/churned`. Ads and landing pages update bounded beliefs;
  they do not directly assign a purchase probability.
- Use an explicit utility model at every transition. The purchase utility must
  combine expected value, product fit, credible evidence, urgency, price,
  switching cost, perceived risk, procurement friction, and accumulated
  exposure. Convert utility to probability only after hard gates are applied.
- Use the LLM judge only to score semantic features of the actual ad: mechanism
  visibility, proof type, claim credibility, visual clarity, offer clarity,
  persona relevance, and ad-to-page consistency.
- Require the semantic scorer to return a bounded feature vector rather than
  funnel rates. Candidate features include pain relevance, demonstrated
  capability, evidence credibility, claim specificity, quantified support,
  identity fit, price clarity, risk reversal, setup clarity, and CTA friction.
- Convert those feature scores to funnel probabilities with a deterministic,
  versioned numerical model calibrated from explicit base rates.
- Apply `proof_pref`, `demo_gate`, `trial_gate`, `base`,
  `mismatch_bounce`, `clickiness`, and `buyiness` mathematically. They must not
  merely appear as text for the judge to interpret.
- Rename every latent field to state its direction. In particular,
  `demo_gate` is the no-visible-demo purchase multiplier; a smaller value means
  a stronger need for demonstration.
- Give each persona explicit acceptance, rejection, and price/commitment rules.
- Seed worlds from parameter distributions, then freeze every sampled
  subpopulation and individual for reproducibility. The learner receives only
  aggregate receipts; reveal tools may inspect the frozen parameters after the
  experiment.
- Add invariant tests showing that visible demonstrations, mismatched claims,
  card-required trials, and persona proof preferences change rates in the
  declared direction.
- Add impossibility tests: strong copy must not convert people with no need, no
  fit, insufficient willingness to pay, no authority, or a violated hard
  requirement.

## 4. Repair the funnel and revenue model

- Replace independent post-signup demo and purchase branches with a declared
  causal funnel, such as signup -> activation/demo -> purchase, plus any
  explicitly modeled direct-purchase path.
- Parameterize price, first payment, average order value, renewal horizon,
  churn, refunds, and gross margin.
- Report first-payment ROAS, contribution ROAS, and LTV ROAS separately.
- Calibrate absolute click, signup, activation, and purchase rates against a
  declared reference dataset instead of allowing the judge model to invent
  their scale from prompt ranges.

## 5. Measure judge reliability

- Maintain a fixed calibration suite of ads with expected directional and
  absolute responses.
- Rejudge semantic near-duplicates and exact controls under fresh calls; report
  between-call and between-batch variance.
- Use an ensemble or deterministic semantic scorer when one model's stylistic
  preference materially changes the winner.
- Fail calibration when judge explanations attribute claims, prices,
  testimonials, or mechanisms that are absent from the ad and landing page.

## 6. Make comparisons honest

- Distinguish `single_cell_oracle`, `full_info_policy`, `frozen_policy`, and
  `hidden_info_learning_policy` in schemas and reports.
- Use common random numbers that remain aligned after policies choose different
  campaign structures.
- Evaluate expected performance and sampled performance over the same horizon,
  including fatigue and market drift.
- Require complete paired worlds for aggregate comparisons; failed worlds must
  make the sweep fail unless partial aggregation is explicitly requested.
