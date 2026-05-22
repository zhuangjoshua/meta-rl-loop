# Business Marketing Skills Add-On

## Verified Implementation - 2026-05-22

Implemented:
- New Takyon-owned skill package: `skills/takyon-business-marketing`.
- The package is copied/adapted from audited external marketing, SEO/GEO, Meta ads, and cold-email skill references, but excludes upstream installers, home-directory writes, Meta mutation, email sending, CAPI sending, and private contact scraping.
- New bounded workflow ids:
  - `business_marketing_context`
  - `business_search_visibility`
  - `business_conversion_review`
  - `business_content_engine`
  - `business_outreach_pipeline`
  - `business_paid_media_review`
  - `business_measurement_plan`
- All workflows run through one generic app-owned runner in `src/lib/business-skills.ts`.
- Every workflow writes visible artifacts to:
  - `business_documents`
  - `business_memory_records` namespace `business_skills`
  - the business workspace under agent-authored files:
    - `memory/product-marketing-context.md`
    - `product/search-visibility.md`
    - `product/conversion-review.md`
    - `product/measurement-plan.md`
    - `outreach/content-engine.md`
    - `outreach/outreach-pipeline.md`
    - `outreach/paid-media-review.md`
- The workspace map now lists these files under `business_skills`, so CEO/runtime inspection can find them without knowing internal code paths.
- The local worker dispatches these workflows with one `business_skill.*` branch.
- CEO chat can queue these workflows from operator requests for positioning, SEO/GEO, CRO, content, outreach pipeline, paid media review, and measurement.
- Daily CEO wakeup may queue at most two of these no-side-effect business skills, after capability preflight and 24-hour recent-job dedupe.
- The `/goal get_first_customer` loop can recommend and queue these workflows as supporting no-side-effect strategy work.
- Optional Hermes/Argon skill sync now mirrors both `takyon-company-factory` and `takyon-business-marketing`.

Safety boundaries:
- No Meta campaign creation, upload, launch, pause, budget mutation, or spend.
- No email sending.
- No MachFive/BlueCraft vendor calls.
- No upstream GEO installer/update/uninstall scripts.
- No global `~/.claude`, `~/.geo-prospects`, or `~/.social-cli` writes.
- No new queue lanes were added; the workflows reuse existing safe lanes and are opt-in for new-company builds.

Verification:
- `npm run typecheck` passed.
- `npm run build` passed. Existing Turbopack NFT warning remains about dynamic filesystem tracing through `business-workspace.ts`.
- `node scripts/sync-argon-hermes-skills.mjs` synced the original company-factory skills plus the seven new business-marketing skills.
