---
name: takyon-meta-ads-v2
description: Launch and operate a Meta (Facebook/Instagram) ad for one business from a creative the sibling creative skills already produced. Media (image/video) is uploaded with a system-user Graph token; the Campaign/AdSet/Ad objects are created with the official Meta Ads MCP token; lifecycle (set budget, pause, activate), metrics sync, good/bad evaluation, and pixel verify/ensure run on top. Test mode suppresses every external call to a truthful local receipt. Never fabricate creative — route to ugc-video-ad or static-ad-creative-generator when the asset is missing.
version: 2.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, meta, facebook, instagram, ads, paid, distribution, mcp, graph, pixel]
    related_skills: [ugc-video-ad, static-ad-creative-generator, takyon-reddit-ads, takyon-distribution, takyon-business-metrics]
    requires_toolsets: [takyon, takyon-authority]
    requires_tools:
      [
        business_read_business,
        business_meta_ad_launch,
        business_meta_ad_control,
        business_meta_ad_insights_sync,
        business_meta_ad_evaluate,
        business_meta_pixel_verify,
        business_meta_pixel_ensure,
      ]
    routing:
      owns: Meta ad launch and lifecycle for one business via the hybrid system-user-Graph (media upload) + official-MCP (ad objects) path — campaign/adset/ad create, budget set/change, pause, activate, delivery-metrics sync, good/bad evaluation, and per-business pixel verify/ensure, from a creative the creative layer already produced.
      when_to_use:
        - a finished image or video creative under product/ needs to launch (or stage paused) as a bounded Meta campaign
        - an existing Meta campaign/adset/ad needs its daily budget changed, pausing, or activation
        - Meta delivery metrics need syncing into Takyon as a durable time-series
        - the operator asks whether a Meta campaign/adset/ad is performing well or poorly and what to do
        - the shared Meta pixel + per-business custom conversion needs verifying or lazily ensuring before a conversion launch
      do_not_use_for:
        - choosing image vs video or writing the ad copy (creative-format / ad-copy skills)
        - producing the creative asset itself (ugc-video-ad / static-ad-creative-generator)
        - inventing placeholder, mock, or stub media just to force a launch through
        - hard-deleting campaigns (the MCP cannot delete; pause here, delete in Ads Manager UI)
        - claiming CAC, ROAS, or conversion attribution Takyon has not joined truthfully
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
      - metrics/meta-pixel/<slug>/preflight.json
      - metrics/meta-pixel/<slug>/ensure.json

required_environment_variables: [META_MCP_OAUTH_TOKEN, META_SYSTEM_USER_ACCESS_TOKEN, META_AD_ACCOUNT_ID, META_PAGE_ID]
required_credential_files: []
---

# Takyon Meta Ads v2

## Overview

Turn a finished creative (image or video) plus a copy bundle into a Meta **Campaign → AdSet → Ad** for one
business, then manage, measure, and evaluate it under a reserved channel-credit cap.

This skill runs a deliberate **hybrid two-token path** because each token can do exactly what the other
cannot:

- **Media upload — system-user Graph token** (`META_SYSTEM_USER_ACCESS_TOKEN`). The generated creative bytes
  are read out of the workspace store and pushed to the Marketing API: a video goes to
  `act_<id>/advideos` (polled until `status.video_status == "ready"`) and an image goes to
  `act_<id>/adimages`. This is an ordinary Graph token; the MCP **rejects** it.
- **Ad objects — official Meta Ads MCP token** (`META_MCP_OAUTH_TOKEN`, endpoint
  `https://mcp.facebook.com/ads`). The MCP creates the creative + campaign + ad set + ad objects. It is
  restricted to the MCP and is **rejected** by the Graph API.

**Why the split exists:** the business's own Meta app is in **Development mode**, so the system-user token
cannot create an ad *creative* (Meta error `100` / subcode `1885183`, "app in development mode"). The MCP
runs on an **approved/Live** app, so it creates the creative and ad with no such block. The two token types
are disjoint and proven so live — a real Facebook video ad was posted end-to-end through this exact path.

