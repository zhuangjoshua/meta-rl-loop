---
name: takyon-meta-ads-v2
description: Launch and operate a Meta ad for one business via Meta's official MCP — create/manage Campaign/AdSet/Ad, upload creative, set/change budget, pause, sync metrics, and evaluate good/bad with a recommended action. Test mode suppresses to local receipts.
version: 2.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, meta, facebook, instagram, ads, paid, distribution, mcp]
    related_skills: [ad-copy, creative-format-decision, ugc-video-ad, static-ad-creative-generator, takyon-distribution, takyon-business-metrics]
    requires_toolsets: [takyon, takyon-authority]
    requires_tools:
      [
        business_read_business,
        business_meta_ad_launch,
        business_meta_ad_control,
        business_meta_ad_insights_sync,
        business_meta_ad_evaluate,
        business_meta_ad_bind_manual_launch,
        business_meta_pixel_verify,
        business_meta_pixel_ensure,
      ]
    routing:
      owns: Meta ad creation/management via the official Meta Ads MCP — campaign/adset/ad CRUD, creative upload, budget set/change, pause, metrics sync, and good/bad evaluation, from an asset the creative layer already chose.
      when_to_use:
        - a chosen image or video asset needs to launch (or stage paused) as a bounded Meta campaign
        - an existing Meta campaign/adset/ad needs budget changed, pausing, or activation
        - delivery metrics need syncing into Takyon as an attributed time-series
        - the operator asks whether a campaign/adset/ad is performing well or poorly
        - Meta connection / ad-account preflight is needed before launch
      do_not_use_for:
        - choosing image vs video vs both (creative-format-decision skill)
        - writing ad copy (ad-copy skill)
        - producing the asset (ugc-video-ad / static-ad-creative-generator)
        - hard-deleting campaigns (MCP cannot; pause here, delete in Ads Manager UI)
  takyon:
    scope: business
    allowed_roots: [product, distribution, metrics]
    output_root: distribution
    publication:
      - distribution/meta-ads/<slug>/plan.json
      - distribution/meta-ads/<slug>/receipt.json
      - distribution/meta-ads/<slug>/actions/<idempotency>.json
      - metrics/meta-ads/<slug>/insights.jsonl
      - metrics/meta-ads/<slug>/syncs/<idempotency>.json
      - metrics/meta-ads/<slug>/evaluations/<idempotency>.json

required_environment_variables: [META_MCP_OAUTH_TOKEN, META_SYSTEM_USER_ACCESS_TOKEN, META_AD_ACCOUNT_ID]
required_credential_files: []
---

# Takyon Meta Ads v2

## Overview

Turn a chosen asset (image or video) + a copy bundle into a Meta **Campaign → AdSet → Ad** via the
official Ads MCP, under a reserved channel-credit cap, then manage, measure, and evaluate it. This skill
does not choose format, write copy, or generate assets.

## When to Use

- A chosen image/video asset needs to launch or stage paused as a bounded Meta campaign.
- An existing Meta object needs its budget changed, pausing, or activation.
- Delivery metrics need syncing as an attributed time-series, or the operator asks if an ad is good/bad.
- **Do not use for:** choosing format (creative-format-decision), writing copy (ad-copy), generating the
  asset (ugc-video-ad / static-ad-creative-generator), or hard-deleting (pause here; delete in Ads
  Manager UI).

## Quick Reference

- Primary roots: `distribution/`, `metrics/`
- Publication paths: `distribution/meta-ads/<slug>/{plan,receipt}.json`, `.../actions/<id>.json`;
  `metrics/meta-ads/<slug>/{insights.jsonl, syncs/<id>.json, evaluations/<id>.json}`
- Tools: `business_meta_ad_launch`, `business_meta_ad_control`, `business_meta_ad_insights_sync`,
  `business_meta_ad_evaluate`, `business_meta_ad_bind_manual_launch`; plus the lazy pixel tools
  `business_meta_pixel_verify` / `business_meta_pixel_ensure` (folded in — `TOOLS/pixel_handlers.py`)
- **Default campaign: Traffic → Website clicks** — `OUTCOME_TRAFFIC` / `LINK_CLICKS` / `WEBSITE`, CBO,
  `LOWEST_COST_WITHOUT_CAP`. Use this unless the business goal clearly requires another objective.

## Prerequisites

- Meta Ads MCP OAuth token **and** a system-user token (for `/advideos`) in Safebox;
  `META_AD_ACCOUNT_ID` resolvable. The tools resolve these in their authority route — never `os.environ`.
- A public asset URL available via `_stage_business_public_asset` (R2 `product-sites`).
- A **functional Meta pixel** on the business site — installed **lazily** by this skill's pixel tools
  (`business_meta_pixel_ensure`) at preflight, never at bootstrap. Required for conversion campaigns and
  any ROAS/CPA evaluation; Traffic can deliver without it but can't be judged on revenue.
- Tools registered (`metadata.hermes.requires_tools`). Live external effect is gated by creative
  credits; test-mode businesses suppress to local receipts.

## References

