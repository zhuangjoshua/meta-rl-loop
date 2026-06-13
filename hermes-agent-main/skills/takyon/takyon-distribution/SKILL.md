---
name: takyon-distribution
description: Create, review, and continue honest Takyon distribution campaign work for one business, including lane planning, campaign artifacts, local test publication, and broader demand-creation coordination. Route X-native posting to takyon-x, organic Reddit execution to takyon-reddit, and Reddit paid execution to takyon-reddit-ads.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, distribution, outreach, campaigns]
    related_skills: [takyon-market-research, takyon-build-product, takyon-business-metrics, takyon-conversation-followup, takyon-x, takyon-reddit, ugc-video-ad, takyon-meta-ads, takyon-reddit-ads, takyon-static-ad-creative-generator]
    requires_toolsets: [takyon]
    requires_tools: [business_read_business, business_create_workspace, business_publish_test_outreach, business_enqueue_job]
    routing:
      owns: campaign work, lane planning, campaign artifacts, and broader demand-creation coordination
      when_to_use:
        - the business needs outbound demand creation or campaign iteration
        - the operator asks to launch, continue, or review outreach or campaigns
      do_not_use_for:
        - channel-native X drafting or X thread handling
        - channel-owned Reddit execution, including organic Reddit posting/comments and paid Reddit ad launch/control/metrics work
        - live paid Meta ad execution
        - live paid Reddit ad execution
        - product-surface changes
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/surface.md
      - distribution/campaign
      - distribution/local-published
required_environment_variables: []
required_credential_files: []
---

# Takyon Distribution

## Overview

Use this skill for cross-channel distribution campaign work: campaign planning, lane selection, campaign artifacts, local test publication, and broader demand-creation coordination.

## When to Use

- Use when the business needs outbound demand creation or campaign iteration.
- Use when the operator asks to launch, continue, or review outreach or campaigns.
- Use when the business needs a visible campaign workspace, lane plan, or campaign update that spans more than one post or reply.
- Do not use this skill for channel-native X drafting or X thread handling; use `takyon-x`.
- Do not use this skill for live paid Meta or Reddit ad execution; hand that move to `takyon-meta-ads` or `takyon-reddit-ads`.
- Do not use for product-surface changes that belong in `takyon-build-product`.

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/surface.md`, `distribution/campaign/`, `distribution/local-published/`
- Best call points: campaign continuation, lane planning, broader demand-creation coordination
- Publication lane: `distribution/surface.md` is the coarse snapshot; visible campaign artifacts live under `distribution/campaign/`; suppressed/local publication goes to `distribution/local-published/`; live sends/posts require tools and receipts
- Tool names used by this skill: `business_read_business`, `business_calculate_pulse`, `business_read_file`, `business_list_files`, `business_create_workspace`, `business_write_file`, `business_patch_file`, `business_publish_test_outreach`, `business_enqueue_job`, `business_ugc_ad_write`
- Paid-channel boundary: use this skill to decide the campaign and creative lane; use the channel-owned paid skill when the next move is a real Meta or Reddit ad launch/control/metrics action.

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` and usually `business_calculate_pulse` so you know whether replies are waiting and what campaign state already exists.
- If you need to inspect specific campaign files, use `business_read_file` or `business_list_files` instead of guessing the current state.
- If the work turns into X-native drafting or reply handling, switch to `takyon-x`.
- If the work turns into live organic Reddit posting/commenting, switch to `takyon-reddit`. If it turns into a live paid Meta or Reddit launch, switch to `takyon-meta-ads` or `takyon-reddit-ads`.
- If replies are large or noisy and you first need a compact triage, load `takyon-conversation-followup` before deciding whether the campaign should change.
- If a provider-backed publish path is blocked, use the publish tools or `business_enqueue_job` to record the real blocker; do not hand-claim success.

## References

- `references/campaign-rules.md`

## Templates

- `templates/campaign.md`
- `templates/reply-draft.md`
- `templates/forum-reply.md`

## How to Run