The creative itself is **never produced here**. It comes from the sibling `ugc-video-ad` (→
`product/ugc-ads/<slug>/ad.mp4`) and `static-ad-creative-generator` (→
`product/static-ads/<slug>/<creative_id>.png`) skills. If the requested asset does not exist yet, route
upstream first; never substitute a placeholder, mock, or stub.

Three layers stay separate, mirroring the Reddit skill:

- **Creative layer** — the image/video bytes under `product/`, owned by the upstream creative skills.
- **Launch/control layer** — the Campaign/AdSet/Ad objects + explicit activate/pause/budget changes, owned
  here under `distribution/`, run through `business_meta_ad_launch` and `business_meta_ad_control`.
- **Metrics layer** — Meta delivery metrics only (no business attribution), owned here under `metrics/`, run
  through `business_meta_ad_insights_sync` and judged by `business_meta_ad_evaluate`.

The **launch** tool owns the bounded spend policy: it reserves the remaining Meta channel credits, uploads
the media on the Graph leg, creates the four ad objects on the MCP leg, leaves everything **PAUSED**, and
only activates (campaign → ad set → ad) when `mode: "live"` and the business is not test-mode. Two extra
facts to remember: the MCP **cannot hard-delete** (it forces `DELETED`/`ARCHIVED` to `PAUSED`), and a
**video creative also needs a thumbnail** image hash/url, so a thumbnail is uploaded alongside the video.

## When to Use

- A finished image or video creative exists under `product/` and the operator wants it launched, or
  intentionally staged paused, as a bounded Meta campaign.
- An existing Meta campaign/adset/ad needs its daily budget changed, pausing, or activation.
- Meta delivery metrics need syncing into Takyon as a durable time-series.
- The operator asks whether a campaign/adset/ad is performing well or poorly and what action to take.
- The shared Meta pixel + per-business custom conversion needs verifying, or lazily ensuring before a
  conversion-objective launch.

**Do not use for:** choosing format (creative-format-decision); writing copy (ad-copy); producing the
creative (ugc-video-ad / static-ad-creative-generator); substituting placeholder, mock, or stub media for a
missing creative; hard-deleting (pause here, delete in Ads Manager UI); or claiming CAC/ROAS/conversion
attribution without a truthful join.

## Quick Reference

- Primary roots: `distribution/`, `metrics/`
- Publication paths:
  `distribution/meta-ads/<slug>/plan.json`,
  `distribution/meta-ads/<slug>/receipt.json`,
  `distribution/meta-ads/<slug>/actions/<idempotency>.json`,
  `metrics/meta-ads/<slug>/insights.jsonl`,
  `metrics/meta-ads/<slug>/syncs/<idempotency>.json`,
  `metrics/meta-ads/<slug>/evaluations/<idempotency>.json`,
  `metrics/meta-pixel/<slug>/preflight.json`,
  `metrics/meta-pixel/<slug>/ensure.json`
- Tools used by this skill:
  **`business_meta_ad_launch`** (hybrid Graph-upload + MCP-create, reserved-credit launch),
  **`business_meta_ad_control`** (`set_budget` | `pause` | `activate`),
  **`business_meta_ad_insights_sync`** (delivery-metrics sync),
  **`business_meta_ad_evaluate`** (good/bad verdict + recommended action),
  **`business_meta_pixel_verify`** / **`business_meta_pixel_ensure`** (shared pixel + per-business custom
  conversion)
- Upstream creative: `product/ugc-ads/<slug>/ad.mp4` (video) or
  `product/static-ads/<slug>/<creative_id>.png` (image), produced by the sibling creative skills — the
  launch handler reads those bytes directly.
- **Two tokens, disjoint:** media upload uses the **system-user Graph** token; ad objects use the **MCP**
  token. Neither token works on the other surface.
- **Default campaign — Traffic → Website clicks:** `objective=OUTCOME_TRAFFIC`,
  `optimization_goal=LINK_CLICKS`, `billing_event=IMPRESSIONS`, `bid_strategy=LOWEST_COST_WITHOUT_CAP`,
  `call_to_action_type=LEARN_MORE`, Facebook-only placement default, geo-only broad targeting, staged paused
  unless launch intent is live. Deviate only when the business goal requires it.
