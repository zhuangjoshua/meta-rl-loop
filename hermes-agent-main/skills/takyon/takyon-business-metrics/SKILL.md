---
name: takyon-business-metrics
description: Compute, summarize, and track canonical Takyon business metrics, wake history, and unresolved inbound state for one business.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, metrics, wake, business, conversations]
    related_skills: [takyon-market-research, takyon-distribution, takyon-build-product]
  takyon:
    scope: business
    allowed_roots: [metrics, research]
    output_root: metrics
    publication:
      - metrics/summary.md
      - metrics/summary.json
      - metrics/wake-history.md
required_environment_variables: []
required_credential_files: []
---

# Takyon Business Metrics

Use this skill to interpret deterministic business metrics, summarize what changed, and keep wake-to-wake continuity without turning metrics into strategy fiction.

## Quick Reference

- Primary root: `metrics/`
- Publication paths: `metrics/summary.md`, `metrics/summary.json`, `metrics/wake-history.md`
- Best call points: every `/wake`, first `/create`, decisions that depend on usage/revenue/replies
- Publication location: `metrics/summary.md`, `metrics/summary.json`, `metrics/wake-history.md`

## References

- `references/metrics-rules.md`

## Templates

- `templates/summary.md`

## When to Use

- Use on every `/wake`.
- Use on `/create` to establish the first baseline.
- Use whenever current users, revenue, usage, or unresolved replies materially matter to the next business move.

## Procedure

1. Start with `business_calculate_pulse`.
2. Compare the current metrics to the previous canonical summary and recent strategy.
3. Write the publication paths.
4. Surface unresolved inbound messages, replies, and evidence gaps clearly.
5. Append one concise wake note after the business decision is made.

## Output Format

- `metrics/summary.md` should be short enough to read every wake.
- `metrics/summary.json` should contain compact structured values and deltas.
- `metrics/wake-history.md` should store concise wake notes, not long diaries.

## Publication

- Publish the human summary to `metrics/summary.md`.
- Publish the structured snapshot to `metrics/summary.json`.
- Publish wake continuity notes to `metrics/wake-history.md`.
- Do not treat metrics summaries or wake history as evidence of an external side effect.

## Pitfalls

- Turning thin metrics into overconfident strategy claims
- Hiding unresolved replies inside aggregate numbers
- Writing wake history like a diary instead of a compact operating log

## Verification

- `metrics/summary.md` is readable in one quick pass
- `metrics/summary.json` and `metrics/summary.md` describe the same current state
- `metrics/wake-history.md` adds one concise note per wake without deleting history

## Rules

1. Metrics are evidence, not strategy by themselves.
2. Missing metrics are gaps, not invented values.
3. Keep unresolved inbound state visible.
4. Do not delete historical summaries or wake notes during ordinary operation.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Metrics are missing | Preserve the gap and name the missing source |
| Too much conversation noise | Use the conversation worker to compress it before deciding |
