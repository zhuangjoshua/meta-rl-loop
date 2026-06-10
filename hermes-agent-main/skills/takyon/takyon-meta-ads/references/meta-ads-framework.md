# Meta Ads Framework (PRODUCTION / LAUNCH layer)

How `business_meta_ad_launch` turns a UGC video into a bounded Meta ad under a reserved credit cap, and the rails
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
Campaign (act_<id>/campaigns, objective, bounded start/end schedule)
   └── AdSet (act_<id>/adsets, daily_budget, optimization_goal, billing_event, targeting, bounded by reserved credits)
          └── Ad (act_<id>/ads, links AdSet + AdCreative)
```

- **AdVideo** — the uploaded mp4. A multipart upload to the `graph-video.facebook.com` host.
- **AdCreative** — the renderable ad: a `object_story_spec` with `page_id` + `video_data`
  (`video_id`, `message`, `image_url` thumbnail, and a `call_to_action` linking out).
- **Campaign** — the objective container (Outcome objective; created under the reserved spend policy).
- **AdSet** — budget + audience + delivery (`daily_budget`, `optimization_goal`,
  `billing_event`, `targeting`; bounded by the reserved spend policy).
- **Ad** — binds one AdSet to one AdCreative.

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
- Live launch also has a **total campaign cap** equal to the reserved remaining Meta channel
  credits after setup. If daily pace or requested end time would exceed that total, or if the
  remaining cap is below the live minimum (default 5 USD), launch fails closed.

## Safety rails (why this is testable without burning money)

1. **Reserved cap first.** The tool reserves the remaining Meta channel credits before provider-side launch. If reservation fails, nothing launches.
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

## Activation

Launch intent is live by default, but it is still bounded by the reserved credit cap, daily safety cap,
and derived end time. If the operator wants review first, the launch request can explicitly stage the
campaign paused instead.