- Safety: a stable `idempotency_key` is **required** on every tool; live spend cannot exceed the reserved
  Meta channel credits; test-mode businesses never call Meta; the MCP cannot hard-delete (pause only); video
  creatives require a thumbnail.

## Prerequisites

- The Takyon toolset must be available and the seven tools above must be registered (gated in frontmatter
  `metadata.hermes.requires_tools`).
- **Two Safebox secrets**, resolved authority-side at call time (never `os.environ`, never hardcoded):
  - **`META_SYSTEM_USER_ACCESS_TOKEN`** — non-expiring system-user token for the Graph media upload and
    lifecycle (`/advideos`, `/adimages`, status/budget updates). Works in Development mode for your own ad
    account, so no App Review is needed for the Graph leg.
  - **`META_MCP_OAUTH_TOKEN`** — the official Meta Ads MCP token (approved-client PKCE, ~60-day, requires
    browser re-consent to renew — there is no non-expiring MCP token). Used to create the ad objects.
- **`META_AD_ACCOUNT_ID`** and **`META_PAGE_ID`** resolvable (config or launch args); optional
  `META_INSTAGRAM_ID` enables Instagram delivery.
- A **finished creative** under `product/`: `product/ugc-ads/<slug>/ad.mp4` for video or
  `product/static-ads/<slug>/<creative_id>.png` for image, produced by the sibling creative skills. If it
  does not exist yet, route upstream first — do not invent it.
- A **functional Meta pixel** for any conversion objective or any ROAS/CPA evaluation. The pixel is shared
  across all sites and installed **lazily** by this skill's pixel tools (`business_meta_pixel_ensure`) at
  preflight, never at bootstrap; per-business isolation comes from a URL-rule **custom conversion**. Traffic
  can deliver without it but cannot be judged on revenue.
- Live external effect is gated by reserved creative credits; test-mode businesses suppress every external
  call to a truthful local receipt.

## References

- [references/meta-mcp-tool-map.md](references/meta-mcp-tool-map.md) — capability → official MCP tool, and
  the not-supported delete/archive note.
- [references/campaign-options.md](references/campaign-options.md) — selectable campaign/adset/ad values and
  the pinned Traffic → Website-clicks default.
- [references/benchmarks.md](references/benchmarks.md) — evaluation thresholds, the learning/fatigue rules,
  and the verdict → recommended-action map used by `business_meta_ad_evaluate`.
- [references/pixel-attribution.md](references/pixel-attribution.md) — one shared pixel + per-business
  custom-conversion attribution model.
- [references/pixel-health.md](references/pixel-health.md) — the two independent proofs that a pixel is
  actually functional.

## Templates

- None. Creative and copy come from the sibling `ugc-video-ad`, `static-ad-creative-generator`, and ad-copy
  skills; this skill consumes their output and never authors a creative or copy template.

## Scripts

- None. All real work lives in the `business_meta_*` tools (`plugins/takyon/meta_ads_v2.py`), with the Graph
  leg in `plugins/takyon/meta_graph.py` and the MCP leg in `plugins/takyon/meta_mcp.py`. Do not shell out.

## How to Run

- **Start from canonical state.** Call `business_read_business` first to confirm the business id and its
  **mode** (test vs live). Then `business_list_files distribution/meta-ads` and `metrics/meta-ads` to see
  prior launches and metrics. If the creative or copy is missing, stop and route to the creative/copy
  skills — do not invent them.
- **Confirm the creative exists.** The launch handler reads the bytes directly from the workspace store
  (`product/ugc-ads/<slug>/ad.mp4` for video, `product/static-ads/<slug>/<creative_id>.png` for image). If
  the file is not there, route upstream to `ugc-video-ad` (video) or `static-ad-creative-generator` (image)
  in the same flow; never point the launch at a placeholder, mock, or stub.
