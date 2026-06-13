---
name: takyon-reddit
description: Honest organic Reddit participation for one Takyon business — subreddit-fit posts, comments, and durable Reddit voice. Not for paid Reddit ads.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]
metadata:
  hermes:
    category: takyon
    tags: [takyon, reddit, distribution, organic, community, posts, comments]
    related_skills: [takyon-reddit-ads, takyon-distribution, takyon-x, takyon-conversation-followup, takyon-business-metrics]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_calculate_pulse,
        business_reddit_publish_outreach,
        business_record_conversation_message,
        business_update_conversation_message_status,
      ]
    routing:
      owns: organic Reddit posts, comments, subreddit selection, and durable Reddit voice
      when_to_use:
        - the business needs an organic subreddit post or comment
        - a wake or campaign turn is clearly Reddit-shaped and not a paid ad
      do_not_use_for:
        - paid Reddit ads or promoted posts (use takyon-reddit-ads)
        - astroturfing, vote manipulation, or undisclosed promotion
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/voice/reddit.md
      - distribution/campaign
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon Reddit

## Overview

Use this skill for one business's organic Reddit work: subreddit selection, post/comment drafting, and durable Reddit voice. This rail owns honest participation only. Paid launch/control/metrics stay on `takyon-reddit-ads`.

## When to Use

- When the operator asks for a Reddit post or Reddit comment.
- When a campaign has a Reddit-native angle that should be expressed as community participation rather than a paid ad.
- When repeated Reddit work needs durable voice guidance in `distribution/voice/reddit.md`.
- Do not use this skill for paid Reddit ads, invented community proof, or disguised promotion.

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/voice/reddit.md`, `distribution/campaign/`, `distribution/local-published/`, `metrics/conversations/`
- Tools: `business_read_business`, `business_calculate_pulse`, `business_read_file`, `business_list_files`, `business_list_conversation_messages`, `business_read_conversation_thread`, `business_write_file`, `business_patch_file`, `business_reddit_publish_outreach`, `business_publish_test_outreach`, `business_record_conversation_message`, `business_update_conversation_message_status`
- Live budget rule: live Reddit publication charges the fixed Reddit creative-credit price from the shared Reddit channel bucket

## Prerequisites

- The Takyon toolset must be available.
- Start with `business_read_business` and usually `business_calculate_pulse` so the Reddit move comes from current business pressure rather than generic "do Reddit" energy.
- Read the target subreddit's norms before posting: self-promo rules, flair requirements, comment expectations, and whether links are even welcome.
- If repeated Reddit work needs a durable voice, keep it in `distribution/voice/reddit.md`.

## References

None bundled yet. The subreddit's current rules and the business's own `research/` state are the canonical inputs.

## Templates

None.

## Scripts

None.

## How to Run

- Call `business_read_business` first to inspect current campaign state, unresolved conversations, and business mode.
- Read relevant `research/` state before drafting a top-level Reddit post so the audience, promise, and proof come from the real business.
- Use `business_list_conversation_messages` and `business_read_conversation_thread` when replying inside an existing Reddit thread.
- Use `business_write_file` or `business_patch_file` to create or refresh `distribution/voice/reddit.md` when repeated Reddit work needs durable channel guidance.
- Draft one truthful post or comment that is actually useful in that subreddit, then publish through `business_reddit_publish_outreach`.
- In test mode, expect a suppressed local artifact under `distribution/local-published/` plus a receipt. In live mode, expect the creative-credit gate, a worker job, and a receipt under `metrics/receipts/outreach/`.

## Procedure

1. Call `business_read_business` and, when useful, `business_calculate_pulse`.
2. Inspect `distribution/voice/reddit.md`, the current campaign workspace, and the specific subreddit or thread you are about to enter.
3. If this is repeated Reddit work and the voice guide is missing or stale, refresh `distribution/voice/reddit.md` first.
4. For a top-level post, read current `research/` state first, then pick the subreddit based on fit and rules, not just audience size.
5. For a comment, read the target thread first and answer the real thing being asked or implied.
6. If the business is in test mode, call `business_reddit_publish_outreach` and expect a suppressed local artifact plus receipt.
7. If the business is in live mode, call `business_reddit_publish_outreach`, then inspect the resulting receipt, job, or blocker before claiming success.
8. Keep `metrics/conversations/` truthful with the conversation tools. Do not mark a thread resolved just because a draft exists.

## Output Format

- Durable Reddit voice guidance lives in `distribution/voice/reddit.md`.
- In-progress Reddit drafts stay visible under `distribution/campaign/` when they are not yet published.
- Local suppressed publication belongs under `distribution/local-published/`.
- Conversation truth belongs in `metrics/conversations/`.

## Publication

- Publish durable Reddit voice guidance to `distribution/voice/reddit.md`.
- Publish Reddit drafts and campaign context to `distribution/campaign/`.
- Publish local suppressed Reddit outputs to `distribution/local-published/`.
- Publish thread and reply state to `metrics/conversations/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.

## Common Pitfalls

- Treating Reddit like X and writing for virality instead of subreddit fit.
- Posting before reading the subreddit's current rules.
- Sliding into disguised promotion instead of useful participation.
- Treating a draft as if it was already posted.
- Losing conversation truth by clearing message state too early.

## Verification Checklist

- [ ] `distribution/voice/reddit.md` exists and matches the current business voice when Reddit is a repeated lane
- [ ] Any claimed Reddit post or comment has a corresponding tool result, local artifact, queued job, or receipt
- [ ] `distribution/local-published/` contains the expected suppressed artifact in test-mode publication paths
- [ ] `metrics/conversations/` reflects unresolved Reddit thread state truthfully
- [ ] The draft matches the target subreddit's rules and does not rely on fake proof or disguised promotion

## Rules

1. Default to one send-ready Reddit post or comment, not a pile of variants, unless the operator explicitly asks for alternatives.
2. Do not claim a live Reddit send without a tool-backed receipt or result.
3. Read the subreddit or thread before posting into it.
4. Keep durable voice guidance in `distribution/voice/reddit.md`, not inside one-off drafts.
5. No fabrication, astroturfing, vote manipulation, or fake community consensus.
6. Paid Reddit execution belongs to `takyon-reddit-ads`, not here.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| The subreddit fit is unclear | Read current `research/` state and the subreddit's recent top posts before drafting |
| The post reads like marketing copy | Rewrite it so it is useful to the subreddit even if nobody clicks through |
| Live publish is blocked | Record the blocker or inspect the worker receipt; do not hand-wave the send |
