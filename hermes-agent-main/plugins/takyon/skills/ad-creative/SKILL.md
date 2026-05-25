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
- For local UGC image/video assets, use `business_generate_creative_asset`; discover Sora/video availability from `business_registry.runtime_capabilities.video_generation`.
- A generated video is not complete until the provider-backed asset tool returns a file and receipt, or an exact gate error such as missing provider, missing API key, missing budget, or model/provider failure.
- For Meta UGC video, default to `campaigns/<campaign>/creatives/meta-ugc/<asset-id>.mp4` unless the operator gives another business-relative path.
- Queue Meta posting, X posting, ad spend, or other outbound external work with `business_enqueue_job`; keep that separate from asset generation.
- Include `requires_api` such as `meta`, `x`, `openai`, `fal`, or explicit `requires_env` names.
- In test mode, still create the copy/spec and generate local assets when provider credentials and business budget gates pass. Use `business_publish_outreach` for local social/ad outreach publication; external delivery and spend must be marked suppressed.
- If the operator asks to "generate and post" while external posting is gated, do the local generation/publication path and queue or record the gated post instead of asking the operator to choose among obvious partial options.
- Record which angle is expected to work and why.

If a generation provider credential or budget is absent, do not invent a generated asset. If posting credentials are absent in live mode, the posting job should fail or stay blocked; do not invent a posted ad. In test mode, local generated creative assets are allowed, while external posting and spend remain suppressed or queued with gates.