- **Pick options (default first):** `OUTCOME_TRAFFIC` + `LINK_CLICKS` + `IMPRESSIONS` billing +
  `LOWEST_COST_WITHOUT_CAP` + `LEARN_MORE` CTA + Facebook-only placement + geo-only broad targeting. Only
  deviate to Sales/Leads/etc. when the business goal requires it **and** the conversion plumbing (functional
  pixel / `promoted_object`) exists. Budget derives from remaining channel credits.
- **Pixel gate (conversion objectives only):** `business_meta_pixel_verify`; if it is not functional, run
  `business_meta_pixel_ensure` to lazily install the shared pixel and the per-business custom conversion.
  Conversion objectives **block** without a functional pixel; Traffic proceeds but warns (no ROAS/CPA
  judgment).
- **Launch / stage:** `business_meta_ad_launch` with a stable `idempotency_key` and `mode`
  (`paused` default | `live`). The tool reserves the credit cap, **uploads the media on the Graph leg**
  (video → `/advideos` polled to ready, plus a thumbnail; image → `/adimages`), then **creates the four ad
  objects on the MCP leg** (creative → campaign → ad set → ad), all PAUSED. It activates campaign → ad set →
  ad only when `mode: "live"` and the business is not test-mode.
- **Control:** `business_meta_ad_control` with `action=set_budget` (Graph `update_daily_budget`),
  `action=pause`, or `action=activate` (Graph `set_status`). Each control action writes
  `distribution/meta-ads/<slug>/actions/<idempotency>.json`.
- **Metrics:** `business_meta_ad_insights_sync` appends a delivery snapshot under `metrics/meta-ads/<slug>/`.
  **Evaluate:** `business_meta_ad_evaluate` reads the insights and writes a verdict + recommended action.
- The tools are **idempotent** on `idempotency_key`: a retry with the same key returns the existing receipt
  instead of creating duplicate Meta objects. Real state is changed only by these `business_meta_*` tools;
  before claiming launched, **re-read** `distribution/meta-ads/<slug>/receipt.json` and report from it.

## Procedure

1. **Read business state** — `business_read_business`. Note the business `mode`. Read existing
   `distribution/meta-ads` and `metrics/meta-ads` state so a retry is recognized. Then read the
   ROAS run history at `metrics/roas/meta.md` (if present): one entry per past campaign sync
   recording the creative that ran (kind, headline, copy, CTA, budget) and what it measurably
   returned (purchases, attributed revenue, ROAS). Favor the creative approaches that scored the
   highest ROAS and do not repeat ones that measurably failed — the history is receipts, not
   narrative; an absent or empty file just means no measured runs yet.
2. **Confirm the creative source** — verify the real asset exists at `product/ugc-ads/<slug>/ad.mp4`
   (video) or `product/static-ads/<slug>/<creative_id>.png` (image). If not, route upstream to the
   creative skill in the same flow; never fabricate placeholder media.
3. **Pixel gate** — for any conversion objective, call `business_meta_pixel_verify`; if not functional,
   `business_meta_pixel_ensure` (lazy install of the shared pixel + the per-business custom conversion).
   Block conversion objectives until functional; pass the resulting `custom_conversion_id` into
   `promoted_object`. Traffic does not require the pixel but cannot be judged on revenue without it.
4. **Draft `plan.json`** under `distribution/meta-ads/<slug>/`: objective and options (default Traffic →
   Website-clicks), `link_url`, `message` (primary text), `headline`, `call_to_action_type`, daily budget
   (derived from reserved credits unless the operator chose a pace), and `mode`.
5. **Launch** — `business_meta_ad_launch` with the plan fields, a stable `idempotency_key`, and `mode`. The
   tool uploads the media on the Graph leg, creates creative → campaign → ad set → ad on the MCP leg, and
   leaves them PAUSED. Expect `status: "created_paused"`, or `status: "activated"`/`"live"` only when
   `mode: "live"` and not test-mode.
6. **Test mode** — if the business is test-mode, the tool makes **no external calls** and writes a truthful
   local receipt with `status: "test_receipt"`. Do not claim a live launch.
