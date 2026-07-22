# Tier-B hidden-persona experiments

Tier B is implemented as a real LLM-judged market. It does not fall back to Tier A,
feature-tag scoring, fabricated rates, or empty provider responses.

## Isolation boundary

The experiment has two LLM roles:

1. The **policy agent** receives only the current policy, platform-visible audience facts,
   the actual landing page, and prior aggregate receipts. It designs three complete ads
   and later produces one semantic thesis at six policy doses.
2. The **hidden judge** receives a sealed decision persona, the actual landing page, and
   the actual headline/message/visual/CTA. It returns funnel-rate distributions. It never
   sees the policy, outcome history, feature tags, or selected policy dose.

Each Codex call starts in a new empty temporary directory with project rules disabled and
a read-only sandbox. Learner prompts explicitly prohibit tools, local files, hidden-world
artifacts, judge state, caches, generators, seeds, and oracles. This is the experiment's
prompt-level boundary; it is not an OS-level security boundary. Cached hidden judgments
live under `sim/cache/` and are never included in learner prompts.

The market then applies the world's hidden audience reach and objective-delivery biases,
samples the LLM rate distributions through a sequential funnel, and emits only aggregate
ad/campaign receipts.

## Complete loop

For each iteration:

1. Design three complete ads and a portfolio whose budgets sum to the run budget.
   Each campaign explicitly lists which ad IDs are eligible to run in it.
2. Judge all 10 hidden personas × 3 ads. Each ad has an independent judge context;
   default persona chunking uses three LLM calls.
3. Sample impressions → clicks → loads → signups → demos/purchases.
4. Append full policy, ads, settings, and receipts to evidence.
5. Execute Semantic Gradient v2: one falsifiable organizing thesis and six independent,
   complete policy rewrites. Matched-pair, replicated-pattern, and design evidence may
   combine when they support the same mechanism; every dose maps consequences across
   creative, campaigns, judgment, and experimentation.
6. Draw from the seeded schedule before adoption; rung 0 keeps the incumbent.
7. Materialize the selected policy and repeat.

After the last iteration, the runner designs a held-out batch from the final policy and
scores both the initial and final designs on the judge's expected distributions. It also
replays the initial design as a frozen sampled baseline for the same number of iterations.

## Backends

Codex uses existing local Codex authentication:

```bash
python3.12 sim/tier_b_experiment.py 26 \
  --landing-page sim/formflow-landing-page.md \
  --schedule default \
  --judge-model PINNED_MODEL --agent-model PINNED_MODEL
```

An OpenAI-compatible judge and policy agent can be separated by endpoint and model:

```bash
export TIER_B_JUDGE_API_KEY=...
export TIER_B_AGENT_API_KEY=...
python3.12 sim/tier_b_experiment.py 26 \
  --landing-page sim/formflow-landing-page.md \
  --judge-provider openai --judge-model JUDGE_MODEL \
  --judge-base-url https://judge.example/v1 \
  --agent-provider openai --agent-model AGENT_MODEL \
  --agent-base-url https://agent.example/v1
```

The runner refuses missing full landing-page text, placeholder ads, malformed rates,
incomplete structured output, unsupported campaigns, or provider failure.

## Schedule experiments

`noise_schedule.py` exposes six presets and exact per-iteration rung probabilities:

```bash
python3.12 sim/noise_schedule.py --iterations 8
```

- `default`: the original `decay=.92`, `width=.18` schedule.
- `conservative`: cools quickly and samples a narrow neighborhood.
- `wide`: keeps uncertainty across several adjacent doses.
- `persistent`: keeps taking large steps later.
- `greedy-small`: selects roughly dose 1 on the first iteration, then mostly keeps
  or makes the smallest revision.
- `semantic-gated`: legacy class-scaled control retained for standalone schedule tests.
  Semantic Gradient v2 does not assign mutually exclusive thesis classes, so full loop
  runs use the ungated schedule.

For the existing eight-iteration design, the original `default` schedule is still
aggressive: its expected rung falls only from 5.16 to 2.93, and its iteration-8
probability of keeping the incumbent is 0.94%. `conservative` reaches expected rung
1.00 with a 22.4% keep probability by iteration 8. The primary comparison is
`default` vs `conservative`; `persistent`, `wide`, and `greedy-small` are useful
schedule controls.

Any run can override `--tau0`, `--decay`, `--floor`, and `--width`. The class-scale
flags remain available only for legacy schedule analysis. Draws are paired by
world, run seed, and iteration, so rerunning an identical schedule reproduces selections.

Run a paired schedule sweep with:

```bash
python3.12 sim/tier_b_sweep.py \
  --worlds 26,27,28,29,30 \
  --schedules default,conservative \
  --landing-page sim/formflow-landing-page.md \
  --judge-model PINNED_MODEL --agent-model PINNED_MODEL
```

