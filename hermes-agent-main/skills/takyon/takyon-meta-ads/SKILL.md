---
name: takyon-meta-ads
description: Launch a Meta (Facebook/Instagram) ad for one Takyon business from a UGC video or static image asset — preflight the access token, then create a PAUSED Campaign/AdSet/Ad. Never serves or spends; activation is out of scope. Test-mode businesses suppress everything to a local receipt.
version: 1.0.0
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
      ]
    routing:
      owns: Meta ad launch staging from a finished UGC or static-image asset, including preflight and paused Campaign/AdSet/Ad creation
      when_to_use:
        - a finished UGC or static ad asset needs to be staged as a paused Meta campaign
        - Meta token or ad-account preflight is needed before launch work
      do_not_use_for:
        - activating live spend or changing live budgets
        - building the video asset itself
  takyon:
    scope: business
    allowed_roots: [product, distribution, metrics]
    output_root: distribution
    publication:
      - distribution/meta-ads/<slug>/plan.json
      - distribution/meta-ads/<slug>/receipt.json

required_environment_variables: [META_ACCESS_TOKEN]
required_credential_files: []
---

# Takyon Meta Ads

## Overview

Turn a finished UGC video or static image bundle into a **paid Meta ad** for one business.
This skill is the **distribution** half of the creative pipeline: `ugc-video-ad` produces
`product/ugc-ads/<slug>/ad.mp4`, while `static-ad-creative-generator` produces
`product/static-ads/<slug>/...png`. This skill turns one of those assets into a Meta
**AdCreative** and builds a **Campaign → AdSet → Ad** — **always PAUSED**.

Two hard layers stay separate:

- **Asset layer** — the video or image. Owned by `ugc-video-ad` / `static-ad-creative-generator`,
  lives under `product/`. This skill never regenerates or edits it; it only consumes the finished asset.
- **Launch layer** — the campaign objects + spend. Owned here, lives under `distribution/`,
  and runs through the guarded **`business_meta_ad_launch`** tool.

The tool **never activates anything and never spends**. Everything it creates is `PAUSED`.
Flipping an ad live (the actual spend decision) is deliberately not part of this skill.

## When to Use

- A business has a UGC ad at `product/ugc-ads/<slug>/ad.mp4` or a static image under
  `product/static-ads/<slug>/` and wants it staged as a Meta (Facebook/Instagram) ad.
- You need to verify the Meta access token / which ad accounts it can touch (preflight)
  before spending any effort.
- You want to stage a campaign safely (PAUSED) for human review before it ever serves.

**Do not use for:** activating/un-pausing ads or changing live budgets (out of scope —
spend is a separate, human-gated decision); non-Meta channels (use `takyon-x`
or `takyon-distribution`); building the video itself (use `ugc-video-ad`).

## Quick Reference

- Primary root: `distribution/`
- Publication paths: `distribution/meta-ads/<slug>/plan.json`, `distribution/meta-ads/<slug>/receipt.json`
- Tool used by this skill: **`business_meta_ad_launch`** (preflight + PAUSED launch)
- Upstream assets: `product/ugc-ads/<slug>/ad.mp4` from `ugc-video-ad` or a local image from `product/static-ads/<slug>/`
- Safety: every object is created `PAUSED`; `daily_budget_usd` is capped by
  `TAKYON_META_MAX_DAILY_BUDGET_USD` (default 50); test-mode businesses never call Meta.

## Prerequisites

- The Takyon toolset must be available; **`business_meta_ad_launch`** must be registered
  (gated in frontmatter `metadata.hermes.requires_tools`).
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
- **Launch (always PAUSED):** call `business_meta_ad_launch` with `mode: "launch"`,
  `ad_video_path`, and the `campaign`/`adset`/`ad` blocks. In **test mode** the tool writes a
  suppressed `receipt.json` and calls Meta **not at all**. In **live mode** it uploads the
  video, builds the creative, and creates Campaign/AdSet/Ad **PAUSED**, then writes
  `receipt.json` with the real object IDs.