7. **Read back** — re-read `distribution/meta-ads/<slug>/receipt.json` and report from it. If the real
   campaign/adset/ad ids are missing, say `blocked`, `attempted`, or `partial_failed` — never `done`.
8. **Control if needed** — `business_meta_ad_control` (`set_budget` | `pause` | `activate`) using the launch
   slug; each writes an `actions/<idempotency>.json`.
9. **Metrics and evaluation** — `business_meta_ad_insights_sync` to persist delivery metrics, then
   `business_meta_ad_evaluate` to write a verdict + recommended action under `metrics/meta-ads/<slug>/`.
10. **Keep state truthful** — if a live step fails after some objects were created (e.g. media uploaded and
    creative made but the ad failed), the tool writes a repair-able receipt recording the ids that already
    exist. Surface that blocker; do not claim success.

## Output Format

- `distribution/meta-ads/<slug>/plan.json` — the structured launch input (objective, options, copy,
  budget, mode).
- `distribution/meta-ads/<slug>/receipt.json` — the tool-written truth of the launch: `status`
  (`created_paused` | `activated` | `live` | `partial_failed` | `test_receipt` | `blocked_*`), `mode`, the
  real Meta object ids (`video_id`/`image_hash`, `creative_id`, `campaign_id`, `adset_id`, `ad_id`), the
  budget, and the credit settlement.
- `distribution/meta-ads/<slug>/actions/<idempotency>.json` — the tool-written truth of each control action
  (`set_budget`, `pause`, `activate`).
- `metrics/meta-ads/<slug>/insights.jsonl` — append-only delivery time-series, deduped and keyed by
  `level` + `object_id` + date.
- `metrics/meta-ads/<slug>/syncs/<idempotency>.json` — the tool-written truth of each metrics sync.
- `metrics/meta-ads/<slug>/evaluations/<idempotency>.json` — the verdict + recommended action from
  `business_meta_ad_evaluate`.
- `metrics/meta-pixel/<slug>/preflight.json` — the tool-written truth of `business_meta_pixel_verify`
  (snippet + per-business custom-conversion proofs and the `ok` verdict).
- `metrics/meta-pixel/<slug>/ensure.json` — the tool-written truth of `business_meta_pixel_ensure`
  (the ensured per-business custom conversion id on the shared pixel).

Structured machine-readable artifacts (Meta ids, status, mode, credit settlement, metrics) live in the
`*.json`/`*.jsonl` files above; this skill writes no prose artifacts.

## Publication

- This skill publishes to the canonical directory **`distribution/meta-ads/<slug>/`** inside the current
  business (`plan.json`, `receipt.json`, `actions/<idempotency>.json`).
- Metrics publication lives under **`metrics/meta-ads/<slug>/`** (`insights.jsonl`,
  `syncs/<idempotency>.json`, `evaluations/<idempotency>.json`).
- Pixel publication lives under **`metrics/meta-pixel/<slug>/`** (`preflight.json` from
  `business_meta_pixel_verify`, `ensure.json` from `business_meta_pixel_ensure`).
- Live external Meta state is proven only by the real campaign/adset/ad ids recorded in `receipt.json` —
  never by hand-written success text.

## Common Pitfalls

- **Mixing the two tokens.** The system-user Graph token is rejected by the MCP, and the MCP token is
  rejected by the Graph API. Media upload must use the Graph token; ad-object creation must use the MCP
  token. Crossing them fails.
- **Trying to create the creative with the system-user token.** The business app is in Development mode, so
  the Graph token cannot create the ad creative (error `100`/`1885183`). The creative is created on the MCP
  leg — that is the whole reason the MCP token exists in this flow.
- **Forgetting the video thumbnail.** A video creative needs a thumbnail image hash/url in addition to the
  `video_id`; without it, creative creation fails.
- **Treating PAUSED as live.** A launch is PAUSED unless `mode: "live"` and the business is not test-mode.
  Do not report a paused or test receipt as a live delivery.
- **Expecting hard-delete.** The MCP forces `DELETED`/`ARCHIVED` to `PAUSED` (`status_forced_to_paused:
  true`). Pause via `business_meta_ad_control`; actually delete in Ads Manager UI.
