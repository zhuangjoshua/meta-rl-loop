---
name: takyon-ad-creative
description: Draft ad angles, copy, landing hooks, creative specs, and posting requests.
---

# Takyon Ad Creative

Use this skill for ad angles, paid-social copy, image/video briefs, landing-page hooks, and creative iteration.

## Practice

- Read the campaign brief, product, audience, positioning, and prior learnings.
- Produce multiple distinct angles when exploration matters.
- Write drafts under the campaign workspace, usually `creatives/`, `copy/`, or `posts/`.
- Queue image/video generation, Meta posting, X posting, or other external work with `business_enqueue_job`.
- Include `requires_api` such as `meta`, `x`, `openai`, `fal`, or explicit `requires_env` names.
- In test mode, still create the copy/spec and use `business_publish_test_outreach` for local social/ad outreach publication; external delivery and spend must be marked suppressed.
- Record which angle is expected to work and why.

If an API credential is absent in live mode, the tool should fail; do not invent a posted ad or generated asset. In test mode, record only local drafts/specs and suppressed-side-effect receipts.
