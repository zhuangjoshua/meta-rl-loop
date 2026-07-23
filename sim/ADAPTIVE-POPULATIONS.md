# Adaptive hidden populations

`population_generator.py` turns a public business specification into a product-relevant,
hidden market. It does not receive an ad. It runs once for a seed, audits the result, validates
hard invariants, and freezes one population JSON for every subsequent policy evaluation.

The generator separates three things:

1. `human-variation-core-v1.json` contains product-independent attention and interpretation
   differences such as two-second scanning, anti-AI-copy sensitivity, price-first reading, and
   mechanism inspection.
2. A business specification contains only product truth, offer truth, market context, and claims
   the simulator must not infer.
3. The generated population contains product-relevant jobs, current alternatives, need, fit,
   authority, timing, evidence requirements, objections, and positive matches. Irrelevant human
   preferences are deliberately absent.

Each generated child also contains two to four `decision_strata`. These are a finite statistical
mixture inside the subpopulation: every stratum has a share and one coherent state for current
need, product fit, authority, budget, switching cost, implementation capacity, and expected
product experience. The states are frozen before ads exist. The ad judge cannot rewrite them.

The ten parent relationships are fixed structural coverage, not fixed personas:
`out_of_market`, `adjacent_without_fit`, `low_frequency_or_urgency`, `core_direct_buyer`,
`secondary_direct_buyer`, `incumbent_solution_user`, `authority_blocked_champion`,
`budget_or_timing_constrained`, `procurement_or_multi_party`, and
`category_relevant_other`. Their children are generated for the supplied business.

## 1. Write the business specification

Copy `business-spec-formflow.json` and replace every product-specific value. Required fields are:

```json
{
  "business_name": "Product name",
  "category": "Specific product category and intended setting",
  "product_summary": "Literal product description",
  "product_facts": {
    "capabilities": ["Only stated capabilities"],
    "limitations": ["Missing, excluded, or unstated capabilities"],
    "activation_event": "Observable activation event",
    "purchase_event": "Observable revenue event"
  },
  "offer": {"pricing": "Exact price and cadence"},
  "market_context": ["Relevant cold-traffic and alternative context"],
  "excluded_claims": ["Claims the model must not infer"]
}
```

Do not describe the desired winning ad or ideal audience. That would leak the optimization target
into the hidden market. Capabilities and limitations are treated as exhaustive product truth.

## 2. Generate and freeze a market

From the repository root, using existing local Codex authentication:

```bash
python3 sim/population_generator.py \
  --business-spec sim/business-spec-formflow.json \
  --platform sim/world-71/platform.json \
  --seed 1 \
  --model gpt-5.6-sol \
  --run-name formflow-seed-1
```

The output is `sim/generated-populations/formflow-seed-1/`. Reusing the same final
`population-model.json` freezes the market. Regenerating with another seed creates a different
synthetic market; it is not another replay of the same world.

The seed is a label, not a sampling seed. It enters the architect and auditor prompts as text and
namespaces the response cache; it is never passed to the model as an RNG seed. Rerunning with the
same seed reproduces a world only because the cached model responses replay. With a cleared or
absent cache, the same seed can legitimately produce a different world. The unit of
reproducibility is therefore the frozen `population-model.json` and its archived run directory,
not the seed number.

To generate a changing set of hidden worlds from the same business:

```bash
python3 sim/population_generator.py \
  --business-spec sim/business-spec-formflow.json \
  --platform sim/world-71/platform.json \
  --seed 1000 \
  --world-count 5 \
  --world-id-start 101 \
  --run-name formflow-worlds-101-105
```

This writes `world-101/` through `world-105/` under one run directory. Each world uses a distinct
seed and independently generated-and-audited population, so shares, children, alternatives,
blockers, relevant preferences, decision-strata mixtures, and targeting affinity can change while
the business and public platform stay fixed. `world-set-manifest.json` indexes the frozen model for
every world; `world-set-events.jsonl` records partial progress and the exact world that failed.

An OpenAI-compatible endpoint can be used instead:

```bash
export TIER_B_AGENT_API_KEY=...
python3 sim/population_generator.py \
  --business-spec sim/business-spec-formflow.json \
  --platform sim/world-71/platform.json \
  --seed 1 \
  --provider openai \
  --model MODEL \
  --auditor-model AUDITOR_MODEL \
  --base-url https://example.test/v1
```

