---
name: takyon-reddit-ads
description: Launch and operate a Reddit ad for one Takyon business through Reddit's open Ads API from an existing promoted post, a public creative URL, or a local business image/video asset that Takyon can stage onto the business publish target first — preflight auth/defaults, create a PAUSED Campaign/Ad Group/Post/Ad, then explicitly activate/pause/update budget and sync ad-platform metrics through guarded tools. Test-mode businesses suppress to local receipts.
version: 1.0.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, reddit, ads, paid, distribution, image, video, carousel]
    related_skills: [ugc-video-ad, static-ad-creative-generator, takyon-distribution, takyon-business-metrics, takyon-meta-ads]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_reddit_ad_launch,
        business_reddit_ad_control,
        business_reddit_ad_insights_sync,
      ]
    routing:
      owns: Reddit ad launch staging, explicit control, and ad-platform metrics sync from an existing promoted post, a public creative URL, or a local business creative asset staged onto the business publish target
      when_to_use:
        - a finished ad creative needs to be staged as a paused Reddit campaign
        - a finished local business image or video asset under `product/` needs to be turned into a Reddit promoted post safely
        - Reddit auth/default discovery is needed before launch work
        - a launched Reddit ad needs to be activated, paused, or have its daily budget updated
        - Reddit delivery metrics need to be synced into Takyon for later tracking
      do_not_use_for:
        - building the image or video asset itself
        - claiming CAC, ROAS, or conversion attribution Takyon has not joined truthfully
  takyon:
    scope: business
    allowed_roots: [product, distribution, metrics]
    output_root: distribution
    publication:
      - product/public-assets/<slug>/receipt.json
      - distribution/reddit-ads/<slug>/plan.json
      - distribution/reddit-ads/<slug>/receipt.json
      - distribution/reddit-ads/<slug>/actions/<idempotency>.json
      - metrics/reddit-ads/<slug>/insights.jsonl
      - metrics/reddit-ads/<slug>/syncs/<idempotency>.json

required_environment_variables:
  [REDDIT_ADS_CLIENT_ID, REDDIT_ADS_CLIENT_SECRET, REDDIT_ADS_REFRESH_TOKEN]
required_credential_files: []
---

# Takyon Reddit Ads

## Overview

Turn an existing promoted post, a public hosted image/video/carousel, or a local business creative file into a **paid Reddit ad** for one business.
This skill is the **distribution** half of the creative pipeline: upstream creative skills can still produce
assets under `product/`, and this skill can stage those local files onto the business publish target when needed.
Reddit’s live API path still ultimately needs either:

- an existing `post_id`, or
- publicly reachable creative URLs the Reddit Ads API can reference when creating a promoted post.

Per Reddit's current Ads API docs, the **Reddit Ads API is open to all developers and does not require
allowlisting or approval from Reddit to access**. The real live gates are:

- an OAuth2 developer application plus access/refresh tokens
- a real business + ad account the token holder can access
- a usable profile, funding instrument, and pixel for live launch
- a unique User-Agent string so Reddit does not heavily throttle generic clients

Three hard layers stay separate:

- **Asset / post layer** — the existing promoted post id or the public creative URLs used to create one.
  Owned by upstream creative work and the business publish target. This skill does not regenerate assets, but it can stage a finished local business asset into a public URL when the publish target is reachable.
- **Launch/control layer** — the campaign objects + explicit activate/pause/budget changes.
  Owned here, lives under `distribution/`, and runs through the guarded
  **`business_reddit_ad_launch`** and **`business_reddit_ad_control`** tools.
- **Metrics layer** — ad-platform delivery metrics only. Owned here, lives under `metrics/`,
  and runs through **`business_reddit_ad_insights_sync`**.

The **launch** tool never activates anything; it only creates `PAUSED` objects. Activation,
pausing, and daily-budget changes are explicit follow-up control actions. Insights sync records
Reddit delivery metrics like spend, impressions, clicks, CTR, CPC, and CPM, but it does **not**
invent business attribution.

## When to Use