- Call `business_read_business` first to inspect current campaign state, conversation state, and existing publication artifacts.
- Read `distribution/surface.md` when it exists for the current high-level campaign snapshot, then fall back to direct campaign files or receipts when you need detail.
- Call `business_calculate_pulse` when you need the latest unresolved inbound or recent activity before deciding whether the campaign should change.
- Read current `research/` state when choosing the audience, promise, objection, or campaign angle.
- If replies are the main issue, use `takyon-x` for X or `takyon-conversation-followup` when the inbox is too noisy to inspect directly before drafting a broad campaign memo.
- Treat `distribution/campaign/` as the canonical campaign workspace. If only the legacy `distribution/phase-1-outreach/` tree exists, migrate or merge that visible campaign state into `distribution/campaign/` before adding new work.
- Use `templates/campaign.md` for the visible campaign workspace and `templates/reply-draft.md` only for generic, non-channel-specific draft notes.
- For broader forum-style replies that are not yet split into their own skill, use `templates/forum-reply.md`.
- Use `business_create_workspace`, `business_write_file`, and `business_patch_file` to create or update the canonical campaign workspace under `distribution/campaign/`.
- Use `ugc-video-ad` when the campaign needs the copied multi-clip UGC video ad pipeline. That skill publishes under `product/ugc-ads/` and records the finished asset with `business_ugc_ad_write`.
- Use `takyon-static-ad-creative-generator` or `ugc-video-ad` when the campaign needs fresh paid creative before a channel-owned launch skill can execute it.
- When a downstream channel or publish rail can make a truthful best-fit target choice from current business state, choose it and proceed; do not stop for a generic preference question just to keep the campaign moving.
- Prefer `business_publish_test_outreach` for generic local/suppressed publication only. For live X posting, hand off to `takyon-x` / `business_x_publish_outreach`; for live organic Reddit posting/comments, hand off to `takyon-reddit` / `business_reddit_publish_outreach`; for live Reddit ads, hand off to `takyon-reddit-ads`.
- If the publish path needs deferred vendor, ads, or external work rather than an immediate publish, use `business_enqueue_job` instead of inventing a successful send.

## Procedure

1. Call `business_read_business` and, when appropriate, `business_calculate_pulse`. Determine whether the real need is a campaign update, a lane change, a broader distribution plan, a non-X discussion-thread move that should stay here, or a channel-native X move.
2. If unresolved replies are the main issue, hand off X work to `takyon-x` or load `takyon-conversation-followup` before expanding the campaign.
3. Inspect the current campaign workspace. `distribution/campaign/` is canonical, and `distribution/surface.md` is the coarse summary. If only the legacy `distribution/phase-1-outreach/` tree exists, move or merge that visible campaign state into `distribution/campaign/` first. If `distribution/campaign/` is missing or too stale to trust after that check, create or refresh it with `business_create_workspace` and write the current campaign files there.
4. Update the visible campaign artifacts: objective, audience, lanes, assets, current blockers, and next iteration. Keep them business-scoped and durable instead of burying them in chat output.
5. If the campaign needs the copied UGC video ad path, run `ugc-video-ad`, then store the resulting `product/ugc-ads/<slug>/` publication path in the campaign workspace after `business_ugc_ad_write` records it.
6. If the next move is live organic Reddit posting or commenting, keep the strategy here but hand the execution to `takyon-reddit`. If the next move is a live paid Meta or Reddit campaign, keep the strategy and asset decisions here, but hand the execution to `takyon-meta-ads` or `takyon-reddit-ads` instead of stretching this skill into channel-owned launch/control work.
7. If the publish path should remain local, call `business_publish_test_outreach` and expect a suppressed local artifact under `distribution/local-published/` plus a receipt.
8. If the business is in live mode and the next move is provider-backed, route X work to `takyon-x`, route organic Reddit work to `takyon-reddit`, route Reddit paid work to `takyon-reddit-ads`, and use `business_enqueue_job` only to record deferred external work that already belongs to a truthful owned rail.
9. Before choosing or changing a campaign angle, read current `research/` state and keep the audience, promise, objection, and angle aligned so the updated distribution direction stays consistent with `research/strategy.md` rather than drifting into a shadow strategy.

## Output Format

- Campaign workspace files should be visible under `distribution/`.
- Generic non-channel draft notes should stay clearly marked as drafts until applied.
- Do not treat the campaign workspace as proof that a channel-native post or reply happened.

## Publication

- Publish the coarse campaign snapshot to `distribution/surface.md`.
- Publish campaign workspaces to `distribution/campaign/`.
- Local suppressed publication belongs under `distribution/local-published/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.

## Common Pitfalls

- Treating a channel-native X move as if it belonged in the broad campaign layer
- Treating live paid Meta or Reddit execution as if it belonged in the broad campaign layer
- Launching more outward work while unresolved replies are obviously waiting
- Treating drafted copy as if it was already sent
- Scattering campaign assets outside `distribution/`

## Verification Checklist

- [ ] Current campaign assets are visible under `distribution/campaign/`
- [ ] Any claimed send, post, or publish has a corresponding tool result, local artifact, queued job, or receipt
- [ ] `distribution/local-published/` contains the expected suppressed artifact in test-mode publication paths
- [ ] Channel-native X work was delegated to `takyon-x` when appropriate

## Rules

1. Do not claim external sends, posts, or spend without receipts or tool success.
2. In test mode, use local publication paths and suppressed receipts.
3. Prefer reply handling before more outward distribution when people are waiting.
4. Keep visible campaign artifacts in `distribution/campaign/`.
5. Use `takyon-x` for channel-native X post and reply work.
6. Use the channel-owned Reddit skill for organic Reddit execution and the paid-ad skill for live Meta/Reddit ad execution.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| The work is really a channel-native X move | Switch to `takyon-x` instead of stretching the campaign skill |
| No publish provider is available | Use local publication if allowed, otherwise record the blocker |
| There are unresolved replies | Handle them directly or use `takyon-conversation-followup` before expanding new outreach |
