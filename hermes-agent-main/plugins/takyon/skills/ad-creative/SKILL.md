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
- For local UGC image/video assets, use `business_generate_creative_asset`. It writes the generated file under the business workspace, writes a receipt, records an event, and does not post or buy media.
- For Meta UGC video, create the hook, script, shot list, generation prompt, local `.mp4`, and receipt. The default path should be `campaigns/<campaign>/creatives/meta-ugc/<asset-id>.mp4` unless the operator gives another business-relative path.
- Queue Meta posting, X posting, ad spend, or other outbound external work with `business_enqueue_job`; keep that separate from asset generation.
- Include `requires_api` such as `meta`, `x`, `openai`, `fal`, or explicit `requires_env` names.
- In test mode, still create the copy/spec and generate local assets when provider credentials and business budget gates pass. Use `business_publish_test_outreach` for local social/ad outreach publication; external delivery and spend must be marked suppressed.
- Record which angle is expected to work and why.

If a generation provider credential or budget is absent, do not invent a generated asset. If posting credentials are absent in live mode, the posting job should fail or stay blocked; do not invent a posted ad. In test mode, local generated creative assets are allowed, while external posting and spend remain suppressed or queued with gates.
