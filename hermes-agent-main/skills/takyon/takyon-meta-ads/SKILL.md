---
name: takyon-meta-ads
description: Launch and operate a Meta (Facebook/Instagram) ad for one Takyon business from a UGC video or static image asset — preflight the token, create a PAUSED Campaign/AdSet/Ad, then explicitly activate/pause/update budget and sync ad-platform metrics through guarded tools. Test-mode businesses suppress to local receipts.
version: 1.2.0
author: Four Manifold
license: Proprietary
platforms: [linux, macos]

metadata:
  hermes:
    category: takyon
    tags: [takyon, meta, facebook, instagram, ads, paid, distribution, ugc, image]
    related_skills: [ugc-video-ad, static-ad-creative-generator, takyon-distribution, takyon-business-metrics]
    requires_toolsets: [takyon]
    requires_tools:
      [
        business_read_business,
        business_meta_ad_launch,
        business_meta_ad_bind_manual_launch,
        business_meta_ad_control,
        business_meta_ad_insights_sync,
      ]
    routing:
      owns: Meta ad launch staging or manual-handoff packaging, explicit control, external launch binding, and ad-platform metrics sync from a finished UGC or static-image asset
      when_to_use:
        - a finished UGC or static ad asset needs to be staged as a paused Meta campaign
        - Meta should stop at a manual handoff packet because the business cannot auto-post yet
        - a manually launched Meta campaign needs its real campaign/adset/ad ids bound back into Takyon
        - Meta token or ad-account preflight is needed before launch work
        - a launched Meta ad needs to be activated, paused, or have its daily budget updated
        - ad-platform delivery metrics need to be synced into Takyon for later tracking, whether they came from the Meta API or a manual operator entry
      do_not_use_for:
        - building the video asset itself
        - inventing conversion attribution, CAC, or ROAS when Takyon has not recorded join keys
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

required_environment_variables: [META_ACCESS_TOKEN]
required_credential_files: []
---

# Takyon Meta Ads

## Overview

Turn a finished UGC video or static image bundle into a **paid Meta ad** for one business.
This skill is the **distribution** half of the creative pipeline: `ugc-video-ad` produces
`product/ugc-ads/<slug>/ad.mp4`, while `static-ad-creative-generator` produces
`product/static-ads/<slug>/...png`. This skill turns one of those assets into a Meta
**AdCreative** and builds a **Campaign → AdSet → Ad** — created **PAUSED first**, then
optionally controlled through a separate guarded tool.

Three hard layers stay separate:

- **Asset layer** — the video or image. Owned by `ugc-video-ad` / `static-ad-creative-generator`,
  lives under `product/`. This skill never regenerates or edits it; it only consumes the finished asset.
- **Launch/control layer** — the campaign objects + explicit activate/pause/budget changes.
  Owned here, lives under `distribution/`, and runs through the guarded
  **`business_meta_ad_launch`** and **`business_meta_ad_control`** tools.
- **Metrics layer** — ad-platform delivery metrics only. Owned here, lives under `metrics/`,
  and runs through **`business_meta_ad_insights_sync`**.

The **launch** tool never activates anything; it only creates `PAUSED` objects. Activation,
pausing, and daily-budget changes are explicit follow-up control actions. Insights sync records
Meta delivery metrics like spend, impressions, clicks, CTR, CPC, and CPM, but it does **not**
invent business attribution.

## When to Use

- A business has a UGC ad at `product/ugc-ads/<slug>/ad.mp4` or a static image under
  `product/static-ads/<slug>/` and wants it staged as a Meta (Facebook/Instagram) ad.
- You need to verify the Meta access token / which ad accounts it can touch (preflight)
  before spending any effort.
- You want to stage a campaign safely (PAUSED) for human review before it ever serves.
- You need to explicitly activate, pause, or update the daily budget of a previously launched
  Meta campaign.
- You want to sync delivery metrics from Meta back into Takyon for future tracking.

**Do not use for:** non-Meta channels (use `takyon-x` or `takyon-distribution`); building
the asset itself (use `ugc-video-ad` or `static-ad-creative-generator`); claiming conversion
attribution or CAC when the join keys have not been recorded.

## Quick Reference

- Primary root: `distribution/`
- Publication paths:
  `distribution/meta-ads/<slug>/plan.json`,
  `distribution/meta-ads/<slug>/receipt.json`,
  `distribution/meta-ads/<slug>/actions/<idempotency>.json`,
  `metrics/meta-ads/<slug>/insights.jsonl`,
  `metrics/meta-ads/<slug>/syncs/<idempotency>.json`
