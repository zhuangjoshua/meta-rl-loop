---
name: takyon-reddit
description: Create, review, and continue honest Reddit posts, comments, and subreddit-aware participation for one Takyon business.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, reddit, distribution, communities, replies]
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
      - distribution/voice/reddit.md
      - distribution/voice/reddit-subreddits.md
      - distribution/campaign
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon Reddit

## Overview

Use this skill for Reddit-specific execution: posts, comments, thread handling, and durable subreddit-aware guidance for one business.

## When to Use

- Use when the operator asks for a Reddit post, comment, reply, or subreddit participation.
- Use on `/wake` when unresolved inbound is clearly Reddit-shaped and can be handled without full inbox compression.
- Use when the business needs subreddit-aware drafting or thread continuation, not just broad planning.
- Do not use this skill for cross-channel campaign strategy or ICP research.

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/voice/reddit.md`, `distribution/voice/reddit-subreddits.md`, `distribution/campaign/`, `distribution/local-published/`, `metrics/conversations/`
- Best call points: Reddit posts, Reddit comments, subreddit notes, Reddit thread handling
- Publication location: `distribution/voice/reddit.md` for durable channel rules, `distribution/voice/reddit-subreddits.md` for recurring subreddit notes, `metrics/conversations/` for thread truth
- Tool names used by this skill: `business_read_business`, `business_calculate_pulse`, `business_read_file`, `business_list_files`, `business_list_conversation_messages`, `business_read_conversation_thread`, `business_write_file`, `business_patch_file`, `business_publish_outreach`, `business_publish_test_outreach`, `business_record_conversation_message`, `business_update_conversation_message_status`, `business_enqueue_job`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` and usually `business_calculate_pulse` so the move begins from real thread state and real campaign context.
- If thread volume is too noisy to inspect directly, load `takyon-conversation-followup` first and use its compact triage before deciding what to post or answer.
- If the business uses Reddit repeatedly and `distribution/voice/reddit.md` or `distribution/voice/reddit-subreddits.md` is missing, stale, or weak, refresh it before drafting more output.

## References

- `references/reddit-style-guide.md`
- `references/reddit-post-style-guide.md`
- `references/reddit-reply-style-guide.md`

## Templates

- `templates/reddit-post.md`
- `templates/reddit-reply.md`

## How to Run

- Call `business_read_business` first to inspect current campaign state, unresolved replies, and existing Reddit artifacts.
- Call `business_calculate_pulse` when reply pressure or recent activity should affect whether you post, comment, or hold.
- Use `business_list_conversation_messages` and `business_read_conversation_thread` to inspect the actual thread state before replying.
- Use `business_write_file` or `business_patch_file` to create or refresh `distribution/voice/reddit.md` and `distribution/voice/reddit-subreddits.md` when repeated Reddit work needs durable channel guidance.
- Treat `distribution/campaign/` as the canonical campaign workspace. If only legacy Reddit drafts live under `distribution/phase-1-outreach/`, move or merge that visible state forward before drafting new work.
- Use `references/reddit-reply-style-guide.md` for replies/comments and `references/reddit-post-style-guide.md` for top-level posts instead of leaning on one blended tone note.
- Keep in-progress Reddit drafts visible under `distribution/campaign/` when the turn is not publishing immediately.
- Prefer `business_publish_outreach` as the main publish path. It will use test-mode behavior when the business is in test mode. Use `business_publish_test_outreach` directly only when you intentionally want a local suppressed artifact without taking the normal publish path.
- Use `business_record_conversation_message` and `business_update_conversation_message_status` so the thread state stays truthful after a draft, suppressed publication, or real publish outcome.

## Procedure

1. Call `business_read_business` and, when useful, `business_calculate_pulse`. If unresolved Reddit replies exist, handle them before drafting new top-level posts unless the operator explicitly prioritizes a post.
2. Inspect `distribution/voice/reddit.md`, `distribution/voice/reddit-subreddits.md`, the current campaign workspace, and the relevant conversation mirrors. If subreddit notes or Reddit channel guidance are missing, stale, or too generic, refresh them first.
3. If this is a reply or comment, read the target thread first, then use `references/reddit-reply-style-guide.md`. React to one point only and default to one send-ready comment, not variants, unless the operator explicitly asks for alternatives.
4. If this is a top-level post, use `references/reddit-post-style-guide.md`. Draft from a specific spark, keep the title slightly unfinished, and end the body with a real question, tension, or unresolved observation instead of a polished conclusion.
5. If the subreddit is unfamiliar or recurrent enough to matter later, update `distribution/voice/reddit-subreddits.md` with what the community rewards, what gets punished, title patterns, and self-promo tolerance before finalizing new content.
6. If the turn is still in draft mode, keep the draft visible under `distribution/campaign/` rather than pretending it was published.
7. If the business is in test mode or the publish path should remain local, call `business_publish_outreach` and expect a suppressed local artifact under `distribution/local-published/` plus a conversation mirror or receipt. If you explicitly need the local-only path, call `business_publish_test_outreach` directly.
8. If the business is in live mode and the publish path is provider-backed, call `business_publish_outreach` and inspect the resulting receipt, job, or blocker. If the channel requires deferred action rather than immediate publication, record that next step with `business_enqueue_job`.
9. After the draft or publish step, keep `metrics/conversations/` truthful with the conversation tools. Do not mark a thread resolved just because a draft exists.

## Output Format

- Durable Reddit channel rules should live in `distribution/voice/reddit.md`.
- Durable subreddit notes should live in `distribution/voice/reddit-subreddits.md`.
- Send-ready drafts should stay visible in `distribution/campaign/` when they are not yet published.
- Local suppressed publication belongs under `distribution/local-published/`.
- Conversation truth belongs in `metrics/conversations/`, not in a narrative summary.

## Publication

- Publish durable Reddit channel guidance to `distribution/voice/reddit.md`.
- Publish recurring subreddit notes to `distribution/voice/reddit-subreddits.md`.
- Publish Reddit drafts and surrounding channel artifacts to `distribution/campaign/`.
- Publish local suppressed Reddit outputs to `distribution/local-published/`.
- Publish thread and reply state to `metrics/conversations/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.

## Common Pitfalls

- Writing polished launch copy instead of a live thread contribution
- Replying to three points at once instead of one
- Cosplaying Reddit slang instead of writing plainly
- Treating a draft as if it was already posted

## Verification Checklist

- [ ] `distribution/voice/reddit.md` exists and matches the current business voice when Reddit is a repeated lane
- [ ] `distribution/voice/reddit-subreddits.md` contains up-to-date recurring subreddit notes when Reddit is a repeated lane
- [ ] Any claimed post or comment has a corresponding tool result, local artifact, queued job, or receipt
- [ ] `distribution/local-published/` contains the expected suppressed artifact in test-mode publication paths
- [ ] `metrics/conversations/` reflects unresolved Reddit thread state truthfully

## Rules

1. Read the thread before answering it.
2. Default to one send-ready comment, not variants, unless the operator explicitly asks for alternatives.
3. React to one point and add one real increment.
4. Do not fake native slang, fake anecdotes, or fake community familiarity.
5. Do not claim a live Reddit send or reply without a tool-backed receipt or result.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Reply sounds too polished or assistant-like | Cut the greeting, react to one point only, and keep one real increment |
| Too many Reddit replies to inspect cheaply | Load `takyon-conversation-followup` first, then come back with the compact triage |
| Publish provider is blocked | Use local publication if allowed, otherwise record the blocker or queue the next step |
