---
name: takyon-reddit-ads
description: >-
  Launch, control, and measure Reddit campaigns from a real promoted post, public creative URL, or
  existing business media asset. Use when finished creative is ready for Reddit paid distribution,
  authentication and account discovery is needed, or a live campaign needs operation. Do not use to
  create media, fabricate URLs, or claim unsupported attribution.
---

# Takyon Reddit Ads

## Overview

Turn an existing promoted post, a public hosted image/video/carousel, or a local business creative file into a **paid Reddit ad** for one business.
This skill is the **distribution** half of the creative pipeline: upstream creative skills can still produce
assets under `product/`, and this skill can stage those local files onto the business publish target when needed.
If the requested launch is `asset_kind: "image"` and no real creative exists yet, route upstream to
`takyon-static-ad-creative-generator` first and launch from that generated asset bundle; if it is
`asset_kind: "video"` and no real video exists yet, route upstream to `ugc-video-ad` first.
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
  Owned by upstream creative work and the business publish target. This skill does not regenerate assets itself, but **when the Reddit channel has allocated creative credits and the required asset does not exist yet, that allocation IS the approval to generate it: route upstream to `takyon-static-ad-creative-generator` (image) or `ugc-video-ad` (video) in the SAME flow, let them reserve from the channel bucket, then launch from the generated asset — do NOT stage the campaign and stop at `blocked_needs_creative` waiting for a separate operator approval.** Only treat missing creative as a hard blocker when the channel has no credits to fund generation or no provider is available. Missing creative is never a license to invent `placehold.co`, mock, fixture, or stub media.
- **Launch/control layer** — the campaign objects + explicit activate/pause/budget changes.
  Owned here, lives under `distribution/`, and runs through the guarded
  **`business_reddit_ad_launch`** and **`business_reddit_ad_control`** tools.
- **Metrics layer** — ad-platform delivery metrics only. Owned here, lives under `metrics/`,
  and runs through **`business_reddit_ad_insights_sync`**.

The **launch** tool owns the bounded spend policy: it reserves the remaining Reddit channel
credits, derives a truthful daily pace and end time when needed, creates the provider objects,
and activates them when the operator asked to launch. Explicit control actions can still pause,
re-activate, and change daily budget inside that reserved cap. Insights sync records
Reddit delivery metrics like spend, impressions, clicks, CTR, CPC, and ECPM/derived CPM, but it does **not**
invent business attribution.

## When to Use

- A business has a real public ad creative URL, a finished local business image/video asset, or an existing `post_id` and wants it staged as a Reddit ad.
- You need to verify Reddit auth, business/ad-account/profile defaults, funding instruments, or pixels before launch work.
- You want to launch a bounded campaign immediately or intentionally stage it paused for review.
- You need to explicitly activate, pause, or update the daily budget of a launched Reddit campaign.
- You want to sync delivery metrics from Reddit back into Takyon for future tracking.

**Do not use for:** building the creative asset itself; substituting placeholders, mocks, or stub media for a missing creative; hiding missing funding/pixel/profile setup; claiming conversions or ROAS without truthful joins.

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
  **`business_reddit_ad_launch`** (preflight + bounded launch),
  **`business_reddit_ad_control`** (activate, pause, set_budget),
  **`business_reddit_ad_insights_sync`** (delivery metrics sync)
- Upstream assets: an existing promoted `post_id`, a public image/video/carousel URL bundle, or a local business image/video bundle under `product/` that Takyon can stage to `product/public-assets/`
- Upstream creative budget rule: when this skill routes upstream to `ugc-video-ad` or `static-ad-creative-generator`, pass `budget_bucket: "reddit"` or `ad_metadata.channel: "reddit"` so the creative spend lands on the Reddit business budget bucket
- Upstream static-image spec rule: when this skill routes to `takyon-static-ad-creative-generator`, author the normal canonical static-ad schema, not a Reddit-launch plan or loose `{headline, subhead, style}` object. Set `platform: "reddit"`, `placement: "feed"`, a feed-safe ratio such as `1:1`, and fill the required `creative_id`, `aspect_ratio`, `goal`, `audience`, `strategy`, `visual`, `product`, `copy`, `layout`, `prompting`, and `qa` fields. Later map `copy.headline` to `post.headline`, `copy.primary_text` to `post.supplementary_text`/body, and `copy.cta` to `post.call_to_action`.
- Safety: `daily_budget_usd` is capped by `TAKYON_REDDIT_MAX_DAILY_BUDGET_USD` (default 50), must meet the live minimum (`TAKYON_REDDIT_MIN_LIVE_BUDGET_USD`, default 5), live launch cannot exceed the reserved Reddit channel credits, test-mode businesses never call Reddit, and metrics sync is ad-platform only.
- Default live budget rule: if `daily_budget_usd` is omitted, `business_reddit_ad_launch` derives a bounded daily pace and end time from the remaining Reddit channel credits after setup. Use `activate=false` only when you intentionally want a paused staged campaign.