- A business has a public ad creative URL, a finished local business image/video asset, or an existing `post_id` and wants it staged as a Reddit ad.
- You need to verify Reddit auth, business/ad-account/profile defaults, funding instruments, or pixels before launch work.
- You want to stage a campaign safely (`PAUSED`) for review before it ever serves.
- You need to explicitly activate, pause, or update the daily budget of a launched Reddit campaign.
- You want to sync delivery metrics from Reddit back into Takyon for future tracking.

**Do not use for:** building the creative asset itself; hiding missing funding/pixel/profile setup; claiming conversions or ROAS without truthful joins.

## Access Model

Use this skill on the assumption that **there is no separate Reddit-side Ads API verification queue** for normal access.
If live work fails, treat the likely blockers as:

- missing OAuth app or refresh token
- wrong business / ad-account permissions on the authenticating Reddit user
- missing profile, funding instrument, or pixel
- trying to auto-stage local files onto a publish target that is not actually public/reachable yet

Do **not** treat "wait for API approval" as the default explanation unless Reddit changes its official docs.

## Quick Reference

- Primary root: `distribution/`
- Publication paths:
  `product/public-assets/<slug>/receipt.json`,
  `distribution/reddit-ads/<slug>/plan.json`,
  `distribution/reddit-ads/<slug>/receipt.json`,
  `distribution/reddit-ads/<slug>/actions/<idempotency>.json`,
  `metrics/reddit-ads/<slug>/insights.jsonl`,
  `metrics/reddit-ads/<slug>/syncs/<idempotency>.json`
- Tools used by this skill:
  **`business_reddit_ad_launch`** (preflight + PAUSED launch),
  **`business_reddit_ad_control`** (activate, pause, set_budget),
  **`business_reddit_ad_insights_sync`** (delivery metrics sync)
- Upstream assets: an existing promoted `post_id`, a public image/video/carousel URL bundle, or a local business image/video bundle under `product/` that Takyon can stage to `product/public-assets/`
- Safety: launch always creates `PAUSED`; `daily_budget_usd` is capped by
  `TAKYON_REDDIT_MAX_DAILY_BUDGET_USD` (default 50); test-mode businesses never call Reddit; metrics sync is ad-platform only and does not imply business attribution.

## Best Live Path

The smallest truthful end-to-end Reddit path is:

1. **Create a Reddit Ads developer application** and complete OAuth2 so Takyon has a refresh token.
2. **Run preflight** with `business_reddit_ad_launch` `mode: "preflight"` to discover the business, ad account, profile, funding instrument, and pixel that the token can actually use.
3. **Prepare the creative upstream** with `ugc-video-ad`, `static-ad-creative-generator`, or an existing promoted post.
4. **If reusing an existing post**, launch with `asset_kind: "existing_post"` plus `post_id`.
5. **If creating a new promoted post**, either provide public media URLs directly or point the `post` block at local business files (`image_path`, `video_path`, `media_path`, `thumbnail_path`) so Takyon can stage them onto the business publish target first.
6. **Launch PAUSED** through `business_reddit_ad_launch`.
7. **Activate explicitly** with `business_reddit_ad_control` only after review.
8. **Sync delivery metrics** with `business_reddit_ad_insights_sync`.
9. **If live staging fails on reachability**, read the `product/public-assets/<slug>/receipt.json` blocker and fix the publish target instead of pretending Reddit accepted a private file.

## Prerequisites

- The Takyon toolset must be available; **`business_reddit_ad_launch`**,
  **`business_reddit_ad_control`**, and **`business_reddit_ad_insights_sync`** must be registered
  (gated in frontmatter `metadata.hermes.requires_tools`).
- **Live launch / preflight credentials** via env-backed Safebox values or a saved local state at
  `$TAKYON_HOME/secrets/reddit_ads.json`:
  - **`REDDIT_ADS_CLIENT_ID`**
  - **`REDDIT_ADS_CLIENT_SECRET`**
  - **`REDDIT_ADS_REFRESH_TOKEN`** or a still-valid `REDDIT_ADS_ACCESS_TOKEN`
  - Optional defaults: `REDDIT_ADS_BUSINESS_ID`, `REDDIT_ADS_ACCOUNT_ID`,
    `REDDIT_ADS_PROFILE_ID`, `REDDIT_ADS_FUNDING_INSTRUMENT_ID`, `REDDIT_ADS_PIXEL_ID`,
    `REDDIT_ADS_USER_AGENT`
