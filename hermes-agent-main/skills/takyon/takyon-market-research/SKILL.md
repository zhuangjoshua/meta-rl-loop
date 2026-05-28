---
name: takyon-market-research
description: Gather customer, competitor, channel, pricing, and demand evidence for one Takyon business and write it into canonical research and metrics files.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, business, market-research, icp, pricing, demand]
    related_skills: [takyon-build-product, takyon-business-metrics, takyon-distribution]
  takyon:
    scope: business
    allowed_roots: [research, metrics]
    output_root: research
    publication:
      - research/market.md
      - research/sources.jsonl
      - metrics/research-summary.json
required_environment_variables: []
required_credential_files: []
---

# Takyon Market Research

Use this skill to reduce uncertainty about who the business should serve, what they urgently want, how they talk, who else is serving them, where they concentrate, and what evidence supports a pricing or channel move.

## Quick Reference

- Primary root: `research/`
- Publication paths: `research/market.md`, `research/sources.jsonl`, `metrics/research-summary.json`
- Best call points: `/create`, pre-product pivots, contradiction-heavy `/wake` turns
- Publication location: `research/market.md`, `research/sources.jsonl`, `metrics/research-summary.json`

## References

- `references/research-sources.md`

## Templates

- `templates/market.md`
- `templates/sources.jsonl`

## When to Use

- Use on `/create` before product work when the business has weak or stale evidence.
- Use on `/wake` when replies, usage, pricing pressure, or conversion results contradict the current strategy.
- Use before major ICP, channel, offer, or pricing changes.

## Procedure

1. Read current business state, especially the current strategy, recent metrics summary, and unresolved inbound messages if they matter.
2. Gather only the missing evidence needed for the decision at hand.
3. Separate raw evidence from interpretation.
4. Update the publication paths.
5. Record strategic changes only when the evidence materially changes the business direction.

## Output Format

- `research/market.md` should stay concise and decision-oriented.
- `research/sources.jsonl` should log one source per line with URL, date, and note.
- `metrics/research-summary.json` should contain compact machine-readable takeaways, not prose dumps.

## Publication

- Publish the decision memo to `research/market.md`.
- Publish the source log to `research/sources.jsonl`.
- Publish the structured rollup to `metrics/research-summary.json`.
- Keep all durable research output inside `research/` and `metrics/`.
- Do not claim any external publication or deployment state from research artifacts.

## Pitfalls

- Dumping generic market advice instead of decision-useful evidence
- Mixing raw evidence and interpretation until the difference disappears
- Letting research sprawl into product or distribution artifacts

## Verification

- `research/market.md` names the current decision, not just background notes
- `research/sources.jsonl` contains dated, source-backed entries
- `metrics/research-summary.json` matches the actual written synthesis

## Rules

1. Keep work business-scoped.
2. Prefer dated, source-backed evidence over generic advice.
3. Label hypotheses as hypotheses.
4. Do not invent customer demand, competitor traction, or pricing evidence.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Web access is unavailable | Record the blocker and do only local synthesis |
| Evidence is thin or contradictory | Preserve the contradiction and name the exact gap |