## Best Live Path

The smallest truthful end-to-end Reddit path is:

1. **Create a Reddit Ads developer application** and complete OAuth2 so Takyon has a refresh token.
2. **Run preflight** with `business_reddit_ad_launch` `mode: "preflight"` to discover the business, ad account, profile, funding instrument, and pixel that the token can actually use.
3. **Prepare the creative upstream** with `ugc-video-ad`, `static-ad-creative-generator`, or an existing promoted post. If the requested launch is `asset_kind: "image"` and there is no truthful image asset yet, stop and use `takyon-static-ad-creative-generator` until a real creative bundle exists under `product/static-ads/<slug>/`; the upstream spec must be the canonical static-ad schema with `platform: "reddit"` and `placement: "feed"`, not a Reddit launch plan. Do not use `placehold.co`, mock placeholders, or ad hoc fallback URLs as launch creative.
4. **If reusing an existing post**, launch with `asset_kind: "existing_post"` plus `post_id`.
5. **If creating a new promoted post**, either provide public media URLs directly or point the `post` block at local business files (`image_path`, `video_path`, `media_path`, `thumbnail_path`) so Takyon can stage them onto the business publish target first.
6. **Launch through** `business_reddit_ad_launch`.
7. **Activate explicitly** with `business_reddit_ad_control` only after review.
8. **Sync delivery metrics** with `business_reddit_ad_insights_sync`.
9. **If live staging fails on reachability**, read the `product/public-assets/<slug>/receipt.json` blocker and fix the publish target instead of pretending Reddit accepted a private file.

## Prerequisites

- The Takyon toolset must be available; **`business_reddit_ad_launch`**,
  **`business_reddit_ad_control`**, and **`business_reddit_ad_insights_sync`** must appear in the
  current Agent SDK tool inventory.
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
- **If `asset_kind: "image"` and no real image asset exists yet, you must route upstream to `takyon-static-ad-creative-generator` first.** A placeholder service URL, mock asset, or fixture image is not a valid live creative input.
- **A unique Reddit User-Agent string**. Generic user agents are throttled much harder by Reddit.
- Credential readiness for this skill is owned by `business_reddit_ad_launch mode=preflight`, not by env-only frontmatter gates, because live auth may come from Safebox-backed env values or the saved local auth state file.

## References

- [references/reddit-ads-framework.md](references/reddit-ads-framework.md) — the Reddit object model,
  required live inputs, rate limits, budget semantics, and the credit-cap / budget-cap / pixel rails.

## Templates

- [templates/plan.json](templates/plan.json) — the structured launch input passed to `business_reddit_ad_launch`.

## How to Run

- Call `business_read_business` first to confirm the business id, its **mode** (test vs live), and the current creative/post source.
- **Preflight (read-only, no objects):** call `business_reddit_ad_launch` with `mode: "preflight"`. It returns the Reddit identity, businesses, ad accounts, profiles, funding instruments, pixels, and discovered defaults. Run this before any launch.
- **Draft the plan:** write `distribution/reddit-ads/<slug>/plan.json` from
  [templates/plan.json](templates/plan.json) with the campaign/ad-group/ad blocks and either:
  - `asset_kind: "existing_post"` plus `post_id`, or
  - `asset_kind: "image" | "video" | "carousel"` plus either public URLs under the `post` block or local business file paths (`image_path`, `video_path`, `media_path`, `thumbnail_path`) that Takyon can stage first.
  If `asset_kind: "image"` and the business does not already have a truthful creative, route to `takyon-static-ad-creative-generator` first and prefer a generated local file such as `product/static-ads/<slug>/<creative>.png` on `post.image_path`. The upstream spec must validate against `templates/ad-spec.schema.json` with `platform: "reddit"` and `placement: "feed"`; do not hand-write a loose spec or a launch-plan-shaped object. Do not put `placehold.co`, mock, fixture, or stub URLs into a live launch plan.
  For new promoted posts, the plan can also carry copy fields such as `headline`, `display_url`,
  `call_to_action`, and `supplementary_text`. If `post.destination_url` / `ad.click_url` is omitted,
  Takyon defaults the click destination to the business's canonical product URL.
  If `ad_group.daily_budget_usd` is omitted, Takyon derives it from the reserved Reddit channel credits; do not stop for a generic daily-budget confirmation when the budget rail is already authorized.
  The launch tool itself already handles the normal Reddit object sequence:
  Campaign → Ad Group → optional Post → Ad.
- **Launch:** call `business_reddit_ad_launch` with `mode: "launch"` and a stable `idempotency_key`.
  It reserves the credit cap, creates Campaign → Ad Group → optional Post → Ad, and activates them unless you explicitly requested a paused staged launch.
- **Public asset staging receipts:** when local files are used, the tool also writes canonical asset receipts under `product/public-assets/<slug>/receipt.json`.
- **Control (explicit):** call `business_reddit_ad_control` using the launch slug/receipt. `pause`
  stops delivery, `activate` resumes it inside the same reserved cap, and `set_budget` updates
  the daily budget without exceeding the remaining reserved total.