Generation fails instead of emitting a partial market when relationship coverage, identifiers,
shares, delivery keys, child detail, or eligibility invariants are invalid.

## 3. Inspect the generation log

Every run writes enough state to reproduce and diagnose the market:

| Artifact | Meaning |
|---|---|
| `manifest.json` | Seed, fingerprints, model identities, call statistics, audit result, validation summary, and artifact index |
| `events.jsonl` | Timestamped run, architect, auditor, and completion events |
| `business-spec.snapshot.json` | Exact business input used for this world |
| `platform.snapshot.json` | Exact public delivery input used for this world |
| `architect-prompt.txt` | Full first-pass prompt; verify that no candidate ad appears |
| `draft-generation.json` | Unmodified architect output |
| `draft-population.normalized.json` | Draft after deterministic share normalization |
| `draft-validation.json` | Draft coverage and eligibility report |
| `auditor-prompt.txt` | Full audit prompt and the normalized draft it reviewed |
| `audit.json` | Full audit issues and complete revised generation |
| `audit-summary.json` | Compact audit verdict and repairs |
| `normalization.json` | Reported and normalized parent/child share totals |
| `validation.json` | Final relationship coverage, eligibility mix, counts, and business fingerprint |
| `population-model.json` | Frozen hidden market consumed by the simulator |

An invalid architect draft is still sent to the auditor for repair. Its exact failure appears in
`draft-validation.json` and as `draft_validation_failed` in `events.jsonl`. Provider failures and
invalid final audits append `run_failed` with the failing stage and error before the command exits.

The run directory and model caches are ignored by Git because they may contain hidden worlds. To
archive an experiment, preserve the whole run directory outside the learner-visible workspace.

## 4. Run an experiment against the frozen market

Blind versus full-information one-shot comparison:

```bash
python3 sim/blind_vs_full_info.py 71 \
  --landing-page sim/formflow-landing-page.md \
  --population-model sim/generated-populations/formflow-seed-1/population-model.json \
  --human-variation-model sim/human-variation-core-v1.json \
  --choice-calibration sim/choice-calibration-v1.json \
  --subscription-economics sim/subscription-economics-v1.json \
  --designer-model gpt-5.6-sol \
  --judge-model gpt-5.6-luna
```

Full-information multi-generation search:

```bash
python3 sim/full_info_policy_v2.py 71 \
  --landing-page sim/formflow-landing-page.md \
  --population-model sim/generated-populations/formflow-seed-1/population-model.json \
  --human-variation-model sim/human-variation-core-v1.json \
  --choice-calibration sim/choice-calibration-v1.json \
  --subscription-economics sim/subscription-economics-v1.json \
  --designer-model gpt-5.6-sol \
  --judge-model gpt-5.6-luna \
  --generations 3 --candidate-count 4 --periods 8
```

For another business, the business specification, landing page, price/offer world, and public
platform note must agree. The generator makes the population reusable across products; it does
not make a Formflow-priced world valid for a differently priced product.

## Invariants and interpretation

- Ads are absent during population generation. The architect and auditor receive only the
  business, platform delivery controls, and seed.
- The generated product-relevant preferences can be positive, negative, conditional, or neutral.
  The audit rejects markets that are mostly descriptions of likely buyers.
- Advertising may change attention, comprehension, credibility, perceived relevance,
  expectations, and trial motivation. It cannot create need, fit, authority, budget, timing,
  implementation capacity, or an absent product capability.
- Out-of-market children cannot purchase directly. Authority-blocked champions cannot be direct
  buyers. The core-buyer population must contain at least one direct buyer.
- Population shares and delivery affinities are explicit synthetic assumptions, not measured Meta
  data. A seed makes them reproducible, not true.
- Product-independent human variation is crossed with every generated child, so a qualified buyer
  can still hate AI-sounding copy or abandon a long ad while a nonbuyer remains a nonbuyer.
- Product fit is not guessed again for every ad. The executable market integrates over each
  child's frozen decision-strata shares. The AI judges only qualitative ad perception; general
  human microprofile shares turn helped, neutral, and rejected reactions into an additional
  statistical mixture.
- Expected stage counts are calculated from persistent pool size, reach, decision-stratum share,
  human-microprofile share, and versioned logistic transition probabilities. Sampled replays then
  draw noisy aggregate receipts from those expectations.
