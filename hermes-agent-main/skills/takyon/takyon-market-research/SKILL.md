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
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_write_file]
    routing:
      owns: customer, competitor, channel, pricing, and demand evidence
      when_to_use:
        - evidence is weak or stale, especially on `/create`
        - ICP, channel, offer, or pricing decisions need fresh truth
      do_not_use_for:
        - cosmetic copy changes that do not change the business decision
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

## Overview

Use this skill to reduce uncertainty about who the business should serve, what they urgently want, how they talk, who else is serving them, where they concentrate, and what evidence supports a pricing or channel move.

## When to Use

- Use on `/create` before product work when the business has weak or stale evidence.
- Use on `/wake` when replies, usage, pricing pressure, or conversion results contradict the current strategy.
- Use before major ICP, channel, offer, or pricing changes.
- Do not use for cosmetic copy changes that do not change the business decision.

## Quick Reference

- Primary root: `research/`
- Publication paths: `research/market.md`, `research/sources.jsonl`, `metrics/research-summary.json`
- Best call points: `/create`, pre-product pivots, contradiction-heavy `/wake` turns
- Tool names used by this skill: `business_read_business`, `business_read_file`, `business_list_files`, `business_write_file`, `business_patch_file`, `business_record_memory`

## Prerequisites

- The Takyon toolset must be available.
- Start from canonical business state, not from stray notes: use `business_read_business` first, then `business_read_file` for the specific research or metrics files you need.
- You MUST gather live evidence before writing any decision memo: run `web_search` for the decision at hand, then `web_extract` on the most relevant URLs the search returns. Do not write `research/market.md` from memory. Every URL you actually used must be logged in `research/sources.jsonl`. Still publish the durable result through Takyon business files.

## References

- `references/research-sources.md`

## Templates

- `templates/market.md`
- `templates/sources.jsonl`

## How to Run

- Call `business_read_business` first to load the current summary, research indexes, metrics summary, and unresolved inbound context.
- Use `business_read_file` for `research/strategy.md`, the existing `research/market.md`, and `metrics/summary.md` when they already exist.
- Use `business_list_files` if you need to confirm whether the canonical research files already exist.
- Update `research/market.md`, `research/sources.jsonl`, and `metrics/research-summary.json` with `business_write_file` or `business_patch_file`.
- If the research materially changes strategy, pricing, ICP, channel, or messaging, patch `research/strategy.md` so the working strategy matches the findings. Use `business_record_memory` only for extra durable notes that do not belong in `research/strategy.md`.

## Procedure

1. Call `business_read_business` for the current business and identify the exact decision this research must resolve: ICP, pain, pricing, channel, competitor, or offer.
2. If `research/strategy.md`, `research/market.md`, or `metrics/research-summary.json` already exist, load them with `business_read_file` and note what is stale, contradicted, or missing. If they do not exist, create a clean baseline instead of pretending there is prior evidence.
3. Gather only the missing evidence needed for the decision at hand. Keep raw source facts separate from interpretation while you work.
4. Write or patch `research/sources.jsonl` first so the source log reflects the evidence actually used. Expect one dated, source-backed entry per line.
5. Write or patch `research/market.md` with a concise decision memo that answers: who, pain, alternatives, channel, pricing signal, and the recommended next move.
6. Write or patch `metrics/research-summary.json` with a compact machine-readable rollup of the same conclusions so future wakes can compare deltas quickly.
7. If the research materially changes strategy, pricing, ICP, channel, or messaging, patch `research/strategy.md` so the working strategy matches the published findings. Use `business_record_memory` only for extra durable notes that do not belong in `research/strategy.md`.

## Output Format

- `research/market.md` should stay concise and decision-oriented: current question, evidence, interpretation, recommendation, and open gaps.
- `research/sources.jsonl` should log one source per line with URL or origin, date, and a short evidence note.
- `metrics/research-summary.json` should contain compact takeaways and deltas, not prose dumps.

## Publication

- Publish the decision memo to `research/market.md`.
- Publish the source log to `research/sources.jsonl`.
- Publish the structured rollup to `metrics/research-summary.json`.
- Keep all durable research output inside `research/` and `metrics/`.
- Do not claim any external publication or deployment state from research artifacts.

## Common Pitfalls

- Dumping generic market advice instead of decision-useful evidence
- Mixing raw evidence and interpretation until the difference disappears
- Letting research sprawl into product or distribution artifacts

## Verification Checklist

- [ ] `research/market.md` names the current decision, not just background notes
- [ ] `research/sources.jsonl` contains dated, source-backed entries that match the memo
- [ ] `metrics/research-summary.json` matches the actual written synthesis
- [ ] Any strategic change implied by the memo is either recorded with `business_record_memory` or explicitly left unchanged

## Rules

1. Keep work business-scoped.
2. Prefer dated, source-backed evidence over generic advice.
3. Label hypotheses as hypotheses.
4. Do not invent customer demand, competitor traction, or pricing evidence.
5. Never assert any web limitation you did not get from a real `web_search`/`web_extract` tool error. Do not claim the web is "limited to publicly cached content" (that exact phrasing is banned) or otherwise confabulate a web restriction to justify skipping research. If a tool genuinely errors, quote the real error and degrade as described in Troubleshooting; otherwise you must actually search and extract.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Web access seems unavailable | Attempt `web_search`/`web_extract` for real first. Only after a call returns an actual error envelope (budget/SpendBlocked, no-provider configured, or a provider error) may you record that exact blocker and degrade to local synthesis. In that case the memo MUST label its claims as domain-knowledge hypotheses, not sourced evidence. |
| Evidence is thin or contradictory | Preserve the contradiction and name the exact gap |