- **Metrics sync:** call `business_reddit_ad_insights_sync` on the launch slug/receipt to write
  a durable delivery snapshot under `metrics/reddit-ads/<slug>/`.
- The tools are **idempotent** on `idempotency_key`: a retry with the same key returns the existing
  receipt instead of creating duplicate Reddit objects.
- Each tool records its own durable truth (event + receipt/snapshot). Do not hand-write success.

## Procedure

1. **Read business state** — `business_read_business`. Note the business `mode`. Confirm whether the truthful live source is an existing `post_id`, a public creative URL bundle, or a local business asset under `product/`.
2. **Preflight the account** — call `business_reddit_ad_launch` `mode: "preflight"`. Verify it returns the right ad account, profile, funding instrument, and pixel. If it errors with a missing-credential or missing-default message, record the blocker and stop.
3. **Confirm the creative source is actually launchable** — either a real `post_id`, public media URLs, or local business files that the launch tool can stage to a reachable publish target. If `asset_kind: "image"` and no real creative exists yet, stop and route upstream to `takyon-static-ad-creative-generator`; do not fabricate `placehold.co`, mock, fixture, or placeholder image URLs just to get a campaign through. In live mode, if the publish target is not actually reachable, stop on that blocker.
4. **Draft `plan.json`** under `distribution/reddit-ads/<slug>/` from the template: choose the objective, targeting, ad copy, and either `post_id`, public media URLs, or local staged-file inputs on the `post` block. Omit `ad_group.daily_budget_usd` when the backend should derive the bounded pace from the reserved Reddit credits; only set it explicitly when the operator chose a different pace.
5. **Launch** — call `business_reddit_ad_launch` `mode: "launch"` with the plan fields and a stable `idempotency_key`.
   - Expect `status: "activated"` by default, or `status: "created_paused"` only when you explicitly requested a paused staged launch.
6. **Control if needed** — if the operator wants the ad live, call
  `business_reddit_ad_control` with `operation: "activate"` and the same slug. To stop it, use
  `operation: "pause"`. To change pace, use `operation: "set_budget"` with `daily_budget_usd`.
7. **Sync metrics** — call `business_reddit_ad_insights_sync` with `level: "campaign"` (or `ad_group` / `ad`) to persist Reddit delivery metrics under `metrics/reddit-ads/<slug>/`.
8. **Keep state truthful** — if a live step fails after some objects were created, the tool writes a `partial_failed` receipt with the IDs that exist. Surface that blocker; do not claim success.
9. **Re-sync on a later review wake** — Reddit delivery does not flow into Takyon on its own. On a subsequent wake, check the pulse's `active_ad_campaigns`: if a live campaign shows `needs_sync: true` (or a stale `insights_synced_ago`), call `business_reddit_ad_insights_sync` again to refresh delivery metrics *before* judging performance or changing the campaign.

## Output Format

- `distribution/reddit-ads/<slug>/plan.json` — the structured launch input.
- `distribution/reddit-ads/<slug>/receipt.json` — the tool-written truth of the launch:
  `status` (`created_paused` | `activated` | `partial_failed` | `blocked_*`), `paused`, the
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
- Using `placehold.co`, mock, fixture, or stub media URLs in a live launch plan instead of a real creative asset
- Assuming `launch` is always paused; live launch activates by default unless you explicitly staged it
- Claiming CAC, ROAS, or conversion attribution from ad-platform delivery metrics alone
- Ignoring a blocked publish-target receipt and pretending Reddit accepted the creative anyway

## Verification Checklist

- [ ] `business_reddit_ad_launch mode=preflight` returned identity, businesses, ad accounts, profiles, funding instruments, and pixels
- [ ] `distribution/reddit-ads/<slug>/plan.json` exists and matches the intended launch shape
- [ ] When `asset_kind=image`, the referenced image is a real external asset or a real business creative bundle under `product/static-ads/<slug>/`, not a placeholder/mock/stub URL
- [ ] `distribution/reddit-ads/<slug>/receipt.json` exists and truthfully reflects `activated`, `created_paused`, `partial_failed`, or an exact `blocked_*` status
- [ ] When local files were used, `product/public-assets/<slug>/receipt.json` exists with the staged public URL or the real blocker
- [ ] Any activate/pause/budget action has a corresponding `distribution/reddit-ads/<slug>/actions/<idempotency>.json`
- [ ] Metrics sync wrote both `metrics/reddit-ads/<slug>/syncs/<idempotency>.json` and `metrics/reddit-ads/<slug>/insights.jsonl`

## Rules

1. Never claim live Reddit ad delivery without the launch/control receipts.
2. Never skip preflight when live defaults are unclear.
3. Live launch activates by default unless you explicitly stage it paused.
4. Treat publish-target reachability as a real blocker when staging local files for live use.
5. Ad-platform metrics are not the same as business attribution.
