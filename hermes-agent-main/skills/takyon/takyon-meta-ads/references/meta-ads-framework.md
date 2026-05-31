# Meta Ads Framework (PRODUCTION / LAUNCH layer)

How `business_meta_ad_launch` turns a UGC video into a **PAUSED** Meta ad, and the rails
that keep it safe. This is the launch layer; the *video* itself is the asset layer owned by
`ugc-video-ad`.

## The object graph

A Meta ad is five objects. The tool creates them in this order:

```
ad.mp4 (product asset)
   │  upload (graph-video host: act_<id>/advideos)
   ▼
AdVideo  ──referenced by──▶  AdCreative  (act_<id>/adcreatives, object_story_spec.video_data)
                                  │
Campaign (act_<id>/campaigns, objective, PAUSED)
   └── AdSet (act_<id>/adsets, daily_budget, optimization_goal, billing_event, targeting, PAUSED)
          └── Ad (act_<id>/ads, links AdSet + AdCreative, PAUSED)
```

- **AdVideo** — the uploaded mp4. A multipart upload to the `graph-video.facebook.com` host.
- **AdCreative** — the renderable ad: a `object_story_spec` with `page_id` + `video_data`
  (`video_id`, `message`, `image_url` thumbnail, and a `call_to_action` linking out).
- **Campaign** — the objective container (Outcome objective; created `PAUSED`).
- **AdSet** — budget + audience + delivery (`daily_budget`, `optimization_goal`,
  `billing_event`, `targeting`; created `PAUSED`).
- **Ad** — binds one AdSet to one AdCreative (created `PAUSED`).

## Objectives (ODAX / Outcome)

Recent API versions accept only Outcome objectives. Pick one for `campaign.objective`:

| Objective | Use for | Typical `optimization_goal` | `billing_event` |
| --- | --- | --- | --- |
| `OUTCOME_TRAFFIC` (default) | send clicks to a link | `LINK_CLICKS` | `IMPRESSIONS` |
| `OUTCOME_ENGAGEMENT` | video views / post engagement | `THRUPLAY` or `POST_ENGAGEMENT` | `IMPRESSIONS` |
| `OUTCOME_AWARENESS` | reach / brand | `REACH` | `IMPRESSIONS` |
| `OUTCOME_LEADS` | lead forms / link leads | `LEAD_GENERATION` or `LINK_CLICKS` | `IMPRESSIONS` |
| `OUTCOME_SALES` | conversions (needs a pixel/promoted_object) | `OFFSITE_CONVERSIONS` | `IMPRESSIONS` |

For a UGC ad that drives traffic to the product, `OUTCOME_TRAFFIC` + `LINK_CLICKS` is the
safe default and needs no pixel. `OUTCOME_SALES`/conversion optimization needs a configured
pixel + `promoted_object`, which this v1 tool does not set — use traffic/engagement unless
you have that wired.

## Targeting

`adset.targeting` is a JSON object. Minimal sane default (US, broad):

```json
{ "geo_locations": { "countries": ["US"] } }
```

Add `age_min`/`age_max`, `genders`, `interests`, etc. as needed. Keep it broad for a first
test — narrow audiences + tiny budgets often under-deliver.

## Budget

- `adset.daily_budget_usd` is **US dollars per day**; the tool converts to minor units
  (cents) for the API.
- It is **capped** by `TAKYON_META_MAX_DAILY_BUDGET_USD` (default **50**). Over-cap launches
  are rejected. Raise the env var deliberately if you really need more.
- Because every object is **PAUSED**, the budget is only a *configured ceiling* — nothing
  spends until a human activates the ad in Ads Manager.

## Safety rails (why this is testable without burning money)

1. **PAUSED, always.** The tool sets `status: PAUSED` on Campaign, AdSet, and Ad. It refuses
   any `activate`/`status: ACTIVE` input. PAUSED objects never serve and never spend.
2. **Budget cap.** `daily_budget_usd` above the cap is rejected before any call.
3. **Test mode.** A test-mode business suppresses *all* Meta calls and writes a local
   `receipt.json` only. Use this to exercise the path with zero external effect.
4. **Sandbox ad account.** For a fully faithful live test with zero spend risk, create a
   **Meta sandbox ad account** and pass it as `ad_account_id`. Sandbox objects are real API
   objects that can never serve or spend.
5. **Preflight.** `mode: "preflight"` is a read-only `GET /me` + `GET /me/adaccounts`. It
   creates nothing and is the cheapest way to confirm the token and pick the account.
6. **Idempotency.** A repeated `idempotency_key` returns the existing receipt instead of
   creating duplicate objects.
7. **Honest partial failure.** If a later step fails after earlier objects were created, the
   tool writes a `partial_failed` receipt listing the created IDs — never a fake success.

## The UGC → Meta handoff

- `ugc-video-ad` writes `product/ugc-ads/<slug>/ad.mp4` and records it via
  `business_ugc_ad_write`. That is the **only** thing this skill consumes from the asset
  layer.
- Pass that path as `ad_video_path` (e.g. `product/ugc-ads/<slug>/ad.mp4`). The launch
  artifacts land under `distribution/meta-ads/<slug>/` — a different root, different slug
  space if you choose.
- The video creative needs a **thumbnail image**. Meta usually auto-generates one shortly
  after upload; the tool fetches it from the video's `thumbnails` edge. If it is not ready,
  pass `ad.image_url` (any public image URL, e.g. a frame you host) or retry.

## Activation (out of scope here, on purpose)

Going live = spending money. That decision is intentionally **not** in this skill or tool.
A human reviews the PAUSED ad in Meta Ads Manager and activates it there. If a future
Takyon-native activation path is wanted, it should be its own guarded tool with an explicit
confirm flag, a budget authority check, and its own receipt — not a flag bolted onto launch.