- Tools used by this skill:
  **`business_meta_ad_launch`** (preflight + PAUSED launch or manual handoff),
  **`business_meta_ad_bind_manual_launch`** (bind real Meta ids after manual launch),
  **`business_meta_ad_control`** (activate, pause, set_budget),
  **`business_meta_ad_insights_sync`** (delivery metrics sync, including manual metrics import)
- Upstream assets: `product/ugc-ads/<slug>/ad.mp4` from `ugc-video-ad` or a local image from `product/static-ads/<slug>/`
- Safety: launch always creates `PAUSED`; `daily_budget_usd` is capped by
  `TAKYON_META_MAX_DAILY_BUDGET_USD` (default 50); test-mode businesses never call Meta; metrics sync is ad-platform only and does not imply business attribution.

## Prerequisites

- The Takyon toolset must be available; **`business_meta_ad_launch`**,
  **`business_meta_ad_bind_manual_launch`**, **`business_meta_ad_control`**, and
  **`business_meta_ad_insights_sync`** must be registered (gated in frontmatter
  `metadata.hermes.requires_tools`).
- **A finished UGC video** at `product/ugc-ads/<slug>/ad.mp4` (build it first with
  `ugc-video-ad`).
- **Live launch / preflight credentials** (env or local `.env`; never hardcode):
  - **`META_ACCESS_TOKEN`** *or* **`META_SYSTEM_USER_ACCESS_TOKEN`** — the access token the
    tool authenticates with.
  - **`META_AD_ACCOUNT_ID`** — the ad account to create objects in (with or without `act_`).
  - **`META_PAGE_ID`** — the Facebook Page a video creative must be tied to.
  - Optional: `META_GRAPH_VERSION` (default `v23.0`).
- **`httpx`** must be importable for the live video upload (it is a runtime dependency).
- **Test-mode businesses need none of these** — the tool suppresses to a local receipt.
- The account `META_AD_ACCOUNT_ID` points at may be a **live, funded** account. PAUSED
  objects cannot spend, but for fully no-risk testing prefer a **Meta sandbox ad account**
  (create one under your business, pass it as `ad_account_id`).

## References

- [references/meta-ads-framework.md](references/meta-ads-framework.md) — the Meta object
  model (AdVideo → AdCreative → Campaign → AdSet → Ad), Outcome objectives,
  optimization-goal/billing-event pairings, targeting basics, the UGC→Meta handoff, and the
  PAUSED / budget-cap / sandbox safety rails.

## Templates

- [templates/plan.json](templates/plan.json) — the launch input (campaign/adset/ad blocks)
  passed to `business_meta_ad_launch`.

## How to Run

- Call `business_read_business` first to confirm the business id, its **mode** (test vs
  live), and that a UGC ad exists at `product/ugc-ads/<slug>/`.
- **Preflight (read-only, no objects):** call `business_meta_ad_launch` with
  `mode: "preflight"`. It returns the token identity and the ad accounts it can touch. Run
  this before any launch to confirm the token works and to pick the right `ad_account_id`.
- **Draft the plan:** write `distribution/meta-ads/<slug>/plan.json` from
  [templates/plan.json](templates/plan.json) (objective, daily budget, targeting, the ad
  copy + destination link, and `ad_video_path`).
- **Launch (always PAUSED first):** call `business_meta_ad_launch` with `mode: "launch"`,
  `ad_video_path` or `ad_image_path`, and the `campaign`/`adset`/`ad` blocks. In **test mode**
  the tool writes a suppressed `receipt.json` and calls Meta **not at all**. In **live mode**
  it uploads the asset, builds the creative, and creates Campaign/AdSet/Ad **PAUSED**, then
  writes `receipt.json` with the real object IDs.
- **Manual handoff:** call `business_meta_ad_launch` with `mode: "manual_handoff"` when Meta
  cannot auto-post yet. The tool writes `plan.json` + `receipt.json` with `status:
  "ready_for_manual_launch"` and stops before calling Meta.
- **Bind external launch:** after a human creates the campaign in Ads Manager, call
  `business_meta_ad_bind_manual_launch` with the real `campaign_id`, `adset_id`, and `ad_id`
  so Takyon's canonical receipt becomes `externally_launched`.
- **Control (explicit):** when the operator wants to serve or stop serving, call
  `business_meta_ad_control` using the launch slug/receipt. `activate` flips the campaign live,
  `pause` stops it again, and `set_budget` updates the ad set daily budget under the same
  safety cap.