- The tool is **idempotent** on `idempotency_key`: a retry with the same key returns the
  existing receipt instead of creating duplicate Meta objects.
- The tool **records the launch itself** (event + receipt). Do not hand-write success.

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
4. **Launch PAUSED** — call `business_meta_ad_launch` `mode: "launch"` with the plan fields
   and a stable `idempotency_key`.
   - **Test mode** → expect `status: "suppressed_test_mode"` and a local `receipt.json`; no
     Meta objects exist.
   - **Live mode** → expect `status: "created_paused"` with `ids` for video/creative/
     campaign/adset/ad, and a `receipt.json` with those IDs.
5. **Verify** — read `distribution/meta-ads/<slug>/receipt.json`. Confirm `paused: true`.
   For a live launch, the operator (a human) reviews the PAUSED ad in Meta Ads Manager and
   activates it there; this skill does not activate.
6. **Keep state truthful** — if a live step fails after some objects were created, the tool
   writes a `partial_failed` receipt with the IDs that exist. Surface that blocker; do not
   claim success.

## Output Format

- `distribution/meta-ads/<slug>/plan.json` — the structured launch input (human-readable).
- `distribution/meta-ads/<slug>/receipt.json` — the tool-written truth of the launch:
  `status` (`suppressed_test_mode` | `created_paused` | `partial_failed`), `paused`, the
  Meta object `ids` (live), the budget, and the source `ad_video_path`.

## Publication

- This skill publishes to the canonical directory **`distribution/meta-ads/<slug>/`** inside
  the `distribution/` root, where `<slug>` derives from the campaign/video (override with
  `slug`).
- The durable truth of a launch is the **`receipt.json`** written by
  `business_meta_ad_launch`, plus the `meta_ad.launch` event it commits. Live external state
  (the real Meta objects) is referenced by ID in that receipt — never claimed without it.
- The upstream asset's truth source remains `business_ugc_ad_write` under `product/`.

## Common Pitfalls

- **Treating PAUSED as live.** This skill stages ads; it never serves or spends. Activation
  is a separate human decision in Ads Manager.
- **Launching without preflight.** Always confirm the token + ad account first; a bad token
  fails the whole chain mid-way.
- **No thumbnail.** A Meta video creative needs an image. If the auto thumbnail is not ready
  yet, pass `ad.image_url` or retry shortly — do not pretend the creative was made.
- **Budget over the cap.** `daily_budget_usd` above `TAKYON_META_MAX_DAILY_BUDGET_USD` is
  rejected. Lower it or set the env cap deliberately.
- **Editing the UGC mp4 here.** The video is a `product/` asset owned by `ugc-video-ad`.
- **Hand-writing a receipt.** Only the tool writes `receipt.json`; never fake it.

## Verification Checklist

- [ ] `business_read_business` confirmed the business mode and that `ad.mp4` exists.
- [ ] `mode: "preflight"` returned a token identity + at least one ad account.
- [ ] `distribution/meta-ads/<slug>/plan.json` exists with `ad_video_path` and a capped budget.
- [ ] Launch result + `receipt.json` show `paused: true` and the right `status`.
- [ ] Live launches carry real Meta `ids`; test launches are `suppressed_test_mode` with no IDs.
- [ ] No object was activated; no spend was incurred by this skill.
- [ ] No state was written outside `distribution/` (asset stays under `product/`).

## Rules

1. Keep work **business-scoped** (one business per run) and one campaign per `<slug>`.
2. **Never activate and never spend.** Only `PAUSED` objects; activation is out of scope.
3. **Do not fake side effects** — Meta calls, object IDs, or the receipt. The tool records
   the launch; the skill prose never claims a live launch without the tool's receipt.
4. Use the canonical tool (`business_meta_ad_launch`) and path
   (`distribution/meta-ads/<slug>/`) — never parallel state.
5. Keep the **asset** layer (`product/`, owned by `ugc-video-ad`) and the **launch** layer
   (`distribution/`, owned here) separate.
6. Credentials come from env/`.env` only; never hardcode tokens.

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
