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