Runs for one world are serialized to protect its cache; different worlds run in parallel.
The sweep reports loop-vs-frozen revenue wins, held-out expected-ROAS improvement, selected
dose, and LLM-call totals by schedule.

## Experiment size

With 10 personas, 3 ads, 8 iterations, and the default 10-pair judge batch:

- each ordinary iteration makes 1 design + 3 judge + 1 gradient = **5 LLM calls**;
- final held-out design and judgment add **4 calls**;
- one 8-iteration world is therefore **44 calls** and 270 persona/ad judgments;
- 5 worlds × 3 schedules is **660 calls** and at most 4,050 judgments;
- 20 worlds × 5 schedules is **4,400 calls** and at most 27,000 judgments.

Exact provider token usage is recorded when the endpoint reports it. Cached exact
persona/ad/page/model judgments are reused. Judge chunks never mix ads, so increasing
the batch size above the persona count does not reduce calls below one per ad.

The limiting resources are provider token/rate budgets and policy-agent context growth,
not simulator CPU. Eight iterations fit comfortably because evidence remains small; for
longer runs the current full-fidelity evidence prompt should be paired with a model whose
context window can hold every retained policy, ad, setting, and receipt.

## Hierarchical population-market v3

The harness is additive; the original Tier-B loop remains runnable. It replaces one rate
judgment per persona/ad with ten parent-population semantic evaluations. Each parent call
sees its inherited child situations, deterministic delivery, the full ad timeline and the
page. Executable persistent-person choice code produces the ordered funnel. Only aggregate
receipts are public.

Run the full-information three-ad policy oracle with lower-cost consumer models:

```bash
python3 sim/full_info_policy_v2.py 71 \
  --landing-page sim/formflow-landing-page.md \
  --designer-model gpt-5.6-sol \
  --judge-model gpt-5.6-luna \
  --population-model sim/population-model-v3.json \
  --human-variation-model sim/human-variation-v4.json \
  --choice-calibration sim/choice-calibration-v1.json \
  --subscription-economics sim/subscription-economics-v1.json \
  --generations 3 --candidate-count 4 --periods 8
```

One generation evaluates all candidate policies in exactly ten population calls, rather
than ten calls per candidate. Consumer reasons and child identities are written only to
the hidden audit artifact.

The v3 model makes role, workflow, current solution, need, authority, and market
eligibility primary. It includes explicit nonbuyers and treats impulsiveness or
skepticism as cross-cutting behavior rather than delivery segments. Its population
and targeting numbers are simulator assumptions and must not be described as measured
Meta distributions.

The judge returns qualitative product-person states and ad-perception signals, never
counts or probabilities. The executable choice model holds need, fit, authority, budget,
switching cost, implementation capacity and product experience fixed outside the ad.
Ads affect perception and expectation matching. Persistent delivery pools deduplicate
purchases across ads and periods. No conversion ceiling or fixed ad-lift bound is applied;
all numerical utility assumptions are versioned in `choice-calibration-v1.json` and copied
into the hidden audit.

Subscription reporting separates first-payment ROAS from realized/modelled cohort
revenue. The bundled economics file provides explicit churn scenarios only; replace
them with the product's observed paid-cohort retention before treating month-6,
month-12, or lifetime ROAS as factual. Trials are not revenue, and the old no-churn
six-month multiplier is no longer emitted.

For a readable operating scale, use contribution CAC payback rather than raw
first-payment ROAS. The bundled illustrative grades are: at most 3 months
`exceptional`, 6 `strong`, 12 `healthy`, 18 `marginal`, and longer `weak`. Both
the margin assumption and grade boundaries are configurable business policy, not
external benchmarks.

## Adaptive product-relevant populations

`sim/population_generator.py` now generates the hidden hierarchy from a public business
specification before any ad exists, audits it for buyer-only bias and invented product facts,
validates hard coverage and eligibility invariants, and freezes the result for the experiment.
Generic human attention variation remains separate in `sim/human-variation-core-v1.json`.
Every generated child has a frozen statistical mixture of decision strata; the ad judge cannot
choose or improve need, fit, authority, budget, switching cost, implementation capacity, or likely
product experience. Multi-world generation varies that hidden market across seeds while holding
the business and public platform fixed.

Generate the bundled Formflow example with:

```bash
python3 sim/population_generator.py \
  --business-spec sim/business-spec-formflow.json \
  --platform sim/world-71/platform.json \
  --seed 1 --run-name formflow-seed-1
```

Add `--world-count 5 --world-id-start 101` to create an indexed set of five independently
generated and audited hidden worlds.

The complete input contract, run-log artifact index, validation rules, and downstream harness
commands are in [ADAPTIVE-POPULATIONS.md](ADAPTIVE-POPULATIONS.md).
