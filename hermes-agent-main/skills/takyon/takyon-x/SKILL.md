---
name: takyon-x
description: Create, review, and continue honest X posts, replies, and thread handling for one Takyon business.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, x, twitter, distribution, replies]
    related_skills: [takyon-distribution, takyon-business-metrics, takyon-conversation-followup, takyon-market-research]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_calculate_pulse,
        business_publish_outreach,
        business_record_conversation_message,
        business_update_conversation_message_status,
      ]
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/voice/x.md
      - distribution/campaign
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon X

## Overview

Use this skill for X-specific execution: posts, replies, thread handling, and durable X voice guidance for one business.

## When to Use

- Use when the operator asks for an X post, X reply, thread response, or X-native participation.
- Use on `/wake` when unresolved inbound is clearly X-shaped and can be handled without broad inbox compression.
- Use when a campaign needs X-ready copy or thread follow-through, not just broad planning.
- Do not use this skill for cross-channel campaign strategy or ICP research.

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/voice/x.md`, `distribution/campaign/`, `distribution/local-published/`, `metrics/conversations/`
- Best call points: X posting, X replies, X thread handling, X voice maintenance
- Publication location: `distribution/voice/x.md` for durable voice guidance, `distribution/local-published/` for suppressed local publication, `metrics/conversations/` for thread truth
- Tool names used by this skill: `business_read_business`, `business_calculate_pulse`, `business_read_file`, `business_list_files`, `business_list_conversation_messages`, `business_read_conversation_thread`, `business_write_file`, `business_patch_file`, `business_publish_outreach`, `business_publish_test_outreach`, `business_record_conversation_message`, `business_update_conversation_message_status`, `business_enqueue_job`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` and usually `business_calculate_pulse` so the move begins from real conversation pressure and real campaign state.
- If thread volume is too noisy to inspect directly, load `takyon-conversation-followup` first and use its compact triage before deciding what to post or answer.
- If the business uses X repeatedly and `distribution/voice/x.md` is missing, stale, or weak, refresh it before drafting more output.

## References

- `references/x-style-guide.md`

## Templates

- `templates/x-post.md`
- `templates/x-reply.md`

## How to Run

- Call `business_read_business` first to inspect current campaign state, unresolved replies, and existing X artifacts.
- Call `business_calculate_pulse` when reply pressure or recent activity should affect whether you post, reply, or hold.
- Use `business_list_conversation_messages` and `business_read_conversation_thread` to inspect the actual thread state before replying.
- Use `business_write_file` or `business_patch_file` to create or refresh `distribution/voice/x.md` when repeated X work needs durable channel guidance.
- Treat `distribution/campaign/` as the canonical campaign workspace. If only legacy X drafts live under `distribution/phase-1-outreach/`, move or merge that visible state forward before drafting new work.
- Keep in-progress X drafts visible under `distribution/campaign/` when the turn is not publishing immediately.
- Prefer `business_publish_outreach` as the main publish path. It will use test-mode behavior when the business is in test mode. Use `business_publish_test_outreach` directly only when you intentionally want a local suppressed artifact without taking the normal publish path.
- Use `business_record_conversation_message` and `business_update_conversation_message_status` so the thread state stays truthful after a draft, suppressed publication, or real publish outcome.

## Procedure

1. Call `business_read_business` and, when useful, `business_calculate_pulse`. If unresolved X replies exist, handle them before drafting new top-level posts unless the operator explicitly prioritizes a post.
2. Inspect `distribution/voice/x.md`, the current campaign workspace, and the relevant conversation mirrors. If X voice guidance is missing, stale, or too generic, refresh `distribution/voice/x.md` first.
3. If this is a reply, read the target thread first and draft exactly one send-ready reply by default. Use one move only, not variants, unless the operator explicitly asks for alternates.
4. If this is a top-level post, draft one send-ready post with one concrete payload. Prefer a real number, product contrast, tradeoff, or direct observation over generic positioning language.
5. If the turn is still in draft mode, keep the draft visible under `distribution/campaign/` rather than pretending it was published.
6. If the business is in test mode or the publish path should remain local, call `business_publish_outreach` and expect a suppressed local artifact under `distribution/local-published/` plus a conversation mirror or receipt. If you explicitly need the local-only path, call `business_publish_test_outreach` directly.
7. If the business is in live mode and the publish path is provider-backed, call `business_publish_outreach` and inspect the resulting receipt, job, or blocker. If the channel requires deferred action rather than immediate publication, record that next step with `business_enqueue_job`.
8. After the draft or publish step, keep `metrics/conversations/` truthful with the conversation tools. Do not mark a thread resolved just because a draft exists.

## Output Format

- Durable X voice guidance should live in `distribution/voice/x.md`.
- Send-ready drafts should stay compact and visible in `distribution/campaign/` when they are not yet published.
- Local suppressed publication belongs under `distribution/local-published/`.
- Conversation truth belongs in `metrics/conversations/`, not in a narrative summary.

## Publication

- Publish durable X voice guidance to `distribution/voice/x.md`.
- Publish X drafts and surrounding channel artifacts to `distribution/campaign/`.
- Publish local suppressed X outputs to `distribution/local-published/`.
- Publish thread and reply state to `metrics/conversations/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.

## Common Pitfalls

- Letting X replies turn into polished rebuttal essays
- Drafting variants when one sharp reply is enough
- Treating a draft as if it was already posted
- Losing thread truth by clearing message state too early

## Verification Checklist

- [ ] `distribution/voice/x.md` exists and matches the current business voice when X is a repeated lane
- [ ] Any claimed post or reply has a corresponding tool result, local artifact, queued job, or receipt
- [ ] `distribution/local-published/` contains the expected suppressed artifact in test-mode publication paths
- [ ] `metrics/conversations/` reflects unresolved X thread state truthfully

## Rules

1. Default to one send-ready reply, not variants, unless the operator explicitly asks for alternatives.
2. Do not claim a live X send or reply without a tool-backed receipt or result.
3. Read the thread before answering it.
4. Keep durable voice guidance in `distribution/voice/x.md`, not inside one-off drafts.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Reply sounds generic or over-explained | Refresh `distribution/voice/x.md`, then redraft with one move and one concrete payload |
| Too many X replies to inspect cheaply | Load `takyon-conversation-followup` first, then come back with the compact triage |
| Publish provider is blocked | Use local publication if allowed, otherwise record the blocker or queue the next step |
