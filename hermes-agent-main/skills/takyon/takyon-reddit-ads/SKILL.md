---
name: takyon-reddit-ads
description: >-
  Launch, control, and measure Reddit campaigns from a real promoted post, public creative URL, or
  existing business media asset. Use when finished creative is ready for Reddit paid distribution,
  authentication and account discovery is needed, or a live campaign needs operation. Do not use to
  create media, fabricate URLs, or claim unsupported attribution.
---

# Reddit Ads

Own Reddit launch staging, explicit control, and ad-platform metric synchronization from an existing
promoted post, a real public creative URL, or a business creative asset staged onto an authorized
public target.

## When to Use

- A finished ad creative needs launch or paused staging as a bounded Reddit campaign.
- A finished local business image or video must become a Reddit promoted post safely.
- Reddit authentication, account, profile, funding-instrument, or pixel discovery is needed before
  launch work.
- A launched ad needs activation, pause, daily-budget change, or delivery-metric synchronization.
- Reddit delivery metrics need to be synchronized into durable business state for later tracking.

## Do Not Use

- Building the image or video asset itself; route missing media to the appropriate creative method.
- Inventing placeholder, mock, fixture, or stub URLs to force a launch through.
- Claiming CAC, ROAS, purchases, pipeline, or conversion and revenue attribution the business has
  not joined truthfully.

## Method

1. Read business state, prior Reddit operations, real creative, and remaining authorized channel
   budget so a retry is not mistaken for a new campaign.
2. Run read-only preflight to discover the identity, business, ad account, profile, funding
   instrument, and pixel available to the bound authority.
3. Choose an existing promoted post or one real creative asset. If the provider requires a public
   URL, stage the asset through the declared publication capability and verify reachability.
4. Define one bounded campaign, ad group, post, and ad with audience, objective, destination,
   schedule, copy, and daily pace. Use a stable idempotency key for the same intent.
5. Request launch in paused or active state as authorized, then read authoritative completion state.
6. Apply activate, pause, or set-budget operations explicitly.
7. Synchronize delivery metrics and preserve their time range, object level, and provider identity.

## Verification

- Preflight resolves real launch identities and required account objects.
- The media or promoted post is real and publicly reachable when required.
- Budget and schedule remain within bound authority and provider minimums.
- Idempotent retries do not create duplicate campaigns or ads.
- Reported metrics remain ad-platform delivery truth and do not imply unsupported attribution.

Read [references/reddit-ads-framework.md](references/reddit-ads-framework.md) for the provider object
model, metric meanings, and launch sequence.