- **Metrics sync:** call `business_meta_ad_insights_sync` on the launch slug/receipt to write
  a durable delivery snapshot under `metrics/meta-ads/<slug>/`. For alpha manual launch flows,
  this same tool can accept raw `spend_usd`, `impressions`, and `clicks` with `source:
  "manual"` and compute CTR/CPC/CPM inside Takyon.
- The tool is **idempotent** on `idempotency_key`: a retry with the same key returns the
  existing receipt instead of creating duplicate Meta objects.
- Each tool records its own durable truth (event + receipt/snapshot). Do not hand-write success.

## Procedure

1. **Read business state** — `business_read_business`. Note the business `mode`. Confirm
   `product/ugc-ads/<slug>/ad.mp4` exists (the upstream `ugc-video-ad` output, normally
   recorded via `business_ugc_ad_write`). If it is missing, stop and build it first.
2. **Preflight the token** — call `business_meta_ad_launch` `mode: "preflight"`. Verify it
   returns an identity and at least one ad account. If it errors with a missing-credential
   message, record the blocker and stop; do not fabricate a launch.
3. **Draft `plan.json`** under `distribution/meta-ads/<slug>/` from the template: pick the
   Outcome objective, a `daily_budget_usd` within the cap, targeting, the ad `message` +
   `link` + `call_to_action`, and set `ad_video_path` to the UGC mp4.
4. **Choose the launch path** — call `business_meta_ad_launch` with the plan fields and a
   stable `idempotency_key`.
   - **Test mode** → expect `status: "suppressed_test_mode"` and a local `receipt.json`; no
     Meta objects exist.
   - **Live auto-post** (`mode: "launch"`) → expect `status: "created_paused"` with `ids` for
     video/creative/campaign/adset/ad, and a `receipt.json` with those IDs.
   - **Manual handoff** (`mode: "manual_handoff"`) → expect `status:
     "ready_for_manual_launch"` plus `distribution/meta-ads/<slug>/plan.json`. Hand this packet
     to the human operator.
5. **Bind manual launch if needed** — when a human launches the campaign in Ads Manager, call
   `business_meta_ad_bind_manual_launch` with the real Meta ids. Expect
   `status: "bound_manual_launch"` and the canonical receipt to change to
   `status: "externally_launched"`.
6. **Control if needed** — if the operator wants the ad live and the campaign was auto-posted, call
   `business_meta_ad_control` with `operation: "activate"` and the same slug. To stop it, use
   `operation: "pause"`. To change pace, use `operation: "set_budget"` with
   `daily_budget_usd`.
7. **Sync metrics** — call `business_meta_ad_insights_sync` with `level: "campaign"` (or
   `adset` / `ad`) to persist Meta delivery metrics under `metrics/meta-ads/<slug>/`. In
   manual-launch alpha, use `source: "manual"` with raw `spend_usd`, `impressions`, and
   `clicks`.
8. **Keep state truthful** — if a live step fails after some objects were created, the tool
   writes a `partial_failed` receipt with the IDs that exist. Surface that blocker; do not
   claim success.

## Output Format

- `distribution/meta-ads/<slug>/plan.json` — the structured launch input (human-readable).
- `distribution/meta-ads/<slug>/receipt.json` — the tool-written truth of the launch:
  `status` (`suppressed_test_mode` | `ready_for_manual_launch` | `created_paused` |
  `externally_launched` | `partial_failed`), `paused`, the Meta object `ids` when known, the
  budget, and the source asset path.
- `distribution/meta-ads/<slug>/actions/<idempotency>.json` — the tool-written truth of each
  control or manual-bind action (`activate`, `pause`, `set_budget`, `manual_bind`).
- `metrics/meta-ads/<slug>/syncs/<idempotency>.json` and `metrics/meta-ads/<slug>/insights.jsonl`
  — the tool-written truth of each metrics sync.

## Publication

- This skill publishes to the canonical directory **`distribution/meta-ads/<slug>/`** inside
  the `distribution/` root, where `<slug>` derives from the campaign/video (override with
  `slug`).
- The durable truth of a launch is the **`receipt.json`** written by
  `business_meta_ad_launch`, plus the `meta_ad.launch` event it commits.
- The durable truth of a manual external launch bind is the updated `receipt.json`, the
  action receipt written by `business_meta_ad_bind_manual_launch`, and the
  `meta_ad.manual_bind` event it commits.
