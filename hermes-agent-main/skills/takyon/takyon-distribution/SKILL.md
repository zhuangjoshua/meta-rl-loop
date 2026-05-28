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
  takyon:
    scope: business
    allowed_roots: [distribution, metrics, research]
    output_root: distribution
    publication:
      - distribution/phase-1-outreach
      - distribution/local-published
      - metrics/conversations
required_environment_variables: []
required_credential_files: []
---

# Takyon Distribution

Use this skill for demand creation and response handling: outreach, campaign workspaces, message drafts, local test publication, and reply review.

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/phase-1-outreach/`, `distribution/local-published/`, `metrics/conversations/`
- Best call points: outbound demand creation, reply handling, campaign continuation
- Publication lane: suppressed/local publication goes to `distribution/local-published/`; live sends/posts require tools and receipts

## References

- `references/campaign-rules.md`

## Templates

- `templates/campaign.md`
- `templates/reply-draft.md`

## When to Use

- Use when the business needs outbound demand creation or campaign iteration.
- Use on `/wake` when unresolved inbound messages need attention.
- Use when the operator asks to launch, continue, or review outreach or campaigns.

## Procedure

1. Check unresolved inbound messages before new outward motion.
2. Continue an existing campaign when it is still the right lane.
3. Publish through the canonical business outreach tools.
4. Keep campaign assets and notes inside `distribution/`.
5. Record reply state and meaningful results in `metrics/`.

## Output Format

- Campaign workspace files should be visible under `distribution/`.
- Reply drafts should stay clearly marked as drafts until applied.
- Metrics-side conversation mirrors should reflect actual state, not guessed state.

## Publication

- Publish campaign workspaces to `distribution/phase-1-outreach/`.
- Local suppressed publication belongs under `distribution/local-published/`.
- Publish conversation mirrors and reply state to `metrics/conversations/`.
- Live external publication belongs to canonical business tools and their receipts, not hand-written success claims.
- Reply/conversation aftermath should be mirrored in `metrics/conversations/`.

## Pitfalls

- Launching new outreach while replies are sitting unresolved
- Treating drafted copy as if it was already sent
- Scattering campaign assets outside `distribution/`

## Verification

- Current campaign assets are visible under `distribution/`
- Any claimed send/post/publish has a corresponding tool success or receipt
- `metrics/conversations/` reflects unresolved inbound and reply state truthfully

## Rules

1. Do not claim external sends, posts, or spend without receipts or tool success.
2. In test mode, use local publication paths and suppressed receipts.
3. Prefer reply handling before more outward distribution when people are waiting.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| No publish provider is available | Use local publication if allowed, otherwise record the blocker |
| There are unresolved replies | Handle or summarize them before expanding new outreach |
