---
name: takyon-meta-ads-v2
description: >-
  Launch, control, measure, and verify Meta campaigns from finished real creative, including pixel
  readiness and evidence-backed performance evaluation. Use when an existing image or video is ready
  for Meta execution or an existing campaign needs operation. Do not use to create media, fabricate
  attribution, hard-delete campaigns, or bypass missing authority.
---

# Meta Ads

Own Meta campaign launch and lifecycle for one business: media readiness, campaign/ad-set/ad creation,
budget change, pause, activation, delivery-metric synchronization, evidence-based evaluation, and
conversion-pixel readiness. Exact media-upload and ad-object authorities are HANDOFF bindings; never
replace them with ambient credentials.

The operational sequence is hybrid media upload plus ad-object creation, followed by budget set or
change, pause or activate, delivery-metrics sync, good/bad evaluation, and per-business pixel verify
or ensure from a creative the creative layer already produced.

## When to Use

- A finished image or video creative needs to launch, or stage paused, as a bounded Meta campaign.
- An existing campaign, ad set, or ad needs its daily budget changed, pausing, or activation.
- Meta delivery metrics need synchronization into a durable time series.
- The operator asks whether an object is performing well or poorly and what action the evidence
  supports.
- A shared pixel and per-business conversion need verification before a conversion launch.

## Do Not Use

- Choosing the creative format, writing copy, or producing the asset itself.
- Inventing placeholder, mock, fixture, or stub media to force launch inputs through.
- Hard-deleting campaigns; the bound interface cannot delete. Pause here and delete only in the Ads
  Manager UI.
- Claiming CAC, ROAS, purchases, or revenue attribution without a truthful joined data source.

## Method

1. Read current business state, existing campaign evidence, allocated channel budget, and prior
   attempts so retries are recognized.
2. Confirm the finished creative is real, policy-compliant, and suitable for the intended placement.
   Route missing creative upstream rather than fabricating it.
3. For conversion objectives, verify pixel and custom-conversion readiness; repair through the bound
   capability or stop with the exact blocker.
4. Define objective, destination, audience, placements, schedule, copy, optimization event, and a
   bounded budget. Preserve a stable idempotency key for the same intent.
5. Request launch in paused or active state according to the operator's intent and authority.
6. Read authoritative completion state before claiming provider objects exist.
7. Apply budget, pause, or activate operations explicitly and idempotently.
8. Synchronize ad-platform delivery metrics, then evaluate only against the benchmarks and minimum
   evidence window selected for the campaign.

## Verification

- Every launch references a real finished asset and a bounded authorized budget.
- Conversion campaigns pass pixel readiness before activation.
- Retries with the same intent do not create duplicate provider objects.
- Control changes and metric syncs are reflected by authoritative provider-backed evidence.
- Evaluation distinguishes platform delivery metrics from downstream customer and revenue truth.

Read [references/benchmarks.md](references/benchmarks.md),
[references/campaign-options.md](references/campaign-options.md),
[references/pixel-attribution.md](references/pixel-attribution.md), and
[references/pixel-health.md](references/pixel-health.md) when applicable.