- **Inventing creative.** A missing asset is a route-upstream signal, never a license to use placeholder,
  mock, or stub media.
- **Reading tokens from `os.environ` or hardcoding ids.** Tokens and ids resolve via Safebox in the
  authority route at call time.
- **Claiming attribution from delivery metrics alone.** Ad-platform metrics are not the same as business
  CAC/ROAS; only the per-business custom conversion + truthful joins support that.

## Verification Checklist

- [ ] The referenced creative is a real asset under `product/ugc-ads/<slug>/` or
      `product/static-ads/<slug>/`, not a placeholder/mock/stub
- [ ] `distribution/meta-ads/<slug>/plan.json` exists and matches the intended launch shape
- [ ] `distribution/meta-ads/<slug>/receipt.json` exists and truthfully reflects `created_paused`,
      `activated`/`live`, `partial_failed`, `test_receipt`, or an exact `blocked_*` status
- [ ] On a non-test live launch, the receipt holds the real `video_id`/`image_hash`, `creative_id`,
      `campaign_id`, `adset_id`, and `ad_id`
- [ ] Any `set_budget`/`pause`/`activate` action has a matching
      `distribution/meta-ads/<slug>/actions/<idempotency>.json`
- [ ] Metrics sync wrote both `metrics/meta-ads/<slug>/insights.jsonl` (deduped, keyed by
      `level`+`object_id`+date) and `metrics/meta-ads/<slug>/syncs/<idempotency>.json`
- [ ] An evaluation request produced `metrics/meta-ads/<slug>/evaluations/<idempotency>.json` with a verdict
      and recommended action
- [ ] Nothing was written outside `distribution/` or `metrics/`

## Rules

1. Keep all work business-scoped.
2. Never claim a live Meta launch, control action, or metric without the corresponding receipt; emit a
   truthful `test_receipt`, `blocked_*`, or `partial_failed` receipt instead.
3. Media upload uses the system-user Graph token; ad objects use the MCP token — never cross them, never
   read tokens from `os.environ`, never hardcode tokens or ids.
4. Never fabricate creative; route to `ugc-video-ad` / `static-ad-creative-generator` when the asset is
   missing.
5. A launch is PAUSED unless `mode: "live"` and the business is not test-mode.
6. The MCP cannot hard-delete; pause here and delete in Ads Manager UI.
7. Default to Traffic → Website-clicks unless the business goal requires another objective, and only run a
   conversion objective with a functional pixel + custom conversion.
8. Ad-platform delivery metrics are not business attribution.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| Graph error `100` / subcode `1885183` "app in development mode" on creative | Expected — the system-user token cannot create the creative. The creative is created on the MCP leg; confirm `META_MCP_OAUTH_TOKEN` is set. |
| MCP rejects the system-user token (or Graph rejects the MCP token) | The token types are disjoint. Use the system-user token only for `/advideos`, `/adimages`, and status/budget; use the MCP token only for `ads_create_*`. |
| Video "not ready" on creative | The launch handler polls `GET /<video_id>?fields=status` until `status.video_status == "ready"`; if it stays not-ready, surface the blocker and retry. |
| Video creative missing thumbnail | Provide or stage a thumbnail; a video creative needs an `image_hash`/`image_url` thumbnail alongside the `video_id`. |
| `META_MCP_OAUTH_TOKEN` expired (~60-day) | Re-run the approved-client PKCE OAuth (see `implementation/AUTONOMOUS-PROD.md`) to mint a fresh token and store it in Safebox. |
| Account `UNSETTLED` / not queryable | Surface the reason; resolve billing in Ads Manager, then retry. |
| Delete requested | Pause via `business_meta_ad_control`; actually delete in Ads Manager UI (the MCP forces delete/archive to PAUSED). |
| Conversion objective blocked | Run `business_meta_pixel_ensure` to install the shared pixel + per-business custom conversion, then pass `custom_conversion_id` into `promoted_object`. |
| No verified interest IDs | Use geo-only broad targeting; never invent numeric interest IDs. |