- The durable truth of control actions is the action receipt written by
  `business_meta_ad_control`, plus the matching `meta_ad.activate`, `meta_ad.pause`, or
  `meta_ad.budget_update` event.
- The durable truth of metrics sync is the sync receipt / JSONL snapshot written by
  `business_meta_ad_insights_sync`, plus the `meta_ad.insights_sync` event.
- Live external state (the real Meta objects) is referenced by ID in the launch receipt —
  never claimed without it.
- The upstream asset's truth source remains `business_ugc_ad_write` under `product/`.

## Common Pitfalls

- **Treating PAUSED as live.** Launch creates only paused objects. Serving starts only after
  an explicit `business_meta_ad_control` activate step.
- **Launching without preflight.** Always confirm the token + ad account first; a bad token
  fails the whole chain mid-way.
- **No thumbnail.** A Meta video creative needs an image. If the auto thumbnail is not ready
  yet, pass `ad.image_url` or retry shortly — do not pretend the creative was made.
- **Budget over the cap.** `daily_budget_usd` above `TAKYON_META_MAX_DAILY_BUDGET_USD` is
  rejected on both launch and `set_budget`. Lower it or set the env cap deliberately.
- **Editing the UGC mp4 here.** The video is a `product/` asset owned by `ugc-video-ad`.
- **Assuming metrics imply attribution.** Insights sync records Meta delivery metrics, not
  downstream business outcomes unless Takyon has separate join keys.
- **Hand-writing receipts.** Only the tools write Meta launch/control/sync receipts; never fake them.

## Verification Checklist

- [ ] `business_read_business` confirmed the business mode and that `ad.mp4` exists.
- [ ] `mode: "preflight"` returned a token identity + at least one ad account.
- [ ] `distribution/meta-ads/<slug>/plan.json` exists with `ad_video_path` and a capped budget.
- [ ] Launch result + `receipt.json` show `paused: true` and the right `status`.
- [ ] Live launches carry real Meta `ids`; test launches are `suppressed_test_mode` with no IDs.
- [ ] Any activate/pause/set_budget action has its own action receipt under `distribution/meta-ads/<slug>/actions/`.
- [ ] Any metrics sync has a receipt under `metrics/meta-ads/<slug>/syncs/` and appended JSONL under `metrics/meta-ads/<slug>/insights.jsonl`.
- [ ] Launch/control state stayed under `distribution/`, metrics state stayed under `metrics/`, and the asset stayed under `product/`.

## Rules

1. Keep work **business-scoped** (one business per run) and one campaign per `<slug>`.
2. **Launch paused first.** Only `business_meta_ad_launch` may create the campaign objects,
   and it must create them `PAUSED`.
3. **Do not fake side effects** — Meta calls, object IDs, activation, budget changes, or
   metrics sync. The guarded tools record them; the skill prose never claims them without the
   tool receipts.
4. Use the canonical tools (`business_meta_ad_launch`, `business_meta_ad_control`,
   `business_meta_ad_insights_sync`) and canonical paths — never parallel state.
5. Keep the **asset** layer (`product/`, owned by `ugc-video-ad`) and the **launch** layer
   (`distribution/`, owned here) separate.
6. Keep **metrics** under `metrics/` and do not present ad-platform metrics as business
   attribution unless Takyon has separate evidence.
7. Credentials come from env/`.env` only; never hardcode tokens.

## Troubleshooting

| Problem | Fix |
| --- | --- |
| `Meta action requires META_SYSTEM_USER_ACCESS_TOKEN or META_ACCESS_TOKEN` | Export the token or add it to a local `.env`; never hardcode. |
| Preflight returns no ad accounts | The token can't see any ad account; fix permissions or use a token with `ads_management`. |
| `live launch requires META_PAGE_ID` | Set `META_PAGE_ID` or pass `ad.page_id`; video creatives must be tied to a Page. |
| `Meta requires a thumbnail ... none was ready` | Pass `ad.image_url`, or retry after the uploaded video finishes processing. |
| `daily_budget_usd ... exceeds the safety cap` | Lower the budget or raise `TAKYON_META_MAX_DAILY_BUDGET_USD` deliberately. |
| Want a fully no-risk live test | Create a **Meta sandbox ad account** and pass it as `ad_account_id`; sandbox objects never serve or spend. |
| `partial_failed` receipt | A later step failed; the receipt lists created IDs — clean them up in Ads Manager or fix the input and retry with a new `idempotency_key`. |
| `Meta video upload requires the httpx package` | Ensure `httpx` is installed in the runtime environment. |