- `references/meta-mcp-tool-map.md` — capability → MCP tool.
- `references/campaign-options.md` — selectable options + the pinned Traffic/Website-clicks default.
- `references/benchmarks.md` — eval thresholds + verdict→action map.
- `references/pixel-attribution.md` — one shared pixel + per-business custom-conversion attribution.
- `references/pixel-health.md` — what proves a pixel is functional.

## Templates

- None. Creative and copy come from sibling skills.

## Scripts

- None. All real work lives in the `business_meta_*` tools (`plugins/takyon/core.py`); see `TOOLS/`.

## How to Run

- Start from canonical state: `business_read_business`, then `business_list_files distribution/meta-ads`
  and `metrics/meta-ads` to see prior launches/metrics. If the asset or copy is missing, stop and route
  to the creative/copy/format skills — do not invent them.
- **Pick options (default first):** `OUTCOME_TRAFFIC` + `LINK_CLICKS` + `WEBSITE` + CBO +
  `LOWEST_COST_WITHOUT_CAP`. Only deviate to Sales/Leads/etc. when the business goal requires it **and**
  the conversion plumbing exists (pixel / promoted_object). Budget from remaining channel credits.
- **Preflight:** `business_meta_ad_launch(preflight=true)` — confirms the MCP connection and lists
  ad accounts/pages, and checks the pixel via `business_meta_pixel_verify` (if not functional, run
  `business_meta_pixel_ensure` first). Conversion objectives **block** without a functional pixel; Traffic
  proceeds but warns (no ROAS/CPA judgment).
- **Launch/stage:** `business_meta_ad_launch` (inputs + defaults: `references/campaign-options.md`).
- **Control:** `business_meta_ad_control(action=set_budget|pause|activate)`.
- **Metrics:** `business_meta_ad_insights_sync`. **Evaluate:** `business_meta_ad_evaluate`.
- Real state is changed only by these `business_meta_*` tools. Before claiming launched, re-read
  `distribution/meta-ads/<slug>/receipt.json`.

## Procedure

1. Read business goal + existing `distribution/meta-ads` and `metrics/meta-ads` state.
2. Confirm the single asset file and copy bundle exist (from sibling skills); pass one explicit file.
3. Choose options: default Traffic→Website-clicks CBO; budget from remaining credits; staged paused
   unless launch intent is live.
4. **Pixel gate:** `business_meta_pixel_verify`; if not functional, `business_meta_pixel_ensure` (lazy
   install). Block conversion objectives until functional; for conversion campaigns pass the
   `custom_conversion_id` (from surface `metadata.meta_pixel`) into `promoted_object`.
5. `business_meta_ad_launch` (preflight first): creative via `image_url` or `/advideos`→`video_id`;
   create campaign→adset→ad; activate if live.
6. Test mode → write a truthful local receipt; no external calls.
7. Re-read `receipt.json` and report from it; if ids are missing say `blocked`/`attempted`, not `done`.
8. For metrics/eval: sync, then evaluate; outputs land under `metrics/meta-ads/<slug>/`.

## Output Format

- `distribution/meta-ads/<slug>/`: `plan.json`, `receipt.json`, `actions/<id>.json` — structured,
  machine-readable (Meta ids, status, mode, credit settlement).
- `metrics/meta-ads/<slug>/`: `insights.jsonl` (append-only time-series), `syncs/<id>.json`,
  `evaluations/<id>.json`.

## Publication

- `distribution/meta-ads/<slug>/plan.json`, `receipt.json`, `actions/<id>.json`
- `metrics/meta-ads/<slug>/insights.jsonl`, `syncs/<id>.json`, `evaluations/<id>.json`
- Live external state is proven by the real campaign/adset/ad ids recorded in `receipt.json`.

## Common Pitfalls

- MCP can't upload creative (image = `image_url`, video = `/advideos`→`video_id`) or hard-delete (pause
  only).
- CBO budget is on the campaign, ABO on the ad set — read `budget_mode` from the receipt before
  `set_budget`; don't infer from `level`.
- Don't claim launched without reading back `receipt.json`.
- Provider tokens come from Safebox, never `os.environ`.

## Verification Checklist

- [ ] `receipt.json` holds real campaign/adset/ad ids (or a truthful blocked/test receipt)
- [ ] creative resolved (preview, or `image_hash`/`video_id` present)
- [ ] `insights.jsonl` gained a row keyed by `level`+`object_id`+date
- [ ] every claimed side effect is backed by a receipt
- [ ] nothing written outside `distribution/` or `metrics/`

## Rules

1. Business-scoped only.
2. No fake launches or metrics — emit truthful blocked/test receipts instead.
3. All real state goes through a `business_meta_*` tool; add the tool (see `TOOLS/`) if one is missing.
4. Default to Traffic→Website-clicks unless the business goal requires another objective.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Account `UNSETTLED` / not queryable | Surface the reason; resolve billing in Ads Manager, then retry. |
| Video "not ready" on creative | Poll `/advideos` status, then retry the creative. |
| Delete requested | Pause via `business_meta_ad_control`; delete in Ads Manager UI. |
| No verified interest IDs | Use geo-only broad targeting. |