- **A funded ad account, profile, and pixel** for live delivery. Preflight shows these defaults and blockers.
- **No separate Ads API allowlist step is assumed.** Live failures should be debugged as auth/permission/setup issues first.
- **An existing `post_id`, public creative URLs, or local business creative files that can be staged onto a publicly reachable business publish target** for live image/video/carousel post creation.
- **A unique Reddit User-Agent string**. Generic user agents are throttled much harder by Reddit.
- **Test-mode businesses need none of these** — the tool suppresses to a local receipt.

## References

- [references/reddit-ads-framework.md](references/reddit-ads-framework.md) — the Reddit object model,
  required live inputs, rate limits, budget semantics, and the PAUSED / budget-cap / pixel rails.

## Templates

- [templates/plan.json](templates/plan.json) — the structured launch input passed to `business_reddit_ad_launch`.

## How to Run

- Call `business_read_business` first to confirm the business id, its **mode** (test vs live), and the current creative/post source.
- **Preflight (read-only, no objects):** call `business_reddit_ad_launch` with `mode: "preflight"`. It returns the Reddit identity, businesses, ad accounts, profiles, funding instruments, pixels, and discovered defaults. Run this before any launch.
- **Draft the plan:** write `distribution/reddit-ads/<slug>/plan.json` from
  [templates/plan.json](templates/plan.json) with the campaign/ad-group/ad blocks and either:
  - `asset_kind: "existing_post"` plus `post_id`, or
  - `asset_kind: "image" | "video" | "carousel"` plus either public URLs under the `post` block or local business file paths (`image_path`, `video_path`, `media_path`, `thumbnail_path`) that Takyon can stage first.
  For new promoted posts, the plan can also carry copy fields such as `headline`, `display_url`,
  `call_to_action`, and `supplementary_text`. If `post.destination_url` / `ad.click_url` is omitted,
  Takyon defaults the click destination to the business's canonical product URL.
  The launch tool itself already handles the normal Reddit object sequence:
  Campaign → Ad Group → optional Post → Ad.
- **Launch (always PAUSED first):** call `business_reddit_ad_launch` with `mode: "launch"` and a stable `idempotency_key`.
  In **test mode** the tool writes a suppressed `receipt.json` and calls Reddit **not at all**.
  In **live mode** it creates Campaign → Ad Group → optional Post → Ad, all **PAUSED**, then
  writes `receipt.json` with the real IDs.
- **Public asset staging receipts:** when local files are used, the tool also writes canonical asset receipts under `product/public-assets/<slug>/receipt.json`.
- **Control (explicit):** when the operator wants the ad live, call
  `business_reddit_ad_control` using the launch slug/receipt. `activate` flips the staged objects live,
  `pause` stops them again, and `set_budget` updates the staged daily budget under the same cap.
- **Metrics sync:** call `business_reddit_ad_insights_sync` on the launch slug/receipt to write
  a durable delivery snapshot under `metrics/reddit-ads/<slug>/`.
- The tools are **idempotent** on `idempotency_key`: a retry with the same key returns the existing
  receipt instead of creating duplicate Reddit objects.
- Each tool records its own durable truth (event + receipt/snapshot). Do not hand-write success.

## Procedure

