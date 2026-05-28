---
name: takyon-distribution
description: Create, publish, review, and continue honest Takyon distribution work for one business, including outreach, replies, and campaign assets.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, distribution, outreach, campaigns, replies]
    related_skills: [takyon-market-research, takyon-build-product, takyon-business-metrics]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_publish_outreach, business_conversation_agent_task]
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/voice
      - distribution/phase-1-outreach
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon Distribution

## Overview

Use this skill for demand creation and response handling: outreach, campaign workspaces, message drafts, local test publication, and reply review.

## When to Use

- Use when the business needs outbound demand creation or campaign iteration.
- Use on `/wake` when unresolved inbound messages need attention.
- Use when the operator asks to launch, continue, or review outreach or campaigns.
- Do not use for product-surface changes that belong in `takyon-build-product`.

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/voice/`, `distribution/phase-1-outreach/`, `distribution/local-published/`, `metrics/conversations/`
- Best call points: outbound demand creation, reply handling, campaign continuation
- Publication lane: channel voice guidance lives under `distribution/voice/`; suppressed/local publication goes to `distribution/local-published/`; live sends/posts require tools and receipts
- Tool names used by this skill: `business_read_business`, `business_calculate_pulse`, `business_read_file`, `business_list_files`, `business_create_workspace`, `business_write_file`, `business_patch_file`, `business_generate_creative_asset`, `business_publish_outreach`, `business_publish_test_outreach`, `business_enqueue_job`, `business_conversation_agent_task`, `business_upsert_conversation_thread`, `business_record_conversation_message`, `business_update_conversation_message_status`

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` and usually `business_calculate_pulse` so you know whether replies are waiting and what campaign state already exists.
- If you need to inspect specific campaign files or conversation mirrors, use `business_read_file` or `business_list_files` instead of guessing the current state.
- If a provider-backed publish path is blocked, use the publish tools or `business_enqueue_job` to record the real blocker; do not hand-claim success.

## References

- `references/campaign-rules.md`
- `references/x-style-guide.md`

## Templates

- `templates/campaign.md`
- `templates/reply-draft.md`
- `templates/x-reply.md`
- `templates/forum-reply.md`

## How to Run

- Call `business_read_business` first to inspect current campaign state, conversation state, and existing publication artifacts.
- Call `business_calculate_pulse` when you need the latest unresolved inbound and reply pressure before deciding whether to push outbound work.
- Use `business_conversation_agent_task` when inbound volume is too large to review manually; otherwise use `business_upsert_conversation_thread`, `business_record_conversation_message`, and `business_update_conversation_message_status` for direct conversation maintenance.
- If the business will post on X or reply in public forums repeatedly, inspect `distribution/voice/` first and refresh the channel guidance before drafting more content.
- Use `business_create_workspace`, `business_write_file`, and `business_patch_file` to create or update campaign workspaces under `distribution/phase-1-outreach/`.
- Use `business_create_workspace`, `business_write_file`, and `business_patch_file` to create or refresh `distribution/voice/` artifacts when the channel voice is missing, stale, or obviously generic.
- Use `business_generate_creative_asset` when the campaign needs provider-backed image or video assets.
- Prefer `business_publish_outreach` as the main publish path. It will use test-mode behavior when the business is in test mode. Use `business_publish_test_outreach` directly only when you intentionally want a local suppressed artifact without taking the normal publish path.
- If the publish path needs deferred vendor, ads, or external work rather than an immediate publish, use `business_enqueue_job` instead of inventing a successful send.

## Procedure

1. Call `business_read_business` and, when appropriate, `business_calculate_pulse`. If unresolved inbound exists, handle that first unless the operator explicitly prioritizes outbound work anyway.
2. If the conversation backlog is large or noisy, call `business_conversation_agent_task` to cluster, triage, or draft replies before starting new outreach. If the backlog is small, maintain the canonical thread and message state directly with the conversation tools.
3. Inspect `distribution/voice/`, the current campaign workspace, and any recent conversation artifacts. If the business is using X or forum channels repeatedly and the channel guidance is missing, stale, or weak, create or refresh the voice files under `distribution/voice/` first.
4. Keep the X and forum guidance durable: default to one send-ready reply by default, not variants; use concrete nouns over abstract copywriting language; and make sure public replies read like live conversation instead of analysis.
5. Inspect the current campaign workspace. If there is no suitable workspace under `distribution/phase-1-outreach/`, create one with `business_create_workspace` and write the initial campaign files there.
6. Draft or update campaign assets under `distribution/phase-1-outreach/`. If the campaign needs creative media, call `business_generate_creative_asset` first and store the resulting local asset path in the workspace.
7. If the business is in test mode or the publish path should remain local, call `business_publish_outreach` and expect a suppressed local artifact under `distribution/local-published/` plus a conversation mirror or receipt. If you explicitly need the local-only path, call `business_publish_test_outreach` directly.
8. If the business is in live mode and the publish path is provider-backed, call `business_publish_outreach` and inspect the resulting job, receipt, or blocker. If the channel requires deferred external work rather than immediate publication, record it with `business_enqueue_job`.
9. After publish or reply handling, make sure thread state and message status are mirrored under `metrics/conversations/` with the conversation tools so future wakes see the real state.

## Output Format

- Voice guidance should live under `distribution/voice/` as reusable channel policy, not inside one-off campaign files.
- Campaign workspace files should be visible under `distribution/`.
- Reply drafts should stay clearly marked as drafts until applied.
- Metrics-side conversation mirrors should reflect actual state, not guessed state.

## Publication

- Publish channel voice guidance to `distribution/voice/`.
- Publish campaign workspaces to `distribution/phase-1-outreach/`.
- Local suppressed publication belongs under `distribution/local-published/`.
- Publish conversation mirrors and reply state to `metrics/conversations/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.
- Reply/conversation aftermath should be mirrored in `metrics/conversations/`.

## Common Pitfalls

- Letting X or forum replies drift into generic bot rebuttals
- Launching new outreach while replies are sitting unresolved
- Treating drafted copy as if it was already sent
- Scattering campaign assets outside `distribution/`

## Verification Checklist

- [ ] `distribution/voice/` contains truthful, reusable channel guidance when the business is working public channels repeatedly
- [ ] Current campaign assets are visible under `distribution/phase-1-outreach/`
- [ ] Any claimed send, post, or publish has a corresponding tool result, local artifact, queued job, or receipt
- [ ] `distribution/local-published/` contains the expected suppressed artifact in test-mode publication paths
- [ ] `metrics/conversations/` reflects unresolved inbound and reply state truthfully

## Rules

1. Do not claim external sends, posts, or spend without receipts or tool success.
2. In test mode, use local publication paths and suppressed receipts.
3. Prefer reply handling before more outward distribution when people are waiting.
4. Default to one send-ready reply, not variants, unless the operator explicitly asks for alternatives.
5. Keep public-channel voice guidance durable under `distribution/voice/`; keep live drafts in campaign workspaces.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Replies sound generic or over-explained | Refresh `distribution/voice/` first, then redraft with one social move and one concrete payload |
| No publish provider is available | Use local publication if allowed, otherwise record the blocker |
| There are unresolved replies | Handle or summarize them before expanding new outreach |
