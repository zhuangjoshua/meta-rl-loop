# Benchmarks + rules (single source for the evaluator)

Tunable **fallback heuristics**, not Meta-official figures — except the Learning-phase rule, which is
Meta-documented. Per-business CPA/ROAS targets from current business research override the table. This file owns the
thresholds, the verdict→action map, and the learning/fatigue/attribution rules used by
bound evaluation capability.

## Thresholds (heuristic)
| Metric | Good | Watch | Poor |
|---|---|---|---|
| CTR (link) | > 1.5% | 1.0–1.5% | < 1.0% |
| CPC | < $2 | $2–$3 | > $3 |
| CPM | < $15 | $15–$20 | > $20 |
| CPA / CPL | ≤ baseline | ≤ 2× baseline | > 2× baseline |
| ROAS | ≥ 2.0 (ecom ≥ 3) | 1.5–2.0 | < 1.5 |
| CVR | > 1.5% | 0.5–1.5% | < 0.5% |
| Frequency (7d) | 1.0–2.5 | 2.5–3.5 | > 3.5 |

**CPA baseline:** per-business CPA target if set; else `LTV × 0.3` when LTV is known; else `$40`.

## Learning phase (Meta-documented)
Ad set exits Learning after **~50 optimization-event conversions in 7 days**; below that it is
**Learning Limited**. Do not score an ad set still in Learning as "poor" on spend alone → verdict
`learning`, action `wait`.

## Fatigue (heuristic)
Frequency > 3.0 **and** CTR down > 15% week-over-week → fatigue.

## Attribution sanity (heuristic)
If Meta-reported conversions vs CRM/server differ > 20% (when join keys exist) → suspect attribution,
not campaign structure.

## Objective reference points (informational only — do NOT auto-adjust the table)
Lead Gen tends to higher CTR / lower CPL than Sales; Traffic has the lowest CPC; Advantage+ Shopping
tends lower CPA than manual. Context for human review, not threshold substitution.

## Verdict → recommended action (snake_case enum)
- `good` + frequency in range + past learning → **scale** (raise budget gradually).
- `bad` from CTR/CPC + past learning → **refresh_creative**.
- `bad` + > 40% of the parent campaign's ad sets Learning Limited → **consolidate**.
- `bad` from CPA/ROAS vs baseline + past learning + fatigued → **pause**.
- still in Learning → **wait**.
