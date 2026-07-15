---
title: "Takyon Business Metrics"
sidebar_label: "Takyon Business Metrics"
description: "Compute, summarize, and track canonical business metrics, wake history, and unresolved inbound state"
---

{/* This page is auto-generated from the skill's SKILL.md by website/scripts/generate-skill-docs.py. Edit the source SKILL.md, not this page. */}

# Takyon Business Metrics

Compute, summarize, and track canonical business metrics, wake history, and unresolved inbound state. Use when running every wake, establishing the first creation baseline, or when users, revenue, usage, or replies materially affect the next move. Do not use to invent strategy from missing measurements.

## Skill metadata

| | |
|---|---|
| Source | Bundled (installed by default) |
| Path | `skills/takyon/takyon-business-metrics` |

## Reference: full SKILL.md

:::info
The following is the complete skill definition that Takyon loads when this skill is triggered. This is what the agent sees as instructions when the skill is active.
:::

# Takyon Business Metrics

## Overview

Use this skill to interpret deterministic business metrics, summarize what changed, and keep wake-to-wake continuity without turning metrics into strategy fiction.

## When to Use

- Use on every `/wake`.
- Use on `/create` to establish the first baseline.
- Use whenever current users, revenue, usage, or unresolved replies materially matter to the next business move.
- Do not use this skill to invent strategy from missing metrics.

## Quick Reference

- Primary root: `metrics/`
- Publication paths: `metrics/summary.md`, `metrics/summary.json`, `metrics/wake-history.md`
- Best call points: every `/wake`, first `/create`, decisions that depend on usage/revenue/replies
- Publication location: `metrics/summary.md`, `metrics/summary.json`, `metrics/wake-history.md`
- Tool names used by this skill: `business_calculate_pulse`, `business_read_business`, `business_read_file`, `business_write_file`, `business_patch_file`, `business_read_app_analytics`
- Web analytics: `business_calculate_pulse` already carries a `web_analytics` block (visitors/pageviews/visits for this business's published site, filtered to its own subdomain). Call `business_read_app_analytics` for a focused or longer window. Both report a truthful not-configured/unavailable state instead of faking numbers — never invent traffic.

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_calculate_pulse`, then use `business_read_business` and `business_read_file` to compare the new pulse against the current canonical summaries.
- If conversation noise is too high to summarize cleanly, load `takyon-conversation-followup` before writing the final metrics summary.

## References

- `references/metrics-rules.md`

## Templates

- `templates/summary.md`

## How to Run

- Call `business_calculate_pulse` first. Treat that as the deterministic input snapshot for the current turn.
- Use `business_read_business` and `business_read_file` to inspect `metrics/summary.md`, `metrics/summary.json`, `metrics/wake-history.md`, and `research/strategy.md` before rewriting them.
- Use `takyon-conversation-followup` if unresolved inbound or conversation volume is too noisy to summarize directly.
- Use `business_write_file` or `business_patch_file` to update the canonical metrics files.

## Procedure

1. Call `business_calculate_pulse` and inspect the current snapshot: users, revenue, usage, jobs, controls, unresolved inbound, recent events, and the `web_analytics` block (published-site visitors/pageviews). For a longer or focused traffic window, call `business_read_app_analytics`; if either reports not-configured/unavailable, say so rather than implying traffic.
2. If `metrics/summary.md`, `metrics/summary.json`, or `metrics/wake-history.md` already exist, load them with `business_read_file` and compare the current pulse against the prior state. If they do not exist, create a first baseline instead of implying history that is not there.
3. Read `research/strategy.md` before finalizing the wake output. If the pulse or follow-up evidence changes the business thesis, ICP, offer, pricing, channel, or X angle, patch `research/strategy.md` first.
4. If unresolved inbound or conversation volume is too noisy to summarize confidently, load `takyon-conversation-followup` and use its published `metrics/conversations/followup.md` before finalizing the metrics narrative.
5. Write or patch `metrics/summary.md` with the short human summary: what changed, what matters, what is blocked, and what evidence gap remains.
6. Write or patch `metrics/summary.json` with the same state in compact structured form so future wakes can compare deltas quickly.
7. After the CEO chooses the next move, append one concise note to `metrics/wake-history.md` describing the wake context and chosen action. Do not turn it into a diary.

## Output Format

- `metrics/summary.md` should be short enough to read every wake.
- `metrics/summary.json` should contain compact structured values and deltas.
- `metrics/wake-history.md` should store concise wake notes, not long diaries.

## Publication

- Publish the human summary to `metrics/summary.md`.
- Publish the structured snapshot to `metrics/summary.json`.
- Publish wake continuity notes to `metrics/wake-history.md`.
- Do not treat metrics summaries or wake history as evidence of an external side effect.

## Common Pitfalls

- Turning thin metrics into overconfident strategy claims
- Hiding unresolved replies inside aggregate numbers
- Writing wake history like a diary instead of a compact operating log

## Verification Checklist

- [ ] `metrics/summary.md` is readable in one quick pass
- [ ] `metrics/summary.json` and `metrics/summary.md` describe the same current state
- [ ] `metrics/wake-history.md` adds one concise note per wake without deleting history
- [ ] Unresolved inbound and evidence gaps remain visible instead of being buried in aggregates

## Rules

1. Metrics are evidence, not strategy by themselves.
2. Missing metrics are gaps, not invented values.
3. Keep unresolved inbound state visible.
4. Do not delete historical summaries or wake notes during ordinary operation.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Metrics are missing | Preserve the gap and name the missing source |
| Too much conversation noise | Use `takyon-conversation-followup` to publish a compact follow-up note before deciding |