1. **Read business state** — `business_read_business`. Note the business `mode`. Confirm whether the truthful live source is an existing `post_id`, a public creative URL bundle, or a local business asset under `product/`.
2. **Preflight the account** — call `business_reddit_ad_launch` `mode: "preflight"`. Verify it returns the right ad account, profile, funding instrument, and pixel. If it errors with a missing-credential or missing-default message, record the blocker and stop.
3. **Confirm the creative source is actually launchable** — either a real `post_id`, public media URLs, or local business files that the launch tool can stage to a reachable publish target. In live mode, if the publish target is not actually reachable, stop on that blocker.
4. **Draft `plan.json`** under `distribution/reddit-ads/<slug>/` from the template: choose the objective, a `daily_budget_usd` within the cap, targeting, ad copy, and either `post_id`, public media URLs, or local staged-file inputs on the `post` block.
5. **Launch PAUSED** — call `business_reddit_ad_launch` `mode: "launch"` with the plan fields and a stable `idempotency_key`.
   - **Test mode** → expect `status: "suppressed_test_mode"` and a local `receipt.json`; no Reddit objects exist.
   - **Live mode** → expect `status: "created_paused"` with `ids` for campaign/ad_group/post/ad and a `receipt.json` with those IDs.
6. **Control if needed** — if the operator wants the ad live, call
  `business_reddit_ad_control` with `operation: "activate"` and the same slug. To stop it, use
  `operation: "pause"`. To change pace, use `operation: "set_budget"` with `daily_budget_usd`.
7. **Sync metrics** — call `business_reddit_ad_insights_sync` with `level: "campaign"` (or `ad_group` / `ad`) to persist Reddit delivery metrics under `metrics/reddit-ads/<slug>/`.
8. **Keep state truthful** — if a live step fails after some objects were created, the tool writes a `partial_failed` receipt with the IDs that exist. Surface that blocker; do not claim success.

## Output Format

- `distribution/reddit-ads/<slug>/plan.json` — the structured launch input.
- `distribution/reddit-ads/<slug>/receipt.json` — the tool-written truth of the launch:
  `status` (`suppressed_test_mode` | `created_paused` | `partial_failed`), `paused`, the
  Reddit object `ids` (live), the budget, and the funding/pixel/profile defaults used.
- `product/public-assets/<slug>/receipt.json` — written when local business files were staged into public asset URLs for the launch path.
- `distribution/reddit-ads/<slug>/actions/<idempotency>.json` — the tool-written truth of each
  control action (`activate`, `pause`, `set_budget`).
- `metrics/reddit-ads/<slug>/syncs/<idempotency>.json` and `metrics/reddit-ads/<slug>/insights.jsonl`
  — the tool-written truth of each metrics sync.

## Publication

- This skill publishes to the canonical directory **`distribution/reddit-ads/<slug>/`** inside
  the current business.
- When local files are staged, it also publishes canonical asset receipts under
  **`product/public-assets/<slug>/`**.
- Metrics publication lives under **`metrics/reddit-ads/<slug>/`**.

## Common Pitfalls

- Treating a private local file path as if Reddit can fetch it without a public URL
- Treating `launch` as if it makes the ad live; it only stages `PAUSED`
- Claiming CAC, ROAS, or conversion attribution from ad-platform delivery metrics alone
- Ignoring a blocked publish-target receipt and pretending Reddit accepted the creative anyway

## Verification Checklist

- [ ] `business_reddit_ad_launch mode=preflight` returned identity, businesses, ad accounts, profiles, funding instruments, and pixels
- [ ] `distribution/reddit-ads/<slug>/plan.json` exists and matches the intended launch shape
- [ ] `distribution/reddit-ads/<slug>/receipt.json` exists and truthfully reflects `suppressed_test_mode`, `created_paused`, or `partial_failed`
- [ ] When local files were used, `product/public-assets/<slug>/receipt.json` exists with the staged public URL or the real blocker
- [ ] Any activate/pause/budget action has a corresponding `distribution/reddit-ads/<slug>/actions/<idempotency>.json`
- [ ] Metrics sync wrote both `metrics/reddit-ads/<slug>/syncs/<idempotency>.json` and `metrics/reddit-ads/<slug>/insights.jsonl`

## Rules

1. Never claim live Reddit ad delivery without the launch/control receipts.
2. Never skip preflight when live defaults are unclear.
3. Launch always stages `PAUSED`; activation is a separate explicit move.
4. Treat publish-target reachability as a real blocker when staging local files for live use.
5. Ad-platform metrics are not the same as business attribution.
