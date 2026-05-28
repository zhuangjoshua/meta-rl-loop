---
name: takyon-conversation-followup
description: Summarize, triage, and follow up on unresolved business conversations and noisy reply backlogs for one business.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, conversations, replies, followup, wake]
    related_skills: [takyon-business-metrics, takyon-distribution]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_calculate_pulse,
        business_read_business,
        business_list_conversation_messages,
        business_read_conversation_thread,
        business_write_file,
      ]
  takyon:
    scope: business
    allowed_roots: [metrics, research]
    output_root: metrics
    publication:
      - metrics/conversations/followup.md
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon Conversation Followup

## Overview

Use this skill to turn unresolved inbound messages into a compact, truthful follow-up plan without spawning a generic subagent or pretending anything was sent.

## When to Use

- Use on `/wake` when unresolved inbound messages are blocking the next move.
- Use when reply volume is large enough that `business_read_business(query="conversations")` is no longer enough by itself.
- Use when the operator asks for reply triage, inbox compression, or a follow-up plan.
- Do not use this skill to claim a real external send or post happened.

## Quick Reference

- Primary root: `metrics/`
- Publication paths: `metrics/conversations/followup.md`, `metrics/conversations/`
- Best call points: noisy inbox review, unresolved reply triage, wake follow-up decisions
- Publication location: `metrics/conversations/followup.md`
- Tool names used by this skill: `business_calculate_pulse`, `business_read_business`, `business_list_conversation_messages`, `business_read_conversation_thread`, `business_write_file`, `business_patch_file`, `business_update_conversation_message_status`, `business_enqueue_job`, `business_record_memory`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_calculate_pulse` or `business_read_business` so the triage starts from real unresolved inbound state rather than stale notes.
- Use `business_list_conversation_messages` for the message slice and `business_read_conversation_thread` for the hottest threads before writing the summary.

## How to Run

- Call `business_calculate_pulse` first when this is a wake or a state-refresh turn.
- Call `business_read_business` with the conversations view when you need the current unresolved count, thread list, and filesystem mirrors.
- Call `business_list_conversation_messages` with `direction=inbound` and `status=needs_response` to get the actual backlog slice you are triaging.
- Call `business_read_conversation_thread` for the threads that matter most so the summary reflects full thread context rather than one isolated message.
- Use `business_write_file` or `business_patch_file` to publish `metrics/conversations/followup.md`.
- Use `business_update_conversation_message_status` only for obvious archive/ignore/responded decisions that the current turn truly resolved.
- If a real external send or post should happen later, record the guarded next step with `business_enqueue_job` instead of claiming it already happened.
- If the backlog reveals a durable objection, opportunity, or positioning change, record it with `business_record_memory`.

## Procedure

1. Call `business_calculate_pulse` and inspect unresolved inbound counts, recent activity, and any obvious urgency.
2. Call `business_read_business` and note the current conversation summary plus any existing `metrics/conversations/followup.md`. If no prior follow-up note exists, create a clean baseline instead of implying history.
3. Call `business_list_conversation_messages` for unresolved inbound messages. If there are none, publish a short note that no follow-up is currently required and stop there.
4. Group the backlog by urgency, thread, source, and repeated objection. For the threads that matter most, call `business_read_conversation_thread` so the summary captures actual context and not just snippets.
5. Publish `metrics/conversations/followup.md` with a compact breakdown: urgent replies, draftable replies, ignore/archive candidates, blocked items, and reusable learnings.
6. Only if the current turn actually resolved status for a message, call `business_update_conversation_message_status`. Do not clear `needs_response` just because a draft exists.
7. If the follow-up review implies a real outbound send, escalation, or external vendor action later, record that with `business_enqueue_job`. If it implies a durable strategic learning, call `business_record_memory`.

## Output Format

- `metrics/conversations/followup.md` should be a compact triage memo, not a diary.
- The memo should name exact thread ids or sources when that helps later wakes recover the context quickly.
- Any draft reply language should be clearly marked as draft language, not as a completed send.

## Publication

- Publish the current conversation triage and follow-up plan to `metrics/conversations/followup.md`.
- Keep thread mirrors and message truth in `metrics/conversations/`.
- Any external send/post claim must come from a separate guarded tool path and receipt, not from this skill.

## Common Pitfalls

- Clearing reply pressure without actually reviewing the thread
- Treating draft language as if it was already sent
- Hiding repeated objections instead of surfacing them as durable learnings

## Verification Checklist

- [ ] `metrics/conversations/followup.md` exists and matches the actual unresolved backlog
- [ ] Any status changes are backed by real decisions made in this turn
- [ ] No external send/post was claimed without a separate guarded tool result
- [ ] Durable objections or learnings were surfaced instead of buried

## Rules

1. Conversation triage is evidence and prioritization, not a fake send.
2. Keep unresolved messages unresolved until the turn truly resolves them.
3. Prefer compact, recoverable thread references over vague prose.
4. Keep all publication inside `metrics/` or `research/`.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| The backlog is too large to read all at once | Triage the highest-pressure slice first and name what was deferred |
| The right response is blocked on credentials or approval | Record the blocker and queue the next real action instead of clearing the thread |
